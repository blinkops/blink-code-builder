"""The linear chain a drafted automation goes through once it exists as YAML: validate
it, save it as a draft, trigger a test run, and inspect the run's log if needed.
"""
import difflib
import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx
import yaml

from ._blink import resolve_playbook_ref, update_draft, update_id_cache, cached_playbook_id, \
    list_packs, find_playbook_across_packs, record_test_evidence, load_test_evidence, \
    yaml_digest, iter_steps, flatten_steps
from ._workspace import (workspace_action_names, WORKFLOWS_LIST_PATH,
                         WORKFLOW_PREFIX, AGENT_PREFIX)
from ._safety import scan_blast_radius
from blink_shared.client import build_client, raise_for_status
from blink_shared.config import catalog_root, editor_url, workspace_base_url, workspace_root


TRUNCATE_LIMIT = 600

DEFAULT_PACK = "blink-code-builder"

# Delimiter the run-streaming endpoint puts between messages.
MESSAGE_SEPARATOR = b"%%%%_____________%%%%BLINK_MESSAGE%%%%_____________%%%%"
END_EXECUTION_COMMAND = "EndExecution"
MAX_WAIT_SECONDS = 600
ALLOWLIST_PATH = workspace_root() / "workflows" / "connections-allowlist.yaml"

AUTOMATION_TYPES = {"on_demand", "scheduled", "event"}
AGENT_STEP_REQUIRED_INPUTS = ("task",)
AGENT_STEP_KNOWN_INPUTS = (
    "task", "output_schema", "roles_and_constraints", "timeout",
    "run_draft", "continue_last_session", "allow_extra_verbose_answer",
)
STEP_REFERENCE = re.compile(r"\{\{\s*steps\.([\w\-.]+)\s*}}")
INPUT_REFERENCE = re.compile(r"\{\{\s*inputs\.([\w\-.]+)\s*}}")

# Engine-handled actions: not registered in the controller catalog. They're valid
# only as direct children of `internal.ifCondition`. Validated structurally below.
IF_CONDITION_ACTION = "internal.ifCondition"
BRANCH_ACTIONS = {"internal.ifTrueBranch", "internal.ifFalseBranch"}

# Actions whose `inputs.code` is inline Python we can syntax-check before running.
PYTHON_ACTIONS = {"core.python", "core.pythonV2"}

# internal.SetVariables: `Variables` is a list of {Name, Type, Value} objects — capitalized,
# not documented in the catalog (its `Variables` param is typed opaque `text`). Shape and
# confirmed `Type` values (String, Number, List) observed in automations Blink's own generator
# produces, plus the case studies in reference/handling-sparse-actions.md.
SET_VARIABLES_ACTION = "internal.SetVariables"
SET_VARIABLES_REQUIRED_KEYS = ("Name", "Type", "Value")
SET_VARIABLES_LOWERCASE_ALIASES = {"name": "Name", "type": "Type", "value": "Value"}
SET_VARIABLES_CONFIRMED_TYPES = {"String", "Number", "List"}

# Parameter whose value must be a dict keyed by field name, not a list — used by
# internal.new_interactive_web_form / internal.new_dynamic_web_form. Confirmed against
# automations Blink's own generator produces.
WEB_FORM_INPUTS_PARAMETER = "web_form_inputs"

# Human-wait detection: steps that block until a person responds. Test runs can't
# exercise these — see reference/safety.md (Human-wait steps section).
SLEEP_ACTION = "internal.Sleep"
LOOP_ACTION_NAME = "internal.for"
WEB_FORM_RESPONSE_MODE = "Web Form Response"
WAIT_FOR_RESPONSE_KEY = "wait_for_response"
LONG_WAIT_UNITS = {"hours", "days"}

CHECKBOX_INPUT_TYPE = "checkbox"
NUMBER_INPUT_TYPE = "number"
MULTI_SELECT_INPUT_TYPE = "multi-select"
_BOOL_STRINGS = {"true", "false", "1", "0", "yes", "no", "on", "off"}
PLAYBOOK_INPUT_TYPES = {
    "text", "dynamic_text", "number", "checkbox", "single-select",
    "long-text", "rich-text", "multi-select", "date", "connections", "file", "list",
}


class _DuplicateKeyLoader(yaml.SafeLoader):
    """SafeLoader that records repeated keys instead of silently keeping the last one."""


def _construct_mapping_tracking_duplicates(loader, node, deep=False):
    mapping = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        value = loader.construct_object(value_node, deep=deep)
        if key in mapping:
            loader.duplicate_keys.append((key, key_node.start_mark.line + 1))
        mapping[key] = value
    return mapping


_DuplicateKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_mapping_tracking_duplicates,
)


def _find_duplicate_keys(yaml_text):
    """Return `[(key, line_no), ...]` for keys repeated within the same mapping.

    PyYAML's default loader silently keeps the last occurrence of a repeated key
    (e.g. two `inputs:` under one step, or two `Content:` entries) and drops the
    earlier value — no parse error, no structural error, just quietly wrong data
    that only surfaces as a broken test run. Parsed separately from the main
    `safe_load` pass so a duplicate never blocks the rest of validation.
    """
    loader = _DuplicateKeyLoader(yaml_text)
    loader.duplicate_keys = []
    try:
        loader.get_single_data()
    except yaml.YAMLError:
        return []  # unparsable; the main parse-error path already reports this
    finally:
        loader.dispose()
    return loader.duplicate_keys


def _yaml_parse_error_hint(message):
    """Best-effort tip for the most common LLM-authored YAML formatting mistakes.

    Confirmed against PyYAML directly (see reference/yaml-formatting.md):
    an unquoted leading `*` (e.g. a cron expression) is parsed as an alias
    reference, and an unquoted scalar containing `: ` or a leading `- ` is
    parsed as a nested mapping/sequence. All three raise ScannerError with a
    distinct, matchable message instead of a hint toward the actual fix.
    """
    lowered = message.lower()
    if "while scanning an alias" in lowered:
        return (
            "[HINT] A value starting with `*` (e.g. a cron expression like `*/5 * * * *`) is "
            "parsed as a YAML alias reference unless quoted. Wrap it in quotes, "
            'e.g. `cron: "*/5 * * * *"`.'
        )
    if "mapping values are not allowed here" in lowered:
        return (
            "[HINT] A plain (unquoted) scalar containing `: ` (colon + space) is parsed as a "
            "new mapping key. Quote the whole value, or use a literal block scalar (`|`) for "
            "multi-line / colon-containing text. See reference/yaml-formatting.md."
        )
    if "sequence entries are not allowed here" in lowered:
        return (
            "[HINT] A plain (unquoted) scalar starting with `- ` (dash + space) is parsed as a "
            "sequence entry. Quote the whole value, or use a literal block scalar (`|`). "
            "See reference/yaml-formatting.md."
        )
    return None


def _split_full_name(full_name):
    service, _, name = full_name.partition(".")
    return (service, name) if name else None


def _extract_all_actions_from_workflow(automation):
    action_names = set()
    for step in flatten_steps(automation.get("workflow")):
        action = step.get("action")
        if isinstance(action, str) and action not in BRANCH_ACTIONS:
            action_names.add(action)
    return action_names


def _load_actions_for_automation(action_names):
    """Load only the action entries referenced in this automation."""
    actions_dir = catalog_root() / "actions"
    if not actions_dir.is_dir():
        return None
    catalog = {}
    for full_name in action_names:
        parts = _split_full_name(full_name)
        if not parts:
            continue
        service, name = parts
        action_file_path = actions_dir / service / f"{name}.json"
        if action_file_path.is_file():
            try:
                catalog[full_name] = json.loads(action_file_path.read_text())
            except json.JSONDecodeError:
                pass
    return catalog


def _load_triggers_catalog():
    triggers_dir = catalog_root() / "triggers"
    if not triggers_dir.is_dir():
        return {}
    triggers_catalog = {}
    for path in triggers_dir.rglob("*.json"):
        try:
            trigger = json.loads(path.read_text())
            triggers_catalog[trigger["full_name"]] = trigger
        except (json.JSONDecodeError, KeyError):
            continue
    return triggers_catalog


def _is_template(value):
    return isinstance(value, str) and "{{" in value and "}}" in value


