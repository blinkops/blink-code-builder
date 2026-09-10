---
name: tables
description: Use Blink Tables (the internal no-code database) from inside a workflow — the 15 tables.*/system.Table* actions covering rows, schema, and files, query/write-mode choices, and looking up real column names before drafting. Auto-triggered from generating-workflow the moment a step needs a Table, whether the step is new or being edited in an existing workflow; never a standalone command.
allowed-tools: Read, Grep, mcp__plugin_blink-code-builder_blink__get_tables_schema
user-invocable: false
---

# Tables

A Table is Blink's internal no-code database — a workspace-owned store of records with a
typed schema (columns, required/unique, references to other tables), managed entirely
through the 15 actions below. Reached from `generating-workflow` the moment its action
lookup hits a `tables.*` or `system.Table*` action; not a standalone `/tables` command.
This applies the same way whether the step is part of a brand-new workflow or being
added/edited in one that already exists — the trigger is per-step, not per-workflow.

## The 15 actions

`tables.*` is the current, structured-parameter family; `system.Table*` is an older
family that takes a raw JSON body instead of per-field params — prefer `tables.*` for
new steps, but recognize `system.Table*` steps in a workflow you're revising.

**`tables.*`** — read/write rows, plus the schema/file helpers:

| Action | Purpose |
|---|---|
| `tables.GetRecordsV3` | Get records from a table. `query_method` picks how: By Condition (default, fill `table`+`ci`), RQL Query (fill `query`), or SQL Query (fill only `sql`). |
| `tables.AddRecordV2` | Add one record, one input per column. |
| `tables.CreateOrUpdateRecordV3` | Update the record(s) matching a condition, or create one if none matches — the upsert action. Prefer this over a separate exists-check + branch. Default `record_data_format` ("Entire record") **blanks any field left empty, even on an update** — use "By field" to touch just specific columns. |
| `tables.UpdateRecords` | Update multiple existing records in one call (JSON array of `{id, ...fields}`). |
| `tables.DeleteRecordsV2` | Delete record(s) by id or by condition. Irreversible, and a condition can match more than one row. |
| `tables.GetTableSchema` | Get one table's column schema live, at run time (e.g. the table name is only known from a prior step). For drafting, prefer this skill's `get_tables_schema` tool instead — see below. |
| `tables.CreateTableFromData` | Create a new table from a block of JSON data, inferring columns. |
| `tables.GetFileContent` | Read a file from a File/Attachment column. `show_output: true` returns text-based content under `output.content`; binaries (images, PDFs) may not come back as usable string content — work by `file_name` instead. |
| `tables.UpdateFileColumn` | Write a file to a File/Attachment column via `file_name` (an identifier from an upstream step's output). **Leaving `file_name` empty clears the column** — not a no-op. Flag this if `file_name` comes from an expression that could evaluate to empty. |

**`system.Table*`** — the older, JSON-body family; three genuinely table-level (create/exists/delete-the-table), two are actually row operations with a raw JSON body:

| Action | Purpose |
|---|---|
| `system.TableCreate` | Create a table from an explicit JSON `schema`. `skip: true` skips creation (no error) if a table with the same name already exists — set it on any step that might run more than once, e.g. a scratch table created at the top of a test run. |
| `system.TableExists` | Check whether a table exists — `search_by` picks Display Name (default), Name, or both. |
| `system.TableRemove` | Delete the table itself, schema included. Irreversible. |
| `system.TableInsert` | Insert one or more records via a single raw JSON body — JSON counterpart to `tables.AddRecordV2`. |
| `system.TableUpdate` | Update one record (by id) via a raw JSON body — JSON counterpart to `tables.CreateOrUpdateRecordV3`. |
| `system.TableClear` | Delete every record, keep the table and its schema. Irreversible — see **Destructive actions** below. |

## Look up the table before writing to it

Never guess a column name or type. Before drafting any step that reads or writes a
Table, read `tables/tables-schema.yaml` in the user's repo — one entry per table, with
its columns, types, and required/unique/reference flags. If the file is missing, or its
modified time (check with `stat` / `ls -la`) is more than 24h old, or the user mentioned
changing a table this session, call the `get_tables_schema` tool first to regenerate it
(no arguments; it overwrites the whole file).

**Use the `table` field's value, not `display_name`, when writing a step's `table:`
input.** A table's `display_name` (e.g. "Incidents") is only the human-readable label
shown in the Blink UI — the value every action actually resolves against is the
internal `table` name (e.g. `incidents`), and a step written with the display name will
fail to find the table at run time.

**A `reference` column takes the target record's id, not a name or display value.**
`references_table` in the schema file names the referenced table — if you don't
already have the id (e.g. from a prior step), fetch it first with `GetRecordsV3`
against that table.

## Destructive actions

`system.TableClear` (wipes every row) and `system.TableRemove` (deletes the table) are
fully irreversible; `tables.DeleteRecordsV2` deletes matching rows. All three are caught
by the safety gate the `trigger_test_run`/`publish_automation` tools already run before
executing anything — see [../generating-workflow/reference/safety.md](../generating-workflow/reference/safety.md)
for the confirmation flow. Nothing further to do here: describe the step in plain
language when the gate flags it, same as any other destructive action.

## Lifecycle: test runs, drafts, publishing

A table has no draft/published split of its own — it's live in the workspace the
moment it's created, regardless of whether the *workflow* referencing it is a draft.

A test run is **not a dry run**: `system.TableCreate` really creates the table,
`AddRecordV2`/`TableInsert` really inserts a row, `UpdateRecords`/`TableUpdate` really
updates one. Nothing is sandboxed, so a step that inserts a row on every Test Run will
keep piling up rows in the real table. Mitigate:

- Set `skip: true` on `system.TableCreate` if the step might run more than once.
- For a step that writes non-trivial data, point the first test run at a scratch table
  (name it with a `test_`/`scratch_` prefix), then tell the user explicitly to repoint
  it at the real table before publishing — there's no validation error today that
  catches a leftover scratch-table reference.

Also: `row_count` in `tables/tables-schema.yaml` is Postgres's approximate live-tuple
estimate, not an exact count — good for "roughly empty vs. huge," not for a delete
condition that needs to match an exact number of rows (run a live `GetRecordsV3` for
that instead).

## Table vs. vendor action

Some data belongs only to this workflow — a note it keeps for itself between runs, like
a dedup list, a running count, or a queue of items still to handle. Nobody else owns
that data, so it lives in a **Table**.

Other data already lives somewhere real — a Jira ticket, a DynamoDB item, a ServiceNow
record. For that, use the matching **vendor action** (`jira.SearchIssues`,
`aws.DynamoDB_*`, `servicenow.ListTables`) instead of copying it into a Table. A copy in
a Table just goes stale the moment the real system changes.

One more difference: a vendor action needs a `connections:` block telling it which
credential to use (which Slack workspace, which AWS account, etc.), because the catalog
declares a `connection_types` for it. Table actions skip that entirely — a Table belongs
to the workspace itself, not to an external vendor, so there's no connection to resolve
and no `connections:` block to write.
