"""Controller reads that both entry points need, kept together so they cannot drift.

The `list_connections` MCP tool and the SessionStart snapshot must describe the same
workspace the same way; if this query ever gains paging or a filter, both follow it.
Each caller formats the rows for its own output — this returns data, not text.
"""

from .client import raise_for_status
from .config import workspace_root

# Written by the refresh_workspace hook on every session start.
DEFAULT_PATH = workspace_root() / "connections" / "connections.tsv"


def fetch_connections(client):
    """Every connection in the workspace as (name, type_name) rows, grouped by type."""
    results = raise_for_status(client.get("/connections")).json().get("results") or []
    return sorted(
        ((connection.get("name") or "", connection.get("type_name") or "") for connection in results),
        key=lambda row: (row[1], row[0]),
    )
