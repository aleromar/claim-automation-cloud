"""Worker root span + pipeline-stage spans (otel-observability REQ-3 — Task 3).

Span names fixed here (spec Task 3): worker_run → pipeline.fetch /
pipeline.email → pipeline.parse_classify / pipeline.render_pdf /
pipeline.create_card. Attributes carry outcomes/actions only — never claim
refs, subjects, or other identifiers (PII stance).
"""

import base64
import io
import json
import subprocess
import sys
from pathlib import Path
from time import monotonic

import pytest
from PIL import Image as PILImage
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import FunctionModel

from app.worker import run_worker
from core.config import Settings
from core.state_store import RunCounts
from pipeline.claim_data import ClaimType
from pipeline.entry import RUN_DEADLINE_S, process_mailbox
from pipeline.extraction import EXTRACTOR_LLM, ClaimFields
from pipeline.llm_extraction import AGENT_NAME, LOG_EVENT, LlmFieldExtractor

pytestmark = pytest.mark.usefixtures("otel_clean")

# llm-extraction REQ-5.4 scoping key (Task 0c): both Pydantic AI spans — the
# `chat …` CLIENT span and the `invoke_agent llm_extraction` INTERNAL span —
# carry this instrumentation scope; they are the ONLY spans allowed to hold
# claim text (REQ-5.2, operator decision: content capture ON).
PYDANTIC_AI_SCOPE = "pydantic-ai"

LLM_SUBJECT = "2026/123456 Declaración de siniestro a colaborador NORMAL"
LLM_BODY = "Compañía: Reale\n\nNif: H12345678\n\nTomador: CDAD EJEMPLO\n\nTfno : 600000001\n"
LLM_ANSWER = {
    "insurance_company": "Reale",
    "nif": "H12345678",
    "phone_number": "600000001",
    "owner_name": "CDAD EJEMPLO",
}
CLAIM_TEXT_NEEDLES = ("Reale", "H12345678", "600000001", "CDAD EJEMPLO", "2026/123456")


def assert_no_claim_text(spans, needles=CLAIM_TEXT_NEEDLES) -> None:
    """PII stance for pipeline spans: no attribute value may carry claim text.
    Pydantic AI spans are skipped — they carry prompt + answer by design."""
    for span in spans:
        if span.instrumentation_scope.name == PYDANTIC_AI_SCOPE:
            continue
        for value in span.attributes.values():
            for needle in needles:
                assert needle not in str(value), (span.name, needle)


def answer_gold(messages, info) -> ModelResponse:
    allowed = info.model_request_parameters.output_object.json_schema["properties"]
    body = {k: v for k, v in LLM_ANSWER.items() if k in allowed}
    return ModelResponse(parts=[TextPart(json.dumps(body))], finish_reason="stop")


async def dummy_token() -> str:
    return "DUMMY"


def run_llm_extraction(capture_content: bool) -> ClaimFields:
    """One extraction under FunctionModel with the production instrumentation
    wiring — the only difference from prod is the model."""
    settings = Settings(
        field_extractor_backend=EXTRACTOR_LLM,
        foundry_endpoint="https://foundry.example/openai/v1/",
        llm_capture_content=capture_content,
    )
    extractor = LlmFieldExtractor(settings, token=dummy_token, model=FunctionModel(answer_gold))
    try:
        return extractor.extract(ClaimType.DECLARACION_SINIESTRO, LLM_SUBJECT, LLM_BODY, LLM_BODY)
    finally:
        extractor.close()


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
    assert_no_claim_text(otel_clean.spans.get_finished_spans(), ("2024/7", "Declaración"))


def test_pipeline_email_span_error_on_failing_email(otel_clean):
    # Claim marker present but no parseable YYYY/N ref → _process_one raises,
    # run continues (per-email boundary).
    counts = _run_mailbox([_msg("bad", "Declaración de siniestro a colaborador sin ref")])
    assert counts.failed == 1
    email_span = next(
        s for s in otel_clean.spans.get_finished_spans() if s.name == "pipeline.email"
    )
    assert email_span.status.status_code.name == "ERROR"


# --- llm-extraction REQ-5.1/5.2/5.4: the two gen_ai spans, content iff the switch ---


def _pydantic_ai_spans(otel) -> dict[str, object]:
    return {
        s.name: s
        for s in otel.spans.get_finished_spans()
        if s.instrumentation_scope.name == PYDANTIC_AI_SCOPE
    }


