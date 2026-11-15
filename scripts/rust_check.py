#!/usr/bin/env python3
"""One command the AI uses to verify Rust code:
   cargo fmt -> clippy -> tests -> SLOP GUARD (rejects unfinished / sloppy new code).

Prints a SHORT report so it doesn't flood the model's context.
Exit code 0 only if everything passes. Extra args (Aider appends file names) are ignored.

  python rust_check.py                   # full check
  python rust_check.py --allow-warnings  # clippy warnings don't fail the check
  python rust_check.py --mark-base       # start of an interactive task: guard checks
                                         # everything changed since this commit

The slop guard only looks at lines ADDED since the base commit, so old code
in your project never trips it. The base comes from $AI_KIT_BASE (set by
pipeline.py / best_of_n.py) or from --mark-base.
"""
import os
import re
import subprocess
import sys
from pathlib import Path

MAX_LINES = 150
ENV = {**os.environ, "CARGO_TERM_COLOR": "never", "RUST_BACKTRACE": "0"}


def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", env=ENV)
    return r.returncode, (r.stdout + "\n" + r.stderr).strip()


def cap(lines):
    if len(lines) > MAX_LINES:
        return "\n".join(lines[:MAX_LINES]) + f"\n... ({len(lines) - MAX_LINES} more lines truncated - fix the errors above first)"
    return "\n".join(lines)


def trim_compiler(out):
    """Keep only error/warning blocks, drop 'Compiling foo' noise."""
    keep, block = [], False
    for line in out.splitlines():
        s = line.lstrip()
        if s.startswith(("error", "warning")):
            if s.startswith(("warning: build failed", "error: could not compile")):
                continue
            block = True
        elif s.startswith(("Compiling", "Checking", "Finished", "Running", "Blocking")):
            block = False
            continue
        if block:
            keep.append(line)
    return cap(keep or out.splitlines())


def trim_tests(out):
    lines = out.splitlines()
    if any("error[" in l or l.startswith("error:") for l in lines) and "test result" not in out:
        return trim_compiler(out)  # tests didn't compile
    idx = [i for i, l in enumerate(lines) if l.strip() == "failures:"]
    return cap(lines[idx[0]:]) if idx else cap(lines[-60:])


# ---------------------------------------------------------------- hints for made-up APIs
def _deps_db():
    p = Path(".ai") / "deps.db"
    if not p.exists():
        return None
    import sqlite3
    try:
        return sqlite3.connect(f"file:{p.resolve().as_posix()}?mode=ro", uri=True, timeout=10)
    except sqlite3.Error:
        return None


def _cargo_deps():
    try:
        toml = Path("Cargo.toml").read_text(encoding="utf-8")
    except OSError:
        return set()
    out = set()
    for sec in re.findall(r"^\[(?:[\w.-]*\.)?(?:dev-|build-)?dependencies\]\s*\n(.*?)(?=^\[|\Z)", toml, re.M | re.S):
        out.update(m.replace("-", "_") for m in re.findall(r"^([A-Za-z0-9_-]+)\s*=", sec, re.M))
    return out


