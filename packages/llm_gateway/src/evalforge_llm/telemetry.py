"""Traces OpenTelemetry : l'API seule ici (sans SDK, chaque span est un no-op), le SDK dans les services qui exportent.

Conventions sémantiques GenAI d'OpenTelemetry pour les appels aux modèles : `gen_ai.operation.name`,
`gen_ai.request.model`, `gen_ai.usage.input_tokens`… Ni prompt ni réponse dans les attributs : ils peuvent contenir
des données personnelles, et une trace se partage plus largement qu'une base.
"""

import os

from opentelemetry import trace

tracer = trace.get_tracer("evalforge")

_configured = False


def configure_telemetry(service_name: str) -> bool:
    """Active l'export des traces si OTEL_EXPORTER_OTLP_ENDPOINT est défini (OTLP/HTTP), ou en console si
    EVALFORGE_OTEL_CONSOLE=1. Sinon rien : les spans restent des no-op. Une fois par processus."""
    global _configured
    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
    console = os.environ.get("EVALFORGE_OTEL_CONSOLE") == "1"
    if _configured or not (endpoint or console):
        return False

    from opentelemetry.sdk.resources import SERVICE_NAME, Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter

    provider = TracerProvider(resource=Resource.create({SERVICE_NAME: service_name}))
    if endpoint:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    if console:
        provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
    trace.set_tracer_provider(provider)
    _configured = True
    return True


_capture = None


def capture_spans():
    """Pour les tests : un exportateur en mémoire, branché une fois sur le fournisseur global (créé au besoin)."""
    global _capture
    if _capture is not None:
        _capture.clear()
        return _capture
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    provider = trace.get_tracer_provider()
    if not isinstance(provider, TracerProvider):
        provider = TracerProvider()
        trace.set_tracer_provider(provider)
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    _capture = exporter
    return exporter
