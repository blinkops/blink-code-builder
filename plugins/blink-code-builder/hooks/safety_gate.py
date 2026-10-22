#!/usr/bin/env python3
"""Safety gates and checks around the remote Blink MCP tools. The server only does what it is
asked; what needs the user's approval is the plugin's decision, made here.

Modes (argv[1]):

- `pre` (PreToolUse):
  - save_automation: denies a YAML with an action that is not in the local catalog (when its
    service is, so likely a typo), or a connection key that is not one of the action's
    connection_types, with the reason, so Claude fixes it (as solution-builder's
    validate-automation.ts does). Actions of services the catalog doesn't have are left alone.
  - trigger_test_run / publish_automation: asks the user, with the risks found in the last
    saved YAML and, for publish, whether that YAML passed a test run.
  - publish_agent: asks, naming auto-approved abilities and code execution.
  - delete_table, and edit_table when it removes columns: asks, since data is deleted.
- `post` (PostToolUse):
  - save_automation / save_agent: records the risks of the saved YAML per id.
  - save_automation: tells Claude which steps have no connection (test-run readiness).
  - trigger_test_run: records whether the saved YAML passed.

publish_automation and trigger_test_run take only an id, so the risks come from what this
plugin recorded at save time (${CLAUDE_PLUGIN_DATA}/safety/<id>.json). An id saved elsewhere
has no record, and the question says so.

Heuristics, not a ban: the user decides. Stdlib only; never fails the call on its own error.
"""

import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from _common import catalog_actions, tool_result
from blink_shared.config import catalog_root

# The only run state that means the test passed (blink-mcp automations.TERMINAL_STATES).
PASSED_STATE = "completed"
GATED = {"trigger_test_run", "publish_automation", "publish_agent", "delete_table", "edit_table"}

LOOP_ACTION = "internal.for"
DESTRUCTIVE = re.compile(
    r"(?i)(delete|remove|terminate|revoke|destroy|purge|suspend|deactivate|erase|wipe|disable|tableclear|\bdrop)"
)
MESSAGING = re.compile(r"(?i)(send|message|mail|notify|broadcast)")
BROAD_MENTION = re.compile(r"(?i)<!(here|channel|everyone)>|@(here|channel|everyone)\b")
HUMAN_WAIT = re.compile(r"(?i)web form response|wait_for_response")
ACTION_LINE = re.compile(r"^(\s*)(?:-\s*)?action:\s*[\"']?([\w.\-]+)")
KEY_LINE = re.compile(r"^(\s*)(-\s+)?([\w.\-]+):(?:\s+(.*?))?\s*$")
# Not catalog actions: branches, and calls to workspace automations and agents.
BRANCH_ACTIONS = {"internal.ifTrueBranch", "internal.ifFalseBranch"}
WORKSPACE_PREFIXES = ("playbooks.", "automations.", "agents.")
NO_CONNECTION = "no_connection"


@dataclass
class YamlMapping:
    """One YAML mapping, read line by line: enough to find a step's action and connections."""
    column: int
    parent: Optional["YamlMapping"]
    parent_key: str
    fields: dict = field(default_factory=dict)
    last_key: str = ""


@dataclass
class Step:
    action: str
    step_id: str
    connections: dict
    disabled: bool  # when: false, or a mocked output: the step doesn't run


def state_dir():
    root = os.environ.get("CLAUDE_PLUGIN_DATA")
    return Path(root) / "safety" if root else None


