"""Typed Table Storage access layer (state-store spec; D11/D23).

One table per data intent (D23); typed accessors exist only for the two rows
items 2-3 will consume: the worker on/off flag and the heartbeat row.

Sync `azure-data-tables` client: call from plain-`def` route handlers (FastAPI
threadpool) or the timer worker — never directly from `async def` code, which
would block the Functions host's single event loop.
"""

import logging
import threading
from enum import StrEnum
from functools import lru_cache

from azure.core.exceptions import ResourceExistsError, ResourceNotFoundError
from azure.data.tables import TableClient, TableErrorCode, TableServiceClient, UpdateMode
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

# Entity property names.
ENABLED_PROP = "enabled"
HEARTBEAT_AT_PROP = "at"
HEARTBEAT_STATUS_PROP = "status"

# The Table service reports a missing entity ("ResourceNotFound", or
# "EntityNotFound" from some responses) distinctly from a missing table
# ("TableNotFound"); only the missing-entity codes are the fail-safe OFF case.
ENTITY_MISSING_CODES = (TableErrorCode.RESOURCE_NOT_FOUND, TableErrorCode.ENTITY_NOT_FOUND)


class HeartbeatStatus(StrEnum):
    SKIPPED_DISABLED = "skipped_disabled"  # woke, flag off, exited
    RAN = "ran"  # pipeline ran to completion
    FAILED = "failed"  # pipeline raised (added by worker-skeleton, 2026-08-12)


class Heartbeat(BaseModel):
    at: AwareDatetime  # UTC by convention — callers use datetime.now(UTC); naive input rejected
    status: HeartbeatStatus


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

    def ensure_tables(self) -> None:
        """Create all five D23 tables if absent (idempotent)."""
        with self._lock:
            for name in ALL_TABLES:
                try:
                    self._service.create_table(self._prefix + name)
                except ResourceExistsError:
                    pass

    def read_enabled(self) -> bool:
        """The worker on/off flag (D4). A missing row reads as OFF (fail-safe);
        a missing table is a deployment fault and propagates."""
        try:
            with self._lock:
                entity = self._table(WORKER_STATE_TABLE).get_entity(
                    WORKER_STATE_PARTITION, ENABLED_ROW
                )
        except ResourceNotFoundError as exc:
            if exc.error_code in ENTITY_MISSING_CODES:
                return False
            raise
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
        with self._lock:
            self._table(HEARTBEAT_TABLE).upsert_entity(
                {
                    "PartitionKey": HEARTBEAT_PARTITION,
                    "RowKey": HEARTBEAT_ROW,
                    HEARTBEAT_AT_PROP: heartbeat.at,
                    HEARTBEAT_STATUS_PROP: heartbeat.status.value,
                },
                mode=UpdateMode.REPLACE,
            )

    def read_heartbeat(self) -> Heartbeat | None:
        try:
            with self._lock:
                entity = self._table(HEARTBEAT_TABLE).get_entity(HEARTBEAT_PARTITION, HEARTBEAT_ROW)
        except ResourceNotFoundError as exc:
            if exc.error_code in ENTITY_MISSING_CODES:
                return None
            raise
        return Heartbeat(
            at=entity[HEARTBEAT_AT_PROP],
            status=HeartbeatStatus(entity[HEARTBEAT_STATUS_PROP]),
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
