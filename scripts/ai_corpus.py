#!/usr/bin/env python3
"""Reference library + dependency index, searched automatically before every pipeline step.

Two indexes, both SQLite full-text (BM25) - no GPU, no embedding model, no model tokens to build:

  CORPUS  (shared by all projects)  <windmill folder>/corpus/
          Big library of well-organised, license-checked Rust code: game engines + games,
          mobile, web front/back-end, DB, async, CLI, GUI, and the official Rust books.
          Gives the model PATTERNS ("how do real projects structure an inventory/plugin/router").

  DEPS    (per project)             <project>/.ai/deps.db
          Public API of the EXACT crate versions in your Cargo.lock, read from cargo's
          local source cache. Gives the model AUTHORITATIVE signatures - stops it
          inventing APIs from the wrong bevy/axum/etc. version.

  python ai_corpus.py fetch                     # download the library (several GB; once)
  python ai_corpus.py fetch --cats game,mobile  # only some categories
  python ai_corpus.py fetch --only bevy,axum     # only some repos
  python ai_corpus.py fetch --include-copyleft  # also GPL/MPL repos (e.g. veloren RPG)
  python ai_corpus.py index                     # build/refresh corpus index (incremental)
  python ai_corpus.py add <git-url> --cat game  # add any repo (e.g. your own old projects)
  python ai_corpus.py discover bevy             # find EVERY public Bevy project (catalog, crates.io, itch.io, GitHub)
  python ai_corpus.py fetch --discovered bevy   # download them all (licence-checked, 6 in parallel; --max N)
  python ai_corpus.py deps                      # (in a project) index Cargo.lock versions
  python ai_corpus.py search "inventory slot stacking"     [--cat game] [--deps|--corpus]
  python ai_corpus.py stats
"""
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import ai_index  # noqa: E402

KIT = Path(__file__).resolve().parent.parent
CORPUS = Path(os.environ.get("AI_KIT_CORPUS", KIT / "corpus"))
MANIFEST = KIT / "corpus_manifest.json"
EXTRA = CORPUS / "extra_repos.json"
CORPUS_DB = CORPUS / "corpus.db"
MAX_FILE = 400_000
CHUNK_LINES = 90
ROLE_WEIGHT = {"example": 1.7, "doc": 1.3, "lib": 1.0, "test": 0.7}
PERMISSIVE = ("MIT", "APACHE", "BSD", "ZLIB", "CC0", "UNLICENSE", "ISC", "BSL-1.0", "0BSD")
COPYLEFT = ("GPL", "MPL", "EUPL", "CDDL", "EPL", "OSL", "SHARE-ALIKE", "CC-BY-SA")
STOP = {"the", "and", "for", "with", "that", "this", "from", "into", "make", "when", "should", "use",
        "new", "all", "are", "not", "can", "has", "have", "add", "fix", "code", "file", "files", "step",
        "self", "pub", "let", "mut", "impl", "struct", "enum", "return", "true", "false", "none", "some",
        "string", "result", "option", "vec", "test", "tests", "done", "when", "each", "only", "must"}


def log(m, quiet=False):
    if not quiet:
        print(m, flush=True)


