"""Évaluation des trajectoires d'agents : pas seulement la réponse finale, le chemin pour y arriver.

L'attendu d'un cas agent :
    {"answer": "...", "tools": ["find_order", "get_return_policy"],
     "arguments": {"find_order": {"order_id": "CMD-1042"}}}

Seuls les appels réussis comptent comme « faits » : un appel aux arguments invalides n'a rien fait. Une métrique sans
attendu ou sans trajectoire est indisponible, pas nulle.
"""

from typing import Any

from evalforge_engine.contracts import EvaluationInput, MetricOutput


def _unavailable(name: str, reason: str) -> MetricOutput:
    return MetricOutput(name=name, score=None, passed=None, reason=reason)


def _expected_tools(data: EvaluationInput) -> list[str] | None:
    tools = (data.expected or {}).get("tools")
    return [str(t) for t in tools] if isinstance(tools, list) else None


def _succeeded(data: EvaluationInput) -> list[dict[str, Any]]:
    return [step for step in data.trajectory or [] if step.get("status") == "success"]


def _is_subsequence(expected: list[str], actual: list[str]) -> bool:
    remaining = iter(actual)
    return all(name in remaining for name in expected)


def _normalize(value: Any) -> str:
    return " ".join(str(value).casefold().split())


class ToolSelection:
    """Les outils attendus ont-ils tous été appelés avec succès ? Score : part des outils attendus appelés."""

    name = "tool_selection"

    async def evaluate(self, data: EvaluationInput) -> MetricOutput:
        expected = _expected_tools(data)
        if expected is None or data.trajectory is None:
            return _unavailable(self.name, "pas d'outils attendus" if expected is None else "pas de trajectoire")
        called = {step["tool"] for step in _succeeded(data)}
        if not expected:  # aucun outil attendu : réussi si l'agent n'en a appelé aucun
            return MetricOutput(name=self.name, score=1.0 if not called else 0.0, passed=not called)
        found = [tool for tool in expected if tool in called]
        missing = [tool for tool in expected if tool not in called]
        return MetricOutput(
            name=self.name,
            score=len(found) / len(expected),
            passed=not missing,
            reason=f"manquants : {', '.join(missing)}" if missing else None,
        )


class ToolOrder:
    """Les outils attendus ont-ils été appelés dans l'ordre attendu (d'autres appels intercalés tolérés) ?"""

    name = "tool_order"

    async def evaluate(self, data: EvaluationInput) -> MetricOutput:
        expected = _expected_tools(data)
        if not expected or data.trajectory is None:
            return _unavailable(self.name, "pas d'ordre attendu" if not expected else "pas de trajectoire")
        actual = [step["tool"] for step in _succeeded(data)]
        ok = _is_subsequence(expected, actual)
        return MetricOutput(
            name=self.name,
            score=1.0 if ok else 0.0,
            passed=ok,
            reason=None if ok else f"ordre suivi : {' → '.join(actual) or 'aucun outil'}",
        )


class ToolArguments:
    """Les arguments attendus ont-ils été transmis ? Score : part des arguments attendus retrouvés."""

    name = "tool_arguments"

    async def evaluate(self, data: EvaluationInput) -> MetricOutput:
        expected = (data.expected or {}).get("arguments")
        if not isinstance(expected, dict) or not expected or data.trajectory is None:
            return _unavailable(self.name, "pas d'arguments attendus")
        total = matched = 0
        wrong = []
        for tool, arguments in expected.items():
            call = next((step for step in _succeeded(data) if step["tool"] == tool), None)
            for key, value in (arguments or {}).items():
                total += 1
                actual = (call or {}).get("arguments", {})
                if isinstance(actual, dict) and _normalize(actual.get(key)) == _normalize(value):
                    matched += 1
                else:
                    wrong.append(f"{tool}.{key}")
        if total == 0:
            return _unavailable(self.name, "pas d'arguments attendus")
        return MetricOutput(
            name=self.name,
            score=matched / total,
            passed=matched == total,
            reason=f"absents ou différents : {', '.join(wrong)}" if wrong else None,
        )


class StepEfficiency:
    """Étapes inutiles : appels hors de l'attendu, appels répétés, appels échoués. Score : attendus / effectués."""

    name = "step_efficiency"

    async def evaluate(self, data: EvaluationInput) -> MetricOutput:
        expected = _expected_tools(data)
        if expected is None or data.trajectory is None:
            return _unavailable(self.name, "pas d'outils attendus" if expected is None else "pas de trajectoire")
        steps = len(data.trajectory)
        if steps == 0:
            return MetricOutput(name=self.name, score=1.0 if not expected else 0.0, passed=not expected)
        extra = steps - len(expected)
        failed = sum(1 for step in data.trajectory if step.get("status") != "success")
        score = min(len(expected) / steps, 1.0) if expected else 0.0
        efficient = extra <= 0 and failed == 0
        return MetricOutput(
            name=self.name,
            score=score,
            passed=efficient,
            reason=None if efficient else f"{steps} étapes pour {len(expected)} attendues, dont {failed} en échec",
            metadata={"steps": steps, "expected_steps": len(expected), "failed_steps": failed},
        )


class TaskCompletion:
    """L'agent a-t-il terminé par une réponse, sans épuiser son budget d'étapes ?"""

    name = "task_completion"

    async def evaluate(self, data: EvaluationInput) -> MetricOutput:
        if data.stop_reason is None:
            return _unavailable(self.name, "pas une exécution d'agent")
        completed = data.stop_reason == "completed" and bool(str(data.actual_output).strip())
        return MetricOutput(
            name=self.name,
            score=1.0 if completed else 0.0,
            passed=completed,
            reason=None if completed else f"arrêt : {data.stop_reason}",
        )
