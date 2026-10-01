"""Pipeline entry point — the real claim pipeline (pipeline-wiring, 5c).

Replaces the 5b read-only probe. `run_pipeline()` is the zero-arg wake
contract: it acquires the run lease, composes the pipeline-owned I/O (Gmail,
Trello, membretes, stores — REQ-6 2nd amendment), runs both preflights, and
delegates to `process_mailbox`. Per-email order: parse+classify → ledger dedup
→ PDF → card+attach+comment (or comunicación comment) → ledger row → relabel.

Durability invariant (operator, 2026-08-21): UNREAD is the checkpoint. An
email loses UNREAD only as its final step, so any crash/failure re-attempts it
next wake; every redo path is idempotent or compensated. Nothing may reorder
the relabel earlier.
"""

import logging
import re
from datetime import UTC, datetime
from email.utils import parseaddr
from time import monotonic
from typing import Final, Literal, Protocol, runtime_checkable

from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient
from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode

from core.config import Settings, get_settings
from core.secret_store import get_store
from core.state_store import ClaimRecord, RunCounts, get_state_store
from pipeline.claim_data import (
    CLAIM_SUBJECT_MARKERS,
    ClaimData,
    ClaimType,
    build_card_comment,
    build_card_description,
    build_card_name,
    build_pdf_filename,
)
from pipeline.extraction import get_field_extractor
from pipeline.gmail_client import GmailClient
from pipeline.membrete_source import BlobMembreteSource, MembreteSource
from pipeline.pdf_gen import generate_pdf_from_email
from pipeline.trello_client import TrelloClient

logger = logging.getLogger(__name__)
# opentelemetry-api only (layering: pipeline never imports app): a no-op
# ProxyTracer unless app/observability installed a provider (otel REQ-3).
_tracer = trace.get_tracer("pipeline.entry")

# Wall-clock cap (gate E1, inherited from the 5b probe): a degraded backend
# must fail/stop the run while the heartbeat can still land, and process-now
# must stay inside Azure's ~230 s HTTP idle limit.
RUN_DEADLINE_S: Final = 120.0
# Laptop label names verbatim (REQ-6) — the mailbox already carries them.
LABEL_PROCESADO: Final = "procesado"
LABEL_FAILED: Final = "failed"
UNREAD_LABEL_ID: Final = "UNREAD"  # Gmail system label id
# Same queryable prefix app.worker uses; duplicated literal because pipeline/
# must not import app (App Insights: traces | where message startswith this).
_PIPELINE_LOG_PREFIX: Final = "worker_run pipeline"
# What _process_one did with an email — the per-email log line's closed set.
# dedup_skip and comment matter most: neither creates a card, and comunicación
# writes no ledger row, so the log line is the only durable record.
ACTION_CARD: Final = "card"
ACTION_COMMENT: Final = "comment"
ACTION_DEDUP_SKIP: Final = "dedup_skip"
ProcessedAction = Literal["card", "comment", "dedup_skip"]
# mailbox-trust-boundary REQ-2: the two rejection reasons, matched by tests and
# by KQL on the email_failed line (`message has "sender_not_allowed"`).
REASON_SENDER_NOT_ALLOWED: Final = "sender_not_allowed"
REASON_NO_SENDER: Final = "no_sender"


def parse_sender_allowlist(raw: str) -> frozenset[str]:
    """CLAIM_SENDER_ALLOWLIST → lowercased domains (REQ-1). Empty, address-shaped
    or space-containing entries are a configuration error: under exact matching
    an address entry would silently reject every email, so it fails the RUN at
    composition like a missing FOUNDRY_ENDPOINT."""
    entries = frozenset(entry.strip().lower() for entry in raw.split(",") if entry.strip())
    if not entries:
        raise ValueError(
            "CLAIM_SENDER_ALLOWLIST is not configured — the pipeline admits no sender without it "
            "(set by the infra deployment as a Function App setting)"
        )
    # Count, never the values: the list is private and this message reaches
    # the heartbeat and the nightly digest.
    bad = sum(1 for entry in entries if "@" in entry or any(ch.isspace() for ch in entry))
    if bad:
        raise ValueError(
            f"CLAIM_SENDER_ALLOWLIST has {bad} entr{'y' if bad == 1 else 'ies'} that are not bare "
            "domains (exact match on the part after '@' of the From address; no addresses, no spaces)"
        )
    return entries


