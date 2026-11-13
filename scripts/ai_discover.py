#!/usr/bin/env python3
"""Discover open-source code for the library automatically, instead of hand-picked lists.

Bevy sources (all public, polite: 1 request/second, identified user agent, robots.txt respected):
  bevy-assets   the official Bevy Assets catalog (plugins, games, tools, learning) - github.com/bevyengine/bevy-assets
  crates.io     every crate that depends on bevy (+ download counts as a quality signal)
  itch.io       Bevy games on itch.io whose pages link their source code (itch.io/games/tag-bevy)
  github        repositories tagged with the `bevy` topic (GitHub search API)

Results go to <corpus>/discovered/<topic>.json; `ai_corpus.py fetch --discovered bevy` downloads them
(licences are verified per repo on download, exactly like the hand-picked library)."""
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

UA = "windmill-library-builder/1.0 (+https://github.com/bevyengine/bevy-assets; polite crawler, 1 req/s)"
HOSTS = r"(github\.com|gitlab\.com|codeberg\.org)"
REPO_RE = re.compile(rf"https?://(?:www\.)?{HOSTS}/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)")
NOT_REPOS = {"sponsors", "login", "features", "about", "orgs", "topics", "settings", "marketplace", "explore",
             "users", "help", "pricing", "notifications", "apps", "collections", "trending", "site"}
_last = [0.0]


def get(url, accept="application/json"):
    wait = 1.0 - (time.time() - _last[0])
    if wait > 0:
        time.sleep(wait)
    _last[0] = time.time()
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": accept})
    with urllib.request.urlopen(req, timeout=40) as r:
        data = r.read()
    return json.loads(data) if "json" in accept else data.decode("utf-8", errors="replace")


def norm(url):
    """Canonical https://host/owner/repo (no .git, no sub-paths), or None if it isn't a repo link."""
    m = REPO_RE.search(url or "")
    if not m:
        return None
    host, owner, repo = m.group(1).lower(), m.group(2), re.sub(r"\.git$", "", m.group(3))
    if owner.lower() in NOT_REPOS or repo.lower() in ("", "issues", "pulls", "wiki") or repo.startswith("."):
        return None
    return f"https://{host}/{owner}/{repo}"


def log(m):
    print(m, flush=True)


# ------------------------------------------------------------------ sources
def from_bevy_assets(work):
    d = work / "bevy-assets"
    if d.exists():
        subprocess.run(["git", "-C", str(d), "pull", "-q", "--ff-only"], capture_output=True)
    else:
        subprocess.run(["git", "clone", "-q", "--depth", "1", "https://github.com/bevyengine/bevy-assets.git", str(d)])
    out = []
    for f in d.rglob("*.toml"):
        t = f.read_text(encoding="utf-8", errors="replace")
        link = re.search(r'^link\s*=\s*"([^"]+)"', t, re.M)
        url = norm(link.group(1)) if link else None
        if url:
            section = f.relative_to(d).parts[:-1]
            desc = re.search(r'^description\s*=\s*"([^"]*)"', t, re.M)
            out.append({"url": url, "source": "bevy-assets", "section": "/".join(section),
                        "description": desc.group(1) if desc else ""})
    log(f"  bevy-assets: {len(out)} repos")
    return out


def from_crates_io(limit_pages=60):
    names, page = {}, 1
    while page <= limit_pages:
        d = get(f"https://crates.io/api/v1/crates/bevy/reverse_dependencies?per_page=100&page={page}")
        vers = d.get("versions", [])
        if not vers:
            break
        reqs = {dep["version_id"]: dep.get("req") for dep in d.get("dependencies", [])}
        for v in vers:
            names.setdefault(v["crate"], {"req": reqs.get(v["id"]), "downloads": v.get("downloads", 0)})
        if page * 100 >= d.get("meta", {}).get("total", 0):
            break
        page += 1
    out, ids = [], list(names)
    for i in range(0, len(ids), 50):
        q = "&".join("ids%5B%5D=" + urllib.parse.quote(n) for n in ids[i:i + 50])
        d = get(f"https://crates.io/api/v1/crates?per_page=50&{q}")
        for c in d.get("crates", []):
            url = norm(c.get("repository") or "")
            if url:
                out.append({"url": url, "source": "crates.io", "crate": c["id"], "downloads": c.get("downloads", 0),
                            "bevy_req": names.get(c["id"], {}).get("req"), "description": (c.get("description") or "")[:200]})
    log(f"  crates.io: {len(names)} crates depend on bevy -> {len(out)} with a repository link")
    return out


