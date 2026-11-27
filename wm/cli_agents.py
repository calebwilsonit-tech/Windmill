"""CLI Agent Integration: connect Claude Code (`claude`) and Gemini CLI (`gemini`) to Windmill
and let them spawn & continuously supervise LOCAL AI sub-agents powered by your installed models.

Works two ways:
  1. Inside Windmill (desktop app & terminal REPL): chat with Claude Code or Gemini CLI directly,
     with Windmill's local sub-agent MCP tools wired in automatically and live UI cards.
  2. From your own Claude Code / Gemini CLI terminal: one-click install of Windmill's MCP server
     and `/windmill` + `/windmill-swarm` slash commands into ~/.claude and ~/.gemini.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from . import claude, config

PROVIDERS = ("claude", "gemini")
LABELS = {"claude": "Claude Code", "gemini": "Gemini CLI"}
NO_WINDOW = {"creationflags": 0x08000000} if os.name == "nt" else {}
_CACHE = {}


def label(provider):
    return LABELS.get(provider, provider)


def _python_exe():
    exe = sys.executable
    if os.name == "nt" and exe.lower().endswith("pythonw.exe"):
        cand = exe[:-11] + "python.exe"
        if Path(cand).is_file():
            return cand
    return exe


def mcp_server_spec(project=None):
    """MCP server config entry that launches `windmill mcp-serve`."""
    args = ["-u", str(config.KIT / "windmill.py"), "mcp-serve"]
    if project:
        args += ["--project", str(project)]
    return {"command": _python_exe(), "args": args}


# ------------------------------------------------------------------ discovery & auth status
def _gemini_candidates():
    home = Path.home()
    appdata = Path(os.environ.get("APPDATA") or home / "AppData" / "Roaming")
    local = Path(os.environ.get("LOCALAPPDATA") or home / "AppData" / "Local")
    fixed = [
        appdata / "npm" / "gemini.cmd",
        appdata / "npm" / "gemini.exe",
        home / ".npm-global" / "bin" / "gemini",
        home / ".local" / "bin" / "gemini",
        home / ".local" / "bin" / "gemini.exe",
        local / "Programs" / "gemini" / "gemini.exe",
        Path("/usr/local/bin/gemini"),
        Path("/opt/homebrew/bin/gemini"),
    ]
    return [p for p in fixed if p.is_file()]


def cli_path(provider):
    if provider == "claude":
        return claude.cli_path()
    if provider == "gemini":
        mine = (config.load().get("gemini_path") or "").strip().strip('"')
        if mine and Path(mine).is_file():
            return mine
        try:
            from . import setup
            setup.refresh_path()
        except Exception:  # noqa: BLE001
            pass
        exe = shutil.which("gemini")
        if exe:
            return exe
        c = _gemini_candidates()
        return str(c[0]) if c else None
    return None


def _gemini_status(refresh=False, home=None):
    c = _CACHE.get("gemini")
    if c and not refresh and home is None and time.time() - c[0] < 300:
        return c[1]
    exe = cli_path("gemini")
    h = Path(home) if home else Path.home()
    info = {"installed": bool(exe), "signed_in": False, "path": exe, "version": None}
    if exe:
        try:
            r = subprocess.run([exe, "--version"], capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=15, stdin=subprocess.DEVNULL, **NO_WINDOW)
            if r.returncode == 0:
                info["version"] = (r.stdout or r.stderr).strip().splitlines()[0][:60]
        except (OSError, subprocess.TimeoutExpired) as e:
            info["problem"] = f"couldn't run it: {str(e)[:120]}"
        # Gemini CLI stores OAuth credentials under ~/.gemini/ or uses GEMINI_API_KEY / GOOGLE_API_KEY
        gdir = h / ".gemini"
        has_oauth = any((gdir / f).is_file() for f in ("oauth_creds.json", "google_accounts.json", "credentials.json"))
        has_env = bool(os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"))
        info["signed_in"] = bool(has_oauth or has_env)
    if home is None:
        _CACHE["gemini"] = (time.time(), info)
    return info


def sign_in(provider):
    """Open the CLI agent's sign-in flow in a visible terminal window. -> error or None."""
    if provider == "claude":
        return claude.sign_in()
    if provider == "gemini":
        exe = cli_path("gemini")
        if not exe:
            return "Gemini CLI wasn't found on this PC. Install it with: npm install -g @google/gemini-cli"
        try:
            if os.name == "nt":
                subprocess.Popen(
                    ["cmd", "/c", "title Gemini CLI sign-in for Windmill && echo Sign in to Gemini CLI, "
                     "then close this window. && \"" + exe + "\""],
                    creationflags=0x00000010,
                )
            else:
                subprocess.Popen([exe])
        except OSError as e:
            return str(e)
        _CACHE.pop("gemini", None)
        return None
    return f"Unknown CLI provider: {provider}"


