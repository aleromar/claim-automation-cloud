"""pipeline-wiring REQ-3/8: the Trello httpx client — preflight taxonomy,
request shapes, compensating delete, bounded 429 retry.

respx mocks the wire (house pattern); ids come from the TrelloConfig row
(D23/D25 — the Settings page edits them), secrets from the SecretStore.
"""

import httpx
import pytest
import respx
from httpx import Response

import pipeline.trello_client
from core.config import Settings
from core.secret_store import TRELLO_API_KEY, TRELLO_TOKEN, FileSecretStore
from core.state_store import TrelloConfig
from pipeline.trello_client import (
    ATTACHMENT_TIMEOUT_S,
    MISSING_CONFIG,
    MISSING_CREDENTIALS,
    REQUEST_TIMEOUT_S,
    TOKEN_REJECTED,
    Attachment,
    TrelloClient,
    TrelloNoAccessError,
)

API_BASE = "https://trello.test"
LIST_ID = "list-abc"
BOARD_ID = "board-xyz"
CONFIG = TrelloConfig(board_id=BOARD_ID, list_id=LIST_ID)


@pytest.fixture
def settings() -> Settings:
    return Settings(trello_api_base_url=API_BASE)


@pytest.fixture
def secrets(tmp_path) -> FileSecretStore:
    store = FileSecretStore(tmp_path / "secrets.json")
    store.set(TRELLO_API_KEY, "key-123")
    store.set(TRELLO_TOKEN, "token-456")
    return store


@pytest.fixture
def trello(settings, secrets) -> TrelloClient:
    client = TrelloClient(settings, secrets, CONFIG)
    yield client
    client.close()


@pytest.fixture(autouse=True)
def no_backoff(monkeypatch):
    monkeypatch.setattr(pipeline.trello_client, "RETRY_BACKOFF_S", 0.0)


def _me_ok():
    return respx.get(f"{API_BASE}/1/members/me").mock(return_value=Response(200, json={"id": "me"}))


# --- preflight (REQ-3) ---


@respx.mock
def test_preflight_happy_path_sends_header_auth(trello):
    # HEADER auth, never query params — httpx logs full URLs at INFO, so
    # param-auth would leak the key+token into App Insights (REQ-3; caught by
    # test_secrets_never_logged during implementation).
    route = _me_ok()
    trello.preflight()
    request = route.calls.last.request
    assert 'oauth_consumer_key="key-123"' in request.headers["Authorization"]
    assert 'oauth_token="token-456"' in request.headers["Authorization"]
    assert "key-123" not in str(request.url)
    assert "token-456" not in str(request.url)


def test_preflight_missing_config_raises_no_access(settings, secrets):
    client = TrelloClient(settings, secrets, None)
    with pytest.raises(TrelloNoAccessError) as exc:
        client.preflight()  # no respx: proves no HTTP happened
    assert exc.value.reason == MISSING_CONFIG


def test_preflight_empty_config_ids_raise_no_access(settings, secrets):
    client = TrelloClient(settings, secrets, TrelloConfig(board_id="", list_id=""))
    with pytest.raises(TrelloNoAccessError) as exc:
        client.preflight()
    assert exc.value.reason == MISSING_CONFIG


def test_preflight_missing_credentials_raise_no_access(settings, tmp_path):
    client = TrelloClient(settings, FileSecretStore(tmp_path / "s.json"), CONFIG)
    with pytest.raises(TrelloNoAccessError) as exc:
        client.preflight()
    assert exc.value.reason == MISSING_CREDENTIALS


@respx.mock
def test_preflight_401_raises_no_access(trello):
    respx.get(f"{API_BASE}/1/members/me").mock(return_value=Response(401, text="invalid token"))
    with pytest.raises(TrelloNoAccessError) as exc:
        trello.preflight()
    assert exc.value.reason == TOKEN_REJECTED


@respx.mock
def test_preflight_server_error_propagates(trello):
    # Transient Trello failure is a failed run, not "needs setup" (REQ-3).
    respx.get(f"{API_BASE}/1/members/me").mock(return_value=Response(503))
    with pytest.raises(httpx.HTTPStatusError):
        trello.preflight()


def test_constructor_does_no_io_and_no_secret_read(settings, tmp_path):
    # E3 stance: constructing ahead of the enabled gate must cost nothing.
    TrelloClient(settings, FileSecretStore(tmp_path / "s.json"), None)  # no respx, no raise


def test_no_access_error_is_the_core_wake_contract():
    from core.exceptions import NoAccessError

    assert issubclass(TrelloNoAccessError, NoAccessError)


# --- create_full_card: the atomic create+attach+comment unit (REQ-3) ---


