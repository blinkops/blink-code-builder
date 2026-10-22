---
name: managing-tables
description: Create and manage Blink Tables at the workspace level — the table itself and its columns, not workflow steps. Use when the user asks to create a table, add a column, or otherwise manage a table's schema directly (not as part of a workflow).
allowed-tools: Read, Grep, mcp__blink-code-builder__get_workspace_resources, mcp__blink-code-builder__create_table, mcp__blink-code-builder__edit_table, mcp__blink-code-builder__delete_table
user-invocable: false
---

# Managing tables

A Blink Table is a workspace-owned database table — a name, a description, and a set of
typed columns (fields). This skill covers building and changing that structure directly.

Currently supported: **creating** a table, **editing** it (rename, description, add/update/
remove fields), and **deleting** it entirely.

## No draft, no undo

Unlike a workflow or an agent, a table has no draft state. Both `create_table` and
`edit_table` make their change live in the workspace immediately — there is nothing to save
or publish afterward. Confirm with the user before calling either if anything about the
request is unclear.

## Look up existing tables first

Read `workspace/tables/tables-schema.yaml` in the user's repo (or call `get_workspace_resources` with `include_tables=true` if it's
missing; a hook writes the file) before creating a table, to avoid creating a near-duplicate of one that already
exists.

## Field types

Each field needs a `display_name` and a `type`. `name` is never set by hand — the server
derives it from `display_name`.

| Type | Needs |
|---|---|
| `text`, `long-text`, `number`, `duration`, `risk-level`, `checkbox`, `date`, `user`, `status` | nothing extra |
| `single-select`, `multi-select` | `options`: a list of choices |
| `reference` | `references_table`: the target table's name |
| `list`, `button`, `attachment`, `sla`, `vendor`, `vendors` | see the table's own field for details before using — less common |

Any field can also set `required`, `unique`, or `default_value`.

A few types are system-only and never valid to request: `sys-id`, `auto-increment-id`,
`incident`, `generic-id`, `sys-time`, `sys-record-modifier`. Blink adds its own 5 system
columns (id, created/updated at, created/updated by) to every table automatically — don't
add them yourself.

**A table can have at most one `reference` field per target table.** Creating a second
reference to the same table fails.

## Creating a table

```
create_table(
    workspace_id="<workspace id>",
    display_name="Incidents",
    description="Open incidents tracked by the SOC",
    fields=[
        {"display_name": "Severity", "type": "single-select", "required": True,
         "options": ["low", "medium", "high"]},
        {"display_name": "Owner", "type": "reference", "references_table": "users"},
    ],
)
```

It returns the new table's schema: `table` (the real name steps use), `display_name`, `id`
and `columns` (each with its derived `name`). Report the `table` name. The schema file is not
updated by this — call `get_workspace_resources` afterwards so `workspace/tables/tables-schema.yaml`
includes the new table.

`with_default_fields=True` adds a generic "Name" (text) and "Number" (number) column.
Only pass it when the user hasn't described their own columns — otherwise it leaves two
unused columns behind.

## Editing a table

`edit_table` covers four kinds of change, and you can combine them in one call:

```
edit_table(
    workspace_id="<workspace id>",
    table="incidents",                 # the real table name, not the display name
    display_name="New Name",           # optional: rename
    description="new description",     # optional
    add_fields=[{"display_name": "Notes", "type": "long-text"}],
    update_fields=[{"name": "severity", "required": False}],
    remove_fields=["scratch_notes"],
)
```

A `update_fields` entry needs `name` — the field's real name (from the schema file's
`columns[].name`, not its display name). Only include the keys you want to change;
anything left out keeps its current value. A field to add takes the same keys as in
`create_table` (no `name`).

It returns just `{table}`. Afterwards call `get_workspace_resources` to refresh the schema file.

**A field's type can never change**, no matter what — Blink's database doesn't support it.
If the user needs a different type, that's really: add a new field with the right type,
copy the values over, then remove the old field. Say this plainly rather than trying to
force a type change.

## Removing a field is irreversible

Deleting a field deletes its values in every row, permanently — there's no confirmation
from the API itself. So an `edit_table` call with `remove_fields` gets a Claude Code approval
prompt from the plugin:

- Only remove a field the user asked to remove — never on your own initiative.
- Before the call, tell the user in plain language which fields go and that their data in
  every row (roughly `row_count` rows, from the schema file) is lost for good.
- Then call `edit_table` with `remove_fields`; Claude Code asks the user to approve.
- If they decline, don't retry — ask what they want instead.

Blink's own system columns (id, created/updated at, created/updated by) can never be
removed — don't try.

## Deleting a table

`delete_table` removes the table entirely — schema and rows both. Workspace-wide and
immediate, same as create/edit: no draft, no undo.

It's gated the same way as `remove_fields`:

- Only delete a table the user asked to delete — never on your own initiative.
- Before the call, tell the user in plain language which table goes and roughly how many
  rows with it (`row_count` in the schema file).
- Then call `delete_table` with `workspace_id` and `table` (the real name); Claude Code asks
  the user to approve. If they decline, don't retry — ask what they want instead.
- It returns `{table}`. Afterwards call `get_workspace_resources` to refresh the schema file.

Reference fields on other tables pointing at the one you're deleting aren't checked
first — if any exist, expect the delete call to fail rather than silently break them.
