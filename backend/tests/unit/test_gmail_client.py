"""gmail-client REQ-1/3/6: the rewritten httpx client — preflight taxonomy,
request shapes, and the I/O-free constructor.

respx mocks the wire (house pattern, cf. test_auth_routes.py); no Google SDK.
"""

import logging

import httpx
import pytest
import respx
from httpx import Response

from app.config import Settings
from app.gmail_client import (
    PROBE_MAX_RESULTS,
    GmailClient,
    GmailNoAccessError,
)
from app.secret_store import GMAIL_REFRESH_TOKEN, GOOGLE_CLIENT_SECRET, FileSecretStore

CLIENT_ID = "client-id-123"
TOKEN_URL = "https://idp.test/token"
API_BASE = "https://gmail.test"
LIST_URL = f"{API_BASE}/gmail/v1/users/me/messages"
ACCESS_TOKEN = "at-secret-value-xyz"
REFRESH_TOKEN = "rt-secret-value-abc"


@pytest.fixture
def settings() -> Settings:
    return Settings(
        google_client_id=CLIENT_ID,
        google_token_url=TOKEN_URL,
        gmail_api_base_url=API_BASE,
    )


@pytest.fixture
def secrets(tmp_path) -> FileSecretStore:
    store = FileSecretStore(tmp_path / "secrets.json")
    store.set(GOOGLE_CLIENT_SECRET, "shh-client-secret")
    store.set(GMAIL_REFRESH_TOKEN, REFRESH_TOKEN)
    return store


@pytest.fixture
def gmail(settings, secrets) -> GmailClient:
    client = GmailClient(settings, secrets)
    yield client
    client.close()


def _mock_token(payload=None, status=200):
    return respx.post(TOKEN_URL).mock(
        return_value=Response(status, json=payload if payload is not None else {})
    )


def _mint_ok():
    return _mock_token({"access_token": ACCESS_TOKEN, "expires_in": 3599})


def _message(msg_id: str, headers: list[dict]) -> dict:
    return {"id": msg_id, "payload": {"headers": headers}}


# --- REQ-1: preflight ---


@respx.mock
def test_preflight_posts_refresh_grant(gmail):
    route = _mint_ok()
    gmail.preflight()
    assert route.called
    body = dict(pair.split("=") for pair in route.calls.last.request.content.decode().split("&"))
    assert body["grant_type"] == "refresh_token"
    assert body["refresh_token"] == REFRESH_TOKEN
    assert body["client_id"] == CLIENT_ID
    assert body["client_secret"] == "shh-client-secret"


@respx.mock
def test_preflight_missing_refresh_token_raises_without_http(settings, tmp_path):
    secrets = FileSecretStore(tmp_path / "secrets.json")
    secrets.set(GOOGLE_CLIENT_SECRET, "shh-client-secret")  # token deliberately absent
    client = GmailClient(settings, secrets)
    # No respx route is registered: any HTTP attempt would raise respx's
    # pass-through error, so reaching GmailNoAccessError proves no call happened.
    with pytest.raises(GmailNoAccessError) as exc_info:
        client.preflight()
    assert exc_info.value.reason == "missing_token"


@pytest.mark.parametrize(
    ("status", "error_code"),
    [(400, "invalid_grant"), (401, "invalid_client"), (401, "deleted_client")],
)
@respx.mock
def test_preflight_definitive_rejection_raises_no_access(gmail, status, error_code):
    # invalid_grant: revoked / 6-months-unused / password change (documented).
    # invalid_client verified live at the P10 gate; deleted_client per gate E2.
    _mock_token({"error": error_code}, status=status)
    with pytest.raises(GmailNoAccessError) as exc_info:
        gmail.preflight()
    assert exc_info.value.reason == "token_rejected"


@respx.mock
def test_preflight_server_error_propagates(gmail):
    # Transient Google failure says nothing about token health (REQ-1): it must
    # NOT masquerade as "needs reconnect".
    _mock_token({"error": "internal_failure"}, status=500)
    with pytest.raises(httpx.HTTPStatusError):
        gmail.preflight()


@respx.mock
def test_preflight_unknown_4xx_error_code_propagates(gmail):
    _mock_token({"error": "temporarily_unavailable"}, status=400)
    with pytest.raises(httpx.HTTPStatusError):
        gmail.preflight()


@respx.mock
def test_preflight_network_error_propagates(gmail):
    respx.post(TOKEN_URL).mock(side_effect=httpx.ConnectError("boom"))
    with pytest.raises(httpx.ConnectError):
        gmail.preflight()


