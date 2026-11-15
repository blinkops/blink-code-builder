---
name: blink-code-harness
description: How the Blink skills on the remote MCP server map to this Claude Code plugin — where the action catalog, the workspace lists and the local workflow, agent and dashboard files are, how to search the catalog, what each Blink tool takes here, what the plugin hooks do, how to ask the user and get approvals, links, sign-in and search scope. Load it together with the first Blink skill of a session, before any Blink work.
---

# The blink-code-builder harness (Claude Code)

The Blink skills on the MCP server are the same for every host. They say "the action catalog",
"the workspace lists", "the local workflow file", "ask the user" and "get the user's approval".
This skill says what each of those means in this plugin. Where the two differ, this skill wins
for harness facts; the MCP skill wins for Blink facts.

## The Blink tools

The remote Blink MCP server `blink-code-builder` gives the tools as
`mcp__blink-code-builder__<tool>`. The skills name them by `<tool>` only.

- **The workspace.** Each session works in one Blink workspace: the default in the plugin config,
  or the one the user picks. Pass its id as `workspace_id` to every tool that takes one: the
  session start note and the picker result give the id. A hook denies any other id, and its
  denial gives the right one.
- **The picker.** If no workspace is chosen yet, before your first Blink tool call, call
  `list_workspaces` with `choose=true`. Claude Code shows the user a picker, and the hook saves the
  user's choice. Also call it when the user asks for another workspace ("switch to prod", "copy
  this workflow to SOC-Prod"). Before a switch, tell the user which changes are not saved yet.
  After the switch, every tool call goes to the new workspace until the user picks again.
