---
name: generating-agent
description: Authors, edits and publishes a Blink agent as YAML — its role, constraints and abilities — and connects agents and workflows to each other. Use when the user asks to create, build, configure, edit or publish a Blink agent, attach a workflow as an agent ability, or call an agent from a workflow.
allowed-tools: Read, Write, Edit, Glob, Grep, mcp__blink-code-builder__get_workspace_resources, mcp__blink-code-builder__fetch_agent, mcp__blink-code-builder__save_agent, mcp__blink-code-builder__publish_agent, mcp__blink-code-builder__fetch_automation, mcp__blink-code-builder__save_automation, mcp__blink-code-builder__trigger_test_run, mcp__blink-code-builder__get_run_log, mcp__blink-code-builder__publish_automation
user-invocable: false
---

# Generating Blink agents

A Blink agent takes a goal in plain language and decides which of the workspace's published
workflows to run to reach it — instead of you wiring the branches by hand. The workflows you
attach are its **abilities**.

Besides those abilities, the agent always has a **direct-answer** tool (answers from general
knowledge and ends the run, executing nothing), and it gets a **knowledge-base search** tool if
it has knowledge files. With `modes.code_execution_enabled` on it can also **author brand-new
workflows at runtime** and run them. None of these live in `abilities`, so don't assume an
agent is limited to the abilities you gave it.

## The one rule

Agents and workflows reference each other in **both** directions, and both are gated on publish:

| Direction | Reference | Created by |
|---|---|---|
| Workflow **is an ability of** an agent | `abilities[].ability_id: <playbook-uuid>` | publishing the **workflow** |
| Agent **is a step in** a workflow | `action: agents.<agent-uuid>` | publishing the **agent** |

> **Publish the callee before the caller references it.**

An ability can only be one of **this workspace's** published, active, on-demand workflows —
never a vendor action, never another workspace's workflow. So *"give the agent the ability to
post to Slack"* means: build a small on-demand workflow that posts to Slack, publish it, then
attach it.

## Where everything lives

```
Your repo                                            Blink workspace
─────────────────────────                            ──────────────────────
workspace/agents/                                     name, title, pack
  alert-triage.yaml    ◄──────────────────────────►   role, abilities…
  agents-list.tsv          id, name, state             draft  ← save_agent
                            (get_workspace_resources; drafts)  published ← publish_agent
workspace/workflows/
  workflows-list.tsv       id, name, automation_type,
                            state, active (get_workspace_resources)
                                                       agents.<id> row created on publish
workspace/connections/
  connections.tsv          name, type_name
                            (refreshed: session start, after every publish)

  you edit ──► self-check ──► save_agent ──► publish_agent ──► callable from a workflow
                 (local)       (draft)        (live; then refresh the lists)
```

## Configuration

One file per agent in `workspace/agents/`. Only `name` and `role` are required.

| Key | Meaning |
|---|---|
| `name` | Identity — keep it unique in the workspace. `save_agent` does **not** match on it: the `agent_id` you pass decides which agent is updated, and no id means a new agent. |
| `title` | Short label shown beside the name in the UI and in step pickers. |
| `description` | One sentence — what this agent is for. Callers see it. |
| `role` | **The agent's system prompt.** The highest-leverage field by far: it decides which ability the agent picks, and when. See [reference/configuration.md](reference/configuration.md). |
| `constraints` | Hard limits the agent must respect while choosing. |
| `abilities` | The workflows it may run. Each is `{ability_id, type, auto_approved, summarize_output, summarize_output_instructions}`. |
| `auto_approved` | **Safety-critical.** `false` (default) makes every run of that ability wait for a human. `true` removes the pause permanently. |
| `modes.chat_enabled` | UI label **Interactive mode** — users can chat with the agent. |
| `modes.code_execution_enabled` | UI label **Agent generated workflows** — the agent may author brand-new workflows at runtime and run them. Default `false`; leave it off unless asked. |
| `knowledge`, `avatar_*` | Managed in the Blink UI. `fetch_agent` preserves them and `save_agent` never removes them, but this plugin cannot create them. Ask the user to upload knowledge files in the agent builder. |

**Task mode** is always on and has no field — that is what lets a workflow step call the agent.

```yaml
name: Alert Triage Agent
title: SOC Triage
description: Triages inbound alerts and enriches observables.
role: |
  Triage inbound security alerts.
  - When an alert names an external IP or domain, run "Enrich Observable" first.
  - Decide malicious / benign / needs-human from the enrichment result.
  - If no ability covers what was asked, say so instead of using a near-miss one.
constraints: |
  Never act on production hosts. Stay within the observables on the alert.
modes:
  chat_enabled: true                 # Interactive mode
  code_execution_enabled: false      # Agent generated workflows
abilities:
  - ability_id: 3f2a…                # a published on-demand workflow, copied from workflows-list.tsv
    auto_approved: false
```

