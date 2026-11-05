"""Faux modèle juge pour les adaptateurs : il lit le schéma JSON joint au prompt et répond un objet conforme.

Les réponses sont écrites à la main pour chaque schéma de Ragas et de DeepEval : on vérifie le branchement réel des
bibliothèques (leurs prompts, leurs schémas, leur calcul du score), pas la qualité d'un modèle.
"""

import json
import re
from collections.abc import Callable

from evalforge_llm import Capabilities, LLMRequest, LLMResponse

Answer = Callable[[str, str], dict]


class SchemaJudge:
    capabilities = Capabilities()

    def __init__(self, answers: dict[str, Answer], *, garbage_first: bool = False) -> None:
        self.answers = answers
        self.garbage_first = garbage_first
        self.titles: list[str] = []

    async def generate(self, request: LLMRequest) -> LLMResponse:
        prompt = request.messages[-1]["content"]
        marker = "Réponds uniquement par un objet JSON conforme à ce schéma JSON :\n"
        if marker not in prompt:
            content = "Texte libre."
        else:
            body, schema_text = prompt.split(marker, 1)
            title = json.loads(schema_text)["title"]
            self.titles.append(title)
            if self.garbage_first:
                self.garbage_first = False
                content = "Bien sûr ! Voici ce que je pense."
            else:
                content = json.dumps(self.answers[title](title, body), ensure_ascii=False)
        return LLMResponse(content=content, model="judge-2026-11", input_tokens=50, output_tokens=10, latency_ms=5)


def sentences(text: str) -> list[str]:
    return [part.strip() for part in re.split(r"(?<=[.!?])\s+", text) if part.strip()]