@respx.mock
def test_token_values_never_logged(gmail, caplog):
    _mint_ok()
    with caplog.at_level(logging.DEBUG):
        gmail.preflight()
    for record in caplog.records:
        assert ACCESS_TOKEN not in record.getMessage()
        assert REFRESH_TOKEN not in record.getMessage()


@respx.mock
def test_token_values_never_logged_on_rejection(gmail, caplog):
    # Gate 2 finding 4: the rejection path logs a WARNING — it must carry the
    # error code only, never the refresh token.
    _mock_token({"error": "invalid_grant"}, status=400)
    with caplog.at_level(logging.DEBUG), pytest.raises(GmailNoAccessError):
        gmail.preflight()
    rejection_lines = [r.getMessage() for r in caplog.records if r.name == "app.gmail_client"]
    assert any("invalid_grant" in line for line in rejection_lines)
    for record in caplog.records:
        assert REFRESH_TOKEN not in record.getMessage()


# --- REQ-6: constructor is I/O-free ---


def test_constructor_does_no_secret_access_and_no_http(settings):
    class ExplodingSecretStore:
        def get(self, name):
            raise AssertionError("constructor must not read secrets (gate E3)")

        def set(self, name, value):  # pragma: no cover
            raise AssertionError("never written")

    GmailClient(settings, ExplodingSecretStore())  # must not raise


# --- REQ-3: list + subject fetch ---


@respx.mock
def test_list_unread_request_shape_and_ids(gmail):
    _mint_ok()
    route = respx.get(LIST_URL).mock(
        return_value=Response(
            200, json={"messages": [{"id": "m1"}, {"id": "m2"}], "resultSizeEstimate": 2}
        )
    )
    gmail.preflight()
    assert gmail.list_unread_message_ids() == ["m1", "m2"]
    request = route.calls.last.request
    assert request.url.params["labelIds"] == "UNREAD"
    assert request.url.params["maxResults"] == str(PROBE_MAX_RESULTS)
    assert request.headers["authorization"] == f"Bearer {ACCESS_TOKEN}"


@respx.mock
def test_list_unread_empty_mailbox_returns_empty(gmail):
    # A no-match response may omit the `messages` key entirely (handled
    # defensively — the docs don't pin either shape).
    _mint_ok()
    respx.get(LIST_URL).mock(return_value=Response(200, json={"resultSizeEstimate": 0}))
    gmail.preflight()
    assert gmail.list_unread_message_ids() == []


@respx.mock
def test_get_subject_request_shape_and_value(gmail):
    _mint_ok()
    route = respx.get(f"{LIST_URL}/m1").mock(
        return_value=Response(200, json=_message("m1", [{"name": "Subject", "value": "Hola"}]))
    )
    gmail.preflight()
    assert gmail.get_subject("m1") == "Hola"
    request = route.calls.last.request
    assert request.url.params["format"] == "metadata"
    assert request.url.params["metadataHeaders"] == "Subject"
    assert request.headers["authorization"] == f"Bearer {ACCESS_TOKEN}"


@respx.mock
def test_get_subject_header_name_case_insensitive(gmail):
    # Lowercase `subject` is legal HTTP (gate E7) — the rewrite doesn't inherit
    # the laptop's exact-match.
    _mint_ok()
    respx.get(f"{LIST_URL}/m1").mock(
        return_value=Response(200, json=_message("m1", [{"name": "subject", "value": "hola"}]))
    )
    gmail.preflight()
    assert gmail.get_subject("m1") == "hola"


@respx.mock
def test_get_subject_missing_header_returns_empty(gmail):
    _mint_ok()
    respx.get(f"{LIST_URL}/m1").mock(
        return_value=Response(200, json=_message("m1", [{"name": "From", "value": "x@y"}]))
    )
    gmail.preflight()
    assert gmail.get_subject("m1") == ""


@respx.mock
def test_list_api_error_propagates(gmail):
    # C4: fail fast — the recorded 400 failedPrecondition transient fails the
    # run; the next 30-min wake retries.
    _mint_ok()
    respx.get(LIST_URL).mock(
        return_value=Response(400, json={"error": {"status": "FAILED_PRECONDITION"}})
    )
    gmail.preflight()
    with pytest.raises(httpx.HTTPStatusError):
        gmail.list_unread_message_ids()


def test_api_calls_before_preflight_are_programming_errors(gmail):
    with pytest.raises(RuntimeError, match="preflight"):
        gmail.list_unread_message_ids()
    with pytest.raises(RuntimeError, match="preflight"):
        gmail.get_subject("m1")
