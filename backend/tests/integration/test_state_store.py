"""Integration tests for the Table Storage state store — real Azurite, no SDK mocks.

Requires Azurite on the standard ports (`make azurite`); fails loudly when it is
down (state-store spec REQ-5.4 — no skip logic).
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from azure.core.exceptions import ResourceNotFoundError

from app.config import Settings
from app.state_store import (
    ALL_TABLES,
    ENABLED_PROP,
    ENABLED_ROW,
    WORKER_STATE_PARTITION,
    WORKER_STATE_TABLE,
    Heartbeat,
    HeartbeatStatus,
    StateStore,
    state_store_from_settings,
)


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
    settings = Settings()  # defaults: connection_string → Azurite
    store = state_store_from_settings(settings)
    assert set(ALL_TABLES) <= _listed_tables(service)
    # Sanity: factory-built store is usable. The value is not asserted — the
    # unprefixed WorkerState row is shared mutable dev state (a killed host-test
    # run can leave enabled=True behind).
    assert isinstance(store.read_enabled(), bool)


def test_enabled_defaults_to_off_when_row_missing(store):
    assert store.read_enabled() is False


def test_set_enabled_roundtrip(store):
    store.set_enabled(True)
    assert store.read_enabled() is True
    store.set_enabled(False)
    assert store.read_enabled() is False


def test_read_enabled_non_bool_property_raises(service, prefix, store):
    # Only set_enabled(bool) legitimately writes this row; a foreign write can
    # store e.g. the string "false", which truthiness would read as ON. Corrupt
    # data must fail loud, never silently run the pipeline (worker-controls REQ-5).
    service.get_table_client(prefix + WORKER_STATE_TABLE).upsert_entity(
        {
            "PartitionKey": WORKER_STATE_PARTITION,
            "RowKey": ENABLED_ROW,
            ENABLED_PROP: "false",
        }
    )
    with pytest.raises(TypeError, match="str"):
        store.read_enabled()


def test_read_enabled_missing_table_raises(service, prefix):
    unprovisioned = StateStore(service, table_prefix=prefix)  # no ensure_tables()
    with pytest.raises(ResourceNotFoundError):
        unprovisioned.read_enabled()


def test_shared_store_is_safe_under_concurrent_use(store):
    # The cached process-wide store (worker-controls REQ-6) is shared between
    # the timer thread and FastAPI's threadpool, and azure-core's sync transport
    # disclaims thread safety (one requests.Session per client) — StateStore
    # serializes its ops with a lock. A race cannot fail deterministically, so
    # this smoke documents the contract and catches gross breakage; the lock is
    # the structural guarantee (see the worker-controls spec Bugfix log).
    def hammer(worker_index: int) -> int:
        for n in range(25):
            store.set_enabled(n % 2 == 0)
            assert isinstance(store.read_enabled(), bool)
            store.write_heartbeat(Heartbeat(at=datetime.now(UTC), status=HeartbeatStatus.RAN))
            assert store.read_heartbeat() is not None
        return worker_index

    with ThreadPoolExecutor(max_workers=8) as executor:
        assert sorted(executor.map(hammer, range(8))) == list(range(8))


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