# ------------------------------------------------------------------ plugin & /command installer
WINDMILL_COMMAND_PROMPT = """You are acting as the LEAD ARCHITECT and CONTINUOUS SUPERVISOR over Windmill's LOCAL AI sub-agents.
Your job is to orchestrate local AI models installed on this computer to build the user's request,
while YOU continuously inspect every completed piece behind them and send concrete fixes until it is right.

USER REQUEST:
{args_placeholder}

WORKFLOW (follow strictly):
1. First call `list_local_models` if you want to see which local AI models are installed on this PC.
2. Break the user's request into small, focused, ordered chunks (1 to 5 chunks).
3. For each chunk, call `spawn_local_subagent` with a clear `name`, focused `task`, and optional `files` list.
   (Do NOT write the bulk implementation yourself unless a local bot is completely stuck after retries — let the local AI sub-agents do the building!)
4. As soon as each local sub-agent finishes, IMMEDIATELY inspect its returned `summary`, `files_changed`, `diff`, and `checks_output` (and read files directly if needed) against the original user instructions.
5. If ANYTHING is missing, buggy, off-spec, has stubs/placeholders, or fails checks:
   - Call `send_subagent_feedback` with that bot's `agent_id` and exact, actionable fix instructions (file, function, what to change).
   - Re-check its updated diff and checks. Repeat until the chunk truly satisfies the instructions!
6. Once a chunk is verified and passes checks, call `approve_subagent` for that `agent_id`, then move to the next chunk.
7. Finish with a concise summary of what each local sub-agent built, what you caught/fixed in review, and the final check status."""

SWARM_COMMAND_PROMPT = """You are acting as the LEAD ARCHITECT and CONTINUOUS SUPERVISOR running a team of Windmill LOCAL AI sub-agents.

USER REQUEST:
{args_placeholder}

WORKFLOW (multi-bot local team):
1. Call `list_local_models` to see available local AI models on this PC.
2. Split the task into specialized local sub-agent roles (for example: Core Logic Bot, UI Bot, Test Bot — each with a clean file scope so they don't overwrite each other).
3. Spawn each local sub-agent with `spawn_local_subagent`, specifying its `name`, `task`, `files`, and optionally a `model`.
4. After EACH local sub-agent finishes, review its `diff`, `files_changed`, and `checks_output` against the user's instructions and the rest of the codebase.
5. Whenever you spot a bug, mismatch, or missing detail, call `send_subagent_feedback(agent_id, feedback)` so that local bot fixes its own work and re-runs checks.
6. Call `approve_subagent(agent_id)` once a bot's work passes review.
7. Run `check_project` at the end and give the user a clear summary of each bot's work and your supervision checks."""


def _read_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _toml_escape(s):
    return s.replace("\\", "\\\\").replace('"""', '\\"\\"\\"')


def plugin_status(provider, home=None):
    """Check whether Windmill's MCP server and /windmill slash commands are installed for `provider`."""
    h = Path(home) if home else Path.home()
    if provider == "claude":
        cfg = _read_json(h / ".claude.json")
        mcp_ok = "windmill" in (cfg.get("mcpServers") or {})
        cmds_dir = h / ".claude" / "commands"
        cmds_ok = (cmds_dir / "windmill.md").is_file() and (cmds_dir / "windmill-swarm.md").is_file()
        return {"mcp_installed": mcp_ok, "commands_installed": cmds_ok, "plugin_installed": mcp_ok and cmds_ok}
    if provider == "gemini":
        cfg = _read_json(h / ".gemini" / "settings.json")
        mcp_ok = "windmill" in (cfg.get("mcpServers") or {})
        cmds_dir = h / ".gemini" / "commands"
        cmds_ok = (cmds_dir / "windmill.toml").is_file() and (cmds_dir / "windmill-swarm.toml").is_file()
        return {"mcp_installed": mcp_ok, "commands_installed": cmds_ok, "plugin_installed": mcp_ok and cmds_ok}
    return {"mcp_installed": False, "commands_installed": False, "plugin_installed": False}


