"""Self-repair: find, verify and fix the things Windmill depends on, automatically.

llama.cpp is the main one. People install it in many ways (unzipped in Downloads, C:\\llama.cpp, winget,
scoop, chocolatey, homebrew, built from source...) and builds can be broken (a CUDA build without its
cudart DLLs) or too old for new models. Windmill looks everywhere, test-runs each copy, repairs what it can,
and downloads a fresh one only when nothing usable exists. It also re-finds a model file that was moved.
Setting `auto_fix` (default true) - set it to false to be asked instead."""
import os
import re
import shutil
import subprocess
import zipfile
from pathlib import Path

from . import config, models, ui

EXE = "llama-server.exe" if os.name == "nt" else "llama-server"
DLL_NOT_FOUND = {3221225781, -1073741515}  # Windows STATUS_DLL_NOT_FOUND
NEEDED_FLAGS = ("--n-cpu-moe", "--jinja")   # features Windmill relies on (newer llama.cpp)


# ------------------------------------------------------------------ where llama.cpp might be
def search_roots():
    home = Path.home()
    roots = [(config.KIT / "llama.cpp", 6), (home / "Downloads", 4), (home / "Desktop", 4), (home / "Documents", 4),
             (home / "llama.cpp", 6), (home / "llama", 5), (home / "AI", 5), (home / "tools", 5), (home, 2)]
    if os.name == "nt":
        la = Path(os.environ.get("LOCALAPPDATA", home / "AppData" / "Local"))
        roots += [(Path("C:/llama.cpp"), 6), (Path("C:/llama"), 5), (Path("C:/AI"), 5), (Path("C:/tools"), 5),
                  (Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "llama.cpp", 5),
                  (la / "Microsoft" / "WinGet" / "Packages", 4), (la / "Microsoft" / "WinGet" / "Links", 1),
                  (home / "scoop" / "apps" / "llama.cpp", 4), (Path("C:/ProgramData/chocolatey/bin"), 1),
                  (la / "Programs", 4)]
    else:
        roots += [(Path("/usr/local/bin"), 1), (Path("/opt/homebrew/bin"), 1), (Path("/usr/bin"), 1),
                  (home / ".local" / "bin", 1), (Path("/opt/llama.cpp"), 5)]
    return roots


def _walk(root, depth, name):
    root = Path(root)
    if not root.is_dir():
        return
    base = len(root.parts)
    try:
        for dirpath, dirs, files in os.walk(root):
            d = Path(dirpath)
            if len(d.parts) - base >= depth:
                dirs[:] = []
            dirs[:] = [x for x in dirs if not x.startswith(".") and x not in ("node_modules", "target", "AppData",
                                                                               "site-packages", "__pycache__")]
            if name in files:
                yield d / name
    except OSError:
        return


def candidates(cfg=None):
    cfg = cfg or config.load()
    seen, out = set(), []

    def add(p):
        try:
            rp = Path(p).resolve()
        except OSError:
            return
        if rp.is_file() and rp not in seen:
            seen.add(rp)
            out.append(rp)

    for p in (cfg.get("llama_server"), os.environ.get("LLAMA_SERVER"), shutil.which("llama-server")):
        if p:
            add(p)
    if os.environ.get("LLAMA_CPP_DIR"):
        for p in _walk(os.environ["LLAMA_CPP_DIR"], 4, EXE):
            add(p)
    for root, depth in search_roots():
        for p in _walk(root, depth, EXE):
            add(p)
    return out


def downloaded_zips():
    """llama.cpp release zips someone downloaded but never unzipped (or only unzipped half of)."""
    home = Path.home()
    zips = []
    for d in (home / "Downloads", home / "Desktop", home / "Documents"):
        if d.is_dir():
            zips += [z for z in d.glob("*.zip") if re.match(r"(llama-.*-bin-|cudart-llama-bin-)", z.name)]
    return sorted(zips, key=lambda z: z.stat().st_mtime, reverse=True)


# ------------------------------------------------------------------ verify a binary
def probe(binary):
    """Run it. Returns (status, detail, build_number). status: ok | old | missing-dlls | broken."""
    try:
        r = subprocess.run([str(binary), "--version"], capture_output=True, text=True, timeout=30,
                           encoding="utf-8", errors="replace", cwd=str(Path(binary).parent))
    except (OSError, subprocess.TimeoutExpired) as e:
        return "broken", str(e), None
    out = (r.stdout + r.stderr)
    if r.returncode in DLL_NOT_FOUND or re.search(r"(cudart|cublas)\S*\.dll|error while loading shared libraries", out, re.I):
        return "missing-dlls", out.strip()[:300], None
    m = re.search(r"version:\s*(\d+)", out)
    build = int(m.group(1)) if m else None
    if r.returncode != 0 and not m:
        return "broken", out.strip()[:300] or f"exit code {r.returncode}", None
    try:
        h = subprocess.run([str(binary), "--help"], capture_output=True, text=True, timeout=30,
                           encoding="utf-8", errors="replace", cwd=str(Path(binary).parent))
        helptext = h.stdout + h.stderr
    except (OSError, subprocess.TimeoutExpired):
        helptext = ""
    if helptext and not all(f in helptext for f in NEEDED_FLAGS):
        return "old", f"build {build or '?'} lacks {', '.join(f for f in NEEDED_FLAGS if f not in helptext)}", build
    return "ok", f"build {build or '?'}", build


def is_cuda_build(binary):
    d = Path(binary).parent
    return any(d.glob("ggml-cuda*")) or "cuda" in str(binary).lower()


