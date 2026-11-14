"""'Is it done?' for any project type: build/lint/test + a slop guard on new code.
Rust uses scripts/rust_check.py; websites, JS/TS, Python and Go get equivalents here."""
import json
import os
import re
import shutil
import subprocess
import sys
from html.parser import HTMLParser
from pathlib import Path

from . import config

MAX_OUT = 6000
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr", "!doctype"}


def kinds(root):
    root = Path(root)
    k = []
    if (root / "Cargo.toml").exists():
        k.append("rust")
        if (root / "android" / "app").is_dir():
            k.append("android")
    if (root / "package.json").exists():
        k.append("node")
    if (root / "pyproject.toml").exists() or (root / "requirements.txt").exists() or list(root.glob("*.py")):
        k.append("python")
    if (root / "go.mod").exists():
        k.append("go")
    if not k and (list(root.glob("*.html")) or (root / "public").is_dir() or (root / "src" / "index.html").exists()):
        k.append("web")
    return k


def sh(cmd, root, timeout=1200):
    env = {**os.environ, "CARGO_TERM_COLOR": "never", "FORCE_COLOR": "0", "NO_COLOR": "1", "CI": "1"}
    try:
        r = subprocess.run(cmd, cwd=root, shell=isinstance(cmd, str), capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        return False, f"`{cmd}` timed out after {timeout}s"
    except FileNotFoundError as e:
        return True, f"(skipped: {e})"
    return r.returncode == 0, (r.stdout + "\n" + r.stderr).strip()


def trim(out, n=MAX_OUT):
    lines = out.splitlines()
    errs = [i for i, l in enumerate(lines) if re.search(r"\b(error|Error|ERROR|FAIL|failed|Traceback)\b", l)]
    if errs and len(out) > n:  # keep the region around the first errors
        start = max(0, errs[0] - 3)
        out = "\n".join(lines[start:start + 120])
    return out if len(out) <= n else out[:n] + "\n... (truncated)"


def run(root=None, quick=False):
    """Run every relevant check. Returns (ok, report)."""
    root = Path(root or config.project_root())
    ks = kinds(root)
    reports = []
    if "rust" in ks:
        ok, out = sh([sys.executable, str(config.SCRIPTS / "rust_check.py")], root)
        if not ok:
            return False, trim(out)
        if "android" in ks and not quick:
            from . import android
            aok, aout = android.check(root)
            if not aok:
                return False, "ANDROID BUILD FAILED (compiles on desktop, but not for the phone):\n" + trim(aout)
            if not aout.startswith("(android check skipped"):
                reports.append("android ok")
        first = out.splitlines()[0] if out else ""
        reports.append("rust: " + (first[first.find("(") + 1:first.rfind(")")] if "(" in first else "ok"))
    if "node" in ks:
        pkg = json.loads((root / "package.json").read_text(encoding="utf-8") or "{}")
        scripts = pkg.get("scripts", {})
        deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
        npm = "npm.cmd" if os.name == "nt" else "npm"
        if not (root / "node_modules").exists() and shutil.which(npm):
            sh([npm, "install", "--no-audit", "--no-fund"], root, timeout=1800)
        steps = []
        if "lint" in scripts:
            steps.append(([npm, "run", "-s", "lint"], "lint"))
        if (root / "tsconfig.json").exists() and "typescript" in deps:
            steps.append((["npx.cmd" if os.name == "nt" else "npx", "tsc", "--noEmit"], "typecheck"))
        if "test" in scripts and "no test specified" not in scripts["test"]:
            steps.append(([npm, "test", "--silent"], "tests"))
        elif "build" in scripts and not quick:
            steps.append(([npm, "run", "-s", "build"], "build"))
        for cmd, name in steps:
            ok, out = sh(cmd, root)
            if not ok:
                return False, f"{name.upper()} FAILED:\n" + trim(out)
            reports.append(f"{name} ok")
    if "python" in ks:
        ok, out = sh([sys.executable, "-m", "compileall", "-q", "-x", r"(\.venv|venv|node_modules|target)", "."], root)
        if not ok:
            return False, "PYTHON SYNTAX ERRORS:\n" + trim(out)
        if shutil.which("ruff"):
            ok, out = sh(["ruff", "check", "."], root)
            if not ok:
                return False, "RUFF FAILED:\n" + trim(out)
        has_tests = list(root.glob("tests/*.py")) or list(root.glob("test_*.py")) or list(root.glob("**/test_*.py"))[:1]
        if has_tests:
            ok, out = sh([sys.executable, "-m", "pytest", "-q", "-x"], root)
            if not ok and "No module named pytest" not in out:
                return False, "TESTS FAILED:\n" + trim(out)
        reports.append("python ok")
    if "go" in ks and shutil.which("go"):
        for cmd in (["go", "vet", "./..."], ["go", "test", "./..."]):
            ok, out = sh(cmd, root)
            if not ok:
                return False, f"{' '.join(cmd).upper()} FAILED:\n" + trim(out)
        reports.append("go ok")
    if "web" in ks or ("node" not in ks and list(root.glob("**/*.html"))):
        problems = web_check(root)
        if problems:
            return False, "WEBSITE CHECK FAILED:\n" + "\n".join(problems[:40])
        reports.append("web ok")
    if "rust" not in ks:  # rust_check has its own slop guard
        problems = slop_guard(root)
        if problems:
            return False, ("SLOP GUARD FAILED - the code runs, but the new code is not finished/clean:\n"
                           + "\n".join(problems[:40]))
    if not ks:
        return True, "(no project files detected yet - nothing to check)"
    return True, "ALL CHECKS PASSED (" + ", ".join(reports) + ")"


# ------------------------------------------------------------------ static website checks
class _HTMLCheck(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack, self.problems, self.refs = [], [], []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        line = self.getpos()[0]
        if tag == "img" and "alt" not in a:
            self.problems.append((line, "<img> without alt text (screen readers + SEO)"))
        if tag in ("button", "a") and not a:
            pass
        for k in ("src", "href"):
            v = a.get(k)
            if v and not re.match(r"^(https?:|//|#|mailto:|tel:|data:|javascript:)", v) and "{" not in v:
                self.refs.append((line, v.split("?")[0].split("#")[0]))
        if tag not in VOID:
            self.stack.append((tag, line))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID and self.stack and self.stack[-1][0] == tag:
            self.stack.pop()

    def handle_endtag(self, tag):
        if tag in VOID:
            return
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                for t, ln in self.stack[i + 1:]:
                    if t not in ("p", "li", "td", "th", "tr", "option", "dt", "dd"):
                        self.problems.append((ln, f"<{t}> is never closed"))
                del self.stack[i:]
                return
        self.problems.append((self.getpos()[0], f"</{tag}> closes a tag that was never opened"))


def leftovers(root, html_files):
    """Scratch/test pages and scripts or stylesheets nothing loads - unfinished-looking clutter in a delivered site."""
    out, refs = [], ""
    skip = ("node_modules", "dist", "target", ".git", ".windmill", ".ai")
    web = [p for p in root.rglob("*") if p.suffix.lower() in (".html", ".js", ".mjs", ".css")
           and not any(x in p.parts for x in skip)]
    for p in web:
        try:
            refs += "\n" + p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            pass
    for p in html_files:
        rel = p.relative_to(root).as_posix()
        if re.match(r"^(test|tests|demo|example|sample|scratch|tmp|temp|debug|playground)[-_.]?.*\.html$", p.name, re.I) \
                and p.name not in refs.replace(p.read_text(encoding="utf-8", errors="replace"), ""):
            out.append(f"{rel}: leftover test/scratch page - delete it (or link it from the site if it's a real page)")
    for p in web:
        if p.suffix.lower() == ".html" or any(x in p.parts for x in ("vendor", "lib", "libs")):
            continue
        if p.name not in refs.replace(p.read_text(encoding="utf-8", errors="replace"), "") and p.name not in ("sw.js", "service-worker.js"):
            out.append(f"{p.relative_to(root).as_posix()}: never loaded by any page or script - wire it in or delete it")
    return out


def dead_theme_toggles(root):
    """A script switches a theme class (dark/light) but no style rule actually restyles the page for it."""
    skip = ("node_modules", "dist", ".git")
    js = "\n".join(p.read_text(encoding="utf-8", errors="replace") for p in root.rglob("*.js") if not any(x in p.parts for x in skip))
    css = "\n".join(p.read_text(encoding="utf-8", errors="replace") for p in root.rglob("*.css") if not any(x in p.parts for x in skip))
    css += "\n".join(re.findall(r"<style[^>]*>(.*?)</style>", "\n".join(
        p.read_text(encoding="utf-8", errors="replace") for p in root.rglob("*.html") if not any(x in p.parts for x in skip)), re.S))
    out = []
    for cls in sorted(set(re.findall(r"classList\.(?:toggle|add)\(\s*['\"]([\w-]*(?:dark|light|theme)[\w-]*)['\"]", js))):
        # restyles the page = the class (on html/body/:root) sets colour variables or page colours
        blocks = re.findall(rf"([^{{}}]*\.{re.escape(cls)}\b[^{{}}]*)\{{([^}}]*)\}}", css)
        if not any(re.search(r"--[\w-]+\s*:|(^|;|\s)(background|color)\s*:", body) and
                   not re.search(r"\.(?!" + re.escape(cls) + r")[\w-]+", sel.split(f".{cls}", 1)[1] or "")
                   for sel, body in blocks):
            out.append(f"the script switches the '{cls}' class, but no CSS restyles the page for it - the toggle does "
                       f"nothing visible. Add e.g. `.{cls} {{ --bg: ...; --text: ...; }}` (or html/body.{cls})")
    return out


def web_check(root):
    out = []
    files = [p for p in root.rglob("*.html") if not any(x in p.parts for x in ("node_modules", "dist", "target", ".git"))]
    for p in files[:200]:
        rel = p.relative_to(root).as_posix()
        src = p.read_text(encoding="utf-8", errors="replace")
        c = _HTMLCheck()
        try:
            c.feed(src)
            c.close()
        except Exception as e:  # noqa: BLE001
            out.append(f"{rel}: could not parse HTML ({e})")
            continue
        for t, ln in c.stack:
            if t not in ("html", "body", "head", "p", "li"):
                c.problems.append((ln, f"<{t}> is never closed"))
        for ln, m in c.problems:
            out.append(f"{rel}:{ln}: {m}")
        for ln, ref in c.refs:
            if ref and not (p.parent / ref).exists() and not (root / ref.lstrip("/")).exists():
                out.append(f"{rel}:{ln}: links to '{ref}' which doesn't exist")
        if "<meta name=\"viewport\"" not in src and "<html" in src:
            out.append(f"{rel}: missing <meta name=\"viewport\"> - page will look broken on phones")
    out += leftovers(root, files)
    out += dead_theme_toggles(root)
    for p in files[:200]:  # stand-in copy and dead links anywhere in the site, not only in newly written code
        for n, tag, txt, fix in slop_lines(p.name, p.read_text(encoding="utf-8", errors="replace")):
            if tag in ("TEMPLATE-COPY", "DEAD-LINK"):
                out.append(f"{p.relative_to(root).as_posix()}:{n}: {tag}: {txt[:90]}\n    -> {fix}")
    for p in [p for p in root.rglob("*.css") if "node_modules" not in p.parts][:200]:
        src = re.sub(r"/\*.*?\*/", "", p.read_text(encoding="utf-8", errors="replace"), flags=re.S)
        if src.count("{") != src.count("}"):
            out.append(f"{p.relative_to(root).as_posix()}: unbalanced {{ }} braces")
    node = shutil.which("node")
    if node:
        for p in [p for p in root.rglob("*.js") if not any(x in p.parts for x in ("node_modules", "dist"))][:200]:
            ok, msg = sh([node, "--check", str(p)], root, timeout=60)
            if not ok:
                out.append(f"{p.relative_to(root).as_posix()}: JavaScript syntax error\n{trim(msg, 800)}")
    return out


# ------------------------------------------------------------------ slop guard (non-Rust)
SLOP = [
    (r"\b(TODO|FIXME|XXX|HACK)\b", "MARKER", "finish it and delete the marker"),
    (r"(//|#|/\*|<!--)\s*(\.\.\.|…)", "PLACEHOLDER", "write the real code instead of '...'"),
    (r"(//|#|/\*|<!--).*\b(placeholder|for now|rest of (the )?(code|logic)|implement (this|later|me)|"
     r"not (yet )?implemented|simplified (version|implementation)|"
     r"in a (real|production|full) (app|application|project|implementation|world|scenario|system|version)|"
     r"add more .* here|your code here)\b", "PLACEHOLDER", "finish the code this comment describes"),
    (r"raise NotImplementedError|throw new Error\(\s*['\"`]not implemented", "UNFINISHED", "implement it"),
    (r"^\s*pass\s*(#.*)?$", "EMPTY", "an empty body - implement it (or remove the function)"),
    (r"\blorem ipsum\b", "LOREM", "write real, specific copy - placeholder text looks unfinished"),
    (r"(placeholder\.com|placehold\.co|via\.placeholder|dummyimage\.com|picsum\.photos)", "PLACEHOLDER-IMG",
     "use a real asset (or a designed CSS/SVG illustration)"),
    (r"^\s*debugger;?\s*$", "DEBUG", "remove the debugger statement"),
    # template copy a site generator leaves behind - looks finished in code review, obviously fake on screen
    (r"\b(Project|Product|Item|Post|Article|Feature|Service|Client|Team Member|Testimonial|Card|Image|Photo|Category)"
     r" (Title|Name|Heading|Description|Text)?\s*#?\d+\b(?![-:/.]\d)(?!\s+of\b)", "TEMPLATE-COPY",
     "replace the numbered stand-in with the real thing (ask_user for real project names/details if you don't have them)"),
    (r"\b(Description|Summary|Details) of (this|the|your|my) (project|product|item|game|service|post|work)\b|"
     r"\b(Your|Company|Business|Brand) (Name|Logo|Tagline) (Here|Goes Here)\b|\bSample (text|title|description|content)\b|"
     r"\bText goes here\b|\bAdd (a |your )?description here\b", "TEMPLATE-COPY",
     "write the real, specific content (ask_user for the facts if you don't have them)"),
    (r"<a\b[^>]*\bhref=[\"']#[\"']", "DEAD-LINK",
     "this link goes nowhere: point it at the real page/profile, or use a <button> if it performs an action"),
    (r"\balert\(\s*['\"`]", "STUB", "show an inline message/toast in the page instead of alert()"),
    (r"@ts-ignore(?!.*because)|eslint-disable(?!.*--)", "SUPPRESS", "fix the error instead of silencing it"),
    (r"\bit\.skip\(|\bdescribe\.skip\(|\btest\.skip\(|@pytest\.mark\.skip|xit\(", "TEST-SKIP", "don't skip tests"),
]
CODE_EXT = {".js", ".jsx", ".ts", ".tsx", ".mjs", ".py", ".go", ".html", ".css", ".scss", ".vue", ".svelte",
            ".kt", ".swift", ".dart", ".gd", ".java", ".cs"}


def guard_base(root):
    if os.environ.get("AI_KIT_BASE"):
        return os.environ["AI_KIT_BASE"]
    r = subprocess.run(["git", "rev-parse", "--git-dir"], cwd=root, capture_output=True, text=True)
    if r.returncode == 0:
        f = (Path(root) / r.stdout.strip()) / "ai-kit-base"
        if f.exists():
            return f.read_text().strip()
    return None


def slop_lines(path, text):
    """Check given text (e.g. what the model just wrote). Returns list of problems."""
    ext = Path(path).suffix.lower()
    if ext not in CODE_EXT:
        return []
    is_test = bool(re.search(r"(^|/)(tests?|__tests__|spec)/|\.(test|spec)\.|_test\.|test_", str(path).replace("\\", "/")))
    out = []
    for n, line in enumerate(text.splitlines(), 1):
        for pat, tag, fix in SLOP:
            if tag == "EMPTY" and ext != ".py":
                continue
            if tag == "LOREM" and is_test:
                continue
            if re.search(pat, line, re.I if tag not in ("MARKER",) else 0):
                out.append((n, tag, line.strip()[:100], fix))
                break
        if ext in (".js", ".ts", ".jsx", ".tsx", ".mjs") and not is_test and re.search(r"\bconsole\.log\(", line):
            out.append((n, "DEBUG", line.strip()[:100], "remove console.log (or use a real logger)"))
    return out


def slop_guard(root):
    base = guard_base(root)
    if not base:
        return []
    r = subprocess.run(["git", "diff", "-U0", "--no-color", base, "--", "."], cwd=root, capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    problems, path, ln = [], None, 0
    for line in r.stdout.splitlines():
        if line.startswith("+++ "):
            path = line[6:] if line.startswith("+++ b/") else None
        elif line.startswith("@@"):
            m = re.search(r"\+(\d+)", line)
            ln = int(m.group(1)) if m else 0
        elif line.startswith("+") and path and not path.endswith(".rs"):
            for _, tag, txt, fix in slop_lines(path, line[1:]):
                problems.append(f"{tag:11} {path}:{ln}: {txt}\n    -> {fix}")
            ln += 1
    u = subprocess.run(["git", "ls-files", "--others", "--exclude-standard"], cwd=root, capture_output=True, text=True)
    for p in u.stdout.splitlines():
        fp = Path(root) / p
        if fp.suffix.lower() in CODE_EXT and fp.is_file() and fp.stat().st_size < 500_000:
            for n, tag, txt, fix in slop_lines(p, fp.read_text(encoding="utf-8", errors="replace")):
                problems.append(f"{tag:11} {p}:{n}: {txt}\n    -> {fix}")
    return problems


def mark_base(root):
    r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True)
    g = subprocess.run(["git", "rev-parse", "--git-dir"], cwd=root, capture_output=True, text=True)
    if r.returncode or g.returncode:
        return None
    (Path(root) / g.stdout.strip() / "ai-kit-base").write_text(r.stdout.strip())
    return r.stdout.strip()
