#!/usr/bin/env python3
"""Builds the project's AI index in .ai/ - deterministic, zero model tokens, incremental.

  .ai/graph.json  full symbol graph: every item (fn/struct/enum/trait/impl/...) with signature,
                  line range, doc line; module tree; file -> file "uses" edges
  .ai/map.md      compact, token-budgeted project map the model reads instead of files
  .ai/journal.md  task log (appended by pipeline.py; committed so history is kept)

  python ai_index.py               # (re)build, only re-parses changed files
  python ai_index.py --init        # first time: build + install git hooks + .ai/.gitignore
  python ai_index.py --budget 3000 # map.md token budget (default 2000)
"""
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

VERSION = 1
PARSER_ID = hashlib.sha1(Path(__file__).read_bytes()).hexdigest()[:10]  # cache invalidates when the kit updates
SKIP_DIRS = {"target", ".git", ".ai", "node_modules", "vendor", ".cargo"}

# ------------------------------------------------------------------ Rust lexing
def clean(src):
    """Blank out comments, strings and char literals (keep newlines/quotes) so braces
    and keywords inside them don't confuse the parser."""
    out, i, n = [], 0, len(src)
    while i < n:
        c = src[i]
        two = src[i:i + 2]
        if two == "//":
            j = src.find("\n", i)
            j = n if j < 0 else j
            out.append(" " * (j - i)); i = j
        elif two == "/*":
            depth, j = 1, i + 2
            while j < n and depth:
                if src[j:j + 2] == "/*": depth += 1; j += 2
                elif src[j:j + 2] == "*/": depth -= 1; j += 2
                else: j += 1
            out.append(re.sub(r"[^\n]", " ", src[i:j])); i = j
        elif c == "r" and re.match(r'r#*"', src[i:i + 10]) and (i == 0 or not (src[i - 1].isalnum() or src[i - 1] == "_")):
            m = re.match(r'r(#*)"', src[i:])
            end = src.find('"' + m.group(1), i + len(m.group(0)))
            end = n if end < 0 else end + 1 + len(m.group(1))
            out.append('"' + re.sub(r"[^\n]", " ", src[i + 1:end - 1]) + '"'); i = end
        elif c == '"':
            j = i + 1
            while j < n and src[j] != '"':
                j += 2 if src[j] == "\\" else 1
            out.append('"' + re.sub(r"[^\n]", " ", src[i + 1:j]) + '"'); i = j + 1
        elif c == "'":
            m = re.match(r"'(\\.[^']*|[^\\'])'", src[i:i + 12])
            if m:
                out.append("' '" + " " * (len(m.group(0)) - 3)); i += len(m.group(0))
            else:
                out.append(c); i += 1  # lifetime
        else:
            out.append(c); i += 1
    return "".join(out)


ITEM_RE = re.compile(
    r"^(?P<vis>pub(?:\s*\([^)]*\))?\s+)?(?:default\s+)?(?:const\s+)?(?:async\s+)?(?:unsafe\s+)?"
    r"(?:extern\s+\"[^\"]*\"\s+)?"
    r"(?P<kind>fn|struct|enum|trait|type|const|static|mod|impl|union|macro_rules!)\b\s*(?P<rest>.*)")
NAME_RE = re.compile(r"(?:r#)?([A-Za-z_]\w*)")
USE_RE = re.compile(r"^(?:pub(?:\s*\([^)]*\))?\s+)?use\s+([^;]+);")
ATTR_TEST = re.compile(r"#\[(?:\w+::)*test\b")


def skip_angles(s):
    """'<A, B<C>> rest' -> ' rest' (balanced, ignores '->')."""
    if not s.startswith("<"):
        return s
    depth, i = 0, 0
    while i < len(s):
        if s[i] == "<":
            depth += 1
        elif s[i] == ">" and (i == 0 or s[i - 1] != "-"):
            depth -= 1
            if depth == 0:
                return s[i + 1:]
        i += 1
    return ""


