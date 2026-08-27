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
    return {"tables": [{"columns": [{"name": n} for n in names], "rows": rows}]}


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


# --- Gate 3 fixes (2026-08-27) ------------------------------------------------


@pytest.mark.parametrize(
    "malformed",
    [{}, {"tables": []}, {"tables": None}, {"error": {"code": "BadRequest"}}],
)
def test_malformed_query_json_raises_instead_of_clean_night(malformed):
    # Gate 3 C1: an unexpected az payload must fail LOUDLY (workflow fails ->
    # GitHub email), never read as "no failures tonight".
    with pytest.raises(ValueError):
        build_result(malformed, _heartbeat(40), DATE)


def test_genuinely_empty_rows_is_still_a_clean_night():
    result = build_result(_query_json([]), _heartbeat(40), DATE)
    assert result["action"] == ACTION_NONE


def test_embedded_backtick_fences_stay_balanced():
    # Gate 3 W3: a stack trace containing ``` must not close our fence early.
    details = "Traceback:\n```\nsneaky embedded fence\nmore lines"
    rows = [["2026-08-27T03:00:00Z", "exception", 3, "doc-string crash", details]]
    body = build_result(_query_json(rows), _heartbeat(40), DATE)["body"]
    assert "````" in body  # wrapping fence is longer than any run inside
    assert body.count("````") == 2


def test_huge_message_does_not_wipe_the_digest():
    # Gate 3 W4: an unbounded group-header message must not starve every
    # other group out of the body.
    rows = [
        _trace("2026-08-27T01:00:00Z", 2, "lorem ipsum " * 6000),
        _trace("2026-08-27T02:00:00Z", 3, "small but important error"),
    ]
    body = build_result(_query_json(rows), _heartbeat(40), DATE)["body"]
    assert "small but important error" in body
    assert max(len(line) for line in body.splitlines()) < 500


def test_first_last_use_time_order_not_string_order():
    # Gate 3 W5: "30.68Z" < "30.685Z" temporally, but not as strings.
    rows = [
        _trace("2026-08-27T07:29:30.685Z", 2, "same failure"),
        _trace("2026-08-27T07:29:30.68Z", 2, "same failure"),
    ]
    body = build_result(_query_json(rows), _heartbeat(40), DATE)["body"]
    assert "first: `2026-08-27T07:29:30.68Z`" in body
    assert "last: `2026-08-27T07:29:30.685Z`" in body


def test_group_keeps_the_richest_details():
    # Gate 3 W6: first occurrence without a trace must not shadow a later
    # occurrence that carries one.
    rows = [
        ["2026-08-27T01:00:00Z", "trace", 2, "flaky call failed", ""],
        ["2026-08-27T02:00:00Z", "trace", 2, "flaky call failed", "Traceback: the good stuff"],
    ]
    body = build_result(_query_json(rows), _heartbeat(40), DATE)["body"]
    assert "the good stuff" in body


def test_null_severity_exception_orders_above_warning():
    rows = [
        _trace("2026-08-27T01:00:00Z", 2, "warning noise"),
        ["2026-08-27T02:00:00Z", "exception", None, "real crash", "Traceback"],
    ]
    body = build_result(_query_json(rows), _heartbeat(40), DATE)["body"]
    assert body.index("real crash") < body.index("warning noise")


def test_acronyms_and_letter_only_hex_words_survive_normalization():
    # Gate 3 S7: UTF-8 / SHA-1 are not claim refs; a hex-alphabet word with no
    # digit is a word, not an id.
    normalized = normalize_message(
        "UTF-8 decode failed in SHA-1 helper: interface CAFEBABEDEADBEEF"
    )
    assert "UTF-8" in normalized
    assert "SHA-1" in normalized
    # An actual id (contains digits) is still stripped:
    assert "<id>" in normalize_message("id 18a9f2c3b4d5e6f7 rejected")


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
