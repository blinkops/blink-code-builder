---
name: setup-blink-plugin
description: First-time install walkthrough for the blink-code-builder plugin — installs Python prerequisites if missing, walks the user through entering each userConfig value in Claude Code's plugin menu, and verifies the catalog populated. Use when the user asks how to install, set up, or configure the plugin, or when generating-workflow scripts fail because the BLINK_* env vars are missing or the catalog is empty.
allowed-tools: Read, Bash
user-invocable: false
---

# Setting up blink-code-builder

For non-developers on macOS using the Claude Code CLI. Do system-level prerequisite installs *for* the user; for plugin config, **never edit settings files yourself** — the user types every value into Claude Code's plugin menu. Never echo `CLAUDE_PLUGIN_OPTION_BLINK_*` values back to them.

## 1. Python prerequisites

The SessionStart hook installs `httpx`/`pyyaml` via `pip install --user --break-system-packages`. The flag was added in pip 23.0.1 (Feb 2023), and old pip rejects unrecognized flags — so the plugin requires **Python 3.11+** (which ships pip ≥ 23.1 by default). macOS Command Line Tools currently ships Python 3.9 + pip 21, which is too old; install via Homebrew instead.

Run in parallel:

- `python3 --version`
- `python3 -m pip --version`

**Python 3.11+ and pip 23+** → continue to step 2.

**Anything else (missing, too old, or pip too old)** → install via Homebrew:

1. Check `brew --version`.
2. If Homebrew is missing, tell the user you're about to install it: it's an open-source package manager, takes ~5–10 minutes, downloads several hundred MB, and **prompts for their login password during install**. Once they OK it, run:
   ```
   /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
   ```
   At the end the installer prints "Next steps" — run the two `eval ... brew shellenv` lines it shows so this session and future shells can find `brew` (typically `eval "$(/opt/homebrew/bin/brew shellenv)"` on Apple Silicon, `/usr/local/bin/brew shellenv` on Intel).
3. With `brew` available, run `brew install python`. This installs the current Python 3.x release with an up-to-date pip.
4. Re-verify: `python3 --version` should report 3.11+ and `python3 -m pip --version` should report 23+.

If install fails, surface the error and ask — don't loop or guess. After step 1 is clean, every subsequent invocation of `python3` (including from the SessionStart hook in step 3) will pick up the Homebrew install via PATH.

## 2. userConfig values

The plugin needs four values, all entered through Claude Code's plugin menu. **Don't write to `~/.claude/settings.json` yourself** — even for non-sensitive values like URL and workspace ID. The `/plugin` menu is the canonical config path; bypassing it isn't your call.

Walk the user through reaching the menu:

1. Type `/plugin` in their Claude Code session.
2. Open the **Installed** tab.
3. Select **blink-code-builder**.
4. Choose **Configure options**.

Then guide them field-by-field. If they already gave you values in their original message (e.g. their Blink URL), tell them what to type in the field — don't re-ask. They paste / type each value into the menu themselves; nothing goes through chat.

- **Blink URL** — Their Blink domain (whatever URL they sign in at, e.g. `https://blink.app.blinkops.com`). **Don't include `/api/v1`** — the plugin appends it.
- **Blink user API key** — Sensitive. Tell them to open `<their-blink-url>/user/profile/apikeys` in their browser, paste an existing key or click **Create**, then paste it into the **Blink user API key** field. Don't have them paste it into chat.
- **Blink workspace ID** — Visible in their browser URL inside Blink: in `https://<blink>/workspaces/<uuid>/...` the UUID after `/workspaces/` is the workspace ID. Tell them to copy that UUID into the field.
- **Blink pack ID** — Leave blank.

Claude Code does **not** re-prompt for missing required values automatically. If they skipped any at install time, this same `/plugin → Installed → Configure options` flow is how they fix it.

### Required permissions for the API key

The key inherits the permissions of the user who created it, and Blink permissions have **two layers** — the user needs the right role in *both*:

- **Company role** (account-wide): **Admin** — everything; **Builder** — can create workspaces and use the portal, but carries **no workflow permissions by itself**; **Consumer** / **Tenant Guest** — portal/view only, not usable for this plugin.
- **Workspace role** (per workspace): **Owner** / **Contributor** — view, edit, test-run, and publish workflows; **Viewer** — can list workflows but can't save or run anything.

For this plugin the user must be a member of the target workspace with **Contributor or above**. A common trap: a service user with only the company "Builder" role gets `403 Forbidden` even on listing workflows (`/automations`) — the fix is a Blink admin adding that user to the workspace, not a new key.

## 3. Restart the Claude Code session

This step fundamentally requires the user — Claude can't restart its own host process. The catalog only populates via the SessionStart hook, and config changes don't take effect mid-session. Tell the user to quit Claude Code and reopen it, then continue here in the new session.

## 4. Verify

In the new session:

- `ls "${CLAUDE_PLUGIN_DATA}/catalog/"` should list `actions/`, `triggers/`, `actions.tsv`, `triggers.tsv`.
- `wc -l "${CLAUDE_PLUGIN_DATA}/catalog/actions.tsv"` should report a few hundred to a few thousand lines.

To confirm the env vars reached the scripts, run a short Python check that prints only `set`/`unset` for each of `CLAUDE_PLUGIN_OPTION_BLINK_{CONTROLLER_URL,USER_API_KEY,WORKSPACE_ID,PACK_ID}`. **Never print the actual values.**

If the catalog dir is empty or has no `.tsv` files:

- Read `${CLAUDE_PLUGIN_DATA}/refresh.log` — the detached refresh process logs errors there.
- 401 → API key wrong or expired, back to step 2.
- 403 → key is valid but its user lacks a workspace role — see "Required permissions for the API key" in step 2.
- Connection refused / DNS error → URL wrong, back to step 2.
- No log at all → SessionStart hook didn't fire (config still missing, or restart didn't actually happen).

Separately, check `/mcp` for the `blink` server — the generating-workflow skill's tools (save,
validate, list connections, etc.) all run through it, distinct from the catalog hook above. If
it doesn't show as connected, run `/reload-plugins` and check again.

## 5. Smoke test

Ask the user for a small automation that doesn't need a vendor connection — e.g. *"a playbook that prints 'hello' using `internal.print`"*. If `generating-workflow` drafts, validates, and saves it, the plugin is set up.

## Rules

- Never echo or print `CLAUDE_PLUGIN_OPTION_BLINK_*` values, even partially. Confirm `set`/`unset` only.
- Never write plugin userConfig values to `~/.claude/settings.json` yourself — even non-sensitive ones. The user enters them via `/plugin Configure options`.
- Never ask the user to paste the API key into chat — only into Claude Code's `/plugin Configure options` menu.
- Don't suggest `.env` files or shell exports — `userConfig` set via the plugin menu is the only supported path.
