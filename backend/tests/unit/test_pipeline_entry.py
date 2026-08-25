"""pipeline-wiring REQ-1/2/4/5/8: process_mailbox — the real per-email pipeline
over protocol fakes (no HTTP), replacing the 5b read-only probe.

Message fixtures use the Gmail format=full shape the 5a from_msg_data seam
walks; fields come from an injected fake extractor (the regex extractor has its
own parity suite).
"""

import base64
import io
import logging
import re
from contextlib import contextmanager

import pytest
from PIL import Image as PILImage

import pipeline.entry
from core.state_store import RunCounts
from pipeline.claim_data import (
    CLAIM_SUBJECT_MARKERS,
    ClaimData,
    ClaimType,
    build_card_comment,
    build_card_description,
    build_card_name,
)
from pipeline.entry import (
    LABEL_FAILED,
    LABEL_PROCESADO,
    RUN_DEADLINE_S,
    UNREAD_LABEL_ID,
    build_claim_query,
    process_mailbox,
)
from pipeline.extraction import ClaimFields

CLAIM_SUBJECT = "AVISO: Declaración de siniestro a colaborador 2026/417"
URGENTE_SUBJECT = "Declaración de siniestro urgente a colaborador 2026/500"
ASISTENCIA_SUBJECT = "Solicitud de asistencia a colaborador 2026/418"
COMUNICACION_SUBJECT = "Comunicación a colaborador 2026/417"
UNPARSEABLE_SUBJECT = "Declaración de siniestro a colaborador (sin referencia)"
NO_MARKER_SUBJECT = "Solicitud de otra cosa 2026/999"


def _b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode()


def _msg(msg_id: str, subject: str, internal_date: int, body: str = "cuerpo") -> dict:
    return {
        "id": msg_id,
        "internalDate": str(internal_date),
        "payload": {
            "headers": [{"name": "Subject", "value": subject}],
            "body": {"data": _b64(body)},
        },
    }


def _synthetic_png(width: int = 60, height: int = 8) -> bytes:
    buffer = io.BytesIO()
    PILImage.new("RGB", (width, height), "white").save(buffer, format="PNG")
    return buffer.getvalue()


class FakeExtractor:
    def extract(self, claim_type, subject, body, raw_body) -> ClaimFields:
        return ClaimFields(
            insurance_company="Aseguradora Ficticia",
            nif="X0000000T",
            address="Calle Falsa 1",
            phone_number="600000000",
            town="Madrid",
            description="rotura de tubería",
            owner_name="Nombre Apellido",
            observaciones="observación de prueba",
        )


class FakeGmail:
    def __init__(self, messages: list[dict], failed_count: int = 0) -> None:
        self._messages = {m["id"]: m for m in messages}
        self._failed_count = failed_count
        self.list_queries: list[str | None] = []
        self.modifications: list[tuple[str, list[str], list[str]]] = []
        self.events: list[str] = []
        self.gauge_raises = False

    def list_unread_message_ids(self, query: str | None = None) -> list[str]:
        self.list_queries.append(query)
        return list(self._messages)

    def get_message(self, message_id: str) -> dict:
        return self._messages[message_id]

    def modify_labels(self, message_id, add_label_ids, remove_label_ids) -> None:
        self.events.append(f"relabel:{message_id}")
        self.modifications.append((message_id, add_label_ids, remove_label_ids))

    def get_or_create_label_id(self, name: str) -> str:
        return f"id-{name}"

    def count_messages_with_label(self, label_id: str) -> int:
        if self.gauge_raises:
            raise ConnectionError("gmail down at gauge time")
        assert label_id == f"id-{LABEL_FAILED}"
        return self._failed_count


