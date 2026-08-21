"""Unit tests for the Table Storage settings additions (state-store spec, REQ-4)."""

import pytest
from pydantic import ValidationError

from core.config import Settings


def test_backend_defaults_to_connection_string():
    s = Settings()
    assert s.table_storage_backend == "connection_string"
    assert s.storage_connection_string == "UseDevelopmentStorage=true"
    assert s.tables_endpoint is None


def test_managed_identity_requires_endpoint():
    with pytest.raises(ValidationError, match="TABLES_ENDPOINT"):
        Settings(table_storage_backend="managed_identity")


def test_managed_identity_with_endpoint_is_valid():
    s = Settings(
        table_storage_backend="managed_identity",
        tables_endpoint="https://example.table.core.windows.net",
    )
    assert s.table_storage_backend == "managed_identity"
    assert s.tables_endpoint == "https://example.table.core.windows.net"
