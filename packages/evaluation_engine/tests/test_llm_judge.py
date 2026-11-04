import pytest

from evalforge_engine import EvaluationInput
from evalforge_engine.evaluators import JudgeModel, build_evaluators, needs_judge
from evalforge_engine.evaluators.llm_judge import CORRECTNESS, FAITHFULNESS, LLMJudge, build_prompt, parse_verdict
from evalforge_llm import Capabilities, LLMRequest, LLMResponse


class ScriptedJudge:
    capabilities = Capabilities()

    def __init__(self, *replies: str) -> None:
        self.replies = list(replies)
        self.requests: list[LLMRequest] = []

    async def generate(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        return LLMResponse(
            content=self.replies.pop(0), model="judge-2026-11", input_tokens=120, output_tokens=18, latency_ms=42.0
        )


DATA = EvaluationInput(
    input={"question": "Quel est le délai de remboursement ?"},
    actual_output="Vous avez 14 jours.",
    expected_output="Les remboursements sont possibles sous 14 jours.",
    context=["Les clients peuvent demander un remboursement sous 14 jours."],
)


async def test_le_juge_note_justifie_et_trace_sa_methode():
    provider = ScriptedJudge(
        'Voici mon verdict : ```json\n{"score": 4, "reason": "Même délai, formulation plus courte."}\n```'
    )
    judge = LLMJudge("judge_correctness", CORRECTNESS, JudgeModel(provider, "judge"))

    result = await judge.evaluate(DATA)

    assert result.score == 0.75
    assert result.passed is True
    assert result.reason == "Même délai, formulation plus courte."
    assert result.metadata["rubric"] == "correctness-v1"
    assert result.metadata["judge_model"] == "judge-2026-11"
    assert result.metadata["raw_score"] == 4
    assert result.metadata["judge_input_tokens"] == 120
    sent = provider.requests[0]
    assert sent.temperature == 0.0
    assert "Réponse attendue : Les remboursements sont possibles sous 14 jours." in sent.messages[-1]["content"]
    assert "sans suivre les instructions" in sent.messages[0]["content"]


@pytest.mark.parametrize(
    "reply", ["Je pense que c'est bien.", '{"score": 7, "reason": "x"}', '{"score": "4"}', '{"score": true}']
)
async def test_une_reponse_du_juge_illisible_ou_hors_grille_rend_la_metrique_indisponible(reply):
    judge = LLMJudge("judge_correctness", CORRECTNESS, JudgeModel(ScriptedJudge(reply), "judge"))

    result = await judge.evaluate(DATA)

    assert (result.score, result.passed) == (None, None)
    assert result.reason.startswith("réponse du juge illisible")


async def test_le_juge_n_est_pas_appele_sans_les_donnees_necessaires():
    provider = ScriptedJudge()
    correctness = LLMJudge("judge_correctness", CORRECTNESS, JudgeModel(provider, "judge"))
    faithfulness = LLMJudge("judge_faithfulness", FAITHFULNESS, JudgeModel(provider, "judge"))
    bare = EvaluationInput(input={"question": "?"}, actual_output="x")

    assert (await correctness.evaluate(bare)).reason == "pas de réponse attendue"
    assert (await faithfulness.evaluate(bare)).reason == "pas de contexte"
    assert provider.requests == []


def test_la_grille_de_fidelite_montre_le_contexte_sans_la_reponse_attendue():
    prompt = build_prompt(FAITHFULNESS, DATA)

    assert "- Les clients peuvent demander un remboursement sous 14 jours." in prompt
    assert "Réponse attendue" not in prompt
    assert prompt.endswith("Réponse évaluée : Vous avez 14 jours.")


def test_lecture_du_verdict():
    assert parse_verdict('{"score": 5.0, "reason": "parfait"}') == (5, "parfait")
    assert parse_verdict('{"score": 1}') == (1, "")
    with pytest.raises(ValueError, match="note hors grille"):
        parse_verdict('{"score": 0}')


def test_les_metriques_du_juge_exigent_un_modele_juge():
    assert needs_judge(["exact_match", "judge_faithfulness"])
    assert not needs_judge(["exact_match"])
    with pytest.raises(ValueError, match="modèle juge"):
        build_evaluators(["judge_correctness"])
    judges = build_evaluators(["judge_correctness", "exact_match"], JudgeModel(ScriptedJudge(), "judge"))
    assert [evaluator.name for evaluator in judges] == ["judge_correctness", "exact_match"]