class FakeTrello:
    def __init__(self, existing_card: dict | None = None) -> None:
        self.created: list[dict] = []
        self.comments: list[tuple[str, str]] = []
        self.searched: list[str] = []
        self.existing_card = existing_card
        self.create_raises: Exception | None = None
        self.events: list[str] = []

    def create_full_card(self, *, name, description, pdf_bytes, pdf_filename, comment) -> str:
        if self.create_raises is not None:
            exc, self.create_raises = self.create_raises, None  # one-shot
            raise exc
        self.events.append(f"create:{name}")
        self.created.append(
            {
                "name": name,
                "description": description,
                "pdf_bytes": pdf_bytes,
                "pdf_filename": pdf_filename,
                "comment": comment,
            }
        )
        return f"https://trello.com/c/{len(self.created)}"

    def add_comment(self, card_id: str, text: str) -> None:
        self.comments.append((card_id, text))

    def find_card_by_claim_ref(self, claim_ref: str) -> dict | None:
        self.searched.append(claim_ref)
        return self.existing_card


class FakeHistory:
    def __init__(self) -> None:
        self.rows: dict[str, object] = {}
        self.events: list[str] = []

    def get_claim(self, claim_ref: str):
        return self.rows.get(claim_ref)

    def record_claim(self, record) -> None:
        self.events.append(f"record:{record.claim_ref}")
        self.rows[record.claim_ref] = record


class FakeMembretes:
    def __init__(self) -> None:
        self._png = _synthetic_png()

    def get(self, name: str) -> bytes:
        return self._png


def _run(messages, trello=None, history=None, gmail=None, deadline_offset: float = RUN_DEADLINE_S):
    from time import monotonic

    gmail = gmail if gmail is not None else FakeGmail(messages)
    trello = trello if trello is not None else FakeTrello()
    history = history if history is not None else FakeHistory()
    counts = process_mailbox(
        gmail,
        trello,
        FakeMembretes(),
        history,
        deadline=monotonic() + deadline_offset,
        extractor=FakeExtractor(),
    )
    return counts, gmail, trello, history


# --- REQ-1: query + ordering ---


def test_query_is_built_from_the_single_source_markers():
    assert build_claim_query() == " OR ".join(f'subject:"{m}"' for m in CLAIM_SUBJECT_MARKERS)


def test_list_uses_the_marker_query():
    _, gmail, _, _ = _run([_msg("m1", CLAIM_SUBJECT, 100)])
    assert gmail.list_queries == [build_claim_query()]


def test_messages_process_in_internal_date_order():
    # Gmail documents no list order — internalDate ascending is the guarantee.
    newer = _msg("m-new", CLAIM_SUBJECT, 2_000)
    older = _msg("m-old", ASISTENCIA_SUBJECT, 1_000, body="pide SERVICIO BRICO HOGAR")
    _, gmail, trello, _ = _run([newer, older])
    assert [c["name"] for c in trello.created] == [
        "MADRID 2026/418 Nombre Apellido",  # older first
        "MADRID 2026/417 Nombre Apellido",
    ]


def test_no_marker_subject_is_failed_not_left_unread():
    # Server filter said yes, our substring check says no: an anomaly to look
    # at — never left UNREAD to reappear every wake (REQ-1/REQ-2).
    counts, gmail, trello, _ = _run([_msg("m1", NO_MARKER_SUBJECT, 100)])
    assert counts.failed == 1
    assert trello.created == []
    assert gmail.modifications == [("m1", [f"id-{LABEL_FAILED}"], [UNREAD_LABEL_ID])]


# --- REQ-2: per-email boundary ---


def test_happy_path_counts_and_relabels():
    counts, gmail, trello, history = _run([_msg("m1", CLAIM_SUBJECT, 100)])
    assert counts == RunCounts(processed=1, failed=0, failed_total=0)
    assert gmail.modifications == [("m1", [f"id-{LABEL_PROCESADO}"], [UNREAD_LABEL_ID])]
    assert "2026/417" in history.rows


