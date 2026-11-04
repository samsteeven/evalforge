"""Tous les modèles, pour Alembic : un module qui n'est pas importé ici n'a pas de table."""

from evalforge_api.core.database import Base
from evalforge_api.modules.datasets.models import Dataset, DatasetVersion, TestCase
from evalforge_api.modules.evaluations.models import EvaluationResult, EvaluationRun, MetricResult
from evalforge_api.modules.experiments.models import Experiment, ExperimentModel
from evalforge_api.modules.projects.models import AIProject
from evalforge_api.modules.prompts.models import PromptVersion
from evalforge_api.modules.providers.models import ModelConfiguration
from evalforge_api.modules.workspaces.models import Workspace

__all__ = [
    "AIProject",
    "Base",
    "Dataset",
    "DatasetVersion",
    "EvaluationResult",
    "EvaluationRun",
    "Experiment",
    "ExperimentModel",
    "MetricResult",
    "ModelConfiguration",
    "PromptVersion",
    "TestCase",
    "Workspace",
]
