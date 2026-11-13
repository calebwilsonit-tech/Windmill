"""The Windmill desktop app: a local web server (127.0.0.1 only, random token) + an app window.

The window shows gui/index.html. Chats run the same Agent as the CLI, streamed live. Every other feature
runs the *CLI command itself* as a task with a live console (menus become buttons), so the app can never
drift from the command line - the CLI stays the source of truth.

  windmill app              open the app (starts the server, opens an app window)
  windmill app --serve      server only (used by Windmill.exe)
  windmill app --install    Windows: build Windmill.exe from source + Desktop/Start-menu shortcuts"""
import hashlib
import json
import mimetypes
import os
import queue
import re
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import config, sessions, ui, version

WEB = config.KIT / "gui"
BIN = config.KIT / "bin"
LAUNCHER_SRC = config.KIT / "launcher" / "Windmill.cs"
INSTALL_LOG = config.LOGS / "app-install.log"


def _log(msg):
    try:
        config.LOGS.mkdir(parents=True, exist_ok=True)
        with open(INSTALL_LOG, "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + msg + "\n")
    except OSError:
        pass
STATE = {"project": None, "last_beat": time.time(), "update": None, "update_at": 0, "seen": 0}
CHATS, TASKS = {}, {}
NOTES = []  # notifications for the tray icon: [n, title, text]
PROMPT_RE = re.compile(r"(^|\n)\? [^\n]*\S\s*$|\(Y/n\)\s*$|\(y/N\)\s*$")


def note(title, text):
    """Tray notification - only shown when the app window isn't on screen."""
    NOTES.append([len(NOTES) + 1, title, text])
    del NOTES[:-50]
LOCK = threading.Lock()


# ------------------------------------------------------------------ live chat runs
class EventPrinter:
    """Same interface as agent.Printer, but records events for the app instead of printing."""

    def __init__(self, run):
        self.run, self.show_thinking, self.quiet = run, False, False

    def _e(self, **ev):
        self.run.push(ev)

    def think_start(self):
        self._e(type="thinking")

    def think_stop(self):
        pass

    def think(self, t):
        self._e(type="think", text=t)

    def text(self, t):
        self._e(type="text", text=t)

    def end_text(self):
        pass

    def tool(self, name, args):
        self._e(type="tool", name=name, args={k: (v if len(str(v)) < 400 else str(v)[:400] + "…") for k, v in (args or {}).items()})

    def result(self, name, out):
        self._e(type="result", name=name, text=out[:1500])

    def debugger(self, msg):
        self._e(type="debugger", text=msg.replace("[WINDMILL DEBUGGER]", "").strip()[:2000])

    def note(self, msg):
        self._e(type="note", text=msg)


class ChatRun:
    def __init__(self, sid, project):
        self.sid, self.project = sid, project
        self.events, self.cond = [], threading.Condition()
        self.busy, self.cancel = False, threading.Event()
        self.answers, self.agent = {}, None

    def push(self, ev):
        with self.cond:
            ev["n"] = len(self.events)
            self.events.append(ev)
            self.cond.notify_all()

    def ensure_agent(self):
        if self.agent is None:
            from .agent import Agent
            s = sessions.Session.load(self.sid)
            self.agent = Agent(self.project, config.load(), session=s, interactive=True)
            self.agent.p = EventPrinter(self)
            self.agent.llm.cancel = self.cancel
            self.agent.ask_fn = self.ask
            self.agent.confirm = lambda q, default=True: self.ask(q + " (yes/no)").lower().startswith("y")
        return self.agent

    def ask(self, question):
        qid = secrets.token_hex(4)
        ev = threading.Event()
        self.answers[qid] = [ev, ""]
        self.push({"type": "ask", "qid": qid, "text": question})
        ev.wait(3600)
        return self.answers.pop(qid, [None, ""])[1]

    def send(self, text):
        if self.busy:
            return False
        self.busy = True
        self.cancel.clear()

        def work():
            try:
                from . import server
                if not server.health(config.load()):
                    self.push({"type": "note", "text": "Starting the AI model (first time can take a minute)..."})
                    if not server.ensure(config.load()):
                        self.push({"type": "error", "text": "The AI model couldn't start. Open Setup > Fix."})
                        return
                final = self.ensure_agent().send(text)
                self.push({"type": "final", "text": final or ""})
                note("Windmill replied", (final or "Done.").strip().splitlines()[0][:180] if (final or "").strip() else "Done.")
            except Exception as e:  # noqa: BLE001
                self.push({"type": "error", "text": f"{type(e).__name__}: {e}"})
            finally:
                self.busy = False
                self.push({"type": "idle"})
        threading.Thread(target=work, daemon=True).start()
        return True


# ------------------------------------------------------------------ CLI tasks with a live console
class TaskRun:
    def __init__(self, args, cwd, title=None):
        self.args, self.cwd = args, cwd
        self.title = title or "windmill " + " ".join(args)
        self.tail, self.asked = "", False
        self.events, self.cond = [], threading.Condition()
        env = {**os.environ, "FORCE_COLOR": "1", "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8",
               "WINDMILL_NO_UPDATE": "1", "WINDMILL_GUI": "1", "COLUMNS": "100"}
        kw = {"creationflags": 0x08000000} if os.name == "nt" else {}
        self.proc = subprocess.Popen([sys.executable, "-u", str(config.KIT / "windmill.py"), *args], cwd=cwd,
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                     env=env, **kw)
        threading.Thread(target=self._read, daemon=True).start()

    def push(self, ev):
        with self.cond:
            ev["n"] = len(self.events)
            self.events.append(ev)
            self.cond.notify_all()

    def _read(self):
        import codecs
        dec = codecs.getincrementaldecoder("utf-8")(errors="replace")
        fd = self.proc.stdout.fileno()
        while True:
            chunk = os.read(fd, 4096)
            if not chunk:
                break
            text = dec.decode(chunk)
            self.push({"type": "out", "text": text})
            self.tail = (self.tail + text)[-600:]
            plain = re.sub(r"\x1b\[[\d;]*[A-Za-z]", "", self.tail).split("\r")[-1]
            if not self.asked and PROMPT_RE.search(plain):
                self.asked = True
                note(self.title, "needs your answer - click to open Windmill")
        self.proc.wait()
        self.push({"type": "exit", "code": self.proc.returncode})
        note(self.title, "finished" if self.proc.returncode == 0 else "stopped with a problem - open Windmill for details")

    def input(self, text):
        try:
            self.proc.stdin.write((text + "\n").encode("utf-8"))
            self.proc.stdin.flush()
            self.asked = False
            self.tail = ""
            self.push({"type": "in", "text": text})
        except OSError:
            pass

    def stop(self):
        if self.proc.poll() is None:
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(self.proc.pid), "/T", "/F"], capture_output=True)
            else:
                self.proc.terminate()


