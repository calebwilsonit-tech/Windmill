"""Local AI sub-agents: spawn local-model worker bots that build in chunks while a CLI agent
(Claude Code or Gemini CLI) supervises and reviews their work continuously.

Sub-agents run on Windmill's installed local GGUF models (via llama-server + wm.agent.Agent),
never spending cloud sub-agent tokens. Each sub-agent keeps its own conversation memory so when
the CLI supervisor sends review feedback, the same local bot fixes its own work and re-checks.
"""
import json
import os
import re
import subprocess
import threading
import time
from pathlib import Path

from . import checks, config, models, server
from .agent import Agent
from .sessions import Session

STATE_DIR = config.HOME / "subagents"
LATEST_FILE = STATE_DIR / "latest.json"
_MODEL_LOCK = threading.Lock()
NO_WINDOW = {"creationflags": 0x08000000} if os.name == "nt" else {}


def resolve_local_model(query, cfg=None):
    """Resolve a user/CLI model hint (path, filename, or partial name) to a discovered local model dict.
    Returns None when no specific model was requested (caller uses Windmill's default coding model)."""
    if not query:
        return None
    cfg = cfg or config.load()
    q = str(query).strip()
    if not q or q.lower() in ("default", "auto", "coding", "current"):
        return None
    p = Path(q)
    if p.is_file():
        return {"path": str(p), "name": p.stem}
    found = models.discover(cfg)
    ql = q.lower()
    for m in found:
        if m["name"].lower() == ql or Path(m["path"]).name.lower() == ql:
            return m
    for m in found:
        if ql in m["name"].lower() or ql in Path(m["path"]).name.lower():
            return m
    return None


def list_local_models(cfg=None):
    """Compact summary of installed local models for CLI agents (Claude Code / Gemini CLI)."""
    cfg = cfg or config.load()
    found = models.discover(cfg)
    cur = cfg.get("model_path") or ""
    loaded = server.loaded_path() or ""
    out = []
    for m in found:
        out.append({
            "name": m["name"],
            "path": m["path"],
            "size_gb": m.get("size_gb"),
            "selected": bool(cur and os.path.normcase(m["path"]) == os.path.normcase(cur)),
            "loaded": bool(loaded and os.path.normcase(m["path"]) == os.path.normcase(loaded)),
            "vision": bool(models.mmproj_for(m["path"])),
            "source": m.get("source") or "",
        })
    return out


def _git_status_files(root):
    try:
        r = subprocess.run(["git", "status", "--porcelain"], cwd=root, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=15, **NO_WINDOW)
        if r.returncode != 0:
            return []
        files = []
        for line in r.stdout.splitlines():
            if len(line) >= 4:
                path = line[3:].strip()
                if " -> " in path:
                    path = path.split(" -> ", 1)[1].strip()
                files.append(path.strip('"'))
        return files
    except (OSError, subprocess.TimeoutExpired):
        return []


def _git_diff(root, max_chars=12000):
    """Capture working-tree diff (tracked + summary of untracked files) so the CLI supervisor can inspect it."""
    parts = []
    try:
        r = subprocess.run(["git", "--no-pager", "diff"], cwd=root, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=20, **NO_WINDOW)
        if r.returncode == 0 and r.stdout.strip():
            parts.append(r.stdout.strip())
    except (OSError, subprocess.TimeoutExpired):
        pass
    try:
        r = subprocess.run(["git", "ls-files", "--others", "--exclude-standard"], cwd=root, capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=15, **NO_WINDOW)
        untracked = [l.strip() for l in r.stdout.splitlines() if l.strip()] if r.returncode == 0 else []
        for rel in untracked[:10]:
            fp = Path(root) / rel
            if fp.is_file() and fp.stat().st_size < 40_000:
                body = fp.read_text(encoding="utf-8", errors="replace")[:2500]
                parts.append(f"--- /dev/null\n+++ b/{rel}\n{body}")
    except (OSError, subprocess.TimeoutExpired):
        pass
    diff = "\n\n".join(parts)
    if len(diff) > max_chars:
        diff = diff[:max_chars] + f"\n\n[... diff truncated at {max_chars} chars; read the files directly for full content ...]"
    return diff


