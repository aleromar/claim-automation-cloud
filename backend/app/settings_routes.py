"""Settings endpoints (settings REQ-1/2; D25).

GET/POST only — two-layer CORS convention (worker-controls; D22 supplies the
layers). Handlers are plain `def`: both stores are sync clients, and FastAPI
runs sync handlers in its threadpool, off the event loop.

Secrets are write-only: presence flags out, never values (REQ-1.2). Saves write
in fixed order (secrets → table) and every write is idempotent, so a failed
save is always safe to retry (REQ-2.7).
"""

import logging

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.auth_routes import get_store
from app.config import Settings, get_settings
from app.secret_store import (
    GMAIL_REFRESH_TOKEN,
    TRELLO_API_KEY,
    TRELLO_TOKEN,
    SecretStore,
)
from app.security import require_operator
from app.state_store import StateStore, TrelloConfig, get_state_store

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/settings", dependencies=[Depends(require_operator)])


class TrelloState(BaseModel):
    api_key_stored: bool
    token_stored: bool
    board_id: str
    list_id: str


class GmailState(BaseModel):
    account_email: str
    refresh_token_stored: bool


class SettingsState(BaseModel):
    trello: TrelloState
    gmail: GmailState


class TrelloSettingsBody(BaseModel):
    # Secrets: blank after trim = keep stored (REQ-2.2). IDs: authoritative as
    # submitted, trimmed (REQ-2.3) — no blank=keep, the form always shows them.
    api_key: str = ""
    token: str = ""
    board_id: str = ""
    list_id: str = ""


def _trello_state(secrets: SecretStore, store: StateStore) -> TrelloState:
    config = store.read_trello_config() or TrelloConfig(board_id="", list_id="")
    return TrelloState(
        api_key_stored=secrets.get(TRELLO_API_KEY) is not None,
        token_stored=secrets.get(TRELLO_TOKEN) is not None,
        board_id=config.board_id,
        list_id=config.list_id,
    )


@router.get("")
def read_settings(
    settings: Settings = Depends(get_settings),
    secrets: SecretStore = Depends(get_store),
    store: StateStore = Depends(get_state_store),
) -> SettingsState:
    return SettingsState(
        trello=_trello_state(secrets, store),
        gmail=GmailState(
            account_email=settings.operator_email,
            refresh_token_stored=secrets.get(GMAIL_REFRESH_TOKEN) is not None,
        ),
    )


@router.post("/trello")
def save_trello_settings(
    body: TrelloSettingsBody,
    secrets: SecretStore = Depends(get_store),
    store: StateStore = Depends(get_state_store),
) -> TrelloState:
    api_key = body.api_key.strip()
    token = body.token.strip()
    if api_key:
        secrets.set(TRELLO_API_KEY, api_key)
    if token:
        secrets.set(TRELLO_TOKEN, token)
    store.write_trello_config(
        TrelloConfig(board_id=body.board_id.strip(), list_id=body.list_id.strip())
    )
    logger.info(
        "trello settings saved: api_key=%s, token=%s, ids=updated",
        "updated" if api_key else "kept",
        "updated" if token else "kept",
    )
    return _trello_state(secrets, store)
