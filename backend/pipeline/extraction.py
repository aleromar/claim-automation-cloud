"""Field extraction behind the FieldExtractor seam (spec C1/REQ-2/REQ-7).

RegexFieldExtractor reproduces the original regexes byte-for-byte; 5a2 adds an
LLM implementation of the same protocol (ClaimFields is its structured-output
contract) and registers it in the factory + the ExtractorBackend Literal.
"""

import re
from typing import Protocol

from pydantic import BaseModel, ConfigDict

from pipeline.claim_data import ClaimType

EXTRACTOR_REGEX = "regex"


class ClaimFields(BaseModel):
    """The per-type optional fields an extractor produces."""

    model_config = ConfigDict(extra="forbid")

    insurance_company: str | None = None
    nif: str | None = None
    address: str | None = None
    phone_number: str | None = None
    town: str | None = None
    description: str | None = None
    owner_name: str | None = None
    observaciones: str | None = None


class FieldExtractor(Protocol):
    def extract(self, claim_type: ClaimType, subject: str, body: str, raw_body: str) -> ClaimFields:
        """`body` is the converted plain text; `raw_body` the pre-conversion
        decode (identical when no conversion ran) — for 5a2's raw-XHTML arm."""
        ...


def _extract_field(text: str, pattern: str, multiline: bool = False) -> str | None:
    flags = re.IGNORECASE | (re.DOTALL if multiline else 0)
    match = re.search(pattern, text, flags)
    return match.group(1).strip() if match else None


class RegexFieldExtractor:
    """The original regex extraction, relocated behind the seam."""

    def extract(self, claim_type: ClaimType, subject: str, body: str, raw_body: str) -> ClaimFields:
        if claim_type == ClaimType.COMUNICACION_A_COLABORADOR:
            return ClaimFields(
                observaciones=_extract_field(
                    body, r"Observaciones:\s*(.*?)(?:\n--|$)", multiline=True
                )
            )
        return ClaimFields(
            insurance_company=_extract_field(body, r"Compañía:\s*(.+)"),
            nif=_extract_field(body, r"Nif:\s*([A-Z0-9]+)"),
            address=_extract_field(body, r"Dirección:[ \t]*([^\n]*)"),
            phone_number=_extract_field(body, r"Tfno\s*:\s*(\d+)"),
            town=_extract_field(body, r"Localidad:\s*(.*?)(?:\s*Código Postal:|$)", multiline=True),
            description=_extract_field(body, r"Descripción:\s*(.+?)\s*Tipo:", multiline=True),
            owner_name=_extract_field(body, r"Tomador:\s*(.+)"),
        )


def get_field_extractor(name: str) -> FieldExtractor:
    registry: dict[str, type[RegexFieldExtractor]] = {EXTRACTOR_REGEX: RegexFieldExtractor}
    if name not in registry:
        raise ValueError(f"Unknown field extractor backend: {name!r}")
    return registry[name]()
