# Handling sparse actions

When the catalog entry for an action isn't enough to author it confidently — required parameter with no description, no example output, an opaque shape, an enum with no listed values — don't guess. The cost of a wrong guess is a broken save or a confusing run-time error the user has to untangle.

## Strategies

Try them in roughly this order. Stop as soon as one gives you enough certainty.

1. **Look at sibling actions in the same service.** Catalog entries for `slack.*`, `aws.ec2_*`, etc. tend to share parameter shapes. If `slack.send_message` has full metadata and `slack.send_message_interactive` doesn't, the missing fields are usually a near-superset.
2. **Search the user's `workspace/workflows/` directory and any committed YAML for prior usage.** A working example in the repo beats any inferred shape. `grep -rn 'action: <full_name>' workflows/` is cheap.
3. **Check the case studies below.** Frequently-tripped-over actions are recorded here — exact shapes learned from real runs.
4. **Run a tiny test in scratch.** Draft a minimal automation in `/tmp/`, save it, trigger a test run with `trigger_test_run`, and read the error. This is fast (one round-trip) and the controller's validation messages are usually specific enough to reverse-engineer the shape.
5. **Ask the user** — last resort, but always preferable to fabricating values for a required field. Frame the question concretely ("the catalog says `Variables` is required but doesn't describe its shape; do you have an example, or can you point me to a working playbook that uses it?") rather than asking the user to fill in a blank.

When you learn a shape this way, **add it as a case study below** so the next run doesn't repeat the work.

## Case studies

Each entry: what the catalog says vs. what the action actually wants, with the rule and a minimal example. Keep these tight; they're a reference, not a tutorial.

## `internal.SetVariables` — `Variables` shape

Catalog JSON says `Variables` is required, with no description or options. Actual shape is a list of objects with **capitalized** keys:

```yaml
- action: internal.SetVariables
  id: S4
  inputs:
    Variables:
      - Name: top_population
        Type: Number
        Value: '{{ 0 }}'
      - Name: top_name
        Type: String
        Value: ""
```

Rules:

- Keys are `Name`, `Type`, `Value` — capitalized. Lowercase `name`/`value` get silently dropped (controller stores empty entries).
- `Type` is a required enum. Confirmed values: `Number`, `String`, `List`. Lowercase (`number`, `string`) raises `SetVariableInvalidTypeErr`. Likely valid: `Boolean`, `Object`, `Array` (untested — confirm before relying on them).
- `Type: Number` is what makes `internal.ifCondition`'s `Greater than` op compare numerically. With `Type: String` (or empty) it compares lexicographically — `"3422000" > "1417492000"` is true.

Reference set variables downstream as `{{ variables.<Name> }}` (lowercase prefix, original-case Name).

## `tables.AddRecordV2` — `record` shape

Catalog JSON describes `record` only as "Specify the content for each field in
the new record," with no shape hint. Actual shape is a **list** of
`{key, value}` objects, one per column — not a flat map of column name → value:

```yaml
- action: tables.AddRecordV2
  inputs:
    table: past_jokes
    record:
      - key: name
        value: "{{event.payload.joke}}"
```

Rules:

- `record` is a list, not a map. Each item is `{key: <column name>, value: <cell value>}`.
- `table` takes the table's name — case-management tables and regular workspace tables use this same action shape.
- Column names aren't in the catalog. Confirm them with the user, or by grepping `workspace/workflows/` for a prior use of the same table, before writing `key:`.

## `internal.new_interactive_web_form` — `wait_for_response: true` doesn't wait in a Test Run

Observed in a Test Run: the step completed in ~20 ms instead of blocking, so the workflow proceeded with the form URL as `output` and no user response captured. Unverified for normal (non-test) executions — may behave correctly there. If you need a runtime user prompt **during a Test Run**, prefer top-level `inputs:` with a `single-select` `PlaybookInputParamDefinition` — that mechanism is reliable in both modes. For non-test runs, the form may still be the right tool; ask the user before swapping it out.

