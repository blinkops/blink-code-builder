"""Code shared by the plugin's two entry points: hooks/ and servers/blink/.

Those two are separate programs — a hook is a short-lived script, the MCP server is a
long-running process — so neither may import from the other. Whatever both need lives
here, and both depend on it downward:

    hooks/        servers/blink/
        \\            /
         blink_shared/

Importing it requires the plugin root on the import path. Each entry point arranges that
once at startup: the server gets it from PYTHONPATH in ../.mcp.json (and runs as
`python3 -m servers.blink.server`); the hooks still set it in hooks/_common.py, because
Claude Code invokes hook scripts by path.

`deps` and `config` import stdlib only, so they work before the third-party packages are
installed. `client` and `connections` need httpx. That is why nothing is re-exported
here — `import blink_shared.config` must not drag httpx in with it.
"""
