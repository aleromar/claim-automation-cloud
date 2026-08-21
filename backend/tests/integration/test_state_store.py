"""Integration tests for the Table Storage state store — real Azurite, no SDK mocks.

Requires Azurite on the standard ports (`make azurite`); fails loudly when it is
down (state-store spec REQ-5.4 — no skip logic).
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from azure.core.exceptions import ResourceNotFoundError

from core.config import Settings
from core.state_store import (
    HEARTBEAT_HISTORY_PARTITION,
    RUN_LEASE_STALE_S,
    ClaimRecord,
    ALL_TABLES,
    ENABLED_PROP,
    ENABLED_ROW,
    HEARTBEAT_TABLE,
    WORKER_STATE_PARTITION,
    WORKER_STATE_TABLE,
    Heartbeat,
    HeartbeatStatus,
    StateStore,
    TrelloConfig,
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


def test_trello_config_missing_reads_none(store):
    # settings REQ-1.3: fresh install — no row is a normal state, not an error.
    assert store.read_trello_config() is None


def test_trello_config_write_read_roundtrip(store):
    written = TrelloConfig(board_id="g7vysmjD", list_id="68875e0d401d7613fcbbc092")
    store.write_trello_config(written)
    got = store.read_trello_config()
    assert got == written


def test_trello_config_empty_strings_round_trip(store):
    # IDs are authoritative-as-submitted including empty (settings REQ-2.3):
    # the real Table service must hand "" back, not drop the property — a
    # dropped property would KeyError in read_trello_config.
    store.write_trello_config(TrelloConfig(board_id="", list_id=""))
    assert store.read_trello_config() == TrelloConfig(board_id="", list_id="")


def test_trello_config_overwrite_replaces(store):
    store.write_trello_config(TrelloConfig(board_id="old", list_id="old"))
    store.write_trello_config(TrelloConfig(board_id="new", list_id="new"))
    assert store.read_trello_config() == TrelloConfig(board_id="new", list_id="new")


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


def test_heartbeat_matched_roundtrip(store):
    # gmail-client REQ-4: the probe count survives the real Table service.
    written = Heartbeat(at=datetime.now(UTC), status=HeartbeatStatus.RAN, matched=3)
    store.write_heartbeat(written)
    got = store.read_heartbeat()
    assert got is not None
    assert got.matched == 3


def test_heartbeat_row_without_matched_reads_none(service, prefix, store):
    # gmail-client REQ-4: rows written before 5b lack the property entirely —
    # they must read as matched=None, no migration.
    from core.state_store import (
        HEARTBEAT_AT_PROP,
        HEARTBEAT_PARTITION,
        HEARTBEAT_ROW,
        HEARTBEAT_STATUS_PROP,
        HEARTBEAT_TABLE,
    )

    service.get_table_client(prefix + HEARTBEAT_TABLE).upsert_entity(
        {
            "PartitionKey": HEARTBEAT_PARTITION,
            "RowKey": HEARTBEAT_ROW,
            HEARTBEAT_AT_PROP: datetime.now(UTC),
            HEARTBEAT_STATUS_PROP: HeartbeatStatus.RAN.value,
        }
    )
    got = store.read_heartbeat()
    assert got is not None
    assert got.matched is None


def test_heartbeat_skipped_no_access_roundtrip(store):
    store.write_heartbeat(Heartbeat(at=datetime.now(UTC), status=HeartbeatStatus.SKIPPED_NO_ACCESS))
    got = store.read_heartbeat()
    assert got is not None
    assert got.status == HeartbeatStatus.SKIPPED_NO_ACCESS
    assert got.matched is None


def test_heartbeat_replace_clears_stale_matched(store):
    # gmail-client gate E8: REPLACE mode must drop a previous run's matched
    # property when the next outcome carries none — a MERGE would leave a stale
    # count attached to a skip/fail heartbeat.
    store.write_heartbeat(Heartbeat(at=datetime.now(UTC), status=HeartbeatStatus.RAN, matched=7))
    store.write_heartbeat(Heartbeat(at=datetime.now(UTC), status=HeartbeatStatus.FAILED))
    got = store.read_heartbeat()
    assert got is not None
    assert got.status == HeartbeatStatus.FAILED
    assert got.matched is None


# --- pipeline-wiring (5c): ClaimHistory ledger, heartbeat dual-write, run lease ---


def _record(claim_ref: str = "2026/417", card_url: str = "https://trello.com/c/abc") -> ClaimRecord:
    return ClaimRecord(
        at=datetime.now(UTC),
        claim_ref=claim_ref,
        subject=f"Declaración de siniestro a colaborador {claim_ref}",
        type="DECLARACION_SINIESTRO",
        town="Madrid",
        owner="Nombre Apellido",
        card_url=card_url,
    )


def test_claim_missing_reads_none(store):
    assert store.get_claim("2026/999") is None


def test_claim_record_roundtrip(store):
    record = _record()
    store.record_claim(record)
    got = store.get_claim("2026/417")
    assert got is not None
    assert got.claim_ref == "2026/417"
    assert got.subject == record.subject
    assert got.type == "DECLARACION_SINIESTRO"
    assert got.town == "Madrid"
    assert got.owner == "Nombre Apellido"
    assert got.card_url == record.card_url


def test_claim_record_rewrite_is_idempotent(store):
    # Ledger semantics (REQ-4): re-recording the same claim ref replaces, never
    # raises — the redo path after a crash-before-relabel must be safe.
    store.record_claim(_record(card_url="https://trello.com/c/first"))
    store.record_claim(_record(card_url="https://trello.com/c/second"))
    got = store.get_claim("2026/417")
    assert got is not None
    assert got.card_url == "https://trello.com/c/second"


def test_claim_optional_fields_roundtrip_as_none(store):
    store.record_claim(
        ClaimRecord(
            at=datetime.now(UTC),
            claim_ref="2026/418",
            subject="s",
            type="SOLICITUD_ASISTENCIA",
            card_url="",
        )
    )
    got = store.get_claim("2026/418")
    assert got is not None
    assert got.town is None
    assert got.owner is None


def test_heartbeat_counts_roundtrip_and_replace_clears(store):
    # pipeline-wiring REQ-5: counts ride the last-run row; REPLACE drops them
    # on countless outcomes (same E8 stance as matched).
    store.write_heartbeat(
        Heartbeat(
            at=datetime.now(UTC), status=HeartbeatStatus.RAN, processed=3, failed=1, failed_total=2
        )
    )
    got = store.read_heartbeat()
    assert got is not None
    assert (got.processed, got.failed, got.failed_total) == (3, 1, 2)
    store.write_heartbeat(Heartbeat(at=datetime.now(UTC), status=HeartbeatStatus.SKIPPED_DISABLED))
    got = store.read_heartbeat()
    assert got is not None
    assert (got.processed, got.failed, got.failed_total) == (None, None, None)


def test_heartbeat_dual_write_appends_history(service, prefix, store):
    # REQ-5: every write lands twice — the last-run REPLACE row plus an
    # append-only history row (inverted-timestamp RowKey → newest first).
    store.write_heartbeat(Heartbeat(at=datetime.now(UTC), status=HeartbeatStatus.RAN, processed=1))
    store.write_heartbeat(Heartbeat(at=datetime.now(UTC), status=HeartbeatStatus.FAILED))
    table = service.get_table_client(prefix + HEARTBEAT_TABLE)
    rows = list(table.query_entities(f"PartitionKey eq '{HEARTBEAT_HISTORY_PARTITION}'"))
    assert len(rows) == 2
    # Ascending RowKey scan = newest first (inversion) — the FAILED write is second.
    statuses = [row["status"] for row in rows]
    assert statuses == ["failed", "ran"]


def test_run_lease_mutual_exclusion(store):
    # REQ-12: exactly one holder; a fresh lease blocks; release frees.
    assert store.try_acquire_run_lease(datetime.now(UTC)) is True
    assert store.try_acquire_run_lease(datetime.now(UTC)) is False
    store.release_run_lease()
    assert store.try_acquire_run_lease(datetime.now(UTC)) is True
    store.release_run_lease()


def test_run_lease_stale_takeover(store):
    # A crashed run's lease expires after the staleness window.
    long_ago = datetime.now(UTC) - timedelta(seconds=RUN_LEASE_STALE_S + 60)
    assert store.try_acquire_run_lease(long_ago) is True  # held with an old stamp
    assert store.try_acquire_run_lease(datetime.now(UTC)) is True  # taken over
    store.release_run_lease()


def test_run_lease_release_without_hold_is_noop(store):
    store.release_run_lease()  # must not raise


def test_run_lease_single_winner_under_concurrency(store):
    # P12 guard for the new methods: N concurrent acquires, exactly one winner.
    now = datetime.now(UTC)
    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(lambda _: store.try_acquire_run_lease(now), range(8)))
    assert results.count(True) == 1
    store.release_run_lease()


def test_new_accessors_safe_under_concurrent_use(store):
    # P12 serialization guard extended to the 5c accessors (same smoke contract
    # as test_shared_store_is_safe_under_concurrent_use).
    def hammer(worker_index: int) -> int:
        for n in range(10):
            store.record_claim(_record(claim_ref=f"2026/{worker_index}00{n}"))
            assert store.get_claim(f"2026/{worker_index}00{n}") is not None
            store.write_heartbeat(
                Heartbeat(at=datetime.now(UTC), status=HeartbeatStatus.RAN, processed=n)
            )
        return worker_index

    with ThreadPoolExecutor(max_workers=8) as executor:
        assert sorted(executor.map(hammer, range(8))) == list(range(8))
