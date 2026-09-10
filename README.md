# blink-code-builder

A [Claude Code](https://claude.com/claude-code) plugin that turns a plain-English request into a
[Blink](https://www.blinkops.com/) automation. It drafts the workflow YAML, validates it against
your workspace's real action catalog, saves it as a draft in Blink, and with your approval
test-runs and publishes it.

> "Every morning at 8am, check for EC2 instances without an owner tag and post the list to #cloud-ops."

## Install

```
/plugin marketplace add blinkops/blink-code-builder
/plugin install blink-code-builder@blink-marketplace
```

Then ask Claude to set up the plugin. The bundled `setup-blink-plugin` skill handles the rest:
Python prerequisites, walking you through the four config values (`/plugin` → **Configure options**),
and verifying the catalog populated.

After setting the config values, **quit Claude Code and reopen it** — they don't take effect
mid-session.

You'll need a Blink user API key, and **Contributor or above** in the target workspace. The
company-level "Builder" role alone carries no workflow permissions.

## What it can do to your workspace

The bundled `blink` MCP server reads your connections and playbooks, saves drafts, triggers test
runs, fetches run logs, and publishes.

## Safety

**A test run is not a dry run.** It sends the real messages, opens the real tickets, and deletes
the real records. The plugin blocks a run it considers risky and asks you first, but the checks are
heuristics, so your review is what actually keeps a run safe.

What you need to do:

- **Keep `trigger_test_run` and `publish_automation` out of your permission allowlist.** Claude
  Code's prompt is the real enforcement. The plugin's own gates are unblocked by a flag Claude
  passes, and nothing verifies a human said yes.
- **Maintain `automations/connections-allowlist.yaml`.** Only connections you list there can be
  used in a test run. If the file is missing, no run is allowed at all.
- **Read what Claude tells you before approving.** When a run is blocked, Claude explains who and
  what the flagged steps touch. That summary is the decision point.
- **Think twice before approving a publish.** Publishing needs your approval, and once a workflow
  is published its schedule and its triggers are live. A scheduled or event-based automation will
  run on its own, without anyone watching it.

## License

[Apache-2.0](LICENSE).
