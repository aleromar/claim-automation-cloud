"""worker-controls REQ-1/2/3: /api/worker/{status,enabled,run}.

Fake in-memory store via dependency_overrides[get_state_store]; the JWT guard is
real (seeded file secret store + mint_session_jwt, the test_auth_routes pattern;
fixtures shared in tests/conftest.py).
"""

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.state_store import Heartbeat, HeartbeatStatus, get_state_store

STATUS_PATH = "/api/worker/status"
ENABLED_PATH = "/api/worker/enabled"
RUN_PATH = "/api/worker/run"


@pytest.fixture(autouse=True)
def store_override(fake_store):
    """Inject the fake store; overrides cleaned in teardown. Store-cache hygiene
    is systemic in tests/conftest.py `secret_env` (PR #15 review M1)."""
    app.dependency_overrides[get_state_store] = lambda: fake_store
    yield
    app.dependency_overrides.pop(get_state_store, None)


@pytest.fixture
def client(secrets) -> TestClient:
    return TestClient(app)


# --- auth guard (REQ-1.2, router-level) ---


@pytest.mark.parametrize(
    ("method", "path"),
    [("GET", STATUS_PATH), ("POST", ENABLED_PATH), ("POST", RUN_PATH)],
)
def test_worker_endpoints_require_auth(client, method, path):
    # No body on the POSTs on purpose: 401 (guard) must win over 422 (validation).
    assert client.request(method, path).status_code == 401


# --- status (REQ-1) ---


def test_status_returns_enabled_and_heartbeat(client, auth, fake_store):
    at = datetime(2026, 8, 15, 12, 30, 0, tzinfo=UTC)
    fake_store.enabled = True
    fake_store.heartbeat = Heartbeat(at=at, status=HeartbeatStatus.RAN)
    resp = client.get(STATUS_PATH, headers=auth)
    assert resp.status_code == 200
    body = resp.json()
    assert body["enabled"] is True
    assert body["heartbeat"]["status"] == HeartbeatStatus.RAN.value
    assert datetime.fromisoformat(body["heartbeat"]["at"]) == at


def test_status_heartbeat_carries_matched_count(client, auth, fake_store):
    # gmail-client REQ-4 / Gate 2 finding 3: the probe count must reach the
    # dashboard through the status response body, not just the stored model.
    fake_store.heartbeat = Heartbeat(
        at=datetime(2026, 8, 21, 12, 30, 0, tzinfo=UTC),
        status=HeartbeatStatus.RAN,
        matched=3,
    )
    body = client.get(STATUS_PATH, headers=auth).json()
    assert body["heartbeat"]["matched"] == 3


def test_status_heartbeat_null_when_no_run_yet(client, auth):
    resp = client.get(STATUS_PATH, headers=auth)
    assert resp.status_code == 200
    assert resp.json() == {"enabled": False, "heartbeat": None}


# --- toggle (REQ-2) ---


def test_set_enabled_on_then_status_reflects_it(client, auth, fake_store):
    resp = client.post(ENABLED_PATH, headers=auth, json={"enabled": True})
    assert resp.status_code == 200
    assert resp.json() == {"enabled": True}
    assert fake_store.set_calls == [True]
    assert client.get(STATUS_PATH, headers=auth).json()["enabled"] is True


def test_set_enabled_off_roundtrip(client, auth, fake_store):
    fake_store.enabled = True
    resp = client.post(ENABLED_PATH, headers=auth, json={"enabled": False})
    assert resp.status_code == 200
    assert resp.json() == {"enabled": False}
    assert fake_store.enabled is False


@pytest.mark.parametrize("body", [{}, {"enabled": "true"}, {"enabled": 1}])
def test_set_enabled_rejects_non_bool(client, auth, fake_store, body):
    # StrictBool: an ambiguous payload must not flip the worker (REQ-2.2).
    assert client.post(ENABLED_PATH, headers=auth, json=body).status_code == 422
    assert fake_store.set_calls == []


# --- process-now (REQ-3) ---


@pytest.fixture(autouse=True)
def fake_gmail(monkeypatch):
    """Process-now composes the full wake path, whose pipeline builds a
    GmailClient and preflights (REQ-6 2nd amendment: construction lives in
    pipeline.entry) — fake the seam so these route tests keep testing routing,
    not Gmail."""

    class HealthyFakeGmail:
        def __init__(self, settings, secret_store) -> None:
            pass

        def preflight(self) -> None:
            pass

        def list_unread_message_ids(self) -> list[str]:  # pragma: no cover
            return []

        def get_subject(self, message_id: str) -> str:  # pragma: no cover
            return ""

        def close(self) -> None:
            pass

    monkeypatch.setattr("pipeline.entry.GmailClient", HealthyFakeGmail)
    monkeypatch.setattr("pipeline.entry.get_settings", lambda: object())
    monkeypatch.setattr("pipeline.entry.get_store", lambda: object())


def test_run_now_disabled_returns_skipped_and_writes_heartbeat(
    client, auth, fake_store, monkeypatch
):
    pipeline_calls: list[str] = []
    monkeypatch.setattr("app.worker.run_pipeline", lambda: pipeline_calls.append("called"))
    resp = client.post(RUN_PATH, headers=auth)
    assert resp.status_code == 200
    assert resp.json() == {"outcome": HeartbeatStatus.SKIPPED_DISABLED.value}
    assert pipeline_calls == []  # gate honored: nothing ran
    assert fake_store.heartbeat is not None  # ...but the run report landed
    assert fake_store.heartbeat.status == HeartbeatStatus.SKIPPED_DISABLED


def test_run_now_enabled_returns_ran_and_writes_heartbeat(client, auth, fake_store, monkeypatch):
    pipeline_calls: list[str] = []
    monkeypatch.setattr("app.worker.run_pipeline", lambda: pipeline_calls.append("called"))
    fake_store.enabled = True
    resp = client.post(RUN_PATH, headers=auth)
    assert resp.status_code == 200
    assert resp.json() == {"outcome": HeartbeatStatus.RAN.value}
    assert pipeline_calls == ["called"]
    assert fake_store.heartbeat is not None
    assert fake_store.heartbeat.status == HeartbeatStatus.RAN


def test_run_now_pipeline_failure_writes_failed_heartbeat_and_500s(
    secrets, auth, fake_store, monkeypatch
):
    def failing_pipeline() -> None:
        raise RuntimeError("pipeline blew up")

    monkeypatch.setattr("app.worker.run_pipeline", failing_pipeline)
    fake_store.enabled = True
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.post(RUN_PATH, headers=auth)
    # Status only — never the body (gate ER-I1: unhandled exceptions bypass
    # CORSMiddleware and the Functions host may substitute its own 500 body).
    assert resp.status_code == 500
    assert fake_store.heartbeat is not None
    assert fake_store.heartbeat.status == HeartbeatStatus.FAILED
