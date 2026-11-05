"""Exécution asynchrone : outbox, RabbitMQ, workers. Les scénarios obligatoires du blueprint.

1. Une évaluation n'est pas perdue si le courtier (ou l'API) tombe après l'enregistrement de son événement.
2. Un même événement reçu deux fois ne crée pas deux résultats.
4. Un worker interrompu : un autre reprend le run sans refaire les réponses déjà enregistrées.
+ Une erreur persistante : nouvelles tentatives, puis file morte et run en échec, avec son événement.
"""

import asyncio
import uuid

import aio_pika
from cryptography.fernet import Fernet
from pydantic import SecretStr
from sqlalchemy import select, text

from evalforge_api.messaging.models import OutboxEvent
from evalforge_api.messaging.outbox import to_message
from evalforge_api.messaging.topology import Topology
from evalforge_api.modules.evaluations.runner import _persist
from evalforge_engine import CaseOutcome, CaseStatus, EvaluationCase, MetricOutput, TargetOutput
from helpers import QUESTIONS, seed, start_run, wait_for_run


async def outbox_events(client, run_id: str) -> list[OutboxEvent]:
    async with client.app.state.session_factory() as session:
        rows = await session.scalars(
            select(OutboxEvent).where(OutboxEvent.correlation_id == run_id).order_by(OutboxEvent.occurred_at)
        )
        return list(rows)


async def run_row(client, run_id: str) -> dict:
    async with client.app.state.session_factory() as session:
        row = await session.execute(
            text("select status, worker_id, attempts, completed_items from evaluation_runs where id = :id"),
            {"id": run_id},
        )
        return dict(row.mappings().one())


async def test_le_run_et_son_evenement_sont_enregistres_ensemble_puis_publies(client, fake_llm):
    fake_llm.answers = dict(QUESTIONS)
    data = await seed(client, models=("parrot",))

    run_id = await start_run(client, data)
    run = await wait_for_run(client, data["headers"], run_id)

    assert run["status"] == "completed"
    # L'événement de fin part au tour suivant du publieur.
    async with asyncio.timeout(10):
        while True:
            events = await outbox_events(client, run_id)
            if all(e.published_at for e in events):
                break
            await asyncio.sleep(0.1)
    assert [e.event_type for e in events] == ["evaluation.requested", "evaluation.completed"]
    assert events[1].envelope["payload"]["completed_items"] == 5


async def test_courtier_injoignable_l_evenement_attend_dans_l_outbox_puis_part(app_factory, settings, fake_llm):
    fake_llm.answers = dict(QUESTIONS)
    down = settings.model_copy(update={"rabbitmq_url": "amqp://evalforge:evalforge@127.0.0.1:1/"})

    async with app_factory(down, with_worker=False) as client:
        data = await seed(client, models=("parrot",))
        run_id = await start_run(client, data)
        async with asyncio.timeout(20):  # sous Windows, une connexion refusée met quelques secondes à échouer
            while not (await outbox_events(client, run_id))[0].attempts:
                await asyncio.sleep(0.1)
        (requested,) = await outbox_events(client, run_id)
        assert requested.published_at is None
        assert requested.attempts >= 1
        assert "Error" in requested.last_error or "error" in requested.last_error.lower()

    # Courtier de retour (et API redémarrée) : l'événement part, le worker exécute le run.
    async with app_factory() as client:
        run = await wait_for_run(client, data["headers"], run_id)
        assert run["status"] == "completed"
        assert run["completed_items"] == 5


async def test_un_meme_evenement_recu_deux_fois_ne_cree_rien_de_plus(client, settings, fake_llm):
    fake_llm.answers = dict(QUESTIONS)
    data = await seed(client, models=("parrot",))
    run_id = await start_run(client, data)
    await wait_for_run(client, data["headers"], run_id)
    calls = fake_llm.calls("parrot")
    before = (await client.get(f"/runs/{run_id}", headers=data["headers"])).json()

    # RabbitMQ livre « au moins une fois » : on rejoue le message à l'identique.
    (requested, _) = await outbox_events(client, run_id)
    connection = await aio_pika.connect(settings.rabbitmq_url)
    async with connection:
        channel = await connection.channel(publisher_confirms=True)
        exchange = await Topology(settings.rabbitmq_prefix).declare(channel)
        await exchange.publish(to_message(requested.envelope), routing_key="evaluation.requested")
        queue = await channel.get_queue(Topology(settings.rabbitmq_prefix).evaluations)
        async with asyncio.timeout(10):
            while (await queue.declare()).message_count:
                await asyncio.sleep(0.1)
    await asyncio.sleep(0.3)

    # Le résultat déjà écrit qu'on réécrirait (traitement rejoué) est ignoré lui aussi.
    first_case = (await client.get(f"/dataset-versions/{data['version']['id']}/cases", headers=data["headers"])).json()[
        0
    ]
    model_id = uuid.UUID(data["configs"][0]["id"])
    await _persist(
        client.app.state.session_factory,
        uuid.UUID(run_id),
        CaseOutcome(
            key=(uuid.UUID(first_case["id"]), model_id),
            case=EvaluationCase(id=first_case["external_id"], input=first_case["input"]),
            status=CaseStatus.SUCCESS,
            output=TargetOutput(text="Autre réponse", latency_ms=1),
            metrics=[MetricOutput(name="exact_match", score=0.0, passed=False)],
        ),
        {model_id: None},
    )

    after = (await client.get(f"/runs/{run_id}", headers=data["headers"])).json()
    results = (await client.get(f"/runs/{run_id}/results", headers=data["headers"])).json()
    assert fake_llm.calls("parrot") == calls
    assert after == before
    assert len(results) == 5
    assert results[0]["actual_output"] == "Sous 14 jours."


