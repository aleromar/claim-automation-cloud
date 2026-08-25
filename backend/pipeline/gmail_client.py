"""Gmail REST client rewritten around the SecretStore-held refresh token
(gmail-client REQ-1/3/6; D2/D16/D18). Plain httpx — the SDK's discovery
plumbing and credential objects add nothing over the documented endpoints.

Mailbox-mutating surface (modify_labels, label create) added by pipeline-wiring
(5c REQ-6) — supersedes 5b's read-only-by-construction note (spec N1/N3).

One instance per wake / per process-now request (REQ-6): no new unlocked
object is shared across threads (constitution P12 satisfied by non-sharing;
the SecretStore it reads carries its own lock). The constructor performs no
I/O — secret reads happen inside preflight() and the httpx client is built
lazily on first request (gate E3/M7), so a disabled wake costs nothing.
"""

import logging
from typing import Final, Literal

import httpx

from core.config import Settings
from core.exceptions import NoAccessError
from core.secret_store import (
    GMAIL_REFRESH_TOKEN,
    GOOGLE_CLIENT_SECRET,
    SecretStore,
    require_secret,
)

logger = logging.getLogger(__name__)

# One page of the newest UNREAD messages — the "analyze the last 100" contract
# (operator, 2026-08-21; saturation blind spot recorded in the spec + backlog).
PROBE_MAX_RESULTS: Final = 100
# Per-request cap; the probe's overall 120 s deadline lives in pipeline.entry.
REQUEST_TIMEOUT_S: Final = 10.0

# Token-endpoint error codes that mean the credentials are definitively dead
# (revoked/stale/deleted/de-authorized — non-transient, operator-actionable),
# as opposed to a transient Google failure which must surface as a failed run
# instead (REQ-1).
_DEFINITIVE_TOKEN_ERRORS: Final = (
    "invalid_grant",
    "invalid_client",
    "deleted_client",
    "unauthorized_client",
)

MISSING_TOKEN: Final = "missing_token"
TOKEN_REJECTED: Final = "token_rejected"
NoAccessReason = Literal["missing_token", "token_rejected"]


class GmailNoAccessError(NoAccessError):
    """Definitive credential failure — the Gmail arm of core's NoAccessError
    wake contract. The scheduler classifies the base type as `skipped_no_access`,
    and only when the *preflight* raises it (gate E5)."""

    def __init__(self, reason: NoAccessReason) -> None:
        super().__init__(reason, f"gmail access unavailable: {reason}")


