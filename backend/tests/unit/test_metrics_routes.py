"""metrics-dashboard REQ-1: GET /api/metrics.

Fake in-memory store via dependency_overrides[get_state_store]; real JWT guard
(the test_worker_routes pattern; fixtures shared in tests/conftest.py).
"""

from datetime import UTC, datetime

import pytest

from app.main import app
from core.state_store import ClaimRecord, ErrorRun, HistoryTotals, get_state_store

METRICS_PATH = "/api/metrics"


@pytest.fixture(autouse=True)
def store_override(fake_store):
    app.dependency_overrides[get_state_store] = lambda: fake_store
    yield
    app.dependency_overrides.pop(get_state_store, None)


def _claim(ref: str, at: datetime) -> ClaimRecord:
    return ClaimRecord(
        at=at,
        claim_ref=ref,
        subject=f"Declaración de siniestro a colaborador {ref}",
        type="DECLARACION_SINIESTRO",
        town="Madrid",
        owner="Nombre Apellido",
        card_url=f"https://trello.com/c/{ref.replace('/', '-')}",
    )


def test_metrics_requires_auth(client, secrets):
    assert client.get(METRICS_PATH).status_code == 401


def test_metrics_empty_store(client, secrets, auth):
    resp = client.get(METRICS_PATH, headers=auth)
    assert resp.status_code == 200
    assert resp.json() == {
        "emails_processed": 0,
        "cards_created": 0,
        "emails_failed": 0,
        "failed_runs": 0,
        "error_runs": [],
        "claims": [],
    }


def test_metrics_returns_totals_and_claims(client, secrets, auth, fake_store):
    newest = datetime(2026, 8, 25, 12, 0, 0, tzinfo=UTC)
    older = datetime(2026, 8, 24, 9, 30, 0, tzinfo=UTC)
    # The store contract already sorts newest-first; the route passes it through.
    fake_store.claims = [_claim("2026/2", newest), _claim("2026/1", older)]
    fake_store.totals = HistoryTotals(emails_processed=7, emails_failed=3, failed_runs=1)
    fake_store.error_runs = [ErrorRun(at=older, failed=3)]
    body = client.get(METRICS_PATH, headers=auth).json()
    assert body["emails_processed"] == 7
    # Delta REQ-7: the two error counters + the graph's error events.
    assert body["emails_failed"] == 3
    assert body["failed_runs"] == 1
    # Parse, don't string-compare: pydantic emits "Z", isoformat() "+00:00".
    assert [(datetime.fromisoformat(r["at"]), r["failed"]) for r in body["error_runs"]] == [
        (older, 3)
    ]
    # cards_created is derived from the ledger, not trusted from a counter.
    assert body["cards_created"] == 2
    assert [c["claim_ref"] for c in body["claims"]] == ["2026/2", "2026/1"]
    first = body["claims"][0]
    assert datetime.fromisoformat(first["at"]) == newest
    assert first["type"] == "DECLARACION_SINIESTRO"
    assert first["town"] == "Madrid"
    assert first["owner"] == "Nombre Apellido"
    assert first["card_url"] == "https://trello.com/c/2026-2"
    # Data minimization (Gate 3 W2): the subject carries personal names and
    # nothing renders it — it must not ship in the payload.
    assert "subject" not in first


def test_metrics_response_is_not_cacheable(client, secrets, auth):
    # REQ-1.4: owner names/subjects in the payload — keep it out of shared caches.
    resp = client.get(METRICS_PATH, headers=auth)
    assert resp.headers["cache-control"] == "no-store"
