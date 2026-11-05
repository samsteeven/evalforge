"""Vérifie qu'un run d'agent va au bout, et qu'il produit une seule trace à travers l'API, le worker et l'agent.

Prérequis : Jaeger (docker compose --profile observability up -d jaeger), le faux fournisseur, l'agent de support,
l'API et un worker démarrés avec OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:4318.

    uv run python scripts/smoke_trace.py            # code 0 si la trace est complète, 1 sinon
    uv run python scripts/smoke_trace.py --sans-trace    # le run seulement (images, Kubernetes)

EVALFORGE_SMOKE_API (http://127.0.0.1:8000) et EVALFORGE_SMOKE_AGENT_URL (http://127.0.0.1:8200, l'adresse de l'agent
vue depuis le worker) s'adaptent à la pile visée.
"""

import os
import sys
import time
import uuid
from datetime import UTC, datetime, timedelta

import httpx

API = os.environ.get("EVALFORGE_SMOKE_API", "http://127.0.0.1:8000").rstrip("/") + "/api/v1"
AGENT_URL = os.environ.get("EVALFORGE_SMOKE_AGENT_URL", "http://127.0.0.1:8200")
JAEGER = "http://127.0.0.1:16686"
EXPECTED_SERVICES = {"evalforge-api", "evalforge-worker", "evalforge-support-agent"}
CASES = [
    {
        "id": "retour-casque",
        "type": "agent",
        "input": {"message": "Commande CMD-1042 : comment faire un retour ?"},
        "expected": {"tools": ["find_order", "get_return_policy"]},
    }
]


def check(response: httpx.Response) -> dict:
    if response.is_error:
        sys.exit(f"{response.request.method} {response.request.url} : HTTP {response.status_code} {response.text}")
    return response.json()


def run_once(client: httpx.Client) -> str:
    session = check(
        client.post(
            "/auth/register",
            json={
                "email": f"trace-{uuid.uuid4().hex[:8]}@atelier-nova.example",
                "password": "mot-de-passe-trace",
                "workspace_name": "Traces",
            },
        )
    )
    auth = {"Authorization": f"Bearer {session['access_token']}"}
    workspace = check(client.get("/auth/me", headers=auth))["workspaces"][0]["workspace_id"]
    headers = {**auth, "X-Workspace-Id": workspace}
    project = check(client.post("/projects", json={"name": "Traces", "type": "agent"}, headers=headers))
    dataset = check(client.post(f"/projects/{project['id']}/datasets", json={"name": "Demandes"}, headers=headers))
    version = check(client.post(f"/datasets/{dataset['id']}/versions", json={"cases": CASES}, headers=headers))
    prompt = check(
        client.post(
            f"/projects/{project['id']}/prompts", json={"name": "brut", "template": "{{ message }}"}, headers=headers
        )
    )
    agent = check(
        client.post(
            f"/projects/{project['id']}/providers",
            json={
                "name": "Agent",
                "provider_kind": "evalforge_agent",
                "base_url": AGENT_URL,
                "model_name": "v1",
            },
            headers=headers,
        )
    )
    experiment = check(
        client.post(
            f"/projects/{project['id']}/experiments",
            json={
                "name": "Trace",
                "prompt_version_id": prompt["id"],
                "model_configuration_ids": [agent["id"]],
                "metrics": ["tool_selection"],
            },
            headers=headers,
        )
    )
    run = check(
        client.post(
            f"/experiments/{experiment['id']}/runs", json={"dataset_version_id": version["id"]}, headers=headers
        )
    )
    for _ in range(60):
        status = check(client.get(f"/runs/{run['run_id']}", headers=headers))["status"]
        if status not in ("queued", "running"):
            if status != "completed":
                sys.exit(f"run {run['run_id']} : {status}")
            return run["run_id"]
        time.sleep(0.5)
    sys.exit("le run ne s'est pas terminé en 30 s")


def find_trace(run_id: str) -> tuple[str, list[tuple[str, str]]] | None:
    """La trace dont un span de traitement porte l'identifiant du run (API v3 de Jaeger, format OTLP)."""
    now = datetime.now(UTC)
    response = httpx.get(
        f"{JAEGER}/api/v3/traces",
        params={
            "query.service_name": "evalforge-worker",
            "query.start_time_min": (now - timedelta(minutes=10)).isoformat(),
            "query.start_time_max": now.isoformat(),
            "query.search_depth": 20,
        },
    )
    if response.status_code == 404:  # aucune trace encore
        return None
    spans: dict[str, list[tuple[str, str, dict]]] = {}
    for resource in response.json()["result"]["resourceSpans"]:
        service = next(
            a["value"]["stringValue"] for a in resource["resource"]["attributes"] if a["key"] == "service.name"
        )
        for scope in resource["scopeSpans"]:
            for span in scope["spans"]:
                attributes = {a["key"]: next(iter(a["value"].values())) for a in span.get("attributes", [])}
                spans.setdefault(span["traceId"], []).append((service, span["name"], attributes))
    for trace_id, items in spans.items():
        ours = any(attributes.get("evalforge.run.id") == run_id for _, _, attributes in items)
        if ours and {service for service, _, _ in items} >= EXPECTED_SERVICES:
            return trace_id, [(service, name) for service, name, _ in items]
    return None


def main() -> None:
    with httpx.Client(base_url=API, headers={"X-Requested-With": "EvalForge"}, timeout=10) as client:
        run_id = run_once(client)
    print(f"Run {run_id} terminé")
    if "--sans-trace" in sys.argv:
        return
    deadline = time.monotonic() + 30  # l'export est groupé : quelques secondes de délai
    while time.monotonic() < deadline:
        found = find_trace(run_id)
        if found:
            trace_id, spans = found
            print(f"Trace {trace_id} : {len(spans)} spans à travers {', '.join(sorted(EXPECTED_SERVICES))}")
            for service, name in sorted(set(spans)):
                print(f"  {service:<26} {name}")
            return
        time.sleep(1)
    sys.exit(f"aucune trace du run {run_id} ne traverse {sorted(EXPECTED_SERVICES)}")


if __name__ == "__main__":
    main()