def _lint_embedded_code(automation):
    """Syntax-check inline Python in core.python / core.pythonV2 steps.

    Catalog-independent — runs in degraded mode too, so a Python typo is caught
    here instead of at runtime. Skips bodies that embed `{{ }}` template
    expressions, which aren't valid standalone Python.
    """
    errors = []
    for step in flatten_steps(automation.get("workflow")):
        if step.get("action") not in PYTHON_ACTIONS:
            continue
        code = (step.get("inputs") or {}).get("code")
        if not isinstance(code, str) or not code.strip():
            continue
        if "{{" in code and "}}" in code:
            continue  # contains Blink template expressions; can't compile statically
        try:
            compile(code, f"<step {step.get('id', '?')}>", "exec")
        except SyntaxError as syntax_error:
            location = f"line {syntax_error.lineno}" if syntax_error.lineno else "unknown line"
            errors.append(
                f"[ERROR] step {step.get('id')} ({step.get('action')}): "
                f"Python SyntaxError: {syntax_error.msg} ({location})"
            )
    return errors


def _check_number(value, options=None):
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return None
    try:
        float(str(value))
        return None
    except (ValueError, TypeError):
        return "cannot be parsed as a number"


def _check_checkbox(value, options=None):
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value in (0, 1):
        return None
    if isinstance(value, str) and value.lower() in _BOOL_STRINGS:
        return None
    return "cannot be parsed as a boolean"


def _check_single_select(value, options):
    if options and str(value) not in [str(o) for o in options]:
        return f"not in options {options}"
    return None


def _check_multi_select(value, options):
    if not options:
        return None
    allowed = {str(o) for o in options}
    bad = [e.strip() for e in str(value).split(",") if e.strip() not in allowed]
    return f"contains options not in {options}: {bad}" if bad else None


def _check_unintended_type_coercion(input_type, value):
    """Flag a value that was probably meant as a literal string but got coerced by
    YAML's implicit typing (the "Norway problem": `NO`/`yes`/`off`/`1.20`/`007` parse as
    `False`/`True`/`False`/`1.2`/`7`). Only fires for non-numeric, non-checkbox parameters —
    a coerced bool/number there is a real mismatch, not an intentional value."""
    if input_type in (NUMBER_INPUT_TYPE, CHECKBOX_INPUT_TYPE):
        return None
    if isinstance(value, bool):
        return (
            f"looks like an unquoted YAML boolean ({value!r}, e.g. from `yes`/`no`/`true`/`off`) "
            "but this parameter isn't a checkbox — quote the value if a literal string was intended."
        )
    if isinstance(value, (int, float)):
        return (
            f"looks like an unquoted YAML number ({value!r}) but this parameter isn't numeric — "
            "if a literal string was intended (e.g. a version, zip code, or ID with leading "
            "zeros), quote it."
        )
    return None


def _validate_set_variables(steps):
    """Validate internal.SetVariables `Variables` shape: list of {Name, Type, Value}.

    Lowercase keys (name/type/value) are silently dropped by the controller — the step
    "succeeds" with the variable left unset, discovered only in a test run.
    Returns (errors, warnings)."""
    errors = []
    warnings = []
    for step in steps:
        if step.get("action") != SET_VARIABLES_ACTION:
            continue
        step_id = step.get("id", "<no id>")
        step_inputs = step.get("inputs") or {}
        variables = step_inputs.get("Variables")

        if variables is None:
            if any(alias in step_inputs for alias in SET_VARIABLES_LOWERCASE_ALIASES):
                errors.append(
                    f"[ERROR] step {step_id} ({SET_VARIABLES_ACTION}): found a lowercase "
                    "`variables` key — the controller only reads capitalized `Variables`; "
                    "the lowercase key is silently dropped."
                )
            continue  # missing entirely: reported as a missing required parameter elsewhere

        if _is_template(variables):
            continue  # templated; can't verify statically
        if not isinstance(variables, list):
            errors.append(
                f"[ERROR] step {step_id} ({SET_VARIABLES_ACTION}): `Variables` must be a "
                f"list of {{Name, Type, Value}} objects, got {type(variables).__name__}."
            )
            continue

        for index, entry in enumerate(variables):
            if not isinstance(entry, dict):
                errors.append(
                    f"[ERROR] step {step_id} ({SET_VARIABLES_ACTION}): `Variables[{index}]` "
                    f"must be an object with Name/Type/Value keys, got {entry!r}."
                )
                continue

            lowercase_used = [key for key in entry if key in SET_VARIABLES_LOWERCASE_ALIASES]
            if lowercase_used:
                errors.append(
                    f"[ERROR] step {step_id} ({SET_VARIABLES_ACTION}): `Variables[{index}]` "
                    f"uses lowercase key(s) {lowercase_used} — Blink only reads the capitalized "
                    f"form ({[SET_VARIABLES_LOWERCASE_ALIASES[k] for k in lowercase_used]}); "
                    "the lowercase entry is silently dropped."
                )

            missing_keys = [key for key in SET_VARIABLES_REQUIRED_KEYS if key not in entry]
            if missing_keys:
                errors.append(
                    f"[ERROR] step {step_id} ({SET_VARIABLES_ACTION}): `Variables[{index}]` "
                    f"missing required key(s) {missing_keys}."
                )

            type_val = entry.get("Type")
            if isinstance(type_val, str) and type_val not in SET_VARIABLES_CONFIRMED_TYPES and not _is_template(type_val):
                # Warning, not an error: doesn't block validation, just reported.
                warnings.append(
                    f"[WARN] step {step_id} ({SET_VARIABLES_ACTION}): `Variables[{index}].Type` "
                    f"{type_val!r} is not one of the confirmed values {sorted(SET_VARIABLES_CONFIRMED_TYPES)} "
                    "— unconfirmed values may still work but haven't been verified; consider a test run."
                )
    return errors, warnings


def _warn_wait_steps(workflow):
    """Return [WARN] lines (non-failing) for wait patterns that make workflows run ~forever.

    Design smells, not structural errors — a long wait is sometimes intentional, so
    these never block validation. The matching hard stop for test runs lives in
    trigger_test_run's human-wait gate."""
    warnings = []

    for step, ancestors in iter_steps(workflow):
        inputs = step.get("inputs") or {}
        if step.get("action") == SLEEP_ACTION:
            step_id = step.get("id", "<no id>")
            if any(parent.get("action") == LOOP_ACTION_NAME for parent in ancestors):
                warnings.append(
                    f"[WARN] step {step_id} ({SLEEP_ACTION}) inside a loop — the wait repeats "
                    "once per item (N items × wait = the workflow effectively runs forever). "
                    "Consider a scheduled trigger or splitting into two workflows."
                )
            if inputs.get("Mode") == WEB_FORM_RESPONSE_MODE:
                warnings.append(
                    f"[WARN] step {step_id} ({SLEEP_ACTION}) waits for a human (web form response). "
                    "The workflow parks until someone answers, and an automatic test run can't "
                    "exercise it (the test-run tool will hand off to the user). The more robust "
                    "pattern: end this workflow after sending, and let a `webforms` trigger start "
                    "a second workflow on submission — recommend it to the user, don't switch silently."
                )
            long_units = [
                f"{key}: {value}" for key, value in inputs.items()
                if isinstance(value, str) and "unit" in key.lower() and value.lower() in LONG_WAIT_UNITS
            ]
            if long_units:
                warnings.append(
                    f"[WARN] step {step_id} ({SLEEP_ACTION}) waits on an hours/days scale "
                    f"({', '.join(long_units)}) — a long-parked run is fragile and easy to forget. "
                    "Consider a scheduled trigger or a second event-triggered workflow instead."
                )
    return warnings


def _validate_web_form_inputs(steps):
    """`web_form_inputs` must be a dict keyed by field name, not a list — an easy mix-up
    with internal.SetVariables' list-of-objects shape."""
    errors = []
    for step in steps:
        value = (step.get("inputs") or {}).get(WEB_FORM_INPUTS_PARAMETER)
        if value is None or _is_template(value):
            continue
        if not isinstance(value, dict):
            errors.append(
                f"[ERROR] step {step.get('id', '<no id>')} ({step.get('action')}): "
                f"`{WEB_FORM_INPUTS_PARAMETER}` must be a dict keyed by field name "
                f"(e.g. `{{my_field: {{type: text, index: 1}}}}`), got {type(value).__name__}."
            )
    return errors


