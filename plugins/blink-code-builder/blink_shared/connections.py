"""Controller reads that both entry points need, kept together so they cannot drift.

The `list_connections` MCP tool and the SessionStart snapshot must describe the same
workspace the same way; if this query ever gains paging or a filter, both follow it.
Each caller formats the returned rows for its own output.
"""

from .client import raise_for_status
from .config import workspace_root

# Written by the refresh_workspace hook on every session start.
CONNECTIONS_PATH = workspace_root() / "connections" / "connections.tsv"


def fetch_connections(client):
    """Every connection in the workspace as (name, type_name) rows, grouped by type."""
    results = raise_for_status(client.get("/connections")).json().get("results") or []
    rows = ((connection.get("name") or "", connection.get("type_name") or "")
            for connection in results)
    # Sort by type first, then by name inside each type.
    return sorted(rows, key=lambda name_and_type: (name_and_type[1], name_and_type[0]))
