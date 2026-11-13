"""The Windmill agent: a Claude-Code-style loop (think -> use tools -> check) with the watchdog built in."""
import datetime
import json
import os
import platform
import sys
from pathlib import Path

from . import checks, config, design, ui
from .debugger import TAG, Watchdog
from .llm import LLM, Cancelled, LLMError, est_tokens
from .sessions import Session
from .tools import Tools, tool_specs

SYSTEM = """You are Windmill, an expert software engineer AND product designer, working directly in the user's
project on their own computer. The user may not be technical: they describe what they want, you build it
completely and well, and you explain results in plain, friendly language.

ENVIRONMENT: {os} | shell for the run tool: {shell} | project folder: {root} | today: {date}

HOW YOU WORK
1. Orient cheaply: project_map first. Then read only what you need (find_code / read_file with line ranges).
   Never guess what a file contains.
2. Unfamiliar library, API or pattern? Call references(topic) BEFORE writing code. Dependency signatures it
   returns are for the exact versions installed and override your memory.
3. For anything non-trivial, write a short numbered plan in your reply, then execute it one step at a time.
4. Make changes with edit_file (exact snippets) or write_file (new files). Write complete, working code.
5. After changes, call check. You are NOT done until check passes. Fix causes, not symptoms.
6. Any UI work (web pages, app screens, game HUD/menus/inventory): call design_brief first and follow it
   exactly - its colours, fonts, spacing and signature moves. Real copy, every state, responsive, accessible.
7. Finish with a short plain-language summary: what you built, how to see/run it, anything the user should know.

NON-NEGOTIABLE
- No placeholders, stubs, TODOs, "...", "simplified version", lorem ipsum, fake data presented as real.
  If something is in scope, finish it. If it truly can't be done, say so plainly.
- Don't wander: only change what the task needs. Don't delete or weaken tests to get green.
- Decide technical questions yourself. Use ask_user only for product decisions you can't reasonably infer.
- Messages starting with {tag} come from Windmill's automatic debugger - treat them as hard requirements.
{extra}"""


class Printer:
    """How the agent shows its work in the terminal."""

    def __init__(self, show_thinking=False, quiet=False):
        self.show_thinking, self.quiet = show_thinking, quiet
        self.spin = None
        self.in_text = False

    def think_start(self):
        if not self.quiet and not self.spin:
            self.spin = ui.Spinner("thinking")
            self.spin.__enter__()

    def think_stop(self):
        if self.spin:
            self.spin.__exit__()
            self.spin = None

    def think(self, t):
        if self.show_thinking and not self.quiet:
            self.think_stop()
            sys.stdout.write(ui.dim(t))
            sys.stdout.flush()
        elif self.spin:
            self.spin.extra = ""

    def text(self, t):
        if self.quiet:
            return
        self.think_stop()
        if not self.in_text:
            sys.stdout.write("\n")
            self.in_text = True
        sys.stdout.write(t)
        sys.stdout.flush()

    def end_text(self):
        if self.in_text and not self.quiet:
            sys.stdout.write("\n")
        self.in_text = False

    def tool(self, name, args):
        self.think_stop()
        self.end_text()
        if self.quiet:
            return
        a = args or {}
        label = {
            "read_file": ("→", f"read {a.get('path', '')}" + (f":{a.get('start_line')}-{a.get('end_line', '')}" if a.get("start_line") else "")),
            "write_file": ("+", f"write {a.get('path', '')}"),
            "edit_file": ("✎", f"edit {a.get('path', '')}"),
            "list_files": ("▤", f"list {a.get('path', '.')}"),
            "search": ("⌕", f"search /{a.get('pattern', '')}/"),
            "run": ("▶", f"run {a.get('command', '')}" + (" (background)" if a.get("background") else "")),
            "project_map": ("◇", "project map"), "find_code": ("⌕", f"find {a.get('name', '')}"),
            "references": ("◇", f"library: {a.get('topic', '')}"), "check": ("◆", "checking the project"),
            "design_brief": ("◇", "design brief"), "ask_user": ("?", "question for you"),
            "phone": ("▣", f"phone: {a.get('action', '')}"),
        }.get(name, ("•", name))
        print(f"  {ui.cyan(label[0])} {ui.dim(label[1][:110])}")

    def result(self, name, out):
        if self.quiet:
            return
        first = out.splitlines()[0] if out else ""
        if out.startswith(("ERROR", "BLOCKED", "TIMEOUT")):
            print(f"    {ui.yellow(first[:140])}")
        elif name == "check":
            ls = out.splitlines()
            detail = next((l for l in ls[1:] if not l.endswith("FAILED:") and l.strip()), ls[1] if len(ls) > 1 else "")
            print(f"    {ui.green('PASS') if out.startswith('PASS') else ui.red('FAIL')} {ui.dim(detail.strip()[:120])}")
        elif name == "run":
            print(f"    {ui.dim(first[:120])}")

    def debugger(self, msg):
        self.end_text()
        if not self.quiet:
            lines = [l for l in msg.replace(TAG, "").strip().splitlines() if l.strip()]
            print(f"  {ui.magenta('⚑ debugger:')} {lines[0][:150]}")
            if len(lines) > 1 and not lines[0].startswith("Checks FAILED"):
                print(f"    {ui.dim(lines[1].strip()[:150])}")

    def note(self, msg):
        self.end_text()
        if not self.quiet:
            print(f"  {ui.gray(msg)}")


