"""Blink agent lifecycle: fetch -> validate -> save -> publish.

An agent is one YAML file locally, and two rows server-side: identity
(name/title/description/pack) plus a versioned behavior blob (role/constraints/
abilities/modes) that has exactly two versions, "draft" and "published".
`role` is the agent's system prompt. The YAML keys are the controller's own
solution-schema names, kept verbatim so a fetched agent round-trips unchanged.

Backend contract (relative to {controller}/api/v1/workspace/{ws}):
  GET    /agents?q={"filter":{...}}   one row per agent: identity + draft_version +
                                      published_version + is_published. The only read
                                      path — the per-id GETs no longer exist.
  GET    /agent_packs                 packs; only used to place a new agent
  POST   /agent_packs                 create a pack ({name})
  POST   /agents          {pack_id}   create agent + empty draft
  PATCH  /agents/:id                  update name/title/description only
  POST   /agents/:id/draft            replace the draft version
  POST   /agents/:id/publish          save draft + published + upsert the agents.<id> action

Publishing an agent makes it a callable action. An ability must be a published, active,
on-demand workflow of this workspace, so publish the callee before the caller.
"""

import json
import re
from pathlib import Path
from typing import NamedTuple
import yaml
from blink_shared.client import build_client, raise_for_status
from blink_shared.config import agent_editor_url, workspace_base_url, workspace_root
from ._workspace import (AGENTS_LIST_PATH, AGENT_ACTION_PREFIX, callable_workflow_ids,
                         agent_id_by_name)


DEFAULT_AGENT_PACK = "Home"
ABILITY_TYPE_WORKFLOW = "workflow"
_PRESERVED_KEYS = ("knowledge", "avatar_logo", "avatar_style", "avatar_color")
_AGENT_MODE_FIELDS = ("chat_enabled", "code_execution_enabled")

# The agent UUID in an agent-builder URL: .../agents/agent-builder/<uuid>
_AGENT_URL_RE = re.compile(r"/agent-builder/([0-9a-fA-F-]{36})")

# Characters that must not reach a filename
_UNSAFE_FILENAME_RE = re.compile(r'[/\\:*?"<>|\x00-\x1f]+')


def _read_agent(api, agent_id="", name=""):
    """Find one agent by id or by name — pass exactly one of them.

    Filters GET /agents server-side, so either key matches at most one agent.
    Returns that agent's row (identity + draft_version + published_version), or None.
    """
    key, value = ("id", agent_id) if agent_id else ("name", name)
    query = json.dumps({"filter": {key: value}})
    payload = raise_for_status(api.get("/agents", params={"q": query})).json()
    rows = payload.get("results") if isinstance(payload, dict) else payload
    return rows[0] if rows else None


def _resolve_agent_ref(ref):
    """Turn an agent reference (URL/UUID) into a plain UUID.
    Gets a UUID or an agent-builder URL; returns the UUID."""
    match = _AGENT_URL_RE.search(ref or "")
    return match.group(1) if match else (ref or "").strip()


def _safe_filename(name, fallback):
    """Turn an agent name into a filename that cannot escape its directory.
    Replaces illegal characters with `-`; returns `fallback` if nothing is left."""
    cleaned = _UNSAFE_FILENAME_RE.sub("-", name or "").strip(" .")
    return cleaned or fallback


class Ability(NamedTuple):
    """One ability: a published, active, on-demand workflow of this workspace that the agent
    may run. Field order is the order the keys are written to YAML."""
    ability_id: str = ""
    type: str = ABILITY_TYPE_WORKFLOW
    auto_approved: bool = False
    summarize_output: bool = False
    summarize_output_instructions: str = ""

    @classmethod
    def from_mapping(cls, entry):
        """Build from one `abilities:` entry, so a partial YAML entry is still valid.

        An entry that is not a mapping at all becomes one with an empty `ability_id`, which
        _validate_config then reports like any other missing id.
        """
        if not isinstance(entry, dict):
            return cls()
        return cls(
            ability_id=str(entry.get("ability_id") or ""),
            type=entry.get("type") or ABILITY_TYPE_WORKFLOW,
            auto_approved=bool(entry.get("auto_approved")),
            summarize_output=bool(entry.get("summarize_output")),
            summarize_output_instructions=entry.get("summarize_output_instructions") or "",
        )

    def to_body(self):
        """This ability as one entry of a request body's `abilities` list."""
        return self._asdict()

    def to_yaml_entry(self):
        """This ability as one `abilities:` entry, empty values dropped for readability."""
        return {field: value for field, value in self._asdict().items()
                if value not in ("", None)}



