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
TRUNCATION_MARK: Final = "… [truncated]"

_SEVERITY_LABELS: Final = {4: "CRITICAL", 3: "ERROR", 2: "WARNING", 1: "INFO", 0: "DEBUG"}

_EMAIL_RE: Final = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_UUID_RE: Final = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)
_HEX_ID_RE: Final = re.compile(r"\b[0-9a-fA-F]{12,}\b")
_URL_RE: Final = re.compile(r"https?://\S+")
_REF_RE: Final = re.compile(r"\b[A-Z]{2,}-\d+\b")
# raise_for_status_logged shape: "HTTP 500 from GET <url>: <=500-char body snippet".
# Matched on the RAW message (before URL substitution — the URL regex would
# otherwise swallow the delimiting colon along with the query string).
_HTTP_BODY_RE: Final = re.compile(r"(HTTP \d+ from \w+ )\S+:\s.*", re.DOTALL)


def normalize_message(message: str) -> str:
    """Collapse the volatile parts of a log message into a stable group key."""
    normalized = _HTTP_BODY_RE.sub(r"\1<url>: <body>", message)
    normalized = _EMAIL_RE.sub("<email>", normalized)
    normalized = _UUID_RE.sub("<id>", normalized)
    normalized = _HEX_ID_RE.sub("<id>", normalized)
    normalized = _URL_RE.sub("<url>", normalized)
    normalized = _REF_RE.sub("<ref>", normalized)
    return " ".join(normalized.split())


def _rows_as_dicts(query: dict[str, Any]) -> list[dict[str, Any]]:
    tables = query.get("tables") or []
    if not tables:
        return []
    names = [column["name"] for column in tables[0]["columns"]]
    return [dict(zip(names, row)) for row in tables[0]["rows"]]


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
        group["first"] = min(group["first"], row["timestamp"])
        group["last"] = max(group["last"], row["timestamp"])
    return sorted(groups.values(), key=lambda g: (-g["severity"], -g["count"]))


def _render_group(group: dict[str, Any], details_cap: int | None) -> str:
    lines = [
        f"### {group['label']} x{group['count']} — {group['message']}",
        f"- first: `{group['first']}` — last: `{group['last']}` (UTC)",
    ]
    details = group["details"]
    if details:
        if details_cap is not None and len(details) > details_cap:
            details = details[:details_cap] + "\n" + TRUNCATION_MARK
        lines.append(f"```\n{details}\n```")
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
    # because whole groups are removed, never sliced).
    while shown:
        shown = shown[:-1]
        dropped = len(groups) - len(shown)
        parts_rendered = parts + [_render_group(g, 500) for g in shown]
        parts_rendered.append(f"…{dropped} groups {TRUNCATION_MARK}")
        body = "\n\n".join(parts_rendered)
        if len(body) <= BODY_LIMIT:
            return body
    return TRUNCATION_MARK


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
                "events": 0,
            }
        return {"action": ACTION_NONE, "title": None, "body": None, "events": 0}
    return {
        "action": ACTION_DIGEST,
        "title": f"[auto] Backend failures {date} ({len(rows)} events)",
        "body": _render_body(_group(rows), heartbeat),
        "events": len(rows),
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
