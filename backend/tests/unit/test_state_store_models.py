"""Unit tests for the state-store models (no Azurite needed)."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from core.state_store import ClaimRecord, Heartbeat, HeartbeatStatus, TrelloConfig


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


def test_heartbeat_status_has_skipped_busy():
    # pipeline-wiring REQ-12: the run-lease exit, snake_case like its siblings.
    assert HeartbeatStatus.SKIPPED_BUSY.value == "skipped_busy"


def test_heartbeat_counts_default_to_none():
    # pipeline-wiring REQ-5: rows predating 5c carry no counts.
    hb = Heartbeat(at=datetime.now(UTC), status=HeartbeatStatus.RAN)
    assert hb.processed is None
    assert hb.failed is None
    assert hb.failed_total is None


def test_heartbeat_carries_run_counts():
    hb = Heartbeat(
        at=datetime.now(UTC),
        status=HeartbeatStatus.RAN,
        processed=3,
        failed=1,
        failed_total=2,
    )
    assert (hb.processed, hb.failed, hb.failed_total) == (3, 1, 2)


def test_claim_record_rejects_naive_datetime():
    # Same stance as Heartbeat: naive input would be silently shifted by the SDK.
    with pytest.raises(ValidationError):
        ClaimRecord(
            at=datetime(2026, 1, 1, 12, 0, 0),
            claim_ref="2026/417",
            subject="Declaración de siniestro a colaborador 2026/417",
            type="DECLARACION_SINIESTRO",
            card_url="https://trello.com/c/abc123",
        )


def test_claim_record_optional_fields_default_none():
    # town/owner are extraction results and may be absent (laptop parity: the
    # regex extractor returns None fields; the JSONL wrote them as-is).
    record = ClaimRecord(
        at=datetime.now(UTC),
        claim_ref="2026/417",
        subject="s",
        type="DECLARACION_SINIESTRO",
        card_url="",
    )
    assert record.town is None
    assert record.owner is None


def test_trello_config_holds_board_and_list_ids():
    # settings REQ-1/2: the TrelloConfig row carries the two runtime-entered IDs (D23).
    cfg = TrelloConfig(board_id="g7vysmjD", list_id="68875e0d401d7613fcbbc092")
    assert cfg.board_id == "g7vysmjD"
    assert cfg.list_id == "68875e0d401d7613fcbbc092"


def test_trello_config_allows_empty_ids():
    # Fresh install: partial config is legal (REQ-1.3) — empty string, not None.
    cfg = TrelloConfig(board_id="", list_id="")
    assert cfg.board_id == ""