class AgentConfig(NamedTuple):
    """One agent's configuration — an agent YAML file, parsed. Field order is write order.

    The mode keys sit flat here rather than in a nested object, since there are only ever
    _AGENT_MODE_FIELDS of them; the `modes` property nests them back for the file and the API.

    The four preserved fields (see _PRESERVED_KEYS) default to None, meaning "the YAML did not
    mention this", which is what makes _refill_from_draft refill them from the agent's
    current draft. A value that is present but empty, e.g. `knowledge: []`, is not None and
    really does delete.
    """
    name: str = ""
    title: str = ""
    description: str = ""
    role: str = ""
    constraints: str = ""
    chat_enabled: bool = False
    code_execution_enabled: bool = False
    abilities: tuple[Ability, ...] = ()
    knowledge: list | None = None
    avatar_style: str | None = None
    avatar_color: str | None = None
    avatar_logo: str | None = None

    @staticmethod
    def modes_of(mapping):
        """Every mode field of `mapping`, as a bool — a missing key is off. Reads a `modes`
        mapping out of YAML or a server draft; returns both the object a request body nests
        and the mode keyword arguments this class takes."""
        return {field: bool((mapping or {}).get(field)) for field in _AGENT_MODE_FIELDS}

    @property
    def modes(self):
        """The mode fields as the one `modes` object the YAML file and the API both nest."""
        return self.modes_of(self._asdict())

    @classmethod
    def from_yaml(cls, mapping):
        """Build from a parsed agent YAML file."""
        return cls(
            name=mapping.get("name") or "",
            title=mapping.get("title") or "",
            description=mapping.get("description") or "",
            role=mapping.get("role") or "",
            constraints=mapping.get("constraints") or "",
            **cls.modes_of(mapping.get("modes")),
            abilities=tuple(Ability.from_mapping(entry)
                            for entry in mapping.get("abilities") or ()),
            knowledge=mapping.get("knowledge"),
            avatar_style=mapping.get("avatar_style"),
            avatar_color=mapping.get("avatar_color"),
            avatar_logo=mapping.get("avatar_logo"),
        )

    @classmethod
    def from_row(cls, row):
        """Build from a GET /agents row, reading its draft version — not the published one,
        because the draft is what save_agent writes back."""
        draft = row.get("draft_version") or {}
        return cls(
            name=row.get("name") or "",
            title=row.get("title") or "",
            description=row.get("description") or "",
            role=draft.get("role") or "",
            constraints=draft.get("constraints") or "",
            **cls.modes_of(draft.get("modes")),
            abilities=tuple(Ability.from_mapping(entry)
                            for entry in draft.get("abilities") or () if entry),
            knowledge=draft.get("knowledge") or [],
            avatar_style=draft.get("avatar_style") or "",
            avatar_color=draft.get("avatar_color") or "",
            avatar_logo=draft.get("avatar_logo") or "",
        )

    def to_yaml(self):
        """Render as the text of an agent YAML file.
        Keeps field order and drops empty values, so the file stays short and readable."""
        ordered = {}
        for field, value in self._asdict().items():
            if field in _AGENT_MODE_FIELDS:
                # The mode fields collapse into one `modes:` mapping holding only what is on,
                # so the file stays short. Written once per mode field; re-assigning the same
                # key leaves it where the first one put it, which keeps write order.
                field, value = "modes", {key: on for key, on in self.modes.items() if on}
            elif field == "abilities":
                value = [ability.to_yaml_entry() for ability in value]
            if value in (None, "", [], {}):
                continue
            ordered[field] = value
        return yaml.dump(ordered, sort_keys=False, allow_unicode=True,
                         default_flow_style=False, width=100)

    def to_identity_body(self):
        """The identity fields PATCH /agents/:id takes — the only fields it reads, and the ones
        /draft and /publish ignore."""
        return {"name": self.name, "title": self.title, "description": self.description}

    def to_body(self, agent_id):
        """The full request body /draft and /publish expect, for this config plus the agent id."""
        return {
            "id": agent_id,
            "name": self.name,
            "title": self.title,
            "description": self.description,
            "role": self.role,
            "constraints": self.constraints,
            "abilities": [ability.to_body() for ability in self.abilities],
            # knowledge and avatar are read-only here: whatever the server has is sent back
            # unchanged. See _PRESERVED_KEYS and _refill_from_draft.
            "knowledge": list(self.knowledge or []),
            "modes": self.modes,
            "avatar_style": self.avatar_style or "",
            "avatar_color": self.avatar_color or "",
            "avatar_logo": self.avatar_logo or "",
        }


