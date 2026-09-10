#!/usr/bin/env python3
"""Snapshot the workspace's callable actions and connections.

SessionStart hook. Writes <root>/{workspace_actions,connections}.tsv on every session —
workspace data changes often, so there's no staleness gate here (refresh_catalog.py, the
vendor catalog, caches for up to 7 days).

Config: CLAUDE_PLUGIN_OPTION_BLINK_{CONTROLLER_URL,USER_API_KEY,WORKSPACE_ID}.
Root: ${CLAUDE_PLUGIN_DATA}/catalog/ — must be set (Claude Code injects it).
"""

import sys

from _common import clean_for_tsv
from blink_shared.config import catalog_root, has_config
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
    """The workspace's own callable actions: published subflows, agents, templates.

    This is the same catalog the product builds a step from, so appearing in it means the
    action is callable right now — a subflow shows up only while its workflow is
    on-demand, published and active, and disappears on deactivation.
    """
    rows = []
    for collection in collections:
        for action in collection.get("actions") or []:
            full_name = action.get("full_name") or ""
            is_subflow = full_name.startswith(SUBFLOW_ACTION_PREFIX)
            if not is_subflow and action.get("action_type") not in WORKSPACE_ACTION_TYPES:
                continue
            rows.append((
                full_name,
                # For a subflow this is the workflow's own name.
                action.get("display_name") or "",
                "subflow" if is_subflow else (action.get("action_type") or "").lower(),
                # Pack name for a subflow, sub-collection for the rest.
                action.get("category") or "",
                action.get("description") or "",
            ))
    return sorted(rows, key=lambda row: (row[2], row[1]))


def write_tsv(path, header, rows):
    lines = [header] + ["\t".join(clean_for_tsv(value) for value in row) for row in rows]
    path.write_text("\n".join(lines) + "\n")


def refresh():
    # Imported here, not at the top: httpx exists only once main() has installed it, and
    # an unconfigured session returns before that happens.
    from blink_shared.client import build_client, raise_for_status
    from blink_shared.connections import fetch_connections

    catalog_dir = catalog_root()
    catalog_dir.mkdir(parents=True, exist_ok=True)

    with build_client(timeout=30) as client:
        collections = raise_for_status(client.get("/actions", params={"q": ACTIONS_QUERY})).json()
        actions = workspace_action_rows(collections.get("collections") or [])
        connections = fetch_connections(client)

    write_tsv(catalog_dir / "workspace_actions.tsv", "# action\tname\tkind\tcategory\tdescription", actions)
    write_tsv(catalog_dir / "connections.tsv", "# name\ttype_name", connections)

    print(
        f"ok: {len(actions)} workspace actions, {len(connections)} connections -> {catalog_dir}",
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