# ------------------------------------------------------------------ text helpers
def split_ident(s):
    """InventorySlot / inventory_slot / HTTPServer -> inventory slot http server"""
    out = []
    for w in re.findall(r"[A-Za-z][A-Za-z0-9]*", s):
        out += re.findall(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|[0-9]+", w)
    return [w.lower() for w in out if len(w) > 1]


# Programming synonyms so word search finds code that uses different vocabulary than the task
SYNONYMS = {
    "save": ["serialize", "persist", "write", "store"], "load": ["deserialize", "read", "restore", "asset"],
    "persist": ["serialize", "save", "store"], "disk": ["file", "fs", "path"], "file": ["fs", "path"],
    "inventory": ["item", "slot", "stack", "bag", "container", "equipment"], "item": ["inventory", "slot"],
    "move": ["movement", "velocity", "translation", "transform"], "movement": ["velocity", "translation"],
    "player": ["character", "controller"], "enemy": ["npc", "ai", "monster"], "spawn": ["insert", "create"],
    "keyboard": ["key", "keycode", "input"], "mouse": ["cursor", "pointer", "click"], "touch": ["finger", "gesture"],
    "camera": ["view", "projection", "follow"], "collision": ["collider", "contact", "intersect", "physics"],
    "health": ["damage", "hp", "hit"], "score": ["points", "counter", "text"], "menu": ["ui", "button", "screen"],
    "button": ["ui", "interaction", "click"], "ui": ["node", "button", "text", "widget"],
    "animation": ["animate", "sprite", "frame", "atlas"], "sound": ["audio", "play"], "music": ["audio", "play"],
    "level": ["map", "tilemap", "scene"], "map": ["tilemap", "tile", "grid"], "state": ["states", "resource"],
    "endpoint": ["route", "handler", "router"], "api": ["route", "handler", "json"], "route": ["router", "handler"],
    "request": ["handler", "extract", "body"], "response": ["into", "response", "status"],
    "auth": ["authentication", "login", "session", "token", "jwt"], "login": ["auth", "session", "password"],
    "database": ["db", "sql", "query", "pool", "sqlx"], "db": ["database", "sql", "pool"],
    "config": ["settings", "configuration", "env"], "error": ["errors", "err", "result", "thiserror"],
    "async": ["await", "tokio", "future", "spawn"], "concurrent": ["thread", "spawn", "channel", "mutex"],
    "parse": ["parser", "from", "deserialize"], "cli": ["args", "command", "clap", "parser"],
    "mobile": ["android", "ios", "touch"], "android": ["mobile", "activity", "apk", "ndk", "jni"], "ios": ["mobile"],
    "apk": ["android", "gradle", "manifest"], "phone": ["android", "mobile", "touch"], "tablet": ["android", "mobile"],
    "permission": ["manifest", "android"], "jni": ["ndk", "android", "java"], "lifecycle": ["suspend", "resume", "pause"],
    "web": ["http", "server", "html"], "page": ["view", "component", "html", "route"],
    "component": ["view", "props", "render"], "random": ["rand", "rng"], "timer": ["time", "duration", "elapsed"],
}


def query_terms(text, limit=28):
    seen, out = set(), []
    words = [w for w in split_ident(text) if len(w) > 2 and w not in STOP]
    for w in words:  # the user's own words first
        if w not in seen:
            seen.add(w)
            out.append(w)
    for w in words:  # then synonyms
        for syn in SYNONYMS.get(w, []):
            if syn not in seen and len(out) < limit:
                seen.add(syn)
                out.append(syn)
    return out[:limit]


def fts_query(terms):
    return " OR ".join(f'"{t}"' for t in terms)


def est_tokens(s):
    return int(len(s) / 3.6)


# ------------------------------------------------------------------ licenses
def detect_license(repo_dir, _depth=0):
    lic, kind = _detect_license(repo_dir)
    if kind == "unknown" and _depth == 0:  # workspaces often keep licences in member crates
        found = [_detect_license(d) for d in sorted(Path(repo_dir).iterdir()) if d.is_dir() and not d.name.startswith(".")]
        found = [f for f in found if f[1] != "unknown"]
        if found:
            kinds = {k for _, k in found}
            return " / ".join(dict.fromkeys(l for l, _ in found)), ("copyleft" if "copyleft" in kinds else "permissive")
    return lic, kind


def _detect_license(repo_dir):
    texts = []
    for toml in (repo_dir / "Cargo.toml",):
        if toml.exists():
            m = re.search(r'^\s*license\s*=\s*"([^"]+)"', toml.read_text(encoding="utf-8", errors="replace"), re.M)
            if m:
                texts.append(m.group(1))
    for lf in sorted(repo_dir.iterdir()):
        if not lf.is_file() or not re.match(r"^(licen[cs]e|copying|unlicense)", lf.name.lower()):
            continue
        head = lf.read_text(encoding="utf-8", errors="replace")[:3000]
        name = lf.name.upper()
        if "MIT" in name or "Permission is hereby granted, free of charge" in head: texts.append("MIT")
        if "APACHE" in name or "Apache License" in head: texts.append("Apache-2.0")
        if "CC0" in name or "CC0" in head or ("Creative Commons Legal Code" in head and "ShareAlike" not in head and "Attribution" not in head): texts.append("CC0-1.0")
        if "GNU AFFERO" in head.upper(): texts.append("AGPL-3.0")
        elif "GNU LESSER" in head.upper(): texts.append("LGPL")
        elif "GNU GENERAL PUBLIC" in head.upper(): texts.append("GPL-3.0")
        if "Mozilla Public License" in head: texts.append("MPL-2.0")
        if "ShareAlike" in head or "Share-Alike" in head or "CC-BY-SA" in head.upper(): texts.append("CC-BY-SA (share-alike)")
        if "BSD" in name or "Redistribution and use in source and binary forms" in head: texts.append("BSD")
        if "zlib" in head.lower() and "altered source versions" in head: texts.append("Zlib")
        if "This is free and unencumbered software" in head: texts.append("Unlicense")
    lic = " / ".join(dict.fromkeys(texts)) or "UNKNOWN"
    up = lic.upper()
    if any(c in up for c in COPYLEFT):
        # "MIT OR GPL" style dual licence counts as permissive (you may choose MIT)
        kind = "permissive" if (" OR " in up and any(p in up for p in PERMISSIVE)) else "copyleft"
    elif any(p in up for p in PERMISSIVE):
        kind = "permissive"
    else:
        kind = "unknown"
    return lic, kind


# ------------------------------------------------------------------ manifest
def load_manifest(include_discovered=True):
    repos = json.loads(MANIFEST.read_text(encoding="utf-8"))["repos"]
    if EXTRA.exists():
        repos += json.loads(EXTRA.read_text(encoding="utf-8"))
    if include_discovered:
        for f in sorted((CORPUS / "discovered").glob("*.json")) if (CORPUS / "discovered").exists() else []:
            try:
                repos += [dict(r, discovered=True) for r in json.loads(f.read_text(encoding="utf-8"))["repos"]]
            except (ValueError, KeyError):
                pass
    return repos


def fetch_one(r, copyleft=False, update=False):
    """Download one repo (shallow, big binaries skipped), verify its licence. Returns a log line."""
    dest = CORPUS / "repos" / r["name"]
    if dest.exists():
        if not update:
            return None
        subprocess.run(["git", "-C", str(dest), "fetch", "-q", "--depth", "1", "origin", r.get("ref") or "HEAD"],
                       capture_output=True)
        subprocess.run(["git", "-C", str(dest), "reset", "-q", "--hard", "FETCH_HEAD"], capture_output=True)
        return f"updated {r['name']}"
    cmd = ["git", "clone", "--depth", "1", "--filter=blob:limit=1m", "--single-branch", "-q"]
    if r.get("paths"):
        cmd += ["--sparse"]
    if r.get("ref"):
        cmd += ["--branch", r["ref"]]
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}  # private/deleted repos must fail, not ask for a password
    try:
        rc = subprocess.run(cmd + [r["url"], str(dest)], capture_output=True, env=env, timeout=900).returncode
    except subprocess.TimeoutExpired:
        rc = 1
    if rc == 0 and r.get("paths"):
        rc = subprocess.run(["git", "-C", str(dest), "sparse-checkout", "set", *r["paths"]], capture_output=True).returncode
    if rc != 0:
        shutil.rmtree(dest, ignore_errors=True)
        return f"  !! {r['name']}: download failed (gone, private or too big) - skipped"
    lic, kind = detect_license(dest)
    if kind == "copyleft" and not copyleft:
        shutil.rmtree(dest, ignore_errors=True)
        return f"  skip {r['name']}: {lic} (copyleft; --include-copyleft to keep)"
    if kind == "unknown" and not r.get("mine"):
        shutil.rmtree(dest, ignore_errors=True)
        return f"  skip {r['name']}: no licence = no permission to reuse"
    return f"fetched {r['name']}  [{lic}]"


