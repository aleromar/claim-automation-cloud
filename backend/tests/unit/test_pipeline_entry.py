"""The stub pipeline entry point (worker-skeleton REQ-3) — item 5 replaces the body."""

import logging

from pipeline.entry import run_pipeline


def test_run_pipeline_is_a_logged_noop(caplog):
    with caplog.at_level(logging.INFO, logger="pipeline.entry"):
        assert run_pipeline() is None
    assert any("pipeline stub" in r.getMessage() for r in caplog.records)
