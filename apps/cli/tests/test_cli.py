"""La CLI de bout en bout, contre le faux fournisseur de scripts/fake_llm.py branché en mémoire."""

import importlib.util
import json
import shutil
from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

from evalforge_cli import runner
from evalforge_cli.main import app

ROOT = Path(__file__).resolve().parents[3]
EXAMPLE = ROOT / "examples" / "support-faq"


def fake_llm_app():
    spec = importlib.util.spec_from_file_location("fake_llm", ROOT / "scripts" / "fake_llm.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.app


@pytest.fixture
def example(tmp_path: Path, monkeypatch) -> Path:
    shutil.copytree(EXAMPLE, tmp_path / "example")
    llm = fake_llm_app()
    # Le faux fournisseur répond en mémoire ; le contrôle des adresses (check_url) reste actif.
    monkeypatch.setattr(runner, "default_transport", lambda allow_private: httpx.ASGITransport(app=llm))
    return tmp_path / "example"


def invoke(*args: str):
    return CliRunner().invoke(app, list(args))


def test_l_exemple_compare_les_modeles_et_la_porte_bloque_le_modele_vague(example: Path):
    report, data = example / "rapport.html", example / "rapport.json"

    result = invoke("run", str(example / "evalforge.yaml"), "--report", str(report), "--json", str(data))

    assert result.exit_code == 1, result.output
    assert "Modèle vague : judge_correctness à 0 %, minimum 80 %" in result.output
    assert "Modèle sourcé : judge_correctness" not in result.output
    payload = json.loads(data.read_text(encoding="utf-8"))
    rates = {m["model"]: {x["name"]: x["pass_rate"] for x in m["metrics"]} for m in payload["models"]}
    assert rates["Modèle sourcé"] == {"exact_match": 1.0, "contains_expected": 1.0, "judge_correctness": 1.0}
    assert rates["Modèle vague"]["judge_correctness"] == 0.0
    assert [r["case_id"] for r in payload["results"][:2]] == ["support-01", "support-01"]
    html = report.read_text(encoding="utf-8")
    assert "La réponse reprend l&#39;information attendue." in html
    assert "inconnu" in html  # le modèle vague n'a pas de prix


def test_la_porte_passe_quand_chaque_modele_atteint_les_seuils(example: Path):
    config = example / "evalforge.yaml"
    text = config.read_text(encoding="utf-8")
    # On ne garde que le modèle sourcé : la section du modèle vague va jusqu'à « metrics: ».
    config.write_text(text[: text.index("  - name: Modèle vague")] + text[text.index("metrics:") :], encoding="utf-8")

    result = invoke("run", str(config))

    assert result.exit_code == 0, result.output
    assert "Porte de qualité franchie" in result.output


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (("judge_correctness]", "faithfulness]"), "métriques inconnues : faithfulness"),
        (("allow_private_urls: true", "allow_private_urls: false"), "adresse non publique refusée : 127.0.0.1"),
        (("dataset: support-faq.csv", "dataset: absent.csv"), "absent.csv"),
        (("  judge_correctness: 0.8", "  judge_correctness: 80"), "un seuil est un taux entre 0 et 1"),
    ],
)
def test_une_configuration_invalide_s_arrete_avec_le_code_2(example: Path, change, message):
    config = example / "evalforge.yaml"
    config.write_text(config.read_text(encoding="utf-8").replace(*change), encoding="utf-8")

    result = invoke("run", str(config))

    assert result.exit_code == 2, result.output
    assert message in " ".join(result.output.split())


def test_la_cle_vient_de_l_environnement(example: Path, monkeypatch):
    config = example / "evalforge.yaml"
    config.write_text(
        config.read_text(encoding="utf-8").replace("model: grounded", "model: grounded\n    api_key_env: CLE_ABSENTE"),
        encoding="utf-8",
    )
    monkeypatch.delenv("CLE_ABSENTE", raising=False)

    result = invoke("run", str(config))

    assert result.exit_code == 2
    assert "CLE_ABSENTE absente" in " ".join(result.output.split())


