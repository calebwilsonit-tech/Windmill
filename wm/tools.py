"""The agent's hands: files, search, shell, project map, library, checks, design. All paths are
confined to the project folder; destructive shell commands are blocked or need confirmation."""
import difflib
import fnmatch
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from . import checks, config, design

sys.path.insert(0, str(config.SCRIPTS))

SKIP = {".git", "target", "node_modules", "dist", "build", ".venv", "venv", "__pycache__", ".ai", ".next", ".windmill"}
MAX_RESULT = 7000
DANGER = [
    r"\brm\s+-[a-z]*r[a-z]*f?\s+(/|~|\*|\.\.|[A-Za-z]:)(\s|$)", r"\brm\s+-[a-z]*f[a-z]*r", r"\bdel\s+/[sq]",
    r"\brmdir\s+/s", r"Remove-Item\b.*-Recurse", r"\bformat\s+[a-z]:", r"\bmkfs", r"\bdd\s+if=", r"\bshutdown\b",
    r"git\s+push\b.*(-f\b|--force)", r"git\s+reset\s+--hard", r"git\s+clean\s+-[a-z]*f", r"curl[^|]*\|\s*(ba)?sh",
    r"iwr[^|]*\|\s*iex", r"\breg\s+delete", r":\(\)\s*\{", r"chmod\s+-R\s+777\s+/", r"\bsudo\b",
]

SCHEMAS = [
    ("read_file", "Read a file (numbered lines). For big files read a line range, or use find_code.",
     {"path": "string", "start_line": "integer?", "end_line": "integer?"}),
    ("write_file", "Create a new file, or completely replace a small file. Write COMPLETE content - never '...'.",
     {"path": "string", "content": "string"}),
    ("edit_file", "Replace an exact snippet in a file. old_text must match exactly once (copy it from read_file, "
     "without line numbers). Include enough surrounding lines to be unique.",
     {"path": "string", "old_text": "string", "new_text": "string", "replace_all": "boolean?"}),
    ("list_files", "List files and folders (tree).", {"path": "string?", "depth": "integer?"}),
    ("search", "Regex search across the project's files. Returns file:line: text.",
     {"pattern": "string", "path": "string?", "file_glob": "string?"}),
    ("run", "Run a shell command in the project folder (build, test, install, git...). Use background=true for "
     "servers/apps that keep running.", {"command": "string", "timeout": "integer?", "background": "boolean?"}),
    ("project_map", "The project's map: modules, public API, hub files, recent work. Cheap - use it before exploring.", {}),
    ("find_code", "Find where a function/type/struct is defined and show ONLY its source (Rust projects), "
     "or regex-search for it (other languages).", {"name": "string"}),
    ("references", "Search the reference library (thousands of real projects: games, web, mobile, UI design "
     "systems, docs) and the EXACT APIs of this project's dependencies. Use before writing unfamiliar code.",
     {"topic": "string"}),
    ("check", "Run the project's checks (build, lint, tests, sloppy-code guard). Do this before saying you're done.", {}),
    ("design_brief", "The project's visual identity (colours, fonts, spacing, signature style) and pro UI rules. "
     "Read before ANY UI work.", {}),
    ("phone", "Android projects: action='build' makes the .apk, 'install' builds+installs+launches it on the "
     "USB-connected phone, 'logs' returns the phone's recent crash/panic/Rust log lines (use after install to "
     "debug crashes).", {"action": "string"}),
    ("ask_user", "Ask the user a plain-language question about WHAT they want (never about how to code it). "
     "Only when you genuinely can't decide yourself.", {"question": "string"}),
]


def tool_specs():
    out = []
    for name, desc, params in SCHEMAS:
        props, req = {}, []
        for k, t in params.items():
            opt = t.endswith("?")
            props[k] = {"type": t.rstrip("?")}
            if not opt:
                req.append(k)
        out.append({"type": "function", "function": {"name": name, "description": desc,
                                                     "parameters": {"type": "object", "properties": props, "required": req}}})
    return out


