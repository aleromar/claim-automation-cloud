"""Shared pytest fixtures for the backend."""

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app
from app.state_store import get_state_store

SIGNING_KEY = "k" * 32
OPERATOR = "operator@example.com"


@pytest.fixture
def client() -> TestClient:
    """In-process HTTP client against the FastAPI app (no real sockets)."""
    return TestClient(app)


@pytest.fixture
def secret_env(tmp_path, monkeypatch):
    """Point settings at a fresh (unseeded) file secret store; reset the
    process-wide caches (settings AND state store — a store cached under one
    test's env must not leak into another; PR #15 review M1)."""
    path = tmp_path / "secrets.json"
    monkeypatch.setenv("SECRET_STORE_BACKEND", "file")
    monkeypatch.setenv("SECRET_STORE_FILE_PATH", str(path))
    monkeypatch.setenv("OPERATOR_EMAIL", OPERATOR)
    get_settings.cache_clear()
    get_state_store.cache_clear()
    yield path
    get_settings.cache_clear()
    get_state_store.cache_clear()
