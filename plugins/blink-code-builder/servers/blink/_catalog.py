"""Reads of workspace_actions.tsv, shared by agents.py and pipeline.py so the file has one parser.

The SessionStart hook (refresh_workspace.py) writes this file every session; it lists every
action this workspace can call right now — published agents, published subflows, templates.
Imported by the sibling modules in this directory.
"""

from typing import NamedTuple

from blink_shared.config import catalog_root

_AGENT_ACTION_PREFIX = "agents."
_SUBFLOW_ACTION_PREFIX = "automations."


class CatalogRow(NamedTuple):
    """One row of workspace_actions.tsv — one action this workspace can call."""
    action: str   # "agents.<id>", "automations.<id>"
    name: str     # display name; for an agent it is "<name> | <title>"
    kind: str     # "agent", "subflow", or a vendor kind


def read_workspace_actions():
    """Read workspace_actions.tsv. Returns its rows as CatalogRow tuples, or None if the file
    is absent — callers that can't verify without it must skip rather than false-flag."""
    try:
        path = catalog_root() / "workspace_actions.tsv"
        lines = path.read_text().splitlines()
    except (OSError, KeyError):
        return None
    rows = []
    for line in lines[1:]: # skip the header
        if not line.strip():
            continue
        cells = line.split("\t")
        if len(cells) >= 3:
            rows.append(CatalogRow(*cells[:3]))
    return rows


def catalog_subflow_ids(rows):
    """Collect the workspace's callable workflows from catalog rows.
    Returns their UUIDs as a set — exactly the ids allowed as an ability."""
    return {row.action.removeprefix(_SUBFLOW_ACTION_PREFIX)
            for row in rows or () if row.kind == "subflow"}


def catalog_agent_id_by_name(rows, name):
    """Look up a published agent's id by name in catalog rows; returns None if absent.
    The name column holds `<name> | <title>`, so it is split first. Case-insensitive,
    matching the server."""
    wanted = (name or "").strip().lower()
    for row in rows or ():
        if row.kind != "agent":
            continue
        if row.name.split(" | ", 1)[0].strip().lower() == wanted:
            return row.action.removeprefix(_AGENT_ACTION_PREFIX)
    return None


def catalog_action_names(rows):
    """Every callable action name from catalog rows, as a set membership check. Returns None
    if `rows` is None (catalog unavailable) — callers must not treat that as an empty set."""
    return None if rows is None else {row.action for row in rows}
