"""Isolation entre espaces de travail."""

from helpers import QUESTIONS, create_workspace, seed, start_run, wait_for_run


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
