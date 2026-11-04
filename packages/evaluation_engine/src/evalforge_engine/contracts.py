"""Contrats internes. Chaque adaptateur (natif, DeepEval, Ragas) convertit ses résultats dans ces types."""

from collections.abc import Awaitable, Callable
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field


class EvaluationCase(BaseModel):
    id: str
    type: Literal["qa", "rag"] = "qa"
    input: dict[str, Any]
    expected: dict[str, Any] | None = None
    context: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def expected_answer(self) -> str | None:
        answer = (self.expected or {}).get("answer")
        return answer if isinstance(answer, str) else None


class EvaluationInput(BaseModel):
    input: dict[str, Any]
    actual_output: Any
    expected_output: Any | None = None
    context: list[str] = Field(default_factory=list)


class MetricOutput(BaseModel):
    name: str
    # None = métrique indisponible pour ce cas : jamais convertie en 0.
    score: float | None
    passed: bool | None
    reason: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class Evaluator(Protocol):
    name: str

    async def evaluate(self, data: EvaluationInput) -> MetricOutput: ...


class TargetOutput(BaseModel):
    """Ce que le système évalué a produit pour un cas, avec ses mesures d'exécution."""

    text: str
    latency_ms: float
    input_tokens: int | None = None
    output_tokens: int | None = None
    model: str | None = None
    request_id: str | None = None


Target = Callable[[EvaluationCase], Awaitable[TargetOutput]]
