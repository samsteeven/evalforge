"""Adaptateur pour toute API compatible OpenAI (/chat/completions) : OpenAI, DeepSeek, Ollama (/v1), vLLM…"""

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from evalforge_llm.contracts import Capabilities, LLMRequest, LLMResponse, ToolCall
from evalforge_llm.errors import ProviderError, ProviderErrorKind
from evalforge_llm.network import UnsafeDestinationError

Sleep = Callable[[float], Awaitable[None]]


class OpenAICompatibleProvider:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str | None = None,
        client: httpx.AsyncClient,
        timeout_seconds: float = 60.0,
        max_attempts: int = 3,
        backoff_seconds: float = 0.5,
        max_retry_after_seconds: float = 10.0,
        capabilities: Capabilities | None = None,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts doit valoir au moins 1")
        self._base_url = base_url.rstrip("/")
        self.__api_key = api_key  # jamais exposée : ni repr, ni message d'erreur
        self._client = client
        self._timeout = timeout_seconds
        self._max_attempts = max_attempts
        self._backoff = backoff_seconds
        self._max_retry_after = max_retry_after_seconds
        self._sleep = sleep
        self.capabilities = capabilities or Capabilities()

    def __repr__(self) -> str:
        return f"OpenAICompatibleProvider(base_url={self._base_url!r})"

    async def generate(self, request: LLMRequest) -> LLMResponse:
        attempt = 0
        while True:
            attempt += 1
            try:
                return await self._send(request)
            except ProviderError as error:
                error.attempts = attempt
                if not error.retryable or attempt >= self._max_attempts:
                    raise
                await self._sleep(self._delay(error, attempt))

    def _delay(self, error: ProviderError, attempt: int) -> float:
        if error.retry_after is not None:
            return min(error.retry_after, self._max_retry_after)
        return self._backoff * 2 ** (attempt - 1)

    async def _send(self, request: LLMRequest) -> LLMResponse:
        body: dict[str, Any] = {
            "model": request.model,
            "messages": request.messages,
            "temperature": request.temperature,
        }
        if request.max_tokens is not None:
            body["max_tokens"] = request.max_tokens
        if request.tools:
            body["tools"] = [
                {
                    "type": "function",
                    "function": {"name": t.name, "description": t.description, "parameters": t.parameters},
                }
                for t in request.tools
            ]
        headers = {"Authorization": f"Bearer {self.__api_key}"} if self.__api_key else {}

        started = time.perf_counter()
        try:
            response = await self._client.post(
                f"{self._base_url}/chat/completions", json=body, headers=headers, timeout=self._timeout
            )
        except UnsafeDestinationError as error:  # jamais rejouée : l'adresse ne deviendra pas publique
            raise ProviderError(ProviderErrorKind.FORBIDDEN_DESTINATION, str(error)) from None
        except httpx.TimeoutException:
            raise ProviderError(ProviderErrorKind.TIMEOUT, f"pas de réponse en {self._timeout:g} s") from None
        except httpx.TransportError as error:
            raise ProviderError(
                ProviderErrorKind.NETWORK, f"fournisseur injoignable ({type(error).__name__})"
            ) from None
        latency_ms = (time.perf_counter() - started) * 1000

        if response.status_code >= 400:
            raise _http_error(response)
        return _parse(response, latency_ms)


def _http_error(response: httpx.Response) -> ProviderError:
    status = response.status_code
    detail = f"HTTP {status} : {response.text[:300]}"
    if status == 429:
        return ProviderError(
            ProviderErrorKind.RATE_LIMITED, detail, status_code=status, retry_after=_retry_after(response)
        )
    if status in (401, 403):
        return ProviderError(ProviderErrorKind.AUTHENTICATION, f"HTTP {status} : accès refusé", status_code=status)
    if status >= 500:
        return ProviderError(ProviderErrorKind.SERVER, detail, status_code=status, retry_after=_retry_after(response))
    return ProviderError(ProviderErrorKind.BAD_REQUEST, detail, status_code=status)


def _retry_after(response: httpx.Response) -> float | None:
    value = response.headers.get("retry-after")
    try:
        return max(float(value), 0.0) if value is not None else None
    except ValueError:  # date HTTP : on retombe sur le délai exponentiel
        return None


def _parse(response: httpx.Response, latency_ms: float) -> LLMResponse:
    try:
        data = response.json()
        message = data["choices"][0]["message"]
        tool_calls = [
            ToolCall(
                id=str(call["id"]), name=call["function"]["name"], arguments=call["function"].get("arguments") or "{}"
            )
            for call in message.get("tool_calls") or []
        ]
        content = message.get("content")
        if content is None and tool_calls:
            content = ""  # réponse faite uniquement d'appels d'outils
        if not isinstance(content, str):
            raise TypeError("contenu absent")
        usage = data.get("usage") or {}
        return LLMResponse(
            content=content,
            tool_calls=tool_calls,
            model=str(data.get("model") or ""),
            input_tokens=usage.get("prompt_tokens"),
            output_tokens=usage.get("completion_tokens"),
            latency_ms=latency_ms,
            provider_request_id=data.get("id") or response.headers.get("x-request-id"),
        )
    except (ValueError, KeyError, IndexError, TypeError) as error:
        raise ProviderError(ProviderErrorKind.INVALID_RESPONSE, f"réponse illisible ({error})") from None
