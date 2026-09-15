"""Tier 3 — nightly, live, reporting-only (llm-extraction REQ-6.4).

Runs the five anonymized fixtures through the production call path against the
staging deployment with a REAL credential (the same DefaultAzureCredential the
Function App uses), then writes the per-field report, the regex comparison
column and the discordant pairs to stdout and to `$GITHUB_STEP_SUMMARY`.

It gates nothing: no deploy or merge depends on it. A red run is a signal to
look — model-side drift, a broken grant, a rate limit — not a block. The
summary is written BEFORE the assertions so it lands on red runs too.

Green means "nothing known broke" (see test_claim_email_fixtures.py); five
emails cannot say the extractor is good.
"""

import os
import time
from time import perf_counter

import pytest
from llm_eval import (
    CASES,
    arm_from_env,
    assert_asymmetric_gate,
    discordant_pairs,
    llm_settings,
    outcomes_by_case,
    regex_column,
    render_report,
    run_eval,
    write_step_summary,
)

from pipeline.extraction import ExtractorUsed
from pipeline.llm_extraction import LlmFieldExtractor

pytestmark = pytest.mark.llm_live

ENDPOINT_VAR = "FOUNDRY_ENDPOINT"
# Seconds between calls. Capacity 10 = 10 requests/minute (Task 0a-http); 8 s
# keeps a five-case run under that until the infra PR raises capacity to 50.
PACE_S = 8.0


class PacedProvenance:
    """Production extractor, paced between calls, recording path + latency per call."""

    def __init__(self, inner: LlmFieldExtractor) -> None:
        self.inner = inner
        self.used: list[ExtractorUsed | None] = []
        self.latency_s: list[float] = []

    def extract(self, claim_type, subject, body, raw_body):
        if self.used:
            time.sleep(PACE_S)
        started = perf_counter()
        fields = self.inner.extract(claim_type, subject, body, raw_body)
        self.latency_s.append(perf_counter() - started)
        self.used.append(fields.extractor_used)
        return fields


@pytest.fixture(scope="module")
def live_extractor() -> LlmFieldExtractor:
    endpoint = os.environ.get(ENDPOINT_VAR)
    if not endpoint:
        pytest.fail(f"{ENDPOINT_VAR} is required for the live tier (staging /openai/v1/ host)")
    extractor = LlmFieldExtractor(llm_settings(endpoint))  # production credential path
    yield extractor
    extractor.close()


def test_live_extraction_report(live_extractor):
    arm = arm_from_env()
    tracked = PacedProvenance(live_extractor)
    report = run_eval(tracked, CASES, arm)
    llm = outcomes_by_case(report)
    regex = regex_column(CASES)
    text = render_report(report, llm, regex, arm)
    latency_lines = [
        f"- {case['slug']}: {latency:.2f} s ({used})"
        for case, latency, used in zip(CASES, tracked.latency_s, tracked.used, strict=True)
    ]
    text += "\n\n### Latency per call (extract(), includes token acquisition)\n\n" + "\n".join(
        latency_lines
    )
    print(text)
    write_step_summary(text)
    # Signals, not gates: a fallback means the call itself failed (grant, quota,
    # timeout); a gate violation means the model's answers moved.
    assert tracked.used == [ExtractorUsed.LLM] * len(CASES), tracked.used
    assert_asymmetric_gate(llm, CASES)
    assert discordant_pairs(llm, regex) is not None  # reported above; never asserted on
