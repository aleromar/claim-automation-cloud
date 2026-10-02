"""Classification parity tests for the ported pipeline (spec REQ-1).

Fixtures ported from ../claim_automation/tests/unit/test_claim_classification.py
(the two Trello-coupled tests moved to 5c — spec N2). New per-category cases and
the no-`YYYY/N`-pattern branch cover the paths the original suite left implicit.
"""

import base64

import pytest

from pipeline.claim_data import ClaimData, ClaimType

# ---------------------------------------------------------------------------
# Fixtures — raw emails encoded as Gmail API messages
# ---------------------------------------------------------------------------

NORMAL_SINIESTRO_SUBJECT = (
    "2026/123456 Declaración de siniestro a colaborador NORMAL (H)Envio N-EXAMPLE1"
)

NORMAL_SINIESTRO_BODY = """\
Comunicación para: AGENTE EJEMPLO LOPEZ - VILLANUEVA


 Declaración de siniestro NORMAL


Fecha de envío: 01/02/2026 Hora: 17:29


Datos de la Entidad:


Compañía: Reale

N° Póliza: 8310000000001

Referª. Cía:

Referencia Asitur: 2026/123456

Observaciones póliza: MODIFICADA: 20240101 10:00


Conditional XPath expression (./isExpatriate ) returned no results

Datos del Asegurado:


Tomador: CDAD DE PROPIETARIOS EDIFICIO EJEMPLO 7

Nif: H12345678

Producto: REALE 2 831-REA-2-NC COMUNIDADES Modalidad: Comunidades

Continente: 1.000.000,00 Contenido: 5.000,00

Fecha efecto: 01/01/2020




Datos del Siniestro:


Dirección: CL FICTICIA 12

Localidad: VILLANUEVA

Código Postal: 34999 Provincia: PALENCIA

Tipo siniestro: Extensivos NO agua

Causa: Otras causas

Descripción: filtración de agua desde la cubierta causa manchas en el techo del portal del edificio

Tipo: Reparable

Fecha Ocurrencia: 01/02/2026

Daños Estéticos Continente : 1000 €

Histórico de Siniestros:


Implicados:



‎ Asegurado: OPERARIO CDAD DE PROPIETARIOS EDIFICIO EJEMPLO 7
‎   Email:
‎   Dirección: CL FICTICIA 12
‎   Tfno : 600000000 administrador Franja1: 00:00 - 00:00 Franja2: -
‎ Perjudicado: OPERARIO CDAD DE PROPIETARIOS EDIFICIO EJEMPLO 7
‎   Email:
‎   Dirección: RESIDENCIA FICTICIA LA COLINA
‎   Tfno : Franja1: Franja2:
"""

URGENTE_SINIESTRO_SUBJECT = (
    "2026/654321 Declaración de siniestro a colaborador URGENTE (H)Envio N-EXAMPLE2"
)

URGENTE_SINIESTRO_BODY = """\
Comunicación para: AGENTE EJEMPLO LOPEZ - VILLANUEVA


 Declaración de siniestro URGENTE


Fecha de envío: 02/02/2026 Hora: 12:37


Datos de la Entidad:


Compañía: Reale

N° Póliza: 8310000000002

Referª. Cía:

Referencia Asitur: 2026/654321

Observaciones póliza: MODIFICADA: 20240202 11:00


Conditional XPath expression (./isExpatriate ) returned no results

Datos del Asegurado:


Tomador: CDAD DE PROPIETARIOS AVENIDA INVENTADA 3

Nif: H87654321

Producto: 831-REA-1-NC COMUNIDADES Modalidad: Comunidades

Continente: 2.000.000,00 Contenido: 3.000,00

Fecha efecto: 01/01/2018



Datos del Siniestro:


Dirección: AV INVENTADA 3

Localidad: VILLANUEVA

Código Postal: 34999 Provincia: PALENCIA

Tipo siniestro: Daños por agua

Causa: Rotura de Tubería Empotrada

Descripción: gotea agua por tubería vista en el trastero, sin daños aparentes, límite 500€ al año

Tipo: Reparable

Fecha Ocurrencia: 02/02/2026

Daños Estéticos Continente :

Histórico de Siniestros:


Implicados:



‎ Asegurado: CDAD DE PROPIETARIOS AVENIDA INVENTADA 3
‎   Email: cliente@example.com
‎   Dirección: AV INVENTADA 3
‎   Tfno : 600000000 operario (administracion) Franja1: 00:00 - 00:00 Franja2: -
"""

ASISTENCIA_SUBJECT = "2026/700123 Solicitud de asistencia a colaborador (H)Envio N-ANEXAMPL"