def _validate_parameter_value(input_type, value, options):
    _TYPE_VALIDATORS = {
        NUMBER_INPUT_TYPE: _check_number,
        CHECKBOX_INPUT_TYPE: _check_checkbox,
        MULTI_SELECT_INPUT_TYPE: _check_multi_select
    }
    validator = _TYPE_VALIDATORS.get(input_type)
    if validator:
        return validator(value, options)
    if options:
        return _check_single_select(value, options)
    return None


def _validate_automation_inputs(automation):
    """Validate top-level inputs: type names must be lowercase and defaults must match their type."""
    errors = []
    inputs = automation.get("inputs") or {}
    if not isinstance(inputs, dict):
        return errors
    for input_name, spec in inputs.items():
        if not isinstance(spec, dict):
            errors.append(
                f"[ERROR] input {input_name!r}: must be a parameter-definition object "
                f"(type, required, default, ...), got {spec!r}. A bare value here isn't "
                "just invalid — the controller silently corrupts it by indexing it "
                f"char-by-char. Wrap it, e.g. `{input_name}: {{type: text, default: {spec!r}}}`."
            )
            continue
        type_val = spec.get("type")
        if type_val is None:
            errors.append(
                f"[ERROR] input {input_name!r}: missing required `type`. Every playbook input "
                f"must declare one of {sorted(PLAYBOOK_INPUT_TYPES)} — without it the UI can't "
                "render the input field when the workflow is run manually. "
                "See reference/handling-sparse-actions.md (PlaybookInputParamDefinition shape)."
            )
            continue
        if type_val != type_val.lower():
            errors.append(
                f"[ERROR] input {input_name!r}: `type` must be lowercase, "
                f"got {type_val!r} — use {type_val.lower()!r}"
            )
            continue
        if type_val not in PLAYBOOK_INPUT_TYPES:
            errors.append(
                f"[ERROR] input {input_name!r}: `type` {type_val!r} is not a valid playbook input type "
                f"(valid: {sorted(PLAYBOOK_INPUT_TYPES)})"
            )
            continue
        default = spec.get("default")
        if default is None or default == "" or _is_template(str(default)):
            continue
        err = _validate_parameter_value(type_val, default, spec.get("options"))
        if err:
            errors.append(
                f"[ERROR] input {input_name!r}: `default` {default!r} {err} (type: {type_val!r})"
            )
    return errors


def _validate_triggers(automation, triggers_catalog):
    """Validate the top-level `triggers:` block against `automation_type`."""
    errors = []
    automation_type = automation.get("automation_type") or "on_demand"
    triggers = automation.get("triggers")

    if automation_type == "on_demand":
        if triggers is not None:
            errors.append("[ERROR] `triggers` set but `automation_type` is `on_demand` — remove `triggers:` or change automation_type.")
        return errors

    # event + scheduled both require triggers.
    if triggers is None:
        errors.append(f"[ERROR] `automation_type: {automation_type}` requires a `triggers:` block.")
        return errors
    if not isinstance(triggers, dict):
        errors.append("[ERROR] `triggers` must be a map keyed by category (`scheduled`, `webhooks`, or `polling`).")
        return errors

    if automation_type == "scheduled":
        allowed = {"scheduled"}
    else:  # event
        allowed = {"webhooks", "polling"}

    disallowed_categories = [key for key in triggers if key not in allowed]
    for category in disallowed_categories:
        if category in {"scheduled", "webhooks", "polling"}:
            errors.append(
                f"[ERROR] `triggers.{category}` is not allowed for `automation_type: {automation_type}` "
                f"(allowed: {sorted(allowed)})."
            )
        else:
            errors.append(
                f"[ERROR] `triggers.{category}` — unknown trigger category. "
                f"Use one of {sorted(allowed)}. The full-name-keyed shape "
                f"(`triggers: {{ <full_name>: {{...}} }}`) is silently stripped on save — "
                "see reference/triggers.md for the canonical list shape."
            )

    for category, entries in triggers.items():
        if category not in allowed:
            continue
        if not isinstance(entries, list) or not entries:
            errors.append(f"[ERROR] `triggers.{category}` must be a non-empty list (wrap the entry in `- ...`).")
            continue
        for index, entry in enumerate(entries):
            if not isinstance(entry, dict):
                errors.append(f"[ERROR] `triggers.{category}[{index}]` must be a map.")
                continue
            if category == "scheduled":
                cron = entry.get("cron")
                if not isinstance(cron, str) or not cron.strip():
                    errors.append(f"[ERROR] `triggers.scheduled[{index}].cron` must be a non-empty crontab string.")
            else:  # webhooks / polling
                trigger_type = entry.get("trigger_type")
                if not isinstance(trigger_type, str) or not trigger_type:
                    errors.append(f"[ERROR] `triggers.{category}[{index}].trigger_type` is required (string).")
                else:
                    # custom_webhook is a built-in generic trigger and isn't in triggers.tsv;
                    # other webhook/polling trigger_types must exist in the catalog.
                    if trigger_type != "custom_webhook" and trigger_type not in triggers_catalog and triggers_catalog:
                        errors.append(
                            f"[ERROR] `triggers.{category}[{index}].trigger_type={trigger_type!r}` "
                            f"not found in triggers catalog."
                        )
                if not isinstance(entry.get("name"), str) or not entry.get("name"):
                    errors.append(f"[ERROR] `triggers.{category}[{index}].name` is required (string).")

    return errors


def _validate_agent_step_inputs(step):
    """Check an `agents.<uuid>` step's inputs against the RunAgent contract.

    Needed because agents-list.tsv has no parameter list, so `_check_step_readiness`
    can't see that `task` is required — a step missing it would otherwise be reported READY
    and then fail at run time. Unknown names are only a warning: if the action ever gains a
    parameter, a stale list here must not block a valid workflow.
    """
    errors, warnings = [], []
    step_inputs = step.get("inputs") or {}
    for name in AGENT_STEP_REQUIRED_INPUTS:
        if _is_value_empty(step_inputs.get(name)):
            errors.append(
                f"[ERROR] step {step.get('id')}: agent step needs a non-empty {name!r} input — "
                "the plain-language goal for this run. The agent has nothing to do without it."
            )
    for name in step_inputs:
        if name not in AGENT_STEP_KNOWN_INPUTS:
            warnings.append(
                f"[WARN] step {step.get('id')}: {name!r} is not a known agent step input and will "
                f"be ignored at run time. Known inputs: {', '.join(AGENT_STEP_KNOWN_INPUTS)}."
            )
    if step_inputs.get("run_draft"):
        warnings.append(
            f"[WARN] step {step.get('id')}: `run_draft: true` runs the agent's UNPUBLISHED draft. "
            "Use it only while testing a change to the agent, and remove it before handing the "
            "workflow over."
        )
    return errors, warnings


def _validate_workspace_action_call(step, action_name, workspace_actions):
    """`automations.<uuid>` (subflow) and `agents.<uuid>` (agent) steps call something the
    workspace owns. Neither is ever reliable to check against the vendor actions catalog —
    both actions are created at publish time and the vendor catalog can lag up to 7 days —
    so check workflows-list.tsv / agents-list.tsv instead, which are kept in sync after every
    save and publish. Presence there *is* callability: the controller exposes these actions
    only while the target is published and active."""
    if workspace_actions is None:
        return []  # workspace snapshot unavailable: can't verify, don't false-flag
    if action_name in workspace_actions:
        return []
    if action_name.startswith(AGENT_PREFIX):
        return [
            f"[ERROR] step {step.get('id')}: agent {action_name!r} is not callable in this "
            "workspace. Either the id is wrong, or the agent has never been published — an "
            "agent becomes callable only when published. Take the `id` column of a "
            "published/modified row in agents-list.tsv and write `agents.<id>`, or publish "
            "the agent first (see the generating-agent skill)."
        ]
    return [
        f"[ERROR] step {step.get('id')}: action {action_name!r} is not callable in this workspace. "
        "Either the id is wrong, or the target workflow isn't published and active — only a "
        "published, active on_demand workflow is callable as a subflow. Take the `id` column "
        "of a callable row in workflows-list.tsv and write `automations.<id>`, or publish the "
        "target workflow first."
    ]


