"""Worker d'évaluation : consomme evaluation.requested et exécute les runs.

    uv run evalforge-worker

Garanties :
- message acquitté seulement quand le run est terminé et enregistré (ou la nouvelle tentative publiée) ;
- message déjà traité ou run déjà terminé : acquitté sans rien refaire ;
- run tenu par un autre worker vivant : remis en attente, revérifié plus tard ;
- worker arrêté en plein run : RabbitMQ relivre le message, le bail expire, un autre worker reprend les réponses
  manquantes ;
- erreur : nouvelle tentative avec un délai croissant, puis file morte et run en échec.
"""

import asyncio
import contextlib
import logging
import os
import socket
import uuid
from datetime import timedelta

import aio_pika
import httpx
from aio_pika.abc import AbstractChannel, AbstractIncomingMessage, AbstractRobustConnection
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from evalforge_api.core.config import Settings
from evalforge_api.core.secrets import SecretBox
from evalforge_api.messaging.events import EVALUATION_REQUESTED, Envelope, EvaluationRequested
from evalforge_api.messaging.outbox import OutboxPublisher
from evalforge_api.messaging.topology import Topology
from evalforge_api.modules.evaluations.models import RunStatus
from evalforge_api.modules.evaluations.runner import (
    Claim,
    already_processed,
    claim_run,
    execute_run,
    finish_run,
    heartbeat,
    release_run,
)
from evalforge_api.modules.providers.factory import ProviderFactory, http_provider_factory
from evalforge_llm.network import SafeTransport

log = logging.getLogger("evalforge.worker")

CONSUMER = "evaluation-worker"
ATTEMPT_HEADER = "x-evalforge-attempt"


