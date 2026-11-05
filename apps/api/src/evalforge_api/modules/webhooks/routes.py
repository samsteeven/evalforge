"""Abonnements webhooks d'un projet et historique de leurs livraisons. Réservé aux administrateurs : l'URL peut
porter un jeton du destinataire, et le secret de signature n'est montré qu'une fois."""

import secrets
import uuid
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, Request, Response
from pydantic import AnyHttpUrl, BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from evalforge_api.core.database import SessionDep
from evalforge_api.core.tenancy import AccessDep, AdminDep, not_found
from evalforge_api.messaging.events import EVALUATION_COMPLETED, EVALUATION_FAILED
from evalforge_api.modules.projects.routes import get_project
from evalforge_api.modules.webhooks.models import Webhook, WebhookDelivery
from evalforge_llm.network import UnsafeDestinationError, check_url

router = APIRouter(tags=["webhooks"])

EventName = Literal["evaluation.completed", "evaluation.failed"]


class WebhookCreate(BaseModel):
    url: AnyHttpUrl
    events: list[EventName] = Field(default=[EVALUATION_COMPLETED, EVALUATION_FAILED], min_length=1)


class WebhookOut(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    url: str
    events: list[str]
    active: bool
    created_at: datetime


class WebhookCreated(WebhookOut):
    # Affiché une seule fois : le destinataire s'en sert pour vérifier X-EvalForge-Signature.
    secret: str


class DeliveryOut(BaseModel):
    id: uuid.UUID
    event_id: uuid.UUID
    event_type: str
    run_id: uuid.UUID | None
    status: str
    attempts: int
    response_status: int | None
    last_error: str | None
    created_at: datetime
    updated_at: datetime
    delivered_at: datetime | None


def to_out(hook: Webhook) -> WebhookOut:
    return WebhookOut(
        id=hook.id,
        project_id=hook.project_id,
        url=hook.url,
        events=hook.events,
        active=hook.active,
        created_at=hook.created_at,
    )


async def get_webhook(session: AsyncSession, workspace_id: uuid.UUID, webhook_id: uuid.UUID) -> Webhook:
    hook = await session.scalar(select(Webhook).where(Webhook.id == webhook_id, Webhook.workspace_id == workspace_id))
    if hook is None:
        raise not_found("Webhook")
    return hook


@router.post("/projects/{project_id}/webhooks", status_code=201, response_model=WebhookCreated)
async def create_webhook(
    project_id: uuid.UUID, body: WebhookCreate, request: Request, access: AccessDep, session: SessionDep
) -> WebhookCreated:
    if not access.at_least("admin"):
        raise HTTPException(403, "Rôle insuffisant : admin au minimum")
    await get_project(session, access.workspace_id, project_id)
    try:
        check_url(str(body.url), allow_private=request.app.state.settings.allow_private_webhook_urls)
    except UnsafeDestinationError as error:
        raise HTTPException(422, f"Adresse de webhook refusée : {error}") from None
    secret = f"whsec_{secrets.token_urlsafe(32)}"
    hook = Webhook(
        workspace_id=access.workspace_id,
        project_id=project_id,
        url=str(body.url),
        events=list(dict.fromkeys(body.events)),
        secret_encrypted=request.app.state.secret_box.encrypt(secret),
        active=True,
        created_by=access.user_id,
    )
    session.add(hook)
    await session.commit()
    return WebhookCreated(**to_out(hook).model_dump(), secret=secret)


@router.get("/projects/{project_id}/webhooks", response_model=list[WebhookOut])
async def list_webhooks(project_id: uuid.UUID, workspace_id: AdminDep, session: SessionDep) -> list[WebhookOut]:
    await get_project(session, workspace_id, project_id)
    hooks = await session.scalars(select(Webhook).where(Webhook.project_id == project_id).order_by(Webhook.created_at))
    return [to_out(hook) for hook in hooks]


@router.delete("/webhooks/{webhook_id}", status_code=204)
async def delete_webhook(webhook_id: uuid.UUID, workspace_id: AdminDep, session: SessionDep) -> Response:
    await session.delete(await get_webhook(session, workspace_id, webhook_id))
    await session.commit()
    return Response(status_code=204)


@router.get("/webhooks/{webhook_id}/deliveries", response_model=list[DeliveryOut])
async def list_deliveries(
    webhook_id: uuid.UUID,
    workspace_id: AdminDep,
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[DeliveryOut]:
    await get_webhook(session, workspace_id, webhook_id)
    deliveries = await session.scalars(
        select(WebhookDelivery)
        .where(WebhookDelivery.webhook_id == webhook_id)
        .order_by(WebhookDelivery.created_at.desc())
        .limit(limit)
    )
    return [DeliveryOut.model_validate(delivery, from_attributes=True) for delivery in deliveries]
