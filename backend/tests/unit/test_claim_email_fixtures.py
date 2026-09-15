"""Real-layout claim-email fixtures (llm-extraction groundwork D-l, §8.1/§8.6).

Five anonymized claim emails under tests/data/claim_emails/ — one asistencia,
two comunicaciones, one NORMAL and one URGENTE siniestro — each stored as the
text/plain and text/html parts of a Gmail forward, with a JSON manifest holding
the subject, expected type and proposed gold ClaimFields (see cases.json for
provenance, the Gmail-forward artifacts and the field rules).

Three jobs here. (1) Tripwire: the fixtures must stay anonymized. (2) Baseline:
the regex extractor's per-(case, field) outcome against gold, with the
asymmetric gate of groundwork §8.6 — a known failure that starts passing must be
recorded, a passing pair that regresses blocks. This is the comparison column an
LLM extractor is measured against. (3) The LLM eval harness (llm-extraction
REQ-6, pydantic-evals inside pytest, helper in llm_eval.py), Tier 1 here under
FunctionModel.

**Read this before trusting a green run.** A green eval means "nothing known broke"
— no (case, field) pair that passed before fails now. It does NOT mean
"the extractor is good": with five emails, a clean sheet leaves a ~60% true
failure rate un-ruled-out (groundwork §8.2, rule of three), and eight fields
per email are correlated, so 40 assertions are not N = 40. There is
no score threshold anywhere in this suite — a score at this N is one fixture
flipping, dressed as a metric.
"""

import base64
import json
import re

import pytest
from llm_eval import (
    ARM_PLAIN,
    ARM_XHTML_STRIPPED,
    ARMS,
    CASES,
    CASES_DIR,
    Outcome,
    assert_asymmetric_gate,
    assert_only_placeholders,
    build_dataset,
    case_body,
    discordant_pairs,
    gold_answering_extractor,
    normalize,
    outcome,
    outcomes_by_case,
    part,
    regex_column,
    render_report,
    run_eval,
    strip_attributes,
)
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import FunctionModel

from core.config import Settings
from pipeline.claim_data import ClaimData, ClaimType
from pipeline.extraction import CLAIM_VALUE_FIELDS, EXTRACTOR_LLM, ClaimFields, RegexFieldExtractor
from pipeline.llm_extraction import SUBJECT_CLOSE, SUBJECT_OPEN, LlmFieldExtractor

CASE_IDS = [case["slug"] for case in CASES]

_part = part
_normalize = normalize


def _make_gmail_message(subject: str, body: str) -> dict:
    encoded_body = base64.urlsafe_b64encode(body.encode("utf-8")).decode("ascii")
    return {
        "payload": {
            "headers": [{"name": "Subject", "value": subject}],
            "parts": [{"mimeType": "text/plain", "body": {"data": encoded_body}}],
        }
    }


def _regex_fields(case: dict, body: str) -> ClaimFields:
    claim_type = ClaimType[case["claim_type"]]
    return RegexFieldExtractor().extract(claim_type, case["subject"], body, body)


# ---------------------------------------------------------------------------
# Tripwire: fixtures stay anonymized
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
def test_both_mime_parts_exist(case):
    assert (CASES_DIR / f"{case['slug']}.txt").exists()
    assert (CASES_DIR / f"{case['slug']}.html").exists()


@pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
def test_no_pii_leaked_back_into_fixture(case):
    """Every identifier-shaped token must be a placeholder. Re-saving a real email
    over a fixture reintroduces real phones/NIFs/addresses and fails here."""
    assert_only_placeholders(_part(case, "txt") + _part(case, "html"))


@pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
def test_gold_values_are_grounded_in_the_text_part(case):
    """The gold set must be readable off the fixture — and doubles as the
    'placeholders present' check of the older XHTML fixture test."""
    text = _normalize(_part(case, "txt"))
    for field, value in case["gold"].items():
        if value is not None:
            assert _normalize(value) in text, (field, value)


# ---------------------------------------------------------------------------
# Classification and identity through the real entry point
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
def test_classifies_and_identifies_as_expected(case):
    claim = ClaimData.from_msg_data(_make_gmail_message(case["subject"], _part(case, "txt")))

    assert claim is not None
    assert claim.type is ClaimType[case["claim_type"]]
    assert claim.year == case["year"]
    assert claim.claim_number == case["claim_number"]


