# Looking up actions

Referenced from [../SKILL.md](../SKILL.md) step 2. The catalog is a per-user cache of the user's Blink workspace, refreshed by the `SessionStart` hook. What's in a tenant depends on which integrations they've installed — **never assume an action exists; always confirm against the catalog.**

If the catalog is missing or empty when you go to look something up, stop and tell the user. The hook couldn't populate it — usually means `userConfig` isn't set, or the controller is unreachable. Don't try to refresh from inside the skill.

## Where it lives

`${CLAUDE_PLUGIN_DATA}/catalog/` (Claude Code injects `CLAUDE_PLUGIN_DATA` per plugin).

## Layout

```
catalog/
├── actions.tsv                        # greppable: full_name <TAB> service <TAB> description <TAB> connection_types
├── triggers.tsv                       # greppable: full_name <TAB> service <TAB> description
├── actions/<service>/<name>.json      # one action's full detail
└── triggers/<service>/<name>.json     # one trigger's full detail

workspace/                             # this workspace's own content — repo files, not the catalog cache
├── workflows/
│   ├── workflows-list.tsv             # greppable: id <TAB> name <TAB> automation_type <TAB> state <TAB> active
│   └── <name>.yaml                    # one fetched workflow
├── agents/
│   ├── agents-list.tsv                # greppable: id <TAB> name <TAB> state
│   └── <name>.yaml                    # one fetched agent
├── connections/
│   └── connections.tsv                # greppable: name <TAB> type_name — the workspace's bound connections
└── tables/
    └── tables-schema.yaml             # every table's schema (no row data)
```

Workflows and agents — the workspace's own callable actions — are not in the catalog at all; they live in the repo instead, as `workspace/workflows/workflows-list.tsv` and `workspace/agents/agents-list.tsv` (see below).

`connection_types` in `actions.tsv` is a comma-separated list of the connection types the action requires (empty string if none). Use it to pre-filter candidates at grep-time — **no need to read the action's JSON solely to discover its connection type**.

`workspace/connections/connections.tsv` is a workspace snapshot, not vendor catalog data — refreshed every session (no staleness gate), unlike `actions.tsv`/`triggers.tsv` which can lag up to 7 days. It lives in the repo rather than the catalog cache, so it also survives a catalog refresh. Use it to pick a connection name for a step (see [../SKILL.md](../SKILL.md) — Connections).

`workspace/workflows/workflows-list.tsv` and `workspace/agents/agents-list.tsv` list every workflow/agent, drafts included — a row is callable right now only once it's published (workflows also need `automation_type=on_demand` and `active=true`). Written by `list_workflows`/`list_agents`; re-run if a file looks stale. You never need these to avoid duplicates when saving: `save_automation`/`save_agent` look the name up live and update that workflow/agent in place.

## Lookup flow

For the full batching protocol (Round 1 parallel / Round 2 batched JSON read), see [../SKILL.md](../SKILL.md) Step 2.

1. **Grep `actions.tsv`** for the capability keyword. Example: `Grep pattern="send message" path="${CLAUDE_PLUGIN_DATA}/catalog/actions.tsv"`. The matching rows give you `full_name`, service, description, and connection type(s). For triggers, grep `triggers.tsv` instead.

2. **Pick candidates** — usually 1–3. If several look plausible and the right choice isn't obvious from the one-liner, **stop and ask the user which one they want**.

3. **Read all picked action files in one Bash call.** Use `tail -n +1 actions/svc1/a.json actions/svc2/b.json ...` — `tail -n +1` prints each file in full with `==> <path> <==` headers, so several JSONs come back in a single tool round-trip. Don't issue separate `Read` calls per file.

4. **Populate `inputs:`** from the parameter info:
   - `required: true` → must be present (unless `linked_hidden` gates it on another parameter).
   - `options: [...]` → value must be one of the options (case-sensitive).
   - `linked_hidden` → only applies when another parameter has a specific value; include only then.
   - `field_type: 2` + `autofill` present → dynamic dropdown; see **Autofill params** below.
   - No sensible default and user didn't provide a value → **ask the user**.

5. **Read the output shape** from `example_output` — use it to plan downstream `{{ steps.<id>.output.<field> }}` access. Watch for `auto_pagination`: when the action has that input and you set it `true`, the runtime wraps the output as `{"results": [...]}` regardless of what `example_output` shows. So a list-shaped `example_output` becomes a dict at runtime when paginating; access via `output.results` (or `context.steps.<id>.output["results"]` from Python).

## Autofill params (dynamic dropdowns)

When a parameter has `"field_type": 2` and an `"autofill"` block, its options come
from a live fetcher call at runtime. The `autofill.fetcher` field names the fetcher;
`autofill.inputs` declares which sibling parameters feed into it.

### Resolution protocol

**Step 1 — Does the user's prompt name a specific value?**
- Yes (e.g. "#general", "production environment", "blinkops org") → go to step 2.
- No + param is required → ask the user before drafting.
- No + param is optional → leave empty; note it at handoff.

**Step 2 — Resolve with the `fetch_options` tool**

