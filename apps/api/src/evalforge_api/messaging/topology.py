"""Topologie RabbitMQ, déclarée de la même façon par l'API (publication) et le worker (consommation).

<préfixe>.events                    échange topic, durable
<préfixe>.evaluations               file quorum liée à evaluation.requested ; consommée par les workers
<préfixe>.evaluations.retry         file d'attente sans consommateur : chaque message y patiente son délai
                                    (expiration par message) puis revient dans <préfixe>.evaluations
<préfixe>.evaluations.dead          file morte : messages abandonnés après le nombre maximal de tentatives
"""

from dataclasses import dataclass

import aio_pika
from aio_pika.abc import AbstractChannel, AbstractExchange

from evalforge_api.messaging.events import EVALUATION_REQUESTED


@dataclass(frozen=True)
class Topology:
    prefix: str

    @property
    def exchange(self) -> str:
        return f"{self.prefix}.events"

    @property
    def evaluations(self) -> str:
        return f"{self.prefix}.evaluations"

    @property
    def retry(self) -> str:
        return f"{self.prefix}.evaluations.retry"

    @property
    def dead(self) -> str:
        return f"{self.prefix}.evaluations.dead"

    async def declare(self, channel: AbstractChannel) -> AbstractExchange:
        exchange = await channel.declare_exchange(self.exchange, aio_pika.ExchangeType.TOPIC, durable=True)
        quorum = {"x-queue-type": "quorum"}
        evaluations = await channel.declare_queue(
            self.evaluations,
            durable=True,
            # Un run long ne doit pas être repris par RabbitMQ parce que son acquittement tarde (30 min par défaut).
            arguments={**quorum, "x-consumer-timeout": 6 * 3600 * 1000},
        )
        await evaluations.bind(exchange, EVALUATION_REQUESTED)
        await channel.declare_queue(
            self.retry,
            durable=True,
            arguments={**quorum, "x-dead-letter-exchange": "", "x-dead-letter-routing-key": self.evaluations},
        )
        await channel.declare_queue(self.dead, durable=True, arguments=quorum)
        return exchange