def install_plugin(provider, home=None):
    """Install Windmill's MCP server + `/windmill` and `/windmill-swarm` slash commands into Claude Code or Gemini CLI."""
    h = Path(home) if home else Path.home()
    spec = mcp_server_spec()

    if provider == "claude":
        cfg_path = h / ".claude.json"
        cfg = _read_json(cfg_path)
        cfg.setdefault("mcpServers", {})["windmill"] = spec
        _write_json(cfg_path, cfg)

        cmds_dir = h / ".claude" / "commands"
        cmds_dir.mkdir(parents=True, exist_ok=True)
        (cmds_dir / "windmill.md").write_text(
            "---\ndescription: Build with Windmill local AI sub-agents under Claude Code's continuous supervision\n---\n\n"
            + WINDMILL_COMMAND_PROMPT.format(args_placeholder="$ARGUMENTS") + "\n",
            encoding="utf-8",
        )
        (cmds_dir / "windmill-swarm.md").write_text(
            "---\ndescription: Spawn a multi-bot team of Windmill local AI sub-agents supervised by Claude Code\n---\n\n"
            + SWARM_COMMAND_PROMPT.format(args_placeholder="$ARGUMENTS") + "\n",
            encoding="utf-8",
        )
        return True, (
            "Installed Windmill plugin for Claude Code (~/.claude.json MCP + /windmill & /windmill-swarm commands). "
            "Restart Claude Code if it's already open."
        )

    if provider == "gemini":
        cfg_path = h / ".gemini" / "settings.json"
        cfg = _read_json(cfg_path)
        cfg.setdefault("mcpServers", {})["windmill"] = spec
        _write_json(cfg_path, cfg)

        cmds_dir = h / ".gemini" / "commands"
        cmds_dir.mkdir(parents=True, exist_ok=True)
        (cmds_dir / "windmill.toml").write_text(
            'description = "Build with Windmill local AI sub-agents under Gemini CLI continuous supervision"\n'
            'prompt = """\n' + _toml_escape(WINDMILL_COMMAND_PROMPT.format(args_placeholder="{{args}}")) + '\n"""\n',
            encoding="utf-8",
        )
        (cmds_dir / "windmill-swarm.toml").write_text(
            'description = "Spawn a multi-bot team of Windmill local AI sub-agents supervised by Gemini CLI"\n'
            'prompt = """\n' + _toml_escape(SWARM_COMMAND_PROMPT.format(args_placeholder="{{args}}")) + '\n"""\n',
            encoding="utf-8",
        )
        return True, (
            "Installed Windmill plugin for Gemini CLI (~/.gemini/settings.json MCP + /windmill & /windmill-swarm commands). "
            "Restart Gemini CLI if it's already open."
        )

    return False, f"Unknown provider: {provider}"


def uninstall_plugin(provider, home=None):
    """Remove Windmill's MCP server entry and slash commands from Claude Code or Gemini CLI."""
    h = Path(home) if home else Path.home()
    if provider == "claude":
        cfg_path = h / ".claude.json"
        if cfg_path.is_file():
            cfg = _read_json(cfg_path)
            if isinstance(cfg.get("mcpServers"), dict):
                cfg["mcpServers"].pop("windmill", None)
                _write_json(cfg_path, cfg)
        for fn in ("windmill.md", "windmill-swarm.md"):
            p = h / ".claude" / "commands" / fn
            if p.is_file():
                p.unlink()
        return True, "Removed Windmill plugin from Claude Code."

    if provider == "gemini":
        cfg_path = h / ".gemini" / "settings.json"
        if cfg_path.is_file():
            cfg = _read_json(cfg_path)
            if isinstance(cfg.get("mcpServers"), dict):
                cfg["mcpServers"].pop("windmill", None)
                _write_json(cfg_path, cfg)
        for fn in ("windmill.toml", "windmill-swarm.toml"):
            p = h / ".gemini" / "commands" / fn
            if p.is_file():
                p.unlink()
        return True, "Removed Windmill plugin from Gemini CLI."

    return False, f"Unknown provider: {provider}"


