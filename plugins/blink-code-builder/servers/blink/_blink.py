"""Server-side helpers for the blink MCP server's operations.

Playbook lookup and draft updates, the name->id cache, the test-evidence store, and the
walk over a workflow's step tree. Config and the HTTP client live in blink_shared,
because the hooks need those too.

Imported by the sibling modules in this directory.
"""

import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path

from blink_shared.client import raise_for_status

CACHE_TTL_SECONDS = 30 * 24 * 60 * 60  # 30 days

# Matches the playbook UUID in a Blink editor URL: .../workflow/<uuid>/edit
_PLAYBOOK_URL_RE = re.compile(r"/workflow/([0-9a-fA-F-]{36})")


def resolve_playbook_ref(ref):
    """Accept a playbook UUID or a Blink editor URL; return the UUID."""
    match = _PLAYBOOK_URL_RE.search(ref or "")
    return match.group(1) if match else (ref or "").strip()


def update_draft(api, playbook_id, yaml_text):
    """POST a draft to a known playbook id. Returns the id on success, None on 404 (stale id)."""
    resp = api.post(
        f"/playbooks/{playbook_id}/draft",
        json={"playbook": yaml_text, "branched_from": 0, "source": "manual"},
    )
    if resp.status_code == 404:
        return None
    raise_for_status(resp)
    return playbook_id


def list_packs(api):
    """All automation packs in the workspace, each carrying its `automations` list."""
    return raise_for_status(api.get("/automations")).json().get("packs") or []


def find_playbook_across_packs(packs, name):
    """First playbook matching `name` across ALL packs. Returns (playbook_id, pack_id) or (None, None).

    The old behavior searched only the default pack, so a playbook authored in the UI
    (which lives in another pack) was missed and re-created as a duplicate.
    """
    for pack in packs:
        for automation in pack.get("automations") or []:
            if automation.get("name") == name:
                return str(automation["id"]), str(pack.get("pack_id"))
    return None, None


# --------------------------------------------------------------------------
# name -> playbook-id cache (shared by save_automation.py and fetch_automation.py)
# --------------------------------------------------------------------------

def _cache_path():
    data_dir = os.environ.get("CLAUDE_PLUGIN_DATA")
    return Path(data_dir) / "automation_cache.json" if data_dir else None


def load_id_cache():
    path = _cache_path()
    if not path or not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    now = time.time()
    return {k: v for k, v in raw.items() if now - v.get("last_used", 0) < CACHE_TTL_SECONDS}


def _write_id_cache(cache):
    path = _cache_path()
    if not path:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(cache, indent=2))
    except OSError as e:
        print(f"warning: could not write automation cache: {e}", file=sys.stderr)


def cached_playbook_id(name, cache=None):
    cache = load_id_cache() if cache is None else cache
    return (cache.get(name) or {}).get("playbook_id")


def update_id_cache(name, playbook_id, cache=None):
    cache = load_id_cache() if cache is None else cache
    cache[name] = {"playbook_id": playbook_id, "last_used": int(time.time())}
    _write_id_cache(cache)
    return cache


# --------------------------------------------------------------------------
# test evidence: playbook-id -> hash of the draft YAML that passed a test run
# (written by pipeline.trigger_test_run / pipeline.get_run_log, read by pipeline.publish_automation)
# --------------------------------------------------------------------------

def _evidence_path():
    data_dir = os.environ.get("CLAUDE_PLUGIN_DATA")
    return Path(data_dir) / "test_evidence.json" if data_dir else None


def yaml_digest(yaml_text):
    return hashlib.sha256(yaml_text.encode()).hexdigest()


def _load_all_evidence():
    path = _evidence_path()
    if not path or not path.is_file():
        return {}
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def record_test_evidence(playbook_id, yaml_text, execution_id):
    """Remember that this exact draft YAML completed a test run successfully."""
    path = _evidence_path()
    if not path:
        return
    evidence = _load_all_evidence()
    evidence[str(playbook_id)] = {
        "yaml_sha256": yaml_digest(yaml_text),
        "execution_id": execution_id,
        "recorded_at": int(time.time()),
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(evidence, indent=2))
    except OSError as e:
        print(f"warning: could not write test evidence: {e}", file=sys.stderr)


def load_test_evidence(playbook_id):
    """Return the evidence dict for this playbook, or None if it never passed a test."""
    return _load_all_evidence().get(str(playbook_id))


# --------------------------------------------------------------------------
# workflow-tree traversal: a workflow is a list of sections, each holding a
# `steps` tree where any step may nest children under its own `steps` key.
# Every scan/validation walks that tree the same way, so the walk lives here once.
# --------------------------------------------------------------------------

def iter_steps(workflow):
    """Yield (step, ancestors) for every step dict in the workflow tree, depth-first.

    `ancestors` is the tuple of enclosing step dicts, outermost first — callers
    that care about context (e.g. "am I inside a loop?") inspect it; the rest
    ignore it."""

    def visit(step, ancestors):
        if not isinstance(step, dict):
            return
        yield step, ancestors
        for child in step.get("steps") or []:
            yield from visit(child, ancestors + (step,))

    for section in workflow or []:
        if isinstance(section, dict):
            for step in section.get("steps") or []:
                yield from visit(step, ())


def flatten_steps(workflow):
    """Return every step in the workflow tree (depth-first)."""
    return [step for step, _ in iter_steps(workflow)]