def test_one_failing_email_does_not_stop_the_batch():
    # Deviation from the laptop (main.py:62-84): per-email isolation.
    trello = FakeTrello()
    trello.create_raises = ConnectionError("trello 500")  # one-shot: fails m1 only
    first = _msg("m1", CLAIM_SUBJECT, 100)
    second = _msg("m2", ASISTENCIA_SUBJECT, 200, body="pide SERVICIO BRICO HOGAR")
    counts, gmail, _, _ = _run([first, second], trello=trello)
    assert counts.processed == 1
    assert counts.failed == 1
    assert gmail.modifications[0] == ("m1", [f"id-{LABEL_FAILED}"], [UNREAD_LABEL_ID])
    assert gmail.modifications[1][0] == "m2"


def test_unparseable_claim_is_failed(caplog):
    with caplog.at_level(logging.WARNING, logger="pipeline.entry"):
        counts, gmail, trello, history = _run([_msg("m1", UNPARSEABLE_SUBJECT, 100)])
    assert counts.failed == 1
    assert trello.created == []
    assert history.rows == {}
    assert gmail.modifications == [("m1", [f"id-{LABEL_FAILED}"], [UNREAD_LABEL_ID])]
    assert any("email_failed" in r.getMessage() for r in caplog.records)


def test_deadline_checked_between_emails_only():
    # REQ-2: a started email always completes; leftovers stay UNREAD for the
    # next wake (the UNREAD-is-the-checkpoint invariant).
    counts, gmail, trello, _ = _run(
        [
            _msg("m1", CLAIM_SUBJECT, 100),
            _msg("m2", ASISTENCIA_SUBJECT, 200, body="pide SERVICIO BRICO HOGAR"),
        ],
        deadline_offset=-1.0,  # already expired: not even the first email starts
    )
    assert counts == RunCounts(processed=0, failed=0, failed_total=0)
    assert trello.created == []
    assert gmail.modifications == []


# --- REQ-4: ledger dedup + ordering ---


def test_already_processed_claim_skips_trello_and_relabels():
    history = FakeHistory()
    history.rows["2026/417"] = object()
    counts, gmail, trello, _ = _run([_msg("m1", CLAIM_SUBJECT, 100)], history=history)
    assert counts.processed == 1
    assert trello.created == []
    assert gmail.modifications == [("m1", [f"id-{LABEL_PROCESADO}"], [UNREAD_LABEL_ID])]


def test_ledger_row_is_written_before_relabel():
    # The self-healing order (REQ-6 deviation): crash between them leaves the
    # email UNREAD and the row present — next wake dedup-skips and relabels.
    history = FakeHistory()
    gmail = FakeGmail([_msg("m1", CLAIM_SUBJECT, 100)])
    shared_events: list[str] = []
    history.events = shared_events
    gmail.events = shared_events
    _run([_msg("m1", CLAIM_SUBJECT, 100)], history=history, gmail=gmail)
    assert shared_events == ["record:2026/417", "relabel:m1"]


def test_ledger_row_carries_the_card_url_and_type():
    _, _, _, history = _run([_msg("m1", CLAIM_SUBJECT, 100)])
    record = history.rows["2026/417"]
    assert record.card_url == "https://trello.com/c/1"
    assert record.type == "DECLARACION_SINIESTRO"
    assert record.town == "Madrid"
    assert record.owner == "Nombre Apellido"


# --- REQ-7 wiring: the PDF reaches Trello in memory ---


def test_card_gets_a_pdf_attachment_from_memory():
    _, _, trello, _ = _run([_msg("m1", CLAIM_SUBJECT, 100)])
    (card,) = trello.created
    assert card["pdf_bytes"][:4] == b"%PDF"
    assert card["pdf_filename"] == "claim_417_2026.pdf"


# --- REQ-8: comunicación ---


