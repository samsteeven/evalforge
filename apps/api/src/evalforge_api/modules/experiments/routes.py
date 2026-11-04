import uuid
from datetime import datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from evalforge_api.core.database import SessionDep
from evalforge_api.core.tenancy import EditorDep, WorkspaceDep, not_found
from evalforge_api.modules.experiments.models import Experiment, ExperimentModel
from evalforge_api.modules.projects.routes import get_project
from evalforge_api.modules.prompts.models import PromptVersion
from evalforge_api.modules.providers.models import ModelConfiguration
from evalforge_engine.evaluators import METRICS, available_metrics, needs_judge

router = APIRouter(tags=["experiments"])


class ExperimentCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    prompt_version_id: uuid.UUID
    model_configuration_ids: list[uuid.UUID] = Field(min_length=1, max_length=5)
    metrics: list[str] = Field(min_length=1, max_length=20)
    judge_model_configuration_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def judge_when_needed(self) -> "ExperimentCreate":
        if needs_judge(self.metrics) and self.judge_model_configuration_id is None:
            raise ValueError("les métriques judge_* demandent un modèle juge (judge_model_configuration_id)")
        if not needs_judge(self.metrics):
            self.judge_model_configuration_id = None  # pas de juge inutile figé dans les runs
        return self

    @field_validator("model_configuration_ids", "metrics")
    @classmethod
    def no_duplicates(cls, values: list) -> list:
        if len(set(values)) != len(values):
            raise ValueError("valeurs en double")
        return values

    @field_validator("metrics")
    @classmethod
    def known_metrics(cls, metrics: list[str]) -> list[str]:
        unknown = [name for name in metrics if name not in available_metrics()]
        if unknown:
            raise ValueError(
                f"métriques inconnues : {', '.join(unknown)} (disponibles : {', '.join(available_metrics())})"
            )
        return metrics


class ExperimentModelOut(BaseModel):
    model_configuration_id: uuid.UUID
    name: str


class ExperimentOut(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    name: str
    prompt_version_id: uuid.UUID
    models: list[ExperimentModelOut]
    metrics: list[str]
    judge: ExperimentModelOut | None
    created_at: datetime


async def get_experiment(session: AsyncSession, workspace_id: uuid.UUID, experiment_id: uuid.UUID) -> Experiment:
    experiment = await session.scalar(
        select(Experiment).where(Experiment.id == experiment_id, Experiment.workspace_id == workspace_id)
    )
    if experiment is None:
        raise not_found("Expérience")
    return experiment


async def experiment_models(session: AsyncSession, experiment_id: uuid.UUID) -> list[ExperimentModel]:
    rows = await session.scalars(
        select(ExperimentModel).where(ExperimentModel.experiment_id == experiment_id).order_by(ExperimentModel.position)
    )
    return list(rows)


async def to_out(session: AsyncSession, experiment: Experiment) -> ExperimentOut:
    models = await experiment_models(session, experiment.id)
    judge = None
    if experiment.judge_model_configuration_id is not None:
        config = await session.get(ModelConfiguration, experiment.judge_model_configuration_id)
        judge = ExperimentModelOut(model_configuration_id=config.id, name=config.name) if config else None
    return ExperimentOut(
        id=experiment.id,
        project_id=experiment.project_id,
        name=experiment.name,
        prompt_version_id=experiment.prompt_version_id,
        models=[ExperimentModelOut(model_configuration_id=m.model_configuration_id, name=m.label) for m in models],
        metrics=experiment.metrics,
        judge=judge,
        created_at=experiment.created_at,
    )


class MetricInfo(BaseModel):
    name: str
    description: str
    method: str
    requires_expected: bool
    requires_context: bool
    needs_judge: bool


@router.get("/metrics", response_model=list[MetricInfo])
async def list_metrics() -> list[MetricInfo]:
    return [
        MetricInfo(
            name=spec.name,
            description=spec.description,
            method=spec.method,
            requires_expected=spec.requires_expected,
            requires_context=spec.requires_context,
            needs_judge=spec.needs_judge,
        )
        for spec in (METRICS[name] for name in available_metrics())
    ]


@router.post("/projects/{project_id}/experiments", status_code=201, response_model=ExperimentOut)
async def create_experiment(
    project_id: uuid.UUID, body: ExperimentCreate, workspace_id: EditorDep, session: SessionDep
) -> ExperimentOut:
    await get_project(session, workspace_id, project_id)
    prompt = await session.scalar(
        select(PromptVersion).where(PromptVersion.id == body.prompt_version_id, PromptVersion.project_id == project_id)
    )
    if prompt is None:
        raise HTTPException(422, "Version de prompt introuvable dans ce projet")
    configs = {
        config.id: config
        for config in await session.scalars(
            select(ModelConfiguration).where(
                ModelConfiguration.id.in_(body.model_configuration_ids), ModelConfiguration.project_id == project_id
            )
        )
    }
    if len(configs) != len(body.model_configuration_ids):
        raise HTTPException(422, "Configuration de modèle introuvable dans ce projet")
    if body.judge_model_configuration_id is not None:
        judge = await session.scalar(
            select(ModelConfiguration.id).where(
                ModelConfiguration.id == body.judge_model_configuration_id, ModelConfiguration.project_id == project_id
            )
        )
        if judge is None:
            raise HTTPException(422, "Modèle juge introuvable dans ce projet")

    experiment = Experiment(
        workspace_id=workspace_id,
        project_id=project_id,
        name=body.name,
        prompt_version_id=prompt.id,
        metrics=body.metrics,
        judge_model_configuration_id=body.judge_model_configuration_id,
    )
    session.add(experiment)
    await session.flush()
    session.add_all(
        ExperimentModel(
            experiment_id=experiment.id,
            model_configuration_id=config_id,
            position=position,
            label=configs[config_id].name,
        )
        for position, config_id in enumerate(body.model_configuration_ids)
    )
    try:
        await session.commit()
    except IntegrityError:
        raise HTTPException(409, "Une expérience porte déjà ce nom dans ce projet") from None
    return await to_out(session, experiment)


@router.get("/projects/{project_id}/experiments", response_model=list[ExperimentOut])
async def list_experiments(
    project_id: uuid.UUID, workspace_id: WorkspaceDep, session: SessionDep
) -> list[ExperimentOut]:
    await get_project(session, workspace_id, project_id)
    rows = await session.scalars(
        select(Experiment).where(Experiment.project_id == project_id).order_by(Experiment.created_at)
    )
    return [await to_out(session, row) for row in rows]


@router.get("/experiments/{experiment_id}", response_model=ExperimentOut)
async def read_experiment(experiment_id: uuid.UUID, workspace_id: WorkspaceDep, session: SessionDep) -> ExperimentOut:
    return await to_out(session, await get_experiment(session, workspace_id, experiment_id))