def type_base(x):
    """'&mut foo::Bar<T>' -> 'Bar' ; 'dyn Fn(u8)' -> 'Fn'"""
    x = x.strip()
    if x == "!":
        return "!"
    x = re.sub(r"^(&\s*('\w+\s+)?(mut\s+)?|\*(const|mut)\s+|dyn\s+|!)", "", x).strip()
    if x[:1] in "([":  # tuple / array / slice types: keep them, compacted
        return re.sub(r"\s+", " ", x)[:40] or "?"
    depth, cut = 0, len(x)
    for i, ch in enumerate(x):  # strip generic args / fn args
        if ch in "<(":
            cut = i
            break
    x = x[:cut].strip().split("::")[-1].strip("&*[] ")
    return x or "?"


def impl_name(rest):
    """'impl<T> Trait for Type<T> where ..' -> ('Type', 'Trait')"""
    s = skip_angles(rest.strip()).split("{")[0]
    s = re.split(r"\bwhere\b", s)[0].strip()
    # split on ' for ' at angle-depth 0
    depth, split_at = 0, -1
    for m in re.finditer(r"<|>|\bfor\b", s):
        t = m.group(0)
        if t == "<":
            depth += 1
        elif t == ">" and s[m.start() - 1:m.start()] != "-":
            depth -= 1
        elif t == "for" and depth == 0:
            split_at = m.start()
    if split_at >= 0:
        return type_base(s[split_at + 3:]), type_base(s[:split_at])
    return type_base(s), None


