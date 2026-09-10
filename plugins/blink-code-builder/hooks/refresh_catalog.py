#!/usr/bin/env python3
"""Refresh the blink action catalog from the controller.

SessionStart hook only. Skips silently if config is missing or the catalog is
<7 days old; otherwise forks a detached child to fetch so the caller returns
immediately. To force a refresh, delete the catalog dir and start a new
Claude Code session.

Writes <root>/{actions,triggers}/<service>/<name>.json per entry, plus flat TSV
lookup files (`actions.tsv`, `triggers.tsv`) — the grep targets for capability search.

Config: CLAUDE_PLUGIN_OPTION_BLINK_{CONTROLLER_URL,USER_API_KEY}.
Root: ${CLAUDE_PLUGIN_DATA}/catalog/ — must be set (Claude Code injects it).
"""

import asyncio
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from _common import clean_for_tsv
from blink_shared.config import ENV_API_KEY, catalog_root, controller_url, has_config
from blink_shared.deps import ensure_installed

STALE_DAYS = 7
HIDDEN_ACTIONS_THAT_SHOULD_BE_INCLUDED = [
    "internal.print",
    "internal.for",
    "internal.append_list",
    "utility.GetEpochTime",
    "internal.ifCondition",
    "internal.switchCaseExpression",
    "internal.switchCaseDefaultLabel",
    "internal.switchCaseLabel",
    "internal.catch",
    "internal.while",
    "internal.SetVariables",
    "internal.Sleep",
    "internal.EndRun",
]


def split_full_name(full_name):
    """Split 'service.name' into a (service, name) pair.

    13 built-in triggers (e.g. 'custom_webhook', 'aws_new_instances') have no
    service prefix — bucket them under '_misc' so the path stays well-formed.
    """
    service, _, name = full_name.partition(".")
    return (service, name) if name else ("_misc", service)


def should_skip():
    """Hook-mode gate: skip silently if config is missing or the catalog is fresh."""
    if not has_config():
        return True
    # actions.tsv is the *last* file refresh() writes — its mtime is the freshness signal.
    sentinel = catalog_root() / "actions.tsv"
    return sentinel.exists() and (time.time() - sentinel.stat().st_mtime) < STALE_DAYS * 24 * 60 * 60


def spawn_detached():
    """Re-invoke ourselves in the background; parent returns immediately.

    The child sets _BLINK_REFRESH_CATALOG_CHILD so main() dispatches straight
    to refresh() without re-running should_skip — otherwise the child would
    fork again and we'd have a fork bomb.
    """
    data_dir = Path(os.environ["CLAUDE_PLUGIN_DATA"])
    data_dir.mkdir(parents=True, exist_ok=True)
    log_file = (data_dir / "refresh.log").open("w")
    child_env = os.environ.copy()
    child_env["_BLINK_REFRESH_CATALOG_CHILD"] = "1"
    subprocess.Popen(
        [sys.executable, __file__],
        env=child_env,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )


def slim_action(action):
    parameters = []
    for parameter in action.get("parameters") or []:
        if parameter.get("hidden") or parameter.get("disabled"):
            continue
        slim_param = {}
        for field in ("name", "description", "input_type", "options", "default_value", "linked_hidden", "autofill"):
            if parameter.get(field):
                slim_param[field] = parameter[field]
        # Preserve boolean fields explicitly — False is falsy but meaningful
        for field in ("required", "field_type"):
            if field in parameter:
                slim_param[field] = parameter[field]
        parameters.append(slim_param)
    result = {
        "full_name": action["full_name"],
        "display_name": action.get("display_name"),
        "description": action.get("description"),
        "parameters": parameters,
        "example_output": action.get("example_output"),
    }
    if action.get("connection_types"):
        result["connection_types"] = action["connection_types"]
        result["connection_optional"] = bool(action.get("connection_optional"))
    return result


def slim_trigger(trigger):
    fields = ("full_name", "display_name", "description", "action", "sample_event")
    return {field: trigger.get(field) for field in fields}


async def _fetch_all_copilot_data(controller, api_key):
    # Imported here, not at the top: httpx exists only once main() has installed it.
    import httpx

    headers = {"BLINK-API-KEY": api_key}
    async with httpx.AsyncClient(base_url=controller, headers=headers, timeout=60) as client:
        actions_resp, triggers_resp = await asyncio.gather(
            client.get("/copilot_data/actions"),
            client.get("/copilot_data/triggers"),
        )
    return actions_resp.raise_for_status().json(), triggers_resp.raise_for_status().json()


def refresh():
    catalog_dir = catalog_root()
    if catalog_dir.exists():
        shutil.rmtree(catalog_dir)
    catalog_dir.mkdir(parents=True)

    # The /copilot_data routes are not workspace-scoped, so this reads the env directly
    # instead of going through the shared client.
    controller = controller_url()
    print(f"fetching from {controller} ...", file=sys.stderr)
    raw_actions, raw_triggers = asyncio.run(_fetch_all_copilot_data(controller, os.environ[ENV_API_KEY]))

    # Triggers written first — actions.tsv ends up as the last file, our freshness sentinel.

    # Per-trigger JSON files
    triggers = [slim_trigger(trigger) for trigger in raw_triggers if not trigger.get("hidden")]
    for trigger in triggers:
        service, name = split_full_name(trigger["full_name"])
        path = catalog_dir / "triggers" / service / f"{name}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(trigger, indent=2, ensure_ascii=False))
    # triggers.tsv index
    lines = ["# full_name\tservice\tdescription"]
    for trigger in sorted(triggers, key=lambda t: t["full_name"]):
        service, _ = split_full_name(trigger["full_name"])
        lines.append(f"{trigger['full_name']}\t{service}\t{clean_for_tsv(trigger.get('description'))}")
    (catalog_dir / "triggers.tsv").write_text("\n".join(lines) + "\n")

    actions = [
        slim_action(action) for action in raw_actions
        if (action.get("active", True) and not action.get("hidden"))
        or action["full_name"] in HIDDEN_ACTIONS_THAT_SHOULD_BE_INCLUDED
    ]
    # Per-action JSON files
    for action in actions:
        service, name = split_full_name(action["full_name"])
        path = catalog_dir / "actions" / service / f"{name}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(action, indent=2, ensure_ascii=False))
    # actions.tsv index (last write — freshness sentinel)
    lines = ["# full_name\tservice\tdescription\tconnection_types"]
    for action in sorted(actions, key=lambda a: a["full_name"]):
        service, _ = split_full_name(action["full_name"])
        connection_types = ",".join(action.get("connection_types") or [])
        lines.append(f"{action['full_name']}\t{service}\t{clean_for_tsv(action.get('description') or '')}\t{connection_types}")
    (catalog_dir / "actions.tsv").write_text("\n".join(lines) + "\n")

    print(f"ok: {len(actions)} actions, {len(triggers)} triggers -> {catalog_dir}", file=sys.stderr)


def main():
    if os.environ.get("_BLINK_REFRESH_CATALOG_CHILD"):
        refresh()
        return
    if should_skip():
        return
    # The detached child starts fetching right away, so the deps must already be there.
    ensure_installed()
    spawn_detached()


if __name__ == "__main__":
    main()
