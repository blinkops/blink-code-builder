# blink-code-builder plugin — for Claude

You author Blink automations. A Blink automation is a YAML file (the controller calls it a "playbook") — a trigger plus a list of steps where each step invokes a Blink **action** by its `full_name` (e.g. `slack.send_message`, `aws.ec2_list_instances`, `playbooks.<subflow-name>`).

The **generating-workflow** skill owns the end-to-end flow: clarify → look up actions → draft → validate → promote → save. Read `skills/generating-workflow/SKILL.md` when the user asks for an automation.

## Catalog

Blink-wide reference data, not tied to any one workspace: the full set of vendor actions
(e.g. `slack.send_message`, `aws.ec2_list_instances`) you can use as workflow steps, along
with their input/output schemas. It's a per-user cache, fetched through the remote MCP tool `export_catalog` (the catalog
gzipped): when the catalog is missing or 7+ days old, the `SessionStart` hook
(`hooks/refresh_catalog.py`) asks you to call it, and the same script, as a `PostToolUse`
hook, unzips the result and writes it to disk. Location: `${CLAUDE_PLUGIN_DATA}/catalog/` (Claude Code
injects `CLAUDE_PLUGIN_DATA` per plugin).

To force a refresh: delete the catalog dir and start a new Claude Code session.

For the layout and how to grep it, see [skills/generating-workflow/reference/looking-up-actions.md](skills/generating-workflow/reference/looking-up-actions.md).

If the catalog is missing when you need it, call `export_catalog` once (a hook saves it to disk). If that tool is missing or fails, stop and tell the user to run `/mcp` and sign in to Blink.

## Workspace data lives in this repo, not the catalog

Workflows, agents, connections, tables, and dashboards belong to a specific workspace, so they are kept as
files in this repo — never in the per-user catalog cache above. Grep these files directly, and
regenerate them on demand with the tool named for each. Never source workspace data from the
catalog.

### Workflows

`workspace/workflows/workflows-list.tsv` lists every workflow (automation/playbook) in the workspace, drafts
included — id, name, `automation_type`, state (`draft`/`published`/`modified`), and `active`.
It's a repo file, written from the remote MCP tool `get_workspace_resources`: the `SessionStart`
hook asks you to call it, and so does a hook after `publish_automation` / `publish_agent`. A
`PostToolUse` hook (`hooks/refresh_workspace.py --save`) writes the files. Grep it first; call
`get_workspace_resources` again if it looks stale (a workflow was just published elsewhere mid-session).

A workflow is callable as a **subflow** (as a step, or as an agent's ability) only when its row
shows `automation_type: on_demand`, `active: true`, and state `published` or `modified` — the
draft alone is never callable. See the `subflows` skill for the full lifecycle, and the
`generating-workflow` skill for authoring one.

Each workflow that's been fetched also has its own YAML file: a `PostToolUse` hook
(`hooks/save_fetched.py`) writes the `fetch_automation` result to `workspace/workflows/<name>.yaml`
and gives you the path and id instead of the YAML. Save it back with `save_automation` and that id.

### Agents

`workspace/agents/agents-list.tsv` lists every agent in the workspace, drafts included — id,
name, and state (`draft`/`published`/`modified`). A step calls an agent as `agents.<id>`.
It's a repo file, written from `get_workspace_resources` like the workflows list. Grep it
first; call `get_workspace_resources` again if it looks stale. An agent is callable as a workflow step only
once its row is `published` or `modified` — a draft is never callable. See the
`generating-agent` skill.

The call goes both ways: an agent gets a workflow as an **ability**, so a workflow can call an
agent as a step and an agent can call a workflow as one of its abilities.

Each agent that's been fetched also has its own YAML file under `workspace/agents/` — a full export of
that agent's config, written by the same hook from `fetch_agent` (or authored by hand) and saved with `save_agent`.

### Connections

`workspace/connections/connections.tsv` lists every connection bound in the workspace (name, type).
It's written from `get_workspace_resources`, together with the workflow and agent lists.

### Tables

`workspace/tables/tables-schema.yaml` is a single YAML file listing every table in the workspace together
with its schema (columns, types, etc.) — it does **not** contain the tables' data (records).
Written from `get_workspace_resources` together with the other lists at session start; call it
again after `create_table`/`edit_table`/`delete_table`. See the **managing-tables** skill to create or
edit a table's structure directly in Blink, and the **tables** skill to connect a table to a
workflow (read/write rows from inside a workflow step).

### Dashboards

`workspace/dashboards/dashboards-list.tsv` lists every dashboard in the workspace — id, display
name, whether it's published to the Portal, and categories. It's written from
`get_workspace_resources`, together with the workflow and agent lists. After a save that creates a
dashboard, and after any publish, a `Stop` hook doesn't let you end the turn until you call
`get_workspace_resources` with `include_tables=false`.

Each dashboard that's been fetched or saved has its own YAML file at
`workspace/dashboards/<name>.yaml`: the `save_fetched.py` hook writes it after `fetch_dashboard`
and again after `save_dashboard` (with the new widget ids). Save it back by passing the file's
content to `save_dashboard`. A dashboard has no draft: a save is live at once. The plugin never deletes a
dashboard or a widget. `publish_dashboard` always makes Claude Code ask the user first (a
`PreToolUse` hook). See the **managing-dashboards** skill.

## Safety and checks (plugin hooks)

The Blink server does what it is asked; approvals are this plugin's job (`hooks/safety_gate.py`):

- `save_automation` is denied when an action is not in the local catalog, or a step's
  connection key is not one of its action's `connection_types` — the reason names the steps;
  fix them and save again. The controller's own errors come back from the tool. After a save,
  a hook adds the test-run readiness: the steps that still have no connection.
- `trigger_test_run`, `publish_automation`, `publish_agent`, `delete_table`, and `edit_table`
  with `remove_fields` make Claude Code ask the user, showing the risks found in the YAML at
  save time and, for publish, whether the saved draft passed a test run.

Before such a call, tell the user in plain words what will happen. If they decline, don't retry.

## Links to Blink

Give the user a clickable link to each Blink object you create, change, publish or run.

- **Workflows, agents and dashboards:** the tools return `editor_url`. Copy it exactly.
- **Everything else:** build the link from the Blink URL and workspace id that the session-start
  hook gave you. Use only these patterns:

| Object | Link |
|---|---|
| Workflow test run | `<workflow editor_url>?execution=<execution_id>` |
| Table | `<Blink URL>/workspace/<workspace id>/tables/<table id>` (the `id` in `tables-schema.yaml`) |
| Dashboard in the Portal | `<Blink URL>/portal/app/<dashboard id>` |

Don't build a link for any other object, and don't guess a path.

## Search scope

Read and grep only inside:

- the catalog cache (`${CLAUDE_PLUGIN_DATA}/catalog/`)
- this repo (the working directory)
- `/tmp/` for scratch drafts

Do **not** search elsewhere on the user's filesystem (`~/go/`, `~/.claude/`, sibling repos, etc.) for examples or schema hints, even if they appear in the user's PATH/Go module cache. If the in-scope sources can't answer a question, ask the user instead of grepping outward.

## Secrets

There is no API key. Every Blink call goes through the remote Blink MCP server (`blink-code-builder`), signed in with the user's own Blink login through `/mcp`; the plugin itself never calls the Blink API. `userConfig` holds only the Blink URL (which picks the MCP server and names the tenant) and the workspace id.
If a Blink tool fails because the server isn't connected or the user isn't signed in, tell them to run `/mcp` and sign in; if the Blink URL or workspace id is missing, route them through the `setup-blink-plugin` skill.