# ------------------------------------------------------------------ data for the app
def remember_project(p):
    rec = [x for x in config.load().get("recent_projects", []) if x != p]
    config.set_value("recent_projects", [p, *rec][:20])


def project():
    p = STATE["project"] or str(config.project_root())
    return Path(p)


def state():
    from . import checks, server
    cfg = config.load()
    root = project()
    rec = None
    try:
        from . import recommend
        recommend.refresh_in_background()
        rec = recommend.home_line()
    except Exception:  # noqa: BLE001
        pass
    return {"version": version.current(), "model": cfg.get("model_name"), "server": server.health(cfg, timeout=0.5),
            "project": str(root), "kinds": checks.kinds(root), "roadmap": (root / "ROADMAP.md").exists(),
            "android": (root / "android" / "app").is_dir(), "tip": rec, "app_installed": app_installed(),
            "library": (config.KIT / "corpus" / "corpus.db").exists(), "os": os.name}


def chat_list(q=None):
    return sessions.recent(60, query=q or None)


def chat_messages(sid):
    s = sessions.Session.load(sid)
    if not s:
        return None
    out = []
    for m in s.messages:
        if m["role"] == "user":
            c = str(m.get("content", ""))
            if c.startswith("[WINDMILL DEBUGGER]"):
                out.append({"role": "debugger", "text": c.replace("[WINDMILL DEBUGGER]", "").strip()[:2000]})
            elif not c.startswith("[WINDMILL SUMMARY"):
                out.append({"role": "user", "text": c})
        elif m["role"] == "assistant":
            if m.get("content"):
                out.append({"role": "assistant", "text": m["content"]})
            for tc in m.get("tool_calls") or []:
                try:
                    args = json.loads(tc["function"]["arguments"])
                except (ValueError, KeyError):
                    args = {}
                out.append({"role": "tool", "name": tc["function"]["name"], "args": args})
        elif m["role"] == "tool":
            out.append({"role": "result", "text": str(m.get("content", ""))[:1500]})
    return {"id": s.id, "title": s.title, "project": s.project, "messages": out,
            "busy": bool(CHATS.get(sid) and CHATS[sid].busy)}