def test_llm_extractor_emits_the_two_gen_ai_spans(otel_clean):
    fields = run_llm_extraction(capture_content=True)
    assert fields.extractor_used == "llm"
    spans = _pydantic_ai_spans(otel_clean)
    agent_span = spans[f"invoke_agent {AGENT_NAME}"]
    (chat_span,) = [s for name, s in spans.items() if name.startswith("chat ")]
    assert agent_span.kind.name == "INTERNAL"
    assert agent_span.attributes["gen_ai.operation.name"] == "invoke_agent"
    assert chat_span.kind.name == "CLIENT"
    assert chat_span.attributes["gen_ai.operation.name"] == "chat"
    assert chat_span.parent.span_id == agent_span.context.span_id


def test_llm_spans_carry_content_when_capture_is_on(otel_clean):
    run_llm_extraction(capture_content=True)
    spans = _pydantic_ai_spans(otel_clean)
    (chat_span,) = [s for name, s in spans.items() if name.startswith("chat ")]
    agent_span = spans[f"invoke_agent {AGENT_NAME}"]
    assert "Reale" in chat_span.attributes["gen_ai.input.messages"]
    assert "H12345678" in chat_span.attributes["gen_ai.output.messages"]
    assert "final_result" in agent_span.attributes  # the second copy, accepted (Task 0c)
    # The shared helper must SKIP these spans and stay strict for pipeline ones.
    assert_no_claim_text(otel_clean.spans.get_finished_spans())


def test_llm_spans_carry_no_content_when_capture_is_off(otel_clean):
    run_llm_extraction(capture_content=False)
    spans = _pydantic_ai_spans(otel_clean)
    assert len(spans) == 2  # skeletons still exported
    for span in spans.values():
        for value in span.attributes.values():
            for needle in CLAIM_TEXT_NEEDLES:
                assert needle not in str(value), (span.name, needle)
    assert "final_result" not in spans[f"invoke_agent {AGENT_NAME}"].attributes


def test_no_claim_text_helper_stays_strict_for_pipeline_spans(otel_clean):
    from opentelemetry import trace

    with trace.get_tracer("pipeline.test").start_as_current_span("pipeline.leak") as span:
        span.set_attribute("bad", "Reale")
    with pytest.raises(AssertionError):
        assert_no_claim_text(otel_clean.spans.get_finished_spans())


# --- llm-extraction REQ-5.3: the INFO line's dimensions reach the OTel log exporter ---


def test_llm_info_line_dimensions_reach_the_log_exporter(otel_clean, caplog):
    # The D28 handler is on the root logger at INFO; the Functions worker runs
    # the root at INFO too (existing pipeline INFO lines land in App Insights).
    # A bare interpreter's root is WARNING, so the level is raised here.
    import logging

    with caplog.at_level(logging.INFO, logger="pipeline.llm_extraction"):
        run_llm_extraction(capture_content=True)
    records = [
        d.log_record
        for d in otel_clean.logs.get_finished_logs()
        if d.log_record.attributes.get("event") == LOG_EVENT
    ]
    assert len(records) == 1, "exactly one llm_extraction line per call"
    attrs = records[0].attributes
    assert attrs["extractor_used"] == "llm"
    assert attrs["claim_type"] == "DECLARACION_SINIESTRO"
    assert attrs["gen_ai.request.model"] == "gpt-5-mini"
    assert isinstance(attrs["duration_ms"], int)
    assert isinstance(attrs["gen_ai.usage.input_tokens"], int)
    assert attrs["finish_reason"] == "stop"
    assert "extraction.prompt_sha" in attrs
    rendered = str(records[0].body) + " ".join(str(v) for v in attrs.values())
    for needle in CLAIM_TEXT_NEEDLES:
        assert needle not in rendered, needle


# --- llm-extraction REQ-5.5: no provider → no spans, no error (fresh interpreter) ---


def test_llm_extractor_noop_without_provider():
    code = (
        "import sys; sys.path.insert(0, 'tests/unit')\n"
        "from opentelemetry import trace\n"
        "from test_telemetry_spans import run_llm_extraction\n"
        "fields = run_llm_extraction(True)\n"
        "print('noop-ok', fields.extractor_used, type(trace.get_tracer_provider()).__name__)\n"
    )
    backend_dir = Path(__file__).resolve().parents[2]
    out = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=backend_dir,
        env={**__import__("os").environ, "PYDANTIC_AI_NO_BANNER": "1"},
    )
    assert out.returncode == 0, out.stderr
    assert "noop-ok llm ProxyTracerProvider" in out.stdout


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
