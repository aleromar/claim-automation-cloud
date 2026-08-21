"""XHTML-body parsing parity tests (spec REQ-3, ported from
../claim_automation/tests/unit/test_xhtml_email.py).

Asitur direct emails declare Content-Type: text/plain but ship a full XHTML
document generated from a Word template. The converter must strip it to plain
text before field extraction, or the regexes capture tag fragments.
"""

import base64
from pathlib import Path

from pipeline.claim_data import ClaimData, ClaimType

FIXTURES_DIR = Path(__file__).parent.parent / "data"


# A trimmed-but-representative slice of the real Asitur XHTML body, kept
# small enough to read but covering every label whose regex was breaking.
ASITUR_XHTML_BODY = """\
<!DOCTYPE html >
<html xmlns="http://www.w3.org/1999/xhtml">
  <head>
    <meta charset="UTF-8" />
    <title>Fax para: ...</title>
    <style>span { white-space: pre-wrap; }
span.pt-Fuentedeprrafopredeter-000011 { font-family: 'Times New Roman'; font-size: 11pt; }
p.pt-Normal-000006 { margin: 0; }
</style>
  </head>
  <body>
    <p dir="ltr" class="pt-Normal-000003">
      <span lang="es-ES_tradnl" class="pt-Fuentedeprrafopredeter-000004">Declaración de siniestro NORMAL</span>
    </p>
    <p dir="ltr" class="pt-Normal-000007">
      <span lang="es-ES_tradnl" class="pt-Fuentedeprrafopredeter-000008">Datos de la Entidad:</span>
    </p>
    <p dir="ltr" class="pt-Normal-000006">
      <span lang="es-ES" xml:space="preserve" class="pt-Fuentedeprrafopredeter-000010">Compañía: </span>
      <span lang="es-ES" class="pt-Fuentedeprrafopredeter-000011">Reale</span>
    </p>
    <p dir="ltr" class="pt-Normal-000014">
      <span lang="es-ES" xml:space="preserve" class="pt-Fuentedeprrafopredeter-000015">Referencia Asitur: </span>
      <span lang="es-ES" class="pt-Fuentedeprrafopredeter-000016">2026/999888</span>
    </p>
    <p dir="ltr" class="pt-Normal-000007">
      <span lang="es-ES_tradnl" class="pt-Fuentedeprrafopredeter-000008">Datos del Asegurado:</span>
    </p>
    <p dir="ltr" class="pt-Normal-000006">
      <span lang="es-ES" xml:space="preserve" class="pt-Fuentedeprrafopredeter-000010">Tomador: </span>
      <span lang="es-ES" class="pt-Fuentedeprrafopredeter-000011">JUAN PEREZ EJEMPLO</span>
    </p>
    <p dir="ltr" class="pt-Normal-000019">
      <span lang="es-ES" xml:space="preserve" class="pt-Fuentedeprrafopredeter-000010">Nif: </span>
      <span lang="es-ES" class="pt-Fuentedeprrafopredeter-000011">00000000A</span>
    </p>
    <p dir="ltr" class="pt-Normal-000007">
      <span lang="es-ES_tradnl" class="pt-Fuentedeprrafopredeter-000008">Datos del Siniestro:</span>
    </p>
    <p dir="ltr" class="pt-Normal-000006">
      <span lang="es-ES_tradnl" xml:space="preserve" class="pt-Fuentedeprrafopredeter-000010">Dirección: </span>
      <span class="pt-Fuentedeprrafopredeter-000022"></span>
    </p>
    <p dir="ltr" class="pt-Normal-000006">
      <span lang="es-ES" xml:space="preserve" class="pt-Fuentedeprrafopredeter-000010">Localidad: </span>
      <span lang="es-ES" class="pt-Fuentedeprrafopredeter-000011">VILLANUEVA</span>
    </p>
    <p dir="ltr" class="pt-Normal-000006">
      <span lang="es-ES_tradnl" class="pt-Fuentedeprrafopredeter-000010">Código Postal:</span>
      <span lang="es-ES" class="pt-Fuentedeprrafopredeter-000011">34880</span>
      <span lang="es-ES_tradnl" xml:space="preserve" class="pt-Fuentedeprrafopredeter-000010">Provincia: </span>
      <span lang="es-ES" class="pt-Fuentedeprrafopredeter-000011">PALENCIA</span>
    </p>
    <p dir="ltr" class="pt-Normal-000006">
      <span lang="es-ES" xml:space="preserve" class="pt-Fuentedeprrafopredeter-000010">Descripción: </span>
      <span lang="es-ES" xml:space="preserve" class="pt-Fuentedeprrafopredeter-000011">VECINA DE ABAJO TIENE DAÑOS AGUA EN TECHOS DE LOS BAÑOS
CONTACTO: PERSONA UNO ¿600 00 00 01
CONTACTO PERJ.: PERSONA DOS 1ºC - +34 600 00 00 02 </span>
    </p>
    <p dir="ltr" class="pt-Normal-000006">
      <span lang="es-ES" xml:space="preserve" class="pt-Fuentedeprrafopredeter-000010">Tipo: </span>
      <span lang="es-ES" class="pt-Fuentedeprrafopredeter-000011">Reparable</span>
    </p>
    <p dir="ltr" class="pt-Normal-000033">
      <span class="pt-Fuentedeprrafopredeter-000011">Rep. generales del hogar   AGENTE EJEMPLO LOPEZ  - VILLANUEVA  </span>
      <span class="pt-Fuentedeprrafopredeter-000010">Tfno:</span>
      <span xml:space="preserve" class="pt-Fuentedeprrafopredeter-000011">911111111</span>
    </p>
  </body>
</html>
"""

