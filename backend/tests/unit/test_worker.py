"""Worker wake logic (worker-skeleton REQ-2/4; gmail-client REQ-2/6):
gate → pipeline → heartbeat.

The heartbeat is the run report, written LAST on every exit path (operator
decision 2026-08-12): `ran` = pipeline completed (carrying the probe's matched
count), `failed` = pipeline raised, `skipped_disabled` = gate exit,
`skipped_no_access` = the pipeline raised core `NoAccessError` — its preflight
is the body's first step (REQ-2 amendment, 2026-08-21). Pure orchestration
with injected dependencies — fakes, no SDK mocks.
"""

import logging
from datetime import UTC, datetime

import pytest

from core.state_store import Heartbeat, HeartbeatStatus, RunCounts
from app.worker import WORKER_RUN_LOG_PREFIX, run_scheduled_worker, run_worker
from core.exceptions import NoAccessError, RunBusyError
from pipeline.gmail_client import GmailNoAccessError


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


def _worker_run(enabled: bool, matched: int = 1):
    events: list[str] = []
    store = FakeStateStore(enabled=enabled, events=events)
    pipeline_calls: list[str] = []

    def pipeline() -> RunCounts:
        events.append("pipeline")
        pipeline_calls.append("called")
        return RunCounts(processed=matched, failed=1, failed_total=2)

    outcome = run_worker(store, pipeline)
    return outcome, store, events, pipeline_calls


def test_disabled_writes_heartbeat_and_skips_pipeline():
    # Gate order: enabled first (2026-08-12) — a disabled wake never invokes
    # the pipeline, whose first step (the preflight) touches the SecretStore
    # and Google (gmail-client REQ-2).
    outcome, store, events, pipeline_calls = _worker_run(enabled=False)
    assert outcome == HeartbeatStatus.SKIPPED_DISABLED
    assert [hb.status for hb in store.heartbeats] == [HeartbeatStatus.SKIPPED_DISABLED]
    assert pipeline_calls == []
    assert events == ["read_enabled", "write_heartbeat"]


def test_enabled_runs_pipeline_then_heartbeat():
    outcome, store, events, pipeline_calls = _worker_run(enabled=True)
    assert outcome == HeartbeatStatus.RAN
    assert [hb.status for hb in store.heartbeats] == [HeartbeatStatus.RAN]
    assert pipeline_calls == ["called"]
    # The heartbeat is the run REPORT: written after the pipeline body completes,
    # so `ran` means "ran to completion" (REQ-2 revision, 2026-08-12).
    assert events == ["read_enabled", "pipeline", "write_heartbeat"]


def test_run_counts_land_on_the_ran_heartbeat():
    # pipeline-wiring REQ-5: processed/failed/failed_total ride the heartbeat;
    # matched is no longer written (5b legacy, readable on old rows only).
    _, store, _, _ = _worker_run(enabled=True, matched=3)
    (heartbeat,) = store.heartbeats
    assert (heartbeat.processed, heartbeat.failed, heartbeat.failed_total) == (3, 1, 2)
    assert heartbeat.matched is None


def test_no_access_pipeline_writes_skipped_no_access(caplog):
    # REQ-2 amendment (2026-08-21): the preflight is the pipeline body's first
    # step, so a pipeline-raised NoAccessError IS the skip signal.
    events: list[str] = []
    store = FakeStateStore(enabled=True, events=events)

    def no_access_pipeline() -> int:
        raise GmailNoAccessError("missing_token")

    with caplog.at_level(logging.INFO, logger="app.worker"):
        outcome = run_worker(store, no_access_pipeline)
    assert outcome == HeartbeatStatus.SKIPPED_NO_ACCESS
    (heartbeat,) = store.heartbeats
    assert heartbeat.status == HeartbeatStatus.SKIPPED_NO_ACCESS
    assert heartbeat.matched is None
    # Structured skip reason precedes the outcome line (gmail-client REQ-2).
    lines = [r.getMessage() for r in caplog.records if r.name == "app.worker"]
    assert (
        f"{WORKER_RUN_LOG_PREFIX} no_access source=GmailNoAccessError reason=missing_token" in lines
    )


def test_scheduler_classifies_the_neutral_contract_exception():
    # The wake contract is workload-agnostic: run_worker catches core's
    # NoAccessError, never a Gmail-specific type — any workload (5c: Trello)
    # signals dead credentials the same way. GmailNoAccessError participates
    # by subclassing.
    store = FakeStateStore(enabled=True, events=[])

    class OtherWorkloadNoAccess(NoAccessError):
        pass

    def other_pipeline() -> int:
        raise OtherWorkloadNoAccess("credentials_revoked")

    outcome = run_worker(store, other_pipeline)
    assert outcome == HeartbeatStatus.SKIPPED_NO_ACCESS
    assert issubclass(GmailNoAccessError, NoAccessError)


def test_failed_pipeline_writes_failed_heartbeat_and_raises():
    events: list[str] = []
    store = FakeStateStore(enabled=True, events=events)

    def failing_pipeline() -> int:
        raise RuntimeError("pipeline blew up")

    # FAILED heartbeat lands, then the exception propagates so App Insights
    # still records a failed invocation (REQ-2.3). Covers the transient case
    # too: a Google blip in the preflight is NOT "needs reconnect" (REQ-1/2) —
    # it propagates as a non-NoAccessError and fails the run.
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
        run_worker(RaisingStateStore(), lambda: 0)


def test_heartbeat_write_error_propagates():
    with pytest.raises(ConnectionError):
        run_worker(HeartbeatRaisingStateStore(), lambda: 0)


