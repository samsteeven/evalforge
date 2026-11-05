"""Baseline d'un projet et comparaison d'un run à une baseline (détection de régression)."""

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import DateTime, Float, ForeignKey, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from evalforge_api.core.database import Base, SessionDep, utcnow
from evalforge_api.core.tenancy import AccessDep, WorkspaceDep, not_found
from evalforge_api.modules.evaluations.models import EvaluationRun, RunStatus
from evalforge_api.modules.evaluations.routes import _records, get_run
from evalforge_api.modules.experiments.models import Experiment
from evalforge_api.modules.projects.routes import get_project
from evalforge_engine.comparison import Comparison, compare

router = APIRouter(tags=["baselines"])

DEFAULT_TOLERANCE = 0.02


class ProjectBaseline(Base):
    """Le run de référence d'un projet : les suivants s'y comparent avant d'être adoptés."""

    __tablename__ = "project_baselines"

    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ai_projects.id", ondelete="CASCADE"), primary_key=True)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("evaluation_runs.id", ondelete="CASCADE"))
    # Baisse de taux de réussite tolérée avant de parler de régression (0,02 = 2 points).
    tolerance: Mapped[float] = mapped_column(Float)
    set_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    set_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class BaselineIn(BaseModel):
    run_id: uuid.UUID
    tolerance: float = Field(default=DEFAULT_TOLERANCE, ge=0, le=1)


class BaselineOut(BaseModel):
    project_id: uuid.UUID
    run_id: uuid.UUID
    tolerance: float
    set_at: datetime


class ComparisonOut(BaseModel):
    baseline_run_id: uuid.UUID
    candidate_run_id: uuid.UUID
    dataset_checksum: str
    comparison: Comparison


async def project_of(session: AsyncSession, run: EvaluationRun) -> uuid.UUID:
    return await session.scalar(select(Experiment.project_id).where(Experiment.id == run.experiment_id))


@router.put("/projects/{project_id}/baseline", response_model=BaselineOut)
async def set_baseline(project_id: uuid.UUID, body: BaselineIn, access: AccessDep, session: SessionDep) -> BaselineOut:
    """Adopter un run comme référence : réservé à qui peut évaluer (membre et au-dessus)."""
    if not access.at_least("member"):
        raise HTTPException(403, "Rôle insuffisant : member au minimum")
    await get_project(session, access.workspace_id, project_id)
    run = await get_run(session, access.workspace_id, body.run_id)
    if await project_of(session, run) != project_id:
        raise HTTPException(422, "Ce run n'appartient pas au projet")
    if run.status != RunStatus.COMPLETED:
        raise HTTPException(422, "Seul un run terminé peut servir de baseline")
    now = utcnow()
    await session.execute(
        insert(ProjectBaseline)
        .values(project_id=project_id, run_id=run.id, tolerance=body.tolerance, set_by=access.user_id, set_at=now)
        .on_conflict_do_update(
            index_elements=["project_id"],
            set_={"run_id": run.id, "tolerance": body.tolerance, "set_by": access.user_id, "set_at": now},
        )
    )
    await session.commit()
    return BaselineOut(project_id=project_id, run_id=run.id, tolerance=body.tolerance, set_at=now)


@router.get("/projects/{project_id}/baseline", response_model=BaselineOut | None)
async def read_baseline(project_id: uuid.UUID, workspace_id: WorkspaceDep, session: SessionDep) -> BaselineOut | None:
    await get_project(session, workspace_id, project_id)
    baseline = await session.get(ProjectBaseline, project_id)
    if baseline is None:
        return None
    return BaselineOut(
        project_id=project_id, run_id=baseline.run_id, tolerance=baseline.tolerance, set_at=baseline.set_at
    )


@router.get("/runs/{run_id}/comparison", response_model=ComparisonOut)
async def compare_to_baseline(
    run_id: uuid.UUID,
    workspace_id: WorkspaceDep,
    session: SessionDep,
    baseline_run_id: Annotated[uuid.UUID | None, Query(description="Par défaut : la baseline du projet")] = None,
    tolerance: Annotated[float | None, Query(ge=0, le=1)] = None,
) -> ComparisonOut:
    """Compare un run à la baseline : mêmes cas (même empreinte de jeu), modèles appariés par leur nom."""
    candidate = await get_run(session, workspace_id, run_id)
    project_id = await project_of(session, candidate)
    stored = await session.get(ProjectBaseline, project_id)
    if baseline_run_id is None:
        if stored is None:
            raise not_found("Baseline du projet")
        baseline_run_id = stored.run_id
    baseline = await get_run(session, workspace_id, baseline_run_id)
    if await project_of(session, baseline) != project_id:
        raise HTTPException(422, "La baseline appartient à un autre projet")
    if RunStatus.COMPLETED not in (candidate.status, baseline.status) or candidate.status != baseline.status:
        raise HTTPException(409, "Les deux runs doivent être terminés pour être comparés")
    checksum = candidate.configuration["dataset_version"]["checksum"]
    if baseline.configuration["dataset_version"]["checksum"] != checksum:
        raise HTTPException(422, "Jeux de données différents : comparaison cas par cas impossible")

    metrics = [name for name in candidate.configuration["metrics"] if name in baseline.configuration["metrics"]]
    applied = tolerance if tolerance is not None else stored.tolerance if stored else DEFAULT_TOLERANCE
    return ComparisonOut(
        baseline_run_id=baseline.id,
        candidate_run_id=candidate.id,
        dataset_checksum=checksum,
        comparison=compare(
            await _records(session, baseline), await _records(session, candidate), metrics, tolerance=applied
        ),
    )
