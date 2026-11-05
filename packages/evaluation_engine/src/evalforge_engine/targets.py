"""Cibles : ce que l'on évalue. Un modèle appelé avec un gabarit de prompt, ou un agent exposé en HTTP."""

import time

import httpx

from evalforge_engine.contracts import EvaluationCase, Target, TargetOutput
from evalforge_engine.templating import render
from evalforge_llm import LLMProvider, LLMRequest, chat_messages
from evalforge_llm.network import UnsafeDestinationError

AGENT_RUN_PATH = "/v1/agent/run"


def llm_target(
    provider: LLMProvider,
    *,
    model: str,
    template: str,
    system: str | None = None,
    temperature: float = 0.0,
    max_tokens: int | None = None,
) -> Target:
    async def target(case: EvaluationCase) -> TargetOutput:
        request = LLMRequest(
            model=model,
            messages=chat_messages(render(template, case), system),
            temperature=temperature,
            max_tokens=max_tokens,
        )
        response = await provider.generate(request)
        return TargetOutput(
            text=response.content,
            latency_ms=response.latency_ms,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            model=response.model or model,
            request_id=response.provider_request_id,
        )

    return target


class AgentError(RuntimeError):
    pass


def agent_target(client: httpx.AsyncClient, *, base_url: str, name: str, template: str) -> Target:
    """Un agent qui respecte le contrat EvalForge : POST {base_url}/v1/agent/run {"message"} →
    {"output", "trajectory", "stop_reason", "model", "input_tokens", "output_tokens"}.

    Le client fourni porte la protection SSRF : l'adresse de l'agent est saisie par l'utilisateur.
    """
    url = base_url.rstrip("/") + AGENT_RUN_PATH

    async def target(case: EvaluationCase) -> TargetOutput:
        started = time.perf_counter()
        try:
            response = await client.post(url, json={"message": render(template, case)})
        except UnsafeDestinationError as error:
            raise AgentError(f"adresse de l'agent refusée : {error}") from None
        except httpx.HTTPError as error:
            raise AgentError(f"agent injoignable ({type(error).__name__})") from None
        latency_ms = (time.perf_counter() - started) * 1000
        if response.status_code >= 400:
            raise AgentError(f"agent : HTTP {response.status_code} {response.text[:200]}")
        try:
            body = response.json()
            output = body["output"]
            trajectory = body["trajectory"]
            if not isinstance(output, str) or not isinstance(trajectory, list):
                raise TypeError("output ou trajectory au mauvais format")
        except (ValueError, KeyError, TypeError) as error:
            raise AgentError(f"réponse de l'agent illisible ({error})") from None
        return TargetOutput(
            text=output,
            latency_ms=latency_ms,
            input_tokens=body.get("input_tokens"),
            output_tokens=body.get("output_tokens"),
            model=body.get("model") or name,
            trajectory=trajectory,
            stop_reason=body.get("stop_reason"),
        )

    return target
