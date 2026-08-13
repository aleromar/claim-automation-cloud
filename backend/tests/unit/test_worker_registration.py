"""Timer registration on the existing Function App (worker-skeleton REQ-1).

The timer must join the app object beside the ASGI catch-all — displacing the HTTP
path would be a deployment Bugfix-#3/#5-class regression.
"""

import json

import function_app
from app.worker import WORKER_FUNCTION_NAME, WORKER_TIMER_SCHEDULE

# Introspect once: get_functions() is not idempotent (the register validates
# name uniqueness against state that persists across calls).
FUNCTIONS = {f.get_function_name(): f for f in function_app.app.get_functions()}


def test_timer_function_registered():
    assert WORKER_FUNCTION_NAME in FUNCTIONS
    trigger = FUNCTIONS[WORKER_FUNCTION_NAME].get_trigger()
    assert trigger is not None
    assert trigger.type == "timerTrigger"


def test_timer_schedule_is_every_30_minutes():
    assert WORKER_TIMER_SCHEDULE == "0 */30 * * * *"  # NCRONTAB: second 0, every 30th minute
    raw = json.loads(FUNCTIONS[WORKER_FUNCTION_NAME].get_raw_bindings()[0])
    assert raw["schedule"] == WORKER_TIMER_SCHEDULE


def test_http_catch_all_still_registered():
    assert "http_app_func" in FUNCTIONS  # the AsgiFunctionApp catch-all (REQ-1.3)
    assert len(FUNCTIONS) == 2
