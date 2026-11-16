"""Optional "Claude check": a stronger model looks at the local AI's work when it matters.

Modes (Settings -> Claude check):
  off        never (default) - fully local, nothing leaves this PC
  stuck      only when the local AI has failed all its retries on a step, or its review rounds ran out
  milestone  Claude does the review of every milestone (phase 3) - plus "stuck"
  step       Claude reviews every step after it passes the checks - plus "milestone" and "stuck"

Claude only REVIEWS and says what to fix; the local model makes the changes.

Two ways to connect (Windmill never stores either in its own settings):
  subscription  the Claude Code app/CLI, signed in with your Claude plan (no key; uses your plan's limits)
  api           an Anthropic API key, kept in the system credential store (Windows Credential Manager),
                with a monthly spending cap
"""
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from . import config

MODES = ("off", "stuck", "milestone", "step")
VIAS = ("auto", "subscription", "api")
MODEL = "claude-opus-5-5"
PRICE_IN, PRICE_OUT = 4.00, 20.00  # US$ per million tokens (input, output)
USAGE = config.HOME / "claude-usage.json"
NO_WINDOW = {"creationflags": 0x08000000} if os.name == "nt" else {}
KEY_HOST = "api.anthropic.com"
_CACHE = {}

REVIEW_SYSTEM = """You are a senior engineer reviewing work done by a small LOCAL AI model that builds this project
step by step. You did not write it. Be concrete and brief: the local model will read your review and fix the code,
so name exact files, functions and the change to make. Use the real API of the dependency versions the project
uses (check Cargo.toml / Cargo.lock / package.json), not older ones.

Respond in markdown with exactly these sections:

## Requirements checklist
- R1: DONE | PARTIAL | MISSING - evidence (file:function, test name)
(one line per requirement in the plan; for a single step, the step's own requirements)

## Issues
- [high|medium|low] file:line - problem - exact fix
Look for: code that doesn't do what the task asks, wrong or outdated APIs, tests that don't really test anything
or are never run, dead code, missing error handling. Ignore style nitpicks. Write "None" if there are none.

Final line, exactly one of:
VERDICT: PASS
VERDICT: FAIL

Any PARTIAL or MISSING requirement, or any high/medium issue => VERDICT: FAIL."""

STUCK_SYSTEM = """You are a senior engineer helping a small LOCAL AI model that is stuck: it tried a step of a build
several times and the project's checks still fail. Find the real cause and give it fix instructions it can follow
exactly: which file, which code to replace with what (show the corrected code). Use the real API of the dependency
versions the project uses (check Cargo.toml / Cargo.lock / package.json) - outdated APIs are a common cause.
If the step itself is impossible or wrong, say what to do instead. Be brief; no pleasantries."""


# ------------------------------------------------------------------ settings
def settings():
    cfg = config.load()
    mode = cfg.get("claude_check") or "off"
    via = cfg.get("claude_via") or "auto"
    return {"mode": mode if mode in MODES else "off", "via": via if via in VIAS else "auto",
            "cap": float(cfg.get("claude_cap_usd") or 0)}


def active_mode():
    """The mode to use right now: 'off' when it's off or nothing is connected."""
    s = settings()
    if s["mode"] == "off":
        return "off"
    return s["mode"] if backend() else "off"


# ------------------------------------------------------------------ connections
def _candidates():
    """Where Claude Code usually lives on Windows (and elsewhere), newest first."""
    home = Path.home()
    appdata = Path(os.environ.get("APPDATA") or home / "AppData" / "Roaming")
    local = Path(os.environ.get("LOCALAPPDATA") or home / "AppData" / "Local")
    fixed = [home / ".local" / "bin" / "claude.exe", home / ".local" / "bin" / "claude",   # Claude Code installer
             appdata / "npm" / "claude.cmd", home / ".npm-global" / "bin" / "claude",       # npm install -g
             home / ".claude" / "local" / "claude.exe", home / ".claude" / "local" / "claude",
             local / "Programs" / "claude" / "claude.exe", local / "Microsoft" / "WinGet" / "Links" / "claude.exe"]
    found = [p for p in fixed if p.is_file()]
    # the copy of Claude Code that the Claude desktop app downloads and keeps up to date
    roots = [appdata / "Claude" / "claude-code", local / "Claude" / "claude-code", local / "AnthropicClaude",
             local / "Programs" / "Claude", local / "Packages"]
    extra = []
    for r in roots:
        if not r.is_dir():
            continue
        try:
            pattern = "Claude*/**/claude-code*/**/claude.exe" if r.name == "Packages" else "**/claude.exe"
            for i, p in enumerate(r.glob(pattern)):
                if i > 50:
                    break
                if p.is_file() and p.stat().st_size > 1_000_000:  # the real program, not a small launcher
                    extra.append(p)
        except OSError:
            pass
    extra.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return found + extra


