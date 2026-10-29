---
name: managing-dashboards
description: Build, edit and publish Blink dashboards — charts, numbers, tables, text and HTML widgets over Blink Tables — as YAML files saved straight to the workspace. Use when the user asks to create a dashboard, add or change a widget, move or resize widgets, see which dashboards exist, or publish/share a dashboard to the Blink Portal.
allowed-tools: Read, Write, Edit, Grep, Glob, mcp__blink-code-builder__fetch_dashboard, mcp__blink-code-builder__save_dashboard, mcp__blink-code-builder__publish_dashboard, mcp__blink-code-builder__get_workspace_resources
user-invocable: false
---

# Managing dashboards

A Blink dashboard is a grid of widgets that show live data from the workspace's Blink Tables:
numbers, charts, tables of records, plus text and custom HTML. The UI calls dashboards
"applications" and the API calls them "apps" — same thing.

Widget field reference: [reference/widgets.md](reference/widgets.md). Read it before writing
or changing a widget.
For a Custom HTML widget, also read [reference/html-widgets.md](reference/html-widgets.md) (how it
gets data) and [reference/html-design.md](reference/html-design.md) (the Blink look), and start
from [reference/html-starter.html](reference/html-starter.html).

## No draft, no undo

Unlike a workflow or an agent, a dashboard has no draft. `save_dashboard` makes every change
live at once, for everyone who can open the dashboard. Confirm with the user before saving if
anything about the request is unclear.

This plugin never deletes anything — no dashboard, no widget. A widget removed from the file
stays on the dashboard. Deleting is done in the dashboard editor.

## Where everything lives

```
Your repo                                          Blink workspace
─────────────────────────                          ─────────────────────
workspace/dashboards/
  dashboards-list.tsv      every dashboard: id,      (get_workspace_resources, at
                           name, published or not     session start)
  soc-overview.yaml   ◄──────────────────────►     the dashboard and its widgets
workspace/tables/
  tables-schema.yaml       the tables and columns a widget can read

  fetch_dashboard ──► you edit the file ──► save_dashboard ──► (optional) publish_dashboard
  (a hook writes        (YAML)               (live; a hook rewrites   (Portal + sharing,
   the file)                                  the file with the ids)   user approves every call)
```

A hook writes the file after `fetch_dashboard` and after every `save_dashboard`, at
`workspace/dashboards/<display name, lowercase, dashes>.yaml`. The tool output you see is a
one-line status with the path, the id and the editor link — not the dashboard.

## Finding a dashboard

Grep `workspace/dashboards/dashboards-list.tsv`. If the dashboard isn't there, call
`get_workspace_resources` (`include_tables=false` is enough) to refresh the list. A hook asks for
the same refresh after a save that creates a dashboard, and after a publish.

## Look up the tables first

Never guess a table or column name. Read `workspace/tables/tables-schema.yaml`; if it's
missing, or a table changed this session, run `get_workspace_resources` with
`include_tables=true`. Use a table's `table` value (not `display_name`) as `table_name`, and its
`columns[].name` values as `column`, `group_by_column` and `aggregation_column`.

Check each column's `type` too: `date_bucket` needs a date column, and `aggregation_column` (for
`sum`, `avg`, `min`, `max`, `median`) needs a number column. A wrong type is not caught before
the save; the widget's query fails after it.

Case Management tables aren't in that file. A widget can still read one, but only use one when
the user names it.

## Creating a dashboard

1. Clarify what the user wants to see: which numbers, which breakdowns, over which tables.
   Grep `dashboards-list.tsv` for the name you plan to use. A dashboard name is unique in the
   workspace; if it's taken, ask the user before you write anything.
2. Look up the tables (above).
3. Write `workspace/dashboards/<name>.yaml` without an `id` and without widget ids. Name the file
   after the display name (lowercase, dashes), so the hook rewrites the same file. Lay the
   widgets out on the 12-column grid — see [reference/widgets.md](reference/widgets.md).
4. `save_dashboard` with the file's content as the `dashboard` object. The hook rewrites the file
   with the new ids — from now on that file is the dashboard.
5. Always show the user the editor link from the status, as a clickable link. Copy it exactly;
   never build a Blink URL yourself.
6. If the status lists widgets that can't load their data, fix them (usually a wrong column or
   aggregation) and save again.

