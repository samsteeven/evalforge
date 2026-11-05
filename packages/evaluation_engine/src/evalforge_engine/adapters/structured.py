"""Sortie structurée à travers la passerelle EvalForge, pour les bibliothèques d'évaluation (Ragas, DeepEval).

Ragas et DeepEval demandent à leur « modèle » de remplir un schéma Pydantic. Plutôt que de leur confier un client
OpenAI, on leur donne un objet qui passe par la passerelle : mêmes délais, reprises, protection SSRF et mesure des
tokens que tout le reste. Le schéma JSON est joint au prompt ; une réponse invalide est redemandée une fois.
"""

import json
import re
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ValidationError

from evalforge_engine.evaluators.llm_judge import JudgeModel
from evalforge_llm import LLMRequest

_JSON = re.compile(r"\{.*\}", re.DOTALL)


class StructuredOutputError(ValueError):
    pass


@dataclass
class Usage:
    """Appels du modèle juge pour une mesure : une métrique de bibliothèque en fait souvent plusieurs."""

    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    models: set[str] = field(default_factory=set)

    def as_metadata(self) -> dict[str, Any]:
        return {
            "judge_calls": self.calls,
            "judge_input_tokens": self.input_tokens,
            "judge_output_tokens": self.output_tokens,
            "judge_model": ", ".join(sorted(self.models)) or None,
        }


async def generate_text(judge: JudgeModel, prompt: str, usage: Usage, max_tokens: int = 1500) -> str:
    response = await judge.provider.generate(
        LLMRequest(
            model=judge.model,
            messages=[{"role": "user", "content": prompt}],
            temperature=judge.temperature,
            max_tokens=max_tokens,
        )
    )
    usage.calls += 1
    usage.input_tokens += response.input_tokens or 0
    usage.output_tokens += response.output_tokens or 0
    usage.models.add(response.model or judge.model)
    return response.content


async def generate_structured[T: BaseModel](judge: JudgeModel, prompt: str, schema: type[T], usage: Usage) -> T:
    instructions = (
        f"{prompt}\n\nRéponds uniquement par un objet JSON conforme à ce schéma JSON :\n"
        f"{json.dumps(schema.model_json_schema(), ensure_ascii=False)}"
    )
    error: Exception | None = None
    for _ in range(2):
        text = await generate_text(judge, instructions, usage)
        match = _JSON.search(text)
        try:
            if not match:
                raise ValueError("pas d'objet JSON")
            return schema.model_validate_json(match[0])
        except (ValidationError, ValueError) as caught:
            error = caught
    raise StructuredOutputError(f"réponse non conforme au schéma {schema.__name__} : {error}")
