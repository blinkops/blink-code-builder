#!/usr/bin/env python3
"""SessionStart hook. Writes the workspace snapshot on every session: connections.tsv,
workflows-list.tsv and agents-list.tsv. Tables stay on demand (get_tables_schema), since
their schema costs one call per table.

Config: CLAUDE_PLUGIN_OPTION_BLINK_{CONTROLLER_URL,USER_API_KEY,WORKSPACE_ID}.
"""

import sys

from _common import clean_for_tsv
from blink_shared.config import has_config
from blink_shared.deps import ensure_installed


def should_skip():
    # Building a client needs the workspace id, so a missing one means "not configured".
    return not has_config(require_workspace=True)


def write_tsv(path, header, rows):
    lines = [header] + ["\t".join(clean_for_tsv(value) for value in row) for row in rows]
    path.write_text("\n".join(lines) + "\n")


def refresh_connections():
    from blink_shared.client import build_client
    from blink_shared.connections import CONNECTIONS_PATH, fetch_connections

    CONNECTIONS_PATH.parent.mkdir(parents=True, exist_ok=True)

    with build_client(timeout=30) as client:
        connections = fetch_connections(client)

    write_tsv(CONNECTIONS_PATH, "# name\ttype_name", connections)

    return f"ok: wrote {CONNECTIONS_PATH} ({len(connections)} connections)"


def refresh_workflows():
    from servers.blink.pipeline import list_workflows

    return list_workflows()


def refresh_agents():
    from servers.blink.agents import list_agents

    return list_agents()


def refresh():
    # Imported inside each refresh, not at the top: httpx exists only once main() has
    # installed it, and an unconfigured session returns before that happens.
    for step in (refresh_connections, refresh_workflows, refresh_agents):
        try:
            print(step(), file=sys.stderr)
        except Exception as exc:
            # One failing read must not cost the others their refresh.
            print(f"warning: {step.__name__} failed: {exc}", file=sys.stderr)


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
