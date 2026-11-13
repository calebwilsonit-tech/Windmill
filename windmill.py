#!/usr/bin/env python3
"""Windmill - local AI that builds things. Run `windmill` for the menu, `windmill help` for commands."""
import sys
from pathlib import Path

if sys.version_info < (3, 9):
    sys.exit("Windmill needs Python 3.9 or newer. Run the installer again or install Python from python.org.")
sys.path.insert(0, str(Path(__file__).resolve().parent))

import os  # noqa: E402
import subprocess  # noqa: E402

# Started without a console (pythonw, i.e. the desktop app): there is no stdout/stderr, and the first print
# would crash. Send everything to a log instead, and keep the programs we start from flashing console windows.
if sys.stdout is None or sys.stderr is None:
    _logdir = Path.home() / ".windmill" / "logs"
    _logdir.mkdir(parents=True, exist_ok=True)
    _log = open(_logdir / "app.log", "a", encoding="utf-8", buffering=1)  # noqa: SIM115
    sys.stdout = sys.stdout or _log
    sys.stderr = sys.stderr or _log
    if os.name == "nt":
        _popen_init = subprocess.Popen.__init__

        def _hidden(self, *a, **kw):
            flags = kw.get("creationflags", 0) & ~0x00000008  # DETACHED_PROCESS cancels "no window"
            if not flags & 0x00000010:  # unless a visible console was asked for on purpose
                flags |= 0x08000000  # CREATE_NO_WINDOW
            kw["creationflags"] = flags
            _popen_init(self, *a, **kw)
        subprocess.Popen.__init__ = _hidden

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