# ---------------------------------------------------------------------------
# Regex baseline: per-(case, field) tripwire with the §8.6 asymmetric gate
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "case,field",
    [(case, field) for case in CASES for field in CLAIM_VALUE_FIELDS],
    ids=[f"{case['slug']}-{field}" for case in CASES for field in CLAIM_VALUE_FIELDS],
)
def test_regex_baseline_against_gold(case, field):
    got = _normalize(getattr(_regex_fields(case, _part(case, "txt")), field))
    want = _normalize(case["gold"][field])

    if field in case["regex_known_failures"]:
        assert got != want, (
            f"regex now gets {field!r} right on {case['slug']} — remove it from "
            "regex_known_failures in cases.json so the pass is locked in"
        )
    else:
        assert got == want


@pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
def test_converter_on_html_part_gives_the_same_regex_outcome(case):
    """The text/html part is a second XHTML shape (Gmail-rewritten, no <head>).
    Run it through _html_to_plain and the regex must land where the text/plain
    path does, field for field — the converter is not template-specific."""
    via_html = _regex_fields(case, ClaimData._html_to_plain(_part(case, "html")))
    via_text = _regex_fields(case, _part(case, "txt"))

    for field in CLAIM_VALUE_FIELDS:
        assert _normalize(getattr(via_html, field)) == _normalize(getattr(via_text, field)), field


# ---------------------------------------------------------------------------
# LLM eval harness, Tier 1 (llm-extraction REQ-6.1–6.3, 6.5) — FunctionModel, no network
# ---------------------------------------------------------------------------

SINIESTRO_SLUG = "siniestro_normal_tuberia"
ASISTENCIA_SLUG = "asistencia_envio_profesionales"
BLANK_ADDRESS_SLUG = "siniestro_direccion_en_blanco"


def _case(slug: str) -> dict:
    return next(case for case in CASES if case["slug"] == slug)


def _subject_of(messages) -> str:
    """The subject the model was shown, read back from the delimited user message."""
    content = messages[0].parts[-1].content
    return content.split(SUBJECT_OPEN, 1)[1].split(SUBJECT_CLOSE, 1)[0].strip()


def _perturbing_extractor(cases: list[dict], slug: str, field: str, value) -> LlmFieldExtractor:
    """Gold for every case, except `field` of `slug` answered with `value`."""
    by_subject = {case["subject"]: case for case in cases}

    def answer(messages, info) -> ModelResponse:
        case = by_subject[_subject_of(messages)]
        allowed = info.model_request_parameters.output_object.json_schema["properties"]
        body = {k: v for k, v in case["gold"].items() if k in allowed}
        if case["slug"] == slug:
            body[field] = value
        return ModelResponse(parts=[TextPart(json.dumps(body))], finish_reason="stop")

    settings = Settings(
        field_extractor_backend=EXTRACTOR_LLM, foundry_endpoint="https://foundry.example/openai/v1/"
    )
    return LlmFieldExtractor(settings, token=_dummy_token, model=FunctionModel(answer))


async def _dummy_token() -> str:
    return "DUMMY"


def _with_known_failures(cases: list[dict], slug: str, fields: list[str]) -> list[dict]:
    return [
        {**case, "llm_known_failures": fields} if case["slug"] == slug else dict(case)
        for case in cases
    ]


def _with_gold(cases: list[dict], slug: str, field: str, value) -> list[dict]:
    return [
        {**case, "gold": {**case["gold"], field: value}} if case["slug"] == slug else dict(case)
        for case in cases
    ]


def test_module_docstring_states_what_green_means():
    assert "nothing known broke" in __doc__
    assert "no score threshold" in __doc__


@pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
def test_manifest_lists_llm_known_failures_per_case(case):
    # REQ-6.2: initially [] — a placeholder until Task 9 records real answers.
    assert isinstance(case["llm_known_failures"], list)
    assert set(case["llm_known_failures"]) <= set(CLAIM_VALUE_FIELDS)


def test_dataset_carries_value_fields_gold_and_known_failure_metadata():
    dataset = build_dataset(CASES, ARM_PLAIN)
    assert [case.name for case in dataset.cases] == CASE_IDS
    for case, manifest in zip(dataset.cases, CASES, strict=True):
        assert set(case.expected_output) == set(CLAIM_VALUE_FIELDS)
        assert "extractor_used" not in case.expected_output
        assert case.inputs.subject == manifest["subject"]
        assert case.inputs.claim_type == manifest["claim_type"]
        assert case.inputs.body == _part(manifest, "txt")
        assert case.metadata.llm_known_failures == manifest["llm_known_failures"]
        assert case.metadata.regex_known_failures == manifest["regex_known_failures"]


