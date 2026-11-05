import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from evalforge_api.core.config import Settings
from evalforge_api.core.secrets import SecretBox
from evalforge_api.core.security import AccessTokens, Passwords
from evalforge_api.messaging.outbox import OutboxPublisher
from evalforge_api.messaging.topology import Topology
from evalforge_api.modules.datasets.routes import router as datasets
from evalforge_api.modules.evaluations.baselines import router as baselines
from evalforge_api.modules.evaluations.routes import router as evaluations
from evalforge_api.modules.experiments.routes import router as experiments
from evalforge_api.modules.identity.routes import router as identity
from evalforge_api.modules.projects.routes import router as projects
from evalforge_api.modules.prompts.routes import router as prompts
from evalforge_api.modules.providers.routes import router as providers
from evalforge_api.modules.workspaces.routes import router as workspaces

log = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    """L'API n'exécute aucun run : elle les enregistre avec leur événement (outbox), les workers les exécutent."""
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = create_async_engine(settings.database_url, pool_pre_ping=True)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        publisher = OutboxPublisher(
            session_factory,
            settings.rabbitmq_url,
            Topology(settings.rabbitmq_prefix),
            interval_seconds=settings.outbox_interval_seconds,
        )
        await publisher.start()
        app.state.session_factory = session_factory
        app.state.outbox = publisher
        try:
            yield
        finally:
            await publisher.stop()
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
    for router in (identity, workspaces, projects, datasets, providers, prompts, experiments, evaluations, baselines):
        api.include_router(router)
    app.include_router(api)

    @app.get("/health", tags=["health"])
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app
