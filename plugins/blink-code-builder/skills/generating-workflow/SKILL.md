---
name: generating-workflow
description: Authors a Blink automation as YAML from a natural-language prompt, checks it against the local action catalog, and saves it as a draft in the user's Blink workspace. Use when the user asks to create, build, author, or scaffold a Blink automation, workflow, playbook, or runbook.
allowed-tools: Read, Write, Edit, Glob, Grep, WebSearch, mcp__blink-code-builder__get_workspace_resources, mcp__blink-code-builder__fetch_automation, mcp__blink-code-builder__fetch_options, mcp__blink-code-builder__export_catalog, mcp__blink-code-builder__save_automation, mcp__blink-code-builder__trigger_test_run, mcp__blink-code-builder__get_run_log, mcp__blink-code-builder__publish_automation
user-invocable: false
---

# Generating Blink automations

Produce Blink automation YAML that saves cleanly as a draft in the user's workspace. Save the real YAML file — never wrap the final output in markdown fences.

## Ask when unsure

The single most important rule. If you aren't certain of a choice, **stop and ask the user** rather than guess.

**But spend questions wisely.** Each question is friction — don't bother the user for nothing. Before asking:

1. **Is the answer already in the prompt?** If they wrote "the ai-monitoring channel," that's the channel name. Don't re-pose it as "name vs id?". Don't manufacture an A/B choice when the user already picked A.
2. **Can you derive it from the repo or catalog?** If another automation in `workspace/workflows/` uses the same vendor, reuse its connection (see the **Connections** section). If the catalog lists one obvious action for the capability, just pick it.
3. **Does the question actually block drafting?** Connections don't — see Connections. A missing channel name does. Save unblocked questions for handoff, not the start.

When you do ask, batch related questions in one round and keep each question tight.

Worth asking about:

- **Ambiguous action choice** when a grep returns several plausible candidates (e.g. `slack.send_message` vs `slack.send_message_interactive`).
- **Trigger cadence** when the prompt says "regularly" or "periodically" without specifying a cron.
- **Workspace-specific identifiers** the prompt didn't supply — channel names, email recipients, Jira project keys, etc. (If the prompt did supply them, use them.)
- **Required input with no sensible default.** Don't fabricate values.
- **Workflows that wait on a human** (ticket reply, approval, form answer). Two valid shapes: (a) one workflow with a wait step (`internal.Sleep`, `Mode: Web Form Response`) and a timeout, or (b) two workflows — one sends and ends, a trigger fires the second when the response arrives. Recommend (b) — a parked workflow can sit running for days and can't be test-run automatically — but **ask, don't switch silently**; build (a) if the user prefers it or explicitly described it. (Waiting on a *machine* — polling, retry backoff — is fine as a short bounded `Sleep`, no question needed.)

Bar to clear: *if getting this wrong means the user has to fix it after a diff review, ask before writing — but only if they haven't already given you the answer.*

## Flow

Never leave YAML the controller rejected in the user's repo. Draft in scratch, check, save, only then promote.

Every `mcp__blink-code-builder__*` tool takes a `workspace_id` — use the id the session-start hook gave you.

