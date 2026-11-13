#!/usr/bin/env python3
"""Query the project index (.ai/graph.json) instead of opening whole files.

  python ai_query.py find Config          where is it defined (file:line + signature)
  python ai_query.py show Config::new     print ONLY that item's source (not the whole file)
  python ai_query.py refs parse_pct       where it's used (file:line), definitions excluded
  python ai_query.py file src/db.rs       outline of one file + what it uses / what uses it
  python ai_query.py related "add csv export to reports"   most relevant files for a task
  python ai_query.py context src/a.rs src/b.rs             signatures of everything those
                                                           files depend on (a "context pack")
"""
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import ai_index  # noqa: E402

MAX_SHOW = 150


def root_dir():
    r = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True)
    return Path(r.stdout.strip()) if r.returncode == 0 else Path.cwd()


def load(root, refresh=True):
    if refresh:  # cheap: only re-parses changed files
        return ai_index.build(root, quiet=True)
    return json.loads((root / ".ai" / "graph.json").read_text(encoding="utf-8"))


def all_items(g):
    for rel, f in g["files"].items():
        for it in f["items"]:
            yield rel, it


def match(it, q):
    """'name', 'Type::method', or 'mod::name'"""
    if "::" in q:
        owner, name = q.rsplit("::", 1)
        return it["name"] == name and (it.get("parent") == owner or (it.get("mod") or "").endswith(owner))
    return it["name"] == q


def fmt(rel, it):
    owner = f"{it['parent']}::" if it.get("parent") else (f"{it['mod']}::" if it.get("mod") else "")
    kind = it["kind"] if it["kind"] != "impl" else "impl" + (f" {it['trait']} for" if it.get("trait") else "")
    return f"{rel}:{it['line']}-{it['end']}  {kind} {owner}{it['name']}  |  {it['sig'][:140]}"


def cmd_find(g, root, q):
    hits = [(r, it) for r, it in all_items(g) if match(it, q) and it["kind"] != "mod"]
    if not hits:
        lo = q.lower().split("::")[-1]
        hits = [(r, it) for r, it in all_items(g) if lo in it["name"].lower()][:20]
        if hits:
            print(f"No exact match for {q}; similar:")
    for r, it in hits[:30]:
        print(fmt(r, it))
    return hits


def cmd_show(g, root, q):
    hits = [(r, it) for r, it in all_items(g) if match(it, q) and it["kind"] not in ("mod",)]
    if not hits:
        print(f"Not found: {q}. Try: ai_query.py find {q}")
        return
    for r, it in hits[:5]:
        lines = (root / r).read_text(encoding="utf-8", errors="replace").splitlines()
        start = it["line"] - 1
        while start > 0 and lines[start - 1].strip().startswith(("///", "#[")):
            start -= 1  # include doc comments + attributes
        end = min(it["end"], start + MAX_SHOW)
        print(f"// {r}:{start + 1}-{it['end']}")
        print("\n".join(lines[start:end]))
        if it["end"] > end:
            print(f"// ... {it['end'] - end} more lines (open {r} if you need them)")
        print()


def cmd_refs(g, root, q):
    name = q.split("::")[-1]
    defs = {(r, it["line"]) for r, it in all_items(g) if it["name"] == name}
    word = re.compile(rf"\b{re.escape(name)}\b")
    n = 0
    for rel in g["files"]:
        cl = ai_index.clean((root / rel).read_text(encoding="utf-8", errors="replace")).splitlines()
        raw = (root / rel).read_text(encoding="utf-8", errors="replace").splitlines()
        for i, line in enumerate(cl, 1):
            if word.search(line) and (rel, i) not in defs and not line.lstrip().startswith("use "):
                print(f"{rel}:{i}: {raw[i - 1].strip()[:120]}")
                n += 1
                if n >= 40:
                    print("... (more; narrow with Type::name)")
                    return


