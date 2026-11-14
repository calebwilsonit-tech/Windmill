"""Run llama-server in the background so the user never has to: start / stop / status / auto-tune,
and download llama.cpp itself if it's missing."""
import io
import json
import os
import re
import platform
import shutil
import signal
import subprocess
import sys
import time
import urllib.request
import zipfile
from pathlib import Path

from . import config, models, ui

PID = config.HOME / "server.pid"
LOG = config.LOGS / "server.log"
EXE = "llama-server.exe" if os.name == "nt" else "llama-server"


def find_binary(cfg):
    """A working llama-server, wherever it was installed (no downloading here; see heal.ensure_llama)."""
    from . import heal
    return heal.ensure_llama(cfg, quiet=True, allow_download=False)


def health(cfg, timeout=2):
    base = config.api_base(cfg).rsplit("/v1", 1)[0]
    try:
        with urllib.request.urlopen(f"{base}/health", timeout=timeout) as r:
            return json.loads(r.read() or b"{}").get("status", "ok") == "ok"
    except Exception:  # noqa: BLE001
        return False


def running_pid():
    if not PID.exists():
        return None
    try:
        pid = int(PID.read_text().split()[0])
    except (ValueError, IndexError):
        return None
    try:
        if os.name == "nt":
            r = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True)
            return pid if str(pid) in r.stdout else None
        os.kill(pid, 0)
        return pid
    except OSError:
        return None


def status(cfg=None):
    cfg = cfg or config.load()
    pid = running_pid()
    up = health(cfg)
    parts = PID.read_text().split(maxsplit=2) if PID.exists() else []
    loaded = parts[1].replace("_", " ") if len(parts) > 1 else None
    return {"pid": pid, "healthy": up, "model": loaded}


def build_cmd(binary, model, hw, cfg):
    p = models.server_params(model, hw, cfg)
    cmd = [binary, "-m", model["path"], "--alias", "local", "-c", str(p["ctx"]), "-ngl", str(p["ngl"]),
           "-fa", "on", "--cache-type-k", p["cache"], "--cache-type-v", p["cache"], "-t", str(p["threads"]),
           "--jinja", "--host", "127.0.0.1", "--port", str(cfg["port"])]
    if p.get("cpu_moe"):
        cmd += ["--n-cpu-moe", str(p["cpu_moe"])]
    mm = models.mmproj_for(model["path"])
    if mm:  # the model can see pictures
        cmd += ["--mmproj", mm]
    return cmd, p


def start(cfg=None, quiet=False, wait=True):
    cfg = cfg or config.load()
    if health(cfg):
        return True
    stop(quiet=True)
    from . import heal
    if cfg.get("auto_fix", True):
        binary = heal.ensure_llama(cfg)           # finds / repairs / installs automatically
        heal.ensure_model(cfg)
        cfg = config.load()
    else:
        binary = find_binary(cfg)
    if not binary:
        ui.err("Couldn't get llama.cpp working. Run  windmill fix  to see what's wrong.")
        return False
    model = models.current_model(cfg)
    if not model:
        ui.err("No model selected. Run:  windmill models")
        return False
    hw = models.hardware()
    cmd, p = build_cmd(binary, model, hw, cfg)
    config.LOGS.mkdir(parents=True, exist_ok=True)
    log = open(LOG, "w", encoding="utf-8", errors="replace")
    kw = {"stdout": log, "stderr": subprocess.STDOUT, "stdin": subprocess.DEVNULL}
    if os.name == "nt":
        kw["creationflags"] = 0x00000200 | 0x08000000  # own group, hidden console (DETACHED would make every child flash a window)
    else:
        kw["start_new_session"] = True
    proc = subprocess.Popen(cmd, **kw)
    PID.write_text(f"{proc.pid} {model['name'].replace(' ', '_')} {model['path']}")
    d = _busy()
    d["last_used"] = time.time()
    _save_busy(d)
    if not quiet:
        ui.info(f"Starting {ui.bold(model['name'])} "
                + ui.dim(f"(ctx {p['ctx']}, GPU layers {p['ngl']}" + (f", experts on CPU {p['cpu_moe']}" if p.get('cpu_moe') else "") + ")"))
    if not wait:
        return True
    with ui.Spinner("loading model into memory (first time can take a minute)") as sp:
        for _ in range(900):
            if health(cfg):
                break
            if proc.poll() is not None:
                break
            time.sleep(1)
    if health(cfg):
        if not quiet:
            ui.ok("Model is ready.")
        try:  # double-check the graphics card is really in use (the log names the GPU backend when it is)
            from . import heal
            log = LOG.read_text(encoding="utf-8", errors="replace") if LOG.exists() else ""
            gpu_used = bool(re.search(r"CUDA\d|Vulkan\d|offloaded \d+/\d+ layers to GPU", log))
            if heal.has_nvidia() and log:
                config.set_value("gpu_not_used", not gpu_used)  # next repair fetches a build this PC's GPU can run
            if heal.has_nvidia() and log and not gpu_used:
                ui.warn("The AI is running on the processor only, not your graphics card (much slower). "
                        "Run  windmill fix  to get the graphics-card version of llama.cpp.")
        except Exception:  # noqa: BLE001
            pass
        return True
    ui.err("The model server didn't start. Last lines of the log:")
    print(ui.dim(tail_log(15)))
    hint = diagnose_log(tail_log(80))
    if hint:
        ui.warn(hint)
    return False


