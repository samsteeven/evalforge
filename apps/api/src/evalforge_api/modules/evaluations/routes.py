import uuid
from collections import defaultdict
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from evalforge_api.core.database import SessionDep
from evalforge_api.core.tenancy import WorkspaceDep, not_found
from evalforge_api.modules.datasets.models import Dataset, TestCase
from evalforge_api.modules.datasets.routes import get_dataset_version
from evalforge_api.modules.evaluations.models import EvaluationResult, EvaluationRun, MetricResult, RunStatus
from evalforge_api.modules.experiments.routes import experiment_models, get_experiment
from evalforge_api.modules.prompts.models import PromptVersion
from evalforge_api.modules.providers.models import ModelConfiguration
from evalforge_engine.scoring import MetricSummary, PerformanceSummary, summarize_metric, summarize_performance

router = APIRouter(tags=["evaluations"])


class ExecutionOptions(BaseModel):
    max_concurrency: int = Field(default=5, ge=1, le=20)
    timeout_seconds: float = Field(default=60, gt=0, le=600)


class RunCreate(BaseModel):
    dataset_version_id: uuid.UUID
    execution: ExecutionOptions = Field(default_factory=ExecutionOptions)


class RunCreated(BaseModel):
    run_id: uuid.UUID
    status: str
    created_at: datetime


class RunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    experiment_id: uuid.UUID
    dataset_version_id: uuid.UUID
    status: str
    execution: dict[str, Any]
    configuration: dict[str, Any]
    total_items: int
    completed_items: int
    error: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class MetricOut(BaseModel):
    name: str
    score: float | None
    passed: bool | None
    reason: str | None


class ResultOut(BaseModel):
    id: uuid.UUID
    case_id: str
    model_configuration_id: uuid.UUID
    model: str
    status: str
    actual_output: str | None
    error: str | None
    latency_ms: float | None
    input_tokens: int | None
    output_tokens: int | None
    cost_usd: float | None
    provider_model: str | None
    metrics: list[MetricOut]


class ModelSummary(BaseModel):
    model_configuration_id: uuid.UUID
    model: str
    performance: PerformanceSummary
    metrics: list[MetricSummary]


class RunMetrics(BaseModel):
    run_id: uuid.UUID
    status: str
    models: list[ModelSummary]


async def get_run(session: AsyncSession, workspace_id: uuid.UUID, run_id: uuid.UUID) -> EvaluationRun:
    run = await session.scalar(
        select(EvaluationRun).where(EvaluationRun.id == run_id, EvaluationRun.workspace_id == workspace_id)
    )
    if run is None:
        raise not_found("Run")
    return run


@router.post("/experiments/{experiment_id}/runs", status_code=202, response_model=RunCreated)
async def create_run(
    experiment_id: uuid.UUID, body: RunCreate, request: Request, workspace_id: WorkspaceDep, session: SessionDep
) -> RunCreated:
    experiment = await get_experiment(session, workspace_id, experiment_id)
    version = await get_dataset_version(session, workspace_id, body.dataset_version_id)
    dataset = await session.get(Dataset, version.dataset_id)
    if dataset is None or dataset.project_id != experiment.project_id:
        raise HTTPException(422, "Ce jeu de données n'appartient pas au projet de l'expérience")
    prompt = await session.get(PromptVersion, experiment.prompt_version_id)
    models = await experiment_models(session, experiment.id)
    configs = {
        config.id: config
        for config in await session.scalars(
            select(ModelConfiguration).where(ModelConfiguration.id.in_([m.model_configuration_id for m in models]))
        )
    }

    run = EvaluationRun(
        workspace_id=workspace_id,
        experiment_id=experiment.id,
        dataset_version_id=version.id,
        status=RunStatus.QUEUED,
        execution=body.execution.model_dump(),
        configuration={
            "dataset_version": {"id": str(version.id), "version": version.version, "checksum": version.checksum},
            "prompt": {
                "id": str(prompt.id),
                "name": prompt.name,
                "version": prompt.version,
                "template": prompt.template,
                "system": prompt.system,
            },
            "models": [
                {
                    "id": str(config.id),
                    "name": config.name,
                    "provider_kind": config.provider_kind,
                    "base_url": config.base_url,
                    "model_name": config.model_name,
                    "parameters": config.parameters,
                    "pricing": config.pricing,
                }
                for config in (configs[m.model_configuration_id] for m in models)
            ],
            "metrics": experiment.metrics,
        },
        total_items=version.case_count * len(models),
        completed_items=0,
    )
    session.add(run)
    await session.commit()
    request.app.state.scheduler.schedule(run.id)
    return RunCreated(run_id=run.id, status=run.status, created_at=run.created_at)


