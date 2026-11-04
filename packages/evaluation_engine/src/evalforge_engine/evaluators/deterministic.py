"""Évaluateurs déterministes : reproductibles, gratuits, sans juge."""

import string

from evalforge_engine.contracts import EvaluationInput, MetricOutput

_TRAILING = string.whitespace + ".!?…"


def normalize(text: str) -> str:
    return " ".join(text.casefold().split()).strip(_TRAILING)


def _unavailable(name: str, reason: str) -> MetricOutput:
    return MetricOutput(name=name, score=None, passed=None, reason=reason)


class ExactMatch:
    """Réponse identique à la réponse attendue, à la casse, aux espaces et à la ponctuation finale près."""

    name = "exact_match"

    async def evaluate(self, data: EvaluationInput) -> MetricOutput:
        if not isinstance(data.expected_output, str):
            return _unavailable(self.name, "pas de réponse attendue")
        matched = normalize(str(data.actual_output)) == normalize(data.expected_output)
        return MetricOutput(name=self.name, score=1.0 if matched else 0.0, passed=matched)


class ContainsExpected:
    """La réponse attendue figure dans la réponse produite (réponse plus longue, mais juste)."""

    name = "contains_expected"

    async def evaluate(self, data: EvaluationInput) -> MetricOutput:
        if not isinstance(data.expected_output, str) or not normalize(data.expected_output):
            return _unavailable(self.name, "pas de réponse attendue")
        found = normalize(data.expected_output) in normalize(str(data.actual_output))
        return MetricOutput(name=self.name, score=1.0 if found else 0.0, passed=found)
