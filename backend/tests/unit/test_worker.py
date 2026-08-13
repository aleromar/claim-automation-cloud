"""Worker wake logic (worker-skeleton REQ-2, REQ-4): gate → maybe pipeline → heartbeat.

The heartbeat is the run report, written LAST on every exit path (operator
decision 2026-08-12): `ran` = pipeline completed, `failed` = pipeline raised,
`skipped_disabled` = gate exit. Pure orchestration with injected dependencies —
a fake store (our own class, no SDK mocks) records what the worker did and in
which order.
"""

import logging
from datetime import UTC, datetime

import pytest

from app.state_store import Heartbeat, HeartbeatStatus
from app.worker import WORKER_RUN_LOG_PREFIX, run_worker


class FakeStateStore:
    """The two accessors the worker consumes, recording calls into a shared event log."""

    def __init__(self, enabled: bool, events: list) -> None:
        self._enabled = enabled
        self.events = events
        self.heartbeats: list[Heartbeat] = []

    def read_enabled(self) -> bool:
        self.events.append("read_enabled")
        return self._enabled

    def write_heartbeat(self, heartbeat: Heartbeat) -> None:
        self.events.append("write_heartbeat")
        self.heartbeats.append(heartbeat)


class RaisingStateStore:
    def read_enabled(self) -> bool:
        raise ConnectionError("storage unreachable")

    def write_heartbeat(self, heartbeat: Heartbeat) -> None:  # pragma: no cover
        raise AssertionError("must not be reached")


class HeartbeatRaisingStateStore:
    def read_enabled(self) -> bool:
        return False

    def write_heartbeat(self, heartbeat: Heartbeat) -> None:
        raise ConnectionError("storage unreachable")


def _worker_run(enabled: bool):
    events: list[str] = []
    store = FakeStateStore(enabled=enabled, events=events)
    pipeline_calls: list[str] = []

    def pipeline() -> None:
        events.append("pipeline")
        pipeline_calls.append("called")

    outcome = run_worker(store, pipeline)
    return outcome, store, events, pipeline_calls


def test_disabled_writes_heartbeat_and_skips_pipeline():
    outcome, store, events, pipeline_calls = _worker_run(enabled=False)
    assert outcome == HeartbeatStatus.SKIPPED_DISABLED
    assert [hb.status for hb in store.heartbeats] == [HeartbeatStatus.SKIPPED_DISABLED]
    assert pipeline_calls == []
    assert events == ["read_enabled", "write_heartbeat"]


def test_enabled_runs_pipeline_then_writes_heartbeat():
    outcome, store, events, pipeline_calls = _worker_run(enabled=True)
    assert outcome == HeartbeatStatus.RAN
    assert [hb.status for hb in store.heartbeats] == [HeartbeatStatus.RAN]
    assert pipeline_calls == ["called"]
    # The heartbeat is the run REPORT: written after the pipeline body completes,
    # so `ran` means "ran to completion" (REQ-2 revision, 2026-08-12).
    assert events == ["read_enabled", "pipeline", "write_heartbeat"]


def test_failed_pipeline_writes_failed_heartbeat_and_raises():
    events: list[str] = []
    store = FakeStateStore(enabled=True, events=events)

    def failing_pipeline() -> None:
        raise RuntimeError("pipeline blew up")

    # FAILED heartbeat lands, then the exception propagates so App Insights
    # still records a failed invocation (REQ-2.3).
    with pytest.raises(RuntimeError, match="pipeline blew up"):
        run_worker(store, failing_pipeline)
    assert [hb.status for hb in store.heartbeats] == [HeartbeatStatus.FAILED]
    assert events == ["read_enabled", "write_heartbeat"]


@pytest.mark.parametrize("enabled", [False, True])
def test_heartbeat_timestamp_is_utc_aware(enabled):
    before = datetime.now(UTC)
    _, store, _, _ = _worker_run(enabled=enabled)
    (heartbeat,) = store.heartbeats
    assert heartbeat.at.tzinfo is not None
    assert before <= heartbeat.at <= datetime.now(UTC)


def test_store_error_propagates():
    # Storage faults must fail the invocation loudly (REQ-2.6) — no swallow-and-continue.
    with pytest.raises(ConnectionError):
        run_worker(RaisingStateStore(), lambda: None)


def test_heartbeat_write_error_propagates():
    with pytest.raises(ConnectionError):
        run_worker(HeartbeatRaisingStateStore(), lambda: None)


@pytest.mark.parametrize(
    ("enabled", "expected_outcome"),
    [(False, HeartbeatStatus.SKIPPED_DISABLED), (True, HeartbeatStatus.RAN)],
)
def test_wake_logs_one_structured_line_per_outcome(caplog, enabled, expected_outcome):
    with caplog.at_level(logging.INFO, logger="app.worker"):
        _worker_run(enabled=enabled)
    worker_lines = [r.getMessage() for r in caplog.records if r.name == "app.worker"]
    assert worker_lines == [f"{WORKER_RUN_LOG_PREFIX} outcome={expected_outcome.value}"]


def test_failed_wake_logs_one_structured_line(caplog):
    def failing_pipeline() -> None:
        raise RuntimeError("boom")

    with caplog.at_level(logging.INFO, logger="app.worker"), pytest.raises(RuntimeError):
        run_worker(FakeStateStore(enabled=True, events=[]), failing_pipeline)
    worker_lines = [r.getMessage() for r in caplog.records if r.name == "app.worker"]
    assert worker_lines == [f"{WORKER_RUN_LOG_PREFIX} outcome={HeartbeatStatus.FAILED.value}"]
