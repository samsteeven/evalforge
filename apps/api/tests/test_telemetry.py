"""Une trace de bout en bout : requête HTTP, outbox, RabbitMQ, worker, cas évalués, appels aux modèles, webhook."""

import asyncio

import pytest

from evalforge_llm.telemetry import capture_spans
from helpers import QUESTIONS, post, seed, start_run, wait_for_run


@pytest.fixture
def spans():
    exporter = capture_spans()
    yield exporter
    exporter.clear()


async def test_le_run_continue_la_trace_de_la_requete_qui_l_a_lance(client, fake_llm, spans):
    fake_llm.answers = dict(QUESTIONS)
    data = await seed(client, models=("parrot",))
    await post(client, f"/projects/{data['project']['id']}/webhooks", data["headers"], {"url": "https://ok.test/x"})

    run_id = await start_run(client, data)
    await wait_for_run(client, data["headers"], run_id)
    async with asyncio.timeout(10):  # la livraison du webhook termine la trace
        while not [s for s in spans.get_finished_spans() if s.name == "process evaluation.completed"]:
            await asyncio.sleep(0.05)

    finished = spans.get_finished_spans()
    (request,) = [
        s for s in finished if s.name == "POST /api/v1/experiments/{experiment_id}/runs" and s.kind.name == "SERVER"
    ]
    trace = [s for s in finished if s.context.trace_id == request.context.trace_id]
    names = [s.name for s in trace]
    # Le worker reprend le contexte transporté par l'événement : même trace que la requête.
    (process,) = [s for s in trace if s.name == "process evaluation.requested"]
    assert process.attributes["evalforge.run.id"] == run_id
    assert process.attributes["messaging.system"] == "rabbitmq"
    cases = [s for s in trace if s.name == "evaluate_case"]
    assert len(cases) == 5
    assert all(s.parent.span_id == process.context.span_id for s in cases)
    calls = [s for s in trace if s.name == "chat parrot"]
    assert len(calls) == 5
    assert {s.parent.span_id for s in calls} == {s.context.span_id for s in cases}
    assert all(s.attributes["gen_ai.usage.input_tokens"] > 0 for s in calls)
    # La fin du run (événement écrit par le worker) porte la même trace jusqu'au webhook.
    assert "process evaluation.completed" in names


async def test_un_cas_en_echec_est_visible_dans_la_trace(client, fake_llm, spans):
    data = await seed(client, models=("vague",))

    run_id = await start_run(client, data)
    await wait_for_run(client, data["headers"], run_id)

    cases = [
        s
        for s in spans.get_finished_spans()
        if s.name == "evaluate_case" and s.attributes.get("evalforge.case.id") == "support-01"
    ]
    assert cases
    assert cases[-1].attributes["evalforge.case.status"] == "success"
    assert list(cases[-1].attributes["evalforge.metrics.failed"]) == ["exact_match", "contains_expected"]
