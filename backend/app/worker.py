"""Scheduled worker wake logic (worker-skeleton spec; gmail-client REQ-2/6; D4/D5).

One wake: read the `enabled` flag first (missing row reads as OFF — fail-safe,
enforced by the state store), invoke the pipeline — whose first step is the
Gmail token preflight (REQ-2 amendment, 2026-08-21) — and write a heartbeat
LAST on every exit path. The heartbeat is the run *report* (operator decision
2026-08-12): `ran` = completed (carrying the probe's matched count), `failed` =
pipeline raised (heartbeat written, then re-raised so App Insights also records
the failure), `skipped_disabled` = gate exit, `skipped_no_access` = the
pipeline raised core NoAccessError (definitive credential failure).

Sync on purpose: the table client is sync and the Python worker runs plain-`def`
functions off the host's event loop (state-store consumer constraint).
"""

import logging
from collections.abc import Callable
from datetime import UTC, datetime

from core.exceptions import NoAccessError, RunBusyError
from core.state_store import Heartbeat, HeartbeatStatus, RunCounts, StateStore, get_state_store
from pipeline.entry import run_pipeline

logger = logging.getLogger(__name__)

WORKER_FUNCTION_NAME = "worker"
WORKER_TIMER_SCHEDULE = "0 */30 * * * *"  # NCRONTAB (6-field): second 0, every 30th minute (D5)
WORKER_RUN_LOG_PREFIX = "worker_run"  # App Insights: traces | where message startswith this


def run_worker(
    store: StateStore,
    pipeline: Callable[[], RunCounts],
) -> HeartbeatStatus:
    """Execute one wake. Storage faults propagate — the host records a failed
    invocation (no swallow-and-continue). The core wake contract exceptions
    classify the exits: NoAccessError (any workload's preflight) → skip;
    RunBusyError (lease held) → busy; a storage fault inside a skip branch
    still escapes (an except clause never catches what its sibling raises)."""
    if not store.read_enabled():
        _report(store, HeartbeatStatus.SKIPPED_DISABLED)
        return HeartbeatStatus.SKIPPED_DISABLED
    try:
        counts = pipeline()
    except RunBusyError:
        # Another invocation holds the run lease (pipeline-wiring REQ-12):
        # clean exit, nothing was touched.
        _report(store, HeartbeatStatus.SKIPPED_BUSY)
        return HeartbeatStatus.SKIPPED_BUSY
    except NoAccessError as exc:
        # The source type says WHICH workload lost access (gmail vs trello).
        logger.warning(
            "%s no_access source=%s reason=%s",
            WORKER_RUN_LOG_PREFIX,
            type(exc).__name__,
            exc.reason,
        )
        _report(store, HeartbeatStatus.SKIPPED_NO_ACCESS)
        return HeartbeatStatus.SKIPPED_NO_ACCESS
    except Exception:
        # Anything else — transient token-endpoint failure included — says
        # nothing about token health: a failed run, retried by the next wake
        # (REQ-1/2).
        _report(store, HeartbeatStatus.FAILED)
        raise
    _report(store, HeartbeatStatus.RAN, counts)
    return HeartbeatStatus.RAN


def _report(store: StateStore, outcome: HeartbeatStatus, counts: RunCounts | None = None) -> None:
    """The end-of-run heartbeat + its one queryable outcome log line."""
    store.write_heartbeat(
        Heartbeat(
            at=datetime.now(UTC),
            status=outcome,
            processed=counts.processed if counts else None,
            failed=counts.failed if counts else None,
            failed_total=counts.failed_total if counts else None,
        )
    )
    logger.info("%s outcome=%s", WORKER_RUN_LOG_PREFIX, outcome.value)


def run_wake(store: StateStore) -> HeartbeatStatus:
    """The single composition point of the wake (PR #15 review M2): the timer
    and the process-now endpoint both call this. The pipeline owns its Gmail
    client (REQ-6, 2nd amendment 2026-08-21) — nothing Gmail-side exists at
    this layer, and a disabled wake constructs nothing. 5c swaps run_pipeline's
    *body* in pipeline/entry.py; this wiring stands."""
    return run_worker(store, run_pipeline)


def run_scheduled_worker() -> None:
    """Composition root for the timer trigger: cached store → run_wake.

    Returns nothing: a timer invocation has no caller to answer (decided
    2026-08-12). The process-now endpoint (worker_routes.py) calls run_wake()
    with its request-scoped store — same wake path (gate honored, heartbeat
    written), with the returned outcome going into its HTTP response. Both
    compose the store via get_state_store() (worker-controls REQ-6.2).
    """
    run_wake(get_state_store())
