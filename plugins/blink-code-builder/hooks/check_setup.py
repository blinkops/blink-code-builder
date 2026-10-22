#!/usr/bin/env python3
"""On every user message, make sure the user knows the next Blink setup step.

UserPromptSubmit hook. By the time the user sends a message, Claude Code has tried to connect
the Blink MCP server, so this can see what the SessionStart hooks couldn't:

- The server needs sign-in: tell the user exactly how to sign in. Claude Code keeps servers
  that need sign-in in `mcp-needs-auth-cache.json` (an internal file, so a missing file just
  means "no hint").
- The catalog (missing or 7+ days old) or the workspace lists are missing: tell Claude to fetch them before answering.

Otherwise it prints nothing. Reads only local files.
"""

import json
import os
import sys
import time
from pathlib import Path

import _common  # noqa: F401 — puts the plugin root on sys.path for blink_shared
from blink_shared.config import ENV_WORKSPACE, catalog_root
from refresh_catalog import STALE_DAYS
from register_mcp import SERVER_NAME, registered_config

LISTS = ("workflows/workflows-list.tsv", "agents/agents-list.tsv", "connections/connections.tsv")


def needs_sign_in():
    config_dir = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")
    try:
        return SERVER_NAME in json.loads((config_dir / "mcp-needs-auth-cache.json").read_text())
    except (OSError, ValueError):
        return False


def main():
    json.load(sys.stdin)  # the hook payload; nothing in it is needed
    workspace_id = os.environ.get(ENV_WORKSPACE, "")
    if not workspace_id or registered_config() is None:
        return  # not set up or not registered yet: the SessionStart hooks show those steps

    if needs_sign_in():
        message = ("Blink: sign in to finish setup. Run /mcp, select blink-code-builder, choose Authenticate "
                   "and sign in with your Blink login. Then send any message.")
        print(json.dumps({"systemMessage": message, "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": "The Blink MCP server needs sign-in; the user was told how. Don't call Blink tools. If they ask for "
                "help, run the setup-blink-plugin skill."}}))
        return

    project = Path(os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()) / "workspace"
    steps = []
    sentinel = catalog_root() / "actions.tsv"
    if not sentinel.exists() or time.time() - sentinel.stat().st_mtime > STALE_DAYS * 24 * 60 * 60:
        steps.append("call the Blink MCP tool `export_catalog` once")
    if not all((project / name).exists() for name in LISTS):
        steps.append(f"call the Blink MCP tool `get_workspace_resources` with workspace_id={workspace_id}")
    if steps:
        print(json.dumps({"systemMessage": "Blink: downloading the action catalog and workspace lists…",
                          "hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": (
                              "Blink data is missing. Before you answer, " + " and ".join(steps) + ". Hooks save the "
                              "results; don't read or summarize them.")}}))


if __name__ == "__main__":
    main()