def api_hints(out):
    """Turn 'this API doesn't exist' errors into the REAL API of the exact crate versions in Cargo.lock."""
    hints, done = [], set()
    deps = _cargo_deps()
    db = _deps_db()
    for m in re.finditer(r"method `(\w+)` is not a member of trait `([\w:]+)`", out):
        meth, trait = m.group(1), m.group(2).split("::")[-1]
        if trait in done:
            continue
        done.add(trait)
        sigs = []
        if db:
            try:
                for crate, ver, name, sig in db.execute(
                        "SELECT crate, version, name, sig FROM api WHERE name LIKE ? AND kind='fn'", (trait + "::%",)):
                    s = re.sub(r"\s+", " ", sig).strip()
                    if s not in sigs:
                        sigs.append(s)
                    src = f"{crate} {ver}"
            except Exception:  # noqa: BLE001
                sigs = []
        if sigs:
            hints.append(f"`{meth}` is not part of `{trait}` in {src} (your code uses an API from an older or "
                         f"different version). The real methods of `{trait}` are:\n" +
                         "\n".join(f"    {s}" for s in sigs[:14]))
        else:
            hints.append(f"`{meth}` is not part of trait `{trait}` in this version - look it up with "
                         f"references(\"{trait} trait\") instead of guessing.")
    for m in re.finditer(r"(?:cannot find module or crate|unresolved import|use of unresolved module or unlinked crate|"
                         r"can't find crate for) `(\w+)`", out):
        c = m.group(1)
        if c in done or c in ("crate", "self", "super", "std", "core", "alloc") or c in deps:
            continue
        done.add(c)
        hints.append(f"`{c}` is not in Cargo.toml (your crates: {', '.join(sorted(deps)) or 'none'}). Don't add a crate "
                     f"just for this: check if one of yours already does it (e.g. eframe::egui, eframe re-exports). "
                     f"If it's truly needed, run `cargo add {c.replace('_', '-')}` and look up its API with references().")
    for m in re.finditer(r"module `(\w+)` is private", out):
        if m.group(1) in done:
            continue
        done.add(m.group(1))
        hints.append(f"`{m.group(1)}` is private inside that crate - you can't use it. Use the public items (look them "
                     f"up with references()); old tutorials often show paths that no longer exist.")
    if not hints:
        return ""
    return "\n\nHOW TO FIX (from the real source of your dependencies):\n- " + "\n- ".join(hints)


# ---------------------------------------------------------------- files cargo never builds
MOD_DECL = re.compile(r"^\s*(?:#\[[^\]]*\]\s*)*(?:pub(?:\([^)]*\))?\s+)?mod\s+(\w+)\s*;", re.M)
PATH_ATTR = re.compile(r'#\[path\s*=\s*"([^"]+)"\]\s*(?:pub(?:\([^)]*\))?\s+)?mod\s+\w+\s*;')


def _strip_comments(src):
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return re.sub(r"//[^\n]*", "", src)


