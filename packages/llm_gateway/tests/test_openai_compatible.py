import json

import httpx
import pytest

from evalforge_llm import LLMRequest, OpenAICompatibleProvider, ProviderError, ProviderErrorKind, chat_messages

SECRET = "sk-test-ne-doit-jamais-sortir"


def completion(content: str = "Bonjour", **extra) -> httpx.Response:
    body = {
        "id": "chatcmpl-42",
        "model": "deepseek-chat-2026-09",
        "choices": [{"message": {"role": "assistant", "content": content}}],
        "usage": {"prompt_tokens": 12, "completion_tokens": 3},
        **extra,
    }
    return httpx.Response(200, json=body)


class Recorder:
    def __init__(self, *responses: httpx.Response | Exception) -> None:
        self.responses = list(responses)
        self.requests: list[httpx.Request] = []
        self.sleeps: list[float] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)

    def provider(self, **kwargs) -> OpenAICompatibleProvider:
        client = httpx.AsyncClient(transport=httpx.MockTransport(self.handle))
        return OpenAICompatibleProvider(
            base_url="https://llm.example/v1/", api_key=SECRET, client=client, sleep=self.sleep, **kwargs
        )


REQUEST = LLMRequest(model="deepseek-chat", messages=chat_messages("Question ?", system="Sois bref."), max_tokens=50)


async def test_normalise_la_reponse_et_envoie_la_requete_attendue():
    recorder = Recorder(completion("Sous 14 jours."))

    response = await recorder.provider().generate(REQUEST)

    assert response.content == "Sous 14 jours."
    assert response.model == "deepseek-chat-2026-09"
    assert (response.input_tokens, response.output_tokens) == (12, 3)
    assert response.provider_request_id == "chatcmpl-42"
    assert response.latency_ms >= 0
    sent = recorder.requests[0]
    assert str(sent.url) == "https://llm.example/v1/chat/completions"
    assert sent.headers["authorization"] == f"Bearer {SECRET}"
    body = json.loads(sent.content)
    assert body == {
        "model": "deepseek-chat",
        "messages": [{"role": "system", "content": "Sois bref."}, {"role": "user", "content": "Question ?"}],
        "temperature": 0.0,
        "max_tokens": 50,
    }


async def test_un_429_est_rejoue_apres_le_delai_indique():
    recorder = Recorder(httpx.Response(429, headers={"Retry-After": "2"}, text="slow down"), completion())

    response = await recorder.provider().generate(REQUEST)

    assert response.content == "Bonjour"
    assert recorder.sleeps == [2.0]


async def test_le_delai_impose_par_le_fournisseur_est_plafonne():
    recorder = Recorder(httpx.Response(429, headers={"Retry-After": "3600"}), completion())

    await recorder.provider(max_retry_after_seconds=10).generate(REQUEST)

    assert recorder.sleeps == [10.0]


async def test_les_erreurs_serveur_sont_rejouees_avec_un_delai_croissant_puis_remontees():
    recorder = Recorder(*(httpx.Response(503, text="indisponible") for _ in range(3)))

    with pytest.raises(ProviderError) as raised:
        await recorder.provider(max_attempts=3, backoff_seconds=0.5).generate(REQUEST)

    assert raised.value.kind is ProviderErrorKind.SERVER
    assert raised.value.attempts == 3
    assert recorder.sleeps == [0.5, 1.0]
    assert "après 3 tentatives" in str(raised.value)


async def test_un_refus_d_authentification_n_est_pas_rejoue():
    recorder = Recorder(httpx.Response(401, text=f"bad key {SECRET}"))

    with pytest.raises(ProviderError) as raised:
        await recorder.provider().generate(REQUEST)

    assert raised.value.kind is ProviderErrorKind.AUTHENTICATION
    assert len(recorder.requests) == 1
    assert SECRET not in str(raised.value)


async def test_un_delai_depasse_est_normalise_et_rejoue():
    recorder = Recorder(httpx.ReadTimeout("lent"), completion())

    response = await recorder.provider().generate(REQUEST)

    assert response.content == "Bonjour"
    assert len(recorder.requests) == 2


async def test_une_reponse_illisible_n_est_pas_rejouee():
    recorder = Recorder(httpx.Response(200, json={"choices": []}))

    with pytest.raises(ProviderError) as raised:
        await recorder.provider().generate(REQUEST)

    assert raised.value.kind is ProviderErrorKind.INVALID_RESPONSE
    assert len(recorder.requests) == 1


def test_la_cle_n_apparait_pas_dans_la_representation():
    assert SECRET not in repr(Recorder().provider())
