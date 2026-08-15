"""Worker control endpoints (worker-controls REQ-1/2/3; D4/D7).

GET/POST only — both CORS layers (app middleware + platform, D22) allow exactly
those verbs. Handlers are plain `def`: the state store client is sync, and
FastAPI runs sync handlers in its threadpool, off the event loop (state-store
consumer constraint).
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel, StrictBool

from app.security import require_operator
from app.state_store import Heartbeat, HeartbeatStatus, StateStore, get_state_store
from app.worker import run_worker
from pipeline.entry import run_pipeline

router = APIRouter(prefix="/api/worker", dependencies=[Depends(require_operator)])


class EnabledBody(BaseModel):
    enabled: StrictBool  # no truthy coercion: "true"/1 must not flip the worker (REQ-2.2)


class WorkerStatus(BaseModel):
    enabled: bool
    heartbeat: Heartbeat | None  # the stored row IS the API contract (spec decision)


class RunResult(BaseModel):
    outcome: HeartbeatStatus


@router.get("/status")
def worker_status(store: StateStore = Depends(get_state_store)) -> WorkerStatus:
    return WorkerStatus(enabled=store.read_enabled(), heartbeat=store.read_heartbeat())


@router.post("/enabled")
def set_worker_enabled(
    body: EnabledBody, store: StateStore = Depends(get_state_store)
) -> EnabledBody:
    """Explicit target state, idempotent — a retry or re-click cannot invert intent."""
    store.set_enabled(body.enabled)
    return body


@router.post("/run")
def run_worker_now(store: StateStore = Depends(get_state_store)) -> RunResult:
    """Process-now: the full wake path, not a bare pipeline call (contract settled
    2026-08-12) — honors the enabled gate and writes the end-of-run heartbeat.
    A pipeline failure propagates AFTER run_worker writes the `failed` heartbeat
    (→ 500; the status refresh carries the outcome, REQ-3.3)."""
    return RunResult(outcome=run_worker(store, run_pipeline))
