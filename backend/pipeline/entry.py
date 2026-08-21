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
    def list_unread_message_ids(self) -> list[str]: ...

    def get_subject(self, message_id: str) -> str: ...


def run_pipeline(gmail: GmailReader) -> int:
    """One read-only probe run: count of claim-subject-matching UNREAD emails."""
    deadline = monotonic() + PROBE_DEADLINE_S
    message_ids = gmail.list_unread_message_ids()
    matched = 0
    for message_id in message_ids:
        if monotonic() > deadline:
            raise TimeoutError(
                f"probe exceeded its {int(PROBE_DEADLINE_S)} s deadline "
                f"after {len(message_ids)} listed messages"
            )
        subject = gmail.get_subject(message_id)
        if any(marker in subject for marker in CLAIM_SUBJECT_MARKERS):
            matched += 1
    logger.info("%s matched=%d scanned=%d", _PROBE_LOG_PREFIX, matched, len(message_ids))
    return matched
