# Changelog

All changes to the `blink-code-builder` plugin. The version is set only in
`plugins/blink-code-builder/.claude-plugin/plugin.json`. Installed users get a change only
when this version increases, so increase it in each PR that changes the plugin.

Versions use [semver](https://semver.org/): patch for bug fixes, minor for new features, major
for breaking changes.

## 0.2.1

- Support Windows. The setup skill now installs Git for Windows and Python on Windows, and the hooks run there too.
- Read and write files as UTF-8 on every OS.

## 0.2.0

- Add dashboard support. You can list, get, check, save and publish Blink dashboards.
- Before the plugin publishes a dashboard, it asks you to approve.

## 0.1.0

- First release.
