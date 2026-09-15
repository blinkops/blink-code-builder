"""Blink credentials: the env-var names, how they resolve, and what derives from them.

Claude Code injects these from the plugin's userConfig — natively into the MCP server
subprocess, and into the hooks under the same names. Stdlib only (see __init__).
"""

import os
from pathlib import Path

ENV_CONTROLLER = "CLAUDE_PLUGIN_OPTION_BLINK_CONTROLLER_URL"
ENV_WORKSPACE = "CLAUDE_PLUGIN_OPTION_BLINK_WORKSPACE_ID"
ENV_API_KEY = "CLAUDE_PLUGIN_OPTION_BLINK_USER_API_KEY"


def has_config(require_workspace=False):
    """Whether the plugin is configured: a controller URL and an API key.

    `require_workspace` also demands the workspace id — set it for anything that builds
    a client, since require_env() raises without one. A hook that gets False back must
    skip quietly instead of failing the session.
    """
    names = [ENV_CONTROLLER, ENV_API_KEY]
    if require_workspace:
        names.append(ENV_WORKSPACE)
    return all(os.environ.get(name) for name in names)


def controller_url():
    """The API base, normalized to end in /api/v1 so a bare domain works too."""
    return os.environ[ENV_CONTROLLER].rstrip("/") + "/api/v1"


def require_env():
    """Return (controller, workspace, api_key), or raise with a secret-safe message.

    Names the missing variables, never their values.
    """
    missing = [name for name in (ENV_CONTROLLER, ENV_WORKSPACE, ENV_API_KEY) if not os.environ.get(name)]
    if missing:
        raise RuntimeError(
            "missing Blink config (" + ", ".join(missing) + "). "
            "The SessionStart hook didn't propagate userConfig — run the setup-blink-plugin skill."
        )
    return controller_url(), os.environ[ENV_WORKSPACE], os.environ[ENV_API_KEY]


def catalog_root():
    """Where the vendor catalog is cached — generic Blink capability, same for every
    workspace."""
    return Path(os.environ["CLAUDE_PLUGIN_DATA"]) / "catalog"


def workspace_root():
    """Where this user's own Blink workspace content lives, inside the project repo —
    workflows, agents, connections, tables. One parent folder so workspace data is
    visibly separate from the repo's own files."""
    return Path("workspace")


def workspace_base_url():
    """The `{controller}/workspace/{workspace}` prefix, for printing API URLs."""
    controller, workspace, _ = require_env()
    return f"{controller}/workspace/{workspace}"


def editor_url(playbook_id):
    """Clickable editor link for a playbook — the app URL, not the API one.

    The configured value is the app domain; dropping the /api/v1 suffix gives the UI
    origin back.
    """
    controller, workspace, _ = require_env()
    app_base = controller.removesuffix("/api/v1")
    return f"{app_base}/workspace/{workspace}/workflow/{playbook_id}/edit"


def agent_editor_url(agent_id):
    """Clickable agent-builder link for an agent — the app URL, not the API one.

    The agent builder lives at agents/agent-builder/:agent_id in the app.
    """
    controller, workspace, _ = require_env()
    app_base = controller.removesuffix("/api/v1")
    return f"{app_base}/workspace/{workspace}/agents/agent-builder/{agent_id}"