def test_comunicacion_comments_existing_card_no_ledger_row():
    trello = FakeTrello(existing_card={"id": "card-7", "name": "MADRID 2026/417 X"})
    counts, gmail, trello, history = _run([_msg("m1", COMUNICACION_SUBJECT, 100)], trello=trello)
    assert counts.processed == 1
    assert trello.created == []
    assert trello.comments == [("card-7", "@board observación de prueba")]
    assert history.rows == {}  # no ledger row (RowKey collision — gate finding)
    assert gmail.modifications == [("m1", [f"id-{LABEL_PROCESADO}"], [UNREAD_LABEL_ID])]


def test_comunicacion_without_card_is_failed():
    # Deviation: the laptop silently drops the comment and marks procesado
    # (main.py:169-188); the observaciones deserve attention.
    counts, gmail, trello, _ = _run([_msg("m1", COMUNICACION_SUBJECT, 100)])
    assert counts.failed == 1
    assert trello.comments == []
    assert gmail.modifications == [("m1", [f"id-{LABEL_FAILED}"], [UNREAD_LABEL_ID])]


def test_comunicacion_ignores_the_ledger():
    # Live search is the point: an archived/deleted card must FAIL the email
    # even when the ledger says the claim was processed (REQ-8 semantics).
    history = FakeHistory()
    history.rows["2026/417"] = object()
    counts, _, trello, _ = _run([_msg("m1", COMUNICACION_SUBJECT, 100)], history=history)
    assert trello.searched == ["2026/417"]
    assert counts.failed == 1


# --- REQ-5: counts + gauge ---


def test_failed_total_gauge_reflects_current_backlog():
    gmail = FakeGmail([_msg("m1", CLAIM_SUBJECT, 100)], failed_count=4)
    counts, _, _, _ = _run([_msg("m1", CLAIM_SUBJECT, 100)], gmail=gmail)
    assert counts == RunCounts(processed=1, failed=0, failed_total=4)


def test_gauge_failure_degrades_to_none():
    # A gauge blip must not fail a run whose mutations completed (REQ-5).
    gmail = FakeGmail([_msg("m1", CLAIM_SUBJECT, 100)])
    gmail.gauge_raises = True
    counts, _, _, _ = _run([_msg("m1", CLAIM_SUBJECT, 100)], gmail=gmail)
    assert counts.processed == 1
    assert counts.failed_total is None


# --- card content builders (laptop parity, trello.py:12-60) ---


def _claim(claim_type: ClaimType, observaciones: str | None = None) -> ClaimData:
    return ClaimData(
        year="2026",
        claim_number="417",
        subject="s",
        email_body="b",
        type=claim_type,
        insurance_company="Aseguradora Ficticia",
        nif="X0000000T",
        address="Calle Falsa 1",
        phone_number="600000000",
        town="Madrid",
        description="rotura",
        owner_name="Nombre Apellido",
        observaciones=observaciones,
    )


def test_card_name_matches_laptop_format():
    assert build_card_name(_claim(ClaimType.DECLARACION_SINIESTRO)) == (
        "MADRID 2026/417 Nombre Apellido"
    )


def test_card_description_matches_laptop_format():
    desc = build_card_description(_claim(ClaimType.DECLARACION_SINIESTRO))
    assert desc == (
        "Empresa de seguros: Aseguradora Ficticia\n"
        "NIF: X0000000T\n"
        "Dirección: Calle Falsa 1\n"
        "Teléfono: 600000000\n"
        "Población: Madrid\n"
        "Descripción: rotura\n"
    )


@pytest.mark.parametrize(
    ("claim_type", "expected"),
    [
        (ClaimType.SOLICITUD_ASISTENCIA_BRICO, "@board Nueva brico asistencia en MADRID"),
        (
            ClaimType.SOLICITUD_ASISTENCIA_ENVIO_PROFESIONALES,
            "@board Nuevo envío de profesionales en MADRID",
        ),
        (ClaimType.DECLARACION_URGENTE, "@board Parte URGENTE en MADRID"),
        (ClaimType.DECLARACION_SINIESTRO, "@board Parte nuevo en MADRID"),
    ],
)
def test_card_comment_taxonomy(claim_type, expected):
    assert build_card_comment(_claim(claim_type)) == expected


