import uuid
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import AnyHttpUrl, BaseModel, Field, SecretStr
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from evalforge_api.core.database import SessionDep
from evalforge_api.core.tenancy import AdminDep, WorkspaceDep
from evalforge_api.modules.projects.routes import get_project
from evalforge_api.modules.providers.models import ModelConfiguration
from evalforge_llm.network import UnsafeDestinationError, check_url

router = APIRouter(tags=["providers"])


class ModelParameters(BaseModel):
    temperature: float = Field(default=0.0, ge=0, le=2)
    max_tokens: int | None = Field(default=None, ge=1, le=32768)


class Pricing(BaseModel):
    input_per_million_usd: float = Field(ge=0)
    output_per_million_usd: float = Field(ge=0)
    # D'où vient le prix (page tarifaire, date) : un coût sans source ne se vérifie pas.
    source: str | None = Field(default=None, max_length=500)


class ModelConfigurationCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    provider_kind: Literal["openai_compatible", "ollama", "evalforge_agent"]
    base_url: AnyHttpUrl
    model_name: str = Field(min_length=1, max_length=200)
    parameters: ModelParameters = Field(default_factory=ModelParameters)
    pricing: Pricing | None = None
    api_key: SecretStr | None = None


class ModelConfigurationOut(BaseModel):
    id: uuid.UUID
    name: str
    provider_kind: str
    base_url: str
    model_name: str
    parameters: dict[str, Any]
    pricing: dict[str, Any] | None
    has_api_key: bool
    created_at: datetime


def to_out(config: ModelConfiguration) -> ModelConfigurationOut:
    return ModelConfigurationOut(
        id=config.id,
        name=config.name,
        provider_kind=config.provider_kind,
        base_url=config.base_url,
        model_name=config.model_name,
        parameters=config.parameters,
        pricing=config.pricing,
        has_api_key=config.api_key_encrypted is not None,
        created_at=config.created_at,
    )


@router.post("/projects/{project_id}/providers", status_code=201, response_model=ModelConfigurationOut)
async def create_model_configuration(
    project_id: uuid.UUID,
    body: ModelConfigurationCreate,
    request: Request,
    workspace_id: AdminDep,
    session: SessionDep,
) -> ModelConfigurationOut:
    await get_project(session, workspace_id, project_id)
    try:
        check_url(str(body.base_url), allow_private=request.app.state.settings.allow_private_provider_urls)
    except UnsafeDestinationError as error:
        raise HTTPException(422, f"Adresse de fournisseur refusée : {error}") from None
    api_key = body.api_key.get_secret_value() if body.api_key else None
    config = ModelConfiguration(
        workspace_id=workspace_id,
        project_id=project_id,
        name=body.name,
        provider_kind=body.provider_kind,
        base_url=str(body.base_url).rstrip("/"),
        model_name=body.model_name,
        parameters=body.parameters.model_dump(),
        pricing=body.pricing.model_dump() if body.pricing else None,
        api_key_encrypted=request.app.state.secret_box.encrypt(api_key) if api_key else None,
    )
    session.add(config)
    try:
        await session.commit()
    except IntegrityError:
        raise HTTPException(409, "Une configuration porte déjà ce nom dans ce projet") from None
    return to_out(config)


@router.get("/projects/{project_id}/providers", response_model=list[ModelConfigurationOut])
async def list_model_configurations(
    project_id: uuid.UUID, workspace_id: WorkspaceDep, session: SessionDep
) -> list[ModelConfigurationOut]:
    await get_project(session, workspace_id, project_id)
    rows = await session.scalars(
        select(ModelConfiguration).where(ModelConfiguration.project_id == project_id).order_by(ModelConfiguration.name)
    )
    return [to_out(row) for row in rows]
