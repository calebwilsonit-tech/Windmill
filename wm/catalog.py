"""Connector catalog: find and install plugins (MCP servers) for almost any service - like Claude's
connectors directory. Featured picks + search of the official MCP Registry (registry.modelcontextprotocol.io).
Installs what a connector needs to run (Node for npx, Python's uv for uvx) automatically."""
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.parse
import urllib.request

from . import mcp, ui

REGISTRY = "https://registry.modelcontextprotocol.io/v0/servers"

FEATURED = [
    {"id": "playwright", "title": "Browser testing (E2E) - Playwright", "publisher": "Microsoft (official)",
     "why": "Lets the AI open your website/app in a real browser, click through it and check it works - "
            "the same kind of end-to-end testing Claude does.",
     "spec": {"command": "npx", "args": ["-y", "@playwright/mcp@latest", "--headless"]}, "needs": "node"},
    {"id": "ludo", "title": "Ludo AI - game assets", "publisher": "Ludo AI (official)",
     "why": "Generate sprites, animations, rigged 3D models, music, sound effects and voices for your games.",
     "spec": {"type": "http", "url": "https://mcp.ludo.ai/mcp"},
     "ask": [{"header": "Authorization", "prompt": "Your Ludo API key (from app.ludo.ai)", "prefix": "ApiKey ", "secret": True}]},
    {"id": "blender", "title": "Blender", "publisher": "ahujasid/blender-mcp (community, popular)",
     "why": "Control Blender: build and edit 3D scenes, materials and models from chat. Also install its Blender "
            "add-on (addon.py from github.com/ahujasid/blender-mcp) and click 'Connect' in Blender's sidebar.",
     "spec": {"command": "uvx", "args": ["blender-mcp"]}, "needs": "uv"},
    {"id": "github", "title": "GitHub", "publisher": "GitHub (official)",
     "why": "Issues, pull requests, code search and repositories.",
     "spec": {"type": "http", "url": "https://api.githubcopilot.com/mcp/"},
     "ask": [{"header": "Authorization", "prompt": "A GitHub personal access token (github.com/settings/tokens)",
              "prefix": "Bearer ", "secret": True}]},
    {"id": "fetch", "title": "Web pages (fetch)", "publisher": "Model Context Protocol (official)",
     "why": "Read web pages and documentation as clean text.",
     "spec": {"command": "uvx", "args": ["mcp-server-fetch"]}, "needs": "uv"},
]


def _get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "windmill", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def search(query, limit=15):
    """Latest version of each matching connector in the official MCP Registry."""
    d = _get(f"{REGISTRY}?{urllib.parse.urlencode({'search': query, 'limit': 60})}")
    seen, out = set(), []
    for e in d.get("servers", []):
        s = e.get("server", e)
        meta = (e.get("_meta") or {}).get("io.modelcontextprotocol.registry/official") or {}
        if s["name"] in seen or meta.get("isLatest") is False:
            continue
        seen.add(s["name"])
        out.append(s)
        if len(out) >= limit:
            break
    return out


def registry_to_spec(s):
    """Turn a registry entry into a plugin spec + the values to ask the user for. None if unsupported."""
    for r in s.get("remotes") or []:
        url = r.get("url", "")
        if r.get("type") in ("streamable-http", "http", "sse") and "{" not in url:
            ask = [{"header": h["name"], "prompt": h.get("description") or h["name"], "secret": h.get("isSecret", False),
                    "required": h.get("isRequired", False)} for h in r.get("headers") or []]
            return {"type": "http", "url": url}, ask, None
    for p in s.get("packages") or []:
        kind = (p.get("registryType") or p.get("registry_type") or "").lower()
        ident, ver = p.get("identifier"), p.get("version")
        env = [{"env": v["name"], "prompt": v.get("description") or v["name"], "secret": v.get("isSecret", False),
                "required": v.get("isRequired", False)} for v in p.get("environmentVariables") or []]
        args = [a.get("value") or a.get("default") for a in p.get("packageArguments") or [] if a.get("value") or a.get("default")]
        if kind == "npm":
            return {"command": "npx", "args": ["-y", f"{ident}@{ver}" if ver else ident, *args]}, env, "node"
        if kind == "pypi":
            return {"command": "uvx", "args": [f"{ident}=={ver}" if ver else ident, *args]}, env, "uv"
        if kind == "oci":
            flags = sum((["-e", e["env"]] for e in env), [])
            return {"command": "docker", "args": ["run", "-i", "--rm", *flags, ident if ":" in ident or not ver else f"{ident}:{ver}"]}, env, "docker"
    return None, None, None


