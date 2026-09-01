"""Worker root span + pipeline-stage spans (otel-observability REQ-3 — Task 3).

Span names fixed here (spec Task 3): worker_run → pipeline.fetch /
pipeline.email → pipeline.parse_classify / pipeline.render_pdf /
pipeline.create_card. Attributes carry outcomes/actions only — never claim
refs, subjects, or other identifiers (PII stance).
"""

import base64
import io
import subprocess
import sys
from pathlib import Path
from time import monotonic

import pytest
from PIL import Image as PILImage

from app.worker import run_worker
from core.state_store import RunCounts
from pipeline.entry import RUN_DEADLINE_S, process_mailbox
from pipeline.extraction import ClaimFields

pytestmark = pytest.mark.usefixtures("otel_clean")


class FakeStore:
    def __init__(self, enabled: bool = True) -> None:
        self._enabled = enabled
        self.heartbeats: list[object] = []

    def read_enabled(self) -> bool:
        return self._enabled

    def write_heartbeat(self, hb) -> None:
        self.heartbeats.append(hb)


def _b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode()


def _msg(msg_id: str, subject: str, internal_date: int = 1) -> dict:
    return {
        "id": msg_id,
        "internalDate": str(internal_date),
        "payload": {
            "headers": [{"name": "Subject", "value": subject}],
            "body": {"data": _b64("cuerpo")},
        },
    }


class FakeGmail:
    def __init__(self, messages: list[dict]) -> None:
        self._messages = {m["id"]: m for m in messages}

    def list_unread_message_ids(self, query=None) -> list[str]:
        return list(self._messages)

    def get_message(self, message_id: str) -> dict:
        return self._messages[message_id]

    def modify_labels(self, message_id, add_label_ids, remove_label_ids) -> None:
        pass

    def get_or_create_label_id(self, name: str) -> str:
        return f"id-{name}"

    def count_messages_with_label(self, label_id: str) -> int:
        return 0


class FakeTrello:
    def create_full_card(self, *, name, description, pdf_bytes, pdf_filename, comment) -> str:
        return "https://trello.com/c/1"

    def add_comment(self, card_id, text) -> None:
        pass

    def find_card_by_claim_ref(self, claim_ref):
        return None


class FakeHistory:
    def get_claim(self, claim_ref):
        return None

    def record_claim(self, record) -> None:
        pass


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


class FakeMembretes:
    def __init__(self) -> None:
        buffer = io.BytesIO()
        PILImage.new("RGB", (60, 8), "white").save(buffer, format="PNG")
        self._png = buffer.getvalue()

    def get(self, name: str) -> bytes:
        return self._png


def _span_names(otel) -> list[str]:
    return [s.name for s in otel.spans.get_finished_spans()]


# --- REQ-3.1: worker root span, both outcomes ---


def test_worker_run_root_span_with_outcome(otel_clean):
    outcome = run_worker(FakeStore(), lambda: RunCounts(processed=1, failed=0, failed_total=0))
    spans = {s.name: s for s in otel_clean.spans.get_finished_spans()}
    assert "worker_run" in spans
    assert spans["worker_run"].attributes["worker.outcome"] == outcome.value


def test_worker_run_span_records_pipeline_failure_and_reraises(otel_clean):
    def exploding_pipeline() -> RunCounts:
        raise RuntimeError("pipeline blew up")

    with pytest.raises(RuntimeError):
        run_worker(FakeStore(), exploding_pipeline)
    spans = {s.name: s for s in otel_clean.spans.get_finished_spans()}
    span = spans["worker_run"]
    assert span.status.status_code.name == "ERROR"
    assert any(e.name == "exception" for e in span.events)


def test_worker_run_is_server_root_on_timer_path(otel_clean):
    """Gate 3 H2: with Host.Results suppressed, the timer run's only chance of
    a `requests`-table row is a SERVER root span."""
    run_worker(FakeStore(), lambda: RunCounts(processed=0, failed=0, failed_total=0))
    span = next(s for s in otel_clean.spans.get_finished_spans() if s.name == "worker_run")
    assert span.parent is None  # root — the bridge tests' timer-root guard, kept
    assert span.kind.name == "SERVER"


