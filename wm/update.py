"""Automatic updates for installed copies of Windmill, plus the developer setup that publishes them.

Installed side: once a day (setting `update_check_hours`) Windmill looks for a newer release tag.
  auto_update = "auto" (default): fixes and minor updates install by themselves, major ones ask first
              = "ask": always ask      = "off": never check
Every update is reversible (`windmill update --rollback`). New library sources that an update adds are
downloaded in the background automatically."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from . import config, ui, version

KIT = config.KIT
HISTORY = config.HOME / "update-history.json"
PRESERVE = {"models", "llama.cpp", "corpus", "android", ".git", "__pycache__"}
SOURCE_FILE = KIT / "UPDATE_SOURCE"
GITIGNORE = "models/*\n!models/.keep\nllama.cpp/\ncorpus/\nandroid/\n__pycache__/\n*.pyc\n"


def git(*a, cwd=KIT, timeout=60):
    try:
        r = subprocess.run(["git", *a], cwd=cwd, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout, env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
        return r.returncode, r.stdout.strip(), r.stderr.strip()
    except (OSError, subprocess.TimeoutExpired) as e:
        return 1, "", str(e)


def is_dev():
    """The Windmill source folder where releases are made never auto-updates itself."""
    return (KIT / ".git" / "windmill-dev").exists()


def source(cfg=None):
    cfg = cfg or config.load()
    if (KIT / ".git").exists():
        rc, url, _ = git("remote", "get-url", "origin")
        if rc == 0 and url:
            return url, "git"
    s = cfg.get("update_source") or (SOURCE_FILE.read_text(encoding="utf-8").strip() if SOURCE_FILE.exists() else "")
    return (s, "copy") if s else (None, None)


def latest(src, timeout=15):
    rc, out, _ = git("ls-remote", "--tags", "--refs", src, timeout=timeout)
    if rc:
        return None
    return version.newest([l.split("refs/tags/")[-1] for l in out.splitlines()])


def check(cfg=None, timeout=15):
    """Returns (newer_tag or None, level, src)."""
    cfg = cfg or config.load()
    src, _ = source(cfg)
    if not src:
        return None, None, None
    tag = latest(src, timeout)
    config.set_value("last_update_check", time.time())
    cur = version.current()
    if tag and version.parse(tag) > version.parse(cur):
        return tag, version.level_between(cur, tag), src
    return None, None, src


def notes(src, tag, since=None):
    """Changelog text newer than `since` (default: the installed version)."""
    cur = version.parse(since or version.current())
    text = ""
    if (KIT / ".git").exists() and tag:
        git("fetch", "-q", "--tags", "origin")
        _, text, _ = git("show", f"{tag}:CHANGELOG.md")
    if not text and (KIT / "CHANGELOG.md").exists():
        text = (KIT / "CHANGELOG.md").read_text(encoding="utf-8")
    out, keep = [], False
    for line in text.splitlines():
        if line.startswith("## "):
            keep = version.parse(line[3:]) > cur
        if keep:
            out.append(line)
    return "\n".join(out[:40])


def _record(old, new, how):
    hist = json.loads(HISTORY.read_text(encoding="utf-8")) if HISTORY.exists() else []
    hist.append({"from": old, "to": new, "how": how, "at": time.strftime("%Y-%m-%d %H:%M")})
    HISTORY.parent.mkdir(parents=True, exist_ok=True)
    HISTORY.write_text(json.dumps(hist[-50:], indent=1), encoding="utf-8")


def _manifest_names():
    try:
        return {r["name"] for r in json.loads((KIT / "corpus_manifest.json").read_text(encoding="utf-8"))["repos"]}
    except (OSError, ValueError, KeyError):
        return set()


def apply(tag, src, quiet=False):
    """Install release `tag`. Returns True on success."""
    old = version.current()
    before = _manifest_names()
    if (KIT / ".git").exists():
        # bin/ holds things Windmill downloads or builds for this PC (the app, the phone app, helpers). Version 1.11.0
        # accidentally tracked two of them in git; reset those so they never block an update.
        git("checkout", "-q", "--", "bin")
        rc, dirty, _ = git("status", "--porcelain", "--untracked-files=no")
        dirty = "\n".join(l for l in dirty.splitlines() if not l[3:].startswith("bin/"))
        if dirty:
            ui.warn("Windmill's own files were edited locally - not updating automatically (run windmill update to see).")
            return False
        git("fetch", "-q", "--tags", "--force", "origin")
        rc, _, err = git("-c", "advice.detachedHead=false", "checkout", "-q", tag)
        if rc:
            ui.err(f"Update failed: {err[:200]}")
            return False
        how = "git"
    else:
        tmp = Path(tempfile.mkdtemp(prefix="windmill-update-"))
        rc, _, err = git("clone", "-q", "--depth", "1", "--branch", tag, src, str(tmp / "new"), timeout=600)
        if rc:
            ui.err(f"Update download failed: {err[:200]}")
            return False
        backup = config.HOME / "backups" / f"windmill-{old}"
        shutil.rmtree(backup, ignore_errors=True)
        backup.mkdir(parents=True)
        for item in KIT.iterdir():
            if item.name not in PRESERVE:
                (shutil.copytree if item.is_dir() else shutil.copy2)(item, backup / item.name)
        for item in list(KIT.iterdir()):
            if item.name not in PRESERVE:
                shutil.rmtree(item) if item.is_dir() else item.unlink()
        for item in (tmp / "new").iterdir():
            if item.name not in PRESERVE:
                (shutil.copytree if item.is_dir() else shutil.copy2)(item, KIT / item.name)
        shutil.rmtree(tmp, ignore_errors=True)
        how = "copy"
    _record(old, version.current(), how)
    try:
        from . import gui
        gui.after_update()  # rebuilds Windmill.exe only if the app is installed and its source changed
    except Exception:  # noqa: BLE001
        pass
    added = _manifest_names() - before
    if added and (KIT / "corpus" / "corpus.db").exists():
        library_background(["fetch", "--only", ",".join(sorted(added))], then_index=True)
        if not quiet:
            ui.info(f"Downloading {len(added)} new library source(s) in the background.")
    return True


def rollback():
    hist = json.loads(HISTORY.read_text(encoding="utf-8")) if HISTORY.exists() else []
    if not hist:
        ui.warn("No update to roll back.")
        return False
    last = hist[-1]
    if last["how"] == "git":
        rc, _, err = git("-c", "advice.detachedHead=false", "checkout", "-q", f"v{last['from']}")
        ok = rc == 0
    else:
        backup = config.HOME / "backups" / f"windmill-{last['from']}"
        ok = backup.is_dir()
        if ok:
            for item in list(KIT.iterdir()):
                if item.name not in PRESERVE:
                    shutil.rmtree(item) if item.is_dir() else item.unlink()
            for item in backup.iterdir():
                (shutil.copytree if item.is_dir() else shutil.copy2)(item, KIT / item.name)
    if ok:
        HISTORY.write_text(json.dumps(hist[:-1], indent=1), encoding="utf-8")
        config.set_value("skip_update", f"v{last['to']}")  # don't auto-reinstall what you just rolled back
        ui.ok(f"Rolled back to Windmill {version.current()}.")
    else:
        ui.err("Rollback failed - the previous version isn't available.")
    return ok


def library_background(args, then_index=True):
    """Run a library job detached (doesn't slow down what you're doing)."""
    config.LOGS.mkdir(parents=True, exist_ok=True)
    py, corpus = sys.executable, str(config.SCRIPTS / "ai_corpus.py")
    cmd = f'"{py}" "{corpus}" ' + " ".join(f'"{a}"' for a in args)
    if then_index:
        cmd += f' && "{py}" "{corpus}" index --quiet'
    log = open(config.LOGS / "library-update.log", "a", encoding="utf-8")
    # hidden console the job's children share - no flashing windows
    kw = {"creationflags": 0x00000200 | 0x08000000} if os.name == "nt" else {"start_new_session": True}
    subprocess.Popen(cmd, shell=True, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, **kw)


def maybe_auto_update(argv):
    """Called on every `windmill` start: quick, rate-limited, never blocks for long."""
    cfg = config.load()
    mode = cfg.get("auto_update", "auto")
    if mode == "off" or is_dev() or os.environ.get("WINDMILL_NO_UPDATE"):
        return
    if time.time() - float(cfg.get("last_update_check") or 0) < float(cfg.get("update_check_hours", 24)) * 3600:
        return
    result = {}
    t = threading.Thread(target=lambda: result.update(zip(("tag", "level", "src"), check(cfg, timeout=8))), daemon=True)
    t.start()
    t.join(10)
    tag, level, src = result.get("tag"), result.get("level"), result.get("src")
    if tag and tag == cfg.get("skip_update"):
        tag = None  # rolled back on purpose; only a newer release auto-installs (windmill update still offers it)
    if not tag:
        library_auto_discover(cfg)
        return
    cur = version.current()
    info = notes(src, tag)
    if mode == "auto" and level != "major":
        ui.info(f"Updating Windmill {cur} -> {tag[1:]} ({level})...")
    else:
        ui.rule(f"Windmill {tag[1:]} is available ({level} update, you have {cur})")
        if info:
            print(ui.dim(info))
        if not sys.stdin.isatty() or not ui.confirm("Update now?", True):
            return
    if apply(tag, src):
        ui.ok(f"Windmill updated to {version.current()}.  (undo: windmill update --rollback)")
        info = info or notes(None, None, since=cur)
        if info and mode == "auto" and level != "major":
            print(ui.dim("\n".join(info.splitlines()[:8])))
        os.execv(sys.executable, [sys.executable, str(KIT / "windmill.py"), *argv])  # restart on the new version


def library_auto_discover(cfg):
    """Keep the library growing by itself: re-discover + download new public projects every N days."""
    days = float(cfg.get("library_refresh_days", 30) or 0)
    if not days or not (KIT / "corpus" / "corpus.db").exists():
        return
    if time.time() - float(cfg.get("last_library_refresh") or 0) < days * 86400:
        return
    config.set_value("last_library_refresh", time.time())
    py, corpus = sys.executable, str(config.SCRIPTS / "ai_corpus.py")
    config.LOGS.mkdir(parents=True, exist_ok=True)
    cmd = (f'"{py}" "{corpus}" fetch --update && "{py}" "{corpus}" discover bevy && '
           f'"{py}" "{corpus}" fetch --discovered bevy && "{py}" "{corpus}" index --quiet')
    log = open(config.LOGS / "library-update.log", "a", encoding="utf-8")
    # hidden console the job's children share - no flashing windows
    kw = {"creationflags": 0x00000200 | 0x08000000} if os.name == "nt" else {"start_new_session": True}
    subprocess.Popen(cmd, shell=True, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, **kw)


# ------------------------------------------------------------------ commands
def cmd_update(args):
    if "--rollback" in args:
        return rollback()
    src, mode = source()
    if not src:
        ui.warn("Updates aren't set up for this copy. If Windmill is published somewhere:\n"
                "  windmill update --source <git-url>")
        if "--source" in args:
            pass
        else:
            return
    if "--source" in args:
        url = args[args.index("--source") + 1]
        config.set_value("update_source", url)
        src = url
        ui.ok(f"Update source set: {url}")
    with ui.Spinner("checking for updates"):
        tag, level, src = check()
    if not tag:
        ui.ok(f"Windmill {version.current()} is the latest version.")
        return
    ui.rule(f"Windmill {tag[1:]} ({level}) - you have {version.current()}")
    print(ui.dim(notes(src, tag) or "(release notes will show after installing)"))
    old = version.current()
    if ui.confirm("Install it?", True) and apply(tag, src):
        ui.ok(f"Updated to {version.current()}.  (undo: windmill update --rollback)")
        print(ui.dim(notes(None, None, since=old)))


def cmd_version():
    cfg = config.load()
    src, mode = source(cfg)
    print(f"  Windmill {ui.bold(version.current())}")
    print(f"  {ui.dim('updates:')} {cfg.get('auto_update', 'auto')}  {ui.dim('from')} {src or 'not configured'}")
    if cfg.get("last_update_check"):
        print(f"  {ui.dim('last checked:')} {ui.ago(float(cfg['last_update_check']))}")
    if is_dev():
        print(f"  {ui.dim('this is the development copy: commits are released automatically')}")


def dev_setup():
    """Turn this Windmill folder into the release source: git repo + release hook."""
    if not (KIT / ".git").exists():
        git("init", "-q")
        git("checkout", "-q", "-b", "main")
    gi = KIT / ".gitignore"
    if not gi.exists():
        gi.write_text(GITIGNORE, encoding="utf-8")
    if not (KIT / "VERSION").exists():
        (KIT / "VERSION").write_text("1.0.0\n", encoding="utf-8")
    rc, head, _ = git("rev-parse", "HEAD")
    if rc:
        git("add", "-A")
        subprocess.run(["git", "commit", "-q", "-m", f"Windmill {version.current()}"], cwd=KIT,
                       env={**os.environ, "WINDMILL_RELEASING": "1"})
        git("tag", "-a", f"v{version.current()}", "-m", f"minor: Windmill {version.current()}")
    hook = KIT / ".git" / "hooks" / "post-commit"
    py = sys.executable.replace("\\", "/")
    rel = str(config.SCRIPTS / "release.py").replace("\\", "/")
    hook.write_text("#!/bin/sh\n# windmill release hook: asks Major/Minor/Fix after each commit\n"
                    '[ -n "$WINDMILL_RELEASING" ] && exit 0\n'
                    f'if [ -r /dev/tty ] && ( : < /dev/tty ) 2>/dev/null; then exec "{py}" "{rel}" --hook < /dev/tty\n'
                    f'else exec "{py}" "{rel}" --hook; fi\n', encoding="utf-8")
    hook.chmod(0o755)
    (KIT / ".git" / "windmill-dev").write_text("release source\n")
    ui.ok("Development mode on: every commit here asks Major / Minor / Fix and releases automatically.")


def dev_publish(url):
    rc, _, _ = git("remote", "get-url", "origin")
    git("remote", "set-url" if rc == 0 else "add", "origin", url)
    SOURCE_FILE.write_text(url + "\n", encoding="utf-8")
    env = {**os.environ, "WINDMILL_RELEASING": "1"}
    subprocess.run(["git", "add", "UPDATE_SOURCE"], cwd=KIT, env=env)
    subprocess.run(["git", "commit", "-q", "-m", "Set update source"], cwd=KIT, env=env)
    r = subprocess.run(["git", "push", "-u", "origin", "HEAD", "--follow-tags"], cwd=KIT, env=env)
    subprocess.run(["git", "push", "origin", "--tags"], cwd=KIT, env=env, capture_output=True)
    (ui.ok if r.returncode == 0 else ui.err)("Published. Installed copies will now update from " + url
                                             if r.returncode == 0 else "Push failed - check the URL and your GitHub login.")