def _card_routes():
    create = respx.post(f"{API_BASE}/1/cards").mock(
        return_value=Response(200, json={"id": "card-1", "shortUrl": "https://trello.com/c/c1"})
    )
    attach = respx.post(f"{API_BASE}/1/cards/card-1/attachments").mock(
        return_value=Response(200, json={"id": "att-1"})
    )
    comment = respx.post(f"{API_BASE}/1/cards/card-1/actions/comments").mock(
        return_value=Response(200, json={"id": "com-1"})
    )
    return create, attach, comment


@respx.mock
def test_create_full_card_happy_path(trello):
    create, attach, comment = _card_routes()
    _me_ok()
    trello.preflight()
    url = trello.create_full_card(
        name="MADRID 2026/417 Nombre Apellido",
        description="desc",
        pdf_bytes=b"%PDF-fake",
        pdf_filename="claim_417_2026.pdf",
        comment="desc",
    )
    assert url == "https://trello.com/c/c1"
    create_params = dict(httpx.QueryParams(create.calls.last.request.content.decode()))
    assert create_params["idList"] == LIST_ID
    assert create_params["name"] == "MADRID 2026/417 Nombre Apellido"
    assert b"claim_417_2026.pdf" in attach.calls.last.request.content
    assert comment.call_count == 1


@respx.mock
def test_create_full_card_deletes_card_when_attach_fails(trello):
    # Operator, 2026-08-21: create+attach+comment is ONE unit — "duplicated
    # comments are ok but lack of them is not". A partial card is deleted so
    # the manual retry recreates the full card.
    respx.post(f"{API_BASE}/1/cards").mock(
        return_value=Response(200, json={"id": "card-1", "shortUrl": "u"})
    )
    respx.post(f"{API_BASE}/1/cards/card-1/attachments").mock(return_value=Response(500))
    delete = respx.delete(f"{API_BASE}/1/cards/card-1").mock(return_value=Response(200, json={}))
    _me_ok()
    trello.preflight()
    with pytest.raises(httpx.HTTPStatusError):
        trello.create_full_card(
            name="n", description="d", pdf_bytes=b"x", pdf_filename="f.pdf", comment="c"
        )
    assert delete.call_count == 1


@respx.mock
def test_create_full_card_deletes_card_even_when_delete_fails_quietly(trello):
    # Best-effort delete: a failing delete must not mask the original error
    # (the narrowed REQ-4 residual covers the leftover card).
    respx.post(f"{API_BASE}/1/cards").mock(
        return_value=Response(200, json={"id": "card-1", "shortUrl": "u"})
    )
    respx.post(f"{API_BASE}/1/cards/card-1/actions/comments").mock(return_value=Response(500))
    respx.post(f"{API_BASE}/1/cards/card-1/attachments").mock(return_value=Response(200, json={}))
    respx.delete(f"{API_BASE}/1/cards/card-1").mock(return_value=Response(503))
    _me_ok()
    trello.preflight()
    with pytest.raises(httpx.HTTPStatusError) as exc:
        trello.create_full_card(
            name="n", description="d", pdf_bytes=b"x", pdf_filename="f.pdf", comment="c"
        )
    assert exc.value.response.status_code == 500  # the comment failure, not the delete's 503


# --- comunicación surface (REQ-8) ---


@respx.mock
def test_add_comment_posts_text(trello):
    route = respx.post(f"{API_BASE}/1/cards/card-9/actions/comments").mock(
        return_value=Response(200, json={"id": "com-1"})
    )
    _me_ok()
    trello.preflight()
    trello.add_comment("card-9", "observaciones text")
    params = dict(httpx.QueryParams(route.calls.last.request.content.decode()))
    assert params["text"] == "observaciones text"


@respx.mock
def test_find_card_searches_all_board_lists_then_archive(trello):
    # Laptop parity (main.py:172-186): every list on the board, then the archive.
    respx.get(f"{API_BASE}/1/boards/{BOARD_ID}/lists").mock(
        return_value=Response(200, json=[{"id": "l1"}, {"id": "l2"}])
    )
    respx.get(f"{API_BASE}/1/lists/l1/cards").mock(return_value=Response(200, json=[]))
    respx.get(f"{API_BASE}/1/lists/l2/cards").mock(
        return_value=Response(
            200, json=[{"id": "card-7", "name": "MADRID 2026/417 X", "shortUrl": "u7"}]
        )
    )
    _me_ok()
    trello.preflight()
    card = trello.find_card_by_claim_ref("2026/417")
    assert card is not None
    assert card["id"] == "card-7"


