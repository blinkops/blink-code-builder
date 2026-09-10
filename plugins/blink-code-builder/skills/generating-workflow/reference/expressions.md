# Writing Blink expressions

Referenced from [../SKILL.md](../SKILL.md) step 3. Blink evaluates step inputs with **Go expr**, not Jinja. Output is a string; `{{ ... }}` inside a YAML scalar is substituted before the step runs.

## Scopes

| Expression | Means |
|---|---|
| `{{ inputs.<name> }}` | Value of an automation-level input (declared under top-level `inputs:`). |
| `{{ steps.<S_ID>.output }}` | Full output of a prior step. |
| `{{ steps.<S_ID>.output.<field> }}` | Nested field of a prior step's output. Check the action's `example_output` to know what fields exist. |
| `{{ steps.<S_ID>.status }}` | Step result — `'OK'` or `'Failed'`. Used in `internal.ifCondition`. |
| `{{ steps.<S_ID>.item }}` | Current item inside an `internal.for` loop that has `variable_name: <x>`. `.item.<field>` works too. |
| `{{ variables.<name> }}` | Execution variable set by `internal.SetVariables`. |
| `{{ variables.<name>.<field> }}` | Nested field of an execution variable. |
| `{{ event.payload.* }}` | Event payload on event-triggered automations. |
| `{{ metadata.<name> }}` | Automation metadata. Available names: `automation_name`, `automation_url`, `automation_id`, `tenant_id`, `workspace_id`, `pack_id`, `execution_id`, `execution_url`, `start_time`, `user_email`, `user_groups`. Use directly — don't add a step to fetch these. |

Only reference real values from these scopes. If a value isn't available, ask the user rather than fabricate.

## YAML-quoting rules

Single-quote any scalar that starts with `{{` or contains both `{{` and `:`:

```yaml
inputs:
  url: 'https://example.com/{{ inputs.path }}'
  items: '{{ steps.S2.output }}'
```

Multi-line content can use block scalars — no quoting needed:

```yaml
inputs:
  text: |-
    Hello {{ inputs.email }}!
    Current time: {{ date_format("15:04:05", steps.S1.output) }}.
```

## Builtins

Blink builds on `expr-lang/expr` (https://expr-lang.org/) for the language itself — operators, slicing, `len`, `contains`, `in` — and adds custom builtins on top. Three docs pages are the canonical reference:

- [Variables](https://docs.blinkops.com/docs/workflows/building-workflows/dynamic-variables/expression-language/workflow-engine-variables) — scope reference (`inputs`, `steps`, `event`, `metadata`, `variables`).
- [Operators](https://docs.blinkops.com/docs/workflows/building-workflows/dynamic-variables/expression-language/workflow-engine-operators) — operators and language-level functions (`all`, `any`, `filter`, `map`, `count`).
- [Functions](https://docs.blinkops.com/docs/workflows/building-workflows/dynamic-variables/expression-language/workflow-engine-functions) — canonical builtin reference. Date math (`now`, `add`, `sub`, `today`, `to_epoch`, `date_format`), string ops (`split`, `replace`, `trim`, `to_lower`/`to_upper`, `encode_base_64`), array ops (`empty`, `reverse`, `range`), JSON helpers (`prettify_json`, `merge_maps`), encoding (`md5`, `random_uuid`, `query_escape`).

When tempted to add a `core.pythonV2` step for date arithmetic, string formatting, simple field extraction, or list-shape checks, check the **Functions** page first — these are usually one expression call inside `internal.SetVariables`.

**Go date-format gotcha**: `date_format(layout, time)` uses Go's reference time, not Java/Python tokens. "format as `YYYY-MM-DD`" → `"2006-01-02"`. `HH:MM` → `"15:04"`. Writing `YYYY-MM-DD` as the layout produces literal text — silently wrong.

## Conditions (`internal.ifCondition`)

```yaml
- action: internal.ifCondition
  id: S4
  inputs:
    condition:
      match: And                    # And | Or
      sentence:
        - lvalue: '{{ inputs.data_type }}'
          op: Equals                 # Equals | Not equals | Greater than | ...
          rvalue: '"a literal string"'
```

Notice `rvalue` wraps a literal string in quotes *inside* the YAML string (`'"..."'`). That's deliberate — expr evaluates the rvalue, so a bare string identifier would be read as a variable.

## Python and Bash steps

Inside `core.pythonV2`, access workflow data via `context.*` — **not** `{{ … }}` interpolation:

- `context.inputs.<name>` — automation inputs.
- `context.steps.S1.output` — full output of a prior step (already a Python object — don't `json.loads()` it).
- `context.steps.S1.output["field"]` / `.field` — nested access.
- `context.steps.S1.status` — `"OK"` or `"Failed"`.
- `context.variables.<name>` — execution variables (assignable: `context.variables.foo = value`).
- `context.event.payload` — event payload (event-triggered automations).
- `context.metadata.<name>` — automation metadata.

**Why not `{{ steps.S1.output }}` inside Python?** Blink renders structured outputs into the `code:` string as Python repr (single quotes, `True`/`None`) — `json.loads()` fails at column 1. `context.steps.S1.output` gives you the parsed object directly.

`core.bash` only supports `{{ … }}` interpolation. Quote shell args carefully to avoid word-splitting on interpolated values.

## Validation

`validate_automation` checks that `{{ steps.<id>.output... }}` references a declared step id. It does not parse expression bodies — syntax errors in `len(...)`, `date_format(...)`, or bad `rvalue` quoting only surface at runtime. Re-read the examples above when writing a new expression.