class Worker:
    def __init__(
        self, settings: Settings, provider_factory: ProviderFactory | None = None, worker_id: str | None = None
    ) -> None:
        self.settings = settings
        self.worker_id = worker_id or f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:6]}"
        self.topology = Topology(settings.rabbitmq_prefix)
        self._provider_factory = provider_factory
        self._connection: AbstractRobustConnection | None = None
        self._channel: AbstractChannel | None = None
        self._in_flight: set[asyncio.Task] = set()

    async def start(self) -> None:
        settings = self.settings
        self._engine = create_async_engine(settings.database_url, pool_pre_ping=True)
        self.session_factory = async_sessionmaker(self._engine, expire_on_commit=False)
        self._http = httpx.AsyncClient(transport=SafeTransport(allow_private=settings.allow_private_provider_urls))
        self.provider_factory = self._provider_factory or http_provider_factory(
            self._http, timeout_seconds=settings.provider_timeout_seconds
        )
        self.secret_box = SecretBox(settings.secret_key.get_secret_value())
        # Les événements de fin écrits par le worker partent aussi sans attendre l'API.
        self.publisher = OutboxPublisher(
            self.session_factory,
            settings.rabbitmq_url,
            self.topology,
            interval_seconds=settings.outbox_interval_seconds,
        )
        await self.publisher.start()

        self._connection = await aio_pika.connect_robust(settings.rabbitmq_url)
        self._channel = await self._connection.channel(publisher_confirms=True)
        await self._channel.set_qos(prefetch_count=settings.worker_prefetch)
        await self.topology.declare(self._channel)
        queue = await self._channel.get_queue(self.topology.evaluations)
        self._consumer_tag = await queue.consume(self._on_message)
        log.info("Worker %s à l'écoute de %s", self.worker_id, self.topology.evaluations)

    async def stop(self) -> None:
        # Les runs en cours s'arrêtent là ; fermer le canal rend leurs messages non acquittés à RabbitMQ, et un
        # autre worker reprendra les réponses manquantes une fois le bail expiré.
        for task in list(self._in_flight):
            task.cancel()
        await asyncio.gather(*self._in_flight, return_exceptions=True)
        if self._connection and not self._connection.is_closed:
            await self._connection.close()
        await self.publisher.stop()
        await self._http.aclose()
        await self._engine.dispose()
        self._stopped = True

    async def stop_quietly(self) -> None:
        """Arrêt idempotent (tests) : un worker déjà arrêté n'est pas arrêté deux fois."""
        if not getattr(self, "_stopped", False):
            await self.stop()

    async def _on_message(self, message: AbstractIncomingMessage) -> None:
        task = asyncio.current_task()
        self._in_flight.add(task)
        try:
            await self._receive(message)
        finally:
            self._in_flight.discard(task)

    async def _receive(self, message: AbstractIncomingMessage) -> None:
        try:
            envelope = Envelope.model_validate_json(message.body)
            if envelope.event_type != EVALUATION_REQUESTED or envelope.schema_version != 1:
                raise ValueError(f"événement non pris en charge : {envelope.event_type} v{envelope.schema_version}")
            request = EvaluationRequested.model_validate(envelope.payload)
        except (ValidationError, ValueError) as error:
            # Un message illisible ne deviendra pas lisible : directement en file morte.
            await self._publish_to(self.topology.dead, message, attempt=1, error=f"message invalide : {error}")
            await message.ack()
            return

        attempt = int((message.headers or {}).get(ATTEMPT_HEADER, 1))
        await self._handle(message, envelope, request, attempt)

    async def _handle(
        self, message: AbstractIncomingMessage, envelope: Envelope, request: EvaluationRequested, attempt: int
    ) -> None:
        run_id = request.run_id
        if await already_processed(self.session_factory, envelope.event_id, CONSUMER):
            await message.ack()
            return

        lease = timedelta(seconds=self.settings.run_lease_seconds)
        claim = await claim_run(self.session_factory, run_id, self.worker_id, lease)
        if claim in (Claim.DONE, Claim.MISSING):
            log.info("Run %s : %s, message acquitté sans traitement", run_id, claim.value)
            await message.ack()
            return
        if claim is Claim.BUSY:
            # Un autre worker le tient : on revérifiera après un bail, sans compter de tentative.
            await self._publish_to(self.topology.retry, message, attempt=attempt, delay=self.settings.run_lease_seconds)
            await message.ack()
            return

        log.info("Run %s pris par %s (tentative %d)", run_id, self.worker_id, attempt)
        try:
            async with heartbeat(self.session_factory, run_id, self.worker_id, self.settings.run_lease_seconds / 4):
                await execute_run(
                    run_id,
                    session_factory=self.session_factory,
                    provider_factory=self.provider_factory,
                    secret_box=self.secret_box,
                )
        except Exception as error:
            reason = f"{type(error).__name__} : {error}"
            log.exception("Run %s en échec (tentative %d/%d)", run_id, attempt, self.settings.max_attempts)
            if attempt >= self.settings.max_attempts:
                await finish_run(
                    self.session_factory,
                    run_id,
                    status=RunStatus.FAILED,
                    error=f"{reason} (après {attempt} tentatives)",
                    message_id=envelope.event_id,
                    consumer=CONSUMER,
                )
                await self._publish_to(self.topology.dead, message, attempt=attempt, error=reason)
            else:
                await release_run(self.session_factory, run_id, self.worker_id, f"tentative {attempt} : {reason}")
                delay = self.settings.retry_base_delay_seconds * 2 ** (attempt - 1)
                await self._publish_to(self.topology.retry, message, attempt=attempt + 1, delay=delay, error=reason)
            await message.ack()
            return

        await finish_run(
            self.session_factory,
            run_id,
            status=RunStatus.COMPLETED,
            error=None,
            message_id=envelope.event_id,
            consumer=CONSUMER,
        )
        await message.ack()
        log.info("Run %s terminé", run_id)

    async def _publish_to(
        self,
        queue: str,
        original: AbstractIncomingMessage,
        *,
        attempt: int,
        delay: float | None = None,
        error: str | None = None,
    ) -> None:
        """Republie par l'échange par défaut ; le canal à confirmations attend l'accusé avant l'acquittement."""
        headers = {**(original.headers or {}), ATTEMPT_HEADER: attempt}
        if error:
            headers["x-evalforge-last-error"] = error[:500]
        await self._channel.default_exchange.publish(
            aio_pika.Message(
                body=original.body,
                content_type=original.content_type,
                delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                message_id=original.message_id,
                correlation_id=original.correlation_id,
                type=original.type,
                headers=headers,
                expiration=timedelta(seconds=delay) if delay else None,
            ),
            routing_key=queue,
        )


async def run_forever() -> None:
    worker = Worker(Settings())
    await worker.start()
    try:
        await asyncio.Event().wait()
    finally:
        await worker.stop()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s : %(message)s")
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(run_forever())


if __name__ == "__main__":
    main()
