"""Manual OTel wiring (otel-observability spec, D28).

Explicit trace + log pipelines: TracerProvider + LoggerProvider with the
Azure Monitor exporters, FastAPI/httpx instrumentors, azure-core tracing
plugin. Installed only when the App Insights connection string is present,
so uvicorn dev runs stay inert. Never raises (REQ-6.3): a failure at
function_app.py import time would fail indexing of every function.

Failure signalling: host.json suppresses host forwarding of user logs, so a
setup failure cannot reach App Insights — it goes to stderr (host console)
and the D27 dead-man check is the durable alarm (spec Task 7 residual ii).
"""

import logging
import os
import sys
import threading

from fastapi import FastAPI

logger = logging.getLogger(__name__)

_lock = threading.Lock()
# P12: force_flush thread-safety across concurrent callers is unproven — the
# flusher thread holds this lock, so an abandoned (timed-out) flush still
# serializes against the next one (guard test in test_observability.py).
_flush_lock = threading.Lock()
_installed = False
_tracer_provider = None
_logger_provider = None


class _DropTelemetryInternals(logging.Filter):
    """Exporter/SDK internals must not feed back into the OTel handler (REQ-6.4)."""

    def filter(self, record: logging.LogRecord) -> bool:
        return not record.name.startswith(("opentelemetry", "azure.monitor.opentelemetry"))


def setup_telemetry(span_exporter=None, log_exporter=None) -> bool:
    """Install both pipelines exactly once. Returns True when telemetry is live.

    `span_exporter`/`log_exporter` are the unit-test seam (structure.md):
    both must be given together; injected exporters get synchronous
    processors and skip the connection-string requirement.
    """
    global _installed
    with _lock:
        if _installed:
            return True
        if (span_exporter is None) != (log_exporter is None):
            raise ValueError("span_exporter and log_exporter must be injected together")
        conn = None
        if span_exporter is None:
            conn = os.environ.get("APPLICATIONINSIGHTS_CONNECTION_STRING")
            if not conn:
                logger.warning(
                    "telemetry uninstalled: APPLICATIONINSIGHTS_CONNECTION_STRING absent"
                )
                return False
        try:
            _install(conn, span_exporter, log_exporter)
        except Exception:
            # stderr on purpose: with host user-log forwarding suppressed and
            # the OTel path dead, this is the only reachable channel.
            logger.warning("setup_telemetry failed; serving untelemetered", exc_info=True)
            print("TELEMETRY SETUP FAILED — serving untelemetered", file=sys.stderr)
            return False
        _installed = True
        return True


def _strip_query(url) -> str:
    """Query + fragment + userinfo stripped (PII stance): Gmail queries embed
    claim-subject terms; never undo the instrumentor's credential redaction."""
    return str(url.copy_with(query=None, fragment=None, username=None, password=None))


def _httpx_request_hook(span, request) -> None:
    try:
        stripped = _strip_query(request.url)
        # http.url = what the pinned instrumentation emits; url.full = hedge
        # for the newer semconv mode (not emitted by 0.64b0, harmless).
        span.set_attribute("http.url", stripped)
        span.set_attribute("url.full", stripped)
    except Exception:  # never let telemetry decoration break a request
        pass


def _install(conn, span_exporter, log_exporter) -> None:
    """Build everything first, mutate globals last (single commit block) —
    a failure mid-build must leave no half-wired handler or provider behind,
    or the retry-on-next-call would double-install (REQ-4.1)."""
    global _tracer_provider, _logger_provider
    from azure.core.settings import settings as azure_core_settings
    from azure.core.tracing.ext.opentelemetry_span import OpenTelemetrySpan
    from opentelemetry import trace
    from opentelemetry._logs import set_logger_provider
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
    from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
    from opentelemetry.sdk._logs.export import BatchLogRecordProcessor, SimpleLogRecordProcessor
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor
    from opentelemetry.sdk.trace.sampling import ALWAYS_ON

    resource = Resource.create(
        {"service.name": os.environ.get("WEBSITE_SITE_NAME", "claim-automation-local")}
    )

    if span_exporter is None:
        from azure.monitor.opentelemetry.exporter import (
            AzureMonitorLogExporter,
            AzureMonitorTraceExporter,
        )

        span_processor = BatchSpanProcessor(AzureMonitorTraceExporter(connection_string=conn))
        log_processor = BatchLogRecordProcessor(AzureMonitorLogExporter(connection_string=conn))
    else:
        span_processor = SimpleSpanProcessor(span_exporter)
        log_processor = SimpleLogRecordProcessor(log_exporter)

    # 100% sampling, explicit (NFR): traffic is a handful of events/day.
    tracer_provider = TracerProvider(resource=resource, sampler=ALWAYS_ON)
    tracer_provider.add_span_processor(span_processor)
    logger_provider = LoggerProvider(resource=resource)
    logger_provider.add_log_record_processor(log_processor)
    handler = LoggingHandler(level=logging.INFO, logger_provider=logger_provider)
    handler.addFilter(_DropTelemetryInternals())

    # Commit block — global mutations only from here on.
    HTTPXClientInstrumentor().instrument(request_hook=_httpx_request_hook)
    azure_core_settings.tracing_implementation = OpenTelemetrySpan
    trace.set_tracer_provider(tracer_provider)
    set_logger_provider(logger_provider)
    logging.getLogger().addHandler(handler)
    _tracer_provider = tracer_provider
    _logger_provider = logger_provider


def instrument_fastapi(app: FastAPI) -> None:
    """Route-named SERVER spans; /api/health excluded (probe noise). Must run
    after setup_telemetry — skipping is loud (C1: silent zero-span mode)."""
    if not _installed:
        logger.warning("instrument_fastapi skipped: telemetry is not installed")
        return
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        FastAPIInstrumentor.instrument_app(app, excluded_urls="/api/health")
    except Exception:
        logger.warning("instrument_fastapi failed; requests untraced", exc_info=True)


def flush_telemetry(timeout_millis: int = 5000) -> None:
    """Bounded, failure-swallowing flush of both providers (REQ-6.1).

    Probe 0d: the SDK's force_flush discards its timeout and can block
    unbounded in the Functions host — so the bound is EXTERNAL: one daemon
    thread flushes both providers (single join = single bound) and holds
    _flush_lock itself, so an abandoned flush still serializes the next one.
    Never raises: a flush problem must not fail a completed run.
    """

    def _flush_both() -> None:
        with _flush_lock:
            for provider in (_tracer_provider, _logger_provider):
                if provider is None:
                    continue
                try:
                    provider.force_flush(timeout_millis)
                except Exception:
                    logger.warning("telemetry flush failed", exc_info=True)

    try:
        t = threading.Thread(target=_flush_both, daemon=True)
        t.start()
        t.join(timeout_millis / 1000)
        if t.is_alive():
            logger.warning("telemetry flush abandoned after %sms", timeout_millis)
    except Exception:
        logger.warning("telemetry flush could not start", exc_info=True)
