"""Espace de travail courant.

Toutes les données métier appartiennent à un espace de travail, et chaque requête filtre dessus.
Tant que l'authentification n'existe pas, l'espace vient de l'en-tête X-Workspace-Id ; il viendra ensuite
de l'appartenance de l'utilisateur authentifié, sans changer les requêtes.
"""

import uuid
from typing import Annotated

from fastapi import Depends, Header, HTTPException
from sqlalchemy import select

from evalforge_api.core.database import SessionDep
from evalforge_api.modules.workspaces.models import Workspace


async def current_workspace_id(x_workspace_id: Annotated[uuid.UUID, Header()], session: SessionDep) -> uuid.UUID:
    if await session.scalar(select(Workspace.id).where(Workspace.id == x_workspace_id)) is None:
        raise HTTPException(404, "Espace de travail introuvable")
    return x_workspace_id


WorkspaceDep = Annotated[uuid.UUID, Depends(current_workspace_id)]


def not_found(what: str) -> HTTPException:
    # Une ressource d'un autre espace répond comme une ressource inexistante.
    return HTTPException(404, f"{what} introuvable")
