"""Shared helpers for pipeline.py and best_of_n.py (stdlib only)."""
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

def _api_base():
    if os.environ.get("AI_KIT_API_BASE"):
        return os.environ["AI_KIT_API_BASE"].rstrip("/")
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from wm import config as wcfg
        return wcfg.api_base().rstrip("/")
    except Exception:  # noqa: BLE001
        return "http://127.0.0.1:8080/v1"


API_BASE = _api_base()
MODEL = os.environ.get("AI_KIT_MODEL", "local")
# Command used to launch Aider (override e.g. AI_KIT_AIDER="python -m aider")
AIDER = os.environ.get("AI_KIT_AIDER", "aider").split()
MAX_DIFF_CHARS = 120_000  # ~30k tokens; larger diffs get truncated for review


def log(msg):
    print(f"\n\033[1;36m[ai-kit]\033[0m {msg}", flush=True)


STOPPED = 4  # exit code: stopped on purpose with a message for the user (not a crash)


def die(msg):
    print(f"\033[1;31m[ai-kit] {msg}\033[0m", file=sys.stderr, flush=True)
    sys.exit(STOPPED)


def git(*args, cwd, check=True):
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if check and r.returncode != 0:
        die(f"git {' '.join(args)} failed:\n{r.stderr}")
    return r.stdout.strip()


def repo_root(start="."):
    r = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=start,
                       capture_output=True, text=True)
    if r.returncode != 0:
        die("Not inside a git repository. Run `git init` and make a first commit.")
    return Path(r.stdout.strip())


KIT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(KIT))


def load_project_cfg(root):
    cfg = {"test_cmd": None, "lint_cmd": None, "max_rounds": 4, "best_of_n": 4, "review_extra": ""}
    p = root / "ai-kit.json"
    if p.exists():
        cfg.update(json.loads(p.read_text(encoding="utf-8")))
    if not cfg.get("test_cmd"):  # any project type: Windmill's own checks (build/lint/tests/slop guard)
        cfg["test_cmd"] = f'"{sys.executable}" "{KIT / "windmill.py"}" check'
    if (root / "Cargo.toml").exists() and not cfg.get("review_extra"):
        cfg["review_extra"] = ("Rust: unwrap()/expect() outside tests, panics in library code, needless clone(), "
                               "unsafe without SAFETY comment, blocking calls in async, truncating `as` casts, "
                               "discarded errors, missing docs on public items.")
    return cfg


def run_dir(root, kind):
    d = Path(os.environ.get("WINDMILL_HOME", Path.home() / ".windmill")) / "runs" / f"{root.name}-{kind}-{time.strftime('%Y%m%d-%H%M%S')}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def engine():
    try:
        from wm import config as wcfg
        return wcfg.load().get("engine", "windmill")
    except Exception:  # noqa: BLE001
        return "windmill"


def check_server():
    try:
        with urllib.request.urlopen(f"{API_BASE}/models", timeout=10) as r:
            r.read()
    except Exception:  # noqa: BLE001
        try:
            from wm import server as wserver
            if wserver.ensure():
                return
        except Exception:  # noqa: BLE001
            pass
        die(f"Can't reach the model server at {API_BASE}. Run:  windmill server start")
    if engine() == "aider" and not shutil.which(AIDER[0]):
        die(f"'{AIDER[0]}' not found on PATH. Install Aider or: windmill settings engine windmill")


def chat(messages, temperature=0.6, max_tokens=16384):
    """One fresh-context call to the local model. Thinking (if on) is returned separately
    by llama-server, so we only get the final answer back."""
    body = json.dumps({"model": MODEL, "messages": messages, "temperature": temperature,
                       "top_p": 0.95, "top_k": 20, "max_tokens": max_tokens}).encode()
    req = urllib.request.Request(f"{API_BASE}/chat/completions", data=body,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=7200) as r:  # slow is fine
            data = json.loads(r.read())
    except urllib.error.HTTPError as e:
        die(f"Model call failed: {e.code} {e.read().decode(errors='replace')[:500]}")
    content = data["choices"][0]["message"].get("content") or ""
    # Safety net if the server left <think> inline
    if "</think>" in content:
        content = content.split("</think>", 1)[1]
    return content.strip()


