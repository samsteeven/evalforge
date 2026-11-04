"""Faux fournisseur compatible OpenAI, pour la démonstration et les tests de bout en bout.

    uv run python scripts/fake_llm.py            # http://127.0.0.1:8100/v1

Le comportement dépend du modèle demandé :
- « grounded » répond la ligne « Contexte : … » du prompt : un modèle qui s'appuie sur ses sources ;
- « vague » répond toujours « Je ne sais pas. » ;
- « judge » joue le juge LLM : 5 si la réponse évaluée contient la réponse attendue (ou, sans réponse attendue,
  un passage du contexte), 1 sinon, avec une justification ;
- tout autre nom renvoie une erreur 404, comme un fournisseur réel.
Les latences sont tirées au hasard pour donner des percentiles réalistes ; aucune donnée ne sort du poste.
"""

import asyncio
import json
import random
import re
import sys
import uuid

import uvicorn
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel

app = FastAPI(title="Faux fournisseur LLM")


class ChatRequest(BaseModel):
    model: str
    messages: list[dict]
    temperature: float = 0.0
    max_tokens: int | None = None


def _judge(prompt: str) -> dict:
    actual = re.search(r"^Réponse évaluée : (.*)$", prompt, re.MULTILINE)
    expected = re.search(r"^Réponse attendue : (.*)$", prompt, re.MULTILINE)
    answer = actual[1].strip() if actual else ""
    if expected:
        supported = expected[1].strip() in answer
    else:
        passages = re.findall(r"^- (.+)$", prompt, re.MULTILINE)
        supported = any(passage.strip() in answer or answer in passage for passage in passages)
    if supported:
        return {"score": 5, "reason": "La réponse reprend l'information attendue."}
    return {"score": 1, "reason": "La réponse ne donne pas l'information demandée."}


@app.post("/v1/chat/completions")
async def chat(request: ChatRequest) -> JSONResponse:
    prompt = str(request.messages[-1].get("content", ""))
    if request.model == "grounded":
        await asyncio.sleep(random.uniform(0.15, 0.6))  # noqa: S311 (simulation, pas de sécurité)
        match = re.search(r"^Contexte : (.+)$", prompt, re.MULTILINE)
        content = match[1].strip() if match else "Je ne trouve pas l'information."
    elif request.model == "judge":
        await asyncio.sleep(random.uniform(0.05, 0.2))  # noqa: S311
        content = json.dumps(_judge(prompt), ensure_ascii=False)
    elif request.model == "vague":
        await asyncio.sleep(random.uniform(0.05, 0.25))  # noqa: S311
        content = "Je ne sais pas."
    else:
        return JSONResponse({"error": {"message": f"model {request.model} not found"}}, status_code=404)
    return JSONResponse(
        {
            "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
            "model": f"{request.model}-2026-11",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": content}}],
            "usage": {"prompt_tokens": len(prompt.split()) + 12, "completion_tokens": len(content.split())},
        }
    )


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8100
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
