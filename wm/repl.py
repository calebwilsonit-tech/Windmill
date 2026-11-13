"""Interactive chat (like Claude Code) with slash commands and easy chat recall."""
import os
import subprocess
import sys
import time

from . import checks, config, server, sessions, ui
from .agent import Agent
from .sessions import Session

HELP = [
    ("/help", "this list"),
    ("/new", "start a fresh chat"),
    ("/history [words]", "list past chats (optionally search them)"),
    ("/resume <n | words>", "switch to a past chat by number or by words in it"),
    ("/rename <title>", "rename this chat"),
    ("/export", "save this chat as a Markdown file in the project"),
    ("/check", "run all checks (build, tests, sloppy-code guard)"),
    ("/debug [problem]", "debugger mode: reproduce, find root cause, fix, add a test"),
    ("/undo", "undo Windmill's last change"),
    ("/diff", "what changed in this chat"),
    ("/run", "run your app"),
    ("/plan <task>", "big task: plan -> verified steps -> review -> mutation tests"),
    ("/best <task>", "hard task: several independent attempts, keep the best"),
    ("/design", "show or change the project's look & feel"),
    ("/ref <topic>", "search the reference library"),
    ("/map", "project map"),
    ("/model", "switch AI model"),
    ("/think", "show/hide the model's thinking"),
    ("/ask", "toggle: ask before running commands"),
    ("/compact", "shrink the conversation memory now"),
    ("/clear", "clear the screen"),
    ("/exit", "leave (chat is saved automatically)"),
]


def read_input(prompt):
    """One message; supports pasted multi-line text, a trailing \\ for continuation, and \"\"\" blocks."""
    try:
        first = input(prompt)
    except EOFError:
        return None
    lines = [first]
    if first.strip() == '"""':
        lines = []
        while True:
            try:
                l = input(ui.dim("... "))
            except EOFError:
                break
            if l.strip() == '"""':
                break
            lines.append(l)
        return "\n".join(lines)
    while lines[-1].endswith("\\"):
        lines[-1] = lines[-1][:-1]
        try:
            lines.append(input(ui.dim("... ")))
        except EOFError:
            break
    lines += _drain_paste()
    return "\n".join(lines)


def _drain_paste():
    """Collect the rest of a multi-line paste that arrived all at once."""
    extra = []
    try:
        if os.name == "nt":
            import msvcrt
            time.sleep(0.05)
            buf = ""
            while msvcrt.kbhit():
                ch = msvcrt.getwche()
                if ch in "\r\n":
                    extra.append(buf)
                    buf = ""
                    time.sleep(0.01)
                else:
                    buf += ch
            if buf:
                extra.append(buf)
        else:
            import select
            while select.select([sys.stdin], [], [], 0.05)[0]:
                line = sys.stdin.readline()
                if not line:
                    break
                extra.append(line.rstrip("\n"))
    except Exception:  # noqa: BLE001
        pass
    return extra


def list_chats(query=None, project=None, limit=15, include_auto=False):
    rows = sessions.recent(limit, project=project, query=query, include_auto=include_auto)
    if not rows:
        ui.info("No saved chats" + (f" matching '{query}'" if query else "") + " yet.")
        return rows
    for i, r in enumerate(rows, 1):
        proj = os.path.basename(r["project"].rstrip("/\\")) or r["project"]
        print(f"  {ui.bcyan(str(i).rjust(2))}  {r['title'][:60]:60}  {ui.dim(proj[:20]):20}  {ui.dim(ui.ago(r['updated']))}")
    return rows


DEBUG_PROMPT = """DEBUG MODE. The user reports a problem:
{problem}

Work like a senior debugger:
1. REPRODUCE: run the app/tests/check to see the failure yourself. If you can't reproduce it, find out why.
2. LOCATE: follow the code path from where it fails back to the cause (find_code / read_file). Check the exact
   dependency API with references(...) if a library is involved.
3. HYPOTHESES: list 2-3 possible root causes and test the most likely first.
4. FIX the root cause with the smallest correct change. Don't hide symptoms (no silencing errors, no skipping tests).
5. PROVE IT: add a test that fails without the fix and passes with it (when the project has tests), then check.
6. EXPLAIN in plain language: what was wrong, what you changed, how to confirm it's fixed."""


