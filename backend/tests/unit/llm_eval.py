"""LLM eval harness (llm-extraction REQ-6) — pydantic-evals inside pytest.

Shared by the three tiers: Tier 1 (FunctionModel, test_claim_email_fixtures),
Tier 2 (cassette replay) and Tier 3 (tests/live, nightly). The extractions are
PRECOMPUTED on the test thread — the production call shape — and the Dataset
evaluates a trivial lookup task (Task 0f: an async task calling run_sync fails).

Gate = the asymmetric per-(case, field) rule (groundwork §8.6): a pair listed
in `llm_known_failures` must still fail (a new pass is locked in by editing the
manifest), a pair not listed must pass. Never a score. Regex is the permanent
comparison column; discordant pairs (one right, one wrong) are reported with
their direction — six same-direction pairs is the significance floor.
"""

import json
import os
import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import FunctionModel
from pydantic_evals import Case, Dataset
from pydantic_evals.evaluators import Evaluator, EvaluatorContext
from pydantic_evals.reporting import EvaluationReport

from core.config import Settings
from pipeline.claim_data import ClaimType
from pipeline.extraction import (
    CLAIM_VALUE_FIELDS,
    EXTRACTOR_LLM,
    FieldExtractor,
    RegexFieldExtractor,
)
from pipeline.llm_extraction import SUBJECT_CLOSE, SUBJECT_OPEN, LlmFieldExtractor

CASES_DIR = Path(__file__).parent.parent / "data" / "claim_emails"
MANIFEST = json.loads((CASES_DIR / "cases.json").read_text(encoding="utf-8"))
CASES: list[dict] = MANIFEST["cases"]

LLM_KNOWN_FAILURES = "llm_known_failures"
REGEX_KNOWN_FAILURES = "regex_known_failures"

# Input arms (REQ-6.5, D-c): `plain` is the production path (converter output ≈
# the .txt part); `xhtml_stripped` exists ONLY here as a switchable eval input.
ARM_PLAIN = "plain"
ARM_XHTML_STRIPPED = "xhtml_stripped"
ARMS = (ARM_PLAIN, ARM_XHTML_STRIPPED)
ARM_ENV_VAR = "LLM_EVAL_ARM"

FOUNDRY_ENDPOINT_PLACEHOLDER = "https://foundry.example/openai/v1/"

_PRESENTATION_ATTRS = re.compile(r'\s+(?:class|style|id|lang|dir|xml:[\w-]+)="[^"]*"')


class Outcome(StrEnum):
    """Per-(case, field) result under the manifest's comparison rule."""

    MATCH = "match"
    OMISSION = "omission"  # gold has a value, the extractor returned None
    HALLUCINATION = "hallucination"  # gold is None, the extractor returned a value
    MISMATCH = "mismatch"  # both present, different


def part(case: dict, ext: str) -> str:
    return (CASES_DIR / f"{case['slug']}.{ext}").read_text(encoding="utf-8")


def normalize(value: str | None) -> str | None:
    """Whitespace-collapsed, direction marks stripped (cases.json field_rules.comparison)."""
    if value is None:
        return None
    return re.sub(r"\s+", " ", value.replace("‎", "")).strip()


def strip_attributes(html: str) -> str:
    """Arm (b′): the Gmail-rewritten HTML part minus class/style/id/lang/dir/xml:* attributes."""
    return _PRESENTATION_ATTRS.sub("", html)


def arm_from_env() -> str:
    arm = os.environ.get(ARM_ENV_VAR, ARM_PLAIN)
    if arm not in ARMS:
        raise ValueError(f"{ARM_ENV_VAR}={arm!r}; expected one of {ARMS}")
    return arm


def case_body(case: dict, arm: str) -> str:
    if arm == ARM_PLAIN:
        return part(case, "txt")
    if arm == ARM_XHTML_STRIPPED:
        return strip_attributes(part(case, "html"))
    raise ValueError(f"Unknown eval arm: {arm!r}")