def status(refresh=False, home=None):
    """Unified status dict for both Claude Code and Gemini CLI."""
    c_sub = claude.subscription(refresh=refresh)
    g_sub = _gemini_status(refresh=refresh, home=home)
    cfg = config.load()
    return {
        "default_provider": cfg.get("cli_provider") or "claude",
        "subagent_max_rounds": int(cfg.get("subagent_max_rounds") or 3),
        "subagent_max_steps": int(cfg.get("subagent_max_steps") or 35),
        "providers": {
            "claude": {
                "provider": "claude",
                "label": LABELS["claude"],
                "installed": bool(c_sub.get("installed")),
                "signed_in": bool(c_sub.get("signed_in")),
                "path": c_sub.get("path"),
                "saved_path": cfg.get("claude_path") or "",
                "problem": c_sub.get("problem"),
                **plugin_status("claude", home=home),
            },
            "gemini": {
                "provider": "gemini",
                "label": LABELS["gemini"],
                "installed": bool(g_sub.get("installed")),
                "signed_in": bool(g_sub.get("signed_in")),
                "path": g_sub.get("path"),
                "saved_path": cfg.get("gemini_path") or "",
                "version": g_sub.get("version"),
                "problem": g_sub.get("problem"),
                **plugin_status("gemini", home=home),
            },
        },
    }


# ------------------------------------------------------------------ running CLI agents
def expand_slash_command(prompt, local_model=None):
    """If the user typed `/windmill <task>` or `/windmill-swarm <task>` or `/swarm <task>`,
    expand it into the full continuous-supervision prompt."""
    text = (prompt or "").strip()
    low = text.lower()
    model_hint = f"\nPREFERRED LOCAL MODEL FOR SUB-AGENTS: {local_model}\n" if local_model else ""
    if low.startswith("/windmill-swarm ") or low.startswith("/swarm "):
        task = text.split(" ", 1)[1].strip()
        return SWARM_COMMAND_PROMPT.format(args_placeholder=task) + model_hint, True
    if low.startswith("/windmill "):
        task = text.split(" ", 1)[1].strip()
        return WINDMILL_COMMAND_PROMPT.format(args_placeholder=task) + model_hint, True
    return text, False


def ask_oneshot(provider, prompt, root, timeout=900):
    """Run one non-interactive prompt through Claude Code or Gemini CLI (used for planning/reviewing).
    Returns {'ok': bool, 'text': str, 'error': str}."""
    exe = cli_path(provider)
    if not exe:
        return {"ok": False, "error": f"{label(provider)} isn't installed or wasn't found on this PC."}
    try:
        if provider == "claude":
            cmd = [exe, "-p", "--output-format", "json", "--tools", "Read", "Grep", "Glob",
                   "--permission-mode", "dontAsk", "--no-session-persistence"]
            r = subprocess.run(cmd, input=prompt, cwd=str(root), capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=timeout, **NO_WINDOW)
            data = {}
            if r.stdout.strip():
                try:
                    data = json.loads(r.stdout.strip().splitlines()[-1])
                except ValueError:
                    pass
            text = (data.get("result") or r.stdout or "").strip()
            if r.returncode != 0 or data.get("is_error") or not text:
                err = text or (r.stderr or r.stdout).strip()[-300:] or f"exit code {r.returncode}"
                return {"ok": False, "error": err}
            return {"ok": True, "text": text}

        if provider == "gemini":
            cmd = [exe, "-p", prompt, "--output-format", "json"]
            r = subprocess.run(cmd, cwd=str(root), capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=timeout, stdin=subprocess.DEVNULL, **NO_WINDOW)
            raw = (r.stdout or "").strip()
            text = ""
            if raw:
                try:
                    data = json.loads(raw)
                    text = (data.get("response") or data.get("result") or data.get("text") or "").strip()
                except ValueError:
                    text = raw
            if r.returncode != 0 or not text:
                err = (r.stderr or raw).strip()[-300:] or f"exit code {r.returncode}"
                return {"ok": False, "error": err}
            return {"ok": True, "text": text}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"{label(provider)} timed out."}
    except OSError as e:
        return {"ok": False, "error": str(e)}
    return {"ok": False, "error": f"Unknown provider: {provider}"}


