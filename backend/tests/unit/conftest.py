"""Unit-scope fixtures for the telemetry tests (otel-observability Tasks 1/3/5).

The OTel global TracerProvider can be set exactly once per process, so all
telemetry tests share one session-scoped install wired to in-memory exporters
(the setup test seam — no sockets, structure.md). NOT autouse: modules that
don't request `otel` never trigger the install.
"""

import os
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

# --- llm-extraction REQ-6.4 Tier 2: pytest-recording (VCR) configuration ---
# Public repo: cassettes hold anonymized fixture text only and no infra
# identifiers. The Foundry host is rewritten in the URI AND filtered from the
# request `host` header (Task 0e: the header kept the real host); Azure/APIM
# response headers are dropped. Body matching is mandatory: all five cases POST
# the same path, so without it an edited prompt would replay a stale answer.
CASSETTE_HOST = "foundry.example"
CASSETTES_DIR = Path(__file__).parent.parent / "cassettes"
SCRUBBED_RESPONSE_HEADER_PREFIXES = ("x-ms-", "apim-", "azureml-", "azureai-", "x-ratelimit-")
# Credential-chain probes must never be recorded or replayed.
IGNORED_HOSTS = ("169.254.169.254", "login.microsoftonline.com")


def _scrub_request(request):
    request.uri = re.sub(r"^https://[^/]+/", f"https://{CASSETTE_HOST}/", request.uri, count=1)
    return request


def _scrub_response(response):
    headers = response.get("headers", {})
    for name in list(headers):
        if name.lower().startswith(SCRUBBED_RESPONSE_HEADER_PREFIXES):
            del headers[name]
    return response


@pytest.fixture(scope="module")
def vcr_config():
    return {
        "match_on": ["method", "scheme", "host", "port", "path", "query", "body"],
        "filter_headers": ["authorization", "api-key", "host"],
        "before_record_request": _scrub_request,
        "before_record_response": _scrub_response,
        "decode_compressed_response": True,
        "ignore_hosts": list(IGNORED_HOSTS),
    }


@pytest.fixture(scope="module")
def vcr_cassette_dir():
    return str(CASSETTES_DIR)


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