def parse_rust(src):
    """Return (items, uses, mods_declared, module_doc)."""
    cl = clean(src).splitlines()
    raw = src.splitlines()
    items, uses, mods = [], [], []
    stack = []  # dicts: kind, name, depth, item(index or None)
    depth = 0
    pending = None  # item waiting for '{' or ';'
    mod_doc = " ".join(l.strip()[3:].strip() for l in raw[:40] if l.strip().startswith("//!"))[:160]

    def in_test():
        return any(s.get("test") for s in stack)

    def ctx_ok():
        top = stack[-1] if stack else None
        if top is None:
            return depth == 0
        return top["kind"] in ("mod", "impl", "trait") and depth == top["depth"]

    def doc_and_attrs(i):
        doc, test, j = "", False, i - 1
        while j >= 0:
            t = raw[j].strip()
            if t.startswith("///"):
                doc = t[3:].strip() or doc
            elif t.startswith("#["):
                test = test or bool(ATTR_TEST.match(t))
            elif t == "" or t.startswith("//"):
                pass
            else:
                break
            j -= 1
        return doc[:120], test

    for i, line in enumerate(cl):
        s = line.strip()
        if pending is None and ctx_ok():
            um = USE_RE.match(s)
            if um:
                uses.append(re.sub(r"\s+", "", um.group(1)))
            m = ITEM_RE.match(s)
            if m:
                if True:
                    kind, rest = m.group("kind"), m.group("rest")
                    if kind == "impl":
                        name, trait = impl_name(rest)
                    else:
                        nm = NAME_RE.match(rest)
                        name, trait = (nm.group(1) if nm else "?"), None
                    if not (kind == "const" and name == "fn"):
                        doc, is_test = doc_and_attrs(i)
                        top = stack[-1] if stack else None
                        it = {"kind": kind, "name": name, "line": i + 1, "end": i + 1,
                              "pub": bool(m.group("vis")) or (top is not None and top["kind"] == "trait"),
                              "parent": top["name"] if top and top["kind"] in ("impl", "trait") else None,
                              "test": is_test or in_test(), "doc": doc, "sig": "", "fields": [],
                              "mod": "::".join(x["name"] for x in stack if x["kind"] == "mod")}
                        if trait:
                            it["trait"] = trait
                        items.append(it)
                        pending = {"idx": len(items) - 1, "text": ""}
                        if kind == "mod" and s.rstrip().endswith(";"):
                            mods.append(name)
        elif pending is None and stack and stack[-1]["kind"] in ("struct", "enum") and depth == stack[-1]["depth"]:
            fm = re.match(r"^(?:pub(?:\s*\([^)]*\))?\s+)?([A-Za-z_]\w*)\s*[:({,]?", s)
            if fm and s and not s.startswith("#"):
                items[stack[-1]["item"]]["fields"].append(fm.group(1))

        if pending is not None:
            cut = re.search(r"[{;]", line)
            pending["text"] += " " + (line[:cut.start()] if cut else line)
            if cut:
                it = items[pending["idx"]]
                full = re.sub(r"\s+", " ", pending["text"]).strip()
                it["sig"] = full[:180]
                if it["kind"] == "impl" and (not it["name"] or it["name"] == "?" or "\n" not in pending["text"][:1]):
                    m2 = re.search(r"\bimpl\b(.*)", full)
                    if m2:
                        nm, tr = impl_name(m2.group(1))
                        if nm and nm != "?":
                            it["name"] = nm
                            if tr:
                                it["trait"] = tr
                if line[cut.start()] == "{":
                    pending["open"] = True
                else:
                    pending = None

        for ch in line:
            if ch == "{":
                depth += 1
                if pending is not None and pending.get("open"):
                    it = items[pending["idx"]]
                    stack.append({"kind": it["kind"], "name": it["name"], "depth": depth,
                                  "item": pending["idx"],
                                  "test": it["kind"] == "mod" and (it["test"] or it["name"] == "tests")})
                    if it["kind"] == "mod" and stack[-1]["test"]:
                        it["test"] = True
                    pending = None
            elif ch == "}":
                if stack and depth == stack[-1]["depth"]:
                    items[stack[-1]["item"]]["end"] = i + 1
                    stack.pop()
                depth = max(0, depth - 1)
    for it in items:
        if it["end"] < it["line"]:
            it["end"] = it["line"]
        if it["kind"] in ("struct", "enum", "union") and not it["fields"]:
            body = "\n".join(cl[it["line"] - 1:it["end"]])
            if "{" in body:
                inner, d, cur, parts = body[body.index("{") + 1:body.rindex("}")] if "}" in body else "", 0, "", []
                for ch in inner:
                    d += ch in "({<[" ; d -= ch in ")}>]"
                    if ch == "," and d == 0:
                        parts.append(cur); cur = ""
                    else:
                        cur += ch
                parts.append(cur)
                for part in parts:
                    fm = re.match(r"\s*(?:#\[[^\]]*\]\s*)*(?:pub(?:\s*\([^)]*\))?\s+)?([A-Za-z_]\w*)", part)
                    if fm:
                        it["fields"].append(fm.group(1))
    return items, uses, mods, mod_doc


# ------------------------------------------------------------------ project layout
def find_crates(root):
    crates = []
    for toml in root.rglob("Cargo.toml"):
        if any(p in SKIP_DIRS for p in toml.relative_to(root).parts):
            continue
        t = toml.read_text(encoding="utf-8", errors="replace")
        m = re.search(r'^\[package\][^\[]*?^name\s*=\s*"([^"]+)"', t, re.M | re.S)
        if not m:
            continue
        deps = []
        for sec in re.finditer(r"^\[(?:[\w.-]*\.)?(?:dev-)?dependencies\]\s*\n(.*?)(?=^\[|\Z)", t, re.M | re.S):
            deps += re.findall(r"^([A-Za-z0-9_-]+)\s*=", sec.group(1), re.M)
        for sec in re.finditer(r"^\[(?:dev-)?dependencies\.([A-Za-z0-9_-]+)\]", t, re.M):
            deps.append(sec.group(1))
        d = toml.parent
        kinds = [k for k, f in (("lib", "src/lib.rs"), ("bin", "src/main.rs")) if (d / f).exists()]
        crates.append({"name": m.group(1), "ident": m.group(1).replace("-", "_"),
                       "dir": str(d.relative_to(root)).replace("\\", "/") if d != root else ".",
                       "kinds": kinds, "deps": sorted(set(deps))})
    return crates


