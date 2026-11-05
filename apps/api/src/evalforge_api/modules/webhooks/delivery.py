"""Livraison des webhooks : charge utile, signature HMAC, envoi, issue de chaque tentative.

Le destinataire vérifie l'authenticité et la fraîcheur d'un appel :
    X-EvalForge-Timestamp : secondes Unix de l'envoi
    X-EvalForge-Signature : sha256=<HMAC-SHA256(secret, "<timestamp>.<corps brut>") en hexadécimal>
Une signature valide mais trop ancienne (rejeu) se refuse : voir verify().

Réponse 2xx : livré. 4xx (sauf 408 et 429) : refus définitif, inutile d'insister. 5xx, 408, 429, délai dépassé ou
réseau : nouvelle tentative par la file de reprise du worker, avec un délai croissant.
"""

import hashlib
import hmac
import json
import time
import uuid
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from evalforge_api.core.database import utcnow
from evalforge_api.core.secrets import SecretBox
from evalforge_api.messaging.events import EVALUATION_COMPLETED, EVALUATION_FAILED, Envelope
from evalforge_api.modules.evaluations.baselines import ProjectBaseline
from evalforge_api.modules.evaluations.models import EvaluationRun, RunStatus
from evalforge_api.modules.evaluations.routes import _model_names, _records
from evalforge_api.modules.experiments.models import Experiment
from evalforge_api.modules.webhooks.models import Webhook, WebhookDelivery
from evalforge_engine.comparison import compare
from evalforge_engine.scoring import ResultRecord, summarize_by_model
from evalforge_llm.network import UnsafeDestinationError

WEBHOOK_EVENTS = (EVALUATION_COMPLETED, EVALUATION_FAILED)
SIGNATURE_HEADER = "X-EvalForge-Signature"
TIMESTAMP_HEADER = "X-EvalForge-Timestamp"
EVENT_HEADER = "X-EvalForge-Event"
DELIVERY_HEADER = "X-EvalForge-Delivery"
RETRYABLE_STATUSES = {408, 429}


def sign(secret: str, timestamp: int, body: bytes) -> str:
    digest = hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def verify(
    secret: str, timestamp: str, body: bytes, signature: str, *, now: float | None = None, max_age_seconds: int = 300
) -> bool:
    """Côté destinataire : signature exacte (comparaison à temps constant) et envoi récent."""
    try:
        sent = int(timestamp)
    except ValueError:
        return False
    if abs((now if now is not None else time.time()) - sent) > max_age_seconds:
        return False
    return hmac.compare_digest(sign(secret, sent, body), signature)


async def _regression(
    session: AsyncSession, project_id: uuid.UUID, run: EvaluationRun, records: list[ResultRecord]
) -> dict[str, Any] | None:
    """Comparaison à la baseline du projet, si elle existe et porte sur le même jeu de données."""
    stored = await session.get(ProjectBaseline, project_id)
    if stored is None or stored.run_id == run.id or run.status != RunStatus.COMPLETED:
        return None
    baseline = await session.get(EvaluationRun, stored.run_id)
    if baseline.configuration["dataset_version"]["checksum"] != run.configuration["dataset_version"]["checksum"]:
        return {"baseline_run_id": str(baseline.id), "comparable": False, "reason": "jeux de données différents"}
    metrics = [name for name in run.configuration["metrics"] if name in baseline.configuration["metrics"]]
    comparison = compare(await _records(session, baseline), records, metrics, tolerance=stored.tolerance)
    return {
        "baseline_run_id": str(baseline.id),
        "comparable": True,
        "tolerance": stored.tolerance,
        "regressed": comparison.regressed,
        "regressions": [
            {
                "model": model.model,
                "metric": metric.name,
                "baseline_rate": metric.baseline_rate,
                "candidate_rate": metric.candidate_rate,
                "broken": metric.broken,
                "p_value": metric.p_value,
            }
            for model in comparison.models
            for metric in model.metrics
            if metric.regressed
        ],
    }


