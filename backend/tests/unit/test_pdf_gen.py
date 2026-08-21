"""PDF generation tests (spec REQ-4), ported from
../claim_automation/tests/unit/test_pdf_gen.py and adapted to the bytes API
(spec C3), the electricidad membrete fix (C4), and fail-fast assets (C6).

Membretes are blob-hosted (C3 REVISED 2026-08-21 — the real PNGs carry personal
data and never live in this repo). Unit tests inject a fake MembreteSource with
synthetic letterheads; the real blob path is covered by
tests/integration/test_membrete_blob.py against Azurite.
"""

import io

import pytest
from PIL import Image as PILImage
from PIL import UnidentifiedImageError

from pipeline.claim_data import ClaimType
from pipeline.pdf_gen import MEMBRETE_BY_TYPE, generate_pdf_from_email

SAMPLE_BODY = "Compañía: Reale\nNif: H34140152\nDirección: CL SAN LAZARO 2\n"


def synthetic_membrete_png(width: int = 600, height: int = 80) -> bytes:
    """A plain white banner — sanitized stand-in for the real letterhead."""
    buf = io.BytesIO()
    PILImage.new("RGB", (width, height), "white").save(buf, format="PNG")
    return buf.getvalue()


class FakeMembreteSource:
    """Dict-backed MembreteSource; missing name raises like a missing blob."""

    def __init__(self, blobs: dict[str, bytes]) -> None:
        self.blobs = blobs

    def get(self, name: str) -> bytes:
        if name not in self.blobs:
            raise FileNotFoundError(name)
        return self.blobs[name]


@pytest.fixture
def membretes() -> FakeMembreteSource:
    png = synthetic_membrete_png()
    return FakeMembreteSource({"normal.png": png, "urgente.png": png, "asistencia.png": png})


def _assert_valid_pdf_with_letterhead(pdf: bytes) -> None:
    assert isinstance(pdf, bytes)
    assert pdf[:4] == b"%PDF"
    # The membrete must actually be embedded (C6): a letterhead-less PDF still
    # starts with %PDF, so magic bytes alone can't catch a missing asset.
    assert b"/XObject" in pdf


def test_generate_pdf_siniestro(membretes):
    pdf = generate_pdf_from_email(
        SAMPLE_BODY, claim_type=ClaimType.DECLARACION_SINIESTRO, membrete_source=membretes
    )
    _assert_valid_pdf_with_letterhead(pdf)


def test_generate_pdf_urgente(membretes):
    pdf = generate_pdf_from_email(
        SAMPLE_BODY, claim_type=ClaimType.DECLARACION_URGENTE, membrete_source=membretes
    )
    _assert_valid_pdf_with_letterhead(pdf)


def test_generate_pdf_brico(membretes):
    pdf = generate_pdf_from_email(
        SAMPLE_BODY, claim_type=ClaimType.SOLICITUD_ASISTENCIA_BRICO, membrete_source=membretes
    )
    _assert_valid_pdf_with_letterhead(pdf)


def test_generate_pdf_envio_profesionales(membretes):
    pdf = generate_pdf_from_email(
        SAMPLE_BODY,
        claim_type=ClaimType.SOLICITUD_ASISTENCIA_ENVIO_PROFESIONALES,
        membrete_source=membretes,
    )
    _assert_valid_pdf_with_letterhead(pdf)


def test_generate_pdf_electricidad_uses_asistencia_membrete(membretes):
    """Spec C4: latent bug fixed — the original raised for this type even
    though classification produced it; it now renders like its siblings."""
    pdf = generate_pdf_from_email(
        SAMPLE_BODY,
        claim_type=ClaimType.SOLICITUD_ASISTENCIA_ELECTRICIDAD_EMERGENCIA,
        membrete_source=membretes,
    )
    _assert_valid_pdf_with_letterhead(pdf)


