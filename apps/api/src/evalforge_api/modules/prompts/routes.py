import uuid
from datetime import datetime

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from evalforge_api.core.database import SessionDep
from evalforge_api.core.tenancy import EditorDep, WorkspaceDep
from evalforge_api.modules.projects.routes import get_project
from evalforge_api.modules.prompts.models import PromptVersion
from evalforge_engine.templating import variables

router = APIRouter(tags=["prompts"])


class PromptCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    template: str = Field(min_length=1, max_length=20000)
    system: str | None = Field(default=None, max_length=20000)


class PromptOut(BaseModel):
    id: uuid.UUID
    name: str
    version: int
    template: str
    system: str | None
    variables: list[str]
    created_at: datetime


def to_out(prompt: PromptVersion) -> PromptOut:
    return PromptOut(
        id=prompt.id,
        name=prompt.name,
        version=prompt.version,
        template=prompt.template,
        system=prompt.system,
        variables=variables(prompt.template),
        created_at=prompt.created_at,
    )


@router.post("/projects/{project_id}/prompts", status_code=201, response_model=PromptOut)
async def create_prompt_version(
    project_id: uuid.UUID, body: PromptCreate, response: Response, workspace_id: EditorDep, session: SessionDep
) -> PromptOut:
    """Chaque modification crée une nouvelle version ; un contenu identique renvoie la dernière."""
    await get_project(session, workspace_id, project_id)
    latest = await session.scalar(
        select(PromptVersion)
        .where(PromptVersion.project_id == project_id, PromptVersion.name == body.name)
        .order_by(PromptVersion.version.desc())
        .limit(1)
    )
    if latest is not None and (latest.template, latest.system) == (body.template, body.system):
        response.status_code = 200
        return to_out(latest)

    prompt = PromptVersion(
        workspace_id=workspace_id,
        project_id=project_id,
        name=body.name,
        version=(latest.version + 1) if latest else 1,
        template=body.template,
        system=body.system,
    )
    session.add(prompt)
    try:
        await session.commit()
    except IntegrityError:
        raise HTTPException(409, "Une autre version vient d'être créée, réessayez") from None
    return to_out(prompt)


@router.get("/projects/{project_id}/prompts", response_model=list[PromptOut])
async def list_prompt_versions(
    project_id: uuid.UUID, workspace_id: WorkspaceDep, session: SessionDep
) -> list[PromptOut]:
    await get_project(session, workspace_id, project_id)
    rows = await session.scalars(
        select(PromptVersion)
        .where(PromptVersion.project_id == project_id)
        .order_by(PromptVersion.name, PromptVersion.version)
    )
    return [to_out(row) for row in rows]