def _load_agent_config(path):
    """Read an agent YAML file into an AgentConfig — the one entry point for a local agent file.
    Raises on a missing file, bad YAML, or a non-mapping, so callers can trust the shape."""
    file_path = Path(path)
    if not file_path.is_file():
        raise RuntimeError(f"file not found: {file_path}")
    try:
        spec = yaml.safe_load(file_path.read_text())
    except yaml.YAMLError as exc:
        raise RuntimeError(f"invalid YAML in {file_path}: {exc}")
    if not isinstance(spec, dict):
        raise RuntimeError(f"{file_path} does not contain a YAML mapping.")
    return AgentConfig.from_yaml(spec)


def _refill_from_draft(api, agent_id, config):
    """Refill the fields the YAML left out from the agent's current draft (_PRESERVED_KEYS).

    Needed because /draft and /publish take a full body and overwrite everything in it, so
    fields the YAML never mentions — knowledge, the avatar — would be wiped on every save.
    Returns a new config. Stays silent on failure: a save must not fail over this.
    """
    config_fields = config._asdict()
    absent = [key for key in _PRESERVED_KEYS if config_fields[key] is None]
    if not absent:
        return config
    try:
        row = _read_agent(api, agent_id=agent_id)
    except Exception:
        return config  # best effort: never fail a save over this
    if not row:
        return config
    current_fields = AgentConfig.from_row(row)._asdict()
    refilled = {key: value for key, value in current_fields.items() if key in absent and value}
    return config._replace(**refilled) if refilled else config




def _agent_state(row):
    """Where an agent stands between its draft and its published version.
    One of `draft`, `published`, `modified` — see the legend in list_agents."""
    if not row.get("is_published"):
        return "draft"
    return "modified" if row.get("has_unpublished_changes") else "published"


def list_agents(output=""):
    """List every agent in the workspace, drafts included, and write it to a TSV file in the
    repo (default: workspace/agents/agents-list.tsv).

    This is the only local way to see a draft agent — an agent becomes callable as
    `agents.<id>` only once published.
    """
    with build_client() as api:
        payload = raise_for_status(api.get("/agents")).json()
    rows = payload.get("results") if isinstance(payload, dict) else payload

    lines = [
        "# state: draft = never published | published = live, draft matches it | "
        "modified = live, but the draft has newer edits that are not published yet",
        "# action\tname\tstate",
    ]
    total = 0
    for row in rows or []:
        total += 1
        state = _agent_state(row)
        name = row.get("name") or ""
        if row.get("title"):
            name += f" | {row['title']}"
        lines.append(f"{AGENT_ACTION_PREFIX}{row.get('id')}\t{name}\t{state}")

    out_path = Path(output) if output else AGENTS_LIST_PATH
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines) + "\n")

    return f"ok: wrote {out_path} ({total} agents)"


def _sync_agents_list():
    try:
        list_agents()
    except Exception:
        pass  # best effort; rerun list_agents if the local file falls out of sync


def fetch_agent(ref, stdout=False, output=""):
    """Download an agent from the workspace and write it as local YAML.

    Gets an agent id or an agent-builder URL. Returns the YAML text if `stdout`, else a
    summary of the file it wrote (`output`, or workspace/agents/<name>.yaml).

    An agent has two versions server-side, draft and published. This writes the draft,
    so that editing the file and calling save_agent updates the same version you pulled.
    """
    api = build_client()
    agent_id = _resolve_agent_ref(ref)

    row = _read_agent(api, agent_id=agent_id)
    if not row:
        raise RuntimeError(
            f"agent {agent_id!r} not found in this workspace. "
            "Check the id/URL and that it belongs to the configured workspace."
        )
    config = AgentConfig.from_row(row)
    yaml_text = config.to_yaml()

    if stdout:
        return yaml_text

    out_path = (Path(output) if output
                else workspace_root() / "agents" / f"{_safe_filename(config.name, agent_id)}.yaml")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(yaml_text)

    return (
        f"ok: wrote {out_path} (agent {agent_id})\n"
        f"name: {config.name}\n"
        f"editor: {agent_editor_url(agent_id)}\n"
        "note: save_agent will update this agent in place (matched by name)."
    )

