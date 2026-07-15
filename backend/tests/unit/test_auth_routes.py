"""REQ-1/2/3: /api/auth/login, /api/auth/callback (all branches), /api/me guard."""

import time
from urllib.parse import parse_qs, urlsplit

import jwt
import pytest
import respx
from fastapi.testclient import TestClient
from httpx import Response

from app.config import get_settings
from app.secret_store import FileSecretStore
from app.security import make_state, mint_session_jwt, verify_session_jwt, verify_state

SIGNING_KEY = "k" * 32
OPERATOR = "operator@example.com"
CLIENT_ID = "client-id-123"
AUTH_URL = "https://idp.test/authorize"
TOKEN_URL = "https://idp.test/token"
FRONTEND = "http://front.test"
GOOGLE_ISS = "https://accounts.google.com"


@pytest.fixture
def secret_path(tmp_path, monkeypatch):
    path = tmp_path / "secrets.json"
    monkeypatch.setenv("SECRET_STORE_BACKEND", "file")
    monkeypatch.setenv("SECRET_STORE_FILE_PATH", str(path))
    monkeypatch.setenv("ALLOWLIST_EMAIL", OPERATOR)
    monkeypatch.setenv("GOOGLE_CLIENT_ID", CLIENT_ID)
    monkeypatch.setenv("GOOGLE_AUTH_URL", AUTH_URL)
    monkeypatch.setenv("GOOGLE_TOKEN_URL", TOKEN_URL)
    monkeypatch.setenv("FRONTEND_BASE_URL", FRONTEND)
    store = FileSecretStore(path)
    store.set("session-signing-key", SIGNING_KEY)
    store.set("google-client-secret", "shh-client-secret")
    get_settings.cache_clear()
    yield path
    get_settings.cache_clear()


@pytest.fixture
def client(secret_path):
    from app.main import app

    return TestClient(app, follow_redirects=False)


def _id_token(email=OPERATOR, iss=GOOGLE_ISS, aud=CLIENT_ID, exp_offset=300):
    claims = {"iss": iss, "aud": aud, "exp": int(time.time()) + exp_offset, "email": email}
    return jwt.encode(claims, "stub-idp-key", algorithm="HS256")


def _mock_token_endpoint(payload=None, status=200):
    return respx.post(TOKEN_URL).mock(
        return_value=Response(status, json=payload if payload is not None else {})
    )


def _fragment(location: str) -> str:
    return urlsplit(location).fragment


# --- /api/auth/login (REQ-1) ---


def test_login_redirects_to_configured_idp(client):
    resp = client.get("/api/auth/login")
    assert resp.status_code == 302
    assert resp.headers["location"].startswith(AUTH_URL + "?")


def test_login_query_params(client):
    query = parse_qs(urlsplit(client.get("/api/auth/login").headers["location"]).query)
    assert query["client_id"] == [CLIENT_ID]
    assert query["response_type"] == ["code"]
    assert query["access_type"] == ["offline"]
    scopes = query["scope"][0].split()
    assert set(scopes) == {"openid", "email", "https://www.googleapis.com/auth/gmail.modify"}
    assert "prompt" not in query  # no forced re-consent (D18)


def test_login_state_is_verifiable(client):
    query = parse_qs(urlsplit(client.get("/api/auth/login").headers["location"]).query)
    assert verify_state(query["state"][0], SIGNING_KEY) is True


# --- /api/auth/callback (REQ-2) ---


@respx.mock
def test_callback_happy_path_mints_jwt_and_stores_refresh_token(client, secret_path):
    route = _mock_token_endpoint({"id_token": _id_token(), "refresh_token": "rt-1"})
    state = make_state(SIGNING_KEY)

    resp = client.get(f"/api/auth/callback?code=abc&state={state}")

    assert resp.status_code == 302
    location = resp.headers["location"]
    assert location.startswith(FRONTEND)
    token = _fragment(location).removeprefix("token=")
    assert verify_session_jwt(token, SIGNING_KEY, allowlist_email=OPERATOR) == OPERATOR
    assert FileSecretStore(secret_path).get("gmail-refresh-token") == "rt-1"
    sent = parse_qs(route.calls.last.request.content.decode())
    assert sent["code"] == ["abc"]
    assert sent["client_id"] == [CLIENT_ID]
    assert sent["client_secret"] == ["shh-client-secret"]
    assert sent["grant_type"] == ["authorization_code"]


@respx.mock
def test_callback_without_refresh_token_keeps_stored_one(client, secret_path):
    FileSecretStore(secret_path).set("gmail-refresh-token", "rt-old")
    _mock_token_endpoint({"id_token": _id_token()})

    resp = client.get(f"/api/auth/callback?code=abc&state={make_state(SIGNING_KEY)}")

    assert _fragment(resp.headers["location"]).startswith("token=")
    assert FileSecretStore(secret_path).get("gmail-refresh-token") == "rt-old"


