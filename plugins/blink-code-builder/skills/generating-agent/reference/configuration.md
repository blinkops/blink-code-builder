# Agent configuration reference

Field-by-field guidance for an agent YAML. Read before writing `role` — it is the field that
decides whether the agent works.

## How the agent actually runs

This matters because it changes how `role` should be written.

At runtime the agent is a **tool-selection loop**, not a general assistant. Each turn it may
only do one of three things: select up to N abilities to run, say the task is complete, or say
it cannot complete the task for a named missing capability. It is explicitly forbidden from
answering, advising, summarizing, or asking the user questions in that stage. A separate stage
writes the final answer afterwards.

`role` and `constraints` are injected verbatim into **both** stages, as `<roles>` and
`<constraints>` blocks.

> Derived from how the Blink service prompts the agent in each of the two stages. Those
> prompts are versioned per model, so treat the guidance below as observed behavior rather
> than a contract — re-check it if an agent stops choosing abilities the way this describes.

## `role` — the system prompt

### Write selection criteria, not personality

The selection stage maps *request → ability*. Persona text does not affect that mapping, and
the stage cannot act on it anyway.

```yaml
# Weak — nothing here helps it choose an ability
role: |
  You are a thorough, detail-oriented SOC analyst who cares about accuracy
  and always explains findings clearly to your colleagues.

# Strong — every line is a decision rule
role: |
  Triage inbound security alerts.
  - When an alert names an external IP or domain, run "Enrich Observable" before deciding.
  - When enrichment returns a reputation score above 70, run "Create Case".
  - When the alert names an internal host only, do not enrich — go straight to a verdict.
  - If no ability covers what was asked, say so instead of using a near-miss one.
```

### Name abilities the way the agent sees them

The agent sees each ability as a tool named after its **workflow name**. Referring to
"Enrich Observable" when the workflow is called `Enrich Observable` makes the mapping direct.
Vague references ("look up the IP") force the model to guess.

### State parameter defaults in the role

The selection stage is forbidden to ask the user for values. It uses a parameter's default
silently, or leaves a required one for the UI to collect. So if a value should be constant,
say it in the role:

```yaml
role: |
  When creating a case, always use severity "medium" unless the alert says otherwise.
```

### Say what to do when nothing fits

The only escape hatch is *"Cannot complete: [missing capability]"*. Without guidance, a model
under pressure to act will pick a near-miss ability. Tell it not to:

```yaml
role: |
  If no ability covers the request, state the missing capability. Never substitute a
  different ability that is only partly right.
```

### Don't put output formatting in `role`

Formatting belongs to the final-answer stage or, when a workflow calls the agent, to the
step's `output_schema` input. Format instructions in `role` also reach the selection stage,
where they are noise.

### Keep it short and declarative

Rules, not prose. The role sits inside an already-long system prompt at temperature 0. A dozen
sharp lines beat three paragraphs.

## `constraints` — hard limits

Reaches both stages like `role`, but reads as prohibitions. Put boundaries here and decision
logic in `role`.

```yaml
constraints: |
  Never act on production hosts.
  Stay within the observables listed on the alert — do not investigate adjacent assets.
  Do not close or modify a case.
```

## `abilities`

Each entry:

| Field | Default | Meaning |
|---|---|---|
| `ability_id` | — | Required. The uuid of a published, active, **on-demand** workflow in this workspace. |
| `type` | `workflow` | The only value that exists. Omit it. |
| `auto_approved` | `false` | See below. |
| `summarize_output` | `false` | Have the output summarized before the agent reasons on it. Use for large outputs. |
| `summarize_output_instructions` | `""` | What the summary should keep. Only meaningful with `summarize_output: true`. |

