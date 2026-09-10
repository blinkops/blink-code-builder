"""Helpers shared by the SessionStart hooks (refresh_catalog, refresh_workspace).

Not a hook itself — the leading underscore keeps it out of hooks.json. Both hooks run as
scripts from this directory, so `import _common` resolves via sys.path[0].

Importing this also puts the plugin root on sys.path, which is what makes `blink_shared`
reachable from a hook. Doing it here means a hook's own imports stay plain imports at the
top of the file.
"""

import sys
from pathlib import Path

_PLUGIN_ROOT = str(Path(__file__).resolve().parent.parent)
if _PLUGIN_ROOT not in sys.path:
    sys.path.insert(0, _PLUGIN_ROOT)


def clean_for_tsv(text, limit=200):
    """Collapse whitespace and truncate, so the value is safe as a TSV cell.
    Embedded tabs would split the row into extra columns; newlines would create fake rows.
    """
    return " ".join((text or "").split())[:limit]
