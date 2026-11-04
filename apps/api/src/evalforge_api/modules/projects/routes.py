import uuid
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from evalforge_api.core.database import SessionDep
from evalforge_api.core.tenancy import WorkspaceDep, not_found
from evalforge_api.modules.projects.models import AIProject

router = APIRouter(tags=["projects"])


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    type: Literal["llm", "rag", "agent"] = "llm"


class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    type: str
    created_at: datetime


async def get_project(session: AsyncSession, workspace_id: uuid.UUID, project_id: uuid.UUID) -> AIProject:
    project = await session.scalar(
        select(AIProject).where(AIProject.id == project_id, AIProject.workspace_id == workspace_id)
    )
    if project is None:
        raise not_found("Projet")
    return project


@router.post("/projects", status_code=201, response_model=ProjectOut)
async def create_project(body: ProjectCreate, workspace_id: WorkspaceDep, session: SessionDep) -> AIProject:
    project = AIProject(workspace_id=workspace_id, name=body.name, type=body.type)
    session.add(project)
    try:
        await session.commit()
    except IntegrityError:
        raise HTTPException(409, "Un projet porte déjà ce nom") from None
    return project


@router.get("/projects", response_model=list[ProjectOut])
async def list_projects(workspace_id: WorkspaceDep, session: SessionDep) -> list[AIProject]:
    rows = await session.scalars(
        select(AIProject).where(AIProject.workspace_id == workspace_id).order_by(AIProject.created_at)
    )
    return list(rows)


@router.get("/projects/{project_id}", response_model=ProjectOut)
async def read_project(project_id: uuid.UUID, workspace_id: WorkspaceDep, session: SessionDep) -> AIProject:
    return await get_project(session, workspace_id, project_id)
