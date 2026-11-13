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
    elif sub in ("login", "signin", "sign-in"):
        login()
    elif sub in ("logout", "signout", "sign-out"):
        logout()
    elif sub == "repos":
        repos_cmd()
    elif sub in ("clone", "open"):
        url = rest[0] if rest else ui.ask("GitHub repo to open (owner/name or URL)")
        if url:
            clone(url, rest[1] if len(rest) > 1 else None)
    else:
        print("windmill git [status | sync [message] | pull | push | commit [message] | connect <repo> | clone <repo> |"
              " login | logout | repos]")


# ------------------------------------------------------------------ GitHub sign-in (like Claude's GitHub access)
# Uses Git Credential Manager (ships with Git for Windows): a browser sign-in, and the token is kept in the
# Windows Credential Manager - Windmill never stores it itself. Git push/pull then use the same sign-in.
import json  # noqa: E402
import shutil  # noqa: E402
import urllib.request  # noqa: E402

API = os.environ.get("WINDMILL_GITHUB_API", "https://api.github.com")  # override only for tests
_CACHE = {}


def _gcm():
    for name in ("git-credential-manager", "git-credential-manager-core"):
        if shutil.which(name):
            return name
    rc, out, _ = git(".", "--exec-path", timeout=10)
    if rc == 0:
        for name in ("git-credential-manager.exe", "git-credential-manager-core.exe"):
            ex = Path(out)  # e.g. C:/Program Files/Git/mingw64/libexec/git-core
            for d in (ex, ex.parent.parent / "bin", ex.parent.parent.parent / "mingw64" / "bin", ex.parent.parent.parent / "bin"):
                if (d / name).exists():
                    return str(d / name)
    return None


def token():
    """The github.com token git already has (never prompts)."""
    env = {**ENV, "GCM_INTERACTIVE": "never", "GIT_ASKPASS": "", "SSH_ASKPASS": ""}
    try:
        r = subprocess.run(["git", "credential", "fill"], input="protocol=https\nhost=github.com\n\n", capture_output=True,
                           text=True, timeout=20, env=env)
    except (OSError, subprocess.TimeoutExpired):
        return None
    vals = dict(line.split("=", 1) for line in r.stdout.splitlines() if "=" in line)
    return vals.get("password") if r.returncode == 0 else None


def api(path, tok=None):
    tok = tok or token()
    if not tok:
        return None
    req = urllib.request.Request(API + path, headers={"Authorization": f"Bearer {tok}", "User-Agent": "windmill",
                                                      "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())


def account(refresh=False):
    """{'signed_in': bool, 'login', 'name', 'avatar'} - cached for a few minutes."""
    c = _CACHE.get("account")
    if c and not refresh and time.time() - c[0] < 300:
        return c[1]
    info = {"signed_in": False}
    try:
        u = api("/user")
        if u:
            info = {"signed_in": True, "login": u["login"], "name": u.get("name") or u["login"], "avatar": u.get("avatar_url")}
    except Exception as e:  # noqa: BLE001 - expired/invalid token or offline
        info = {"signed_in": False, "error": str(e)[:200]}
    _CACHE["account"] = (time.time(), info)
    return info


def my_repos():
    out = []
    for page in range(1, 6):
        batch = api(f"/user/repos?per_page=100&page={page}&sort=updated&affiliation=owner,collaborator,organization_member")
        if not batch:
            break
        out += [{"full_name": r["full_name"], "private": r["private"], "description": r.get("description") or "",
                 "updated": r.get("pushed_at") or r.get("updated_at"), "url": r["clone_url"], "web": r["html_url"]}
                for r in batch]
        if len(batch) < 100:
            break
    return out


def _store(login, tok):
    data = f"protocol=https\nhost=github.com\nusername={login}\npassword={tok}\n\n"
    subprocess.run(["git", "credential", "approve"], input=data, text=True, capture_output=True, env=ENV, timeout=20)


def login():
    a = account(refresh=True)
    if a["signed_in"]:
        ui.ok(f"Already signed in to GitHub as {a['login']}.")
        return True
    gcm = _gcm()
    if gcm:
        ui.info("A GitHub sign-in window is opening - choose 'Sign in with your browser' and approve Windmill's access.")
        r = subprocess.run([gcm, "github", "login"], env={**os.environ, "GCM_INTERACTIVE": "always"})
        a = account(refresh=True)
        if a["signed_in"]:
            ui.ok(f"Signed in to GitHub as {a['login']}. Windmill can now see your repos, and push/pull just works.")
            return True
        if r.returncode:
            ui.warn("The sign-in window didn't finish.")
    else:
        ui.warn("Git's sign-in helper wasn't found (it comes with Git for Windows).")
    print(ui.dim("  You can sign in with a token instead: github.com/settings/tokens > 'Generate new token (classic)',\n"
                 "  tick 'repo' (and 'workflow' if you use Actions), then paste it here."))
    tok = ui.ask("GitHub token (or Enter to cancel)")
    if not tok:
        return False
    try:
        u = api("/user", tok.strip())
    except Exception as e:  # noqa: BLE001
        ui.err(f"That token didn't work: {e}")
        return False
    _store(u["login"], tok.strip())
    a = account(refresh=True)
    (ui.ok if a["signed_in"] else ui.warn)(f"Signed in to GitHub as {u['login']}." if a["signed_in"] else
                                           "The token works but git didn't save it - is a credential helper set up?")
    return a["signed_in"]


def logout():
    a = account(refresh=True)
    gcm = _gcm()
    if gcm and a.get("login"):
        subprocess.run([gcm, "github", "logout", a["login"]], capture_output=True)
    tok = token()
    if tok:
        subprocess.run(["git", "credential", "reject"], input="protocol=https\nhost=github.com\n\n", text=True,
                       capture_output=True, env=ENV, timeout=20)
    _CACHE.pop("account", None)
    ui.ok("Signed out of GitHub on this PC.")


def repos_cmd():
    a = account()
    if not a["signed_in"]:
        ui.warn("Not signed in - run  windmill git login  first.")
        return
    rs = my_repos()
    for r in rs:
        print(f"  {'🔒' if r['private'] else '  '} {r['full_name']:<45} {ui.dim(r['description'][:60])}")
    print(ui.dim(f"\n  {len(rs)} repos. Open one:  windmill git clone owner/name"))
