#!/usr/bin/env python3
"""Refresh the workspace snapshot through the remote Blink MCP server.

The snapshot is connections.tsv, workflows-list.tsv, agents-list.tsv and dashboards-list.tsv
in the session's workspace folder, workspace/<name>--<short id>/ in the project. A hook can't call an
MCP tool, so this runs in these modes:

- SessionStart: names the session's workspace (the user's choice, or the default in the plugin
  config) and asks Claude to call the remote tool `get_workspace_resources`. With no workspace
  yet, it asks Claude to show the workspace picker at the first Blink task.
- PostToolUse after publish_automation / publish_agent / publish_dashboard (no args): asks
  Claude to call `get_workspace_resources`.
- PostToolUse on `get_workspace_resources` (`--save`): the tool returns objects; this writes
  them as the TSV lists and the tables YAML, and replaces the tool output Claude sees with a
  one-line status.
- Stop (`--stop`): after a publish, or a save that created a dashboard, Claude can't end its
  turn until it calls `get_workspace_resources` (a per-session marker, cleared by `--save`).

Tables are included: the server reads them, one request per table.

Claude passes the session's workspace id; active_workspace.py denies any other id.
"""

import json
import os
import sys

from _common import (MCP_NOT_READY_NOTE, CHOOSE, active_workspace, clean_for_tsv, clear_refresh_needed,
                     mark_refresh_needed, project_dir, refresh_needed, to_yaml, tool_result, workspace_folder,
                     workspace_label, workspace_rows)  # also puts the plugin root on sys.path for blink_shared
from blink_shared.config import ENV_CONTROLLER
from register_mcp import blink_host

# Result key -> path in the workspace folder, the paths the skills read.
FILES = {
    "workflows": "workflows/workflows-list.tsv",
    "agents": "agents/agents-list.tsv",
    "connections": "connections/connections.tsv",
    "dashboards": "dashboards/dashboards-list.tsv",
    "tables": "tables/tables-schema.yaml",
}
CALL = "call the Blink MCP tool `get_workspace_resources`"

STATE_NOTE = ("# state: draft = never published | published = live, draft matches it | "
              "modified = live, but the draft has newer edits that are not published yet")


def tsv(header, rows, note=None):
    lines = ([note] if note else []) + [header]
    lines += ["\t".join(clean_for_tsv(str(cell), limit=None) for cell in row) for row in rows]
    return "\n".join(lines)


def pack_items(packs, field):
    """(item, pack name) for every item of every pack."""
    return [(item, pack["name"]) for pack in packs for item in pack[field]]


def render(result):
    """({path key: file text}, {path key: row count}) from the tool's objects."""
    workflows = pack_items(result["automation_packs"], "automations")
    agents = pack_items(result["agent_packs"], "agents")
    files = {
        "workflows": tsv("# id\tname\tautomation_type\tstate\tactive\tpack_name",
                         [[w["id"], w["name"], w["automation_type"], w["state"], "true" if w["active"] else "false",
                           pack] for w, pack in workflows], STATE_NOTE),
        "agents": tsv("# id\tname\tstate\tpack_name",
                      [[a["id"], f"{a['name']} | {a['title']}" if a.get("title") else a["name"], a["state"], pack]
                       for a, pack in agents], STATE_NOTE),
        "connections": tsv("# name\ttype_name",
                           [[c["name"], c["type_name"]] for c in result["connections"]]),
        "dashboards": tsv("# id\tdisplay_name\tpublished_to_portal\tcategories",
                          [[d["id"], d["display_name"], "true" if d["is_portal"] else "false",
                            ",".join(d["categories"])] for d in result["dashboards"]]),
        "tables": to_yaml({"tables": sorted(result["tables"], key=lambda table: table["table"])}),
    }
    sizes = {"workflows": len(workflows), "agents": len(agents)}
    sizes |= {key: len(result[key]) for key in ("connections", "dashboards", "tables")}
    return files, sizes


def save():
    payload = json.load(sys.stdin)
    clear_refresh_needed(payload.get("session_id"))
    try:
        folder = project_dir() / workspace_folder((payload.get("tool_input") or {}).get("workspace_id"))
        result = tool_result(payload.get("tool_response"))
        files, sizes = render(result)
        written = []
        for key, text in files.items():
            path = folder / FILES[key]
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text.rstrip("\n") + "\n", encoding="utf-8")
            written.append(key)
        if not written:
            raise ValueError(f"no workspace lists in the result (keys: {sorted(result)})")
        status = f"ok: saved {', '.join(written)} to {folder}."
        counts = ", ".join(f"{sizes[key]} {key}" for key in written)
        message = f"Blink \u2713 workspace lists ready: {counts}."
    except Exception as exc:
        status = f"error: could not save the workspace lists ({exc}). Tell the user."
        message = f"Blink: could not save the workspace lists ({exc})."
    print(json.dumps({"systemMessage": message,
                      "hookSpecificOutput": {"hookEventName": "PostToolUse", "updatedToolOutput": status}}))


def stop():
    payload = json.load(sys.stdin)
    session_id = payload.get("session_id")
    if not refresh_needed(session_id):
        return
    if payload.get("stop_hook_active"):
        # Already blocked once and still not refreshed (the server may be down): let Claude stop.
        clear_refresh_needed(session_id)
        return
    print(json.dumps({"decision": "block", "reason": (
        f"The workspace lists are out of date after the publish or the new dashboard. To refresh them, {CALL}, "
        "then finish.")}))


def session_start(payload):
    host = blink_host(os.environ.get(ENV_CONTROLLER, ""))
    blink_url = f"The user's Blink URL is https://{host}. " if host else ""
    workspace = active_workspace(payload.get("session_id"))
    if workspace is None:
        note = (f"{blink_url}No Blink workspace is chosen for this session yet. Before your first Blink tool call, "
                f"{CHOOSE}. " + MCP_NOT_READY_NOTE)
        return {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": note}}
    label = workspace_label(workspace)
    # The folder name comes from workspaces.tsv: without the file, list the workspaces first.
    first = "" if workspace_rows() else "call the Blink MCP tool `list_workspaces` without choose, then "
    note = (f"{blink_url}This session's Blink workspace is {label}. Pass workspace_id=\"{workspace['id']}\" to every "
            f"Blink tool that takes one. Its files are in {workspace_folder(workspace['id'])}/. "
            f"When the user asks for another workspace, {CHOOSE}. "
            f"Before you answer the user's first message, {first}{CALL}. "
            "A hook saves the results as the workspace lists; don't read or summarize them, and don't mention this "
            "unless it fails. " + MCP_NOT_READY_NOTE)
    return {"systemMessage": f"Blink workspace: {label}",
            "hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": note}}


def main():
    payload = json.load(sys.stdin)
    event = payload.get("hook_event_name") or "SessionStart"
    if event == "SessionStart":
        print(json.dumps(session_start(payload)))
        return
    if payload.get("tool_name", "").endswith("publish_dashboard") and not (
            payload.get("tool_input") or {}).get("confirm"):
        return  # a preview changes nothing
    mark_refresh_needed(payload.get("session_id"))
    note = f"The publish changed the workspace lists. To refresh them, {CALL}."
    print(json.dumps({"hookSpecificOutput": {"hookEventName": event, "additionalContext": note}}))


if __name__ == "__main__":
    if "--save" in sys.argv:
        save()
    elif "--stop" in sys.argv:
        stop()
    else:
        main()
