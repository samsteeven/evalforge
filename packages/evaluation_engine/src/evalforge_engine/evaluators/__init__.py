"""Registre des métriques : ce que chacune mesure, ce dont elle a besoin, et comment la construire."""

import importlib.util
from dataclasses import dataclass
from typing import Literal

from evalforge_engine.contracts import Evaluator
from evalforge_engine.evaluators.agent import StepEfficiency, TaskCompletion, ToolArguments, ToolOrder, ToolSelection
from evalforge_engine.evaluators.deterministic import ContainsExpected, ExactMatch
from evalforge_engine.evaluators.llm_judge import CORRECTNESS, FAITHFULNESS, JudgeModel, LLMJudge
from evalforge_engine.evaluators.security import NoForbiddenContent, NoForbiddenTool

AGENT_EVALUATORS = {
    evaluator.name: evaluator for evaluator in (ToolSelection, ToolOrder, ToolArguments, StepEfficiency, TaskCompletion)
}

Method = Literal["deterministic", "agent", "security", "llm_judge", "ragas", "deepeval"]


@dataclass(frozen=True)
class MetricSpec:
    name: str
    description: str
    method: Method
    requires_expected: bool
    requires_context: bool

    @property
    def needs_judge(self) -> bool:
        """Juge LLM, Ragas, DeepEval interrogent un modèle : le modèle juge de l'expérience."""
        return self.method in ("llm_judge", "ragas", "deepeval")

    @property
    def installed(self) -> bool:
        """Ragas et DeepEval sont des groupes de dépendances optionnels (uv sync --group ragas / deepeval)."""
        return self.method not in ("ragas", "deepeval") or importlib.util.find_spec(self.method) is not None


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
            "tool_selection",
            "Agent : les outils attendus ont-ils tous été appelés avec succès ?",
            "agent",
            requires_expected=True,
            requires_context=False,
        ),
        MetricSpec(
            "tool_order",
            "Agent : les outils attendus ont-ils été appelés dans l'ordre attendu ?",
            "agent",
            requires_expected=True,
            requires_context=False,
        ),
        MetricSpec(
            "tool_arguments",
            "Agent : les arguments attendus ont-ils été transmis aux outils ?",
            "agent",
            requires_expected=True,
            requires_context=False,
        ),
        MetricSpec(
            "step_efficiency",
            "Agent : étapes inutiles, répétées ou en échec (attendues / effectuées)",
            "agent",
            requires_expected=True,
            requires_context=False,
        ),
        MetricSpec(
            "task_completion",
            "Agent : réponse finale donnée sans épuiser le budget d'étapes",
            "agent",
            requires_expected=False,
            requires_context=False,
        ),
        MetricSpec(
            "no_forbidden_content",
            "Sécurité : aucun contenu interdit (secret, consigne interne, promesse) dans la réponse",
            "security",
            requires_expected=True,
            requires_context=False,
        ),
        MetricSpec(
            "no_forbidden_tool",
            "Sécurité : aucun appel, même raté, aux outils interdits (injection de prompt suivie)",
            "security",
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
        MetricSpec(
            "ragas_faithfulness",
            "Ragas : part des affirmations de la réponse appuyées par le contexte (plusieurs appels au juge)",
            "ragas",
            requires_expected=False,
            requires_context=True,
        ),
        MetricSpec(
            "deepeval_faithfulness",
            "DeepEval : affirmations de la réponse confrontées aux faits du contexte, avec justification",
            "deepeval",
            requires_expected=False,
            requires_context=True,
        ),
        MetricSpec(
            "deepeval_answer_relevancy",
            "DeepEval : part des énoncés de la réponse qui répondent à la question",
            "deepeval",
            requires_expected=False,
            requires_context=False,
        ),
    )
}


def available_metrics() -> list[str]:
    """Les métriques utilisables ici : celles d'une bibliothèque non installée sont absentes."""
    return sorted(name for name, spec in METRICS.items() if spec.installed)


def needs_judge(names: list[str]) -> bool:
    return any(METRICS[name].needs_judge for name in names if name in METRICS)


def build_evaluators(names: list[str], judge: JudgeModel | None = None) -> list[Evaluator]:
    unknown = [name for name in names if name not in METRICS]
    if unknown:
        raise KeyError(f"métriques inconnues : {', '.join(unknown)}")
    missing = sorted({METRICS[name].method for name in names if not METRICS[name].installed})
    if missing:
        raise ValueError(f"bibliothèque non installée : {', '.join(missing)} (uv sync --group {missing[0]})")
    if needs_judge(names) and judge is None:
        raise ValueError("ces métriques demandent un modèle juge")

    evaluators: list[Evaluator] = []
    for name in names:
        match name:
            case "exact_match":
                evaluators.append(ExactMatch())
            case "contains_expected":
                evaluators.append(ContainsExpected())
            case "tool_selection" | "tool_order" | "tool_arguments" | "step_efficiency" | "task_completion":
                evaluators.append(AGENT_EVALUATORS[name]())
            case "no_forbidden_content":
                evaluators.append(NoForbiddenContent())
            case "no_forbidden_tool":
                evaluators.append(NoForbiddenTool())
            case "judge_correctness":
                evaluators.append(LLMJudge(name, CORRECTNESS, judge))  # type: ignore[arg-type]
            case "judge_faithfulness":
                evaluators.append(LLMJudge(name, FAITHFULNESS, judge))  # type: ignore[arg-type]
            case "ragas_faithfulness":
                from evalforge_engine.adapters.ragas_adapter import RagasFaithfulness

                evaluators.append(RagasFaithfulness(judge))  # type: ignore[arg-type]
            case "deepeval_faithfulness":
                from evalforge_engine.adapters.deepeval_adapter import DeepEvalFaithfulness

                evaluators.append(DeepEvalFaithfulness(judge))  # type: ignore[arg-type]
            case "deepeval_answer_relevancy":
                from evalforge_engine.adapters.deepeval_adapter import DeepEvalAnswerRelevancy

                evaluators.append(DeepEvalAnswerRelevancy(judge))  # type: ignore[arg-type]
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
