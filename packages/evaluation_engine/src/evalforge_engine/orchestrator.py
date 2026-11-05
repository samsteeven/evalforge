"""Exécution des cas : concurrence bornée, délai par cas, résultats rendus au fil de l'eau.

Un cas en échec (délai, erreur du fournisseur, gabarit) devient un résultat en erreur : il ne fait jamais
échouer le run. Les résultats sortent dans l'ordre où ils se terminent, pour être enregistrés au fur et à mesure.
"""

import asyncio
from collections.abc import AsyncIterator, Hashable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum

from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode

from evalforge_engine.contracts import EvaluationCase, EvaluationInput, Evaluator, MetricOutput, Target, TargetOutput

tracer = trace.get_tracer("evalforge")


class CaseStatus(StrEnum):
    SUCCESS = "success"
    ERROR = "error"
    TIMEOUT = "timeout"


@dataclass(frozen=True)
class WorkItem:
    key: Hashable
    case: EvaluationCase
    target: Target


@dataclass
class CaseOutcome:
    key: Hashable
    case: EvaluationCase
    status: CaseStatus
    output: TargetOutput | None = None
    error: str | None = None
    metrics: list[MetricOutput] = field(default_factory=list)


async def evaluate_cases(
    items: Sequence[WorkItem],
    evaluators: Sequence[Evaluator],
    *,
    max_concurrency: int = 5,
    timeout_seconds: float = 60.0,
) -> AsyncIterator[CaseOutcome]:
    if max_concurrency < 1:
        raise ValueError("max_concurrency doit valoir au moins 1")
    semaphore = asyncio.Semaphore(max_concurrency)
    finished: asyncio.Queue[CaseOutcome] = asyncio.Queue()

    async def run(item: WorkItem) -> None:
        async with semaphore:
            outcome = await _evaluate_one(item, evaluators, timeout_seconds)
        await finished.put(outcome)

    tasks = [asyncio.create_task(run(item)) for item in items]
    try:
        for _ in tasks:
            yield await finished.get()
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def _evaluate_one(item: WorkItem, evaluators: Sequence[Evaluator], timeout_seconds: float) -> CaseOutcome:
    with tracer.start_as_current_span("evaluate_case", attributes={"evalforge.case.id": item.case.id}) as span:
        outcome = await _evaluate(item, evaluators, timeout_seconds)
        span.set_attribute("evalforge.case.status", outcome.status.value)
        failed = [metric.name for metric in outcome.metrics if metric.passed is False]
        if failed:
            span.set_attribute("evalforge.metrics.failed", failed)
        if outcome.status is not CaseStatus.SUCCESS:
            span.set_status(Status(StatusCode.ERROR, outcome.error or outcome.status.value))
        return outcome


async def _evaluate(item: WorkItem, evaluators: Sequence[Evaluator], timeout_seconds: float) -> CaseOutcome:
    try:
        output = await asyncio.wait_for(item.target(item.case), timeout_seconds)
    except TimeoutError:
        return CaseOutcome(item.key, item.case, CaseStatus.TIMEOUT, error=f"pas de réponse en {timeout_seconds:g} s")
    except Exception as error:  # un cas en échec ne doit pas arrêter les autres
        return CaseOutcome(item.key, item.case, CaseStatus.ERROR, error=str(error) or type(error).__name__)

    data = EvaluationInput(
        input=item.case.input,
        actual_output=output.text,
        expected_output=item.case.expected_answer,
        context=item.case.context,
        expected=item.case.expected,
        trajectory=output.trajectory,
        stop_reason=output.stop_reason,
    )
    metrics = [await _measure(evaluator, data) for evaluator in evaluators]
    return CaseOutcome(item.key, item.case, CaseStatus.SUCCESS, output=output, metrics=metrics)


async def _measure(evaluator: Evaluator, data: EvaluationInput) -> MetricOutput:
    try:
        return await evaluator.evaluate(data)
    except Exception as error:  # évaluateur en panne : métrique indisponible, pas un score de 0
        return MetricOutput(name=evaluator.name, score=None, passed=None, reason=f"évaluateur en échec : {error}")
