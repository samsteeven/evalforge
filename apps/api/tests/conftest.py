"""Les tests d'API tournent contre un vrai PostgreSQL, migré par Alembic.

Par défaut un conteneur jetable (Testcontainers, données en mémoire). EVALFORGE_TEST_DATABASE_URL permet de
viser une base déjà démarrée. Chaque test crée son propre espace de travail : aucune donnée partagée.
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

from evalforge_api.core.config import Settings
from evalforge_api.main import create_app
from evalforge_api.modules.providers.factory import http_provider_factory

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


def migrate(url: str) -> None:
    config = Config(str(API_DIR / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", url)
    config.attributes["configure_logger"] = False
    command.upgrade(config, "head")


@pytest.fixture(scope="session")
def settings(database_url: str) -> Settings:
    return Settings(database_url=database_url, secret_key=Fernet.generate_key().decode())


class FakeLLM:
    """Faux fournisseur compatible OpenAI. Le comportement dépend du modèle demandé :

    - « parrot » répond la réponse attendue de la question trouvée dans le prompt ;
    - « vague » répond toujours « Je ne sais pas. » ;
    - « hang » ne répond jamais (arrêt de l'API pendant un run).
    """

    def __init__(self) -> None:
        self.answers: dict[str, str] = {}
        self.requests: list[httpx.Request] = []

    async def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        body = json.loads(request.content)
        prompt = body["messages"][-1]["content"]
        model = body["model"]
        if model == "hang":
            await asyncio.sleep(3600)
        if model == "parrot":
            content = next((answer for question, answer in self.answers.items() if question in prompt), "?")
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
async def app_factory(settings: Settings, fake_llm: FakeLLM):
    """Démarre l'application (lifespan compris) ; appelable deux fois pour simuler un redémarrage."""
    llm_client = httpx.AsyncClient(transport=httpx.MockTransport(fake_llm.handle))
    factory = http_provider_factory(llm_client, timeout_seconds=5, backoff_seconds=0)

    def start():
        app = create_app(settings, provider_factory=factory)

        class Running:
            async def __aenter__(self) -> httpx.AsyncClient:
                self._lifespan = app.router.lifespan_context(app)
                await self._lifespan.__aenter__()
                self._client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test/api/v1")
                self._client.app = app  # accès à app.state pour les tests du traitement des runs
                return self._client

            async def __aexit__(self, *exc) -> None:
                await self._client.aclose()
                await self._lifespan.__aexit__(None, None, None)

        return Running()

    yield start
    await llm_client.aclose()


@pytest.fixture
async def client(app_factory) -> AsyncIterator[httpx.AsyncClient]:
    async with app_factory() as client:
        yield client
