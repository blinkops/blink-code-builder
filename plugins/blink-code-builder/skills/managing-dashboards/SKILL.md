---
name: managing-dashboards
description: Build, edit and publish Blink dashboards — charts, numbers, tables, text and HTML widgets over Blink Tables — as YAML files saved straight to the workspace. Use when the user asks to create a dashboard, add or change a widget, move or resize widgets, see which dashboards exist, or publish/share a dashboard to the Blink Portal.
allowed-tools: Read, Write, Edit, Grep, Glob, mcp__plugin_blink-code-builder_blink__list_dashboards, mcp__plugin_blink-code-builder_blink__fetch_dashboard, mcp__plugin_blink-code-builder_blink__validate_dashboard, mcp__plugin_blink-code-builder_blink__save_dashboard, mcp__plugin_blink-code-builder_blink__publish_dashboard, mcp__plugin_blink-code-builder_blink__get_tables_schema
user-invocable: false
---

# Managing dashboards

A Blink dashboard is a grid of widgets that show live data from the workspace's Blink Tables:
numbers, charts, tables of records, plus text and custom HTML. The UI calls dashboards
"applications" and the API calls them "apps" — same thing.

Widget field reference: [reference/widgets.md](reference/widgets.md). Read it before writing
or changing a widget.

## No draft, no undo

Unlike a workflow or an agent, a dashboard has no draft. `save_dashboard` makes every change
live at once, for everyone who can open the dashboard. Confirm with the user before saving if
anything about the request is unclear.

This plugin never deletes anything — no dashboard, no widget. A widget removed from the file
stays on the dashboard, and the save says so. Deleting is done in the dashboard editor.

## Where everything lives

```
Your repo                                          Blink workspace
─────────────────────────                          ─────────────────────
workspace/dashboards/
  dashboards-list.yaml     every dashboard +        (list_dashboards; on demand,
                           its widgets, summarized   re-synced after save/publish)
  soc_overview.yaml   ◄──────────────────────►     the dashboard and its widgets
workspace/tables/
  tables-schema.yaml       the tables and columns a widget can read (get_tables_schema)

  you edit ──► validate_dashboard ──► save_dashboard ──► (optional) publish_dashboard
                  (local)          (live, rewrites file    (Portal + sharing,
                                    with widget ids)         user approves every call)
```

## Finding a dashboard

Read `workspace/dashboards/dashboards-list.yaml`. If it's missing, old, or doesn't have the
dashboard the user means, run `list_dashboards` first — it costs one call per dashboard, so
nothing refreshes it at session start.

## Look up the tables first

Never guess a table or column name. Read `workspace/tables/tables-schema.yaml`; if it's
missing, more than 24h old, or a table changed this session, run `get_tables_schema`. Use a
table's `table` value (not `display_name`) as `table_name`, and its `columns[].name` values
as `column`, `group_by_column` and `aggregation_column`.

Case Management tables aren't in that file. A widget can still read one (validation warns it
couldn't check it), but only use one when the user names it.

## Creating a dashboard

1. Clarify what the user wants to see: which numbers, which breakdowns, over which tables.
2. Look up the tables (above).
3. Write `workspace/dashboards/<name>.yaml` without an `id` and without widget ids. Lay the
   widgets out on the 12-column grid — see [reference/widgets.md](reference/widgets.md).
4. `validate_dashboard` with `path`. Clear every `[ERROR]`. Tell the user about `[WARN]` lines
   rather than silently working around them.
5. `save_dashboard` with `path`. It rewrites the file with the new ids — from now on that file
   is the dashboard. Report the editor link it returns, always the clickable link.
6. Read the `[WARN]` lines `save_dashboard` returns: each widget's query runs once after the
   save, and a widget that can't load its data is reported there. Fix it and save again.

If `save_dashboard` returns **`[BLOCKED EXISTS]`**, a dashboard with that display name already
exists. Don't work around it. Ask the user: edit that one (`fetch_dashboard` it, change that
file) or pick another name.

## Editing a dashboard

1. **Already in `workspace/dashboards/`?** Re-fetch it anyway if it may have changed in the
   editor since — saving sends your copy of each widget you touched. **Only in Blink?**
   `fetch_dashboard` with `ref` (the dashboard id or its editor URL).
2. Edit that file in place. Keep every `id`. Never create a `_v2.yaml`.
3. To move or resize a widget, change its `display_config`. Only widgets that differ from
   the server are sent.
4. `validate_dashboard`, then `save_dashboard`.

## Publishing to the Portal

Publishing puts the dashboard in the Blink Portal and shares it with users and groups. They
see its data — including the rows behind each chart, which they can download — without any
access to the workspace. Treat it like sending the data to them.

- Never publish on your own initiative.
- Before calling `publish_dashboard`, tell the user in plain words which dashboard goes to
  which users or groups, and what they'll be able to see. Example: "This shares *SOC Overview*
  with the *Management* group. They'll see the incident counts and the incident rows behind
  them."
- Claude Code then asks the user to approve the call (a plugin hook makes sure of it, every
  time). If they decline, don't retry — ask what they want instead.
- `share_with` takes users by name or email and groups by name. A name that matches nobody,
  or more than one, stops the call before anything changes — ask the user which one.
- To unpublish or remove someone's access, point the user to "Manage portal access" in the
  dashboard editor. This plugin doesn't do that.

## From a workflow

To use a dashboard inside a workflow — export it to PDF and send it, or update a Text/HTML
widget on a schedule — use the catalog actions `system.ExportDashboard`, `system.GetDashboard`
and `system.UpdateWidgetContent` through the `generating-workflow` skill. Pick the dashboard id
from `dashboards-list.yaml`.

## Edge cases

| Situation | What happens | What you do |
|---|---|---|
| New filter or flow widget in the file | `[ERROR]` | They link widgets or tables in ways the editor wires up. Ask the user to add it in the editor; existing ones can be moved like any widget. |
| Widget in Blink but not in the file | `[WARN]` after save, widget left as is | Tell the user. If it should go, they delete it in the editor. |
| Widget id in the file isn't on the dashboard | Save stops, nothing written | It was deleted in the editor, or copied from another dashboard. Re-fetch and redo the edit. |
| Changing a widget's `type` | Save stops, nothing written | A type never changes. Add a new widget instead; the old one is removed in the editor. |
| Switching a widget to another table | Works for Blink Tables | For a Case Management table, change the source in the editor. |
| Widget can't load its data after save | `[WARN] widget … can't load its data` | Usually a wrong column or aggregation. Fix the file, save again. |
| `icon` isn't a known name | `[WARN]`, the default Blink icon is shown | Use an action collection name, like "Slack" or "AWS". |
| Save failed half-way | `PARTIAL SAVE …` naming what landed | The file keeps the new widget ids. Re-run `save_dashboard`; it won't add them twice. |

## Never

- Guess a table or column name.
- Publish, or re-share with new people, without the user asking for it in this conversation.
- Hand-edit `workspace/dashboards/dashboards-list.yaml` — re-run `list_dashboards` instead.
- Remove a widget's `id` to "start fresh" — that adds a duplicate widget next to the old one.