1. **Clarify blockers** using the rule above.
2. **Look up every action** in the catalog. See [reference/looking-up-actions.md](reference/looking-up-actions.md). Copy `full_name` verbatim — **never type a vendor slug from memory.** A plausible-looking but wrong slug (especially a word-order transposition, where a multi-word vendor name has its parts reordered) parses as valid YAML but is rejected on save. Use the *same* integration slug for the action `full_name`, the step's `connections:` key, and the trigger. When the catalog entry doesn't tell you enough to use the action confidently — sparse parameters, missing enum values, ambiguous shapes — see [reference/handling-sparse-actions.md](reference/handling-sparse-actions.md) for strategies and recorded case studies.

   **Batch the lookups, don't chain them.** Independent calls — meaning calls whose inputs don't depend on each other's output — must go in a single tool round-trip. Two concrete batches in this step:

   - **Round 1 (parallel):** all the greps (`actions.tsv`, `triggers.tsv`, `workspace/workflows/` for existing connections) plus reading any reference docs you'll need (`triggers.md`, `expressions.md`, `looking-up-actions.md`, `yaml-formatting.md`, `safety.md`). None of these depend on each other — issue them as one set of parallel tool calls.
   - **Round 2 (single Bash call):** once you know which JSON files to read, fetch them all in one `tail -n +1 file1 file2 file3` call. Do **not** issue separate `cat`/`Read` per file. See [reference/looking-up-actions.md](reference/looking-up-actions.md) step 3.
   - **Round 3 (conditional — autofill resolution):** after reading action JSONs, identify any `field_type: 2` params where the user named a specific value. For each, call the `fetch_options` tool with `fetcher_name`, `search: "<user-term>"`, and optionally `inputs`/`connections` to resolve the human-readable label to the actual `value` the action expects. **Pass `connections` whenever the action has `connection_types`** — fetchers that call vendor APIs (Azure, GitHub, Jira, etc.) fail without it. It returns a list of `{value, label}` options: one whose `label` matches the user's term exactly → use its `value`; only partial matches → show the candidates to the user and ask them to confirm; an empty list → retry with a shorter term, then use WebSearch for public identifiers, or ask the user a targeted question for private/internal ones. See [reference/looking-up-actions.md](reference/looking-up-actions.md) — Autofill section. **Skip this round entirely if no autofill params need resolving.**

   > **Always verify user-supplied values for `field_type: 2` params.** Even if the user stated a value explicitly (e.g. `"gemini"`, `"BLK"`), you must still call `fetch_options` with `search: "<value>"` to confirm it resolves before writing it into the YAML. Never write a user-provided autofill value unverified — if it doesn't resolve, you'll publish a broken automation silently.

   "Independent" in this skill means: catalog grepping, reading reference docs, scanning `workspace/workflows/` for prior connection names, listing existing files. "Dependent" (must be sequential) means: anything that feeds the next call's arguments — e.g. the JSON-file reads depend on the grep results, so they go in a *second* round, but still as one parallel batch within that round.
3. **Compose expressions** correctly — Go-expr, not Jinja. See [reference/expressions.md](reference/expressions.md). **Format the YAML itself correctly** — use literal block scalars (`|`) for multi-line or colon/dash-containing values, quote cron expressions and anything that could collide with YAML's implicit booleans/numbers, never repeat a key in one mapping. See [reference/yaml-formatting.md](reference/yaml-formatting.md) — most test-run failures trace back to one of these, not to the action logic.
4. **Draft to `/tmp/<name>.yaml`**. Do not write into the user's repo yet. For autofill params, write the full object — both `display_name` (the label) and `value` (the ID) — e.g.:
   ```yaml
   Project Key:
     display_name: R&D
     value: BLK
   ```
   If resolution failed because no playbooks exist yet, leave the param empty and call it out in the step 8 handoff note.
5. **Self-check.** Run the draft through the pre-save checklist in [reference/validation.md](reference/validation.md) — YAML gotchas, action names verbatim from the catalog, required inputs, enum values, step-id references, inline Python syntax, and **connection keys that are one of the action's `connection_types`** (a wrong/transposed integration slug on a step's `connections:` saves fine even when the action `full_name` is correct, and never binds). If the catalog is missing, call `export_catalog` once; if that fails, stop and route the user through `setup-blink-plugin` — unless this is a **code-only revision** (no new actions), which can go ahead without it.
6. **Save** as a draft: Read `/tmp/<name>.yaml` and call the `save_automation` tool with its content as `yaml`. To update an existing automation pass its `automation_id`; an empty `automation_id` **creates a new one**. So before a first save, grep `workspace/workflows/workflows-list.tsv` for the `name:` — if a row matches, it's the same automation: pass that row's `id` (or ask, if it may be a different workflow with the same name). Optionally pass `pack_id` to create it in a specific pack. Two ways a save fails:
   - **The plugin refuses the call** naming actions that aren't in the local catalog (likely typos) → look them up, fix, re-call.
   - **A tool error with the controller's message** (invalid YAML) → fix every reported problem in one pass, re-save.

   Cap at 3 tries, then stop and show the user the last errors. If the call fails with a missing-config or sign-in error, **stop and route the user through the `setup-blink-plugin` skill** — don't try to source `.env` files or guess values. On success the tool returns `{automation_id}` — keep it for every later call, and give it to the user. **Don't echo the YAML body**; the user reviews via `git diff`.
