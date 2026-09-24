"""Claim attachment download (attachment-download REQ-2; D30).

One GET returns every uploaded file on the claim's Trello card as a zip built
in memory — Trello serves attachment bytes only with the OAuth header, so the
browser can never fetch them itself, and Consumption instances have no disk
worth writing to. GET only (both CORS layers allow GET/POST, D22). Plain `def`:
sync httpx client, sync state store, FastAPI threadpool.
"""

import logging
from io import BytesIO
from pathlib import PurePosixPath
from time import monotonic
from typing import Final
from zipfile import ZIP_STORED, ZipFile

import httpx
from fastapi import APIRouter, Depends, HTTPException, Path, Response
from opentelemetry import trace

from app.security import require_operator
from core.config import Settings, get_settings
from core.secret_store import SecretStore, get_store
from core.state_store import StateStore, get_state_store
from pipeline.claim_data import build_pdf_filename
from pipeline.trello_client import Attachment, TrelloClient, TrelloNoAccessError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/claims", dependencies=[Depends(require_operator)])

# ASCII classes, not `\d`: Python's `\d` admits Unicode digits, which then
# crash the latin-1 Content-Disposition header into a 500 outside CORS; the
# frontend's JS `\d` is ASCII-only, so the two sides must agree on [0-9].
YEAR_PATTERN: Final = r"^[0-9]{4}$"
NUMBER_PATTERN: Final = r"^[0-9]+$"
# Checked between files only. If every read of one file times out (3 × 30 s +
# 429 back-off) the request ends ≈ 184 s, under Azure's 230 s HTTP cutoff; a
# trickling file is unbounded by us and the load balancer ends it instead.
DOWNLOAD_DEADLINE_S: Final = 90.0
ZIP_MEDIA_TYPE: Final = "application/zip"
# Closed set of stable details — the frontend maps each to fixed Spanish copy.
DETAIL_CARD_NOT_FOUND: Final = "card_not_found"
DETAIL_NO_ATTACHMENTS: Final = "no_attachments"
DETAIL_TRELLO_NO_ACCESS: Final = "trello_no_access"
DETAIL_TRELLO_ERROR: Final = "trello_error"
DETAIL_TIMEOUT: Final = "timeout"


def _entry_names(attachments: list[Attachment]) -> list[str]:
    """Zip entry names: basename only (ZipFile.writestr stores `../evil.jpg`
    verbatim), the attachment id when the name is empty, and `stem (n).ext` on
    collision — a zip with duplicate entries is malformed."""
    seen: dict[str, int] = {}
    names: list[str] = []
    for attachment in attachments:
        base = PurePosixPath(attachment.name).name or attachment.id
        count = seen.get(base, 0) + 1
        seen[base] = count
        if count == 1:
            names.append(base)
        else:
            path = PurePosixPath(base)
            names.append(f"{path.stem} ({count}){path.suffix}")
    return names


@router.get("/{year}/{number}/attachments")
def download_claim_attachments(
    year: str = Path(pattern=YEAR_PATTERN),
    number: str = Path(pattern=NUMBER_PATTERN),
    settings: Settings = Depends(get_settings),
    secrets: SecretStore = Depends(get_store),
    store: StateStore = Depends(get_state_store),
) -> Response:
    claim_ref = f"{year}/{number}"
    started = monotonic()
    deadline = started + DOWNLOAD_DEADLINE_S
    trello = TrelloClient(settings, secrets, store.read_trello_config())
    buffer = BytesIO()
    total_bytes = 0
    try:
        try:
            try:
                trello.preflight()
            except TrelloNoAccessError as exc:
                raise HTTPException(status_code=503, detail=DETAIL_TRELLO_NO_ACCESS) from exc
            card = trello.find_card_by_claim_ref(claim_ref)
            if card is None:
                raise HTTPException(status_code=404, detail=DETAIL_CARD_NOT_FOUND)
            # Uploads only (links have no bytes), minus the pipeline's own
            # letterhead PDF — the operator wants the photos (grill Q1/Q1b).
            pipeline_pdf = build_pdf_filename(year, number)
            attachments = [
                attachment
                for attachment in trello.list_attachments(card["id"])
                if attachment.is_upload and attachment.name != pipeline_pdf
            ]
            if not attachments:
                raise HTTPException(status_code=404, detail=DETAIL_NO_ATTACHMENTS)
            with ZipFile(buffer, "w", ZIP_STORED) as archive:
                for entry_name, attachment in zip(_entry_names(attachments), attachments):
                    if monotonic() > deadline:
                        raise HTTPException(status_code=504, detail=DETAIL_TIMEOUT)
                    content = trello.download_attachment(card["id"], attachment.id, attachment.name)
                    total_bytes += len(content)
                    archive.writestr(entry_name, content)
        except httpx.HTTPError as exc:
            # An HTTPException, never an unhandled raise: those bypass
            # CORSMiddleware and the cross-origin SPA could not read the status.
            logger.exception("attachments_download ref=%s trello call failed", claim_ref)
            raise HTTPException(status_code=502, detail=DETAIL_TRELLO_ERROR) from exc
    finally:
        trello.close()
    span = trace.get_current_span()
    span.set_attribute("attachments.count", len(attachments))
    span.set_attribute("attachments.bytes", total_bytes)
    logger.info(
        "attachments_download ref=%s files=%d bytes=%d ms=%d",
        claim_ref,
        len(attachments),
        total_bytes,
        int((monotonic() - started) * 1000),
    )
    return Response(
        buffer.getvalue(),
        media_type=ZIP_MEDIA_TYPE,
        headers={"Content-Disposition": f'attachment; filename="{year}_{number}.zip"'},
    )
