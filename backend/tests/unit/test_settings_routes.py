"""settings REQ-1/2: /api/settings + /api/settings/trello.

Fake in-memory store via dependency_overrides[get_state_store]; the JWT guard is
real (seeded file secret store + mint_session_jwt — the test_worker_routes
pattern; fixtures shared in tests/conftest.py). Secrets are write-only:
presence flags out, never values (REQ-1.2).
"""

import logging

import pytest
import respx
from fastapi.testclient import TestClient

from app.main import app
from core.secret_store import (
    GMAIL_REFRESH_TOKEN,
    TRELLO_API_KEY,
    TRELLO_TOKEN,
    FileSecretStore,
)
from core.state_store import TrelloConfig, get_state_store
from tests.conftest import OPERATOR

SETTINGS_PATH = "/api/settings"
TRELLO_PATH = "/api/settings/trello"


@pytest.fixture(autouse=True)
def store_override(fake_store):
    app.dependency_overrides[get_state_store] = lambda: fake_store
    yield
    app.dependency_overrides.pop(get_state_store, None)


@pytest.fixture
def client(secrets) -> TestClient:
    return TestClient(app)


# --- auth guard (REQ-1.4/2.6, router-level) ---


@pytest.mark.parametrize(("method", "path"), [("GET", SETTINGS_PATH), ("POST", TRELLO_PATH)])
def test_settings_endpoints_require_auth(client, method, path):
    # No body on the POST on purpose: 401 (guard) must win over 422 (validation).
    assert client.request(method, path).status_code == 401


# --- read state (REQ-1) ---


def test_fresh_install_reads_defaults(client, auth):
    resp = client.get(SETTINGS_PATH, headers=auth)
    assert resp.status_code == 200
    assert resp.json() == {
        "trello": {
            "api_key_stored": False,
            "token_stored": False,
            "board_id": "",
            "list_id": "",
        },
        "gmail": {"account_email": OPERATOR, "refresh_token_stored": False},
    }


def test_configured_state_shows_flags_and_ids_but_never_values(client, auth, secrets, fake_store):
    secrets.set(TRELLO_API_KEY, "key-hunter2")
    secrets.set(TRELLO_TOKEN, "tok-hunter2")
    secrets.set(GMAIL_REFRESH_TOKEN, "rt-hunter2")
    fake_store.trello = TrelloConfig(board_id="g7vysmjD", list_id="68875e0d")
    resp = client.get(SETTINGS_PATH, headers=auth)
    assert resp.status_code == 200
    body = resp.json()
    assert body["trello"]["api_key_stored"] is True
    assert body["trello"]["token_stored"] is True
    assert body["trello"]["board_id"] == "g7vysmjD"
    assert body["gmail"]["refresh_token_stored"] is True
    assert "hunter2" not in resp.text  # write-only secrets (REQ-1.2)


def test_settings_response_is_never_cached(client, auth, secrets):
    # Presence flags/IDs/email are not secret values, but a cached copy could
    # show stale credential state after a save or reconnect. Must be set in
    # FastAPI — SWA globalHeaders don't touch API responses.
    resp = client.get(SETTINGS_PATH, headers=auth)
    assert resp.headers["Cache-Control"] == "no-store"


def test_presence_flags_track_their_own_secret(client, auth, secrets):
    # Asymmetric on purpose: swapping the two lookups in _trello_state passes
    # every both-stored/both-absent test.
    secrets.set(TRELLO_API_KEY, "key-only")
    trello = client.get(SETTINGS_PATH, headers=auth).json()["trello"]
    assert trello["api_key_stored"] is True
    assert trello["token_stored"] is False


# --- save (REQ-2) ---


# Zero respx routes: any outbound httpx call raises (REQ-2.5 store-only);
# TestClient's ASGI transport passes through untouched.
@respx.mock
def test_save_all_four_fields(client, auth, secrets, fake_store):
    resp = client.post(
        TRELLO_PATH,
        headers=auth,
        json={
            "api_key": "key-1",
            "token": "tok-1",
            "board_id": "board-1",
            "list_id": "list-1",
        },
    )
    assert resp.status_code == 200
    assert resp.json() == {
        "api_key_stored": True,
        "token_stored": True,
        "board_id": "board-1",
        "list_id": "list-1",
    }
    assert secrets.get(TRELLO_API_KEY) == "key-1"
    assert secrets.get(TRELLO_TOKEN) == "tok-1"
    assert fake_store.write_calls == [TrelloConfig(board_id="board-1", list_id="list-1")]


@pytest.mark.parametrize("blank", ["", "   ", None])
def test_blank_secret_keeps_stored_value(client, auth, secrets, fake_store, blank):
    # blank = keep, trimmed BEFORE the check (REQ-2.2): whitespace is blank.
    secrets.set(TRELLO_API_KEY, "key-keep")
    secrets.set(TRELLO_TOKEN, "tok-keep")
    body = {"board_id": "board-2", "list_id": "list-2"}
    if blank is not None:
        body |= {"api_key": blank, "token": blank}
    resp = client.post(TRELLO_PATH, headers=auth, json=body)
    assert resp.status_code == 200
    assert resp.json()["api_key_stored"] is True
    assert secrets.get(TRELLO_API_KEY) == "key-keep"
    assert secrets.get(TRELLO_TOKEN) == "tok-keep"
    assert fake_store.trello == TrelloConfig(board_id="board-2", list_id="list-2")


