import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import DateTime, Float, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from evalforge_api.core.database import Base, CreatedAtMixin, IdMixin


class RunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


class EvaluationRun(IdMixin, CreatedAtMixin, Base):
    __tablename__ = "evaluation_runs"

    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    experiment_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("experiments.id", ondelete="CASCADE"), index=True)
    dataset_version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("dataset_versions.id"))
    status: Mapped[str] = mapped_column(String(20), default=RunStatus.QUEUED)
    execution: Mapped[dict[str, Any]] = mapped_column(JSONB)
    # Ce qui a réellement été exécuté (prompt, modèles, paramètres, prix, métriques), sans les secrets :
    # modifier une configuration ensuite ne change pas l'histoire d'un run.
    configuration: Mapped[dict[str, Any]] = mapped_column(JSONB)
    total_items: Mapped[int] = mapped_column(Integer)
    completed_items: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Bail du worker qui exécute le run : renouvelé par battements. Un bail expiré permet à un autre de reprendre.
    worker_id: Mapped[str | None] = mapped_column(String(100))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")


class EvaluationResult(IdMixin, CreatedAtMixin, Base):
    """Une réponse produite pour un cas et un modèle. Ses mesures sont dans MetricResult."""

    __tablename__ = "evaluation_results"
    # Une seule réponse par cas et par modèle dans un run : un traitement rejoué ne crée pas de doublon.
    __table_args__ = (UniqueConstraint("run_id", "test_case_id", "model_configuration_id"),)

    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("evaluation_runs.id", ondelete="CASCADE"))
    test_case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("test_cases.id"))
    model_configuration_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("model_configurations.id"))
    status: Mapped[str] = mapped_column(String(20))
    actual_output: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    latency_ms: Mapped[float | None] = mapped_column(Float)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    cost_usd: Mapped[float | None] = mapped_column(Numeric(14, 8, asdecimal=False))
    provider_model: Mapped[str | None] = mapped_column(String(200))
    provider_request_id: Mapped[str | None] = mapped_column(String(200))
    # Agents : étapes suivies (outil, arguments, statut, résultat) et raison de l'arrêt.
    trajectory: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)
    stop_reason: Mapped[str | None] = mapped_column(String(40))


class MetricResult(IdMixin, Base):
    __tablename__ = "metric_results"
    __table_args__ = (UniqueConstraint("result_id", "name"),)

    result_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("evaluation_results.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(100))
    score: Mapped[float | None] = mapped_column(Float)
    passed: Mapped[bool | None]
    reason: Mapped[str | None] = mapped_column(Text)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
