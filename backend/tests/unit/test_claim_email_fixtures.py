"""Real-layout claim-email fixtures (llm-extraction groundwork D-l, §8.1/§8.6).

Five anonymized claim emails under tests/data/claim_emails/ — one asistencia,
two comunicaciones, one NORMAL and one URGENTE siniestro — each stored as the
text/plain and text/html parts of a Gmail forward, with a JSON manifest holding
the subject, expected type and proposed gold ClaimFields (see cases.json for
provenance, the Gmail-forward artifacts and the field rules).

Two jobs here. (1) Tripwire: the fixtures must stay anonymized. (2) Baseline:
the regex extractor's per-(case, field) outcome against gold, with the
asymmetric gate of groundwork §8.6 — a known failure that starts passing must be
recorded, a passing pair that regresses blocks. This is the comparison column an
LLM extractor is measured against.
"""

import base64
import json
import re
from pathlib import Path

import pytest

from pipeline.claim_data import ClaimData, ClaimType
from pipeline.extraction import ClaimFields, RegexFieldExtractor

CASES_DIR = Path(__file__).parent.parent / "data" / "claim_emails"
MANIFEST = json.loads((CASES_DIR / "cases.json").read_text(encoding="utf-8"))
CASES = MANIFEST["cases"]
CASE_IDS = [case["slug"] for case in CASES]
FIELDS = list(ClaimFields.model_fields)

# The insurer's system sender is the one non-placeholder address allowed to survive.
INSURER_SENDER = "colaboradores.hogar@notificaciones.asitur.es"


def _part(case: dict, ext: str) -> str:
    return (CASES_DIR / f"{case['slug']}.{ext}").read_text(encoding="utf-8")


def _make_gmail_message(subject: str, body: str) -> dict:
    encoded_body = base64.urlsafe_b64encode(body.encode("utf-8")).decode("ascii")
    return {
        "payload": {
            "headers": [{"name": "Subject", "value": subject}],
            "parts": [{"mimeType": "text/plain", "body": {"data": encoded_body}}],
        }
    }


def _normalize(value: str | None) -> str | None:
    """Whitespace-collapsed, direction marks stripped (cases.json field_rules.comparison)."""
    if value is None:
        return None
    return re.sub(r"\s+", " ", value.replace("‎", "")).strip()


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
    text = _part(case, "txt") + _part(case, "html")
    for phone in re.findall(r"\b[6789]\d{8}\b", text):
        assert phone.startswith(("600000", "900000")), phone
    for nif in re.findall(r"\b\d{8}[A-Z]\b", text):
        assert nif.startswith("000000"), nif
    for address in re.findall(r"[\w.+-]+@[\w-]+\.[\w.]+", text):
        assert address.endswith("@example.com") or address == INSURER_SENDER, address
    for year in re.findall(r"\b(20\d\d)/\d{5,}\b", text):
        assert year.startswith("209"), year


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
    [(case, field) for case in CASES for field in FIELDS],
    ids=[f"{case['slug']}-{field}" for case in CASES for field in FIELDS],
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

    for field in FIELDS:
        assert _normalize(getattr(via_html, field)) == _normalize(getattr(via_text, field)), field
