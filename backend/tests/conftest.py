"""Shared pytest fixtures for the backend."""

import os

# Hermeticity (otel Gate 3 H1): an ambient connection string would make
# test_function_app's import install REAL Azure exporters at collection time
# — before any fixture can intervene — and ship unit-test telemetry to a live
# App Insights. Module-level on purpose: conftest imports precede test-module
# imports.
os.environ.pop("APPLICATIONINSIGHTS_CONNECTION_STRING", None)

import pytest
from fastapi.testclient import TestClient

from core.config import get_settings
from app.main import app
from core.secret_store import SESSION_SIGNING_KEY, FileSecretStore, get_store
from app.security import mint_session_jwt
from core.state_store import (
    ClaimRecord,
    ErrorRun,
    Heartbeat,
    HistoryTotals,
    TrelloConfig,
    get_state_store,
)

SIGNING_KEY = "k" * 32
OPERATOR = "operator@example.com"


class FakeStateStore:
    """The StateStore accessors the route tests consume; records writes."""

    def __init__(
        self,
        enabled: bool = False,
        heartbeat: Heartbeat | None = None,
        trello: TrelloConfig | None = None,
    ) -> None:
        self.enabled = enabled
        self.heartbeat = heartbeat
        self.trello = trello
        self.set_calls: list[bool] = []
        self.write_calls: list[TrelloConfig] = []
        # metrics-dashboard REQ-1/7: seeded by tests, returned verbatim.
        self.claims: list[ClaimRecord] = []
        self.totals = HistoryTotals(emails_processed=0, emails_failed=0, failed_runs=0)
        self.error_runs: list[ErrorRun] = []

    def read_enabled(self) -> bool:
        return self.enabled

    def set_enabled(self, enabled: bool) -> None:
        self.set_calls.append(enabled)
        self.enabled = enabled

    def read_heartbeat(self) -> Heartbeat | None:
        return self.heartbeat

    def write_heartbeat(self, heartbeat: Heartbeat) -> None:
        self.heartbeat = heartbeat

    def read_trello_config(self) -> TrelloConfig | None:
        return self.trello

    def write_trello_config(self, config: TrelloConfig) -> None:
        self.write_calls.append(config)
        self.trello = config

    def list_claims(self) -> list[ClaimRecord]:
        return self.claims

    def history_totals(self) -> HistoryTotals:
        return self.totals

    def list_error_runs(self) -> list[ErrorRun]:
        return self.error_runs


@pytest.fixture
def client() -> TestClient:
    """In-process HTTP client against the FastAPI app (no real sockets)."""
    return TestClient(app)


@pytest.fixture
def fake_store() -> FakeStateStore:
    return FakeStateStore()


@pytest.fixture
def secrets(secret_env) -> FileSecretStore:
    """File store seeded with the session signing key — enough for the JWT guard."""
    store = FileSecretStore(secret_env)
    store.set(SESSION_SIGNING_KEY, SIGNING_KEY)
    return store


@pytest.fixture
def auth() -> dict[str, str]:
    token = mint_session_jwt(OPERATOR, SIGNING_KEY, ttl_hours=8)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def secret_env(tmp_path, monkeypatch):
    """Point settings at a fresh (unseeded) file secret store; reset the
    process-wide caches (settings, state store AND secret store — a store
    cached under one test's env must not leak into another; PR #15 review M1)."""
    path = tmp_path / "secrets.json"
    monkeypatch.setenv("SECRET_STORE_BACKEND", "file")
    monkeypatch.setenv("SECRET_STORE_FILE_PATH", str(path))
    monkeypatch.setenv("OPERATOR_EMAIL", OPERATOR)
    get_settings.cache_clear()
    get_state_store.cache_clear()
    get_store.cache_clear()
    yield path
    get_settings.cache_clear()
    get_state_store.cache_clear()
    get_store.cache_clear()
