import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from evalforge_api.core.database import Base, CreatedAtMixin, IdMixin, utcnow


class Webhook(IdMixin, CreatedAtMixin, Base):
    """Abonnement d'une URL externe aux fins de run d'un projet (CI, messagerie d'équipe, outil interne)."""

    __tablename__ = "webhooks"

    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ai_projects.id", ondelete="CASCADE"), index=True)
    url: Mapped[str] = mapped_column(String(500))
    events: Mapped[list[str]] = mapped_column(JSONB)
    # Secret de signature HMAC, chiffré comme les clés d'API : montré une seule fois, à la création.
    secret_encrypted: Mapped[str] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))


class WebhookDelivery(IdMixin, CreatedAtMixin, Base):
    """Une livraison par abonnement et par événement : la clé unique empêche un double envoi réussi."""

    __tablename__ = "webhook_deliveries"
    __table_args__ = (UniqueConstraint("webhook_id", "event_id"),)

    webhook_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("webhooks.id", ondelete="CASCADE"), index=True)
    event_id: Mapped[uuid.UUID]
    event_type: Mapped[str] = mapped_column(String(100))
    run_id: Mapped[uuid.UUID | None]
    # pending : à (re)tenter ; delivered : réponse 2xx ; failed : refus définitif (4xx) ou tentatives épuisées.
    status: Mapped[str] = mapped_column(String(20), default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    response_status: Mapped[int | None] = mapped_column(Integer)
    last_error: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