def module_path(rel, crate_dir):
    """src/a/b.rs -> a::b ; src/a/mod.rs -> a ; src/lib.rs -> '' ; tests/x.rs -> tests::x"""
    p = rel if crate_dir == "." else rel[len(crate_dir) + 1:]
    parts = p[:-3].split("/")
    if parts[0] == "src":
        parts = parts[1:]
        if parts and parts[-1] in ("lib", "main", "mod"):
            parts = parts[:-1]
        if parts[:1] == ["bin"]:
            parts = parts[1:]
        return "::".join(parts)
    return "::".join(parts)  # tests/, benches/, examples/


def rs_files(root):
    r = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard", "--", "*.rs"],
                       cwd=root, capture_output=True, text=True)
    if r.returncode == 0:
        files = [f for f in r.stdout.splitlines() if f and (root / f).exists()]
    else:
        files = [str(p.relative_to(root)).replace("\\", "/") for p in root.rglob("*.rs")]
    return sorted(f for f in files if not any(part in SKIP_DIRS for part in f.split("/")))


# ------------------------------------------------------------------ build
def build(root, budget=2000, quiet=False):
    t0 = time.time()
    ai = root / ".ai"
    ai.mkdir(exist_ok=True)
    if not (ai / ".gitignore").exists():
        (ai / ".gitignore").write_text(GITIGNORE, encoding="utf-8")
    gpath = ai / "graph.json"
    old = {}
    if gpath.exists():
        try:
            g = json.loads(gpath.read_text(encoding="utf-8"))
            if g.get("version") == VERSION and g.get("parser") == PARSER_ID:
                old = g["files"]
        except (ValueError, KeyError):
            pass

    crates = find_crates(root)
    crate_dirs = sorted(crates, key=lambda c: -len(c["dir"]))
    files, parsed = {}, 0
    for rel in rs_files(root):
        data = (root / rel).read_bytes()
        h = hashlib.sha1(data).hexdigest()[:16]
        crate = next((c for c in crate_dirs if c["dir"] == "." or rel.startswith(c["dir"] + "/")), None)
        if rel in old and old[rel]["hash"] == h:
            files[rel] = old[rel]
            continue
        items, uses, mods, mdoc = parse_rust(data.decode("utf-8", errors="replace"))
        parsed += 1
        files[rel] = {"hash": h, "crate": crate["name"] if crate else None,
                      "module": module_path(rel, crate["dir"]) if crate else rel[:-3],
                      "doc": mdoc, "items": items, "use_paths": uses, "mods": mods,
                      "lines": data.count(b"\n") + 1}

    # resolve use paths -> file edges
    by_mod = {}
    for rel, f in files.items():
        by_mod[(f["crate"], f["module"])] = rel
    idents = {c["ident"]: c["name"] for c in crates}
    for rel, f in files.items():
        deps = set()
        for u in f["use_paths"]:
            for path in expand_use(u):
                segs = path.split("::")
                crate = f["crate"]
                if segs[0] == "crate":
                    segs = segs[1:]
                elif segs[0] in ("super", "self"):
                    base = f["module"].split("::") if f["module"] else []
                    while segs and segs[0] in ("super", "self"):
                        if segs[0] == "super" and base:
                            base = base[:-1]
                        segs = segs[1:]
                    segs = base + segs
                elif segs[0] in idents:
                    crate, segs = idents[segs[0]], segs[1:]
                else:
                    continue  # external crate / std
                for k in range(len(segs), -1, -1):
                    target = by_mod.get((crate, "::".join(segs[:k])))
                    if target and target != rel:
                        deps.add(target)
                        break
        # `mod x;` children are structural, not usage
        f["uses"] = sorted(deps)
    for f in files.values():
        f["used_by"] = []
    for rel, f in files.items():
        for d in f["uses"]:
            files[d]["used_by"].append(rel)

    graph = {"version": VERSION, "parser": PARSER_ID, "generated": time.strftime("%Y-%m-%d %H:%M"), "crates": crates, "files": files}
    gpath.write_text(json.dumps(graph, separators=(",", ":")), encoding="utf-8")
    (ai / "map.md").write_text(render_map(graph, root, budget), encoding="utf-8")
    if not quiet:
        print(f"ai_index: {len(files)} files ({parsed} re-parsed) in {time.time() - t0:.2f}s -> .ai/map.md "
              f"(~{est_tokens((ai / 'map.md').read_text(encoding='utf-8'))} tokens), .ai/graph.json")
    return graph


