"""error-issues: nightly failure-digest formatter (REQ-1/2/3/5).

Pure, stdlib-only transformation: az-CLI App Insights query JSON in ->
{action, title, body} JSON out. The workflow in the private roadmap repo does
all I/O (az query, gh issue create); this module only formats. Grouping keys
normalize volatile message parts (ids, URLs, HTTP body snippets) so one flaky
cause renders as one counted group, not one row per occurrence.
"""

import argparse
import json
import re
from pathlib import Path
from typing import Any, Final

ACTION_NONE: Final = "none"
ACTION_DIGEST: Final = "digest"
ACTION_TELEMETRY_SILENT: Final = "telemetry_silent"

# GitHub issue-body hard limit is 65 536 chars; stay at the limit, cut fences safely.
BODY_LIMIT: Final = 65536
TOP_GROUPS: Final = 20
# Per-group stack-trace budget once the body overflows (first shrink step).
DETAILS_CAP: Final = 4000
# Group-header cap: one unbounded message must not starve the other groups
# out of the body (App Insights messages run to 32 KB).
MESSAGE_CAP: Final = 300
TRUNCATION_MARK: Final = "… [truncated]"

_SEVERITY_LABELS: Final = {4: "CRITICAL", 3: "ERROR", 2: "WARNING", 1: "INFO", 0: "DEBUG"}

# Local part bounded to 64 chars (RFC limit): the open-ended original was
# O(n²) on long @-less messages — 2 s at App Insights' 32 KB message cap.
_EMAIL_RE: Final = re.compile(r"[\w.+-]{1,64}@[\w-]+\.[\w.-]+")
_UUID_RE: Final = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)
# The digit lookahead keeps hex-alphabet WORDS (CAFEBABEDEADBEEF, "interface
# ABCDEF...") out of the id bucket; real ids virtually always carry a digit.
_HEX_ID_RE: Final = re.compile(r"\b(?=[0-9a-fA-F]*\d)[0-9a-fA-F]{12,}\b")
_URL_RE: Final = re.compile(r"https?://\S+")
# 3+ digits: claim refs (CLM-0042), not acronyms (UTF-8, SHA-1).
_REF_RE: Final = re.compile(r"\b[A-Z]{2,}-\d{3,}\b")
# raise_for_status_logged shape: "HTTP 500 from GET <url>: <=500-char body snippet".
# Matched on the RAW message (before URL substitution — the URL regex would
# otherwise swallow the delimiting colon along with the query string).
_HTTP_BODY_RE: Final = re.compile(r"(HTTP \d+ from \w+ )\S+:\s.*", re.DOTALL)


def normalize_message(message: str) -> str:
    """Collapse the volatile parts of a log message into a stable group key."""
    normalized = _HTTP_BODY_RE.sub(r"\1<url>: <body>", message)
    if "@" in normalized:
        normalized = _EMAIL_RE.sub("<email>", normalized)
    normalized = _UUID_RE.sub("<id>", normalized)
    normalized = _HEX_ID_RE.sub("<id>", normalized)
    normalized = _URL_RE.sub("<url>", normalized)
    normalized = _REF_RE.sub("<ref>", normalized)
    return " ".join(normalized.split())


def _rows_as_dicts(query: dict[str, Any]) -> list[dict[str, Any]]:
    # Strict on shape: an unexpected az payload (error body, wrong file) must
    # fail LOUDLY — the workflow failure emails the operator — never read as a
    # clean night. Only a well-formed table with zero rows means "no events".
    tables = query.get("tables")
    if not isinstance(tables, list) or not tables:
        raise ValueError(f"unexpected az query result shape (no tables): {str(query)[:200]}")
    rows = tables[0].get("rows")
    if not isinstance(rows, list):
        raise ValueError("unexpected az query result shape: tables[0].rows is not a list")
    names = [column["name"] for column in tables[0]["columns"]]
    return [dict(zip(names, row)) for row in rows]


def _ts_key(timestamp: str) -> tuple[str, str]:
    """Chronological sort key: az emits ISO-8601 Z with VARIABLE fraction width,
    so plain string comparison inverts e.g. 30.685Z vs 30.68Z."""
    base, dot, fraction = timestamp.rstrip("Z").partition(".")
    return (base, fraction.ljust(9, "0") if dot else "0" * 9)


def _heartbeat_count(query: dict[str, Any]) -> int:
    rows = _rows_as_dicts(query)
    return int(rows[0]["Count"]) if rows else 0


def _severity_label(row: dict[str, Any]) -> str:
    if row.get("itemType") == "exception":
        return "EXCEPTION"
    return _SEVERITY_LABELS.get(row.get("severityLevel"), "ERROR")


