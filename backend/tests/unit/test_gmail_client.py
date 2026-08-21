"""gmail-client REQ-1/3/6: the rewritten httpx client — preflight taxonomy,
request shapes, and the I/O-free constructor.

respx mocks the wire (house pattern, cf. test_auth_routes.py); no Google SDK.
"""

import logging
from urllib.parse import parse_qs

import httpx
import pytest
import respx
from httpx import Response

from core.config import Settings
from pipeline.gmail_client import (
    MISSING_TOKEN,
    PROBE_MAX_RESULTS,
    TOKEN_REJECTED,
    GmailClient,
    GmailNoAccessError,
)
from core.secret_store import GMAIL_REFRESH_TOKEN, GOOGLE_CLIENT_SECRET, FileSecretStore
from pipeline.entry import run_pipeline

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
    body = {k: v[0] for k, v in parse_qs(route.calls.last.request.content.decode()).items()}
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
    assert exc_info.value.reason == MISSING_TOKEN


@respx.mock
def test_preflight_missing_client_secret_is_a_deploy_fault_not_no_access(settings, tmp_path):
    # Gate 3 H1: an absent google-client-secret would reach Google as
    # client_secret="" -> 401 invalid_client -> a FALSE "reconnect Gmail"
    # dead-end (reconnecting cannot mint a secret). It must fail loudly as a
    # deployment fault instead — require_secret, same stance as auth_routes.
    secrets = FileSecretStore(tmp_path / "secrets.json")
    secrets.set(GMAIL_REFRESH_TOKEN, REFRESH_TOKEN)  # secret deliberately absent
    client = GmailClient(settings, secrets)
    with pytest.raises(RuntimeError, match="google-client-secret"):
        client.preflight()  # no respx route: proves no HTTP happened


@pytest.mark.parametrize(
    ("status", "error_code"),
    [
        (400, "invalid_grant"),
        (401, "invalid_client"),
        (401, "deleted_client"),
        (401, "unauthorized_client"),
    ],
)
@respx.mock
def test_preflight_definitive_rejection_raises_no_access(gmail, status, error_code):
    # invalid_grant: revoked / 6-months-unused / password change (documented).
    # invalid_client verified live at the P10 gate; deleted_client per gate E2;
    # unauthorized_client per Gate 3 H3 (client lost the grant — non-transient).
    _mock_token({"error": error_code}, status=status)
    with pytest.raises(GmailNoAccessError) as exc_info:
        gmail.preflight()
    assert exc_info.value.reason == TOKEN_REJECTED


@respx.mock
def test_preflight_server_error_propagates(gmail):
    # Transient Google failure says nothing about token health (REQ-1): it must
    # NOT masquerade as "needs reconnect".
    _mock_token({"error": "internal_failure"}, status=500)
    with pytest.raises(httpx.HTTPStatusError):
        gmail.preflight()


@respx.mock
def test_preflight_unknown_4xx_error_code_propagates_and_is_logged(gmail, caplog):
    _mock_token({"error": "temporarily_unavailable"}, status=400)
    with caplog.at_level(logging.WARNING), pytest.raises(httpx.HTTPStatusError):
        gmail.preflight()
    # Gate 3 H3: App Insights needs the OAuth error code, not just status+URL.
    assert any("temporarily_unavailable" in r.getMessage() for r in caplog.records)


@respx.mock
def test_preflight_non_dict_json_error_body_propagates_status_error(gmail):
    # Gate 3 M6: a valid-JSON-but-not-dict 400 body must surface as the HTTP
    # error, not an AttributeError from the error-code probe.
    _mock_token(["weird"], status=400)
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
    rejection_lines = [r.getMessage() for r in caplog.records if r.name == "pipeline.gmail_client"]
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


def test_constructor_builds_no_http_client(settings, secrets, monkeypatch):
    # Gate 3 M7: httpx.Client() eagerly builds an SSLContext (measured ~100 ms
    # cold) — every disabled wake would pay it. Construction must be lazy.
    def exploding_client(*args, **kwargs):
        raise AssertionError("constructor must not build the httpx client")

    monkeypatch.setattr("pipeline.gmail_client.httpx.Client", exploding_client)
    client = GmailClient(settings, secrets)
    client.close()  # close on a never-used client must be safe too


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
def test_get_subject_missing_payload_fails_loud(gmail):
    # Gate 3 H2: format=metadata documents payload.headers — a response without
    # them is shape drift and must raise (-> failed heartbeat), never count as
    # a silent no-match.
    _mint_ok()
    respx.get(f"{LIST_URL}/m1").mock(return_value=Response(200, json={"id": "m1"}))
    gmail.preflight()
    with pytest.raises(KeyError):
        gmail.get_subject("m1")


@respx.mock
def test_run_pipeline_accepts_the_real_client(gmail):
    # Gate 3 M8: the only proof GmailClient satisfies the GmailReader protocol —
    # every other test uses hand-rolled fakes, so a method rename would ship
    # green and AttributeError on the first connected wake.
    _mint_ok()
    respx.get(LIST_URL).mock(
        return_value=Response(200, json={"messages": [{"id": "m1"}, {"id": "m2"}]})
    )
    respx.get(f"{LIST_URL}/m1").mock(
        return_value=Response(
            200,
            json=_message(
                "m1",
                [{"name": "Subject", "value": "Declaración de siniestro a colaborador 2026/9"}],
            ),
        )
    )
    respx.get(f"{LIST_URL}/m2").mock(
        return_value=Response(200, json=_message("m2", [{"name": "Subject", "value": "spam"}]))
    )
    assert run_pipeline(gmail) == 1  # run_pipeline mints via gmail.preflight()


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
