import uuid

from sqlalchemy import ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from evalforge_api.core.database import Base, CreatedAtMixin, IdMixin


class Experiment(IdMixin, CreatedAtMixin, Base):
    __tablename__ = "experiments"
    __table_args__ = (UniqueConstraint("project_id", "name"),)

    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ai_projects.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(200))
    prompt_version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("prompt_versions.id"))
    metrics: Mapped[list[str]] = mapped_column(JSONB)
    # Modèle qui note les réponses pour les métriques « judge_* » ; absent si aucune ne le demande.
    judge_model_configuration_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("model_configurations.id"))


class ExperimentModel(Base):
    """Modèles comparés par une expérience, dans l'ordre choisi."""

    __tablename__ = "experiment_models"
    __table_args__ = (UniqueConstraint("experiment_id", "position"),)

    experiment_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("experiments.id", ondelete="CASCADE"), primary_key=True)
    model_configuration_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("model_configurations.id"), primary_key=True)
    position: Mapped[int] = mapped_column(Integer)
    # Rappel du nom pour l'affichage, la configuration complète est figée dans chaque run.
    label: Mapped[str] = mapped_column(String(200))
