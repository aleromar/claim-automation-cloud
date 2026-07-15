"""FastAPI application for Claim Automation (Cloud).

Plain FastAPI app — identical in local dev (uvicorn) and in Azure (via the
AsgiFunctionApp adapter in function_app.py). No Functions-specific code here.
"""

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.auth_routes import router as auth_router
from app.config import get_settings
from app.secret_store import create_secret_store
from app.security import require_operator


@asynccontextmanager
async def lifespan(_: FastAPI):
    """Fail fast if the session signing key is absent (REQ-5.4).

    Never auto-generate it: Consumption scale-out would mint divergent keys.
    """
    store = create_secret_store(get_settings())
    if store.get("session-signing-key") is None:
        raise RuntimeError(
            "session-signing-key missing from the secret store — seed it before starting "
            "(dev: see README; prod: infra pipeline)"
        )
    yield


app = FastAPI(title="claim-automation-cloud", lifespan=lifespan)
app.include_router(auth_router)

# Prod only: SWA and Function App are different origins (D22). Unset in dev
# (Vite proxy is same-origin), so this is decided once at process start.
_cors_origin = get_settings().cors_allowed_origin
if _cors_origin:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[_cors_origin],
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization"],
    )


@app.get("/api/health")
async def health() -> dict[str, str]:
    """Liveness probe (REQ-1.1). No dependency checks — see spec S11."""
    return {"status": "ok"}


@app.get("/api/me")
async def me(email: str = Depends(require_operator)) -> dict[str, str]:
    """Authenticated-state probe (REQ-3.4); future endpoints reuse the guard."""
    return {"email": email}