def add_cudart(binary):
    """Fix a CUDA build that can't start because the CUDA runtime DLLs aren't next to it."""
    d = Path(binary).parent
    for z in downloaded_zips():
        if z.name.startswith("cudart-"):
            with zipfile.ZipFile(z) as zf:
                for m in zf.infolist():
                    if m.filename.lower().endswith(".dll"):
                        (d / Path(m.filename).name).write_bytes(zf.read(m))
            return probe(binary)[0] == "ok"
    try:  # not downloaded: fetch the matching runtime from the latest release
        from .server import download, pick_assets
        import io
        import json
        import urllib.request
        req = urllib.request.Request("https://api.github.com/repos/ggml-org/llama.cpp/releases/latest",
                                     headers={"User-Agent": "windmill"})
        rel = json.loads(urllib.request.urlopen(req, timeout=30).read())
        names = pick_assets(rel["assets"], models.hardware())
        url = {a["name"]: a["browser_download_url"] for a in rel["assets"]}
        for n in names:
            if n and n.startswith("cudart"):
                with zipfile.ZipFile(io.BytesIO(download(url[n]))) as zf:
                    for m in zf.infolist():
                        if m.filename.lower().endswith(".dll"):
                            (d / Path(m.filename).name).write_bytes(zf.read(m))
        return probe(binary)[0] == "ok"
    except Exception:  # noqa: BLE001
        return False


def unzip_downloads():
    """Extract downloaded llama.cpp zips into windmill/llama.cpp (main build + its cudart, newest first)."""
    zips = downloaded_zips()
    mains = [z for z in zips if z.name.startswith("llama-")]
    if not mains:
        return None
    dest = config.KIT / "llama.cpp"
    dest.mkdir(parents=True, exist_ok=True)
    picked = [mains[0]]
    cuda = re.search(r"cuda-([\d.]+)", mains[0].name)
    if cuda:
        rt = [z for z in zips if z.name.startswith("cudart-") and f"cuda-{cuda.group(1)}" in z.name] or \
             [z for z in zips if z.name.startswith("cudart-")]
        picked += rt[:1]
    for z in picked:
        with zipfile.ZipFile(z) as zf:
            for m in zf.infolist():
                name = Path(m.filename).name
                if name and not m.is_dir():
                    target = dest / name
                    target.write_bytes(zf.read(m))
                    if os.name != "nt":
                        target.chmod(0o755)
    b = dest / EXE
    return b if b.exists() else None


# ------------------------------------------------------------------ the repair routine
def ensure_llama(cfg=None, quiet=False, allow_download=True):
    """Return a working llama-server path, fixing whatever is wrong. None only if everything failed."""
    cfg = cfg or config.load()
    say = (lambda m: None) if quiet else ui.info
    cur = cfg.get("llama_server")
    if cur and Path(cur).is_file() and probe(cur)[0] == "ok":
        return cur
    found = candidates(cfg)
    if not found:
        b = unzip_downloads()
        if b:
            say(f"Found llama.cpp zip(s) in your Downloads - unpacked them into {b.parent}")
            found = [b]
    results = []
    for b in found:
        status, detail, build = probe(b)
        if status == "missing-dlls" and os.name == "nt" and is_cuda_build(b):
            say(f"{b} can't start: the CUDA runtime DLLs are missing - adding them...")
            if add_cudart(b):
                status, detail, build = probe(b)
        results.append((b, status, detail, build))
    good = sorted([r for r in results if r[1] == "ok"], key=lambda r: (r[3] or 0, is_cuda_build(r[0])), reverse=True)
    if good:
        b = str(good[0][0])
        if b != cur:
            config.set_value("llama_server", b)
            say(f"Using the llama.cpp found at {b} ({good[0][2]}).")
        return b
    for b, status, detail, _ in results:
        say(f"Found {b}, but it's not usable ({status}: {detail[:120]}).")
    if not allow_download:
        return None
    if results and any(r[1] == "old" for r in results):
        say("Your llama.cpp is too old for Windmill's settings - installing the latest version...")
    else:
        say("llama.cpp isn't installed (or no copy works) - installing the right version for this PC...")
    try:
        from .server import install_llamacpp
        b = install_llamacpp()
    except Exception as e:  # noqa: BLE001
        ui.err(f"Automatic install failed: {e}")
        return None
    if b and probe(b)[0] == "missing-dlls" and os.name == "nt":
        add_cudart(b)
    return b


def ensure_model(cfg=None, quiet=False):
    """If the selected model file was moved or renamed folders, find it again by file name."""
    cfg = cfg or config.load()
    p = cfg.get("model_path")
    if not p or Path(p).exists():
        return p
    name = Path(p).name.lower()
    for m in models.discover(cfg):
        if Path(m["path"]).name.lower() == name:
            config.set_value("model_path", m["path"])
            if not quiet:
                ui.info(f"Your model moved - found it again at {m['path']}")
            return m["path"]
    return None


def report():
    """Everything found, for `windmill doctor --deep` and `windmill fix`."""
    cfg = config.load()
    ui.rule("llama.cpp copies on this computer")
    found = candidates(cfg)
    if not found:
        print("  none found" + (f" (but {len(downloaded_zips())} downloaded zip(s) in Downloads)" if downloaded_zips() else ""))
    for b in found:
        status, detail, _ = probe(b)
        mark = ui.green("ok ") if status == "ok" else ui.yellow(status)
        print(f"  {mark} {b}  {ui.dim(detail[:100])}")
    print(f"  {ui.dim('selected:')} {cfg.get('llama_server') or 'none'}")
