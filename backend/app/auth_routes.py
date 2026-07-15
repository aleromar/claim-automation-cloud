"""OAuth broker routes (REQ-1/2): login redirect + Google callback.

The callback validates id_token *claims* explicitly (iss/aud/exp): the token
arrives over TLS directly from the configured token endpoint (OIDC
direct-channel rule), and PyJWT's verify_signature=False would otherwise
silently skip every claim check too.
"""

from urllib.parse import urlencode

import httpx
import jwt
from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse

from app.config import Settings, get_settings
from app.secret_store import SecretStore, create_secret_store
from app.security import make_state, mint_session_jwt, verify_state

GOOGLE_ISSUERS = ("https://accounts.google.com", "accounts.google.com")
SCOPES = "openid email https://www.googleapis.com/auth/gmail.modify"

router = APIRouter(prefix="/api/auth")


def get_store(settings: Settings = Depends(get_settings)) -> SecretStore:
    return create_secret_store(settings)


@router.get("/login")
def login(
    settings: Settings = Depends(get_settings), store: SecretStore = Depends(get_store)
) -> RedirectResponse:
    params = {
        "client_id": settings.google_client_id,
        "redirect_uri": settings.oauth_redirect_uri,
        "response_type": "code",
        "scope": SCOPES,
        "access_type": "offline",  # no prompt=consent: re-consent only on staleness (D18)
        "state": make_state(store.get("session-signing-key") or ""),
    }
    return RedirectResponse(f"{settings.google_auth_url}?{urlencode(params)}", status_code=302)


@router.get("/callback")
def callback(
    request: Request,
    settings: Settings = Depends(get_settings),
    store: SecretStore = Depends(get_store),
) -> RedirectResponse:
    # Redirect target is the fixed configured SPA URL — never request-derived (REQ-2.2).
    def fail(kind: str = "login_failed") -> RedirectResponse:
        return RedirectResponse(f"{settings.frontend_base_url}#error={kind}", status_code=302)

    params = request.query_params
    if params.get("error") or not params.get("code") or not params.get("state"):
        return fail()

    signing_key = store.get("session-signing-key") or ""
    if not verify_state(params["state"], signing_key):
        return fail()

    try:
        token_response = httpx.post(
            settings.google_token_url,
            data={
                "code": params["code"],
                "client_id": settings.google_client_id,
                "client_secret": store.get("google-client-secret") or "",
                "redirect_uri": settings.oauth_redirect_uri,
                "grant_type": "authorization_code",
            },
        )
        token_response.raise_for_status()
        payload = token_response.json()
    except (httpx.HTTPError, ValueError):
        return fail()

    id_token = payload.get("id_token")
    if not id_token:
        return fail()
    try:
        claims = jwt.decode(
            id_token,
            options={
                "verify_signature": False,
                "verify_exp": True,
                "verify_aud": True,
                "require": ["exp", "iss", "aud", "email"],
            },
            audience=settings.google_client_id,
        )
    except jwt.InvalidTokenError:
        return fail()
    if claims["iss"] not in GOOGLE_ISSUERS:
        return fail()

    email = claims["email"]
    if email != settings.allowlist_email:
        return fail("unauthorized")  # nothing stored for rejected logins (REQ-2.5)

    refresh_token = payload.get("refresh_token")
    if refresh_token:  # absent on repeat grants — keep the stored one (REQ-2.3)
        store.set("gmail-refresh-token", refresh_token)

    session_jwt = mint_session_jwt(email, signing_key, settings.jwt_ttl_hours)
    return RedirectResponse(f"{settings.frontend_base_url}#token={session_jwt}", status_code=302)
