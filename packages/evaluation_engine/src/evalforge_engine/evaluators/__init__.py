from evalforge_engine.contracts import Evaluator
from evalforge_engine.evaluators.deterministic import ContainsExpected, ExactMatch

_REGISTRY: dict[str, Evaluator] = {evaluator.name: evaluator for evaluator in (ExactMatch(), ContainsExpected())}


def available_metrics() -> list[str]:
    return sorted(_REGISTRY)


def get_evaluators(names: list[str]) -> list[Evaluator]:
    unknown = [name for name in names if name not in _REGISTRY]
    if unknown:
        raise KeyError(f"métriques inconnues : {', '.join(unknown)}")
    return [_REGISTRY[name] for name in names]


__all__ = ["ContainsExpected", "ExactMatch", "available_metrics", "get_evaluators"]
