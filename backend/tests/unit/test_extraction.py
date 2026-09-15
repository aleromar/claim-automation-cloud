"""Extraction seam, model contract, and extractor-selection tests
(spec REQ-2, REQ-6, REQ-7).

The seam is 5a2's swap point: a FieldExtractor implementation must be usable
without touching classification, model, or PDF code.
"""

import base64
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from core.config import Settings
from pipeline.claim_data import ClaimData, ClaimType
from pipeline.extraction import (
    CLAIM_VALUE_FIELDS,
    EXTRACTOR_LLM,
    EXTRACTOR_REGEX,
    ClaimFields,
    ExtractorUsed,
    RegexFieldExtractor,
    get_field_extractor,
)

SUBJECT = "2026/123456 Declaración de siniestro a colaborador NORMAL (H)Envio N-X"
COMUNICACION_SUBJECT = "2026/123456 Comunicación a colaborador (H)Envio N-X"

PLAIN_BODY = "Compañía: Reale\n\nNif: H12345678\n\nTomador: CDAD EJEMPLO\n"
FOUNDRY_ENDPOINT = "https://foundry.example/openai/v1/"

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
        # Closed set: the Literal must equal the registry keys, so a typo in
        # the app setting fails at startup instead of at the first email.
        monkeypatch.setenv("FIELD_EXTRACTOR_BACKEND", "carrier-pigeon")
        with pytest.raises(ValidationError):
            Settings()

    def test_setting_accepts_llm(self, monkeypatch):
        monkeypatch.setenv("FIELD_EXTRACTOR_BACKEND", EXTRACTOR_LLM)
        assert Settings().field_extractor_backend == EXTRACTOR_LLM


class TestLlmBackendSelection:
    """llm-extraction REQ-1: the flag is read at composition, the endpoint is
    enforced by the factory (fails the run), never by Settings (fails the app)."""

    def test_llm_setting_constructs_without_an_endpoint(self, monkeypatch):
        # REQ-1.2: no Settings validator — a bad live flip must not take the
        # dashboard routes down; the run fails instead (factory below).
        monkeypatch.delenv("FOUNDRY_ENDPOINT", raising=False)
        settings = Settings(field_extractor_backend=EXTRACTOR_LLM)
        assert settings.foundry_endpoint is None

    def test_foundry_settings_defaults(self, monkeypatch):
        for name in ("FOUNDRY_ENDPOINT", "FOUNDRY_DEPLOYMENT", "LLM_CAPTURE_CONTENT"):
            monkeypatch.delenv(name, raising=False)
        settings = Settings()
        assert settings.foundry_endpoint is None
        assert settings.foundry_deployment == "gpt-5-mini"
        assert settings.llm_capture_content is True

    def test_foundry_settings_read_from_env(self, monkeypatch):
        # Infra impact (a): the three app settings bicep writes.
        monkeypatch.setenv("FOUNDRY_ENDPOINT", FOUNDRY_ENDPOINT)
        monkeypatch.setenv("FOUNDRY_DEPLOYMENT", "gpt-5-mini-test")
        monkeypatch.setenv("LLM_CAPTURE_CONTENT", "false")
        settings = Settings()
        assert settings.foundry_endpoint == FOUNDRY_ENDPOINT
        assert settings.foundry_deployment == "gpt-5-mini-test"
        assert settings.llm_capture_content is False

    def test_factory_llm_without_endpoint_fails_the_run_naming_the_setting(self):
        settings = Settings(field_extractor_backend=EXTRACTOR_LLM, foundry_endpoint=None)
        with pytest.raises(ValueError, match="FOUNDRY_ENDPOINT"):
            get_field_extractor(EXTRACTOR_LLM, settings)

    def test_factory_llm_without_settings_fails_naming_the_setting(self):
        with pytest.raises(ValueError, match="FOUNDRY_ENDPOINT"):
            get_field_extractor(EXTRACTOR_LLM)

    def test_factory_returns_llm_extractor_configured_from_settings(self):
        from pipeline.llm_extraction import LlmFieldExtractor

        settings = Settings(
            field_extractor_backend=EXTRACTOR_LLM, foundry_endpoint=FOUNDRY_ENDPOINT
        )
        extractor = get_field_extractor(EXTRACTOR_LLM, settings)
        try:
            assert isinstance(extractor, LlmFieldExtractor)
        finally:
            extractor.close()

    def test_factory_regex_ignores_settings(self):
        # P13(a): under regex the Foundry settings are not required.
        settings = Settings(foundry_endpoint=None)
        assert isinstance(get_field_extractor(EXTRACTOR_REGEX, settings), RegexFieldExtractor)
        assert isinstance(get_field_extractor(EXTRACTOR_REGEX), RegexFieldExtractor)

    def test_factory_rejects_unknown_name_with_settings(self):
        with pytest.raises(ValueError):
            get_field_extractor("carrier-pigeon", Settings())

    def test_regex_extractor_close_is_a_noop(self):
        # run_pipeline closes whatever the factory returned (REQ-1.3).
        assert RegexFieldExtractor().close() is None

    def test_regex_path_never_imports_the_llm_stack(self):
        """NFR "regex path adds 0" (replaces the dropped Task 0b question 3):
        a fresh interpreter composing and running the regex extractor must not
        load the leaf module or its dependencies."""
        code = (
            "import sys\n"
            "from pipeline.extraction import EXTRACTOR_REGEX, get_field_extractor\n"
            "from pipeline.claim_data import ClaimData, ClaimType\n"
            "import pipeline.entry\n"
            "extractor = get_field_extractor(EXTRACTOR_REGEX)\n"
            "extractor.extract(ClaimType.DECLARACION_SINIESTRO, 's', 'Nif: X1', 'Nif: X1')\n"
            "loaded = [m for m in ('pipeline.llm_extraction', 'pydantic_ai', 'openai')"
            " if m in sys.modules]\n"
            "print('loaded', loaded)\n"
        )
        backend_dir = Path(__file__).resolve().parents[2]
        out = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            timeout=60,
            cwd=backend_dir,
        )
        assert out.returncode == 0, out.stderr
        assert "loaded []" in out.stdout


