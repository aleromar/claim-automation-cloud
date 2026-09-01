"""Unit-scope fixtures for the telemetry tests (otel-observability Tasks 1/3/5).

The OTel global TracerProvider can be set exactly once per process, so all
telemetry tests share one session-scoped install wired to in-memory exporters
(the setup test seam — no sockets, structure.md). NOT autouse: modules that
don't request `otel` never trigger the install.
"""

import os
from types import SimpleNamespace

import pytest


@pytest.fixture(scope="session")
def otel():
    # Force, not setdefault: the service-name assertion must not depend on
    # whatever WEBSITE_SITE_NAME the shell happens to export (Gate 3).
    os.environ["WEBSITE_SITE_NAME"] = "unit-test-site"
    from opentelemetry.sdk._logs.export import InMemoryLogRecordExporter
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    from app.main import app
    from app.observability import instrument_fastapi, setup_telemetry

    span_exporter = InMemorySpanExporter()
    log_exporter = InMemoryLogRecordExporter()
    assert setup_telemetry(span_exporter=span_exporter, log_exporter=log_exporter) is True
    instrument_fastapi(app)
    # Test-only: earlier test modules may already have served requests, which
    # caches Starlette's middleware stack — rebuild so instrumentation applies
    # mid-session. (Production instruments before the first request.)
    app.middleware_stack = app.build_middleware_stack()
    return SimpleNamespace(spans=span_exporter, logs=log_exporter)


@pytest.fixture
def otel_clean(otel):
    """Per-test view: exporters cleared on entry."""
    otel.spans.clear()
    otel.logs.clear()
    return otel
