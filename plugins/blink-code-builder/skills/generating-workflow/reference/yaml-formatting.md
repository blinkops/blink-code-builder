# YAML formatting

These are plain YAML gotchas — nothing Blink-specific — but they're the most common source of
automations that pass a casual read, then fail either at the `validate_automation` tool (with a cryptic
PyYAML error) or, worse, silently produce wrong data that only surfaces in a test run. They bite
model-generated YAML often enough that Blink's own server-side generator has to repair them after
the fact. Getting these right up front is cheaper than debugging a failed test run.

## Rule: use a literal block scalar (`|`) for anything that isn't a simple bare value

For any multi-line string input (`body`, `data`, `headers`, `Question`, `Content`, `text`,
`Description`, …) write the value as a literal block scalar. Never start a multi-line value on the
same line as its key, and never fake one with an escaped `"…\n…"` string. Use a block scalar
whenever the value spans multiple lines, contains a colon followed by a space (`: `), starts with a
YAML-special character (`{`, `[`, `-`, `*`), or has lines starting with a dash. Concretely:

```yaml
# Good — block scalar, no escaping needed even though the text contains ": "
Content: |-
  Jira bug: {{ steps.S5.output.key }}
  Status: created

# Bad — breaks the parse (ScannerError: mapping values are not allowed here)
Content: Jira bug: {{ steps.S5.output.key }}

# Also bad — technically parses, but it's fragile and unreadable
Content: "Jira bug: {{ steps.S5.output.key }}\nStatus: created"
```

`|-` strips the trailing newline (usual choice for a single value fed to an action input); plain
`|` keeps it. Don't reach for escaped `\n`/`\"` strings as a substitute — they're exactly what the
rule above forbids, and they're the harder-to-read, easier-to-typo option.

## Rule: quote any value starting with `*` (cron expressions especially)

An unquoted leading `*` is parsed as a YAML alias reference, not a literal asterisk — which makes
a cron expression the single most common way to break an otherwise valid automation:

```yaml
# Bad — ScannerError: while scanning an alias ... expected alphabetic or numeric character
triggers:
  scheduled:
    - cron: */5 * * * *

# Good
triggers:
  scheduled:
    - cron: "*/5 * * * *"
```

## Rule: never repeat a key within the same mapping

YAML does **not** error on this — PyYAML's loader silently keeps the *last* occurrence and drops
the earlier one:

```yaml
inputs:
  Content: "first draft"
  Content: "final draft"   # "first draft" silently vanishes, no error anywhere
```

This is the most dangerous class of mistake here because nothing about it looks wrong: the file
parses, the `validate_automation` tool used to see nothing wrong either. It now runs a duplicate-key
scan on the raw YAML text (independent of the normal parse) and reports each repeated key with its
line number, before the file ever reaches a test run. If you see that error, don't just delete one
of the two entries — check whether you actually meant to combine them (e.g. two `Content:` lines
that should have been one multi-line block scalar) or genuinely lost content by overwriting a step.

## Rule: quote string values that collide with YAML's implicit types

PyYAML resolves several bare words and number-shaped strings to non-string types even when a
literal string was clearly intended:

```yaml
country: NO        # -> False (the "Norway problem")
flag: yes           # -> True
flag: off           # -> False
version: 1.20        # -> 1.2   (trailing zero silently dropped)
zip: 007             # -> 7     (leading zeros silently dropped)
```

Quote any value that could collide with a boolean word (`yes`/`no`/`true`/`false`/`on`/`off`, any
case) or that needs its exact digits/format preserved (version strings, zip/postal codes, IDs with
leading zeros): `country: "NO"`, `version: "1.20"`. The `validate_automation` tool now flags this when
the catalog says a parameter is a plain text field but the parsed YAML value came back as a bool
or number — that mismatch is the tell.

## Related, action-specific gotchas

The rules above are generic YAML. For Blink-specific shape gotchas (e.g. `internal.SetVariables`'
capitalized `Name`/`Type`/`Value` keys, `web_form_inputs` being a dict not a list, the singular
`connection:` key being silently dropped), see
[reference/handling-sparse-actions.md](handling-sparse-actions.md) instead — that file is about
action parameter *shapes*, this one is about YAML *syntax*.