def models_info():
    from . import models
    cfg = config.load()
    hw = models.hardware()
    try:
        from .bench import best_scores
        scores = best_scores()
    except Exception:  # noqa: BLE001
        scores = {}
    out = []
    for m in models.discover(cfg):
        tag, why = models.fit(m, hw)
        out.append({**m, "fit": tag, "why": why, "selected": m["path"] == cfg.get("model_path"),
                    "bench": scores.get(m["name"])})
    return {"models": out, "hw": hw}


SETTING_HELP = {
    "ctx": "How much the AI can keep in mind at once (tokens). Bigger = more memory.",
    "auto_update": "auto: fixes and new features install themselves, big changes ask | ask | off",
    "permission": "auto: the AI runs commands itself (risky ones ask) | ask: confirm every command",
    "show_thinking": "Show the model's thinking while it works",
    "auto_fix": "Find and repair llama.cpp / moved models automatically",
    "library_refresh_days": "Grow the reference library every N days (0 = never)",
    "max_steps": "Actions per message before Windmill checks in with you",
    "port": "Port the AI model server listens on",
    "cache_type": "Memory format for the model's context (q8_0 recommended)",
}


def app_installed():
    return (BIN / ("Windmill.exe" if os.name == "nt" else "windmill-app")).exists()


def update_info(force=False):
    if not force and time.time() - STATE["update_at"] < 3600 and STATE["update"] is not None:
        return STATE["update"]
    from . import update
    try:
        tag, level, src = update.check(timeout=10)
    except Exception:  # noqa: BLE001
        tag, level, src = None, None, None
    info = {"current": version.current(), "available": bool(tag), "latest": tag[1:] if tag else None,
            "level": level, "notes": update.notes(src, tag) if tag else "", "configured": bool(update.source()[0]),
            "app": app_installed()}
    STATE.update(update=info, update_at=time.time())
    return info


PICK_PS = r"""
Add-Type -AssemblyName System.Windows.Forms, System.Drawing
[System.Windows.Forms.Application]::EnableVisualStyles()
# An invisible, always-on-top owner window: without it the dialog opens BEHIND the app (Windows doesn't let
# a background process put windows in front), so it looks like nothing happened.
$o = New-Object System.Windows.Forms.Form
$o.TopMost = $true; $o.ShowInTaskbar = $false; $o.Opacity = 0
$o.StartPosition = 'CenterScreen'; $o.Size = New-Object System.Drawing.Size(1, 1)
$o.Show(); $o.Activate()
$f = New-Object System.Windows.Forms.FolderBrowserDialog
$f.Description = '__TITLE__'
$f.ShowNewFolderButton = $true
if ('__START__' -and (Test-Path -LiteralPath '__START__')) { $f.SelectedPath = '__START__' }
$r = $f.ShowDialog($o)
$o.Close()
if ($r -eq 'OK') { [Console]::Out.Write($f.SelectedPath) }
"""


def pick_folder(start="", title="Choose a project folder"):
    """Native folder dialog. Returns (path or None, error or None)."""
    if os.name == "nt":
        q = lambda s: str(s or "").replace("'", "''")  # noqa: E731
        ps = PICK_PS.replace("__TITLE__", q(title)).replace("__START__", q(start))
        try:
            r = subprocess.run(["powershell", "-NoProfile", "-STA", "-ExecutionPolicy", "Bypass", "-Command", ps],
                               capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900)
        except (OSError, subprocess.TimeoutExpired) as e:
            _log(f"folder picker failed: {e}")
            return None, str(e)
        if r.returncode:
            _log(f"folder picker exit {r.returncode}: {r.stderr.strip()[-800:]}")
            return None, (r.stderr.strip().splitlines() or ["the folder window couldn't open"])[-1]
        return (r.stdout.strip() or None), None
    for tool in (["zenity", "--file-selection", "--directory"], ["kdialog", "--getexistingdirectory"]):
        if shutil.which(tool[0]):
            r = subprocess.run(tool, capture_output=True, text=True)
            return (r.stdout.strip() or None), None
    return None, "no folder window available here - type the path instead"


