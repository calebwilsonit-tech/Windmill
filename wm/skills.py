"""Skills: folders of instructions the AI loads when relevant (same SKILL.md format as Claude's skills).

  ~/.windmill/skills/<name>/SKILL.md      your skills (all projects)
  <project>/.windmill/skills/<name>/       project skills
  ~/.claude/skills/<name>/                 skills you already made for Claude Code are picked up too
SKILL.md starts with frontmatter:
  ---
  name: release-checklist
  description: How we cut a release - use when the user asks to publish a version
  ---
  (instructions...)"""
import re
from pathlib import Path

from . import config


def roots(project=None):
    r = [config.HOME / "skills", Path.home() / ".claude" / "skills", config.KIT / "skills"]
    if project:
        r.insert(0, Path(project) / ".windmill" / "skills")
    return [d for d in r if d.is_dir()]


def parse(md):
    meta, body = {}, md
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", md, re.S)
    if m:
        for line in m.group(1).splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                meta[k.strip()] = v.strip().strip('"').strip("'")
        body = m.group(2)
    return meta, body


def discover(project=None):
    out, seen = [], set()
    for root in roots(project):
        for f in sorted(root.glob("*/SKILL.md")):
            meta, _ = parse(f.read_text(encoding="utf-8", errors="replace"))
            name = meta.get("name") or f.parent.name
            if name in seen:
                continue  # project skills override global ones of the same name
            seen.add(name)
            out.append({"name": name, "description": meta.get("description", ""), "path": str(f.parent),
                        "source": "project" if ".windmill" in f.parts else ("claude" if ".claude" in f.parts else "windmill")})
    return out


def load(name, project=None, file=None):
    for s in discover(project):
        if s["name"] == name:
            d = Path(s["path"])
            if file:
                p = (d / file).resolve()
                if d.resolve() not in p.parents or not p.is_file():
                    return f"ERROR: {file} is not a file in skill {name}"
                return p.read_text(encoding="utf-8", errors="replace")[:20000]
            _, body = parse((d / "SKILL.md").read_text(encoding="utf-8", errors="replace"))
            extra = [p.relative_to(d).as_posix() for p in d.rglob("*") if p.is_file() and p.name != "SKILL.md"][:40]
            return body + (f"\n\n(Other files in this skill - load with use_skill(name, file): {', '.join(extra)})" if extra else "")
    return f"ERROR: no skill named {name}. Available: {', '.join(s['name'] for s in discover(project)) or 'none'}"


def prompt_index(project=None):
    sk = discover(project)
    if not sk:
        return ""
    lines = "\n".join(f"- {s['name']}: {s['description'][:160]}" for s in sk[:40])
    return f"SKILLS (load with use_skill when relevant - they contain the user's preferred way of doing things):\n{lines}"
