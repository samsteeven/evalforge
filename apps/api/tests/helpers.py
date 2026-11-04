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


PASSWORD = "mot-de-passe-de-test"


def new_email() -> str:
    return f"user-{uuid.uuid4().hex[:10]}@atelier-nova.example"


async def register(client: httpx.AsyncClient, email: str | None = None) -> dict:
    """Inscrit un compte ; renvoie la session, l'e-mail et les en-têtes prêts à l'emploi pour son espace."""
    email = email or new_email()
    response = await client.post(
        "/auth/register", json={"email": email, "password": PASSWORD, "workspace_name": "Atelier Nova"}
    )
    assert response.status_code == 201, response.text
    session = response.json()
    auth = {"Authorization": f"Bearer {session['access_token']}"}
    me = (await client.get("/auth/me", headers=auth)).json()
    workspace_id = me["workspaces"][0]["workspace_id"]
    return {"email": email, "session": session, "auth": auth, "headers": {**auth, "X-Workspace-Id": workspace_id}}


async def create_workspace(client: httpx.AsyncClient) -> dict[str, str]:
    """Un nouveau compte et son espace : chaque test a ses propres données."""
    return (await register(client))["headers"]


async def join(client: httpx.AsyncClient, owner_headers: dict, role: str) -> dict[str, str]:
    """Un autre compte, ajouté à l'espace du propriétaire avec le rôle donné ; renvoie ses en-têtes."""
    other = await register(client)
    await post(client, "/workspaces/current/members", owner_headers, {"email": other["email"], "role": role})
    return {**other["auth"], "X-Workspace-Id": owner_headers["X-Workspace-Id"]}


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
