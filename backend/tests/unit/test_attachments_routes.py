"""attachment-download REQ-2: GET /api/claims/{year}/{number}/attachments → zip.

Fake store via dependency_overrides (Trello config row seeded), real JWT guard
(shared `secrets`/`auth` fixtures), respx on the Trello wire. The Trello base
URL is pinned through the env so no test can reach api.trello.com.
"""

import logging
import zipfile
from io import BytesIO
from urllib.parse import quote

import pytest
import respx
from fastapi.testclient import TestClient
from httpx import Response

from app.attachments_routes import (
    DETAIL_CARD_NOT_FOUND,
    DETAIL_NO_ATTACHMENTS,
    DETAIL_TIMEOUT,
    DETAIL_TRELLO_ERROR,
    DETAIL_TRELLO_NO_ACCESS,
    DOWNLOAD_DEADLINE_S,
)
from app.main import app
from core.config import get_settings
from core.secret_store import TRELLO_API_KEY, TRELLO_TOKEN
from core.state_store import TrelloConfig, get_state_store
from pipeline.trello_client import TrelloClient

API_BASE = "https://trello.test"
BOARD_ID = "board-xyz"
PATH = "/api/claims/2026/417/attachments"
PDF_NAME = "claim_417_2026.pdf"


@pytest.fixture(autouse=True)
def trello_env(monkeypatch, secrets, fake_store):
    monkeypatch.setenv("TRELLO_API_BASE_URL", API_BASE)
    get_settings.cache_clear()
    secrets.set(TRELLO_API_KEY, "key-123")
    secrets.set(TRELLO_TOKEN, "token-456")
    fake_store.trello = TrelloConfig(board_id=BOARD_ID, list_id="list-abc")
    app.dependency_overrides[get_state_store] = lambda: fake_store
    yield
    app.dependency_overrides.pop(get_state_store, None)


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def _board(cards: list[dict], archived: list[dict] | None = None) -> None:
    respx.get(f"{API_BASE}/1/members/me").mock(return_value=Response(200, json={"id": "me"}))
    respx.get(f"{API_BASE}/1/boards/{BOARD_ID}/lists").mock(
        return_value=Response(200, json=[{"id": "l1"}])
    )
    respx.get(f"{API_BASE}/1/lists/l1/cards").mock(return_value=Response(200, json=cards))
    respx.get(f"{API_BASE}/1/boards/{BOARD_ID}/cards").mock(
        return_value=Response(200, json=archived or [])
    )


CARD = {"id": "card-7", "name": "MADRID 2026/417 Nombre Apellido"}


def _upload(att_id: str, name: str, size: int = 3) -> dict:
    return {"id": att_id, "name": name, "bytes": size, "mimeType": "image/jpeg", "isUpload": True}


def _attachments(items: list[dict]) -> None:
    respx.get(f"{API_BASE}/1/cards/card-7/attachments").mock(return_value=Response(200, json=items))


def _download(att_id: str, name: str, body: bytes) -> respx.Route:
    return respx.get(
        f"{API_BASE}/1/cards/card-7/attachments/{att_id}/download/{quote(name, safe='')}"
    ).mock(return_value=Response(200, content=body))


def _happy_card() -> None:
    _board([CARD])
    _attachments(
        [
            _upload("att-1", "IMG_0001.jpg"),
            _upload("att-2", PDF_NAME),
            {"id": "att-3", "name": "https://example.test/doc", "isUpload": False},
            _upload("att-4", "IMG_0002.jpg"),
        ]
    )
    _download("att-1", "IMG_0001.jpg", b"one")
    _download("att-4", "IMG_0002.jpg", b"two")


