"""Field extraction behind the FieldExtractor seam (spec C1/REQ-2/REQ-7).

RegexFieldExtractor reproduces the original regexes byte-for-byte. The LLM
implementation lives in the leaf module `pipeline.llm_extraction`, imported
ONLY inside the `llm` branch of `get_field_extractor`: the regex path must
never load pydantic_ai/openai (cold-start NFR), and the provenance enums live
here so that module can import them without a cycle.
"""

import re
from enum import StrEnum
from typing import Final, Protocol

from pydantic import BaseModel, ConfigDict

from core.config import Settings
from pipeline.claim_data import ClaimType

EXTRACTOR_REGEX = "regex"
EXTRACTOR_LLM = "llm"

# The eight value fields an extractor fills. Evaluators, fixture tests and the
# LLM output mapping iterate THIS, never ClaimFields.model_fields, which also
# carries the provenance attribute below.
CLAIM_VALUE_FIELDS: Final[tuple[str, ...]] = (
    "insurance_company",
    "nif",
    "address",
    "phone_number",
    "town",
    "description",
    "owner_name",
    "observaciones",
)


class ExtractorUsed(StrEnum):
    """Which implementation produced the fields (llm-extraction REQ-3.2)."""

    REGEX = "regex"
    LLM = "llm"
    REGEX_FALLBACK = "regex_fallback"


class FailureClass(StrEnum):
    """Why an LLM call fell back to regex (llm-extraction REQ-3.1) — the KQL
    dimension's closed set. OTHER is a suspected bug, not weather."""

    CONTENT_FILTER = "content_filter"
    TRUNCATION = "truncation"
    VALIDATION_FAILED = "validation_failed"
    TIMEOUT = "timeout"
    HTTP_ERROR = "http_error"
    OTHER = "other"


class ClaimFields(BaseModel):
    """The per-type optional fields an extractor produces, plus provenance."""

    model_config = ConfigDict(extra="forbid")

    insurance_company: str | None = None
    nif: str | None = None
    address: str | None = None
    phone_number: str | None = None
    town: str | None = None
    description: str | None = None
    owner_name: str | None = None
    observaciones: str | None = None
    # Not a value field: travels to ClaimData and the ledger row unchanged.
    extractor_used: ExtractorUsed | None = None


class FieldExtractor(Protocol):
    def extract(self, claim_type: ClaimType, subject: str, body: str, raw_body: str) -> ClaimFields:
        """`body` is the converted plain text; `raw_body` the pre-conversion
        decode (identical when no conversion ran) — passed and unused by both
        implementations in v1 (llm-extraction D-f)."""
        ...

    def close(self) -> None:
        """Release per-run resources; run_pipeline calls it in its `finally`."""
        ...


def _extract_field(text: str, pattern: str, multiline: bool = False) -> str | None:
    flags = re.IGNORECASE | (re.DOTALL if multiline else 0)
    match = re.search(pattern, text, flags)
    return match.group(1).strip() if match else None


class RegexFieldExtractor:
    """The original regex extraction, relocated behind the seam. Value logic
    is byte-identical to the laptop's; the provenance tag is its one side
    effect (llm-extraction P3 keep/change row)."""

    def extract(self, claim_type: ClaimType, subject: str, body: str, raw_body: str) -> ClaimFields:
        if claim_type == ClaimType.COMUNICACION_A_COLABORADOR:
            return ClaimFields(
                observaciones=_extract_field(
                    body, r"Observaciones:\s*(.*?)(?:\n--|$)", multiline=True
                ),
                extractor_used=ExtractorUsed.REGEX,
            )
        return ClaimFields(
            insurance_company=_extract_field(body, r"Compañía:\s*(.+)"),
            nif=_extract_field(body, r"Nif:\s*([A-Z0-9]+)"),
            address=_extract_field(body, r"Dirección:[ \t]*([^\n]*)"),
            phone_number=_extract_field(body, r"Tfno\s*:\s*(\d+)"),
            town=_extract_field(body, r"Localidad:\s*(.*?)(?:\s*Código Postal:|$)", multiline=True),
            description=_extract_field(body, r"Descripción:\s*(.+?)\s*Tipo:", multiline=True),
            owner_name=_extract_field(body, r"Tomador:\s*(.+)"),
            extractor_used=ExtractorUsed.REGEX,
        )

    def close(self) -> None:
        return None


def get_field_extractor(name: str, settings: Settings | None = None) -> FieldExtractor:
    """Compose the configured extractor. `regex` ignores `settings`; `llm`
    requires them and fails the RUN loudly when the endpoint is missing
    (REQ-1.2) — the only place the LLM stack is imported (REQ-1.5)."""
    if name == EXTRACTOR_REGEX:
        return RegexFieldExtractor()
    if name == EXTRACTOR_LLM:
        if settings is None or not settings.foundry_endpoint:
            raise ValueError(
                "FOUNDRY_ENDPOINT is not configured — required when FIELD_EXTRACTOR_BACKEND=llm "
                "(set by the infra deployment as a Function App setting)"
            )
        from pipeline.llm_extraction import LlmFieldExtractor

        return LlmFieldExtractor(settings)
    raise ValueError(f"Unknown field extractor backend: {name!r}")
