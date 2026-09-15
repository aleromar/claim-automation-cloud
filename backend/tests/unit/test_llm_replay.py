"""Tier 2 — the real model's answers, replayed (llm-extraction REQ-6.4).

One cassette per case under tests/cassettes/, recorded ONLY from the anonymized
fixtures with the operator's identity against staging:

    FOUNDRY_ENDPOINT=https://<account>.cognitiveservices.azure.com/openai/v1/ \\
    uv run pytest tests/unit/test_llm_replay.py --record-mode=once

CI runs pytest-recording's default `--record-mode=none`: no socket is opened,
and an unmatched request (an edited prompt or schema changes the body, which
is part of the match) raises instead of replaying a stale answer. Because that
raise reaches the extractor as a connection error, a replay run that FALLS BACK
is a cassette miss — the first assertion below makes it loud.

Green here means "the recorded answers still produce what the manifest
expects" — see test_claim_email_fixtures.py for what green does not mean.
"""

import hashlib
import json
import os
from pathlib import Path

import pytest
import yaml
from llm_eval import (
    ARM_PLAIN,
    CASES,
    assert_asymmetric_gate,
    assert_only_placeholders,
    dummy_token,
    llm_settings,
    outcomes_by_case,
    run_eval,
)

from pipeline.claim_data import ClaimType
from pipeline.extraction import ExtractorUsed
from pipeline.llm_extraction import (
    COGNITIVE_SERVICES_SCOPE,
    MAX_COMPLETION_TOKENS,
    OUTPUT_MODEL_BY_TYPE,
    PROMPT,
    PROMPT_SHA,
    REASONING_EFFORT,
    LlmFieldExtractor,
)

pytestmark = pytest.mark.llm_replay

CASE_IDS = [case["slug"] for case in CASES]
CASSETTES_DIR = Path(__file__).parent.parent / "cassettes"
RECORD_ENDPOINT_VAR = "FOUNDRY_ENDPOINT"
RECORD_MODE_NONE = "none"
CASSETTE_HOST = "foundry.example"
# Anything that would identify the real account in a public repo.
REAL_HOST_MARKERS = ("cognitiveservices.azure.com", "openai.azure.com", "aif-claim")
SCRUBBED_REQUEST_HEADERS = ("authorization", "api-key", "host")
SCRUBBED_RESPONSE_HEADER_PREFIXES = ("x-ms-", "apim-", "azureml-", "azureai-", "x-ratelimit-")


def _cassette_path(case: dict) -> Path:
    # pytest-recording's default naming: the test node id → one file per case.
    return CASSETTES_DIR / f"test_replay[{case['slug']}].yaml"


def _cassette(case: dict) -> dict:
    return yaml.safe_load(_cassette_path(case).read_text(encoding="utf-8"))


class Provenance:
    """Wraps an extractor to record which path produced each result."""

    def __init__(self, inner: LlmFieldExtractor) -> None:
        self.inner = inner
        self.used: list[ExtractorUsed | None] = []

    def extract(self, claim_type, subject, body, raw_body):
        fields = self.inner.extract(claim_type, subject, body, raw_body)
        self.used.append(fields.extractor_used)
        return fields


@pytest.fixture
def replay_extractor(request):
    # pytest-recording leaves the option unset (None) when not given; it treats that as "none".
    if (request.config.getoption("--record-mode") or RECORD_MODE_NONE) == RECORD_MODE_NONE:
        # Replay: the placeholder host is what the scrubbed cassettes carry;
        # no credential is ever constructed (REQ-6.4).
        extractor = LlmFieldExtractor(llm_settings(), token=dummy_token)
    else:
        endpoint = os.environ.get(RECORD_ENDPOINT_VAR)
        if not endpoint:
            pytest.fail(f"recording needs {RECORD_ENDPOINT_VAR} (the staging /openai/v1/ host)")
        from azure.identity import AzureCliCredential, get_bearer_token_provider

        sync_token = get_bearer_token_provider(AzureCliCredential(), COGNITIVE_SERVICES_SCOPE)

        async def operator_token() -> str:
            return sync_token()

        extractor = LlmFieldExtractor(llm_settings(endpoint), token=operator_token)
    yield extractor
    extractor.close()


@pytest.mark.vcr
@pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
def test_replay(case, replay_extractor):
    tracked = Provenance(replay_extractor)
    outcomes = outcomes_by_case(run_eval(tracked, [case], ARM_PLAIN))
    assert tracked.used == [ExtractorUsed.LLM], (
        "fell back to regex under replay — the cassette did not match the current request "
        "(prompt/schema edit?) — re-record it"
    )
    assert_asymmetric_gate(outcomes, [case])


