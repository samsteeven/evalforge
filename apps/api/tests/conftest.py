"""Les tests d'API tournent contre un vrai PostgreSQL migré par Alembic et un vrai RabbitMQ.

Par défaut des conteneurs jetables (Testcontainers, données en mémoire). EVALFORGE_TEST_DATABASE_URL et
EVALFORGE_TEST_RABBITMQ_URL permettent de viser des services déjà démarrés. Chaque test crée son propre espace de
travail et ses propres files (préfixe unique) : aucune donnée partagée.
"""

import asyncio
import json
import os
import re
import uuid
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import httpx
import pytest
from alembic import command
from alembic.config import Config
from cryptography.fernet import Fernet
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from evalforge_api.core.config import Settings
from evalforge_api.main import create_app
from evalforge_api.modules.providers.factory import http_provider_factory
from evalforge_api.worker.main import Worker

API_DIR = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def database_url() -> Iterator[str]:
    url = os.environ.get("EVALFORGE_TEST_DATABASE_URL")
    if url:
        migrate(url)
        yield url
        return
    from testcontainers.community.postgres import PostgresContainer

    container = PostgresContainer("postgres:18-alpine", driver="asyncpg").with_tmpfs_mount("/var/lib/postgresql")
    with container as postgres:
        url = postgres.get_connection_url()
        migrate(url)
        yield url


@pytest.fixture(scope="session")
def rabbitmq_url() -> Iterator[str]:
    url = os.environ.get("EVALFORGE_TEST_RABBITMQ_URL")
    if url:
        yield url
        return
    from testcontainers.core.container import DockerContainer
    from testcontainers.core.waiting_utils import wait_for_logs

    container = (
        DockerContainer("rabbitmq:4.4-alpine")
        .with_env("RABBITMQ_DEFAULT_USER", "evalforge")
        .with_env("RABBITMQ_DEFAULT_PASS", "evalforge")
        .with_exposed_ports(5672)
        .with_tmpfs_mount("/var/lib/rabbitmq")
    )
    with container as rabbit:
        wait_for_logs(rabbit, "Server startup complete", timeout=120)
        yield f"amqp://evalforge:evalforge@{rabbit.get_container_host_ip()}:{rabbit.get_exposed_port(5672)}/"


