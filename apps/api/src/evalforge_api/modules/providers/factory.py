from typing import Protocol

import httpx

from evalforge_llm import Capabilities, LLMProvider, OpenAICompatibleProvider

# Capacités déclarées par type de fournisseur : seules celles vérifiées sont annoncées.
CAPABILITIES = {
    "openai_compatible": Capabilities(),
    "ollama": Capabilities(),
}


class ProviderFactory(Protocol):
    def __call__(self, *, provider_kind: str, base_url: str, api_key: str | None) -> LLMProvider: ...


def http_provider_factory(
    client: httpx.AsyncClient, *, timeout_seconds: float, backoff_seconds: float = 0.5
) -> ProviderFactory:
    """Ollama expose la même API sous /v1 : un seul adaptateur pour les deux."""

    def build(*, provider_kind: str, base_url: str, api_key: str | None) -> LLMProvider:
        return OpenAICompatibleProvider(
            base_url=base_url,
            api_key=api_key,
            client=client,
            timeout_seconds=timeout_seconds,
            backoff_seconds=backoff_seconds,
            capabilities=CAPABILITIES[provider_kind],
        )

    return build
