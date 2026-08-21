"""Claim classification and parsing, ported from ../claim_automation (spec 5a).

Classification and the Gmail-message-dict decode are kept byte-compatible with
the original; field extraction is delegated to the FieldExtractor seam
(pipeline.extraction) so 5a2 can swap in an LLM extractor.
"""

import base64
import html
import re
from enum import Enum
from typing import TYPE_CHECKING, Final

from pydantic import BaseModel, ConfigDict

if TYPE_CHECKING:
    from pipeline.extraction import FieldExtractor

# The three subject substrings that make an email claim-bearing. Single source
# for from_subject AND the 5b probe's matching criterion (gmail-client C6) —
# hoisting them is behavior-identical, guarded by the ported parity tests.
DECLARACION_MARKER: Final = "Declaración de siniestro a colaborador"
ASISTENCIA_MARKER: Final = "Solicitud de asistencia a colaborador"
COMUNICACION_MARKER: Final = "Comunicación a colaborador"
CLAIM_SUBJECT_MARKERS: Final = (DECLARACION_MARKER, ASISTENCIA_MARKER, COMUNICACION_MARKER)


class ClaimType(Enum):
    DECLARACION_SINIESTRO = "Declaración de siniestro a colaborador"
    DECLARACION_URGENTE = "Declaración urgente de siniestro a colaborador"
    SOLICITUD_ASISTENCIA_BRICO = "Solicitud de asistencia brico hogar"
    SOLICITUD_ASISTENCIA_ENVIO_PROFESIONALES = "Solicitud de asistencia envío de profesionales"
    SOLICITUD_ASISTENCIA_ELECTRICIDAD_EMERGENCIA = (
        "Solicitud de asistencia electricidad de emergencia"
    )
    COMUNICACION_A_COLABORADOR = "Comunicación a colaborador"

    @classmethod
    def from_subject(cls, subject: str | None, body: str | None) -> "ClaimType | None":
        if not subject:
            return None
        if DECLARACION_MARKER in subject:
            if "urgente" in subject.lower():
                return cls.DECLARACION_URGENTE
            return cls.DECLARACION_SINIESTRO
        elif ASISTENCIA_MARKER in subject:
            if body and "SERVICIO BRICO HOGAR".lower() in body.lower():
                return cls.SOLICITUD_ASISTENCIA_BRICO
            elif body and "ENVÍO DE PROFESIONALES".lower() in body.lower():
                return cls.SOLICITUD_ASISTENCIA_ENVIO_PROFESIONALES
            elif body and "ELECTRICIDAD DE EMERGENCIA".lower() in body.lower():
                return cls.SOLICITUD_ASISTENCIA_ELECTRICIDAD_EMERGENCIA
            else:
                raise ValueError("Invalid claim type in subject or body")
        elif COMUNICACION_MARKER in subject:
            return cls.COMUNICACION_A_COLABORADOR
        return None


