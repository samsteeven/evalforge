"""Adaptateur DeepEval, contre la vraie bibliothèque (groupe `deepeval`) ; ignoré si elle n'est pas installée."""

import os

import pytest

pytest.importorskip("deepeval")

from adapter_support import SchemaJudge
from evalforge_engine import EvaluationInput
from evalforge_engine.adapters.deepeval_adapter import DeepEvalAnswerRelevancy, DeepEvalFaithfulness
from evalforge_engine.evaluators import JudgeModel, available_metrics

CONTEXT = ["Les remboursements sont possibles sous 14 jours.", "Le retour est gratuit en point relais."]
DATA = EvaluationInput(
    input={"question": "Quelles sont les conditions de retour ?"},
    actual_output="Remboursés sous 14 jours. Retour gratuit. Livraison offerte.",
    context=CONTEXT,
)


def verdicts(*values: str):
    return lambda *_: {"verdicts": [{"verdict": v, "reason": None if v == "yes" else "non appuyé"} for v in values]}


def test_la_telemetrie_de_deepeval_est_coupee():
    assert os.environ["DEEPEVAL_TELEMETRY_OPT_OUT"] == "YES"
    assert {"deepeval_faithfulness", "deepeval_answer_relevancy"} <= set(available_metrics())


async def test_fidelite_au_contexte():
    provider = SchemaJudge(
        {
            "Truths": lambda *_: {"truths": CONTEXT},
            "Claims": lambda *_: {"claims": ["Remboursés sous 14 jours.", "Retour gratuit.", "Livraison offerte."]},
            "Verdicts": verdicts("yes", "yes", "no"),
            "FaithfulnessScoreReason": lambda *_: {"reason": "La livraison offerte n'est pas dans le contexte."},
        }
    )

    result = await DeepEvalFaithfulness(JudgeModel(provider, "judge")).evaluate(DATA)

    assert result.score == pytest.approx(2 / 3)
    assert result.passed is False
    assert result.reason == "La livraison offerte n'est pas dans le contexte."
    assert result.metadata["metric"] == "FaithfulnessMetric"
    assert result.metadata["judge_calls"] == len(provider.titles)
    assert {"Truths", "Claims", "Verdicts", "FaithfulnessScoreReason"} <= set(provider.titles)


async def test_pertinence_de_la_reponse():
    provider = SchemaJudge(
        {
            "Statements": lambda *_: {
                "statements": ["Remboursés sous 14 jours.", "Retour gratuit.", "Livraison offerte."]
            },
            "Verdicts": verdicts("yes", "yes", "yes"),
            "AnswerRelevancyScoreReason": lambda *_: {"reason": "Tout répond à la question."},
        }
    )

    result = await DeepEvalAnswerRelevancy(JudgeModel(provider, "judge")).evaluate(DATA)

    assert result.score == 1.0
    assert result.passed is True
    assert result.reason == "Tout répond à la question."


async def test_sans_question_deepeval_n_est_pas_appele():
    provider = SchemaJudge({})

    result = await DeepEvalAnswerRelevancy(JudgeModel(provider, "judge")).evaluate(
        EvaluationInput(input={"texte": "x"}, actual_output="y")
    )

    assert result.reason == "pas de question"
    assert provider.titles == []
