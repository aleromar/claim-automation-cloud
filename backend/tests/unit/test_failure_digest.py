"""error-issues REQ-1/2/3/5: the nightly digest formatter.

Pure transformation: az-CLI query JSON in -> {action, title, body} out. The
fixtures under tests/data/ are REAL az output captured from prod at task 1
(2026-08-27); synthetic rows below cover branches prod has no data for yet
(exceptions table, truncation, grouping cardinality).
"""

import json
from pathlib import Path

import pytest

from scripts.failure_digest import (
    ACTION_DIGEST,
    ACTION_NONE,
    ACTION_TELEMETRY_SILENT,
    BODY_LIMIT,
    TOP_GROUPS,
    build_result,
    main,
    normalize_message,
)

DATA = Path(__file__).parent.parent / "data"
DATE = "2026-08-27"


def _query_json(rows: list, columns: list[str] | None = None) -> dict:
    """az CLI result shape: {"tables":[{"columns":[{"name":..}],"rows":[..]}]}."""
    names = columns or ["timestamp", "itemType", "severityLevel", "message", "details"]
    return {"tables": [{"columns": [{"name": n, "type": "dynamic"} for n in names], "rows": rows}]}


def _heartbeat(count: int) -> dict:
    return _query_json([[count]], columns=["Count"])


def _trace(ts: str, sev: int, message: str) -> list:
    return [ts, "trace", sev, message, ""]


@pytest.fixture()
def real_failures() -> dict:
    return json.loads((DATA / "app_insights_failure_query.json").read_text())


@pytest.fixture()
def real_heartbeat() -> dict:
    return json.loads((DATA / "app_insights_heartbeat_query.json").read_text())


# --- REQ-1.1/1.2: the digest ------------------------------------------------


def test_real_fixture_produces_digest(real_failures, real_heartbeat):
    result = build_result(real_failures, real_heartbeat, DATE)
    assert result["action"] == ACTION_DIGEST
    assert result["title"] == f"[auto] Backend failures {DATE} (2 events)"
    assert "callback rejected: invalid or expired state" in result["body"]
    # Per-group metadata: severity, count, first/last UTC timestamps (REQ-1.2).
    assert "2026-08-27T07:29:30" in result["body"]
    assert "WARNING" in result["body"]


def test_no_events_with_heartbeat_is_silent(real_heartbeat):
    result = build_result(_query_json([]), real_heartbeat, DATE)
    assert result["action"] == ACTION_NONE
    assert result["title"] is None and result["body"] is None


# --- REQ-5: dead-man's check ------------------------------------------------


def test_no_events_no_heartbeat_is_telemetry_silent():
    result = build_result(_query_json([]), _heartbeat(0), DATE)
    assert result["action"] == ACTION_TELEMETRY_SILENT
    assert result["title"] == f"[auto] Telemetry silent {DATE}"
    assert "worker" in result["body"].lower()


def test_failures_with_no_heartbeat_still_digest_but_flagged():
    rows = [_trace("2026-08-27T01:00:00Z", 3, "boom")]
    result = build_result(_query_json(rows), _heartbeat(0), DATE)
    assert result["action"] == ACTION_DIGEST
    # The missing heartbeat must be visible in the digest, not swallowed.
    assert "no worker heartbeat" in result["body"].lower()


# --- REQ-3.2: normalization + grouping --------------------------------------


def test_variable_ids_collapse_to_one_group():
    rows = [
        _trace(
            "2026-08-27T01:00:00Z", 2, "email_failed ref=CLM-0042 id=18a9f2c3b4d5e6f7 reason=boom"
        ),
        _trace(
            "2026-08-27T02:00:00Z", 2, "email_failed ref=CLM-0099 id=28b3e1d4c5f6a7b8 reason=boom"
        ),
    ]
    result = build_result(_query_json(rows), _heartbeat(40), DATE)
    assert result["body"].count("email_failed") == 1
    assert "x2" in result["body"]


