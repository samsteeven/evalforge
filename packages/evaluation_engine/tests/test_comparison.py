import pytest

from evalforge_engine.comparison import compare, mcnemar_exact
from evalforge_engine.scoring import MetricRecord, ResultRecord


def record(case: str, passed: bool | None, model: str = "deepseek", status: str = "success", reason: str | None = None):
    return ResultRecord(
        case_id=case,
        model=model,
        status=status,
        latency_ms=100,
        metrics=[
            MetricRecord(
                name="exact_match", score=None if passed is None else float(passed), passed=passed, reason=reason
            )
        ],
    )


def test_mcnemar_exact():
    assert mcnemar_exact(0, 0) is None
    assert mcnemar_exact(0, 1) == 1.0  # un seul cas discordant ne prouve rien
    assert mcnemar_exact(0, 6) == pytest.approx(0.03125)  # 2 × 0,5⁶
    assert mcnemar_exact(3, 3) == 1.0
    assert mcnemar_exact(1, 9) == pytest.approx(0.021484375)


def test_les_cas_casses_et_repares_sont_appariees_et_la_regression_detectee():
    baseline = [record("c1", True), record("c2", True), record("c3", False), record("c4", True)]
    candidate = [
        record("c1", True),
        record("c2", False, reason="Répond à côté."),
        record("c3", True),
        record("c4", False),
    ]

    result = compare(baseline, candidate, ["exact_match"], tolerance=0.0)

    (model,) = result.models
    (metric,) = model.metrics
    assert (metric.baseline_rate, metric.candidate_rate, metric.delta) == (0.75, 0.5, -0.25)
    assert (metric.paired, metric.fixed, metric.broken) == (4, 1, 2)
    assert metric.regressed and not metric.improved
    assert metric.significant is False  # 3 cas discordants : rien de prouvé statistiquement
    assert result.regressed
    broken = [c for c in result.changes if c.change == "broken"]
    assert [c.case_id for c in broken] == ["c2", "c4"]
    assert broken[0].candidate_reason == "Répond à côté."


def test_une_baisse_dans_la_tolerance_n_est_pas_une_regression():
    baseline = [record(f"c{i}", True) for i in range(50)]
    candidate = [record(f"c{i}", i != 0) for i in range(50)]  # 98 %

    assert not compare(baseline, candidate, ["exact_match"], tolerance=0.02).regressed
    assert compare(baseline, candidate, ["exact_match"], tolerance=0.0).regressed


def test_une_metrique_indisponible_n_entre_pas_dans_l_appariement():
    baseline = [record("c1", True), record("c2", True)]
    candidate = [record("c1", None), record("c2", True)]

    (metric,) = compare(baseline, candidate, ["exact_match"]).models[0].metrics

    assert metric.paired == 1
    assert metric.broken == 0
    assert metric.candidate_rate == 1.0
    assert not metric.regressed


def test_les_modeles_absents_d_un_cote_sont_signales():
    baseline = [record("c1", True, model="deepseek"), record("c1", True, model="llama")]
    candidate = [record("c1", True, model="deepseek"), record("c1", True, model="mistral")]

    result = compare(baseline, candidate, ["exact_match"])

    assert [m.model for m in result.models] == ["deepseek"]
    assert (result.only_in_baseline, result.only_in_candidate) == (["llama"], ["mistral"])


def test_une_grosse_regression_est_significative():
    baseline = [record(f"c{i}", True) for i in range(20)]
    candidate = [record(f"c{i}", i >= 8) for i in range(20)]

    (metric,) = compare(baseline, candidate, ["exact_match"]).models[0].metrics

    assert metric.broken == 8
    assert metric.p_value == pytest.approx(2 / 2**8)
    assert metric.significant
