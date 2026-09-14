#!/usr/bin/env python3
"""Blink MCP server: exposes the generating-workflow operations as MCP tools.

Bundled with the blink-code-builder plugin and started automatically by Claude Code
when the plugin is enabled (see ../../.mcp.json, stdio transport). Reads Blink
credentials directly from CLAUDE_PLUGIN_OPTION_BLINK_* — Claude Code injects these into
MCP server subprocesses natively.
"""

from blink_shared.deps import ensure_installed

# Everything below imports third-party packages, so they have to exist by now.
ensure_installed()

from mcp.server.fastmcp import FastMCP

from . import discovery, pipeline, agents, tables

mcp = FastMCP("blink")


@mcp.tool(name="list_connections")
def _list_connections() -> str:
    """List the connections that exist in the configured Blink workspace, one per line as `<name>\\t<type>`."""
    return discovery.list_connections()


@mcp.tool(name="fetch_automation")
def _fetch_automation(ref: str, stdout: bool = False, output: str = "") -> str:
    """Pull an existing Blink playbook (by id or editor URL) into automations/ as YAML."""
    return discovery.fetch_automation(ref, stdout=stdout, output=output)


@mcp.tool(name="fetch_options")
def _fetch_options(fetcher_name: str, playbook_id: str = "", inputs: str = "",
                    connections: str = "", runner: str = "", execution_id: str = "",
                    search: str = "") -> str:
    """Preview live fetcher options from the Blink API, for resolving autofill (field_type: 2) values."""
    return discovery.fetch_options(
        fetcher_name, playbook_id=playbook_id, inputs=inputs, connections=connections,
        runner=runner, execution_id=execution_id, search=search,
    )


@mcp.tool(name="validate_automation")
def _validate_automation(path: str, allow_missing_catalog: bool = False) -> str:
    """Validate a Blink automation YAML against the local catalog; reports structural errors and test-run readiness."""
    return pipeline.validate_automation(path, allow_missing_catalog=allow_missing_catalog)


@mcp.tool(name="save_automation")
def _save_automation(path: str, playbook_id: str = "") -> str:
    """Save a Blink automation YAML as a draft playbook (creates by name, or updates by --playbook-id/cache/name match)."""
    return pipeline.save_automation(path, playbook_id=playbook_id)


@mcp.tool(name="trigger_test_run")
def _trigger_test_run(playbook_id: str, acknowledge_risks: bool = False) -> str:
    """Trigger a draft test run via the controller's streaming endpoint and block until it finishes.

    Gated: connections allowlist, human-wait steps, and blast-radius scan. Pass
    acknowledge_risks=True only after the user explicitly approved the steps a
    previous `[BLOCKED SAFETY]` response flagged."""
    return pipeline.trigger_test_run(playbook_id, acknowledge_risks=acknowledge_risks)


@mcp.tool(name="get_run_log")
def _get_run_log(playbook_id: str, execution_id: str = "") -> str:
    """Fetch the latest (or a specific) execution log for a playbook."""
    return pipeline.get_run_log(playbook_id, execution_id=execution_id or None)


@mcp.tool(name="publish_automation")
def _publish_automation(playbook_id: str, acknowledge_risks: bool = False, allow_untested: bool = False) -> str:
    """Publish a playbook's draft so scheduled/triggered/production runs execute it.

    Gated: the draft must match a successfully tested version (`[BLOCKED UNTESTED]`,
    override with allow_untested=True only after the user insists despite the warning)
    and pass the blast-radius scan (`[BLOCKED SAFETY]`, override with
    acknowledge_risks=True only after a fresh explicit user yes — test-run approval
    does not carry over)."""
    return pipeline.publish_automation(
        playbook_id, acknowledge_risks=acknowledge_risks, allow_untested=allow_untested,
    )


@mcp.tool(name="get_tables_schema")
def _get_tables_schema(output: str = "") -> str:
    """Write every table in the workspace, with column-level schema, to a YAML file (default: tables/tables-schema.yaml)."""
    return tables.get_tables_schema(output=output)


