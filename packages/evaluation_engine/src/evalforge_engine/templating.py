"""Gabarits de prompt : seulement des substitutions `{{ variable }}`.

Pas de str.format ni de moteur de gabarits complet : un prompt vient d'un utilisateur, et `{x.__class__}`
ou une boucle Jinja lui donneraient accès à plus que les champs du cas.
"""

import json
import re

from evalforge_engine.contracts import EvaluationCase

_VARIABLE = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")


class TemplateError(ValueError):
    pass


def variables(template: str) -> list[str]:
    return list(dict.fromkeys(_VARIABLE.findall(template)))


def render(template: str, case: EvaluationCase) -> str:
    values: dict[str, object] = {"context": "\n".join(case.context), **case.input}

    def substitute(match: re.Match[str]) -> str:
        name = match[1]
        if name not in values:
            raise TemplateError(f"variable « {name} » absente du cas {case.id}")
        value = values[name]
        return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)

    return _VARIABLE.sub(substitute, template)