def _classify_unresolved_abilities(api, unresolved):
    """Explain why ability ids are missing from the local catalog, in one live call.

    Gets the unresolved ids. Returns ({id: workflow name} that exist but are not callable,
    [ids] that do not exist in this workspace at all) — the two need different fixes.
    """
    packs = raise_for_status(api.get("/automations")).json().get("packs") or []
    known = {}
    for pack in packs:
        for automation in pack.get("automations") or []:
            known[str(automation.get("id"))] = automation.get("name") or ""
    unpublished = {aid: known[aid] for aid in unresolved if aid in known}
    missing = [aid for aid in unresolved if aid not in known]
    return unpublished, missing


def _validate_config(config):
    """Check an agent configuration and collect every problem. Returns (errors, warnings).

    Errors block a save. Warnings never block: a broken ability makes an agent weaker
    rather than dangerous, and blocking would stop the user fixing the rest of the file.
    """
    errors, warnings = [], []

    if not config.name.strip():
        errors.append("`name` is empty — the controller rejects an agent without one.")
    if not config.role.strip():
        errors.append("`role` is empty — it is the agent's system prompt; without it the "
                      "agent has no instructions.")

    seen_ids = set()
    ability_ids = []
    for index, ability in enumerate(config.abilities):
        if not ability.ability_id:
            errors.append(f"abilities[{index}]: every entry needs an `ability_id` "
                          "(a published on-demand workflow's uuid).")
            continue
        ability_id = ability.ability_id
        if ability_id in seen_ids:
            errors.append(f"abilities[{index}]: `{ability_id}` is listed twice — the agent "
                          "would see the same ability twice with contradictory settings. Keep one entry.")
        seen_ids.add(ability_id)
        ability_ids.append(ability_id)
        if ability.type != ABILITY_TYPE_WORKFLOW:
            errors.append(f"abilities[{index}]: type `{ability.type}` does not exist — abilities "
                          "are workflows only. To reach a vendor action, wrap it in a small "
                          "on-demand workflow, publish it, and attach that.")
        if ability.auto_approved:
            warnings.append(f"ability {ability_id} is `auto_approved: true` — the agent runs it "
                            "with NO human approval, in every future session. Publish will block "
                            "on this until the user explicitly approves.")

    # Check every ability is a callable workflow, locally first, then live for the misses.
    # Warnings only, never errors: the local file can be stale.
    unique_ids = list(dict.fromkeys(ability_ids))
    callable_ids = callable_workflow_ids() or set()
    unresolved = [aid for aid in unique_ids if aid not in callable_ids]

    if unresolved:
        try:
            with build_client(timeout=30) as api:
                unpublished, missing = _classify_unresolved_abilities(api, unresolved)
        except Exception as exc:  # offline or unconfigured: warn, never block
            warnings.append(f"{len(unresolved)} abilit(y/ies) not in the local catalog and the "
                            f"live check failed ({exc}) — re-check when the workspace is reachable.")
        else:
            for ability_id, wf_name in unpublished.items():
                warnings.append(f"ability {ability_id} is workflow {wf_name!r} in this workspace "
                                "but it is NOT callable (unpublished, deactivated, or not "
                                "on-demand). Publish it, then re-validate.")
            for ability_id in missing:
                warnings.append(f"ability {ability_id} does not exist in this workspace — likely "
                                "imported from another workspace without its workflow, or deleted. "
                                "The agent will silently never see it. Do NOT delete the line to "
                                "clear this warning — tell the user and ask.")

    return errors, warnings


def validate_agent(path):
    """Validate a local agent YAML file and report the result as text.
    [ERROR] lines block save_agent and publish_agent; [WARN] lines never block."""
    config = _load_agent_config(path)
    errors, warnings = _validate_config(config)

    lines = [f"[ERROR] {msg}" for msg in errors] + [f"[WARN] {msg}" for msg in warnings]
    abilities = config.abilities
    auto_approved = sum(1 for ability in abilities if ability.auto_approved)
    if errors:
        lines.append(f"\n{len(errors)} error(s) — fix before saving.")
    else:
        lines.append("[OK] agent is valid" + (" (with warnings)" if warnings else ""))
    lines.append(f"{len(abilities)} abilities, {auto_approved} auto-approved")
    return "\n".join(lines)



