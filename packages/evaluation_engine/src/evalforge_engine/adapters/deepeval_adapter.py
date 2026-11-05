"""Adaptateur DeepEval (groupe de dépendances `deepeval`). Importé seulement si une métrique deepeval_* est demandée."""

import os

# DeepEval envoie des statistiques d'usage par défaut : coupées avant tout import.
os.environ.setdefault("DEEPEVAL_TELEMETRY_OPT_OUT", "YES")

from deepeval.metrics import AnswerRelevancyMetric, FaithfulnessMetric
from deepeval.models import DeepEvalBaseLLM
from deepeval.test_case import LLMTestCase
from pydantic import BaseModel

from evalforge_engine.adapters.structured import Usage, generate_structured, generate_text
from evalforge_engine.contracts import EvaluationInput, MetricOutput
from evalforge_engine.evaluators.llm_judge import JudgeModel

THRESHOLD = 0.75


class GatewayDeepEvalModel(DeepEvalBaseLLM):
    """Le « modèle » que DeepEval interroge : chaque appel passe par la passerelle EvalForge."""

    def __init__(self, judge: JudgeModel, usage: Usage) -> None:
        self.judge = judge
        self.usage = usage
        super().__init__(judge.model)

    def load_model(self) -> "GatewayDeepEvalModel":
        return self

    def generate(self, prompt: str, schema: type[BaseModel] | None = None) -> str:
        raise NotImplementedError("EvalForge appelle DeepEval en asynchrone uniquement")

    async def a_generate(self, prompt: str, schema: type[BaseModel] | None = None) -> BaseModel | str:
        if schema is None:
            return await generate_text(self.judge, prompt, self.usage)
        return await generate_structured(self.judge, prompt, schema, self.usage)

    def get_model_name(self) -> str:
        return f"evalforge:{self.judge.model}"


class _DeepEvalMetric:
    name: str
    needs_context: bool

    def __init__(self, judge: JudgeModel) -> None:
        self.judge = judge

    def _metric(self, model: GatewayDeepEvalModel):  # pragma: no cover - surchargée
        raise NotImplementedError

    async def evaluate(self, data: EvaluationInput) -> MetricOutput:
        question = data.input.get("question")
        if not isinstance(question, str) or not question:
            return MetricOutput(name=self.name, score=None, passed=None, reason="pas de question")
        if self.needs_context and not data.context:
            return MetricOutput(name=self.name, score=None, passed=None, reason="pas de contexte")

        usage = Usage()
        metric = self._metric(GatewayDeepEvalModel(self.judge, usage))
        test_case = LLMTestCase(
            input=question, actual_output=str(data.actual_output), retrieval_context=data.context or None
        )
        await metric.a_measure(test_case, _show_indicator=False)
        score = float(metric.score) if metric.score is not None else None
        return MetricOutput(
            name=self.name,
            score=score,
            passed=None if score is None else score >= THRESHOLD,
            reason=metric.reason,
            metadata={
                "method": "deepeval",
                "metric": type(metric).__name__,
                "threshold": THRESHOLD,
                **usage.as_metadata(),
            },
        )


class DeepEvalFaithfulness(_DeepEvalMetric):
    """DeepEval : affirmations de la réponse confrontées aux faits extraits du contexte."""

    name = "deepeval_faithfulness"
    needs_context = True

    def _metric(self, model: GatewayDeepEvalModel) -> FaithfulnessMetric:
        return FaithfulnessMetric(model=model, threshold=THRESHOLD, include_reason=True, async_mode=True)


class DeepEvalAnswerRelevancy(_DeepEvalMetric):
    """DeepEval : part des énoncés de la réponse qui répondent à la question posée."""

    name = "deepeval_answer_relevancy"
    needs_context = False

    def _metric(self, model: GatewayDeepEvalModel) -> AnswerRelevancyMetric:
        return AnswerRelevancyMetric(model=model, threshold=THRESHOLD, include_reason=True, async_mode=True)
