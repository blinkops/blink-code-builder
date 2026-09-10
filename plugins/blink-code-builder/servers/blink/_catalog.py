"""Reads of workspace/{workflows,agents}/index.tsv, shared by agents.py and pipeline.py so
each file has one parser.

The SessionStart hook (refresh_workspace.py) writes these files every session; together
they list every action this workspace can call right now — published agents, published
subflows, templates. Imported by the sibling modules in this directory.
"""

from typing import NamedTuple

from blink_shared.config import workspace_root

_AGENT_ACTION_PREFIX = "agents."
_SUBFLOW_ACTION_PREFIX = "automations."


class CatalogRow(NamedTuple):
    """One row of workspace/workflows/index.tsv or workspace/agents/index.tsv."""
    action: str   # "agents.<id>", "automations.<id>"
    name: str     # display name; for an agent it is "<name> | <title>"
    kind: str     # "agent", "subflow", or a vendor kind


def _read_index(relative_path):
    """Read one workspace index.tsv. Returns its rows as CatalogRow tuples, or None if the
    file is absent — callers that can't verify without it must skip rather than false-flag."""
    try:
        lines = (workspace_root() / relative_path).read_text().splitlines()
    except OSError:
        return None
    rows = []
    for line in lines[1:]:  # skip the header
        if not line.strip():
            continue
        cells = line.split("\t")
        if len(cells) >= 3:
            rows.append(CatalogRow(*cells[:3]))
    return rows


def read_workspace_workflows():
    """Read workspace/workflows/index.tsv — callable subflows and templates."""
    return _read_index("workflows/index.tsv")


def read_workspace_agents():
    """Read workspace/agents/index.tsv — published agents."""
    return _read_index("agents/index.tsv")


def read_workspace_actions():
    """Every callable workspace action — workflows and agents combined. Returns None only
    if both files are absent; one missing file degrades to just the other's rows, since
    the two are written independently and either one succeeding is still useful signal."""
    workflows = read_workspace_workflows()
    agents = read_workspace_agents()
    if workflows is None and agents is None:
        return None
    return (workflows or []) + (agents or [])


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