def cli_path():
    """The Claude Code program: your saved choice, then PATH (re-read: it may be newer than Windmill), then the
    usual install folders, including the copy inside the Claude desktop app."""
    mine = (config.load().get("claude_path") or "").strip().strip('"')
    if mine and Path(mine).is_file():
        return mine
    try:
        from . import setup
        setup.refresh_path()
    except Exception:  # noqa: BLE001
        pass
    exe = shutil.which("claude")
    if exe:
        return exe
    c = _candidates()
    return str(c[0]) if c else None


def sign_in():
    """Open Claude Code's sign-in (a browser page) in a small window the user can see. -> error or None."""
    exe = cli_path()
    if not exe:
        return "Claude Code wasn't found on this PC."
    try:
        if os.name == "nt":
            subprocess.Popen(["cmd", "/c", "title Claude sign-in for Windmill && echo Sign in in the browser page "
                              "that opens, then close this window. && \"" + exe + "\" auth login & pause"],
                             creationflags=0x00000010)  # CREATE_NEW_CONSOLE: the one window you're meant to see
        else:
            subprocess.Popen([exe, "auth", "login"])
    except OSError as e:
        return str(e)
    _CACHE.pop("sub", None)
    return None


def subscription(refresh=False):
    """{'installed', 'signed_in', 'method'} for the Claude Code CLI (cached a few minutes)."""
    c = _CACHE.get("sub")
    if c and not refresh and time.time() - c[0] < 300:
        return c[1]
    exe = cli_path()
    info = {"installed": bool(exe), "signed_in": False, "method": None, "path": exe}
    if exe:
        try:
            r = subprocess.run([exe, "auth", "status", "--json"], capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=30, stdin=subprocess.DEVNULL, **NO_WINDOW)
            st = json.loads(r.stdout or "{}")
            info.update(signed_in=bool(st.get("loggedIn")), method=st.get("authMethod"))
        except (OSError, ValueError, subprocess.TimeoutExpired) as e:
            info["problem"] = f"couldn't run it: {str(e)[:120]}"
    _CACHE["sub"] = (time.time(), info)
    return info


def _git_env():
    return {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "never", "GIT_ASKPASS": "", "SSH_ASKPASS": ""}


def api_key():
    """The saved API key from the system credential store (never prompts)."""
    try:
        r = subprocess.run(["git", "credential", "fill"], input=f"protocol=https\nhost={KEY_HOST}\nusername=windmill\n\n",
                           capture_output=True, text=True, timeout=20, env=_git_env(), **NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired):
        return None
    vals = dict(line.split("=", 1) for line in r.stdout.splitlines() if "=" in line)
    key = vals.get("password") if r.returncode == 0 else None
    return key if key and key.startswith("sk-ant-") else None


def save_key(key):
    """Check the key with Anthropic (free call), then keep it in the credential store. -> (ok, message)"""
    key = (key or "").strip()
    if not key.startswith("sk-ant-"):
        return False, "That doesn't look like an Anthropic API key (they start with sk-ant-)."
    try:
        anthropic = _sdk()
        anthropic.Anthropic(api_key=key, max_retries=1, timeout=30).models.retrieve(MODEL)
    except Exception as e:  # noqa: BLE001
        return False, _api_error(e)
    data = f"protocol=https\nhost={KEY_HOST}\nusername=windmill\npassword={key}\n\n"
    subprocess.run(["git", "credential", "approve"], input=data, text=True, capture_output=True, env=_git_env(),
                   timeout=20, **NO_WINDOW)
    if api_key() != key:
        return False, "The key works, but the system credential store didn't keep it (is Git for Windows installed?)."
    return True, "API key saved in Windows Credential Manager."


