"""Adaptateur Ragas, contre la vraie bibliothèque (groupe `ragas`) ; ignoré si elle n'est pas installée."""

import os

import pytest

pytest.importorskip("ragas")

from adapter_support import SchemaJudge
from evalforge_engine import EvaluationInput
from evalforge_engine.adapters.ragas_adapter import RagasFaithfulness
from evalforge_engine.evaluators import JudgeModel, available_metrics, build_evaluators

CONTEXT = ["Les remboursements sont possibles sous 14 jours.", "Le retour est gratuit en point relais."]


def data(answer: str) -> EvaluationInput:
    return EvaluationInput(
        input={"question": "Quelles sont les conditions de retour ?"}, actual_output=answer, context=CONTEXT
    )


def judge_supporting(*verdicts: int) -> SchemaJudge:
    """Ragas découpe la réponse en affirmations, puis demande un verdict (0/1) pour chacune."""
    statements = ["Remboursement sous 14 jours.", "Retour gratuit.", "Livraison offerte."][: len(verdicts)]
    return SchemaJudge(
        {
            "StatementGeneratorOutput": lambda *_: {"statements": statements},
            "NLIStatementOutput": lambda *_: {
                "statements": [
                    {"statement": s, "reason": "dans le contexte" if v else "absent du contexte", "verdict": v}
                    for s, v in zip(statements, verdicts, strict=True)
                ]
            },
        }
    )


def test_la_telemetrie_de_ragas_est_coupee():
    assert os.environ["RAGAS_DO_NOT_TRACK"] == "true"
    assert "ragas_faithfulness" in available_metrics()


async def test_la_part_d_affirmations_appuyees_devient_le_score():
    provider = judge_supporting(1, 1, 0)
    metric = RagasFaithfulness(JudgeModel(provider, "judge"))

    result = await metric.evaluate(data("Remboursés sous 14 jours, retour gratuit, livraison offerte."))

    assert result.score == pytest.approx(2 / 3)
    assert result.passed is False  # sous le seuil de 0,75
    assert result.metadata["method"] == "ragas"
    assert result.metadata["judge_calls"] == 2
    assert result.metadata["judge_model"] == "judge-2026-11"
    # Les prompts de Ragas passent par la passerelle avec ses propres schémas.
    assert provider.titles == ["StatementGeneratorOutput", "NLIStatementOutput"]


async def test_une_reponse_sans_affirmation_est_indisponible_pas_nulle():
    metric = RagasFaithfulness(JudgeModel(judge_supporting(), "judge"))

    result = await metric.evaluate(data("Je ne sais pas."))

    assert (result.score, result.passed) == (None, None)
    assert result.reason == "aucune affirmation à vérifier"


async def test_une_sortie_invalide_est_redemandee_une_fois():
    provider = judge_supporting(1, 1)
    provider.garbage_first = True
    metric = RagasFaithfulness(JudgeModel(provider, "judge"))

    result = await metric.evaluate(data("Remboursés sous 14 jours, retour gratuit."))

    assert result.score == 1.0
    assert result.metadata["judge_calls"] == 3


async def test_sans_contexte_ragas_n_est_pas_appele():
    provider = judge_supporting(1)
    (metric,) = build_evaluators(["ragas_faithfulness"], JudgeModel(provider, "judge"))

    result = await metric.evaluate(EvaluationInput(input={"question": "?"}, actual_output="x"))

    assert result.reason == "pas de contexte"
    assert provider.titles == []
