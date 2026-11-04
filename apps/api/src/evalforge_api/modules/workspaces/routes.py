import uuid
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, select

from evalforge_api.core.database import SessionDep
from evalforge_api.core.tenancy import AccessDep, UserDep, WorkspaceDep
from evalforge_api.modules.identity.models import User
from evalforge_api.modules.identity.routes import MembershipOut, create_workspace_for
from evalforge_api.modules.workspaces.models import Workspace, WorkspaceMember

router = APIRouter(tags=["workspaces"])

Role = Literal["viewer", "member", "admin", "owner"]


class WorkspaceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class MemberIn(BaseModel):
    email: EmailStr
    role: Role = "member"


class MemberOut(BaseModel):
    user_id: uuid.UUID
    email: str
    role: str
    created_at: datetime


@router.post("/workspaces", status_code=201, response_model=MembershipOut)
async def create_workspace(body: WorkspaceCreate, user_id: UserDep, session: SessionDep) -> MembershipOut:
    workspace = await create_workspace_for(session, user_id, body.name)
    await session.commit()
    return MembershipOut(workspace_id=workspace.id, name=workspace.name, slug=workspace.slug, role="owner")


@router.get("/workspaces/current", response_model=MembershipOut)
async def current_workspace(access: AccessDep, session: SessionDep) -> MembershipOut:
    workspace = await session.get(Workspace, access.workspace_id)
    return MembershipOut(workspace_id=workspace.id, name=workspace.name, slug=workspace.slug, role=access.role)


@router.get("/workspaces/current/members", response_model=list[MemberOut])
async def list_members(workspace_id: WorkspaceDep, session: SessionDep) -> list[MemberOut]:
    rows = await session.execute(
        select(WorkspaceMember, User.email)
        .join(User, User.id == WorkspaceMember.user_id)
        .where(WorkspaceMember.workspace_id == workspace_id)
        .order_by(User.email)
    )
    return [
        MemberOut(user_id=member.user_id, email=email, role=member.role, created_at=member.created_at)
        for member, email in rows
    ]


@router.post("/workspaces/current/members", status_code=201, response_model=MemberOut)
async def add_member(body: MemberIn, access: AccessDep, session: SessionDep) -> MemberOut:
    """Ajoute un compte existant. Seul un propriétaire peut nommer un administrateur ou un propriétaire."""
    if not access.at_least("admin"):
        raise HTTPException(403, "Rôle insuffisant : admin au minimum")
    if body.role in ("admin", "owner") and not access.at_least("owner"):
        raise HTTPException(403, "Seul un propriétaire peut accorder ce rôle")
    user = await session.scalar(select(User).where(User.email == body.email.lower()))
    if user is None:
        raise HTTPException(404, "Aucun compte avec cet e-mail : la personne doit d'abord s'inscrire")
    existing = await session.get(WorkspaceMember, (access.workspace_id, user.id))
    if existing is not None:
        raise HTTPException(409, "Déjà membre de cet espace")
    member = WorkspaceMember(workspace_id=access.workspace_id, user_id=user.id, role=body.role)
    session.add(member)
    await session.commit()
    return MemberOut(user_id=user.id, email=user.email, role=member.role, created_at=member.created_at)


@router.delete("/workspaces/current/members/{user_id}", status_code=204)
async def remove_member(user_id: uuid.UUID, access: AccessDep, session: SessionDep) -> None:
    if not access.at_least("admin"):
        raise HTTPException(403, "Rôle insuffisant : admin au minimum")
    member = await session.get(WorkspaceMember, (access.workspace_id, user_id))
    if member is None:
        raise HTTPException(404, "Membre introuvable")
    if member.role == "owner":
        if not access.at_least("owner"):
            raise HTTPException(403, "Seul un propriétaire peut retirer un propriétaire")
        owners = await session.scalar(
            select(func.count()).where(
                WorkspaceMember.workspace_id == access.workspace_id, WorkspaceMember.role == "owner"
            )
        )
        if owners == 1:
            raise HTTPException(409, "Un espace garde au moins un propriétaire")
    await session.delete(member)
    await session.commit()