# ------------------------------------------------------------------ HTTP
class Handler(BaseHTTPRequestHandler):
    server_version = "Windmill"

    def log_message(self, *a):
        pass

    # --- helpers
    def _json(self, obj, code=200):
        body = json.dumps(obj, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}") if n else {}

    def _authed(self):
        tok = self.server.token
        cookie = self.headers.get("Cookie") or ""
        if f"wm_token={tok}" in cookie or self.headers.get("X-Windmill-Token") == tok:
            host = (self.headers.get("Host") or "").split(":")[0]
            origin = self.headers.get("Origin")
            return host in ("127.0.0.1", "localhost") and (not origin or re.match(r"https?://(127\.0\.0\.1|localhost)(:\d+)?$", origin))
        return False

    def _sse(self, run, since):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        i = since
        try:
            while True:
                with run.cond:
                    while i >= len(run.events):
                        if not run.cond.wait(15):
                            break
                    new = run.events[i:]
                if not new:
                    self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
                    continue
                for ev in new:
                    self.wfile.write(b"data: " + json.dumps(ev).encode() + b"\n\n")
                i += len(new)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            return

    # --- routes
    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        path = u.path
        if path == "/health":
            return self._json({"ok": True, "version": version.current()})
        if path in ("/", "/index.html"):
            if q.get("token", [""])[0] == self.server.token:  # first load from the launcher: set the cookie
                self.send_response(302)
                self.send_header("Set-Cookie", f"wm_token={self.server.token}; Path=/; SameSite=Strict; HttpOnly")
                self.send_header("Location", "/")
                self.end_headers()
                return
            if not self._authed():
                return self._json({"error": "Open Windmill from its app icon or `windmill app`."}, 403)
            return self._static("index.html")
        if path.startswith("/static/"):
            return self._static(path[len("/static/"):])
        if not self._authed():
            return self._json({"error": "forbidden"}, 403)
        STATE["last_beat"] = time.time()
        if path == "/api/state":
            return self._json(state())
        if path == "/api/chats":
            return self._json(chat_list(q.get("q", [""])[0]))
        m = re.match(r"^/api/chats/([0-9a-f]+)$", path)
        if m:
            data = chat_messages(m.group(1))
            return self._json(data or {"error": "not found"}, 200 if data else 404)
        m = re.match(r"^/api/chats/([0-9a-f]+)/events$", path)
        if m:
            run = CHATS.get(m.group(1))
            if not run:
                return self._json({"error": "not running"}, 404)
            return self._sse(run, int(q.get("since", ["0"])[0]))
        m = re.match(r"^/api/tasks/(\w+)/log$", path)  # whole output so far + exit code (tests, support)
        if m:
            t = TASKS.get(m.group(1))
            if not t:
                return self._json({"error": "no task"}, 404)
            return self._json({"text": "".join(e.get("text", "") for e in t.events if e["type"] == "out"),
                               "code": t.proc.poll()})
        m = re.match(r"^/api/tasks/(\w+)/events$", path)
        if m:
            t = TASKS.get(m.group(1))
            return self._sse(t, int(q.get("since", ["0"])[0])) if t else self._json({"error": "no task"}, 404)
        if path == "/api/projects-home":
            from . import project as proj
            return self._json({"path": str(proj.projects_home())})
        if path == "/api/github":
            from . import gitops
            return self._json(gitops.account(refresh=q.get("refresh", ["0"])[0] == "1"))
        if path == "/api/github/repos":
            from . import gitops
            try:
                return self._json({"repos": gitops.my_repos()})
            except Exception as e:  # noqa: BLE001
                return self._json({"error": str(e)[:300]}, 502)
        if path == "/api/git":
            from . import gitops
            return self._json(gitops.info(project()))
        if path == "/api/projects":
            cur = str(project())
            rec = [x for x in config.load().get("recent_projects", []) if x != cur and Path(x).is_dir()]
            return self._json({"current": cur, "recent": rec[:12]})
        if path == "/api/tray":  # the tray icon's 15-second check-in (also keeps Windmill running in the background)
            since = int(q.get("since", ["0"])[0] or 0)
            running = sum(1 for t in TASKS.values() if t.proc.poll() is None) + sum(1 for r in CHATS.values() if r.busy)
            waiting = sum(1 for t in TASKS.values() if t.proc.poll() is None and t.asked)
            window = time.time() - STATE["seen"] < 40
            new = [n for n in NOTES if n[0] > since]
            return self._json({"running": running, "waiting": waiting, "window": window,
                               "next": NOTES[-1][0] if NOTES else 0,
                               "notes": [] if window else [f"{n[1]}: {n[2]}" for n in new[-3:]]})
        if path == "/api/update/status":
            return self._json({**UPDATING, "version": version.current()})
        if path == "/api/models":
            return self._json(models_info())
        if path == "/api/settings":
            return self._json({"values": config.load(), "help": SETTING_HELP})
        if path == "/api/plugins":
            from . import mcp
            return self._json({**mcp.load(), "claude_desktop": str(mcp.claude_desktop_config() or "")})
        if path == "/api/skills":
            from . import skills
            return self._json(skills.discover(project()))
        if path == "/api/update":
            return self._json(update_info(force=q.get("force") == ["1"]))
        if path == "/api/server":
            from . import server
            return self._json({**server.status(), "log": server.tail_log(40)})
        if path == "/api/changes":
            r = subprocess.run(["git", "status", "--short"], cwd=project(), capture_output=True, text=True)
            log = subprocess.run(["git", "log", "--oneline", "-12"], cwd=project(), capture_output=True, text=True)
            return self._json({"status": r.stdout, "log": log.stdout})
        if path == "/api/design":
            from . import design
            return self._json({"brief": design.load(project()) or ""})
        return self._json({"error": "unknown"}, 404)

    def do_POST(self):
        if not self._authed():
            return self._json({"error": "forbidden"}, 403)
        STATE["last_beat"] = time.time()
        path = urllib.parse.urlparse(self.path).path
        b = self._body()
        if path == "/api/beat":
            if b.get("visible"):
                STATE["seen"] = time.time()
            return self._json({"ok": True})
        if path == "/api/project":
            p = Path(b.get("path", "")).expanduser()
            if not p.is_dir():
                return self._json({"error": f"{p} isn't a folder"}, 400)
            STATE["project"] = str(p.resolve())
            remember_project(STATE["project"])
            return self._json(state())
        if path == "/api/projects/forget":
            config.set_value("recent_projects", [x for x in config.load().get("recent_projects", []) if x != b.get("path")])
            return self._json({"ok": True})
        if path == "/api/pick-folder":
            path, error = pick_folder(b.get("start") or str(project()), b.get("title") or "Choose a project folder")
            return self._json({"path": path, "error": error})
        if path == "/api/chats":
            s = sessions.Session.new(project())
            return self._json({"id": s.id})
        m = re.match(r"^/api/chats/([0-9a-f]+)/(send|stop|answer|rename|delete|export)$", path)
        if m:
            sid, act = m.groups()
            if act == "delete":
                sessions.delete(sid)
                CHATS.pop(sid, None)
                return self._json({"ok": True})
            if act == "rename":
                s = sessions.Session.load(sid)
                s.rename(b.get("title", s.title))
                return self._json({"ok": True})
            if act == "export":
                s = sessions.Session.load(sid)
                dest = Path(s.project) / f"chat-{sid}.md"
                sessions.export_markdown(sid, dest)
                return self._json({"path": str(dest)})
            with LOCK:
                run = CHATS.get(sid)
                if not run:
                    s = sessions.Session.load(sid)
                    if not s:
                        return self._json({"error": "no chat"}, 404)
                    run = CHATS[sid] = ChatRun(sid, s.project if Path(s.project).is_dir() else str(project()))
            if act == "send":
                ok = run.send(b.get("text", ""))
                return self._json({"ok": ok, "since": len(run.events)} if ok else {"error": "still working"}, 200 if ok else 409)
            if act == "stop":
                run.cancel.set()
                for qid, slot in list(run.answers.items()):
                    slot[1] = "no"
                    slot[0].set()
                return self._json({"ok": True})
            if act == "answer":
                slot = run.answers.get(b.get("qid"))
                if slot:
                    slot[1] = b.get("text", "")
                    slot[0].set()
                return self._json({"ok": bool(slot)})
        if path == "/api/tasks":
            args = [str(a) for a in b.get("args", [])]
            tid = secrets.token_hex(4)
            TASKS[tid] = TaskRun(args, b.get("cwd") or str(project()), b.get("title"))
            return self._json({"id": tid})
        m = re.match(r"^/api/tasks/(\w+)/(input|stop)$", path)
        if m:
            t = TASKS.get(m.group(1))
            if not t:
                return self._json({"error": "no task"}, 404)
            t.input(b.get("text", "")) if m.group(2) == "input" else t.stop()
            return self._json({"ok": True})
        if path == "/api/models/select":
            from . import models, setup
            found = [x for x in models.discover() if x["path"] == b.get("path")]
            if found:
                setup.select(found[0])
            return self._json({"ok": bool(found)})
        if path == "/api/settings":
            cfg = config.load()
            for k, v in (b.get("values") or {}).items():
                if k in config.DEFAULTS:
                    cfg[k] = v
            config.save(cfg)
            return self._json({"ok": True})
        if path == "/api/plugins":
            from . import mcp
            if b.get("action") == "import":
                n, src = mcp.import_claude_desktop()
                return self._json({"added": n, "from": str(src or "")})
            if b.get("action") == "test":
                ok, info = mcp.test(b["name"])
                return self._json({"ok": ok, "info": info})
            data = mcp.load()
            if b.get("action") == "save":
                data["mcpServers"][b["name"]] = b["spec"]
            elif b.get("action") == "remove":
                data["mcpServers"].pop(b["name"], None)
            elif b.get("action") in ("enable", "disable"):
                d = set(data["disabled"])
                (d.discard if b["action"] == "enable" else d.add)(b["name"])
                data["disabled"] = sorted(d)
            mcp.save(data)
            return self._json({"ok": True})
        if path in ("/api/server/start", "/api/server/stop"):
            from . import server
            if path.endswith("start"):
                threading.Thread(target=server.start, daemon=True).start()
            else:
                server.stop(quiet=True)
            return self._json({"ok": True})
        if path == "/api/update/apply":
            if UPDATING.get("step") and not UPDATING.get("done"):
                return self._json({"ok": True, "started": True})
            info = update_info(force=True)
            if not info["available"]:
                return self._json({"ok": False, "error": "already up to date"})
            UPDATING.clear()
            UPDATING.update(step="Downloading the update…", target=info["latest"], started=time.time())
            threading.Thread(target=run_update, args=(f"v{info['latest']}",), daemon=True).start()
            return self._json({"ok": True, "started": True})
        if path == "/api/shutdown":
            self._json({"ok": True})
            threading.Thread(target=lambda: (time.sleep(0.3), os._exit(0)), daemon=True).start()
            return
        return self._json({"error": "unknown"}, 404)

    def _static(self, name):
        p = (WEB / name).resolve()
        if WEB.resolve() not in p.parents or not p.is_file():
            return self._json({"error": "not found"}, 404)
        data = p.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(str(p))[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)


