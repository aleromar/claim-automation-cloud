"""Unit tests for the state-store models (no Azurite needed)."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from core.state_store import Heartbeat, HeartbeatStatus, TrelloConfig


def test_heartbeat_rejects_naive_datetime():
    # A naive datetime would be reinterpreted as *local* time by the SDK and
    # silently shifted — callers must pass datetime.now(UTC), never utcnow().
    with pytest.raises(ValidationError):
        Heartbeat(at=datetime(2026, 1, 1, 12, 0, 0), status=HeartbeatStatus.RAN)


def test_heartbeat_accepts_aware_utc():
    hb = Heartbeat(at=datetime.now(UTC), status=HeartbeatStatus.RAN)
    assert hb.at.tzinfo is not None


def test_heartbeat_status_has_skipped_no_access():
    # gmail-client REQ-4: the preflight outcome, snake_case like its siblings
    # (the worker-controls frontend comment anticipated this exact spelling).
    assert HeartbeatStatus.SKIPPED_NO_ACCESS.value == "skipped_no_access"


def test_heartbeat_matched_defaults_to_none():
    # gmail-client REQ-4: rows predating 5b (and non-ran outcomes) carry no count.
    hb = Heartbeat(at=datetime.now(UTC), status=HeartbeatStatus.SKIPPED_DISABLED)
    assert hb.matched is None


def test_heartbeat_carries_matched_count():
    hb = Heartbeat(at=datetime.now(UTC), status=HeartbeatStatus.RAN, matched=3)
    assert hb.matched == 3


def test_trello_config_holds_board_and_list_ids():
    # settings REQ-1/2: the TrelloConfig row carries the two runtime-entered IDs (D23).
    cfg = TrelloConfig(board_id="g7vysmjD", list_id="68875e0d401d7613fcbbc092")
    assert cfg.board_id == "g7vysmjD"
    assert cfg.list_id == "68875e0d401d7613fcbbc092"


def test_trello_config_allows_empty_ids():
    # Fresh install: partial config is legal (REQ-1.3) — empty string, not None.
    cfg = TrelloConfig(board_id="", list_id="")
    assert cfg.board_id == ""
