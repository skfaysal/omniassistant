"""Distributed tracing with OpenTelemetry — shows the complete request
lifecycle as it hops between services.

An OpenTelemetry `TracerProvider` is installed with the service name, and the
FastAPI app is auto-instrumented so every request produces spans that
participate in W3C trace-context propagation across the agent-server →
mcp-server hop. Point `OTEL_EXPORTER_OTLP_ENDPOINT` at a collector in
production; for local development, set `MCP_OTEL_CONSOLE_EXPORT=true` to dump
spans to stdout.
"""

from __future__ import annotations

from opentelemetry import trace
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import ConsoleSpanExporter, SimpleSpanProcessor


def setup_tracing(app, service_name: str, console_export: bool = False) -> None:
    provider = TracerProvider(
        resource=Resource.create({"service.name": service_name})
    )
    if console_export:
        provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))
    trace.set_tracer_provider(provider)
    FastAPIInstrumentor.instrument_app(app, tracer_provider=provider)


def get_tracer(name: str) -> trace.Tracer:
    return trace.get_tracer(name)
