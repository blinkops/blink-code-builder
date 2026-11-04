#!/usr/bin/env python3
"""Safety gates and checks around the remote Blink MCP tools. The server only does what it is
asked; what needs the user's approval is the plugin's decision, made here.

Modes (argv[1]):

- `pre` (PreToolUse):
  - save_automation: denies a YAML with an action that is not in the local catalog (when its
    service is, so likely a typo), or a connection key that is not one of the action's
    connection_types, with the reason, so Claude fixes it (as solution-builder's
    validate-automation.ts does). Actions of services the catalog doesn't have are left alone.
  - trigger_test_run: asks, with the risks found in the last saved YAML, only for a risk the
    user has not approved in a test run of this session, or whose step (or its loop) changed
    since. Otherwise it gives no decision, so the user's permission mode decides.
  All other tools get no decision from the plugin: the user's permission mode decides.
- `post` (PostToolUse):
  - save_automation: records the risks of the saved YAML per id.
  - save_automation: tells Claude which steps have no connection (test-run readiness).
  - trigger_test_run: records that the risks of the saved YAML are approved for this session
    (per risk, the hash of its step and of the loops around it).

trigger_test_run takes only an id, so the risks come from what this plugin recorded at save time (${CLAUDE_PLUGIN_DATA}/safety/<id>.json). An id saved elsewhere
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

GATED = {"trigger_test_run"}

LOOP_ACTION = "internal.for"
DESTRUCTIVE = re.compile(
    r"(?i)(delete|remove|terminate|revoke|destroy|purge|suspend|deactivate|erase|wipe|disable|tableclear|\bdrop)"
)
MESSAGING = re.compile(r"(?i)(send|message|mail|notify|broadcast)")
BROAD_MENTION = re.compile(r"(?i)<!(here|channel|everyone)>|@(here|channel|everyone)\b")
HUMAN_WAIT = re.compile(r"(?i)web form response|wait_for_response")
ACTION_LINE = re.compile(r"^(\s*)(?:-\s*)?action:\s*[\"']?([\w.\-]+)")
ITEM_LINE = re.compile(r"^(\s*)-\s+")
ID_LINE = re.compile(r"^\s*(?:-\s+)?id:\s*[\"']?([\w.\-]+)")
STEPS_LINE = re.compile(r"steps:\s*$")
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


def step_blocks(yaml_text):
    """(lines, own lines, loops around it) for each step: a list item with an `action:`. The own
    lines leave out the nested `steps:` list, so a change in a child step doesn't change its parent."""
    lines = yaml_text.splitlines()
    items = []
    for start, line in enumerate(lines):
        if not (match := ITEM_LINE.match(line)):
            continue
        dash = len(match.group(1))
        end = start + 1
        while end < len(lines) and (not lines[end].strip() or indent_of(lines[end]) > dash):
            end += 1
        block = lines[start:end]
        own = own_lines(block, len(match.group(0)))
        if any(ACTION_LINE.match(text) for text in own):
            items.append((start, end, block, own))
    for start, end, block, own in items:
        loops = [outer_own for outer_start, outer_end, _, outer_own in items
                 if outer_start < start and end <= outer_end and step_action(outer_own) == LOOP_ACTION]
        yield block, own, loops


def indent_of(line):
    return len(line) - len(line.lstrip())


def own_lines(block, key_column):
    own, skipping = [], False
    for number, line in enumerate(block):
        if not line.strip():
            continue
        column = key_column if number == 0 else indent_of(line)
        if skipping and (column > key_column or (column == key_column and line.lstrip().startswith("-"))):
            continue
        skipping = column == key_column and bool(STEPS_LINE.match(line[column:]))
        if not skipping:
            own.append(line)
    return own


def step_action(own):
    return next((match.group(2) for text in own if (match := ACTION_LINE.match(text))), "")


def step_id(own):
    return next((match.group(1) for text in own if (match := ID_LINE.match(text))), "")


