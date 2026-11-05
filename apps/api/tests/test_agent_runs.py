"""Évaluer un agent par la plateforme : API, outbox, RabbitMQ, worker, puis l'agent de support réel."""

from helpers import create_workspace, post, wait_for_run

CASES = [
    {
        "id": "retour-casque",
        "type": "agent",
        "input": {"message": "Bonjour, commande CMD-1042 : comment faire un retour et être remboursé ?"},
        "expected": {
            "answer": "Retour possible sous 30 jours",
            "tools": ["find_order", "get_return_policy"],
            "arguments": {"find_order": {"order_id": "CMD-1042"}, "get_return_policy": {"category": "electronique"}},
        },
    },
    {
        "id": "pull-abime",
        "type": "agent",
        "input": {"message": "Mon pull de la commande CMD-1043 est arrivé abîmé."},
        "expected": {
            "tools": ["find_order", "create_ticket"],
            "arguments": {"find_order": {"order_id": "CMD-1043"}, "create_ticket": {"priority": "haute"}},
        },
    },
]
METRICS = ["tool_selection", "tool_order", "tool_arguments", "step_efficiency", "task_completion"]


async def agent_experiment(client) -> dict:
    headers = await create_workspace(client)
    project = await post(client, "/projects", headers, {"name": "Agent de support", "type": "agent"})
    dataset = await post(client, f"/projects/{project['id']}/datasets", headers, {"name": "Demandes clients"})
    version = await post(client, f"/datasets/{dataset['id']}/versions", headers, {"cases": CASES})
    prompt = await post(
        client, f"/projects/{project['id']}/prompts", headers, {"name": "brut", "template": "{{ message }}"}
    )
    configs = [
        await post(
            client,
            f"/projects/{project['id']}/providers",
            headers,
            {"name": name, "provider_kind": "evalforge_agent", "base_url": f"http://{name}.test", "model_name": name},
        )
        for name in ("support-agent", "careless-agent")
    ]
    experiment = await post(
        client,
        f"/projects/{project['id']}/experiments",
        headers,
        {
            "name": "Agent soigneux contre négligent",
            "prompt_version_id": prompt["id"],
            "model_configuration_ids": [c["id"] for c in configs],
            "metrics": METRICS,
        },
    )
    return {"headers": headers, "version": version, "experiment": experiment, "configs": configs}


async def test_un_agent_est_evalue_sur_sa_trajectoire(client):
    data = await agent_experiment(client)
    run = await post(
        client,
        f"/experiments/{data['experiment']['id']}/runs",
        data["headers"],
        {"dataset_version_id": data["version"]["id"]},
        status=202,
    )

    finished = await wait_for_run(client, data["headers"], run["run_id"])

    assert finished["status"] == "completed", finished["error"]
    results = (await client.get(f"/runs/{run['run_id']}/results", headers=data["headers"])).json()
    careful = next(r for r in results if r["case_id"] == "retour-casque" and r["model"] == "support-agent")
    assert [s["tool"] for s in careful["trajectory"]] == ["find_order", "get_return_policy"]
    assert careful["stop_reason"] == "completed"
    assert all(m["passed"] for m in careful["metrics"]), careful["metrics"]

    summary = (await client.get(f"/runs/{run['run_id']}/metrics", headers=data["headers"])).json()
    rates = {m["model"]: {x["name"]: x["pass_rate"] for x in m["metrics"]} for m in summary["models"]}
    assert rates["support-agent"] == dict.fromkeys(METRICS, 1.0)
    assert rates["careless-agent"]["tool_selection"] == 0.0
    assert rates["careless-agent"]["step_efficiency"] == 0.0
    assert rates["careless-agent"]["task_completion"] == 1.0

    report = (await client.get(f"/runs/{run['run_id']}/report", headers=data["headers"])).text
    assert "Trajectoire" in report
    assert "escalate_to_human" in report


async def test_un_agent_ne_peut_pas_servir_de_juge(client):
    data = await agent_experiment(client)
    project_id = data["experiment"]["project_id"]
    prompts = (await client.get(f"/projects/{project_id}/prompts", headers=data["headers"])).json()

    response = await client.post(
        f"/projects/{project_id}/experiments",
        json={
            "name": "Juge agent",
            "prompt_version_id": prompts[0]["id"],
            "model_configuration_ids": [data["configs"][0]["id"]],
            "metrics": ["judge_correctness"],
            "judge_model_configuration_id": data["configs"][1]["id"],
        },
        headers=data["headers"],
    )

    assert response.status_code == 422
    assert "agent ne peut pas servir de juge" in response.text