def cmd_fetch(args):
    from concurrent.futures import ThreadPoolExecutor
    cats = set(str(args.get("--cats", "")).split(",")) - {""}
    only = set(str(args.get("--only", "")).split(",")) - {""}
    copyleft = "--include-copyleft" in args
    update = "--update" in args
    disc = args.get("--discovered")
    limit = int(args.get("--max", 0) or 0)
    jobs = int(args.get("--jobs", 6) or 6)
    (CORPUS / "repos").mkdir(parents=True, exist_ok=True)
    todo = []
    for r in load_manifest(include_discovered=bool(disc)):
        if disc and not (r.get("discovered") and r["name"].startswith(f"{disc}~")):
            continue
        if not disc and r.get("discovered"):
            continue
        if cats and not cats & set(r["cats"]):
            continue
        if only and r["name"] not in only:
            continue
        if r.get("copyleft") and not copyleft:
            log(f"skip  {r['name']:24} (copyleft: use --include-copyleft)")
            continue
        todo.append(r)
    if limit:
        todo = todo[:limit]
    new = [r for r in todo if not (CORPUS / "repos" / r["name"]).exists()]
    log(f"{len(todo)} repos selected, {len(new)} new" + (" (+ updating existing)" if update else "") + f", {jobs} at a time")
    done = 0
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        for line in pool.map(lambda r: fetch_one(r, copyleft, update), todo):
            done += 1
            if line:
                log(f"[{done}/{len(todo)}] {line}")
    log("Done. Now run:  python ai_corpus.py index")


def cmd_add(args):
    url = args["_pos"][0]
    cats = args.get("--cat", "mine").split(",")
    name = args.get("--name") or re.sub(r"\.git$", "", url.rstrip("/").split("/")[-1])
    extra = json.loads(EXTRA.read_text(encoding="utf-8")) if EXTRA.exists() else []
    if Path(url).expanduser().is_dir():  # a local folder
        url = str(Path(url).expanduser().resolve())
    extra = [e for e in extra if e["name"] != name] + [{"name": name, "url": url, "cats": cats, "mine": True}]
    CORPUS.mkdir(parents=True, exist_ok=True)
    EXTRA.write_text(json.dumps(extra, indent=2), encoding="utf-8")
    log(f"Added {name} [{', '.join(cats)}]. Fetching...")
    cmd_fetch({"--cats": ",".join(cats), "--include-copyleft": True} if "--include-copyleft" in args else {"--cats": ",".join(cats)})
    cmd_index({})


# ------------------------------------------------------------------ chunking
def role_of(path, item_test=False):
    p = "/" + path.lower()
    if item_test or "/tests/" in p or "/benches/" in p:
        return "test"
    if any(x in p for x in ("/examples/", "/example/", "/demo", "/samples/", "/src/bin/", "/templates/")):
        return "example"
    if p.endswith(".md"):
        return "doc"
    return "lib"


def rust_chunks(path, src):
    lines = src.splitlines()
    role0 = role_of(path)
    # small example files are most useful whole
    if role0 == "example" and len(lines) <= CHUNK_LINES * 2:
        items, _, _, doc = ai_index.parse_rust(src)
        names = [it["name"] for it in items if not it.get("parent")][:20]
        yield {"name": Path(path).stem, "kind": "file", "line": 1, "sig": f"example file: {', '.join(names)}",
               "doc": doc, "body": src, "role": "example"}
        return
    items, _, _, _ = ai_index.parse_rust(src)
    for it in items:
        if it["kind"] in ("mod", "use", "macro_rules!") or (it.get("parent") and it["kind"] != "fn"):
            continue
        size = it["end"] - it["line"] + 1
        if it.get("parent"):  # method: only chunk separately when its impl is too big
            owner = next((o for o in items if o["kind"] in ("impl", "trait") and o["name"] == it["parent"]
                          and o["line"] <= it["line"] <= o["end"]), None)
            if owner and owner["end"] - owner["line"] + 1 <= CHUNK_LINES:
                continue
        elif it["kind"] in ("impl", "trait") and size > CHUNK_LINES:
            continue  # methods are chunked individually instead
        start = it["line"] - 1
        while start > 0 and lines[start - 1].strip().startswith(("///", "#[")):
            start -= 1
        body = "\n".join(lines[start:min(it["end"], start + CHUNK_LINES)])
        name = (f"{it['parent']}::" if it.get("parent") else "") + it["name"]
        if it["kind"] == "impl" and it.get("trait"):
            name = f"{it['trait']} for {it['name']}"
        yield {"name": name, "kind": it["kind"], "line": start + 1, "sig": it["sig"], "doc": it["doc"],
               "body": body, "role": role_of(path, it["test"])}


