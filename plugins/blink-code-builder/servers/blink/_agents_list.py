"""Reads agents/agents-list.tsv, the repo-local snapshot of every agent in the workspace.

Written by `list_agents` (mirrors how `tables.get_tables_schema` writes
tables/tables-schema.yaml). Lives in the repo, not the per-user catalog cache, so it can
go stale between runs — callers needing certainty should re-run `list_agents` first.
"""

from pathlib import Path
from typing import NamedTuple

DEFAULT_PATH = Path("agents") / "agents-list.tsv"

_AGENT_ACTION_PREFIX = "agents."


class AgentListRow(NamedTuple):
    """One row of agents-list.tsv — one agent in the workspace."""
    action: str   # "agents.<id>"
    name: str
    title: str
    state: str    # "draft", "published", or "modified"


def read_agents_list(path=None):
    """Read agents-list.tsv. Returns its rows as AgentListRow tuples, or None if the file
    is absent — callers that can't verify without it must skip rather than false-flag."""
    try:
        lines = Path(path or DEFAULT_PATH).read_text().splitlines()
    except OSError:
        return None
    rows = []
    for line in lines:
        if not line.strip() or line.startswith("#"):
            continue
        cells = line.split("\t")
        if len(cells) >= 4:
            rows.append(AgentListRow(*cells[:4]))
    return rows


def agent_id_by_name(rows, name):
    """Look up an agent's id by name in agents-list.tsv rows; returns None if absent.
    Case-insensitive, matching the server."""
    wanted = (name or "").strip().lower()
    for row in rows or ():
        if row.name.strip().lower() == wanted:
            return row.action.removeprefix(_AGENT_ACTION_PREFIX)
    return None
