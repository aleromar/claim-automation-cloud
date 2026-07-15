"""Session security (REQ-2/3): stateless CSRF state + backend-minted session JWT.

Both use the session signing key from the secret store. The state MAC input is
domain-separated ("state:" prefix) from the JWT use of the same key.
"""

import hashlib
import hmac
import secrets
import time

import jwt
from fastapi import Depends, HTTPException, Request

from app.config import Settings, get_settings
from app.secret_store import create_secret_store

STATE_TTL_SECONDS = 600


class InvalidSession(Exception):
    """Bearer token failed verification (signature / expiry / claims / allowlist)."""


def _state_mac(signing_key: str, nonce: str, timestamp: str) -> str:
    message = f"state:{nonce}.{timestamp}".encode()
    return hmac.new(signing_key.encode(), message, hashlib.sha256).hexdigest()


def make_state(signing_key: str) -> str:
    nonce = secrets.token_urlsafe(16)
    timestamp = str(int(time.time()))
    return f"{nonce}.{timestamp}.{_state_mac(signing_key, nonce, timestamp)}"


def verify_state(state: str, signing_key: str) -> bool:
    parts = state.split(".")
    if len(parts) != 3:
        return False
    nonce, timestamp, mac = parts
    if not hmac.compare_digest(mac, _state_mac(signing_key, nonce, timestamp)):
        return False
    try:
        age = time.time() - int(timestamp)
    except ValueError:
        return False
    return 0 <= age <= STATE_TTL_SECONDS


def mint_session_jwt(email: str, signing_key: str, ttl_hours: int) -> str:
    now = int(time.time())
    claims = {"email": email, "iat": now, "exp": now + ttl_hours * 3600}
    return jwt.encode(claims, signing_key, algorithm="HS256")


def verify_session_jwt(token: str, signing_key: str, allowlist_email: str) -> str:
    """Verify signature (pinned HS256), expiry, and the allowlisted email claim.

    Returns the email; raises InvalidSession on any failure (REQ-3.1/3.2).
    """
    try:
        claims = jwt.decode(
            token,
            signing_key,
            algorithms=["HS256"],
            options={"require": ["exp", "iat"]},
        )
    except jwt.InvalidTokenError as exc:
        raise InvalidSession(str(exc)) from exc
    email = claims.get("email")
    if not email or email != allowlist_email:
        raise InvalidSession("email not allowlisted")
    return email


def require_operator(request: Request, settings: Settings = Depends(get_settings)) -> str:
    """FastAPI dependency: 401 unless a valid operator Bearer JWT is presented."""
    scheme, _, token = request.headers.get("Authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    signing_key = create_secret_store(settings).get("session-signing-key") or ""
    try:
        return verify_session_jwt(token, signing_key, settings.allowlist_email)
    except InvalidSession as exc:
        raise HTTPException(status_code=401, detail="Not authenticated") from exc
