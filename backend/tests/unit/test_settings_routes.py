"""settings REQ-1/2: /api/settings + /api/settings/trello.

Fake in-memory store via dependency_overrides[get_state_store]; the JWT guard is
real (seeded file secret store + mint_session_jwt — the test_worker_routes
pattern). Secrets are write-only: presence flags out, never values (REQ-1.2).
"""

import logging

import pytest
import respx
from fastapi.testclient import TestClient

from app.main import app
from app.secret_store import (
    GMAIL_REFRESH_TOKEN,
    SESSION_SIGNING_KEY,
    TRELLO_API_KEY,
    TRELLO_TOKEN,
    FileSecretStore,
)
from app.security import mint_session_jwt
from app.state_store import TrelloConfig, get_state_store
from tests.conftest import OPERATOR, SIGNING_KEY

SETTINGS_PATH = "/api/settings"
TRELLO_PATH = "/api/settings/trello"


class FakeStateStore:
    """The two accessors the settings routes consume; records writes."""

    def __init__(self, trello: TrelloConfig | None = None) -> None:
        self.trello = trello
        self.write_calls: list[TrelloConfig] = []

    def read_trello_config(self) -> TrelloConfig | None:
        return self.trello

    def write_trello_config(self, config: TrelloConfig) -> None:
        self.write_calls.append(config)
        self.trello = config


@pytest.fixture
def fake_store() -> FakeStateStore:
    return FakeStateStore()


@pytest.fixture(autouse=True)
def store_override(fake_store):
    app.dependency_overrides[get_state_store] = lambda: fake_store
    yield
    app.dependency_overrides.pop(get_state_store, None)


@pytest.fixture
def secrets(secret_env) -> FileSecretStore:
    store = FileSecretStore(secret_env)
    store.set(SESSION_SIGNING_KEY, SIGNING_KEY)
    return store


@pytest.fixture
def client(secrets) -> TestClient:
    return TestClient(app)


@pytest.fixture
def auth() -> dict[str, str]:
    token = mint_session_jwt(OPERATOR, SIGNING_KEY, ttl_hours=8)
    return {"Authorization": f"Bearer {token}"}


# --- auth guard (REQ-1.4/2.6, router-level) ---


@pytest.mark.parametrize(("method", "path"), [("GET", SETTINGS_PATH), ("POST", TRELLO_PATH)])
def test_settings_endpoints_require_auth(client, method, path):
    # No body on the POST on purpose: 401 (guard) must win over 422 (validation).
    assert client.request(method, path).status_code == 401


def test_no_new_verbs_beyond_get_post():
    # Two-layer CORS convention (worker-controls; D22 supplies the layers).
    from app.settings_routes import router

    methods = {m for route in router.routes for m in route.methods}
    assert methods <= {"GET", "HEAD", "POST"}


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


# --- save (REQ-2) ---


@respx.mock  # zero routes: any outbound httpx call raises (REQ-2.5 store-only;
def test_save_all_four_fields(client, auth, secrets, fake_store):  # noqa: E501 — TestClient's ASGI transport passes through)
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
    assert "hunter2" not in resp.text  # generic body, no submitted values


def test_422_does_not_echo_submitted_values(client, auth):
    # FastAPI's default validation body carries an `input` echo — stripped by
    # the global handler (REQ-2.8, P5).
    resp = client.post(TRELLO_PATH, headers=auth, json={"api_key": {"nested": "key-hunter2"}})
    assert resp.status_code == 422
    assert "hunter2" not in resp.text
