"""Inscription, connexion et sessions.

Le jeton d'accès (15 min) circule en en-tête Authorization et ne vit qu'en mémoire côté navigateur. Le jeton de
renouvellement est dans un cookie HttpOnly limité à /api/v1/auth : le JavaScript ne peut pas le lire. Il change à
chaque usage ; présenter un jeton déjà remplacé (vol, rejeu) révoque toute la famille, donc la session volée
comme la légitime. Les routes qui lisent ce cookie exigent l'en-tête X-Requested-With : un formulaire d'un autre
site ne peut pas l'ajouter, ce qui ferme la porte au CSRF.
"""

import re
import secrets
import unicodedata
import uuid
from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Cookie, Header, HTTPException, Request, Response
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from evalforge_api.core.database import SessionDep, utcnow
from evalforge_api.core.security import hash_refresh_token, new_refresh_token
from evalforge_api.core.tenancy import UserDep, unauthorized
from evalforge_api.modules.identity.models import RefreshToken, User
from evalforge_api.modules.workspaces.models import Workspace, WorkspaceMember

router = APIRouter(prefix="/auth", tags=["auth"])

REFRESH_COOKIE = "evalforge_refresh"
COOKIE_PATH = "/api/v1/auth"
CSRF_HEADER_VALUE = "EvalForge"


class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=10, max_length=200)
    workspace_name: str = Field(min_length=1, max_length=200)


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(max_length=200)


class UserOut(BaseModel):
    id: uuid.UUID
    email: str


class SessionOut(BaseModel):
    access_token: str
    token_type: str = "bearer"  # noqa: S105 (type de jeton OAuth, pas un secret)
    expires_in: int
    user: UserOut


class MembershipOut(BaseModel):
    workspace_id: uuid.UUID
    name: str
    slug: str
    role: str


class MeOut(BaseModel):
    user: UserOut
    workspaces: list[MembershipOut]


def slugify(name: str) -> str:
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    base = re.sub(r"[^a-z0-9]+", "-", ascii_name).strip("-")[:40] or "espace"
    return f"{base}-{secrets.token_hex(3)}"


async def create_workspace_for(session: AsyncSession, user_id: uuid.UUID, name: str) -> Workspace:
    workspace = Workspace(name=name, slug=slugify(name))
    session.add(workspace)
    await session.flush()
    session.add(WorkspaceMember(workspace_id=workspace.id, user_id=user_id, role="owner"))
    return workspace


async def open_session(
    request: Request, response: Response, session: AsyncSession, user: User, family_id: uuid.UUID | None = None
) -> tuple[SessionOut, RefreshToken]:
    settings = request.app.state.settings
    raw, digest = new_refresh_token()
    token = RefreshToken(
        id=uuid.uuid4(),
        user_id=user.id,
        family_id=family_id or uuid.uuid4(),
        token_hash=digest,
        expires_at=utcnow() + timedelta(days=settings.refresh_token_days),
    )
    session.add(token)
    response.set_cookie(
        REFRESH_COOKIE,
        raw,
        max_age=settings.refresh_token_days * 86400,
        path=COOKIE_PATH,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="strict",
    )
    tokens = request.app.state.access_tokens
    out = SessionOut(
        access_token=tokens.issue(user.id),
        expires_in=int(tokens.lifetime.total_seconds()),
        user=UserOut(id=user.id, email=user.email),
    )
    return out, token


def clear_cookie(response: Response) -> None:
    response.delete_cookie(REFRESH_COOKIE, path=COOKIE_PATH)


def require_csrf_header(value: str | None) -> None:
    if value != CSRF_HEADER_VALUE:
        raise HTTPException(403, "En-tête X-Requested-With manquant")


@router.post("/register", status_code=201, response_model=SessionOut)
async def register(body: RegisterIn, request: Request, response: Response, session: SessionDep) -> SessionOut:
    user = User(email=body.email.lower(), password_hash=request.app.state.passwords.hash(body.password))
    session.add(user)
    try:
        await session.flush()
    except IntegrityError:
        raise HTTPException(409, "Un compte existe déjà avec cet e-mail") from None
    await create_workspace_for(session, user.id, body.workspace_name)
    out, _ = await open_session(request, response, session, user)
    await session.commit()
    return out


@router.post("/login", response_model=SessionOut)
async def login(body: LoginIn, request: Request, response: Response, session: SessionDep) -> SessionOut:
    user = await session.scalar(select(User).where(User.email == body.email.lower()))
    if not request.app.state.passwords.verify(user.password_hash if user else None, body.password):
        raise unauthorized("E-mail ou mot de passe incorrect")
    out, _ = await open_session(request, response, session, user)
    await session.commit()
    return out


@router.post("/refresh", response_model=SessionOut)
async def refresh(
    request: Request,
    response: Response,
    session: SessionDep,
    x_requested_with: Annotated[str | None, Header()] = None,
    evalforge_refresh: Annotated[str | None, Cookie()] = None,
) -> SessionOut:
    require_csrf_header(x_requested_with)
    if not evalforge_refresh:
        raise unauthorized("Session absente")
    token = await session.scalar(
        select(RefreshToken).where(RefreshToken.token_hash == hash_refresh_token(evalforge_refresh)).with_for_update()
    )
    now = utcnow()
    if token is None or token.expires_at <= now:
        clear_cookie(response)
        raise unauthorized("Session expirée")
    if token.revoked_at is not None:
        # Jeton déjà utilisé : quelqu'un d'autre l'a. Toute la famille tombe, l'utilisateur se reconnecte.
        await session.execute(
            update(RefreshToken)
            .where(RefreshToken.family_id == token.family_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=now)
        )
        await session.commit()
        clear_cookie(response)
        raise unauthorized("Session révoquée")

    user = await session.get(User, token.user_id)
    out, replacement = await open_session(request, response, session, user, family_id=token.family_id)
    token.revoked_at = now
    token.replaced_by_id = replacement.id
    await session.commit()
    return out


@router.post("/logout", status_code=204)
async def logout(
    response: Response,
    session: SessionDep,
    x_requested_with: Annotated[str | None, Header()] = None,
    evalforge_refresh: Annotated[str | None, Cookie()] = None,
) -> None:
    require_csrf_header(x_requested_with)
    if evalforge_refresh:
        family = await session.scalar(
            select(RefreshToken.family_id).where(RefreshToken.token_hash == hash_refresh_token(evalforge_refresh))
        )
        if family is not None:
            await session.execute(
                update(RefreshToken)
                .where(RefreshToken.family_id == family, RefreshToken.revoked_at.is_(None))
                .values(revoked_at=utcnow())
            )
            await session.commit()
    clear_cookie(response)


@router.get("/me", response_model=MeOut)
async def me(user_id: UserDep, session: SessionDep) -> MeOut:
    user = await session.get(User, user_id)
    if user is None:
        raise unauthorized("Compte introuvable")
    rows = await session.execute(
        select(Workspace, WorkspaceMember.role)
        .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
        .where(WorkspaceMember.user_id == user_id)
        .order_by(Workspace.name)
    )
    return MeOut(
        user=UserOut(id=user.id, email=user.email),
        workspaces=[MembershipOut(workspace_id=w.id, name=w.name, slug=w.slug, role=role) for w, role in rows],
    )