def _list_agent_packs(api):
    """Every agent pack in the workspace, as a list.
    Unwraps the {"results": [...]} envelope that list endpoints return."""
    payload = raise_for_status(api.get("/agent_packs")).json()
    if isinstance(payload, dict):
        payload = payload.get("results") or []
    return payload


def _ensure_agent_pack(api, packs):
    """The id of the pack a new agent goes into.
    Gets the existing packs; creates DEFAULT_AGENT_PACK if none of them matches."""
    for pack in packs:
        if (pack.get("name") or "").strip().lower() == DEFAULT_AGENT_PACK.lower():
            return str(pack["id"])
    created = raise_for_status(api.post("/agent_packs", json={"name": DEFAULT_AGENT_PACK})).json()
    return str(created["id"])


class ExistingAgentError(RuntimeError):
    """Raised when a save would overwrite an agent the caller did not mean to touch."""


def _resolve_or_create_agent(api, name, explicit_id, allow_overwrite=False):
    """Decide which agent to write to, creating one if needed. Returns (agent_id, note).

    Tries in order: the explicit id, agents-list.tsv by name, the server by name, then create.
    A name match raises ExistingAgentError unless `explicit_id` or `allow_overwrite` says the
    caller means that agent — otherwise "create an agent called X" would overwrite an existing X.
    """
    if explicit_id:
        return _resolve_agent_ref(explicit_id), "(--agent-id)"

    match = agent_id_by_name(name)
    if match:
        catalog_id, state = match
        if not allow_overwrite:
            raise ExistingAgentError(catalog_id)
        return catalog_id, f"(matched {state} agent by name)"

    # Not in the local list, or it's stale: ask the server. Names are unique per workspace,
    # so a name filter is an exact lookup. This is what finds drafts when the file is stale.
    existing = _read_agent(api, name=name)
    if existing:
        if not allow_overwrite:
            raise ExistingAgentError(str(existing["id"]))
        return str(existing["id"]), "(matched draft agent by name)"

    pack_id = _ensure_agent_pack(api, _list_agent_packs(api))
    created = raise_for_status(api.post("/agents", json={"pack_id": pack_id})).json()
    return str(created["id"]), f"(created new agent in pack {pack_id})"


def _blocked_exists(name, existing_id, tool):
    """The [BLOCKED EXISTS] text for a name that belongs to another agent — nothing was written.
    Gets the name, that agent's id, and the tool to re-call; returns the message."""
    return (
        f"[BLOCKED EXISTS] an agent named {name!r} already exists in this workspace "
        f"(id {existing_id}).\n"
        f"Continuing would REPLACE its role, constraints and abilities — the existing agent may "
        f"be in use by workflows and chat sessions you cannot see from here.\n"
        f"Decide with the user, then either:\n"
        f"  - editing that agent on purpose? re-call {tool} with agent_id=\"{existing_id}\" "
        f"(and fetch_agent first if you have not read its current configuration), or\n"
        f"  - want a separate new agent? change `name:` in the YAML to something unused."
    )


def _write_agent(api, config, resolved_id):
    """Write a config to an agent: PATCH identity, then POST the draft. Returns the body sent.

    Two calls, because /draft ignores identity fields. Identity goes first so the server's name
    matches the file, and a retry after a failure resolves to this agent instead of a duplicate.
    """
    resp = api.patch(f"/agents/{resolved_id}", json=config.to_identity_body())
    if resp.status_code == 404:
        raise RuntimeError(f"agent {resolved_id!r} not found (404) — check the id/URL.")
    raise_for_status(resp)

    body = _refill_from_draft(api, resolved_id, config).to_body(resolved_id)
    try:
        raise_for_status(api.post(f"/agents/{resolved_id}/draft", json=body))
    except RuntimeError as exc:
        # Half-written: report exactly what landed, so the user is not told "nothing happened".
        raise RuntimeError(
            f"PARTIAL SAVE of agent {resolved_id}: name/title/description WERE updated, but the "
            f"draft (role, constraints, abilities, modes) was NOT — the agent still has its "
            f"previous behavior. Re-run save_agent to finish; it resolves to the same agent. "
            f"Server said: {exc}"
        ) from exc
    return body