def sender_domain(message: dict) -> str | None:
    """Lowercased text after the last '@' of the parsed From address; None when
    the header is missing or parseaddr yields no '@' (malformed, several
    addresses, display-name spoofs — all fail closed, REQ-2.4)."""
    headers = message.get("payload", {}).get("headers", [])
    raw = next((h["value"] for h in headers if h["name"] == "From"), None)
    if raw is None:
        return None
    _, address = parseaddr(raw)
    if "@" not in address:
        return None
    return address.rpartition("@")[2].lower()


def build_claim_query() -> str:
    """The server-side Gmail filter, built from the single-source markers
    (REQ-1): quoted phrases match anywhere in the subject; new claim types
    extend the query automatically."""
    return " OR ".join(f'subject:"{marker}"' for marker in CLAIM_SUBJECT_MARKERS)


@runtime_checkable
class GmailPipeline(Protocol):
    """What process_mailbox needs from Gmail — the test seam, and the M8-style
    structural proof that the real client satisfies it."""

    def list_unread_message_ids(self, query: str | None = None) -> list[str]: ...

    def get_message(self, message_id: str) -> dict: ...

    def modify_labels(
        self, message_id: str, add_label_ids: list[str], remove_label_ids: list[str]
    ) -> None: ...

    def get_or_create_label_id(self, name: str) -> str: ...

    def count_messages_with_label(self, label_id: str) -> int: ...


@runtime_checkable
class TrelloPipeline(Protocol):
    def create_full_card(
        self, *, name: str, description: str, pdf_bytes: bytes, pdf_filename: str, comment: str
    ) -> str: ...

    def add_comment(self, card_id: str, text: str) -> None: ...

    def find_card_by_claim_ref(self, claim_ref: str) -> dict | None: ...


class ClaimLedger(Protocol):
    def get_claim(self, claim_ref: str) -> ClaimRecord | None: ...

    def record_claim(self, record: ClaimRecord) -> None: ...


