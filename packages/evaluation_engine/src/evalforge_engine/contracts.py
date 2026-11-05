"""Contrats internes. Chaque adaptateur (natif, DeepEval, Ragas) convertit ses résultats dans ces types."""

from collections.abc import Awaitable, Callable
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field


class EvaluationCase(BaseModel):
    id: str
    # agent : expected peut contenir tools (outils attendus, dans l'ordre) et arguments ({outil: {argument: valeur}}).
    type: Literal["qa", "rag", "agent", "security"] = "qa"
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
    # Pour les agents : l'attendu complet (outils, arguments) et la trajectoire réellement suivie.
    expected: dict[str, Any] | None = None
    trajectory: list[dict[str, Any]] | None = None
    stop_reason: str | None = None


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
    # Agents : étapes suivies (outil, arguments, statut, résultat) et raison de l'arrêt.
    trajectory: list[dict[str, Any]] | None = None
    stop_reason: str | None = None


Target = Callable[[EvaluationCase], Awaitable[TargetOutput]]
