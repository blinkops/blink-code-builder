#!/usr/bin/env python3
"""Refresh the workspace snapshot through the remote Blink MCP server.

The snapshot is connections.tsv, workflows-list.tsv and agents-list.tsv under the project's
workspace/ folder. A hook can't call an MCP tool, so this runs in two modes:

- SessionStart, and PostToolUse after publish_automation / publish_agent (no args): asks
  Claude to call the remote tool `get_workspace_resources`, and gives it the workspace id.
- PostToolUse on `get_workspace_resources` (`--save`): the tool returns objects; this writes
  them as the TSV lists and the tables YAML, and replaces the tool output Claude sees with a
  one-line status.

Tables are included: the server reads them, one request per table.

Config: CLAUDE_PLUGIN_OPTION_BLINK_WORKSPACE_ID.
"""

import json
import os
import sys
from pathlib import Path

from _common import MCP_NOT_READY_NOTE, clean_for_tsv, tool_result  # also puts the plugin root on sys.path for blink_shared
from blink_shared.config import ENV_WORKSPACE

# Result key -> path under the project, the paths the skills read.
FILES = {
    "workflows": "workspace/workflows/workflows-list.tsv",
    "agents": "workspace/agents/agents-list.tsv",
    "connections": "workspace/connections/connections.tsv",
    "tables": "workspace/tables/tables-schema.yaml",
}

STATE_NOTE = ("# state: draft = never published | published = live, draft matches it | "
              "modified = live, but the draft has newer edits that are not published yet")


def tsv(header, rows, note=None):
    lines = ([note] if note else []) + [header]
    lines += ["\t".join(clean_for_tsv(str(cell), limit=None) for cell in row) for row in rows]
    return "\n".join(lines)


def to_yaml(value, indent=0):
    """Block YAML without PyYAML. Strings are JSON-quoted, which YAML reads as-is."""
    pad = " " * indent
    if isinstance(value, dict):
        if not value:
            return "{}"
        lines = []
        for key, item in value.items():
            if isinstance(item, (dict, list)) and item:
                lines.append(f"{pad}{key}:\n{to_yaml(item, indent + 2)}")
            else:
                lines.append(f"{pad}{key}: {to_yaml(item)}")
        return "\n".join(lines)
    if isinstance(value, list):
        if not value:
            return "[]"
        lines = []
        for item in value:
            if isinstance(item, (dict, list)) and item:
                body = to_yaml(item, indent + 2)
                lines.append(f"{pad}- {body[indent + 2:]}")
            else:
                lines.append(f"{pad}- {to_yaml(item)}")
        return "\n".join(lines)
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    return json.dumps(value, ensure_ascii=False)


def render(result):
    """{relative path key: file text} from the tool's objects. Tables only when included."""
    files = {
        "workflows": tsv("# id\tname\tautomation_type\tstate\tactive",
                         [[w["id"], w["name"], w["automation_type"], w["state"], "true" if w["active"] else "false"]
                          for w in result["workflows"]], STATE_NOTE),
        "agents": tsv("# id\tname\tstate",
                      [[a["id"], f"{a['name']} | {a['title']}" if a.get("title") else a["name"], a["state"]]
                       for a in result["agents"]], STATE_NOTE),
        "connections": tsv("# name\ttype_name",
                           [[c["name"], c["type_name"]] for c in result["connections"]]),
    }
    if result.get("tables") is not None:
        tables = sorted(result["tables"], key=lambda table: table["table"])
        files["tables"] = to_yaml({"tables": tables})
    return files


def save():
    payload = json.load(sys.stdin)
    project = Path(os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())
    try:
        result = tool_result(payload.get("tool_response"))
        written = []
        for key, text in render(result).items():
            path = project / FILES[key]
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text.rstrip("\n") + "\n", encoding="utf-8")
            written.append(key)
        if not written:
            raise ValueError(f"no workspace lists in the result (keys: {sorted(result)})")
        status = f"ok: saved {', '.join(written)} to {project / 'workspace'}."
        counts = ", ".join(f"{len(result[key])} {key}" for key in written)
        message = f"Blink \u2713 workspace lists ready: {counts}."
    except Exception as exc:
        status = f"error: could not save the workspace lists ({exc}). Tell the user."
        message = f"Blink: could not save the workspace lists ({exc})."
    print(json.dumps({"systemMessage": message,
                      "hookSpecificOutput": {"hookEventName": "PostToolUse", "updatedToolOutput": status}}))


def main():
    event = json.load(sys.stdin).get("hook_event_name") or "SessionStart"
    workspace_id = os.environ.get(ENV_WORKSPACE, "")
    if not workspace_id:
        return  # not configured yet: the setup-blink-plugin skill handles that
    call = f"call the Blink MCP tool `get_workspace_resources` with workspace_id={workspace_id}"
    if event == "SessionStart":
        note = (f"The user's Blink workspace id is {workspace_id}: pass it as `workspace_id` to every Blink tool "
                f"that takes one. Before you answer the user's first message, {call}. A hook saves the result as "
                "the workspace/ lists; don't read or summarize it, and don't mention this unless it fails. "
                + MCP_NOT_READY_NOTE)
    else:
        note = f"The publish changed the workspace lists. To refresh them, {call}."
    print(json.dumps({"hookSpecificOutput": {"hookEventName": event, "additionalContext": note}}))


if __name__ == "__main__":
    save() if "--save" in sys.argv else main()