class TestValueFieldsAndProvenance:
    """llm-extraction REQ-2.3 / REQ-3.2: the eight value fields are named once;
    provenance is a ninth attribute that is not a value field."""

    def test_claim_value_fields_are_the_eight_value_names(self):
        assert set(CLAIM_VALUE_FIELDS) == {
            "insurance_company",
            "nif",
            "address",
            "phone_number",
            "town",
            "description",
            "owner_name",
            "observaciones",
        }
        assert len(CLAIM_VALUE_FIELDS) == 8
        assert "extractor_used" not in CLAIM_VALUE_FIELDS
        for field in CLAIM_VALUE_FIELDS:
            assert field in ClaimFields.model_fields

    def test_extractor_used_is_the_closed_set(self):
        assert {member.value for member in ExtractorUsed} == {"regex", "llm", "regex_fallback"}

    def test_claim_fields_provenance_defaults_to_none(self):
        assert ClaimFields().extractor_used is None

    def test_regex_extractor_tags_its_result(self):
        siniestro = RegexFieldExtractor().extract(
            ClaimType.DECLARACION_SINIESTRO, SUBJECT, PLAIN_BODY, PLAIN_BODY
        )
        comunicacion = RegexFieldExtractor().extract(
            ClaimType.COMUNICACION_A_COLABORADOR, COMUNICACION_SUBJECT, "Observaciones: x", ""
        )
        assert siniestro.extractor_used is ExtractorUsed.REGEX
        assert comunicacion.extractor_used is ExtractorUsed.REGEX
        # Value logic byte-identical (P3 keep row): the tag is the only change.
        assert siniestro.insurance_company == "Reale"
        assert comunicacion.observaciones == "x"

    def test_provenance_flows_onto_claim_data(self):
        claim = ClaimData.from_msg_data(_make_gmail_message(SUBJECT, PLAIN_BODY))
        assert claim is not None
        assert claim.extractor_used == ExtractorUsed.REGEX
        assert claim.extractor_used == "regex"  # a StrEnum value lands as plain str
