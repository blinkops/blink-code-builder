# Validation reference — generating-workflow

Referenced from [../SKILL.md](../SKILL.md) step 5. Run the validator, read errors, fix, repeat. Cap at **3 iterations**. If a 4th would start, stop and show the user the last YAML plus remaining errors — don't burn more tokens guessing.

## How to run it

Call the `validate_automation` tool with `path: "<path/to/automation.yaml>"`.

The returned text carries the outcome:
- `[OK]` — structurally valid. The output also includes a **Test run readiness** section (see below) — read it, but readiness blockers don't fail this step.
- `[ERROR]` (one or more lines) — errors in the YAML. The full error list is in the returned text. Fix everything in one pass where possible.
- `[CATALOG MISSING]` — catalog missing. For a **new** automation, **stop and tell the user** — the `SessionStart` hook is responsible for populating the catalog; if it's not there, config is wrong or the controller is unreachable, and you can't safely verify invented action names. For a **code-only revision** (you only changed inputs/code inside existing steps, no new actions), re-call with `allow_missing_catalog: true`: the validator drops to degraded mode (structural + embedded-Python checks) and skips action-existence/enum/readiness. Don't try to refresh the catalog from the skill.
- A hard failure (e.g. file not found) surfaces as a tool error rather than any of the text markers above.

## Test run readiness

After `[OK]`, the tool's returned text includes either `Test run readiness: READY` or `Test run readiness: BLOCKED` followed by the specific reasons. This mirrors the same checks the UI uses to enable the "Test run" button (see step 8 of the SKILL flow):

- `[CONNECTIONS]` — a step's action requires a connection and none is set on the step. Ask the user which existing connection to use; don't fabricate names.
- `[DATA]` — a step's required action input is empty (passes structural validation because the key is *present* but the value is empty). Patch the YAML if the value is obvious from the user's prompt; otherwise ask.
- `[RUNTIME_INPUTS]` — the playbook declares required `inputs` with no `default`. Either give them defaults or have the user supply Test Parameters in the UI before running.

`READY` means the `trigger_test_run` tool can fire the run automatically — no need to ask the user to click anything.

## Error categories and fixes