@mcp.tool(name="create_table")
def _create_table(
    display_name: str,
    fields: list[dict],
    description: str = "",
    with_default_fields: bool = False,
    with_default_records: bool = False,
) -> str:
    """Create a new Blink    Table. Live immediately, no draft step.

    Each field in `fields` is an object: {display_name, type, description?, required?,
    unique?, default_value?, options? (for single-select/multi-select), references_table?
    (for reference)}. See the managing-tables skill for the full field type list.
    """
    return tables.create_table(
        display_name=display_name,
        fields=fields,
        description=description,
        with_default_fields=with_default_fields,
        with_default_records=with_default_records,
    )


@mcp.tool(name="edit_table")
def _edit_table(
    table: str,
    display_name: str = "",
    description: str = "",
    add_fields: list[dict] | None = None,
    update_fields: list[dict] | None = None,
    remove_fields: list[str] | None = None,
    acknowledge_risks: bool = False,
) -> str:
    """Change an existing table: rename it, edit its description, or add/update/remove
    fields. `table` is the real table name, not its display name.

    A field's type can never change. update_fields entries need `name` (the field's
    real name) plus whichever of display_name/description/required/unique/options/
    default_value are changing. Removing a field is irreversible — the first call
    returns a preview; re-call with acknowledge_risks=True to actually remove.
    """
    return tables.edit_table(
        table=table,
        display_name=display_name,
        description=description,
        add_fields=add_fields,
        update_fields=update_fields,
        remove_fields=remove_fields,
        acknowledge_risks=acknowledge_risks,
    )


@mcp.tool(name="delete_table")
def _delete_table(table: str, acknowledge_risks: bool = False) -> str:
    """Delete a Blink Table entirely — schema and all rows. Irreversible — the first
    call returns a preview with the row count; re-call with acknowledge_risks=True
    (only after the user explicitly approved) to actually delete."""
    return tables.delete_table(table, acknowledge_risks=acknowledge_risks)


@mcp.tool(name="list_agents")
def _list_agents(output: str = "") -> str:
    """Write every agent in the workspace, drafts included, to a TSV file (default:
    agents/agents-list.tsv) — mirrors get_tables_schema for tables.

    The local catalog lists only published agents, so use this to find a draft agent or to
    answer "which agents exist?" when you don't know the exact name."""
    return agents.list_agents(output=output)


@mcp.tool(name="fetch_agent")
def _fetch_agent(ref: str, stdout: bool = False, output: str = "") -> str:
    """Pull an existing Blink agent (by id or agent-builder URL) into agents/ as YAML."""
    return agents.fetch_agent(ref, stdout=stdout, output=output)


@mcp.tool(name="validate_agent")
def _validate_agent(path: str) -> str:
    """Validate a Blink agent YAML: required fields, and whether each ability is a callable workflow.

    `[ERROR]` lines block save_agent. `[WARN]` lines never block — notably an ability that
    isn't callable from this workspace, which makes the agent weaker but not unsafe."""
    return agents.validate_agent(path)


@mcp.tool(name="save_agent")
def _save_agent(path: str, agent_id: str = "", allow_overwrite: bool = False) -> str:
    """Save a Blink agent YAML as the agent's draft.

    Targets the agent by `agent_id` if given, else by the YAML's `name:`. If the name already
    belongs to another agent, returns `[BLOCKED EXISTS]` instead of overwriting it — resolve
    that with the user by passing agent_id (editing that agent on purpose) or by renaming.
    Pass allow_overwrite=True only when the user confirmed which agent to replace."""
    return agents.save_agent(path, agent_id=agent_id, allow_overwrite=allow_overwrite)


@mcp.tool(name="publish_agent")
def _publish_agent(ref: str = "", path: str = "", acknowledge_risks: bool = False,
                   allow_overwrite: bool = False) -> str:
    """Publish an agent so it goes live and becomes callable from workflows as `agents.<id>`.

    Pass `path` to publish a local YAML, or `ref` (id/agent-builder URL) to publish the
    server's current draft. Gated with `[BLOCKED SAFETY]` when the agent can act without a
    human: an `auto_approved: true` ability, or `modes.code_execution_enabled`. Pass
    acknowledge_risks=True only after the user explicitly approved each flagged point —
    approval never carries over between publishes. Can also return `[BLOCKED EXISTS]`
    (see save_agent)."""
    return agents.publish_agent(ref=ref, path=path, acknowledge_risks=acknowledge_risks,
                                allow_overwrite=allow_overwrite)


if __name__ == "__main__":
    mcp.run()