def run_aider(cwd, message, run_path, name, test_cmd=None, lint_cmd=None, read=(), edit=()):
    """Run the edit engine non-interactively in a FRESH context for one instruction."""
    msg_file = run_path / f"{name}.prompt.md"
    msg_file.write_text(message, encoding="utf-8")
    if engine() != "aider":
        log(f"windmill agent: {name}")
        from wm.agent import run_headless
        return run_headless(cwd, message, read=read, edit=edit, name=name, run_path=run_path)
    cmd = [*AIDER, "--yes-always", "--no-check-update",
           "--message-file", str(msg_file),
           "--chat-history-file", str(run_path / f"{name}.history.md"),
           "--input-history-file", str(run_path / "input.history")]
    for f in read:
        cmd += ["--read", str(f)]
    for f in edit:
        cmd += ["--file", str(f)]
    if test_cmd:
        cmd += ["--test-cmd", test_cmd, "--auto-test"]
    if lint_cmd:
        cmd += ["--lint-cmd", lint_cmd, "--auto-lint"]
    log(f"aider: {name}")
    return subprocess.run(cmd, cwd=cwd).returncode


def run_shell(cmd, cwd, timeout=1800):
    """Run the project's test/lint command. Returns (ok, output tail)."""
    if not cmd:
        return True, "(no command configured)"
    try:
        r = subprocess.run(cmd, cwd=cwd, shell=True, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, f"`{cmd}` timed out after {timeout}s"
    out = (r.stdout + "\n" + r.stderr).strip()
    return r.returncode == 0, out[-8000:]


def repo_map(cwd):
    """Compact project map from ai_index (zero model tokens to build). Falls back to Aider's map."""
    try:
        sys.path.insert(0, str(Path(__file__).parent))
        import ai_index
        ai_index.build(Path(cwd), quiet=True)
        return (Path(cwd) / ".ai" / "map.md").read_text(encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        log(f"ai_index failed ({e}); using Aider's repo map")
    r = subprocess.run([*AIDER, "--show-repo-map", "--map-tokens", "3000", "--no-check-update"],
                       cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode == 0 and len(r.stdout.strip()) > 50:
        return r.stdout.strip()
    return "Files in repo:\n" + git("ls-files", cwd=cwd, check=False)[:20000]


# Never spend tokens on lockfiles, generated index files or snapshots
DIFF_EXCLUDE = [":(exclude)Cargo.lock", ":(exclude).ai", ":(exclude)*.snap", ":(exclude)*.lock"]


def is_dirty(cwd):
    """Uncommitted changes, ignoring our own .ai/ folder."""
    out = git("status", "--porcelain", cwd=cwd, check=False)
    return [l for l in out.splitlines() if not l[3:].startswith(".ai/") and l[3:] != ".ai"]


def journal(cwd, status, task, lines):
    """Append a short entry to .ai/journal.md and commit it (persistent project memory)."""
    j = Path(cwd) / ".ai" / "journal.md"
    j.parent.mkdir(exist_ok=True)
    if not j.exists():
        j.write_text("# AI work journal\nAppended automatically by pipeline.py. Newest at the bottom.\n",
                     encoding="utf-8")
    title = " ".join(task.split())[:90]
    entry = f"\n## {time.strftime('%Y-%m-%d %H:%M')} | {status} | {title}\n" + "".join(f"- {l}\n" for l in lines)
    with j.open("a", encoding="utf-8") as f:
        f.write(entry)
    subprocess.run(["git", "add", ".ai/journal.md", ".ai/.gitignore"], cwd=cwd, capture_output=True)
    subprocess.run(["git", "commit", "-q", "-m", f"ai-journal: {status} {title[:50]}", "--",
                    ".ai/journal.md", ".ai/.gitignore"], cwd=cwd, capture_output=True)


def diff_since(base, cwd):
    d = git("diff", base, "--", ".", *DIFF_EXCLUDE, cwd=cwd, check=False)  # committed + uncommitted vs base
    untracked = git("ls-files", "--others", "--exclude-standard", "--", ".", *DIFF_EXCLUDE, cwd=cwd, check=False)
    if untracked:
        d += "\n\n# Untracked new files:\n" + untracked
    if len(d) > MAX_DIFF_CHARS:
        d = d[:MAX_DIFF_CHARS] + "\n\n[... diff truncated ...]"
    return d