def test_membrete_mapping_is_pinned():
    """A wrong-membrete regression renders a structurally valid PDF the other
    assertions can't distinguish, so the mapping itself is pinned to literals."""
    assert MEMBRETE_BY_TYPE == {
        ClaimType.DECLARACION_SINIESTRO: "normal.png",
        ClaimType.DECLARACION_URGENTE: "urgente.png",
        ClaimType.SOLICITUD_ASISTENCIA_BRICO: "asistencia.png",
        ClaimType.SOLICITUD_ASISTENCIA_ENVIO_PROFESIONALES: "asistencia.png",
        ClaimType.SOLICITUD_ASISTENCIA_ELECTRICIDAD_EMERGENCIA: "asistencia.png",
    }


def test_only_comunicacion_is_unmapped():
    """Guards 5a2/future enum additions: a new ClaimType must get a membrete
    (or an explicit decision), never fall into the raise by accident."""
    assert set(ClaimType) - set(MEMBRETE_BY_TYPE) == {ClaimType.COMUNICACION_A_COLABORADOR}


def test_body_text_grows_the_pdf(membretes):
    """A broken flowable loop would ship letterhead-only PDFs that still pass
    the structural assertions."""
    with_body = generate_pdf_from_email(
        SAMPLE_BODY, claim_type=ClaimType.DECLARACION_SINIESTRO, membrete_source=membretes
    )
    empty = generate_pdf_from_email(
        "", claim_type=ClaimType.DECLARACION_SINIESTRO, membrete_source=membretes
    )
    assert len(with_body) > len(empty)


def test_generate_pdf_invalid_type_raises(membretes):
    with pytest.raises(ValueError, match="Invalid claim type"):
        generate_pdf_from_email(
            SAMPLE_BODY,
            claim_type=ClaimType.COMUNICACION_A_COLABORADOR,
            membrete_source=membretes,
        )


def test_missing_membrete_raises(membretes):
    """Spec C6: a missing asset must fail loud, not render letterhead-less
    (the original logged and continued)."""
    del membretes.blobs["normal.png"]
    with pytest.raises(FileNotFoundError):
        generate_pdf_from_email(
            SAMPLE_BODY, claim_type=ClaimType.DECLARACION_SINIESTRO, membrete_source=membretes
        )


def test_corrupt_membrete_raises(membretes):
    """Spec C6 (revised): PIL.open on the fetched bytes is the integrity check."""
    membretes.blobs["normal.png"] = b"not a png"
    with pytest.raises(UnidentifiedImageError):
        generate_pdf_from_email(
            SAMPLE_BODY, claim_type=ClaimType.DECLARACION_SINIESTRO, membrete_source=membretes
        )


def test_generate_pdf_with_stray_xhtml_markup_in_body(membretes):
    """Email bodies occasionally leak raw XHTML fragments (e.g. xmlns declarations).

    ReportLab's Paragraph treats input as XML mini-markup, so any '<...>' in the
    body — combined with pdf_gen's 'split on first colon and wrap in <b>' logic
    on a URL like 'http://...' — produces malformed nested tags and crashes.

    The PDF generator must be robust to such markup.
    """
    body_with_markup = (
        "Compañía: Reale\n"
        '<para><b><html xmlns="http://www.w3.org/1999/xhtml"></para>\n'
        "Nif: H34140152\n"
    )
    pdf = generate_pdf_from_email(
        body_with_markup, claim_type=ClaimType.DECLARACION_SINIESTRO, membrete_source=membretes
    )
    _assert_valid_pdf_with_letterhead(pdf)


def test_generate_pdf_with_ampersand_and_brackets(membretes):
    """Lone '&', '<', '>' characters must not break the paragraph parser."""
    body = "Observaciones: Daños < 2000 € & otros\nDescripción: rotura tubería\n"
    pdf = generate_pdf_from_email(
        body, claim_type=ClaimType.DECLARACION_SINIESTRO, membrete_source=membretes
    )
    _assert_valid_pdf_with_letterhead(pdf)