class SubAgentPrinter:
    """Captures a local sub-agent's activity and forwards structured events to the parent UI/manager."""

    def __init__(self, agent_id, name, on_event=None):
        self.agent_id = agent_id
        self.name = name
        self.on_event = on_event
        self.show_thinking = False
        self.quiet = True
        self.tool_calls = []
        self.notes = []

    def _emit(self, kind, **data):
        if self.on_event:
            try:
                self.on_event({"type": kind, "agent_id": self.agent_id, "agent_name": self.name, **data})
            except Exception:  # noqa: BLE001
                pass

    def think_start(self):
        pass

    def think_stop(self):
        pass

    def think(self, t):
        pass

    def text(self, t):
        pass

    def end_text(self):
        pass

    def tool(self, name, args):
        brief = {k: (v if len(str(v)) < 200 else str(v)[:200] + "...") for k, v in (args or {}).items()}
        self.tool_calls.append({"tool": name, "args": brief})
        self._emit("subagent_tool", tool=name, args=brief)

    def result(self, name, out):
        first = (out or "").splitlines()[0][:200] if out else ""
        if self.tool_calls:
            self.tool_calls[-1]["result"] = first
        self._emit("subagent_result", tool=name, text=first)

    def debugger(self, msg):
        line = (msg or "").strip().splitlines()[0][:240] if msg else ""
        self.notes.append(f"debugger: {line}")
        self._emit("subagent_note", text=f"debugger: {line}")

    def note(self, msg):
        self.notes.append(str(msg)[:240])
        self._emit("subagent_note", text=str(msg)[:240])


SUBAGENT_WRAPPER = """You are a focused LOCAL AI sub-agent ({name}) working inside the project at {root}.
A senior supervising engineer (CLI agent) delegated this specific chunk to you and will review your work
as soon as you finish.

YOUR ASSIGNED TASK:
{task}
{scope_note}

RULES FOR THIS SUB-AGENT:
1. Stay focused strictly on your assigned task — do not modify unrelated files.
2. Read the relevant files first before editing; never guess.
3. Write complete, working code (no placeholders, stubs, or TODOs).
4. Run `check` when your edits are done and fix any build/test errors you introduced.
5. End with a concise summary listing the exact files you created/edited and what you verified."""


FEEDBACK_WRAPPER = """SUPERVISOR REVIEW FEEDBACK (Round {round}):
The supervising CLI agent reviewed your work against the user's instructions and requested these fixes:

{feedback}

Apply every requested fix accurately, run `check` to verify everything builds and passes, and summarize what you fixed."""