def _sort_severity(row: dict[str, Any]) -> int:
    # Null-severity exception rows outrank warnings: an exception is an error.
    severity = row.get("severityLevel")
    return 3 if severity is None else int(severity)


def _group(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple, dict[str, Any]] = {}
    for row in rows:
        key = (
            row.get("itemType"),
            _sort_severity(row),
            normalize_message(row.get("message") or ""),
        )
        group = groups.setdefault(
            key,
            {
                "label": _severity_label(row),
                "severity": _sort_severity(row),
                "message": key[2],
                "count": 0,
                "first": row["timestamp"],
                "last": row["timestamp"],
                "details": row.get("details") or "",
            },
        )
        group["count"] += 1
        group["first"] = min(group["first"], row["timestamp"], key=_ts_key)
        group["last"] = max(group["last"], row["timestamp"], key=_ts_key)
        # Keep the richest trace: the first occurrence may have arrived bare.
        details = row.get("details") or ""
        if len(details) > len(group["details"]):
            group["details"] = details
    return sorted(groups.values(), key=lambda g: (-g["severity"], -g["count"]))


def _render_group(group: dict[str, Any], details_cap: int | None) -> str:
    message = group["message"]
    if len(message) > MESSAGE_CAP:
        message = message[:MESSAGE_CAP] + TRUNCATION_MARK
    lines = [
        f"### {group['label']} x{group['count']} — {message}",
        f"- first: `{group['first']}` — last: `{group['last']}` (UTC)",
    ]
    details = group["details"]
    if details:
        if details_cap is not None and len(details) > details_cap:
            details = details[:details_cap] + "\n" + TRUNCATION_MARK
        # The fence must outrun any backtick run inside the trace, or an
        # embedded ``` closes our block early / unbalances the body.
        longest_run = max((len(m.group()) for m in re.finditer(r"`+", details)), default=0)
        fence = "`" * max(3, longest_run + 1)
        lines.append(f"{fence}\n{details}\n{fence}")
    return "\n".join(lines)


def _render_body(groups: list[dict[str, Any]], heartbeat: int) -> str:
    parts = []
    if heartbeat == 0:
        parts.append(
            "**⚠ No worker heartbeat in the window** — the 30-min timer logged "
            "nothing; investigate scheduler health alongside the failures below."
        )
    shown = groups[:TOP_GROUPS]
    hidden = len(groups) - len(shown)
    for details_cap in (None, DETAILS_CAP, 500):
        parts_rendered = parts + [_render_group(g, details_cap) for g in shown]
        if hidden:
            parts_rendered.append(f"…and {hidden} more groups (see App Insights for the rest).")
        body = "\n\n".join(parts_rendered)
        if len(body) <= BODY_LIMIT:
            return body
    # Last resort: drop trailing groups until the body fits (fences stay balanced
    # because whole groups are removed, never sliced). With MESSAGE_CAP bounding
    # every header, the zero-group floor always fits.
    for keep in range(len(shown) - 1, -1, -1):
        dropped = len(groups) - keep
        parts_rendered = parts + [_render_group(g, 500) for g in shown[:keep]]
        parts_rendered.append(f"…{dropped} groups {TRUNCATION_MARK}")
        body = "\n\n".join(parts_rendered)
        if len(body) <= BODY_LIMIT:
            return body
    return "\n\n".join(parts + [f"…{len(groups)} groups {TRUNCATION_MARK}"])


def build_result(
    failures: dict[str, Any], heartbeat_query: dict[str, Any], date: str
) -> dict[str, Any]:
    rows = _rows_as_dicts(failures)
    heartbeat = _heartbeat_count(heartbeat_query)
    if not rows:
        if heartbeat == 0:
            return {
                "action": ACTION_TELEMETRY_SILENT,
                "title": f"[auto] Telemetry silent {date}",
                "body": (
                    "No failure rows AND no worker heartbeat traces in the last 25 h. "
                    "A quiet night is only a clean night if the 30-min worker's wake "
                    "logs are visible — telemetry may be dead (broken connection "
                    "string, recreated App Insights resource, stopped app)."
                ),
            }
        return {"action": ACTION_NONE, "title": None, "body": None}
    return {
        "action": ACTION_DIGEST,
        "title": f"[auto] Backend failures {date} ({len(rows)} events)",
        "body": _render_body(_group(rows), heartbeat),
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--failures", required=True, help="az failure-query JSON file")
    parser.add_argument("--heartbeat", required=True, help="az heartbeat-count JSON file")
    parser.add_argument("--date", required=True, help="digest date, YYYY-MM-DD (UTC)")
    args = parser.parse_args(argv)
    result = build_result(
        json.loads(Path(args.failures).read_text()),
        json.loads(Path(args.heartbeat).read_text()),
        args.date,
    )
    print(json.dumps(result))


if __name__ == "__main__":
    main()