class Agent:
    def __init__(self, root, cfg=None, session=None, interactive=True, printer=None, critic=True, max_steps=None,
                 llm=None):
        self.root = Path(root).resolve()
        self.cfg = cfg or config.load()
        self.llm = llm or LLM(self.cfg)
        self.interactive = interactive
        self.p = printer or Printer(self.cfg.get("show_thinking"), quiet=not interactive)
        self.confirm = ui.confirm      # the app replaces these with its own dialogs
        self.ask_fn = None
        self.plugins = None
        if interactive:
            try:
                from . import mcp
                if mcp.load()["mcpServers"]:
                    self.plugins = mcp.Plugins()
                    for name, err in self.plugins.errors.items():
                        ui.warn(f"Plugin '{name}' didn't start: {err[:150]}")
            except Exception as e:  # noqa: BLE001
                ui.warn(f"(plugins unavailable: {e})")
        self.tools = Tools(self.root, interactive, self.cfg.get("permission", "auto"),
                           ask=self._ask if interactive else None, plugins=self.plugins)
        self.session = session or Session.new(self.root)
        self.critic_on = critic
        self.max_steps = max_steps or self.cfg.get("max_steps", 80)
        self.messages = [{"role": "system", "content": self.system_prompt()}]
        self.messages += [m for m in self.session.messages if m.get("role") != "system"]
        self.ensure_git()

    # ---------------------------------------------------------- setup
    def system_prompt(self):
        extra = []
        rules = self.root / "AGENTS.md"
        if rules.exists():
            extra.append("PROJECT RULES (AGENTS.md):\n" + rules.read_text(encoding="utf-8", errors="replace")[:3500])
        brief = self.root / "BRIEF.md"
        if brief.exists():
            extra.append("PRODUCT BRIEF (BRIEF.md):\n" + brief.read_text(encoding="utf-8", errors="replace")[:2500])
        if design.load(self.root):
            extra.append("This project has a design brief - call design_brief before any UI work.")
        k = checks.kinds(self.root)
        if k:
            extra.append(f"Project type detected: {', '.join(k)}.")
        try:
            from . import skills
            idx = skills.prompt_index(self.root)
            if idx:
                extra.append(idx)
        except Exception:  # noqa: BLE001
            pass
        return SYSTEM.format(os=f"{platform.system()} {platform.release()}", shell=Tools.shell_name(), root=self.root,
                             date=datetime.date.today().isoformat(), tag=TAG, extra="\n\n".join(extra))

    def ensure_git(self):
        if (self.root / ".git").exists():
            return
        import subprocess
        subprocess.run(["git", "init", "-q"], cwd=self.root, capture_output=True)
        gi = self.root / ".gitignore"
        if not gi.exists():
            gi.write_text("target/\nnode_modules/\ndist/\n.venv/\n__pycache__/\n.env\n", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=self.root, capture_output=True)
        subprocess.run(["git", "-c", "user.name=Windmill", "-c", "user.email=windmill@localhost", "commit", "-q",
                        "--allow-empty", "-m", "windmill: start"], cwd=self.root, capture_output=True)

    def _ask(self, q):
        if self.ask_fn:
            return self.ask_fn(q)
        self.p.think_stop()
        self.p.end_text()
        print()
        return ui.ask(ui.bold(q))

    def add(self, msg):
        self.messages.append(msg)
        self.session.add(msg)

    # ---------------------------------------------------------- context management
    def budget(self):
        return int(self.cfg.get("ctx", 65536) * 0.55)

    def compact(self, force=False):
        total = est_tokens(self.messages)
        if total < self.budget() and not force:
            return
        keep = 10
        # 1) cheap: shrink old tool outputs
        for m in self.messages[1:-keep]:
            if m.get("role") == "tool" and len(m.get("content", "")) > 300:
                m["content"] = m["content"][:250] + "\n[... old output removed to save memory ...]"
        if est_tokens(self.messages) < self.budget() * 0.8 and not force:
            self.session.replace_all(self.messages[1:])
            return
        # 2) summarise the older half of the conversation
        old, recent = self.messages[1:-keep], self.messages[-keep:]
        while recent and recent[0].get("role") == "tool":  # don't orphan tool results
            old.append(recent.pop(0))
        transcript = "\n".join(f"{m['role']}: {str(m.get('content', ''))[:1500]}" for m in old)
        self.p.note("(compacting conversation memory...)")
        try:
            summary = self.llm.simple(
                "Summarise this coding session so work can continue seamlessly. Keep: the user's goals and "
                "preferences, decisions made, files changed and why, what's done, what's left, open problems. "
                "Be specific (file names, function names). Max 400 words.", transcript, temperature=0.2, max_tokens=1200)
        except LLMError:
            summary = transcript[-3000:]
        self.messages = [self.messages[0], {"role": "user", "content": f"[WINDMILL SUMMARY of earlier conversation]\n{summary}"},
                         {"role": "assistant", "content": "Understood - continuing from that summary."}] + recent
        self.session.replace_all(self.messages[1:])

    # ---------------------------------------------------------- the loop
    def send(self, text):
        """Handle one user message until the model is done (or stuck). Returns final text."""
        self.add({"role": "user", "content": text})
        wd = Watchdog(self.root, self.llm, self.tools, self.interactive, log=self.p.note)
        self.tools.changed = set()
        steps, critic_rounds, gated_since_change = 0, 0, False
        ui_task = design.is_ui(text)
        while True:
            self.compact()
            self.p.think_start()
            try:
                r = self.llm.chat(self.messages, tools=self.tools.specs(), on_text=self.p.text, on_think=self.p.think)
            except (KeyboardInterrupt, Cancelled):
                self.p.think_stop()
                self.p.end_text()
                self.add({"role": "assistant", "content": "(interrupted by user)"})
                return "(interrupted)"
            except LLMError as e:
                self.p.think_stop()
                ui.err(str(e))
                return f"(error: {e})"
            self.p.think_stop()
            self.p.end_text()
            msg = {"role": "assistant", "content": r["content"]}
            if r["tool_calls"]:
                msg["tool_calls"] = [{"id": c["id"], "type": "function",
                                      "function": {"name": c["name"], "arguments": json.dumps(c["arguments"])}}
                                     for c in r["tool_calls"]]
            self.add(msg)

            if r["tool_calls"]:
                injections = []
                for c in r["tool_calls"]:
                    if self.llm.cancel is not None and self.llm.cancel.is_set():
                        self.add({"role": "tool", "tool_call_id": c["id"], "content": "Stopped by the user."})
                        continue
                    self.p.tool(c["name"], c["arguments"])
                    try:
                        out = self.tools.call(c["name"], c["arguments"])
                    except KeyboardInterrupt:
                        out = "INTERRUPTED by the user."
                    self.p.result(c["name"], out)
                    self.add({"role": "tool", "tool_call_id": c["id"], "content": out})
                    if c["name"] in ("write_file", "edit_file") and not out.startswith("ERROR"):
                        gated_since_change = False
                        if any(str(f).endswith(design.UI_EXT) for f in self.tools.changed):
                            ui_task = True
                    if c["name"] == "check" and out.startswith("PASS"):
                        wd.checkpoint()
                        gated_since_change = True
                    inj = wd.after_tool(c["name"], c["arguments"], out)
                    if inj:
                        injections.append(inj)
                if injections:
                    for inj in injections:
                        self.p.debugger(inj)
                    self.add({"role": "user", "content": "\n\n".join(injections)})
                steps += 1
                if self.llm.cancel is not None and self.llm.cancel.is_set():
                    self.add({"role": "assistant", "content": "(stopped by user)"})
                    return "(stopped)"
                if steps >= self.max_steps:
                    if self.interactive and self.confirm(f"Windmill has taken {steps} actions on this. Keep going?", True):
                        steps = 0
                        continue
                    return r["content"] or "(paused after many steps)"
                continue

            # The model ended its turn. If it changed code, verify before accepting "done".
            if self.tools.changed and not gated_since_change:
                self.p.tool("check", {})
                ok, out, give_up = wd.gate()
                raw = getattr(wd, "last_raw", out)
                self.p.result("check", ("PASS\n" if ok else "FAIL\n") + "\n".join(l for l in raw.splitlines() if l.strip()))
                gated_since_change = True
                if give_up:
                    wd.rollback()
                    summary = wd.plain_summary(out)
                    self.add({"role": "assistant", "content": summary})
                    if self.interactive:
                        print("\n" + ui.yellow(summary))
                    return summary
                if not ok:
                    self.p.debugger(out if out.startswith(TAG) else f"{TAG} checks failed")
                    self.add({"role": "user", "content": out if out.startswith(TAG) else
                              f"{TAG} Checks FAILED - you are not done. Fix the cause, then run check:\n{out}"})
                    continue
                if self.critic_on and critic_rounds < 2:
                    self.p.note("(fresh-eyes review of the changes...)")
                    passed, review = wd.critic(text, ui=ui_task)
                    critic_rounds += 1
                    if not passed:
                        self.p.debugger(f"{TAG} reviewer found problems")
                        self.add({"role": "user", "content": f"{TAG} An independent review of your changes found "
                                                             f"problems. Fix every real one, then check again:\n{review}"})
                        gated_since_change = False
                        continue
                wd.checkpoint("windmill: " + text[:60].replace("\n", " "))
            return r["content"]


