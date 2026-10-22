#!/usr/bin/env python3
"""Connect the remote Blink MCP server that matches the user's Blink URL.

SessionStart hook. Each Blink environment has its own blinkmcp server on AgentCore, with
its own Cognito pool, and .mcp.json can't choose between them. So this hook splits the
configured Blink URL into tenant subdomain + environment domain
(acme.eu1.blinkops.com -> acme + eu1.blinkops.com), looks the domain up in ../mcp-servers.json,
and registers that environment's server in the user's Claude Code config (user scope) with
`claude mcp add-json`. A domain that isn't listed falls back to the file's default
environment. A server registered here connects from the next session on; the user signs in
once through /mcp.

Does nothing when the right server is already registered. Stdlib only, and never fails
the session: every problem is reported as a message instead.

Config: CLAUDE_PLUGIN_OPTION_BLINK_CONTROLLER_URL.
"""

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple
from urllib.parse import urlparse

import _common  # noqa: F401 — puts the plugin root on sys.path for blink_shared
from blink_shared.config import ENV_CONTROLLER

SERVER_NAME = "blink-code-builder"
MAPPING_PATH = Path(__file__).resolve().parent.parent / "mcp-servers.json"
OAUTH_CALLBACK_PORT = 8080
OAUTH_SCOPES = "openid email profile"
# trigger_test_run waits up to 150s for a run; the default MCP request timeout is 60s.
TOOL_TIMEOUT_MS = 300_000
# blinkmcp calls the tenant's own controller (https://<subdomain>.<domain>), so every request
# names the tenant. The runtime's requestHeaderAllowlist passes this header through.
SUBDOMAIN_HEADER = "Blink-Subdomain"


@dataclass(frozen=True)
class Environment:
    """One entry of mcp-servers.json."""
    name: str
    domain: str
    url: str
    client_id: str
    auth_server_metadata_url: str


class Route(NamedTuple):
    environment: Environment
    subdomain: str
    matched: bool  # False when the URL's domain isn't listed and the default environment is used


def blink_host(url: str) -> str:
    """The hostname of a Blink URL, with or without a scheme or path."""
    parsed = urlparse(url if "://" in url else f"https://{url}")
    return (parsed.hostname or "").lower()


def route(blink_url: str) -> Route:
    """Split the Blink URL's host into tenant subdomain + listed environment domain.

    The longest matching domain wins. A host on no listed domain gets the default
    environment and its first label as the subdomain.
    """
    host = blink_host(blink_url)
    mapping = json.loads(MAPPING_PATH.read_text())
    environments = [Environment(**entry) for entry in mapping["environments"]]
    for environment in sorted(environments, key=lambda env: len(env.domain), reverse=True):
        suffix = "." + environment.domain
        if host.endswith(suffix) and len(host) > len(suffix):
            return Route(environment, host[: -len(suffix)], matched=True)
    for environment in environments:
        if environment.name == mapping["default"]:
            return Route(environment, host.split(".")[0], matched=False)
    raise ValueError(f"the default environment {mapping['default']!r} isn't listed")


def server_config(target: Route) -> dict:
    return {
        "type": "http",
        "url": target.environment.url,
        "headers": {SUBDOMAIN_HEADER: target.subdomain},
        "oauth": {
            "clientId": target.environment.client_id,
            "callbackPort": OAUTH_CALLBACK_PORT,
            "authServerMetadataUrl": target.environment.auth_server_metadata_url,
            "scopes": OAUTH_SCOPES,
        },
        "timeout": TOOL_TIMEOUT_MS,
    }


def registered_config():
    """This plugin's server as it is registered at user scope now, or None."""
    config_dir = os.environ.get("CLAUDE_CONFIG_DIR")
    path = Path(config_dir) / ".claude.json" if config_dir else Path.home() / ".claude.json"
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    return (data.get("mcpServers") or {}).get(SERVER_NAME)


def is_current(registered, wanted):
    # Compare only what decides the target: Claude Code may store the entry with fields
    # added or reordered, and a full comparison would re-register on every session.
    return (
        registered is not None
        and registered.get("url") == wanted["url"]
        and (registered.get("oauth") or {}).get("clientId") == wanted["oauth"]["clientId"]
        and registered.get("headers") == wanted["headers"]
    )


def register(config):
    claude = shutil.which("claude")
    if not claude:
        raise RuntimeError("the `claude` command is not on PATH")
    # add-json refuses an existing name, so drop the old entry first (a no-op if absent).
    subprocess.run([claude, "mcp", "remove", SERVER_NAME, "-s", "user"], capture_output=True, timeout=30)
    result = subprocess.run(
        [claude, "mcp", "add-json", SERVER_NAME, json.dumps(config), "-s", "user"],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout).strip())


def report(message):
    """Show the message to the user and give it to Claude as context."""
    print(json.dumps({
        "systemMessage": message,
        "hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": (
            message + " The Blink MCP server isn't connected in this session, so skip the other Blink "
            "instructions (catalog, workspace lists) until the next session, and if the user asks about "
            "Blink, tell them to restart Claude Code.")},
    }))


def main():
    blink_url = os.environ.get(ENV_CONTROLLER, "")
    if not blink_url:
        return  # not configured yet: the setup-blink-plugin skill handles that

    host = blink_host(blink_url)
    target = route(blink_url)
    name = target.environment.name
    # An unlisted domain still gets a server (the default one); say so, since sign-in fails if it's wrong.
    fallback = "" if target.matched else (
        f" {host} isn't a known Blink domain, so the default server ({name}) is used. If sign-in fails, "
        "check the Blink URL in the plugin options (/plugin > blink-code-builder > Configure options)."
    )
    wanted = server_config(target)
    if is_current(registered_config(), wanted):
        return

    try:
        register(wanted)
    except Exception as exc:
        report(f"Blink MCP: could not add the server for {name}: {exc}")
        return
    report(f"Blink MCP: added the server for {name} ({host}). Start a new session, then run /mcp and "
           f"sign in to Blink.{fallback}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        report(f"Blink MCP: setup failed: {exc}")
