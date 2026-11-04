"""Juge LLM dans un run, import de fichier, rapport HTML."""

import json

from helpers import QUESTIONS, cases, create_workspace, post, seed, wait_for_run

PROVIDER = {"provider_kind": "openai_compatible", "base_url": "https://llm.example/v1"}


async def judged_experiment(client, data, judge_model: str = "judge") -> dict:
    project = data["project"]["id"]
    judge = await post(
        client,
        f"/projects/{project}/providers",
        data["headers"],
        {"name": judge_model, "model_name": judge_model, **PROVIDER},
    )
    return await post(
        client,
        f"/projects/{project}/experiments",
        data["headers"],
        {
            "name": f"Avec juge {judge_model}",
            "prompt_version_id": data["prompt"]["id"],
            "model_configuration_ids": [c["id"] for c in data["configs"]],
            "metrics": ["exact_match", "judge_correctness"],
            "judge_model_configuration_id": judge["id"],
        },
    )


async def launch(client, data, experiment) -> dict:
    run = await post(
        client,
        f"/experiments/{experiment['id']}/runs",
        data["headers"],
        {"dataset_version_id": data["version"]["id"]},
        status=202,
    )
    return await wait_for_run(client, data["headers"], run["run_id"])


async def test_le_juge_note_chaque_reponse_et_sa_methode_est_figee_dans_le_run(client, fake_llm):
    fake_llm.answers = dict(QUESTIONS)
    data = await seed(client)
    experiment = await judged_experiment(client, data)

    run = await launch(client, data, experiment)

    assert run["status"] == "completed"
    assert run["configuration"]["judge"]["model_name"] == "judge"
    assert experiment["judge"]["name"] == "judge"
    summary = (await client.get(f"/runs/{run['id']}/metrics", headers=data["headers"])).json()
    rates = {m["model"]: {x["name"]: x["pass_rate"] for x in m["metrics"]} for m in summary["models"]}
    assert rates == {
        "parrot": {"exact_match": 1.0, "judge_correctness": 1.0},
        "vague": {"exact_match": 0.0, "judge_correctness": 0.0},
    }
    results = (await client.get(f"/runs/{run['id']}/results", headers=data["headers"])).json()
    judged = next(m for m in results[0]["metrics"] if m["name"] == "judge_correctness")
    assert (judged["score"], judged["reason"]) == (1.0, "Même information.")
    judge_prompts = [json.loads(r.content) for r in fake_llm.requests if json.loads(r.content)["model"] == "judge"]
    assert len(judge_prompts) == 10
    assert judge_prompts[0]["temperature"] == 0.0


async def test_un_juge_illisible_rend_la_metrique_indisponible_sans_faire_echouer_le_run(client, fake_llm):
    fake_llm.answers = dict(QUESTIONS)
    data = await seed(client, models=("parrot",))
    experiment = await judged_experiment(client, data, judge_model="judge-garbage")

    run = await launch(client, data, experiment)

    assert run["status"] == "completed"
    summary = (await client.get(f"/runs/{run['id']}/metrics", headers=data["headers"])).json()
    judge = next(m for m in summary["models"][0]["metrics"] if m["name"] == "judge_correctness")
    assert (judge["evaluated"], judge["unavailable"], judge["pass_rate"]) == (0, 5, None)
    exact = next(m for m in summary["models"][0]["metrics"] if m["name"] == "exact_match")
    assert exact["pass_rate"] == 1.0


async def test_une_metrique_du_juge_sans_modele_juge_est_refusee(client):
    data = await seed(client, models=("parrot",))

    response = await client.post(
        f"/projects/{data['project']['id']}/experiments",
        json={
            "name": "Sans juge",
            "prompt_version_id": data["prompt"]["id"],
            "model_configuration_ids": [data["configs"][0]["id"]],
            "metrics": ["judge_faithfulness"],
        },
        headers=data["headers"],
    )

    assert response.status_code == 422
    assert "modèle juge" in response.text


