"""GitHub / git for a project, in plain words: which repo it's connected to, sync (save + get + send),
pull, push, connect a different repo, or open a GitHub repo as a new project.
Used by `windmill git ...` and by the desktop app's project window."""
import os
import re
import subprocess
import time
from pathlib import Path

from . import config, ui

ENV = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}  # never hang waiting for a typed password


def git(root, *a, timeout=300):
    try:
        r = subprocess.run(["git", *a], cwd=root, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout, env=ENV)
        return r.returncode, r.stdout.strip(), r.stderr.strip()
    except FileNotFoundError:
        return 127, "", "Git isn't installed - run  windmill setup  to install it."
    except subprocess.TimeoutExpired:
        return 124, "", "git took too long (network?)"


def is_repo(root):
    return git(root, "rev-parse", "--is-inside-work-tree", timeout=10)[0] == 0


def remote(root):
    rc, out, _ = git(root, "remote", "get-url", "origin", timeout=10)
    return out if rc == 0 else ""


def branch(root):
    rc, out, _ = git(root, "symbolic-ref", "--short", "HEAD", timeout=10)
    return out if rc == 0 else ""


def web_url(url):
    """https page for a remote URL (git@github.com:a/b.git -> https://github.com/a/b)."""
    m = re.match(r"^(?:git@|ssh://git@)([^:/]+)[:/](.+?)(?:\.git)?/?$", url or "")
    if m:
        return f"https://{m.group(1)}/{m.group(2)}"
    return re.sub(r"\.git/?$", "", url or "") or ""


def normalize(url):
    """Accept 'owner/repo', a github.com page URL, or a full git URL."""
    url = (url or "").strip().strip('"')
    if re.fullmatch(r"[\w.-]+/[\w.-]+", url):
        return f"https://github.com/{url}.git"
    if re.match(r"^https?://github\.com/[\w.-]+/[\w.-]+/?$", url):
        return url.rstrip("/") + ".git"
    return url


def info(root):
    """Quick, offline status for the app (no network)."""
    root = Path(root)
    if not is_repo(root):
        return {"repo": False}
    _, changes, _ = git(root, "status", "--porcelain", timeout=20)
    rc, counts, _ = git(root, "rev-list", "--left-right", "--count", "@{u}...HEAD", timeout=10)
    behind, ahead = (int(x) for x in counts.split()) if rc == 0 and counts else (0, 0)
    url = remote(root)
    return {"repo": True, "remote": url, "web": web_url(url), "branch": branch(root),
            "changes": len([x for x in changes.splitlines() if x.strip()]), "ahead": ahead, "behind": behind}


def _say(rc, out, err, ok_msg):
    if rc == 0:
        ui.ok(ok_msg)
        return True
    msg = (err or out).strip()
    ui.err(msg[-1200:] or "git failed")
    if re.search(r"Authentication|could not read Username|403|denied|terminal prompts disabled", msg, re.I):
        ui.info("GitHub needs you to sign in once. Open a terminal and run  git push  in this folder - "
                "a GitHub sign-in window opens; after that Windmill can push and pull on its own.")
    return False


def ensure_repo(root):
    if is_repo(root):
        return True
    rc, out, err = git(root, "init", "-b", "main")
    if rc:
        rc, out, err = git(root, "init")
    return _say(rc, out, err, "Started version history (git) for this project.")


def commit(root, msg=None):
    if not ensure_repo(root):
        return False
    git(root, "add", "-A")
    if not git(root, "status", "--porcelain")[1]:
        ui.info("Nothing new to save.")
        return True
    msg = msg or f"Update from Windmill ({time.strftime('%Y-%m-%d %H:%M')})"
    return _say(*git(root, "commit", "-q", "-m", msg), f"Saved your changes: {msg}")


def pull(root):
    if not remote(root):
        ui.warn("This project isn't connected to a GitHub repo yet.")
        return False
    b = branch(root) or "main"
    with ui.Spinner("getting the latest changes"):
        r = git(root, "pull", "--rebase", "--autostash", "origin", b)
    if r[0] and "couldn't find remote ref" in (r[2] or "").lower():
        ui.info("The GitHub repo is empty - nothing to get yet.")
        return True
    return _say(*r, "Up to date with GitHub.")


def push(root):
    if not remote(root):
        ui.warn("This project isn't connected to a GitHub repo yet.")
        return False
    with ui.Spinner("sending your changes to GitHub"):
        r = git(root, "push", "-u", "origin", "HEAD")
    return _say(*r, f"Sent to {web_url(remote(root))}")


def sync(root, msg=None):
    """Save -> get the latest -> send."""
    return commit(root, msg) and pull(root) and push(root)


def set_remote(root, url):
    url = normalize(url)
    if not url:
        ui.warn("No repo given.")
        return False
    if not ensure_repo(root):
        return False
    rc = git(root, "remote", "set-url" if remote(root) else "add", "origin", url)
    if not _say(*rc, f"Connected to {web_url(url)}"):
        return False
    with ui.Spinner("checking the repo"):
        r = git(root, "ls-remote", "--heads", "origin", timeout=60)
    if r[0]:
        ui.warn(f"Couldn't reach it yet: {(r[2] or r[1])[-300:]}")
    elif r[1] and not git(root, "rev-parse", "HEAD", timeout=10)[0] == 0:
        ui.info("The repo already has files - getting them.")
        b = "main" if "refs/heads/main" in r[1] else r[1].split("refs/heads/")[1].splitlines()[0]
        git(root, "fetch", "-q", "origin")
        git(root, "checkout", "-q", "-B", b, f"origin/{b}")
    return True


def clone(url, parent=None):
    from .project import projects_home
    url = normalize(url)
    name = re.sub(r"\.git$", "", url.rstrip("/").split("/")[-1].split(":")[-1]) or "project"
    dest = Path(parent or projects_home()) / name
    n = 2
    while dest.exists() and any(dest.iterdir()):
        dest = Path(parent or projects_home()) / f"{name}-{n}"
        n += 1
    with ui.Spinner(f"downloading {web_url(url)}"):
        rc, out, err = git(Path(dest).parent, "clone", url, str(dest), timeout=1800)
    if _say(rc, out, err, "Downloaded."):
        print(f"Project ready at {dest}")
        return dest
    return None


def status(root):
    i = info(root)
    if not i["repo"]:
        ui.info("No version history yet. `windmill git connect <repo>` connects this folder to GitHub.")
        return
    print(f"  branch:  {i['branch'] or '(none)'}\n  GitHub:  {i['web'] or 'not connected'}\n"
          f"  unsaved changes: {i['changes']}   to send: {i['ahead']}   to get: {i['behind']}")


def cmd(args):
    root = config.project_root()
    sub = args[0] if args else "status"
    rest = args[1:]
    if sub == "status":
        status(root)
    elif sub == "sync":
        sync(root, " ".join(rest) or None)
    elif sub == "pull":
        pull(root)
    elif sub == "push":
        push(root)
    elif sub in ("commit", "save"):
        commit(root, " ".join(rest) or None)
    elif sub in ("connect", "remote"):
        set_remote(root, rest[0] if rest else ui.ask("GitHub repo (owner/name or URL)"))
    elif sub in ("clone", "open"):
        url = rest[0] if rest else ui.ask("GitHub repo to open (owner/name or URL)")
        if url:
            clone(url, rest[1] if len(rest) > 1 else None)
    else:
        print("windmill git [status | sync [message] | pull | push | commit [message] | connect <repo> | clone <repo>]")
