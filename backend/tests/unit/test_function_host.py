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
