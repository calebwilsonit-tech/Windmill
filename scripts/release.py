#!/usr/bin/env python3
"""Windmill's release manager (runs automatically from a git hook in the Windmill source folder).

After every commit you're asked once: Major / Minor / Fix / Not yet. It then bumps VERSION, writes
CHANGELOG.md from the commit messages since the last release, tags vX.Y.Z and (if a publish remote is
set up) pushes it - which is what every installed Windmill picks up as an automatic update.

  python release.py --hook          (called by the post-commit hook)
  python release.py major|minor|fix (manual release of everything since the last one)"""
import datetime
import os
import subprocess
import sys
from pathlib import Path

KIT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(KIT))
from wm import version  # noqa: E402

SHIP_IGNORE = ("VERSION", "CHANGELOG.md")


def git(*a, check=False):
    r = subprocess.run(["git", *a], cwd=KIT, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if check and r.returncode:
        raise SystemExit(f"git {' '.join(a)} failed: {r.stderr}")
    return r.stdout.strip()


def last_tag():
    return git("describe", "--tags", "--abbrev=0", "--match", "v*.*.*") or None


def pending_commits():
    t = last_tag()
    rng = f"{t}..HEAD" if t else "HEAD"
    subjects = git("log", "--format=%s", rng).splitlines()
    return [s for s in subjects if s and not s.startswith(("release v", "windmill release"))]


def ask_level(cur, commits):
    print(f"\n  \033[1;36mWindmill change committed.\033[0m  Current version {cur}. Changes since last release:")
    for c in commits[:12]:
        print(f"    - {c}")
    if len(commits) > 12:
        print(f"    ... and {len(commits) - 12} more")
    opts = [("major", version.bump(cur, "major")), ("minor", version.bump(cur, "minor")), ("fix", version.bump(cur, "fix"))]
    for i, (lvl, v) in enumerate(opts, 1):
        print(f"    \033[1;36m{i}\033[0m  {lvl.capitalize():6} -> {v}   {version.LEVELS[lvl]}")
    print("    \033[1;36m4\033[0m  Not yet  (keep working; release later)")
    try:
        ans = input("  Release as? [1/2/3/4] (default 3): ").strip().lower()
    except EOFError:
        return None
    return {"1": "major", "2": "minor", "3": "fix", "": "fix", "4": None, "major": "major", "minor": "minor",
            "fix": "fix", "n": None, "no": None}.get(ans, "fix")


def release(level, commits, note=""):
    cur = version.current()
    new = version.bump(cur, level)
    (KIT / "VERSION").write_text(new + "\n", encoding="utf-8")
    cl = KIT / "CHANGELOG.md"
    old = cl.read_text(encoding="utf-8") if cl.exists() else "# Changelog\n"
    head, _, rest = old.partition("\n")
    entry = (f"\n## {new} - {datetime.date.today().isoformat()} ({level})\n"
             + "".join(f"- {c}\n" for c in commits) + (f"- {note}\n" if note else ""))
    cl.write_text(head + "\n" + entry + rest, encoding="utf-8")
    env = {**os.environ, "WINDMILL_RELEASING": "1"}
    subprocess.run(["git", "add", "VERSION", "CHANGELOG.md"], cwd=KIT, env=env)
    subprocess.run(["git", "commit", "-q", "--amend", "--no-edit", "--no-verify"], cwd=KIT, env=env)
    msg = f"{level}: " + "; ".join(commits)[:900]
    subprocess.run(["git", "tag", "-a", f"v{new}", "-m", msg], cwd=KIT, env=env)
    print(f"  \033[32m✓ Released Windmill {new} ({level}).\033[0m")
    if git("remote", "get-url", "origin"):
        print("  Publishing (installed copies will update themselves)...")
        r = subprocess.run(["git", "push", "-q", "origin", "HEAD", f"v{new}"], cwd=KIT, env=env,
                           capture_output=True, text=True)
        print("  \033[32m✓ Published.\033[0m" if r.returncode == 0 else
              f"  ! Publish failed (will retry on the next release): {r.stderr.strip()[:200]}")
    return new


def main():
    if os.environ.get("WINDMILL_RELEASING"):
        return
    args = sys.argv[1:]
    if "--hook" in args:
        changed = git("show", "--name-only", "--format=", "HEAD").split()
        if changed and all(c in SHIP_IGNORE for c in changed):
            return
        commits = pending_commits()
        if not commits:
            return
        level = os.environ.get("WINDMILL_BUMP")
        note = ""
        if not level:
            if sys.stdin.isatty():
                level = ask_level(version.current(), commits)
            else:
                level, note = "fix", "(released as a fix automatically: no terminal was available to ask)"
        if level in ("major", "minor", "fix"):
            release(level, commits, note)
        else:
            print("  Not released yet - these changes will be included in the next release.")
        return
    level = args[0] if args and args[0] in ("major", "minor", "fix") else None
    commits = pending_commits()
    if not commits:
        print("Nothing new since the last release.")
        return
    level = level or ask_level(version.current(), commits)
    if level:
        release(level, commits)


if __name__ == "__main__":
    main()
