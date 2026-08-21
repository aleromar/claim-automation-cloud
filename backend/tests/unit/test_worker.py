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

from app.state_store import Heartbeat, HeartbeatStatus
from app.worker import WORKER_RUN_LOG_PREFIX, run_scheduled_worker, run_worker
from core.exceptions import NoAccessError
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

    def pipeline() -> int:
        events.append("pipeline")
        pipeline_calls.append("called")
        return matched

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


def test_matched_count_lands_on_the_ran_heartbeat():
    _, store, _, _ = _worker_run(enabled=True, matched=3)
    (heartbeat,) = store.heartbeats
    assert heartbeat.matched == 3


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
    assert f"{WORKER_RUN_LOG_PREFIX} gmail_no_access reason=missing_token" in lines


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


class CountingFakeGmailClient:
    """run_wake's composition seam (gmail-client REQ-6): fresh instance per call."""

    instances: list["CountingFakeGmailClient"] = []

    def __init__(self, settings, secret_store) -> None:
        self.closed = False
        CountingFakeGmailClient.instances.append(self)

    def preflight(self) -> None:
        pass

    def list_unread_message_ids(self) -> list[str]:
        return []

    def get_subject(self, message_id: str) -> str:  # pragma: no cover
        return ""

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def fake_gmail(monkeypatch):
    CountingFakeGmailClient.instances = []
    monkeypatch.setattr("app.worker.GmailClient", CountingFakeGmailClient)
    monkeypatch.setattr("app.worker.get_store", lambda: object())
    return CountingFakeGmailClient


def test_run_wake_builds_a_fresh_client_per_call_and_closes_it(monkeypatch, fake_gmail):
    from app.worker import run_wake

    store = FakeStateStore(enabled=True, events=[])
    run_wake(store)
    run_wake(store)
    assert len(fake_gmail.instances) == 2
    assert fake_gmail.instances[0] is not fake_gmail.instances[1]
    assert all(instance.closed for instance in fake_gmail.instances)


def test_run_wake_closes_client_even_when_pipeline_fails(monkeypatch, fake_gmail):
    from app.worker import run_wake

    class FailingListClient(CountingFakeGmailClient):
        def list_unread_message_ids(self) -> list[str]:
            raise ConnectionError("gmail down")

    monkeypatch.setattr("app.worker.GmailClient", FailingListClient)
    store = FakeStateStore(enabled=True, events=[])
    with pytest.raises(ConnectionError):
        run_wake(store)
    assert fake_gmail.instances[-1].closed


def test_run_wake_disabled_constructs_client_without_side_effects(fake_gmail):
    # The constructor is I/O-free (gate E3), so building it pre-gate is safe;
    # what matters is that no preflight/list ever runs on the disabled path —
    # covered by the events assertion in the disabled test above. Here: the
    # instance still gets closed.
    from app.worker import run_wake

    store = FakeStateStore(enabled=False, events=[])
    assert run_wake(store) == HeartbeatStatus.SKIPPED_DISABLED
    assert fake_gmail.instances[-1].closed


def test_scheduled_worker_uses_cached_accessor(monkeypatch, fake_gmail):
    # worker-controls REQ-6.2: the timer composes via get_state_store() (shared,
    # cached) — supersedes per-wake construction.
    events: list[str] = []
    store = FakeStateStore(enabled=False, events=events)
    monkeypatch.setattr("app.worker.get_state_store", lambda: store)
    run_scheduled_worker()
    assert [hb.status for hb in store.heartbeats] == [HeartbeatStatus.SKIPPED_DISABLED]
