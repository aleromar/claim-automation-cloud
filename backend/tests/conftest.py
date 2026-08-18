"""Shared pytest fixtures for the backend."""

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app
from app.secret_store import SESSION_SIGNING_KEY, FileSecretStore, get_store
from app.security import mint_session_jwt
from app.state_store import Heartbeat, TrelloConfig, get_state_store

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