@pytest.mark.parametrize(
    "got,want,expected",
    [
        (None, None, Outcome.MATCH),
        ("VILLANUEVA", "VILLANUEVA", Outcome.MATCH),
        ("a  b\n c", "a b c", Outcome.MATCH),  # whitespace-collapsed
        ("‎X", "X", Outcome.MATCH),  # U+200E stripped
        (None, "VILLANUEVA", Outcome.OMISSION),
        ("VILLANUEVA", None, Outcome.HALLUCINATION),
        ("VILLAFICTICIA", "VILLANUEVA", Outcome.MISMATCH),
        ("villanueva", "VILLANUEVA", Outcome.MISMATCH),  # otherwise exact
    ],
)
def test_outcome_taxonomy(got, want, expected):
    assert outcome(got, want) is expected


def test_harness_is_green_when_the_model_returns_gold():
    # Harness bug ⇒ red: every (case, field) must read `match`.
    report = run_eval(gold_answering_extractor(CASES), CASES, ARM_PLAIN)
    outcomes = outcomes_by_case(report)
    assert set(outcomes) == set(CASE_IDS)
    for slug, per_field in outcomes.items():
        assert set(per_field) == set(CLAIM_VALUE_FIELDS), slug
        assert set(per_field.values()) == {Outcome.MATCH}, (slug, per_field)
    assert_asymmetric_gate(outcomes, CASES)  # cases.json lists no llm failures today


def test_run_eval_precomputes_one_extraction_per_case():
    calls = []

    def counting(messages, info) -> ModelResponse:
        calls.append(_subject_of(messages))
        allowed = info.model_request_parameters.output_object.json_schema["properties"]
        return ModelResponse(parts=[TextPart(json.dumps({k: None for k in allowed}))])

    settings = Settings(
        field_extractor_backend=EXTRACTOR_LLM, foundry_endpoint="https://foundry.example/openai/v1/"
    )
    run_eval(
        LlmFieldExtractor(settings, token=_dummy_token, model=FunctionModel(counting)),
        CASES,
        ARM_PLAIN,
    )
    assert sorted(calls) == sorted(case["subject"] for case in CASES)


def test_wrong_value_reads_as_mismatch():
    extractor = _perturbing_extractor(CASES, SINIESTRO_SLUG, "town", "VILLAFICTICIA")
    outcomes = outcomes_by_case(run_eval(extractor, CASES, ARM_PLAIN))
    assert outcomes[SINIESTRO_SLUG]["town"] is Outcome.MISMATCH
    assert outcomes[SINIESTRO_SLUG]["nif"] is Outcome.MATCH


def test_missing_value_reads_as_omission():
    extractor = _perturbing_extractor(CASES, SINIESTRO_SLUG, "town", None)
    outcomes = outcomes_by_case(run_eval(extractor, CASES, ARM_PLAIN))
    assert outcomes[SINIESTRO_SLUG]["town"] is Outcome.OMISSION


def test_invented_value_reads_as_hallucination():
    # Gold says the field is absent; the model invents one.
    cases = _with_gold(CASES, SINIESTRO_SLUG, "nif", None)
    extractor = _perturbing_extractor(cases, SINIESTRO_SLUG, "nif", "X1234567A")
    outcomes = outcomes_by_case(run_eval(extractor, cases, ARM_PLAIN))
    assert outcomes[SINIESTRO_SLUG]["nif"] is Outcome.HALLUCINATION


def test_gate_blocks_an_unlisted_failure():
    extractor = _perturbing_extractor(CASES, SINIESTRO_SLUG, "town", "VILLAFICTICIA")
    outcomes = outcomes_by_case(run_eval(extractor, CASES, ARM_PLAIN))
    with pytest.raises(AssertionError, match=f"{SINIESTRO_SLUG}.*town.*mismatch"):
        assert_asymmetric_gate(outcomes, CASES)


