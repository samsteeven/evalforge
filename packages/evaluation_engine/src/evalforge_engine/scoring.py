"""Agrégats d'un run. Qualité, performance et coût restent séparés : un score de 0,92 ne se compare pas à 2 s."""

import math
from collections.abc import Sequence
from typing import Any

from pydantic import BaseModel


class MetricSummary(BaseModel):
    name: str
    evaluated: int
    # Cas où la métrique n'a pas pu être calculée (pas de réponse attendue, évaluateur en panne…).
    unavailable: int
    mean_score: float | None
    pass_rate: float | None


class PerformanceSummary(BaseModel):
    total: int
    succeeded: int
    errors: int
    timeouts: int
    success_rate: float | None
    p50_latency_ms: float | None
    p95_latency_ms: float | None
    input_tokens: int | None
    output_tokens: int | None
    cost_usd: float | None
    # Nombre de réponses dont le coût est connu : un coût partiel n'est pas présenté comme un total.
    cost_known_for: int


def percentile(values: Sequence[float], p: float) -> float | None:
    """Rang le plus proche : la valeur renvoyée a réellement été observée."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(math.ceil(p / 100 * len(ordered)), 1)
    return ordered[rank - 1]


def summarize_metric(name: str, scores: Sequence[tuple[float | None, bool | None]]) -> MetricSummary:
    available = [(score, passed) for score, passed in scores if score is not None]
    judged = [passed for _, passed in available if passed is not None]
    return MetricSummary(
        name=name,
        evaluated=len(available),
        unavailable=len(scores) - len(available),
        mean_score=sum(score for score, _ in available) / len(available) if available else None,
        pass_rate=sum(judged) / len(judged) if judged else None,
    )


def summarize_performance(
    statuses: Sequence[str],
    latencies_ms: Sequence[float],
    input_tokens: Sequence[int | None],
    output_tokens: Sequence[int | None],
    costs_usd: Sequence[float | None],
) -> PerformanceSummary:
    total = len(statuses)
    succeeded = statuses.count("success")
    known_costs = [cost for cost in costs_usd if cost is not None]
    return PerformanceSummary(
        total=total,
        succeeded=succeeded,
        errors=statuses.count("error"),
        timeouts=statuses.count("timeout"),
        success_rate=succeeded / total if total else None,
        p50_latency_ms=percentile(latencies_ms, 50),
        p95_latency_ms=percentile(latencies_ms, 95),
        input_tokens=_sum_known(input_tokens),
        output_tokens=_sum_known(output_tokens),
        cost_usd=sum(known_costs) if known_costs and len(known_costs) == succeeded else None,
        cost_known_for=len(known_costs),
    )


def _sum_known(values: Sequence[int | None]) -> int | None:
    known = [value for value in values if value is not None]
    return sum(known) if known else None


def cost_usd(
    input_tokens: int | None, output_tokens: int | None, input_per_million: float, output_per_million: float
) -> float | None:
    if input_tokens is None or output_tokens is None:
        return None
    return (input_tokens * input_per_million + output_tokens * output_per_million) / 1_000_000


class MetricRecord(BaseModel):
    name: str
    score: float | None
    passed: bool | None
    reason: str | None = None


class ResultRecord(BaseModel):
    """Une réponse et ses mesures, sous une forme commune à l'API et à la CLI."""

    case_id: str
    model: str
    status: str
    output: str | None = None
    error: str | None = None
    latency_ms: float | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: float | None = None
    trajectory: list[dict[str, Any]] | None = None
    stop_reason: str | None = None
    metrics: list[MetricRecord] = []


class ModelReport(BaseModel):
    model: str
    performance: PerformanceSummary
    metrics: list[MetricSummary]


def summarize_by_model(
    models: Sequence[str], metric_names: Sequence[str], records: Sequence[ResultRecord]
) -> list[ModelReport]:
    """Qualité calculée sur les réponses obtenues seulement ; les échecs comptent dans la performance."""
    reports = []
    for model in models:
        own = [record for record in records if record.model == model]
        succeeded = [record for record in own if record.status == "success"]
        scores: dict[str, list[tuple[float | None, bool | None]]] = {name: [] for name in metric_names}
        for record in succeeded:
            for metric in record.metrics:
                scores.setdefault(metric.name, []).append((metric.score, metric.passed))
        reports.append(
            ModelReport(
                model=model,
                performance=summarize_performance(
                    [record.status for record in own],
                    [record.latency_ms for record in succeeded if record.latency_ms is not None],
                    [record.input_tokens for record in succeeded],
                    [record.output_tokens for record in succeeded],
                    [record.cost_usd for record in succeeded],
                ),
                metrics=[summarize_metric(name, values) for name, values in scores.items()],
            )
        )
    return reports
