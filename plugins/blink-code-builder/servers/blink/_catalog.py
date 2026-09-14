"""Reads of workflows-list.tsv and agents-list.tsv, shared by agents.py and pipeline.py so
each file has one parser.

Both are repo files (not the per-session catalog): list_workflows() (pipeline.py) and
list_agents() (agents.py) write them, and each is kept in sync after its own save/publish.
Each lists every workflow/agent in the workspace, drafts included.
Imported by the sibling modules in this directory.
"""

from blink_shared.config import workspace_root

WORKFLOWS_LIST_PATH = workspace_root() / "workflows" / "workflows-list.tsv"
AGENTS_LIST_PATH = workspace_root() / "agents" / "agents-list.tsv"

_WORKFLOW_ACTION_PREFIX = "automations."
_AGENT_ACTION_PREFIX = "agents."

# A workflow/agent row in this state is live and callable; `draft` never is.
_CALLABLE_STATES = ("published", "modified")


def _read_rows(path):
    """Parse a TSV written by list_workflows/list_agents: comment lines (leading `#`) and
    blank lines are skipped. Returns rows as lists of cells, or None if the file is absent —
    callers that can't verify without it must skip rather than false-flag."""
    try:
        lines = Path(path).read_text().splitlines()
    except OSError:
        return None
    return [line.split("\t") for line in lines if line.strip() and not line.startswith("#")]


def callable_workflow_ids(path=WORKFLOWS_LIST_PATH):
    """Workflow ids callable as a subflow right now: on_demand, active, published or modified.
    Returns their bare UUIDs (no `automations.` prefix) as a set, or None if workflows-list.tsv
    is absent — callers that can't verify without it must skip rather than false-flag."""
    rows = _read_rows(path)
    if rows is None:
        return None
    ids = set()
    for cells in rows:
        if len(cells) < 5:
            continue
        action, _name, automation_type, state, active = cells[:5]
        if automation_type == "on_demand" and active == "true" and state in _CALLABLE_STATES:
            ids.add(action.removeprefix(_WORKFLOW_ACTION_PREFIX))
    return ids


def agent_id_by_name(name, path=AGENTS_LIST_PATH):
    """Look up an agent's id and state by name in agents-list.tsv (any state, drafts included).
    Returns (id, state) or None if absent or the file doesn't exist. Case-insensitive, matching
    the server. The name column may hold `<name> | <title>`, so it is split first."""
    rows = _read_rows(path)
    if not rows:
        return None
    wanted = (name or "").strip().lower()
    for cells in rows:
        if len(cells) < 3:
            continue
        action, row_name, state = cells[:3]
        if row_name.split(" | ", 1)[0].strip().lower() == wanted:
            return action.removeprefix(_AGENT_ACTION_PREFIX), state
    return None


def workspace_action_names(workflows_path=WORKFLOWS_LIST_PATH, agents_path=AGENTS_LIST_PATH):
    """Every `automations.<id>` / `agents.<id>` full action name callable right now, for
    validating a step's `action:` field. Returns None only if BOTH lists are unavailable —
    callers that can't verify without at least one of them must skip rather than false-flag.
    If only one list is missing, the other kind is still checked."""
    names = set()
    found_any = False

    workflow_rows = _read_rows(workflows_path)
    if workflow_rows is not None:
        found_any = True
        for cells in workflow_rows:
            if len(cells) >= 5 and cells[2] == "on_demand" and cells[4] == "true" \
                    and cells[3] in _CALLABLE_STATES:
                names.add(cells[0])

    agent_rows = _read_rows(agents_path)
    if agent_rows is not None:
        found_any = True
        for cells in agent_rows:
            if len(cells) >= 3 and cells[2] in _CALLABLE_STATES:
                names.add(cells[0])

    return names if found_any else None