# Minimal synthetic asistencia bodies — classification keys on the service-name
# substring only; no redacted real samples exist for these categories yet.
BRICO_BODY = """\
Comunicación para: AGENTE EJEMPLO LOPEZ - VILLANUEVA

SERVICIO BRICO HOGAR

Compañía: Reale

Tomador: JUAN PEREZ EJEMPLO

Nif: 00000000A
"""

ENVIO_PROFESIONALES_BODY = BRICO_BODY.replace("SERVICIO BRICO HOGAR", "ENVÍO DE PROFESIONALES")

ELECTRICIDAD_BODY = BRICO_BODY.replace("SERVICIO BRICO HOGAR", "ELECTRICIDAD DE EMERGENCIA")

NO_SERVICE_BODY = BRICO_BODY.replace("SERVICIO BRICO HOGAR", "SERVICIO DESCONOCIDO")

COMUNICACION_SUBJECT = "2026/555001 Comunicación a colaborador (H)Envio N-ANEXAMPL"

COMUNICACION_BODY = """\
Comunicación para: AGENTE EJEMPLO LOPEZ - VILLANUEVA

Referencia Asitur: 2026/555001

Observaciones: Enviado por < < COMUNICACION REALE >> presupuesto aprobado
--
AVISO LEGAL: footer
"""