def test_heartbeat_write_error_propagates_on_skip_no_access_path():
    # A storage fault inside the skip branch must escape, not be reclassified
    # by any surrounding except (heartbeat writes stay outside every except).
    class EnabledHeartbeatRaisingStore:
        def read_enabled(self) -> bool:
            return True

        def write_heartbeat(self, heartbeat: Heartbeat) -> None:
            raise ConnectionError("storage unreachable")

    def no_access_pipeline() -> int:
        raise GmailNoAccessError("missing_token")

    with pytest.raises(ConnectionError):
        run_worker(EnabledHeartbeatRaisingStore(), no_access_pipeline)


@pytest.mark.parametrize(
    ("enabled", "expected_outcome"),
    [(False, HeartbeatStatus.SKIPPED_DISABLED), (True, HeartbeatStatus.RAN)],
)
def test_wake_logs_exactly_one_outcome_line(caplog, enabled, expected_outcome):
    # Revised from one-line-per-wake (gate X6): the skip path adds a reason
    # line, so the invariant is exactly one `outcome=` line per wake.
    with caplog.at_level(logging.INFO, logger="app.worker"):
        _worker_run(enabled=enabled)
    outcome_lines = [
        r.getMessage()
        for r in caplog.records
        if r.name == "app.worker" and "outcome=" in r.getMessage()
    ]
    assert outcome_lines == [f"{WORKER_RUN_LOG_PREFIX} outcome={expected_outcome.value}"]


def test_failed_wake_logs_one_structured_line(caplog):
    def failing_pipeline() -> int:
        raise RuntimeError("boom")

    with caplog.at_level(logging.INFO, logger="app.worker"), pytest.raises(RuntimeError):
        run_worker(FakeStateStore(enabled=True, events=[]), failing_pipeline)
    worker_lines = [r.getMessage() for r in caplog.records if r.name == "app.worker"]
    assert worker_lines == [f"{WORKER_RUN_LOG_PREFIX} outcome={HeartbeatStatus.FAILED.value}"]


def test_skip_no_access_logs_exactly_one_outcome_line(caplog):
    store = FakeStateStore(enabled=True, events=[])

    def no_access_pipeline() -> int:
        raise GmailNoAccessError("token_rejected")

    with caplog.at_level(logging.INFO, logger="app.worker"):
        run_worker(store, no_access_pipeline)
    outcome_lines = [
        r.getMessage()
        for r in caplog.records
        if r.name == "app.worker" and "outcome=" in r.getMessage()
    ]
    assert outcome_lines == [
        f"{WORKER_RUN_LOG_PREFIX} outcome={HeartbeatStatus.SKIPPED_NO_ACCESS.value}"
    ]


def test_run_wake_delegates_to_the_pipeline_entry_point(monkeypatch):
    # REQ-6 2nd amendment (2026-08-21): run_wake ≡ run_worker(store, run_pipeline)
    # — the pipeline owns its Gmail client; nothing Gmail-side exists at this
    # layer (the fresh-client/close guards live in test_pipeline_entry.py).
    from app.worker import run_wake

    monkeypatch.setattr(
        "app.worker.run_pipeline", lambda: RunCounts(processed=2, failed=0, failed_total=1)
    )
    store = FakeStateStore(enabled=True, events=[])
    assert run_wake(store) == HeartbeatStatus.RAN
    (heartbeat,) = store.heartbeats
    assert (heartbeat.processed, heartbeat.failed, heartbeat.failed_total) == (2, 0, 1)


def test_scheduled_worker_uses_cached_accessor(monkeypatch):
    # worker-controls REQ-6.2: the timer composes via get_state_store() (shared,
    # cached) — supersedes per-wake construction.
    events: list[str] = []
    store = FakeStateStore(enabled=False, events=events)
    monkeypatch.setattr("app.worker.get_state_store", lambda: store)
    run_scheduled_worker()
    assert [hb.status for hb in store.heartbeats] == [HeartbeatStatus.SKIPPED_DISABLED]


def test_trello_no_access_logs_its_source(caplog):
    # REQ-3: the skip line says WHICH workload lost access (gmail vs trello).
    from pipeline.trello_client import TrelloNoAccessError

    store = FakeStateStore(enabled=True, events=[])

    def trello_dead_pipeline() -> RunCounts:
        raise TrelloNoAccessError("missing_config")

    with caplog.at_level(logging.INFO, logger="app.worker"):
        outcome = run_worker(store, trello_dead_pipeline)
    assert outcome == HeartbeatStatus.SKIPPED_NO_ACCESS
    lines = [r.getMessage() for r in caplog.records if r.name == "app.worker"]
    assert (
        f"{WORKER_RUN_LOG_PREFIX} no_access source=TrelloNoAccessError reason=missing_config"
        in lines
    )


def test_run_busy_writes_skipped_busy_and_returns(caplog):
    # REQ-12: a held lease is a clean exit — heartbeat written, nothing raised.
    store = FakeStateStore(enabled=True, events=[])

    def busy_pipeline() -> RunCounts:
        raise RunBusyError("lease held")

    with caplog.at_level(logging.INFO, logger="app.worker"):
        outcome = run_worker(store, busy_pipeline)
    assert outcome == HeartbeatStatus.SKIPPED_BUSY
    (heartbeat,) = store.heartbeats
    assert heartbeat.status == HeartbeatStatus.SKIPPED_BUSY
    outcome_lines = [
        r.getMessage()
        for r in caplog.records
        if r.name == "app.worker" and "outcome=" in r.getMessage()
    ]
    assert outcome_lines == [f"{WORKER_RUN_LOG_PREFIX} outcome=skipped_busy"]