def test_nonempty_secret_overwrites(client, auth, secrets):
    secrets.set(TRELLO_API_KEY, "key-old")
    client.post(
        TRELLO_PATH,
        headers=auth,
        json={"api_key": "  key-new  ", "board_id": "b", "list_id": "l"},
    )
    assert secrets.get(TRELLO_API_KEY) == "key-new"  # trimmed, stored


def test_ids_are_trimmed_and_authoritative(client, auth, fake_store):
    # Visible fields: submitted value wins, including empty (no blank=keep).
    fake_store.trello = TrelloConfig(board_id="old", list_id="old")
    resp = client.post(TRELLO_PATH, headers=auth, json={"board_id": "  b-new  ", "list_id": ""})
    assert resp.json()["board_id"] == "b-new"
    assert fake_store.trello == TrelloConfig(board_id="b-new", list_id="")


def test_save_logs_one_no_values_line(client, auth, secrets, caplog):
    # REQ-2.9: one correlation line, never values.
    with caplog.at_level(logging.INFO, logger="app.settings_routes"):
        client.post(
            TRELLO_PATH,
            headers=auth,
            json={"api_key": "key-hunter2", "board_id": "b", "list_id": "l"},
        )
    saves = [r for r in caplog.records if "trello settings saved" in r.getMessage()]
    assert len(saves) == 1
    assert "api_key=updated" in saves[0].getMessage()
    assert "token=kept" in saves[0].getMessage()
    assert "hunter2" not in saves[0].getMessage()


# --- error contract (REQ-2.7/2.8) ---


def test_table_write_failure_returns_generic_500(secrets, auth, fake_store):
    def boom(config):
        raise RuntimeError("table down")

    fake_store.write_trello_config = boom
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.post(
        TRELLO_PATH,
        headers=auth,
        json={"api_key": "key-hunter2", "board_id": "b", "list_id": "l"},
    )
    assert resp.status_code == 500
    # App-owned JSON body (REQ-2.7): must be raised as HTTPException inside the
    # app — an unhandled exception is answered by Starlette's outermost
    # ServerErrorMiddleware, outside CORSMiddleware, unreadable cross-origin.
    assert resp.json() == {"detail": "settings save failed"}
    assert "hunter2" not in resp.text  # generic body, no submitted values


def test_secret_write_failure_stops_before_the_table_write(auth, secrets, fake_store, monkeypatch):
    # The FIRST write failing (Key Vault down) must behave like the table case
    # — and the fixed secrets→table order means the table stays untouched,
    # which is what makes "retry is safe" true (REQ-2.7).
    def boom(self, name, value):
        raise RuntimeError("vault down")

    monkeypatch.setattr(FileSecretStore, "set", boom)
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.post(
        TRELLO_PATH,
        headers=auth,
        json={"api_key": "key-hunter2", "board_id": "b", "list_id": "l"},
    )
    assert resp.status_code == 500
    assert resp.json() == {"detail": "settings save failed"}
    assert "hunter2" not in resp.text
    assert fake_store.write_calls == []


def test_write_failure_logs_no_values_line(secrets, auth, fake_store, caplog):
    # REQ-2.9 must fire on failure too — a Key Vault/table outage is the exact
    # event the correlation line exists for.
    def boom(config):
        raise RuntimeError("table down")

    fake_store.write_trello_config = boom
    client = TestClient(app, raise_server_exceptions=False)
    with caplog.at_level(logging.INFO, logger="app.settings_routes"):
        client.post(
            TRELLO_PATH,
            headers=auth,
            json={"api_key": "key-hunter2", "board_id": "b", "list_id": "l"},
        )
    failures = [r for r in caplog.records if "trello settings save failed" in r.getMessage()]
    assert len(failures) == 1
    assert "hunter2" not in caplog.text  # includes the traceback


def test_422_does_not_echo_submitted_values(client, auth):
    # FastAPI's default validation body carries an `input` echo — the global
    # handler allowlists loc/msg/type so the no-echo property survives
    # dependency upgrades that add new input-derived keys (REQ-2.8, P5).
    resp = client.post(TRELLO_PATH, headers=auth, json={"api_key": {"nested": "key-hunter2"}})
    assert resp.status_code == 422
    assert "hunter2" not in resp.text
    for error in resp.json()["detail"]:
        assert set(error) <= {"loc", "msg", "type"}


def test_oversized_field_is_a_422_not_a_500(client, auth, fake_store):
    # Key Vault caps values at 25KB / Table properties at 64KB: without a body
    # cap an oversized input becomes a misleading "safe to retry" 500 loop.
    # string_too_long is also the one error whose default body carries `ctx`.
    resp = client.post(
        TRELLO_PATH,
        headers=auth,
        json={"api_key": "key-hunter2" + "k" * 30_000, "board_id": "b", "list_id": "l"},
    )
    assert resp.status_code == 422
    assert "hunter2" not in resp.text
    assert fake_store.write_calls == []  # rejected before any write
    for error in resp.json()["detail"]:
        assert set(error) <= {"loc", "msg", "type"}