def migrate(url: str) -> None:
    config = Config(str(API_DIR / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", url)
    config.attributes["configure_logger"] = False
    command.upgrade(config, "head")


@pytest.fixture(scope="session")
def base_settings(database_url: str, rabbitmq_url: str) -> Settings:
    return Settings(
        database_url=database_url,
        rabbitmq_url=rabbitmq_url,
        secret_key=Fernet.generate_key().decode(),
        jwt_secret_key="cle-de-test-pour-les-jetons-d-acces-0123456789",
        cookie_secure=False,  # le client de test parle http
        # Délais raccourcis : le comportement est le même, les tests vont plus vite.
        outbox_interval_seconds=0.2,
        run_lease_seconds=2.0,
        retry_base_delay_seconds=0.2,
        max_attempts=2,
    )


@pytest.fixture
async def settings(base_settings: Settings) -> Settings:
    """Files propres à chaque test, et outbox vide au départ.

    La base est partagée entre les tests : un événement resté en attente (test précédent arrêté net) serait sinon
    publié dans les files de ce test. En production, une base sert un seul environnement.
    """
    engine = create_async_engine(base_settings.database_url)
    async with engine.begin() as connection:
        await connection.execute(text("update outbox_events set published_at = now() where published_at is null"))
    await engine.dispose()
    return base_settings.model_copy(update={"rabbitmq_prefix": f"test-{uuid.uuid4().hex[:10]}"})


class FakeLLM:
    """Faux fournisseur compatible OpenAI. Le comportement dépend du modèle demandé :

    - « parrot » répond la réponse attendue de la question trouvée dans le prompt ;
    - « vague » répond toujours « Je ne sais pas. » ;
    - « slow » répond comme « parrot », en 0,3 s (arrêt d'un worker en plein run) ;
    - « judge » joue le juge : 5 si la réponse évaluée contient la réponse attendue, 1 sinon ;
    - « judge-garbage » répond un texte sans JSON.
    """

    def __init__(self) -> None:
        self.answers: dict[str, str] = {}
        self.requests: list[httpx.Request] = []

    def calls(self, model: str) -> int:
        return sum(1 for request in self.requests if json.loads(request.content)["model"] == model)

    async def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        body = json.loads(request.content)
        prompt = body["messages"][-1]["content"]
        model = body["model"]
        if model == "slow":
            await asyncio.sleep(0.3)
        if model in ("parrot", "slow"):
            content = next((answer for question, answer in self.answers.items() if question in prompt), "?")
        elif model == "judge":
            expected = re.search(r"^Réponse attendue : (.*)$", prompt, re.MULTILINE)
            actual = re.search(r"^Réponse évaluée : (.*)$", prompt, re.MULTILINE)
            right = bool(expected and actual and expected[1].strip() in actual[1])
            verdict = {"score": 5 if right else 1, "reason": "Même information." if right else "Ne répond pas."}
            content = json.dumps(verdict, ensure_ascii=False)
        elif model == "judge-garbage":
            content = "Excellente réponse, bravo."
        else:
            content = "Je ne sais pas."
        words = len(re.findall(r"\w+", prompt))
        return httpx.Response(
            200,
            json={
                "id": f"req-{uuid.uuid4().hex[:8]}",
                "model": f"{model}-2026-11",
                "choices": [{"message": {"role": "assistant", "content": content}}],
                "usage": {"prompt_tokens": words, "completion_tokens": len(content.split())},
            },
        )


@pytest.fixture
def fake_llm() -> FakeLLM:
    return FakeLLM()


@pytest.fixture
async def support_agent_client():
    """L'agent de support réel, piloté par le faux fournisseur de scripts/fake_llm.py, joignable en mémoire.

    Le modèle pilote est choisi par l'adresse : http://support-agent.test ou http://careless-agent.test.
    """
    import importlib.util

    from evalforge_llm import OpenAICompatibleProvider
    from support_agent.agent import SupportAgent

    spec = importlib.util.spec_from_file_location("fake_llm", API_DIR.parents[1] / "scripts" / "fake_llm.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    llm = httpx.AsyncClient(transport=httpx.ASGITransport(app=module.app))

    async def handle(request: httpx.Request) -> httpx.Response:
        model = request.url.host.removesuffix(".test")
        agent = SupportAgent(OpenAICompatibleProvider(base_url="http://fake-llm/v1", client=llm), model)
        result = await agent.run(json.loads(request.content)["message"])
        return httpx.Response(200, json=result.model_dump(mode="json"))

    client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    yield client
    await client.aclose()
    await llm.aclose()


@pytest.fixture
async def worker_factory(settings: Settings, fake_llm: FakeLLM, support_agent_client):
    """Démarre des workers branchés sur le faux fournisseur ; arrêtés en fin de test."""
    llm_client = httpx.AsyncClient(transport=httpx.MockTransport(fake_llm.handle))
    factory = http_provider_factory(llm_client, timeout_seconds=5, backoff_seconds=0)
    started: list[Worker] = []

    async def start(custom_settings: Settings | None = None, worker_id: str | None = None) -> Worker:
        worker = Worker(
            custom_settings or settings,
            provider_factory=factory,
            worker_id=worker_id,
            agent_client=support_agent_client,
        )
        await worker.start()
        started.append(worker)
        return worker

    yield start
    for worker in started:
        await worker.stop_quietly()
    await llm_client.aclose()


@pytest.fixture
async def app_factory(settings: Settings, worker_factory):
    """Démarre l'API (lifespan compris) et, sauf demande contraire, un worker."""

    def start(custom_settings: Settings | None = None, *, with_worker: bool = True):
        current = custom_settings or settings
        app = create_app(current)

        class Running:
            async def __aenter__(self) -> httpx.AsyncClient:
                self._lifespan = app.router.lifespan_context(app)
                await self._lifespan.__aenter__()
                if with_worker:
                    await worker_factory(current)
                self._client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test/api/v1")
                self._client.app = app  # accès à app.state pour les tests du traitement des runs
                return self._client

            async def __aexit__(self, *exc) -> None:
                await self._client.aclose()
                await self._lifespan.__aexit__(None, None, None)

        return Running()

    return start


@pytest.fixture
async def client(app_factory) -> AsyncIterator[httpx.AsyncClient]:
    async with app_factory() as client:
        yield client
