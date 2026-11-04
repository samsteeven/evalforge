"""Rapport HTML d'un run : un seul fichier, sans dépendance externe, lisible hors ligne et imprimable.

Le même rendu sert l'API (`GET /runs/{id}/report`) et la CLI (`evalforge run … --report`).
"""

from datetime import datetime
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape
from pydantic import BaseModel

from evalforge_engine.scoring import ModelReport, ResultRecord

_TEMPLATES = Path(__file__).parent / "templates"
_env = Environment(loader=FileSystemLoader(_TEMPLATES), autoescape=select_autoescape(["html", "j2"]))


def _percent(value: float | None) -> str:
    return "—" if value is None else f"{round(value * 100)} %"


def _ms(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value / 1000:.1f} s".replace(".", ",") if value >= 1000 else f"{round(value)} ms"


def _usd(value: float | None) -> str:
    if value is None:
        return "inconnu"
    if value == 0:
        return "0 $"
    text = str(float(f"{value:.2g}")) if value < 0.01 else f"{value:.4f}" if value < 1 else f"{value:.2f}"
    return text.replace(".", ",") + " $"


_env.filters.update(percent=_percent, ms=_ms, usd=_usd)


class ReportModel(BaseModel):
    name: str
    model_name: str
    provider: str


class ReportConfig(BaseModel):
    prompt_name: str
    prompt_version: int | None = None
    template: str
    system: str | None = None
    dataset_label: str
    dataset_checksum: str | None = None
    models: list[ReportModel]
    metrics: list[str]
    judge: str | None = None
    execution: dict[str, Any] = {}


class Report(BaseModel):
    title: str
    generated_at: datetime
    status: str
    config: ReportConfig
    models: list[ModelReport]
    results: list[ResultRecord]


def render_report(report: Report) -> str:
    return _env.get_template("report.html.j2").render(report=report)
