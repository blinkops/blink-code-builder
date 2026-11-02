#!/usr/bin/env python3
"""PreToolUse hook on `publish_dashboard`: ask the user before a real publish.

`confirm=false` is a preview that changes nothing, so it runs without a prompt. `confirm=true`
publishes the dashboard and shares its data, so Claude Code asks the user every time.
"""

import json
import sys


def main():
    payload = json.load(sys.stdin)
    if not (payload.get("tool_input") or {}).get("confirm"):
        decision, reason = "allow", "Preview only: nothing is published or shared."
    else:
        decision, reason = "ask", ("Publishing shares this dashboard and its data, including the rows "
                                   "behind each chart, with the people listed.")
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": decision,
                                             "permissionDecisionReason": reason}}))


if __name__ == "__main__":
    main()