If the save fails because the name already exists, don't work around it. Ask the user: edit that
one (`fetch_dashboard` it, change that file) or pick another name.

## Editing a dashboard

1. `fetch_dashboard` with its id (from `dashboards-list.tsv`), even if the file is already
   there: it may have changed in the editor since, and a save sends your copy of each widget.
2. Edit that file in place. Keep every `id`. Never create a `_v2.yaml`.
3. To move or resize a widget, change its `display_config`. Only widgets that differ from the
   server are sent.
4. `save_dashboard` with the file's content. Show the editor link.

## Checks

`save_dashboard` checks before it writes anything, and stops on the first problem:

- The values the controller would store without a check: `attributes.type`, `chart_type`,
  `aggregation_function`, `date_bucket`, `sort_by` (number and chart widgets), and the 12-column
  grid.
- The controller then checks the rest: widget `type`, a type change, `palette`, the table, the
  dashboard name, sizes.

After the save, each widget's query runs once. A wrong column only shows up here — the status
lists those widgets. The dashboard is already saved; fix the file and save again.

## Publishing to the Portal

Publishing puts the dashboard in the Blink Portal and shares it with users and groups. They
see its data — including the rows behind each chart, which they can download — without any
access to the workspace. Treat it like sending the data to them.

- Never publish on your own initiative.
- Publishing takes two calls:
  1. **Preview:** `publish_dashboard` with `share_with` as the user gave it and `confirm=false`
     (the default). It changes nothing and needs no approval. For each name it returns a
     `target` (the one user or group found) or up to 10 `options`, and `more` when there are
     more matches.
  2. **Publish:** after the user agrees, the same call with `confirm=true` and `share_with` set
     to each target's exact email (users) or exact name (groups). Claude Code asks the user to
     approve it (a plugin hook, every time). If they decline, don't retry — ask what they want.
- Before you search, write a name the way Blink stores it: translate a Hebrew or other non-Latin
  name to Latin letters ("ניב" → "niv"). The search needs 3 letters or more.
- **Several options:** let the user pick one (AskUserQuestion, name and email in each option).
  Never pick for them. If `more` is above 0, ask for a longer name instead.
- **One target:** show it ("Niv Levi <niv.l@blinkops.com>") in the sentence before the publish.
- **No match:** say so, and offer two ways: the user gives a better name or email, or shares
  it themselves — the preview's `editor_url` → menu → **Manage portal access**.
- Before the publish, tell the user in plain words which dashboard goes to which users or
  groups, and what they'll see. Example: "This shares *SOC Overview* with the *Management*
  group. They'll see the incident counts and the incident rows behind them."
- To unpublish or remove someone's access, point the user to "Manage portal access" in the
  dashboard editor. This plugin doesn't do that.

## From a workflow

To use a dashboard inside a workflow — export it to PDF and send it, or update a Text/HTML
widget on a schedule — use the catalog actions `system.ExportDashboard`, `system.GetDashboard`
and `system.UpdateWidgetContent` through the `generating-workflow` skill. Pick the dashboard id
from `dashboards-list.tsv`.

## Edge cases

| Situation | What happens | What you do |
|---|---|---|
| New filter or flow widget | Allowed, but it links other widgets by id | Prefer the editor. Existing ones can be moved like any widget. |
| Widget in Blink but not in the file | Left as is | Tell the user. If it should go, they delete it in the editor. |
| Widget id in the file isn't on the dashboard | Save stops, nothing written | It was deleted in the editor, or copied from another dashboard. Re-fetch and redo the edit. |
| Changing a widget's `type` | The controller refuses the widget | A type never changes. Add a new widget instead; the old one is removed in the editor. |
| Switching a widget to another table | Works for Blink Tables | For a Case Management table, change the source in the editor. |
| `icon` isn't a known name | The default Blink icon is shown | Use the vendor's display name, like "Slack", "AWS" or "Microsoft Teams" (case doesn't matter). |
| Save failed half-way | The controller error names the failing call | Re-fetch the dashboard before you save again, so new widgets aren't added twice. |

## Never

- Guess a table or column name.
- Publish, or re-share with new people, without the user asking for it in this conversation.
- Hand-edit `workspace/dashboards/dashboards-list.tsv` — call `get_workspace_resources` instead.
- Remove a widget's `id` to "start fresh" — that adds a duplicate widget next to the old one.
