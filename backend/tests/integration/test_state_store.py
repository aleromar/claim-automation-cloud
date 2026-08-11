"""Integration tests for the Table Storage state store — real Azurite, no SDK mocks.

Requires Azurite on the standard ports (`make azurite`); fails loudly when it is
down (state-store spec REQ-5.4 — no skip logic).
"""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from azure.core.exceptions import ResourceNotFoundError
from azure.data.tables import TableServiceClient

from app.config import Settings
from app.state_store import (
    ALL_TABLES,
    Heartbeat,
    HeartbeatStatus,
    StateStore,
    state_store_from_settings,
)

AZURITE_CONNECTION_STRING = "UseDevelopmentStorage=true"


@pytest.fixture
def service():
    client = TableServiceClient.from_connection_string(AZURITE_CONNECTION_STRING)
    yield client
    client.close()


@pytest.fixture
def prefix() -> str:
    # Letter-first: table names must match ^[A-Za-z][A-Za-z0-9]{2,62}$.
    return f"t{uuid4().hex[:8]}"


@pytest.fixture
def store(service, prefix):
    s = StateStore(service, table_prefix=prefix)
    s.ensure_tables()
    yield s
    for name in ALL_TABLES:
        service.delete_table(prefix + name)


def _listed_tables(service) -> set[str]:
    return {t.name for t in service.list_tables()}


def test_ensure_tables_creates_all_five(service, prefix, store):
    assert {prefix + name for name in ALL_TABLES} <= _listed_tables(service)


def test_ensure_tables_is_idempotent(service, prefix, store):
    store.ensure_tables()  # second run (fixture already ran it once) must not raise
    assert {prefix + name for name in ALL_TABLES} <= _listed_tables(service)


def test_factory_ensures_tables(service):
    settings = Settings(_env_file=None)  # defaults: connection_string → Azurite
    store = state_store_from_settings(settings)
    assert set(ALL_TABLES) <= _listed_tables(service)
    assert store.read_enabled() is False  # sanity: factory-built store is usable


def test_enabled_defaults_to_off_when_row_missing(store):
    assert store.read_enabled() is False


def test_set_enabled_roundtrip(store):
    store.set_enabled(True)
    assert store.read_enabled() is True
    store.set_enabled(False)
    assert store.read_enabled() is False


def test_read_enabled_missing_table_raises(service, prefix):
    unprovisioned = StateStore(service, table_prefix=prefix)  # no ensure_tables()
    with pytest.raises(ResourceNotFoundError):
        unprovisioned.read_enabled()


def test_heartbeat_missing_reads_none(store):
    assert store.read_heartbeat() is None


def test_heartbeat_write_read_roundtrip(store):
    # Whole milliseconds: Edm.DateTime does not promise sub-ms precision.
    at = datetime.now(UTC).replace(microsecond=123000)
    written = Heartbeat(at=at, status=HeartbeatStatus.RAN)
    store.write_heartbeat(written)
    got = store.read_heartbeat()
    assert got is not None
    assert got.status == HeartbeatStatus.RAN
    assert got.at == at
    assert got.at.tzinfo is not None
