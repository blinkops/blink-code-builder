---
name: subflows
description: How to create, call, and reuse Blink subflows — workflows that are callable as a step from another workflow. Covers when to use one, sync vs async, the save/validate/test/publish order, and where to find what already exists in the workspace. Use when the user asks to create a reusable/callable workflow, call one workflow from another, or split existing automation logic into a subflow.
allowed-tools: Read, Grep, mcp__plugin_blink-code-builder_blink__list_connections, mcp__plugin_blink-code-builder_blink__list_workflows, mcp__plugin_blink-code-builder_blink__fetch_automation, mcp__plugin_blink-code-builder_blink__validate_automation, mcp__plugin_blink-code-builder_blink__save_automation, mcp__plugin_blink-code-builder_blink__trigger_test_run, mcp__plugin_blink-code-builder_blink__get_run_log, mcp__plugin_blink-code-builder_blink__publish_automation
user-invocable: false
---

# Subflows

A subflow is another published, active On-Demand workflow run as a reusable step inside a parent workflow. It reduces duplication and simplifies logic by letting you treat a whole workflow as a reusable sub-component.

Key details:

- Supports **synchronous** (parent waits for the result) or **asynchronous** (parent continues immediately) execution.
- Maximum nesting depth of **10 levels**.
- Input parameters of a subflow **cannot be modified or deleted** without breaking the parent workflows that call it.

There is no special YAML type for a subflow — it's a normal automation with `automation_type: on_demand` that has been published. Once published and active, any other workflow can call it as a step.

## When to split into parent + subflow

Split when:
- The user explicitly asks for something reusable/callable, or names a distinct sub-process.
- The same steps are needed from more than one place — extract once, call from everywhere.
- Part of the flow is a long, independent wait (human approval, ticket reply) — cleaner as its own on-demand unit.
- An existing workflow already covers the needed logic — call it instead of re-authoring it.

Don't split just because a workflow is long, or for speculative reuse with no concrete second caller — in that case, ask the user first.

## Creating a subflow

Author it like any other automation (see the `generating-workflow` skill), with `automation_type: on_demand`. A workflow that was never published is **not callable at all**, and a parent calls the **published** version by default — so publish before wiring it into a parent (see "Which version does the parent actually call?" below). Run the subflow through its full loop **before** the parent references it:

1. Draft the subflow (resolve its connections as usual).
2. `validate_automation`
3. `save_automation`
4. `trigger_test_run` + `get_run_log` — test it on its own.
5. `publish_automation` — publishing also activates it; this is what makes it callable.

Keep the id from the save/publish response — that's what the parent references.

## Calling a subflow from a parent

```yaml
- id: S2
  action: "automations.<uuid>"
  configuration:
    run_async: false
  inputs:
    <input_name>: <value>
```

- Always reference by **uuid**, never by name. The `action` column of a row in `workflows/workflows-list.tsv` is that exact string — copy it, don't assemble it by hand.
- `inputs:` come from the subflow's own declared top-level `inputs:` — check `fetch_automation` or the catalog if unsure.
- The calling step normally needs **no `connections:` block** — the subflow's internal steps carry their own connections. If the subflow declares an input of `type: connections`, pass the connection's **name** as a regular input value.
- The subflow's output is available at `{{ steps.<id>.output }}` — sync calls only.

### Step `configuration:` parameters

All optional; template expressions are allowed in the string fields:

| Field | Type | Meaning |
|-------|------|---------|
| `run_async` | bool | Async execution — parent continues immediately (default: sync) |
| `run_draft` | bool | Run the subflow's **draft** version instead of the published one (useful for testing before publish) |
| `subflow_timeout_in_minutes` | string/int | How long a sync call waits for the subflow before erroring |
| `subflow_future_schedule_time` | string (time) | Schedule the subflow to run at a future time — only honored with `run_async: true` |
| `custom_runner_group_name` | string | Run the subflow on a specific runner group |

Then run the parent through the same loop: validate → save → test → publish.

### Which version does the parent actually call? (draft vs published)

This is the part that silently goes wrong, so decide it explicitly for every subflow call:

| State of the target workflow | Row in `workflows/workflows-list.tsv` | Can the parent call it? |
|---|---|---|
| Published + active, on-demand | `automation_type=on_demand`, `state=published` (or `modified`), `active=true` | **Yes** — the step runs the **published** version by default |
| Saved as a draft, never published | `state=draft` | **No.** The `automations.<uuid>` action does not exist yet, so the step cannot even reference it. `run_draft: true` does not help here |
| Was published, then deactivated | `active=false` | **No** — reactivate/publish it again first |
| Not `automation_type: on_demand` | `automation_type=scheduled` or `event` | **No** — only on-demand workflows can be subflows |

