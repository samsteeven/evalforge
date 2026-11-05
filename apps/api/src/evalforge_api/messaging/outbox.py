"""Outbox transactionnelle : l'événement est écrit avec le changement, puis publié avec confirmation du courtier."""

import asyncio
import contextlib
import logging
from typing import Any

import aio_pika
from aio_pika.abc import AbstractConnection
from opentelemetry.propagate import inject
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from evalforge_api.core.database import utcnow
from evalforge_api.messaging.events import Envelope
from evalforge_api.messaging.models import OutboxEvent
from evalforge_api.messaging.topology import Topology

log = logging.getLogger(__name__)


def enqueue(session: AsyncSession, event_type: str, payload: dict[str, Any], correlation_id: str) -> Envelope:
    """Ajoute l'événement à la transaction en cours : il n'existe que si le changement est validé."""
    carrier: dict[str, str] = {}
    inject(carrier)
    envelope = Envelope(event_type=event_type, correlation_id=correlation_id, payload=payload, trace_context=carrier)
    session.add(
        OutboxEvent(
            id=envelope.event_id,
            event_type=event_type,
            correlation_id=correlation_id,
            envelope=envelope.model_dump(mode="json"),
        )
    )
    return envelope


def to_message(envelope: dict[str, Any], *, headers: dict[str, Any] | None = None) -> aio_pika.Message:
    return aio_pika.Message(
        body=Envelope.model_validate(envelope).model_dump_json().encode(),
        content_type="application/json",
        delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
        message_id=str(envelope["event_id"]),
        correlation_id=envelope["correlation_id"],
        type=envelope["event_type"],
        headers=headers or {},
    )


class OutboxPublisher:
    """Publie les événements en attente. Plusieurs instances peuvent tourner : SKIP LOCKED les répartit."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        rabbitmq_url: str,
        topology: Topology,
        *,
        interval_seconds: float = 2.0,
        batch_size: int = 50,
        connect_timeout_seconds: float = 10.0,
    ) -> None:
        self._connect_timeout = connect_timeout_seconds
        self._session_factory = session_factory
        self._url = rabbitmq_url
        self._topology = topology
        self._interval = interval_seconds
        self._batch = batch_size
        self._wake = asyncio.Event()
        self._connection: AbstractConnection | None = None
        self._task: asyncio.Task[None] | None = None

    def wake(self) -> None:
        """Après un enregistrement : publier tout de suite plutôt qu'au prochain tour."""
        self._wake.set()

    async def start(self) -> None:
        self._task = asyncio.create_task(self._loop(), name="outbox-publisher")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
        if self._connection and not self._connection.is_closed:
            await self._connection.close()

    async def _loop(self) -> None:
        while True:
            # Remis à zéro avant de publier : un réveil demandé pendant la publication n'est pas perdu.
            self._wake.clear()
            try:
                while await self.publish_pending():
                    pass  # un lot publié : il en reste peut-être
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Publication de l'outbox en échec, nouvel essai au prochain tour")
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._wake.wait(), self._interval)

    async def _exchange(self):
        # Connexion simple, sans reconnexion automatique : le publieur réessaie de lui-même au tour suivant, et un
        # arrêt pendant une tentative ne laisse aucune boucle de reconnexion orpheline.
        if self._connection is None or self._connection.is_closed:
            self._connection = await asyncio.wait_for(aio_pika.connect(self._url), self._connect_timeout)
            self._channel = await self._connection.channel(publisher_confirms=True)
            self._exchange_obj = await self._topology.declare(self._channel)
        return self._exchange_obj

    async def publish_pending(self) -> int:
        """Publie un lot ; renvoie le nombre d'événements publiés. Un échec laisse l'événement en attente."""
        async with self._session_factory() as session:
            events = list(
                await session.scalars(
                    select(OutboxEvent)
                    .where(OutboxEvent.published_at.is_(None))
                    .order_by(OutboxEvent.occurred_at)
                    .limit(self._batch)
                    .with_for_update(skip_locked=True)
                )
            )
            if not events:
                return 0
            published = 0
            try:
                exchange = await self._exchange()
                for event in events:
                    # Avec publisher_confirms, publish() attend l'accusé du courtier : publié = reçu.
                    await exchange.publish(to_message(event.envelope), routing_key=event.event_type)
                    event.published_at = utcnow()
                    published += 1
            except Exception as error:
                for event in events[published:]:
                    event.attempts += 1
                    event.last_error = f"{type(error).__name__} : {error}"[:500]
                await session.commit()
                raise
            await session.commit()
            return published


async def pending_count(session: AsyncSession) -> int:
    rows = await session.scalars(select(OutboxEvent.id).where(OutboxEvent.published_at.is_(None)))
    return len(list(rows))
