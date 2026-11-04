"""La tranche verticale : projet → cinq questions → deux modèles → résultats et comparaison."""

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from helpers import QUESTIONS, TEMPLATE, cases, create_workspace, post, seed, start_run, wait_for_run


async def test_une_experience_compare_deux_modeles_sur_cinq_questions(client, fake_llm):
    fake_llm.answers = dict(QUESTIONS)
    data = await seed(client)
    headers = data["headers"]

    run_id = await start_run(client, data)
    run = await wait_for_run(client, headers, run_id)

    assert run["status"] == "completed", run["error"]
    assert (run["total_items"], run["completed_items"]) == (10, 10)
    # Ce qui a été exécuté est figé dans le run, sans les clés.
    assert run["configuration"]["prompt"]["template"] == TEMPLATE
    assert run["configuration"]["dataset_version"]["checksum"] == data["version"]["checksum"]
    assert [model["model_name"] for model in run["configuration"]["models"]] == ["parrot", "vague"]
    assert "secret" not in str(run)

    results = (await client.get(f"/runs/{run_id}/results", headers=headers)).json()
    assert len(results) == 10
    first = results[0]
    assert (first["case_id"], first["model"], first["status"]) == ("support-01", "parrot", "success")
    assert first["actual_output"] == "Sous 14 jours."
    assert first["provider_model"] == "parrot-2026-11"
    assert {m["name"]: m["passed"] for m in first["metrics"]} == {"contains_expected": True, "exact_match": True}
    assert results[1]["model"] == "vague"
    assert results[1]["actual_output"] == "Je ne sais pas."

    summary = (await client.get(f"/runs/{run_id}/metrics", headers=headers)).json()
    parrot, vague = summary["models"]
    assert parrot["model"] == "parrot"
    assert {m["name"]: m["pass_rate"] for m in parrot["metrics"]} == {"exact_match": 1.0, "contains_expected": 1.0}
    assert {m["name"]: m["pass_rate"] for m in vague["metrics"]} == {"exact_match": 0.0, "contains_expected": 0.0}
    assert parrot["performance"]["success_rate"] == 1.0
    assert parrot["performance"]["p95_latency_ms"] is not None
    assert parrot["performance"]["input_tokens"] > 0
    # Prix renseigné : coût calculé. Pas de prix : coût inconnu, jamais 0.
    assert parrot["performance"]["cost_usd"] > 0
    assert vague["performance"]["cost_usd"] is None
    assert vague["performance"]["cost_known_for"] == 0

    # Le prompt envoyé au modèle contient le contexte et la question du cas.
    prompts = [request.content.decode() for request in fake_llm.requests]
    assert any("Politique : Sous 14 jours." in prompt and "Quel est le d" in prompt for prompt in prompts)


async def test_une_version_de_jeu_de_donnees_est_immuable_et_dedupliquee(client):
    headers = await create_workspace(client)
    project = await post(client, "/projects", headers, {"name": "Support"})
    dataset = await post(client, f"/projects/{project['id']}/datasets", headers, {"name": "FAQ"})
    url = f"/datasets/{dataset['id']}/versions"

    v1 = await post(client, url, headers, {"cases": cases()})
    again = await post(client, url, headers, {"cases": cases()}, status=200)
    changed = cases()
    changed[0]["expected"]["answer"] = "Sous 30 jours."
    v2 = await post(client, url, headers, {"cases": changed})

    assert (v1["version"], again["id"], v2["version"]) == (1, v1["id"], 2)
    assert v1["checksum"] != v2["checksum"]
    v1_cases = (await client.get(f"/dataset-versions/{v1['id']}/cases", headers=headers)).json()
    assert v1_cases[0]["expected"] == {"answer": "Sous 14 jours."}
    assert [case["external_id"] for case in v1_cases] == [f"support-{i:02d}" for i in range(1, 6)]


async def test_des_identifiants_de_cas_en_double_sont_refuses(client):
    headers = await create_workspace(client)
    project = await post(client, "/projects", headers, {"name": "Support"})
    dataset = await post(client, f"/projects/{project['id']}/datasets", headers, {"name": "FAQ"})
    duplicated = [cases()[0], cases()[0]]

    response = await client.post(f"/datasets/{dataset['id']}/versions", json={"cases": duplicated}, headers=headers)

    assert response.status_code == 422
    assert "support-01" in response.text


async def test_un_prompt_modifie_cree_une_nouvelle_version(client):
    headers = await create_workspace(client)
    project = await post(client, "/projects", headers, {"name": "Support"})
    url = f"/projects/{project['id']}/prompts"

    v1 = await post(client, url, headers, {"name": "faq", "template": TEMPLATE})
    same = await post(client, url, headers, {"name": "faq", "template": TEMPLATE}, status=200)
    v2 = await post(client, url, headers, {"name": "faq", "template": TEMPLATE + "\nSois bref."})

    assert (v1["version"], same["id"], v2["version"]) == (1, v1["id"], 2)
    assert v1["variables"] == ["context", "question"]


async def test_une_metrique_inconnue_est_refusee(client):
    data = await seed(client, models=("parrot",))

    response = await client.post(
        f"/projects/{data['project']['id']}/experiments",
        json={
            "name": "Autre",
            "prompt_version_id": data["prompt"]["id"],
            "model_configuration_ids": [data["configs"][0]["id"]],
            "metrics": ["faithfulness"],
        },
        headers=data["headers"],
    )

    assert response.status_code == 422
    assert "faithfulness" in response.text
    assert "exact_match" in response.text


async def test_un_cas_en_erreur_ne_fait_pas_echouer_le_run_ni_ne_compte_comme_zero(client):
    data = await seed(client, models=("parrot",), template="{{ question }} en {{ langue }}")

    run = await wait_for_run(client, data["headers"], await start_run(client, data))

    assert run["status"] == "completed"
    results = (await client.get(f"/runs/{run['id']}/results", headers=data["headers"])).json()
    assert {result["status"] for result in results} == {"error"}
    assert "variable « langue » absente" in results[0]["error"]
    summary = (await client.get(f"/runs/{run['id']}/metrics", headers=data["headers"])).json()
    (model,) = summary["models"]
    assert model["performance"]["errors"] == 5
    assert model["performance"]["success_rate"] == 0.0
    assert {m["name"]: (m["evaluated"], m["mean_score"]) for m in model["metrics"]} == {
        "exact_match": (0, None),
        "contains_expected": (0, None),
    }


async def test_la_cle_d_api_est_chiffree_et_jamais_renvoyee(client, settings, fake_llm):
    fake_llm.answers = dict(QUESTIONS)
    data = await seed(client, models=("parrot",))
    config = data["configs"][0]

    assert config["has_api_key"] is True
    assert "api_key" not in config
    engine = create_async_engine(settings.database_url)
    async with engine.connect() as connection:
        stored = await connection.scalar(
            text("select api_key_encrypted from model_configurations where id = :id"), {"id": config["id"]}
        )
    await engine.dispose()
    assert stored and "sk-parrot-secret" not in stored

    await wait_for_run(client, data["headers"], await start_run(client, data))
    assert fake_llm.requests[0].headers["authorization"] == "Bearer sk-parrot-secret"


@pytest.mark.parametrize("header", [None, "00000000-0000-0000-0000-000000000000"])
async def test_un_espace_de_travail_absent_ou_inconnu_est_refuse(client, header):
    headers = {"X-Workspace-Id": header} if header else {}

    response = await client.get("/projects", headers=headers)

    assert response.status_code == (404 if header else 422)
