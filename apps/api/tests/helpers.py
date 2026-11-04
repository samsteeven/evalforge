import asyncio
import uuid

import httpx

QUESTIONS = [
    ("Quel est le délai de remboursement ?", "Sous 14 jours."),
    ("Les frais de retour sont-ils payants ?", "Non, ils sont gratuits."),
    ("Peut-on payer en trois fois ?", "Oui, dès 100 euros."),
    ("Où suivre ma commande ?", "Dans l'onglet Mes commandes."),
    ("Comment joindre le support ?", "Par le chat, de 9 h à 18 h."),
]

TEMPLATE = "Réponds en une phrase.\nContexte : {{ context }}\nQuestion : {{ question }}"


def cases() -> list[dict]:
    return [
        {
            "id": f"support-{index:02d}",
            "type": "rag",
            "input": {"question": question},
            "expected": {"answer": answer},
            "context": [f"Politique : {answer}"],
            "metadata": {"category": "customer-support"},
        }
        for index, (question, answer) in enumerate(QUESTIONS, start=1)
    ]


async def create_workspace(client: httpx.AsyncClient) -> dict[str, str]:
    slug = f"ws-{uuid.uuid4().hex[:10]}"
    response = await client.post("/workspaces", json={"name": "Atelier Nova", "slug": slug})
    assert response.status_code == 201, response.text
    return {"X-Workspace-Id": response.json()["id"]}


async def post(client: httpx.AsyncClient, url: str, headers: dict, body: dict, status: int = 201) -> dict:
    response = await client.post(url, json=body, headers=headers)
    assert response.status_code == status, response.text
    return response.json()


async def seed(client: httpx.AsyncClient, *, models: tuple[str, ...] = ("parrot", "vague"), template: str = TEMPLATE):
    """Le parcours de la tranche verticale jusqu'à l'expérience : projet, cinq questions, prompt, modèles."""
    headers = await create_workspace(client)
    project = await post(client, "/projects", headers, {"name": "Customer Support AI", "type": "rag"})
    dataset = await post(client, f"/projects/{project['id']}/datasets", headers, {"name": "FAQ support"})
    version = await post(client, f"/datasets/{dataset['id']}/versions", headers, {"cases": cases()})
    prompt = await post(client, f"/projects/{project['id']}/prompts", headers, {"name": "faq", "template": template})
    configs = []
    for model in models:
        body = {
            "name": model,
            "provider_kind": "openai_compatible",
            "base_url": "https://llm.example/v1",
            "model_name": model,
            "api_key": f"sk-{model}-secret",
        }
        if model == "parrot":
            body["pricing"] = {"input_per_million_usd": 0.27, "output_per_million_usd": 1.10, "source": "test"}
        configs.append(await post(client, f"/projects/{project['id']}/providers", headers, body))
    experiment = await post(
        client,
        f"/projects/{project['id']}/experiments",
        headers,
        {
            "name": "Comparaison",
            "prompt_version_id": prompt["id"],
            "model_configuration_ids": [config["id"] for config in configs],
            "metrics": ["exact_match", "contains_expected"],
        },
    )
    return {
        "headers": headers,
        "project": project,
        "dataset": dataset,
        "version": version,
        "prompt": prompt,
        "configs": configs,
        "experiment": experiment,
    }


async def start_run(client: httpx.AsyncClient, data: dict, **execution) -> str:
    body = {"dataset_version_id": data["version"]["id"], "execution": {"max_concurrency": 3, **execution}}
    run = await post(client, f"/experiments/{data['experiment']['id']}/runs", data["headers"], body, status=202)
    assert run["status"] == "queued"
    return run["run_id"]


async def wait_for_run(client: httpx.AsyncClient, headers: dict, run_id: str, timeout: float = 15) -> dict:
    async with asyncio.timeout(timeout):
        while True:
            run = (await client.get(f"/runs/{run_id}", headers=headers)).json()
            if run["status"] not in ("queued", "running"):
                return run
            await asyncio.sleep(0.05)