def stop(quiet=False):
    pid = running_pid()
    if pid:
        try:
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
            else:
                os.killpg(os.getpgid(pid), signal.SIGTERM)
        except OSError:
            pass
        if not quiet:
            ui.ok("Model server stopped.")
    PID.unlink(missing_ok=True)


# ------------------------------------------------------------------ two models, one at a time: coding + chat
# Builds / fixes use the coding model; everyday chats can use a separate chat model (writing, questions). Only one is
# ever loaded (switching unloads the other first), a model that's in use is never swapped out from under its work,
# and after `unload_after_minutes` unused the model is unloaded so its memory is free again.
BUSY = config.HOME / "model-busy.json"


def role_cfg(cfg, role, model_path=None):
    """The settings to run the model for a role ('code' or 'chat'), or for a model a chat picked itself."""
    if model_path and Path(model_path).exists():
        if model_path == cfg.get("model_path"):
            return cfg
        if model_path == cfg.get("chat_model_path"):
            return role_cfg(cfg, "chat")
        return {**cfg, "model_path": model_path, "model_name": Path(model_path).stem, "cpu_moe": None, "ngl": None}
    if role == "chat" and cfg.get("chat_model_path") and Path(cfg["chat_model_path"]).exists() \
            and cfg["chat_model_path"] != cfg.get("model_path"):
        return {**cfg, "model_path": cfg["chat_model_path"], "model_name": cfg.get("chat_model_name"),
                "cpu_moe": cfg.get("chat_cpu_moe"), "ngl": cfg.get("chat_ngl")}
    return cfg