def _emit_mcp_tool_event(tool_name, tool_input, on_event):
    """Turn Windmill MCP tool calls from Claude/Gemini into rich sub-agent UI events."""
    base = tool_name.split("__")[-1] if "__" in tool_name else tool_name
    inp = tool_input if isinstance(tool_input, dict) else {}
    if base == "spawn_local_subagent":
        on_event({
            "type": "subagent_start",
            "agent_id": inp.get("name") or "local-bot",
            "agent_name": inp.get("name") or "Local Bot",
            "model": inp.get("model") or "default local model",
            "task": str(inp.get("task") or "")[:300],
            "round": 1,
        })
    elif base == "send_subagent_feedback":
        on_event({
            "type": "subagent_review",
            "agent_id": inp.get("agent_id") or "local-bot",
            "agent_name": inp.get("agent_id") or "Local Bot",
            "verdict": "FAIL",
            "feedback": str(inp.get("feedback") or "")[:1000],
        })
    elif base == "approve_subagent":
        on_event({
            "type": "subagent_review",
            "agent_id": inp.get("agent_id") or "local-bot",
            "agent_name": inp.get("agent_id") or "Local Bot",
            "verdict": "PASS",
            "feedback": str(inp.get("note") or "Approved by CLI supervisor.")[:600],
        })


def _emit_mcp_result_event(tool_name, content_text, on_event):
    base = tool_name.split("__")[-1] if "__" in tool_name else tool_name
    if base not in ("spawn_local_subagent", "send_subagent_feedback"):
        return
    try:
        data = json.loads(content_text)
    except (ValueError, TypeError):
        return
    if isinstance(data, dict) and data.get("agent_id"):
        on_event({
            "type": "subagent_done",
            "agent_id": data["agent_id"],
            "agent_name": data.get("name") or data["agent_id"],
            "model": data.get("model") or "local",
            "status": data.get("status") or "awaiting_review",
            "round": data.get("round") or 1,
            "files_changed": data.get("files_changed") or [],
            "checks_ok": data.get("checks_ok"),
            "summary": str(data.get("summary") or "")[:800],
            "error": data.get("error"),
        })


