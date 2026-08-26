"""Metrics endpoint (metrics-dashboard REQ-1; D7).

GET only — both CORS layers (app middleware + platform, D22) allow exactly
GET/POST. Plain `def` handler: the state store client is sync, FastAPI runs it
in the threadpool (state-store consumer constraint).
"""

from fastapi import APIRouter, Depends, Response
from pydantic import AwareDatetime, BaseModel

from app.security import require_operator
from core.state_store import ErrorRun, StateStore, get_state_store

router = APIRouter(prefix="/api/metrics", dependencies=[Depends(require_operator)])


class ClaimOut(BaseModel):
    """The ledger row minus `subject` (Gate 3 W2): the subject carries personal
    names and nothing on the dashboard renders it — don't ship unused PII."""

    at: AwareDatetime
    claim_ref: str
    type: str
    town: str | None = None
    owner: str | None = None
    card_url: str


class MetricsResponse(BaseModel):
    emails_processed: int  # Σ processed over heartbeat history (approximate — spec Overview)
    cards_created: int  # ledger row count: one row per card-created claim
    emails_failed: int  # Σ per-run failed — terminal `failed` labels (delta REQ-7)
    failed_runs: int  # runs that crashed outright (status=failed, no counts)
    error_runs: list[ErrorRun]  # failed>0 rows — the graph's error events
    claims: list[ClaimOut]  # newest first


@router.get("")
def metrics(response: Response, store: StateStore = Depends(get_state_store)) -> MetricsResponse:
    # no-store (REQ-1.4): the claims payload carries owner names.
    response.headers["Cache-Control"] = "no-store"
    claims = store.list_claims()
    totals = store.history_totals()
    return MetricsResponse(
        emails_processed=totals.emails_processed,
        cards_created=len(claims),
        emails_failed=totals.emails_failed,
        failed_runs=totals.failed_runs,
        error_runs=store.list_error_runs(),
        claims=[ClaimOut(**record.model_dump(exclude={"subject"})) for record in claims],
    )
