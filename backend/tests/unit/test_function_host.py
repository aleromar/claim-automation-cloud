"""The Functions host only indexes a package that ships host.json (deployment NFR).

Local dev and e2e run uvicorn directly, so nothing else exercises this file —
without it the deployed app registers zero functions and every route 404s.
"""

import json
from pathlib import Path

HOST_JSON = Path(__file__).parents[2] / "host.json"


def test_host_json_is_shipped() -> None:
    assert HOST_JSON.is_file(), "backend/host.json missing — host cannot index the app"


def test_host_json_declares_v2_and_adaptive_sampling() -> None:
    config = json.loads(HOST_JSON.read_text())
    assert config["version"] == "2.0"
    sampling = config["logging"]["applicationInsights"]["samplingSettings"]
    assert sampling["isEnabled"] is True
    # Traces/exceptions stay unsampled: at ~50 runs/day the volume ceiling
    # sampling protects against doesn't exist, and a failure burst is exactly
    # when every line matters. Dependencies remain sampled (the noisy type).
    excluded = set(sampling["excludedTypes"].split(";"))
    assert {"Request", "Exception", "Trace"} <= excluded


def test_host_json_empties_route_prefix() -> None:
    """AsgiFunctionApp registers '/{*route}'; any non-empty routePrefix composes
    the invalid template 'api//{*route}' and crashes the host (worker bug #1310).
    FastAPI declares the /api prefix itself, so the Functions layer must not."""
    config = json.loads(HOST_JSON.read_text())
    assert config["extensions"]["http"]["routePrefix"] == ""
