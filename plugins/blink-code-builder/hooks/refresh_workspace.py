#!/usr/bin/env python3
"""Snapshot the workspace's callable actions and connections.

SessionStart hook. Writes workspace/{workflows,agents,connections}/index.tsv in the
project repo on every session — workspace data changes often, so there's no staleness
gate here (refresh_catalog.py, the vendor catalog, caches for up to 7 days).

Config: CLAUDE_PLUGIN_OPTION_BLINK_{CONTROLLER_URL,USER_API_KEY,WORKSPACE_ID}.
Root: workspace/ in the project repo — vendor content stays in
${CLAUDE_PLUGIN_DATA}/catalog/ (see blink_shared.config).
"""

import sys

from _common import clean_for_tsv
from blink_shared.config import has_config, workspace_root
from blink_shared.deps import ensure_installed

# A subflow's action name is `automations.<playbook-id>` — exactly what a calling step's
# `action:` must say, so it is stored verbatim.
SUBFLOW_ACTION_PREFIX = "automations."
# `action_type` values that mark an action as workspace-owned rather than vendor content.
WORKSPACE_ACTION_TYPES = ("Template", "Agent")
# Ask for everything: all collections, all actions in each. There is no server-side
# "workspace-owned only" filter, so the selection happens below.
ACTIONS_QUERY = '{"filter":{"search":"","action_per_collection_limit":0},"limit":0,"offset":0}'


def should_skip():
    # Building a client needs the workspace id, so a missing one means "not configured".
    return not has_config(require_workspace=True)


def workspace_action_rows(collections):
    """The workspace's own callable actions, split into (workflow_rows, agent_rows).

    A workflow row is a subflow or a template action — everything workspace-owned that
    isn't an agent. This is the same catalog the product builds a step from, so appearing
    in it means the action is callable right now — a subflow shows up only while its
    workflow is on-demand, published and active, and disappears on deactivation.
    """
    workflow_rows = []
    agent_rows = []
    for collection in collections:
        for action in collection.get("actions") or []:
            full_name = action.get("full_name") or ""
            is_subflow = full_name.startswith(SUBFLOW_ACTION_PREFIX)
            if not is_subflow and action.get("action_type") not in WORKSPACE_ACTION_TYPES:
                continue
            kind = "subflow" if is_subflow else (action.get("action_type") or "").lower()
            row = (
                full_name,
                # For a subflow this is the workflow's own name.
                action.get("display_name") or "",
                kind,
                # Pack name for a subflow, sub-collection for the rest.
                action.get("category") or "",
                action.get("description") or "",
            )
            (agent_rows if kind == "agent" else workflow_rows).append(row)
    key = lambda row: (row[2], row[1])
    return sorted(workflow_rows, key=key), sorted(agent_rows, key=key)


def write_tsv(path, header, rows):
    lines = [header] + ["\t".join(clean_for_tsv(value) for value in row) for row in rows]
    path.write_text("\n".join(lines) + "\n")


def refresh():
    # Imported here, not at the top: httpx exists only once main() has installed it, and
    # an unconfigured session returns before that happens.
    from blink_shared.client import build_client, raise_for_status
    from blink_shared.connections import fetch_connections

    workspace_dir = workspace_root()
    workflows_dir = workspace_dir / "workflows"
    agents_dir = workspace_dir / "agents"
    connections_dir = workspace_dir / "connections"
    for directory in (workflows_dir, agents_dir, connections_dir):
        directory.mkdir(parents=True, exist_ok=True)

    with build_client(timeout=30) as client:
        collections = raise_for_status(client.get("/actions", params={"q": ACTIONS_QUERY})).json()
        workflow_rows, agent_rows = workspace_action_rows(collections.get("collections") or [])
        connections = fetch_connections(client)

    action_header = "# action\tname\tkind\tcategory\tdescription"
    write_tsv(workflows_dir / "index.tsv", action_header, workflow_rows)
    write_tsv(agents_dir / "index.tsv", action_header, agent_rows)
    write_tsv(connections_dir / "index.tsv", "# name\ttype_name", connections)

    print(
        f"ok: {len(workflow_rows)} workflow actions, {len(agent_rows)} agent actions, "
        f"{len(connections)} connections -> {workspace_dir}",
        file=sys.stderr,
    )


def main():
    if should_skip():
        return
    try:
        ensure_installed()
        refresh()
    except Exception as exc:
        # Nothing here may fail session start — not a brief controller outage, not a
        # failing pip. When these files are missing the skill falls back to the MCP tools.
        print(f"warning: could not refresh workspace snapshot: {exc}", file=sys.stderr)


if __name__ == "__main__":
    main()