async def build_payload(session: AsyncSession, envelope: Envelope) -> tuple[uuid.UUID, dict[str, Any]] | None:
    """Résumé du run tel qu'en base : taux de réussite par modèle et régression face à la baseline."""
    run = await session.get(EvaluationRun, uuid.UUID(envelope.payload["run_id"]))
    if run is None:
        return None
    experiment = await session.get(Experiment, run.experiment_id)
    records = await _records(session, run)
    reports = summarize_by_model(list(_model_names(run).values()), run.configuration["metrics"], records)
    return experiment.project_id, {
        "event_id": str(envelope.event_id),
        "event_type": envelope.event_type,
        "occurred_at": envelope.occurred_at.isoformat(),
        "run": {
            "id": str(run.id),
            "experiment_id": str(run.experiment_id),
            "experiment_name": experiment.name,
            "project_id": str(experiment.project_id),
            "status": run.status,
            "completed_items": run.completed_items,
            "total_items": run.total_items,
            "error": run.error,
        },
        "models": [
            {
                "model": report.model,
                "success_rate": report.performance.success_rate,
                "pass_rates": {metric.name: metric.pass_rate for metric in report.metrics},
            }
            for report in reports
        ],
        "regression": await _regression(session, experiment.project_id, run, records),
    }


async def _send(
    client: httpx.AsyncClient, url: str, secret: str, envelope: Envelope, delivery_id: uuid.UUID, body: bytes
) -> tuple[int | None, str | None]:
    timestamp = int(time.time())
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "EvalForge-Webhooks/1",
        EVENT_HEADER: envelope.event_type,
        DELIVERY_HEADER: str(delivery_id),
        TIMESTAMP_HEADER: str(timestamp),
        SIGNATURE_HEADER: sign(secret, timestamp, body),
    }
    try:
        # Pas de redirection suivie : une redirection pourrait mener vers une adresse interne.
        response = await client.post(url, content=body, headers=headers, follow_redirects=False)
    except UnsafeDestinationError as error:
        return None, f"adresse refusée : {error}"
    except httpx.TimeoutException:
        return None, "délai dépassé"
    except httpx.HTTPError as error:
        return None, f"destinataire injoignable ({type(error).__name__})"
    return response.status_code, None if response.is_success else f"HTTP {response.status_code}"


def _permanent(status: int | None, error: str | None) -> bool:
    if error is not None and error.startswith("adresse refusée"):
        return True
    return status is not None and 400 <= status < 500 and status not in RETRYABLE_STATUSES


async def dispatch(
    session_factory: async_sessionmaker[AsyncSession],
    envelope: Envelope,
    *,
    client: httpx.AsyncClient,
    secret_box: SecretBox,
    final_attempt: bool,
) -> int:
    """Envoie l'événement aux abonnements du projet encore à servir. Renvoie le nombre de livraisons à retenter."""
    async with session_factory() as session:
        built = await build_payload(session, envelope)
        if built is None:
            return 0
        project_id, payload = built
        hooks = [
            hook
            for hook in await session.scalars(
                select(Webhook).where(Webhook.project_id == project_id, Webhook.active.is_(True))
            )
            if envelope.event_type in hook.events
        ]
        if not hooks:
            return 0
        await session.execute(
            insert(WebhookDelivery)
            .values(
                [
                    {
                        "id": uuid.uuid4(),
                        "webhook_id": hook.id,
                        "event_id": envelope.event_id,
                        "event_type": envelope.event_type,
                        "run_id": uuid.UUID(payload["run"]["id"]),
                        "status": "pending",
                        "attempts": 0,
                        "created_at": utcnow(),
                        "updated_at": utcnow(),
                    }
                    for hook in hooks
                ]
            )
            .on_conflict_do_nothing(index_elements=["webhook_id", "event_id"])
        )
        await session.commit()
        deliveries = {
            delivery.webhook_id: delivery
            for delivery in await session.scalars(
                select(WebhookDelivery).where(
                    WebhookDelivery.event_id == envelope.event_id,
                    WebhookDelivery.webhook_id.in_([hook.id for hook in hooks]),
                )
            )
        }

        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
        pending = 0
        for hook in hooks:
            delivery = deliveries[hook.id]
            if delivery.status != "pending":  # déjà livré, ou refusé définitivement : rien à refaire
                continue
            status, error = await _send(
                client, hook.url, secret_box.decrypt(hook.secret_encrypted), envelope, delivery.id, body
            )
            delivery.attempts += 1
            delivery.response_status = status
            delivery.last_error = error
            delivery.updated_at = utcnow()
            if error is None:
                delivery.status = "delivered"
                delivery.delivered_at = delivery.updated_at
            elif _permanent(status, error):
                delivery.status = "failed"
            elif final_attempt:
                delivery.status = "failed"
                delivery.last_error = f"{error} (tentatives épuisées)"
            else:
                pending += 1
            await session.commit()
        return pending
