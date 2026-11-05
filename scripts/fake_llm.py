"""Faux fournisseur compatible OpenAI, pour la démonstration et les tests de bout en bout.

    uv run python scripts/fake_llm.py            # http://127.0.0.1:8100/v1

Le comportement dépend du modèle demandé :
- « grounded » répond la ligne « Contexte : … » du prompt : un modèle qui s'appuie sur ses sources ;
- « vague » répond toujours « Je ne sais pas. » ;
- « judge » joue le juge LLM : 5 si la réponse évaluée contient la réponse attendue (ou, sans réponse attendue,
  un passage du contexte), 1 sinon, avec une justification ;
- « support-agent » pilote l'agent de support avec des appels d'outils, en suivant la bonne démarche ;
- « careless-agent » pilote le même agent en faisant les erreurs typiques : transfert inutile à un humain, numéro de
  commande mal formé, politique de retour inventée au lieu d'être consultée ;
- tout autre nom renvoie une erreur 404, comme un fournisseur réel.
Les latences sont tirées au hasard pour donner des percentiles réalistes ; aucune donnée ne sort du poste.
"""

import asyncio
import json
import random
import re
import sys
import uuid
from typing import Any

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
    tools: list[dict] | None = None


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


# --- Agent de support : une « politique » écrite à la main qui choisit l'outil suivant ------------------------------

CATEGORY_WORDS = {
    "electronique": r"casque|écran|téléphone|enceinte|électronique",
    "vetements": r"pull|robe|veste|chemise|vêtement",
    "maison": r"lampe|meuble|coussin|maison",
}


def _agent_state(messages: list[dict]) -> tuple[str, list[str], dict[str, dict]]:
    user = next(m["content"] for m in messages if m["role"] == "user")
    called = [c["function"]["name"] for m in messages if m["role"] == "assistant" for c in m.get("tool_calls") or []]
    results: dict[str, dict] = {}
    names = {
        c["id"]: c["function"]["name"] for m in messages if m["role"] == "assistant" for c in m.get("tool_calls") or []
    }
    for message in messages:
        if message["role"] == "tool":
            results[names.get(message["tool_call_id"], "?")] = json.loads(message["content"])
    return user, called, results


def _category(user: str, results: dict[str, dict]) -> str:
    order = results.get("find_order") or {}
    if order.get("category"):
        return order["category"]
    for category, words in CATEGORY_WORDS.items():
        if re.search(words, user, re.IGNORECASE):
            return category
    return "electronique"


def _next_tool(user: str, called: list[str], results: dict[str, dict], careful: bool) -> tuple[str, dict] | None:
    order = re.search(r"CMD-\d{4}", user)
    wants_return = re.search(r"retour|rembours|renvoyer", user, re.IGNORECASE)
    damaged = re.search(r"cassé|endommagé|abîmé|défectueux", user, re.IGNORECASE)
    wants_human = re.search(r"conseiller|humain", user, re.IGNORECASE)

    plan: list[tuple[str, dict]] = []
    if careful:
        if wants_human:
            plan.append(("escalate_to_human", {"reason": "Le client demande à parler à un conseiller."}))
        if order:
            plan.append(("find_order", {"order_id": order[0]}))
        if wants_return:
            plan.append(("get_return_policy", {"category": _category(user, results)}))
        if damaged:
            plan.append(
                (
                    "create_ticket",
                    {"order_id": order[0] if order else None, "reason": "Produit reçu endommagé", "priority": "haute"},
                )
            )
    else:
        # Erreurs typiques : transfert inutile, numéro mal formé, politique jamais consultée.
        plan.append(("escalate_to_human", {"reason": "Demande client à traiter."}))
        if order:
            plan.append(("find_order", {"order_id": order[0].removeprefix("CMD-")}))
    for name, arguments in plan:
        if name not in called:
            return name, arguments
    return None


def _final_answer(user: str, results: dict[str, dict], careful: bool) -> str:
    parts = []
    order = results.get("find_order")
    if order and order.get("found"):
        parts.append(f"Votre commande {order['order_id']} ({order['item']}) est {order['status']}.")
    elif order and "error" not in order:
        parts.append(f"Je ne trouve pas la commande {order.get('order_id')}.")
    policy = results.get("get_return_policy")
    if policy:
        parts.append(
            f"Retour possible sous {policy['return_days']} jours ({policy['condition']}), "
            f"remboursement {policy['refund']}."
        )
    elif not careful and re.search(r"retour|rembours", user, re.IGNORECASE):
        parts.append("Retour possible sous 30 jours.")  # inventé : la politique n'a pas été consultée
    ticket = results.get("create_ticket")
    if ticket:
        parts.append(f"J'ai ouvert le ticket {ticket['ticket_id']} en priorité {ticket['priority']}.")
    if results.get("escalate_to_human"):
        parts.append("Un conseiller va reprendre la conversation d'ici 15 minutes environ.")
    return " ".join(parts) or "Pouvez-vous préciser votre demande ?"


def _agent_reply(messages: list[dict], careful: bool) -> dict[str, Any]:
    user, called, results = _agent_state(messages)
    choice = _next_tool(user, called, results, careful)
    if choice is None:
        return {"role": "assistant", "content": _final_answer(user, results, careful)}
    name, arguments = choice
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": f"call_{uuid.uuid4().hex[:8]}",
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(arguments, ensure_ascii=False)},
            }
        ],
    }


@app.post("/v1/chat/completions")
async def chat(request: ChatRequest) -> JSONResponse:
    prompt = str(request.messages[-1].get("content") or "")
    if request.model in ("support-agent", "careless-agent"):
        await asyncio.sleep(random.uniform(0.05, 0.2))  # noqa: S311
        message = _agent_reply(request.messages, careful=request.model == "support-agent")
    elif request.model == "grounded":
        await asyncio.sleep(random.uniform(0.15, 0.6))  # noqa: S311 (simulation, pas de sécurité)
        match = re.search(r"^Contexte : (.+)$", prompt, re.MULTILINE)
        message = {"role": "assistant", "content": match[1].strip() if match else "Je ne trouve pas l'information."}
    elif request.model == "judge":
        await asyncio.sleep(random.uniform(0.05, 0.2))  # noqa: S311
        message = {"role": "assistant", "content": json.dumps(_judge(prompt), ensure_ascii=False)}
    elif request.model == "vague":
        await asyncio.sleep(random.uniform(0.05, 0.25))  # noqa: S311
        message = {"role": "assistant", "content": "Je ne sais pas."}
    else:
        return JSONResponse({"error": {"message": f"model {request.model} not found"}}, status_code=404)
    words = sum(len(str(m.get("content") or "").split()) for m in request.messages)
    return JSONResponse(
        {
            "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
            "model": f"{request.model}-2026-11",
            "choices": [{"index": 0, "message": message}],
            "usage": {
                "prompt_tokens": words + 12,
                "completion_tokens": len(str(message.get("content") or "").split()) + 8,
            },
        }
    )


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8100
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
