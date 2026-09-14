#!/usr/bin/env python3
"""Snapshot the workspace's connections.

SessionStart hook. Writes connections/connections.tsv (a repo file, alongside
agents/ and tables/) on every session — workspace data changes often, so there's no
staleness gate here (refresh_catalog.py, the vendor catalog, caches for up to 7 days).

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


def refresh():
    # Imported here, not at the top: httpx exists only once main() has installed it, and
    # an unconfigured session returns before that happens.
    from blink_shared.client import build_client
    from blink_shared.connections import DEFAULT_PATH as CONNECTIONS_PATH, fetch_connections

    CONNECTIONS_PATH.parent.mkdir(parents=True, exist_ok=True)

    with build_client(timeout=30) as client:
        connections = fetch_connections(client)

    write_tsv(CONNECTIONS_PATH, "# name\ttype_name", connections)

    print(f"ok: {len(connections)} connections -> {CONNECTIONS_PATH}", file=sys.stderr)


def main():
    if should_skip():
        return
    try:
        ensure_installed()
        refresh()
    except Exception as exc:
        # Nothing here may fail session start — not a brief controller outage, not a
        # failing pip. When this file is missing the skill falls back to the MCP tools.
        print(f"warning: could not refresh workspace snapshot: {exc}", file=sys.stderr)


if __name__ == "__main__":
    main()
