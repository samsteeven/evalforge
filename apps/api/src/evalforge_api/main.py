import functools
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import APIRouter, FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from evalforge_api.core.config import Settings
from evalforge_api.core.secrets import SecretBox
from evalforge_api.modules.datasets.routes import router as datasets
from evalforge_api.modules.evaluations.routes import router as evaluations
from evalforge_api.modules.evaluations.runner import RunScheduler, execute_run, interrupt_abandoned_runs
from evalforge_api.modules.experiments.routes import router as experiments
from evalforge_api.modules.projects.routes import router as projects
from evalforge_api.modules.prompts.routes import router as prompts
from evalforge_api.modules.providers.factory import ProviderFactory, http_provider_factory
from evalforge_api.modules.providers.routes import router as providers
from evalforge_api.modules.workspaces.routes import router as workspaces

log = logging.getLogger(__name__)


def create_app(settings: Settings | None = None, provider_factory: ProviderFactory | None = None) -> FastAPI:
    settings = settings or Settings()
    secret_box = SecretBox(settings.secret_key.get_secret_value())

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = create_async_engine(settings.database_url, pool_pre_ping=True)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        http_client = httpx.AsyncClient()
        factory = provider_factory or http_provider_factory(
            http_client, timeout_seconds=settings.provider_timeout_seconds
        )
        interrupted = await interrupt_abandoned_runs(session_factory)
        if interrupted:
            log.warning("%d run(s) abandonné(s) par un arrêt précédent, marqué(s) interrompu(s)", interrupted)
        scheduler = RunScheduler(
            functools.partial(
                execute_run, session_factory=session_factory, provider_factory=factory, secret_box=secret_box
            )
        )
        app.state.session_factory = session_factory
        app.state.secret_box = secret_box
        app.state.scheduler = scheduler
        try:
            yield
        finally:
            await scheduler.shutdown()
            await http_client.aclose()
            await engine.dispose()

    app = FastAPI(title="EvalForge", version="0.1.0", lifespan=lifespan)
    api = APIRouter(prefix="/api/v1")
    for router in (workspaces, projects, datasets, providers, prompts, experiments, evaluations):
        api.include_router(router)
    app.include_router(api)

    @app.get("/health", tags=["health"])
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app
