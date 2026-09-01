"""Azure Functions entry point (REQ-1.2).

The Functions runtime discovers `app` here and plays the role uvicorn plays
locally: it feeds every HTTP request into the ASGI (FastAPI) app. One catch-all
route; FastAPI does all routing. ANONYMOUS disables Azure's function-key gate so
our own auth (JWT, per tech.md D17/D22) is the single, deliberate gate.
"""

import azure.functions as func

from app.main import app as fastapi_app
from app.observability import instrument_fastapi, setup_telemetry
from app.worker import WORKER_FUNCTION_NAME, WORKER_TIMER_SCHEDULE, run_scheduled_worker

# Telemetry lives in this Functions-only entry point: app/main.py stays free of
# Functions-specific wiring (structure.md), and instrumentation must attach
# before the host serves the first request (otel-observability REQ-1). Under
# uvicorn neither call has any effect (no connection string → inert).
setup_telemetry()
instrument_fastapi(fastapi_app)

app = func.AsgiFunctionApp(app=fastapi_app, http_auth_level=func.AuthLevel.ANONYMOUS)


@app.function_name(WORKER_FUNCTION_NAME)
@app.timer_trigger(arg_name="timer", schedule=WORKER_TIMER_SCHEDULE)
def worker(timer: func.TimerRequest) -> None:
    """Scheduled worker (worker-skeleton spec; D4/D5): the timer always fires;
    the enabled gate + heartbeat live in app/worker.py."""
    run_scheduled_worker()