# ------------------------------------------------------------------ lifecycle
UPDATING = {}


def run_update(tag):
    """In-app update, in the background, with steps the app shows; then restart on the new code."""
    from . import update
    try:
        _log(f"in-app update to {tag} started")
        src, _ = update.source()
        UPDATING["step"] = "Installing the update (and rebuilding the app if it changed)…"
        if not update.apply(tag, src, quiet=True):
            UPDATING.update(done=True, error="The update couldn't be installed. Open a terminal and run  windmill update  "
                                             "to see why (details: ~/.windmill/logs/app.log).")
            _log("in-app update failed")
            return
        UPDATING["step"] = "Restarting Windmill…"
        _log(f"updated to {version.current()} - restarting")
        restart()
    except Exception as e:  # noqa: BLE001
        UPDATING.update(done=True, error=f"Update failed: {e}")
        _log(f"in-app update error: {e!r}")


def restart():
    """After an update: start a fresh engine on the new code with the same port/token, then exit.
    (The new process waits for the port to free up; the main thread is held so Python can't quit first.)"""
    time.sleep(0.5)
    STATE["restarting"] = True
    srv = STATE.get("httpd")
    if srv:
        srv.shutdown()
        srv.server_close()
    exe = sys.executable
    kw = {"creationflags": 0x00000200 | 0x08000000} if os.name == "nt" else {"start_new_session": True}
    subprocess.Popen([exe, str(config.KIT / "windmill.py"), "app", "--serve", "--port", str(STATE["port"]),
                      "--token", STATE["token"]], cwd=str(config.KIT), stdin=subprocess.DEVNULL, close_fds=True, **kw)
    os._exit(0)


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def idle_watch():
    """Shut down when the window has been closed (no heartbeat) and nothing is running."""
    while True:
        time.sleep(15)
        busy = any(r.busy for r in CHATS.values()) or any(t.proc.poll() is None for t in TASKS.values())
        if not busy and time.time() - STATE["last_beat"] > 90:
            os._exit(0)


