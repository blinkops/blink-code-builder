# blink-code-builder plugin — for Claude

You author Blink automations. A Blink automation is a YAML file (the controller calls it a "playbook") — a trigger plus a list of steps where each step invokes a Blink **action** by its `full_name` (e.g. `slack.send_message`, `aws.ec2_list_instances`, `playbooks.<subflow-name>`).

The **generating-workflow** skill owns the end-to-end flow: clarify → look up actions → draft → validate → promote → save. Read `skills/generating-workflow/SKILL.md` when the user asks for an automation.

## Catalog

Per-user cache, populated by a `SessionStart` hook that runs `hooks/refresh_catalog.py`. Location: `${CLAUDE_PLUGIN_DATA}/catalog/` (Claude Code injects `CLAUDE_PLUGIN_DATA` per plugin).

To force a refresh: delete the catalog dir and start a new Claude Code session.

Layout (see [skills/generating-workflow/reference/looking-up-actions.md](skills/generating-workflow/reference/looking-up-actions.md)):

If the catalog is missing when you need it, **stop and route the user through the `setup-blink-plugin` skill** — the hook couldn't populate it (usually means `userConfig` isn't set). Don't try to recover from inside another skill.

## Where things live

Two clearly separated locations, by whether the content is generic Blink capability or
this user's own workspace:

- **Vendor** (same for every workspace): the actions/triggers catalog, in
  `${CLAUDE_PLUGIN_DATA}/catalog/`.
- **Workspace** (this user's own workspace — workflows/subflows, agents, connections,
  tables): always in the project repo, under `workspace/`.

```
workspace/
├── workflows/    # index.tsv (callable subflows/templates), connections-allowlist.yaml, <name>.yaml bodies
├── agents/       # index.tsv (published agents), <name>.yaml bodies
├── connections/  # index.tsv (bound connections, list only)
└── tables/       # schema.yaml (list only)
```

### Terminology

- **Vendor action** — a catalog entry from `actions.tsv`, generic across workspaces.
- **Workflow** = **Automation** = **Playbook** — same object, different name depending on
  context. A **Subflow** is not a separate object: it's a published, active, on-demand
  workflow, callable as a step.
- **Agent** / **Table** — workspace-owned objects with their own lifecycle (see the
  `generating-agent` / `tables` skills).

## Search scope

Read and grep only inside:

- the vendor catalog cache (`${CLAUDE_PLUGIN_DATA}/catalog/`)
- this repo (the working directory), including its `workspace/` directory
- `/tmp/` for scratch drafts

Do **not** search elsewhere on the user's filesystem (`~/go/`, `~/.claude/`, sibling repos, etc.) for examples or schema hints, even if they appear in the user's PATH/Go module cache. If the in-scope sources can't answer a question, ask the user instead of grepping outward.

## First Blink interaction each session

If this looks like the first Blink-related turn this session (the catalog/workspace
snapshot just refreshed, and the user hasn't asked for anything Blink-specific yet),
offer a one-line inventory summary instead of waiting to be asked — e.g. counts of
workflows, agents, connections, and tables from the `workspace/` index files. Skip this
if the user already opened with a specific request; don't delay answering it just to
show the summary first.

## Secrets

The user's blink credentials live in `userConfig` and reach the `blink` MCP server via `CLAUDE_PLUGIN_OPTION_BLINK_*` env vars, injected natively into the server's process.
If an MCP tool call fails with `missing Blink config (...)`, that means the plugin's `userConfig` isn't set — route the user through the `setup-blink-plugin` skill.
