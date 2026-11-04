import functools
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import APIRouter, FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from evalforge_api.core.config import Settings
from evalforge_api.core.secrets import SecretBox
from evalforge_api.core.security import AccessTokens, Passwords
from evalforge_api.modules.datasets.routes import router as datasets
from evalforge_api.modules.evaluations.routes import router as evaluations
from evalforge_api.modules.evaluations.runner import RunScheduler, execute_run, interrupt_abandoned_runs
from evalforge_api.modules.experiments.routes import router as experiments
from evalforge_api.modules.identity.routes import router as identity
from evalforge_api.modules.projects.routes import router as projects
from evalforge_api.modules.prompts.routes import router as prompts
from evalforge_api.modules.providers.factory import ProviderFactory, http_provider_factory
from evalforge_api.modules.providers.routes import router as providers
from evalforge_api.modules.workspaces.routes import router as workspaces
from evalforge_llm.network import SafeTransport

log = logging.getLogger(__name__)


def create_app(settings: Settings | None = None, provider_factory: ProviderFactory | None = None) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = create_async_engine(settings.database_url, pool_pre_ping=True)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        # Tous les appels aux fournisseurs passent par le contrôle SSRF à la connexion.
        http_client = httpx.AsyncClient(
            transport=SafeTransport(allow_private=settings.allow_private_provider_urls), follow_redirects=False
        )
        factory = provider_factory or http_provider_factory(
            http_client, timeout_seconds=settings.provider_timeout_seconds
        )
        interrupted = await interrupt_abandoned_runs(session_factory)
        if interrupted:
            log.warning("%d run(s) abandonné(s) par un arrêt précédent, marqué(s) interrompu(s)", interrupted)
        scheduler = RunScheduler(
            functools.partial(
                execute_run,
                session_factory=session_factory,
                provider_factory=factory,
                secret_box=app.state.secret_box,
            )
        )
        app.state.session_factory = session_factory
        app.state.scheduler = scheduler
        try:
            yield
        finally:
            await scheduler.shutdown()
            await http_client.aclose()
            await engine.dispose()

    app = FastAPI(
        title="EvalForge",
        version="0.2.0",
        description=(
            "Évaluation d'applications LLM, RAG et agents.\n\n"
            "Authentification : `Authorization: Bearer <jeton d'accès>` (voir `/api/v1/auth`). "
            "Toutes les routes métier exigent aussi l'en-tête `X-Workspace-Id` : l'espace de travail courant, "
            "dont l'utilisateur doit être membre."
        ),
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.secret_box = SecretBox(settings.secret_key.get_secret_value())
    app.state.access_tokens = AccessTokens(settings.jwt_secret_key.get_secret_value(), settings.access_token_minutes)
    app.state.passwords = Passwords()

    api = APIRouter(prefix="/api/v1")
    for router in (identity, workspaces, projects, datasets, providers, prompts, experiments, evaluations):
        api.include_router(router)
    app.include_router(api)

    @app.get("/health", tags=["health"])
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app
