---
name: generating-agent
description: Authors, edits and publishes a Blink agent as YAML — its role, constraints and abilities — and connects agents and workflows to each other. Use when the user asks to create, build, configure, edit or publish a Blink agent, attach a workflow as an agent ability, or call an agent from a workflow.
allowed-tools: Read, Write, Edit, Glob, Grep, mcp__plugin_blink-code-builder_blink__list_agents, mcp__plugin_blink-code-builder_blink__fetch_agent, mcp__plugin_blink-code-builder_blink__validate_agent, mcp__plugin_blink-code-builder_blink__save_agent, mcp__plugin_blink-code-builder_blink__publish_agent, mcp__plugin_blink-code-builder_blink__fetch_automation, mcp__plugin_blink-code-builder_blink__validate_automation, mcp__plugin_blink-code-builder_blink__save_automation, mcp__plugin_blink-code-builder_blink__trigger_test_run, mcp__plugin_blink-code-builder_blink__get_run_log, mcp__plugin_blink-code-builder_blink__publish_automation
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
Your repo                      ${CLAUDE_PLUGIN_DATA}/catalog        Blink workspace
─────────────────────────      ────────────────────────────        ──────────────────────
agents/                        workspace_actions.tsv               name, title, pack
  alert-triage.yaml    ◄──────►   kind=agent   → agents.<id>       role, abilities…
                                  kind=subflow → automations.<id>       draft  ← save_agent
automations/                                                            published ← publish_agent
  enrich-observable.yaml                                          playbooks           (workflows)
connections/                                                     actions             (agents.<id> row,
  connections.tsv                                                                      created on publish)
    refreshed: session start,
    after every publish

  you edit ──► validate_agent ──► save_agent ──► publish_agent ──► callable from a workflow
                  (local)          (draft)        (live)
```

## Configuration

One file per agent in `agents/`. Only `name` and `role` are required.

| Key | Meaning |
|---|---|
| `name` | Identity, unique in the workspace. What `save_agent` matches on. |
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
  - ability_id: 3f2a…                # a published on-demand workflow, copied from the catalog
    auto_approved: false
```

## Finding an agent

Two sources, in this order:

1. **`workspace_actions.tsv`** (`kind=agent` rows) — free to grep, but **published agents only**.
2. **`list_agents`** — one call, every agent including drafts, each with its state:
   `draft` (never published), `published` (live, draft matches it), `modified`
   (live, but the draft has newer edits that are not published yet).

Call `list_agents` whenever the grep misses, **before** telling the user an agent doesn't exist.
A draft agent is invisible in the catalog by design.

## Creating an agent

1. **Decide the abilities first.** Grep `${CLAUDE_PLUGIN_DATA}/catalog/workspace_actions.tsv`
   for `kind=subflow` rows — those are the workspace's callable workflows and the only
   eligible abilities. The `action` column is `automations.<uuid>`; the `ability_id` is that
   uuid **without** the prefix.
2. **Any ability that doesn't exist yet?** Author and publish that workflow first with the
   `generating-workflow` skill — full loop, including its own test run. It is not attachable
   until it is published.
3. **Write `agents/<name>.yaml`.** Spend the effort on `role`; read
   [reference/configuration.md](reference/configuration.md) before writing it.
4. **`validate_agent`** with `path`. Clear every `[ERROR]`. Report `[WARN]` lines to the user
   rather than silently fixing them.
5. **`save_agent`** with `path`. Report the agent id and the full editor link it returns —
   always the clickable link, never just an id. Don't echo the YAML; the user reviews with
   `git diff`.

   If it returns **`[BLOCKED EXISTS]`**, an agent with this name already exists and saving would
   replace its role and abilities. Do not force it. Tell the user which agent it found, then:
   - they meant to edit that agent → `fetch_agent` it first so you can see its current setup,
     then re-call with `agent_id`;
   - they wanted a separate agent → change `name:` in the YAML.

   Only pass `allow_overwrite: true` after the user confirmed they want that specific agent
   replaced.
6. **Offer to publish — never publish on your own initiative.** Say in plain language what
   the agent will do, which abilities it can run, and who they affect. Then `publish_agent`.
   Until it is published the agent is a draft: not callable from any workflow.

## Agent and workflow together

When the user wants both, the order follows the one rule — whichever is the **callee** goes first.

- **Workflow is the agent's ability** → build and publish the workflow, then the agent.
- **Agent is a step in the workflow** → build and publish the agent, then the workflow.

Both at once (an agent with abilities, called from a workflow): publish the ability workflows,
then the agent, then the calling workflow. Say which order you're taking and why in one line.