def cmd_file(g, root, rel):
    rel = rel.replace("\\", "/")
    f = g["files"].get(rel)
    if not f:
        cands = [r for r in g["files"] if r.endswith(rel)]
        if len(cands) == 1:
            rel, f = cands[0], g["files"][cands[0]]
        else:
            print(f"Not indexed: {rel}" + (f" (did you mean: {', '.join(cands[:5])})" if cands else ""))
            return
    print(f"{rel}  ({f['lines']} lines, module {f['crate']}::{f['module'] or '(root)'})")
    for it in f["items"]:
        if it["kind"] == "mod" and not it["test"]:
            continue
        ind = "    " if it.get("parent") else "  "
        tag = " [test]" if it["test"] else ("" if it["pub"] else " [private]")
        if it["test"] and it["kind"] != "fn":
            continue
        print(f"{ind}{it['line']}-{it['end']}{tag} {it['sig'][:130] or it['kind'] + ' ' + it['name']}")
    print(f"uses:    {', '.join(f['uses']) or '-'}")
    print(f"used by: {', '.join(f['used_by']) or '-'}")


def words(text):
    parts = re.findall(r"[A-Za-z][a-z0-9]+|[A-Z]+(?![a-z])", text)
    stop = {"the", "and", "add", "for", "with", "that", "this", "from", "into", "make", "when", "should",
            "fix", "use", "new", "all", "are", "not", "can", "has", "have", "test", "tests", "code"}
    return {p.lower() for p in parts if len(p) > 2 and p.lower() not in stop}


def related(g, text, k=8):
    want = words(text)
    scores = {}
    for rel, f in g["files"].items():
        hay = words(rel) | words(f.get("doc", ""))
        sc = 3 * len(want & words(rel))
        for it in f["items"]:
            iw = words(it["name"]) | words(it.get("doc", ""))
            sc += len(want & iw) * (2 if it["pub"] else 1)
        sc += len(want & hay)
        if sc:
            scores[rel] = scores.get(rel, 0) + sc
    for rel, sc in list(scores.items()):  # spread to graph neighbours
        for nb in g["files"][rel]["uses"] + g["files"][rel]["used_by"]:
            scores[nb] = scores.get(nb, 0) + sc * 0.3
    return sorted(scores.items(), key=lambda x: -x[1])[:k]


def cmd_related(g, root, text):
    for rel, sc in related(g, text):
        f = g["files"][rel]
        pub = [it["name"] for it in f["items"] if it["pub"] and not it["test"] and it["kind"] != "impl"][:8]
        print(f"{rel}  (score {sc:.1f})  {', '.join(pub)}")


def context_pack(g, targets, budget_tokens=1500):
    """Signatures of the public API of every file the targets depend on (not the targets
    themselves - those get opened for editing anyway)."""
    targets = [t.replace("\\", "/") for t in targets]
    deps = []
    for t in targets:
        for d in g["files"].get(t, {}).get("uses", []):
            if d not in targets and d not in deps:
                deps.append(d)
    out = ["# Context pack: APIs the files you're editing depend on",
           "Signatures only. Use these exactly; don't guess other APIs. Open a file only if you must.", ""]
    for d in deps:
        block = ai_index.file_block(d, g["files"][d], 0)
        if ai_index.est_tokens("\n".join(out) + block) > budget_tokens:
            block = ai_index.file_block(d, g["files"][d], 2)
            if ai_index.est_tokens("\n".join(out) + block) > budget_tokens:
                out.append(f"(+ {len(deps) - deps.index(d)} more dependency files omitted for space)")
                break
        out.append(block)
    if not deps:
        out.append("(the target files don't depend on other files in this project)")
    return "\n".join(out) + "\n"


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    cmd, args = sys.argv[1], sys.argv[2:]
    root = root_dir()
    g = load(root)
    if cmd == "find":
        cmd_find(g, root, args[0])
    elif cmd == "show":
        cmd_show(g, root, args[0])
    elif cmd == "refs":
        cmd_refs(g, root, args[0])
    elif cmd == "file":
        cmd_file(g, root, args[0])
    elif cmd == "related":
        cmd_related(g, root, " ".join(args))
    elif cmd == "context":
        print(context_pack(g, args))
    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    try:
        main()
    except BrokenPipeError:
        pass
