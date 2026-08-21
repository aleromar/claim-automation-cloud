"""Typed Table Storage access layer (state-store spec; D11/D23).

One table per data intent (D23); typed accessors exist only for the rows the
features shipped so far consume: the worker on/off flag, the heartbeat row and
the Trello board/list config row.

Sync `azure-data-tables` client: call from plain-`def` route handlers (FastAPI
threadpool) or the timer worker — never directly from `async def` code, which
would block the Functions host's single event loop.
"""

import logging
import threading
from enum import StrEnum
from functools import lru_cache
from time import time_ns

from azure.core.exceptions import ResourceExistsError, ResourceNotFoundError
from azure.data.tables import (
    TableClient,
    TableEntity,
    TableErrorCode,
    TableServiceClient,
    UpdateMode,
)
from azure.identity import DefaultAzureCredential
from pydantic import AwareDatetime, BaseModel, Field

from core.config import Settings, get_settings

logger = logging.getLogger(__name__)

# Table names (D23) — one per data intent.
WORKER_STATE_TABLE = "WorkerState"
TRELLO_CONFIG_TABLE = "TrelloConfig"
HEARTBEAT_TABLE = "Heartbeat"
METRICS_TABLE = "Metrics"
CLAIM_HISTORY_TABLE = "ClaimHistory"
ALL_TABLES = (
    WORKER_STATE_TABLE,
    TRELLO_CONFIG_TABLE,
    HEARTBEAT_TABLE,
    METRICS_TABLE,
    CLAIM_HISTORY_TABLE,
)

# Entity keys (D23).
WORKER_STATE_PARTITION = "worker"
ENABLED_ROW = "enabled"
HEARTBEAT_PARTITION = "run"
HEARTBEAT_ROW = "last"
HEARTBEAT_HISTORY_PARTITION = "history"  # append-only run log (pipeline-wiring REQ-5)
TRELLO_CONFIG_PARTITION = "trello"
TRELLO_CONFIG_ROW = "config"
CLAIM_PARTITION = "claim"  # ClaimHistory ledger rows (pipeline-wiring REQ-4)
RUN_LEASE_ROW = "run_lock"  # timer × process-now mutual exclusion (REQ-12)

# A crashed run's lease expires after this window — a dead holder must never
# deadlock the worker (pipeline-wiring REQ-12).
RUN_LEASE_STALE_S = 600

# Entity property names.
ENABLED_PROP = "enabled"
HEARTBEAT_AT_PROP = "at"
HEARTBEAT_STATUS_PROP = "status"
HEARTBEAT_MATCHED_PROP = "matched"
HEARTBEAT_PROCESSED_PROP = "processed"
HEARTBEAT_FAILED_PROP = "failed"
HEARTBEAT_FAILED_TOTAL_PROP = "failed_total"
BOARD_ID_PROP = "board_id"
LIST_ID_PROP = "list_id"
CLAIM_REF_PROP = "claim_ref"
CLAIM_AT_PROP = "at"
CLAIM_SUBJECT_PROP = "subject"
CLAIM_TYPE_PROP = "type"
CLAIM_TOWN_PROP = "town"
CLAIM_OWNER_PROP = "owner"
CLAIM_CARD_URL_PROP = "card_url"
RUN_LEASE_AT_PROP = "at"

# The Table service reports a missing entity ("ResourceNotFound", or
# "EntityNotFound" from some responses) distinctly from a missing table
# ("TableNotFound"); only the missing-entity codes are the fail-safe OFF case.
ENTITY_MISSING_CODES = (TableErrorCode.RESOURCE_NOT_FOUND, TableErrorCode.ENTITY_NOT_FOUND)


class HeartbeatStatus(StrEnum):
    SKIPPED_DISABLED = "skipped_disabled"  # woke, flag off, exited
    RAN = "ran"  # pipeline ran to completion
    FAILED = "failed"  # pipeline raised (added by worker-skeleton, 2026-08-12)
    SKIPPED_NO_ACCESS = "skipped_no_access"  # token preflight failed (gmail-client, 2026-08-21)
    SKIPPED_BUSY = "skipped_busy"  # run lease held by another invocation (pipeline-wiring, REQ-12)


class Heartbeat(BaseModel):
    at: AwareDatetime  # UTC by convention — callers use datetime.now(UTC); naive input rejected
    status: HeartbeatStatus
    # Probe count for `ran` outcomes (gmail-client REQ-4); None otherwise and on
    # rows written before 5b — Table Storage has no null, so None is "property absent".
    # No longer written since 5c (superseded by the counts below); kept readable.
    matched: int | None = Field(default=None, ge=0)
    # Run counts + failed-label gauge (pipeline-wiring REQ-5); None on pre-5c rows.
    processed: int | None = Field(default=None, ge=0)
    failed: int | None = Field(default=None, ge=0)
    failed_total: int | None = Field(default=None, ge=0)