def workflow_risks(yaml_text):
    """{risk: fingerprint}. Each risk names its step; the fingerprint is a hash of that step and of
    the loops around it, so it changes only when the risky step or its loop changes."""
    risks = {}

    def add(text, *parts):
        risks[text] = digest("\n".join(parts))

    for block, own, loops in step_blocks(yaml_text):
        action, label = step_action(own), step_id(own) or "?"
        fingerprint = ["\n".join(block)] + ["\n".join(loop) for loop in loops]
        if DESTRUCTIVE.search(action):
            add(f"{label} {action}: deletes or disables something" + (", once per loop item" if loops else ""),
                *fingerprint)
        elif loops and MESSAGING.search(action):
            add(f"{label} {action}: sends a message once per loop item", *fingerprint)
        own_text = "\n".join(own)
        if match := BROAD_MENTION.search(own_text):
            add(f"{label} mentions {match.group(0)}: notifies everyone in the channel", *fingerprint)
        if HUMAN_WAIT.search(own_text):
            add(f"{label} waits for a person (web form): an automatic test run cannot answer it", *fingerprint)
    # Outside any step (the trigger, the inputs): the whole YAML is the fingerprint.
    if (match := BROAD_MENTION.search(yaml_text)) and not any("mentions" in risk for risk in risks):
        add(f"mentions {match.group(0)}: notifies everyone in the channel", yaml_text)
    if HUMAN_WAIT.search(yaml_text) and not any("waits for a person" in risk for risk in risks):
        add("waits for a person (web form): an automatic test run cannot answer it", yaml_text)
    return dict(sorted(risks.items()))


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


def unapproved_risks(record, session_id):
    """The risks of the saved YAML that the user has not approved in a test run of this session,
    or whose step changed since. A record without fingerprints (an older plugin) has none approved."""
    fingerprints = record.get("fingerprints") or {}
    approved = record.get("approved") or {}
    approved_now = approved.get("fingerprints", {}) if session_id and approved.get("session_id") == session_id else {}
    return [risk for risk in record["risks"] if risk not in fingerprints or approved_now.get(risk) != fingerprints[risk]]


def approved_note(record, new):
    if count := len(record["risks"]) - len(new):
        return f"\n{count} other risk(s) not shown: approved earlier in this session, and those steps did not change."
    return ""


def pre(payload):
    name, args = tool(payload.get("tool_name", "")), payload.get("tool_input") or {}
    if name == "save_automation":
        if errors := automation_errors(args.get("yaml") or ""):
            decide("deny", "Not saved. Fix these in the YAML and save again:\n" + "\n".join(f"- {e}" for e in errors))
    elif name == "trigger_test_run":
        record = load_record(args.get("automation_id"))
        if record is None:
            decide("ask", "Runs the saved draft for real, against live systems.\n" + risk_lines(record))
        elif new := unapproved_risks(record, payload.get("session_id")):
            decide("ask", "Runs the saved draft for real, against live systems.\n"
                   + risk_lines({"risks": new}) + approved_note(record, new))
        # No new risks: no decision, so the user's permission mode decides.


def post(payload):
    name, args = tool(payload.get("tool_name", "")), payload.get("tool_input") or {}
    try:
        result = tool_result(payload.get("tool_response"))
    except ValueError:
        result = {}
    if name == "save_automation":
        item_id = result.get("automation_id") or args.get("automation_id")
        yaml_text = args.get("yaml") or ""
        risks = workflow_risks(yaml_text)
        # Keep the approval: unapproved_risks() compares it with the new fingerprints.
        approved = (load_record(item_id) or {}).get("approved")
        save_record(item_id, {"risks": list(risks), "fingerprints": risks, "sha": digest(yaml_text),
                              "approved": approved})
        if missing := steps_without_connection(yaml_text):
            readiness = ("Test run readiness: BLOCKED. These steps have no connection; pick one from "
                         "workspace/connections/connections.tsv or ask the user:\n" + "\n".join(f"- {m}" for m in missing))
        else:
            readiness = "Test run readiness: READY."
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": readiness}}))
    elif name == "trigger_test_run":
        item_id = args.get("automation_id")
        record = load_record(item_id)
        if record is not None:
            # The run went ahead, so the user approved its risks (or none were new): remember
            # them for this session until their steps change.
            record["approved"] = {"session_id": payload.get("session_id"), "fingerprints": record.get("fingerprints") or {}}
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
