"""Shared pytest fixtures for the backend."""

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client() -> TestClient:
    """In-process HTTP client against the FastAPI app (no real sockets)."""
    return TestClient(app)