@respx.mock
def test_find_card_falls_back_to_archive(trello):
    respx.get(f"{API_BASE}/1/boards/{BOARD_ID}/lists").mock(
        return_value=Response(200, json=[{"id": "l1"}])
    )
    respx.get(f"{API_BASE}/1/lists/l1/cards").mock(return_value=Response(200, json=[]))
    respx.get(f"{API_BASE}/1/boards/{BOARD_ID}/cards").mock(
        return_value=Response(200, json=[{"id": "arch-1", "name": "2026/417 old", "shortUrl": "u"}])
    )
    _me_ok()
    trello.preflight()
    card = trello.find_card_by_claim_ref("2026/417")
    assert card is not None
    assert card["id"] == "arch-1"


@respx.mock
def test_find_card_none_when_absent_everywhere(trello):
    respx.get(f"{API_BASE}/1/boards/{BOARD_ID}/lists").mock(
        return_value=Response(200, json=[{"id": "l1"}])
    )
    respx.get(f"{API_BASE}/1/lists/l1/cards").mock(return_value=Response(200, json=[]))
    respx.get(f"{API_BASE}/1/boards/{BOARD_ID}/cards").mock(return_value=Response(200, json=[]))
    _me_ok()
    trello.preflight()
    assert trello.find_card_by_claim_ref("2026/999") is None


# --- attachments (attachment-download REQ-1) ---


@respx.mock
def test_list_attachments_maps_fields(trello):
    route = respx.get(f"{API_BASE}/1/cards/card-7/attachments").mock(
        return_value=Response(
            200,
            json=[
                {
                    "id": "att-1",
                    "name": "IMG_0001.jpg",
                    "bytes": 1234,
                    "mimeType": "image/jpeg",
                    "isUpload": True,
                },
                {"id": "att-2", "name": "https://example.test/doc", "isUpload": False},
            ],
        )
    )
    _me_ok()
    trello.preflight()
    attachments = trello.list_attachments("card-7")
    # Only what the route consumes is modelled (gate 3 I1): extra Trello
    # fields in the payload are ignored, not mapped.
    assert attachments == [
        Attachment(id="att-1", name="IMG_0001.jpg", is_upload=True),
        Attachment(id="att-2", name="https://example.test/doc", is_upload=False),
    ]
    assert route.calls.last.request.url.params["fields"] == "id,name,isUpload"


@respx.mock
def test_download_attachment_sends_header_auth_and_quotes_name(trello):
    # The name is a path segment Trello echoes back; `/` and spaces in a
    # filename must not become path structure.
    route = respx.get(
        f"{API_BASE}/1/cards/card-7/attachments/att-1/download/foto%20sal%C3%B3n%2F1.jpg"
    ).mock(return_value=Response(200, content=b"\xff\xd8jpeg"))
    _me_ok()
    trello.preflight()
    assert trello.download_attachment("card-7", "att-1", "foto salón/1.jpg") == b"\xff\xd8jpeg"
    request = route.calls.last.request
    assert 'oauth_token="token-456"' in request.headers["Authorization"]
    assert "token-456" not in str(request.url)


@respx.mock
def test_download_attachment_uses_long_timeout(trello):
    route = respx.get(f"{API_BASE}/1/cards/card-7/attachments/att-1/download/a.jpg").mock(
        return_value=Response(200, content=b"x")
    )
    _me_ok()
    trello.preflight()
    trello.download_attachment("card-7", "att-1", "a.jpg")
    assert route.calls.last.request.extensions["timeout"]["read"] == ATTACHMENT_TIMEOUT_S
    assert ATTACHMENT_TIMEOUT_S > REQUEST_TIMEOUT_S


@respx.mock
def test_download_attachment_follows_redirect_without_auth(trello):
    # Trello may 302 to its CDN. Following is required (raise_for_status is
    # silent on 3xx, so an unfollowed redirect would zip an empty body); the
    # OAuth header must NOT travel to the other origin.
    respx.get(f"{API_BASE}/1/cards/card-7/attachments/att-1/download/a.jpg").mock(
        return_value=Response(302, headers={"Location": "https://cdn.test/blob/a.jpg"})
    )
    cdn = respx.get("https://cdn.test/blob/a.jpg").mock(return_value=Response(200, content=b"img"))
    _me_ok()
    trello.preflight()
    assert trello.download_attachment("card-7", "att-1", "a.jpg") == b"img"
    assert "Authorization" not in cdn.calls.last.request.headers


@respx.mock
@pytest.mark.parametrize(
    "path", ["/1/cards/card-7/attachments", "/1/cards/card-7/attachments/att-1/download/a.jpg"]
)
def test_attachment_error_bodies_are_logged(trello, caplog, path):
    import logging

    respx.get(f"{API_BASE}{path}").mock(return_value=Response(404, text="attachment not found"))
    _me_ok()
    trello.preflight()
    with caplog.at_level(logging.WARNING), pytest.raises(httpx.HTTPStatusError):
        if path.endswith("/attachments"):
            trello.list_attachments("card-7")
        else:
            trello.download_attachment("card-7", "att-1", "a.jpg")
    assert any("attachment not found" in r.getMessage() for r in caplog.records)


