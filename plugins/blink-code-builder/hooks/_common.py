"""Helpers shared by the hooks (refresh_catalog, refresh_workspace, safety_gate, save_fetched).

Not a hook itself — the leading underscore keeps it out of hooks.json. Both hooks run as
scripts from this directory, so `import _common` resolves via sys.path[0].

Importing this also puts the plugin root on sys.path, which is what makes `blink_shared`
reachable from a hook. Doing it here means a hook's own imports stay plain imports at the
top of the file.
"""

import json
import os
import re
import sys
from pathlib import Path

_PLUGIN_ROOT = str(Path(__file__).resolve().parent.parent)
if _PLUGIN_ROOT not in sys.path:
    sys.path.insert(0, _PLUGIN_ROOT)

# Appended to the SessionStart notes that ask Claude to call a Blink tool.
MCP_NOT_READY_NOTE = (
    "If the Blink tools aren't found because the server is still connecting, search for them again "
    "(it takes a few seconds). If the server needs sign-in, tell the user: \"Run /mcp, select "
    "blink-code-builder, choose Authenticate and sign in to Blink, then send any message.\" Whenever "
    "the tools are still missing, do this before your next answer instead; if it keeps failing, run "
    "the setup-blink-plugin skill."
)


def clean_for_tsv(text, limit=200):
    """Collapse whitespace and truncate, so the value is safe as a TSV cell.
    Embedded tabs would split the row into extra columns; newlines would create fake rows.
    """
    return " ".join((text or "").split())[:limit]


def catalog_actions():
    """Every action full_name in the local catalog (actions.tsv), or an empty set without one."""
    from blink_shared.config import catalog_root

    try:
        lines = (catalog_root() / "actions.tsv").read_text(encoding="utf-8").splitlines()
    except (OSError, KeyError):
        return set()
    return {line.split("\t", 1)[0] for line in lines if line and not line.startswith("#")}


def tool_result(tool_response):
    """The tool's result object: as is, inline JSON text, or the file Claude Code saved a large
    result to. Structured output arrives as {"result": "<the JSON text>"}."""
    if isinstance(tool_response, list):
        tool_response = "".join(block.get("text", "") for block in tool_response if isinstance(block, dict))
    if isinstance(tool_response, dict):
        result = tool_response
    else:
        text = str(tool_response)
        try:
            result = json.loads(text)
        except json.JSONDecodeError:
            match = re.search(r"saved to (.+?\.(?:txt|json))", text)
            if not match:
                raise ValueError(f"unexpected tool output: {text[:200]!r}")
            result = json.loads(Path(match.group(1)).read_text(encoding="utf-8"))
    if isinstance(result, dict) and isinstance(result.get("result"), str):
        result = json.loads(result["result"])
    if not isinstance(result, dict):
        raise ValueError(f"unexpected tool output: {str(result)[:200]!r}")
    return result


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


def _refresh_marker(session_id):
    """The marker that says this session must refresh the workspace lists, or None outside
    Claude Code. It lives in the plugin data dir, not in the user's repo."""
    data = os.environ.get("CLAUDE_PLUGIN_DATA")
    if not data or not session_id:
        return None
    return Path(data) / "refresh-needed" / session_id


def mark_refresh_needed(session_id):
    marker = _refresh_marker(session_id)
    if marker:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.touch()


def clear_refresh_needed(session_id):
    marker = _refresh_marker(session_id)
    if marker:
        marker.unlink(missing_ok=True)


def refresh_needed(session_id):
    marker = _refresh_marker(session_id)
    return bool(marker and marker.exists())
