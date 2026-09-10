# Trigger types

Sets the top-level `automation_type:` and (when needed) the `triggers:` block. Pick the type from the user's prompt:

| `automation_type` | Use when... | `triggers:` block |
|---|---|---|
| `on_demand` (default) | runs only when a human clicks Run / API call / parent flow invokes | omit entirely |
| `scheduled` | "every day at 9", "every 15 minutes", any cron-like cadence | required, `scheduled:` list |
| `event` | reacts to a vendor event (new PR, new email, alert, ...) or a custom webhook | required, `webhooks:` or `polling:` list |

The `triggers:` block is a map keyed by **category** (`scheduled`, `webhooks`, `polling`), and each category's value is a **list** even with one entry. Vendor triggers go inside the list as objects with `trigger_type`, `name`, and optionally `inputs`.

> **Top-level `inputs:` are only valid for `on_demand`.** Scheduled and event automations run from a fixed signal — there's no caller to provide input values. For event automations, read from the trigger payload via `{{ event.payload.* }}` instead.

> **Sanity-check after save.** Trigger shape mistakes silently strip on save (the controller parses but doesn't echo bad shapes back). Always re-read `GET /playbooks/{id}/draft` after a save and confirm the `triggers:` block survived. Wrong shapes don't fail validation but break the trigger card in the UI ("click to select an event").

## `on_demand`

```yaml
automation_type: on_demand
# no triggers: block
```

## `scheduled`

```yaml
automation_type: scheduled
triggers:
  scheduled:
    - cron: "0 9 * * *"
```

Standard 5-field UTC cron. The map form (`scheduled: { cron: "..." }`) is rejected — the target is a Go slice, so wrap each entry in a list. The catalog does not ship a JSON schema for `scheduled` triggers; use the shape above directly.

Scheduled triggers don't carry a meaningful payload — use `{{ Now() }}` / `{{ date_format(...) }}` for the firing time. See [expressions.md](expressions.md).

## `event` — webhook

Default for `automation_type: event`. The trigger fires on an inbound HTTP call to the playbook's webhook URL, which Blink generates after save.

```yaml
automation_type: event
triggers:
  webhooks:
    - trigger_type: custom_webhook
      name: Custom Webhook
```

For a vendor webhook trigger (e.g. `crowdstrike.CrowdStrikeRunWebhook`), use the trigger's `full_name` from `triggers.tsv` and a human-readable `name`:

```yaml
triggers:
  webhooks:
    - trigger_type: crowdstrike.CrowdStrikeRunWebhook
      name: CrowdStrike Anomalous User Alert
```

If you can't find the requested trigger in `triggers.tsv`, fall back to `custom_webhook` — that's the universal generic webhook entry.

## `event` — polling

For vendor triggers that **poll** an external service (GitHub, Jira, email-on-new-message, ...). These appear in `triggers.tsv` with names like `github.OnNewPullRequest`, `github.OnMergedPullRequest`, `email.OnNewMessage`. Use `polling:` instead of `webhooks:`, and include the trigger's required `inputs`.

```yaml
automation_type: event
triggers:
  polling:
    - trigger_type: github.OnNewPullRequest
      name: On New Pull Request
      inputs:
        owner:
          value: mycompany
          display_name: mycompany
        repo:
          value: myRepo
          display_name: myRepo
```

Note the `{value, display_name}` wrapping on string inputs — the UI uses `display_name` for the chip label and `value` as the actual parameter. For workflow-step inputs the controller often accepts a bare string and wraps it on save, but for trigger inputs supply both fields up front.

Look up the polling trigger's required inputs in the catalog the same way you look up actions (see [looking-up-actions.md](looking-up-actions.md)). The trigger JSON's `sample_event` shows the payload shape — use it to write `{{ event.payload.* }}` references in the workflow body.

## Referencing the trigger payload

Event triggers (both webhook and polling) expose `{{ event.payload.* }}` in workflow steps. Polling-trigger payloads match the catalog's `sample_event`; webhook payloads match whatever the inbound caller sent.

For test runs of event-triggered playbooks, the test session does **not** auto-inject `sample_event`. `{{ event.payload.* }}` resolves to empty strings unless the user pastes a sample payload in the UI's Test Run dialog. Validate-readiness (`READY`) does not catch this — it's a runtime data issue, not a structural one. Tell the user to paste a sample payload, or fire a real event in the source system.
