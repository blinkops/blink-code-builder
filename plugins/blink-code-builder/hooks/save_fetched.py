#!/usr/bin/env python3
"""Write a fetched automation or agent to its YAML file in the project.

PostToolUse hook on `fetch_automation` and `fetch_agent`. The tool returns
{id, name, yaml, editor_url}; this writes the YAML to workspace/workflows/<name>.yaml or
workspace/agents/<name>.yaml and gives Claude the path and id instead of the YAML, so Claude
edits the file and saves it back with that id.
"""

import json
import os
import re
import sys
from pathlib import Path

from _common import tool_result

# Tool -> (folder under the project, id argument of the matching save tool).
TARGETS = {
    "fetch_automation": ("workspace/workflows", "save_automation", "automation_id"),
    "fetch_agent": ("workspace/agents", "save_agent", "agent_id"),
}


def file_name(name):
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "unnamed"


def save(tool, result):
    folder, save_tool, id_arg = TARGETS[tool]
    relative_path = f"{folder}/{file_name(result['name'])}.yaml"
    path = Path(os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()) / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(result["yaml"].rstrip("\n") + "\n", encoding="utf-8")
    return (f"ok: wrote {relative_path} (\"{result['name']}\", {id_arg}={result['id']}, editor: "
            f"{result.get('editor_url', '-')}). Read and edit that file, then call {save_tool} with the file's "
            f"content as `yaml` and {id_arg}={result['id']} so the save updates this one in place.")


def main():
    payload = json.load(sys.stdin)
    tool = payload.get("tool_name", "").rsplit("__", 1)[-1]
    try:
        status = save(tool, tool_result(payload.get("tool_response")))
    except Exception as exc:
        # Keep the tool's own output, so Claude still has the YAML.
        print(f"save_fetched: could not write the YAML to a file: {exc}", file=sys.stderr)
        return
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse", "updatedToolOutput": status}}))


if __name__ == "__main__":
    main()