def test_comunicacion_comment_carries_observaciones():
    claim = _claim(ClaimType.COMUNICACION_A_COLABORADOR, observaciones="texto libre")
    assert build_card_comment(claim) == "@board texto libre"


# --- hygiene ---


def test_run_deadline_is_the_inherited_budget():
    assert RUN_DEADLINE_S == 120.0


def test_log_prefix_matches_the_worker_prefix():
    from app.worker import WORKER_RUN_LOG_PREFIX

    assert pipeline.entry._PIPELINE_LOG_PREFIX.startswith(WORKER_RUN_LOG_PREFIX)


def test_markers_are_the_three_classification_literals():
    # C6 single-source guard carried over from 5b.
    assert CLAIM_SUBJECT_MARKERS == (
        "Declaración de siniestro a colaborador",
        "Solicitud de asistencia a colaborador",
        "Comunicación a colaborador",
    )


def test_from_subject_source_uses_markers():
    import inspect

    source = inspect.getsource(ClaimType.from_subject.__func__)
    for marker in CLAIM_SUBJECT_MARKERS:
        assert not re.search(rf'"{re.escape(marker)}"', source), (
            f"literal {marker!r} re-inlined in from_subject; use CLAIM_SUBJECT_MARKERS"
        )


# --- run_pipeline composition (REQ-3/7/12) — monkeypatched seams, no HTTP ---


class SeamEvents:
    log: list[str] = []


class SeamClient:
    """Stands in for both real clients at the composition seam."""

    instances: list["SeamClient"] = []

    def __init__(self, *args) -> None:
        self.closed = False
        SeamClient.instances.append(self)

    def preflight(self) -> None:
        SeamEvents.log.append(f"preflight:{type(self).__name__}")

    def close(self) -> None:
        self.closed = True


class SeamGmail(SeamClient):
    pass


class SeamTrello(SeamClient):
    pass


class SeamStore:
    """Mirrors StateStore.run_lease's contract (the real CM has its own
    stub-table tests in test_state_store_models)."""

    def __init__(self, lease_free: bool = True) -> None:
        self.lease_free = lease_free
        self.lease_calls = 0
        self.released = False

    @contextmanager
    def run_lease(self):
        from core.exceptions import RunBusyError

        self.lease_calls += 1
        if not self.lease_free:
            raise RunBusyError("lease held")
        try:
            yield
        finally:
            self.released = True

    def read_trello_config(self):
        return None  # SeamTrello ignores it


@pytest.fixture
def seams(monkeypatch):
    SeamClient.instances = []
    SeamEvents.log = []
    store = SeamStore()
    monkeypatch.setattr(pipeline.entry, "GmailClient", SeamGmail)
    monkeypatch.setattr(pipeline.entry, "TrelloClient", SeamTrello)
    monkeypatch.setattr(pipeline.entry, "get_state_store", lambda: store)
    monkeypatch.setattr(pipeline.entry, "get_settings", lambda: object())
    monkeypatch.setattr(pipeline.entry, "get_store", lambda: object())
    monkeypatch.setattr(pipeline.entry, "_build_membrete_source", lambda settings: FakeMembretes())
    monkeypatch.setattr(
        pipeline.entry,
        "process_mailbox",
        lambda *a, **k: RunCounts(processed=0, failed=0, failed_total=0),
    )
    return store


def test_run_pipeline_acquires_and_releases_the_lease(seams):
    from pipeline.entry import run_pipeline

    assert run_pipeline() == RunCounts(processed=0, failed=0, failed_total=0)
    assert seams.lease_calls == 1
    assert seams.released is True


def test_run_pipeline_raises_busy_when_lease_held(seams):
    from core.exceptions import RunBusyError
    from pipeline.entry import run_pipeline

    seams.lease_free = False
    with pytest.raises(RunBusyError):
        run_pipeline()
    assert SeamClient.instances == []  # nothing composed, nothing touched


