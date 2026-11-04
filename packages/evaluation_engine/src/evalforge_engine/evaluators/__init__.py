"""Registre des métriques : ce que chacune mesure, ce dont elle a besoin, et comment la construire."""

from dataclasses import dataclass
from typing import Literal

from evalforge_engine.contracts import Evaluator
from evalforge_engine.evaluators.deterministic import ContainsExpected, ExactMatch
from evalforge_engine.evaluators.llm_judge import CORRECTNESS, FAITHFULNESS, JudgeModel, LLMJudge

Method = Literal["deterministic", "llm_judge"]


@dataclass(frozen=True)
class MetricSpec:
    name: str
    description: str
    method: Method
    requires_expected: bool
    requires_context: bool

    @property
    def needs_judge(self) -> bool:
        return self.method != "deterministic"


METRICS: dict[str, MetricSpec] = {
    spec.name: spec
    for spec in (
        MetricSpec(
            "exact_match",
            "Réponse identique à la réponse attendue (casse, espaces et ponctuation finale ignorés)",
            "deterministic",
            requires_expected=True,
            requires_context=False,
        ),
        MetricSpec(
            "contains_expected",
            "La réponse attendue figure dans la réponse produite",
            "deterministic",
            requires_expected=True,
            requires_context=False,
        ),
        MetricSpec(
            "judge_correctness",
            "Juge LLM : exactitude par rapport à la réponse attendue, note de 1 à 5 justifiée",
            "llm_judge",
            requires_expected=True,
            requires_context=False,
        ),
        MetricSpec(
            "judge_faithfulness",
            "Juge LLM : chaque affirmation de la réponse est-elle dans le contexte fourni ? Note de 1 à 5 justifiée",
            "llm_judge",
            requires_expected=False,
            requires_context=True,
        ),
    )
}


def available_metrics() -> list[str]:
    return sorted(METRICS)


def needs_judge(names: list[str]) -> bool:
    return any(METRICS[name].needs_judge for name in names if name in METRICS)


def build_evaluators(names: list[str], judge: JudgeModel | None = None) -> list[Evaluator]:
    unknown = [name for name in names if name not in METRICS]
    if unknown:
        raise KeyError(f"métriques inconnues : {', '.join(unknown)}")
    if needs_judge(names) and judge is None:
        raise ValueError("ces métriques demandent un modèle juge")

    evaluators: list[Evaluator] = []
    for name in names:
        match name:
            case "exact_match":
                evaluators.append(ExactMatch())
            case "contains_expected":
                evaluators.append(ContainsExpected())
            case "judge_correctness":
                evaluators.append(LLMJudge(name, CORRECTNESS, judge))  # type: ignore[arg-type]
            case "judge_faithfulness":
                evaluators.append(LLMJudge(name, FAITHFULNESS, judge))  # type: ignore[arg-type]
    return evaluators


__all__ = [
    "METRICS",
    "ContainsExpected",
    "ExactMatch",
    "JudgeModel",
    "LLMJudge",
    "MetricSpec",
    "available_metrics",
    "build_evaluators",
    "needs_judge",
]
