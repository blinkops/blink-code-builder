# blink-code-builder plugin — for Claude

You author Blink automations. A Blink automation is a YAML file (the controller calls it a "playbook") — a trigger plus a list of steps where each step invokes a Blink **action** by its `full_name` (e.g. `slack.send_message`, `aws.ec2_list_instances`, `playbooks.<subflow-name>`).

The **generating-workflow** skill owns the end-to-end flow: clarify → look up actions → draft → validate → promote → save. Read `skills/generating-workflow/SKILL.md` when the user asks for an automation.

## Agents and tables live in this repo, not the catalog

`agents/` holds `agents-list.tsv` (every agent, id/name/title/state — written by `list_agents`)
plus one YAML per agent (written by `fetch_agent`/authored by hand, saved by `save_agent`).
`tables/tables-schema.yaml` is the equivalent snapshot for Tables. Both are repo files you read
directly and regenerate on demand — never sourced from the catalog.

## Catalog

Per-user cache, populated by a `SessionStart` hook that runs `hooks/refresh_catalog.py`. Location: `${CLAUDE_PLUGIN_DATA}/catalog/` (Claude Code injects `CLAUDE_PLUGIN_DATA` per plugin).

To force a refresh: delete the catalog dir and start a new Claude Code session.

Layout (see [skills/generating-workflow/reference/looking-up-actions.md](skills/generating-workflow/reference/looking-up-actions.md)):

If the catalog is missing when you need it, **stop and route the user through the `setup-blink-plugin` skill** — the hook couldn't populate it (usually means `userConfig` isn't set). Don't try to recover from inside another skill.

## Search scope

Read and grep only inside:

- the catalog cache (`${CLAUDE_PLUGIN_DATA}/catalog/`)
- this repo (the working directory)
- `/tmp/` for scratch drafts

Do **not** search elsewhere on the user's filesystem (`~/go/`, `~/.claude/`, sibling repos, etc.) for examples or schema hints, even if they appear in the user's PATH/Go module cache. If the in-scope sources can't answer a question, ask the user instead of grepping outward.

## Secrets

The user's blink credentials live in `userConfig` and reach the `blink` MCP server via `CLAUDE_PLUGIN_OPTION_BLINK_*` env vars, injected natively into the server's process.
If an MCP tool call fails with `missing Blink config (...)`, that means the plugin's `userConfig` isn't set — route the user through the `setup-blink-plugin` skill.
