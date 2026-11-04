import asyncio

import pytest

from evalforge_engine import CaseStatus, EvaluationCase, EvaluationInput, MetricOutput, TargetOutput, WorkItem
from evalforge_engine.evaluators import ContainsExpected, ExactMatch, available_metrics, get_evaluators
from evalforge_engine.orchestrator import evaluate_cases
from evalforge_engine.scoring import cost_usd, percentile, summarize_metric, summarize_performance
from evalforge_engine.templating import TemplateError, render, variables


def case(case_id: str = "c1", answer: str | None = "Sous 14 jours.", **input_values) -> EvaluationCase:
    return EvaluationCase(
        id=case_id,
        input=input_values or {"question": "Délai de remboursement ?"},
        expected={"answer": answer} if answer is not None else None,
        context=["Les clients peuvent demander un remboursement sous 14 jours."],
    )


# --- Gabarits ---------------------------------------------------------------------------------------------------


def test_le_gabarit_substitue_les_champs_du_cas_et_le_contexte():
    template = "Contexte :\n{{ context }}\nQuestion : {{question}} ({{ options }})"

    rendered = render(template, case(question="Délai ?", options=["a", "b"]))

    assert rendered == (
        'Contexte :\nLes clients peuvent demander un remboursement sous 14 jours.\nQuestion : Délai ? (["a", "b"])'
    )
    assert variables(template) == ["context", "question", "options"]


def test_une_variable_absente_est_une_erreur_explicite():
    with pytest.raises(TemplateError, match="variable « langue » absente du cas c1"):
        render("{{ langue }}", case())


def test_le_gabarit_ne_donne_acces_a_rien_d_autre_que_les_champs():
    assert render("{question.__class__} {{ question }}", case(question="Q")) == "{question.__class__} Q"


# --- Évaluateurs déterministes -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("actual", "expected", "exact", "contains"),
    [
        ("Sous 14 jours.", "Sous 14 jours.", True, True),
        ("  sous 14   JOURS ", "Sous 14 jours.", True, True),
        ("Les remboursements sont possibles sous 14 jours.", "Sous 14 jours.", False, True),
        ("Sous 30 jours.", "Sous 14 jours.", False, False),
    ],
)
async def test_correspondance_exacte_et_inclusion(actual, expected, exact, contains):
    data = EvaluationInput(input={}, actual_output=actual, expected_output=expected)

    assert (await ExactMatch().evaluate(data)).passed is exact
    assert (await ContainsExpected().evaluate(data)).passed is contains


async def test_sans_reponse_attendue_la_metrique_est_indisponible_pas_nulle():
    result = await ExactMatch().evaluate(EvaluationInput(input={}, actual_output="x"))

    assert (result.score, result.passed) == (None, None)
    assert result.reason == "pas de réponse attendue"


def test_registre_des_metriques():
    assert available_metrics() == ["contains_expected", "exact_match"]
    with pytest.raises(KeyError, match="faithfulness"):
        get_evaluators(["exact_match", "faithfulness"])


# --- Orchestration -----------------------------------------------------------------------------------------------


def answering(text: str, delay: float = 0.0):
    async def target(_: EvaluationCase) -> TargetOutput:
        await asyncio.sleep(delay)
        return TargetOutput(text=text, latency_ms=delay * 1000, input_tokens=10, output_tokens=2)

    return target


async def collect(items, evaluators=(), **options):
    return [outcome async for outcome in evaluate_cases(items, list(evaluators), **options)]


async def test_chaque_cas_est_evalue_et_mesure():
    outcomes = await collect([WorkItem("k1", case(), answering("Sous 14 jours."))], [ExactMatch()])

    (outcome,) = outcomes
    assert outcome.status is CaseStatus.SUCCESS
    assert outcome.key == "k1"
    assert outcome.output.text == "Sous 14 jours."
    assert outcome.metrics == [MetricOutput(name="exact_match", score=1.0, passed=True)]


