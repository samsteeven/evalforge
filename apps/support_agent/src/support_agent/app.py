"""L'agent exposé en HTTP, selon le contrat qu'EvalForge sait évaluer.

POST /v1/agent/run   {"message": "..."}  →  {"output", "trajectory", "stop_reason", "model", tokens}

uv run evalforge-support-agent          # port 8200 ; modèle : SUPPORT_AGENT_LLM_BASE_URL, _MODEL, _API_KEY
"""

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
import uvicorn
from fastapi import FastAPI
from pydantic import BaseModel, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from evalforge_llm import OpenAICompatibleProvider
from evalforge_llm.network import SafeTransport
from support_agent.agent import AgentResult, SupportAgent


class AgentSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SUPPORT_AGENT_", env_file=".env", extra="ignore")

    llm_base_url: str = "http://127.0.0.1:8100/v1"
    llm_model: str = "support-agent"
    llm_api_key: SecretStr | None = None
    max_steps: int = 6
    # Le faux fournisseur de démonstration écoute sur 127.0.0.1.
    allow_private_urls: bool = True


class RunIn(BaseModel):
    message: str = Field(min_length=1, max_length=4000)


def create_app(settings: AgentSettings | None = None, client: httpx.AsyncClient | None = None) -> FastAPI:
    settings = settings or AgentSettings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        http = client or httpx.AsyncClient(transport=SafeTransport(allow_private=settings.allow_private_urls))
        provider = OpenAICompatibleProvider(
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key.get_secret_value() if settings.llm_api_key else None,
            client=http,
        )
        app.state.agent = SupportAgent(provider, settings.llm_model, max_steps=settings.max_steps)
        try:
            yield
        finally:
            if client is None:
                await http.aclose()

    app = FastAPI(title="Agent de support (démonstration EvalForge)", version="0.1.0", lifespan=lifespan)

    @app.post("/v1/agent/run", response_model=AgentResult)
    async def run(body: RunIn) -> AgentResult:
        return await app.state.agent.run(body.message)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


def main() -> None:
    uvicorn.run(create_app(), host="127.0.0.1", port=int(os.environ.get("SUPPORT_AGENT_PORT", "8200")))
