"""Start the plugin the way Claude Code does, on whatever OS this runs on.

Runs each hook command from hooks.json through bash (Git Bash on Windows) and starts the
MCP server from .mcp.json by direct spawn, then checks it answers `tools/list`. No Blink
credentials needed: hooks must skip quietly, and the server lists tools without calling Blink.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[2] / "plugins" / "blink-code-builder"
# Claude Code substitutes plugin paths with forward slashes on Windows.
PLUGIN_ROOT = PLUGIN.as_posix()
DATA = Path(tempfile.mkdtemp()).as_posix()
FAKE_CONFIG = {
    "CLAUDE_PLUGIN_OPTION_BLINK_CONTROLLER_URL": "http://127.0.0.1:9",
    "CLAUDE_PLUGIN_OPTION_BLINK_USER_API_KEY": "fake",
    "CLAUDE_PLUGIN_OPTION_BLINK_WORKSPACE_ID": "00000000-0000-0000-0000-000000000000",
}


def hook_commands():
    hooks = json.loads((PLUGIN / "hooks" / "hooks.json").read_text())["hooks"]
    for groups in hooks.values():
        for group in groups:
            for hook in group["hooks"]:
                yield hook["command"]


def run_hooks(extra_env):
    env = {**os.environ, "CLAUDE_PLUGIN_ROOT": PLUGIN_ROOT, "CLAUDE_PLUGIN_DATA": DATA, **extra_env}
    for command in set(hook_commands()):
        result = subprocess.run(["bash", "-c", command], env=env, cwd=DATA, capture_output=True, text=True, timeout=300)
        print(f"hook exit {result.returncode}: {command}\n{result.stderr}")
        assert result.returncode == 0, f"hook failed: {command}"


def check_detached_refresh():
    """With (fake) config the catalog hook forks a child; it must run and log on its own."""
    log = Path(DATA) / "refresh.log"
    for _ in range(120):
        if log.exists() and "fetching from" in log.read_text():
            print("detached catalog refresh ran:\n" + log.read_text())
            return
        time.sleep(1)
    raise AssertionError("detached catalog refresh never wrote refresh.log")


def run_mcp_server():
    server = json.loads((PLUGIN / ".mcp.json").read_text())["mcpServers"]["blink"]

    def substitute(value):
        value = value.replace("${CLAUDE_PLUGIN_ROOT}", PLUGIN_ROOT)
        return re.sub(r"\$\{user_config\.(\w+)\}", "x", value)

    command = shutil.which(server["command"])
    assert command, f"`{server['command']}` is not on PATH — the MCP server could not start"
    env = {**os.environ, **{k: substitute(v) for k, v in server["env"].items()}, "CLAUDE_PLUGIN_DATA": DATA}
    proc = subprocess.Popen(
        [command, *map(substitute, server["args"])], env=env,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, encoding="utf-8", cwd=DATA,
    )
    messages = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2024-11-05", "capabilities": {},
            "clientInfo": {"name": "smoke", "version": "0"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    ]
    try:
        for message in messages:
            proc.stdin.write(json.dumps(message) + "\n")
            proc.stdin.flush()
            if "id" in message:
                reply = json.loads(proc.stdout.readline())
                assert "result" in reply, reply
        tools = [tool["name"] for tool in reply["result"]["tools"]]
        print(f"MCP server listed {len(tools)} tools")
        assert "validate_automation" in tools
    finally:
        proc.kill()


if __name__ == "__main__":
    print(f"platform: {sys.platform}")
    run_hooks({})
    run_hooks(FAKE_CONFIG)
    check_detached_refresh()
    run_mcp_server()
    print("ok")
