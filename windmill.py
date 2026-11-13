#!/usr/bin/env python3
"""Windmill - local AI that builds things. Run `windmill` for the menu, `windmill help` for commands."""
import sys
from pathlib import Path

if sys.version_info < (3, 9):
    sys.exit("Windmill needs Python 3.9 or newer. Run the installer again or install Python from python.org.")
sys.path.insert(0, str(Path(__file__).resolve().parent))

import os  # noqa: E402
import subprocess  # noqa: E402

# Fresh PCs often have no git identity, which makes every checkpoint commit fail silently.
# Only fill it in when the user hasn't set one.
try:
    if not subprocess.run(["git", "config", "user.name"], capture_output=True, text=True).stdout.strip():
        for k in ("GIT_AUTHOR_NAME", "GIT_COMMITTER_NAME"):
            os.environ.setdefault(k, "Windmill")
        for k in ("GIT_AUTHOR_EMAIL", "GIT_COMMITTER_EMAIL"):
            os.environ.setdefault(k, "windmill@localhost")
except OSError:
    pass

from wm.cli import main  # noqa: E402

if __name__ == "__main__":
    main()