def forget_key():
    subprocess.run(["git", "credential", "reject"], input=f"protocol=https\nhost={KEY_HOST}\nusername=windmill\n\n",
                   text=True, capture_output=True, env=_git_env(), timeout=20, **NO_WINDOW)


def backend():
    """'subscription', 'api' or None - what a check would use right now."""
    via = settings()["via"]
    if via in ("auto", "subscription") and subscription()["signed_in"]:
        return "subscription"
    if via in ("auto", "api") and api_key():
        return "api"
    return None


# ------------------------------------------------------------------ usage (counter + monthly cap)
def _month():
    return time.strftime("%Y-%m")


def usage():
    try:
        u = json.loads(USAGE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        u = {}
    if u.get("month") != _month():
        u = {"month": _month(), "calls": 0, "usd": 0.0, "week": []}
    week_ago = time.time() - 7 * 86400
    u["week"] = [t for t in u.get("week", []) if t > week_ago]
    return u


def _record(usd):
    u = usage()
    u["calls"] += 1
    u["usd"] = round(u.get("usd", 0.0) + usd, 4)
    u["week"].append(time.time())
    try:
        tmp = USAGE.with_name(USAGE.name + ".tmp")
        tmp.write_text(json.dumps(u), encoding="utf-8")
        os.replace(tmp, USAGE)
    except OSError:
        pass


def status():
    s = settings()
    u = usage()
    sub = subscription()
    return {**s, "backend": backend(), "subscription": sub, "has_key": bool(api_key()),
            "claude_path": config.load().get("claude_path") or "",
            "calls_month": u["calls"], "calls_week": len(u["week"]), "usd_month": round(u["usd"], 2)}


# ------------------------------------------------------------------ asking Claude
def _sdk():
    try:
        import anthropic
    except ImportError:  # installed the first time an API key is used
        subprocess.run([sys.executable, "-m", "pip", "install", "--user", "--quiet", "anthropic"], capture_output=True,
                       **NO_WINDOW)
        import importlib
        import site
        importlib.invalidate_caches()
        try:
            sys.path.append(site.getusersitepackages())
        except Exception:  # noqa: BLE001
            pass
        import anthropic
    return anthropic


def _api_error(e):
    try:
        anthropic = _sdk()
        if isinstance(e, anthropic.AuthenticationError):
            return "Anthropic says the API key isn't valid."
        if isinstance(e, anthropic.PermissionDeniedError):
            return "This API key isn't allowed to use Claude (check your Anthropic Console)."
        if isinstance(e, anthropic.RateLimitError):
            return "Anthropic's rate limit was hit - try again in a minute."
        if isinstance(e, anthropic.APIStatusError):
            return f"Anthropic error {e.status_code}: {str(e.message)[:200]}"
        if isinstance(e, anthropic.APIConnectionError):
            return "Couldn't reach Anthropic (no internet?)."
    except Exception:  # noqa: BLE001
        pass
    if isinstance(e, RuntimeError):
        return str(e)[:300]
    return f"{type(e).__name__}: {str(e)[:200]}"


def _via_subscription(system, prompt, root):
    exe = cli_path()
    # Read-only: Claude may read and search the project to understand it, but can't edit or run anything.
    cmd = [exe, "-p", "--output-format", "json", "--model", "opus", "--tools", "Read", "Grep", "Glob",
           "--permission-mode", "dontAsk", "--no-session-persistence", "--append-system-prompt", system]
    r = subprocess.run(cmd, input=prompt, cwd=str(root), capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=1200, **NO_WINDOW)
    try:
        data = json.loads(r.stdout.strip().splitlines()[-1]) if r.stdout.strip() else {}
    except (ValueError, IndexError):
        data = {}
    text = data.get("result") or ""
    if r.returncode != 0 or data.get("is_error") or not text:
        err = text or (r.stderr or r.stdout).strip()[-300:] or f"exit code {r.returncode}"
        if "limit" in err.lower():
            err = "Your Claude plan's usage limit is reached for now - it resets later. " + err[:150]
        raise RuntimeError(err)
    return text, 0.0  # covered by the plan - not counted against the API cap


def _via_api(system, prompt):
    s = settings()
    u = usage()
    if s["cap"] and u["usd"] >= s["cap"]:
        raise RuntimeError(f"This month's Claude spending cap (${s['cap']:.2f}) is reached - raise it in Settings "
                           "or wait until next month.")
    anthropic = _sdk()
    client = anthropic.Anthropic(api_key=api_key(), max_retries=2)
    with client.beta.messages.stream(
        model=MODEL,
        max_tokens=16000,
        system=system,
        messages=[{"role": "user", "content": prompt}],
        output_config={"effort": "high"},
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",  # if this model declines, Anthropic retries on a suitable one
    ) as stream:
        msg = stream.get_final_message()
    if msg.stop_reason == "refusal":
        raise RuntimeError("Claude declined to review this.")
    text = "".join(b.text for b in msg.content if b.type == "text").strip()
    us = msg.usage
    usd = ((us.input_tokens + (getattr(us, "cache_creation_input_tokens", 0) or 0) * 1.25
            + (getattr(us, "cache_read_input_tokens", 0) or 0) * 0.1) * PRICE_IN + us.output_tokens * PRICE_OUT) / 1e6
    if not text:
        raise RuntimeError("Claude sent an empty answer.")
    return text, usd


def ask(kind, prompt, root, files=()):
    """kind: 'review' or 'stuck'. -> {'ok', 'text', 'via', 'usd', 'error'}. Never raises."""
    via = backend()
    if not via:
        return {"ok": False, "error": "Claude isn't connected (Settings -> Claude check)."}
    system = REVIEW_SYSTEM if kind == "review" else STUCK_SYSTEM
    try:
        if via == "subscription":
            text, usd = _via_subscription(system, prompt + "\n\n(You can read any file in this project.)", root)
        else:
            text, usd = _via_api(system, prompt + _file_pack(root, files))
    except subprocess.TimeoutExpired:
        return {"ok": False, "via": via, "error": "Claude took too long (20 minutes) - skipped."}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "via": via, "error": _api_error(e) if via == "api" else str(e)[:400]}
    _record(usd)
    return {"ok": True, "text": text, "via": via, "usd": usd}


