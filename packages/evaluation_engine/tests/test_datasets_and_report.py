from datetime import UTC, datetime

import pytest

from evalforge_engine.datasets import DatasetFormatError, load_cases, parse, parse_csv, parse_json
from evalforge_engine.reporting import Report, ReportConfig, ReportModel, render_report
from evalforge_engine.scoring import MetricRecord, ResultRecord, summarize_by_model

CSV = (
    "﻿id;question;expected_answer;context;metadata.category\n"
    "faq-1;Délai de remboursement ?;Sous 14 jours.;Politique : 14 jours.||Exception : soldes;support\n"
    ";Frais de retour ?;;;\n"
    ";;;;\n"
)


def test_csv_au_format_excel_francais():
    cases = parse_csv(CSV)

    assert cases == [
        {
            "id": "faq-1",
            "input": {"question": "Délai de remboursement ?"},
            "expected": {"answer": "Sous 14 jours."},
            "context": ["Politique : 14 jours.", "Exception : soldes"],
            "metadata": {"category": "support"},
        },
        {"input": {"question": "Frais de retour ?"}, "metadata": {}, "context": []},
    ]


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("expected_answer,context\nx,y\n", "aucune colonne d'entrée"),
        ("question,question\na,b\n", "colonnes en double"),
        ("question,expected_answer\n,réponse\n", "ligne 2 : aucune valeur d'entrée"),
        ("question\n", "aucun cas"),
    ],
)
def test_csv_invalide(text, message):
    with pytest.raises(DatasetFormatError, match=message):
        parse_csv(text)


def test_json_liste_ou_objet_et_limites():
    assert parse_json('[{"input": {"q": 1}}]') == parse_json('{"cases": [{"input": {"q": 1}}]}')
    with pytest.raises(DatasetFormatError, match="ligne 1"):
        parse_json("[{")
    with pytest.raises(DatasetFormatError, match="1000 au plus"):
        parse_json("[" + ",".join(['{"input": {"q": 1}}'] * 1001) + "]")
    with pytest.raises(DatasetFormatError, match=r"\.json ou \.csv"):
        parse("[]", "cas.xlsx")


def test_chargement_pour_la_cli(tmp_path):
    path = tmp_path / "cas.csv"
    path.write_text("question,expected_answer\nA ?,a\nB ?,b\n", encoding="utf-8")

    cases = load_cases(path)

    assert [case.id for case in cases] == ["case-001", "case-002"]
    assert cases[1].expected_answer == "b"


def test_le_rapport_html_echappe_le_contenu_et_garde_le_cout_inconnu():
    records = [
        ResultRecord(
            case_id="faq-1",
            model="modèle A",
            status="success",
            output="<script>alert(1)</script>",
            latency_ms=320,
            input_tokens=100,
            output_tokens=10,
            cost_usd=None,
            metrics=[MetricRecord(name="judge_correctness", score=0.75, passed=True, reason="Même délai.")],
        ),
        ResultRecord(case_id="faq-2", model="modèle A", status="timeout", error="pas de réponse en 60 s"),
    ]
    report = Report(
        title="Run d'évaluation",
        generated_at=datetime(2026, 11, 5, 10, 30, tzinfo=UTC),
        status="completed",
        config=ReportConfig(
            prompt_name="faq",
            prompt_version=2,
            template="Question : {{ question }}",
            dataset_label="FAQ v1",
            models=[ReportModel(name="modèle A", model_name="deepseek-chat", provider="openai_compatible")],
            metrics=["judge_correctness"],
            judge="juge (gpt-judge)",
        ),
        models=summarize_by_model(["modèle A"], ["judge_correctness"], records),
        results=records,
    )

    html = render_report(report)

    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html
    assert "Question : {{ question }}" in html
    assert "inconnu" in html
    assert "Même délai." in html
    assert "(75 %)" in html
    assert "timeout : pas de réponse en 60 s" in html
    assert "100 %" in html  # taux de réussite sur la seule réponse obtenue