## `web_form_inputs` — dict keyed by field name, not a list

Easy to mix up with `internal.SetVariables`' `Variables` (a list of objects). `web_form_inputs`
(used by both `internal.new_interactive_web_form` and `internal.new_dynamic_web_form`) is instead
a **dict keyed by the field name**:

```yaml
web_form_inputs:
  question:
    index: 1
    name: question
    display_name: Question
    type: single-select
    options: [largest_population, longest_official_name]
```

A list here (`web_form_inputs: [{name: question, ...}]`) fails validation/is silently mishandled — the validator now flags this structurally.

## Web form flow — step-based (ask, then continue)

No end-to-end example existed for this; only the gotcha below was recorded. Correct order:

```yaml
name: Learn Single Step Web Forms
automation_type: on_demand
workflow:
  - section: Steps
    steps:
      - action: internal.new_interactive_web_form
        id: S1
        name: Create Web Form
        inputs:
          access_control: Anyone with link (public)
          cta: Submit
          description: Please fill out this form
          header: Sample Web Form
          web_form_flow: Single Step
          web_form_inputs:
            textual_input_example:
              index: 1
              name: textual_input_example
              type: text
          with_logo: false
          with_metadata: false
      - action: internal.print
        id: S2
        name: Print Web Form URL
        inputs:
          text: "Web form URL: {{steps.S1.web_form_url}}"
      - action: internal.Sleep
        id: S3
        name: Wait for Web Form Response
        inputs:
          Mode: Web Form Response
          WebFormTimeoutUnit: Hours
      - action: internal.print
        id: S4
        name: Print Web Form Output
        inputs:
          text: "Web form response: {{steps.S1.output.response.textual_input_example}}"
```

Rules:

- Order matters: create form → share the URL → `internal.Sleep` with `Mode: Web Form Response` → read the answer.
- The answer only exists after submission, under `steps.<form_step_id>.output.response.<input_name>`. Before that, `output` only holds the form URL.
- Each key inside `web_form_inputs` is one form field; its nested `name` must match that key.

## Web form flow — trigger-based (react to submissions)

Use a `webforms` trigger instead of a step when the automation should start the moment someone submits the form — no wait step needed:

```yaml
name: Let users submit jokes
automation_type: event
triggers:
  webforms:
    - trigger_type: blink_web_form
      inputs:
        WebFormTimeoutUnit: Hours
        access_control: Anyone in this tenant
        cta: Submit Joke
        header: Give me your best joke
        wait_for_response: true
        web_form_flow: Single Step
        web_form_inputs:
          joke:
            index: 1
            name: joke
            type: long-text
            required: false
workflow:
  - section: Steps
    steps:
      - action: tables.AddRecordV2
        id: S1
        name: Store Joke
        inputs:
          table: past_jokes
          record:
            - key: name
              value: "{{event.payload.joke}}"
      - action: internal.web_form_interactive_set_status
        id: S2
        name: Set Interactive Web Form Status
        inputs:
          status: Success
          title: Joke Entered Successfully
          text: Thanks for participating
```

Rules:

- The submitted values land directly in `event.payload.<input_name>` — no wait step needed.
- Use `internal.web_form_interactive_set_status` afterwards to show the submitter a success/failure message.

## Top-level `inputs:` — `PlaybookInputParamDefinition` shape

`inputs.<name>` at the top of a playbook is **not** a default-value map. Each entry must be a parameter definition object — same shape `web_form_inputs` uses on the form action:

```yaml
inputs:
  question:
    name: question
    display_name: Question
    type: single-select
    index: 1
    required: true
    options_type: static
    options:
      - largest_population
      - longest_official_name
    default: largest_population
```

Bare `inputs.question: largest_population` errors at runtime with `cannot unmarshal string into PlaybookInputParamDefinition`, and the controller's storage layer corrupts the value — it indexes the string char-by-char into `{"0":"l","1":"a",...}` and adds `index: 1`.

The shape isn't documented; it was reverse-engineered from that corrupted storage and from the `web_form_inputs` example.
