"""REQ-5: SecretStore abstraction — file backend and factory."""

import pytest

from app.config import Settings
from app.secret_store import FileSecretStore, create_secret_store


@pytest.fixture
def store(tmp_path):
    return FileSecretStore(tmp_path / "secrets.json")


def test_set_get_roundtrip(store):
    store.set("gmail-refresh-token", "tok-123")
    assert store.get("gmail-refresh-token") == "tok-123"


def test_get_missing_returns_none(store):
    assert store.get("nope") is None


def test_persists_across_instances(tmp_path):
    path = tmp_path / "secrets.json"
    FileSecretStore(path).set("session-signing-key", "k" * 32)
    assert FileSecretStore(path).get("session-signing-key") == "k" * 32


def test_overwrite_replaces_value(store):
    store.set("google-client-secret", "old")
    store.set("google-client-secret", "new")
    assert store.get("google-client-secret") == "new"


def test_file_created_with_0600(store, tmp_path):
    store.set("a", "b")
    mode = (tmp_path / "secrets.json").stat().st_mode & 0o777
    assert mode == 0o600


def test_write_is_atomic_no_temp_leftovers(store, tmp_path):
    """Atomic replace (temp + rename): no stray temp files after a write."""
    store.set("a", "b")
    store.set("c", "d")
    assert [p.name for p in tmp_path.iterdir()] == ["secrets.json"]


def _settings(**overrides) -> Settings:
    return Settings(**overrides)


def test_factory_selects_file_backend(tmp_path):
    settings = _settings(
        secret_store_backend="file", secret_store_file_path=str(tmp_path / "s.json")
    )
    store = create_secret_store(settings)
    assert isinstance(store, FileSecretStore)


def test_factory_unknown_backend_rejected():
    with pytest.raises(ValueError):
        _settings(secret_store_backend="s3")