def md_chunks(path, src):
    sections, cur, title, line0 = [], [], Path(path).stem, 1
    for i, l in enumerate(src.splitlines(), 1):
        if re.match(r"^#{1,3} ", l) and cur:
            sections.append((title, line0, cur))
            cur, title, line0 = [], l.lstrip("# ").strip(), i
        elif re.match(r"^#{1,3} ", l):
            title, line0 = l.lstrip("# ").strip(), i
        cur.append(l)
    if cur:
        sections.append((title, line0, cur))
    for title, ln, body in sections:
        text = "\n".join(body).strip()
        if len(text) < 200:
            continue
        for k in range(0, len(body), CHUNK_LINES):
            yield {"name": title[:80], "kind": "doc", "line": ln + k, "sig": title[:120], "doc": "",
                   "body": "\n".join(body[k:k + CHUNK_LINES]), "role": "doc"}


INDEX_EXT = {".rs", ".md", ".mdx", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".py", ".go", ".css", ".scss", ".html",
             ".vue", ".svelte", ".astro", ".kt", ".swift", ".dart", ".gd", ".java", ".cs", ".lua",
             ".gradle", ".kts", ".xml"}


def wanted_file(rel):
    """Skip noisy XML (resources, IDE files); keep Android manifests and layouts."""
    if rel.endswith(".xml"):
        return rel.endswith("AndroidManifest.xml") or "/res/layout" in "/" + rel
    return True
DECL = re.compile(
    r"^(?:export\s+(?:default\s+)?|async\s+|public\s+|private\s+|internal\s+|static\s+|abstract\s+|data\s+|open\s+|"
    r"final\s+|@\w+(?:\([^)]*\))?\s+)*"
    r"(?:function\*?|class|interface|type|enum|struct|def|func|fun|object|protocol|extension|const|let|var|val|"
    r"static\s+func|component|mixin|signal)\s+([A-Za-z_$][\w$]*)")
CSS_RULE = re.compile(r"^([.#:@\[a-zA-Z*][^{]*)\{")


def generic_chunks(path, src):
    """Language-agnostic chunking: split at top-level declarations (or CSS rules), keep small files whole."""
    lines = src.splitlines()
    if not lines or len(src) > MAX_FILE:
        return
    role = role_of(path)
    ext = path.rsplit(".", 1)[-1].lower()
    if len(lines) <= 120:
        names = [m.group(1) for m in (DECL.match(l) for l in lines) if m][:15]
        yield {"name": Path(path).stem, "kind": "file", "line": 1,
               "sig": f"{ext} file: {', '.join(names)}" if names else f"{ext} file", "doc": "", "body": src, "role": role}
        return
    starts = []
    for i, l in enumerate(lines):
        if not l or l[0] in " \t}])":
            continue
        m = DECL.match(l)
        if m:
            starts.append((i, m.group(1), l.strip()))
        elif ext in ("css", "scss") and CSS_RULE.match(l):
            starts.append((i, l.split("{")[0].strip()[:60], l.strip()))
    if not starts:
        starts = [(i, f"{Path(path).stem}:{i + 1}", lines[i].strip()) for i in range(0, len(lines), CHUNK_LINES)]
    # merge tiny neighbours so chunks are meaningful, cap size
    merged = []
    for j, (i, name, sig) in enumerate(starts):
        end = starts[j + 1][0] if j + 1 < len(starts) else len(lines)
        if merged and i - merged[-1][0] < 12 and end - merged[-1][0] <= CHUNK_LINES:
            merged[-1] = (merged[-1][0], merged[-1][1], merged[-1][2], end)
        else:
            merged.append((i, name, sig, end))
    for i, name, sig, end in merged:
        start = i
        while start > 0 and lines[start - 1].strip().startswith(("//", "#", "*", "/*", "///", "@", '"""')) and i - start < 15:
            start -= 1
        body = "\n".join(lines[start:min(end, start + CHUNK_LINES)])
        yield {"name": name, "kind": "decl", "line": start + 1, "sig": sig[:160], "doc": "", "body": body, "role": role}


def want_md(path, cats):
    p = path.lower()
    return ("learning" in cats or "ui-design" in cats or "/docs/" in "/" + p or "book" in p or p.endswith("readme.md")
            or "/examples/" in "/" + p or "migration" in p)


# ------------------------------------------------------------------ corpus index
def open_corpus():
    CORPUS.mkdir(parents=True, exist_ok=True)
    db = connect(CORPUS_DB)
    db.execute("CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT)")
    row = db.execute("SELECT v FROM meta WHERE k='schema'").fetchone()
    if not row or row[0] != SCHEMA:  # older index layout: rebuild (re-indexing is fast, downloads are kept)
        db.execute("DROP TABLE IF EXISTS repos")
        db.execute("DROP TABLE IF EXISTS chunks")
        db.execute("INSERT OR REPLACE INTO meta VALUES ('schema', ?)", (SCHEMA,))
    db.execute("CREATE TABLE IF NOT EXISTS repos(name TEXT PRIMARY KEY, commit_id TEXT, license TEXT, kind TEXT, "
               "cats TEXT, chunks INT, at TEXT, bevy TEXT, pop REAL)")
    db.execute("""CREATE VIRTUAL TABLE IF NOT EXISTS chunks USING fts5(
        repo UNINDEXED, path, name, terms, sig, doc, body, cats, license UNINDEXED, role UNINDEXED,
        line UNINDEXED, kind UNINDEXED, h UNINDEXED, tokenize='unicode61')""")
    return db