class SubAgent:
    """One local AI worker bot with persistent conversation state across review/fix rounds."""

    def __init__(self, agent_id, name, task, root, model_path=None, model_name=None,
                 files_scope=None, max_steps=None, on_event=None, cancel=None, agent_factory=None):
        self.id = agent_id
        self.name = name or agent_id
        self.task = task
        self.root = Path(root).resolve()
        self.model_path = model_path
        self.model_name = model_name or (Path(model_path).stem if model_path else None)
        self.files_scope = list(files_scope or [])
        self.max_steps = max_steps
        self.on_event = on_event
        self.cancel = cancel or threading.Event()
        self._agent_factory = agent_factory  # optional test hook

        self.status = "queued"
        self.round = 0
        self.created = time.time()
        self.updated = self.created
        self.summary = ""
        self.files_changed = []
        self.diff = ""
        self.checks_ok = None
        self.checks_output = ""
        self.error = None
        self.review_history = []
        self.printer = SubAgentPrinter(self.id, self.name, on_event=on_event)
        self._agent = None

    def _emit(self, kind, **data):
        if self.on_event:
            try:
                self.on_event({"type": kind, "agent_id": self.id, "agent_name": self.name, **data})
            except Exception:  # noqa: BLE001
                pass

    def _ensure_agent(self, cfg):
        if self._agent is not None:
            return self._agent
        if self._agent_factory:
            self._agent = self._agent_factory(self, cfg)
            return self._agent
        s = Session.new(self.root, title=f"[subagent] {self.name}: {self.task[:50]}")
        steps = self.max_steps or int(cfg.get("subagent_max_steps") or 35)
        self._agent = Agent(self.root, cfg, session=s, interactive=False, printer=self.printer,
                            critic=True, max_steps=steps, mode="code")
        self._agent.llm.cancel = self.cancel
        return self._agent

    def _run_turn(self, prompt, status_during="running"):
        cfg = config.load()
        self.status = status_during
        self.updated = time.time()
        self.error = None

        want_model = self.model_path
        if not self.model_name:
            eff = server.effective(cfg, "code", want_model)
            self.model_name = eff.get("model_name") or Path(eff.get("model_path") or "local").stem

        self._emit("subagent_start" if self.round == 1 else "subagent_fix",
                   round=self.round, model=self.model_name, task=self.task[:300], status=self.status)

        with _MODEL_LOCK:
            if self.cancel.is_set():
                self.status = "stopped"
                self.error = "Stopped by user."
                return self.report()

            if not self._agent_factory:
                if not server.ensure(cfg, role="code", model_path=want_model):
                    self.status = "failed"
                    self.error = "Local AI model server could not start. Check `windmill doctor` or select a model."
                    self._emit("subagent_done", status=self.status, error=self.error, round=self.round)
                    return self.report()

            before_files = set(_git_status_files(self.root))
            try:
                agent = self._ensure_agent(cfg)
                if not self._agent_factory:
                    with server.hold("code"):
                        final = agent.send(prompt)
                else:
                    final = agent.send(prompt)
                self.summary = (final or "").strip()
            except Exception as e:  # noqa: BLE001
                self.status = "failed"
                self.error = f"{type(e).__name__}: {e}"
                self.updated = time.time()
                self._emit("subagent_done", status=self.status, error=self.error, round=self.round)
                return self.report()

            after_files = _git_status_files(self.root)
            self.files_changed = sorted(set(after_files) | (set(after_files) - before_files))
            self.diff = _git_diff(self.root)
            try:
                ok, out = checks.run(self.root)
                self.checks_ok = bool(ok)
                self.checks_output = (out or "")[:4000]
            except Exception as e:  # noqa: BLE001
                self.checks_ok = False
                self.checks_output = f"Check error: {e}"

            self.status = "stopped" if self.cancel.is_set() else "awaiting_review"
            self.updated = time.time()
            self._emit("subagent_done", status=self.status, round=self.round, model=self.model_name,
                       files_changed=self.files_changed, checks_ok=self.checks_ok, summary=self.summary[:800])
            return self.report()

    def start(self):
        self.round = 1
        scope = ""
        if self.files_scope:
            scope = "\nFOCUS FILES:\n" + "\n".join(f"- {f}" for f in self.files_scope)
        prompt = SUBAGENT_WRAPPER.format(name=self.name, root=self.root, task=self.task, scope_note=scope)
        return self._run_turn(prompt, status_during="running")

    def apply_feedback(self, feedback):
        self.round += 1
        self.review_history.append({"round": self.round - 1, "verdict": "FAIL", "feedback": feedback, "at": time.time()})
        self._emit("subagent_review", round=self.round - 1, verdict="FAIL", feedback=feedback[:1000])
        prompt = FEEDBACK_WRAPPER.format(round=self.round, feedback=feedback)
        return self._run_turn(prompt, status_during="fixing")

    def approve(self, note=""):
        self.status = "passed"
        self.updated = time.time()
        self.review_history.append({"round": self.round, "verdict": "PASS", "feedback": note or "Approved", "at": time.time()})
        self._emit("subagent_review", round=self.round, verdict="PASS", feedback=note or "Approved by CLI supervisor.")
        return self.report()

    def report(self, include_diff=True):
        out = {
            "agent_id": self.id,
            "name": self.name,
            "model": self.model_name or "default",
            "task": self.task,
            "status": self.status,
            "round": self.round,
            "summary": self.summary,
            "files_changed": self.files_changed,
            "checks_ok": self.checks_ok,
            "checks_output": self.checks_output,
            "tool_calls": self.printer.tool_calls[-20:],
            "review_history": self.review_history,
            "error": self.error,
            "updated": self.updated,
        }
        if include_diff:
            out["diff"] = self.diff
        return out


