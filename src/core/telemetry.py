"""
Lightweight OpenTelemetry setup for ForgeOS.

Exports a tracer that writes spans to the console by default or to an
OTLP collector when ``OTEL_EXPORTER_OTLP_ENDPOINT`` is set.

Usage::

    from src.core.telemetry import get_tracer
    tracer = get_tracer()
    with tracer.start_as_current_span("my-operation") as span:
        span.set_attribute("agent_id", agent_id)
        ...
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

_tracer = None


class _NoopSpan:
    """Minimal stand-in when OpenTelemetry is unavailable."""

    def set_attribute(self, key, value):
        pass

    def set_status(self, *a, **kw):
        pass

    def record_exception(self, exc):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


class _NoopTracer:
    def start_as_current_span(self, name, **kw):
        return _NoopSpan()


def init_telemetry(service_name: str = "forgeos") -> None:
    """Initialize OpenTelemetry tracing (call once at boot)."""
    global _tracer
    try:
        from opentelemetry import trace
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.resources import Resource

        resource = Resource.create({"service.name": service_name})
        provider = TracerProvider(resource=resource)

        otlp_endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "")
        if otlp_endpoint:
            from opentelemetry.sdk.trace.export import BatchSpanProcessor
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

            exporter = OTLPSpanExporter(endpoint=otlp_endpoint)
            provider.add_span_processor(BatchSpanProcessor(exporter))
            logger.info("OpenTelemetry: OTLP exporter → %s", otlp_endpoint)
        else:
            from opentelemetry.sdk.trace.export import SimpleSpanProcessor, ConsoleSpanExporter

            provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))
            logger.info("OpenTelemetry: console exporter (set OTEL_EXPORTER_OTLP_ENDPOINT for OTLP)")

        trace.set_tracer_provider(provider)
        _tracer = trace.get_tracer(service_name)

    except ImportError:
        logger.debug("OpenTelemetry SDK not installed — tracing disabled")
        _tracer = _NoopTracer()


def get_tracer():
    """Return the global tracer (noop if telemetry not initialized)."""
    global _tracer
    if _tracer is None:
        _tracer = _NoopTracer()
    return _tracer
