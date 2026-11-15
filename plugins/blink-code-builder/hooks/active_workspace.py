#!/usr/bin/env python3
"""The Blink workspace of each session.

- `check` (PreToolUse on every Blink tool): denies a call whose `workspace_id` is not the
  session's workspace, so no prompt or tool output can point a call at another workspace. It
  never changes the input, because auto mode reviews the input Claude wrote. It gives no allow:
  the user's permission mode still decides.
- `list` (PostToolUse on `list_workspaces`): saves the user's workspaces as
  workspace/workspaces.tsv (id, name, role, description). After `choose=true`, the result names the
  row the user chose (`chosen_id`): it becomes the session's workspace, and Claude must
  refresh the workspace lists.

- `answer` (PostToolUse on AskUserQuestion): when the picker did not open, Claude asks with the
  question box, and the user's answer becomes the session's workspace.

So the user changes the workspace only in the picker or the question box, never the model.
The state is ${CLAUDE_PLUGIN_DATA}/active-workspace/<session_id>.json.
"""

import json
import sys

from _common import (CHOOSE, UUID, WORKSPACES_FILE, active_workspace, mark_refresh_needed, save_workspace_rows,
                     set_active_workspace, tool_result, workspace_folder, workspace_label, workspace_rows)


def check(payload):
    tool_input = payload.get("tool_input") or {}
    if "workspace_id" not in tool_input:
        return
    workspace = active_workspace(payload.get("session_id"))
    if workspace is None:
        reason = f"No Blink workspace is chosen for this session. To choose one, {CHOOSE}, then call this tool again."
    elif tool_input["workspace_id"] != workspace["id"]:
        reason = (f"This session's Blink workspace is {workspace_label(workspace)}. Call this tool again with "
                  f'workspace_id="{workspace["id"]}". Only the user can change the workspace: {CHOOSE}.')
    else:
        return
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": reason}}))


QUESTION_HEADER = "Workspace"
ASK = ("No workspace was chosen: the picker did not open, or the user closed it. Ask the user with the AskUserQuestion tool: one question with the header "
       f'"{QUESTION_HEADER}", and one option for each workspace in {WORKSPACES_FILE} (at most 4), with the label '
       '"<name> · <role> · <first 8 characters of the id>". With more than 4 workspaces, tell the user to type the '
       "name in Other. A hook saves the user's answer as the workspace.")


def choose(payload, workspace, event):
    """Saves the workspace the user chose as the session's workspace, and tells Claude to refresh."""
    session_id = payload.get("session_id")
    if not set_active_workspace(session_id, workspace):
        return False
    mark_refresh_needed(session_id)
    label = workspace_label(workspace)
    status = (f"ok: the user chose the workspace {label}. Pass workspace_id=\"{workspace['id']}\" to every Blink "
              f"tool that takes one. Its files are in {workspace_folder(workspace['id'])}/. The list of the user's "
              f"workspaces is in {WORKSPACES_FILE}. Now call `get_workspace_resources`.")
    output = {"updatedToolOutput": status} if event == "list" else {"additionalContext": status}
    print(json.dumps({"systemMessage": f"Blink workspace: {label}",
                      "hookSpecificOutput": {"hookEventName": "PostToolUse", **output}}))
    return True


def listed(payload):
    """PostToolUse on `list_workspaces`: keeps workspace/workspaces.tsv, and saves the user's choice.
    When the picker did not open, Claude asks with AskUserQuestion, and `answered` saves the answer."""
    try:
        result = tool_result(payload.get("tool_response"))
        rows = [[w["id"], w["name"], w["role"], w.get("description", "")] for w in result["workspaces"]]
    except (ValueError, KeyError, TypeError):
        return  # an error: Claude sees the tool's own output
    if not rows or not all(UUID.fullmatch(str(row[0])) for row in rows):
        return
    save_workspace_rows(rows)
    chosen = result.get("chosen_id")
    workspace = next((dict(zip(("id", "name", "role"), row)) for row in rows if row[0] == chosen), None)
    if workspace and choose(payload, workspace, "list"):
        return
    status = f"ok: saved the {len(rows)} workspaces of the user to {WORKSPACES_FILE}. The workspace did not change."
    if (payload.get("tool_input") or {}).get("choose"):
        status += " " + ASK
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse", "updatedToolOutput": status}}))


def answered(payload):
    """PostToolUse on AskUserQuestion: if the user answered the "Workspace" question with one row of
    workspaces.tsv (by the short id in the label, or by the exact name typed in Other), that row
    becomes the session's workspace. The user's click decides, not the model."""
    tool_input = payload.get("tool_input") or {}
    if not any(q.get("header") == QUESTION_HEADER for q in tool_input.get("questions") or []):
        return
    # The answers field holds only the user's answers; the response text may also repeat the options.
    answer = json.dumps(tool_input.get("answers") or payload.get("tool_response"), ensure_ascii=False).lower()
    rows = workspace_rows()
    matches = [row for row in rows if row[0][:8].lower() in answer]
    if not matches:
        matches = [row for row in rows if row[1] and json.dumps(row[1].lower(), ensure_ascii=False) in answer]
    if len(matches) == 1:
        choose(payload, dict(zip(("id", "name", "role"), matches[0])), "answer")


if __name__ == "__main__":
    {"check": check, "list": listed, "answer": answered}[sys.argv[1]](json.load(sys.stdin))
