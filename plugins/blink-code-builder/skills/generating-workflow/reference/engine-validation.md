# Workflow engine validation

Referenced from [../SKILL.md](../SKILL.md) step 5 and [validation.md](validation.md). The workflow engine compiles the YAML every time it runs it — test runs, scheduled/triggered runs, and every subflow a run loads — and reports an incorrect or missing YAML property as a workflow validation error. **`save_automation` does not run this compile**, so a draft can save cleanly and still fail on its first run with:

```
Workflow <automation-id>: <message>
```

Every rule below is one of those validation errors. Check the draft against all of them before saving — the save won't catch them for you.

## Top level

| Rule | Error | Fix |
|---|---|---|
| `name` is set and non-empty | `schema should have a name` | Always write `name:`. |
| `automation_type` is set | `automation type is required` | Always write it, even for `on_demand`. |
| `automation_type` is one of `on_demand`, `scheduled`, `event` | `invalid automation type: <x>` | Use one of the three verbatim. |
| Every section has a name | `section should have a name` | `- section: Steps`. |
| Every top-level `inputs.<x>` has a string `type` | `input type is required` | Write a full definition with `type:` (see [validation.md](validation.md) — Input format). |
| `outputs` is a map of `key: "<expr>"` or `key: {value, index}` with an integer `index` | `outputs: unsupported format at line N` | Use one of the two shapes. |
| No output has an empty key or value | `output key and value cannot be empty` | Drop the entry or give it a `{{ … }}` value. |
| Don't emit `comments:` | `comment id is required`, `author is required`, `timestamp is required`, … | Comments are editor canvas notes. Leave them out; when revising a fetched automation, keep existing ones byte-for-byte. |

## Triggers

See [triggers.md](triggers.md) for the shapes.

| Rule | Error |
|---|---|
| At most **one** trigger in total, across all categories | `only one trigger is allowed` (also `only one <kind> trigger is allowed` for two in one list) |
| `scheduled` entry has a `cron` | `cron is required` |
| `cron` parses as 5 fields (minute hour day-of-month month day-of-week) or a descriptor (`@daily`, `@hourly`, `@every 15m`, …) — no seconds field | `cron expression is not valid: …` |
| `webhooks` entry has a `name` | `name is required` |
| `webhooks` `method`, if set, is `apikey` or `signature` | `method should be apikey or signature` |
| `polling` entry has `name` and `trigger_type` | `name is required` / `trigger type is required` |
| `polling` entry has `polling_interval_minutes` between 1 and 60 | `interval must be between 1 and 60 minutes` |
| Trigger `connections` keys and values are non-empty | `connection key is required` / `connection value is required` |
| Trigger `condition` follows the condition rules below | see **Conditions** |

## Every step

| Rule | Error | Fix |
|---|---|---|
| `id` is set — at every nesting level, including branch, loop, try/catch, and switch children | `step id is required` | `S<integer>` (`S1`, `S2`, …), unique across the flattened workflow. New steps get the highest id + 1 — see guideline 1 in [../SKILL.md](../SKILL.md). |
| `action` is set | `step action is required` | Copy `full_name` from the catalog. |
| `action` is a string | `action <x> is not a string` | Write the `full_name` as a plain string, never a number, list, or map. |
| `when`, if set, is `true` or `false` | `when condition must be a boolean` (nested steps). On a top-level step any non-boolean value — e.g. `'{{ … }}'` — raises no error: the engine treats the step as disabled and silently never runs it. | `when` only disables a step; it is not a runtime condition. Use `internal.ifCondition` for that. |
| `mock_output_enabled: true` comes with `selected_mock_output_state` of `completed`, `failed`, or `timeout` | `invalid mock output state: …` | Don't mock unless the user asked. |

### Step-level options