- If `list_workspaces` with `choose=true` says "No workspace was chosen", ask the user with
  AskUserQuestion as the result says (header "Workspace", labels "<name> · <role> · <first 8 of the
  id>"). A hook saves the user's answer as the workspace. Never choose the workspace yourself.
- `list_workspaces` without `choose` lists the user's workspaces (`id`, `name`, `role`, `description`), for
  questions such as "which workspaces do I have?". The hook saves the list as `workspace/workspaces.tsv`.
  The list does not change the workspace. To find a workspace by name, read `workspace/workspaces.tsv`.
- If a tool fails because the server isn't connected or the user isn't signed in, tell the user:
  run `/mcp`, select `blink-code-builder`, choose Authenticate and sign in to Blink. If the Blink
  URL is missing, run the `setup-blink-plugin` skill.
- There is no API key. Never call the Blink API with curl or httpx, and never read `.env` files.

## Where things are

| Skill term | In this plugin |
|---|---|
| The action catalog | `${CLAUDE_PLUGIN_DATA}/catalog/` — a per-user cache, the same for every workspace |
| Action index | `${CLAUDE_PLUGIN_DATA}/catalog/actions.tsv` — `full_name` ⇥ service ⇥ description ⇥ `connection_types` |
| Trigger index | `${CLAUDE_PLUGIN_DATA}/catalog/triggers.tsv` — same shape |
| Action / trigger detail entry | `${CLAUDE_PLUGIN_DATA}/catalog/actions/<service>/<name>.json`, `.../triggers/<service>/<name>.json` (built-in triggers without a service are under `_misc/`) |
| Workspace list | `workspace/workspaces.tsv` — `id` ⇥ `name` ⇥ `role` ⇥ `description`: the workspaces of the user. A hook writes it after every `list_workspaces` call. |
| Workspace folder | `workspace/<name>--<first 8 of the id>/` — one folder for each workspace; the hooks name it. All the paths below are in it. |
| Workflow list | `workflows/workflows-list.tsv` — `id` ⇥ `name` ⇥ `automation_type` ⇥ `state` ⇥ `active` ⇥ `pack_name` |
| Agent list | `agents/agents-list.tsv` — `id` ⇥ `name` (with ` \| <title>` when the agent has one) ⇥ `state` ⇥ `pack_name` |
| Connection list | `connections/connections.tsv` — `name` ⇥ `type_name` |
| Table schemas | `tables/tables-schema.yaml` — every table's columns, no rows |
| Dashboard list | `dashboards/dashboards-list.tsv` — `id` ⇥ `display_name` ⇥ `published_to_portal` ⇥ `categories` |
| Local workflow file | `workflows/<name, lowercase, dashes>.yaml` |
| Local agent file | `agents/<name, lowercase, dashes>.yaml` |
| Local dashboard file | `dashboards/<display name, lowercase, dashes>.yaml` |
| Scratch draft | `/tmp/<name>.yaml` |

The workspace folder is relative to the user's project (the working directory). Its files are repo
files: the user reviews your changes with `git diff`, so don't echo YAML bodies in chat.

## Searching the catalog

Use the Grep tool on the TSV indexes, and `connection_types` (column 4) to pre-filter.

```
Grep pattern="send message" path="${CLAUDE_PLUGIN_DATA}/catalog/actions.tsv"
Grep pattern="^array\." path="${CLAUDE_PLUGIN_DATA}/catalog/actions.tsv"     # by service prefix
Grep pattern="OnNewPullRequest" path="${CLAUDE_PLUGIN_DATA}/catalog/triggers.tsv"
```

Read several detail entries in one Bash call — `tail -n +1` prints each file with a
`==> <path> <==` header:

```bash
cd "${CLAUDE_PLUGIN_DATA}/catalog" && tail -n +1 actions/slack/send_message.json actions/jira/CreateIssue.json
```

To find the built-ins, grep the index or list `actions/internal/` and `actions/core/`.
To find prior use of an action in the workspace: `grep -rn 'action: <full_name>' workspace/*/workflows/`.

The catalog and the workspace lists are written by hooks. Never edit them by hand.

## What the hooks do

A hook can't call an MCP tool, so some hooks ask you to call one and others process the result.

| When | Hook | What it does |
|---|---|---|
| Session start | `register_mcp.py` | Registers the MCP server that matches the user's Blink URL. |
| Session start | `refresh_catalog.py` | If the catalog is missing or 7+ days old, asks you to call `export_catalog`. |
| Session start | `refresh_workspace.py` | Names the session's workspace and asks you to call `get_workspace_resources`. With no workspace yet, asks you to show the picker before your first Blink tool call. |
| Before every Blink tool | `active_workspace.py check` | Denies the call if `workspace_id` is not the session's workspace, and gives the right id. With no workspace yet, it denies the call: show the picker. |
| After `list_workspaces` | `active_workspace.py list` | Saves the list as `workspace/workspaces.tsv`. After `choose=true`, it also saves the user's choice as the session's workspace and asks you to call `get_workspace_resources`. |
| Every user message | `check_setup.py` | Tells the user how to sign in, or asks you to fetch missing data first. |
| After `export_catalog` | `refresh_catalog.py --save` | Unzips the catalog to disk. You see a one-line status. |
| After `get_workspace_resources` | `refresh_workspace.py --save` | Writes the workspace lists and the table schemas. You see a one-line status. |
| After `fetch_automation` / `fetch_agent` | `save_fetched.py` | Writes the YAML to the local workflow or agent file. You get the path and the id, not the YAML. |
| After `fetch_dashboard` / `save_dashboard` | `save_fetched.py` | Writes the dashboard to the local dashboard file, with the new widget ids after a save. |
| Before `save_automation` | `safety_gate.py pre` | Denies the save when an action is not in the catalog (likely a typo) or a connection key is not one of the action's `connection_types`. The reason names the steps: fix them and save again. |
| After `save_automation` / `save_agent` | `safety_gate.py post` | Records the risks of the saved YAML. For an automation, adds the test-run readiness: the steps that still have no connection. |
| Before `trigger_test_run` | `safety_gate.py pre` | Makes Claude Code ask the user only for a risk the user has not approved in a test run of this session, or whose step (or its loop) changed, or when the YAML was not saved through this plugin. Otherwise it gives no decision and the user's permission mode decides. |
| After any publish | `refresh_workspace.py` | Asks you to call `get_workspace_resources`. |
| Stop | `refresh_workspace.py --stop` | After a publish, or a save that created a dashboard, you can't end the turn until you call `get_workspace_resources` . |

To force a catalog refresh, the user deletes the catalog folder and starts a new session.

## What each tool takes here

- **`save_automation`** — Read the scratch draft and pass its content as `yaml`; pass
  `automation_id` to update (from `fetch_automation`, an earlier save, or the workflow list), and
  optionally `pack` (a pack name; a new workflow goes in that pack, created if missing). Then copy the saved YAML to the local workflow file yourself — the hook
  writes that file only after a fetch.
- **`save_agent`** — pass the agent file's content as `yaml`, and `agent_id` to update.
- **`save_dashboard`** — pass the dashboard file's content as the `dashboard` object. The hook
  rewrites the file with the new ids.
- **`fetch_automation` / `fetch_agent` / `fetch_dashboard`** — the hook writes the local file and
  returns its path and id. Read the file.
- **`export_catalog` / `get_workspace_resources` / `list_workspaces`** — call them when a hook asks, or when the
  catalog or a list is missing or stale. They run without a permission prompt.

## Asking the user and approvals

- **Ask the user** with the AskUserQuestion tool when you offer choices (for example several
  matching connections, actions or Portal users: put the name and email in each option). Ask
  free-form questions in plain text.
- **Approvals.** Only `trigger_test_run` has a hook: Claude Code asks the user when the hook finds a
  new risk (see the hooks table). For every other tool that needs the user's approval in the MCP
  skills (`publish_automation`, `publish_agent`, `publish_dashboard` with `confirm=true`,
  `delete_table`, `edit_table` with `remove_fields`), no hook asks: tell the user in plain words what
  will happen, get their yes in the chat, then call the tool. If the user declines, don't retry — ask
  what they want instead.
- The question policy is the one in the MCP skills: ask when you aren't sure, and batch the
  questions.

## Links to Blink

Give the user a clickable link to each Blink object you create, change, publish or run.

- Workflows, agents and dashboards: copy the tool's `editor_url` exactly.
- A test run: `<workflow editor_url>?execution=<execution_id>`.
- A table: `<Blink URL>/workspace/<workspace id>/tables/<table id>` (`id` in `tables-schema.yaml`).
- A dashboard in the Portal: `<Blink URL>/portal/app/<dashboard id>`.

Take the Blink URL and the workspace id from the session-start hook, or from the picker result. Don't build any other link.

## Helpers and search scope

- Read and grep only inside the catalog cache, the user's project, and `/tmp/` for scratch
  drafts. Don't search elsewhere on the filesystem (`~/go/`, `~/.claude/`, sibling repos), even if
  they are in the user's PATH or Go module cache. If these sources can't answer a question, ask the
  user instead.
- Beyond the Blink tools, repos that use this plugin often have their own Blink API helpers
  (`scripts/`, `tools/`, `utils/`, or an index in the README or CLAUDE.md). Look there before you
  write one. If you must write a new helper, save it in that folder with a one-line docstring
  that names the API it wraps, and add it to the index.