def test_gate_requires_a_listed_failure_to_still_fail():
    # A known failure that starts passing must be locked in by editing the manifest.
    cases = _with_known_failures(CASES, SINIESTRO_SLUG, ["town"])
    outcomes = outcomes_by_case(run_eval(gold_answering_extractor(cases), cases, ARM_PLAIN))
    with pytest.raises(AssertionError, match="lock"):
        assert_asymmetric_gate(outcomes, cases)


def test_gate_accepts_a_listed_failure_that_still_fails():
    cases = _with_known_failures(CASES, SINIESTRO_SLUG, ["town"])
    extractor = _perturbing_extractor(cases, SINIESTRO_SLUG, "town", "VILLAFICTICIA")
    assert_asymmetric_gate(outcomes_by_case(run_eval(extractor, cases, ARM_PLAIN)), cases)


def test_regex_column_restates_the_regex_tripwire():
    # REQ-6.3: regex is the permanent comparison column — always on its
    # production input (the plain arm), whatever arm the LLM ran on.
    column = regex_column(CASES)
    for case in CASES:
        for field in CLAIM_VALUE_FIELDS:
            failed = column[case["slug"]][field] is not Outcome.MATCH
            assert failed == (field in case["regex_known_failures"]), (case["slug"], field)


def test_discordant_pairs_report_direction():
    llm = outcomes_by_case(run_eval(gold_answering_extractor(CASES), CASES, ARM_PLAIN))
    pairs = discordant_pairs(llm, regex_column(CASES))
    # The discordant pairs known today, both favouring the LLM: the asistencia
    # description (regex's `Tipo:` anchor) and the blank-siniestro-address case
    # (regex captures the blank line; the rule says use the Asegurado's).
    assert set(pairs) == {
        (ASISTENCIA_SLUG, "description", "llm"),
        (BLANK_ADDRESS_SLUG, "address", "llm"),
    }
    extractor = _perturbing_extractor(CASES, SINIESTRO_SLUG, "town", None)
    llm = outcomes_by_case(run_eval(extractor, CASES, ARM_PLAIN))
    assert (SINIESTRO_SLUG, "town", "regex") in discordant_pairs(llm, regex_column(CASES))


def test_rendered_report_carries_the_column_and_the_pairs():
    report = run_eval(gold_answering_extractor(CASES), CASES, ARM_PLAIN)
    llm = outcomes_by_case(report)
    text = render_report(report, llm, regex_column(CASES), ARM_PLAIN)
    assert ASISTENCIA_SLUG in text and "description" in text
    assert "discordant" in text.lower()
    assert "regex" in text and "llm" in text
    for value in ("00000015S", "600000015", "ENCARNA"):
        assert value not in text  # outcomes and counts only — no field values


@pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
def test_xhtml_stripped_arm_drops_presentation_attributes_and_keeps_text(case):
    # REQ-6.5 / D-c: arm (b′) exists only here, as a switchable eval input.
    stripped = strip_attributes(_part(case, "html"))
    assert not re.search(r'\s(class|style|id|lang|dir|xml:\w+)="', stripped), case["slug"]
    assert len(stripped) < len(_part(case, "html"))
    assert "Compañía" in stripped or "Comunicación" in stripped
    assert case_body(case, ARM_PLAIN) == _part(case, "txt")
    assert case_body(case, ARM_XHTML_STRIPPED) == stripped


@pytest.mark.parametrize("arm", ARMS)
def test_harness_runs_on_either_arm(arm):
    seen_bodies = []

    def answer(messages, info) -> ModelResponse:
        seen_bodies.append(messages[0].parts[-1].content)
        case = next(c for c in CASES if c["subject"] == _subject_of(messages))
        allowed = info.model_request_parameters.output_object.json_schema["properties"]
        body = {k: v for k, v in case["gold"].items() if k in allowed}
        return ModelResponse(parts=[TextPart(json.dumps(body))])

    settings = Settings(
        field_extractor_backend=EXTRACTOR_LLM, foundry_endpoint="https://foundry.example/openai/v1/"
    )
    extractor = LlmFieldExtractor(settings, token=_dummy_token, model=FunctionModel(answer))
    outcomes = outcomes_by_case(run_eval(extractor, CASES, arm))
    assert all(set(v.values()) == {Outcome.MATCH} for v in outcomes.values())
    html_seen = any("<div" in body for body in seen_bodies)
    assert html_seen == (arm == ARM_XHTML_STRIPPED)
    assert CASES_DIR.name == "claim_emails"