| Rule | Error |
|---|---|
| `connections` keys and values are non-empty — if no connection is known yet, **omit the key**, don't write `slack: ""` | `connection key is required` / `connection value is required` |
| `timeout` (seconds) is ≥ 0 | `invalid timeout: <n>` |
| `save_output_to_file: true` comes with `output_file_identifier` | `output file identifier is required when using save output to file option` |
| `retry: true` comes with `max_retries` 1–5 | `max retries must be between 1 and 5: <n>` |
| … and `interval_type` `exponential` or `constant` | `invalid interval type: <x>` |
| … and a non-empty `retry_status_codes` list — codes 100–599 or ranges like `"500-599"` | `status codes cannot be empty` / `invalid status codes: <x>` |
| … and, for `constant`, `time_between_retries_in_mill` ≥ 0 | `invalid time between retries: <n>` |

```yaml
retry: true
max_retries: 3
interval_type: exponential
retry_status_codes: ["429", "500-599"]
```

## Flow control

| Step | Rule | Error |
|---|---|---|
| `internal.for` | `inputs.items` set | `items are required` |
| `internal.for` | no `continue_on_error: true` | `continue on error is not supported for loop step` |
| `internal.for` | no `output_transform` | `output transformation is not supported for loop step` |
| `internal.ifCondition` | `inputs.condition` follows **Conditions**; children are `internal.ifTrueBranch` / `internal.ifFalseBranch`, each with its own `id` | see Conditions |
| `internal.ifCondition` + branches | no `continue_on_error: true` | `continue on error is not supported for if step` / `… for if branch` |
| `internal.while` | `inputs.condition` follows **Conditions** | see Conditions |
| `internal.try` / `internal.catch` | no `continue_on_error: true` | `continue on error is not supported for try step` / `… for catch step` |
| `internal.switchCaseExpression` | `inputs.switch_expression_inputs.expression` set | `switch expression is required` |
| `internal.switchCaseExpression` | at least one child, and the **last** child is `internal.switchCaseDefaultLabel` | `switch case must have at least one case or default branch` / `default case must be the last step in switch` |
| `internal.switchCaseLabel` | `inputs.case_label_inputs.labels` is a non-empty list of non-empty strings | `case labels are required for switch case label` / `case label <i> cannot be empty` |
| `internal.switchCaseDefaultLabel` | no labels | `default case should not have labels` |
| switch children | only `switchCaseLabel` / `switchCaseDefaultLabel`; no `continue_on_error: true` anywhere in the switch | `invalid switch case branch action` / `continue on error is not supported for switch case …` |

```yaml
- action: internal.switchCaseExpression
  id: S1
  inputs:
    switch_expression_inputs:
      expression: '{{ inputs.severity }}'
  steps:
    - action: internal.switchCaseLabel
      id: S2
      inputs:
        case_label_inputs:
          labels: [high, critical]
      steps: [...]
    - action: internal.switchCaseDefaultLabel
      id: S3
      steps: [...]
```

### Conditions

Used by `internal.ifCondition`, `internal.while`, and trigger `condition`:

```yaml
condition:
  match: And          # omit for a single sentence
  sentence:
    - lvalue: '{{ steps.S1.output.count }}'
      op: Greater than
      rvalue: '10'
    - lvalue: '{{ inputs.env }}'
      op: Equals
      rvalue: '"production"'
```

| Rule | Error |
|---|---|
| `match` is omitted, `And`, or `Or` (capitalized) | `invalid match type: <x>` |
| No `match` → exactly one sentence | `empty match should have only one sentence` |
| `match: And`/`Or` → at least two sentences | `non-empty match should have at least two sentences` |
| Every sentence has an `lvalue` | `lvalue is required` |
| `op` is exactly one of: `Equals`, `Not equals`, `Less than`, `Less or equals to`, `Greater than`, `Greater or equals to`, `Contains`, `Not Contains`, `Starts with`, `Ends with`, `Regex match`, `Is empty`, `Is not empty`, `Is true`, `Is false` | `invalid operator type: <x>` |

## Variables and lists

Variable names everywhere below must match `^[a-zA-Z][a-zA-Z0-9_]*$` — `variable name is required` / `variable name cannot contain spaces` / `variable name can only contain alphanumeric characters and underscores, and must start with a letter`.

