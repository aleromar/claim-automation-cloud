"""Typed Table Storage access layer (state-store spec; D11/D23).

One table per data intent (D23); typed accessors exist only where a consumer
exists today (items 2-3): the worker on/off flag and the heartbeat row.

Sync `azure-data-tables` client: call from plain-`def` route handlers (FastAPI
threadpool) or the timer worker — never directly from `async def` code, which
would block the Functions host's single event loop.
"""

import logging
from datetime import datetime
from enum import StrEnum

from azure.core.exceptions import ResourceExistsError, ResourceNotFoundError
from azure.data.tables import TableClient, TableServiceClient, UpdateMode
from azure.identity import DefaultAzureCredential
from pydantic import BaseModel

from app.config import Settings

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

# The Table service distinguishes a missing entity ("ResourceNotFound") from a
# missing table ("TableNotFound"); only the former is the fail-safe OFF case.
ENTITY_NOT_FOUND = "ResourceNotFound"


class HeartbeatStatus(StrEnum):
    SKIPPED_DISABLED = "skipped_disabled"  # woke, flag off, exited
    RAN = "ran"  # pipeline executed


class Heartbeat(BaseModel):
    at: datetime  # tz-aware UTC
    status: HeartbeatStatus


class StateStore:
    def __init__(self, service: TableServiceClient, table_prefix: str = "") -> None:
        self._service = service
        self._prefix = table_prefix

    def _table(self, name: str) -> TableClient:
        return self._service.get_table_client(self._prefix + name)

    def ensure_tables(self) -> None:
        """Create all five D23 tables if absent (idempotent)."""
        for name in ALL_TABLES:
            try:
                self._service.create_table(self._prefix + name)
            except ResourceExistsError:
                pass

    def read_enabled(self) -> bool:
        """The worker on/off flag (D4). A missing row reads as OFF (fail-safe);
        a missing table is a deployment fault and propagates."""
        try:
            entity = self._table(WORKER_STATE_TABLE).get_entity(WORKER_STATE_PARTITION, ENABLED_ROW)
        except ResourceNotFoundError as exc:
            if exc.error_code == ENTITY_NOT_FOUND:
                return False
            raise
        return bool(entity[ENABLED_PROP])

    def set_enabled(self, enabled: bool) -> None:
        self._table(WORKER_STATE_TABLE).upsert_entity(
            {
                "PartitionKey": WORKER_STATE_PARTITION,
                "RowKey": ENABLED_ROW,
                ENABLED_PROP: enabled,
            },
            mode=UpdateMode.REPLACE,
        )

    def write_heartbeat(self, heartbeat: Heartbeat) -> None:
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
            entity = self._table(HEARTBEAT_TABLE).get_entity(HEARTBEAT_PARTITION, HEARTBEAT_ROW)
        except ResourceNotFoundError as exc:
            if exc.error_code == ENTITY_NOT_FOUND:
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
        service = TableServiceClient(
            endpoint=settings.tables_endpoint or "", credential=DefaultAzureCredential()
        )
    else:
        service = TableServiceClient.from_connection_string(settings.storage_connection_string)
    logger.info("state store backend: %s", settings.table_storage_backend)
    store = StateStore(service)
    store.ensure_tables()
    return store