SCHEMA = "3"


def bevy_version_of(repo_dir):
    """Which Bevy a repo targets: '0.14', 'git' (main branch), or None if it doesn't use Bevy."""
    best = None
    for toml in list(Path(repo_dir).glob("Cargo.toml")) + list(Path(repo_dir).glob("*/Cargo.toml"))[:40]:
        try:
            t = toml.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if toml.parent == Path(repo_dir) and re.search(r'^name\s*=\s*"bevy"', t, re.M):
            m = re.search(r'^version\s*=\s*"(\d+\.\d+)', t, re.M)
            return m.group(1) if m else "git"
        # `bevy = "0.14"` or Bevy sub-crates (`bevy_ecs = { version = "0.14" }`) - not third-party bevy_* plugins
        m = re.search(r'^\s*bevy(?:_(?:ecs|app|math|reflect|utils|render|asset|core|transform|window|input|time|'
                      r'log|tasks|hierarchy|ui|sprite|pbr|text|winit|scene|audio|gizmos|state|color|image|camera|picking))?'
                      r'\s*=\s*(?:"[=^~]?(\d+\.\d+)|\{[^}\n]*?(?:version\s*=\s*"[=^~]?(\d+\.\d+)|git\s*=))', t, re.M)
        if m:
            v = m.group(1) or m.group(2) or "git"
            if not best or (v != "git" and (best == "git" or _ver(v) > _ver(best))):
                best = v
    return best


def _ver(v):
    try:
        return tuple(int(x) for x in v.split(".")[:2])
    except ValueError:
        return (0, 999)


def body_hash(body):
    import hashlib
    return hashlib.sha1(re.sub(r"\s+", "", body).encode("utf-8", "replace")).hexdigest()[:16]