def _validate(automation, actions_catalog, triggers_catalog, workspace_actions, catalog_available=True):
    """Returns (errors, warnings) — warnings never fail validation."""
    errors = []
    warnings = []

    # Top-level shape.
    if "workflow" not in automation:
        errors.append("[ERROR] top-level `workflow` missing")
    automation_type = automation.get("automation_type")
    if automation_type is not None and automation_type not in AUTOMATION_TYPES:
        errors.append(f"[ERROR] `automation_type` must be one of {sorted(AUTOMATION_TYPES)}, got {automation_type!r}")
    workflow = automation.get("workflow")
    if workflow is not None and not isinstance(workflow, list):
        errors.append("[ERROR] `workflow` must be a list")
        return errors, warnings
    if isinstance(workflow, list) and len(workflow) > 1:
        section_names = [s.get("section") for s in workflow if isinstance(s, dict)]
        errors.append(
            f"[ERROR] `workflow` has {len(workflow)} sections {section_names} — use exactly one "
            "section (named `Steps`). The Blink editor UI rejects saves with more than one "
            "section; express phases/branching via flow-control actions inside the single section, "
            "not by splitting sections."
        )
    errors.extend(_validate_triggers(automation, triggers_catalog))
    errors.extend(_validate_automation_inputs(automation))
    errors.extend(_lint_embedded_code(automation))
    warnings.extend(_warn_wait_steps(workflow))

    steps = flatten_steps(workflow)
    set_variables_errors, set_variables_warnings = _validate_set_variables(steps)
    errors.extend(set_variables_errors)
    warnings.extend(set_variables_warnings)
    errors.extend(_validate_web_form_inputs(steps))

    # Per-step checks: shape, id uniqueness, ifCondition structure, action lookup + inputs.
    seen_ids = set()
    duplicate_ids = set()
    for step in steps:
        # Shape + id uniqueness.
        if step_id := step.get("id"):
            if step_id in seen_ids:
                duplicate_ids.add(step_id)
            seen_ids.add(step_id)
        else:
            errors.append(f"[ERROR] step missing `id` ({json.dumps(step)[:160]})")

        if "connection" in step:
            errors.append(
                f"[ERROR] step {step.get('id', '<no id>')}: uses singular `connection:` key "
                "— silently dropped at runtime. Use the plural `connections: {<type>: <name>}` map."
            )

        if not (action_name := step.get("action")):
            errors.append(f"[ERROR] step {step.get('id', '<no id>')}: missing `action`")
            continue

        # ifCondition structure: children must be exactly the two branches.
        if action_name == IF_CONDITION_ACTION:
            child_actions = [
                child.get("action") for child in (step.get("steps") or [])
                if isinstance(child, dict)
            ]
            if sorted(child_actions) != sorted(BRANCH_ACTIONS):
                errors.append(
                    f"[ERROR] step {step.get('id')} ({IF_CONDITION_ACTION}): "
                    f"children must be exactly {sorted(BRANCH_ACTIONS)}, got {child_actions}"
                )

        # Action exists + required parameters + enum options.
        if action_name in BRANCH_ACTIONS:
            continue  # engine-handled, structurally validated above
        if action_name.startswith((WORKFLOW_PREFIX, AGENT_PREFIX)):
            errors.extend(_validate_workspace_action_call(step, action_name, workspace_actions))
            if action_name.startswith(AGENT_PREFIX):
                agent_errors, agent_warnings = _validate_agent_step_inputs(step)
                errors.extend(agent_errors)
                warnings.extend(agent_warnings)
            continue
        action = actions_catalog.get(action_name)
        if action is None:
            if catalog_available:
                errors.append(f"[ERROR] step {step.get('id')}: action {action_name!r} not in catalog. Search actions.tsv for the correct full_name and fix the YAML.")
            continue  # degraded mode: can't verify the action without the catalog

        # Connection key must be one of the action's connection_types. Catches a
        # wrong/transposed integration slug on the connection even when the action
        # full_name itself is correct — e.g. an action whose connection key reorders
        # the parts of a multi-word vendor slug. The key is then a non-existent slug,
        # so the connection never binds at runtime.
        connection_types = action.get("connection_types") or []
        step_connections = step.get("connections")
        if connection_types and isinstance(step_connections, dict):
            for connection_key in step_connections:
                if connection_key not in connection_types:
                    suggestions = difflib.get_close_matches(connection_key, connection_types, n=3, cutoff=0.3)
                    hint = f" Did you mean: {', '.join(suggestions)}?" if suggestions else ""
                    errors.append(
                        f"[ERROR] step {step.get('id')} ({action_name}): connection key "
                        f"{connection_key!r} is not one of this action's connection_types.{hint}"
                    )

        step_parameters = step.get("inputs") or {}
        action_parameters_by_name = {parameter["name"]: parameter for parameter in action.get("parameters") or []}
        # Missing required parameters are reported by `check_step_readiness`.
        for parameter_name, value in step_parameters.items():
            action_parameter = action_parameters_by_name.get(parameter_name)
            if not action_parameter or value is None:
                continue
            if _is_template(value):
                continue  # templated; can't verify statically
            input_type = action_parameter.get("input_type")
            error = _validate_parameter_value(input_type, value, action_parameter.get("options"))
            if error:
                errors.append(f"[ERROR] step {step.get('id')} ({action_name}): parameter {parameter_name}={value!r} {error}")
            coercion_error = _check_unintended_type_coercion(input_type, value)
            if coercion_error:
                errors.append(f"[ERROR] step {step.get('id')} ({action_name}): parameter {parameter_name} {coercion_error}")

    for step_id in sorted(duplicate_ids):
        errors.append(f"[ERROR] duplicate step id {step_id!r}")

    # `{{ steps.<id> }}` and `{{ inputs.<name> }}` references must point at declared step ids
    # and automation inputs — second pass since this needs the full set.
    automation_inputs = automation.get("inputs")
    declared_input_names = set(automation_inputs) if isinstance(automation_inputs, dict) else set()
    for step in steps:
        for parameter_name, value in (step.get("inputs") or {}).items():
            if not isinstance(value, str):
                continue
            for match in STEP_REFERENCE.finditer(value):
                referenced_id = match.group(1).split(".")[0]
                if referenced_id not in seen_ids:
                    errors.append(f"[ERROR] step {step.get('id')}: parameter {parameter_name} references unknown step id {referenced_id!r}")
            for match in INPUT_REFERENCE.finditer(value):
                referenced_input = match.group(1).split(".")[0]
                if referenced_input not in declared_input_names:
                    errors.append(f"[ERROR] step {step.get('id')}: parameter {parameter_name} references unknown automation input {referenced_input!r}")

    return errors, warnings


def _is_value_empty(value):
    """Mirror UI's isValueEmpty: empty string/None/empty list/array w/ falsy first item."""
    if value is None:
        return True
    if isinstance(value, str):
        return value == ""
    if isinstance(value, list):
        return not value or not value[0]
    if isinstance(value, dict):
        return not value
    return False


def _is_step_inactive(step):
    """Skip mock-mode + disabled steps (UI: filterActiveSteps)."""
    return step.get("when") == "false" or bool(step.get("mock_output_enabled"))


def _get_parameter_value(step, parameter_name):
    """Look up `parameter_name` in the step's parameters (step.inputs). For internal.* actions,
    also check one level of nesting (e.g. inputs.condition.lvalue) since the UI groups them there.

    Concrete case: `internal.ifCondition` declares `sentence`/`lvalue`/`op`/`rvalue` as
    required in the catalog, but the YAML stores them under `inputs.condition.*` — without
    the nested lookup, every ifCondition step would falsely flag those as missing."""
    step_parameters = step.get("inputs") or {}
    if parameter_name in step_parameters:
        return step_parameters[parameter_name]
    if step.get("action", "").startswith("internal."):
        for value in step_parameters.values():
            if isinstance(value, dict) and parameter_name in value:
                return value[parameter_name]
    return None