7. **Promote** the saved YAML to the user's `workspace/workflows/` directory (create if missing). One file per automation.
8. **Run the test, or hand off to the user.** First check test-run readiness (see [reference/validation.md](reference/validation.md)):
   - **Ready** — every step that needs a connection has one, required action inputs are filled, and required playbook `inputs` have defaults. Tell the user in plain words what the run will do and what it touches (a test run is **not a dry run** — every step executes for real; see [reference/safety.md](reference/safety.md)), then call the `trigger_test_run` tool with `automation_id`. It runs the **saved** draft. Claude Code asks the user to approve first, listing the risks the plugin found; if the user declines, don't retry — ask what they want instead. The tool returns `{execution_id, state, step_results}` and waits up to ~90 s. `state` = `Completed` with no step errors → one-line success note, done. `Failed`/`Cancelled`/`Timeout`/`Skipped`/`Paused` → step 9. Any other state means it is still running → wait, then step 9.
   - **Waits for a person** (`internal.Sleep` with `Mode: Web Form Response`, or `wait_for_response: true`) — an automatic test run either parks for hours or silently skips the wait, so don't call `trigger_test_run`. **Don't offer to change the YAML** — hand off: tell the user why the automatic run is skipped, and ask them to open the workflow in the Blink editor, click Test Run, and answer the form themselves. Once they confirm it finished → step 9 (ask them for the run's execution id).
   - **Missing connections** — List the steps and the connection type(s) each one needs and ask which existing connections to use (see **Connections**). Patch the YAML, re-save (step 6), re-run this step. If they'd rather bind them in the UI, ask them to click **Test Run** themselves; once they confirm the run finished → step 9.
   - **Empty required action inputs** — For each: if the value is obvious from the user's original prompt, patch the YAML; otherwise ask the user. Re-save (step 6) and re-run this step.
   - **Required playbook `inputs` without `default`** — Ask the user to either give them defaults (re-save and re-run this step) or run from the UI with Test Parameters filled in (then → step 9).
9. **Fetch the run log when needed.** Run only if step 8 didn't already confirm success — i.e. the run wasn't `Completed`, was still running, or the user triggered the test themselves. Call the `get_run_log` tool with the `execution_id` and report:
   - If `state` = `Completed` and no step has an `error` → one-sentence success note.
   - If any step failed → name the step (`step_id`), its action, and the `error` (plus `error_details`). Ask the user whether to patch the YAML (→ Revising flow below) or leave it as-is.
   - If it's still running → tell the user and check again shortly.
10. **Offer to publish — only after a successful test.** The draft is what the editor shows; the **published** version is what scheduled/triggered/production runs execute. Never publish on your own initiative — only when the user asks in this conversation — and only after steps 8–9 confirmed a successful run of the **current saved draft** (re-saving after the test means it must be tested again). When the test succeeded, offer it once, together with a short plain-language summary the user can approve at a glance:
    - **When it runs:** the trigger — cron schedule, event type, or "manual only" (`on_demand`).
    - **What it does:** 2–4 sentences covering the steps and who/what they touch (systems, channels, records).
    - **Blast radius, always and prominently:** describe each high-risk step (destructive actions, messaging in loops, broadcast mentions) in plain language — who/what is affected and roughly how many — **even if the user already approved them for the test run**. A test approval covers one supervised run; publishing means these steps fire on every future trigger. See [reference/safety.md](reference/safety.md) — Publishing section.

    Then branch on the user's answer:
    - **User says yes** → call the `publish_automation` tool with `automation_id`. Claude Code asks the user to approve, showing the risks and whether the saved YAML passed a test. A successful call returns nothing — report that it's published. Done.
    - **User says no, or declines the approval prompt** → stop; the draft stays saved, nothing goes live. Don't retry — ask what they want instead.
    - **No passed test of the current draft** → tell the user which case it is (never tested, or edited after the test) and offer to run the test first (→ step 8). Publish untested only if the user, **after hearing that warning, explicitly accepts it**.

### Revising an existing automation

**First, get the YAML into `workspace/workflows/`.** The automation may already be a local file, or it may live only in Blink (e.g. authored in the UI, or the user handed you an editor URL).

- **Already in `workspace/workflows/`?** Read it.
- **Lives in Blink (id or editor URL, not in the repo)?** Pull it first: call the `fetch_automation` tool with `automation_id` (from an editor URL `.../workflow/<id>/edit`, take `<id>`). A hook writes its YAML to `workspace/workflows/<name>.yaml` and returns that path and the `id` — read the file, and keep the `id` so the later save updates that same automation in place (no duplicate).

Then:

1. Draft the edit into `/tmp/<name>.yaml` (scratch) and self-check it there (step 5 of the Flow).
2. Save it (step 6 of the Flow) with the automation's `automation_id` — from `fetch_automation`, an earlier save, or the `id` column of its row in `workspace/workflows/workflows-list.tsv`. Without it the save creates a duplicate.
3. Overwrite the existing `workspace/workflows/<name>.yaml` in place — same filename, same `name:`. Do not create `_v2.yaml`. Report what changed in one or two sentences. Don't echo the new YAML.

## YAML shape

Only `workflow` is strictly required; everything else has sensible defaults.

| Key               | Required | Notes                                                                     |
|-------------------|----------|---------------------------------------------------------------------------|
| `workflow`        | yes      | List containing **exactly one** section, with `section:` name (`Steps`) and `steps:` list. See Guidelines below. |
| `name`            | no       | Human-readable. Set it — the UI uses it.                                  |
| `automation_type` | no       | `on_demand` (default), `scheduled`, or `event`.                           |
| `desc`            | no       | Purpose blurb.                                                            |
| `inputs`          | no       | Map of automation-level inputs (see below).                               |
| `outputs`         | no       | Map of values returned when the automation completes.                     |
| `triggers`        | no       | Required for `scheduled` and `event`. See [reference/triggers.md](reference/triggers.md). |
| `runner`          | no       | Execution environment, e.g. `blink_cloud`.                                |

### Step

| Key          | Required | Notes                                                                       |
|--------------|----------|-----------------------------------------------------------------------------|
| `id`         | yes      | Unique within the flattened workflow. Use `S1`, `S2`, ... sequential.       |
| `action`     | yes      | Exact `full_name` from the catalog. Never invent.                          |
| `name`       | no       | Descriptive per step.                                                      |
| `inputs`     | no       | Key-value map; keys match the action's parameter names.                    |
| `connections`| no       | Map of `<type>: <connection-name>` (plural). Required for most vendor actions. The singular `connection: <name>` is silently dropped at runtime — always use the plural map. |
| `steps`      | no       | Nested `steps:` list — used by flow-control actions (`internal.for`, `internal.ifCondition`, `internal.ifTrueBranch`, etc.). |

## Connections

**Don't ask about connections up front.** Drafting and saving can proceed without them. The workspace's connections are the source of truth — only names that come back from the `get_workspace_resources` tool will actually bind at runtime. Resolve in this order:

1. **List the workspace's connections — two sources, pick deliberately.** `workspace/connections/connections.tsv` (`<name>\t<type_name>`) is a repo file, refreshed at session start and whenever `get_workspace_resources` is called — free to grep, no round-trip. The `get_workspace_resources` tool hits the workspace live — always current, but costs a call (a hook rewrites the file from it). Default to the cached file; call `get_workspace_resources` instead when you suspect it's stale (the user just mentioned adding a connection this session, or the file is missing/empty) or when the cached file simply isn't there for some reason. Either way, use the result as the candidate set for every step that needs a connection.
2. **Pick by type.** Filter the list to the connection type the step needs (e.g. `slack` for `slack.send_message`):
   - Exactly one match → use it.
   - Multiple matches → if one of them already appears in another `workflows/*.yaml` (grep `workspace/workflows/` to check), prefer it and tell the user you reused it and where from. Otherwise ask the user which to bind.
   - No match → leave blank and call it out at handoff.
3. **If the user volunteers a connection** (in the original prompt or in response), use that name verbatim — even if it isn't in the `get_workspace_resources` output (the workspace may have been updated since). Put it in the YAML and re-run save (step 6 of the Flow) **before moving forward** to test run. Don't leave the connection name parked in chat.

## Subflows — calling another workflow

See the `subflows` skill for the full lifecycle (create, publish, call, reuse). Short version:

- **Creating one:** author it like any other automation in this skill (`automation_type: on_demand`), but run it through its own full loop — check, save, test — **and publish it** before any parent references it. Publishing is what activates it and makes it callable; a workflow that was never published can't be called at all.
- **Calling one as a step:** take the `id` column of a row in `workspace/workflows/workflows-list.tsv` (in the repo, regenerate with `get_workspace_resources` if it looks stale) and write `automations.<id>` (never call by name). Callable only when the row shows `automation_type: on_demand`, `active: true`, and state `published` or `modified`; the step runs the **published** version unless you set `configuration.run_draft: true`, which is for testing only and must not be left in finished work.
- **Reuse before rebuild — but only on an exact match:** grep `workspace/workflows/workflows-list.tsv` for candidates, then `fetch_automation` and **read** the candidate: what it does, its `inputs:`, its output, its side effects. Reuse it only if it does exactly what this step needs; if it's only partly right, say so in one line and author a new workflow instead — never bend an existing active subflow to fit. Drafts are listed too (state `draft`), so if the user points at one, tell them it must be published before it's callable.

## Agents — calling an agent, or building one

See the `generating-agent` skill for the full lifecycle (configure, save, publish, connect). Short version:

- **Calling one as a step:** take the `id` column of a `published`/`modified` row in `workspace/agents/agents-list.tsv` (in the repo, regenerate with `get_workspace_resources` if it looks stale) and write `agents.<id>` (never call by name). Required input `task:` (plain-language goal), optional `output_schema:` for structured output; the result is at `{{ steps.<id>.output.agent_output }}`.
- **A workflow as an agent's ability:** an agent can only run **published, active, on-demand** workflows of this workspace — the same rows in `workspace/workflows/workflows-list.tsv`. Publish the workflow before attaching it.
- **Ordering when the user wants both:** publish the callee first. Workflow-is-an-ability → workflow first; agent-is-a-step → agent first.
- **"Give the agent the ability to do X with a vendor"** means author a small on-demand workflow that does X, publish it, then attach it — abilities are workflows only, never vendor actions.

## Tables — reading/writing Blink's internal database

See the `tables` skill the moment an action lookup hits a `tables.*` or `system.Table*`
action. Short version:

- **When to reach for one:** data that's this workflow's own bookkeeping (a dedup
  list, a running tally, a queue) with no existing system of record — not data that
  already lives in a real vendor system (use that vendor's action instead).
- **Look up the schema before drafting a step:** read `workspace/tables/tables-schema.yaml`
  (regenerate with `get_workspace_resources` with `include_tables=true` if it's missing, stale >24h, or the
  user just changed a table this session) instead of guessing column names. Use the
  table's internal `table` name in a step's `table:` input, not its `display_name`.
- **`system.TableClear`, `system.TableRemove`, and `tables.DeleteRecordsV2`** are
  destructive/irreversible and flagged in the same approval prompt as any other
  destructive action (see [reference/safety.md](reference/safety.md)) — nothing extra
  to do beyond the usual plain-language warning before the run.
- **A test run of a table-writing step is not a dry run** — `system.TableCreate`
  really creates the table, `tables.AddRecordV2`/`system.TableInsert` really insert a
  row. Default `skip: true` on `system.TableCreate` and consider a disposable scratch
  table for a first test run of a write step.

## Reuse before rebuild — API helpers

Never write ad-hoc code for something an existing script already does. Rebuilding known helpers burns tokens and produces divergent copies.

1. **The plugin's MCP tools cover the whole flow** — save, fetch, test-run, run logs, publish, workspace lists, catalog, autofill options (`save_automation`, `fetch_automation`, `trigger_test_run`, `get_run_log`, `publish_automation`, `get_workspace_resources`, `export_catalog`, `fetch_options`). Use them; never hand-roll an httpx/curl call against the Blink API for anything they already do.
2. **Beyond the plugin tools, search the repo before writing.** Repos that use this plugin often carry their own Blink API utilities (`scripts/`, `tools/`, `utils/` directories, or an index in the repo's README/CLAUDE.md). Glob for them and grep for the API route or action you need — a working helper in the repo beats a fresh one, every time.
3. **If a new helper is genuinely needed, make it findable next time:** save it in the repo's existing utilities directory (not `/tmp`, unless it's truly one-off) with a one-line docstring stating which API action it wraps, and add a one-line entry to the repo's helper index (README/CLAUDE.md) if one exists. The goal: the next session greps, finds it, and skips the rebuild.

## Guidelines

1. Sequential step IDs (`S1`, `S2`, ...). Unique across the flattened workflow, including inside `if`/`for` branches.
2. Descriptive `name` on every step.
3. **Exactly one section per automation, named `Steps`.** Never emit more than one entry under `workflow:` — the Blink editor UI rejects saves with more than one section (you'd have to manually delete the extras before it saves). Blink's own server-side automation generator emits this same shape — a single `- section: Steps` entry, never more. Don't split multi-phase work ("Parse CSV", "Resolve Conflicts", "Done", etc.) into separate sections — those are just steps (and flow-control actions like `internal.ifCondition`/`internal.for`) inside the single `Steps` section.
4. Action `full_name` always verbatim from the catalog.
5. `connections: { <type>: <name> }` (plural) on every vendor action — see the **Connections** section below. Never use the singular `connection: <name>`; the runtime drops it.
6. Default to `on_demand` unless the prompt specifies a schedule or event.
7. Run the pre-save checklist and get a successful save before claiming done.
8. Never embed secrets in YAML — reference existing connections by name.
9. **Draft small by default — minimize blast radius.** Default recipients to the requesting user or a test channel (not a team channel) unless the user named the audience; put a `LIMIT` on any query that feeds a loop for the first test run; never add `@here`/`@channel` or target "all users" on your own initiative. See [reference/safety.md](reference/safety.md) — a test run executes every step for real.
10. **Prefer specific actions over flexible code-execution actions.** Reach for `core.pythonV2`, `core.bash`, `core.container`, etc. only when no domain-specific action covers the task. Before falling back, search the catalog by service prefix as well as keyword (e.g. `Grep pattern="^array\." path="${CLAUDE_PLUGIN_DATA}/catalog/actions.tsv"`) — capability-named services like `array.*`, `utility.*`, `http.*` often have the right primitive (`array.ArraySort`, `array.GetArrayItem`, `array.ArrayFilter`, `utility.Convert*`, etc.). A one-line Python lambda fed to a domain action's `key:`/`filterFunction:` parameter is fine; a wholesale `core.pythonV2` step replicating that domain action is not.
The same rule applies to the expression language: when work can be done as an `{{ … }}` expression — date math (`add`, `sub`), string ops (`split`, `replace`), field extraction, list-shape checks (`empty`) — wrap it in `internal.SetVariables` rather than a `core.pythonV2` step. See [reference/expressions.md](reference/expressions.md).