def test_url_and_body_snippets_collapse():
    m1 = 'HTTP 500 from GET https://api.trello.com/1/cards/abc123?key=secret: {"error": "a"}'
    m2 = 'HTTP 500 from GET https://api.trello.com/1/cards/xyz789?key=secret: {"error": "b"}'
    rows = [_trace("2026-08-27T01:00:00Z", 2, m1), _trace("2026-08-27T02:00:00Z", 2, m2)]
    result = build_result(_query_json(rows), _heartbeat(40), DATE)
    assert "x2" in result["body"]
    # Neither raw URL query string nor response body may leak into the group key line.
    assert "key=secret" not in normalize_message(m1)


def test_normalize_strips_uuids_and_emails():
    msg = "token for a1b2c3d4-e5f6-7890-abcd-ef1234567890 of user someone@example.com rejected"
    normalized = normalize_message(msg)
    assert "a1b2c3d4" not in normalized
    assert "someone@example.com" not in normalized


# --- REQ-1.2: cap, ordering, exception rows ---------------------------------


def test_top_groups_cap():
    rows = [
        _trace(f"2026-08-27T0{i % 10}:00:00Z", 2, f"distinct failure kind {chr(65 + i)}")
        for i in range(TOP_GROUPS + 5)
    ]
    result = build_result(_query_json(rows), _heartbeat(40), DATE)
    assert result["body"].count("distinct failure kind") == TOP_GROUPS
    assert "5 more" in result["body"]
    assert f"({TOP_GROUPS + 5} events)" in result["title"]


def test_severity_orders_before_count():
    rows = [
        _trace("2026-08-27T01:00:00Z", 2, "frequent warning"),
        _trace("2026-08-27T02:00:00Z", 2, "frequent warning"),
        _trace("2026-08-27T03:00:00Z", 3, "rare error"),
    ]
    body = build_result(_query_json(rows), _heartbeat(40), DATE)["body"]
    assert body.index("rare error") < body.index("frequent warning")


def test_exception_row_with_null_severity_is_included():
    # Server-side the KQL keeps null-severity exception rows (gate-2 C1); the
    # formatter must tolerate severityLevel=None. Prod has no exception rows
    # yet, so this branch rides on a hand-built row (Confidence notes).
    rows = [
        [
            "2026-08-27T03:00:00Z",
            "exception",
            None,
            "ValueError: bad claim",
            "Traceback (most recent call last):\n  ...",
        ]
    ]
    result = build_result(_query_json(rows), _heartbeat(40), DATE)
    assert result["action"] == ACTION_DIGEST
    assert "ValueError: bad claim" in result["body"]
    assert "```" in result["body"]  # stack traces are fenced (REQ-1.2)


# --- REQ-1.2: fence-safe truncation ------------------------------------------


def test_truncation_is_fence_safe():
    huge = "Traceback (most recent call last):\n" + ("  File line\n" * 20000)
    rows = [["2026-08-27T03:00:00Z", "exception", 3, "OOM in pdf_gen", huge]]
    body = build_result(_query_json(rows), _heartbeat(40), DATE)["body"]
    assert len(body) <= BODY_LIMIT
    assert body.count("```") % 2 == 0  # never cut inside a fence
    assert "truncated" in body.lower()
    assert "OOM in pdf_gen" in body


# --- CLI contract (the workflow's interface) ---------------------------------


def test_cli_prints_result_json(tmp_path, capsys, real_failures, real_heartbeat):
    f = tmp_path / "f.json"
    h = tmp_path / "h.json"
    f.write_text(json.dumps(real_failures))
    h.write_text(json.dumps(real_heartbeat))
    main(["--failures", str(f), "--heartbeat", str(h), "--date", DATE])
    printed = json.loads(capsys.readouterr().out)
    assert printed["action"] == ACTION_DIGEST
    assert printed["title"].startswith("[auto] Backend failures")
