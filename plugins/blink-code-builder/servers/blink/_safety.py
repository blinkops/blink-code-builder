"""Shared blast-radius scan for the blink MCP server's operations.

Used by both pipeline.trigger_test_run (before a test run) and
pipeline.publish_automation (before publishing): the same heuristics guard both
moments, because approval for a test run is not approval for publishing (see
reference/safety.md).

Imported by the sibling modules in this directory, same convention as _blink.py.
"""

import re

from ._blink import iter_steps

# Blast-radius heuristics — see reference/safety.md. Warn-and-confirm, never a hard ban:
# a match blocks the run only until the user explicitly approves (acknowledge_risks=True).
LOOP_ACTION = "internal.for"
DESTRUCTIVE_ACTION_PATTERN = re.compile(
    r"(?i)(delete|remove|terminate|revoke|destroy|purge|drop|suspend|deactivate|erase|wipe|disable|tableclear)"
)
MESSAGING_ACTION_PATTERN = re.compile(r"(?i)(send|message|mail|notify|broadcast)")
BROAD_MENTION_PATTERN = re.compile(r"(?i)<!(here|channel|everyone)>|@(here|channel|everyone)\b")


def _iter_strings(value):
    """Yield every string nested anywhere inside a step's inputs value."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for child in value.values():
            yield from _iter_strings(child)
    elif isinstance(value, list):
        for child in value:
            yield from _iter_strings(child)


def scan_blast_radius(workflow):
    """Return [(step_id, action, reason), ...] for steps whose impact is
    potentially large or irreversible. Heuristics, not judgment — the SKILL flow
    translates these into a plain-language question for the user."""
    findings = []

    for step, ancestors in iter_steps(workflow):
        action = step.get("action") or ""
        step_id = step.get("id", "<no id>")
        in_loop = any(parent.get("action") == LOOP_ACTION for parent in ancestors)

        if DESTRUCTIVE_ACTION_PATTERN.search(action):
            reason = "destructive/irreversible action name"
            if in_loop:
                reason += " inside a loop — repeats once per item, item count unknown until runtime"
            findings.append((step_id, action, reason))
        elif in_loop and MESSAGING_ACTION_PATTERN.search(action):
            findings.append((
                step_id, action,
                "messaging action inside a loop — sends once per item, item count unknown until runtime",
            ))

        for text in _iter_strings(step.get("inputs") or {}):
            if match := BROAD_MENTION_PATTERN.search(text):
                findings.append((
                    step_id, action,
                    f"broadcast mention ({match.group(0)}) — notifies everyone in the channel/org",
                ))
                break

    return findings