def test_attachment_calls_before_preflight_are_programming_errors(trello):
    with pytest.raises(RuntimeError, match="preflight"):
        trello.list_attachments("card-7")
    with pytest.raises(RuntimeError, match="preflight"):
        trello.download_attachment("card-7", "att-1", "a.jpg")


# --- bounded 429 retry (REQ-3) ---


@respx.mock
def test_429_is_retried_then_succeeds(trello):
    respx.get(f"{API_BASE}/1/members/me").mock(
        side_effect=[Response(429), Response(200, json={"id": "me"})]
    )
    trello.preflight()  # no raise


@respx.mock
def test_429_exhausts_after_two_retries(trello):
    route = respx.get(f"{API_BASE}/1/members/me").mock(return_value=Response(429))
    with pytest.raises(httpx.HTTPStatusError):
        trello.preflight()
    assert route.call_count == 3  # initial + 2 retries


# --- hygiene ---


@respx.mock
def test_api_calls_before_preflight_are_programming_errors(trello):
    with pytest.raises(RuntimeError, match="preflight"):
        trello.add_comment("c", "t")
    with pytest.raises(RuntimeError, match="preflight"):
        trello.find_card_by_claim_ref("2026/417")


@respx.mock
def test_secrets_never_logged(trello, caplog):
    import logging

    _me_ok()
    with caplog.at_level(logging.DEBUG):
        trello.preflight()
    for record in caplog.records:
        assert "key-123" not in record.getMessage()
        assert "token-456" not in record.getMessage()


def test_real_client_satisfies_the_pipeline_protocol(trello):
    # Same M8-style structural proof as the Gmail side: process_mailbox tests
    # use fakes, so a rename must not ship green.
    from pipeline.entry import TrelloPipeline

    assert isinstance(trello, TrelloPipeline)


@respx.mock
def test_find_card_requires_a_ref_boundary(trello):
    # Gate 3 M4 (deviation from the laptop's bare substring): "2026/41" must
    # NOT match card "2026/417" — a comunicación comment on the wrong card is
    # worse than a miss.
    respx.get(f"{API_BASE}/1/boards/{BOARD_ID}/lists").mock(
        return_value=Response(200, json=[{"id": "l1"}])
    )
    respx.get(f"{API_BASE}/1/lists/l1/cards").mock(
        return_value=Response(
            200, json=[{"id": "card-417", "name": "MADRID 2026/417 X", "shortUrl": "u"}]
        )
    )
    respx.get(f"{API_BASE}/1/boards/{BOARD_ID}/cards").mock(return_value=Response(200, json=[]))
    _me_ok()
    trello.preflight()
    assert trello.find_card_by_claim_ref("2026/41") is None
    card = trello.find_card_by_claim_ref("2026/417")
    assert card is not None and card["id"] == "card-417"


# --- diagnosability: error bodies + compensation causes in the log ---


@respx.mock
def test_api_error_response_body_is_logged(trello, caplog):
    # httpx's HTTPStatusError message drops the body — the part that says WHY
    # Trello refused (the email gets a terminal failed label; no reproducing).
    import logging

    respx.post(f"{API_BASE}/1/cards").mock(
        return_value=Response(400, text="invalid value for desc")
    )
    _me_ok()
    trello.preflight()
    with caplog.at_level(logging.WARNING), pytest.raises(httpx.HTTPStatusError):
        trello.create_full_card(
            name="n", description="d", pdf_bytes=b"x", pdf_filename="f.pdf", comment="c"
        )
    assert any("invalid value for desc" in r.getMessage() for r in caplog.records)


@respx.mock
def test_failed_compensating_delete_logs_the_cause(trello, caplog):
    # The leftover partial card is a real board artifact — its log line must
    # say why the cleanup failed, not just that it did.
    import logging

    respx.post(f"{API_BASE}/1/cards").mock(
        return_value=Response(200, json={"id": "card-1", "shortUrl": "u"})
    )
    respx.post(f"{API_BASE}/1/cards/card-1/attachments").mock(return_value=Response(200, json={}))
    respx.post(f"{API_BASE}/1/cards/card-1/actions/comments").mock(return_value=Response(500))
    respx.delete(f"{API_BASE}/1/cards/card-1").mock(return_value=Response(503))
    _me_ok()
    trello.preflight()
    with caplog.at_level(logging.WARNING), pytest.raises(httpx.HTTPStatusError):
        trello.create_full_card(
            name="n", description="d", pdf_bytes=b"x", pdf_filename="f.pdf", comment="c"
        )
    (record,) = [r for r in caplog.records if "compensating delete" in r.getMessage()]
    assert record.exc_info is not None