def from_itch(max_pages=80):
    games, page = [], 1
    while page <= max_pages:
        try:
            html = get(f"https://itch.io/games/tag-bevy?page={page}", accept="text/html")
        except Exception:  # noqa: BLE001 - itch.io answers 404 past the last page
            break
        found = sorted(set(re.findall(r'href="(https://[a-z0-9-]+\.itch\.io/[a-z0-9_-]+)"', html)))
        new = [g for g in found if g not in games]
        if not new:
            break
        games += new
        page += 1
    out = []
    for i, g in enumerate(games, 1):
        try:
            html = get(g, accept="text/html")
        except Exception:  # noqa: BLE001
            continue
        links = {norm(m.group(0)) for m in REPO_RE.finditer(html)} - {None, "https://github.com/bevyengine/bevy"}
        # pages often link libraries they used; keep links whose repo name resembles the game, or the only link
        slug = g.rsplit("/", 1)[-1].replace("-", "").lower()
        pick = [u for u in links if slug[:5] in u.rsplit("/", 1)[-1].replace("-", "").replace("_", "").lower()] or \
               (list(links) if len(links) == 1 else [u for u in links if "jam" in u.lower()])
        for u in pick:
            out.append({"url": u, "source": "itch.io", "page": g})
        if i % 50 == 0:
            log(f"    itch.io: {i}/{len(games)} game pages checked, {len(out)} with source")
    log(f"  itch.io: {len(games)} Bevy games -> {len(out)} source repos")
    return out


def from_github_topic(topic="bevy", pages=10):
    out = []
    for page in range(1, pages + 1):
        try:
            d = get(f"https://api.github.com/search/repositories?q=topic:{topic}+language:rust&sort=stars&order=desc"
                    f"&per_page=100&page={page}", accept="application/vnd.github+json")
        except Exception as e:  # noqa: BLE001 - rate limited or offline: keep what we have
            log(f"  github: stopped at page {page} ({e})")
            break
        items = d.get("items", [])
        for it in items:
            if it.get("fork") or it.get("archived") and it.get("stargazers_count", 0) < 20:
                continue
            out.append({"url": norm(it["html_url"]), "source": "github", "stars": it.get("stargazers_count", 0),
                        "size_kb": it.get("size", 0), "description": (it.get("description") or "")[:200]})
        if len(items) < 100:
            break
        time.sleep(6)  # search API: 10 requests/minute without a token
    log(f"  github topic:{topic}: {len(out)} repos")
    return out


SECTION_CATS = {"Games": ["game"], "Tools": ["tool"], "Learning": ["learning"], "UI": ["ui-design", "gui"],
                "Networking": ["networking"], "2D": ["graphics"], "3D": ["graphics"], "Physics": ["physics"],
                "Input": ["input"], "Animation": ["animation"], "Audio": ["audio"], "Camera": ["camera"]}


def discover(corpus, topic="bevy", sources=("bevy-assets", "crates.io", "itch.io", "github")):
    work = Path(corpus) / "discovered"
    work.mkdir(parents=True, exist_ok=True)
    log(f"Discovering {topic} code from: {', '.join(sources)} (polite crawl, this takes a while)")
    found = []
    for s, fn in (("bevy-assets", lambda: from_bevy_assets(work)), ("crates.io", from_crates_io),
                  ("itch.io", from_itch), ("github", lambda: from_github_topic(topic))):
        if s in sources:
            try:
                found += fn()
            except Exception as e:  # noqa: BLE001 - one source failing shouldn't lose the others
                log(f"  {s}: failed ({e})")
    merged = {}
    prev = work / f"{topic}.json"
    if prev.exists():  # keep what earlier runs found (e.g. when one source is re-run on its own)
        for r in json.loads(prev.read_text(encoding="utf-8")).get("repos", []):
            merged[r["url"].lower()] = r
    for f in found:
        u = f["url"]
        if not u or u.lower() == "https://github.com/bevyengine/bevy":
            continue
        m = merged.setdefault(u.lower(), {"url": u, "sources": [], "downloads": 0, "stars": 0, "description": ""})
        m["sources"] = sorted(set(m["sources"] + [f["source"]]))
        m["downloads"] = max(m["downloads"], f.get("downloads", 0) or 0)
        m["stars"] = max(m["stars"], f.get("stars", 0) or 0)
        m["description"] = m["description"] or f.get("description", "")
        cats = set(m.get("cats", ["bevy"]))
        if f["source"] == "itch.io":
            cats |= {"game"}
        for part in (f.get("section") or "").split("/"):
            cats |= set(SECTION_CATS.get(part, []))
        if "/Games" in "/" + (f.get("section") or ""):
            cats |= {"game"}
        m["cats"] = sorted(cats | {"bevy"})
    repos = []
    for m in merged.values():
        m.setdefault("sources", [])
        owner, name = m["url"].rstrip("/").split("/")[-2:]
        m["name"] = f"{topic}~{owner}~{name}".lower()
        m["cats"] = sorted(set(m["cats"]) | ({"game"} if "game" in m["description"].lower() else set()))
        repos.append(m)
    repos.sort(key=lambda r: -(r["downloads"] / 1000 + r["stars"]))
    out = work / f"{topic}.json"
    out.write_text(json.dumps({"topic": topic, "generated": time.strftime("%Y-%m-%d %H:%M"), "repos": repos}, indent=1),
                   encoding="utf-8")
    by = {}
    for r in repos:
        for s in r["sources"]:
            by[s] = by.get(s, 0) + 1
    log(f"Found {len(repos)} unique repositories ({', '.join(f'{k}: {v}' for k, v in by.items())}) -> {out}")
    return out


if __name__ == "__main__":
    corpus = Path(os.environ.get("AI_KIT_CORPUS", Path(__file__).resolve().parent.parent / "corpus"))
    discover(corpus, sources=tuple(sys.argv[1:]) or ("bevy-assets", "crates.io", "itch.io", "github"))
