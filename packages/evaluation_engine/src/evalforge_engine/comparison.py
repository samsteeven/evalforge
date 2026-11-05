"""Comparaison d'un run à une baseline : qu'est-ce qui a régressé, sur quels cas, et est-ce plus que du bruit ?

Les deux runs portent sur les mêmes cas : la comparaison est appariée, cas par cas. Pour chaque modèle présent dans
les deux runs et chaque métrique :
- taux de réussite avant / après et écart ;
- cas cassés (réussis dans la baseline, échoués maintenant) et cas réparés ;
- test de McNemar exact sur ces cas discordants : la probabilité d'observer un tel déséquilibre par hasard ;
- régression si le taux baisse de plus que la tolérance.

Une métrique indisponible d'un côté ou de l'autre n'entre pas dans l'appariement : elle n'est ni un échec ni un succès.
"""

import math
from collections.abc import Sequence

from pydantic import BaseModel

from evalforge_engine.scoring import ResultRecord, percentile, summarize_metric

SIGNIFICANCE = 0.05


class CaseChange(BaseModel):
    case_id: str
    model: str
    metric: str
    change: str  # "broken" ou "fixed"
    baseline_reason: str | None = None
    candidate_reason: str | None = None


class MetricDelta(BaseModel):
    name: str
    baseline_rate: float | None
    candidate_rate: float | None
    delta: float | None
    paired: int
    fixed: int
    broken: int
    p_value: float | None
    significant: bool
    regressed: bool
    improved: bool


class ModelDelta(BaseModel):
    model: str
    metrics: list[MetricDelta]
    baseline_success_rate: float | None
    candidate_success_rate: float | None
    baseline_p95_latency_ms: float | None
    candidate_p95_latency_ms: float | None


class Comparison(BaseModel):
    tolerance: float
    regressed: bool
    models: list[ModelDelta]
    changes: list[CaseChange]
    # Modèles présents d'un seul côté : rien à comparer pour eux.
    only_in_baseline: list[str]
    only_in_candidate: list[str]


def mcnemar_exact(fixed: int, broken: int) -> float | None:
    """p bilatérale du test de McNemar exact (binomial sur les paires discordantes, p = 0,5)."""
    n = fixed + broken
    if n == 0:
        return None
    tail = sum(math.comb(n, i) for i in range(min(fixed, broken) + 1)) / 2**n
    return min(1.0, 2 * tail)


def _index(records: Sequence[ResultRecord]) -> dict[tuple[str, str], ResultRecord]:
    return {(record.case_id, record.model): record for record in records}


def _success_rate(records: Sequence[ResultRecord]) -> float | None:
    return sum(r.status == "success" for r in records) / len(records) if records else None


def compare(
    baseline: Sequence[ResultRecord],
    candidate: Sequence[ResultRecord],
    metric_names: Sequence[str],
    *,
    tolerance: float = 0.0,
) -> Comparison:
    base_models = list(dict.fromkeys(r.model for r in baseline))
    cand_models = list(dict.fromkeys(r.model for r in candidate))
    shared = [model for model in cand_models if model in base_models]
    base_index = _index(baseline)  # l'appariement part de chaque réponse du candidat

    models: list[ModelDelta] = []
    changes: list[CaseChange] = []
    for model in shared:
        base_own = [r for r in baseline if r.model == model]
        cand_own = [r for r in candidate if r.model == model]
        deltas = []
        for name in metric_names:
            base_scores = [
                (m.score, m.passed) for r in base_own if r.status == "success" for m in r.metrics if m.name == name
            ]
            cand_scores = [
                (m.score, m.passed) for r in cand_own if r.status == "success" for m in r.metrics if m.name == name
            ]
            base_rate = summarize_metric(name, base_scores).pass_rate
            cand_rate = summarize_metric(name, cand_scores).pass_rate

            fixed = broken = paired = 0
            for record in cand_own:
                previous = base_index.get((record.case_id, model))
                if previous is None:
                    continue
                before = next((m for m in previous.metrics if m.name == name), None)
                after = next((m for m in record.metrics if m.name == name), None)
                if before is None or after is None or before.passed is None or after.passed is None:
                    continue
                paired += 1
                if before.passed and not after.passed:
                    broken += 1
                    changes.append(
                        CaseChange(
                            case_id=record.case_id,
                            model=model,
                            metric=name,
                            change="broken",
                            baseline_reason=before.reason,
                            candidate_reason=after.reason,
                        )
                    )
                elif after.passed and not before.passed:
                    fixed += 1
                    changes.append(CaseChange(case_id=record.case_id, model=model, metric=name, change="fixed"))

            delta = None if base_rate is None or cand_rate is None else cand_rate - base_rate
            p_value = mcnemar_exact(fixed, broken)
            deltas.append(
                MetricDelta(
                    name=name,
                    baseline_rate=base_rate,
                    candidate_rate=cand_rate,
                    delta=delta,
                    paired=paired,
                    fixed=fixed,
                    broken=broken,
                    p_value=p_value,
                    significant=p_value is not None and p_value < SIGNIFICANCE,
                    regressed=delta is not None and delta < -tolerance - 1e-9,
                    improved=delta is not None and delta > tolerance + 1e-9,
                )
            )
        models.append(
            ModelDelta(
                model=model,
                metrics=deltas,
                baseline_success_rate=_success_rate(base_own),
                candidate_success_rate=_success_rate(cand_own),
                baseline_p95_latency_ms=percentile([r.latency_ms for r in base_own if r.latency_ms is not None], 95),
                candidate_p95_latency_ms=percentile([r.latency_ms for r in cand_own if r.latency_ms is not None], 95),
            )
        )
    return Comparison(
        tolerance=tolerance,
        regressed=any(metric.regressed for model in models for metric in model.metrics),
        models=models,
        changes=changes,
        only_in_baseline=[m for m in base_models if m not in cand_models],
        only_in_candidate=[m for m in cand_models if m not in base_models],
    )
