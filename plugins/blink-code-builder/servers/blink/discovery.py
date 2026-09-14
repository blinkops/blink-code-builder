"""Everything used to gather information before or while drafting an automation:
pulling an existing playbook to revise, listing workspace connections, and resolving
autofill (field_type: 2) values via a live fetcher call.
"""
import json
from pathlib import Path

import httpx
import yaml

from ._blink import resolve_playbook_ref, update_id_cache, list_packs
from blink_shared.client import build_client, raise_for_status
from blink_shared.connections import fetch_connections


MATCH_EXACT = "exact"
MATCH_PARTIAL = "partial"
MATCH_NONE = "none"


def fetch_automation(ref, stdout=False, output=""):
    """Pull an existing Blink playbook out of the workspace into workflows/ as YAML.

    This is the entry point for *revising a workflow that lives in Blink* (e.g. one
    authored in the UI). It accepts a playbook id OR a Blink editor URL
    (https://.../workflow/<id>/edit), writes the playbook YAML to
    workflows/<name>.yaml, and records the id in the shared name->id cache so the
    next `save_automation` call updates this same playbook in place instead of
    creating a duplicate.

    Config: CLAUDE_PLUGIN_OPTION_BLINK_{CONTROLLER_URL,USER_API_KEY,WORKSPACE_ID}.
    """
    api = build_client()
    playbook_id = resolve_playbook_ref(ref)

    resp = api.get(f"/playbooks/{playbook_id}")
    if resp.status_code == 404:
        raise RuntimeError(
            f"playbook {playbook_id!r} not found in this workspace (404). "
            "Check the id/URL and that it belongs to the configured workspace."
        )
    data = raise_for_status(resp).json()

    yaml_text = data.get("playbook") or ""
    if not yaml_text:
        raise RuntimeError(f"playbook {playbook_id} has no YAML body to pull.")

    name = (yaml.safe_load(yaml_text) or {}).get("name") or data.get("name") or playbook_id
    update_id_cache(name, playbook_id)

    if stdout:
        return yaml_text

    out_path = Path(output) if output else Path("workflows") / f"{name}.yaml"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(yaml_text)

    return (
        f"ok: wrote {out_path} (playbook {playbook_id})\n"
        f"name: {name}\n"
        "note: save_automation will update this playbook in place (id cached)."
    )


def _discover_playbook_id(api: httpx.Client, explicit_id: str) -> str:
    if explicit_id:
        return explicit_id
    for pack in list_packs(api):
        for automation in pack.get("automations") or []:
            return str(automation["id"])
    raise RuntimeError(
        "no playbooks found in workspace — save the automation first "
        "(step 7), then re-run fetch_options"
    )


def _validate_json_flags(inputs_json: str, connections_json: str) -> None:
    for flag, value in (("--inputs", inputs_json), ("--connections", connections_json)):
        if value:
            try:
                json.loads(value)
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"{flag} is not valid JSON: {exc}")


def _build_request_params(fetcher_name: str, search: str, inputs_json: str,
                         connections_json: str, runner: str, execution_id: str) -> dict:
    params = {"fetcher": fetcher_name}
    if search:
        params["query"] = search
    if inputs_json:
        params["inputs"] = inputs_json
    if connections_json:
        params["connections"] = connections_json
    if runner:
        params["runner"] = runner
    if execution_id:
        params["execution_id"] = execution_id
    return params


def _extract_400_message(response: httpx.Response) -> str:
    try:
        return response.json().get("message") or response.text
    except Exception:
        return response.text


def _call_fetch(api: httpx.Client, playbook_id: str, params: dict) -> list:
    response = api.get(f"/playbooks/{playbook_id}/fetch", params=params)
    if response.status_code == 400:
        message = _extract_400_message(response)
        if "test run" in message.lower():
            raise RuntimeError(
                "this fetcher references prior step outputs and needs a live "
                "execution context — pass --execution-id after a test run, or leave "
                "the param empty and note it at handoff; the user can set it in the "
                "UI dropdown after a test run."
            )
        raise RuntimeError(f"400: {message}")
    raise_for_status(response)
    return response.json().get("results") or []


# The server treats --search as a hint, not a strict filter — results may include non-matching items.
# We re-filter client-side to enforce exact/partial/none match semantics and surface them clearly.
def _filter_results(results: list, search_term: str) -> tuple[list, str]:
    if not search_term or not results:
        return results, MATCH_EXACT
    term = search_term.lower()
    exact = []
    partial = []
    for item in results:
        label = (item.get("label") or item.get("name") or "").lower()
        value = (item.get("value") or "").lower()
        if label == term or value == term:
            exact.append(item)
        elif term in label or term in value:
            partial.append(item)
    if exact:
        return exact, MATCH_EXACT
    if partial:
        return partial, MATCH_PARTIAL
    return results, MATCH_NONE


def _render_results(results: list, match_type: str, search_term: str) -> str:
    if match_type == MATCH_EXACT:
        lines = [f"# {len(results)} results"]
    elif match_type == MATCH_PARTIAL:
        lines = [f"# no exact match for {search_term!r} — {len(results)} partial matches, ask user to confirm"]
    else:
        lines = [f"# 0 matches for {search_term!r} — showing all {len(results)} server results"]
    for item in results:
        value = item.get("value", "")
        label = item.get("label") or item.get("name") or value
        lines.append(f"{value}\t{label}")
    return "\n".join(lines)


def fetch_options(fetcher_name, playbook_id="", inputs="", connections="", runner="", execution_id="", search=""):
    """Call a Blink fetcher and return its options as value<TAB>label lines."""
    _validate_json_flags(inputs, connections)

    api = build_client(timeout=30)
    resolved_playbook_id = _discover_playbook_id(api, playbook_id)
    params = _build_request_params(fetcher_name, search, inputs, connections, runner, execution_id)
    results = _call_fetch(api, resolved_playbook_id, params)
    results, match_type = _filter_results(results, search)
    return _render_results(results, match_type, search)


def list_connections():
    """List the connections that exist in the configured Blink workspace.

    Use during drafting to find a real connection name for a vendor step (so the
    YAML's `connections: { <type>: <name> }` binds to something the workspace
    actually has). Returns one line per connection: `<name>\t<type>`.
    """
    with build_client() as api:
        rows = fetch_connections(api)
    lines = [f"{name}\t{type_name}" for name, type_name in rows]
    lines.append(f"total: {len(rows)}")
    return "\n".join(lines)
