"""Traces des appels aux modèles : conventions GenAI d'OpenTelemetry, sans prompt ni clé dans les attributs."""

import httpx
import pytest
from opentelemetry.trace import StatusCode

from evalforge_llm import ProviderError
from evalforge_llm.telemetry import capture_spans
from test_openai_compatible import REQUEST, SECRET, Recorder, completion


@pytest.fixture
def spans():
    exporter = capture_spans()
    yield exporter
    exporter.clear()


async def test_un_appel_reussi_est_trace_avec_modele_et_tokens(spans):
    recorder = Recorder(httpx.Response(429, headers={"Retry-After": "1"}), completion("Sous 14 jours."))

    await recorder.provider().generate(REQUEST)

    (span,) = spans.get_finished_spans()
    assert span.name == "chat deepseek-chat"
    attributes = dict(span.attributes)
    assert attributes["gen_ai.operation.name"] == "chat"
    assert attributes["gen_ai.request.model"] == "deepseek-chat"
    assert attributes["gen_ai.response.model"] == "deepseek-chat-2026-09"
    assert (attributes["gen_ai.usage.input_tokens"], attributes["gen_ai.usage.output_tokens"]) == (12, 3)
    assert attributes["evalforge.attempts"] == 2  # un span pour l'appel, reprise comprise
    assert attributes["server.address"] == "llm.example"
    # Ni le prompt, ni la réponse, ni la clé : une trace se partage plus largement qu'une base.
    flat = repr(attributes)
    assert "Question ?" not in flat and "Sous 14 jours" not in flat and SECRET not in flat


async def test_un_echec_definitif_marque_le_span_en_erreur(spans):
    recorder = Recorder(httpx.Response(401, text="clé refusée"))

    with pytest.raises(ProviderError):
        await recorder.provider().generate(REQUEST)

    (span,) = spans.get_finished_spans()
    assert span.status.status_code is StatusCode.ERROR
    assert span.attributes["error.type"] == "authentication"
