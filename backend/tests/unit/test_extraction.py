"""Extraction seam, model contract, and extractor-selection tests
(spec REQ-2, REQ-6, REQ-7).

The seam is 5a2's swap point: a FieldExtractor implementation must be usable
without touching classification, model, or PDF code.
"""

import base64

import pytest
from pydantic import ValidationError

from core.config import Settings
from pipeline.claim_data import ClaimData, ClaimType
from pipeline.extraction import (
    EXTRACTOR_REGEX,
    ClaimFields,
    RegexFieldExtractor,
    get_field_extractor,
)

SUBJECT = "2026/123456 Declaración de siniestro a colaborador NORMAL (H)Envio N-X"

PLAIN_BODY = "Compañía: Reale\n\nNif: H12345678\n\nTomador: CDAD EJEMPLO\n"

XHTML_BODY = (
    '<!DOCTYPE html ><html xmlns="http://www.w3.org/1999/xhtml"><body>'
    '<p><span class="pt-Fuentedeprrafopredeter-000010">Compañía: </span>'
    "<span>Reale</span></p></body></html>"
)


def _make_gmail_message(subject: str, body: str) -> dict:
    encoded_body = base64.urlsafe_b64encode(body.encode("utf-8")).decode("ascii")
    return {
        "payload": {
            "headers": [{"name": "Subject", "value": subject}],
            "parts": [{"mimeType": "text/plain", "body": {"data": encoded_body}}],
        }
    }


class RecordingExtractor:
    """Fake FieldExtractor: records its inputs, returns canned fields."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def extract(
        self,
        claim_type: ClaimType,
        subject: str,
        body: str,
        raw_body: str,
    ) -> ClaimFields:
        self.calls.append(
            {
                "claim_type": claim_type,
                "subject": subject,
                "body": body,
                "raw_body": raw_body,
            }
        )
        return ClaimFields(insurance_company="FAKE-INSURER", nif="FAKE-NIF")


class RaisingExtractor:
    def extract(self, claim_type, subject, body, raw_body) -> ClaimFields:
        raise RuntimeError("extractor exploded")


class TestFieldExtractorSeam:
    def test_custom_extractor_fields_land_on_claim(self):
        fake = RecordingExtractor()
        msg = _make_gmail_message(SUBJECT, PLAIN_BODY)
        claim = ClaimData.from_msg_data(msg, extractor=fake)

        assert claim is not None
        assert claim.insurance_company == "FAKE-INSURER"
        assert claim.nif == "FAKE-NIF"
        # Deterministic parts are NOT the extractor's job.
        assert claim.year == "2026"
        assert claim.claim_number == "123456"
        assert claim.type is ClaimType.DECLARACION_SINIESTRO

    def test_extractor_receives_classification_and_both_bodies(self):
        fake = RecordingExtractor()
        msg = _make_gmail_message(SUBJECT, PLAIN_BODY)
        ClaimData.from_msg_data(msg, extractor=fake)

        assert len(fake.calls) == 1
        call = fake.calls[0]
        assert call["claim_type"] is ClaimType.DECLARACION_SINIESTRO
        assert call["subject"] == SUBJECT
        # Plain input: converted and raw bodies are identical.
        assert call["body"] == call["raw_body"]

    def test_extractor_receives_converted_body_and_raw_xhtml(self):
        """REQ-2: the seam gets the converted plain text as `body`, and the
        pre-conversion decode as `raw_body` (5a2's raw-XHTML arm)."""
        fake = RecordingExtractor()
        msg = _make_gmail_message(SUBJECT, XHTML_BODY)
        ClaimData.from_msg_data(msg, extractor=fake)

        call = fake.calls[0]
        assert "<span" not in call["body"]
        assert "Compañía: Reale" in call["body"]
        assert "<span" in call["raw_body"]

    def test_extractor_exception_propagates(self):
        msg = _make_gmail_message(SUBJECT, PLAIN_BODY)
        with pytest.raises(RuntimeError, match="extractor exploded"):
            ClaimData.from_msg_data(msg, extractor=RaisingExtractor())

    def test_default_extractor_is_regex(self):
        """No extractor argument → RegexFieldExtractor behavior (parity path)."""
        msg = _make_gmail_message(SUBJECT, PLAIN_BODY)
        claim = ClaimData.from_msg_data(msg)

        assert claim is not None
        assert claim.insurance_company == "Reale"
        assert claim.nif == "H12345678"
        assert claim.owner_name == "CDAD EJEMPLO"


class TestClaimDataModelContract:
    """REQ-6 / C2: pinned Pydantic semantics."""

    def test_unknown_field_raises(self):
        with pytest.raises(ValidationError):
            ClaimData(
                year="2026",
                claim_number="1",
                subject="s",
                email_body="b",
                type=ClaimType.DECLARACION_SINIESTRO,
                bogus_field="nope",
            )

    def test_missing_required_field_raises(self):
        with pytest.raises(ValidationError):
            ClaimData(year="2026", claim_number="1")

    def test_enum_identity_preserved(self):
        claim = ClaimData(
            year="2026",
            claim_number="1",
            subject="s",
            email_body="b",
            type=ClaimType.DECLARACION_URGENTE,
        )
        assert claim.type is ClaimType.DECLARACION_URGENTE

    def test_optional_fields_default_to_none(self):
        claim = ClaimData(
            year="2026",
            claim_number="1",
            subject="s",
            email_body="b",
            type=ClaimType.DECLARACION_SINIESTRO,
        )
        assert claim.insurance_company is None
        assert claim.observaciones is None


class TestExtractorSelection:
    """REQ-7: factory + setting (flip = one app-setting change, no redeploy)."""

    def test_factory_returns_regex_extractor(self):
        extractor = get_field_extractor(EXTRACTOR_REGEX)
        assert isinstance(extractor, RegexFieldExtractor)

    def test_factory_rejects_unknown_name(self):
        with pytest.raises(ValueError):
            get_field_extractor("carrier-pigeon")

    def test_setting_defaults_to_regex(self, monkeypatch):
        monkeypatch.delenv("FIELD_EXTRACTOR_BACKEND", raising=False)
        assert Settings().field_extractor_backend == EXTRACTOR_REGEX

    def test_setting_rejects_unregistered_backend(self, monkeypatch):
        # "llm" joins the Literal in 5a2; until then it must fail fast.
        monkeypatch.setenv("FIELD_EXTRACTOR_BACKEND", "llm")
        with pytest.raises(ValidationError):
            Settings()
