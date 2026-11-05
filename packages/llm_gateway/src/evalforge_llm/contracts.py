"""Contrat commun à tous les fournisseurs : le reste du code ne connaît que ces types."""

from typing import Any, Protocol

from pydantic import BaseModel


class ToolSpec(BaseModel):
    """Un outil proposé au modèle : nom, description, schéma JSON de ses arguments."""

    name: str
    description: str
    parameters: dict[str, Any]


class ToolCall(BaseModel):
    """Un appel d'outil demandé par le modèle. Les arguments restent bruts : c'est à l'appelant de les valider."""

    id: str
    name: str
    arguments: str


class LLMRequest(BaseModel):
    model: str
    messages: list[dict[str, Any]]
    temperature: float = 0.0
    max_tokens: int | None = None
    tools: list[ToolSpec] | None = None


class LLMResponse(BaseModel):
    # Vide quand le modèle ne répond que par des appels d'outils.
    content: str
    tool_calls: list[ToolCall] = []
    # Identifiant renvoyé par le fournisseur : il peut différer du nom demandé (alias, version datée).
    model: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    latency_ms: float
    provider_request_id: str | None = None


class Capabilities(BaseModel):
    """Ce que l'adaptateur sait faire. Une API « compatible OpenAI » n'implémente pas forcément tout."""

    structured_output: bool = False
    streaming: bool = False
    tool_calling: bool = False
    embeddings: bool = False


class LLMProvider(Protocol):
    capabilities: Capabilities

    async def generate(self, request: LLMRequest) -> LLMResponse: ...


def chat_messages(prompt: str, system: str | None = None) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = [{"role": "system", "content": system}] if system else []
    messages.append({"role": "user", "content": prompt})
    return messages