async def test_un_worker_arrete_en_plein_run_est_relaye_sans_refaire_le_travail(
    app_factory, worker_factory, settings, fake_llm
):
    fake_llm.answers = dict(QUESTIONS)
    async with app_factory(with_worker=False) as client:
        first = await worker_factory(worker_id="worker-a")
        data = await seed(client, models=("slow",))
        run_id = await start_run(client, data, max_concurrency=1)
        async with asyncio.timeout(10):
            while (await run_row(client, run_id))["completed_items"] < 2:
                await asyncio.sleep(0.05)

        await first.stop()  # panne : plus de battements, message rendu à RabbitMQ
        stopped = await run_row(client, run_id)
        assert (stopped["status"], stopped["worker_id"]) == ("running", "worker-a")
        done_before = stopped["completed_items"]

        await worker_factory(worker_id="worker-b")
        run = await wait_for_run(client, data["headers"], run_id, timeout=30)

        final = await run_row(client, run_id)
        results = (await client.get(f"/runs/{run_id}/results", headers=data["headers"])).json()
    assert run["status"] == "completed"
    assert (final["worker_id"], final["attempts"]) == ("worker-b", 2)
    assert len(results) == 5 == run["completed_items"]
    # Les réponses enregistrées par le premier worker ne sont pas redemandées. Au plus deux appels perdus à l'arrêt :
    # celui dont la réponse était en cours d'enregistrement, et le suivant, déjà lancé pendant cet enregistrement.
    assert fake_llm.calls("slow") <= 5 + 2
    assert fake_llm.calls("slow") >= 5
    assert done_before >= 2


async def test_un_doublon_recu_pendant_l_execution_ne_lance_pas_une_seconde_execution(
    app_factory, worker_factory, settings, fake_llm
):
    fake_llm.answers = dict(QUESTIONS)
    async with app_factory(with_worker=False) as client:
        await worker_factory(worker_id="worker-a")
        data = await seed(client, models=("slow",))
        run_id = await start_run(client, data, max_concurrency=1)
        async with asyncio.timeout(10):
            while (await run_row(client, run_id))["status"] != "running":
                await asyncio.sleep(0.05)

        # Le même message arrive à un second worker pendant que le premier travaille (bail valide).
        await worker_factory(worker_id="worker-b")
        requested = (await outbox_events(client, run_id))[0]
        connection = await aio_pika.connect(settings.rabbitmq_url)
        async with connection:
            channel = await connection.channel(publisher_confirms=True)
            exchange = await Topology(settings.rabbitmq_prefix).declare(channel)
            await exchange.publish(to_message(requested.envelope), routing_key="evaluation.requested")

        run = await wait_for_run(client, data["headers"], run_id, timeout=30)
        final = await run_row(client, run_id)

    assert run["status"] == "completed"
    # Une seule exécution : chaque cas n'est demandé qu'une fois, et le run n'a été pris qu'une fois.
    assert fake_llm.calls("slow") == 5
    assert (final["worker_id"], final["attempts"]) == ("worker-a", 1)


async def test_une_erreur_persistante_mene_a_la_file_morte_et_a_l_echec(
    app_factory, worker_factory, settings, fake_llm
):
    fake_llm.answers = dict(QUESTIONS)
    # Le worker n'a pas la même clé de chiffrement que l'API : la clé d'API du modèle est illisible à chaque essai.
    wrong_key = settings.model_copy(update={"secret_key": SecretStr(Fernet.generate_key().decode())})
    async with app_factory(with_worker=False) as client:
        await worker_factory(wrong_key)
        data = await seed(client, models=("parrot",))
        run_id = await start_run(client, data)
        run = await wait_for_run(client, data["headers"], run_id, timeout=30)

        events = await outbox_events(client, run_id)
        final = await run_row(client, run_id)

    assert run["status"] == "failed"
    assert "secret illisible" in run["error"]
    assert "après 2 tentatives" in run["error"]
    assert final["attempts"] == 2
    assert fake_llm.calls("parrot") == 0
    assert [e.event_type for e in events][-1] == "evaluation.failed"

    connection = await aio_pika.connect(settings.rabbitmq_url)
    async with connection:
        channel = await connection.channel()
        dead = await channel.get_queue(Topology(settings.rabbitmq_prefix).dead)
        message = await dead.get(no_ack=True, fail=False)
    assert message is not None
    assert message.headers["x-evalforge-attempt"] == 2
    assert "secret illisible" in message.headers["x-evalforge-last-error"]