## Finding an agent

Grep `workspace/agents/agents-list.tsv` (in the repo, not the catalog) — every agent in the workspace,
drafts included, each with its state: `draft` (never published), `published` (live, draft
matches it), `modified` (live, but the draft has newer edits that are not published yet).

If the grep misses or the file might be stale (missing, old, or an agent was just
saved/published elsewhere), call `get_workspace_resources` again before telling the user an agent doesn't exist.

## Creating an agent

1. **Decide the abilities first.** Grep `workspace/workflows/workflows-list.tsv` for rows with
   `automation_type=on_demand`, `state=published`/`modified`, and `active=true` — those are
   the workspace's callable workflows and the only eligible abilities. The `id` column is
   the bare uuid, which is exactly what `ability_id` takes.
2. **Any ability that doesn't exist yet?** Author and publish that workflow first with the
   `generating-workflow` skill — full loop, including its own test run. It is not attachable
   until it is published.
3. **Write `workspace/agents/<name>.yaml`.** Spend the effort on `role`; read
   [reference/configuration.md](reference/configuration.md) before writing it.
4. **Self-check before saving.** Every `ability_id` is the `id` of a callable row from step 1;
   no `type: action`; no `ability_id` listed twice; `modes` lists both keys; `auto_approved:
   true` and `code_execution_enabled: true` only where the user said yes. Tell the user about
   anything you couldn't resolve rather than silently fixing it.
5. **Check the name is free.** Grep `workspace/agents/agents-list.tsv` for the `name:`. If an
   agent with that name already exists, don't create a second one. Tell the user which agent
   you found, then:
   - they meant to edit that agent → follow **Editing an agent** (fetch it, save with its `agent_id`);
   - they wanted a separate agent → change `name:` in the YAML.
6. **`save_agent`** with `yaml` = the file's content (Read it) and no `agent_id` — that creates
   the agent. It returns `{agent_id}`: report that id and keep it, every later save and publish
   of this agent takes it. Don't echo the YAML; the user reviews with `git diff`.

   If it fails because abilities "are not runnable", those ids aren't published, active,
   on-demand workflows of this workspace — see **Edge cases**. Fix and re-save.
7. **Offer to publish — never publish on your own initiative.** Say in plain language what
   the agent will do, which abilities it can run, who they affect, and the risks (any
   auto-approved ability, code execution). Then `publish_agent` with `agent_id`. Claude Code
   shows the user an approval prompt with those risks; if they decline, don't retry — ask what
   they want instead. Until it is published the agent is a draft: not callable from any workflow.
   After a publish, call `get_workspace_resources` again so `agents-list.tsv` shows it.

## Agent and workflow together

When the user wants both, the order follows the one rule — whichever is the **callee** goes first.

- **Workflow is the agent's ability** → build and publish the workflow, then the agent.
- **Agent is a step in the workflow** → build and publish the agent, then the workflow.

Both at once (an agent with abilities, called from a workflow): publish the ability workflows,
then the agent, then the calling workflow. Say which order you're taking and why in one line.

## Calling an agent from a workflow

```yaml
- id: S3
  action: agents.<agent-uuid>        # agents.<id>, from the `id` column of a published/modified row in agents-list.tsv
  name: Triage the alert
  inputs:
    task: "Triage this alert and return a verdict"
    output_schema: |                 # optional — only when you need structured output
      {"verdict": "malicious", "confidence": "80%"}
```

- Always copy the `action` string from `workspace/agents/agents-list.tsv`. Never assemble it by hand,
  never call an agent by name.
- Output is at `{{ steps.S3.output.agent_output }}`.
- Optional advanced inputs: `roles_and_constraints` (extra instructions for this run only —
  appended to the agent's own role, so a step can narrow an agent without editing it),
  `timeout` (minutes, default 30), `run_draft` (run the agent's draft while editing — testing
  only, remove before handing over), `continue_last_session`, `allow_extra_verbose_answer`.
- No `connections:` block — the agent's abilities carry their own.

## Editing an agent

1. **Get its id** from the `id` column of `workspace/agents/agents-list.tsv` (or from an
   agent-builder URL the user gave you — the uuid in it). Don't have it? See **Finding an agent** above.
2. **Already in `workspace/agents/`?** Read it. **Lives only in Blink?** `fetch_agent` with
   `agent_id` — a hook writes its YAML to `workspace/agents/<name>.yaml` and returns that path and the id.
3. Edit that file in place. Same filename, same `name:`. Never create `_v2.yaml`. Keep every
   field: a save replaces the agent's draft, so a field left out of the YAML (`role`,
   `abilities`, `modes`, …) is cleared, except the few the server keeps (`title`,
   `description`, `constraints`, `knowledge`, …). Start from a fresh `fetch_agent` if the
   local file may be old.