class ClaimRecord(BaseModel):
    """One ClaimHistory ledger row (pipeline-wiring REQ-4): 'successfully
    processed once' — NOT a mirror of the Trello card's current state."""

    at: AwareDatetime
    claim_ref: str  # "YYYY/N" — the RowKey stores it '/'→'-' (keys forbid '/')
    subject: str
    type: str  # ClaimType.name as a string — core must not import pipeline
    town: str | None = None
    owner: str | None = None
    card_url: str


class TrelloConfig(BaseModel):
    # Runtime-entered Trello IDs (D23/D25); empty strings are legal (fresh install,
    # partial config). The secrets (key/token) live in the SecretStore, never here.
    board_id: str
    list_id: str


class StateStore:
    def __init__(self, service: TableServiceClient, table_prefix: str = "") -> None:
        self._service = service
        self._prefix = table_prefix
        # Serialize all table ops: the process-wide cached store (get_state_store)
        # is shared between the timer thread and FastAPI's threadpool, and every
        # op funnels through the service client's single requests.Session —
        # azure-core's sync RequestsTransport explicitly disclaims thread safety.
        # At single-operator volume (~ms point ops) serialization costs nothing.
        self._lock = threading.Lock()

    def _table(self, name: str) -> TableClient:
        return self._service.get_table_client(self._prefix + name)

    def _get_entity_or_none(self, table: str, partition: str, row: str) -> TableEntity | None:
        """Missing entity → None (a normal state, each reader decides its
        fail-safe); a missing table is a deployment fault and propagates."""
        try:
            with self._lock:
                return self._table(table).get_entity(partition, row)
        except ResourceNotFoundError as exc:
            if exc.error_code in ENTITY_MISSING_CODES:
                return None
            raise

    def ensure_tables(self) -> None:
        """Create all five D23 tables if absent (idempotent)."""
        with self._lock:
            for name in ALL_TABLES:
                try:
                    self._service.create_table(self._prefix + name)
                except ResourceExistsError:
                    pass

    def read_enabled(self) -> bool:
        """The worker on/off flag (D4). A missing row reads as OFF (fail-safe)."""
        entity = self._get_entity_or_none(WORKER_STATE_TABLE, WORKER_STATE_PARTITION, ENABLED_ROW)
        if entity is None:
            return False
        value = entity[ENABLED_PROP]
        # Only set_enabled(bool) legitimately writes this row; truthiness would
        # read a foreign "false" string as ON — corrupt data fails loud instead
        # (same stance as the missing-table fault above).
        if not isinstance(value, bool):
            raise TypeError(
                f"WorkerState.{ENABLED_PROP} is {type(value).__name__!r}, expected bool"
            )
        return value

    def set_enabled(self, enabled: bool) -> None:
        with self._lock:
            self._table(WORKER_STATE_TABLE).upsert_entity(
                {
                    "PartitionKey": WORKER_STATE_PARTITION,
                    "RowKey": ENABLED_ROW,
                    ENABLED_PROP: enabled,
                },
                mode=UpdateMode.REPLACE,
            )

    def write_heartbeat(self, heartbeat: Heartbeat) -> None:
        """Dual-write (pipeline-wiring REQ-5): the last-run REPLACE row (the
        dashboard card contract) plus an append-only history row — the scheduler
        stays unaware of the second write."""
        props: dict = {
            HEARTBEAT_AT_PROP: heartbeat.at,
            HEARTBEAT_STATUS_PROP: heartbeat.status.value,
        }
        for prop, value in (
            (HEARTBEAT_MATCHED_PROP, heartbeat.matched),
            (HEARTBEAT_PROCESSED_PROP, heartbeat.processed),
            (HEARTBEAT_FAILED_PROP, heartbeat.failed),
            (HEARTBEAT_FAILED_TOTAL_PROP, heartbeat.failed_total),
        ):
            if value is not None:
                props[prop] = value
        # Inverted wall-clock ns: ascending RowKey scans read newest first.
        history_row_key = f"{2**63 - time_ns():020d}"
        with self._lock:
            table = self._table(HEARTBEAT_TABLE)
            # REPLACE (not MERGE) so a countless outcome drops any stale counts.
            table.upsert_entity(
                {"PartitionKey": HEARTBEAT_PARTITION, "RowKey": HEARTBEAT_ROW, **props},
                mode=UpdateMode.REPLACE,
            )
            table.create_entity(
                {
                    "PartitionKey": HEARTBEAT_HISTORY_PARTITION,
                    "RowKey": history_row_key,
                    **props,
                }
            )

    def write_trello_config(self, config: TrelloConfig) -> None:
        with self._lock:
            self._table(TRELLO_CONFIG_TABLE).upsert_entity(
                {
                    "PartitionKey": TRELLO_CONFIG_PARTITION,
                    "RowKey": TRELLO_CONFIG_ROW,
                    BOARD_ID_PROP: config.board_id,
                    LIST_ID_PROP: config.list_id,
                },
                mode=UpdateMode.REPLACE,
            )

    def read_trello_config(self) -> TrelloConfig | None:
        entity = self._get_entity_or_none(
            TRELLO_CONFIG_TABLE, TRELLO_CONFIG_PARTITION, TRELLO_CONFIG_ROW
        )
        if entity is None:
            return None
        return TrelloConfig(board_id=entity[BOARD_ID_PROP], list_id=entity[LIST_ID_PROP])

    def read_heartbeat(self) -> Heartbeat | None:
        entity = self._get_entity_or_none(HEARTBEAT_TABLE, HEARTBEAT_PARTITION, HEARTBEAT_ROW)
        if entity is None:
            return None
        return Heartbeat(
            at=entity[HEARTBEAT_AT_PROP],
            status=HeartbeatStatus(entity[HEARTBEAT_STATUS_PROP]),
            matched=entity.get(HEARTBEAT_MATCHED_PROP),
            processed=entity.get(HEARTBEAT_PROCESSED_PROP),
            failed=entity.get(HEARTBEAT_FAILED_PROP),
            failed_total=entity.get(HEARTBEAT_FAILED_TOTAL_PROP),
        )

    def record_claim(self, record: ClaimRecord) -> None:
        """Upsert, not insert: re-recording after a crash-before-relabel redo
        must be safe (REQ-4 ledger semantics)."""
        entity = {
            "PartitionKey": CLAIM_PARTITION,
            "RowKey": record.claim_ref.replace("/", "-"),
            CLAIM_AT_PROP: record.at,
            CLAIM_REF_PROP: record.claim_ref,
            CLAIM_SUBJECT_PROP: record.subject,
            CLAIM_TYPE_PROP: record.type,
            CLAIM_CARD_URL_PROP: record.card_url,
        }
        if record.town is not None:
            entity[CLAIM_TOWN_PROP] = record.town
        if record.owner is not None:
            entity[CLAIM_OWNER_PROP] = record.owner
        with self._lock:
            self._table(CLAIM_HISTORY_TABLE).upsert_entity(entity, mode=UpdateMode.REPLACE)

    def get_claim(self, claim_ref: str) -> ClaimRecord | None:
        entity = self._get_entity_or_none(
            CLAIM_HISTORY_TABLE, CLAIM_PARTITION, claim_ref.replace("/", "-")
        )
        if entity is None:
            return None
        return ClaimRecord(
            at=entity[CLAIM_AT_PROP],
            claim_ref=entity[CLAIM_REF_PROP],
            subject=entity[CLAIM_SUBJECT_PROP],
            type=entity[CLAIM_TYPE_PROP],
            town=entity.get(CLAIM_TOWN_PROP),
            owner=entity.get(CLAIM_OWNER_PROP),
            card_url=entity[CLAIM_CARD_URL_PROP],
        )

    def try_acquire_run_lease(self, now: AwareDatetime) -> bool:
        """Cross-process mutual exclusion (REQ-12): insert-if-absent is the
        atomic acquire; a stale row (crashed holder) is taken over. In-process
        atomicity comes from the store lock; cross-process, from create_entity."""
        entity = {
            "PartitionKey": WORKER_STATE_PARTITION,
            "RowKey": RUN_LEASE_ROW,
            RUN_LEASE_AT_PROP: now,
        }
        with self._lock:
            table = self._table(WORKER_STATE_TABLE)
            try:
                table.create_entity(entity)
                return True
            except ResourceExistsError:
                held_at = table.get_entity(WORKER_STATE_PARTITION, RUN_LEASE_ROW)[RUN_LEASE_AT_PROP]
                if (now - held_at).total_seconds() > RUN_LEASE_STALE_S:
                    table.upsert_entity(entity, mode=UpdateMode.REPLACE)
                    return True
                return False

    def release_run_lease(self) -> None:
        with self._lock:
            try:
                self._table(WORKER_STATE_TABLE).delete_entity(WORKER_STATE_PARTITION, RUN_LEASE_ROW)
            except ResourceNotFoundError:
                pass


def state_store_from_settings(settings: Settings) -> StateStore:
    """Build the store for the configured backend and ensure the tables exist."""
    if settings.table_storage_backend == "managed_identity":
        # tables_endpoint is guaranteed by the settings validator.
        assert settings.tables_endpoint
        service = TableServiceClient(
            endpoint=settings.tables_endpoint, credential=DefaultAzureCredential()
        )
    else:
        service = TableServiceClient.from_connection_string(settings.storage_connection_string)
    logger.info("state store backend: %s", settings.table_storage_backend)
    store = StateStore(service)
    store.ensure_tables()
    return store


@lru_cache
def get_state_store() -> StateStore:
    """Process-wide store (worker-controls REQ-6): ensure_tables() runs once at
    first use, not per request/wake. Shared by the worker routes (FastAPI
    dependency) and the timer — one Functions host process; cross-thread safety
    comes from StateStore's own lock, NOT the SDK (azure-core's sync transport
    disclaims it)."""
    return state_store_from_settings(get_settings())