class Tools:
    def __init__(self, root, interactive=True, permission="auto", ask=None):
        self.root = Path(root).resolve()
        self.interactive = interactive
        self.permission = permission
        self.ask = ask  # callable(question) -> answer, for interactive sessions
        self.changed = set()  # files edited this task
        self.bg = []

    # ---------------------------------------------------------- helpers
    def path(self, p):
        p = (p or ".").strip().strip('"').strip("'")
        full = (self.root / p).resolve() if not os.path.isabs(p) else Path(p).resolve()
        if full != self.root and self.root not in full.parents:
            raise ValueError(f"'{p}' is outside the project folder - only files inside {self.root} can be used")
        return full

    def rel(self, p):
        return Path(p).resolve().relative_to(self.root).as_posix()

    def call(self, name, args):
        fn = getattr(self, "t_" + name, None)
        if not fn:
            return f"ERROR: unknown tool '{name}'. Available: {', '.join(s[0] for s in SCHEMAS)}"
        if args.get("_error"):
            return f"ERROR: {args['_error']}. Re-send the tool call with valid JSON arguments."
        try:
            out = fn(**{k: v for k, v in args.items() if not k.startswith("_")})
        except TypeError as e:
            return f"ERROR: wrong arguments for {name}: {e}"
        except ValueError as e:
            return f"ERROR: {e}"
        except Exception as e:  # noqa: BLE001
            return f"ERROR: {type(e).__name__}: {e}"
        out = str(out)
        if len(out) > MAX_RESULT:
            out = out[:MAX_RESULT] + f"\n... ({len(out) - MAX_RESULT} more characters cut - narrow your request)"
        return out

    # ---------------------------------------------------------- files
    def t_read_file(self, path, start_line=None, end_line=None):
        f = self.path(path)
        if not f.is_file():
            return f"ERROR: {path} doesn't exist." + self._suggest(path)
        lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
        s = max(1, int(start_line or 1))
        e = int(end_line or (s + 399))
        if not start_line and len(lines) > 400:
            head = "\n".join(f"{i:5}| {l}" for i, l in enumerate(lines[:250], 1))
            return (f"{path} has {len(lines)} lines - showing 1-250. Read more with start_line/end_line, "
                    f"or use find_code for one function.\n{head}")
        body = "\n".join(f"{i:5}| {l}" for i, l in enumerate(lines[s - 1:e], s))
        more = f"\n... ({len(lines) - e} more lines)" if e < len(lines) else ""
        return body + more if body else "(empty file)"

    def t_write_file(self, path, content):
        f = self.path(path)
        f.parent.mkdir(parents=True, exist_ok=True)
        existed = f.exists()
        f.write_text(content, encoding="utf-8")
        self.changed.add(self.rel(f))
        return f"{'Replaced' if existed else 'Created'} {path} ({content.count(chr(10)) + 1} lines)."

    def t_edit_file(self, path, old_text, new_text, replace_all=False):
        f = self.path(path)
        if not f.is_file():
            return f"ERROR: {path} doesn't exist. Use write_file to create it."
        src = f.read_text(encoding="utf-8", errors="replace")
        old = re.sub(r"(?m)^\s*\d+\| ", "", old_text) if re.match(r"^\s*\d+\| ", old_text) else old_text
        n = src.count(old)
        if n == 0:
            # tolerate trailing-whitespace / indentation-width differences
            norm = lambda s: "\n".join(l.rstrip() for l in s.splitlines())
            if norm(old) and norm(src).count(norm(old)) == 1:
                src2 = norm(src).replace(norm(old), new_text)
                f.write_text(src2 + ("\n" if src.endswith("\n") else ""), encoding="utf-8")
                self.changed.add(self.rel(f))
                return f"Edited {path} (matched ignoring trailing spaces)."
            return "ERROR: old_text not found. " + self._closest(src, old) + " Re-read the file and copy the text exactly."
        if n > 1 and not replace_all:
            return f"ERROR: old_text appears {n} times in {path}. Add surrounding lines so it's unique, or set replace_all."
        f.write_text(src.replace(old, new_text) if replace_all else src.replace(old, new_text, 1), encoding="utf-8")
        self.changed.add(self.rel(f))
        added, removed = new_text.count("\n") + 1, old.count("\n") + 1
        return f"Edited {path} (-{removed} +{added} lines)."

    def _closest(self, src, old):
        lines = src.splitlines()
        first = old.strip().splitlines()[0].strip() if old.strip() else ""
        cands = difflib.get_close_matches(first, [l.strip() for l in lines], n=1, cutoff=0.5)
        if not cands:
            return ""
        i = [l.strip() for l in lines].index(cands[0])
        snippet = "\n".join(f"{j + 1:5}| {lines[j]}" for j in range(max(0, i - 2), min(len(lines), i + 6)))
        return f"Closest match is around line {i + 1}:\n{snippet}\n"

    def _suggest(self, path):
        name = Path(path).name
        hits = [self.rel(p) for p in self._files() if p.name == name][:5]
        return f" Did you mean: {', '.join(hits)}" if hits else ""

    def _files(self, base=None):
        base = base or self.root
        for r, ds, fs in os.walk(base):
            ds[:] = [d for d in ds if d not in SKIP and not d.startswith(".")]
            for fn in fs:
                yield Path(r) / fn

    def t_list_files(self, path=".", depth=2):
        base = self.path(path)
        out, depth = [], int(depth or 2)
        for r, ds, fs in os.walk(base):
            lvl = Path(r).relative_to(base).parts
            ds[:] = sorted(d for d in ds if d not in SKIP and not d.startswith("."))
            if len(lvl) >= depth:
                ds[:] = []
            ind = "  " * len(lvl)
            if lvl:
                out.append(f"{ind[:-2]}{lvl[-1]}/")
            for fn in sorted(fs)[:60]:
                out.append(f"{ind}{fn}")
            if len(out) > 300:
                out.append("... (more)")
                break
        return "\n".join(out) or "(empty folder)"

    def t_search(self, pattern, path=".", file_glob=None):
        try:
            rx = re.compile(pattern)
        except re.error as e:
            return f"ERROR: bad regex: {e}"
        hits = []
        for p in self._files(self.path(path)):
            if file_glob and not fnmatch.fnmatch(p.name, file_glob):
                continue
            try:
                if p.stat().st_size > 1_000_000:
                    continue
                for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
                    if rx.search(line):
                        hits.append(f"{self.rel(p)}:{i}: {line.strip()[:160]}")
                        if len(hits) >= 60:
                            return "\n".join(hits) + "\n... (more matches - narrow the pattern)"
            except (UnicodeDecodeError, OSError):
                continue
        return "\n".join(hits) or "No matches."

    # ---------------------------------------------------------- shell
    def shell_cmd(self, command):
        if os.name == "nt":
            bash = shutil.which("bash") or r"C:\Program Files\Git\bin\bash.exe"
            if Path(bash).exists():
                return [bash, "-lc", command]
            return ["cmd", "/c", command]
        return ["bash", "-lc", command]

    @staticmethod
    def shell_name():
        if os.name != "nt":
            return "bash"
        bash = shutil.which("bash") or r"C:\Program Files\Git\bin\bash.exe"
        return "bash (Git for Windows)" if Path(bash).exists() else "cmd.exe"

    def t_run(self, command, timeout=300, background=False):
        if any(re.search(p, command, re.I) for p in DANGER):
            if self.interactive and self.ask:
                if not self.ask(f"The AI wants to run a risky command:\n    {command}\nAllow it? (yes/no)").lower().startswith("y"):
                    return "BLOCKED: the user did not allow this command. Find a safer way."
            else:
                return "BLOCKED: destructive command not allowed. Find a safer way (Windmill handles rollbacks itself)."
        elif self.permission == "ask" and self.interactive and self.ask:
            if not self.ask(f"Run: {command}  (yes/no)").lower().startswith("y"):
                return "BLOCKED: the user declined."
        env = {**os.environ, "CARGO_TERM_COLOR": "never", "NO_COLOR": "1", "FORCE_COLOR": "0", "CI": "1"}
        if background:
            log = config.LOGS / f"bg-{len(self.bg)}.log"
            config.LOGS.mkdir(parents=True, exist_ok=True)
            fh = open(log, "w", encoding="utf-8", errors="replace")
            kw = {"start_new_session": True} if os.name != "nt" else {"creationflags": 0x00000200}
            p = subprocess.Popen(self.shell_cmd(command), cwd=self.root, stdout=fh, stderr=subprocess.STDOUT, env=env, **kw)
            self.bg.append(p)
            import time
            time.sleep(6)
            out = log.read_text(encoding="utf-8", errors="replace")[-3000:]
            state = "still running" if p.poll() is None else f"exited with code {p.returncode}"
            return f"Started in background (pid {p.pid}, {state}). First output:\n{out}"
        try:
            r = subprocess.run(self.shell_cmd(command), cwd=self.root, capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=int(timeout or 300), env=env)
        except subprocess.TimeoutExpired:
            return f"TIMEOUT after {timeout}s. If this is a server or app that keeps running, use background=true."
        out = (r.stdout + ("\n" + r.stderr if r.stderr.strip() else "")).strip()
        out = checks.trim(out, 5000)
        return f"exit code {r.returncode}\n{out}" if out else f"exit code {r.returncode} (no output)"

    # ---------------------------------------------------------- knowledge
    def t_project_map(self):
        try:
            import ai_index
            ai_index.build(self.root, quiet=True)
            m = (self.root / ".ai" / "map.md").read_text(encoding="utf-8")
            if "## Files\n" in m and m.strip().endswith("## Files"):
                raise ValueError
            return m
        except Exception:  # noqa: BLE001 - non-Rust projects: a file tree is the map
            return "Project files:\n" + self.t_list_files(".", 3)

    def t_find_code(self, name):
        if (self.root / "Cargo.toml").exists():
            import ai_index
            import ai_query
            g = ai_index.build(self.root, quiet=True)
            hits = [(r, it) for r, it in ai_query.all_items(g) if ai_query.match(it, name)]
            if hits:
                out = []
                for r, it in hits[:3]:
                    lines = (self.root / r).read_text(encoding="utf-8", errors="replace").splitlines()
                    s = it["line"] - 1
                    while s > 0 and lines[s - 1].strip().startswith(("///", "#[")):
                        s -= 1
                    body = "\n".join(f"{i:5}| {l}" for i, l in enumerate(lines[s:min(it['end'], s + 120)], s + 1))
                    out.append(f"// {r}:{s + 1}-{it['end']}\n{body}")
                return "\n\n".join(out)
        simple = re.escape(name.split("::")[-1])
        return self.t_search(rf"(fn|def|function|class|struct|enum|trait|interface|type|const|let|var)\s+{simple}\b")

    def t_references(self, topic):
        try:
            import ai_corpus
            import json as _json
            kit = self.root / "ai-kit.json"
            cats = (_json.loads(kit.read_text(encoding="utf-8")).get("corpus_categories") if kit.exists() else None) or None
            pack = ai_corpus.reference_pack(self.root, topic, budget=2200, cats=cats)
            return pack or "No references found (the library may not be downloaded yet: windmill library fetch)."
        except Exception as e:  # noqa: BLE001
            return f"Reference search unavailable: {e}"

    def t_check(self):
        ok, out = checks.run(self.root)
        return ("PASS\n" if ok else "FAIL\n") + out

    def t_design_brief(self):
        b = design.load(self.root)
        return (b or "No design brief yet for this project - follow the rules below and choose a distinctive, "
                "consistent direction.") + "\n\n" + design.UI_RULES

    def t_phone(self, action):
        from . import android
        if not android.is_android(self.root):
            return "ERROR: this isn't an Android project (no android/ folder). `windmill android init` adds one."
        action = (action or "").lower()
        if action == "logs":
            return android.logs(self.root)
        if action == "build":
            apk = android.build_apk(self.root, release=False)
            return f"Built {apk}" if apk else "Build FAILED - read the compiler/Gradle output above, fix, and retry."
        if action == "install":
            if not android.devices():
                return "No phone connected. Tell the user how to enable USB debugging, or test on desktop with `cargo run`."
            ok = android.install(self.root, release=False)
            return ("Installed and launched. Wait a few seconds, then call phone(action='logs') to check for crashes."
                    if ok else "Install failed - see output.")
        return "ERROR: action must be build, install or logs"

    def t_ask_user(self, question):
        if self.interactive and self.ask:
            return "User answered: " + (self.ask(question) or "(no answer - decide yourself)")
        return "The user isn't available right now. Make the most sensible choice, state the assumption in your summary."