| Error pattern | Root cause | Fix |
|---|---|---|
| `YAML parse: ... while scanning an alias ...` (+ `[HINT]`) | A value starts with an unquoted `*` — most often a cron expression (`*/5 * * * *`). YAML reads leading `*` as an alias reference. | Quote it: `cron: "*/5 * * * *"`. See [yaml-formatting.md](yaml-formatting.md). |
| `YAML parse: ... mapping values are not allowed here ...` (+ `[HINT]`) | An unquoted scalar contains `: ` (colon + space), which YAML reads as a new mapping key. | Quote the value, or use a literal block scalar (`\|`) for multi-line/colon-containing text. See [yaml-formatting.md](yaml-formatting.md). |
| `YAML parse: ... sequence entries are not allowed here ...` (+ `[HINT]`) | An unquoted scalar starts with `- ` (dash + space), which YAML reads as a list entry. | Quote the value, or use a literal block scalar (`\|`). See [yaml-formatting.md](yaml-formatting.md). |
| `duplicate key '<x>' at line N` | The same key appears twice in one mapping (e.g. two `Content:` entries on a step). YAML doesn't error on this — it silently keeps the last one. | Decide whether the two were meant to be merged (e.g. into one block-scalar value) or whether one is leftover — don't just delete one blindly. See [yaml-formatting.md](yaml-formatting.md). |
| `parameter <name> looks like an unquoted YAML boolean/number ...` | A text-typed parameter's value was implicitly coerced by YAML (e.g. `NO` → `False`, `1.20` → `1.2`, `007` → `7`). | Quote the value so it round-trips as the literal string intended. See [yaml-formatting.md](yaml-formatting.md). |
| `top-level \`workflow\` missing` | The draft forgot the `workflow:` key. | Add a `workflow:` list with exactly one section (named `Steps`) containing steps. |
| `a step calls this automation's own playbook id (...)` | A step's `automations.<uuid>` points at the automation itself. | Remove the self-call — a subflow must never call itself. See [subflows.md](../../subflows/SKILL.md). |
| `top-level \`automation_type\` must be one of ...` | Typo or invented value. | Only `on_demand`, `scheduled`, or `event`. |
| `section '<x>': step missing \`id\`` | A step is missing `id`. | Assign sequential `S1`, `S2`, ... |
| `step <id>: missing \`action\`` | Step has no action reference. | Look up the intended action via [looking-up-actions.md](looking-up-actions.md). Never invent action names. |
| `duplicate step id '<x>'` | Two steps share an id. | Re-number so every id in the flattened workflow is unique. |
| `action '<full_name>' not in catalog. Did you mean: <a>, <b>, <c>?` | Action name typo or wrong service prefix. | Read the suggested action files (parallel reads), pick one, copy the exact `full_name`. If none match, re-grep `actions.tsv` by capability. |
| `action 'automations.<uuid>' is not callable in this workspace` | No callable row in `workflows/workflows-list.tsv`. Either the uuid is wrong/deleted, or the target workflow is a draft, deactivated, or not `on_demand` — none of those are callable as a subflow. | Check `workflows/workflows-list.tsv` for the row: it needs `automation_type=on_demand`, `state=published`/`modified`, and `active=true` to be callable — copy its `action` column. If it's not there yet, `fetch_automation` the intended workflow and ask the user to publish/activate it. Never invent an id. See [subflows/SKILL.md](../../subflows/SKILL.md). |
| `agent 'agents.<uuid>' is not callable in this workspace` | No callable row in `agents/agents-list.tsv`. Either the uuid is wrong/deleted, or the agent has never been published. | Check `agents/agents-list.tsv` for a `published`/`modified` row and copy its `action` column. If it's not there yet, publish the agent first. Never invent an id. See the `generating-agent` skill. |
| `input '<x>' missing` (required) | A required parameter wasn't supplied. | Re-read the action JSON for its `parameters[]`. Supply a value or **ask the user** — don't fabricate. |
| `input <name>=<v> not in options [...]` | Value doesn't match the parameter's enum. | Pick one of the listed options verbatim (case matters). |
| `input <name> references unknown step id '<x>'` | `{{ steps.Sx.output }}` refers to a step that doesn't exist. | Correct the id. Remember flow-control children have their own ids. |
| `step <id> (core.python): Python SyntaxError: <msg> (line N)` | Inline Python in a `core.python`/`core.pythonV2` step doesn't parse. | Fix the syntax at the reported line. (Bodies containing `{{ }}` template expressions are skipped — they aren't standalone Python.) |
| `input '<x>': must be a parameter-definition object ... got '<value>'` | Top-level `inputs.<x>` is a bare value instead of a `{type, required, default, ...}` object. | Wrap it as a full param definition. This isn't just a lint nit — the controller silently corrupts a bare value by indexing it char-by-char in storage. |
| `input '<x>': missing required \`type\`` | A top-level input has no `type` field. | Add one of the valid types (`text`, `number`, `single-select`, ...). Without it the UI can't render the input field for a manual run. See the PlaybookInputParamDefinition shape in [handling-sparse-actions.md](handling-sparse-actions.md). |
| `step <id> (internal.SetVariables): 'Variables[i]' uses lowercase key(s) [...]` | `Variables` entries used `name`/`type`/`value` instead of `Name`/`Type`/`Value`. | Capitalize the keys. Lowercase keys are silently dropped at runtime, not rejected. |
| `step <id> (internal.SetVariables): 'Variables[i]' missing required key(s) [...]` | A `Variables` entry is missing `Name`, `Type`, or `Value`. | Supply all three keys on every entry. |
| `step <id> (internal.SetVariables): 'Variables[i].Type' '<x>' is not one of the confirmed values` (`[WARN]`, doesn't fail) | `Type` isn't one of the confirmed values (`String`, `Number`, `List`). | Usually a typo/casing issue (e.g. `number` instead of `Number`). Unconfirmed values aren't necessarily wrong — verify with a test run if you need one outside this set. |
| `step <id> (<action>): 'web_form_inputs' must be a dict keyed by field name ... got list` | `web_form_inputs` was written as a list (easy mix-up with `SetVariables`' list shape). | Use a dict keyed by field name, e.g. `web_form_inputs: {my_field: {type: text, index: 1}}`. |
| `step <id>: uses singular 'connection:' key` | Step declared `connection: <name>` instead of the plural map. | Use `connections: {<type>: <name>}`. The singular key is silently dropped at runtime. |
| `step <id> (<full_name>): connection key '<x>' is not one of this action's connection_types. Did you mean: <a>, <b>?` | The `connections:` key on the step is a wrong or transposed integration slug (e.g. a multi-word vendor name with its parts reordered) — the action `full_name` is fine but the connection never binds at runtime. | Use the suggested slug. Confirm it against the action's `connection_types` in the catalog, and use that **same** slug for the action `full_name`, the step's `connections:` key, and the trigger. Never type a vendor slug from memory. |

## Loop protocol

Never leave an unvalidated YAML in the user's repo.

1. Draft to `/tmp/<name>.yaml` (scratch).
2. Call `validate_automation` against the scratch file.
3. On `[OK]` → promote the file to `workflows/<name>.yaml`, then call `save_automation`.
4. On `[ERROR]` → read **all** error lines (they surface together), apply fixes in one pass, re-call. Cap at **3 iterations**.
5. On `[CATALOG MISSING]` → for a new automation, stop and ask the user to check `userConfig` and `${CLAUDE_PLUGIN_DATA}/refresh.log`; don't try to recover from inside the skill. For a code-only revision, re-call with `allow_missing_catalog: true` and continue.
6. If iteration 4 would start (genuine YAML errors the validator keeps rejecting), stop. Report the last errors and what you tried. Don't keep guessing.

For revisions on an **existing** automation: draft the edit into `/tmp/`, validate there, and only overwrite `workflows/<name>.yaml` once `[OK]`. Don't echo the new YAML in your response — the user reviews via `git diff`.

## Input format

Automation-level inputs are declared under the top-level `inputs:` key. Each entry is a map with these fields:

```yaml
inputs:
  test:
    type: long-text
    required: true
    index: 1
  environment:
    type: single-select
    options: [staging, production]
    required: false
    index: 2
```

- `type` — required; must be lowercase. Valid values: `text`, `long-text`, `rich-text`, `number`, `checkbox`, `single-select`, `multi-select`, `date`, `connections`, `file`, `list`, `dynamic_text`.
- `required` — if `true` and no `default` is set, the user must supply a value at run time (surfaced as `[RUNTIME_INPUTS]` by the validator).
- `index` — 1-based display order in the UI.
- `default` — optional; must be a valid value for the declared `type` (the validator checks this).
- `options` — required for `single-select` and `multi-select` types.

### Required inputs and test runs

If any input has `required: true` and no `default`, the validator will report `[RUNTIME_INPUTS]`. This means the automation **cannot be test-run automatically** — the user must open the UI, fill in the Test Parameters, and click Test Run themselves. When this happens:

1. Tell the user which inputs need values.
2. Either ask the user to provide defaults (so you can patch them into the YAML and re-save), or instruct them to supply the values in the UI's Test Parameters panel before running.

Do **not** invent placeholder values for required inputs — ask the user what to use.

## Output format

The optional top-level `outputs:` key maps named keys to structured output entries. Each entry has a `value` (a `{{ ... }}` expression) and an `index` (1-based display order).

```yaml
outputs:
  ticket_url:
    value: "{{steps.S3.output.url}}"
    index: 1
  item_count:
    value: "{{inputs.count}}"
    index: 2
```

Rules:

- Keys are plain strings — no spaces, no template syntax.
- `value` is a `{{ ... }}` expression. Reference step outputs via `{{steps.<id>.output.<field>}}` or automation inputs via `{{inputs.<name>}}`.
- `index` controls display order in the UI; use sequential integers starting at 1.
- When an action has `auto_pagination: true`, the runtime wraps pages as `{"results": [...]}` — access via `{{steps.<id>.output.results}}`.
- Omit `outputs:` entirely when the automation doesn't need to return structured data.

### Validator errors for outputs

The validator does **not** currently check `outputs:` values — broken `{{steps.X}}` references in `outputs:` are silently ignored at validation time. Use the same step-id discipline here as in step inputs to avoid runtime surprises.

## Don't over-correct

Some fixes are judgment calls, not purely mechanical:

- A required param asks for `connections:` (the plural type→name map) and the user hasn't said which → **ask**. Don't pick arbitrarily.
- The suggestion list has three similar actions and none clearly matches → **ask** which one the user intended.
- `[WARN]` lines are fine to ship. Don't chase them unless they block save.