async def test_la_liste_des_metriques_dit_ce_dont_chacune_a_besoin(client):
    headers = await create_workspace(client)

    metrics = {m["name"]: m for m in (await client.get("/metrics", headers=headers)).json()}

    assert metrics["judge_faithfulness"]["needs_judge"] is True
    assert metrics["judge_faithfulness"]["requires_context"] is True
    assert metrics["exact_match"]["needs_judge"] is False


# --- Import de fichier -------------------------------------------------------------------------------------------


async def dataset(client) -> tuple[dict, str]:
    headers = await create_workspace(client)
    project = await post(client, "/projects", headers, {"name": "Support"})
    created = await post(client, f"/projects/{project['id']}/datasets", headers, {"name": "FAQ"})
    return headers, created["id"]


async def test_import_csv_puis_json_identique_reconnu(client):
    headers, dataset_id = await dataset(client)
    csv = "id;type;question;expected_answer;context;metadata.category\n" + "".join(
        f"support-{i:02d};rag;{q};{a};Politique : {a};customer-support\n" for i, (q, a) in enumerate(QUESTIONS, start=1)
    )

    imported = await client.post(
        f"/datasets/{dataset_id}/versions/import",
        files={"file": ("faq.csv", csv.encode(), "text/csv")},
        headers=headers,
    )
    same = await client.post(
        f"/datasets/{dataset_id}/versions/import",
        files={"file": ("faq.json", json.dumps(cases()).encode(), "application/json")},
        headers=headers,
    )

    assert imported.status_code == 201, imported.text
    assert imported.json()["case_count"] == 5
    # Le CSV et le JSON décrivent les mêmes cas : même empreinte, aucune nouvelle version.
    assert same.status_code == 200
    assert same.json()["id"] == imported.json()["id"]
    stored = (await client.get(f"/dataset-versions/{imported.json()['id']}/cases", headers=headers)).json()
    assert stored[0]["input"] == {"question": QUESTIONS[0][0]}
    assert stored[0]["context"] == [f"Politique : {QUESTIONS[0][1]}"]


async def test_un_fichier_invalide_est_refuse_avec_un_message_utile(client):
    headers, dataset_id = await dataset(client)
    attempts = [
        (("faq.csv", b"expected_answer\nx\n", "text/csv"), "aucune colonne d'entrée"),
        (("faq.xlsx", b"PK", "application/octet-stream"), ".json ou .csv"),
        (("faq.csv", "question\nDélai ?\n".encode("latin-1"), "text/csv"), "UTF-8"),
        (("faq.json", b'[{"input": {}}]', "application/json"), "cases.0.input"),
    ]

    for file, message in attempts:
        response = await client.post(f"/datasets/{dataset_id}/versions/import", files={"file": file}, headers=headers)
        assert response.status_code == 422, file[0]
        assert message in response.json()["detail"], response.json()["detail"]


# --- Rapport -----------------------------------------------------------------------------------------------------


async def test_le_rapport_html_reprend_la_configuration_et_les_resultats(client, fake_llm):
    fake_llm.answers = dict(QUESTIONS)
    data = await seed(client)
    experiment = await judged_experiment(client, data)
    run = await launch(client, data, experiment)

    response = await client.get(f"/runs/{run['id']}/report", headers=data["headers"])

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    html = response.text
    assert "Run d&#39;évaluation : Avec juge judge" in html
    assert "faq v1" in html
    assert "judge (judge)" in html
    assert "Même information." in html
    assert html.count("support-01") == 2  # une ligne par modèle
    assert "inconnu" in html  # « vague » n'a pas de prix


async def test_le_rapport_d_un_autre_espace_est_introuvable(client, fake_llm):
    fake_llm.answers = dict(QUESTIONS)
    data = await seed(client, models=("parrot",))
    run = await launch(client, data, data["experiment"])
    intruder = await create_workspace(client)

    assert (await client.get(f"/runs/{run['id']}/report", headers=intruder)).status_code == 404