| Step | Inputs | Errors |
|---|---|---|
| `internal.SetVariables` | `Variables`: non-empty list; each entry has `Name`, `Type`, `Value` | `variables is required`, `variable type is required`, `variable type is invalid` |
| `internal.SetVariable` | `Name`, `Type`, `Value` | same |
| both | `Type` is exactly `String`, `Number`, `Boolean`, or `List` | `variable type is invalid` |
| `internal.append_list` | `name` (valid variable name) | `name is required` |
| `internal.extend_list` | `name`; `list_to_append` (a valid variable name) or `item` | `name is required`, `list to append or item is required` |
| `internal.increment_variable` | `name` | `name is required` |

## Engine-implemented actions — required inputs

| Action | Required inputs | Errors |
|---|---|---|
| `internal.EndRun` | `Status`: `Completed` or `Failed` | `end run status is invalid` |
| `internal.Sleep` | `Mode`: `Time Interval`, `Web Form Response`, or `Automation Execution` | `wait mode <x> is not valid` |
| ↳ `Time Interval` | `Duration` (> 0), `TimeUnit`: `Seconds`, `Minutes`, or `Hours` | `time unit <x> is not valid`, `timeout duration cannot be empty`, `duration must be greater than 0` |
| ↳ `Web Form Response` | `WebFormTimeoutUnit` (same units); `WebFormTimeoutDuration`, if set, > 0 | `time unit <x> is not valid`, `duration must be greater than 0` |
| ↳ `Automation Execution` | `AwaitExecMode`: `All async automation executions`, `Step ID` (needs `Steps`), or `Execution ID` (needs `Executions`) | `async subflow await mode <x> is not valid`, `stepIds cannot be empty`, `executionIds cannot be empty` |
| `core.email`, `core.emailV2` | `To`, `Subject`, and `Content` or `rich_text_content` | `To is required`, `Subject is required`, `Content or rich_text_content is required` |
| `core.python`, `core.pythonV2` | `code` | `code is required` |
| `internal.new_interactive_web_form` | `web_form_flow` (`Single Step`, `New Interactive`, or `Existing Interactive`), `access_control`, `header`, `web_form_inputs` (non-empty) | `web form flow '<x>' is invalid`, `access control cannot be empty`, `header cannot be empty`, `web form inputs cannot be empty` |
| `system.SlackInteractivity` | `Blocks`; `actionType`: `Send Message` (needs `To`), `Update Message` (needs `channel`, `ts`), or `Send Ephemeral Message` (needs `channel`, `user`) | `Blocks input cannot be empty`, `invalid slack interactivity action type: …`, `To input cannot be empty for Send Message`, `channel input cannot be empty for …`, `ts input cannot be empty for Update Message`, `user input cannot be empty for Send Ephemeral Message` |
| `agents.<id>` | `task`; `output_schema`, if set, must be valid JSON | `task cannot be empty`, `output_schema must be a valid JSON` |
| `agents.RunAgent` | the `agents.<id>` inputs plus `agent_id: {display_name, value}` with a `value` | `agent_id cannot be empty` |
| `internal.dynamic_subflow` | `router_id: {display_name, value}` with a `value` | `router_id is required` |
| `internal.CreateGlobalVariable`, `internal.UpdateGlobalVariable` | `Name: {display_name, value}` with a `value`, and `Value` | `Name is required`, `Value is required` |
| `internal.GetGlobalVariable`, `internal.DeleteGlobalVariable` | `Name: {display_name, value}` with a `value` | `Name is required` |
| `utility.GetEpochTime` | `time`, `format` | `time is required`, `format is required` |
| `utility.GetTimezone` | `Type` (`Current Workspace` or `Latitude/Longitude`); `Latitude` and `Longitude` when `Latitude/Longitude` | `Type is required`, `Latitude is required`, `Longitude is required` |
| `utility.GetTimezoneOffset` | `Name` | `Name is required` |
| `utility.GeneratePassword` | `name`, `password_len`, `include_special_chars`, `include_digit_chars`, `include_uppercase_chars`, `include_lowercase_chars` | `<field> is required` |

Other actions (vendor actions, `http.*`, `tables.*`, other `system.*`) have no engine-side input validation — the catalog's `parameters[]` is the reference.
