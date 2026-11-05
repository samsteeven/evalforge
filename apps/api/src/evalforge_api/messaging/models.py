import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from evalforge_api.core.database import Base, utcnow


class OutboxEvent(Base):
    """Événement écrit dans la même transaction que le changement qu'il annonce, publié ensuite.

    Si l'API s'arrête entre l'enregistrement et la publication, l'événement attend ici : il n'est jamais perdu.
    """

    __tablename__ = "outbox_events"
    __table_args__ = (Index("ix_outbox_events_unpublished", "occurred_at", postgresql_where="published_at IS NULL"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)  # = event_id de l'enveloppe
    event_type: Mapped[str] = mapped_column(String(100))
    correlation_id: Mapped[str] = mapped_column(String(100))
    envelope: Mapped[dict[str, Any]] = mapped_column(JSONB)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)


class ProcessedMessage(Base):
    """Message déjà traité par un consommateur : RabbitMQ livre « au moins une fois », c'est ici qu'on dédoublonne."""

    __tablename__ = "processed_messages"

    message_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    consumer: Mapped[str] = mapped_column(String(100), primary_key=True)
    processed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