def expand_use(u):
    """a::{b, c::{d, e}} -> a::b, a::c::d, a::c::e (drops 'as' aliases, keeps globs' parent)."""
    u = re.sub(r"\bas\s*\w+", "", u.replace(" ", ""))
    m = re.search(r"\{", u)
    if not m:
        return [u.rstrip(":*").rstrip(":")]
    prefix, body, depth, parts, cur = u[:m.start()], u[m.start() + 1:-1], 0, [], ""
    for ch in body:
        if ch == "{": depth += 1
        if ch == "}": depth -= 1
        if ch == "," and depth == 0:
            parts.append(cur); cur = ""
        else:
            cur += ch
    parts.append(cur)
    out = []
    for p in parts:
        if not p:
            continue
        if p == "self":
            out.append(prefix.rstrip(":"))
        else:
            out += expand_use(prefix + p)
    return out


# ------------------------------------------------------------------ map rendering
def est_tokens(s):
    return int(len(s) / 3.6)


def short_sig(it, level):
    if level == 0:
        sig = it["sig"]
        sig = re.sub(r"^pub(\([^)]*\))?\s+", "", sig)
        if it["kind"] in ("struct", "enum", "union") and it["fields"]:
            f = it["fields"][:8]
            sig = re.sub(r"\s*(where.*)?$", "", sig) + "{" + ",".join(f) + ("…" if len(it["fields"]) > 8 else "") + "}"
        return sig
    if level == 1:
        return f"{it['kind']} {it['name']}"
    return it["name"]


def file_block(rel, f, level):
    api = [it for it in f["items"] if it["pub"] and not it["test"] and it["kind"] not in ("mod", "impl", "use")]
    impls = [it for it in f["items"] if it["kind"] == "impl" and not it["test"]]
    tests = sum(1 for it in f["items"] if it["test"] and it["kind"] == "fn")
    head = f"{rel}"
    if f["doc"]:
        head += f" - {f['doc'][:80]}"
    if level >= 3:
        return f"{head} ({len(api)} pub items, {tests} tests)"
    lines = [head]
    top = [it for it in api if not it["parent"]]
    for it in top:
        sig = short_sig(it, level)
        if it.get("mod"):
            sig = f"[{it['mod']}] " + sig
        if it["kind"] == "trait":
            ms = [x["name"] for x in f["items"] if x["parent"] == it["name"] and x["kind"] == "fn"
                  and it["line"] <= x["line"] <= it["end"]]
            if ms:
                sig += ": " + ", ".join(ms[:12])
        lines.append("  " + sig)
    for im in impls:
        methods = [it["name"] for it in f["items"] if it["parent"] == im["name"] and it["kind"] == "fn"
                   and it["line"] >= im["line"] and it["end"] <= im["end"] and (it["pub"] or im.get("trait"))]
        label = f"impl {im.get('trait') + ' for ' if im.get('trait') else ''}{im['name']}"
        if methods or im.get("trait"):
            lines.append(f"  {label}: {', '.join(methods[:12])}{'…' if len(methods) > 12 else ''}")
    if tests:
        lines.append(f"  tests: {tests}")
    return "\n".join(lines)