class GmailClient:
    def __init__(self, settings: Settings, secret_store: SecretStore) -> None:
        self._settings = settings
        self._secrets = secret_store
        # Lazy: httpx.Client() eagerly builds an SSLContext (~100 ms cold) —
        # a disabled wake must not pay it (gate M7).
        self._http: httpx.Client | None = None
        self._access_token: str | None = None
        # Per-instance = per-run label cache (5c REQ-6): the laptop re-listed
        # labels on every modify; one lookup per run suffices.
        self._label_ids: dict[str, str] = {}

    def _http_client(self) -> httpx.Client:
        if self._http is None:
            self._http = httpx.Client(timeout=REQUEST_TIMEOUT_S)
        return self._http

    def close(self) -> None:
        if self._http is not None:
            self._http.close()

    def preflight(self) -> None:
        """Mint an access token from the stored refresh token (REQ-1) — one
        POST, reused for the whole run. This IS the token-health check."""
        refresh_token = self._secrets.get(GMAIL_REFRESH_TOKEN)
        if not refresh_token:
            raise GmailNoAccessError(MISSING_TOKEN)
        response = self._http_client().post(
            self._settings.google_token_url,
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": self._settings.google_client_id,
                # require_secret: an absent client secret must be a deployment
                # fault (failed run), NOT reach Google as client_secret="" →
                # invalid_client → a false, unfixable "reconnect Gmail" (H1).
                "client_secret": require_secret(self._secrets, GOOGLE_CLIENT_SECRET),
            },
        )
        if response.status_code in (400, 401):
            error_code = self._error_code(response)
            if error_code in _DEFINITIVE_TOKEN_ERRORS:
                logger.warning("token endpoint rejected the refresh token: %s", error_code)
                raise GmailNoAccessError(TOKEN_REJECTED)
            # Non-definitive rejection: propagates as failed below, but App
            # Insights needs the OAuth code, not just status+URL (H3).
            logger.warning("token endpoint 4xx with non-definitive error: %s", error_code)
        response.raise_for_status()
        self._access_token = response.json()["access_token"]

    @staticmethod
    def _error_code(response: httpx.Response) -> str:
        try:
            body = response.json()
        except ValueError:
            return ""
        # A valid-JSON non-dict body (M6) carries no OAuth error field.
        return body.get("error", "") if isinstance(body, dict) else ""

    def list_unread_message_ids(self, query: str | None = None) -> list[str]:
        """Ids of one UNREAD page (C1), optionally server-side filtered
        (pipeline-wiring REQ-1: the subject query rides as `q`). List order is
        undocumented — 5c sorts client-side on internalDate instead."""
        params: dict = {"labelIds": "UNREAD", "maxResults": PROBE_MAX_RESULTS}
        if query is not None:
            params["q"] = query
        body = self._get("/gmail/v1/users/me/messages", params=params)
        # A no-match response may omit the key entirely (docs pin neither shape).
        return [message["id"] for message in body.get("messages", [])]

    def get_message(self, message_id: str) -> dict:
        """The full message resource (format=full): bodies for the 5a
        `from_msg_data` seam and `internalDate` for the chronological sort come
        from this ONE fetch (pipeline-wiring REQ-1)."""
        return self._get(
            f"/gmail/v1/users/me/messages/{message_id}",
            params={"format": "full"},
        )

    def modify_labels(
        self, message_id: str, add_label_ids: list[str], remove_label_ids: list[str]
    ) -> None:
        """One messages.modify call — add and remove ride together (laptop
        parity: relabel + mark-as-read is a single mutation)."""
        self._post(
            f"/gmail/v1/users/me/messages/{message_id}/modify",
            json={"addLabelIds": add_label_ids, "removeLabelIds": remove_label_ids},
        )

    def get_or_create_label_id(self, name: str) -> str:
        """Case-insensitive lookup, created if missing (port of the laptop's
        get_or_create_label_id — main.py:196-209), cached per instance."""
        cached = self._label_ids.get(name.lower())
        if cached is not None:
            return cached
        body = self._get("/gmail/v1/users/me/labels", params={})
        for label in body.get("labels", []):
            self._label_ids[label["name"].lower()] = label["id"]
        found = self._label_ids.get(name.lower())
        if found is not None:
            return found
        created = self._post("/gmail/v1/users/me/labels", json={"name": name})
        self._label_ids[name.lower()] = created["id"]
        return created["id"]

    def get_subject(self, message_id: str) -> str:
        """Subject header only (C5: format=metadata). payload.headers is
        documented for METADATA responses — indexing is strict on purpose so
        shape drift raises instead of counting as a silent no-match (H2).
        A present header set without Subject → "" (counts as no match)."""
        body = self._get(
            f"/gmail/v1/users/me/messages/{message_id}",
            params={"format": "metadata", "metadataHeaders": "Subject"},
        )
        for header in body["payload"]["headers"]:
            # Header names are case-insensitive on the wire (gate E7).
            if header.get("name", "").lower() == "subject":
                return header.get("value", "")
        return ""

    def _get(self, path: str, params: dict) -> dict:
        response = self._http_client().get(
            f"{self._settings.gmail_api_base_url}{path}",
            params=params,
            headers=self._auth_header(),
        )
        response.raise_for_status()
        return response.json()

    def _post(self, path: str, json: dict) -> dict:
        response = self._http_client().post(
            f"{self._settings.gmail_api_base_url}{path}",
            json=json,
            headers=self._auth_header(),
        )
        response.raise_for_status()
        return response.json()

    def _auth_header(self) -> dict[str, str]:
        if self._access_token is None:
            raise RuntimeError("preflight() must succeed before Gmail API calls")
        return {"Authorization": f"Bearer {self._access_token}"}

    def count_messages_with_label(self, label_id: str) -> int:
        """One-page count for the failed-label gauge (pipeline-wiring REQ-5):
        a nextPageToken means "more than the page" — disclosed as the 101 cap
        rather than paying pagination for a number whose message is "a lot"."""
        body = self._get(
            "/gmail/v1/users/me/messages",
            params={"labelIds": label_id, "maxResults": PROBE_MAX_RESULTS},
        )
        if body.get("nextPageToken"):
            return PROBE_MAX_RESULTS + 1
        return len(body.get("messages", []))
