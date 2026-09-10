"""Installing the plugin's third-party dependencies.

Stdlib only — every caller runs this before those dependencies exist.
"""

import subprocess
import sys
from pathlib import Path

REQUIREMENTS = Path(__file__).resolve().parent.parent / "requirements.txt"


def ensure_installed():
    """Install anything missing from requirements.txt. Safe to call on every start.

    Cheap when already satisfied: pip resolves the bounds in requirements.txt against
    what's already installed and reaches PyPI only for a package that doesn't satisfy
    them. Writes nothing to stdout — for the MCP server that channel carries JSON-RPC,
    and for a hook it would land in the session transcript.
    """
    subprocess.check_call(
        [sys.executable, "-m", "pip", "install", "--user", "--break-system-packages",
         "--quiet", "-r", str(REQUIREMENTS)],
        stdout=subprocess.DEVNULL,
    )
