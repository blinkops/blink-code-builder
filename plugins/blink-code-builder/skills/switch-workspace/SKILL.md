---
name: switch-workspace
description: Changes the Blink workspace of this session. Shows the user a picker of their workspaces and refreshes the workspace lists. Use when the user runs /switch-workspace, or asks to work in, switch to, or copy something to another Blink workspace.
allowed-tools: mcp__blink-code-builder__list_workspaces, mcp__blink-code-builder__get_workspace_resources
---

# Switch the Blink workspace

1. If there are changes that are not saved to Blink yet, tell the user which ones. They stay in
   the old workspace folder.
2. Call `list_workspaces` with `choose=true`. Claude Code shows the user a picker. A hook saves the
   user's choice as the workspace of this session, and saves the list of workspaces as
   `workspace/workspaces.tsv`.
3. When the result says "ok: the user chose the workspace ...", call `get_workspace_resources`
   with the workspace id that the result gives.
4. Tell the user in one line: "Now in <name>." From now on, every Blink tool call goes to this
   workspace.

If the result says "No workspace was chosen", ask the user with AskUserQuestion as the result
says. A hook saves the user's answer as the workspace. If the user does not answer, the workspace
did not change: tell the user.
