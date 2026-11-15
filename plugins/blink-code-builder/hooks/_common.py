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

from blink_shared.config import ENV_WORKSPACE  # noqa: E402 — needs the sys.path entry above

UUID = re.compile(r"[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}")
SESSION_ID = re.compile(r"[\w-]+")
WORKSPACES_FILE = "workspace/workspaces.tsv"  # id, name, role, description: the user's workspaces
# Used inside a sentence: "To choose one, {CHOOSE}."
CHOOSE = "call the Blink MCP tool `list_workspaces` with choose=true (it shows the user a workspace picker)"

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
    if not data or not SESSION_ID.fullmatch(session_id or ""):
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


def _active_file(session_id):
    data = os.environ.get("CLAUDE_PLUGIN_DATA")
    if not data or not SESSION_ID.fullmatch(session_id or ""):
        return None
    return Path(data) / "active-workspace" / f"{session_id}.json"


def active_workspace(session_id):
    """The session's Blink workspace {id, name, role}: the one the user picked, else the default
    in the plugin config, else None. Only a UUID is accepted, because the id becomes a folder name."""
    path = _active_file(session_id)
    try:
        workspace = json.loads(path.read_text(encoding="utf-8")) if path else None
    except (OSError, ValueError):
        workspace = None
    if not isinstance(workspace, dict):
        default = os.environ.get(ENV_WORKSPACE, "").strip()
        row = next((r for r in workspace_rows() if r[0] == default), [default, "", ""])
        workspace = {"id": row[0], "name": row[1], "role": row[2]}
    return workspace if UUID.fullmatch(str(workspace.get("id", ""))) else None


def set_active_workspace(session_id, workspace):
    """Saves the session's workspace. False when there is no place to save it."""
    path = _active_file(session_id)
    if not path:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(workspace), encoding="utf-8")
    return True


def workspace_label(workspace):
    """"SOC-Dev (admin)", or the id for a default workspace that was never picked."""
    if workspace.get("name"):
        return f"{workspace['name']} ({workspace['role']})" if workspace.get("role") else workspace["name"]
    return workspace["id"]


def workspace_rows():
    """The rows of workspace/workspaces.tsv: [id, name, role, description] for each workspace."""
    try:
        lines = (project_dir() / WORKSPACES_FILE).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    return [(line.split("\t") + [""] * 4)[:4] for line in lines if line and not line.startswith("#")]


def save_workspace_rows(rows):
    path = project_dir() / WORKSPACES_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# id\tname\trole\tdescription"]
    lines += ["\t".join(clean_for_tsv(str(cell), limit=None) for cell in row) for row in rows]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def workspace_folder(workspace_id):
    """The workspace's folder, relative to the project: workspace/<name>--<first 8 of the id>.
    Every workspace has its own lists and files. The name comes from workspaces.tsv; a workspace
    that is not in the file yet gets workspace/<first 8 of the id>."""
    if not UUID.fullmatch(str(workspace_id)):
        raise ValueError(f"not a workspace id: {workspace_id!r}")
    short = workspace_id[:8].lower()
    name = next((row[1] for row in workspace_rows() if row[0] == workspace_id), "")
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return f"workspace/{slug}--{short}" if slug else f"workspace/{short}"


def project_dir():
    return Path(os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())