4. Self-check (Creating step 4), then `save_agent` with `yaml` and **the `agent_id`**. Without
   the id it creates a second agent instead of updating this one.
5. Offer to publish, same as Creating step 7. **Say what changes for existing callers**: changing `role` or
   `abilities` on a published agent changes behavior for every workflow step and chat session
   already using it, and those callers are not visible from here.

## Edge cases

| Situation | What happens | What you do |
|---|---|---|
| Ability's workflow exists here but isn't published (or isn't active / on-demand) | `save_agent` fails: abilities … are not runnable | Publish (or activate) the workflow, then re-save. Not attachable until then. |
| Ability doesn't exist in this workspace (imported agent, deleted workflow) | `save_agent` fails the same way | Tell the user, name the ability, and ask: rebuild and publish that workflow, or remove the ability. **Never delete the line on your own to get the save through** — that strips a capability with no trace. |
| `type: action` on an ability | Caught by your self-check; `save_agent` rejects it anyway (its id is not a workflow) | Abilities are workflows only. Wrap the vendor action in a small on-demand workflow, publish it, attach that. |
| Same `ability_id` listed twice | Nothing rejects it — your self-check is the only catch | Keep one entry. Two with different `auto_approved` make the agent pick unpredictably. |
| An ability has `auto_approved: true`, or `modes.code_execution_enabled` is on | The approval prompt at `publish_agent` lists them | Before publishing, describe each one and who it affects in plain words and ask a direct yes/no. A yes covers this publish only. |
| An agent with this `name:` already exists | Nothing stops a second one: `save_agent` without `agent_id` always creates a new agent | Check `agents-list.tsv` before creating. Either edit that agent (`fetch_agent`, save with its `agent_id`), or rename in the YAML (separate agent). |
| An ability's workflow itself calls an agent | nothing local | The controller enforces the depth limit of 5 at run time and errors if exceeded, so a cycle stops itself. Mention it if the user is designing deep chains. |
| User names an agent that isn't in `agents-list.tsv`, or the file looks stale | It's probably a draft, or the file wasn't re-synced yet | Call `get_workspace_resources` again, then grep again. Still missing? `fetch_agent` by id if you have one. Tell the user it must be published before a workflow can call it. Never invent an id. |
| A workflow or agent was published in the UI this session | The local list is stale, so the grep misses | Call `get_workspace_resources` again. `save_agent` checks abilities against the live workspace, so a stale list never causes a wrong rejection. |
| Two agents with the same name | Possible — saving without `agent_id` creates a new agent every time | Always pass `agent_id` when editing. If duplicates already exist, show the user both ids and ask which one they mean. |
| Agent has `knowledge:` files or an avatar | Preserved on fetch, never removed on save, cannot be created here | Ask the user to upload them in the agent builder. **Never copy a `knowledge:` block from another agent** — the attachment is one shared row, and publishing the original agent can delete it out from under this one. |
| Hand-written YAML lists only one `modes` sub-key | The controller stores `modes` as one JSON object, so an unlisted sub-key becomes `false` | `save_agent` fills a missing sub-key with `false`, and a YAML without `modes` turns both off. Always list both. |
| User asks the agent to "figure out its own steps" / compose abilities on the fly | That's `modes.code_execution_enabled` | Explain it lets the agent author and run brand-new workflows, so its capability is no longer bounded by the ability list. Ask before enabling, especially alongside any auto-approved ability. |

## Agent behavior

**Ask the user before:**

- Publishing anything — an agent or one of its ability workflows.
- Setting `auto_approved: true` on any ability. Default to `false` and say you did.
- Turning on `modes.code_execution_enabled` — it lets the agent build and run workflows you never reviewed. Doubly so if any ability is auto-approved: the agent can then compose the destructive step itself.
- Editing a published agent's `role` or `abilities` — other callers may depend on it.
- Choosing between several plausible existing workflows for one ability.
- Removing an ability that doesn't resolve.

**Never:**

- Publish on your own initiative, reuse a previous publish approval, or retry a publish the
  user declined in the approval prompt.
- Set `auto_approved: true` on an ability that changes anything outside Blink, without a
  direct yes for that specific ability.
- Attach an ability by name or invent a uuid when nothing matches.
- Attach a workflow you haven't read — `fetch_automation` and check what it actually does,
  its inputs, and its side effects.
- Delete an unresolvable ability to get `save_agent` through.
- Leave `run_draft: true` in a workflow you hand over as finished.
- Copy a `knowledge:` block between agents.
- Hand-edit `workspace/agents/agents-list.tsv` or `workspace/workflows/workflows-list.tsv` — they're generated by
  `get_workspace_resources` (and refreshed after publish); call it again instead.
