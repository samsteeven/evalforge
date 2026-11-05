"""Test de charge de l'API : lectures courantes de l'interface, et lancement de runs exécutés par les workers.

Chaque utilisateur virtuel crée son compte, son projet, un jeu de 5 cas, un prompt, un modèle (le faux fournisseur)
et une expérience, puis enchaîne les requêtes d'une personne qui suit ses évaluations. Les runs passent par l'outbox,
RabbitMQ et les workers : on mesure aussi le délai entre la demande et la fin du run.

    uv run --group load locust -f loadtests/locustfile.py --headless -u 20 -r 5 -t 60s --host http://127.0.0.1:8000

Seuils (code de sortie 1 s'ils ne sont pas tenus, pour la CI) : aucune erreur, P95 des lectures sous
EVALFORGE_LOAD_P95_MS (500 ms par défaut), chaque run lancé terminé.
"""

import os
import time
import uuid

from locust import HttpUser, between, events, task

LLM_URL = os.environ.get("EVALFORGE_LOAD_LLM_URL", "http://127.0.0.1:8100/v1")
P95_LIMIT_MS = float(os.environ.get("EVALFORGE_LOAD_P95_MS", "500"))
READS = ("GET /projects", "GET /experiments/{id}/runs", "GET /runs/{id}/metrics", "GET /runs/{id}")
CASES = [
    {
        "id": f"cas-{index}",
        "type": "rag",
        "input": {"question": f"Question {index} ?"},
        "expected": {"answer": f"Réponse {index}."},
        "context": [f"Réponse {index}."],
    }
    for index in range(1, 6)
]

run_durations: list[float] = []
unfinished_runs: list[str] = []


class EvaluationUser(HttpUser):
    wait_time = between(0.5, 1.5)

    def on_start(self) -> None:
        self.client.headers["X-Requested-With"] = "EvalForge"
        session = self._post(
            "/auth/register",
            {
                "email": f"charge-{uuid.uuid4().hex[:10]}@atelier-nova.example",
                "password": "mot-de-passe-charge",
                "workspace_name": "Charge",
            },
            "POST /auth/register",
        )
        self.client.headers["Authorization"] = f"Bearer {session['access_token']}"
        me = self.client.get("/api/v1/auth/me", name="GET /auth/me").json()
        self.client.headers["X-Workspace-Id"] = me["workspaces"][0]["workspace_id"]
        project = self._post("/projects", {"name": "Charge", "type": "rag"}, "POST /projects")
        dataset = self._post(f"/projects/{project['id']}/datasets", {"name": "FAQ"}, "POST /projects/{id}/datasets")
        self.version = self._post(
            f"/datasets/{dataset['id']}/versions", {"cases": CASES}, "POST /datasets/{id}/versions"
        )
        prompt = self._post(
            f"/projects/{project['id']}/prompts",
            {"name": "faq", "template": "Contexte : {{ context }}\nQuestion : {{ question }}"},
            "POST /projects/{id}/prompts",
        )
        model = self._post(
            f"/projects/{project['id']}/providers",
            {"name": "Sourcé", "provider_kind": "openai_compatible", "base_url": LLM_URL, "model_name": "grounded"},
            "POST /projects/{id}/providers",
        )
        self.experiment = self._post(
            f"/projects/{project['id']}/experiments",
            {
                "name": "Charge",
                "prompt_version_id": prompt["id"],
                "model_configuration_ids": [model["id"]],
                "metrics": ["exact_match", "contains_expected"],
            },
            "POST /projects/{id}/experiments",
        )
        self.last_run: str | None = None

    def _post(self, path: str, body: dict, name: str) -> dict:
        with self.client.post(f"/api/v1{path}", json=body, name=name, catch_response=True) as response:
            if response.status_code not in (200, 201, 202):
                response.failure(f"HTTP {response.status_code} : {response.text[:200]}")
                raise RuntimeError(f"{name} : HTTP {response.status_code}")
            return response.json()

    @task(5)
    def list_projects(self) -> None:
        self.client.get("/api/v1/projects", name="GET /projects")

    @task(3)
    def list_runs(self) -> None:
        self.client.get(f"/api/v1/experiments/{self.experiment['id']}/runs", name="GET /experiments/{id}/runs")

    @task(2)
    def read_metrics(self) -> None:
        if self.last_run:
            self.client.get(f"/api/v1/runs/{self.last_run}/metrics", name="GET /runs/{id}/metrics")

    @task(1)
    def launch_run(self) -> None:
        """Lance un run et suit sa progression comme l'interface (une lecture par seconde au plus)."""
        run = self._post(
            f"/experiments/{self.experiment['id']}/runs",
            {"dataset_version_id": self.version["id"]},
            "POST /experiments/{id}/runs",
        )
        started = time.perf_counter()
        deadline = started + 60
        while time.perf_counter() < deadline:
            status = self.client.get(f"/api/v1/runs/{run['run_id']}", name="GET /runs/{id}").json()["status"]
            if status not in ("queued", "running"):
                run_durations.append(time.perf_counter() - started)
                if status != "completed":
                    unfinished_runs.append(f"{run['run_id']} : {status}")
                self.last_run = run["run_id"]
                return
            time.sleep(0.5)
        unfinished_runs.append(f"{run['run_id']} : pas terminé en 60 s")


@events.quitting.add_listener
def check_thresholds(environment, **_) -> None:
    stats = environment.stats
    problems = []
    if stats.total.num_failures:
        problems.append(f"{stats.total.num_failures} requêtes en erreur")
    for name in READS:
        entry = stats.get(name, "GET")
        if entry.num_requests and entry.get_response_time_percentile(0.95) > P95_LIMIT_MS:
            problems.append(f"{name} : P95 {entry.get_response_time_percentile(0.95):.0f} ms > {P95_LIMIT_MS:.0f} ms")
    if unfinished_runs:
        problems.append(f"runs non terminés : {', '.join(unfinished_runs[:5])}")
    if run_durations:
        ordered = sorted(run_durations)
        p95 = ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))]
        print(f"Runs : {len(ordered)} terminés, médiane {ordered[len(ordered) // 2]:.2f} s, P95 {p95:.2f} s")
    for problem in problems:
        print(f"SEUIL NON TENU : {problem}")
    environment.process_exit_code = 1 if problems else 0
