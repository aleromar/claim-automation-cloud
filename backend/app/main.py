"""FastAPI application for Claim Automation (Cloud).

Plain FastAPI app — identical in local dev (uvicorn) and in Azure (via the
AsgiFunctionApp adapter in function_app.py). No Functions-specific code here.
"""

from fastapi import FastAPI

app = FastAPI(title="claim-automation-cloud")


@app.get("/api/health")
async def health() -> dict[str, str]:
    """Liveness probe (REQ-1.1). No dependency checks — see spec S11."""
    return {"status": "ok"}
