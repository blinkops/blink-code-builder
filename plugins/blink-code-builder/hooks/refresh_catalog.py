#!/usr/bin/env python3
"""Refresh the blink action catalog through the remote Blink MCP server.

A hook can't call an MCP tool, so this runs in two modes:

- SessionStart (no args): skips silently if the catalog is <7 days old; otherwise asks
  Claude to call the remote tool `export_catalog`. To force a refresh, delete the
  catalog dir and start a new Claude Code session.
- PostToolUse on `export_catalog` (`--save`): Claude Code saves the tool's gzipped blob
  to a .bin file and passes its path in the tool response. This unzips it and writes the
  catalog, then replaces the tool output Claude sees with a one-line status.

Writes <root>/{actions,triggers}/<service>/<name>.json per entry, plus flat TSV
lookup files (`actions.tsv`, `triggers.tsv`) — the grep targets for capability search.

Root: ${CLAUDE_PLUGIN_DATA}/catalog/ — must be set (Claude Code injects it).
"""

import gzip
import json
import re
import shutil
import sys
import time
from pathlib import Path

from _common import MCP_NOT_READY_NOTE, clean_for_tsv
from blink_shared.config import catalog_root

STALE_DAYS = 7


def split_full_name(full_name):
    """Split 'service.name' into a (service, name) pair.

    13 built-in triggers (e.g. 'custom_webhook', 'aws_new_instances') have no
    service prefix — bucket them under '_misc' so the path stays well-formed.
    """
    service, _, name = full_name.partition(".")
    return (service, name) if name else ("_misc", service)


def is_fresh():
    # actions.tsv is the *last* file write_catalog() writes — its mtime is the freshness signal.
    sentinel = catalog_root() / "actions.tsv"
    return sentinel.exists() and (time.time() - sentinel.stat().st_mtime) < STALE_DAYS * 24 * 60 * 60


def saved_blob_path(tool_response):
    """The .bin file Claude Code saved the gzipped catalog to, from the tool response text.
    Matches up to `.bin`, so a path with spaces (C:\\Users\\Jane Doe\\...) stays whole."""
    if isinstance(tool_response, list):
        tool_response = " ".join(block.get("text", "") for block in tool_response if isinstance(block, dict))
    match = re.search(r"saved to (.+?\.bin)", str(tool_response))
    if not match:
        raise ValueError(f"no saved catalog file in the tool output: {str(tool_response)[:200]!r}")
    return Path(match.group(1))


def write_catalog(actions, triggers):
    catalog_dir = catalog_root()
    if catalog_dir.exists():
        shutil.rmtree(catalog_dir)
    catalog_dir.mkdir(parents=True)

    # Triggers written first — actions.tsv ends up as the last file, our freshness sentinel.

    # Per-trigger JSON files
    for trigger in triggers:
        service, name = split_full_name(trigger["full_name"])
        path = catalog_dir / "triggers" / service / f"{name}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(trigger, indent=2, ensure_ascii=False), encoding="utf-8")
    # triggers.tsv index
    lines = ["# full_name\tservice\tdescription"]
    for trigger in sorted(triggers, key=lambda t: t["full_name"]):
        service, _ = split_full_name(trigger["full_name"])
        lines.append(f"{trigger['full_name']}\t{service}\t{clean_for_tsv(trigger.get('description'))}")
    (catalog_dir / "triggers.tsv").write_text("\n".join(lines) + "\n", encoding="utf-8")

    # Per-action JSON files
    for action in actions:
        service, name = split_full_name(action["full_name"])
        path = catalog_dir / "actions" / service / f"{name}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(action, indent=2, ensure_ascii=False), encoding="utf-8")
    # actions.tsv index (last write — freshness sentinel)
    lines = ["# full_name\tservice\tdescription\tconnection_types"]
    for action in sorted(actions, key=lambda a: a["full_name"]):
        service, _ = split_full_name(action["full_name"])
        connection_types = ",".join(action.get("connection_types") or [])
        lines.append(f"{action['full_name']}\t{service}\t{clean_for_tsv(action.get('description') or '')}\t{connection_types}")
    (catalog_dir / "actions.tsv").write_text("\n".join(lines) + "\n", encoding="utf-8")

    return f"ok: saved the catalog ({len(actions)} actions, {len(triggers)} triggers) to {catalog_dir}."


def save():
    payload = json.load(sys.stdin)
    try:
        data = json.loads(gzip.decompress(saved_blob_path(payload.get("tool_response")).read_bytes()))
        status = write_catalog(data["actions"], data["triggers"])
        message = f"Blink \u2713 action catalog ready: {len(data['actions']):,} actions, {len(data['triggers']):,} triggers."
    except Exception as exc:
        status = f"error: could not save the catalog ({exc}). Tell the user."
        message = f"Blink: could not save the action catalog ({exc})."
    print(json.dumps({"systemMessage": message,
                      "hookSpecificOutput": {"hookEventName": "PostToolUse", "updatedToolOutput": status}}))


def main():
    if is_fresh():
        return
    note = (
        "The Blink action catalog is missing or out of date. Before you answer the user's first message, "
        "call the Blink MCP tool `export_catalog` once. A hook saves it to disk; don't read or summarize "
        "the result, and don't mention this to the user unless it fails. "
        + MCP_NOT_READY_NOTE
    )
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": note}}))


if __name__ == "__main__":
    save() if "--save" in sys.argv else main()
