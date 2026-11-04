"""`evalforge` : évaluer en local ou en CI.

    evalforge run evalforge.yaml --report rapport.html --json resultats.json
    evalforge metrics

Codes de sortie : 0 tout va bien, 1 porte de qualité non franchie, 2 configuration ou données invalides.
"""

import asyncio
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from evalforge_cli.config import ConfigError, load_config
from evalforge_cli.runner import run_evaluation
from evalforge_engine.datasets import DatasetFormatError
from evalforge_engine.evaluators import METRICS
from evalforge_engine.reporting import render_report

app = typer.Typer(help="EvalForge : évaluer des applications LLM en local ou en CI.", no_args_is_help=True)
# Pas de retour à la ligne forcé dans les messages : un chemin coupé ne se copie plus depuis un journal de CI.
console = Console(soft_wrap=True)


def _percent(value: float | None) -> str:
    return "—" if value is None else f"{round(value * 100)} %"


@app.command()
def run(
    config_path: Annotated[Path, typer.Argument(help="Fichier YAML de l'évaluation", exists=True, dir_okay=False)],
    report: Annotated[Path | None, typer.Option(help="Écrit le rapport HTML")] = None,
    json_output: Annotated[Path | None, typer.Option("--json", help="Écrit le rapport en JSON")] = None,
) -> None:
    """Exécute l'évaluation décrite dans CONFIG_PATH et applique la porte de qualité."""
    try:
        config = load_config(config_path)
        evaluation = asyncio.run(run_evaluation(config))
    except (ConfigError, DatasetFormatError) as error:
        console.print(f"[red]Configuration invalide[/red] : {error}")
        raise typer.Exit(2) from None

    table = Table(title=evaluation.report.title)
    table.add_column("Modèle")
    for metric in config.metrics:
        table.add_column(metric, justify="right")
    for column in ("Réussite", "P95", "Coût"):
        table.add_column(column, justify="right")
    for model in evaluation.report.models:
        rates = {m.name: m.pass_rate for m in model.metrics}
        p = model.performance
        cost = "inconnu" if p.cost_usd is None else f"{p.cost_usd:.6f} $"
        p95 = "—" if p.p95_latency_ms is None else f"{round(p.p95_latency_ms)} ms"
        table.add_row(
            model.model, *[_percent(rates.get(m)) for m in config.metrics], _percent(p.success_rate), p95, cost
        )
    console.print(table)

    if report:
        report.write_text(render_report(evaluation.report), encoding="utf-8")
        console.print(f"Rapport HTML : {report}")
    if json_output:
        json_output.write_text(evaluation.report.model_dump_json(indent=2), encoding="utf-8")
        console.print(f"Rapport JSON : {json_output}")

    if evaluation.failures:
        for f in evaluation.failures:
            console.print(f"[red]✗[/red] {f.model} : {f.metric} à {_percent(f.actual)}, minimum {_percent(f.required)}")
        raise typer.Exit(1)
    if config.thresholds:
        console.print("[green]✓[/green] Porte de qualité franchie")


@app.command()
def metrics() -> None:
    """Liste les métriques disponibles et ce dont elles ont besoin."""
    table = Table()
    for column in ("Métrique", "Méthode", "Besoins", "Description"):
        table.add_column(column)
    for spec in METRICS.values():
        needs = [
            label
            for label, needed in (
                ("réponse attendue", spec.requires_expected),
                ("contexte", spec.requires_context),
                ("modèle juge", spec.needs_judge),
            )
            if needed
        ]
        table.add_row(spec.name, spec.method, ", ".join(needs), spec.description)
    console.print(table)
