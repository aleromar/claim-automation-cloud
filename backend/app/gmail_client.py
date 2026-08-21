"""Gmail REST client rewritten around the SecretStore-held refresh token
(gmail-client REQ-1/3/6; D2/D16/D18). Plain httpx — the SDK's discovery
plumbing and credential objects add nothing over the documented endpoints.

Read-only by construction: no modify/label/send method exists here — the
mailbox-mutating surface ships with its 5c callers (spec N1/N3).

One instance per wake / per process-now request (REQ-6): nothing here is shared
across threads, so no lock (constitution P12 satisfied by non-sharing). The
constructor performs no I/O — secret reads happen inside preflight(), so a
disabled wake never touches the SecretStore (gate E3).
"""

import logging
from typing import Final

import httpx

from app.config import Settings
from app.secret_store import GMAIL_REFRESH_TOKEN, GOOGLE_CLIENT_SECRET, SecretStore

logger = logging.getLogger(__name__)

# One page of the newest UNREAD messages — the "analyze the last 100" contract
# (operator, 2026-08-21; saturation blind spot recorded in the spec + backlog).
PROBE_MAX_RESULTS: Final = 100
# Per-request cap; the probe's overall 120 s deadline lives in pipeline.entry.
REQUEST_TIMEOUT_S: Final = 10.0

# Token-endpoint error codes that mean the credentials are definitively dead
# (revoked/stale/deleted — "reconnect Gmail" territory), as opposed to a
# transient Google failure which must surface as a failed run instead (REQ-1).
_DEFINITIVE_TOKEN_ERRORS: Final = ("invalid_grant", "invalid_client", "deleted_client")

MISSING_TOKEN = "missing_token"
TOKEN_REJECTED = "token_rejected"


class GmailNoAccessError(Exception):
    """Definitive credential failure — the wake exits `skipped_no_access`."""

    def __init__(self, reason: str) -> None:
        super().__init__(f"gmail access unavailable: {reason}")
        self.reason = reason


class GmailClient:
    def __init__(self, settings: Settings, secret_store: SecretStore) -> None:
        self._settings = settings
        self._secrets = secret_store
        self._http = httpx.Client(timeout=REQUEST_TIMEOUT_S)
        self._access_token: str | None = None

    def close(self) -> None:
        self._http.close()

    def preflight(self) -> None:
        """Mint an access token from the stored refresh token (REQ-1) — one
        POST, reused for the whole run. This IS the token-health check."""
        refresh_token = self._secrets.get(GMAIL_REFRESH_TOKEN)
        if not refresh_token:
            raise GmailNoAccessError(MISSING_TOKEN)
        response = self._http.post(
            self._settings.google_token_url,
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": self._settings.google_client_id,
                "client_secret": self._secrets.get(GOOGLE_CLIENT_SECRET),
            },
        )
        if response.status_code in (400, 401):
            error_code = self._error_code(response)
            if error_code in _DEFINITIVE_TOKEN_ERRORS:
                logger.warning("token endpoint rejected the refresh token: %s", error_code)
                raise GmailNoAccessError(TOKEN_REJECTED)
        response.raise_for_status()
        self._access_token = response.json()["access_token"]

    @staticmethod
    def _error_code(response: httpx.Response) -> str:
        try:
            return response.json().get("error", "")
        except ValueError:
            return ""

    def list_unread_message_ids(self) -> list[str]:
        """Ids of the newest PROBE_MAX_RESULTS UNREAD messages, one page (C1).
        Note: "newest first" is observed Gmail behavior, not documented —
        accepted residual, see the spec's verified-claims note."""
        body = self._get(
            "/gmail/v1/users/me/messages",
            params={"labelIds": "UNREAD", "maxResults": PROBE_MAX_RESULTS},
        )
        return [message["id"] for message in body.get("messages", [])]

    def get_subject(self, message_id: str) -> str:
        """Subject header only (C5: format=metadata). Missing header → ""."""
        body = self._get(
            f"/gmail/v1/users/me/messages/{message_id}",
            params={"format": "metadata", "metadataHeaders": "Subject"},
        )
        for header in body.get("payload", {}).get("headers", []):
            # Header names are case-insensitive on the wire (gate E7).
            if header.get("name", "").lower() == "subject":
                return header.get("value", "")
        return ""

    def _get(self, path: str, params: dict) -> dict:
        if self._access_token is None:
            raise RuntimeError("preflight() must succeed before Gmail API calls")
        response = self._http.get(
            f"{self._settings.gmail_api_base_url}{path}",
            params=params,
            headers={"Authorization": f"Bearer {self._access_token}"},
        )
        response.raise_for_status()
        return response.json()
