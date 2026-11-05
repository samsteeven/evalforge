"""Adaptateur Ragas (groupe de dépendances `ragas`). Importé seulement si une métrique ragas_* est demandée."""

import math
import os

# Ragas envoie des statistiques d'usage par défaut : coupées avant tout import.
os.environ.setdefault("RAGAS_DO_NOT_TRACK", "true")

from pydantic import BaseModel
from ragas.llms.base import InstructorBaseRagasLLM
from ragas.metrics.collections import Faithfulness

from evalforge_engine.adapters.structured import Usage, generate_structured
from evalforge_engine.contracts import EvaluationInput, MetricOutput
from evalforge_engine.evaluators.llm_judge import JudgeModel

THRESHOLD = 0.75


class GatewayRagasLLM(InstructorBaseRagasLLM):
    """Le « modèle » que Ragas interroge : chaque appel passe par la passerelle EvalForge."""

    def __init__(self, judge: JudgeModel, usage: Usage) -> None:
        self.judge = judge
        self.usage = usage

    def generate[T: BaseModel](self, prompt: str, response_model: type[T]) -> T:
        raise NotImplementedError("EvalForge appelle Ragas en asynchrone uniquement")

    async def agenerate[T: BaseModel](self, prompt: str, response_model: type[T]) -> T:
        return await generate_structured(self.judge, prompt, response_model, self.usage)


def _question(data: EvaluationInput) -> str | None:
    question = data.input.get("question")
    return question if isinstance(question, str) and question else None


class RagasFaithfulness:
    """Ragas : la réponse est découpée en affirmations, chacune vérifiée contre le contexte (part soutenue)."""

    name = "ragas_faithfulness"

    def __init__(self, judge: JudgeModel) -> None:
        self.judge = judge

    async def evaluate(self, data: EvaluationInput) -> MetricOutput:
        question = _question(data)
        if not data.context or question is None or not str(data.actual_output).strip():
            reason = "pas de contexte" if not data.context else "pas de question ou de réponse"
            return MetricOutput(name=self.name, score=None, passed=None, reason=reason)

        usage = Usage()
        metric = Faithfulness(llm=GatewayRagasLLM(self.judge, usage))
        result = await metric.ascore(
            user_input=question, response=str(data.actual_output), retrieved_contexts=data.context
        )
        metadata = {"method": "ragas", "metric": "Faithfulness", **usage.as_metadata()}
        value = result.value
        if not isinstance(value, int | float) or math.isnan(value):
            # Aucune affirmation extraite (« Je ne sais pas ») : Ragas renvoie NaN, pas 0.
            return MetricOutput(
                name=self.name, score=None, passed=None, reason="aucune affirmation à vérifier", metadata=metadata
            )
        score = float(value)
        return MetricOutput(
            name=self.name,
            score=score,
            passed=score >= THRESHOLD,
            reason=getattr(result, "reason", None)
            or f"{round(score * 100)} % des affirmations appuyées par le contexte",
            metadata={**metadata, "threshold": THRESHOLD},
        )
