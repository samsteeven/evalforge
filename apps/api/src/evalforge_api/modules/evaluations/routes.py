import uuid
from collections import defaultdict
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from evalforge_api.core.database import SessionDep, utcnow
from evalforge_api.core.tenancy import EditorDep, WorkspaceDep, not_found
from evalforge_api.modules.datasets.models import Dataset, TestCase
from evalforge_api.modules.datasets.routes import get_dataset_version
from evalforge_api.modules.evaluations.models import EvaluationResult, EvaluationRun, MetricResult, RunStatus
from evalforge_api.modules.experiments.models import Experiment
from evalforge_api.modules.experiments.routes import experiment_models, get_experiment
from evalforge_api.modules.prompts.models import PromptVersion
from evalforge_api.modules.providers.models import ModelConfiguration
from evalforge_engine.reporting import Report, ReportConfig, ReportModel, render_report
from evalforge_engine.scoring import MetricRecord, MetricSummary, PerformanceSummary, ResultRecord, summarize_by_model

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


def snapshot(config: ModelConfiguration) -> dict[str, Any]:
    """Ce qui sera réellement appelé, figé dans le run. Jamais la clé d'API."""
    return {
        "id": str(config.id),
        "name": config.name,
        "provider_kind": config.provider_kind,
        "base_url": config.base_url,
        "model_name": config.model_name,
        "parameters": config.parameters,
        "pricing": config.pricing,
    }


@router.post("/experiments/{experiment_id}/runs", status_code=202, response_model=RunCreated)
async def create_run(
    experiment_id: uuid.UUID, body: RunCreate, request: Request, workspace_id: EditorDep, session: SessionDep
) -> RunCreated:
    experiment = await get_experiment(session, workspace_id, experiment_id)
    version = await get_dataset_version(session, workspace_id, body.dataset_version_id)
    dataset = await session.get(Dataset, version.dataset_id)
    if dataset is None or dataset.project_id != experiment.project_id:
        raise HTTPException(422, "Ce jeu de données n'appartient pas au projet de l'expérience")
    prompt = await session.get(PromptVersion, experiment.prompt_version_id)
    models = await experiment_models(session, experiment.id)
    wanted = [m.model_configuration_id for m in models] + [experiment.judge_model_configuration_id]
    configs = {
        config.id: config
        for config in await session.scalars(select(ModelConfiguration).where(ModelConfiguration.id.in_(wanted)))
    }
    judge = configs.get(experiment.judge_model_configuration_id) if experiment.judge_model_configuration_id else None

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
            "models": [snapshot(configs[m.model_configuration_id]) for m in models],
            "metrics": experiment.metrics,
            "judge": snapshot(judge) if judge else None,
        },
        total_items=version.case_count * len(models),
        completed_items=0,
    )
    session.add(run)
    await session.commit()
    request.app.state.scheduler.schedule(run.id)
    return RunCreated(run_id=run.id, status=run.status, created_at=run.created_at)


class RunSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    status: str
    dataset_version_id: uuid.UUID
    total_items: int
    completed_items: int
    created_at: datetime
    finished_at: datetime | None


@router.get("/experiments/{experiment_id}/runs", response_model=list[RunSummary])
async def list_runs(experiment_id: uuid.UUID, workspace_id: WorkspaceDep, session: SessionDep) -> list[EvaluationRun]:
    await get_experiment(session, workspace_id, experiment_id)
    rows = await session.scalars(
        select(EvaluationRun)
        .where(EvaluationRun.experiment_id == experiment_id)
        .order_by(EvaluationRun.created_at.desc())
        .limit(50)
    )
    return list(rows)


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


async def _records(session: AsyncSession, run: EvaluationRun) -> list[ResultRecord]:
    """Toutes les réponses du run, dans l'ordre des cas puis des modèles, sous la forme commune du moteur."""
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
    metrics = await _metrics_by_result(session, [row.EvaluationResult.id for row in rows])
    return [
        ResultRecord(
            case_id=external_id,
            model=names.get(result.model_configuration_id, "?"),
            status=result.status,
            output=result.actual_output,
            error=result.error,
            latency_ms=result.latency_ms,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            cost_usd=result.cost_usd,
            metrics=[
                MetricRecord(name=m.name, score=m.score, passed=m.passed, reason=m.reason) for m in metrics[result.id]
            ],
        )
        for result, external_id, _ in rows
    ]


@router.get("/runs/{run_id}/metrics", response_model=RunMetrics)
async def run_metrics(run_id: uuid.UUID, workspace_id: WorkspaceDep, session: SessionDep) -> RunMetrics:
    """Agrégats par modèle : qualité (par métrique), puis performance, tokens et coût, sans les mélanger."""
    run = await get_run(session, workspace_id, run_id)
    names = _model_names(run)
    ids = {name: model_id for model_id, name in names.items()}  # noms uniques dans un projet
    reports = summarize_by_model(list(names.values()), run.configuration["metrics"], await _records(session, run))
    return RunMetrics(
        run_id=run.id,
        status=run.status,
        models=[
            ModelSummary(
                model_configuration_id=ids[r.model], model=r.model, performance=r.performance, metrics=r.metrics
            )
            for r in reports
        ],
    )


@router.get("/runs/{run_id}/report", response_class=HTMLResponse)
async def run_report(run_id: uuid.UUID, workspace_id: WorkspaceDep, session: SessionDep) -> HTMLResponse:
    """Rapport HTML autonome : à archiver, joindre à une revue, imprimer."""
    run = await get_run(session, workspace_id, run_id)
    experiment = await session.get(Experiment, run.experiment_id)
    config = run.configuration
    records = await _records(session, run)
    names = list(_model_names(run).values())
    judge = config.get("judge")
    report = Report(
        title=f"Run d'évaluation : {experiment.name if experiment else run.id}",
        generated_at=utcnow(),
        status=run.status,
        config=ReportConfig(
            prompt_name=config["prompt"]["name"],
            prompt_version=config["prompt"]["version"],
            template=config["prompt"]["template"],
            system=config["prompt"]["system"],
            dataset_label=f"version {config['dataset_version']['version']}",
            dataset_checksum=config["dataset_version"]["checksum"],
            models=[
                ReportModel(name=m["name"], model_name=m["model_name"], provider=m["provider_kind"])
                for m in config["models"]
            ],
            metrics=config["metrics"],
            judge=f"{judge['name']} ({judge['model_name']})" if judge else None,
            execution=run.execution,
        ),
        models=summarize_by_model(names, config["metrics"], records),
        results=records,
    )
    return HTMLResponse(
        render_report(report),
        headers={"Content-Disposition": f'inline; filename="evalforge-run-{run.id}.html"'},
    )
