"""Azure Functions entry point (REQ-1.2).

The Functions runtime discovers `app` here and plays the role uvicorn plays
locally: it feeds every HTTP request into the ASGI (FastAPI) app. One catch-all
route; FastAPI does all routing. ANONYMOUS disables Azure's function-key gate so
our own auth (JWT, per tech.md D17/D22) is the single, deliberate gate.
"""

import azure.functions as func

from app.main import app as fastapi_app

app = func.AsgiFunctionApp(app=fastapi_app, http_auth_level=func.AuthLevel.ANONYMOUS)
