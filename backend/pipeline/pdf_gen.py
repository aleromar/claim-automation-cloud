"""Letterhead PDF generation, ported from ../claim_automation (spec 5a).

Deviations from the original (spec C3–C6): returns bytes instead of writing a
file; membretes fetched from a private Blob container via MembreteSource (the
PNGs carry personal data — never in this repo; D26 amended 2026-08-21);
ELECTRICIDAD_EMERGENCIA maps to the asistencia membrete; a missing or
unreadable membrete raises instead of rendering letterhead-less.
"""

import io
from xml.sax.saxutils import escape

from PIL import Image as PILImage
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer

from pipeline.claim_data import ClaimType
from pipeline.membrete_source import MembreteSource

MEMBRETE_BY_TYPE: dict[ClaimType, str] = {
    ClaimType.DECLARACION_SINIESTRO: "normal.png",
    ClaimType.DECLARACION_URGENTE: "urgente.png",
    ClaimType.SOLICITUD_ASISTENCIA_BRICO: "asistencia.png",
    ClaimType.SOLICITUD_ASISTENCIA_ENVIO_PROFESIONALES: "asistencia.png",
    ClaimType.SOLICITUD_ASISTENCIA_ELECTRICIDAD_EMERGENCIA: "asistencia.png",
}


def generate_pdf_from_email(
    email_text: str, claim_type: ClaimType, membrete_source: MembreteSource
) -> bytes:
    membrete_name = MEMBRETE_BY_TYPE.get(claim_type)
    if membrete_name is None:
        raise ValueError("Invalid claim type")
    membrete_png = membrete_source.get(membrete_name)

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=72,
        leftMargin=72,
        topMargin=72,
        bottomMargin=72,
    )

    styles = getSampleStyleSheet()
    normal = styles["Normal"]

    header_style = ParagraphStyle(
        "HeaderStyle",
        parent=normal,
        fontSize=13,  # Slightly larger than default
        spaceAfter=6,
        spaceBefore=6,
    )

    flowables = []

    # 1. Add the header image. No try/except: a failed fetch or unreadable
    # membrete must raise (spec C6), not ship a letterhead-less PDF. PIL.open
    # is the bytes-integrity check.
    with PILImage.open(io.BytesIO(membrete_png)) as pil_img:
        width_px, height_px = pil_img.size
        aspect_ratio = height_px / width_px

    img = Image(io.BytesIO(membrete_png))
    max_width = A4[0] - doc.leftMargin - doc.rightMargin
    img.drawWidth = max_width
    img.drawHeight = max_width * aspect_ratio
    flowables.append(img)
    flowables.append(Spacer(1, 0.2 * inch))

    # 2. Process and format plain text
    lines = email_text.splitlines()
    for line in lines:
        line = line.strip()
        if not line:
            continue

        # Section headers (ending with ':') — bold, italic, underline, and larger
        if line.endswith(":"):
            formatted = f"<u><i><b>{escape(line)}</b></i></u>"
            flowables.append(Paragraph(formatted, header_style))
            continue

        # Field + value (e.g. Nif: 12345678)
        elif ":" in line:
            field, value = line.split(":", 1)
            formatted = f"<b>{escape(field.strip())}:</b> {escape(value.strip())}"
            flowables.append(Paragraph(formatted, normal))
            flowables.append(Spacer(1, 4))

        else:
            flowables.append(Paragraph(escape(line), normal))
            flowables.append(Spacer(1, 3))

    # 3. Build the PDF
    doc.build(flowables)
    return buffer.getvalue()