def orphan_files(crate_dir="."):
    """.rs files under src/ that no `mod x;` reaches: cargo never compiles them, so their tests never run."""
    root = Path(crate_dir)
    src = root / "src"
    if not src.is_dir():
        return []
    starts = [p for p in (src / "main.rs", src / "lib.rs") if p.exists()]
    starts += list((src / "bin").glob("*.rs")) + list((src / "bin").glob("*/main.rs"))
    seen, todo = set(), list(starts)
    while todo:
        f = todo.pop()
        if f.resolve() in seen or not f.exists():
            continue
        seen.add(f.resolve())
        try:
            text = _strip_comments(f.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
        own = f.parent if f.name in ("main.rs", "lib.rs", "mod.rs") or f.parent.name == "bin" else f.with_suffix("")
        for pth in PATH_ATTR.findall(text):
            todo.append(f.parent / pth)
        for name in MOD_DECL.findall(text):
            todo += [own / f"{name}.rs", own / name / "mod.rs"]
    out = []
    for p in sorted(src.rglob("*.rs")):
        if p.resolve() not in seen:
            out.append(p.relative_to(root).as_posix())
    return out


def orphan_problems():
    probs = []
    has_lib = Path("src/lib.rs").exists()
    for rel in orphan_files("."):
        test_like = "/tests/" in "/" + rel or "test" in Path(rel).stem
        if test_like:
            probs.append(f"NOT-COMPILED {rel}: no `mod` line includes this file, so cargo never builds it and its tests "
                         f"never run (\"all checks pass\" would be a lie).\n    -> unit tests go in a `#[cfg(test)] mod tests` "
                         f"at the bottom of the file they test; integration tests go in tests/{Path(rel).name}"
                         + ("" if has_lib else " and need a src/lib.rs (a binary-only crate can't be imported by tests/)"))
        else:
            probs.append(f"NOT-COMPILED {rel}: no `mod` line includes this file, so cargo never builds it.\n    -> add "
                         f"`mod {Path(rel).stem};` where it belongs, or delete the file if it's not needed")
    if not has_lib:
        for t in sorted(Path("tests").glob("*.rs")) if Path("tests").is_dir() else []:
            name = Path.cwd().name.replace("-", "_")
            if re.search(rf"\buse\s+{re.escape(name)}::", t.read_text(encoding="utf-8", errors="replace")):
                probs.append(f"NO-LIB      {t.as_posix()} imports `{name}::` but there is no src/lib.rs\n    -> move the "
                             f"logic into src/lib.rs (main.rs keeps just fn main) so tests can use it")
    return probs


# ---------------------------------------------------------------- slop guard
def git(*args):
    return run(["git", *args])


def base_file():
    code, gd = git("rev-parse", "--git-dir")
    return Path(gd) / "ai-kit-base" if code == 0 else None


def resolve_base():
    if os.environ.get("AI_KIT_BASE"):
        return os.environ["AI_KIT_BASE"]
    f = base_file()
    if f and f.exists():
        return f.read_text().strip()
    return None


MARKER = re.compile(r"//.*\b(TODO|FIXME|XXX|HACK)\b")  # case-sensitive, anywhere
PLACEHOLDER = re.compile(  # case-insensitive, plain // comments in non-test code
    r"^\s*//(?![/!]).*\b(placeholder|for now|simplified (version|implementation|logic)|"
    r"rest of (the )?(code|logic|implementation)|remaining (code|logic|cases)|"
    r"implement(ed)? (this|later|me|properly)|not (yet )?implemented|left as an exercise|"
    r"in a real (app|application|implementation|project)|would (go|be) here|add more .* here)\b", re.I)
ELLIPSIS_COMMENT = re.compile(r"^\s*//+\s*\.\.\.")


def test_region_start(path):
    """Line number where `#[cfg(test)]` starts in a file (lines after it count as test code)."""
    try:
        for i, line in enumerate(Path(path).read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if line.strip().startswith("#[cfg(test)]"):
                return i
    except OSError:
        pass
    return 10**9


def added_lines(base):
    """Yield (path, lineno, text) for every line added since base, plus counts of removed #[test]."""
    _, diff = git("diff", "-U0", "--no-color", base, "--", "*.rs")
    path, ln, removed_tests, added_tests = None, 0, 0, 0
    out = []
    for line in diff.splitlines():
        if line.startswith("+++ "):
            path = line[6:] if line.startswith("+++ b/") else None
        elif line.startswith("@@"):
            m = re.search(r"\+(\d+)", line)
            ln = int(m.group(1)) if m else 0
        elif line.startswith("+") and path:
            out.append((path, ln, line[1:]))
            added_tests += "#[test]" in line or "#[tokio::test" in line
            ln += 1
        elif line.startswith("-") and not line.startswith("---"):
            removed_tests += "#[test]" in line or "#[tokio::test" in line
    _, untracked = git("ls-files", "--others", "--exclude-standard", "--", "*.rs")
    for p in untracked.splitlines():
        try:
            for i, t in enumerate(Path(p).read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                out.append((p, i, t))
        except OSError:
            pass
    return out


TEST_FN = re.compile(r"#\[(?:\w+::)*test[^\]]*\][\s\S]*?\bfn\s+(\w+)")


def removed_tests(base):
    """Test functions that existed at base but no longer exist anywhere in the crate."""
    _, changed = git("diff", "--name-only", base, "--", "*.rs")
    before = []
    for p in changed.splitlines():
        code, old = git("show", f"{base}:{p}")
        if code == 0:
            before += [(p, n) for n in TEST_FN.findall(old)]
    if not before:
        return []
    now = set()
    _, files = git("ls-files", "--cached", "--others", "--exclude-standard", "--", "*.rs")
    for f in files.splitlines():
        try:
            now.update(TEST_FN.findall(Path(f).read_text(encoding="utf-8", errors="replace")))
        except OSError:
            pass
    return [(p, n) for p, n in before if n not in now]


def slop_guard(base):
    lines = added_lines(base)
    problems = []
    test_start = {}
    file_cache = {}

    def is_test(p, n):
        if p.startswith(("tests/", "benches/", "examples/")) or "/tests/" in p:
            return True
        if p not in test_start:
            test_start[p] = test_region_start(p)
        return n > test_start[p]

    def ctx(p, n, k=3):
        if p not in file_cache:
            try:
                file_cache[p] = Path(p).read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                file_cache[p] = []
        return "\n".join(file_cache[p][max(0, n - 1 - k):n - 1])

    for p, n, t in lines:
        code = t.split("//")[0]
        where = f"{p}:{n}: {t.strip()[:100]}"
        if re.search(r"\b(todo|unimplemented)!\s*\(", code):
            problems.append(f"UNFINISHED  {where}\n    -> implement it fully; todo!/unimplemented! are not allowed")
        elif MARKER.search(t) or (not is_test(p, n) and (PLACEHOLDER.search(t) or ELLIPSIS_COMMENT.search(t))):
            problems.append(f"PLACEHOLDER {where}\n    -> finish the code this comment describes, then delete the comment")
        if re.search(r"\bdbg!\s*\(", code):
            problems.append(f"DEBUG       {where}\n    -> remove dbg!")
        if re.search(r"#\[\s*ignore", t):
            problems.append(f"TEST-SKIP   {where}\n    -> don't skip tests; make them pass")
        if re.search(r"#!?\[\s*allow\s*\(", t) and "reason" not in t:
            problems.append(f"ALLOW       {where}\n    -> fix the lint instead; if truly wrong use #[allow(lint, reason = \"why\")]")
        if re.search(r"assert!\s*\(\s*true\s*\)", code):
            problems.append(f"FAKE-TEST   {where}\n    -> assert real behaviour")
        if not is_test(p, n):
            if re.search(r"\.unwrap\(\)", code):
                problems.append(f"UNWRAP      {where}\n    -> propagate with ? or use expect(\"why this cannot fail\")")
            if re.search(r"\bunsafe\b", re.sub(r"#!?\[\s*unsafe\s*\(", "", code)) and "SAFETY:" not in (ctx(p, n) + t):
                problems.append(f"UNSAFE      {where}\n    -> add a // SAFETY: comment above, or avoid unsafe")
    for p, name in removed_tests(base):
        problems.append(f"TEST-REMOVED {p}: test `{name}` was deleted\n    -> restore it unless the task explicitly asked to remove it")
    return problems


# ---------------------------------------------------------------- main
def main():
    if "--mark-base" in sys.argv:
        code, head = git("rev-parse", "HEAD")
        f = base_file()
        if code != 0 or not f:
            print("Not a git repo with commits.")
            return 1
        f.write_text(head.strip())
        print(f"Slop guard base set to {head.strip()[:10]}. New code since this commit will be checked.")
        return 0

    allow_warnings = "--allow-warnings" in sys.argv
    if not os.path.exists("Cargo.toml"):
        print("rust_check: no Cargo.toml in the current directory")
        return 1

    run(["cargo", "fmt", "--all"])

    clippy = ["cargo", "clippy", "--all-targets", "--all-features", "--quiet"]
    if not allow_warnings:
        clippy += ["--", "-D", "warnings"]
    code, out = run(clippy)
    if code != 0:
        print("BUILD/CLIPPY FAILED (fix these; clippy warnings count as errors):\n")
        print(trim_compiler(out) + api_hints(out))
        return 1

    code, out = run(["cargo", "test", "--all-features", "--quiet"])
    if code != 0:
        print("TESTS FAILED:\n")
        print(trim_tests(out) + api_hints(out))
        return 1
    summary = [l for l in out.splitlines() if l.startswith("test result")]
    orphans = orphan_problems()
    if orphans:
        print("FILES CARGO NEVER BUILDS:\n")
        print(cap("\n".join(orphans).splitlines()))
        return 1

    base = resolve_base()
    if base:
        problems = slop_guard(base)
        if problems:
            print("SLOP GUARD FAILED - the code compiles and tests pass, but the new code is not finished/clean:\n")
            print(cap("\n".join(problems).splitlines()))
            return 1
        guard = "slop guard"
    else:
        guard = "slop guard SKIPPED (run with --mark-base first)"

    print(f"ALL CHECKS PASSED (fmt, clippy, tests, {guard})")
    print("\n".join(summary[-5:]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