Rules that follow from that:

- **Publish the subflow once before the parent references it.** Publishing (= activating an on-demand workflow) is what creates the callable action; that is the whole point of step 5 in "Creating a subflow".
- After it exists, `run_draft: true` on the parent's step runs the subflow's **current draft** instead of the published version. Use it only to test a change to the subflow before publishing it, and say so to the user — leaving `run_draft: true` in a workflow you hand over means production traffic runs unpublished code. Remove it before the final publish.
- **If you changed the subflow's `inputs:`, publish the subflow again** before the parent passes the new input. The callable action's parameter list is regenerated from the published workflow, so an unpublished new input is not part of it yet.
- **A row with `state=draft` or `active=false` is not callable.** `workflows/workflows-list.tsv` lists every workflow including drafts, so if the user points at one that isn't callable yet, tell them it has to be published (and active) before a parent can call it, and ask. Never work around it by calling it by name or inventing an id. Re-run `list_workflows` first if the file might be stale.

## Sync vs async

Controlled by the step's `configuration.run_async` field (default: sync):

- **Sync** — the parent waits; use whenever the parent needs the subflow's output. Set `subflow_timeout_in_minutes` if the subflow can legitimately run long.
- **Async (`run_async: true`)** — fire and forget; no output available. Use when the parent doesn't need the result. Can optionally be deferred with `subflow_future_schedule_time`.

If the request doesn't make the choice obvious, ask the user.

## Reusing an existing workflow

### Where to look

- `workflows/workflows-list.tsv` — every workflow in the workspace, drafts included (`action`, `name`, `automation_type`, `state`, `active`). Callable right now only when `automation_type=on_demand`, `state=published`/`modified`, and `active=true` — its `action` column is the exact `automations.<uuid>` string to put in the step. Written by `list_workflows`; re-run it if the file looks stale. `agents/agents-list.tsv` is the equivalent for agents (`published`/`modified` rows are callable), written by `list_agents`.
- `${CLAUDE_PLUGIN_DATA}/catalog/connections.tsv` — every connection (`name`, `type_name`).

Grep these first; only call `fetch_automation` / `list_connections` live when you need more detail. Drafts are listed too — see the last rule of the draft-vs-published section. You don't need one to avoid duplicates: `save_automation` matches the name live across all packs and updates that workflow in place.

### Reuse only on a real match — otherwise build a new one

A name that looks close is **not** a match. Reusing the wrong workflow is worse than writing a new one: it silently changes what the user's automation does, and its inputs/outputs may not fit at all.

1. Grep `workflows/workflows-list.tsv` for candidates by name.
2. For each serious candidate, `fetch_automation` and **read it** — what it actually does, its `inputs:`, what it returns, which connections it uses, and any side effect (it writes a ticket, sends mail, deletes something).
3. Reuse it only if it does **exactly** what this step needs, and its inputs cover what you have to pass. Partly-right is not right.
4. If it doesn't fit, say so in one line ("`Enrich User` only looks up AD, not Okta — building a new subflow") and author a new workflow instead. Do **not** edit an existing active subflow to make it fit — other parents may call it, and those callers are not visible from here.
5. If two candidates both look plausible, stop and ask the user which one.

## Agent behavior

**Ask the user before:**
- Splitting into parent + subflow when the signal isn't clear-cut.
- Choosing between multiple plausible existing subflows.
- Editing an already-active subflow's inputs/outputs — other parents may call it, and not all callers are visible locally.
- Publishing anything on the user's behalf.

**Never:**
- Call by name/slug instead of uuid, or invent an id when nothing matches.
- Reuse an existing workflow you haven't read with `fetch_automation`, or one that only partly does what was asked — build a new one and say why.
- Leave `run_draft: true` in a workflow you hand over as finished.
- Create a call cycle — a direct self-call is caught by validation, but a multi-hop cycle between subflows isn't; check the chain yourself.
- Silently deactivate, delete, or republish an existing subflow with breaking changes.
- Write to the local catalog files — they're read-only, refreshed by the session hook.
  (`workflows/workflows-list.tsv` is different — it's a repo file, and `list_workflows` is
  meant to overwrite it.)