def test_worker_run_nests_internal_under_active_span(otel_clean):
    """Under process-now an HTTP SERVER span is active — a second SERVER row
    would re-create the duplication REQ-1.2 eliminated."""
    from opentelemetry import trace

    tracer = trace.get_tracer("test")
    with tracer.start_as_current_span("fake_http_request", kind=trace.SpanKind.SERVER):
        run_worker(FakeStore(), lambda: RunCounts(processed=0, failed=0, failed_total=0))
    span = next(s for s in otel_clean.spans.get_finished_spans() if s.name == "worker_run")
    assert span.parent is not None
    assert span.kind.name == "INTERNAL"


def test_worker_run_outcome_attribute_set_on_failure(otel_clean):
    with pytest.raises(RuntimeError):
        run_worker(FakeStore(), _raise_runtime)
    span = next(s for s in otel_clean.spans.get_finished_spans() if s.name == "worker_run")
    assert span.attributes["worker.outcome"] == "failed"


def _raise_runtime() -> RunCounts:
    raise RuntimeError("boom")


def test_timer_path_flushes_even_when_wake_raises(monkeypatch):
    """REQ-6.1: the finally-flush in run_scheduled_worker must survive a
    raising wake (deleting the try/finally must fail this test)."""
    import app.worker as worker_mod

    flushed = []
    monkeypatch.setattr(worker_mod, "flush_telemetry", lambda: flushed.append(True))
    monkeypatch.setattr(worker_mod, "get_state_store", lambda: FakeStore())
    monkeypatch.setattr(
        worker_mod, "run_wake", lambda store: (_ for _ in ()).throw(RuntimeError("boom"))
    )
    with pytest.raises(RuntimeError):
        worker_mod.run_scheduled_worker()
    assert flushed == [True]


def test_function_app_wires_setup_before_instrumentation():
    """Gate 3 W1: swapping the two calls yields silent zero-span mode. The
    contract is source order in the one file that owns the wiring."""
    source = (Path(__file__).resolve().parents[2] / "function_app.py").read_text()
    assert source.index("setup_telemetry()") < source.index("instrument_fastapi(")


# --- REQ-3.1/3.2: pipeline-stage spans as one tree ---


def _run_mailbox(messages) -> RunCounts:
    return process_mailbox(
        FakeGmail(messages),
        FakeTrello(),
        FakeMembretes(),
        FakeHistory(),
        deadline=monotonic() + RUN_DEADLINE_S,
        extractor=FakeExtractor(),
    )


def test_pipeline_stage_spans_happy_path(otel_clean):
    counts = _run_mailbox([_msg("m1", "Declaración de siniestro a colaborador 2024/7")])
    assert counts.processed == 1
    names = _span_names(otel_clean)
    for expected in (
        "pipeline.fetch",
        "pipeline.email",
        "pipeline.parse_classify",
        "pipeline.render_pdf",
        "pipeline.create_card",
    ):
        assert expected in names, f"missing span {expected} in {names}"
    email_span = next(
        s for s in otel_clean.spans.get_finished_spans() if s.name == "pipeline.email"
    )
    assert email_span.attributes["email.action"] == "card"
    # PII stance: no attribute value may carry the claim ref or subject text
    for s in otel_clean.spans.get_finished_spans():
        for v in s.attributes.values():
            assert "2024/7" not in str(v)
            assert "Declaración" not in str(v)


def test_pipeline_email_span_error_on_failing_email(otel_clean):
    # Claim marker present but no parseable YYYY/N ref → _process_one raises,
    # run continues (per-email boundary).
    counts = _run_mailbox([_msg("bad", "Declaración de siniestro a colaborador sin ref")])
    assert counts.failed == 1
    email_span = next(
        s for s in otel_clean.spans.get_finished_spans() if s.name == "pipeline.email"
    )
    assert email_span.status.status_code.name == "ERROR"


# --- pipeline no-ops without any provider (fresh interpreter, no SDK wiring) ---


def test_pipeline_spans_noop_without_provider():
    code = (
        "from time import monotonic\n"
        "import sys; sys.path.insert(0, 'tests/unit')\n"
        "from test_telemetry_spans import FakeGmail, FakeTrello, FakeHistory, FakeMembretes\n"
        "from pipeline.entry import process_mailbox\n"
        "counts = process_mailbox(FakeGmail([]), FakeTrello(), FakeMembretes(), FakeHistory(),\n"
        "                         deadline=monotonic() + 5)\n"
        "print('noop-ok', counts.processed)\n"
    )
    backend_dir = Path(__file__).resolve().parents[2]  # cwd-independent (Gate 3 W4)
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=60, cwd=backend_dir
    )
    assert out.returncode == 0, out.stderr
    assert "noop-ok 0" in out.stdout