def process_mailbox(
    gmail: GmailPipeline,
    trello: TrelloPipeline,
    membretes: MembreteSource,
    history: ClaimLedger,
    deadline: float,
    extractor=None,
    *,
    allowed_domains: frozenset[str],
) -> RunCounts:
    """One run over the filtered UNREAD page, chronologically (REQ-1/2).
    `allowed_domains` is keyword-only with no default: a caller that forgets it
    fails instead of admitting every sender (mailbox-trust-boundary REQ-2)."""
    procesado_id = gmail.get_or_create_label_id(LABEL_PROCESADO)
    failed_id = gmail.get_or_create_label_id(LABEL_FAILED)
    with _tracer.start_as_current_span("pipeline.fetch"):
        message_ids = gmail.list_unread_message_ids(query=build_claim_query())
        # The fetch phase honors the deadline too (Gate 3 M5): 100 slow fetches
        # must not eat the functionTimeout — unfetched messages stay UNREAD.
        messages: list[dict] = []
        for message_id in message_ids:
            if monotonic() > deadline:
                logger.warning(
                    "%s deadline reached during fetch (%d of %d) — the rest stay UNREAD",
                    _PIPELINE_LOG_PREFIX,
                    len(messages),
                    len(message_ids),
                )
                break
            messages.append(gmail.get_message(message_id))
    # Gmail documents no list order: internalDate ascending IS the guarantee.
    messages.sort(key=lambda message: int(message["internalDate"]))
    processed = failed = 0
    for position, message in enumerate(messages, start=1):
        # Between emails only: a started email always completes its sequence
        # (REQ-2) — leftovers stay UNREAD for the next wake.
        if monotonic() > deadline:
            logger.warning(
                "%s deadline reached at message %d of %d — the rest stay UNREAD",
                _PIPELINE_LOG_PREFIX,
                position,
                len(messages),
            )
            break
        with _tracer.start_as_current_span("pipeline.email") as email_span:
            try:
                action = _process_one(
                    message, trello, membretes, history, extractor, allowed_domains=allowed_domains
                )
            except Exception as exc:
                # Per-email boundary (REQ-2, deviation from the laptop's
                # batch-abort): terminal `failed` label = the operator work queue.
                # exc_info: the email leaves UNREAD forever — this record must say
                # WHERE in the sequence it died, not just str(exc).
                email_span.record_exception(exc)
                email_span.set_status(Status(StatusCode.ERROR))
                logger.warning(
                    "%s email_failed ref=%s id=%s reason=%s",
                    _PIPELINE_LOG_PREFIX,
                    _claim_ref_of(message),
                    message["id"],
                    exc,
                    exc_info=exc,
                )
                gmail.modify_labels(message["id"], [failed_id], [UNREAD_LABEL_ID])
                failed += 1
                continue
            # OUTSIDE the boundary (Gate 3 M2): a transient relabel failure on a
            # fully-processed email must fail the RUN (email stays UNREAD, next
            # wake dedup-skips and relabels) — never burn a terminal failed label.
            gmail.modify_labels(message["id"], [procesado_id], [UNREAD_LABEL_ID])
            processed += 1
            email_span.set_attribute("email.action", action)
            logger.info(
                "%s email_processed ref=%s id=%s action=%s",
                _PIPELINE_LOG_PREFIX,
                _claim_ref_of(message),
                message["id"],
                action,
            )
    try:
        # After processing, so this run's failures appear in the gauge (REQ-5).
        failed_total = gmail.count_messages_with_label(failed_id)
    except Exception:
        # A gauge blip must not fail a run whose mutations completed.
        logger.warning("%s failed_total gauge unavailable", _PIPELINE_LOG_PREFIX, exc_info=True)
        failed_total = None
    # matched = listed ids: a silent 100-cap truncation (saturation backlog
    # item) shows up as matched pinned at the cap while UNREAD keeps growing.
    logger.info(
        "%s matched=%d processed=%d failed=%d failed_total=%s",
        _PIPELINE_LOG_PREFIX,
        len(message_ids),
        processed,
        failed,
        failed_total,
    )
    return RunCounts(processed=processed, failed=failed, failed_total=failed_total)


def _claim_ref_of(message: dict) -> str:
    """Best-effort claim ref for the failure log (REQ-2) — the boundary can't
    rely on a parsed ClaimData."""
    match = re.search(r"\d{4}/\d+", ClaimData.extract_subject(message) or "")
    return match.group(0) if match else "unparsed"


