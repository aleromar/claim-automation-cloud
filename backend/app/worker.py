"""Scheduled worker wake logic (worker-skeleton spec; gmail-client REQ-2/6; D4/D5).

One wake: read the `enabled` flag first (missing row reads as OFF — fail-safe,
enforced by the state store), run the Gmail token preflight, invoke the pipeline
only when both pass, and write a heartbeat LAST on every exit path. The
heartbeat is the run *report* (operator decision 2026-08-12): `ran` = completed
(carrying the probe's matched count), `failed` = preflight-transient or pipeline
raised (heartbeat written, then re-raised so App Insights also records the
failure), `skipped_disabled` = gate exit, `skipped_no_access` = definitive
credential failure at preflight (gmail-client, 2026-08-21).

Sync on purpose: the table client is sync and the Python worker runs plain-`def`
functions off the host's event loop (state-store consumer constraint).
"""

import logging
from collections.abc import Callable
from datetime import UTC, datetime

from core.config import get_settings
from pipeline.gmail_client import GmailClient, GmailNoAccessError
from core.secret_store import get_store
from app.state_store import Heartbeat, HeartbeatStatus, StateStore, get_state_store
from pipeline.entry import run_pipeline

logger = logging.getLogger(__name__)

WORKER_FUNCTION_NAME = "worker"
WORKER_TIMER_SCHEDULE = "0 */30 * * * *"  # NCRONTAB (6-field): second 0, every 30th minute (D5)
WORKER_RUN_LOG_PREFIX = "worker_run"  # App Insights: traces | where message startswith this


def run_worker(
    store: StateStore,
    pipeline: Callable[[], int],
    preflight: Callable[[], None],
) -> HeartbeatStatus:
    """Execute one wake. Storage faults propagate — the host records a failed
    invocation (no swallow-and-continue). GmailNoAccessError is classified as
    a skip ONLY when raised by the preflight call itself (gate E5)."""
    if not store.read_enabled():
        _report(store, HeartbeatStatus.SKIPPED_DISABLED)
        return HeartbeatStatus.SKIPPED_DISABLED
    try:
        preflight()
    except GmailNoAccessError as exc:
        logger.warning("%s gmail_no_access reason=%s", WORKER_RUN_LOG_PREFIX, exc.reason)
        _report(store, HeartbeatStatus.SKIPPED_NO_ACCESS)
        return HeartbeatStatus.SKIPPED_NO_ACCESS
    except Exception:
        # Transient token-endpoint failure: says nothing about token health —
        # a failed run, retried by the next wake (REQ-1/2).
        _report(store, HeartbeatStatus.FAILED)
        raise
    try:
        matched = pipeline()
    except Exception:
        _report(store, HeartbeatStatus.FAILED)
        raise
    _report(store, HeartbeatStatus.RAN, matched)
    return HeartbeatStatus.RAN


def _report(store: StateStore, outcome: HeartbeatStatus, matched: int | None = None) -> None:
    """The end-of-run heartbeat + its one queryable outcome log line."""
    store.write_heartbeat(Heartbeat(at=datetime.now(UTC), status=outcome, matched=matched))
    logger.info("%s outcome=%s", WORKER_RUN_LOG_PREFIX, outcome.value)


def run_wake(store: StateStore) -> HeartbeatStatus:
    """The single composition point of wake + preflight + probe (PR #15 review
    M2; gmail-client REQ-6): the timer and the process-now endpoint both call
    this. A fresh GmailClient per wake — nothing Gmail-side is shared across
    threads (P12 by non-sharing); the client does no secret reads and builds
    its HTTP client lazily (gates E3/M7), so constructing it ahead of the
    enabled gate costs nothing. 5c swaps run_pipeline's *body* in
    pipeline/entry.py; this wiring stands."""
    gmail = GmailClient(get_settings(), get_store())
    try:
        return run_worker(store, lambda: run_pipeline(gmail), gmail.preflight)
    finally:
        gmail.close()


def run_scheduled_worker() -> None:
    """Composition root for the timer trigger: cached store → run_wake.

    Returns nothing: a timer invocation has no caller to answer (decided
    2026-08-12). The process-now endpoint (worker_routes.py) calls run_wake()
    with its request-scoped store — same wake path (gate honored, heartbeat
    written), with the returned outcome going into its HTTP response. Both
    compose the store via get_state_store() (worker-controls REQ-6.2).
    """
    run_wake(get_state_store())