# ------------------------------------------------------------------ runtimes
def ensure_runtime(needs, spec):
    """Make sure the program the connector runs with exists; install it if we can."""
    if needs == "node":
        if shutil.which("npx"):
            return spec
        if os.name == "nt" and shutil.which("winget"):
            ui.info("This connector needs Node.js - installing it...")
            subprocess.run(["winget", "install", "-e", "--id", "OpenJS.NodeJS.LTS", "--accept-source-agreements",
                            "--accept-package-agreements"])
            npx = os.path.join(os.environ.get("ProgramFiles", "C:/Program Files"), "nodejs", "npx.cmd")
            if os.path.exists(npx):
                return {**spec, "command": npx}
        raise RuntimeError("Node.js is needed for this connector - install it from nodejs.org, then try again.")
    if needs == "uv":
        if shutil.which("uvx"):
            return spec
        ui.info("This connector needs uv (a Python tool runner) - installing it...")
        subprocess.run([sys.executable, "-m", "pip", "install", "--user", "--quiet", "uv"])
        # run through this Python so PATH doesn't matter: `python -m uv tool run <pkg>` == `uvx <pkg>`
        return {**spec, "command": sys.executable, "args": ["-m", "uv", "tool", "run", *spec["args"]]}
    if needs == "docker" and not shutil.which("docker"):
        raise RuntimeError("This connector runs in Docker - install Docker Desktop first.")
    return spec


def install(name, spec, ask=(), needs=None):
    spec = json.loads(json.dumps(spec))
    for a in ask or []:
        if not a.get("required", True) and not ui.confirm(f"Set '{a.get('header') or a.get('env')}'? ({a['prompt'][:80]})", False):
            continue
        v = ui.ask(f"{a['prompt']}:")
        if not v:
            if a.get("required", True):
                ui.warn("Skipped - this connector needs that value. Add it later in Plugins.")
                return False
            continue
        if a.get("prefix") and not v.startswith(a["prefix"].strip()):
            v = a["prefix"] + v
        if a.get("header"):
            spec.setdefault("headers", {})[a["header"]] = v
        else:
            spec.setdefault("env", {})[a["env"]] = v
    try:
        spec = ensure_runtime(needs, spec) if needs else spec
    except RuntimeError as e:
        ui.err(str(e))
        return False
    data = mcp.load()
    data["mcpServers"][name] = spec
    if name in data["disabled"]:
        data["disabled"].remove(name)
    mcp.save(data)
    with ui.Spinner(f"starting {name} to check it works (first run downloads it)"):
        ok, info = mcp.test(name)
    (ui.ok if ok else ui.warn)(f"{name}: {info}" if ok else f"{name} was added but didn't start: {info}")
    return ok


def browse(query=None):
    """Interactive catalog (CLI, and the app's console shows it with clickable buttons)."""
    if query:
        try:
            with ui.Spinner(f"searching the MCP Registry for '{query}'"):
                results = search(query)
        except Exception as e:  # noqa: BLE001
            ui.err(f"Couldn't reach the registry: {e}")
            return
        opts = []
        for i, s in enumerate(results):
            spec, _, needs = registry_to_spec(s)
            if spec:
                kind = "hosted" if spec.get("url") else needs
                opts.append((i, (s.get("title") or s["name"])[:44], f"{(s.get('description') or '')[:70]}  [{s['name'].split('/')[0]}, {kind}]"))
        if not opts:
            ui.warn("No installable connectors found for that.")
            return
        k = ui.menu(f"Connectors matching '{query}' (from the official MCP Registry)", opts)
        if k is None:
            return
        s = results[k]
        spec, ask, needs = registry_to_spec(s)
        print(ui.dim(f"  {s.get('description', '')}\n  publisher: {s['name'].split('/')[0]}"
                     + (f"   source: {s['repository']['url']}" if s.get("repository") else "")))
        print(ui.dim("  Only install connectors from publishers you trust - they act on your behalf."))
        if ui.confirm("Install it?", True):
            install(re.sub(r"[^a-z0-9-]+", "-", (s.get("title") or s["name"].split("/")[-1]).lower()).strip("-")[:30],
                    spec, ask, needs)
        return
    opts = [(f["id"], f["title"], f"{f['publisher']}") for f in FEATURED] + [("search", "Search all connectors…",
                                                                             "Gmail, Notion, Slack, databases, Figma…")]
    k = ui.menu("Connect Windmill to other apps and services", opts)
    if k is None:
        return
    if k == "search":
        q = ui.ask("What service? (e.g. gmail, notion, postgres, figma)")
        return browse(q) if q else None
    f = next(x for x in FEATURED if x["id"] == k)
    print(ui.dim(f"  {f['why']}"))
    install(f["id"], f["spec"], f.get("ask"), f.get("needs"))
