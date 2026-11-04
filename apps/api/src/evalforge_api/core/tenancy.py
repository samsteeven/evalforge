"""Utilisateur authentifié et espace de travail courant.

Le jeton d'accès identifie l'utilisateur ; l'espace vient de l'en-tête X-Workspace-Id et n'est accepté que si
l'utilisateur en est membre. L'appartenance est relue à chaque requête : un membre retiré perd l'accès tout de
suite, sans attendre l'expiration de son jeton. Toutes les requêtes métier filtrent sur cet espace.
"""

import uuid
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select

from evalforge_api.core.database import SessionDep
from evalforge_api.core.security import ROLE_RANK, InvalidTokenError
from evalforge_api.modules.workspaces.models import WorkspaceMember

_bearer = HTTPBearer(auto_error=False)


def unauthorized(detail: str) -> HTTPException:
    return HTTPException(401, detail, headers={"WWW-Authenticate": "Bearer"})


async def current_user_id(
    request: Request, credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)]
) -> uuid.UUID:
    if credentials is None:
        raise unauthorized("Authentification requise")
    try:
        return request.app.state.access_tokens.verify(credentials.credentials)
    except InvalidTokenError:
        raise unauthorized("Jeton invalide ou expiré") from None


UserDep = Annotated[uuid.UUID, Depends(current_user_id)]


@dataclass(frozen=True)
class WorkspaceAccess:
    workspace_id: uuid.UUID
    user_id: uuid.UUID
    role: str

    def at_least(self, role: str) -> bool:
        return ROLE_RANK[self.role] >= ROLE_RANK[role]


async def workspace_access(
    x_workspace_id: Annotated[uuid.UUID, Header()], user_id: UserDep, session: SessionDep
) -> WorkspaceAccess:
    role = await session.scalar(
        select(WorkspaceMember.role).where(
            WorkspaceMember.workspace_id == x_workspace_id, WorkspaceMember.user_id == user_id
        )
    )
    if role is None:  # espace inexistant ou dont l'utilisateur n'est pas membre : même réponse
        raise HTTPException(404, "Espace de travail introuvable")
    return WorkspaceAccess(x_workspace_id, user_id, role)


AccessDep = Annotated[WorkspaceAccess, Depends(workspace_access)]


def require(role: str):
    async def dependency(access: AccessDep) -> uuid.UUID:
        if not access.at_least(role):
            raise HTTPException(403, f"Rôle insuffisant : {role} au minimum")
        return access.workspace_id

    return dependency


# Lecture pour tout membre ; écriture à partir de « member » ; secrets et équipe à partir de « admin ».
WorkspaceDep = Annotated[uuid.UUID, Depends(require("viewer"))]
EditorDep = Annotated[uuid.UUID, Depends(require("member"))]
AdminDep = Annotated[uuid.UUID, Depends(require("admin"))]


def not_found(what: str) -> HTTPException:
    # Une ressource d'un autre espace répond comme une ressource inexistante.
    return HTTPException(404, f"{what} introuvable")