def run_stream(provider, prompt, root, session_id=None, local_model=None, on_event=None, cancel=None):
    """Run Claude Code or Gemini CLI live in `root` with Windmill's local sub-agent MCP tools wired in.
    Streams structured events (`text`, `tool`, `result`, `subagent_start`, `subagent_review`, `subagent_done`,
    `note`, `error`) to `on_event`. Returns {'ok', 'text', 'session_id', 'error'}."""
    emit = on_event or (lambda ev: None)
    cancel = cancel or threading.Event()
    exe = cli_path(provider)
    if not exe:
        msg = f"{label(provider)} wasn't found on this PC. Open CLI Agents to set its path or install it."
        emit({"type": "error", "text": msg})
        return {"ok": False, "text": "", "session_id": session_id, "error": msg}

    full_prompt, expanded = expand_slash_command(prompt, local_model=local_model)
    if expanded:
        emit({"type": "phase", "text": f"{label(provider)} supervising Windmill local AI sub-agents…"})
    else:
        emit({"type": "activity", "text": f"Running {label(provider)}"})

    mcp_tmp = None
    try:
        if provider == "claude":
            mcp_cfg = {"mcpServers": {"windmill": mcp_server_spec(project=root)}}
            fd, mcp_tmp = tempfile.mkstemp(prefix="windmill-mcp-", suffix=".json")
            os.close(fd)
            Path(mcp_tmp).write_text(json.dumps(mcp_cfg), encoding="utf-8")

            cmd = [
                exe, "-p",
                "--output-format", "stream-json",
                "--verbose",
                "--mcp-config", mcp_tmp,
                "--permission-mode", "bypassPermissions",
            ]
            if session_id:
                cmd += ["--resume", session_id]
            proc = subprocess.Popen(
                cmd, cwd=str(root), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding="utf-8", errors="replace", **NO_WINDOW,
            )
            proc.stdin.write(full_prompt)
            proc.stdin.close()
        elif provider == "gemini":
            # Ensure Windmill MCP is available to Gemini CLI (user-level settings)
            if not plugin_status("gemini")["mcp_installed"]:
                install_plugin("gemini")
            cmd = [exe, "-p", full_prompt, "--output-format", "stream-json", "--yolo"]
            proc = subprocess.Popen(
                cmd, cwd=str(root), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding="utf-8", errors="replace", **NO_WINDOW,
            )
        else:
            return {"ok": False, "text": "", "session_id": session_id, "error": f"Unknown provider: {provider}"}

        # Watch cancel event in a background helper so Stop kills the CLI cleanly
        def _watch_cancel():
            while proc.poll() is None:
                if cancel.wait(0.2):
                    try:
                        proc.terminate()
                    except OSError:
                        pass
                    return

        threading.Thread(target=_watch_cancel, daemon=True).start()

        final_parts = []
        new_session = session_id
        pending_tools = {}  # tool_use_id -> tool_name

        for raw in iter(proc.stdout.readline, ""):
            line = raw.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except ValueError:
                final_parts.append(line + "\n")
                emit({"type": "text", "text": line + "\n"})
                continue

            if not isinstance(ev, dict):
                continue

            etype = ev.get("type")
            if ev.get("session_id"):
                new_session = ev["session_id"]

            # Claude Code stream-json format
            if etype == "assistant":
                msg = ev.get("message") or {}
                for block in msg.get("content") or []:
                    btype = block.get("type")
                    if btype == "text" and block.get("text"):
                        final_parts.append(block["text"])
                        emit({"type": "text", "text": block["text"]})
                    elif btype == "tool_use":
                        tname = block.get("name") or "tool"
                        targs = block.get("input") or {}
                        if block.get("id"):
                            pending_tools[block["id"]] = tname
                        emit({"type": "tool", "name": tname, "args": targs})
                        _emit_mcp_tool_event(tname, targs, emit)
            elif etype == "user":
                msg = ev.get("message") or {}
                for block in msg.get("content") or []:
                    if block.get("type") == "tool_result":
                        tid = block.get("tool_use_id")
                        tname = pending_tools.pop(tid, "tool")
                        c = block.get("content")
                        if isinstance(c, list):
                            text = "\n".join(x.get("text", "") for x in c if isinstance(x, dict))
                        else:
                            text = str(c or "")
                        emit({"type": "result", "name": tname, "text": text[:1500]})
                        _emit_mcp_result_event(tname, text, emit)
            elif etype == "result":
                if ev.get("is_error") and ev.get("result"):
                    emit({"type": "error", "text": str(ev["result"])[:600]})
                elif ev.get("result") and not final_parts:
                    final_parts.append(str(ev["result"]))
                    emit({"type": "text", "text": str(ev["result"])})

            # Gemini CLI stream-json format
            elif etype == "message" and ev.get("role") == "assistant" and ev.get("content"):
                final_parts.append(ev["content"])
                emit({"type": "text", "text": ev["content"]})
            elif etype == "tool_use":
                tname = ev.get("tool_name") or ev.get("name") or "tool"
                targs = ev.get("parameters") or ev.get("args") or {}
                if ev.get("tool_id"):
                    pending_tools[ev["tool_id"]] = tname
                emit({"type": "tool", "name": tname, "args": targs})
                _emit_mcp_tool_event(tname, targs, emit)
            elif etype == "tool_result":
                tid = ev.get("tool_id")
                tname = pending_tools.pop(tid, ev.get("tool_name") or "tool")
                text = str(ev.get("output") or ev.get("result") or "")
                emit({"type": "result", "name": tname, "text": text[:1500]})
                _emit_mcp_result_event(tname, text, emit)

        stderr = (proc.stderr.read() or "").strip() if proc.stderr else ""
        code = proc.wait()
        final_text = "".join(final_parts).strip()
        if cancel.is_set():
            emit({"type": "note", "text": "Stopped."})
            return {"ok": False, "text": final_text, "session_id": new_session, "error": "Stopped."}
        if code != 0 and not final_text:
            err = stderr[-500:] or f"{label(provider)} exited with code {code}"
            emit({"type": "error", "text": err})
            return {"ok": False, "text": "", "session_id": new_session, "error": err}
        return {"ok": True, "text": final_text, "session_id": new_session, "error": None}
    except OSError as e:
        emit({"type": "error", "text": str(e)})
        return {"ok": False, "text": "", "session_id": session_id, "error": str(e)}
    finally:
        if mcp_tmp:
            try:
                os.unlink(mcp_tmp)
            except OSError:
                pass