async def test_la_concurrence_est_bornee():
    running = 0
    peak = 0

    async def target(_: EvaluationCase) -> TargetOutput:
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await asyncio.sleep(0.01)
        running -= 1
        return TargetOutput(text="ok", latency_ms=10)

    outcomes = await collect([WorkItem(i, case(str(i)), target) for i in range(12)], max_concurrency=3)

    assert len(outcomes) == 12
    assert peak == 3


async def test_un_cas_trop_lent_ou_en_erreur_n_arrete_pas_les_autres():
    async def failing(_: EvaluationCase) -> TargetOutput:
        raise RuntimeError("fournisseur en panne")

    outcomes = await collect(
        [
            WorkItem("lent", case("lent"), answering("ok", delay=1)),
            WorkItem("erreur", case("erreur"), failing),
            WorkItem("ok", case("ok"), answering("Sous 14 jours.")),
        ],
        [ExactMatch()],
        timeout_seconds=0.05,
    )

    by_key = {outcome.key: outcome for outcome in outcomes}
    assert by_key["lent"].status is CaseStatus.TIMEOUT
    assert by_key["lent"].error == "pas de réponse en 0.05 s"
    assert by_key["erreur"].status is CaseStatus.ERROR
    assert by_key["erreur"].error == "fournisseur en panne"
    assert by_key["erreur"].metrics == []
    assert by_key["ok"].status is CaseStatus.SUCCESS
    # Les résultats sortent dans l'ordre où ils se terminent : le cas lent en dernier.
    assert outcomes[-1].key == "lent"


async def test_un_evaluateur_en_panne_rend_la_metrique_indisponible():
    class Broken:
        name = "broken"

        async def evaluate(self, data: EvaluationInput) -> MetricOutput:
            raise ValueError("juge injoignable")

    (outcome,) = await collect([WorkItem("k", case(), answering("x"))], [Broken()])

    assert outcome.status is CaseStatus.SUCCESS
    assert outcome.metrics == [
        MetricOutput(name="broken", score=None, passed=None, reason="évaluateur en échec : juge injoignable")
    ]


# --- Agrégats ----------------------------------------------------------------------------------------------------


def test_percentile_au_rang_le_plus_proche():
    values = [float(v) for v in range(1, 101)]

    assert percentile(values, 50) == 50.0
    assert percentile(values, 95) == 95.0
    assert percentile([120.0], 95) == 120.0
    assert percentile([], 95) is None


def test_une_metrique_indisponible_ne_compte_pas_comme_zero():
    summary = summarize_metric("exact_match", [(1.0, True), (0.0, False), (None, None)])

    assert (summary.evaluated, summary.unavailable) == (2, 1)
    assert summary.mean_score == 0.5
    assert summary.pass_rate == 0.5
    assert summarize_metric("exact_match", [(None, None)]).mean_score is None


def test_le_cout_n_est_totalise_que_s_il_est_connu_pour_chaque_reponse():
    complete = summarize_performance(["success", "success"], [100.0, 300.0], [10, 20], [2, 4], [0.001, 0.002])
    partial = summarize_performance(["success", "success"], [100.0, 300.0], [10, None], [2, None], [0.001, None])

    assert complete.cost_usd == pytest.approx(0.003)
    assert complete.input_tokens == 30
    assert partial.cost_usd is None
    assert partial.cost_known_for == 1


def test_taux_de_reussite_et_cout_unitaire():
    summary = summarize_performance(["success", "error", "timeout", "success"], [100.0, 200.0], [], [], [])

    assert (summary.succeeded, summary.errors, summary.timeouts) == (2, 1, 1)
    assert summary.success_rate == 0.5
    assert cost_usd(1_000_000, 500_000, 0.27, 1.10) == pytest.approx(0.82)
    assert cost_usd(None, 10, 1, 1) is None
