"""gmail-client REQ-3: the read-only probe replaces the worker-skeleton stub.

The probe counts claim-subject matches among the listed UNREAD messages via a
fake GmailReader — no HTTP, no app imports (pipeline stays pure orchestration
over the injected reader).
"""

import logging
import re

import pytest

import pipeline.entry
from pipeline.claim_data import CLAIM_SUBJECT_MARKERS
from pipeline.entry import PROBE_DEADLINE_S, probe, run_pipeline
from pipeline.gmail_client import GmailNoAccessError

CLAIM_SUBJECT = "AVISO: Declaración de siniestro a colaborador 2026/417"
ASISTENCIA_SUBJECT = "Solicitud de asistencia a colaborador 2026/418"
COMUNICACION_SUBJECT = "Comunicación a colaborador 2026/417"
NOISE_SUBJECT = "Your weekly newsletter"


class FakeReader:
    def __init__(self, subjects_by_id: dict[str, str]) -> None:
        self._subjects = subjects_by_id
        self.get_calls: list[str] = []
        self.events: list[str] = []

    def preflight(self) -> None:
        self.events.append("preflight")

    def list_unread_message_ids(self) -> list[str]:
        self.events.append("list")
        return list(self._subjects)

    def get_subject(self, message_id: str) -> str:
        self.get_calls.append(message_id)
        return self._subjects[message_id]


def test_probe_counts_only_marker_matching_subjects():
    reader = FakeReader(
        {
            "m1": CLAIM_SUBJECT,
            "m2": NOISE_SUBJECT,
            "m3": ASISTENCIA_SUBJECT,
            "m4": COMUNICACION_SUBJECT,
            "m5": "Re: lunch?",
        }
    )
    assert probe(reader) == 3
    assert reader.get_calls == ["m1", "m2", "m3", "m4", "m5"]


def test_probe_empty_mailbox_returns_zero():
    assert probe(FakeReader({})) == 0


def test_probe_logs_structured_count(caplog):
    reader = FakeReader({"m1": CLAIM_SUBJECT, "m2": NOISE_SUBJECT})
    with caplog.at_level(logging.INFO, logger="pipeline.entry"):
        probe(reader)
    lines = [r.getMessage() for r in caplog.records if r.name == "pipeline.entry"]
    assert lines == ["worker_run probe matched=1 scanned=2"]


def test_probe_runs_preflight_before_any_listing():
    # REQ-2 amendment (2026-08-21): the preflight is the pipeline body's first
    # step — no Gmail polling before it succeeds.
    reader = FakeReader({"m1": CLAIM_SUBJECT})
    probe(reader)
    assert reader.events == ["preflight", "list"]


def test_probe_preflight_no_access_propagates_without_listing():
    # The skip signal escapes the pipeline for the scheduler to classify
    # (gmail-client REQ-2); nothing is listed after a failed preflight.
    class NoAccessReader(FakeReader):
        def preflight(self) -> None:
            raise GmailNoAccessError("missing_token")

    reader = NoAccessReader({"m1": CLAIM_SUBJECT})
    with pytest.raises(GmailNoAccessError):
        probe(reader)
    assert reader.events == []


def test_probe_reader_errors_propagate():
    class RaisingReader:
        def preflight(self):
            pass

        def list_unread_message_ids(self):
            raise ConnectionError("gmail down")

        def get_subject(self, message_id):  # pragma: no cover
            raise AssertionError("unreached")

    with pytest.raises(ConnectionError):
        probe(RaisingReader())


class CountingFakeGmailClient:
    """run_pipeline's construction seam (REQ-6, 2nd amendment): the pipeline
    builds a fresh client per run and closes it on every exit."""

    instances: list["CountingFakeGmailClient"] = []

    def __init__(self, settings, secret_store) -> None:
        self.closed = False
        CountingFakeGmailClient.instances.append(self)

    def preflight(self) -> None:
        pass

    def list_unread_message_ids(self) -> list[str]:
        return []

    def get_subject(self, message_id: str) -> str:  # pragma: no cover
        return ""

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def fake_gmail(monkeypatch):
    CountingFakeGmailClient.instances = []
    monkeypatch.setattr(pipeline.entry, "GmailClient", CountingFakeGmailClient)
    monkeypatch.setattr(pipeline.entry, "get_settings", lambda: object())
    monkeypatch.setattr(pipeline.entry, "get_store", lambda: object())
    return CountingFakeGmailClient


def test_run_pipeline_builds_a_fresh_client_per_call_and_closes_it(fake_gmail):
    assert run_pipeline() == 0
    assert run_pipeline() == 0
    assert len(fake_gmail.instances) == 2
    assert fake_gmail.instances[0] is not fake_gmail.instances[1]
    assert all(instance.closed for instance in fake_gmail.instances)


def test_run_pipeline_closes_client_even_when_probe_fails(monkeypatch, fake_gmail):
    class FailingListClient(CountingFakeGmailClient):
        def list_unread_message_ids(self) -> list[str]:
            raise ConnectionError("gmail down")

    monkeypatch.setattr(pipeline.entry, "GmailClient", FailingListClient)
    with pytest.raises(ConnectionError):
        run_pipeline()
    assert fake_gmail.instances[-1].closed


def test_probe_deadline_exceeded_raises_with_progress(monkeypatch):
    # Gate E1: a degraded Gmail (each fetch slow) must fail the run inside the
    # Functions 5-min budget so the `failed` heartbeat still lands. The message
    # is the sole degraded-Gmail breadcrumb, so it reports actual progress
    # (Gate 3 M5), not the listed total.
    clock = iter(range(0, 10_000, 100))  # each tick jumps 100 s
    monkeypatch.setattr(pipeline.entry, "monotonic", lambda: next(clock))
    reader = FakeReader({f"m{i}": NOISE_SUBJECT for i in range(5)})
    with pytest.raises(TimeoutError, match=rf"{int(PROBE_DEADLINE_S)}.*message 2 of 5"):
        probe(reader)


def test_probe_log_prefix_matches_the_worker_prefix():
    # The probe's count line must stay findable by the same App Insights query
    # as the worker's outcome line (the prefix is duplicated because pipeline/
    # must not import app — this test IS the drift guard).
    from app.worker import WORKER_RUN_LOG_PREFIX

    assert pipeline.entry._PROBE_LOG_PREFIX.startswith(WORKER_RUN_LOG_PREFIX)


def test_markers_are_the_three_classification_literals():
    # C6 single-source guard: the probe's criterion IS classification's criterion.
    assert CLAIM_SUBJECT_MARKERS == (
        "Declaración de siniestro a colaborador",
        "Solicitud de asistencia a colaborador",
        "Comunicación a colaborador",
    )


def test_from_subject_source_uses_markers():
    # from_subject must consume the shared constants, not re-inlined literals —
    # a drifted copy would silently split probe and classification criteria.
    import inspect

    from pipeline.claim_data import ClaimType

    source = inspect.getsource(ClaimType.from_subject.__func__)
    for marker in CLAIM_SUBJECT_MARKERS:
        assert not re.search(rf'"{re.escape(marker)}"', source), (
            f"literal {marker!r} re-inlined in from_subject; use CLAIM_SUBJECT_MARKERS"
        )
