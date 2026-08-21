"""Deployment REQ-2: KeyVaultSecretStore — Key Vault behind the SecretStore protocol.

Azure SDK client is mocked (no network); DefaultAzureCredential is never constructed
in tests.
"""

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock, patch

import pytest
from azure.core.exceptions import ResourceNotFoundError
from pydantic import ValidationError

from core.config import Settings
from core.secret_store import KeyVaultSecretStore, create_secret_store

VAULT_URI = "https://kv-claim-test.vault.azure.net/"


@pytest.fixture
def client():
    return MagicMock()


@pytest.fixture
def store(client):
    return KeyVaultSecretStore(VAULT_URI, client=client)


def test_get_returns_secret_value(store, client):
    client.get_secret.return_value = MagicMock(value="tok-123")
    assert store.get("gmail-refresh-token") == "tok-123"
    client.get_secret.assert_called_once_with("gmail-refresh-token")


def test_get_missing_returns_none(store, client):
    """404 maps to None so require_secret raises uniformly across backends (REQ-2.2)."""
    client.get_secret.side_effect = ResourceNotFoundError("not found")
    assert store.get("gmail-refresh-token") is None


def test_set_creates_or_updates_secret(store, client):
    store.set("gmail-refresh-token", "tok-456")
    client.set_secret.assert_called_once_with("gmail-refresh-token", "tok-456")


def test_ops_are_serialized_across_threads(store, client):
    # get_store() is a process-wide singleton shared by FastAPI's threadpool,
    # and azure-core's sync RequestsTransport (one requests.Session per client)
    # disclaims thread safety — the store must serialize its ops, the same
    # stance StateStore takes.
    in_flight = 0
    seen = []
    meter = threading.Lock()

    def slow_get(name):
        nonlocal in_flight
        with meter:
            in_flight += 1
            seen.append(in_flight)
        time.sleep(0.005)
        with meter:
            in_flight -= 1
        return MagicMock(value="v")

    client.get_secret.side_effect = slow_get
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda _: store.get("session-signing-key"), range(16)))
    assert max(seen) == 1  # never two threads inside the client at once


def test_default_credential_used_when_no_client():
    """Managed identity in Azure, az CLI locally — via DefaultAzureCredential (REQ-2.1)."""
    with (
        patch("core.secret_store.SecretClient") as sc,
        patch("core.secret_store.DefaultAzureCredential") as cred,
    ):
        KeyVaultSecretStore(VAULT_URI)
    sc.assert_called_once_with(vault_url=VAULT_URI, credential=cred.return_value)


def test_factory_selects_keyvault_backend():
    settings = Settings(secret_store_backend="keyvault", key_vault_uri=VAULT_URI)
    with patch("core.secret_store.SecretClient"), patch("core.secret_store.DefaultAzureCredential"):
        store = create_secret_store(settings)
    assert isinstance(store, KeyVaultSecretStore)


def test_keyvault_without_uri_rejected_at_settings_load():
    """keyvault + no URI is unconstructable: fails at settings load, not store creation."""
    with pytest.raises(ValidationError, match="KEY_VAULT_URI"):
        Settings(secret_store_backend="keyvault", key_vault_uri=None)
