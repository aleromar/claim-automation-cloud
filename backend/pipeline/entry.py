"""Pipeline entry point — read-only Gmail probe (gmail-client REQ-3).

Replaces the worker-skeleton stub: list the newest UNREAD page, count the
claim-subject matches, touch nothing. Roadmap 5c swaps this body for the real
processing pipeline; the reader-injection wiring stands. The GmailReader
protocol keeps pipeline/ free of app imports (dependency inversion, same
pattern as MembreteSource).
"""

import logging
from time import monotonic
from typing import Final, Protocol

from pipeline.claim_data import CLAIM_SUBJECT_MARKERS

logger = logging.getLogger(__name__)

# Wall-clock cap (gate E1): the Functions Consumption default functionTimeout is
# 5 min — a degraded Gmail must fail the run while the heartbeat can still land,
# and process-now must stay inside Azure's ~230 s HTTP idle limit.
PROBE_DEADLINE_S: Final = 120.0
# Same queryable prefix app.worker uses; duplicated literal because pipeline/
# must not import app (App Insights: traces | where message startswith this).
_PROBE_LOG_PREFIX: Final = "worker_run probe"


class GmailReader(Protocol):
    def preflight(self) -> None: ...

    def list_unread_message_ids(self) -> list[str]: ...

    def get_subject(self, message_id: str) -> str: ...


def run_pipeline(gmail: GmailReader) -> int:
    """One read-only probe run: count of claim-subject-matching UNREAD emails.

    The preflight is the body's first step (REQ-2 amendment, 2026-08-21): its
    NoAccessError escapes for the scheduler to classify as `skipped_no_access`.
    The count is an upper bound on processable claims: a marker match doesn't
    guarantee the `YYYY/N` claim number or (for asistencia) a recognized
    service in the body — 5c's processing owns those failures.
    """
    gmail.preflight()
    deadline = monotonic() + PROBE_DEADLINE_S
    message_ids = gmail.list_unread_message_ids()
    matched = 0
    for position, message_id in enumerate(message_ids, start=1):
        if monotonic() > deadline:
            # The sole degraded-Gmail breadcrumb: report actual progress.
            raise TimeoutError(
                f"probe exceeded its {int(PROBE_DEADLINE_S)} s deadline "
                f"at message {position} of {len(message_ids)}"
            )
        subject = gmail.get_subject(message_id)
        if any(marker in subject for marker in CLAIM_SUBJECT_MARKERS):
            matched += 1
    logger.info("%s matched=%d scanned=%d", _PROBE_LOG_PREFIX, matched, len(message_ids))
    return matched
