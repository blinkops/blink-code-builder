# blink-code-builder

A [Claude Code](https://claude.com/claude-code) plugin that turns a plain-English request into a
[Blink](https://www.blinkops.com/) automation. It drafts the workflow YAML, validates it against
your workspace's real action catalog, saves it as a draft in Blink, and with your approval
test-runs and publishes it.

> "Every morning at 8am, check for EC2 instances without an owner tag and post the list to #cloud-ops."

## Install

You need `python3` (3.9 or later) and **Contributor or above** in the target Blink workspace.
The company-level "Builder" role alone carries no workflow permissions. You do not need an API key.

1. Add the marketplace and install the plugin:

   ```
   /plugin marketplace add blinkops/blink-code-builder
   /plugin install blink-code-builder@blink-marketplace
   ```

2. Open `/plugin` → **Installed** → **blink-code-builder** → **Configure options**. Enter two values:
   - **Blink URL**: the domain you sign in at, for example `https://acme.app.blinkops.com`.
   - **Blink workspace ID**: the UUID after `/workspaces/` in your browser URL inside Blink.
3. Quit Claude Code and open it again. On the first start, the plugin registers its Blink MCP
   server for your URL and asks you to restart one more time.
4. Run `/mcp`, select **blink-code-builder**, choose **Authenticate**, and sign in with your Blink login.

If a step fails, ask Claude to set up the plugin. The bundled `setup-blink-plugin` skill checks
each step and fixes what is missing.

## Update

To see your installed version, run `claude plugin list`. Compare it with
[CHANGELOG.md](CHANGELOG.md). To get the latest version:

```
claude plugin marketplace update blink-marketplace
claude plugin update blink-code-builder@blink-marketplace
```

The new version loads in your next session, or run `/reload-plugins` in a running one. To get
updates automatically, open `/plugin` → **Marketplaces** → `blink-marketplace` and turn on
**Enable auto-update** (it is off by default for this marketplace).

### Updating from 0.2.x to 0.3.0

Version 0.3.0 connects through the remote Blink MCP server and your own Blink login. It no longer
uses an API key. After you update:

1. Quit Claude Code and open it again, two times (the plugin registers its MCP server on the first start).
2. Run `/mcp`, select **blink-code-builder**, and sign in with your Blink login.

Your Blink URL and workspace ID stay as they are. The old API key and pack ID values are no longer used.

## What it can do to your workspace

The remote Blink MCP server, signed in as you, reads your connections and playbooks, saves drafts, triggers test
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
