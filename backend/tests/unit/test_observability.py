"""Telemetry package tests (otel-observability REQ-1/4/5/6/7 — Tasks 1 and 5).

Session-global provider via the `otel` fixture (see conftest). Exporters are
in-memory with synchronous processors — no sockets, no flush choreography.
"""

import logging
import threading
import time

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.observability import flush_telemetry, instrument_fastapi, setup_telemetry
from app.observability import setup as obs_setup

MARKER = "otel-unit-marker"


# --- REQ-6.3 / inertness: paths that must not install (never touch the global) ---


def test_setup_without_connection_string_is_inert(monkeypatch):
    monkeypatch.setattr(obs_setup, "_installed", False)
    monkeypatch.delenv("APPLICATIONINSIGHTS_CONNECTION_STRING", raising=False)
    assert setup_telemetry() is False


def test_setup_never_raises_on_malformed_connection_string(monkeypatch):
    monkeypatch.setattr(obs_setup, "_installed", False)
    monkeypatch.setenv("APPLICATIONINSIGHTS_CONNECTION_STRING", "garbage-not-a-connstring")
    assert setup_telemetry() is False  # warning logged, app serves untelemetered


# --- P12: single install under concurrency ---


def test_setup_concurrent_calls_install_exactly_once(monkeypatch):
    calls: list[int] = []

    def slow_install(*args) -> None:
        calls.append(1)
        time.sleep(0.05)

    monkeypatch.setattr(obs_setup, "_installed", False)
    monkeypatch.setattr(obs_setup, "_install", slow_install)
    monkeypatch.setenv("APPLICATIONINSIGHTS_CONNECTION_STRING", "InstrumentationKey=fake")
    threads = [threading.Thread(target=setup_telemetry) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(calls) == 1


# --- REQ-4.1: exactly one bridge, idempotent re-setup ---


def test_repeat_setup_keeps_single_logging_handler(otel):
    from opentelemetry.sdk._logs import LoggingHandler

    assert setup_telemetry() is True  # second call: no-op
    handlers = [h for h in logging.getLogger().handlers if isinstance(h, LoggingHandler)]
    assert len(handlers) == 1


# --- REQ-1.1: route-named SERVER span; /api/health excluded ---


def test_route_named_server_span_emitted(otel_clean, client):
    client.get("/api/metrics")  # 401 is fine — the span carries the route name
    names = [s.name for s in otel_clean.spans.get_finished_spans() if s.kind.name == "SERVER"]
    assert "GET /api/metrics" in names


def test_health_route_excluded_from_spans(otel_clean, client):
    client.get("/api/health")
    assert otel_clean.spans.get_finished_spans() == ()


# --- REQ-5.2: resource carries service.name from WEBSITE_SITE_NAME ---


def test_service_name_from_website_site_name(otel_clean, client):
    client.get("/api/metrics")
    span = otel_clean.spans.get_finished_spans()[0]
    assert span.resource.attributes["service.name"] == "unit-test-site"


# --- REQ-4.2: threadpool (plain-def) route logs carry the request's trace context ---


def test_threadpool_route_log_carries_request_trace_context(otel_clean):
    app = FastAPI()

    @app.get("/boom")
    def boom():  # plain def → anyio threadpool, the path the host used to drop
        logging.getLogger("app.unit").warning("%s threadpool", MARKER)
        return {"ok": True}

    instrument_fastapi(app)
    TestClient(app).get("/boom")
    server_spans = [s for s in otel_clean.spans.get_finished_spans() if s.kind.name == "SERVER"]
    assert server_spans, "instrumentation produced no SERVER span"
    records = [
        d.log_record
        for d in otel_clean.logs.get_finished_logs()
        if MARKER in str(d.log_record.body)
    ]
    assert records, "threadpool log record was not exported"
    assert records[0].trace_id == server_spans[0].context.trace_id


# --- REQ-6.4: SDK/exporter internals never feed back into the OTel handler ---


def test_internal_loggers_excluded_from_export(otel_clean):
    logging.getLogger("opentelemetry.sdk.whatever").warning("%s internal", MARKER)
    logging.getLogger("azure.monitor.opentelemetry.exporter.x").warning("%s internal", MARKER)
    # azure.core pipeline policies log the EXPORTER'S OWN ingestion POSTs at
    # INFO — captured, they feed back into the export queue: a self-sustaining
    # telemetry loop (~54k rows/4h observed live, and the probe's force_flush
    # hang: EXPORT_ALL can never drain a queue that export itself refills).
    logging.getLogger("azure.core.pipeline.policies._universal").warning("%s internal", MARKER)
    logging.getLogger("azure.core.pipeline.policies.http_logging_policy").warning(
        "%s internal", MARKER
    )
    logging.getLogger("app.unit").warning("%s external", MARKER)
    bodies = [str(d.log_record.body) for d in otel_clean.logs.get_finished_logs()]
    assert [b for b in bodies if MARKER in b] == [f"{MARKER} external"]


# --- REQ-7.2: GenAI content capture stays at safe defaults ---


def test_genai_content_env_vars_unset(otel):
    import os

    assert "OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT" not in os.environ
    assert "AZURE_TRACING_GEN_AI_CONTENT_RECORDING_ENABLED" not in os.environ


# --- REQ-7.1: the provider we installed IS the global one (the 5a2 seam) ---


def test_global_tracer_provider_is_ours(otel):
    from opentelemetry import trace

    assert trace.get_tracer_provider() is obs_setup._tracer_provider


# --- NFR: sampling is explicitly ALWAYS_ON (100%) ---


def test_sampler_is_always_on(otel):
    from opentelemetry.sdk.trace.sampling import ALWAYS_ON

    assert obs_setup._tracer_provider.sampler is ALWAYS_ON


# --- REQ-6.1: flush is bounded and swallows failures (probe 0d: SDK ignores its timeout) ---


class _HangingProvider:
    def force_flush(self, timeout_millis: int = 30000) -> bool:
        time.sleep(60)
        return True


class _RaisingProvider:
    def force_flush(self, timeout_millis: int = 30000) -> bool:
        raise RuntimeError("exporter went away")


@pytest.mark.parametrize("provider", [_HangingProvider(), _RaisingProvider()])
def test_flush_bounded_and_never_raises(monkeypatch, provider):
    monkeypatch.setattr(obs_setup, "_tracer_provider", provider)
    monkeypatch.setattr(obs_setup, "_logger_provider", provider)
    start = time.perf_counter()
    flush_telemetry(timeout_millis=300)  # must not raise
    assert time.perf_counter() - start < 2.0


def test_flush_concurrent_calls_serialized(monkeypatch):
    """P12 guard: force_flush thread-safety is unproven — concurrent
    flush_telemetry calls (timer + any future path) must serialize."""
    state = {"active": 0, "max_active": 0}
    gate = threading.Lock()

    class TrackingProvider:
        def force_flush(self, timeout_millis: int = 30000) -> bool:
            with gate:
                state["active"] += 1
                state["max_active"] = max(state["max_active"], state["active"])
            time.sleep(0.05)
            with gate:
                state["active"] -= 1
            return True

    monkeypatch.setattr(obs_setup, "_tracer_provider", TrackingProvider())
    monkeypatch.setattr(obs_setup, "_logger_provider", None)
    # Fresh lock: the bounded-flush test above deliberately abandons a daemon
    # still holding the module lock (that IS the abandonment contract).
    monkeypatch.setattr(obs_setup, "_flush_lock", threading.Lock())
    threads = [
        threading.Thread(target=flush_telemetry, kwargs={"timeout_millis": 1000}) for _ in range(4)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert state["max_active"] == 1


# --- PII stance: the httpx request hook strips query strings from URL attrs.
# (Unit-tests the hook directly: the instrumentor patches the real
# HTTPTransport, which MockTransport bypasses — the wired end-to-end path is
# verified live at Task 7, and was observed in the Task 0 probe.)


def test_httpx_request_hook_strips_query():
    class FakeSpan:
        def __init__(self) -> None:
            self.attrs: dict[str, str] = {}

        def set_attribute(self, key: str, value: str) -> None:
            self.attrs[key] = value

    class FakeRequest:
        url = httpx.URL("https://api.example.test/claims?q=subject-terms&id=2024/7")

    span = FakeSpan()
    obs_setup._httpx_request_hook(span, FakeRequest())
    assert span.attrs["http.url"] == "https://api.example.test/claims"
    assert span.attrs["url.full"] == "https://api.example.test/claims"