def _make_gmail_message(subject: str | None, body: str) -> dict:
    """Build a minimal Gmail API message dict."""
    encoded_body = base64.urlsafe_b64encode(body.encode("utf-8")).decode("ascii")
    headers = [] if subject is None else [{"name": "Subject", "value": subject}]
    return {
        "payload": {
            "headers": headers,
            "parts": [
                {
                    "mimeType": "text/plain",
                    "body": {"data": encoded_body},
                }
            ],
        }
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestNormalSiniestroClassification:
    """A 'NORMAL' siniestro must never be classified as URGENTE."""

    def test_claim_type_is_declaracion_siniestro(self):
        msg = _make_gmail_message(NORMAL_SINIESTRO_SUBJECT, NORMAL_SINIESTRO_BODY)
        claim = ClaimData.from_msg_data(msg)

        assert claim is not None
        assert claim.type is ClaimType.DECLARACION_SINIESTRO

    def test_claim_type_is_not_urgente(self):
        msg = _make_gmail_message(NORMAL_SINIESTRO_SUBJECT, NORMAL_SINIESTRO_BODY)
        claim = ClaimData.from_msg_data(msg)

        assert claim is not None
        assert claim.type is not ClaimType.DECLARACION_URGENTE

    def test_enum_members_are_distinct(self):
        """The two enum members must not be aliases of each other."""
        assert ClaimType.DECLARACION_SINIESTRO is not ClaimType.DECLARACION_URGENTE


class TestUrgenteSiniestroClassification:
    """An 'URGENTE' siniestro must be classified as DECLARACION_URGENTE."""

    def test_claim_type_is_declaracion_urgente(self):
        msg = _make_gmail_message(URGENTE_SINIESTRO_SUBJECT, URGENTE_SINIESTRO_BODY)
        claim = ClaimData.from_msg_data(msg)

        assert claim is not None
        assert claim.type is ClaimType.DECLARACION_URGENTE

    def test_claim_type_is_not_normal(self):
        msg = _make_gmail_message(URGENTE_SINIESTRO_SUBJECT, URGENTE_SINIESTRO_BODY)
        claim = ClaimData.from_msg_data(msg)

        assert claim is not None
        assert claim.type is not ClaimType.DECLARACION_SINIESTRO


class TestAsistenciaClassification:
    """The three 'Solicitud de asistencia' variants key on the body service name."""

    def test_brico(self):
        msg = _make_gmail_message(ASISTENCIA_SUBJECT, BRICO_BODY)
        claim = ClaimData.from_msg_data(msg)

        assert claim is not None
        assert claim.type is ClaimType.SOLICITUD_ASISTENCIA_BRICO

    def test_envio_profesionales(self):
        msg = _make_gmail_message(ASISTENCIA_SUBJECT, ENVIO_PROFESIONALES_BODY)
        claim = ClaimData.from_msg_data(msg)

        assert claim is not None
        assert claim.type is ClaimType.SOLICITUD_ASISTENCIA_ENVIO_PROFESIONALES

    def test_electricidad_emergencia(self):
        msg = _make_gmail_message(ASISTENCIA_SUBJECT, ELECTRICIDAD_BODY)
        claim = ClaimData.from_msg_data(msg)

        assert claim is not None
        assert claim.type is ClaimType.SOLICITUD_ASISTENCIA_ELECTRICIDAD_EMERGENCIA

    def test_unrecognized_service_raises(self):
        msg = _make_gmail_message(ASISTENCIA_SUBJECT, NO_SERVICE_BODY)
        with pytest.raises(ValueError):
            ClaimData.from_msg_data(msg)


class TestComunicacionClassification:
    def test_claim_type_is_comunicacion(self):
        msg = _make_gmail_message(COMUNICACION_SUBJECT, COMUNICACION_BODY)
        claim = ClaimData.from_msg_data(msg)

        assert claim is not None
        assert claim.type is ClaimType.COMUNICACION_A_COLABORADOR

    def test_observaciones_extracted_up_to_footer(self):
        msg = _make_gmail_message(COMUNICACION_SUBJECT, COMUNICACION_BODY)
        claim = ClaimData.from_msg_data(msg)

        assert claim is not None
        assert claim.observaciones is not None
        assert claim.observaciones.startswith("Enviado por")
        assert "AVISO LEGAL" not in claim.observaciones


class TestNotAClaim:
    def test_unknown_subject_returns_none(self):
        msg = _make_gmail_message("Informe mensual BBVA Julio 2026", "cuerpo irrelevante")
        assert ClaimData.from_msg_data(msg) is None

    def test_missing_subject_header_returns_none(self):
        msg = _make_gmail_message(None, "cuerpo irrelevante")
        assert ClaimData.from_msg_data(msg) is None

    def test_category_match_without_claim_number_returns_none(self):
        """Inherited branch (spec REQ-1): a recognized category whose subject
        lacks the YYYY/N pattern is still not a claim."""
        msg = _make_gmail_message(
            "Declaración de siniestro a colaborador NORMAL", NORMAL_SINIESTRO_BODY
        )
        assert ClaimData.from_msg_data(msg) is None

    def test_year_and_claim_number_parsed_from_subject(self):
        msg = _make_gmail_message(NORMAL_SINIESTRO_SUBJECT, NORMAL_SINIESTRO_BODY)
        claim = ClaimData.from_msg_data(msg)

        assert claim is not None
        assert claim.year == "2026"
        assert claim.claim_number == "123456"


class TestGmailPayloadShapes:
    """Pin the inherited decode behavior for payload shapes the happy-path
    fixtures don't cover (Gate 3 H3)."""

    def test_flat_payload_without_parts_decodes(self):
        encoded = base64.urlsafe_b64encode(NORMAL_SINIESTRO_BODY.encode("utf-8")).decode("ascii")
        msg = {
            "payload": {
                "headers": [{"name": "Subject", "value": NORMAL_SINIESTRO_SUBJECT}],
                "body": {"data": encoded},
            }
        }
        claim = ClaimData.from_msg_data(msg)

        assert claim is not None
        assert claim.insurance_company == "Reale"

    def test_html_only_payload_yields_claim_with_empty_fields(self):
        """Inherited behavior, pinned: a message with no text/plain part decodes
        to an empty body; a siniestro still classifies on the subject alone and
        produces a claim whose extracted fields are all None. Whether 5b/5c
        should treat this differently is that spec's decision, not a parity
        change here."""
        encoded = base64.urlsafe_b64encode(b"<html><body>x</body></html>").decode("ascii")
        msg = {
            "payload": {
                "headers": [{"name": "Subject", "value": NORMAL_SINIESTRO_SUBJECT}],
                "parts": [{"mimeType": "text/html", "body": {"data": encoded}}],
            }
        }
        claim = ClaimData.from_msg_data(msg)

        assert claim is not None
        assert claim.email_body == ""
        assert claim.insurance_company is None
        assert claim.nif is None


def test_every_claim_subject_marker_is_recognized_by_from_subject():
    # gmail-client C6 / Gate 2 finding 1: the probe counts by CLAIM_SUBJECT_MARKERS —
    # every marker must be a subject from_subject actually classifies, or the probe
    # would count emails classification later rejects.
    from pipeline.claim_data import CLAIM_SUBJECT_MARKERS

    for marker in CLAIM_SUBJECT_MARKERS:
        claim_type = ClaimType.from_subject(
            f"AVISO: {marker} 2026/1", "atención: SERVICIO BRICO HOGAR"
        )
        assert claim_type is not None, f"marker not classified: {marker!r}"


# ---------------------------------------------------------------------------
# Gestión con Perito (gestion-perito spec, roadmap #24)
# ---------------------------------------------------------------------------

GESTION_SUBJECT = "209901000017 Gestión con Perito"

GESTION_NOTICE = (
    "Le informamos que en este expediente interviene el perito PERITO EJEMPLO.Teléfono de "
    "contacto 900 000 017 Correo Electrónico perito.ejemplo@example.com\n"
    "No continúe con los trabajos y permanezca a la espera de recibir instrucciones del perito "
    "para que valore el siniestro.\n"
    "Hasta que no obtenga el conforme pericial, le recordamos que no dispone de autorización "
    "para continuar con los trabajos."
)

GESTION_BODY = GESTION_NOTICE + "\n\nAVISO LEGAL:\nEste correo y sus archivos asociados ...\n"


class TestGestionConPeritoClassification:
    def test_marker_is_fetched_by_the_gmail_query(self):
        from pipeline.claim_data import CLAIM_SUBJECT_MARKERS, GESTION_PERITO_MARKER
        from pipeline.entry import build_claim_query

        assert GESTION_PERITO_MARKER == "Gestión con Perito"  # accent kept: Gmail search needs it
        assert GESTION_PERITO_MARKER in CLAIM_SUBJECT_MARKERS
        assert f'subject:"{GESTION_PERITO_MARKER}"' in build_claim_query()

    def test_claim_type_value_is_the_marker(self):
        from pipeline.claim_data import GESTION_PERITO_MARKER

        assert ClaimType.GESTION_CON_PERITO.value == GESTION_PERITO_MARKER

    def test_classified_regardless_of_body(self):
        assert ClaimType.from_subject(GESTION_SUBJECT, None) is ClaimType.GESTION_CON_PERITO
        assert ClaimType.from_subject(GESTION_SUBJECT, "") is ClaimType.GESTION_CON_PERITO

    def test_wins_over_another_marker_without_raising(self):
        # Checked first: the asistencia branch would raise without a body service marker.
        subject = "209901000017 Gestión con Perito — Solicitud de asistencia a colaborador"
        assert ClaimType.from_subject(subject, "sin servicio") is ClaimType.GESTION_CON_PERITO

    def test_slashless_subject_parses_year_and_number(self):
        claim = ClaimData.from_msg_data(_make_gmail_message(GESTION_SUBJECT, GESTION_BODY))

        assert claim is not None
        assert claim.type is ClaimType.GESTION_CON_PERITO
        assert (claim.year, claim.claim_number) == ("2099", "1000017")

    def test_regex_notice_is_the_body_up_to_aviso_legal(self):
        claim = ClaimData.from_msg_data(_make_gmail_message(GESTION_SUBJECT, GESTION_BODY))

        assert claim is not None
        assert claim.observaciones == GESTION_NOTICE
        assert claim.insurance_company is None and claim.town is None

    def test_regex_notice_without_aviso_legal_is_the_whole_body(self):
        claim = ClaimData.from_msg_data(_make_gmail_message(GESTION_SUBJECT, GESTION_NOTICE + "\n"))

        assert claim is not None
        assert claim.observaciones == GESTION_NOTICE

    def test_regex_notice_trims_crlf_and_emphasis_markers(self):
        body = "\r\n" + GESTION_NOTICE.replace("\n", "\r\n") + "\r\n*AVISO LEGAL:*\r\nresto"
        claim = ClaimData.from_msg_data(_make_gmail_message(GESTION_SUBJECT, body))

        assert claim is not None
        assert claim.observaciones == GESTION_NOTICE.replace("\n", "\r\n")

    def test_regex_notice_of_an_empty_body_is_none(self):
        claim = ClaimData.from_msg_data(_make_gmail_message(GESTION_SUBJECT, "\nAVISO LEGAL: x"))

        assert claim is not None
        assert claim.observaciones is None


class TestParseClaimRef:
    @pytest.mark.parametrize(
        "subject,expected",
        [
            ("202601234567 Gestión con Perito", ("2026", "1234567")),
            ("Fwd: 202609876543 Gestión con Perito", ("2026", "9876543")),
            ("202600001234 Gestión con Perito", ("2026", "1234")),  # several zeros
            ("2026056646 Gestión con Perito", ("2026", "56646")),  # 10 digits: the minimum
            ("2026/1234567 Gestión con Perito", ("2026", "1234567")),  # slashed wins
            ("202600000000 Gestión con Perito", None),  # all zeros after the year
            ("202612345 Gestión con Perito", None),  # under 10 digits
            ("12345 Gestión con Perito", None),
            ("979123456 Gestión con Perito", None),  # a phone-shaped run
            ("199901234567 Gestión con Perito", None),  # not 20xx
            ("2026٠١٢٣٤٥٦٧ Gestión con Perito", None),  # non-ASCII digits never make a ref
        ],
    )
    def test_slashless_fallback(self, subject, expected):
        from pipeline.claim_data import parse_claim_ref

        assert parse_claim_ref(subject, slashless=True) == expected

    def test_slashless_is_off_for_other_types(self):
        from pipeline.claim_data import parse_claim_ref

        assert parse_claim_ref("202601234567 Comunicación a colaborador", slashless=False) is None
        assert parse_claim_ref("2026/41 Comunicación a colaborador", slashless=False) == (
            "2026",
            "41",
        )

    def test_other_types_reject_a_slashless_subject(self):
        msg = _make_gmail_message("202601234567 Comunicación a colaborador", COMUNICACION_BODY)
        assert ClaimData.from_msg_data(msg) is None
