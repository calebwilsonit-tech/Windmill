"""Files and photos attached to a chat message (desktop and phone).

- Documents (text, code, csv, json, Word .docx, PDF) are read and put into the message, trimmed to a sensible size.
- Photos go to the model as images if it can see (has a vision adapter); otherwise the chat says so.
- Kept in ~/.windmill/uploads/<chat>/ - the chat window shows them again when you reopen the chat."""
import base64
import mimetypes
import re
import subprocess
import sys
import time
import zipfile
from html import unescape
from pathlib import Path

from . import config

ROOT = config.HOME / "uploads"
MAX_BYTES = 30 * 2**20
MAX_TEXT = 15000          # characters of a document put into a message (~4,000 tokens)
TEXT_EXT = {".txt", ".md", ".markdown", ".rst", ".py", ".rs", ".js", ".mjs", ".ts", ".tsx", ".jsx", ".html", ".htm",
            ".css", ".scss", ".json", ".csv", ".tsv", ".toml", ".yaml", ".yml", ".xml", ".ini", ".cfg", ".log", ".java",
            ".c", ".h", ".cpp", ".hpp", ".cs", ".go", ".kt", ".swift", ".sql", ".sh", ".bat", ".ps1", ".gd", ".lua",
            ".rb", ".php", ".dart", ".vue", ".svelte", ".tex", ".srt", ".env.example"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".heic", ".heif"}


def _safe(name):
    name = re.sub(r"[^\w.\- ()]+", "_", Path(str(name or "file")).name).strip(" .") or "file"
    return name[:120]


def save(sid, name, data):
    """Store an uploaded file. Returns its info for the chat."""
    if len(data) > MAX_BYTES:
        raise ValueError(f"That file is {len(data) / 2**20:.0f} MB - the limit is {MAX_BYTES // 2**20} MB.")
    d = ROOT / re.sub(r"[^0-9a-f]", "", sid)
    d.mkdir(parents=True, exist_ok=True)
    name = _safe(name)
    out = d / name
    if out.exists():
        out = d / f"{Path(name).stem}-{int(time.time() * 1000) % 100000}{Path(name).suffix}"
    out.write_bytes(data)
    return info(sid, out)


def info(sid, p):
    p = Path(p)
    return {"name": p.name, "kind": kind(p), "size": p.stat().st_size, "url": f"/api/uploads/{sid}/{p.name}"}


def path_of(sid, name):
    p = (ROOT / re.sub(r"[^0-9a-f]", "", sid) / _safe(name)).resolve()
    return p if ROOT.resolve() in p.parents and p.is_file() else None


def kind(p):
    ext = Path(p).suffix.lower()
    if ext in IMAGE_EXT:
        return "image"
    if ext in TEXT_EXT or ext in (".docx", ".pdf"):
        return "doc"
    return "other"


# ------------------------------------------------------------------ reading documents
def _docx(p):
    with zipfile.ZipFile(p) as z:
        xml = z.read("word/document.xml").decode("utf-8", errors="replace")
    xml = re.sub(r"</w:p>", "\n", xml)
    xml = re.sub(r"<w:tab/>", "\t", xml)
    return unescape(re.sub(r"<[^>]+>", "", xml))


def _pdf(p):
    try:
        import pypdf  # noqa: F401
    except ImportError:  # a small add-on, installed the first time a PDF is attached
        kw = {"creationflags": 0x08000000} if sys.platform == "win32" else {}
        subprocess.run([sys.executable, "-m", "pip", "install", "--user", "--quiet", "pypdf"], capture_output=True, **kw)
        import importlib
        import site
        importlib.invalidate_caches()
        try:
            sys.path.append(site.getusersitepackages())
        except Exception:  # noqa: BLE001
            pass
    import pypdf
    r = pypdf.PdfReader(str(p))
    return "\n\n".join((pg.extract_text() or "") for pg in r.pages[:60])


def read_text(p):
    p = Path(p)
    ext = p.suffix.lower()
    try:
        if ext == ".docx":
            t = _docx(p)
        elif ext == ".pdf":
            t = _pdf(p)
        else:
            t = p.read_bytes().decode("utf-8", errors="replace")
    except Exception as e:  # noqa: BLE001
        return f"(couldn't read this file: {e})"
    t = re.sub(r"\n{3,}", "\n\n", t).strip()
    if len(t) > MAX_TEXT:
        t = t[:MAX_TEXT] + f"\n[... {len(t) - MAX_TEXT:,} more characters not included ...]"
    return t or "(the file has no readable text)"


def image_url(p):
    p = Path(p)
    mime = mimetypes.guess_type(p.name)[0] or "image/jpeg"
    return f"data:{mime};base64," + base64.b64encode(p.read_bytes()).decode()


def build(sid, names, text):
    """-> (full message text for the model, [image data URLs], [attachment infos for the chat window])"""
    parts, images, shown = [text], [], []
    for n in names[:10]:
        p = path_of(sid, n)
        if not p:
            continue
        i = info(sid, p)
        shown.append(i)
        if i["kind"] == "image":
            images.append((p.name, image_url(p)))
        elif i["kind"] == "doc":
            parts.append(f"\n\n[Attached file: {p.name}]\n```\n{read_text(p)}\n```")
        else:
            parts.append(f"\n\n[Attached file: {p.name} ({i['size']:,} bytes) - not a text file, so its contents "
                         "aren't shown]")
    return "".join(parts), images[:4], shown
