"""Tier 3 (llm-extraction REQ-6.4): live calls against the STAGING Foundry deployment.

Never collected by default — `addopts = "-m 'not llm_live'"` in pyproject.toml;
the nightly workflow opts in with `-m llm_live`. Needs `FOUNDRY_ENDPOINT` and an
Azure identity holding Cognitive Services OpenAI User on the staging account
(the app-CI principal via `azure/login`, or the operator's `az login`).

The eval harness lives beside the Tier-1/Tier-2 tests; pytest's prepend import
mode only puts THIS directory on sys.path, so the unit directory is added here
(same module name `llm_eval` in every tier — one sys.modules entry).
"""

import sys
from pathlib import Path

UNIT_DIR = Path(__file__).resolve().parents[1] / "unit"
if str(UNIT_DIR) not in sys.path:
    sys.path.insert(0, str(UNIT_DIR))
