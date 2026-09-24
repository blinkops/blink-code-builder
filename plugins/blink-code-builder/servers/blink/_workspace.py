"""Reads workflows-list.tsv and agents-list.tsv so validate_automation, validate_agent and
save_agent can check what exists and what is callable without calling the Blink API.
A missing file gives None, never an empty result, so "couldn't check" stays visible.
"""

from pathlib import Path
from typing import NamedTuple

from blink_shared.config import workspace_root

WORKFLOWS_LIST_PATH = workspace_root() / "workflows" / "workflows-list.tsv"
AGENTS_LIST_PATH = workspace_root() / "agents" / "agents-list.tsv"

WORKFLOW_PREFIX = "automations."
AGENT_PREFIX = "agents."

# A row in one of these states is live and callable; `draft` never is.
CALLABLE_STATES = ("published", "modified")


class WorkflowRow(NamedTuple):
    """One line of workflows-list.tsv."""
    id: str = ""                  # bare UUID
    name: str = ""
    automation_type: str = ""     # on_demand | scheduled | event
    state: str = ""               # draft | published | modified
    active: str = ""              # "true" | "false"

    def action(self):
        """What a step's `action:` field takes: `automations.<id>`."""
        return WORKFLOW_PREFIX + self.id

    def is_callable(self):
        """Whether a workflow step or an agent ability can call this row right now."""
        return (self.automation_type == "on_demand"
                and self.active == "true"
                and self.state in CALLABLE_STATES)


class AgentRow(NamedTuple):
    """One line of agents-list.tsv."""
    id: str = ""                  # bare UUID
    name: str = ""                # may be "<name> | <title>"
    state: str = ""               # draft | published | modified

    def action(self):
        """What a step's `action:` field takes: `agents.<id>`."""
        return AGENT_PREFIX + self.id

    def is_callable(self):
        """Whether a workflow step can call this agent right now."""
        return self.state in CALLABLE_STATES

    def plain_name(self):
        """The name alone, dropping the ` | <title>` suffix the file may carry."""
        return self.name.split(" | ", 1)[0].strip()


def _read_rows(path, row_class):
    """Parse one list file into `row_class` records, skipping `#` comments and blank lines.

    Returns None if the file is absent, so a caller can tell "nothing to check" apart from
    "checked, found nothing". Short lines are dropped; extra cells are ignored.
    """
    try:
        lines = Path(path).read_text().splitlines()
    except OSError:
        return None
    rows = []
    for line in lines:
        if not line.strip() or line.startswith("#"):
            continue
        cells = line.split("\t")
        if len(cells) >= len(row_class._fields):
            rows.append(row_class(*cells[:len(row_class._fields)]))
    return rows


def read_workflows(path=WORKFLOWS_LIST_PATH):
    """Every WorkflowRow in workflows-list.tsv, or None if the file is absent."""
    return _read_rows(path, WorkflowRow)


def read_agents(path=AGENTS_LIST_PATH):
    """Every AgentRow in agents-list.tsv, or None if the file is absent."""
    return _read_rows(path, AgentRow)


def callable_workflow_ids(path=WORKFLOWS_LIST_PATH):
    """Bare UUIDs of the workflows callable as a subflow right now, as a set.

    Returns None if workflows-list.tsv is absent — a caller that cannot verify must skip
    rather than report a false problem.
    """
    rows = read_workflows(path)
    if rows is None:
        return None
    return {row.id for row in rows if row.is_callable()}


def agent_id_by_name(name, path=AGENTS_LIST_PATH):
    """Find an agent by name, drafts included. Returns (id, state), or None if not found.

    Matching is case-insensitive, like the server's.
    """
    rows = read_agents(path)
    if not rows:
        return None
    wanted = (name or "").strip().lower()
    for row in rows:
        if row.plain_name().lower() == wanted:
            return row.id, row.state
    return None


def workspace_action_names(workflows_path=WORKFLOWS_LIST_PATH, agents_path=AGENTS_LIST_PATH):
    """Every action name a step can call right now — `automations.<id>` and `agents.<id>`.

    Used to validate a step's `action:` field. Returns None only when both files are
    absent; if just one is, the other kind is still checked.
    """
    workflow_rows = read_workflows(workflows_path)
    agent_rows = read_agents(agents_path)
    if workflow_rows is None and agent_rows is None:
        return None
    names = {row.action() for row in workflow_rows or () if row.is_callable()}
    names |= {row.action() for row in agent_rows or () if row.is_callable()}
    return names