def serve(port=None, token=None, block=True):
    port, token = port or free_port(), token or secrets.token_urlsafe(24)
    for attempt in range(60):  # after an update restart the old process may still hold the port for a moment
        try:
            httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
            break
        except OSError:
            if attempt == 59:
                raise
            time.sleep(0.5)
    httpd.token = token
    httpd.daemon_threads = True
    STATE.update(port=port, token=token, httpd=httpd, last_beat=time.time())
    info = config.HOME / "app.json"
    info.parent.mkdir(parents=True, exist_ok=True)
    info.write_text(json.dumps({"port": port, "token": token, "pid": os.getpid()}), encoding="utf-8")
    threading.Thread(target=idle_watch, daemon=True).start()
    if block:
        httpd.serve_forever()
        while STATE.get("restarting"):  # restart() is starting the new engine - don't let Python exit under it
            time.sleep(1)
    else:
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return port, token


def find_browser_app():
    """A browser that can open a chrome-less app window (Edge ships with Windows)."""
    cands = []
    if os.name == "nt":
        for base in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles"), os.environ.get("LOCALAPPDATA")):
            if base:
                cands += [Path(base) / "Microsoft" / "Edge" / "Application" / "msedge.exe",
                          Path(base) / "Google" / "Chrome" / "Application" / "chrome.exe"]
    else:
        cands += [shutil.which(n) for n in ("microsoft-edge", "google-chrome", "chromium", "chromium-browser")]
        cands.append("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
    return next((str(c) for c in cands if c and Path(c).exists()), None)


def open_window(url):
    b = find_browser_app()
    if b:
        prof = config.HOME / "app-window"
        subprocess.Popen([b, f"--app={url}", f"--user-data-dir={prof}", "--window-size=1320,880", "--no-first-run",
                          "--no-default-browser-check", "--disable-features=Translate"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        webbrowser.open(url)


def running_app():
    """If the app is already running, reuse it instead of starting a second server."""
    try:
        info = json.loads((config.HOME / "app.json").read_text(encoding="utf-8"))
        import urllib.request
        with urllib.request.urlopen(f"http://127.0.0.1:{info['port']}/health", timeout=1):
            return info
    except Exception:  # noqa: BLE001
        return None


# ------------------------------------------------------------------ install (Windows .exe + shortcuts)
def csc():
    win = Path(os.environ.get("WINDIR", "C:/Windows"))
    for d in ("Framework64", "Framework"):
        c = win / "Microsoft.NET" / d / "v4.0.30319" / "csc.exe"
        if c.exists():
            return str(c)
    return shutil.which("csc")


def source_hash():
    return hashlib.sha1(LAUNCHER_SRC.read_bytes()).hexdigest()[:12] if LAUNCHER_SRC.exists() else ""


def download_exe(out):
    """Fallback: the Windmill.exe GitHub built for this exact version (attached to the release)."""
    import urllib.request
    from . import update
    src, _ = update.source()
    m = re.search(r"github\.com[/:]([^/]+)/([^/.]+)", src or "")
    if not m:
        return None
    url = f"https://github.com/{m.group(1)}/{m.group(2)}/releases/download/v{version.current()}/Windmill.exe"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "windmill"})
        with urllib.request.urlopen(req, timeout=60) as r:
            data = r.read()
        if data[:2] != b"MZ":  # not a Windows program
            return None
        out.write_bytes(data)
        return out
    except Exception:  # noqa: BLE001
        return None


def build_exe(quiet=False):
    """Compile Windmill.exe from launcher/Windmill.cs with the C# compiler built into Windows
    (or download the copy GitHub built for this version if compiling isn't possible)."""
    c = csc()
    BIN.mkdir(parents=True, exist_ok=True)
    exe = BIN / "Windmill.exe"
    out = exe
    if exe.exists():
        try:
            os.rename(exe, exe)  # fails if the app is running
        except OSError:
            out = BIN / "Windmill.new.exe"  # the running launcher swaps itself on next start
    icon = WEB / "windmill.ico"
    _log(f"building {out} with compiler {c}")
    r = subprocess.run([c, "/nologo", "/target:winexe", "/optimize+", f"/out:{out}", f"/win32icon:{icon}",
                        "/r:System.Windows.Forms.dll", "/r:System.Drawing.dll", "/r:System.Net.Http.dll", str(LAUNCHER_SRC)],
                       capture_output=True, text=True) if c else None
    if r is not None:
        _log(f"compiler exit {r.returncode}: {(r.stdout + r.stderr).strip()[-2000:]}")
    if r is None or r.returncode != 0:
        _log("compiling not possible - downloading the release's Windmill.exe")
        if download_exe(out):
            if not quiet:
                ui.info("Couldn't build Windmill.exe here, so downloaded the copy GitHub built for this version.")
        else:
            if not quiet:
                ui.err("Couldn't build or download Windmill.exe." +
                       ("\n" + (r.stdout + r.stderr)[-1500:] if r else " (no C# compiler found)"))
            return None
    pyw = Path(sys.executable).with_name("pythonw.exe")
    (BIN / "python.txt").write_text(str(pyw if pyw.exists() else sys.executable), encoding="utf-8")
    (BIN / "launcher.hash").write_text(source_hash(), encoding="utf-8")
    return out


def shortcuts():
    _log("creating shortcuts")
    exe = BIN / "Windmill.exe"
    ps = ("$s = New-Object -ComObject WScript.Shell; "
          f"foreach ($p in @([Environment]::GetFolderPath('Desktop'), '{config.KIT}', "
          "(Join-Path ([Environment]::GetFolderPath('StartMenu')) 'Programs'))) { "
          "$l = $s.CreateShortcut((Join-Path $p 'Windmill.lnk')); "
          f"$l.TargetPath = '{exe}'; $l.WorkingDirectory = '{config.KIT}'; "
          f"$l.IconLocation = '{exe},0'; $l.Description = 'Windmill - local AI that builds things'; $l.Save() }}")
    r = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True)
    _log(f"shortcuts exit {r.returncode} {r.stderr.strip()[-500:]}")