def _process_one(
    message: dict,
    trello: TrelloPipeline,
    membretes: MembreteSource,
    history: ClaimLedger,
    extractor,
    *,
    allowed_domains: frozenset[str],
) -> ProcessedAction:
    # mailbox-trust-boundary REQ-2: origin before content. Nothing about the
    # email is parsed — no extractor, no model call — until its sender domain
    # is trusted; the boundary's `reason=` is str(exc), domain only.
    domain = sender_domain(message)
    if domain is None:
        raise ValueError(REASON_NO_SENDER)
    if domain not in allowed_domains:
        raise ValueError(f"{REASON_SENDER_NOT_ALLOWED} domain={domain}")
    with _tracer.start_as_current_span("pipeline.parse_classify"):
        subject = ClaimData.extract_subject(message) or ""
        if not any(marker in subject for marker in CLAIM_SUBJECT_MARKERS):
            # Gmail's phrase match said yes, the in-code source of truth says no —
            # an anomaly to investigate, never left UNREAD to reappear (REQ-1).
            raise ValueError("server filter matched but no claim marker in the subject")
        claim = ClaimData.from_msg_data(message, extractor)
        if claim is None:
            raise ValueError("claim-marked subject without a parseable YYYY/N reference")
        claim_ref = f"{claim.year}/{claim.claim_number}"
    if claim.type is ClaimType.COMUNICACION_A_COLABORADOR:
        # Live search on purpose (REQ-8): an archived card must fail the email;
        # the ledger is not consulted and no row is written (RowKey collision).
        card = trello.find_card_by_claim_ref(claim_ref)
        if card is None:
            raise ValueError(f"comunicación {claim_ref} has no existing card")
        trello.add_comment(card["id"], build_card_comment(claim))
        return ACTION_COMMENT
    if history.get_claim(claim_ref) is not None:
        # Idempotent re-delivery / forward of a processed claim (D12c): skip
        # Trello entirely; the caller relabels and counts it processed.
        return ACTION_DEDUP_SKIP
    with _tracer.start_as_current_span("pipeline.render_pdf"):
        pdf = generate_pdf_from_email(claim.email_body, claim.type, membretes)
    with _tracer.start_as_current_span("pipeline.create_card"):
        card_url = trello.create_full_card(
            name=build_card_name(claim),
            description=build_card_description(claim),
            pdf_bytes=pdf,
            pdf_filename=build_pdf_filename(claim.year, claim.claim_number),
            comment=build_card_comment(claim),
        )
    # Ledger BEFORE relabel (REQ-6 deviation): a crash between them leaves the
    # email UNREAD with the row present — next wake dedup-skips and relabels.
    history.record_claim(
        ClaimRecord(
            at=datetime.now(UTC),
            claim_ref=claim_ref,
            subject=claim.subject,
            type=claim.type.name,
            town=claim.town,
            owner=claim.owner_name,
            card_url=card_url,
            extractor_used=claim.extractor_used,
        )
    )
    return ACTION_CARD


def _build_membrete_source(settings: Settings) -> BlobMembreteSource:
    """Pipeline-owned composition (D26). Endpoint enforcement (REQ-7): under
    managed identity a missing blob endpoint fails the RUN loudly — not the
    whole app at startup, so routes stay alive on a misdeployed config."""
    if settings.table_storage_backend == "managed_identity":
        if not settings.blob_endpoint:
            raise RuntimeError("blob_endpoint must be configured under managed_identity (REQ-7)")
        service = BlobServiceClient(
            account_url=settings.blob_endpoint, credential=DefaultAzureCredential()
        )
        return BlobMembreteSource(service, settings.membretes_container)
    return BlobMembreteSource.from_connection_string(
        settings.storage_connection_string, settings.membretes_container
    )


def run_pipeline() -> RunCounts:
    """The zero-arg wake contract: lease → compose → preflights → process.
    A held lease raises RunBusyError (scheduler classifies `skipped_busy`);
    everything composed here — clients and the field extractor alike — is
    per-run (P12 by non-sharing; the lease confines a run to one thread) and
    closed on every exit."""
    settings = get_settings()
    store = get_state_store()
    with store.run_lease():
        secrets = get_store()
        # First: the composition steps that can fail on config alone (llm
        # without an endpoint, REQ-1.2; an empty sender allowlist,
        # mailbox-trust-boundary REQ-1.3) — fail before opening clients.
        extractor = get_field_extractor(settings.field_extractor_backend, settings)
        allowed_domains = parse_sender_allowlist(settings.claim_sender_allowlist)
        gmail = GmailClient(settings, secrets)
        trello = TrelloClient(settings, secrets, store.read_trello_config())
        try:
            gmail.preflight()
            trello.preflight()
            return process_mailbox(
                gmail,
                trello,
                _build_membrete_source(settings),
                store,
                deadline=monotonic() + RUN_DEADLINE_S,
                extractor=extractor,
                allowed_domains=allowed_domains,
            )
        finally:
            try:
                gmail.close()
            finally:
                try:
                    trello.close()
                finally:
                    extractor.close()