def outcome(got: str | None, want: str | None) -> Outcome:
    got, want = normalize(got), normalize(want)
    if got == want:
        return Outcome.MATCH
    if got is None:
        return Outcome.OMISSION
    if want is None:
        return Outcome.HALLUCINATION
    return Outcome.MISMATCH


@dataclass
class EvalInputs:
    slug: str
    subject: str
    claim_type: str  # ClaimType.name
    body: str


@dataclass
class EvalMeta:
    slug: str
    llm_known_failures: list[str]
    regex_known_failures: list[str]


ValueFields = dict[str, str | None]


def gold_value_fields(case: dict) -> ValueFields:
    # Value fields only — never `extractor_used` (REQ-6.1).
    return {name: case["gold"][name] for name in CLAIM_VALUE_FIELDS}


@dataclass
class PerFieldOutcome(Evaluator[EvalInputs, ValueFields, EvalMeta]):
    """One label per value field: match | omission | hallucination | mismatch."""

    def evaluate(self, ctx: EvaluatorContext[EvalInputs, ValueFields, EvalMeta]) -> dict[str, str]:
        expected = ctx.expected_output or {}
        return {
            name: outcome(ctx.output.get(name), expected.get(name)).value
            for name in CLAIM_VALUE_FIELDS
        }


def build_dataset(cases: list[dict], arm: str) -> Dataset[EvalInputs, ValueFields, EvalMeta]:
    return Dataset(
        name=f"claim_emails[{arm}]",
        cases=[
            Case(
                name=case["slug"],
                inputs=EvalInputs(
                    slug=case["slug"],
                    subject=case["subject"],
                    claim_type=case["claim_type"],
                    body=case_body(case, arm),
                ),
                expected_output=gold_value_fields(case),
                metadata=EvalMeta(
                    slug=case["slug"],
                    llm_known_failures=list(case[LLM_KNOWN_FAILURES]),
                    regex_known_failures=list(case[REGEX_KNOWN_FAILURES]),
                ),
            )
            for case in cases
        ],
        evaluators=[PerFieldOutcome()],
    )


def extract_case(extractor: FieldExtractor, case: dict, body: str) -> ValueFields:
    fields = extractor.extract(ClaimType[case["claim_type"]], case["subject"], body, body)
    return {name: getattr(fields, name) for name in CLAIM_VALUE_FIELDS}


def run_eval(extractor: FieldExtractor, cases: list[dict], arm: str) -> EvaluationReport:
    """Precompute every extraction on this thread, then evaluate a lookup task."""
    outputs = {case["slug"]: extract_case(extractor, case, case_body(case, arm)) for case in cases}
    return build_dataset(cases, arm).evaluate_sync(
        lambda inputs: outputs[inputs.slug], progress=False
    )


Outcomes = dict[str, dict[str, Outcome]]  # slug → field → outcome


def outcomes_by_case(report: EvaluationReport) -> Outcomes:
    if report.failures:
        raise AssertionError(f"eval task failed for: {[f.name for f in report.failures]}")
    return {
        case.name: {name: Outcome(case.labels[name].value) for name in CLAIM_VALUE_FIELDS}
        for case in report.cases
    }


def assert_asymmetric_gate(
    outcomes: Outcomes, cases: list[dict], known_key: str = LLM_KNOWN_FAILURES
) -> None:
    """Listed ⇒ must still fail; unlisted ⇒ must pass. Raises with every violation."""
    problems: list[str] = []
    for case in cases:
        slug = case["slug"]
        for name in CLAIM_VALUE_FIELDS:
            result = outcomes[slug][name]
            failed = result is not Outcome.MATCH
            listed = name in case[known_key]
            if listed and not failed:
                problems.append(
                    f"{slug}: {name!r} now passes — remove it from {known_key} in cases.json "
                    "so the pass is locked in"
                )
            elif failed and not listed:
                problems.append(f"{slug}: {name} {result.value} — not listed in {known_key}")
    if problems:
        raise AssertionError("\n".join(problems))


