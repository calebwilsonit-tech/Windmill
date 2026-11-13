"""Every chat is saved; find and resume any of them by number, by words in it, or 'the last one here'."""
import json
import re
import sqlite3
import time
import uuid
from pathlib import Path

from . import config

DB = config.HOME / "sessions.db"


def db():
    config.HOME.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB)
    c.execute("CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY, title TEXT, project TEXT, created REAL, "
              "updated REAL, n INT DEFAULT 0, summary TEXT DEFAULT '')")
    c.execute("CREATE TABLE IF NOT EXISTS messages(session TEXT, idx INT, msg TEXT, PRIMARY KEY(session, idx))")
    c.execute("CREATE VIRTUAL TABLE IF NOT EXISTS search USING fts5(session UNINDEXED, text, tokenize='unicode61')")
    return c


class Session:
    def __init__(self, sid, title, project, messages=None):
        self.id, self.title, self.project = sid, title, project
        self.messages = messages or []

    @classmethod
    def new(cls, project, title="New chat"):
        s = cls(uuid.uuid4().hex[:10], title, str(project))
        now = time.time()
        with db() as c:
            c.execute("INSERT INTO sessions VALUES (?,?,?,?,?,0,'')", (s.id, title, s.project, now, now))
        return s

    @classmethod
    def load(cls, sid):
        with db() as c:
            row = c.execute("SELECT id, title, project FROM sessions WHERE id=?", (sid,)).fetchone()
            if not row:
                return None
            msgs = [json.loads(m) for (m,) in c.execute("SELECT msg FROM messages WHERE session=? ORDER BY idx", (sid,))]
        return cls(row[0], row[1], row[2], msgs)

    def add(self, msg):
        self.messages.append(msg)
        idx = len(self.messages) - 1
        text = msg.get("content") or ""
        if isinstance(text, list):
            text = " ".join(p.get("text", "") for p in text if isinstance(p, dict))
        with db() as c:
            c.execute("INSERT OR REPLACE INTO messages VALUES (?,?,?)", (self.id, idx, json.dumps(msg, ensure_ascii=False)))
            if msg.get("role") in ("user", "assistant") and text and not text.startswith("[WINDMILL"):
                c.execute("INSERT INTO search VALUES (?,?)", (self.id, text[:20000]))
            if msg.get("role") == "user" and self.title == "New chat" and text and not text.startswith("["):
                self.title = make_title(text)
                c.execute("UPDATE sessions SET title=? WHERE id=?", (self.title, self.id))
            c.execute("UPDATE sessions SET updated=?, n=? WHERE id=?", (time.time(), len(self.messages), self.id))

    def replace_all(self, messages):
        """After compaction: store the new (shorter) history."""
        self.messages = []
        with db() as c:
            c.execute("DELETE FROM messages WHERE session=?", (self.id,))
        for m in messages:
            self.messages.append(m)
            with db() as c:
                c.execute("INSERT OR REPLACE INTO messages VALUES (?,?,?)",
                          (self.id, len(self.messages) - 1, json.dumps(m, ensure_ascii=False)))

    def rename(self, title):
        self.title = title
        with db() as c:
            c.execute("UPDATE sessions SET title=? WHERE id=?", (title, self.id))


def make_title(text):
    t = re.sub(r"\s+", " ", text).strip()
    t = re.sub(r"^(please|can you|could you|i want( you)? to|help me)\s+", "", t, flags=re.I)
    return (t[:60] + "…") if len(t) > 60 else t


def recent(limit=20, project=None, query=None, include_auto=False):
    with db() as c:
        if query:
            q = " OR ".join(f'"{w}"' for w in re.findall(r"\w{2,}", query)) or '""'
            best = {}
            for sid, score in c.execute("SELECT session, bm25(search) FROM search WHERE search MATCH ? "
                                        "ORDER BY bm25(search) LIMIT 500", (q,)):
                best[sid] = min(score, best.get(sid, 0))
            meta = {r[0]: r for r in c.execute(
                f"SELECT id, title, project, updated, n FROM sessions WHERE id IN ({','.join('?' * len(best))})",
                list(best))} if best else {}
            rows = [(*meta[sid], sc) for sid, sc in sorted(best.items(), key=lambda x: x[1]) if sid in meta][:limit]
            title_hits = c.execute("SELECT id, title, project, updated, n, 0 FROM sessions WHERE title LIKE ? "
                                   "ORDER BY updated DESC LIMIT ?", (f"%{query}%", limit)).fetchall()
            seen, merged = set(), []
            for r in title_hits + rows:
                if r[0] not in seen:
                    seen.add(r[0])
                    merged.append(r)
            rows = [r for r in merged if include_auto or not r[1].startswith("[auto]")][:limit]
        elif project:
            rows = c.execute("SELECT id, title, project, updated, n, 0 FROM sessions WHERE project=? AND n>0 "
                             "AND (? OR title NOT LIKE '[auto]%') ORDER BY updated DESC LIMIT ?",
                             (str(project), include_auto, limit)).fetchall()
        else:
            rows = c.execute("SELECT id, title, project, updated, n, 0 FROM sessions WHERE n>0 "
                             "AND (? OR title NOT LIKE '[auto]%') ORDER BY updated DESC LIMIT ?",
                             (include_auto, limit)).fetchall()
    return [{"id": r[0], "title": r[1], "project": r[2], "updated": r[3], "n": r[4]} for r in rows]


def resolve(arg, project=None):
    """'3' -> 3rd most recent; 'a1b2c3' -> id; 'inventory bug' -> best search hit; '' -> last here."""
    arg = (arg or "").strip()
    if not arg:
        r = recent(1, project=project) or recent(1)
        return r[0]["id"] if r else None
    if arg.isdigit():
        r = recent(int(arg))
        return r[int(arg) - 1]["id"] if len(r) >= int(arg) else None
    with db() as c:
        row = c.execute("SELECT id FROM sessions WHERE id LIKE ?", (arg + "%",)).fetchone()
    if row:
        return row[0]
    r = recent(1, query=arg)
    return r[0]["id"] if r else None


def delete(sid):
    with db() as c:
        for t in ("sessions", "messages", "search"):
            c.execute(f"DELETE FROM {t} WHERE {'id' if t == 'sessions' else 'session'}=?", (sid,))


def export_markdown(sid, dest):
    s = Session.load(sid)
    lines = [f"# {s.title}", f"_project: {s.project}_", ""]
    for m in s.messages:
        if m["role"] == "user" and not str(m.get("content", "")).startswith("[WINDMILL"):
            lines += ["## You", str(m["content"]), ""]
        elif m["role"] == "assistant" and m.get("content"):
            lines += ["## Windmill", str(m["content"]), ""]
    Path(dest).write_text("\n".join(lines), encoding="utf-8")
    return dest