ASITUR_SUBJECT = "2026/999888 Declaración de siniestro a colaborador NORMAL (H)Envio N-EXAMPLE3"


def _make_gmail_message(subject: str, body: str, mime: str = "text/plain") -> dict:
    encoded_body = base64.urlsafe_b64encode(body.encode("utf-8")).decode("ascii")
    return {
        "payload": {
            "headers": [{"name": "Subject", "value": subject}],
            "parts": [
                {
                    "mimeType": mime,
                    "body": {"data": encoded_body},
                }
            ],
        }
    }


class TestXhtmlDetection:
    def test_doctype_html_is_detected(self):
        assert ClaimData._looks_like_html("<!DOCTYPE html>\n<html>...")

    def test_html_tag_is_detected(self):
        assert ClaimData._looks_like_html("<html><body>foo</body></html>")

    def test_asitur_class_marker_is_detected(self):
        assert ClaimData._looks_like_html(
            '<div><span class="pt-Fuentedeprrafopredeter-000011">x</span></div>'
        )

    def test_plain_text_is_not_flagged(self):
        plain = "Comunicación para: AGENTE\n\nCompañía: Reale\n"
        assert not ClaimData._looks_like_html(plain)

    def test_lt_gt_in_text_is_not_flagged(self):
        # Real observaciones contain literal "< <" / ">>" — must not trigger.
        assert not ClaimData._looks_like_html(
            "Observaciones: Enviado por < < COMUNICACION REALE >> < < 28/05 >>"
        )


class TestHtmlToPlain:
    def test_strips_tags_keeps_text(self):
        out = ClaimData._html_to_plain('<p><span class="x">Compañía: </span><span>Reale</span></p>')
        assert "Compañía: Reale" in out
        assert "<span" not in out
        assert 'class="x"' not in out

    def test_drops_style_block_entirely(self):
        out = ClaimData._html_to_plain(
            "<html><head><style>span { color: red; }</style></head><body><p>Hello</p></body></html>"
        )
        assert "color: red" not in out
        assert "Hello" in out

    def test_drops_head_block_entirely(self):
        # Asitur's <title> contains a template variable ("Fax para: @p_NombreReceptor@")
        # that would otherwise render as a stray field row in the PDF.
        out = ClaimData._html_to_plain(
            "<html><head>"
            "<meta charset='UTF-8' />"
            "<title>Fax para: @p_NombreReceptor (p_NumeroFax)@</title>"
            "<style>span { white-space: pre-wrap; }</style>"
            "</head>"
            "<body><p>Declaración de siniestro NORMAL</p></body></html>"
        )
        assert "Fax para" not in out
        assert "NombreReceptor" not in out
        assert "white-space" not in out
        assert "Declaración de siniestro NORMAL" in out

    def test_unescapes_entities(self):
        out = ClaimData._html_to_plain("<p>Da&ntilde;os &amp; perjuicios</p>")
        assert "Daños & perjuicios" in out

    def test_keeps_bare_lt_gt(self):
        # Strip should not touch literal "< <" since it's not a tag-shape.
        out = ClaimData._html_to_plain("<p>Enviado por < < COMUNICACION >></p>")
        assert "< < COMUNICACION >>" in out


class TestAsiturXhtmlEndToEnd:
    """The full pipeline on an Asitur XHTML-as-plain body."""

    def test_body_extraction_returns_clean_text(self):
        msg = _make_gmail_message(ASITUR_SUBJECT, ASITUR_XHTML_BODY)
        body = ClaimData._extract_body(msg)
        assert "<span" not in body
        assert "<!DOCTYPE" not in body
        assert 'class="pt-' not in body
        assert "Compañía: Reale" in body
        assert "Tomador: JUAN PEREZ EJEMPLO" in body
        assert "Descripción: VECINA DE ABAJO" in body

    def test_claim_classification(self):
        msg = _make_gmail_message(ASITUR_SUBJECT, ASITUR_XHTML_BODY)
        claim = ClaimData.from_msg_data(msg)
        assert claim is not None
        assert claim.type is ClaimType.DECLARACION_SINIESTRO
        assert claim.year == "2026"
        assert claim.claim_number == "999888"

    def test_fields_extracted_cleanly(self):
        msg = _make_gmail_message(ASITUR_SUBJECT, ASITUR_XHTML_BODY)
        claim = ClaimData.from_msg_data(msg)
        assert claim.insurance_company == "Reale"
        assert claim.nif == "00000000A"
        assert claim.owner_name == "JUAN PEREZ EJEMPLO"
        assert claim.town == "VILLANUEVA"
        assert claim.phone_number == "911111111"
        assert claim.description is not None
        assert claim.description.startswith("VECINA DE ABAJO")
        # No HTML markup leaked into any field.
        for field in (
            claim.insurance_company,
            claim.nif,
            claim.owner_name,
            claim.town,
            claim.description,
            claim.phone_number,
        ):
            assert "<" not in (field or "")
            assert "span" not in (field or "").lower()

    def test_empty_direccion_does_not_swallow_next_line(self):
        """Asitur direct emails leave Dirección blank and put the street on
        the next line under Implicados. The address regex must not walk past
        the newline and capture 'Localidad: VILLANUEVA' as the address."""
        msg = _make_gmail_message(ASITUR_SUBJECT, ASITUR_XHTML_BODY)
        claim = ClaimData.from_msg_data(msg)
        assert claim.address != "Localidad: VILLANUEVA"
        assert not (claim.address or "").startswith("Localidad:")