## Calling an agent from a workflow

```yaml
- id: S3
  action: agents.<agent-uuid>        # copy the `action` column of a kind=agent row, verbatim
  name: Triage the alert
  inputs:
    task: "Triage this alert and return a verdict"
    output_schema: |                 # optional — only when you need structured output
      {"verdict": "malicious", "confidence": "80%"}
```

- Always copy the `action` string from `workspace_actions.tsv`. Never assemble it by hand,
  never call an agent by name.
- Output is at `{{ steps.S3.output.agent_output }}`.
- Optional advanced inputs: `roles_and_constraints` (extra instructions for this run only —
  appended to the agent's own role, so a step can narrow an agent without editing it),
  `timeout` (minutes, default 30), `run_draft` (run the agent's draft while editing — testing
  only, remove before handing over), `continue_last_session`, `allow_extra_verbose_answer`.
- No `connections:` block — the agent's abilities carry their own.

## Editing an agent

1. **Already in `agents/`?** Read it. **Lives only in Blink?** `fetch_agent` with `ref` (the
   agent id or an agent-builder URL) — it writes `agents/<name>.yaml`. Don't have the id? See
   **Finding an agent** above.
2. Edit that file in place. Same filename, same `name:`. Never create `_v2.yaml`.
3. `validate_agent`, then `save_agent`.
4. Offer to publish. **Say what changes for existing callers**: changing `role` or
   `abilities` on a published agent changes behavior for every workflow step and chat session
   already using it, and those callers are not visible from here.

## Edge cases

| Situation | What happens | What you do |
|---|---|---|
| Ability's workflow exists here but isn't published | `[WARN] … is NOT callable` | Publish the workflow, re-validate. Not callable until then. |
| Ability doesn't exist in this workspace (imported agent, deleted workflow) | `[WARN] … does not exist in this workspace`. Save and publish still succeed; the agent silently never sees it | Tell the user, name the ability. **Never delete the line to clear the warning** — that strips a capability with no trace. |
| `type: action` on an ability | `[ERROR]` | Abilities are workflows only. Wrap the vendor action in a small on-demand workflow, publish it, attach that. |
| Same `ability_id` listed twice | `[ERROR]` | Keep one entry. Two with different `auto_approved` make the agent pick unpredictably. |
| An ability has `auto_approved: true`, or `modes.code_execution_enabled` is on | `[WARN]` at validate, `[BLOCKED SAFETY]` at publish | Describe each flagged point and who it affects, ask a direct yes/no, then re-call with `acknowledge_risks: true`. Never carries to the next publish. |
| An agent with this `name:` already exists | `[BLOCKED EXISTS]` from save/publish | Never force it. Either `fetch_agent` + re-call with `agent_id` (editing that agent), or rename in the YAML (separate agent). |
| An ability's workflow itself calls an agent | nothing local | The controller enforces the depth limit of 5 at run time and errors if exceeded, so a cycle stops itself. Mention it if the user is designing deep chains. |
| User names an agent that isn't in `workspace_actions.tsv` | It's probably a draft — drafts aren't in the catalog | Call `list_agents` to find it, then `fetch_agent` by id. Tell the user it must be published before a workflow can call it. Never invent an id, never say it doesn't exist without checking `list_agents`. |
| A workflow was published in the UI this session | The catalog snapshot is stale, so the grep misses | Nothing — validation falls back to a live lookup before reporting anything. |
| Two agents with the same name | Can't happen — names are unique per workspace | Saving matches by name and updates in place; re-saving an edited file never duplicates. |
| Agent has `knowledge:` files or an avatar | Preserved on fetch, never removed on save, cannot be created here | Ask the user to upload them in the agent builder. **Never copy a `knowledge:` block from another agent** — the attachment is one shared row, and publishing the original agent can delete it out from under this one. |
| Hand-written YAML lists only one `modes` sub-key | The controller stores `modes` as one JSON object, so an unlisted sub-key becomes `false` | `save_agent` always sends both keys, so this is safe through the tools. If you hand-edit, list both. |
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

- Publish on your own initiative, or reuse a previous `acknowledge_risks` approval.
- Set `auto_approved: true` on an ability that changes anything outside Blink, without a
  direct yes for that specific ability.
- Attach an ability by name or invent a uuid when nothing matches.
- Attach a workflow you haven't read — `fetch_automation` and check what it actually does,
  its inputs, and its side effects.
- Delete an unresolvable ability to make validation quiet.
- Leave `run_draft: true` in a workflow you hand over as finished.
- Copy a `knowledge:` block between agents.
- Write to the local catalog files — they're read-only, refreshed by the session hook.
