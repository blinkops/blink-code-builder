# Blast radius and test-run safety

**A test run is not a dry run.** It executes every step for real: messages actually send,
tickets actually open, deletes actually delete. The controller has no sandbox mode. So the
moment of highest risk in this skill is the `trigger_test_run` tool — one keystroke away from, say,
a Slack message to everyone in the org (a real incident that motivated this file).

Two layers keep that in check: draft small by default, and make sure the user knows the risks
before anything runs.

## What counts as high blast radius

Think "how many people/objects does this touch, and can it be undone?":

- **Broad messaging** — Slack/Teams/email to a large or unknown audience: big channels,
  `@here`/`@channel`/`@everyone` mentions, or any messaging step inside an `internal.for`
  loop (one message per item, and the item count is a runtime unknown).
- **Destructive or irreversible operations** — action names containing delete / remove /
  terminate / revoke / destroy / purge / drop / suspend / deactivate / erase / wipe / disable.
  Deleting records, terminating instances, revoking access, disabling users.
- **Writes at scale** — any write action inside a loop fed by an unbounded query result
  (create a ticket per row, update every matching record).
- **Identity and access changes** — password resets, permission changes, key rotation.
- **Production-looking targets** — connection or resource names suggesting prod. The plugin's
  scan can't detect this; use judgment and when in doubt, ask.

## Draft small by default

Cheapest mitigation is choosing low-blast-radius values at drafting time — no confirmation
needed for risk that was never created:

- **Recipients:** default to the requesting user or a test channel, not a team channel —
  unless the user named the audience explicitly. Never target "all users"/org-wide anything
  the user didn't ask for by name.
- **Loops:** when a loop is fed by a query, put a `LIMIT` on the query for the first test
  run and say so at handoff ("remove the LIMIT once you've seen one good run").
- **Mentions:** don't add `@here`/`@channel` on your own initiative.
- **Destructive steps:** if the task needs one, prefer shapes where the test can be aimed at
  a disposable target (a test record, a sandbox resource), and say what the step will touch
  at handoff.

These are drafting defaults, not bans — if the user explicitly asked for the wide version,
build the wide version and just make sure what you tell the user before the run (below) names the scope.

## The gate: Claude Code's approval prompt

`trigger_test_run` and `publish_automation` never run on their own: a plugin hook makes Claude
Code ask the user first, and the prompt lists the risks it found in the YAML at save time
(destructive action names, messaging inside `internal.for`, broadcast mentions, human-wait
steps). The prompt is the user's gate — your job is to make sure they understand it before it
appears:

1. **Tell the user what will happen, in plain language, before you call the tool** — concrete
   scope, not pattern names. Not "S6 matched a messaging pattern" but "step S6 sends a Slack
   message once per row of the S4 query — that could be one message or five hundred, and I
   can't tell from the YAML. The channel is #ai-quality." Do this even when the scan found
   nothing — it can miss things (prod targets, writes at scale).
2. **If there's a cheap way to shrink the radius first** (add a `LIMIT`, retarget to a test
   channel for this run), offer it before running.
3. **Then call the tool.** Claude Code shows the approval prompt; the user answers it.
4. **If the user declines the prompt, don't retry.** Ask what they want instead — usually a
   patched YAML (→ Revising flow), re-saved, then a fresh run.

Never call either tool on your own initiative or "to save a round trip." Each approval covers
one call — a yes for this test run is not standing approval for future runs or for publishing.

## Human-wait steps

Separate from blast radius: a step that waits for a *person* (`internal.Sleep` with
`Mode: Web Form Response`, or `wait_for_response: true`) can't be exercised by an automatic
test run at all. The two observed failure modes are opposites — the Sleep mode parks the run
until a human answers or an hours-scale timeout expires, while `wait_for_response: true` has
been observed to *not* wait in test runs, continuing instantly with an empty response. Either
way the automatic test is meaningless, so **don't call `trigger_test_run`** for such a draft.
The resolution is a hand-off:

1. Tell the user why the automatic run is skipped (one sentence, name the step).
2. Ask them to open the workflow in the Blink editor, click **Test Run**, and answer the form
   themselves.
3. When they confirm it finished, ask for the execution id of that run, fetch results with the
   `get_run_log` tool (`execution_id`), and report as usual.

Don't offer to rewrite the workflow to get past this — the wait may be exactly what the
user asked for. Design alternatives (trigger-based split) belong at drafting time, as a
question (see "Worth asking about" in the SKILL), never as a silent change.

## Publishing: the highest-stakes moment

A test run executes once, supervised. **Publishing changes what every future scheduled and
triggered run executes** — the risk repeats on every trigger, unattended. So:

- **Only when the user asked.** Publish only when the user asked for it in this conversation.
- **Only after a passed test of the current draft.** The saved draft must have completed a test
  run (`state` = `Completed`) and not been edited since — otherwise what passed is not what
  would go live. The fix is always "test it first." Publish untested only if the user, after
  hearing that warning, explicitly accepts it. The approval prompt also says whether the saved
  YAML passed a test (it only knows about runs started with `trigger_test_run`).
- **Restate the risks, fresh.** Prior approvals don't carry over — a yes for the test run
  covered one supervised execution, not a standing schedule. Describe the flagged steps again
  and remind the user they'll fire on every future trigger.
- **Before calling the tool**, summarize the automation in plain language: when it runs (the
  trigger), what it does, who/what it touches. The user should be able to approve the publish
  from the summary alone, without re-reading the YAML.

## False positives

The scan is heuristic (name patterns), so it will sometimes flag benign steps — e.g. an
action named `RemoveLabel` on a single ticket. That's fine and by design: the cost of a
false positive is one quick question; the cost of a false negative is a Slack blast to the
org. Don't try to pre-empt or argue the gate away — explain the risk, let the user answer the
prompt. If a specific action is flagged run after run in a repo and the user is tired of
confirming it, that's feedback for the plugin maintainer.