class TestRealAsiturXhtmlFixture:
    """Regression test against the redacted ~34 KB XHTML body taken from a
    real direct-from-Asitur claim email. Catches breakage if the strip or
    extraction ever regresses on the full template volume (multiple sections,
    Implicados table, Observaciones, CUADRO DE COBERTURAS, etc.).

    The fixture lives at tests/data/asitur_xhtml_sample.html with all PII
    replaced by clearly-fake placeholders (JUAN PEREZ EJEMPLO, 00000000A,
    CALLE FALSA, 911111111, claim 2099/000001, etc.).
    """

    FIXTURE_PATH = FIXTURES_DIR / "asitur_xhtml_sample.html"
    SUBJECT = "2099/000001 Declaración de siniestro a colaborador NORMAL (H)Envio N-EXAMPLE"

    def _load_msg(self):
        body = self.FIXTURE_PATH.read_text()
        return _make_gmail_message(self.SUBJECT, body)

    def test_fixture_file_exists(self):
        assert self.FIXTURE_PATH.exists(), (
            f"Missing fixture {self.FIXTURE_PATH}. Copy the redacted sample "
            "from ../claim_automation/tests/data/."
        )

    def test_body_is_detected_as_html_and_stripped(self):
        msg = self._load_msg()
        body = ClaimData._extract_body(msg)
        # Should detect the leading <!DOCTYPE
        assert "<!doctype" not in body.lower()
        assert "<span" not in body.lower()
        assert "<style" not in body.lower()
        assert 'class="pt-' not in body.lower()
        # And shouldn't leak the <title> from <head>
        assert "Fax para" not in body
        assert "NombreReceptor" not in body

    def test_extracted_fields_match_redacted_values(self):
        msg = self._load_msg()
        claim = ClaimData.from_msg_data(msg)
        assert claim is not None
        assert claim.type is ClaimType.DECLARACION_SINIESTRO
        assert claim.year == "2099"
        assert claim.claim_number == "000001"
        assert claim.insurance_company == "Reale"
        assert claim.nif == "00000000A"
        assert claim.owner_name == "JUAN PEREZ EJEMPLO"
        assert claim.town == "VILLANUEVA"
        # Asitur leaves Dirección blank in the body; same-line capture → empty.
        assert claim.address == ""
        # Phone comes from the Interviene line (top of "Implicados" block).
        assert claim.phone_number == "911111111"
        assert claim.description is not None
        assert claim.description.startswith("VECINA DE ABAJO")

    def test_all_major_sections_survive_the_strip(self):
        """The full Asitur template includes more than just the field rows.
        Verify the rich content blocks (which earlier broken extractions
        dropped or mangled) are all present in the stripped body."""
        msg = self._load_msg()
        body = ClaimData._extract_body(msg)
        assert "Datos de la Entidad:" in body
        assert "Datos del Asegurado:" in body
        assert "Datos del Siniestro:" in body
        assert "Implicados:" in body
        assert "Interviene:" in body
        assert "Observaciones:" in body
        assert "CUADRO DE COBERTURAS" in body

    def test_strip_shrinks_body_significantly(self):
        """Sanity: the stripped output should be much smaller than the
        XHTML input (≥80% reduction, mostly from removing the <style>
        block and tag verbosity). If this ever stops shrinking, the
        strip is likely no-op'ing — investigate."""
        msg = self._load_msg()
        body = ClaimData._extract_body(msg)
        original = self.FIXTURE_PATH.read_text()
        assert len(body) < len(original) * 0.2

    def test_no_pii_leaked_back_into_fixture(self):
        """Belt-and-braces: the fixture must contain only the fake redacted
        placeholders. If someone re-saves an unredacted real email over it,
        these placeholders disappear and this test fails."""
        original = self.FIXTURE_PATH.read_text()
        for expected in ("JUAN PEREZ EJEMPLO", "00000000A", "911111111"):
            assert expected in original, (
                f"Expected redacted placeholder {expected!r} missing from "
                "fixture — it may have been overwritten with a real email."
            )