def run_headless(root, message, read=(), edit=(), name="task", run_path=None, critic=False, max_steps=60):
    """Used by the pipeline / best-of-N: one instruction, no user interaction. Returns 0 on success."""
    root = Path(root)
    parts = [message]
    for f in read:
        try:
            parts.append(f"\n--- {Path(f).name} (read-only reference) ---\n" + Path(f).read_text(encoding="utf-8")[:12000])
        except OSError:
            pass
    if edit:
        parts.append("\nFiles you'll most likely edit: " + ", ".join(Path(e).resolve().relative_to(root.resolve()).as_posix()
                                                                     for e in edit if Path(e).exists()))
    agent = Agent(root, interactive=False, critic=critic, max_steps=max_steps,
                  session=Session.new(root, title=f"[auto] {name}"))
    agent.p = Printer(quiet=True)
    try:
        final = agent.send("\n".join(parts))
    except LLMError as e:
        final = f"(error: {e})"
    if run_path:
        lines = [f"# {name}\n"]
        for m in agent.messages[1:]:
            c = m.get("content") or ""
            if m.get("tool_calls"):
                c += "\n" + "\n".join(f"-> {t['function']['name']}({t['function']['arguments'][:300]})" for t in m["tool_calls"])
            lines.append(f"## {m['role']}\n{c[:4000]}\n")
        (Path(run_path) / f"{name}.history.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"  {ui.dim(name + ': ' + (final or '').strip().splitlines()[0][:120] if final and final.strip() else name + ': done')}")
    return 0