def render_map(graph, root, budget):
    files = graph["files"]
    crates = graph["crates"]
    header = ["# Project map", "Auto-generated by ai_index.py on every commit - do not edit. "
              "Query details with ai_query.py instead of opening whole files.", ""]
    for c in crates:
        header.append(f"crate {c['name']} ({'+'.join(c['kinds']) or 'other'}) at {c['dir']}"
                      + (f" deps: {', '.join(c['deps'][:15])}" if c["deps"] else ""))
    hubs = sorted(files, key=lambda r: -len(files[r]["used_by"]))[:8]
    hubs = [h for h in hubs if files[h]["used_by"]]
    if hubs:
        header += ["", "## Hubs (most depended-on - change carefully)"]
        header += [f"- {h} (used by {len(files[h]['used_by'])})" for h in hubs]
    journal = root / ".ai" / "journal.md"
    recent = []
    if journal.exists():
        entries = [l for l in journal.read_text(encoding="utf-8").splitlines() if l.startswith("## ")]
        recent = ["", "## Recent work (details: .ai/journal.md)"] + [f"- {e[3:]}" for e in entries[-5:]]

    # order: hubs first, then by path; test-only dirs last
    order = sorted(files, key=lambda r: (r.startswith(("tests/", "benches/", "examples/")) or "/tests/" in r,
                                         -len(files[r]["used_by"]), r))
    # degrade detail per file until under budget
    levels = {r: 0 for r in order}
    def render():
        body = ["", "## Files"] + [file_block(r, files[r], levels[r]) for r in order]
        return "\n".join(header + recent + body) + "\n"
    text = render()
    for lvl in (1, 2, 3):
        for r in reversed(order):  # least important files degrade first
            if est_tokens(text) <= budget:
                return text
            levels[r] = lvl
            text = render()
    return text


# ------------------------------------------------------------------ hooks
HOOK_MARK = "# ai-kit index hook"
GITIGNORE = "# generated files - rebuilt by git hooks\n*\n!.gitignore\n!journal.md\n"


def install(root):
    ai = root / ".ai"
    ai.mkdir(exist_ok=True)
    gi = ai / ".gitignore"
    if not gi.exists():
        gi.write_text(GITIGNORE, encoding="utf-8")
    j = ai / "journal.md"
    if not j.exists():
        j.write_text("# AI work journal\nAppended automatically by pipeline.py. Newest at the bottom.\n", encoding="utf-8")
    r = subprocess.run(["git", "rev-parse", "--git-common-dir"], cwd=root, capture_output=True, text=True)
    if r.returncode != 0:
        print("Not a git repo - skipped hooks.")
        return
    hooks = (root / r.stdout.strip()).resolve() / "hooks"
    hooks.mkdir(exist_ok=True)
    py = sys.executable.replace("\\", "/")
    me = str(Path(__file__).resolve()).replace("\\", "/")
    corpus = me.replace("ai_index.py", "ai_corpus.py")
    line = (f'{HOOK_MARK}\n[ -n "$AI_KIT_NO_INDEX" ] || (("{py}" "{me}" --quiet; "{py}" "{corpus}" deps --quiet) >/dev/null 2>&1 &)\n')
    for name in ("post-commit", "post-merge", "post-checkout", "post-rewrite"):
        h = hooks / name
        existing = h.read_text(encoding="utf-8") if h.exists() else "#!/bin/sh\n"
        if HOOK_MARK in existing:  # upgrade our block in place, keep anything else in the hook
            kept, skip = [], False
            for l in existing.splitlines():
                if l.strip() == HOOK_MARK:
                    skip = True
                    continue
                if skip:
                    skip = False
                    continue
                kept.append(l)
            existing = "\n".join(kept) + "\n"
        h.write_text(existing.rstrip("\n") + "\n" + line, encoding="utf-8")
        os.chmod(h, 0o755)
    print(f"Installed git hooks in {hooks} - the index now refreshes after every commit.")
    print("Commit .ai/.gitignore and .ai/journal.md so the journal is kept with the project.")


def main():
    args = sys.argv[1:]
    r = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True)
    root = Path(r.stdout.strip()) if r.returncode == 0 else Path.cwd()
    budget = int(args[args.index("--budget") + 1]) if "--budget" in args else 2000
    if "--init" in args:
        install(root)
    build(root, budget, quiet="--quiet" in args)


if __name__ == "__main__":
    try:
        main()
    except BrokenPipeError:
        pass
