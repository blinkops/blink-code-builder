---
name: setup-blink-plugin
description: Sets up and repairs the blink-code-builder plugin — checks python3, the plugin config values, the Blink MCP server and sign-in (/mcp), the action catalog and the workspace lists, and fixes whatever is missing. Use when the user asks to install, set up or configure the plugin, when a Blink hook message says setup is incomplete, when the Blink tools are missing or fail to connect, when a hook fails with `python3: command not found`, or when the catalog or workspace lists are missing.
allowed-tools: Read, Bash, mcp__blink-code-builder__get_workspace_resources, mcp__blink-code-builder__export_catalog
user-invocable: false
---

# Setting up blink-code-builder

For non-developers on macOS using the Claude Code CLI. Do system-level prerequisite installs *for* the user; for plugin config, **never edit settings files yourself** — the user types every value into Claude Code's plugin menu. Never echo `CLAUDE_PLUGIN_OPTION_BLINK_*` values back to them.

## 1. Python prerequisites

The plugin's hooks are small Python scripts that use only the standard library, so the plugin needs a `python3` on PATH and nothing else — no pip installs. Run `python3 --version`: any 3.9+ works (macOS Command Line Tools' Python 3.9 is fine).

If `python3` is missing, install it with Homebrew (`brew install python`). If Homebrew is missing too, tell the user before you install it: it's an open-source package manager, takes ~5–10 minutes, and **prompts for their login password during install**. Once they OK it, run:
```
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```
then the `eval ... brew shellenv` lines it prints, then `brew install python`. If install fails, surface the error and ask — don't loop or guess.

## 2. userConfig values

The plugin needs three values, all entered through Claude Code's plugin menu. **Don't write to `~/.claude/settings.json` yourself** — even for non-sensitive values like URL and workspace ID. The `/plugin` menu is the canonical config path; bypassing it isn't your call.

Walk the user through reaching the menu:

1. Type `/plugin` in their Claude Code session.
2. Open the **Installed** tab.
3. Select **blink-code-builder**.
4. Choose **Configure options**.

Then guide them field-by-field. If they already gave you values in their original message (e.g. their Blink URL), tell them what to type in the field — don't re-ask. They paste / type each value into the menu themselves; nothing goes through chat.

- **Blink URL** — Their Blink domain (whatever URL they sign in at, e.g. `https://acme.app.blinkops.com`). It picks the Blink MCP server for their environment and names their tenant, so it must be their own tenant's URL.
- **Blink workspace ID** — Visible in their browser URL inside Blink: in `https://<blink>/workspaces/<uuid>/...` the UUID after `/workspaces/` is the workspace ID. Tell them to copy that UUID into the field.

Claude Code does **not** re-prompt for missing required values automatically. If they skipped any at install time, this same `/plugin → Installed → Configure options` flow is how they fix it.

### Required permissions

The plugin acts as the user who signs in through `/mcp`, and Blink permissions have **two layers** — the user needs the right role in *both*:

- **Company role** (account-wide): **Admin** — everything; **Builder** — can create workspaces and use the portal, but carries **no workflow permissions by itself**; **Consumer** / **Tenant Guest** — portal/view only, not usable for this plugin.
- **Workspace role** (per workspace): **Owner** / **Contributor** — view, edit, test-run, and publish workflows; **Viewer** — can list workflows but can't save or run anything.

For this plugin the user must be a member of the target workspace with **Contributor or above**. A common trap: a service user with only the company "Builder" role gets `403 Forbidden` even on listing workflows (`/automations`) — the fix is a Blink admin adding that user to the workspace.

## 3. Restart the Claude Code session

This step fundamentally requires the user — Claude can't restart its own host process. Config changes don't take effect mid-session, and the SessionStart hook is what asks for the catalog when it's missing. Tell the user to quit Claude Code and reopen it, then continue here in the new session.

On the first start with a new Blink URL, the plugin registers its MCP server for that URL and asks for one more restart. After that restart, have the user run `/mcp`, select **blink-code-builder** and sign in with their Blink login.

## 4. Check each item, and fix what is missing

Go through these in order. Fix an item before moving to the next one.

1. **python3** — `python3 --version` works (step 1). If a hook fails with `python3: command not found`, that's the cause: install it (step 1), then ask the user to restart Claude Code.
2. **Config** — only print `set`/`unset` for `CLAUDE_PLUGIN_OPTION_BLINK_{CONTROLLER_URL,WORKSPACE_ID}`, **never the values**. Anything required that's `unset` → step 2.
3. **MCP server** — check `/mcp`: `blink-code-builder` must be connected. Not listed → the plugin registers it at session start, so ask the user to restart Claude Code once (step 3). `needs authentication` → the user runs `/mcp`, selects **blink-code-builder**, chooses **Authenticate**, signs in with their Blink login, then sends any message.
4. **Workspace lists** — `workspace/workflows/workflows-list.tsv`, `workspace/agents/agents-list.tsv`, `workspace/connections/connections.tsv` and `workspace/tables/tables-schema.yaml` exist in the project. Missing → call `get_workspace_resources` with the configured workspace id and `include_tables=true`; a hook saves the files.
5. **Catalog** — `${CLAUDE_PLUGIN_DATA}/catalog/` has `actions/`, `triggers/`, `actions.tsv` and `triggers.tsv`, and `actions.tsv` has a few thousand lines. Missing → call `export_catalog` once; a hook saves it.

If a save hook replies with an `error:` line, show it to the user.

## 5. Smoke test

Ask the user for a small automation that doesn't need a vendor connection — e.g. *"a playbook that prints 'hello' using `internal.print`"*. If `generating-workflow` drafts and saves it, the plugin is set up.

## Rules

- Never echo or print `CLAUDE_PLUGIN_OPTION_BLINK_*` values, even partially. Confirm `set`/`unset` only.
- Never write plugin userConfig values to `~/.claude/settings.json` yourself — even non-sensitive ones. The user enters them via `/plugin Configure options`.
- Don't suggest `.env` files or shell exports — `userConfig` set via the plugin menu is the only supported path.
