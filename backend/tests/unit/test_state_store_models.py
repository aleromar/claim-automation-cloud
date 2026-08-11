"""Unit tests for the state-store models (no Azurite needed)."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.state_store import Heartbeat, HeartbeatStatus


def test_heartbeat_rejects_naive_datetime():
    # A naive datetime would be reinterpreted as *local* time by the SDK and
    # silently shifted — callers must pass datetime.now(UTC), never utcnow().
    with pytest.raises(ValidationError):
        Heartbeat(at=datetime(2026, 1, 1, 12, 0, 0), status=HeartbeatStatus.RAN)


def test_heartbeat_accepts_aware_utc():
    hb = Heartbeat(at=datetime.now(UTC), status=HeartbeatStatus.RAN)
    assert hb.at.tzinfo is not None
