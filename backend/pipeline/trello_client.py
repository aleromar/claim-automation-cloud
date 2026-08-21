"""Trello httpx client (pipeline-wiring REQ-3/8; D25 creds injection).

Board/list ids come from the TrelloConfig table row (D23 — the Settings page
edits them at runtime), the key/token secrets from the SecretStore: nothing is
bound at import time (the laptop's config.yaml globals are the recorded
anti-pattern). One instance per run (P12 by non-sharing); the constructor does
no I/O and the httpx client is built lazily (same E3/M7 stance as GmailClient).
"""

import logging
from time import sleep
from typing import Final, Literal

import httpx

from core.config import Settings
from core.exceptions import NoAccessError
from core.secret_store import TRELLO_API_KEY, TRELLO_TOKEN, SecretStore
from core.state_store import TrelloConfig

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT_S: Final = 10.0
# Bounded 429 retry (REQ-3): Trello's token limit is 100 req/10 s — a rate
# blip must not burn a terminal `failed` label on an otherwise-fine email.
MAX_429_RETRIES: Final = 2
RETRY_BACKOFF_S: Final = 2.0

MISSING_CONFIG: Final = "missing_config"
MISSING_CREDENTIALS: Final = "missing_credentials"
TOKEN_REJECTED: Final = "token_rejected"
NoAccessReason = Literal["missing_config", "missing_credentials", "token_rejected"]


class TrelloNoAccessError(NoAccessError):
    """Definitive credential/config failure — the Trello arm of core's
    NoAccessError wake contract (the scheduler classifies the base type as
    `skipped_no_access`; the reason lands in the skip log line)."""

    def __init__(self, reason: NoAccessReason) -> None:
        super().__init__(reason, f"trello access unavailable: {reason}")


class TrelloClient:
    def __init__(
        self, settings: Settings, secret_store: SecretStore, config: TrelloConfig | None
    ) -> None:
        self._settings = settings
        self._secrets = secret_store
        self._config = config
        self._http: httpx.Client | None = None
        self._auth: dict[str, str] | None = None

    def _http_client(self) -> httpx.Client:
        if self._http is None:
            self._http = httpx.Client(timeout=REQUEST_TIMEOUT_S)
        return self._http

    def close(self) -> None:
        if self._http is not None:
            self._http.close()

    def preflight(self) -> None:
        """Config + credential health in one shot (REQ-3): missing board/list
        ids or key/token mean "needs setup" (definitive, operator-entered —
        unlike Gmail's client secret they are not deploy artifacts); a 401 from
        /1/members/me means the token is dead."""
        if self._config is None or not self._config.board_id or not self._config.list_id:
            raise TrelloNoAccessError(MISSING_CONFIG)
        api_key = self._secrets.get(TRELLO_API_KEY)
        token = self._secrets.get(TRELLO_TOKEN)
        if not api_key or not token:
            raise TrelloNoAccessError(MISSING_CREDENTIALS)
        # HEADER auth, never query params: httpx logs full request URLs at INFO,
        # so param-auth would leak key+token into App Insights (caught by the
        # no-log guard test before landing).
        self._auth = {
            "Authorization": f'OAuth oauth_consumer_key="{api_key}", oauth_token="{token}"'
        }
        response = self._request("GET", "/1/members/me")
        if response.status_code == 401:
            logger.warning("trello rejected the token")
            raise TrelloNoAccessError(TOKEN_REJECTED)
        response.raise_for_status()

    def create_full_card(
        self, *, name: str, description: str, pdf_bytes: bytes, pdf_filename: str, comment: str
    ) -> str:
        """Create + attach + comment as ONE unit (operator, 2026-08-21:
        "duplicated comments are ok but lack of them is not"): a failure after
        the create best-effort-deletes the card so the sanctioned manual retry
        recreates the FULL card instead of duplicating a partial one."""
        assert self._config is not None  # preflight guarantees it
        card = self._checked(
            self._request(
                "POST",
                "/1/cards",
                data={"idList": self._config.list_id, "name": name, "desc": description},
            )
        ).json()
        try:
            self._checked(
                self._request(
                    "POST",
                    f"/1/cards/{card['id']}/attachments",
                    files={"file": (pdf_filename, pdf_bytes, "application/pdf")},
                )
            )
            self._checked(
                self._request(
                    "POST", f"/1/cards/{card['id']}/actions/comments", data={"text": comment}
                )
            )
        except Exception:
            try:
                self._checked(self._request("DELETE", f"/1/cards/{card['id']}"))
            except Exception:
                # The leftover card is the narrowed REQ-4 residual; the original
                # failure must surface, not the delete's.
                logger.warning("compensating delete failed for card %s", card["id"])
            raise
        return card["shortUrl"]

    def add_comment(self, card_id: str, text: str) -> None:
        self._checked(
            self._request("POST", f"/1/cards/{card_id}/actions/comments", data={"text": text})
        )

    def find_card_by_claim_ref(self, claim_ref: str) -> dict | None:
        """Every list on the board, then the archive (laptop parity,
        main.py:172-186) — substring match on the card name."""
        assert self._config is not None
        board = self._config.board_id
        lists = self._checked(self._request("GET", f"/1/boards/{board}/lists")).json()
        for board_list in lists:
            cards = self._checked(self._request("GET", f"/1/lists/{board_list['id']}/cards")).json()
            for card in cards:
                if claim_ref in card.get("name", ""):
                    return card
        archived = self._checked(
            self._request("GET", f"/1/boards/{board}/cards", params={"filter": "closed"})
        ).json()
        for card in archived:
            if claim_ref in card.get("name", ""):
                return card
        return None

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict | None = None,
        data: dict | None = None,
        files: dict | None = None,
    ) -> httpx.Response:
        if self._auth is None:
            raise RuntimeError("preflight() must succeed before Trello API calls")
        for attempt in range(MAX_429_RETRIES + 1):
            response = self._http_client().request(
                method,
                f"{self._settings.trello_api_base_url}{path}",
                params=params,
                data=data,
                files=files,
                headers=self._auth,
            )
            if response.status_code != 429:
                return response
            if attempt < MAX_429_RETRIES:
                sleep(RETRY_BACKOFF_S)
        return response

    @staticmethod
    def _checked(response: httpx.Response) -> httpx.Response:
        response.raise_for_status()
        return response