class SubAgentManager:
    """Tracks local sub-agents for a session or MCP server and persists live state for the Windmill GUI."""

    def __init__(self, root=None, on_event=None, cancel=None, agent_factory=None, persist=True):
        self.root = Path(root or config.project_root()).resolve()
        self.on_event = on_event
        self.cancel = cancel or threading.Event()
        self._agent_factory = agent_factory
        self._persist = persist
        self.agents = {}
        self._seq = 0
        self._lock = threading.Lock()

    def _save_state(self):
        if not self._persist:
            return
        try:
            STATE_DIR.mkdir(parents=True, exist_ok=True)
            data = {
                "project": str(self.root),
                "updated": time.time(),
                "agents": [a.report(include_diff=False) for a in self.agents.values()],
            }
            tmp = LATEST_FILE.with_name(f"{LATEST_FILE.name}.{os.getpid()}.tmp")
            tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
            os.replace(tmp, LATEST_FILE)
        except OSError:
            pass

    def spawn(self, name, task, model=None, project_dir=None, files_scope=None, max_steps=None):
        cfg = config.load()
        target_root = Path(project_dir).resolve() if project_dir else self.root
        resolved = resolve_local_model(model, cfg)
        model_path = resolved["path"] if resolved else None
        model_name = resolved["name"] if resolved else None
        with self._lock:
            self._seq += 1
            aid = f"bot-{self._seq}"
            bot = SubAgent(
                agent_id=aid,
                name=name or f"Local Bot {self._seq}",
                task=task,
                root=target_root,
                model_path=model_path,
                model_name=model_name,
                files_scope=files_scope,
                max_steps=max_steps,
                on_event=self.on_event,
                cancel=self.cancel,
                agent_factory=self._agent_factory,
            )
            self.agents[aid] = bot
        self._save_state()
        rep = bot.start()
        self._save_state()
        return rep

    def feedback(self, agent_id, feedback):
        bot = self.agents.get(agent_id)
        if not bot:
            return {"error": f"Unknown sub-agent '{agent_id}'. Known: {', '.join(self.agents) or 'none'}"}
        rep = bot.apply_feedback(feedback)
        self._save_state()
        return rep

    def approve(self, agent_id, note=""):
        bot = self.agents.get(agent_id)
        if not bot:
            return {"error": f"Unknown sub-agent '{agent_id}'."}
        rep = bot.approve(note)
        self._save_state()
        return rep

    def status(self, agent_id=None, include_diff=True):
        if agent_id:
            bot = self.agents.get(agent_id)
            return bot.report(include_diff=include_diff) if bot else {"error": f"Unknown sub-agent '{agent_id}'."}
        return {"project": str(self.root), "agents": [a.report(include_diff=False) for a in self.agents.values()]}

    def stop_all(self):
        self.cancel.set()


def latest_state():
    """Read the last persisted sub-agent snapshot (including bots spawned from external Claude/Gemini CLI)."""
    try:
        return json.loads(LATEST_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"project": "", "updated": 0, "agents": []}


# ------------------------------------------------------------------ structured continuous supervision
PLAN_PROMPT = """You are the lead architect supervising LOCAL AI sub-agents in this project.
Break the user's request into 1 to 5 small, concrete, ordered build chunks that local AI sub-agents
will implement one by one while you review each chunk right behind them.

USER REQUEST:
{goal}

Respond with ONLY a JSON array of objects, no markdown fences or extra text:
[
  {{"name": "short bot role name", "task": "clear, self-contained instructions for this local sub-agent", "files": ["optional/file/hints"]}}
]"""

SUPERVISE_REVIEW_PROMPT = """You are supervising a LOCAL AI sub-agent ({name}, round {round}) against the user's original instructions.

ORIGINAL USER INSTRUCTIONS:
{goal}

SUB-AGENT'S ASSIGNED CHUNK:
{task}

SUB-AGENT'S SUMMARY:
{summary}

PROJECT CHECKS: {checks_status}
{checks_output}

GIT DIFF PRODUCED BY SUB-AGENT:
{diff}

Inspect the work carefully (you may also read project files if needed).
Check whether this chunk accurately satisfies the instructions, has no bugs/stubs/missing pieces, and passes checks.

Respond in markdown with:
## Review
(brief concrete assessment)

## Fixes needed
(exact file, function, and change the local sub-agent must make - or "None" if approved)

Final line, exactly one of:
VERDICT: PASS
VERDICT: FAIL"""


def _parse_plan(text, fallback_goal):
    """Extract a JSON task list from the CLI supervisor's plan output."""
    m = re.search(r"\[[\s\S]*\]", text or "")
    if m:
        try:
            items = json.loads(m.group(0))
            if isinstance(items, list) and items:
                clean = []
                for i, it in enumerate(items[:6], 1):
                    if isinstance(it, dict) and it.get("task"):
                        clean.append({
                            "name": str(it.get("name") or f"Worker {i}")[:48],
                            "task": str(it["task"]),
                            "files": [str(f) for f in (it.get("files") or []) if f],
                        })
                if clean:
                    return clean
        except ValueError:
            pass
    return [{"name": "Local Builder", "task": fallback_goal, "files": []}]


