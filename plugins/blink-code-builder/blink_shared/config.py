"""Plugin config: the userConfig env-var names the hooks read, and the catalog location.

Claude Code injects userConfig values into the hooks as CLAUDE_PLUGIN_OPTION_<KEY>. Stdlib
only (see __init__). There is no API key: every Blink call goes through the remote MCP
server, signed in with the user's own Blink login (/mcp).
"""

import os
from pathlib import Path

ENV_CONTROLLER = "CLAUDE_PLUGIN_OPTION_BLINK_CONTROLLER_URL"
ENV_WORKSPACE = "CLAUDE_PLUGIN_OPTION_BLINK_WORKSPACE_ID"


def catalog_root():
    """Where the vendor catalog is cached — generic Blink capability, same for every
    workspace."""
    return Path(os.environ["CLAUDE_PLUGIN_DATA"]) / "catalog"