@respx.mock
def test_callback_without_refresh_token_first_login_still_succeeds(client, secret_path):
    _mock_token_endpoint({"id_token": _id_token()})

    resp = client.get(f"/api/auth/callback?code=abc&state={make_state(SIGNING_KEY)}")

    assert _fragment(resp.headers["location"]).startswith("token=")
    assert FileSecretStore(secret_path).get("gmail-refresh-token") is None


@respx.mock
def test_callback_bad_state_rejected(client, secret_path):
    route = _mock_token_endpoint({"id_token": _id_token(), "refresh_token": "rt-1"})

    resp = client.get("/api/auth/callback?code=abc&state=forged.123.deadbeef")

    assert _fragment(resp.headers["location"]) == "error=login_failed"
    assert not route.called
    assert FileSecretStore(secret_path).get("gmail-refresh-token") is None


@respx.mock
def test_callback_failed_exchange_rejected(client):
    _mock_token_endpoint(status=400)
    resp = client.get(f"/api/auth/callback?code=bad&state={make_state(SIGNING_KEY)}")
    assert _fragment(resp.headers["location"]) == "error=login_failed"


@respx.mock
def test_callback_access_denied_no_token_request(client):
    route = _mock_token_endpoint({})
    resp = client.get("/api/auth/callback?error=access_denied")
    assert _fragment(resp.headers["location"]) == "error=login_failed"
    assert not route.called


@respx.mock
def test_callback_non_allowlisted_email_rejected(client, secret_path):
    _mock_token_endpoint(
        {"id_token": _id_token(email="mallory@example.com"), "refresh_token": "rt-evil"}
    )

    resp = client.get(f"/api/auth/callback?code=abc&state={make_state(SIGNING_KEY)}")

    assert _fragment(resp.headers["location"]) == "error=unauthorized"
    assert FileSecretStore(secret_path).get("gmail-refresh-token") is None


@pytest.mark.parametrize(
    "id_token_kwargs",
    [
        {"iss": "https://evil.example"},
        {"aud": "other-client"},
        {"exp_offset": -300},
    ],
    ids=["bad-iss", "bad-aud", "expired"],
)
@respx.mock
def test_callback_invalid_id_token_claims_rejected(client, secret_path, id_token_kwargs):
    _mock_token_endpoint({"id_token": _id_token(**id_token_kwargs), "refresh_token": "rt-1"})

    resp = client.get(f"/api/auth/callback?code=abc&state={make_state(SIGNING_KEY)}")

    assert _fragment(resp.headers["location"]) == "error=login_failed"
    assert FileSecretStore(secret_path).get("gmail-refresh-token") is None


@respx.mock
def test_callback_ignores_hostile_redirect_params(client):
    _mock_token_endpoint({"id_token": _id_token(), "refresh_token": "rt-1"})
    state = make_state(SIGNING_KEY)

    resp = client.get(
        f"/api/auth/callback?code=abc&state={state}"
        "&redirect_uri=https://evil.example&next=https://evil.example"
    )

    assert resp.headers["location"].startswith(FRONTEND)  # fixed target only (REQ-2.2)


# --- /api/me guard (REQ-3) ---


def _me(client, token=None, header=None):
    headers = {}
    if header is not None:
        headers["Authorization"] = header
    elif token is not None:
        headers["Authorization"] = f"Bearer {token}"
    return client.get("/api/me", headers=headers)


def test_me_valid_jwt(client):
    token = mint_session_jwt(OPERATOR, SIGNING_KEY, ttl_hours=8)
    resp = _me(client, token)
    assert resp.status_code == 200
    assert resp.json() == {"email": OPERATOR}


@pytest.mark.parametrize(
    "token_factory",
    [
        lambda: None,  # missing
        lambda: "garbage",  # malformed
        lambda: mint_session_jwt(OPERATOR, SIGNING_KEY, ttl_hours=-1),  # expired
        lambda: mint_session_jwt(OPERATOR, "x" * 32, ttl_hours=8),  # bad signature
        lambda: jwt.encode(  # wrong algorithm
            {"email": OPERATOR, "iat": int(time.time()), "exp": int(time.time()) + 300},
            SIGNING_KEY,
            algorithm="HS512",
        ),
        lambda: mint_session_jwt("mallory@example.com", SIGNING_KEY, ttl_hours=8),  # wrong email
    ],
    ids=["missing", "malformed", "expired", "bad-sig", "wrong-alg", "wrong-email"],
)
def test_me_invalid_tokens_401(client, token_factory):
    token = token_factory()
    resp = _me(client, token) if token else client.get("/api/me")
    assert resp.status_code == 401


def test_me_non_bearer_scheme_401(client):
    resp = _me(client, header="Basic abc123")
    assert resp.status_code == 401


def test_health_stays_public(client):
    assert client.get("/api/health").status_code == 200