def _verdict_pass(text):
    v = re.findall(r"VERDICT:\s*(PASS|FAIL)", text or "", re.I)
    return bool(v) and v[-1].upper() == "PASS"


def supervise_goal(goal, root, provider="claude", model=None, max_rounds=None,
                   on_event=None, cancel=None, cli_ask_fn=None, agent_factory=None):
    """Continuous supervision loop:
    1. CLI agent (Claude Code or Gemini CLI) plans the chunks.
    2. For each chunk, a local AI sub-agent builds it using Windmill's installed local model.
    3. Immediately after each chunk, the CLI agent inspects the diff & checks against `goal`.
    4. If VERDICT: FAIL, feedback is sent back to the SAME local sub-agent to fix (up to `max_rounds`).
    5. Continues until all chunks pass or max_rounds is reached."""
    from . import cli_agents
    cfg = config.load()
    max_rounds = int(max_rounds or cfg.get("subagent_max_rounds") or 3)
    cancel = cancel or threading.Event()
    ask_cli = cli_ask_fn or (lambda prompt: cli_agents.ask_oneshot(provider, prompt, root))
    emit = on_event or (lambda ev: None)

    emit({"type": "phase", "text": f"{cli_agents.label(provider)} is planning local sub-agent chunks…"})
    plan_res = ask_cli(PLAN_PROMPT.format(goal=goal))
    if not plan_res.get("ok"):
        err = plan_res.get("error") or f"{provider} failed to plan."
        emit({"type": "error", "text": err})
        return {"ok": False, "error": err, "agents": []}

    chunks = _parse_plan(plan_res.get("text", ""), goal)
    emit({"type": "note", "text": f"Planned {len(chunks)} local sub-agent chunk(s): " + ", ".join(c["name"] for c in chunks)})

    mgr = SubAgentManager(root=root, on_event=on_event, cancel=cancel, agent_factory=agent_factory)
    results = []

    for idx, ch in enumerate(chunks, 1):
        if cancel.is_set():
            break
        emit({"type": "step", "text": f"Chunk {idx}/{len(chunks)}: {ch['name']}"})
        rep = mgr.spawn(name=ch["name"], task=ch["task"], model=model, files_scope=ch.get("files"))
        aid = rep["agent_id"]

        for rnd in range(1, max_rounds + 1):
            if cancel.is_set() or rep.get("status") == "failed":
                break
            emit({"type": "activity", "text": f"{cli_agents.label(provider)} is reviewing {ch['name']} (round {rnd})"})
            rev_prompt = SUPERVISE_REVIEW_PROMPT.format(
                name=ch["name"],
                round=rnd,
                goal=goal,
                task=ch["task"],
                summary=rep.get("summary") or "(no summary)",
                checks_status="PASS" if rep.get("checks_ok") else "FAIL",
                checks_output=(rep.get("checks_output") or "")[:2000],
                diff=rep.get("diff") or "(no git diff)",
            )
            rev = ask_cli(rev_prompt)
            if not rev.get("ok"):
                emit({"type": "warn", "text": f"Supervisor review skipped: {rev.get('error')}"})
                break
            review_text = rev.get("text") or ""
            if _verdict_pass(review_text) and rep.get("checks_ok") is not False:
                rep = mgr.approve(aid, review_text[:800])
                break
            if rnd >= max_rounds:
                emit({"type": "subagent_review", "agent_id": aid, "agent_name": ch["name"],
                      "round": rnd, "verdict": "FAIL", "feedback": review_text[:1000]})
                emit({"type": "warn", "text": f"{ch['name']} reached max review rounds ({max_rounds})."})
                break
            rep = mgr.feedback(aid, review_text)

        results.append(rep)

    passed = sum(1 for r in results if r.get("status") == "passed")
    summary = f"Supervised build finished: {passed}/{len(results)} local sub-agent chunk(s) passed review."
    emit({"type": "ok" if passed == len(results) else "warn", "text": summary})
    return {"ok": passed == len(results), "summary": summary, "agents": results}