def save_agent(path, agent_id="", allow_overwrite=False):
    """Save a local agent YAML as the agent's draft.

    Gets the file path, and optionally the agent id to write to. Returns a text summary with any
    warnings, or [BLOCKED EXISTS] if the name belongs to another agent. Validation errors raise.
    The draft is not callable from workflows until publish_agent runs.
    """
    config = _load_agent_config(path)
    errors, warnings = _validate_config(config)
    if errors:
        raise RuntimeError("validation failed — fix before saving:\n"
                           + "\n".join(f"[ERROR] {msg}" for msg in errors))

    api = build_client()
    name = config.name
    try:
        resolved_id, note = _resolve_or_create_agent(api, name, agent_id, allow_overwrite)
    except ExistingAgentError as exc:
        return _blocked_exists(name, exc.args[0], "save_agent")

    _write_agent(api, config, resolved_id)
    _sync_agents_list()

    lines = [f"[WARN] {msg}" for msg in warnings]
    lines.append(f"ok: saved agent draft {resolved_id} {note}")
    lines.append(f"api: {workspace_base_url()}/agents/{resolved_id}")
    lines.append(f"editor: {agent_editor_url(resolved_id)}")
    lines.append("note: the draft is not callable from workflows — publish_agent makes it live.")
    return "\n".join(lines)


def _blast_radius_findings(config):
    """List what lets this agent act without a human in the loop; empty means nothing to gate.

    Gets an AgentConfig. Flags each `auto_approved` ability, which runs with no approval step,
    and `code_execution_enabled`, which lets the agent write and run workflows nobody reviewed.
    """
    findings = [
        f"ability {ability.ability_id} is auto-approved — it runs with NO human approval, "
        "in every future session and workflow step."
        for ability in config.abilities if ability.auto_approved
    ]
    if config.code_execution_enabled:
        auto_approved = bool(findings)
        findings.append(
            "`code_execution_enabled` is on — the agent can author brand-new workflows at run "
            "time and run them, so its capability is NOT limited to the abilities listed here."
        )
        if auto_approved:
            findings.append(
                "These combine: the agent can build a new workflow AND run it without approval."
            )
    return findings


def publish_agent(ref="", path="", acknowledge_risks=False, allow_overwrite=False):
    """Publish an agent — it becomes callable as `agents.<id>`, and the new behavior goes live
    for every workflow and chat session already using it.

    Gets `path` (a local YAML, validated) or `ref` (an id or URL, publishing the server's draft).
    Returns a summary, or [BLOCKED SAFETY] if the agent could act unsupervised (acknowledge_risks).
    """
    if not path and not ref:
        raise RuntimeError("pass `ref` (agent id or agent-builder URL) or `path` (agent YAML).")

    api = build_client()
    warnings = []

    if path:
        config = _load_agent_config(path)
        errors, warnings = _validate_config(config)
        if errors:
            raise RuntimeError("validation failed — fix before publishing:\n"
                               + "\n".join(f"[ERROR] {msg}" for msg in errors))
        name = config.name
        try:
            agent_id, _ = _resolve_or_create_agent(api, name, ref, allow_overwrite)
        except ExistingAgentError as exc:
            return _blocked_exists(name, exc.args[0], "publish_agent")
        config = _refill_from_draft(api, agent_id, config)
    else:
        agent_id = _resolve_agent_ref(ref)
        row = _read_agent(api, agent_id=agent_id)
        if not row:
            raise RuntimeError(f"agent {agent_id!r} not found in this workspace.")
        # No local file: the agent's current draft, as the server holds it, is what goes live.
        config = AgentConfig.from_row(row)

    blocked = _blast_radius_findings(config)
    if blocked and not acknowledge_risks:
        lines = ["[BLOCKED SAFETY] publishing this agent lets it act without a human in the loop:"]
        lines += [f"  - {finding}" for finding in blocked]
        lines.append("Describe each point to the user in plain language and ask a direct yes/no. "
                     "Only after an explicit yes, re-call with acknowledge_risks=True. "
                     "Approval is per-publish and never carries over.")
        return "\n".join(lines)

    resp = api.post(f"/agents/{agent_id}/publish", json=config.to_body(agent_id))
    if resp.status_code == 404:
        raise RuntimeError(f"agent {agent_id!r} not found (404) — check the id.")
    raise_for_status(resp)
    _sync_agents_list()

    lines = [f"[WARN] {msg}" for msg in warnings]
    lines.append(f"ok: published agent {agent_id} — now callable from workflows as "
                 f"`agents.{agent_id}`")
    lines.append(f"editor: {agent_editor_url(agent_id)}")
    return "\n".join(lines)
