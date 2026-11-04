"""Exécution d'un run, dans le processus de l'API.

Provisoire : un run en cours est perdu si l'API s'arrête (il est alors marqué « interrupted » au démarrage
suivant). L'incrément « asynchronisme » le remplace par une outbox, RabbitMQ et des workers séparés.
"""

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from evalforge_api.core.database import utcnow
from evalforge_api.core.secrets import SecretBox
from evalforge_api.modules.datasets.models import TestCase
from evalforge_api.modules.evaluations.models import EvaluationResult, EvaluationRun, MetricResult, RunStatus
from evalforge_api.modules.providers.factory import ProviderFactory
from evalforge_api.modules.providers.models import ModelConfiguration
from evalforge_engine import CaseOutcome, EvaluationCase, WorkItem, evaluate_cases
from evalforge_engine.evaluators import JudgeModel, build_evaluators
from evalforge_engine.scoring import cost_usd
from evalforge_engine.targets import llm_target
from evalforge_llm import LLMProvider

log = logging.getLogger(__name__)

INTERRUPTED_MESSAGE = "L'API s'est arrêtée pendant l'exécution ; relancez le run."

SessionFactory = async_sessionmaker[AsyncSession]


def to_engine_case(case: TestCase) -> EvaluationCase:
    return EvaluationCase(
        id=case.external_id,
        type=case.type,
        input=case.input,
        expected=case.expected,
        context=case.context,
        metadata=case.metadata_,
    )


async def execute_run(
    run_id: uuid.UUID, *, session_factory: SessionFactory, provider_factory: ProviderFactory, secret_box: SecretBox
) -> None:
    async with session_factory() as session:
        run = await session.get(EvaluationRun, run_id, with_for_update=True)
        if run is None or run.status != RunStatus.QUEUED:
            return  # déjà pris en charge : ne jamais exécuter deux fois le même run
        run.status = RunStatus.RUNNING
        run.started_at = utcnow()
        await session.commit()
        configuration, execution, dataset_version_id = run.configuration, run.execution, run.dataset_version_id

    # Tout ce qui suit le passage en « running » est couvert : une erreur termine le run en « failed ».
    try:
        async with session_factory() as session:
            cases = list(
                await session.scalars(
                    select(TestCase)
                    .where(TestCase.dataset_version_id == dataset_version_id)
                    .order_by(TestCase.position)
                )
            )
            judge_config = configuration.get("judge")  # absent des runs créés avant le juge
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
            JudgeModel(
                provider_for(judge_config),
                judge_config["model_name"],
                temperature=judge_config["parameters"]["temperature"],
            )
            if judge_config
            else None
        )
        items: list[WorkItem] = []
        pricing: dict[uuid.UUID, dict[str, Any] | None] = {}
        for model in configuration["models"]:
            model_id = uuid.UUID(model["id"])
            provider = provider_for(model)
            target = llm_target(
                provider,
                model=model["model_name"],
                template=configuration["prompt"]["template"],
                system=configuration["prompt"]["system"],
                temperature=model["parameters"]["temperature"],
                max_tokens=model["parameters"]["max_tokens"],
            )
            pricing[model_id] = model["pricing"]
            items.extend(WorkItem((case.id, model_id), to_engine_case(case), target) for case in cases)

        outcomes = evaluate_cases(
            items,
            build_evaluators(configuration["metrics"], judge),
            max_concurrency=execution["max_concurrency"],
            timeout_seconds=execution["timeout_seconds"],
        )
        async for outcome in outcomes:
            await _persist(session_factory, run_id, outcome, pricing)
        await _finish(session_factory, run_id, RunStatus.COMPLETED, None)
    except asyncio.CancelledError:
        raise  # arrêt de l'API : le run reste « running » et sera marqué interrompu au démarrage suivant
    except Exception as error:
        log.exception("Run %s en échec", run_id)
        await _finish(session_factory, run_id, RunStatus.FAILED, f"{type(error).__name__} : {error}")


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


async def _finish(session_factory: SessionFactory, run_id: uuid.UUID, status: RunStatus, error: str | None) -> None:
    async with session_factory() as session:
        await session.execute(
            update(EvaluationRun)
            .where(EvaluationRun.id == run_id)
            .values(status=status, error=error, finished_at=utcnow())
        )
        await session.commit()


async def interrupt_abandoned_runs(session_factory: SessionFactory) -> int:
    """Au démarrage : un run resté en file ou en cours a été abandonné par un processus arrêté."""
    async with session_factory() as session:
        result = await session.execute(
            update(EvaluationRun)
            .where(EvaluationRun.status.in_([RunStatus.QUEUED, RunStatus.RUNNING]))
            .values(status=RunStatus.INTERRUPTED, error=INTERRUPTED_MESSAGE, finished_at=utcnow())
        )
        await session.commit()
        return result.rowcount


class RunScheduler:
    def __init__(self, runner: Callable[[uuid.UUID], Awaitable[None]]) -> None:
        self._runner = runner
        self._tasks: set[asyncio.Task[None]] = set()

    def schedule(self, run_id: uuid.UUID) -> None:
        task = asyncio.create_task(self._runner(run_id), name=f"run-{run_id}")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def shutdown(self) -> None:
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
