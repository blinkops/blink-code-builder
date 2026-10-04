#!/usr/bin/env python3
"""PreToolUse hook for publish_dashboard: makes Claude Code ask the user before every call.

Publishing shows the dashboard's data to people outside the workspace, so a human has to say
yes each time. The decision is Claude Code's own permission prompt, not a flag Claude passes,
so it holds even when the tool is in the user's allowlist. The reason below is shown inside
that prompt.

Stdlib only, and no API call: it must answer fast and can't fail open. Whatever goes wrong,
the answer is still "ask".
"""

import json
import sys


def build_reason(tool_input):
    dashboard = tool_input.get("dashboard") or "(no dashboard given)"
    share_with = tool_input.get("share_with") or []
    if isinstance(share_with, str):
        share_with = [share_with]
    targets = ", ".join(str(name) for name in share_with) or "(nobody given)"
    return (
        f"Publishes dashboard {dashboard} to the Blink Portal and shares it with: {targets}. "
        "They will see its data, including the rows behind each chart, and can download them."
    )


def main():
    try:
        tool_input = json.load(sys.stdin).get("tool_input") or {}
    except Exception:
        tool_input = {}
    if not isinstance(tool_input, dict):
        tool_input = {}
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "ask",
            "permissionDecisionReason": build_reason(tool_input),
        }
    }))


if __name__ == "__main__":
    main()
