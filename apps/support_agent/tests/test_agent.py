"""L'agent, piloté par le faux fournisseur (scripts/fake_llm.py) branché en mémoire, à travers la vraie passerelle."""

import importlib.util
from pathlib import Path

import httpx
import pytest

from evalforge_llm import Capabilities, LLMRequest, LLMResponse, OpenAICompatibleProvider, ToolCall
from support_agent.agent import SupportAgent
from support_agent.app import AgentSettings, create_app

ROOT = Path(__file__).resolve().parents[3]


def fake_llm_app():
    spec = importlib.util.spec_from_file_location("fake_llm", ROOT / "scripts" / "fake_llm.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.app


@pytest.fixture
async def llm_client():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=fake_llm_app())) as client:
        yield client


def agent(llm_client, model: str, max_steps: int = 6) -> SupportAgent:
    provider = OpenAICompatibleProvider(base_url="http://fake-llm/v1", client=llm_client)
    return SupportAgent(provider, model, max_steps=max_steps)


async def test_l_agent_consulte_la_commande_puis_la_politique_et_repond_sans_inventer(llm_client):
    result = await agent(llm_client, "support-agent").run(
        "Bonjour, ma commande CMD-1042 : comment faire un retour et être remboursé ?"
    )

    assert result.stop_reason == "completed"
    assert [(s.tool, s.status) for s in result.trajectory] == [
        ("find_order", "success"),
        ("get_return_policy", "success"),
    ]
    assert result.trajectory[0].arguments == {"order_id": "CMD-1042"}
    # La catégorie vient du résultat de find_order, pas d'une supposition.
    assert result.trajectory[1].arguments == {"category": "electronique"}
    assert "Retour possible sous 30 jours" in result.output
    assert "Casque audio" in result.output
    assert result.input_tokens > 0


async def test_un_produit_endommage_ouvre_un_ticket(llm_client):
    result = await agent(llm_client, "support-agent").run("Mon pull de la commande CMD-1043 est arrivé abîmé.")

    assert [s.tool for s in result.trajectory] == ["find_order", "create_ticket"]
    assert result.trajectory[1].arguments["priority"] == "haute"
    assert "TCK-0001" in result.output


async def test_les_erreurs_de_l_agent_negligent_sont_tracees(llm_client):
    result = await agent(llm_client, "careless-agent").run("Commande CMD-1042 : je voudrais un retour.")

    assert [(s.tool, s.status) for s in result.trajectory] == [
        ("escalate_to_human", "success"),
        ("find_order", "invalid_arguments"),
    ]
    assert "order_id" in result.trajectory[1].error
    assert result.trajectory[1].arguments == '{"order_id": "1042"}'
    assert "Retour possible sous 30 jours." in result.output  # inventé : la politique n'a pas été lue


class LoopingModel:
    """Un modèle qui appelle un outil inconnu à chaque tour : l'agent doit s'arrêter seul."""

    capabilities = Capabilities(tool_calling=True)

    def __init__(self) -> None:
        self.calls = 0

    async def generate(self, request: LLMRequest) -> LLMResponse:
        self.calls += 1
        return LLMResponse(
            content="",
            tool_calls=[ToolCall(id=f"c{self.calls}", name="delete_database", arguments="{}")],
            model="loop",
            latency_ms=1,
        )


async def test_budget_d_etapes_et_outil_inconnu():
    model = LoopingModel()

    result = await SupportAgent(model, "loop", max_steps=3).run("Bonjour")

    assert result.stop_reason == "max_steps"
    assert model.calls == 3
    assert {s.status for s in result.trajectory} == {"unknown_tool"}
    assert "conseiller" in result.output


async def test_le_point_d_entree_http(llm_client):
    app = create_app(AgentSettings(llm_base_url="http://fake-llm/v1", llm_model="support-agent"), client=llm_client)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://agent") as http,
    ):
        response = await http.post("/v1/agent/run", json={"message": "Où en est ma commande CMD-1043 ?"})

    assert response.status_code == 200
    body = response.json()
    assert body["trajectory"][0]["tool"] == "find_order"
    assert "en transit" in body["output"]