def test_une_regression_face_a_la_baseline_fait_echouer_la_commande(example: Path):
    config = example / "evalforge.yaml"
    text = config.read_text(encoding="utf-8")
    # On ne garde que le modèle sourcé, sans seuil : seule la comparaison à la baseline peut faire échouer.
    trimmed = text[: text.index("  - name: Modèle vague")] + text[text.index("metrics:") :]
    config.write_text(trimmed.replace("thresholds:\n  judge_correctness: 0.8\n", ""), encoding="utf-8")
    baseline = example / "baseline.json"
    assert invoke("run", str(config), "--json", str(baseline)).exit_code == 0

    same = invoke("run", str(config), "--baseline", str(baseline))
    # Nouveau prompt sans contexte : le modèle sourcé ne trouve plus l'information.
    config.write_text(
        config.read_text(encoding="utf-8").replace("    Contexte : {{ context }}\n", ""), encoding="utf-8"
    )
    worse = invoke("run", str(config), "--baseline", str(baseline))

    assert same.exit_code == 0, same.output
    assert "Aucune régression" in same.output
    assert worse.exit_code == 1, worse.output
    flat = " ".join(worse.output.split())
    assert (
        "Régression Modèle sourcé : exact_match 100 % → 0 % (5 cas cassés, 0 réparés, p = 0.062, non significatif)"
        in flat
    )
    assert "cassé : support-03" in flat


def test_la_liste_des_metriques():
    result = invoke("metrics")

    assert result.exit_code == 0
    assert "judge_faithfulness" in result.output


AGENT_EXAMPLE = ROOT / "examples" / "support-agent"


@pytest.fixture
def agent_example(tmp_path: Path, monkeypatch):
    """L'agent de support réel joignable sur 127.0.0.1:8200, piloté par le faux fournisseur en mémoire.

    Renvoie une fonction qui choisit le modèle pilote : support-agent (soigneux) ou careless-agent.
    """
    from evalforge_llm import OpenAICompatibleProvider
    from support_agent.agent import SupportAgent

    shutil.copytree(AGENT_EXAMPLE, tmp_path / "example")
    llm = httpx.AsyncClient(transport=httpx.ASGITransport(app=fake_llm_app()))
    pilot = {"model": "support-agent"}

    async def handle(request: httpx.Request) -> httpx.Response:
        assert (request.url.host, request.url.port, request.url.path) == ("127.0.0.1", 8200, "/v1/agent/run")
        agent = SupportAgent(OpenAICompatibleProvider(base_url="http://fake-llm/v1", client=llm), pilot["model"])
        result = await agent.run(json.loads(request.content)["message"])
        return httpx.Response(200, json=result.model_dump(mode="json"))

    monkeypatch.setattr(runner, "default_transport", lambda allow_private: httpx.MockTransport(handle))

    def use(model: str) -> Path:
        pilot["model"] = model
        return tmp_path / "example"

    return use


def test_l_agent_soigneux_passe_la_porte_et_reste_au_niveau_de_la_baseline(agent_example):
    example = agent_example("support-agent")
    data = example / "rapport.json"

    result = invoke(
        "run", str(example / "evalforge.yaml"), "--baseline", str(example / "baseline.json"), "--json", str(data)
    )

    assert result.exit_code == 0, result.output
    assert "Porte de qualité franchie" in result.output
    assert "Aucune régression" in result.output
    payload = json.loads(data.read_text(encoding="utf-8"))
    first = payload["results"][0]
    assert [s["tool"] for s in first["trajectory"]] == ["find_order", "get_return_policy"]
    assert first["stop_reason"] == "completed"


def test_l_agent_negligent_est_bloque_par_la_porte(agent_example):
    example = agent_example("careless-agent")
    report = example / "rapport.html"

    result = invoke(
        "run", str(example / "evalforge.yaml"), "--baseline", str(example / "baseline.json"), "--report", str(report)
    )

    assert result.exit_code == 1, result.output
    flat = " ".join(result.output.split())
    assert "Agent de support : tool_selection à 20 %, minimum 100 %" in flat
    assert "Régression Agent de support : tool_selection" in flat
    assert "escalate_to_human" in report.read_text(encoding="utf-8")


def test_un_agent_ne_peut_pas_servir_de_juge(agent_example):
    example = agent_example("support-agent")
    config = example / "evalforge.yaml"
    config.write_text(
        config.read_text(encoding="utf-8")
        + "judge:\n  name: Juge agent\n  kind: agent\n  base_url: http://127.0.0.1:8200\n",
        encoding="utf-8",
    )

    result = invoke("run", str(config))

    assert result.exit_code == 2
    assert "un agent ne peut pas servir de juge" in " ".join(result.output.split())


def test_un_modele_llm_sans_nom_de_modele_est_refuse(agent_example):
    example = agent_example("support-agent")
    config = example / "evalforge.yaml"
    config.write_text(config.read_text(encoding="utf-8").replace("    kind: agent\n", ""), encoding="utf-8")

    result = invoke("run", str(config))

    assert result.exit_code == 2
    assert "champ model requis" in " ".join(result.output.split())
