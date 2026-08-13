"""Scheduled worker wake logic (worker-skeleton spec; D4/D5).

One wake: read the `enabled` flag first (missing row reads as OFF — fail-safe,
enforced by the state store), invoke the pipeline only when ON, and write a
heartbeat LAST on every exit path. The heartbeat is the run *report* (operator
decision 2026-08-12): `ran` = completed, `failed` = pipeline raised (heartbeat
written, then re-raised so App Insights also records the failure),
`skipped_disabled` = gate exit. Item 5's token preflight adds its
`skipped-no-access` exit the same way — heartbeat at run end.

Sync on purpose: the table client is sync and the Python worker runs plain-`def`
functions off the host's event loop (state-store consumer constraint).
"""

import logging
from collections.abc import Callable
from datetime import UTC, datetime

from app.config import get_settings
from app.state_store import Heartbeat, HeartbeatStatus, StateStore, state_store_from_settings
from pipeline.entry import run_pipeline

logger = logging.getLogger(__name__)

WORKER_FUNCTION_NAME = "worker"
WORKER_TIMER_SCHEDULE = "0 */30 * * * *"  # NCRONTAB (6-field): second 0, every 30th minute (D5)
WORKER_RUN_LOG_PREFIX = "worker_run"  # App Insights: traces | where message startswith this


def run_worker(store: StateStore, pipeline: Callable[[], None]) -> HeartbeatStatus:
    """Execute one wake. Storage faults propagate — the host records a failed
    invocation (no swallow-and-continue)."""
    if store.read_enabled():
        try:
            pipeline()
        except Exception:
            _report(store, HeartbeatStatus.FAILED)
            raise
        outcome = HeartbeatStatus.RAN
    else:
        outcome = HeartbeatStatus.SKIPPED_DISABLED
    _report(store, outcome)
    return outcome


def _report(store: StateStore, outcome: HeartbeatStatus) -> None:
    """The end-of-run heartbeat + its one queryable log line."""
    store.write_heartbeat(Heartbeat(at=datetime.now(UTC), status=outcome))
    logger.info("%s outcome=%s", WORKER_RUN_LOG_PREFIX, outcome.value)


def run_scheduled_worker() -> None:
    """Composition root for the timer trigger: settings → store → stub pipeline.

    Returns nothing: a timer invocation has no caller to answer (decided
    2026-08-12). Item 3's process-now endpoint instead calls run_worker()
    directly — same wake path (gate honored, heartbeat written), with the
    returned outcome going into its HTTP response.
    """
    run_worker(state_store_from_settings(get_settings()), run_pipeline)
