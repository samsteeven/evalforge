"""Fichier de configuration d'une évaluation locale (YAML). Les chemins sont relatifs au fichier.

Aucune clé d'API dans le fichier : seulement le nom de la variable d'environnement qui la contient.
"""

import os
from pathlib import Path

import yaml
from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, ValidationError, model_validator

from evalforge_engine.evaluators import METRICS, available_metrics, needs_judge
from evalforge_llm.network import UnsafeDestinationError, check_url


class ConfigError(ValueError):
    pass


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Pricing(Strict):
    input_per_million_usd: float = Field(ge=0)
    output_per_million_usd: float = Field(ge=0)


class ModelConfig(Strict):
    name: str
    base_url: AnyHttpUrl
    model: str
    api_key_env: str | None = None
    temperature: float = Field(default=0.0, ge=0, le=2)
    max_tokens: int | None = Field(default=None, ge=1)
    pricing: Pricing | None = None

    def api_key(self) -> str | None:
        if self.api_key_env is None:
            return None
        key = os.environ.get(self.api_key_env)
        if not key:
            raise ConfigError(f"variable d'environnement {self.api_key_env} absente (clé du modèle « {self.name} »)")
        return key


class PromptConfig(Strict):
    template: str | None = None
    template_file: Path | None = None
    system: str | None = None
    name: str = "prompt"

    @model_validator(mode="after")
    def one_source(self) -> "PromptConfig":
        if (self.template is None) == (self.template_file is None):
            raise ValueError("indiquer template ou template_file, pas les deux")
        return self


class ExecutionConfig(Strict):
    max_concurrency: int = Field(default=5, ge=1, le=50)
    timeout_seconds: float = Field(default=60, gt=0, le=600)


class EvalConfig(Strict):
    dataset: Path
    prompt: PromptConfig
    models: list[ModelConfig] = Field(min_length=1, max_length=10)
    metrics: list[str] = Field(min_length=1)
    judge: ModelConfig | None = None
    execution: ExecutionConfig = Field(default_factory=ExecutionConfig)
    # Porte de qualité : taux de réussite minimal par métrique, exigé de chaque modèle.
    thresholds: dict[str, float] = Field(default_factory=dict)
    # Autorise les adresses locales (Ollama, faux fournisseur) ; refusées par défaut, comme dans la plateforme.
    allow_private_urls: bool = False

    @model_validator(mode="after")
    def coherent(self) -> "EvalConfig":
        unknown = [name for name in self.metrics if name not in METRICS]
        if unknown:
            raise ValueError(f"métriques inconnues : {', '.join(unknown)} (disponibles : {', '.join(sorted(METRICS))})")
        absent = [name for name in self.metrics if name not in available_metrics()]
        if absent:
            group = METRICS[absent[0]].method
            raise ValueError(f"{', '.join(absent)} : bibliothèque {group} non installée (uv sync --group {group})")
        if needs_judge(self.metrics) and self.judge is None:
            raise ValueError("ces métriques interrogent un modèle : section judge requise")
        stray = [name for name in self.thresholds if name not in self.metrics]
        if stray:
            raise ValueError(f"seuil pour une métrique non mesurée : {', '.join(stray)}")
        if any(not 0 <= value <= 1 for value in self.thresholds.values()):
            raise ValueError("un seuil est un taux entre 0 et 1")
        names = [model.name for model in self.models]
        if len(set(names)) != len(names):
            raise ValueError("deux modèles portent le même nom")
        return self


def load_config(path: Path) -> EvalConfig:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        raise ConfigError(f"lecture de {path} impossible : {error}") from None
    try:
        config = EvalConfig.model_validate(raw)
    except ValidationError as error:
        first = error.errors()[0]
        where = ".".join(str(part) for part in first["loc"]) or "racine"
        raise ConfigError(f"{path.name} : {where} : {first['msg']}") from None
    base = path.parent
    config.dataset = (base / config.dataset).resolve()
    if not config.dataset.is_file():
        raise ConfigError(f"jeu de données introuvable : {config.dataset}")
    if config.prompt.template_file is not None:
        config.prompt.template_file = (base / config.prompt.template_file).resolve()
        try:
            config.prompt.template = config.prompt.template_file.read_text(encoding="utf-8")
        except OSError as error:
            raise ConfigError(f"gabarit illisible : {error}") from None
    for model in [*config.models, *([config.judge] if config.judge else [])]:
        try:
            check_url(str(model.base_url), allow_private=config.allow_private_urls)
        except UnsafeDestinationError as error:
            raise ConfigError(
                f"modèle « {model.name} » : {error} (allow_private_urls: true pour un modèle local)"
            ) from None
    return config