def regex_column(cases: list[dict]) -> Outcomes:
    """The permanent comparison column (REQ-6.3), always on regex's production input."""
    regex = RegexFieldExtractor()
    return {
        case["slug"]: {
            name: outcome(got, case["gold"][name])
            for name, got in extract_case(regex, case, part(case, "txt")).items()
        }
        for case in cases
    }


def discordant_pairs(llm: Outcomes, regex: Outcomes) -> list[tuple[str, str, str]]:
    """(slug, field, favours) where exactly one extractor is right."""
    pairs = []
    for slug, per_field in llm.items():
        for name, result in per_field.items():
            llm_right = result is Outcome.MATCH
            regex_right = regex[slug][name] is Outcome.MATCH
            if llm_right != regex_right:
                pairs.append((slug, name, "llm" if llm_right else "regex"))
    return pairs


def render_report(report: EvaluationReport, llm: Outcomes, regex: Outcomes, arm: str) -> str:
    """Markdown for stdout / $GITHUB_STEP_SUMMARY: outcomes and counts, never values."""
    slugs = list(llm)
    lines = [
        f"## LLM extraction eval — arm `{arm}`, {len(slugs)} cases, dataset `{report.name}`",
        "",
        "| field | llm match | regex match | agreement |",
        "|---|---|---|---|",
    ]
    for name in CLAIM_VALUE_FIELDS:
        llm_ok = sum(llm[s][name] is Outcome.MATCH for s in slugs)
        regex_ok = sum(regex[s][name] is Outcome.MATCH for s in slugs)
        agree = sum(
            (llm[s][name] is Outcome.MATCH) == (regex[s][name] is Outcome.MATCH) for s in slugs
        )
        lines.append(
            f"| {name} | {llm_ok}/{len(slugs)} | {regex_ok}/{len(slugs)} | {agree}/{len(slugs)} |"
        )
    lines += ["", "### Non-match outcomes (llm)", ""]
    misses = [(s, n, r.value) for s in slugs for n, r in llm[s].items() if r is not Outcome.MATCH]
    lines += [f"- {s}: {n} → {r}" for s, n, r in misses] or ["- none"]
    pairs = discordant_pairs(llm, regex)
    lines += [
        "",
        f"### Discordant pairs ({len(pairs)}; six same-direction = significance floor)",
        "",
    ]
    lines += [f"- {s}: {n} favours **{who}**" for s, n, who in pairs] or ["- none"]
    return "\n".join(lines)


def write_step_summary(text: str) -> None:
    """Append to the GitHub job summary when running in Actions (Tier 3); no-op locally."""
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as summary:
            summary.write(text + "\n")


def llm_settings(endpoint: str = FOUNDRY_ENDPOINT_PLACEHOLDER, **overrides: Any) -> Settings:
    return Settings(field_extractor_backend=EXTRACTOR_LLM, foundry_endpoint=endpoint, **overrides)


async def dummy_token() -> str:
    """Replay/Tier-1 token: no credential is ever constructed (REQ-6.4)."""
    return "DUMMY"


def subject_of(messages) -> str:
    """The subject the model was shown, read back from the delimited user message."""
    content = messages[0].parts[-1].content
    return content.split(SUBJECT_OPEN, 1)[1].split(SUBJECT_CLOSE, 1)[0].strip()


def gold_answering_extractor(cases: list[dict]) -> LlmFieldExtractor:
    """Tier-1 extractor whose model answers each case's gold (harness bug ⇒ red)."""
    by_subject = {case["subject"]: case for case in cases}

    def answer(messages, info) -> ModelResponse:
        case = by_subject[subject_of(messages)]
        allowed = info.model_request_parameters.output_object.json_schema["properties"]
        body = {k: v for k, v in case["gold"].items() if k in allowed}
        return ModelResponse(parts=[TextPart(json.dumps(body))], finish_reason="stop")

    return LlmFieldExtractor(llm_settings(), token=dummy_token, model=FunctionModel(answer))