class ClaimData(BaseModel):
    # Pinned semantics (spec C2): unknown kwargs rejected like the original
    # dataclass; `type` stays a ClaimType instance (no use_enum_values) — the
    # parity tests assert enum identity.
    model_config = ConfigDict(extra="forbid")

    year: str
    claim_number: str
    subject: str
    email_body: str
    type: ClaimType
    insurance_company: str | None = None
    nif: str | None = None
    address: str | None = None
    phone_number: str | None = None
    town: str | None = None
    description: str | None = None
    owner_name: str | None = None
    observaciones: str | None = None

    @classmethod
    def from_msg_data(
        cls, msg_data, extractor: "FieldExtractor | None" = None
    ) -> "ClaimData | None":
        subject = cls._extract_subject(msg_data)
        raw_body = cls._decode_body(msg_data)
        # Conversion happens upstream of the seam: the regex extractor is
        # coupled to _html_to_plain's exact whitespace output (spec REQ-2).
        body = cls._to_plain(raw_body)

        pattern = r"(\d{4})/(\d+)"
        match = re.search(pattern, subject or "")
        claim_type = ClaimType.from_subject(subject, body)
        if match and claim_type is not None:
            if extractor is None:
                # Local import: extraction imports ClaimType from this module.
                from pipeline.extraction import RegexFieldExtractor

                extractor = RegexFieldExtractor()
            fields = extractor.extract(claim_type, subject, body, raw_body)
            return cls(
                year=match.group(1),
                claim_number=match.group(2),
                type=claim_type,
                subject=subject,
                email_body=body,
                **fields.model_dump(),
            )
        return None

    @staticmethod
    def _extract_subject(msg_data) -> str | None:
        headers = msg_data.get("payload", {}).get("headers", [])
        return next((h["value"] for h in headers if h["name"] == "Subject"), None)

    @staticmethod
    def _decode_body(msg_data) -> str:
        """Base64/multipart walk — the body exactly as Gmail delivered it."""

        def get_plain_text(parts):
            for part in parts:
                mime = part.get("mimeType")
                body = part.get("body", {}).get("data")
                sub_parts = part.get("parts")

                if mime == "text/plain" and body:
                    return base64.urlsafe_b64decode(body).decode("utf-8", errors="replace")
                elif sub_parts:
                    result = get_plain_text(sub_parts)
                    if result:
                        return result
            return ""

        payload = msg_data.get("payload", {})
        parts = payload.get("parts", [])

        if parts:
            return get_plain_text(parts)
        body = payload.get("body", {}).get("data")
        return base64.urlsafe_b64decode(body).decode("utf-8", errors="replace") if body else ""

    @staticmethod
    def _extract_body(msg_data) -> str:
        """Decoded body, converted to plain text when it sniffs as HTML."""
        return ClaimData._to_plain(ClaimData._decode_body(msg_data))

    @staticmethod
    def _to_plain(raw: str) -> str:
        # Asitur direct emails declare Content-Type: text/plain but the body is
        # actually XHTML (Word template). Sniff and convert before downstream
        # regex extraction so labels like "Compañía:" aren't wrapped in tags.
        if ClaimData._looks_like_html(raw):
            return ClaimData._html_to_plain(raw)
        return raw

    @staticmethod
    def _looks_like_html(text: str) -> bool:
        head = text.lstrip()[:500].lower()
        return (
            head.startswith("<!doctype html")
            or head.startswith("<html")
            or "<style" in head
            or 'class="pt-fuentedeprrafopredeter' in head
        )

    @staticmethod
    def _html_to_plain(text: str) -> str:
        # Drop the entire <head> block — title/meta/style text would otherwise
        # leak into the body (e.g. Asitur's "Fax para: @p_NombreReceptor@" title).
        text = re.sub(r"<head[^>]*>.*?</head>", "", text, flags=re.DOTALL | re.IGNORECASE)
        # Belt-and-braces for HTML fragments that put style/script outside <head>.
        text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"<script[^>]*>.*?</script>", "", text, flags=re.DOTALL | re.IGNORECASE)
        # Use sentinels for explicit breaks so they survive the tag-and-whitespace collapse.
        text = re.sub(r"<br\s*/?>", "\x01", text, flags=re.IGNORECASE)
        text = re.sub(r"</p\s*>", "\x02", text, flags=re.IGNORECASE)
        # Strip remaining tags AND any surrounding whitespace — inline spans wrapping
        # "Compañía: " and "Reale" on separate source lines must collapse to one line.
        # Only matches tag-shaped <...> (starts with letter, /, !, or ?) so bare < > stays.
        text = re.sub(r"\s*<[/!?]?[a-zA-Z][^>]*>\s*", " ", text)
        text = html.unescape(text)
        text = text.replace("\x01", "\n").replace("\x02", "\n\n")
        # Tidy: collapse spaces/tabs, trim each line, cap blank-line runs at one.
        text = re.sub(r"[ \t]+", " ", text)
        text = "\n".join(line.strip() for line in text.split("\n"))
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()