Get `ability_id` from `workspace/workflows/workflows-list.tsv`: a callable row's (`automation_type=
on_demand`, `state=published`/`modified`, `active=true`) `id` column is
`automations.<uuid>` — the `ability_id` is that uuid without the prefix.

An ability can **only** be a workflow. There is no ability type for a vendor action, and no
way to attach another workspace's workflow. To give the agent a vendor capability, wrap the
vendor action in a small on-demand workflow, publish it, and attach that.

### `auto_approved` — the one field to be deliberate about

`false` (the default): when the agent decides to run that ability, the platform records a
pending-approval event and waits for a person to approve before the workflow executes.

`true`: no pause, ever. On every future session and every workflow step.

Why this deserves a decision rather than a copy-paste:

- **The agent chooses, not you.** With a workflow you know exactly what runs. With an agent
  you wrote a goal in prose; the model picks which abilities to use, how often, and with what
  inputs. `auto_approved: true` on *"Disable User Account"* means the model can disable
  accounts based on its own reading of an alert.
- **It's permanent** until someone edits and republishes the agent.
- **Where runtime workflow authoring is enabled**, an agent can create a workflow and run it
  on a later turn — so the set of things it can do is not fully fixed by your ability list.
- **Nothing else warns you.** No server-side check, no runtime prompt.

Reasonable rule of thumb: read-only enrichment and lookups are fine auto-approved — waiting on
a reputation lookup is pure friction. Anything that changes state outside Blink should stay
approval-gated.

`publish_agent` returns `[BLOCKED SAFETY]` listing every auto-approved ability, and only
proceeds after an explicit user yes passed as `acknowledge_risks: true`.

## `modes`

```yaml
modes:
  chat_enabled: true            # UI label: "Interactive mode"
  code_execution_enabled: false # UI label: "Agent generated workflows"
```

Both default to `false`.

| Key | UI label | Meaning |
|---|---|---|
| `chat_enabled` | Interactive mode | Users can hold a conversation with the agent. Also required before the agent can be shared to the portal, and before an ability's auto-approve toggle can be edited in the UI. |
| `code_execution_enabled` | Agent generated workflows | The agent may **compose brand-new workflows at runtime** from its abilities, and run them. |

**Task mode is always on and has no field** — that's what makes an agent usable from a workflow
step. The UI says so explicitly: *"Agents always have Task mode enabled, this allows them to be
used in workflows."*

### `code_execution_enabled` deserves the same care as `auto_approved`

With it on, the agent's capability is no longer bounded by the ability list you wrote. It can
author a new workflow that composes its abilities — including `core.pythonV2` / `core.jq` steps
for transformations — persist it in the workspace, and call it. Useful when the agent needs to
loop an ability over many inputs or reshape a large output; risky in combination with
auto-approved destructive abilities, because the agent can build the composition itself.

Leave it `false` unless the user asks for it, and say what you chose.

> Careful with these keys: the controller stores `modes` as one JSON object, so a sub-key
> missing from a save is stored as `false`. `save_agent` always sends both keys explicitly, so
> enabling one mode never silently turns the other off — but if you hand-edit the YAML, list
> both.

## `name`, `title`, `description`

- `name` — unique in the workspace, and the key `save_agent` matches on to decide update vs
  create. Renaming an agent in the YAML creates a **new** agent unless you pass the existing
  `agent_id`.
- `title` — short label; the UI shows `name | title` in step pickers.
- `description` — one sentence. Read by people choosing which agent to call, so describe what
  it decides, not how.

## `knowledge` and avatar fields

`knowledge`, `avatar_style`, `avatar_color`, `avatar_logo` are managed in the Blink UI.
`fetch_agent` preserves whatever the server has, and when your YAML **omits** one of these
keys `save_agent` carries the server's current value forward — so editing an agent never wipes
them, even in a file you wrote by hand. This plugin cannot create them: uploading a knowledge
file needs a multipart attachment upload plus an indexing step, so ask the user to do it in the
agent builder (max 10 files, and unavailable on tenants using their own Bedrock keys).

Writing an **explicit** empty value (`knowledge: []`) does still remove them — the controller
deletes the underlying attachments on publish. Only do that when the user asked for it.

**Never copy a `knowledge:` block from one agent to another.** The attachment is a single
shared row; when the original owner publishes, stale-attachment cleanup can delete the file
out from under the second agent, with no local signal that it happened.

## Built-in tools

Beyond the abilities you attach, the agent gets these automatically:

| Tool | Present when | Configured by |
|---|---|---|
| Direct answer — answers from general knowledge, ends the run, executes nothing | always | nothing; cannot be turned off |
| Knowledge-base search | the agent has knowledge files | add/remove knowledge files (UI only) |
| Author a new workflow at runtime | `modes.code_execution_enabled` is true | that key |

None of them appear in `abilities` and none can be added or removed there. So don't describe an
agent to the user as limited strictly to its ability list — with the direct-answer tool it can
always respond without running anything, and with `code_execution_enabled` it can build new
workflows.
