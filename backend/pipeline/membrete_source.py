"""Membrete letterhead assets, fetched from a private Blob container (D26,
amended 2026-08-21).

The real PNGs render personal data into the letterhead, so they must never
live in this (public) repo: the infra repo uploads them to the `membretes`
container; Azurite serves the identical fetch path locally, seeded with
synthetic sanitized letterheads by the tests.
"""

import threading
from typing import Protocol

from azure.storage.blob import BlobServiceClient


class MembreteSource(Protocol):
    def get(self, name: str) -> bytes: ...


class BlobMembreteSource:
    """Blob name → bytes from one container, cached in memory — the assets are
    static, so each function instance fetches each membrete at most once."""

    def __init__(self, service: BlobServiceClient, container: str) -> None:
        self._container = service.get_container_client(container)
        self._cache: dict[str, bytes] = {}
        # The timer thread and FastAPI's threadpool (process-now) can overlap,
        # and azure-core's sync RequestsTransport disclaims thread safety —
        # same rationale as the StateStore lock (state_store.py).
        self._lock = threading.Lock()

    @classmethod
    def from_connection_string(cls, connection_string: str, container: str) -> "BlobMembreteSource":
        return cls(BlobServiceClient.from_connection_string(connection_string), container)

    def get(self, name: str) -> bytes:
        with self._lock:
            if name not in self._cache:
                self._cache[name] = self._container.download_blob(name).readall()
            return self._cache[name]
