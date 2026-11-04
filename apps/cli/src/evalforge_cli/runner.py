"""Exécution locale : les mêmes cibles, évaluateurs, agrégats et rapport que la plateforme."""

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx

from evalforge_cli.config import EvalConfig, ModelConfig
from evalforge_engine import WorkItem, evaluate_cases
from evalforge_engine.datasets import load_cases
from evalforge_engine.evaluators import JudgeModel, build_evaluators
from evalforge_engine.reporting import Report, ReportConfig, ReportModel
from evalforge_engine.scoring import MetricRecord, ModelReport, ResultRecord, cost_usd, summarize_by_model
from evalforge_engine.targets import llm_target
from evalforge_llm import OpenAICompatibleProvider
from evalforge_llm.network import SafeTransport, check_url

TransportFactory = Callable[[bool], httpx.AsyncBaseTransport]


def default_transport(allow_private: bool) -> httpx.AsyncBaseTransport:
    return SafeTransport(allow_private=allow_private)


@dataclass
class GateFailure:
    model: str
    metric: str
    required: float
    actual: float | None


@dataclass
class Evaluation:
    report: Report
    failures: list[GateFailure]


def _provider(model: ModelConfig, client: httpx.AsyncClient, config: EvalConfig) -> OpenAICompatibleProvider:
    check_url(str(model.base_url), allow_private=config.allow_private_urls)
    return OpenAICompatibleProvider(
        base_url=str(model.base_url),
        api_key=model.api_key(),
        client=client,
        timeout_seconds=config.execution.timeout_seconds,
    )


def check_gate(config: EvalConfig, models: list[ModelReport]) -> list[GateFailure]:
    """Une métrique sans aucune mesure ne passe pas la porte : ce qui n'est pas mesuré n'est pas prouvé."""
    failures = []
    for report in models:
        rates = {metric.name: metric.pass_rate for metric in report.metrics}
        for metric, required in config.thresholds.items():
            actual = rates.get(metric)
            if actual is None or actual < required:
                failures.append(GateFailure(report.model, metric, required, actual))
    return failures


async def run_evaluation(config: EvalConfig, transport_factory: TransportFactory | None = None) -> Evaluation:
    cases = load_cases(config.dataset)
    transport = (transport_factory or default_transport)(config.allow_private_urls)
    async with httpx.AsyncClient(transport=transport) as client:
        judge = (
            JudgeModel(_provider(config.judge, client, config), config.judge.model, config.judge.temperature)
            if config.judge
            else None
        )
        evaluators = build_evaluators(config.metrics, judge)
        items = []
        for model in config.models:
            target = llm_target(
                _provider(model, client, config),
                model=model.model,
                template=config.prompt.template or "",
                system=config.prompt.system,
                temperature=model.temperature,
                max_tokens=model.max_tokens,
            )
            items.extend(WorkItem((case.id, model.name), case, target) for case in cases)

        records = []
        pricing = {model.name: model.pricing for model in config.models}
        async for outcome in evaluate_cases(
            items,
            evaluators,
            max_concurrency=config.execution.max_concurrency,
            timeout_seconds=config.execution.timeout_seconds,
        ):
            case_id, model_name = outcome.key
            output, price = outcome.output, pricing[model_name]
            records.append(
                ResultRecord(
                    case_id=case_id,
                    model=model_name,
                    status=outcome.status.value,
                    output=output.text if output else None,
                    error=outcome.error,
                    latency_ms=output.latency_ms if output else None,
                    input_tokens=output.input_tokens if output else None,
                    output_tokens=output.output_tokens if output else None,
                    cost_usd=cost_usd(
                        output.input_tokens,
                        output.output_tokens,
                        price.input_per_million_usd,
                        price.output_per_million_usd,
                    )
                    if output and price
                    else None,
                    metrics=[
                        MetricRecord(name=m.name, score=m.score, passed=m.passed, reason=m.reason)
                        for m in outcome.metrics
                    ],
                )
            )

    order = {case.id: index for index, case in enumerate(cases)}
    model_order = {model.name: index for index, model in enumerate(config.models)}
    records.sort(key=lambda record: (order[record.case_id], model_order[record.model]))
    models = summarize_by_model([model.name for model in config.models], config.metrics, records)
    digest = hashlib.sha256(config.dataset.read_bytes()).hexdigest()
    report = Report(
        title=f"Évaluation locale : {config.dataset.name}",
        generated_at=datetime.now(UTC),
        status="completed",
        config=ReportConfig(
            prompt_name=config.prompt.name,
            template=config.prompt.template or "",
            system=config.prompt.system,
            dataset_label=f"{config.dataset.name} ({len(cases)} cas)",
            dataset_checksum=digest,
            models=[ReportModel(name=m.name, model_name=m.model, provider=str(m.base_url)) for m in config.models],
            metrics=config.metrics,
            judge=f"{config.judge.name} ({config.judge.model})" if config.judge else None,
            execution=config.execution.model_dump(),
        ),
        models=models,
        results=records,
    )
    return Evaluation(report=report, failures=check_gate(config, models))