def install(quiet=False):
    if os.name != "nt":
        if not quiet:
            ui.info("The installable app is for Windows. Here, `windmill app` opens the same app in a browser window.")
        return False
    exe = build_exe(quiet)
    if not exe:
        return False
    shortcuts()
    if not quiet:
        ui.ok(f"Windmill app installed: {BIN / 'Windmill.exe'} (+ Desktop and Start-menu shortcuts)")
    return True


def after_update():
    """Called after every update: keep the .exe in step with the code (only if the app is installed)."""
    if os.name == "nt" and app_installed():
        hf = BIN / "launcher.hash"
        built = hf.read_text(encoding="utf-8").strip() if hf.exists() else ""
        if built != source_hash():
            build_exe(quiet=True)


def main(args):
    if "--install" in args:
        install()
        if "--launch" not in args:
            return
    opt = lambda k: args[args.index(k) + 1] if k in args and args.index(k) + 1 < len(args) else None
    if "--serve" in args:
        serve(int(opt("--port") or 0) or None, opt("--token"))
        return
    info = running_app()
    if info:
        open_window(f"http://127.0.0.1:{info['port']}/?token={info['token']}")
        return
    port, token = serve(block=False)
    url = f"http://127.0.0.1:{port}/?token={token}"
    ui.ok(f"Windmill app running at {url}")
    open_window(url)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass
