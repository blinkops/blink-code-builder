# Blast radius and test-run safety

**A test run is not a dry run.** It executes every step for real: messages actually send,
tickets actually open, deletes actually delete. The controller has no sandbox mode. So the
moment of highest risk in this skill is the `trigger_test_run` tool — one keystroke away from, say,
a Slack message to everyone in the org (a real incident that motivated this file).

Two layers keep that in check: draft small by default, and confirm explicitly before running
anything the tool flags.

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
- **Production-looking targets** — connection or resource names suggesting prod. The scan
  can't reliably detect this; use judgment and when in doubt, ask.

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
build the wide version and just make sure the test-run confirmation (below) names the scope.

## The gate: `[BLOCKED SAFETY]`

The `trigger_test_run` tool scans the draft before opening the run. Any match on the patterns
above (destructive action names, messaging inside `internal.for`, broadcast mentions) →
it returns a `[BLOCKED SAFETY]` block listing the flagged steps **without running**. When that happens:

1. **Translate the findings for the user** — plain language, concrete scope. Not
   "S6 matched MESSAGING_ACTION_PATTERN" but "step S6 sends a Slack message once per row of
   the S4 query — that could be one message or five hundred, and I can't tell from the YAML.
   The channel is #ai-quality."
2. **Ask a direct yes/no question.** If there's a cheap way to shrink the radius first
   (add a `LIMIT`, retarget to a test channel for this run), offer it as the alternative.
3. **Only after an explicit yes**, re-call with `acknowledge_risks: true`. The approval is per-run —
   a yes for this test run is not standing approval for future runs or for publishing.
4. **If the user says no or wants changes**, patch the YAML (→ Revising flow), re-save,
   re-run the gate fresh.

Never pass `acknowledge_risks: true` preemptively, "to save a round trip," or because a previous
run was approved. The whole point is that a human sees the blast radius before it happens.

## Human-wait steps: `[BLOCKED HUMAN_WAIT]`

Separate from blast radius: a step that waits for a *person* (`internal.Sleep` with
`Mode: Web Form Response`, or `wait_for_response: true`) can't be exercised by an automatic
test run at all. The two observed failure modes are opposites — the Sleep mode parks the run
until a human answers or an hours-scale timeout expires, while `wait_for_response: true` has
been observed to *not* wait in test runs, continuing instantly with an empty response. Either
way the automatic test is meaningless, so the tool refuses (`[BLOCKED HUMAN_WAIT]`) and there
is **no override flag**. The resolution is a hand-off, not an acknowledgment:

1. Tell the user why the automatic run is skipped (one sentence, name the step).
2. Give them the full editor link the tool returns —
   and ask them to click **Test Run** and answer the form themselves.
3. When they confirm it finished, fetch results with the `get_run_log` tool and report as usual.

Don't offer to rewrite the workflow to get past this gate — the wait may be exactly what the
user asked for. Design alternatives (trigger-based split) belong at drafting time, as a
question (see "Worth asking about" in the SKILL), never as a silent change.

## Publishing: the highest-stakes moment

A test run executes once, supervised. **Publishing changes what every future scheduled and
triggered run executes** — the risk repeats on every trigger, unattended. The `publish_automation` tool
therefore gates harder than the test tool:

- **`[BLOCKED UNTESTED]`.** The current draft must be byte-identical to a draft that
  completed a successful test run (evidence recorded by the `trigger_test_run` tool, or by
  the `get_run_log` tool for runs the user triggered manually). Two ways to hit this gate: the draft
  was never tested, or it was **edited after** the test — in which case what passed the test is
  not what would go live. The fix is always "test it first." The `allow_untested: true` override is
  for the user who, after hearing that warning, explicitly insists again — a single "just
  publish it" is not enough, and Claude never passes the override on its own.
- **`[BLOCKED SAFETY]`.** The same blast-radius scan as the test gate, re-run at
  publish time, and **prior approvals don't carry over** — a yes for the test run covered one
  supervised execution, not a standing schedule. Present the flagged steps again, remind the
  user they'll fire on every future trigger, and re-call with `acknowledge_risks: true` only after a
  fresh explicit yes.
- **Before asking for the yes**, summarize the automation in plain language: when it runs (the
  trigger), what it does, who/what it touches. The user should be able to approve the publish
  from the summary alone, without re-reading the YAML.

## False positives

The scan is heuristic (name patterns), so it will sometimes flag benign steps — e.g. an
action named `RemoveLabel` on a single ticket. That's fine and by design: the cost of a
false positive is one quick question; the cost of a false negative is a Slack blast to the
org. Don't try to pre-empt or argue the gate away — relay the question, let the user answer.
If a specific action is flagged run after run in a repo and the user is tired of confirming
it, that's feedback for the plugin maintainer (a per-repo acknowledgment list could be added,
like the connections allowlist) — not a reason to auto-pass the override.
