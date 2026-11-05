"""Exécution d'un run par un worker : prise avec bail, reprise sans doublon, fin atomique avec son événement.

Cycle d'un run :
    queued ──claim──► running (bail renouvelé par battements) ──► completed | failed
                         │ worker arrêté : bail expiré, message relivré par RabbitMQ
                         └──claim──► running chez un autre worker, qui reprend les réponses manquantes
"""

import asyncio
import contextlib
import logging
import uuid
from collections.abc import AsyncIterator
from datetime import timedelta
from enum import StrEnum
from typing import Any

import httpx
from sqlalchemy import and_, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from evalforge_api.core.database import utcnow
from evalforge_api.core.secrets import SecretBox
from evalforge_api.messaging.events import EVALUATION_COMPLETED, EVALUATION_FAILED
from evalforge_api.messaging.models import ProcessedMessage
from evalforge_api.messaging.outbox import enqueue
from evalforge_api.modules.datasets.models import TestCase
from evalforge_api.modules.evaluations.models import EvaluationResult, EvaluationRun, MetricResult, RunStatus
from evalforge_api.modules.providers.factory import AGENT_KIND, ProviderFactory
from evalforge_api.modules.providers.models import ModelConfiguration
from evalforge_engine import CaseOutcome, EvaluationCase, WorkItem, evaluate_cases
from evalforge_engine.evaluators import JudgeModel, build_evaluators
from evalforge_engine.scoring import cost_usd
from evalforge_engine.targets import agent_target, llm_target
from evalforge_llm import LLMProvider

log = logging.getLogger(__name__)

SessionFactory = async_sessionmaker[AsyncSession]


class Claim(StrEnum):
    CLAIMED = "claimed"  # ce worker exécute le run
    BUSY = "busy"  # un autre worker vivant l'exécute
    DONE = "done"  # terminé ou en échec : rien à faire
    MISSING = "missing"


def to_engine_case(case: TestCase) -> EvaluationCase:
    return EvaluationCase(
        id=case.external_id,
        type=case.type,
        input=case.input,
        expected=case.expected,
        context=case.context,
        metadata=case.metadata_,
    )


async def claim_run(session_factory: SessionFactory, run_id: uuid.UUID, worker_id: str, lease: timedelta) -> Claim:
    """Prend le run s'il est en file, ou si le bail de son worker a expiré. Une seule requête : pas de course."""
    now = utcnow()
    async with session_factory() as session:
        claimed = await session.scalar(
            update(EvaluationRun)
            .where(
                EvaluationRun.id == run_id,
                or_(
                    EvaluationRun.status == RunStatus.QUEUED,
                    and_(EvaluationRun.status == RunStatus.RUNNING, EvaluationRun.heartbeat_at < now - lease),
                ),
            )
            .values(
                status=RunStatus.RUNNING,
                worker_id=worker_id,
                heartbeat_at=now,
                attempts=EvaluationRun.attempts + 1,
            )
            .returning(EvaluationRun.id)
        )
        if claimed is not None:
            # Date de premier démarrage conservée lors d'une reprise.
            await session.execute(
                update(EvaluationRun)
                .where(EvaluationRun.id == run_id, EvaluationRun.started_at.is_(None))
                .values(started_at=now)
            )
            await session.commit()
            return Claim.CLAIMED
        status = await session.scalar(select(EvaluationRun.status).where(EvaluationRun.id == run_id))
    if status is None:
        return Claim.MISSING
    return Claim.BUSY if status == RunStatus.RUNNING else Claim.DONE


@contextlib.asynccontextmanager
async def heartbeat(
    session_factory: SessionFactory, run_id: uuid.UUID, worker_id: str, every_seconds: float
) -> AsyncIterator[None]:
    """Renouvelle le bail tant que le run s'exécute ; s'arrête avec lui."""

    async def beat() -> None:
        while True:
            await asyncio.sleep(every_seconds)
            async with session_factory() as session:
                await session.execute(
                    update(EvaluationRun)
                    .where(EvaluationRun.id == run_id, EvaluationRun.worker_id == worker_id)
                    .values(heartbeat_at=utcnow())
                )
                await session.commit()

    task = asyncio.create_task(beat(), name=f"heartbeat-{run_id}")
    try:
        yield
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def execute_run(
    run_id: uuid.UUID,
    *,
    session_factory: SessionFactory,
    provider_factory: ProviderFactory,
    secret_box: SecretBox,
    agent_client: httpx.AsyncClient,
) -> None:
    """Exécute les réponses qui manquent encore. Une erreur remonte : le worker décide de reprendre ou d'abandonner."""
    async with session_factory() as session:
        run = await session.get(EvaluationRun, run_id)
        configuration, execution = run.configuration, run.execution
        cases = list(
            await session.scalars(
                select(TestCase)
                .where(TestCase.dataset_version_id == run.dataset_version_id)
                .order_by(TestCase.position)
            )
        )
        # Reprise : ce qu'un worker précédent a déjà enregistré n'est ni recalculé ni refacturé.
        rows = await session.execute(
            select(EvaluationResult.test_case_id, EvaluationResult.model_configuration_id).where(
                EvaluationResult.run_id == run_id
            )
        )
        done = {(case_id, model_id) for case_id, model_id in rows}
        judge_config = configuration.get("judge")
        model_ids = [uuid.UUID(model["id"]) for model in configuration["models"]]
        if judge_config:
            model_ids.append(uuid.UUID(judge_config["id"]))
        rows = await session.execute(
            select(ModelConfiguration.id, ModelConfiguration.api_key_encrypted).where(
                ModelConfiguration.id.in_(model_ids)
            )
        )
        encrypted_keys = {model_id: encrypted for model_id, encrypted in rows}

    def provider_for(model: dict[str, Any]) -> LLMProvider:
        encrypted = encrypted_keys.get(uuid.UUID(model["id"]))
        return provider_factory(
            provider_kind=model["provider_kind"],
            base_url=model["base_url"],
            api_key=secret_box.decrypt(encrypted) if encrypted else None,
        )

    judge = (
        JudgeModel(provider_for(judge_config), judge_config["model_name"], judge_config["parameters"]["temperature"])
        if judge_config
        else None
    )
    items: list[WorkItem] = []
    pricing: dict[uuid.UUID, dict[str, Any] | None] = {}
    for model in configuration["models"]:
        model_id = uuid.UUID(model["id"])
        if model["provider_kind"] == AGENT_KIND:
            # Un agent reçoit le prompt rendu et renvoie sa réponse et sa trajectoire.
            target = agent_target(
                agent_client,
                base_url=model["base_url"],
                name=model["name"],
                template=configuration["prompt"]["template"],
            )
        else:
            target = llm_target(
                provider_for(model),
                model=model["model_name"],
                template=configuration["prompt"]["template"],
                system=configuration["prompt"]["system"],
                temperature=model["parameters"]["temperature"],
                max_tokens=model["parameters"]["max_tokens"],
            )
        pricing[model_id] = model["pricing"]
        items.extend(
            WorkItem((case.id, model_id), to_engine_case(case), target)
            for case in cases
            if (case.id, model_id) not in done
        )
    if done:
        log.info("Run %s repris : %d réponses déjà enregistrées, %d à produire", run_id, len(done), len(items))

    outcomes = evaluate_cases(
        items,
        build_evaluators(configuration["metrics"], judge),
        max_concurrency=execution["max_concurrency"],
        timeout_seconds=execution["timeout_seconds"],
    )
    async for outcome in outcomes:
        await _persist(session_factory, run_id, outcome, pricing)


