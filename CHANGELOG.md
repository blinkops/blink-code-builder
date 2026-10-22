# Changelog

All changes to the `blink-code-builder` plugin. The version is set only in
`plugins/blink-code-builder/.claude-plugin/plugin.json`. Installed users get a change only
when this version increases, so increase it in each PR that changes the plugin.

Versions use [semver](https://semver.org/): patch for bug fixes, minor for new features, major
for breaking changes.

## 0.3.0

- The plugin talks to Blink through the remote Blink MCP server. The local MCP server is removed.
- You sign in with your own Blink login through `/mcp`. The plugin no longer uses an API key or a pack ID.
- The plugin selects the MCP server from the domain of your Blink URL.
- The plugin validates automations locally against the action catalog before it saves them.
- The plugin writes fetched automations and agents to files in `workspace/`.
- Add a safety gate hook. It asks you to approve test runs, publishes and table deletes. It blocks a save when an action or connection is not in the catalog.
- Add the Simplified Technical English output style.
- **Breaking:** after you update, run the setup skill again and sign in through `/mcp`.

## 0.2.0

- Add dashboard support. You can list, get, check, save and publish Blink dashboards.
- Before the plugin publishes a dashboard, it asks you to approve.

## 0.1.0

- First release.
