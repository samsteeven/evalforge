"""Cibles : ce que l'on évalue. Pour l'instant un modèle appelé directement avec un gabarit de prompt."""

from evalforge_engine.contracts import EvaluationCase, Target, TargetOutput
from evalforge_engine.templating import render
from evalforge_llm import LLMProvider, LLMRequest, chat_messages


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
