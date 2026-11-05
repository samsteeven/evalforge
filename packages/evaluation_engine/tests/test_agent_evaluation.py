"""Évaluation de trajectoires d'agents, et cible « agent » contre l'agent de support réel (en mémoire)."""

import importlib.util
from pathlib import Path

import httpx
import pytest

from evalforge_engine import EvaluationCase, EvaluationInput, WorkItem, evaluate_cases
from evalforge_engine.evaluators import build_evaluators
from evalforge_engine.evaluators.agent import StepEfficiency, TaskCompletion, ToolArguments, ToolOrder, ToolSelection
from evalforge_engine.targets import AgentError, agent_target

EXPECTED = {
    "answer": "Retour possible sous 30 jours",
    "tools": ["find_order", "get_return_policy"],
    "arguments": {"find_order": {"order_id": "CMD-1042"}, "get_return_policy": {"category": "electronique"}},
}


def step(tool: str, arguments, status: str = "success") -> dict:
    return {"step": 0, "tool": tool, "arguments": arguments, "status": status, "latency_ms": 1}


def data(*steps: dict, stop_reason: str = "completed", output: str = "ok") -> EvaluationInput:
    return EvaluationInput(
        input={"message": "?"}, actual_output=output, expected=EXPECTED, trajectory=list(steps), stop_reason=stop_reason
    )


GOOD = data(step("find_order", {"order_id": "CMD-1042"}), step("get_return_policy", {"category": "electronique"}))
CARELESS = data(
    step("escalate_to_human", {"reason": "x"}),
    step("find_order", '{"order_id": "1042"}', status="invalid_arguments"),
)


async def score(evaluator, sample):
    result = await evaluator.evaluate(sample)
    return result.score, result.passed


async def test_une_bonne_trajectoire_passe_toutes_les_metriques():
    for evaluator in (ToolSelection(), ToolOrder(), ToolArguments(), StepEfficiency(), TaskCompletion()):
        assert await score(evaluator, GOOD) == (1.0, True), evaluator.name


async def test_une_trajectoire_negligente_est_detaillee_metrique_par_metrique():
    assert await score(ToolSelection(), CARELESS) == (0.0, False)  # l'appel invalide n'a rien fait
    assert await score(ToolOrder(), CARELESS) == (0.0, False)
    assert await score(ToolArguments(), CARELESS) == (0.0, False)
    efficiency = await StepEfficiency().evaluate(CARELESS)
    assert (efficiency.score, efficiency.passed) == (1.0, False)  # 2 étapes pour 2, mais une inutile et une ratée
    assert efficiency.metadata == {"steps": 2, "expected_steps": 2, "failed_steps": 1}


async def test_ordre_et_arguments():
    reversed_order = data(
        step("get_return_policy", {"category": "electronique"}), step("find_order", {"order_id": "cmd-1042"})
    )
    extra = data(
        step("find_order", {"order_id": "CMD-1042"}),
        step("find_order", {"order_id": "CMD-1042"}),
        step("get_return_policy", {"category": "maison"}),
    )

    assert await score(ToolOrder(), reversed_order) == (0.0, False)
    assert await score(ToolArguments(), reversed_order) == (1.0, True)  # casse ignorée
    assert await score(ToolArguments(), extra) == (0.5, False)
    efficiency = await StepEfficiency().evaluate(extra)
    assert efficiency.score == pytest.approx(2 / 3)
    assert efficiency.passed is False


async def test_sans_attendu_ou_sans_trajectoire_c_est_indisponible():
    bare = EvaluationInput(input={}, actual_output="x")

    for evaluator in (ToolSelection(), ToolOrder(), ToolArguments(), StepEfficiency(), TaskCompletion()):
        result = await evaluator.evaluate(bare)
        assert (result.score, result.passed) == (None, None), evaluator.name
    assert await score(TaskCompletion(), data(stop_reason="max_steps")) == (0.0, False)


# --- Cible « agent », contre l'agent de support piloté par le faux fournisseur ---------------------------------------

ROOT = Path(__file__).resolve().parents[3]


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
async def agent_client():
    pytest.importorskip("support_agent")
    from evalforge_llm import OpenAICompatibleProvider
    from support_agent.agent import SupportAgent
    from support_agent.app import AgentSettings, create_app

    fake_llm = load("fake_llm", ROOT / "scripts" / "fake_llm.py").app
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=fake_llm)) as llm:
        app = create_app(AgentSettings(llm_base_url="http://fake-llm/v1"), client=llm)

        async def use_model(model: str) -> None:
            """Même agent, autre modèle pilote : le bon (« support-agent ») ou le négligent."""
            app.state.agent = SupportAgent(OpenAICompatibleProvider(base_url="http://fake-llm/v1", client=llm), model)

        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(transport=httpx.ASGITransport(app=app)) as client,
        ):
            client.use_model = use_model
            yield client


async def test_evaluer_l_agent_de_support_de_bout_en_bout(agent_client):
    case = EvaluationCase(
        id="retour-casque",
        type="agent",
        input={"message": "Commande CMD-1042 : comment faire un retour ?"},
        expected=EXPECTED,
    )
    metrics = [
        "tool_selection",
        "tool_order",
        "tool_arguments",
        "step_efficiency",
        "task_completion",
        "contains_expected",
    ]
    results = {}
    for model in ("support-agent", "careless-agent"):
        await agent_client.use_model(model)
        target = agent_target(agent_client, base_url="http://agent", name=model, template="{{ message }}")
        (outcome,) = [o async for o in evaluate_cases([WorkItem(model, case, target)], build_evaluators(metrics))]
        results[model] = {m.name: m.passed for m in outcome.metrics}
        assert outcome.output.trajectory

    assert all(results["support-agent"].values()), results["support-agent"]
    assert results["careless-agent"] == {
        "tool_selection": False,
        "tool_order": False,
        "tool_arguments": False,
        "step_efficiency": False,
        "task_completion": True,  # il a bien répondu… en inventant la politique
        "contains_expected": True,  # et la phrase inventée tombe juste : seule la trajectoire révèle le problème
    }


async def test_un_agent_qui_ne_respecte_pas_le_contrat_est_une_erreur_explicite():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"answer": "pas le bon format"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        target = agent_target(client, base_url="http://agent", name="x", template="{{ message }}")
        with pytest.raises(AgentError, match="illisible"):
            await target(EvaluationCase(id="c", input={"message": "?"}))
