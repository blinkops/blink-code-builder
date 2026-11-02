# Validation reference — generating-workflow

Referenced from [../SKILL.md](../SKILL.md) steps 5–6. Two layers: you self-check the draft against the catalog and the checklist below, then `save_automation` runs the controller's validation. Cap the save-fix loop at **3 iterations**. If a 4th would start, stop and show the user the last YAML plus remaining errors — don't burn more tokens guessing.

## What checks what

- **You, before saving** — every row of the checklist below, plus the engine compile rules in [engine-validation.md](engine-validation.md). Nothing else catches most of them before a test run.
- **The workflow engine, on every run** — compiles the YAML (and each subflow it loads) before running a step. It is stricter than the save: missing `name`/`automation_type`, bad conditions, empty connection values, and missing engine-action inputs all fail here with `Workflow <id>: <message>`, even though the save accepted the YAML. See [engine-validation.md](engine-validation.md).
- **The plugin's save check** — a hook on `save_automation` refuses the call when an action isn't in the local catalog (almost always a typo) and names the actions. Look up the right `full_name` and re-call.
- **The controller, on save** — invalid YAML comes back as a tool error with the controller's message. Read **all** of it, fix everything in one pass, re-save.
- **Catalog missing** — if `${CLAUDE_PLUGIN_DATA}/catalog/` is missing or empty, call `export_catalog` once (a hook saves it to disk). If that tool is missing or fails, stop and tell the user to run `/mcp` and sign in to Blink — you can't safely verify action names without it. A **code-only revision** (only inputs/code inside existing steps changed, no new actions) can go ahead without it.

## Test-run readiness

Check these before the test run (step 8 of the SKILL flow) — the same checks the UI uses to enable the "Test run" button:

- **Connections** — a step's action requires a connection and none is set on the step. Ask the user which existing connection to use; don't fabricate names.
- **Data** — a step's required action input is empty (the key is *present* but the value is empty). Patch the YAML if the value is obvious from the user's prompt; otherwise ask.
- **Runtime inputs** — the playbook declares required `inputs` with no `default`. Either give them defaults or have the user supply Test Parameters in the UI before running.

All clear means `trigger_test_run` can run it — no need to ask the user to click anything.

## Pre-save checklist

