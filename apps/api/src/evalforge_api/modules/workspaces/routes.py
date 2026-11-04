import uuid
from datetime import datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from evalforge_api.core.database import SessionDep
from evalforge_api.core.tenancy import WorkspaceDep
from evalforge_api.modules.workspaces.models import Workspace

router = APIRouter(tags=["workspaces"])


class WorkspaceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    slug: str = Field(max_length=100, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class WorkspaceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    slug: str
    created_at: datetime


@router.post("/workspaces", status_code=201, response_model=WorkspaceOut)
async def create_workspace(body: WorkspaceCreate, session: SessionDep) -> Workspace:
    workspace = Workspace(name=body.name, slug=body.slug)
    session.add(workspace)
    try:
        await session.commit()
    except IntegrityError:
        raise HTTPException(409, "Cet identifiant d'espace est déjà pris") from None
    return workspace


@router.get("/workspaces/current", response_model=WorkspaceOut)
async def current_workspace(workspace_id: WorkspaceDep, session: SessionDep) -> Workspace:
    return await session.scalar(select(Workspace).where(Workspace.id == workspace_id))
