"""Isolation entre espaces de travail, et sort d'un run quand l'API s'arrête."""

import asyncio
import uuid

from evalforge_api.modules.evaluations.runner import _persist, execute_run
from evalforge_engine import CaseOutcome, CaseStatus, EvaluationCase, MetricOutput, TargetOutput
from helpers import QUESTIONS, create_workspace, seed, start_run, wait_for_run


async def test_un_resultat_rejoue_ou_un_run_relance_ne_duplique_rien(client, fake_llm):
    fake_llm.answers = dict(QUESTIONS)
    data = await seed(client, models=("parrot",))
    headers = data["headers"]
    run_id = await start_run(client, data)
    await wait_for_run(client, headers, run_id)
    state = client.app.state
    first_case = (await client.get(f"/dataset-versions/{data['version']['id']}/cases", headers=headers)).json()[0]
    model_id = uuid.UUID(data["configs"][0]["id"])
    replayed = CaseOutcome(
        key=(uuid.UUID(first_case["id"]), model_id),
        case=EvaluationCase(id=first_case["external_id"], input=first_case["input"]),
        status=CaseStatus.SUCCESS,
        output=TargetOutput(text="Autre réponse", latency_ms=1),
        metrics=[MetricOutput(name="exact_match", score=0.0, passed=False)],
    )
    before = (await client.get(f"/runs/{run_id}", headers=headers)).json()
    providers_built = []

    def provider_factory(**kwargs):
        providers_built.append(kwargs)
        raise AssertionError("un run terminé ne doit pas être réexécuté")

    await _persist(state.session_factory, uuid.UUID(run_id), replayed, {model_id: None})
    await execute_run(
        uuid.UUID(run_id),
        session_factory=state.session_factory,
        provider_factory=provider_factory,
        secret_box=state.secret_box,
    )

    run = (await client.get(f"/runs/{run_id}", headers=headers)).json()
    results = (await client.get(f"/runs/{run_id}/results", headers=headers)).json()
    assert providers_built == []
    assert run == before
    assert len(results) == 5
    assert results[0]["actual_output"] == "Sous 14 jours."


async def test_un_espace_ne_voit_jamais_les_donnees_d_un_autre(client, fake_llm):
    fake_llm.answers = dict(QUESTIONS)
    data = await seed(client, models=("parrot",))
    run_id = await start_run(client, data)
    await wait_for_run(client, data["headers"], run_id)
    intruder = await create_workspace(client)

    urls = [
        f"/projects/{data['project']['id']}",
        f"/projects/{data['project']['id']}/datasets",
        f"/projects/{data['project']['id']}/providers",
        f"/datasets/{data['dataset']['id']}/versions",
        f"/dataset-versions/{data['version']['id']}/cases",
        f"/experiments/{data['experiment']['id']}",
        f"/runs/{run_id}",
        f"/runs/{run_id}/results",
        f"/runs/{run_id}/metrics",
    ]
    for url in urls:
        response = await client.get(url, headers=intruder)
        assert response.status_code == 404, url

    assert (await client.get("/projects", headers=intruder)).json() == []
    launch = await client.post(
        f"/experiments/{data['experiment']['id']}/runs",
        json={"dataset_version_id": data["version"]["id"]},
        headers=intruder,
    )
    assert launch.status_code == 404


async def test_un_jeu_de_donnees_d_un_autre_projet_est_refuse(client):
    data = await seed(client, models=("parrot",))
    other = await seed(client, models=("parrot",))

    response = await client.post(
        f"/experiments/{data['experiment']['id']}/runs",
        json={"dataset_version_id": other["version"]["id"]},
        headers=data["headers"],
    )

    # Version d'un autre espace : introuvable, comme si elle n'existait pas.
    assert response.status_code == 404


async def test_un_run_interrompu_par_un_arret_de_l_api_est_marque_comme_tel(app_factory):
    async with app_factory() as client:
        data = await seed(client, models=("hang",))
        run_id = await start_run(client, data, timeout_seconds=600)
        async with asyncio.timeout(5):
            while (await client.get(f"/runs/{run_id}", headers=data["headers"])).json()["status"] != "running":
                await asyncio.sleep(0.05)
    # L'API s'est arrêtée pendant le run : au redémarrage, il est marqué interrompu au lieu de rester « running ».

    async with app_factory() as client:
        run = (await client.get(f"/runs/{run_id}", headers=data["headers"])).json()

    assert run["status"] == "interrupted"
    assert "relancez" in run["error"]
    assert run["completed_items"] == 0