def test_run_pipeline_preflights_gmail_then_trello_before_processing(seams, monkeypatch):
    from pipeline.entry import run_pipeline

    monkeypatch.setattr(
        pipeline.entry,
        "process_mailbox",
        lambda *a, **k: (
            SeamEvents.log.append("process"),
            RunCounts(processed=0, failed=0, failed_total=0),
        )[1],
    )
    run_pipeline()
    assert SeamEvents.log == ["preflight:SeamGmail", "preflight:SeamTrello", "process"]


def test_run_pipeline_closes_clients_and_releases_lease_on_failure(seams, monkeypatch):
    from pipeline.entry import run_pipeline

    def exploding_process(*args, **kwargs):
        raise ConnectionError("mid-run failure")

    monkeypatch.setattr(pipeline.entry, "process_mailbox", exploding_process)
    with pytest.raises(ConnectionError):
        run_pipeline()
    assert all(client.closed for client in SeamClient.instances)
    assert seams.released is True


def test_membrete_source_requires_blob_endpoint_under_managed_identity():
    # REQ-7 [REVISED]: composition-time enforcement fails the RUN, not the app.
    from core.config import Settings
    from pipeline.entry import _build_membrete_source

    settings = Settings(
        table_storage_backend="managed_identity",
        tables_endpoint="https://tables.test",
        blob_endpoint=None,
    )
    with pytest.raises(RuntimeError, match="blob_endpoint"):
        _build_membrete_source(settings)


def test_failure_log_carries_the_claim_ref_when_parseable(caplog):
    # REQ-2: "claim ref if parsed, else message id" (Gate 2 drift fix).
    with caplog.at_level(logging.WARNING, logger="pipeline.entry"):
        _run([_msg("m1", COMUNICACION_SUBJECT, 100)])  # no card found -> failed
    lines = [r.getMessage() for r in caplog.records if "email_failed" in r.getMessage()]
    assert lines and "ref=2026/417" in lines[0]


def test_busy_exit_does_not_release_the_holders_lease(seams):
    # Gate 3 M6: RunBusyError is raised OUTSIDE the try/finally — releasing
    # here would delete the ACTIVE holder's lease and let a third run in.
    from core.exceptions import RunBusyError
    from pipeline.entry import run_pipeline

    seams.lease_free = False
    with pytest.raises(RunBusyError):
        run_pipeline()
    assert seams.released is False


def test_procesado_relabel_failure_fails_the_run_not_the_email(caplog):
    # Gate 3 M2: a fully-processed email whose success-relabel blips must NOT
    # get the terminal failed label — the run fails, the email stays UNREAD,
    # and the next wake dedup-skips + relabels.
    class RelabelBlipGmail(FakeGmail):
        def modify_labels(self, message_id, add_label_ids, remove_label_ids):
            if add_label_ids == [f"id-{LABEL_PROCESADO}"]:
                raise ConnectionError("gmail 500 at relabel")
            super().modify_labels(message_id, add_label_ids, remove_label_ids)

    gmail = RelabelBlipGmail([_msg("m1", CLAIM_SUBJECT, 100)])
    with pytest.raises(ConnectionError, match="relabel"):
        _run([_msg("m1", CLAIM_SUBJECT, 100)], gmail=gmail)
    assert gmail.modifications == []  # in particular: no failed label


def test_fetch_phase_honors_the_deadline():
    # Gate 3 M5: an expired deadline stops fetching too — 100 slow fetches
    # must not eat the functionTimeout.
    gmail = FakeGmail([_msg("m1", CLAIM_SUBJECT, 100)])
    fetched: list[str] = []
    original = gmail.get_message
    gmail.get_message = lambda message_id: (fetched.append(message_id), original(message_id))[1]
    counts, _, _, _ = _run([_msg("m1", CLAIM_SUBJECT, 100)], gmail=gmail, deadline_offset=-1.0)
    assert fetched == []
    assert counts.processed == 0