def _alive(pid):
    try:
        if os.name == "nt":
            r = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True,
                               creationflags=0x08000000)
            return str(pid) in r.stdout
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _busy():
    try:
        d = json.loads(BUSY.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        d = {"holders": {}, "last_used": time.time()}
    d["holders"] = {k: v for k, v in d.get("holders", {}).items() if _alive(int(v["pid"]))}
    return d


def _save_busy(d):
    try:
        BUSY.parent.mkdir(parents=True, exist_ok=True)
        BUSY.write_text(json.dumps(d), encoding="utf-8")
    except OSError:
        pass


class hold:
    """`with server.hold("code"):` - marks the loaded model as in use (so it isn't swapped or unloaded meanwhile)."""

    def __init__(self, role="code"):
        self.role, self.key = role, f"{os.getpid()}-{id(self)}"

    def __enter__(self):
        d = _busy()
        d["holders"][self.key] = {"pid": os.getpid(), "role": self.role, "since": time.time()}
        _save_busy(d)
        return self

    def __exit__(self, *a):
        d = _busy()
        d["holders"].pop(self.key, None)
        d["last_used"] = time.time()
        _save_busy(d)


def loaded_path():
    if not PID.exists() or not running_pid():
        return None
    parts = PID.read_text(encoding="utf-8").split(maxsplit=2)
    return parts[2] if len(parts) > 2 else None


def ensure(cfg=None, role="code", model_path=None):
    """Make sure the model for this kind of work is running. Switches models only when nothing is using the other one
    (then this work simply uses the loaded model - e.g. a chat during a build uses the coding model)."""
    cfg = cfg or config.load()
    want = role_cfg(cfg, role, model_path)
    up = health(cfg)
    if up:
        have = loaded_path()
        if not have or not want.get("model_path") or Path(have) == Path(want["model_path"]):
            return True
        others = [h for h in _busy()["holders"].values() if h["role"] != role]
        if others and role == "code":  # a build waits for a chat answer to finish (short) rather than run on the chat model
            ui.info("(waiting for the chat answer to finish, then switching to the coding model)")
            end = time.time() + 900
            while others and time.time() < end:
                time.sleep(2)
                others = [h for h in _busy()["holders"].values() if h["role"] != role]
        if others:
            ui.info("(the other model is busy with " + ("a build" if others[0]["role"] == "code" else "a chat")
                    + " - using it for this too)")
            return True
        ui.info(f"Switching to {want.get('model_name') or Path(want['model_path']).stem} (about a minute)...")
        stop(quiet=True)
    if not cfg.get("auto_start_server", True):
        ui.err("Model server is not running. Start it with:  windmill server start")
        return False
    return start(want)


def unload_if_idle(cfg=None):
    """Free the model's memory when it hasn't been used for a while (and nothing is using it)."""
    cfg = cfg or config.load()
    mins = cfg.get("unload_after_minutes", 30) or 0
    if mins <= 0 or not running_pid():
        return False
    d = _busy()
    if d["holders"] or time.time() - d.get("last_used", time.time()) < mins * 60:
        return False
    stop(quiet=True)
    return True


def tail_log(n=20):
    if not LOG.exists():
        return ""
    return "\n".join(LOG.read_text(encoding="utf-8", errors="replace").splitlines()[-n:])


def diagnose_log(text):
    t = text.lower()
    if "out of memory" in t or "failed to allocate" in t or "cudamalloc failed" in t:
        return "Ran out of memory. Try: windmill tune   (or pick a smaller model / lower context in windmill settings)"
    if "unknown argument" in t or "invalid argument" in t:
        return "Your llama.cpp is too old for these settings. Run: windmill setup --update-llama"
    if "unknown model architecture" in t:
        return "Your llama.cpp doesn't know this model yet. Run: windmill setup --update-llama"
    if "address already in use" in t or "bind" in t and "failed" in t:
        return "Port is busy. Change it: windmill settings port 8081"
    return None


# ------------------------------------------------------------------ install llama.cpp
def pick_assets(assets, hw):
    """Choose the right llama.cpp release files for this OS/GPU."""
    names = [a["name"] for a in assets]
    osn, mach = platform.system(), platform.machine().lower()
    def first(*subs, exclude=()):
        for n in names:
            if all(s in n for s in subs) and not any(e in n for e in exclude) and n.endswith(".zip"):
                return n
        return None
    if osn == "Windows":
        arm = "arm64" in mach
        if hw.get("gpu") and "nvidia" in hw["gpu"].lower() or hw.get("vram_gb"):
            cudas = sorted((n for n in names if n.startswith("llama-") and "win-cuda" in n and "x64" in n and n.endswith(".zip")), reverse=True)
            try:  # CUDA 13 builds need an NVIDIA driver from the 580 series or newer - otherwise use CUDA 12
                drv = int(str(hw.get("driver") or "0").split(".")[0])
            except ValueError:
                drv = 0
            if drv < 580:
                cudas = [n for n in cudas if "win-cuda-13" not in n and "win-cuda-14" not in n] or cudas
            for main in cudas:  # newest CUDA build (that this driver runs) that also has its runtime zip
                ver = main.split("win-cuda-")[1].split("-x64")[0]
                rt = next((n for n in names if n.startswith("cudart") and f"cuda-{ver}" in n), None)
                if rt:
                    return [main, rt]
        return [first("win-vulkan", "x64") or first("win-cpu", "arm64" if arm else "x64")]
    if osn == "Darwin":
        return [first("macos", "arm64" if "arm" in mach else "x64")]
    return [first("ubuntu", "vulkan", "x64") if hw.get("vram_gb") else first("ubuntu", "x64", exclude=("vulkan",))]


def _releases():
    """Recent llama.cpp releases as [{tag_name, assets: [{name, browser_download_url}]}], newest first.
    Uses GitHub's API; if that refuses (it allows ~60 requests/hour per network), reads the normal releases page."""
    try:
        req = urllib.request.Request("https://api.github.com/repos/ggml-org/llama.cpp/releases?per_page=6",
                                     headers={"Accept": "application/vnd.github+json", "User-Agent": "windmill"})
        with urllib.request.urlopen(req, timeout=30) as r:
            rels = json.loads(r.read())
        if rels:
            return rels
    except Exception:  # noqa: BLE001 - fall back to the web page
        pass
    req = urllib.request.Request("https://github.com/ggml-org/llama.cpp/releases/latest", headers={"User-Agent": "windmill"})
    with urllib.request.urlopen(req, timeout=30) as r:
        tag = r.geturl().rstrip("/").split("/")[-1]
    req = urllib.request.Request(f"https://github.com/ggml-org/llama.cpp/releases/expanded_assets/{tag}",
                                 headers={"User-Agent": "windmill"})
    names = {}
    for url in (f"https://github.com/ggml-org/llama.cpp/releases/expanded_assets/{tag}",  # the file list
                f"https://github.com/ggml-org/llama.cpp/releases/tag/{tag}"):            # the release notes' links
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "windmill"}), timeout=30) as r:
                page = r.read().decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            continue
        for n in re.findall(rf'(?:https://github\.com)?/ggml-org/llama\.cpp/releases/download/{re.escape(tag)}/([^"\s<>)]+)', page):
            names.setdefault(n, None)
    return [{"tag_name": tag, "assets": [{"name": n, "browser_download_url":
                                          f"https://github.com/ggml-org/llama.cpp/releases/download/{tag}/{n}"} for n in names]}]