class Repl:
    def __init__(self, root, session=None):
        self.root = root
        self.cfg = config.load()
        self.agent = None
        self.session = session

    def start_agent(self):
        self.agent = Agent(self.root, self.cfg, session=self.session)
        self.session = self.agent.session

    def banner(self):
        ui.logo()
        name = self.cfg.get("model_name") or "no model"
        kinds = ", ".join(checks.kinds(self.root)) or "empty folder"
        print(f"  {ui.dim('model')} {name}   {ui.dim('project')} {self.root} {ui.dim('(' + kinds + ')')}")
        if self.session and self.session.messages:
            print(f"  {ui.dim('resumed')} {ui.bold(self.session.title)} {ui.dim(f'({len(self.session.messages)} messages)')}")
        print(ui.dim("  Type what you want. /help for commands. Ctrl+C stops the current action.\n"))

    def run(self):
        if not server.ensure(self.cfg):
            return
        self.start_agent()
        self.banner()
        if self.session.messages:
            last = [m for m in self.session.messages if m["role"] in ("user", "assistant") and m.get("content")][-2:]
            for m in last:
                who = ui.cyan("you") if m["role"] == "user" else ui.yellow("windmill")
                print(f"{who}: {ui.dim(str(m['content'])[:300])}")
            print()
        while True:
            try:
                text = read_input(ui.bcyan("windmill › "))
            except KeyboardInterrupt:
                print()
                continue
            if text is None:
                break
            text = text.strip()
            if not text:
                continue
            if text.startswith("/"):
                if self.command(text) == "exit":
                    break
                continue
            try:
                self.agent.send(text)
            except KeyboardInterrupt:
                print(ui.yellow("\n(stopped)"))
            print()
        ui.info(f"Chat saved. Resume any time with:  windmill resume   {ui.dim('(or windmill history)')}")

    # ---------------------------------------------------------- slash commands
    def command(self, text):
        cmd, _, arg = text.partition(" ")
        arg = arg.strip()
        if cmd in ("/exit", "/quit", "/q"):
            return "exit"
        if cmd == "/help":
            for c, d in HELP:
                print(f"  {ui.cyan(c.ljust(22))} {d}")
        elif cmd == "/new":
            self.session = None
            self.start_agent()
            ui.ok("New chat started.")
        elif cmd == "/history":
            list_chats(query=arg or None)
            print(ui.dim("  /resume <number> to open one"))
        elif cmd == "/resume":
            sid = sessions.resolve(arg, project=self.root)
            if not sid:
                ui.warn("No chat found. Try /history")
                return
            self.session = Session.load(sid)
            self.start_agent()
            ui.ok(f"Resumed: {self.session.title}")
        elif cmd == "/rename" and arg:
            self.session.rename(arg)
            ui.ok("Renamed.")
        elif cmd == "/export":
            dest = self.root / f"chat-{self.session.id}.md"
            sessions.export_markdown(self.session.id, dest)
            ui.ok(f"Saved {dest}")
        elif cmd == "/check":
            with ui.Spinner("checking"):
                ok, out = checks.run(self.root)
            (ui.ok if ok else ui.err)(out.splitlines()[0] if out else "")
            if not ok:
                print(ui.dim("\n".join(out.splitlines()[1:30])))
                print(ui.dim("  Tip: /debug to have Windmill fix it."))
        elif cmd == "/debug":
            problem = arg
            if not problem:
                ok, out = checks.run(self.root)
                problem = ("The project's checks fail:\n" + out) if not ok else ui.ask("Describe the problem (what you did, what happened, what you expected):")
            self.agent.send(DEBUG_PROMPT.format(problem=problem))
        elif cmd == "/undo":
            r = subprocess.run(["git", "log", "-1", "--format=%s"], cwd=self.root, capture_output=True, text=True)
            last = r.stdout.strip()
            if not last.startswith("windmill"):
                ui.warn("The last change wasn't made by Windmill - not undoing it.")
            elif ui.confirm(f"Undo: '{last}'?"):
                subprocess.run(["git", "reset", "--hard", "HEAD~1", "-q"], cwd=self.root)
                ui.ok("Undone.")
        elif cmd == "/diff":
            subprocess.run(["git", "--no-pager", "diff", "--stat", "HEAD~5", "--"], cwd=self.root)
        elif cmd == "/run":
            from . import project
            project.run_app(self.root)
        elif cmd in ("/plan", "/best"):
            if not arg:
                ui.warn(f"Usage: {cmd} <what to build>")
                return
            script = "pipeline.py" if cmd == "/plan" else "best_of_n.py"
            subprocess.run([sys.executable, str(config.SCRIPTS / script), arg], cwd=self.root)
        elif cmd == "/design":
            from . import project
            project.design_menu(self.root)
            self.start_agent()  # reload system prompt with the brief
        elif cmd == "/ref":
            subprocess.run([sys.executable, str(config.SCRIPTS / "ai_corpus.py"), "search", arg or "example"], cwd=self.root)
        elif cmd == "/map":
            print(self.agent.tools.t_project_map()[:4000])
        elif cmd == "/model":
            from . import cli
            cli.models_menu()
            self.cfg = config.load()
            self.start_agent()
        elif cmd == "/think":
            self.cfg["show_thinking"] = not self.cfg.get("show_thinking")
            config.set_value("show_thinking", self.cfg["show_thinking"])
            self.agent.p.show_thinking = self.cfg["show_thinking"]
            ui.ok(f"Thinking display {'on' if self.cfg['show_thinking'] else 'off'}.")
        elif cmd == "/ask":
            mode = "ask" if self.cfg.get("permission") != "ask" else "auto"
            config.set_value("permission", mode)
            self.cfg["permission"] = mode
            self.agent.tools.permission = mode
            ui.ok("Windmill will " + ("ask before running each command." if mode == "ask" else "run commands itself (risky ones still need your OK)."))
        elif cmd == "/compact":
            self.agent.compact(force=True)
            ui.ok("Compacted.")
        elif cmd == "/clear":
            os.system("cls" if os.name == "nt" else "clear")
        else:
            ui.warn(f"Unknown command {cmd}. /help lists them.")
