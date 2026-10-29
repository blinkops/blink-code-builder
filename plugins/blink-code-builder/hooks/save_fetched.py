#!/usr/bin/env python3
"""Write a fetched automation, agent or dashboard to its YAML file in the project.

PostToolUse hook on `fetch_automation`, `fetch_agent`, `fetch_dashboard` and `save_dashboard`.

- Automation / agent: the tool returns {id, name, yaml, editor_url}; this writes the YAML to
  workspace/workflows/<name>.yaml or workspace/agents/<name>.yaml.
- Dashboard: the tool returns {dashboard, editor_url} (plus `data_errors` after a save); this
  writes the dashboard object as YAML to workspace/dashboards/<display name>.yaml. After a save
  the file gets the new widget ids, so the next save updates those widgets instead of adding
  them again. After a save that created a dashboard, it asks Claude to refresh the workspace lists.

Claude gets the path and id instead of the full object, so it edits the file and saves it back.
"""

import json
import os
import re
import sys
from pathlib import Path

from _common import mark_refresh_needed, to_yaml, tool_result

# Tool -> (folder under the project, id argument of the matching save tool).
TARGETS = {
    "fetch_automation": ("workspace/workflows", "save_automation", "automation_id"),
    "fetch_agent": ("workspace/agents", "save_agent", "agent_id"),
}
DASHBOARD_TOOLS = {"fetch_dashboard", "save_dashboard"}
DASHBOARD_FOLDER = "workspace/dashboards"


def file_name(name):
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "unnamed"


def write(relative_path, text):
    path = Path(os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()) / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.rstrip("\n") + "\n", encoding="utf-8")


def save(tool, result):
    folder, save_tool, id_arg = TARGETS[tool]
    relative_path = f"{folder}/{file_name(result['name'])}.yaml"
    write(relative_path, result["yaml"])
    return (f"ok: wrote {relative_path} (\"{result['name']}\", {id_arg}={result['id']}, editor: "
            f"{result.get('editor_url', '-')}). Read and edit that file, then call {save_tool} with the file's "
            f"content as `yaml` and {id_arg}={result['id']} so the save updates this one in place.")


def save_dashboard(tool, result, tool_input, session_id):
    dashboard = result["dashboard"]
    relative_path = f"{DASHBOARD_FOLDER}/{file_name(dashboard['display_name'])}.yaml"
    write(relative_path, to_yaml(dashboard))
    done = "saved, live now" if tool == "save_dashboard" else "fetched"
    status = (f"ok: {done}. Wrote {relative_path} (\"{dashboard['display_name']}\", id={dashboard['id']}, "
              f"{len(dashboard['resources'])} widgets, editor: {result['editor_url']}). To change it, edit that "
              "file, then call save_dashboard with the file's content as the `dashboard` object.")
    if tool == "save_dashboard" and not (tool_input.get("dashboard") or {}).get("id"):
        mark_refresh_needed(session_id)
        status += (" This created a new dashboard: call get_workspace_resources with include_tables=false "
                   "to add it to dashboards-list.tsv.")
    errors = result.get("data_errors") or []
    if errors:
        status += " These widgets can't load their data; fix them and save again: " + "; ".join(
            f"{error['widget']}: {error['error']}" for error in errors)
    return status


def main():
    payload = json.load(sys.stdin)
    tool = payload.get("tool_name", "").rsplit("__", 1)[-1]
    try:
        result = tool_result(payload.get("tool_response"))
        if tool in DASHBOARD_TOOLS:
            status = save_dashboard(tool, result, payload.get("tool_input") or {}, payload.get("session_id"))
        else:
            status = save(tool, result)
    except Exception as exc:
        # Keep the tool's own output, so Claude still has the object.
        print(f"save_fetched: could not write the YAML to a file: {exc}", file=sys.stderr)
        return
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse", "updatedToolOutput": status}}))


if __name__ == "__main__":
    main()