async def _persist(
    session_factory: SessionFactory,
    run_id: uuid.UUID,
    outcome: CaseOutcome,
    pricing: dict[uuid.UUID, dict[str, Any] | None],
) -> None:
    test_case_id, model_id = outcome.key
    output = outcome.output
    price = pricing[model_id]
    cost = (
        cost_usd(
            output.input_tokens, output.output_tokens, price["input_per_million_usd"], price["output_per_million_usd"]
        )
        if output is not None and price
        else None
    )
    async with session_factory() as session:
        result_id = await session.scalar(
            insert(EvaluationResult)
            .values(
                run_id=run_id,
                test_case_id=test_case_id,
                model_configuration_id=model_id,
                status=outcome.status.value,
                actual_output=output.text if output else None,
                error=outcome.error,
                latency_ms=output.latency_ms if output else None,
                input_tokens=output.input_tokens if output else None,
                output_tokens=output.output_tokens if output else None,
                cost_usd=cost,
                provider_model=output.model if output else None,
                provider_request_id=output.request_id if output else None,
                trajectory=output.trajectory if output else None,
                stop_reason=output.stop_reason if output else None,
            )
            .on_conflict_do_nothing(index_elements=["run_id", "test_case_id", "model_configuration_id"])
            .returning(EvaluationResult.id)
        )
        if result_id is not None:  # None : résultat déjà enregistré, rien à refaire
            if outcome.metrics:
                await session.execute(
                    insert(MetricResult),
                    [
                        {
                            "result_id": result_id,
                            "name": metric.name,
                            "score": metric.score,
                            "passed": metric.passed,
                            "reason": metric.reason,
                            "details": metric.metadata,
                        }
                        for metric in outcome.metrics
                    ],
                )
            await session.execute(
                update(EvaluationRun)
                .where(EvaluationRun.id == run_id)
                .values(completed_items=EvaluationRun.completed_items + 1)
            )
        await session.commit()


async def finish_run(
    session_factory: SessionFactory,
    run_id: uuid.UUID,
    *,
    status: RunStatus,
    error: str | None,
    message_id: uuid.UUID,
    consumer: str,
) -> None:
    """Fin du run, message marqué traité et événement de fin : la même transaction, tout ou rien."""
    async with session_factory() as session:
        run = await session.get(EvaluationRun, run_id, with_for_update=True)
        run.status = status
        run.error = error
        run.finished_at = utcnow()
        run.heartbeat_at = None
        await session.execute(
            insert(ProcessedMessage).values(message_id=message_id, consumer=consumer).on_conflict_do_nothing()
        )
        enqueue(
            session,
            EVALUATION_COMPLETED if status == RunStatus.COMPLETED else EVALUATION_FAILED,
            {
                "run_id": str(run_id),
                "experiment_id": str(run.experiment_id),
                "workspace_id": str(run.workspace_id),
                "status": status.value,
                "completed_items": run.completed_items,
                "total_items": run.total_items,
                "error": error,
            },
            correlation_id=str(run_id),
        )
        await session.commit()


async def release_run(session_factory: SessionFactory, run_id: uuid.UUID, worker_id: str, error: str) -> None:
    """Avant une nouvelle tentative : le run repasse en file, prêt à être repris par n'importe quel worker."""
    async with session_factory() as session:
        await session.execute(
            update(EvaluationRun)
            .where(EvaluationRun.id == run_id, EvaluationRun.worker_id == worker_id)
            .values(status=RunStatus.QUEUED, heartbeat_at=None, worker_id=None, error=error)
        )
        await session.commit()


async def already_processed(session_factory: SessionFactory, message_id: uuid.UUID, consumer: str) -> bool:
    async with session_factory() as session:
        found = await session.scalar(
            select(ProcessedMessage.message_id).where(
                ProcessedMessage.message_id == message_id, ProcessedMessage.consumer == consumer
            )
        )
        return found is not None