def load_record(item_id):
    directory = state_dir()
    if not directory or not item_id:
        return None
    try:
        return json.loads((directory / f"{item_id}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def save_record(item_id, record):
    directory = state_dir()
    if not directory or not item_id:
        return
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{item_id}.json").write_text(json.dumps(record), encoding="utf-8")


def actions_with_loops(yaml_text):
    """(action, inside a loop) for each `action:` line. Loop scope comes from indentation, so a
    step nested under an internal.for step counts as inside it."""
    loops = []  # indents of open internal.for steps
    for line in yaml_text.splitlines():
        match = ACTION_LINE.match(line)
        if not match:
            continue
        indent, action = len(match.group(1)), match.group(2)
        while loops and indent <= loops[-1]:
            loops.pop()
        yield action, bool(loops)
        if action == LOOP_ACTION:
            loops.append(indent)


def workflow_risks(yaml_text):
    risks = []
    for action, in_loop in actions_with_loops(yaml_text):
        if DESTRUCTIVE.search(action):
            risks.append(f"{action}: deletes or disables something" + (", once per loop item" if in_loop else ""))
        elif in_loop and MESSAGING.search(action):
            risks.append(f"{action}: sends a message once per loop item")
    if match := BROAD_MENTION.search(yaml_text):
        risks.append(f"mentions {match.group(0)}: notifies everyone in the channel")
    if HUMAN_WAIT.search(yaml_text):
        risks.append("waits for a person (web form): an automatic test run cannot answer it")
    return sorted(set(risks))


def agent_risks(yaml_text):
    risks = []
    if re.search(r"(?m)^\s*-?\s*auto_approved:\s*true\b", yaml_text):
        risks.append("an ability is auto_approved: the agent runs it without asking anyone")
    if re.search(r"(?m)^\s*code_execution_enabled:\s*true\b", yaml_text):
        risks.append("code execution is on: the agent can run code")
    return risks


def scalar(value):
    return (value or "").strip().strip("\"'")


def inline_mapping(value):
    """`{a: x, b: y}` -> {"a": "x", "b": "y"}; anything else -> {}."""
    value = value.strip()
    if not (value.startswith("{") and value.endswith("}")):
        return {}
    pairs = (item.split(":", 1) for item in value[1:-1].split(",") if ":" in item)
    return {scalar(key): scalar(val) for key, val in pairs}


def parse_mappings(yaml_text):
    """Every mapping in the YAML, with its scalar fields. Block scalars (`|`, `>`) are skipped."""
    mappings, open_mappings, block_column = [], [], None
    for line in yaml_text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        if block_column is not None:
            if indent > block_column:
                continue
            block_column = None
        match = KEY_LINE.match(line)
        if not match:
            continue
        dash, key, value = match.group(2) or "", match.group(3), match.group(4) or ""
        column = len(match.group(1)) + len(dash)
        while open_mappings and (open_mappings[-1].column > column or (dash and open_mappings[-1].column == column)):
            open_mappings.pop()
        if not open_mappings or open_mappings[-1].column < column:
            parent = open_mappings[-1] if open_mappings else None
            mapping = YamlMapping(column, parent, parent.last_key if parent else "")
            mappings.append(mapping)
            open_mappings.append(mapping)
        mapping = open_mappings[-1]
        mapping.fields[key], mapping.last_key = value, key
        if value.startswith(("|", ">")):
            block_column = column
    return mappings


def parse_steps(yaml_text):
    """The steps of a workflow YAML: every mapping that has an `action`."""
    mappings = parse_mappings(yaml_text)
    steps = []
    for mapping in mappings:
        action = scalar(mapping.fields.get("action"))
        if not action:
            continue
        connections = inline_mapping(mapping.fields.get("connections", ""))
        for child in mappings:
            if child.parent is mapping and child.parent_key == "connections":
                connections.update({key: scalar(val) for key, val in child.fields.items()})
        disabled = (scalar(mapping.fields.get("when")).lower() == "false"
                    or scalar(mapping.fields.get("mock_output_enabled")).lower() == "true")
        steps.append(Step(action, scalar(mapping.fields.get("id")), connections, disabled))
    return steps


def is_catalog_action(action):
    return action not in BRANCH_ACTIONS and "." in action and not action.startswith(WORKSPACE_PREFIXES)


def catalog_entry(full_name):
    """The action's catalog JSON, or None when the action isn't in the catalog."""
    service, _, name = full_name.partition(".")
    try:
        return json.loads((catalog_root() / "actions" / service / f"{name}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def automation_errors(yaml_text):
    """Why the YAML must not be saved: actions not in the catalog, and connection keys that
    aren't one of the action's connection_types (a wrong key never binds at runtime)."""
    known = catalog_actions()
    if not known:
        return ["The action catalog is missing, so the YAML can't be checked. Call `export_catalog` "
                "once (a hook saves it), then save again."]
    services = {name.split(".", 1)[0] for name in known}
    errors = []
    for step in parse_steps(yaml_text):
        if not is_catalog_action(step.action) or step.action.split(".", 1)[0] not in services:
            continue
        entry = catalog_entry(step.action)
        if entry is None:
            errors.append(f"step {step.step_id}: action '{step.action}' is not in the catalog. Grep actions.tsv "
                          "for the right full_name.")
            continue
        connection_types = entry.get("connection_types") or []
        for key in step.connections:
            if connection_types and key not in connection_types:
                errors.append(f"step {step.step_id} ({step.action}): connection key '{key}' is not one of "
                              f"its connection_types {connection_types}.")
    return errors


def steps_without_connection(yaml_text):
    """Steps that need a connection and have none: a test run of the saved draft fails on them."""
    missing = []
    for step in parse_steps(yaml_text):
        if step.disabled or not is_catalog_action(step.action):
            continue
        entry = catalog_entry(step.action) or {}
        if not entry.get("connection_types") or entry.get("connection_optional"):
            continue
        if not any(name and name != NO_CONNECTION for name in step.connections.values()):
            missing.append(f"{step.step_id} ({step.action}), type: {', '.join(entry['connection_types'])}")
    return missing


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


def tool(name):
    return name.rsplit("__", 1)[-1]


def decide(decision, reason):
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse", "permissionDecision": decision, "permissionDecisionReason": reason}}))


def risk_lines(record):
    if record is None:
        return "No record of the saved YAML (it was saved outside this plugin), so it was not checked."
    if not record["risks"]:
        return "No risky steps found in the saved YAML."
    return "Risks in the saved YAML:\n" + "\n".join(f"- {risk}" for risk in record["risks"])


def pre(payload):
    name, args = tool(payload.get("tool_name", "")), payload.get("tool_input") or {}
    if name == "save_automation":
        if errors := automation_errors(args.get("yaml") or ""):
            decide("deny", "Not saved. Fix these in the YAML and save again:\n" + "\n".join(f"- {e}" for e in errors))
    elif name == "trigger_test_run":
        decide("ask", "Runs the saved draft for real, against live systems.\n"
               + risk_lines(load_record(args.get("automation_id"))))
    elif name == "publish_automation":
        record = load_record(args.get("automation_id"))
        tested = "The saved YAML passed a test run." if record and record.get("tested") else \
            "The saved YAML has NO successful test run since its last save."
        decide("ask", f"Publishing makes the automation live.\n{tested}\n{risk_lines(record)}")
    elif name == "publish_agent":
        decide("ask", "Publishing makes the agent callable from workflows.\n"
               + risk_lines(load_record(args.get("agent_id"))))
    elif name == "delete_table":
        decide("ask", f"Deletes the table {args.get('table') or '(unknown)'!r} and all its rows. This cannot be undone.")
    elif name == "edit_table" and args.get("remove_fields"):
        decide("ask", "Removes columns " + ", ".join(args["remove_fields"])
               + f" from table {args.get('table')!r}, with their data. This cannot be undone.")


def post(payload):
    name, args = tool(payload.get("tool_name", "")), payload.get("tool_input") or {}
    try:
        result = tool_result(payload.get("tool_response"))
    except ValueError:
        result = {}
    if name == "save_automation":
        item_id = result.get("automation_id") or args.get("automation_id")
        yaml_text = args.get("yaml") or ""
        save_record(item_id, {"risks": workflow_risks(yaml_text), "sha": digest(yaml_text), "tested": False})
        if missing := steps_without_connection(yaml_text):
            readiness = ("Test run readiness: BLOCKED. These steps have no connection; pick one from "
                         "workspace/connections/connections.tsv or ask the user:\n" + "\n".join(f"- {m}" for m in missing))
        else:
            readiness = "Test run readiness: READY."
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": readiness}}))
    elif name == "save_agent":
        item_id = result.get("agent_id") or args.get("agent_id")
        save_record(item_id, {"risks": agent_risks(args.get("yaml") or "")})
    elif name == "trigger_test_run":
        item_id = args.get("automation_id")
        record = load_record(item_id)
        if record is not None:
            record["tested"] = str(result.get("state", "")).lower() == PASSED_STATE
            save_record(item_id, record)


def main():
    payload = {}
    try:
        payload = json.load(sys.stdin)
        (post if sys.argv[1:] == ["post"] else pre)(payload)
    except Exception as exc:
        # A broken check never blocks work, but a gated call still asks the user.
        if sys.argv[1:] != ["post"] and tool(payload.get("tool_name", "")) in GATED:
            decide("ask", f"The plugin's safety check failed ({exc}), so this call was not checked.")
        else:
            print(f"safety_gate: {exc}", file=sys.stderr)


if __name__ == "__main__":
    main()
