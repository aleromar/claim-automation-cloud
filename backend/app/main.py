"""FastAPI application for Claim Automation (Cloud).

Plain FastAPI app — identical in local dev (uvicorn) and in Azure (via the
AsgiFunctionApp adapter in function_app.py). No Functions-specific code here.
"""

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.auth_routes import router as auth_router
from app.config import get_settings
from app.secret_store import (
    GOOGLE_CLIENT_SECRET,
    SESSION_SIGNING_KEY,
    create_secret_store,
    require_secret,
)
from app.security import require_operator
from app.settings_routes import router as settings_router
from app.version import get_build_version
from app.worker_routes import router as worker_router


@asynccontextmanager
async def lifespan(_: FastAPI):
    """Fail fast if required auth secrets/config are absent (REQ-5.4).

    Never auto-generate the signing key: Consumption scale-out would mint
    divergent keys.
    """
    settings = get_settings()
    store = create_secret_store(settings)
    require_secret(store, SESSION_SIGNING_KEY)
    require_secret(store, GOOGLE_CLIENT_SECRET)
    if not settings.operator_email:
        raise RuntimeError(
            "OPERATOR_EMAIL is not configured — set it in the environment (dev: .env "
            "loaded via `make dev`, see backend/README; prod: app settings)"
        )
    yield


app = FastAPI(title="claim-automation-cloud", lifespan=lifespan)
app.include_router(auth_router)
app.include_router(worker_router)
app.include_router(settings_router)


@app.exception_handler(RequestValidationError)
async def validation_error_without_input(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """422 bodies must not echo submitted values (settings REQ-2.8; P5) —
    FastAPI's default includes an `input` field, which for the settings form
    could carry a live credential (`ctx` can embed input-derived context too).
    Allowlist, not denylist: a dependency upgrade that adds a new
    input-derived key must fail closed. Location + message keep the error
    actionable."""
    errors = [
        {key: error[key] for key in ("loc", "msg", "type") if key in error}
        for error in exc.errors()
    ]
    return JSONResponse(status_code=422, content={"detail": errors})


# Prod only: SWA and Function App are different origins (D22). Unset in dev
# (Vite proxy is same-origin), so this is decided once at process start.
# Not sufficient alone in prod: the Functions host answers OPTIONS preflights
# from the platform CORS allowlist (see D22 amendment / deployment Bugfix #6).
_cors_origin = get_settings().cors_allowed_origin
if _cors_origin:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[_cors_origin],
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "Content-Type"],
    )


@app.get("/api/health")
async def health() -> dict[str, str]:
    """Liveness probe (REQ-1.1). No dependency checks — see spec S11
    (amended by version-display REQ-1: body carries the build version)."""
    return {"status": "ok", "version": get_build_version()}


@app.get("/api/me")
async def me(email: str = Depends(require_operator)) -> dict[str, str]:
    """Authenticated-state probe (REQ-3.4); future endpoints reuse the guard."""
    return {"email": email}
