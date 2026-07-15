"""REQ-2/3/5.4: state HMAC, session JWT (pinned HS256 + claims), startup fail-fast."""

import time

import jwt
import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.security import (
    InvalidSession,
    make_state,
    mint_session_jwt,
    verify_session_jwt,
    verify_state,
)

KEY = "k" * 32
OPERATOR = "operator@example.com"


# --- state (CSRF) ---


def test_state_roundtrip():
    assert verify_state(make_state(KEY), KEY) is True


def test_state_tampered_rejected():
    state = make_state(KEY)
    tampered = state[:-1] + ("0" if state[-1] != "0" else "1")
    assert verify_state(tampered, KEY) is False


def test_state_wrong_key_rejected():
    assert verify_state(make_state(KEY), "x" * 32) is False


def test_state_garbage_rejected():
    assert verify_state("not-a-state", KEY) is False


def test_state_expired_rejected(monkeypatch):
    import app.security as security

    state = make_state(KEY)
    future = time.time() + 601
    monkeypatch.setattr(security.time, "time", lambda: future)
    assert verify_state(state, KEY) is False


# --- session JWT ---


def test_jwt_roundtrip_returns_email():
    token = mint_session_jwt(OPERATOR, KEY, ttl_hours=8)
    assert verify_session_jwt(token, KEY, allowlist_email=OPERATOR) == OPERATOR


def test_jwt_has_iat_and_exp_8h():
    token = mint_session_jwt(OPERATOR, KEY, ttl_hours=8)
    claims = jwt.decode(token, KEY, algorithms=["HS256"])
    assert claims["exp"] - claims["iat"] == 8 * 3600


def test_jwt_expired_rejected():
    token = mint_session_jwt(OPERATOR, KEY, ttl_hours=-1)
    with pytest.raises(InvalidSession):
        verify_session_jwt(token, KEY, allowlist_email=OPERATOR)


def test_jwt_wrong_signature_rejected():
    token = mint_session_jwt(OPERATOR, "x" * 32, ttl_hours=8)
    with pytest.raises(InvalidSession):
        verify_session_jwt(token, KEY, allowlist_email=OPERATOR)


def test_jwt_wrong_algorithm_rejected():
    hs512 = jwt.encode({"email": OPERATOR, "exp": time.time() + 300}, KEY, algorithm="HS512")
    with pytest.raises(InvalidSession):
        verify_session_jwt(hs512, KEY, allowlist_email=OPERATOR)


def test_jwt_alg_none_rejected():
    unsigned = jwt.encode({"email": OPERATOR, "exp": time.time() + 300}, None, algorithm="none")
    with pytest.raises(InvalidSession):
        verify_session_jwt(unsigned, KEY, allowlist_email=OPERATOR)


def test_jwt_non_allowlisted_email_rejected():
    token = mint_session_jwt("mallory@example.com", KEY, ttl_hours=8)
    with pytest.raises(InvalidSession):
        verify_session_jwt(token, KEY, allowlist_email=OPERATOR)


# --- startup fail-fast (REQ-5.4) — lifespan only runs under `with` ---


@pytest.fixture
def fresh_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("SECRET_STORE_BACKEND", "file")
    monkeypatch.setenv("SECRET_STORE_FILE_PATH", str(tmp_path / "secrets.json"))
    get_settings.cache_clear()
    yield tmp_path / "secrets.json"
    get_settings.cache_clear()


def test_startup_fails_without_signing_key(fresh_settings):
    from app.main import app

    with pytest.raises(RuntimeError, match="session-signing-key"):
        with TestClient(app):
            pass


def test_startup_succeeds_with_signing_key(fresh_settings):
    from app.main import app
    from app.secret_store import FileSecretStore

    FileSecretStore(fresh_settings).set("session-signing-key", KEY)
    with TestClient(app) as client:
        assert client.get("/api/health").status_code == 200
