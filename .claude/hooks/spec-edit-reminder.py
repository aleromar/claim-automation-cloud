#!/usr/bin/env python3
"""PostToolUse hook: when a steering/spec doc under .specs/ is edited, remind the
agent to run the triangulated review gate (constitution Principle 10, tier b).

Non-blocking: emits an additionalContext reminder and always exits 0. Reads the
Claude Code hook payload as JSON on stdin.
"""
import json
import os
import re
import sys


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except Exception:
        return  # malformed payload — never block the edit
    path = (data.get("tool_input") or {}).get("file_path", "")
    if not path:
        return
    # Only steering/spec markdown; skip the audit report itself to avoid nagging on triage.
    if re.search(r"/\.specs/.*\.md$", path) and not path.endswith("steering-audit.md"):
        msg = (
            f"Steering/spec doc changed: {os.path.basename(path)}. "
            "Per constitution Principle 10, run the triangulated review gate before "
            "implementation: fan out the spec-review skill into independent critics "
            "(cross-reference, contradiction, constitution alignment, engineering risk), "
            "reconcile their findings, and triage them."
        )
        print(
            json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": "PostToolUse",
                        "additionalContext": msg,
                    }
                }
            )
        )


if __name__ == "__main__":
    main()
