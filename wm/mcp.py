"""Plugins: Model Context Protocol (MCP) servers - the same plugin system Claude Desktop uses.

Config: ~/.windmill/plugins.json in Claude Desktop's format, so existing setups can be imported as-is:
  {"mcpServers": {"github": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-github"],
                             "env": {"GITHUB_TOKEN": "..."}},
                  "remote-thing": {"type": "http", "url": "https://example.com/mcp", "headers": {...}}},
   "disabled": ["remote-thing"]}
Each enabled server's tools become agent tools named  mcp__<server>__<tool>  (CLI and app alike)."""
import atexit
import itertools
import json
import os
import re
import shutil
import subprocess
import threading
import time
import urllib.request
from pathlib import Path

from . import config

CONFIG = config.HOME / "plugins.json"
PROTOCOL = "2025-06-18"
TIMEOUT = 60


def load():
    try:
        data = json.loads(CONFIG.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    data.setdefault("mcpServers", {})
    data.setdefault("disabled", [])
    return data


def save(data):
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    CONFIG.write_text(json.dumps(data, indent=2), encoding="utf-8")


def claude_desktop_config():
    """Where Claude Desktop keeps its plugin list on this OS."""
    home = Path.home()
    cands = [Path(os.environ.get("APPDATA", home / "AppData" / "Roaming")) / "Claude" / "claude_desktop_config.json",
             home / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json",
             home / ".config" / "Claude" / "claude_desktop_config.json"]
    return next((c for c in cands if c.exists()), None)


def import_claude_desktop():
    src = claude_desktop_config()
    if not src:
        return 0, None
    theirs = json.loads(src.read_text(encoding="utf-8")).get("mcpServers", {})
    data = load()
    added = 0
    for name, spec in theirs.items():
        if name not in data["mcpServers"]:
            data["mcpServers"][name] = spec
            added += 1
    save(data)
    return added, src


def safe(name):
    return re.sub(r"[^A-Za-z0-9_-]", "_", name)


# ------------------------------------------------------------------ transports
class StdioServer:
    def __init__(self, name, spec):
        self.name, self.spec = name, spec
        self.ids = itertools.count(1)
        self.pending, self.lock = {}, threading.Lock()
        cmd = spec["command"]
        exe = shutil.which(cmd) or cmd  # finds npx.cmd / uvx.exe etc. on Windows
        env = {**os.environ, **{k: str(v) for k, v in (spec.get("env") or {}).items()}}
        kw = {"creationflags": 0x08000000} if os.name == "nt" else {}
        self.proc = subprocess.Popen([exe, *spec.get("args", [])], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.DEVNULL, env=env, cwd=spec.get("cwd") or None, **kw)
        threading.Thread(target=self._reader, daemon=True).start()

    def _reader(self):
        for line in self.proc.stdout:
            try:
                msg = json.loads(line)
            except ValueError:
                continue  # servers sometimes print logs to stdout
            if "id" in msg and msg["id"] in self.pending:
                slot = self.pending[msg["id"]]
                slot["msg"] = msg
                slot["ev"].set()

    def request(self, method, params=None, timeout=TIMEOUT):
        i = next(self.ids)
        slot = {"ev": threading.Event(), "msg": None}
        self.pending[i] = slot
        self._send({"jsonrpc": "2.0", "id": i, "method": method, "params": params or {}})
        if not slot["ev"].wait(timeout):
            self.pending.pop(i, None)
            raise TimeoutError(f"plugin '{self.name}' didn't answer {method} within {timeout}s")
        self.pending.pop(i, None)
        if "error" in slot["msg"]:
            raise RuntimeError(slot["msg"]["error"].get("message", str(slot["msg"]["error"])))
        return slot["msg"].get("result", {})

    def notify(self, method, params=None):
        self._send({"jsonrpc": "2.0", "method": method, "params": params or {}})

    def _send(self, obj):
        with self.lock:
            self.proc.stdin.write((json.dumps(obj) + "\n").encode())
            self.proc.stdin.flush()

    def alive(self):
        return self.proc.poll() is None

    def close(self):
        try:
            self.proc.terminate()
        except OSError:
            pass


class HttpServer:
    """Remote MCP server (Streamable HTTP transport)."""

    def __init__(self, name, spec):
        self.name, self.spec, self.session = name, spec, None
        self.ids = itertools.count(1)

    def _post(self, obj, timeout=TIMEOUT):
        headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream",
                   "MCP-Protocol-Version": PROTOCOL, **(self.spec.get("headers") or {})}
        if self.spec.get("auth") == "github" and "Authorization" not in (self.spec.get("headers") or {}):
            from . import gitops  # your GitHub sign-in, read from the system's credential store - never saved here
            tok = gitops.token()
            if tok:
                headers["Authorization"] = f"Bearer {tok}"
        if self.session:
            headers["Mcp-Session-Id"] = self.session
        req = urllib.request.Request(self.spec["url"], data=json.dumps(obj).encode(), headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            self.session = r.headers.get("Mcp-Session-Id") or self.session
            body = r.read().decode("utf-8", errors="replace")
            if "text/event-stream" in (r.headers.get("Content-Type") or ""):
                msgs = [json.loads(l[5:]) for l in body.splitlines() if l.startswith("data:") and l[5:].strip()]
                return next((m for m in msgs if m.get("id") == obj.get("id")), msgs[-1] if msgs else {})
            return json.loads(body) if body.strip() else {}

    def request(self, method, params=None, timeout=TIMEOUT):
        msg = self._post({"jsonrpc": "2.0", "id": next(self.ids), "method": method, "params": params or {}}, timeout)
        if "error" in msg:
            raise RuntimeError(msg["error"].get("message", str(msg["error"])))
        return msg.get("result", {})

    def notify(self, method, params=None):
        try:
            self._post({"jsonrpc": "2.0", "method": method, "params": params or {}})
        except Exception:  # noqa: BLE001
            pass

    def alive(self):
        return True

    def close(self):
        pass


# ------------------------------------------------------------------ manager
TOOL_CACHE = config.HOME / "plugin-tools.json"


def _spec_key(spec):
    import hashlib
    return hashlib.sha256(json.dumps(spec, sort_keys=True).encode()).hexdigest()[:16]


def _tool_cache():
    try:
        return json.loads(TOOL_CACHE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_tool_cache(c):
    try:
        TOOL_CACHE.parent.mkdir(parents=True, exist_ok=True)
        TOOL_CACHE.write_text(json.dumps(c), encoding="utf-8")
    except OSError:
        pass


class Plugins:
    """Starts enabled plugins, lists their tools, routes tool calls."""

    def __init__(self, names=None, lazy=True):
        """lazy: don't launch plugins up front - use their remembered tool lists and start a plugin the first time
        one of its tools is actually called (so e.g. Blender's plugin doesn't start for a chat that never uses it)."""
        self.servers, self.tools, self.errors = {}, {}, {}
        self.specs_by = {}
        data = load()
        cache = _tool_cache()
        for name, spec in data["mcpServers"].items():
            if name in data["disabled"] or (names and name not in names):
                continue
            key = _spec_key(spec)
            if lazy and cache.get(name, {}).get("key") == key:
                self.specs_by[name] = spec
                for t in cache[name]["tools"]:
                    self.tools[f"mcp__{safe(name)}__{safe(t['name'])}"[:64]] = (name, t)
                continue
            if lazy:  # never seen: don't start it just to learn its tools - offer one "start it" tool instead
                self.specs_by[name] = spec
                self.tools[f"mcp__{safe(name)}__start"[:64]] = (name, {
                    "name": "__start__", "description": f"Start the '{name}' plugin to get its tools. Call this ONLY "
                    f"when the task really needs {name}.", "inputSchema": {"type": "object", "properties": {}}})
                continue
            try:
                self._start(name, spec)
            except Exception as e:  # noqa: BLE001 - one broken plugin must not break Windmill
                self.errors[name] = str(e)[:300]
        atexit.register(self.close)

    def _start(self, name, spec):
        srv = HttpServer(name, spec) if spec.get("url") or spec.get("type") in ("http", "streamable-http") \
            else StdioServer(name, spec)
        srv.request("initialize", {"protocolVersion": PROTOCOL, "capabilities": {},
                                   "clientInfo": {"name": "windmill", "version": _version()}}, timeout=TIMEOUT)
        srv.notify("notifications/initialized")
        tools, cursor = [], None
        while True:
            res = srv.request("tools/list", {"cursor": cursor} if cursor else {})
            tools += res.get("tools", [])
            cursor = res.get("nextCursor")
            if not cursor:
                break
        self.servers[name] = srv
        for t in tools:
            full = f"mcp__{safe(name)}__{safe(t['name'])}"[:64]
            self.tools[full] = (name, t)
        c = _tool_cache()
        c[name] = {"key": _spec_key(spec), "tools": [{k: t.get(k) for k in ("name", "description", "inputSchema")}
                                                      for t in tools]}
        _save_tool_cache(c)

    def specs(self):
        out = []
        for full, (server, t) in self.tools.items():
            schema = t.get("inputSchema") or {"type": "object", "properties": {}}
            out.append({"type": "function", "function": {
                "name": full, "description": f"[{server}] " + _short(t.get("description") or t["name"]),
                "parameters": _lean(schema)}})
        return out

    def call(self, full, args):
        server, t = self.tools[full]
        if t.get("name") == "__start__":
            self.tools.pop(full, None)
            self._start(server, self.specs_by[server])
            names = [tt["name"] for s, tt in self.tools.values() if s == server]
            return f"Started the '{server}' plugin. Its tools are now available: {', '.join(names)}"
        if server not in self.servers:  # first use: start it now
            self._start(server, self.specs_by[server])
        srv = self.servers[server]
        res = srv.request("tools/call", {"name": t["name"], "arguments": args or {}}, timeout=300)
        parts = []
        for c in res.get("content", []):
            if c.get("type") == "text":
                parts.append(c.get("text", ""))
            elif c.get("type") == "resource":
                parts.append(json.dumps(c.get("resource", {}))[:4000])
            else:
                parts.append(f"[{c.get('type')} content]")
        if res.get("structuredContent") and not parts:
            parts.append(json.dumps(res["structuredContent"])[:6000])
        text = "\n".join(parts) or "(no output)"
        return ("ERROR from plugin: " + text) if res.get("isError") else text

    def close(self):
        for s in self.servers.values():
            s.close()


def _short(desc, limit=240):
    """Plugin tool descriptions are sent with EVERY request - keep the first sentence or two only."""
    d = " ".join(desc.split())
    if len(d) <= limit:
        return d
    cut = d[:limit]
    return (cut[:cut.rfind(". ") + 1] if ". " in cut else cut.rsplit(" ", 1)[0] + "…")


def _lean(schema):
    """Drop long per-parameter descriptions/examples from the schema (types and names are what matter)."""
    if isinstance(schema, dict):
        out = {}
        for k, v in schema.items():
            if k in ("examples", "example", "$schema", "title"):
                continue
            if k == "description" and isinstance(v, str):
                out[k] = _short(v, 120)
            else:
                out[k] = _lean(v)
        return out
    if isinstance(schema, list):
        return [_lean(x) for x in schema]
    return schema


def _version():
    try:
        return (config.KIT / "VERSION").read_text().strip()
    except OSError:
        return "0"


def test(name):
    """Start one plugin on its own and report its tools (for `windmill plugins test` and the app)."""
    t0 = time.time()
    p = Plugins(names=[name], lazy=False)
    if name in p.errors:
        return False, p.errors[name]
    tools = [t["name"] for s, t in p.tools.values()]
    p.close()
    return True, f"{len(tools)} tools in {time.time() - t0:.1f}s: " + ", ".join(tools[:30])