def _needs_user_value(action_parameter):
    """A required parameter the user must supply a value for explicitly.

    Excludes: optional parameters, linked-hidden ones (inherited at runtime), checkboxes
    (always true/false), and parameters with a catalog-provided default."""
    return (
        action_parameter.get("required")
        and not action_parameter.get("linked_hidden")
        and action_parameter.get("input_type") != CHECKBOX_INPUT_TYPE
        and action_parameter.get("default_value") in (None, "")
    )


def _has_bound_connection(step):
    """True if the step binds at least one real connection (not `no_connection` or blank).

    Connection key is `connections: { <type>: <name> }` (plural map). The deprecated
    singular `connection: <name>` is silently dropped at runtime, so a step that
    only declares the singular form is treated here as missing a connection."""
    step_connections = step.get("connections")
    if not isinstance(step_connections, dict):
        return False
    return any(
        isinstance(name, str) and name and name != "no_connection"
        for name in step_connections.values()
    )


def _check_step_readiness(step, actions_catalog):
    """Return (missing_parameter_names, connection_types_or_None) for a single step.

    Mirrors step-template.component.ts:checkIfAttributesAreValid in the UI.
    """
    if _is_step_inactive(step):
        return [], None
    action_name = step.get("action")
    if not isinstance(action_name, str):
        return [], None
    if action_name in BRANCH_ACTIONS:
        return [], None
    action = actions_catalog.get(action_name)
    if action is None:
        return [], None  # structural validation already flagged unknown actions

    missing_parameters = [
        action_parameter["name"]
        for action_parameter in action.get("parameters") or []
        if _needs_user_value(action_parameter)
        and _is_value_empty(_get_parameter_value(step, action_parameter["name"]))
    ]

    connection_types = action.get("connection_types") or []
    if connection_types and not action.get("connection_optional", False) and not _has_bound_connection(step):
        return missing_parameters, connection_types

    return missing_parameters, None


def _required_inputs_without_default(automation):
    """Automation-level inputs the user must supply on Run."""
    automation_inputs = automation.get("inputs") or {}
    if not isinstance(automation_inputs, dict):
        return []
    return [
        name for name, spec in automation_inputs.items()
        if isinstance(spec, dict) and spec.get("required") and not spec.get("default")
    ]


def _render_readiness(automation, actions_catalog):
    """Render the Test-run readiness section. Called after structural validation passes."""
    connection_lines = []
    parameter_lines = []
    for step in flatten_steps(automation.get("workflow")):
        parameter_names, connection_types = _check_step_readiness(step, actions_catalog)
        step_id = step.get("id") or "<no id>"
        action_name = step.get("action")
        if connection_types:
            connection_lines.append(f"    {step_id} ({action_name}) — type: {', '.join(connection_types)}")
        if parameter_names:
            parameter_lines.append(f"    {step_id} ({action_name}) — {', '.join(parameter_names)}")
    runtime_inputs = _required_inputs_without_default(automation)

    if not (connection_lines or parameter_lines or runtime_inputs):
        return "\nTest run readiness: READY — can be triggered via Test run."

    lines = ["\nTest run readiness: BLOCKED — fix or ask the user before running:"]
    if connection_lines:
        lines.append("  [CONNECTIONS] missing — ask the user which existing connection to use:")
        lines.append("\n".join(connection_lines))
    if parameter_lines:
        lines.append("  [DATA] required action inputs are empty — fix in YAML or ask the user:")
        lines.append("\n".join(parameter_lines))
    if runtime_inputs:
        lines.append("  [RUNTIME_INPUTS] required playbook inputs without defaults — user must supply on Run:")
        lines.append("\n".join(f"    - {name}" for name in runtime_inputs))
    return "\n".join(lines)


def validate_automation(path, allow_missing_catalog=False):
    """Validate a blink automation YAML against the local catalog.

    Reports two layers:
      * Structural validity (action exists, required parameters present, references resolve, ...).
        Mirrors the controller-side validators.
      * Test-run readiness (the same checks the UI uses to enable/disable the "Test run"
        button — missing required action inputs, missing required connections, required
        playbook-level inputs without defaults). Surfaced so the SKILL flow can decide
        whether to trigger the run, fix the YAML, or ask the user.

    Inline Python in core.python / core.pythonV2 steps is syntax-checked too (catalog-independent).

    Returns rendered text carrying the markers callers branch on: `[ERROR]` (structural errors),
    `[CATALOG MISSING]` (suppressed by allow_missing_catalog=True, which drops to degraded mode:
    structural + embedded-code checks only), or `[OK]` plus a readiness section. Raises `RuntimeError`
    only for a hard failure (file not found).
    """
    file_path = Path(path)
    if not file_path.is_file():
        raise RuntimeError(f"file not found: {file_path}")

    raw_text = file_path.read_text()
    try:
        automation = yaml.safe_load(raw_text) or {}
    except yaml.YAMLError as parse_error:
        result = f"[ERROR] YAML parse: {parse_error}"
        hint = _yaml_parse_error_hint(str(parse_error))
        return f"{result}\n{hint}" if hint else result

    duplicate_key_errors = [
        f"[ERROR] duplicate key {key!r} at line {line} — YAML silently keeps only the last "
        "occurrence and drops the earlier value. Rename one or merge them."
        for key, line in _find_duplicate_keys(raw_text)
    ]

    automation_actions_names = _extract_all_actions_from_workflow(automation)
    actions_catalog = _load_actions_for_automation(automation_actions_names)
    catalog_available = actions_catalog is not None
    lines = []
    if not catalog_available:
        if not allow_missing_catalog:
            return (
                "[CATALOG MISSING] no actions catalog found. Check the SessionStart hook + userConfig. "
                "(For a code-only revision, re-run with --allow-missing-catalog.)"
            )
        lines.append(
            "[WARN] actions catalog not found — degraded mode: skipping action-existence, enum, "
            "and readiness checks. Structural + embedded-code checks still run."
        )
        actions_catalog = {}

    triggers_catalog = _load_triggers_catalog()
    workspace_actions = workspace_action_names()

    errors, warnings = _validate(automation, actions_catalog, triggers_catalog, workspace_actions, catalog_available)
    errors = duplicate_key_errors + errors

    # A subflow must never call itself directly. We only know this automation's own id if a
    # previous save cached it — indirect (multi-hop) cycles are the engine's job to catch.
    own_playbook_id = cached_playbook_id(automation.get("name") or "")
    if own_playbook_id and f"{WORKFLOW_PREFIX}{own_playbook_id}" in automation_actions_names:
        errors.append(
            f"[ERROR] a step calls this automation's own playbook id ({own_playbook_id}) — "
            "a subflow must never call itself."
        )

    lines.extend(warnings)
    lines.extend(errors)
    if errors:
        return "\n".join(lines)

    if catalog_available:
        lines.append("[OK] automation is valid")
        lines.append(_render_readiness(automation, actions_catalog))
    else:
        lines.append("[OK] automation is structurally valid (degraded: catalog not checked)")
        lines.append("\nTest run readiness: UNKNOWN — catalog missing, readiness not evaluated.")
    return "\n".join(lines)


def _resolve_pack_id(api, packs, pack_override):
    """Pack to create a NEW playbook in: the override, else the default pack (created if absent)."""
    if pack_override:
        return pack_override
    for pack in packs:
        if pack.get("pack_name") == DEFAULT_PACK:
            return str(pack.get("pack_id"))
    created = raise_for_status(api.post("/automation_packs", json={"name": DEFAULT_PACK})).json()
    return str(created["id"])


def _create_playbook(api, pack_id, name, yaml_text):
    """Create a new draft-only playbook. Turns the common 400 into an actionable message."""
    resp = api.post("/playbooks/ui", json={"name": name, "playbook": yaml_text, "pack_id": pack_id})
    if resp.status_code == 400:
        raise RuntimeError(
            f"could not create playbook {name!r} (HTTP 400). A playbook with this name "
            "may already exist (possibly in another pack the listing didn't return), or the YAML "
            "was rejected. If you know the playbook id, re-run with --playbook-id <id|url>, or pull "
            f"it first with fetch_automation <id|url>. Server said: {resp.text[:300]}"
        )
    return str(raise_for_status(resp).json()["id"])


def _workflow_state(automation):
    """Where a workflow stands between its draft and its published version.
    One of `draft`, `published`, `modified` — same semantics as an agent's state."""
    if not automation.get("is_published"):
        return "draft"
    return "modified" if automation.get("has_unpublished_changes") else "published"


def list_workflows(output=""):
    """List every workflow in the workspace, drafts included, and write it to a TSV file in
    the repo (default: workspace/workflows/workflows-list.tsv) — mirrors get_tables_schema for tables.

    This is the only local way to see a draft or inactive workflow — a row that isn't
    `on_demand`/`active`/`published`-or-`modified` isn't callable as a subflow yet.
    """
    with build_client() as api:
        packs = list_packs(api)

    lines = [
        "# state: draft = never published | published = live, draft matches it | "
        "modified = live, but the draft has newer edits that are not published yet",
        "# id\tname\tautomation_type\tstate\tactive",
    ]
    total = 0
    for pack in packs:
        for automation in pack.get("automations") or []:
            total += 1
            lines.append(
                f"{automation.get('id')}\t{automation.get('name') or ''}\t"
                f"{automation.get('automation_type') or 'on_demand'}\t{_workflow_state(automation)}\t"
                f"{'true' if automation.get('active') else 'false'}"
            )

    out_path = Path(output) if output else WORKFLOWS_LIST_PATH
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines) + "\n")

    return f"ok: wrote {out_path} ({total} workflows)"


def _sync_workflows_list():
    try:
        list_workflows()
    except Exception:
        pass  # best effort; rerun list_workflows if the local file falls out of sync


def _report(base_url, playbook_id, suffix=""):
    _sync_workflows_list()
    return (
        f"ok: saved playbook {playbook_id}{suffix}"
        f"\napi: {base_url}/playbooks/{playbook_id}"
        f"\neditor: {editor_url(playbook_id)}"
    )


def save_automation(path, playbook_id=""):
    """Save a blink automation YAML as a draft playbook.

    Resolution order for the target playbook:
      1. --playbook-id <id|url>  — update that playbook directly (the clean path after
         fetch_automation, or for any playbook authored in the UI).
      2. name->id cache          — fast path for playbooks this tool has touched before.
      3. search ALL packs by name — finds playbooks in any pack, not just the default one.
      4. create a new playbook    — only when the name isn't found anywhere.
    """
    file_path = Path(path)
    if not file_path.is_file():
        raise RuntimeError(f"file not found: {file_path}")
    yaml_text = file_path.read_text()
    name = (yaml.safe_load(yaml_text) or {}).get("name") or file_path.stem

    api = build_client()
    base_url = workspace_base_url()

    # 1. Explicit id wins — the unambiguous path for UI-authored playbooks.
    if playbook_id:
        resolved_id = resolve_playbook_ref(playbook_id)
        updated = update_draft(api, resolved_id, yaml_text)
        if not updated:
            raise RuntimeError(f"playbook {resolved_id!r} not found (404) — check the id/URL.")
        update_id_cache(name, updated)
        return _report(base_url, updated, " (--playbook-id)")

    lines = []

    # 2. Cache fast path: skip GET /automations when we've saved this name before.
    cached = cached_playbook_id(name)
    if cached:
        updated = update_draft(api, cached, yaml_text)
        if updated:
            update_id_cache(name, updated)
            return _report(base_url, updated, " (cached)")
        lines.append(f"cached playbook {cached} not found, falling back to full lookup")

    # 3. Search every pack by name (not just the default pack).
    packs = list_packs(api)
    existing_id, pack_of = find_playbook_across_packs(packs, name)
    if existing_id:
        lines.append(f"updating existing playbook {existing_id!r} in pack {pack_of!r}")
        updated = update_draft(api, existing_id, yaml_text)
        if not updated:
            raise RuntimeError(f"playbook {existing_id!r} listed in pack but returned 404 — try again")
        update_id_cache(name, updated)
        lines.append(_report(base_url, updated))
        return "\n".join(lines)

    # 4. Not found anywhere — create it.
    pack_id = _resolve_pack_id(api, packs, os.environ.get("CLAUDE_PLUGIN_OPTION_BLINK_PACK_ID", ""))
    lines.append(f"creating new playbook {name!r} in pack {pack_id!r}")
    new_playbook_id = _create_playbook(api, pack_id, name, yaml_text)
    update_id_cache(name, new_playbook_id)
    lines.append(_report(base_url, new_playbook_id))
    return "\n".join(lines)


def _collect_step_connection_names(workflow):
    """Walk the workflow tree, return the set of connection names referenced at the step level.
    Canonical YAML: `connections: { <type>: <name> }` (plural map).
    """
    names = set()
    for step, _ in iter_steps(workflow):
        connections = step.get("connections")
        if isinstance(connections, dict):
            for value in connections.values():
                if isinstance(value, str) and value:
                    names.add(value)
    return names


def _load_allowlist():
    """Return the set of allowed connection names, or None if the file doesn't exist."""
    if not ALLOWLIST_PATH.is_file():
        return None
    try:
        data = yaml.safe_load(ALLOWLIST_PATH.read_text()) or []
    except yaml.YAMLError as parse_error:
        raise RuntimeError(f"{ALLOWLIST_PATH}: {parse_error}")
    if not isinstance(data, list):
        raise RuntimeError(f"{ALLOWLIST_PATH} must be a YAML list of connection names.")
    return {str(name) for name in data if isinstance(name, str) and name}


def _enforce_connection_allowlist(yaml_text):
    """Return a `[BLOCKED CONNECTIONS]` message if any step-level connection in the draft
    isn't on the per-repo allowlist, else None.
    """
    referenced = _collect_step_connection_names((yaml.safe_load(yaml_text) or {}).get("workflow"))
    if not referenced:
        return None
    allowed = _load_allowlist()
    if allowed is None:
        lines = [
            f"[BLOCKED CONNECTIONS] allowlist file missing: {ALLOWLIST_PATH}",
            "automation references these connections — none are allowed yet:",
        ]
        lines.extend(f"  - {name}" for name in sorted(referenced))
        return "\n".join(lines)
    disallowed = sorted(referenced - allowed)
    if disallowed:
        lines = [f"[BLOCKED CONNECTIONS] not in allowlist ({ALLOWLIST_PATH}):"]
        lines.extend(f"  - {name}" for name in disallowed)
        return "\n".join(lines)
    return None


def _scan_human_waits(workflow):
    """Return [(step_id, action, reason), ...] for steps that block on a human response."""
    findings = []
    for step, _ in iter_steps(workflow):
        inputs = step.get("inputs") or {}
        if step.get("action") == SLEEP_ACTION and inputs.get("Mode") == WEB_FORM_RESPONSE_MODE:
            findings.append((
                step.get("id", "<no id>"), SLEEP_ACTION,
                "waits for a web form response — a test run would park here until a human answers (or the timeout, measured in hours, expires)",
            ))
        if inputs.get(WAIT_FOR_RESPONSE_KEY) is True:
            findings.append((
                step.get("id", "<no id>"), step.get("action", "<no action>"),
                "wait_for_response: true — observed to NOT wait in test runs; the run continues instantly with an empty response",
            ))
    return findings


def _enforce_human_wait_gate(yaml_text, playbook_id):
    """Return a `[BLOCKED HUMAN_WAIT]` message if any step waits on a human, else None.

    No override flag — the resolution is a manual run, not an acknowledgment: the
    user runs it from the editor, answers the form themselves, and the SKILL flow
    reads the run log afterwards (get_run_log).
    """
    findings = _scan_human_waits((yaml.safe_load(yaml_text) or {}).get("workflow"))
    if not findings:
        return None
    lines = ["[BLOCKED HUMAN_WAIT] this workflow waits for a human — an automatic test run can't exercise it:"]
    lines.extend(f"  - {step_id} ({action}): {reason}" for step_id, action, reason in findings)
    lines.append("Hand off to the user: ask them to open the workflow in the Blink editor:")
    lines.append(f"  {editor_url(playbook_id)}")
    lines.append("click Test Run, and answer the form themselves. Once they confirm it finished, fetch results with get_run_log.")
    return "\n".join(lines)


def _enforce_blast_radius_gate(yaml_text, acknowledged, publishing=False):
    """Return (blocked_message_or_None, info_lines).

    The `[BLOCKED SAFETY]` marker is dedicated to this gate so the SKILL flow can
    branch on it: present the findings to the user in plain language, and re-call
    with acknowledge_risks=True only after an explicit yes. A test run executes
    real actions — this is the last stop before e.g. a Slack message to the whole
    org. For publishing, approval given for a test run does NOT carry over —
    publish affects every future scheduled/triggered execution, not one
    supervised run.
    """
    findings = scan_blast_radius((yaml.safe_load(yaml_text) or {}).get("workflow"))
    if not findings:
        return None, []
    finding_lines = [f"  - {step_id} ({action}): {reason}" for step_id, action, reason in findings]
    if acknowledged:
        verb = "publishing" if publishing else "proceeding"
        return None, [f"[SAFETY ACKNOWLEDGED] {verb} despite flagged steps:"] + finding_lines
    lines = ["[BLOCKED SAFETY] potentially high blast-radius steps — user confirmation required:"]
    lines.extend(finding_lines)
    if publishing:
        lines.append("Describe these to the user in plain language (who/what is affected, roughly how many),")
        lines.append("and remind them these will run on every future trigger/schedule, not just once.")
        lines.append("Re-call with acknowledge_risks=True only after the user explicitly approves publishing.")
    else:
        lines.append("Describe these to the user in plain language (who/what is affected, roughly how many).")
        lines.append("Re-call with acknowledge_risks=True only after the user explicitly approves.")
    return "\n".join(lines), []


def _build_run_workflow_body(draft, playbook_id, yaml_text):
    """Build the request body for POST /playbooks/{id}/start/stream."""
    runner = draft.get("runner") or ""
    test_session_id = str(uuid.uuid4())

    return {
        "id": playbook_id,
        "playbook": yaml_text,
        "input_values": {},
        "is_draft": True,
        "test_session_id": test_session_id,
        "runner": runner,
        "command": {
            "cmd_id": str(uuid.uuid4()),
            "cmd_type": "RunWorkflow",
            "params": {
                "playbook_id": playbook_id,
                "_Context": {
                    "inputs_type": draft.get("inputs") or {},
                    "runner_group": runner,
                    "connections": draft.get("connections") or {},
                },
                "draft_playbook": yaml_text,
                "test_session_id": test_session_id,
                "command_params": {},
            },
        },
    }


def _parse_messages(response):
    """Yield each parsed JSON message from the separator-delimited stream."""
    buffer = b""
    for chunk in response.iter_bytes():
        buffer += chunk
        while (index := buffer.find(MESSAGE_SEPARATOR)) >= 0:
            raw, buffer = buffer[:index], buffer[index + len(MESSAGE_SEPARATOR):]
            if raw.strip():
                yield json.loads(raw)


def _iter_messages(response):
    """Read the handshake, return (execution_id, remaining_messages_iter).

    The first message carries the execution_id; subsequent messages flow through the iterator.
    """
    messages = _parse_messages(response)
    first = next(messages, None)
    if first is None:
        raise RuntimeError("stream closed before sending any message.")
    execution_id = (first.get("data") or {}).get("id")
    if not execution_id:
        raise RuntimeError(f"stream did not return an execution id; first message: {first!r}")
    return execution_id, messages


def _start_test_run(api, base_url, playbook_id, draft, yaml_text):
    """Open the streaming test run, block until EndExecution. Returns (execution_id, info_lines)."""
    body = _build_run_workflow_body(draft, playbook_id, yaml_text)
    lines = []
    try:
        with api.stream(
            "POST",
            f"/playbooks/{playbook_id}/start/stream",
            json=body,
            timeout=httpx.Timeout(connect=30, read=MAX_WAIT_SECONDS, write=30, pool=30),
        ) as response:
            raise_for_status(response)
            execution_id, messages = _iter_messages(response)

            lines.append("ok: test run started")
            lines.append(f"execution_id: {execution_id}")
            lines.append(f"api: {base_url}/executions/{execution_id}")

            for message in messages:
                data = message.get("data") or {}
                if data.get("cmd_type") == END_EXECUTION_COMMAND and data.get("execution_id") == execution_id:
                    return execution_id, lines
            return execution_id, lines  # stream closed without EndExecution
    except httpx.ReadTimeout:
        raise RuntimeError(f"no progress on stream for {MAX_WAIT_SECONDS}s; aborting.")


def trigger_test_run(playbook_id, acknowledge_risks=False):
    """Trigger a draft test run via the controller's streaming endpoint and block until it finishes.

    Mirrors the UI's Test Run: opens an SSE-style POST to /playbooks/{id}/start/stream,
    which dispatches the RunWorkflow command. We read the streaming response until we see
    an `EndExecution` message (the same signal the UI uses to know the run finished), then
    fetch /executions/{id} once for the final state summary.

    Before opening the run, three gates (a test run is NOT a dry run — it executes real
    actions against real systems, see reference/safety.md):
      1. Connections allowlist (`workspace/workflows/connections-allowlist.yaml`, a YAML list of
         connection names). Any step-level connection not on the list → `[BLOCKED
         CONNECTIONS]` block so the SKILL flow can offer to extend the allowlist.
      2. Human-wait scan. A step that waits for a human (internal.Sleep with
         `Mode: Web Form Response`, or `wait_for_response: true`) can't be exercised by an
         automatic test run — → `[BLOCKED HUMAN_WAIT]` block. No override flag: the
         resolution is a manual run from the editor, then get_run_log.
      3. Blast-radius scan. Steps matching high-impact patterns (destructive action names,
         messaging inside a loop, broadcast mentions like @channel) → `[BLOCKED SAFETY]`
         block. Re-call with acknowledge_risks=True only after an explicit user yes.
    """
    api = build_client()
    base_url = workspace_base_url()

    draft = raise_for_status(api.get(f"/playbooks/{playbook_id}/draft")).json()
    yaml_text = draft.get("playbook") or ""
    if not yaml_text:
        raise RuntimeError(f"no draft YAML for playbook {playbook_id}; save it first.")

    blocked = _enforce_connection_allowlist(yaml_text)
    if blocked:
        return blocked
    blocked = _enforce_human_wait_gate(yaml_text, playbook_id)
    if blocked:
        return blocked
    blocked, safety_lines = _enforce_blast_radius_gate(yaml_text, acknowledge_risks)
    if blocked:
        return blocked

    execution_id, lines = _start_test_run(api, base_url, playbook_id, draft, yaml_text)
    lines = safety_lines + lines

    # Stream closed → run is done. One final fetch for the summary.
    execution = raise_for_status(api.get(f"/executions/{execution_id}")).json()
    state_ui = execution.get("state_ui") or ""
    lines.append(f"state_ui: {state_ui or '<none>'}")
    if execution.get("step_results"):
        lines.append(f"step_results: {execution['step_results']}")
    if state_ui == "Completed":
        # Unlocks publish_automation's [BLOCKED UNTESTED] gate for this exact draft.
        record_test_evidence(playbook_id, yaml_text, execution_id)
    return "\n".join(lines)


def _format_iso(milliseconds):
    if not milliseconds:
        return "-"
    return datetime.fromtimestamp(milliseconds / 1000, tz=timezone.utc).isoformat(timespec="seconds")


def _truncate(value, limit=TRUNCATE_LIMIT):
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return text if len(text) <= limit else f"{text[:limit]}... [truncated, {len(text) - limit} more chars]"


def _compute_duration_ms(step):
    started, finished = step.get("started_at"), step.get("finished_at")
    return finished - started if started and finished else None


def _find_latest_execution(api, playbook_id):
    """Server-side RQL filter."""
    q = json.dumps({"filter": {"playbook_id": playbook_id}, "sort": ["-started_at"], "limit": 1})
    results = raise_for_status(api.get("/executions", params={"q": q})).json().get("results") or []
    return results[0]["id"] if results else None


def _record_evidence_for_completed_run(api, playbook_id, execution_id):
    """A Completed run unlocks publish_automation's [BLOCKED UNTESTED] gate.

    Covers runs the user triggered manually from the editor (the human-wait
    handoff), which never pass through trigger_test_run. Hashes the *current*
    draft — if the user edited it after their manual run, the publish gate will
    correctly see a mismatch on the next edit, not here."""
    try:
        draft_yaml = raise_for_status(api.get(f"/playbooks/{playbook_id}/draft")).json().get("playbook") or ""
    except Exception:
        return  # evidence is best-effort; never fail the log report over it
    if draft_yaml:
        record_test_evidence(playbook_id, draft_yaml, execution_id)


def get_run_log(playbook_id, execution_id=None):
    """Fetch the latest execution log for a playbook; return an LLM-friendly summary."""

    api = build_client()

    if not execution_id:
        execution_id = _find_latest_execution(api, playbook_id)
        if execution_id is None:
            return f"no executions found for playbook {playbook_id}. Ask the user to trigger a test run."

    execution = raise_for_status(api.get(f"/executions/{execution_id}")).json()
    if execution.get("state_ui") == "Completed":
        _record_evidence_for_completed_run(api, playbook_id, execution_id)
    lines = [
        f"Playbook: {execution.get('name')} (id={execution.get('playbook_id')})",
        f"Execution: {execution.get('id')}",
        f"  state={execution.get('state')}  state_ui={execution.get('state_ui')}  duration_ms={execution.get('net_runtime_ms')}",
        f"  started={_format_iso(execution.get('started_at'))}  finished={_format_iso(execution.get('finished_at'))}",
    ]
    error_details = execution.get("error_details") or {}
    if error_details.get("message"):
        lines.append(f"  error: {_truncate(error_details['message'], 400)}  (code={error_details.get('error_code')})")
    if execution.get("inputs"):
        lines.append(f"  inputs: {_truncate(execution['inputs'], 400)}")

    steps_response = raise_for_status(api.get(f"/executions/{execution_id}/steps")).json()
    steps = sorted(
        steps_response.get("results") or [],
        key=lambda step: step.get("started_at") or 0,
    )
    if not steps:
        lines.append("\n(no step results yet — run may still be in progress)")
        return "\n".join(lines)

    lines.append(f"\nSteps ({len(steps)}):")
    for step in steps:
        lines.append(f"  {step.get('step_id')} [{step.get('state')}]  dur_ms={_compute_duration_ms(step)}")
        if step.get("input_parameters"):
            lines.append(f"    inputs: {_truncate(step['input_parameters'])}")
        if step.get("output_data"):
            lines.append(f"    output: {_truncate(step['output_data'])}")
        if step.get("error_data"):
            lines.append(f"    error: {_truncate(step['error_data'])}")

    return "\n".join(lines)


def _enforce_test_evidence_gate(playbook_id, yaml_text, allow_untested):
    """Return (blocked_message_or_None, info_lines) for the publish test-evidence gate.

    The `[BLOCKED UNTESTED]` marker is dedicated to this gate so the SKILL flow can
    branch on it: offer to run the test first, and only skip the gate after the user
    insists again despite the warning."""
    evidence = load_test_evidence(playbook_id)
    tested = bool(evidence) and evidence.get("yaml_sha256") == yaml_digest(yaml_text)
    if tested:
        return None, [f"test evidence: draft passed test run (execution {evidence.get('execution_id')})"]
    if allow_untested:
        return None, ["[UNTESTED ACKNOWLEDGED] publishing without a successful test run of this draft."]
    lines = []
    if evidence:
        lines.append("[BLOCKED UNTESTED] the draft was EDITED after its last successful test run —")
        lines.append("what passed the test is not what would be published.")
    else:
        lines.append("[BLOCKED UNTESTED] this draft has no successful test run on record.")
    lines.append("Resolution: call trigger_test_run first (or, for human-wait workflows, have the")
    lines.append("user test from the editor and confirm via get_run_log). Only re-call with")
    lines.append("allow_untested=True if the user, after being warned, explicitly insists on publishing untested.")
    return "\n".join(lines), []


def _publish_facts(automation):
    """Machine-readable facts for the SKILL flow's plain-language summary
    (name, when it runs, what it does). Returned before the gates so Claude can
    build the user-facing summary even when a gate blocks."""
    lines = ["publish target:"]
    lines.append(f"  name: {automation.get('name') or '<unnamed>'}")
    automation_type = automation.get("automation_type") or "on_demand"
    lines.append(f"  automation_type: {automation_type}")
    for category, entries in (automation.get("triggers") or {}).items():
        for entry in entries if isinstance(entries, list) else []:
            if not isinstance(entry, dict):
                continue
            detail = entry.get("cron") or entry.get("trigger_type") or "?"
            lines.append(f"  trigger: {category} — {detail}")
    steps = []
    for section in automation.get("workflow") or []:
        if isinstance(section, dict):
            steps.extend(step for step in section.get("steps") or [] if isinstance(step, dict))
    lines.append(f"  top-level steps: {len(steps)}")
    for step in steps:
        lines.append(f"    {step.get('id', '<no id>')}: {step.get('action', '?')} — {step.get('name', '')}")
    return lines


def publish_draft(api, playbook_id, yaml_text):
    """POST the YAML to the published version (what scheduled/triggered runs execute).

    Same body as update_draft, different path. Returns the playbook id on success,
    None on 404 (unknown playbook). Other HTTP errors raise."""
    resp = api.post(
        f"/playbooks/{playbook_id}/public",
        json={"playbook": yaml_text, "branched_from": 0, "source": "manual"},
    )
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return playbook_id


def publish_automation(playbook_id, acknowledge_risks=False, allow_untested=False):
    """Publish a playbook's draft so scheduled/triggered/production runs execute it.

    Publishing is the moment the automation goes live — a draft only affects the
    editor, the published version is what actually runs on triggers and schedules.
    The SKILL flow must present a plain-language summary (trigger, what it does,
    blast radius) and get an explicit yes BEFORE calling this tool.

    Before publishing, two gates (same pattern as trigger_test_run):
      1. Test-evidence check. The current draft must be byte-identical to a draft
         that completed a successful test run (recorded by trigger_test_run /
         get_run_log in ${CLAUDE_PLUGIN_DATA}/test_evidence.json). Never tested,
         or edited after the test → `[BLOCKED UNTESTED]` block. The allow_untested
         override exists but the SKILL flow only passes it after the user has been
         warned and explicitly insisted again.
      2. Blast-radius scan (shared with trigger_test_run). Approval given for a
         test run is NOT approval for publishing — findings block again here
         (`[BLOCKED SAFETY]`) until the user gives a fresh explicit yes and the
         flow re-calls with acknowledge_risks=True.
    """
    api = build_client()
    base_url = workspace_base_url()

    draft = raise_for_status(api.get(f"/playbooks/{playbook_id}/draft")).json()
    yaml_text = draft.get("playbook") or ""
    if not yaml_text:
        raise RuntimeError(f"no draft YAML for playbook {playbook_id}; save it first.")

    lines = _publish_facts(yaml.safe_load(yaml_text) or {})

    blocked, evidence_lines = _enforce_test_evidence_gate(playbook_id, yaml_text, allow_untested)
    if blocked:
        return "\n".join(lines + [blocked])
    lines.extend(evidence_lines)

    blocked, safety_lines = _enforce_blast_radius_gate(yaml_text, acknowledge_risks, publishing=True)
    if blocked:
        return "\n".join(lines + [blocked])
    lines.extend(safety_lines)

    try:
        published = publish_draft(api, playbook_id, yaml_text)
    except httpx.HTTPStatusError as error:
        if error.response.status_code == 400:
            # The controller's publish-time validation errors are terse prose
            # (e.g. "failed parsing: description") — surface them with next steps.
            raise RuntimeError(
                "the controller rejected the publish (HTTP 400). Its publish-time "
                "validation is stricter than the draft save and its messages are terse. "
                f"Server said: {error.response.text[:300]}\n"
                "Re-run validate_automation on the YAML; if it passes locally, the failing "
                "field is usually named in the server message — fix it, re-save, re-test, re-publish."
            ) from error
        raise RuntimeError(f"HTTP {error.response.status_code}: {error.response.text[:400]}") from error
    if not published:
        raise RuntimeError(f"playbook {playbook_id!r} not found (404) — check the id.")

    _sync_workflows_list()

    lines.append(f"ok: published playbook {playbook_id}")
    lines.append(f"api: {base_url}/playbooks/{playbook_id}")
    lines.append(f"editor: {editor_url(playbook_id)}")
    return "\n".join(lines)