def _file_pack(root, files, budget=60000):
    """The API can't open files itself: include the build files and the files involved (trimmed)."""
    root = Path(root)
    want = [p for p in ("Cargo.toml", "package.json", "pyproject.toml", "go.mod", "AGENTS.md") if (root / p).exists()]
    want += [str(f) for f in files]
    out, used, seen = [], 0, set()
    for f in want:
        p = (root / f) if not Path(f).is_absolute() else Path(f)
        try:
            rel = p.resolve().relative_to(root.resolve()).as_posix()
        except ValueError:
            continue
        if rel in seen or not p.is_file() or p.stat().st_size > 200_000:
            continue
        seen.add(rel)
        t = p.read_text(encoding="utf-8", errors="replace")
        if used + len(t) > budget:
            t = t[:max(0, budget - used)] + "\n[... trimmed ...]"
        out.append(f"\n\n### {rel}\n```\n{t}\n```")
        used += len(t)
        if used >= budget:
            break
    return "\n\nFILES:" + "".join(out) if out else ""


def verdict(text):
    """True when a review approves the work."""
    import re
    v = re.findall(r"VERDICT:\s*(PASS|FAIL)", text or "", re.I)
    incomplete = re.search(r"^\s*[-*]?\s*\**R\d+\**\s*:\s*\**(PARTIAL|MISSING)", text or "", re.M | re.I)
    return bool(v) and v[-1].upper() == "PASS" and not incomplete
