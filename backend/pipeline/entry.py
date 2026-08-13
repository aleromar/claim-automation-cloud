"""Pipeline entry point — stub (worker-skeleton REQ-3).

Roadmap item 5 (pipeline-port) replaces this body with the real
Gmail → claim → PDF → Trello pipeline; the signature stays.
"""

import logging

logger = logging.getLogger(__name__)


def run_pipeline() -> None:
    logger.info("pipeline stub invoked — real pipeline arrives with roadmap item 5")