def _zip_names(body: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(BytesIO(body)) as archive:
        return {info.filename: archive.read(info) for info in archive.infolist()}


# --- guard + validation (REQ-2.2, 2.9) ---


def test_attachments_route_requires_auth(client):
    assert client.get(PATH).status_code == 401


@pytest.mark.parametrize("ref", ["26/1", "2026/abc", "20266/1", "٢٠٢٦/١"])
def test_bad_claim_ref_segments_422(client, auth, ref):
    # Unicode digits (last case) pass `\d` but not `[0-9]` — and would crash
    # the latin-1 Content-Disposition header into a 500 outside CORS (gate ER-1).
    year, number = ref.split("/")
    resp = client.get(f"/api/claims/{year}/{number}/attachments", headers=auth)
    assert resp.status_code == 422
    assert "input" not in str(resp.json())


# --- lookup outcomes (REQ-2.3, 2.4) ---


@respx.mock
def test_card_not_found_404(client, auth):
    _board([])
    resp = client.get(PATH, headers=auth)
    assert resp.status_code == 404
    assert resp.json() == {"detail": DETAIL_CARD_NOT_FOUND}


@respx.mock
def test_archived_card_is_found(client, auth):
    _board([], archived=[CARD])
    _attachments([_upload("att-1", "a.jpg")])
    _download("att-1", "a.jpg", b"a")
    assert client.get(PATH, headers=auth).status_code == 200


@respx.mock
def test_no_uploads_404(client, auth):
    # A link attachment plus the pipeline's own PDF: nothing to download.
    _board([CARD])
    _attachments(
        [_upload("att-2", PDF_NAME), {"id": "att-3", "name": "https://x", "isUpload": False}]
    )
    resp = client.get(PATH, headers=auth)
    assert resp.status_code == 404
    assert resp.json() == {"detail": DETAIL_NO_ATTACHMENTS}


@pytest.mark.parametrize(
    "setup",
    [
        pytest.param(lambda secrets, store: store.__setattr__("trello", None), id="missing_config"),
        pytest.param(
            lambda secrets, store: secrets.set(TRELLO_TOKEN, ""), id="missing_credentials"
        ),
        pytest.param(
            lambda secrets, store: respx.get(f"{API_BASE}/1/members/me").mock(
                return_value=Response(401, text="invalid token")
            ),
            id="token_rejected",
        ),
    ],
)
@respx.mock
def test_trello_no_access_503(client, auth, secrets, fake_store, setup):
    setup(secrets, fake_store)
    resp = client.get(PATH, headers=auth)
    assert resp.status_code == 503
    assert resp.json() == {"detail": DETAIL_TRELLO_NO_ACCESS}


# --- the zip (REQ-2.1) ---


@respx.mock
def test_zip_roundtrip_excludes_pipeline_pdf_and_links(client, auth):
    _happy_card()
    resp = client.get(PATH, headers=auth)
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/zip"
    assert resp.headers["content-disposition"] == 'attachment; filename="2026_417.zip"'
    assert _zip_names(resp.content) == {"IMG_0001.jpg": b"one", "IMG_0002.jpg": b"two"}


@respx.mock
def test_entry_names_are_basenames(client, auth):
    # ZipFile.writestr stores "../evil.jpg" verbatim (gate ER-3): a hostile or
    # odd Trello name must not become a path in the operator's zip.
    _board([CARD])
    _attachments(
        [_upload("att-1", "../evil.jpg"), _upload("att-2", "/abs.jpg"), _upload("att-3", "")]
    )
    _download("att-1", "../evil.jpg", b"e")
    _download("att-2", "/abs.jpg", b"a")
    _download("att-3", "", b"n")
    names = _zip_names(client.get(PATH, headers=auth).content)
    assert names == {"evil.jpg": b"e", "abs.jpg": b"a", "att-3": b"n"}


@respx.mock
def test_duplicate_names_are_suffixed(client, auth):
    _board([CARD])
    _attachments(
        [_upload("att-1", "foto.jpg"), _upload("att-2", "foto.jpg"), _upload("att-3", "foto.jpg")]
    )
    _download("att-1", "foto.jpg", b"1")
    _download("att-2", "foto.jpg", b"2")
    _download("att-3", "foto.jpg", b"3")
    names = _zip_names(client.get(PATH, headers=auth).content)
    assert names == {"foto.jpg": b"1", "foto (2).jpg": b"2", "foto (3).jpg": b"3"}


# --- Trello failures + deadline (REQ-2.5, 2.6) ---


@respx.mock
def test_trello_5xx_502(client, auth):
    _board([CARD])
    _attachments([_upload("att-1", "a.jpg")])
    respx.get(f"{API_BASE}/1/cards/card-7/attachments/att-1/download/a.jpg").mock(
        return_value=Response(503)
    )
    resp = client.get(PATH, headers=auth)
    assert resp.status_code == 502
    assert resp.json() == {"detail": DETAIL_TRELLO_ERROR}


@respx.mock
def test_trello_5xx_at_preflight_502(client, auth):
    # Gate 3 W1: a Trello outage on /1/members/me is a Trello failure too — it
    # must be the CORS-readable 502, not an unhandled 500.
    respx.get(f"{API_BASE}/1/members/me").mock(return_value=Response(503))
    resp = client.get(PATH, headers=auth)
    assert resp.status_code == 502
    assert resp.json() == {"detail": DETAIL_TRELLO_ERROR}


def _past_deadline(monkeypatch) -> None:
    clock = iter([0.0, 0.0, DOWNLOAD_DEADLINE_S + 1.0])
    monkeypatch.setattr("app.attachments_routes.monotonic", lambda: next(clock))


@respx.mock
def test_deadline_504_never_partial(client, auth, monkeypatch):
    _happy_card()
    _past_deadline(monkeypatch)
    resp = client.get(PATH, headers=auth)
    assert resp.status_code == 504
    assert resp.json() == {"detail": DETAIL_TIMEOUT}


def _trello_down(monkeypatch) -> None:
    _board([CARD])
    _attachments([_upload("att-1", "a.jpg")])
    respx.get(f"{API_BASE}/1/cards/card-7/attachments/att-1/download/a.jpg").mock(
        return_value=Response(503)
    )


def _timed_out(monkeypatch) -> None:
    _happy_card()
    _past_deadline(monkeypatch)


@pytest.mark.parametrize(
    "arrange",
    [
        pytest.param(lambda monkeypatch: _board([]), id="not_found"),
        pytest.param(
            lambda monkeypatch: respx.get(f"{API_BASE}/1/members/me").mock(
                return_value=Response(401)
            ),
            id="no_access",
        ),
        pytest.param(_trello_down, id="trello_error"),
        pytest.param(_timed_out, id="timeout"),
        pytest.param(lambda monkeypatch: _happy_card(), id="ok"),
    ],
)
@respx.mock
def test_client_closed_on_every_path(client, auth, monkeypatch, arrange):
    closed: list[bool] = []
    original = TrelloClient.close

    def recording_close(self) -> None:
        closed.append(True)
        original(self)

    monkeypatch.setattr(TrelloClient, "close", recording_close)
    arrange(monkeypatch)
    client.get(PATH, headers=auth)
    assert closed == [True]


# --- telemetry (REQ-2.8) ---


@respx.mock
def test_download_logs_count_and_bytes(client, auth, caplog):
    _happy_card()
    with caplog.at_level(logging.INFO):
        client.get(PATH, headers=auth)
    (record,) = [r for r in caplog.records if "attachments_download" in r.getMessage()]
    assert "ref=2026/417 files=2 bytes=6" in record.getMessage()


@respx.mock
def test_download_sets_span_attributes(client, auth, otel_clean):
    _happy_card()
    client.get(PATH, headers=auth)
    spans = [s for s in otel_clean.spans.get_finished_spans() if "attachments" in s.name]
    assert spans, [s.name for s in otel_clean.spans.get_finished_spans()]
    assert spans[-1].attributes["attachments.count"] == 2
    assert spans[-1].attributes["attachments.bytes"] == 6