_releases_seen = []


def install_llamacpp(hw=None):
    hw = hw or models.hardware()
    ui.info("Looking up the latest llama.cpp release...")
    rel, chosen = None, []
    _releases_seen[:] = _releases()
    for r in _releases_seen:  # the newest release whose files for this PC are uploaded (new ones fill in over minutes)
        chosen = [n for n in pick_assets(r.get("assets") or [], hw) if n]
        if chosen:
            rel = r
            break
    if not rel:
        seen = ", ".join(f"{r.get('tag_name')} ({len(r.get('assets') or [])} files)" for r in _releases_seen) or "none"
        ui.warn(f"(releases looked at: {seen})")
        ui.err("Couldn't find a llama.cpp build for this computer. Download it manually from "
               "github.com/ggml-org/llama.cpp/releases into the windmill\\llama.cpp folder.")
        return None
    dest = config.KIT / "llama.cpp"
    dest.mkdir(exist_ok=True)
    urls = {a["name"]: a["browser_download_url"] for a in rel["assets"]}
    for name in chosen:
        ui.info(f"Downloading {name} ({rel['tag_name']})...")
        data = download(urls[name])
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            for m in z.infolist():
                target = dest / Path(m.filename).name  # flatten: everything next to llama-server
                if m.is_dir() or not Path(m.filename).name:
                    continue
                with z.open(m) as src, open(target, "wb") as out:
                    shutil.copyfileobj(src, out)
                if os.name != "nt":
                    target.chmod(0o755)
    b = find_binary({})
    if b:
        config.set_value("llama_server", b)
        ui.ok(f"llama.cpp {rel['tag_name']} installed.")
    return b


def download(url, dest=None):
    req = urllib.request.Request(url, headers={"User-Agent": "windmill"})
    with urllib.request.urlopen(req, timeout=60) as r:
        total = int(r.headers.get("Content-Length") or 0)
        buf = open(dest, "wb") if dest else io.BytesIO()
        done, t0 = 0, time.time()
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            buf.write(chunk)
            done += len(chunk)
            if total and (sys.stdout.isatty() or os.environ.get("WINDMILL_GUI")):
                pct = done * 100 // total
                speed = done / max(0.1, time.time() - t0) / 2**20
                sys.stdout.write(f"\r  {ui.cyan('█' * (pct // 4))}{ui.gray('░' * (25 - pct // 4))} {pct:3d}%  "
                                 f"{done / 2**30:.2f}/{total / 2**30:.2f} GB  {speed:.1f} MB/s ")
                sys.stdout.flush()
        if total:
            print()
        if dest:
            buf.close()
            return dest
        return buf.getvalue()


# ------------------------------------------------------------------ tune
def tune(cfg=None):
    """Find how much of the model the GPU can hold: start the server with fewer and fewer
    experts/layers on the CPU while VRAM stays below ~90%."""
    cfg = cfg or config.load()
    hw = models.hardware()
    model = models.current_model(cfg)
    if not model:
        ui.err("Pick a model first: windmill models")
        return
    if not hw["vram_gb"]:
        ui.info("No NVIDIA GPU detected - nothing to tune; the model runs on the CPU.")
        return
    layers = model.get("layers") or 48
    key = "cpu_moe" if model.get("experts") else "ngl"
    limit = hw["vram_gb"] * 0.9
    ui.info(f"Tuning {model['name']} for {hw['gpu']} ({hw['vram_gb']:.1f} GB). This restarts the model a few times.")
    best = None
    values = range(layers, -1, -4) if key == "cpu_moe" else range(4, layers + 5, 4)
    for v in values:
        cfg[key] = v
        stop(quiet=True)
        if not start(cfg, quiet=True):
            break
        g = nvidia_used()
        ui.info(f"  {key}={v:3d}  VRAM {g:.1f}/{hw['vram_gb']:.1f} GB")
        if g > limit:
            break
        best = v
    if best is None:
        best = layers if key == "cpu_moe" else 0
    config.set_value(key, best)
    stop(quiet=True)
    ui.ok(f"Saved {key}={best}. The model will now use your GPU as much as it safely can.")


def nvidia_used():
    g = models.nvidia_smi()
    return g["vram_used_gb"] if g else 0.0
