"""Baseline et détection de régression : le scénario de la démonstration du blueprint.

On évalue, on adopte le run comme baseline, on change le prompt, on réévalue : EvalForge dit ce qui a régressé.
"""

from helpers import QUESTIONS, TEMPLATE, join, post, seed, start_run, wait_for_run


async def new_prompt_experiment(client, data, template: str, name: str) -> dict:
    project = data["project"]["id"]
    # Un prompt identique renvoie sa version existante (200), un nouveau en crée une (201).
    response = await client.post(
        f"/projects/{project}/prompts", json={"name": "faq", "template": template}, headers=data["headers"]
    )
    assert response.status_code in (200, 201), response.text
    prompt = response.json()
    return await post(
        client,
        f"/projects/{project}/experiments",
        data["headers"],
        {
            "name": name,
            "prompt_version_id": prompt["id"],
            "model_configuration_ids": [c["id"] for c in data["configs"]],
            "metrics": ["exact_match"],
        },
    )


async def run_experiment(client, data, experiment) -> str:
    run = await post(
        client,
        f"/experiments/{experiment['id']}/runs",
        data["headers"],
        {"dataset_version_id": data["version"]["id"]},
        status=202,
    )
    finished = await wait_for_run(client, data["headers"], run["run_id"])
    assert finished["status"] == "completed"
    return run["run_id"]


async def test_un_changement_de_prompt_qui_casse_des_reponses_est_detecte(client, fake_llm):
    fake_llm.answers = dict(QUESTIONS)
    data = await seed(client, models=("parrot",))
    project = data["project"]["id"]
    baseline_run = await wait_for_run(client, data["headers"], await start_run(client, data))

    adopted = await client.put(
        f"/projects/{project}/baseline", json={"run_id": baseline_run["id"], "tolerance": 0.0}, headers=data["headers"]
    )
    assert adopted.status_code == 200
    # Nouveau prompt sans la question : le modèle ne la voit plus et répond « ? ».
    broken = await new_prompt_experiment(client, data, "Contexte : {{ context }}", "Prompt sans question")
    candidate = await run_experiment(client, data, broken)

    response = await client.get(f"/runs/{candidate}/comparison", headers=data["headers"])

    assert response.status_code == 200, response.text
    result = response.json()
    assert result["baseline_run_id"] == baseline_run["id"]
    comparison = result["comparison"]
    assert comparison["regressed"] is True
    (model,) = comparison["models"]
    (metric,) = model["metrics"]
    assert (metric["baseline_rate"], metric["candidate_rate"], metric["delta"]) == (1.0, 0.0, -1.0)
    assert (metric["paired"], metric["broken"], metric["fixed"]) == (5, 5, 0)
    assert metric["p_value"] == 0.0625  # 5 cas cassés sur 5 : p = 2 × 0,5⁵
    assert metric["significant"] is False  # 5 cas, c'est peu : la régression est nette mais pas « prouvée »
    assert sorted(c["case_id"] for c in comparison["changes"]) == [f"support-{i:02d}" for i in range(1, 6)]


async def test_le_meme_prompt_ne_regresse_pas(client, fake_llm):
    fake_llm.answers = dict(QUESTIONS)
    data = await seed(client, models=("parrot",))
    first = await wait_for_run(client, data["headers"], await start_run(client, data))
    await client.put(
        f"/projects/{data['project']['id']}/baseline", json={"run_id": first["id"]}, headers=data["headers"]
    )
    same = await new_prompt_experiment(client, data, TEMPLATE, "Même prompt")

    candidate = await run_experiment(client, data, same)
    comparison = (await client.get(f"/runs/{candidate}/comparison", headers=data["headers"])).json()["comparison"]

    assert comparison["regressed"] is False
    assert comparison["changes"] == []
    assert comparison["tolerance"] == 0.02


async def test_une_baseline_doit_etre_un_run_termine_du_projet(client, fake_llm):
    fake_llm.answers = dict(QUESTIONS)
    data = await seed(client, models=("parrot",))
    other = await seed(client, models=("parrot",))
    other_run = await wait_for_run(client, other["headers"], await start_run(client, other))
    project = data["project"]["id"]

    foreign = await client.put(
        f"/projects/{project}/baseline", json={"run_id": other_run["id"]}, headers=data["headers"]
    )
    missing = await client.get(f"/runs/{other_run['id']}/comparison", headers=other["headers"])
    no_baseline = await client.get(f"/projects/{project}/baseline", headers=data["headers"])

    assert foreign.status_code == 404  # run d'un autre espace : introuvable
    assert missing.status_code == 404  # pas de baseline dans ce projet
    assert no_baseline.json() is None


async def test_un_lecteur_ne_change_pas_la_baseline(client, fake_llm):
    fake_llm.answers = dict(QUESTIONS)
    data = await seed(client, models=("parrot",))
    run = await wait_for_run(client, data["headers"], await start_run(client, data))
    viewer = await join(client, data["headers"], "viewer")

    response = await client.put(
        f"/projects/{data['project']['id']}/baseline", json={"run_id": run["id"]}, headers=viewer
    )

    assert response.status_code == 403