| Check | Root cause | Fix |
|---|---|---|
| A value starts with an unquoted `*` | Most often a cron expression (`*/5 * * * *`). YAML reads leading `*` as an alias reference — parse error. | Quote it: `cron: "*/5 * * * *"`. See [yaml-formatting.md](yaml-formatting.md). |
| An unquoted scalar contains `: ` (colon + space) | YAML reads it as a new mapping key — parse error. | Quote the value, or use a literal block scalar (`\|`) for multi-line/colon-containing text. See [yaml-formatting.md](yaml-formatting.md). |
| An unquoted scalar starts with `- ` (dash + space) | YAML reads it as a list entry — parse error. | Quote the value, or use a literal block scalar (`\|`). See [yaml-formatting.md](yaml-formatting.md). |
| The same key appears twice in one mapping | E.g. two `Content:` entries on a step. YAML doesn't error on this — it silently keeps the last one. | Decide whether the two were meant to be merged (e.g. into one block-scalar value) or whether one is leftover — don't just delete one blindly. See [yaml-formatting.md](yaml-formatting.md). |
| A text-typed parameter's bare value looks like a boolean/number | YAML coerces it (e.g. `NO` → `False`, `1.20` → `1.2`, `007` → `7`). | Quote the value so it round-trips as the literal string intended. See [yaml-formatting.md](yaml-formatting.md). |
| Top-level `workflow` present | The draft forgot the `workflow:` key. | Add a `workflow:` list with exactly one section (named `Steps`) containing steps. |
| No step calls this automation's own id | A step's `automations.<uuid>` points at the automation itself. | Remove the self-call — a subflow must never call itself. See [subflows/SKILL.md](../../subflows/SKILL.md). |
| Top-level `name` present and non-empty | Left out because it "looked optional". The engine fails the run with `schema should have a name`. | Always write `name:`. |
| `automation_type` present and valid | Missing (`automation type is required`), or a typo/invented value (`invalid automation type: <x>`). | Always write it — `on_demand`, `scheduled`, or `event`. |
| Every step has an `id` | A step is missing `id` — at any nesting level (branch, loop, try/catch, and switch children too). | Give it the current highest id + 1. |
| Every `id` is `S<integer>` | Lowercase `s1`, a leading zero (`S01`), a suffix (`S3a`), or a descriptive id (`fetch_users`). | Capital `S` plus an integer. New steps get the highest existing id + 1; don't renumber existing steps. |
| Every step has an `action` | Step has no action reference. | Look up the intended action via [looking-up-actions.md](looking-up-actions.md). Never invent action names. |
| Step ids are unique | Two steps share an id. | Give the newer step the highest existing id + 1, and update any `{{ steps.<id> }}` / `context.steps.<id>` references that meant it. |
| Every action is in the catalog | Action name typo or wrong service prefix. The save hook refuses these. | Grep `actions.tsv` by capability, read the candidate files (parallel reads), pick one, copy the exact `full_name`. |
| `automations.<uuid>` is callable | No callable row in `workspace/workflows/workflows-list.tsv`. Either the uuid is wrong/deleted, or the target workflow is a draft, deactivated, or not `on_demand` — none of those are callable as a subflow. | The row needs `automation_type=on_demand`, `state=published`/`modified`, and `active=true` — use its `id` column as `automations.<id>`. If it's not there yet, `fetch_automation` the intended workflow and ask the user to publish/activate it. Never invent an id. See [subflows/SKILL.md](../../subflows/SKILL.md). |
| `agents.<uuid>` is callable | No `published`/`modified` row in `workspace/agents/agents-list.tsv`. Either the uuid is wrong/deleted, or the agent has never been published. | Use the row's `id` column as `agents.<id>`. If it's not there yet, publish the agent first. Never invent an id. See the `generating-agent` skill. |
| Required inputs supplied | A required parameter wasn't supplied. | Re-read the action JSON for its `parameters[]`. Supply a value or **ask the user** — don't fabricate. |
| Enum values match | Value doesn't match the parameter's options. | Pick one of the listed options verbatim (case matters). |
| `{{ steps.Sx.output }}` ids exist | The reference points at a step that doesn't exist. | Correct the id. Remember flow-control children have their own ids. |
| Inline Python parses | Python in a `core.python`/`core.pythonV2` step has a syntax error. | Fix it. (Bodies containing `{{ }}` template expressions aren't standalone Python — check them by eye.) |
| No bare `context.steps` anywhere in the file | Python reads the whole steps dict (`context.steps`, `context.steps["S1"]`, `for s in context.steps`), or a comment/docstring/string mentions `context.steps` on its own. | Always follow it with a step id: `context.steps.S1.output`. Reword comments too. See [expressions.md](expressions.md) — Python and Bash steps. |
| Python never assigns `context.variables` | `context.variables.foo = …` inside a `core.python`/`core.pythonV2` step. | Print the values as a JSON map (`print(json.dumps({...}))`) and set them in an `internal.SetVariables` step after it, from `{{ steps.<id>.output.<key> }}`. |
| Condition sides have the same type | A boolean compared to a string (`Equals` + `rvalue: '"true"'`), or a number to a string. | Booleans → `Is true`/`Is false` with no `rvalue`; numbers → numeric `rvalue`; strings → quoted string `rvalue`. See [expressions.md](expressions.md) — Conditions. |
| Conditions are well-formed | `internal.ifCondition` / `internal.while` / trigger `condition` with `match: And`/`Or` and only one sentence, two sentences and no `match`, a missing `lvalue`, or an `op` outside the list. | One sentence → omit `match`. Two or more → `match: And` or `Or`. Copy `op` verbatim from [engine-validation.md](engine-validation.md) — Conditions. |
| No `continue_on_error: true` on flow control | Set on `internal.for`, `internal.ifCondition` or its branches, `internal.try`, `internal.catch`, or a switch step/branch. | Remove it; wrap the risky child step in `internal.try`/`internal.catch` instead. |
| `when` is only `true`/`false` | Used as a runtime condition (`when: "{{ … }}"`). On a top-level step the engine silently treats it as disabled and never runs it; inside a flow-control step it fails with `when condition must be a boolean`. | Use `internal.ifCondition`. |
| Top-level `inputs.<x>` is a parameter-definition object | It's a bare value instead of a `{type, required, default, ...}` object. | Wrap it as a full param definition. This isn't just a lint nit — the controller silently corrupts a bare value by indexing it char-by-char in storage. |
| Every top-level input has a `type` | Without it the UI can't render the input field for a manual run. | Add one of the valid types (`text`, `number`, `single-select`, ...). See the PlaybookInputParamDefinition shape in [handling-sparse-actions.md](handling-sparse-actions.md). |
| `internal.SetVariables` keys are capitalized | `Variables` entries used `name`/`type`/`value` instead of `Name`/`Type`/`Value`. | Capitalize the keys. Lowercase keys are silently dropped at runtime, not rejected. |
| Every `Variables` entry has `Name`, `Type`, `Value` | An entry is missing one, or `Variables` is empty (`variables is required`). | Supply all three keys on every entry. |
| `Variables[i].Name` is a valid variable name | Spaces, dashes, or a leading digit/underscore. | Match `^[a-zA-Z][a-zA-Z0-9_]*$` — e.g. `top_name`. Same rule for `name` on `internal.append_list`, `internal.extend_list`, `internal.increment_variable`. |
| `Variables[i].Type` is `String`, `Number`, `Boolean`, or `List` | A typo/casing issue (e.g. `number`) or an unsupported type (`Object`, `Array`). | Use one of the four verbatim — anything else fails with `variable type is invalid`. |
| `web_form_inputs` is a dict | Written as a list (easy mix-up with `SetVariables`' list shape). | Use a dict keyed by field name, e.g. `web_form_inputs: {my_field: {type: text, index: 1}}`. |
| No singular `connection:` key | Step declared `connection: <name>` instead of the plural map. | Use `connections: {<type>: <name>}`. The singular key is silently dropped at runtime. |
| No empty connection values | A step has `connections: {slack: ""}` because no connection was picked yet — `connection value is required`. | Omit `connections:` until there's a real name; call it out at handoff. |
| Engine-implemented actions have their required inputs | E.g. `internal.Sleep` without a valid `Mode`/`TimeUnit`, `internal.EndRun` without `Status`, `core.email` without `To`/`Subject`/`Content`, `agents.<id>` without `task`. | Check the action's row in [engine-validation.md](engine-validation.md). |
| `connections:` keys are the action's `connection_types` | A wrong or transposed integration slug (e.g. a multi-word vendor name with its parts reordered) — the action `full_name` is fine but the connection never binds at runtime. | Copy the slug from the action's `connection_types` in the catalog, and use that **same** slug for the action `full_name`, the step's `connections:` key, and the trigger. Never type a vendor slug from memory. |

## Loop protocol

Never leave YAML the controller rejected in the user's repo.

1. Draft to `/tmp/<name>.yaml` (scratch).
2. Run the checklist above against it.
3. Read the file and call `save_automation` with its content as `yaml` (and the `automation_id` when updating an existing one).
4. On success → promote the file to `workspace/workflows/<name>.yaml`.
5. On a refused call or a tool error → read **all** reported problems, apply fixes in one pass, re-save. Cap at **3 iterations**.
6. If iteration 4 would start, stop. Report the last errors and what you tried. Don't keep guessing.

For revisions on an **existing** automation: draft the edit into `/tmp/`, save from there, and only overwrite `workspace/workflows/<name>.yaml` once the save succeeds. Don't echo the new YAML in your response — the user reviews via `git diff`.

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

- `type` — required (the engine fails with `input type is required` without it); must be lowercase. Valid values: `text`, `long-text`, `rich-text`, `number`, `checkbox`, `single-select`, `multi-select`, `date`, `connections`, `file`, `list`, `dynamic_text`.
- `required` — if `true` and no `default` is set, the user must supply a value at run time.
- `index` — 1-based display order in the UI.
- `default` — optional; must be a valid value for the declared `type`.
- `options` — required for `single-select` and `multi-select` types.

### Required inputs and test runs

If any input has `required: true` and no `default`, the automation **cannot be test-run automatically** — the user must open the UI, fill in the Test Parameters, and click Test Run themselves. When this happens:

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
- Neither a key nor a `value` may be empty — the engine fails with `output key and value cannot be empty`.
- `value` is a `{{ ... }}` expression. Reference step outputs via `{{steps.<id>.output.<field>}}` or automation inputs via `{{inputs.<name>}}`.
- `index` controls display order in the UI; use sequential integers starting at 1 (a non-integer fails with `outputs: unsupported format`).
- When an action has `auto_pagination: true`, the runtime wraps pages as `{"results": [...]}` — access via `{{steps.<id>.output.results}}`.
- Omit `outputs:` entirely when the automation doesn't need to return structured data.

### Checking outputs

Broken `{{steps.X}}` references in `outputs:` aren't caught on save. Use the same step-id discipline here as in step inputs to avoid runtime surprises.

## Don't over-correct

Some fixes are judgment calls, not purely mechanical:

- A required param asks for `connections:` (the plural type→name map) and the user hasn't said which → **ask**. Don't pick arbitrarily.
- A catalog grep returns three similar actions and none clearly matches → **ask** which one the user intended.
