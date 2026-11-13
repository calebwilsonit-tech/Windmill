"""Windmill's version number (semantic versioning: MAJOR.MINOR.FIX)."""
import re

from . import config

VERSION_FILE = config.KIT / "VERSION"
LEVELS = {"major": "big or breaking change", "minor": "new features / additions", "fix": "bug fixes"}


def current():
    try:
        return VERSION_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return "0.0.0"


def parse(v):
    m = re.match(r"v?(\d+)\.(\d+)\.(\d+)", (v or "").strip())
    return tuple(int(x) for x in m.groups()) if m else (0, 0, 0)


def bump(v, level):
    a, b, c = parse(v)
    return {"major": f"{a + 1}.0.0", "minor": f"{a}.{b + 1}.0", "fix": f"{a}.{b}.{c + 1}"}[level]


def level_between(old, new):
    o, n = parse(old), parse(new)
    if n[0] != o[0]:
        return "major"
    return "minor" if n[1] != o[1] else "fix"


def newest(tags):
    vs = [t for t in tags if re.fullmatch(r"v\d+\.\d+\.\d+", t)]
    return max(vs, key=parse) if vs else None
