"""Shared fixtures for the integration tier — everything here talks to real Azurite.

Azurite must be up (`make azurite`); when it is down these tests fail loudly —
no skip logic (state-store spec REQ-5.4).
"""

import pytest
from azure.data.tables import TableServiceClient

AZURITE_CONNECTION_STRING = "UseDevelopmentStorage=true"


@pytest.fixture(scope="session")
def azurite_connection_string() -> str:
    return AZURITE_CONNECTION_STRING


@pytest.fixture(scope="session")
def service():
    """One Table service client for the whole session (stateless HTTP wrapper)."""
    client = TableServiceClient.from_connection_string(AZURITE_CONNECTION_STRING)
    yield client
    client.close()