def head_commit(d):
    r = subprocess.run(["git", "-C", str(d), "rev-parse", "HEAD"], capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else str(int(d.stat().st_mtime))


def iter_files(d):
    for p in d.rglob("*"):
        if not p.is_file() or p.suffix.lower() not in INDEX_EXT:
            continue
        rel = p.relative_to(d).as_posix()
        if any(part in ai_index.SKIP_DIRS or part.startswith(".") for part in rel.split("/")[:-1]):
            continue
        if p.stat().st_size > MAX_FILE or not wanted_file(rel):
            continue
        yield rel, p


def cmd_index(args, quiet=False):
    db = open_corpus()
    manifest = {r["name"]: r for r in load_manifest()}
    repos_dir = CORPUS / "repos"
    present = sorted(p.name for p in repos_dir.iterdir() if p.is_dir()) if repos_dir.exists() else []
    if not present:
        log("No repos downloaded yet. Run:  python ai_corpus.py fetch", quiet)
        return
    for (gone,) in db.execute("SELECT name FROM repos").fetchall():
        if gone not in present:
            db.execute("DELETE FROM chunks WHERE repo=?", (gone,))
            db.execute("DELETE FROM repos WHERE name=?", (gone,))
    total = 0
    for name in present:
        d = repos_dir / name
        commit = f"{head_commit(d)}:{ai_index.PARSER_ID}"  # re-index if repo OR parser changes
        row = db.execute("SELECT commit_id FROM repos WHERE name=?", (name,)).fetchone()
        if row and row[0] == commit:
            continue
        lic, kind = detect_license(d)
        meta = manifest.get(name, {"cats": ["mine"]})
        bevy = bevy_version_of(d)
        import math
        pop = 1.0 + 0.08 * math.log10(1 + meta.get("downloads", 0) / 100 + meta.get("stars", 0)) if meta.get("discovered") else 1.15
        cats = " ".join(meta["cats"])
        if kind == "copyleft":
            lic += " (COPYLEFT - study only)"
        t0 = time.time()
        db.execute("DELETE FROM chunks WHERE repo=?", (name,))
        n = 0
        for rel, p in iter_files(d):
            src = p.read_text(encoding="utf-8", errors="replace")
            if src.startswith(("// @generated", "// This file is generated", "// Generated by")):
                continue
            if rel.endswith(".rs"):
                gen = rust_chunks(rel, src)
            elif rel.endswith((".md", ".mdx")):
                gen = md_chunks(rel, src) if want_md(rel, meta["cats"]) else ()
            else:
                gen = generic_chunks(rel, src)
            rows = []
            for c in gen:
                terms = " ".join(dict.fromkeys(split_ident(c["name"] + " " + c["sig"] + " " + rel + " "
                                                           + " ".join(re.findall(r"[A-Za-z_]\w{3,}", c["body"]))[:4000])))
                rows.append((name, rel, c["name"], terms, c["sig"], c["doc"], c["body"], cats, lic, c["role"],
                             c["line"], c["kind"], body_hash(c["body"])))
            db.executemany("INSERT INTO chunks VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
            n += len(rows)
        db.execute("INSERT OR REPLACE INTO repos VALUES (?,?,?,?,?,?,?,?,?)",
                   (name, commit, lic, kind, cats, n, time.strftime("%Y-%m-%d %H:%M"), bevy, pop))
        db.commit()
        total += n
        log(f"indexed {name[:40]:40} {n:7} chunks  [{lic[:30]}]" + (f" bevy {bevy}" if bevy else "") + f"  {time.time() - t0:.1f}s", quiet)
    db.execute("INSERT INTO chunks(chunks) VALUES('optimize')")
    db.commit()
    log(f"Corpus index up to date ({CORPUS_DB}, {CORPUS_DB.stat().st_size / 1e6:.0f} MB).", quiet)


# ------------------------------------------------------------------ deps index (per project)
FFI_CRATE = re.compile(r"^(windows[-_].*|winapi.*|libc|.*-sys|objc2.*|core-foundation.*|linux-raw-sys|wasm-bindgen.*)$")


def cargo_home():
    return Path(os.environ.get("CARGO_HOME", Path.home() / ".cargo"))


def lock_packages(root):
    lock = root / "Cargo.lock"
    if not lock.exists():
        return []
    pk = re.findall(r'\[\[package\]\]\s*\nname = "([^"]+)"\s*\nversion = "([^"]+)"(?:\s*\nsource = "([^"]+)")?',
                    lock.read_text(encoding="utf-8"))
    return [(n, v) for n, v, src in pk if src and src.startswith("registry+")]


def direct_deps(root):
    out = set()
    for c in ai_index.find_crates(root):
        out.update(d.replace("-", "_") for d in c["deps"])
    return out


def find_src(name, version):
    for reg in (cargo_home() / "registry" / "src").glob("*"):
        d = reg / f"{name}-{version}"
        if d.is_dir():
            return d
    return None


def connect(path):
    """SQLite that waits for other Windmill processes instead of failing with 'database is locked'."""
    db = sqlite3.connect(path, timeout=120)
    try:
        db.execute("PRAGMA journal_mode=WAL")  # readers never block the indexer and vice versa
    except sqlite3.OperationalError:
        pass
    return db


class IndexLock:
    """Only one dependency indexer per project at a time (git hooks can fire several in a row)."""

    def __init__(self, root):
        self.path = Path(root) / ".ai" / "deps.lock"
        self.held = False

    def __enter__(self):
        try:
            if self.path.exists() and time.time() - self.path.stat().st_mtime > 3600:
                self.path.unlink()  # stale lock from a crashed run
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            self.held = True
        except FileExistsError:
            self.held = False
        return self

    def __exit__(self, *a):
        if self.held:
            self.path.unlink(missing_ok=True)


def open_deps(root):
    db = connect(root / ".ai" / "deps.db")
    db.execute("CREATE TABLE IF NOT EXISTS crates(id TEXT PRIMARY KEY, items INT)")
    db.execute("""CREATE VIRTUAL TABLE IF NOT EXISTS api USING fts5(
        crate UNINDEXED, version UNINDEXED, path, name, terms, sig, doc, body UNINDEXED,
        line UNINDEXED, kind UNINDEXED, tokenize='unicode61')""")
    return db


def cmd_deps(root, quiet=False):
    (Path(root) / ".ai").mkdir(exist_ok=True)
    with IndexLock(root) as lock:
        if not lock.held:
            log("deps index: another Windmill process is already updating it - skipping.", quiet)
            return
        _cmd_deps(root, quiet)


def _cmd_deps(root, quiet=False):
    pkgs = lock_packages(root)
    if not pkgs:
        log("No Cargo.lock with registry crates (run `cargo build` or `cargo generate-lockfile` first).", quiet)
        return
    missing = [p for p in pkgs if not find_src(*p)]
    if missing:
        log(f"{len(missing)} crate sources not in cargo cache - running `cargo fetch`...", quiet)
        subprocess.run(["cargo", "fetch", "--quiet"], cwd=root)
    (root / ".ai").mkdir(exist_ok=True)
    db = open_deps(root)
    want = {f"{n}@{v}" for n, v in pkgs}
    have = {r[0] for r in db.execute("SELECT id FROM crates")}
    for gone in have - want:
        n, v = gone.split("@")
        db.execute("DELETE FROM api WHERE crate=? AND version=?", (n, v))
        db.execute("DELETE FROM crates WHERE id=?", (gone,))
    added = 0
    for n, v in pkgs:
        cid = f"{n}@{v}"
        if cid in have:
            continue
        d = find_src(n, v)
        if not d:
            continue
        rows = []
        for p in (d / "src").rglob("*.rs") if (d / "src").exists() else []:
            if p.stat().st_size > MAX_FILE:
                continue
            src = p.read_text(encoding="utf-8", errors="replace")
            lines = src.splitlines()
            rel = p.relative_to(d).as_posix()
            items, _, _, _ = ai_index.parse_rust(src)
            for it in items:
                if not it["pub"] or it["test"] or it["kind"] in ("mod", "impl", "use"):
                    continue
                if re.match(r"(\w+\s+)*pub\s*\(", it["sig"]):  # pub(crate)/pub(super): not usable by you
                    continue
                name = (f"{it['parent']}::" if it.get("parent") else "") + it["name"]
                small = it["end"] - it["line"] < 12
                body = "\n".join(lines[it["line"] - 1:it["end"]]) if small else ""
                terms = " ".join(dict.fromkeys(split_ident(f"{n} {name} {it['sig']} {rel}")))
                rows.append((n, v, rel, name, terms, it["sig"], it["doc"], body, it["line"], it["kind"]))
        db.executemany("INSERT INTO api VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
        db.execute("INSERT OR REPLACE INTO crates VALUES (?,?)", (cid, len(rows)))
        db.commit()  # one crate per transaction: never holds the lock for long
        added += 1
    db.commit()
    total = db.execute("SELECT count(*), sum(items) FROM crates").fetchone()
    log(f"deps index: {total[0]} crate versions, {total[1] or 0} public items ({added} newly indexed) -> .ai/deps.db", quiet)


# ------------------------------------------------------------------ search
def search_deps(root, text, k=12, extra_terms=()):
    dbp = root / ".ai" / "deps.db"
    terms = query_terms(text) + [t for t in extra_terms if t not in STOP]
    if not dbp.exists() or not terms:
        return []
    db = connect(dbp)
    direct = direct_deps(root)
    try:
        rows = db.execute(
            "SELECT crate, version, path, name, sig, doc, body, line, kind, bm25(api, 0,0, 1, 8, 4, 3, 2) AS s "
            "FROM api WHERE api MATCH ? ORDER BY s LIMIT 200", (fts_query(terms),)).fetchall()
    except sqlite3.OperationalError:
        return []
    scored = []
    for r in rows:
        c = r[0].replace("-", "_")
        family = any(c == d or c.startswith(d + "_") or d.startswith(c + "_") for d in direct)
        s = -r[9] * (1.6 if c in direct else 1.2 if family else 0.25)  # your crates (and their sub-crates) first
        if FFI_CRATE.match(r[0]) or re.search(r"_(derive|macros?|impl|codegen)$", c):
            s *= 0.3  # raw bindings / macro internals: rarely what you call
        scored.append((s, r))
    scored.sort(key=lambda x: -x[0])
    out, seen, per_crate = [], set(), {}
    for s, r in scored:
        key = (r[0], r[3])
        if key in seen or per_crate.get(r[0], 0) >= 5:  # share the slots between crates
            continue
        seen.add(key)
        per_crate[r[0]] = per_crate.get(r[0], 0) + 1
        out.append(r[:9])
        if len(out) >= k:
            break
    return out


def version_weight(repo_bevy, want):
    """Code for your Bevy version first; far-older Bevy code teaches outdated APIs."""
    if not want or not repo_bevy:
        return 1.0
    if repo_bevy == "git":
        return 0.9
    a, b = _ver(repo_bevy), _ver(want)
    if a[0] != b[0]:
        return 0.3
    d = abs(a[1] - b[1])
    return 1.35 if d == 0 else 1.0 if d == 1 else 0.6 if d == 2 else 0.25


def search_corpus(text, k=6, cats=None, include_copyleft=False, min_rel=0.5, bevy=None):
    terms = query_terms(text)
    if not CORPUS_DB.exists() or not terms:
        return []
    db = connect(CORPUS_DB)
    try:
        rows = db.execute(
            "SELECT repo, path, name, sig, doc, body, cats, license, role, line, kind, "
            "bm25(chunks, 0, 2, 10, 4, 3, 2, 1, 2, 0, 0, 0, 0, 0) AS s, terms, h FROM chunks WHERE chunks MATCH ? "
            "ORDER BY s LIMIT 600", (fts_query(terms),)).fetchall()
        repo_meta = {r[0]: (r[1], r[2] or 1.0) for r in db.execute("SELECT name, bevy, pop FROM repos")}
    except sqlite3.OperationalError:
        return []
    want = set(cats or [])
    user_terms = set(query_terms(text, limit=60)[:len([w for w in split_ident(text) if len(w) > 2 and w not in STOP])])
    need = 2 if len(user_terms) >= 3 else 1
    scored = []
    for r in rows:
        if len(user_terms & set(r[12].split())) < need:  # must share real vocabulary with the task
            continue
        if want and not want & set(r[6].split()):
            continue
        if "COPYLEFT" in r[7] and not include_copyleft:
            continue
        rb, pop = repo_meta.get(r[0], (None, 1.0))
        scored.append((-r[11] * ROLE_WEIGHT.get(r[8], 1.0) * version_weight(rb, bevy) * pop, r))
    scored.sort(key=lambda x: -x[0])
    top = scored[0][0] if scored else 0
    out, seen_files, per_repo, seen_hash = [], set(), {}, set()
    for s, r in scored:  # diversity: one chunk per file, max 2 per repo, no duplicate code
        if s < top * min_rel:  # weak match: not worth the tokens
            break
        if (r[0], r[1]) in seen_files or per_repo.get(r[0], 0) >= 2 or r[13] in seen_hash:
            continue
        seen_hash.add(r[13])
        seen_files.add((r[0], r[1]))
        per_repo[r[0]] = per_repo.get(r[0], 0) + 1
        out.append(r[:11])
        if len(out) >= k:
            break
    return out


def best_window(lines, keep, terms):
    """The `keep`-line window with the most query words (plus the first line for context), not just the top."""
    if len(lines) <= keep:
        return "\n".join(lines)
    score = [len(terms & set(split_ident(l))) for l in lines]
    best, best_i, cur = -1, 0, sum(score[:keep])
    for i in range(0, len(lines) - keep + 1):
        if i:
            cur += score[i + keep - 1] - score[i - 1]
        if cur > best:
            best, best_i = cur, i
    head = [lines[0], "    // ..."] if best_i > 1 else lines[:best_i]
    tail = ["    // ..."] if best_i + keep < len(lines) else []
    return "\n".join(head + lines[best_i:best_i + keep] + tail)


def reference_pack(root, text, files=(), budget=1800, cats=None, include_copyleft=False):
    """What the pipeline hands the model before each step: exact dependency APIs + example patterns."""
    root = Path(root)
    extra = []
    for f in files:  # crates the target files already import
        try:
            src = Path(f).read_text(encoding="utf-8", errors="replace")
            extra += [w for u in re.findall(r"^\s*use\s+([A-Za-z_]\w*)", src, re.M) for w in split_ident(u)]
        except OSError:
            pass
    out = []
    deps = search_deps(root, text, extra_terms=extra)
    if deps:
        out += ["## Exact APIs from YOUR dependency versions (authoritative - use these signatures)"]
        for c, v, path, name, sig, doc, body, line, kind in deps:
            entry = f"- {c}@{v} {name}: `{sig}`" + (f" - {doc[:90]}" if doc else "")
            if est_tokens("\n".join(out) + entry) > budget * 0.4:
                break
            out.append(entry)
    want_bevy = None
    lock = root / "Cargo.lock"
    if lock.exists():
        m = re.search(r'name = "bevy"\nversion = "(\d+\.\d+)', lock.read_text(encoding="utf-8", errors="replace"))
        want_bevy = m.group(1) if m else None
    ex = search_corpus(text, cats=cats, include_copyleft=include_copyleft, bevy=want_bevy)
    qterms = set(query_terms(text, limit=60))
    if ex:
        out += ["", "## Reference examples from the library (patterns to learn from; their crate versions may differ "
                "from yours - the APIs above win. Adapt, don't copy blindly.)"]
        for repo, path, name, sig, doc, body, rcats, lic, role, line, kind in ex:
            body_lines = body.splitlines()
            remaining = budget - est_tokens("\n".join(out))
            if remaining < 120:
                break
            keep = max(8, min(len(body_lines), int(remaining * 3.6 / 45)))
            snippet = best_window(body_lines, keep, qterms)
            fence = "rust" if path.endswith(".rs") else "md"
            out.append(f"\n### {repo}/{path}:{line} ({role}, {lic})\n```{fence}\n{snippet}\n```")
    if not out:
        return ""
    return "# Reference pack (auto-retrieved for this step)\n" + "\n".join(out) + "\n"


# ------------------------------------------------------------------ CLI
def parse_args(argv):
    args, pos, i = {}, [], 0
    while i < len(argv):
        a = argv[i]
        if a.startswith("--") and i + 1 < len(argv) and not argv[i + 1].startswith("--") and a not in ("--include-copyleft", "--update", "--deps", "--corpus", "--quiet"):
            args[a] = argv[i + 1]; i += 2
        elif a.startswith("--"):
            args[a] = True; i += 1
        else:
            pos.append(a); i += 1
    args["_pos"] = pos
    return args


def project_root():
    r = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True)
    return Path(r.stdout.strip()) if r.returncode == 0 else Path.cwd()


def main():
    if len(sys.argv) < 2:
        print(__doc__); sys.exit(1)
    cmd, args = sys.argv[1], parse_args(sys.argv[2:])
    quiet = "--quiet" in args
    if cmd == "fetch":
        cmd_fetch(args)
    elif cmd == "index":
        cmd_index(args, quiet)
    elif cmd == "discover":
        import ai_discover
        topic = args["_pos"][0] if args["_pos"] else "bevy"
        srcs = tuple(str(args.get("--sources", "bevy-assets,crates.io,itch.io,github")).split(","))
        ai_discover.discover(CORPUS, topic, srcs)
    elif cmd == "add":
        cmd_add(args)
    elif cmd == "deps":
        cmd_deps(project_root(), quiet)
    elif cmd == "search":
        text = " ".join(args["_pos"])
        cats = args["--cat"].split(",") if isinstance(args.get("--cat"), str) else None
        if "--corpus" not in args:
            for c, v, path, name, sig, doc, body, line, kind in search_deps(project_root(), text):
                print(f"[dep] {c}@{v} {path}:{line}  {name}  |  {sig[:110]}")
        if "--deps" not in args:
            for repo, path, name, sig, doc, body, rcats, lic, role, line, kind in search_corpus(text, k=10, cats=cats, include_copyleft="--include-copyleft" in args):
                print(f"[{role}] {repo}/{path}:{line}  {name}  |  {sig[:90]}  ({lic})")
    elif cmd == "pack":  # preview exactly what a pipeline step would receive
        print(reference_pack(project_root(), " ".join(args["_pos"])))
    elif cmd == "stats":
        if CORPUS_DB.exists():
            db = connect(CORPUS_DB)
            for r in db.execute("SELECT name, chunks, license, cats FROM repos ORDER BY name"):
                print(f"{r[0]:24} {r[1]:7} chunks  {r[2]:28} {r[3]}")
            print(f"corpus.db: {CORPUS_DB.stat().st_size / 1e6:.0f} MB")
        dp = project_root() / ".ai" / "deps.db"
        if dp.exists():
            r = sqlite3.connect(dp).execute("SELECT count(*), sum(items) FROM crates").fetchone()
            print(f"deps.db: {r[0]} crate versions, {r[1]} public items")
    else:
        print(__doc__); sys.exit(1)


if __name__ == "__main__":
    try:
        main()
    except BrokenPipeError:
        pass