# --- cassette integrity: the recording must describe the CURRENT code ---


@pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
def test_cassette_exists_with_one_interaction(case):
    assert _cassette_path(case).exists(), "record it: see the module docstring"
    interactions = _cassette(case)["interactions"]
    assert len(interactions) == 1  # one attempt, no retries (REQ-3.4)
    assert interactions[0]["response"]["status"]["code"] == 200


@pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
def test_cassette_request_carries_the_current_prompt_and_schema(case):
    body = json.loads(_cassette(case)["interactions"][0]["request"]["body"])
    system_message, user_message = body["messages"]
    assert system_message["role"] in ("system", "developer")
    assert system_message["content"] == PROMPT
    assert hashlib.sha256(system_message["content"].encode()).hexdigest()[:12] == PROMPT_SHA
    assert user_message["role"] == "user"
    output_model = OUTPUT_MODEL_BY_TYPE[ClaimType[case["claim_type"]]]
    json_schema = body["response_format"]["json_schema"]
    assert json_schema["name"] == output_model.__name__
    assert json_schema["strict"] is True
    assert set(json_schema["schema"]["properties"]) == set(output_model.model_fields)
    assert json_schema["schema"]["additionalProperties"] is False
    # The decided settings reached the wire (REQ-2.2 / REQ-3.4).
    assert body["max_completion_tokens"] == MAX_COMPLETION_TOKENS
    assert body["reasoning_effort"] == REASONING_EFFORT
    assert "temperature" not in body
    assert "tools" not in body


@pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
def test_cassette_is_scrubbed_of_infra_identifiers(case):
    text = _cassette_path(case).read_text(encoding="utf-8")
    for marker in REAL_HOST_MARKERS:
        assert marker not in text, marker
    interaction = _cassette(case)["interactions"][0]
    assert interaction["request"]["uri"].startswith(f"https://{CASSETTE_HOST}/")
    request_headers = {name.lower() for name in interaction["request"]["headers"]}
    assert request_headers.isdisjoint(SCRUBBED_REQUEST_HEADERS), request_headers
    response_headers = {name.lower() for name in interaction["response"]["headers"]}
    leaked = {h for h in response_headers if h.startswith(SCRUBBED_RESPONSE_HEADER_PREFIXES)}
    assert leaked == set(), leaked


@pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
def test_cassette_holds_only_placeholders(case):
    # The fixture PII tripwire, over the cassettes too (REQ-6.4).
    assert_only_placeholders(_cassette_path(case).read_text(encoding="utf-8"))


# --- Tier 3 wiring guards: the live tier is opt-in, and the nightly runs only it ---

BACKEND_DIR = Path(__file__).resolve().parents[2]
NIGHTLY_WORKFLOW = BACKEND_DIR.parent / ".github" / "workflows" / "nightly-llm-eval.yml"


def test_live_tier_is_never_collected_by_default():
    """REQ-6.4 T3: `addopts` deselects `llm_live`, so `make test`/CI never call
    Foundry; the nightly opts in with `-m llm_live` (fresh interpreter, default opts)."""
    import subprocess
    import sys

    out = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "tests/live"],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=BACKEND_DIR,
    )
    assert out.returncode in (0, 5), out.stderr  # 5 = "no tests collected" after deselection
    assert "test_llm_live.py::" not in out.stdout, out.stdout
    assert "deselected" in out.stdout, out.stdout


def test_nightly_workflow_runs_only_the_live_tier_against_staging():
    """REQ-6.4 T3: scheduled + dispatchable, `environment: staging` (OIDC subject
    → the app-CI federated credential), Foundry endpoint from a GitHub variable,
    the live marker selected explicitly, and it gates nothing (no `needs`)."""
    workflow = yaml.safe_load(NIGHTLY_WORKFLOW.read_text(encoding="utf-8"))
    triggers = workflow[True] if True in workflow else workflow["on"]  # YAML parses `on` as True
    assert "schedule" in triggers and "workflow_dispatch" in triggers
    assert workflow["permissions"]["id-token"] == "write"
    (job,) = workflow["jobs"].values()
    assert job["environment"] == "staging"
    assert "needs" not in job
    assert job["env"]["FOUNDRY_ENDPOINT"] == "${{ vars.FOUNDRY_ENDPOINT }}"
    steps = job["steps"]
    assert any(step.get("uses", "").startswith("azure/login@") for step in steps)
    run_steps = [step["run"] for step in steps if "run" in step]
    assert any("tests/live" in run and "-m llm_live" in run for run in run_steps), run_steps