@router.get("/runs/{run_id}", response_model=RunOut)
async def read_run(run_id: uuid.UUID, workspace_id: WorkspaceDep, session: SessionDep) -> EvaluationRun:
    return await get_run(session, workspace_id, run_id)


def _model_names(run: EvaluationRun) -> dict[uuid.UUID, str]:
    return {uuid.UUID(model["id"]): model["name"] for model in run.configuration["models"]}


async def _metrics_by_result(session: AsyncSession, result_ids: list[uuid.UUID]) -> dict[uuid.UUID, list[MetricResult]]:
    grouped: dict[uuid.UUID, list[MetricResult]] = defaultdict(list)
    if result_ids:
        for metric in await session.scalars(
            select(MetricResult).where(MetricResult.result_id.in_(result_ids)).order_by(MetricResult.name)
        ):
            grouped[metric.result_id].append(metric)
    return grouped


@router.get("/runs/{run_id}/results", response_model=list[ResultOut])
async def list_results(
    run_id: uuid.UUID,
    workspace_id: WorkspaceDep,
    session: SessionDep,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> list[ResultOut]:
    run = await get_run(session, workspace_id, run_id)
    names = _model_names(run)
    order = {model_id: position for position, model_id in enumerate(names)}
    rows = (
        await session.execute(
            select(EvaluationResult, TestCase.external_id, TestCase.position)
            .join(TestCase, TestCase.id == EvaluationResult.test_case_id)
            .where(EvaluationResult.run_id == run.id)
        )
    ).all()
    rows.sort(key=lambda row: (row.position, order.get(row.EvaluationResult.model_configuration_id, 0)))
    page = rows[offset : offset + limit]
    metrics = await _metrics_by_result(session, [row.EvaluationResult.id for row in page])
    return [
        ResultOut(
            id=result.id,
            case_id=external_id,
            model_configuration_id=result.model_configuration_id,
            model=names.get(result.model_configuration_id, "?"),
            status=result.status,
            actual_output=result.actual_output,
            error=result.error,
            latency_ms=result.latency_ms,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            cost_usd=result.cost_usd,
            provider_model=result.provider_model,
            metrics=[
                MetricOut(name=m.name, score=m.score, passed=m.passed, reason=m.reason) for m in metrics[result.id]
            ],
        )
        for result, external_id, _ in page
    ]


@router.get("/runs/{run_id}/metrics", response_model=RunMetrics)
async def run_metrics(run_id: uuid.UUID, workspace_id: WorkspaceDep, session: SessionDep) -> RunMetrics:
    """Agrégats par modèle : qualité (par métrique), puis performance, tokens et coût, sans les mélanger."""
    run = await get_run(session, workspace_id, run_id)
    results = list(await session.scalars(select(EvaluationResult).where(EvaluationResult.run_id == run.id)))
    metrics = await _metrics_by_result(session, [result.id for result in results])

    summaries = []
    for model_id, name in _model_names(run).items():
        own = [result for result in results if result.model_configuration_id == model_id]
        succeeded = [result for result in own if result.status == "success"]
        scores: dict[str, list[tuple[float | None, bool | None]]] = {
            metric: [] for metric in run.configuration["metrics"]
        }
        for result in succeeded:
            for metric in metrics[result.id]:
                scores.setdefault(metric.name, []).append((metric.score, metric.passed))
        summaries.append(
            ModelSummary(
                model_configuration_id=model_id,
                model=name,
                performance=summarize_performance(
                    [result.status for result in own],
                    [result.latency_ms for result in succeeded if result.latency_ms is not None],
                    [result.input_tokens for result in succeeded],
                    [result.output_tokens for result in succeeded],
                    [result.cost_usd for result in succeeded],
                ),
                metrics=[summarize_metric(metric, values) for metric, values in scores.items()],
            )
        )
    return RunMetrics(run_id=run.id, status=run.status, models=summaries)