Always pass `search` with the user's term. Provider APIs cap results (GitHub: 30,
Jira: 50, etc.) and the fetcher's `$query` filters server-side before the cap applies.

```
fetch_options(fetcher_name="<fetcher_name>", search="<user-term>", inputs='{"key":"val"}', connections='{"type":"name"}')
```

**Pass `connections` whenever the action has `connection_types`** (visible in
`actions.tsv` column 4, and in the action's JSON). Use the connection name you
already resolved for that step (from `list_connections`). Without it, fetchers
that call vendor APIs return 400. Example:

```
fetch_options(fetcher_name="azure.ListSubscriptions", connections='{"azure":"my-azure-conn"}')
fetch_options(fetcher_name="github.SearchRepos", inputs='{"owner":"google"}', connections='{"github":"my-gh-conn"}', search="gemini")
```

If `autofill.inputs` lists required sibling params, resolve those first (they may
themselves be autofill params — repeat this protocol for each dependency).

**Step 3 — Read the header line and act**

The first line of the tool's returned text tells you what happened:

| Header | Meaning | Action |
|---|---|---|
| `# N results` | Exact match found | Write the input as `{display_name: <label>, value: <value>}` — done. |
| `# no exact match for "X" — N partial matches, ask user to confirm` | Partial matches only | Show the candidates to the user and ask them to confirm which one. |
| `# 0 matches for "X" — showing all N server results` | No client-side match | Retry with a shorter term (step 4). |
| `# 0 results` | Server returned nothing | Retry with a shorter term (step 4). |

**Step 4 — Handle no-match**

1. Retry with a shorter / broader search term (e.g. "Fix login bug" → "login",
   full title → first keyword). Provider APIs cap results server-side (GitHub: 30,
   Jira: 50) — a full page of results is not exhaustive.
2. If still no match, decide whether the identifier is **public/static** or
   **private/dynamic** before reaching for WebSearch:

   - **Public/static** — a well-known identifier that exists independently of the
     user's account and is likely documented publicly (e.g. a Jira project key for a
     known open-source project, a public package name, a standard Azure region slug).
     → **Use WebSearch** to look it up. If the result is unambiguous, use it and
     continue. If WebSearch returns multiple plausible candidates, show them and ask
     the user to confirm.

   - **Private/dynamic** — a resource that lives inside the user's account or org and
     is only visible to authenticated users (e.g. a GitHub repo, a Jira issue, an
     Azure subscription, a Slack channel). The fetcher already has credentials; if it
     returned nothing, the resource either doesn't exist in the connected account or
     the user misspelled it. **Do NOT use WebSearch** — a public search can't see
     private resources and may return a same-named resource from a different org,
     causing a silently wrong automation. → Ask the user to confirm the exact name.

**When NOT to call `fetch_options`:**
- The user wrote the raw ID directly (e.g. `"C0123456789"`, `"42"`) — call `fetch_options` with `search: "<id>"` to verify it exists. Exact match → confirmed, use it. No match → warn the user the ID may be wrong.
- The value comes from a prior step — look at that step's action JSON to understand its output shape. If a specific field matches what's needed use `{{ steps.<id>.output.<field> }}`; if the shape is unknown use `{{ steps.<id>.output }}`. Never call `fetch_options` for step references — the value only exists at runtime.
- The param is optional and the user didn't mention it — leave empty.
- The workspace has no playbooks yet (the tool needs one for auth) — note the gap
  at handoff; the user can set the value in the UI dropdown.

**Error handling** (surfaces as a tool error, not returned text):
- `this fetcher references prior step outputs…` — the fetcher's template uses
  `{{Steps.SN.output}}` and needs a live execution context. Can't resolve statically.
  Leave the param empty, note it at handoff — user sets it via UI dropdown after a
  test run.
- `400: <msg>` — check you passed `connections` for vendor actions; re-read
  the action JSON to confirm required `autofill.inputs` and re-call with `inputs`.

## Common primitives

Flow-control and built-ins live under the `internal` and `core` services: `core.bash`, `core.pythonV2`, `http.requestV2`, `internal.for`, `internal.ifCondition`, `internal.SetVariables`, `internal.print`, `utility.GetEpochTime`, etc. They're real catalog entries — same parameter rules apply. Find them by greping `actions.tsv` or by listing `actions/internal/` and `actions/core/`.

## Calling another workflow

To call another workflow as a step, don't look it up here — there's no `playbooks.<name>` action. Grep **`workspace/workflows/workflows-list.tsv`** (in the repo, not the catalog; regenerate with `list_workflows` if it looks stale) and take its `id` column and write `automations.<id>` in the step. It's callable only when the row shows `automation_type: on_demand`, `active: true`, and state `published` or `modified` — otherwise (draft, deactivated, or not on-demand) see the `subflows` skill: publish it first, and never call it by name.

## When lookup fails

- **No grep hits** → broaden the keyword, or browse by vendor. If still nothing, tell the user — **don't invent a `full_name`**. The validator will reject unknown actions anyway.
- **Catalog empty or missing** (no `actions.tsv`) → the `SessionStart` hook couldn't populate it. Stop and ask the user to check `userConfig` and `${CLAUDE_PLUGIN_DATA}/refresh.log`.
