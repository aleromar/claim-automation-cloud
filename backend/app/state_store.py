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

from azure.core.exceptions import ResourceExistsError, ResourceNotFoundError
from azure.data.tables import (
    TableClient,
    TableEntity,
    TableErrorCode,
    TableServiceClient,
    UpdateMode,
)
from azure.identity import DefaultAzureCredential
from pydantic import AwareDatetime, BaseModel

from app.config import Settings, get_settings

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
TRELLO_CONFIG_PARTITION = "trello"
TRELLO_CONFIG_ROW = "config"

# Entity property names.
ENABLED_PROP = "enabled"
HEARTBEAT_AT_PROP = "at"
HEARTBEAT_STATUS_PROP = "status"
HEARTBEAT_MATCHED_PROP = "matched"
BOARD_ID_PROP = "board_id"
LIST_ID_PROP = "list_id"

# The Table service reports a missing entity ("ResourceNotFound", or
# "EntityNotFound" from some responses) distinctly from a missing table
# ("TableNotFound"); only the missing-entity codes are the fail-safe OFF case.
ENTITY_MISSING_CODES = (TableErrorCode.RESOURCE_NOT_FOUND, TableErrorCode.ENTITY_NOT_FOUND)


class HeartbeatStatus(StrEnum):
    SKIPPED_DISABLED = "skipped_disabled"  # woke, flag off, exited
    RAN = "ran"  # pipeline ran to completion
    FAILED = "failed"  # pipeline raised (added by worker-skeleton, 2026-08-12)
    SKIPPED_NO_ACCESS = "skipped_no_access"  # token preflight failed (gmail-client, 2026-08-21)


class Heartbeat(BaseModel):
    at: AwareDatetime  # UTC by convention — callers use datetime.now(UTC); naive input rejected
    status: HeartbeatStatus
    # Probe count for `ran` outcomes (gmail-client REQ-4); None otherwise and on
    # rows written before 5b — Table Storage has no null, so None is "property absent".
    matched: int | None = None


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
        entity = {
            "PartitionKey": HEARTBEAT_PARTITION,
            "RowKey": HEARTBEAT_ROW,
            HEARTBEAT_AT_PROP: heartbeat.at,
            HEARTBEAT_STATUS_PROP: heartbeat.status.value,
        }
        if heartbeat.matched is not None:
            entity[HEARTBEAT_MATCHED_PROP] = heartbeat.matched
        with self._lock:
            # REPLACE (not MERGE) so a countless outcome drops any stale matched.
            self._table(HEARTBEAT_TABLE).upsert_entity(entity, mode=UpdateMode.REPLACE)

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
        )


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
