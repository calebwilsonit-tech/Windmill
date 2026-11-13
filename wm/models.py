"""Find every local GGUF model (LM Studio, Ollama, Hugging Face cache, Downloads, custom folders),
read its header for architecture / MoE / layers, and work out good server settings for this PC."""
import json
import os
import platform
import re
import struct
import subprocess
from pathlib import Path

from . import config

QUANT_RE = re.compile(r"(IQ\d_[A-Z0-9_]+|Q\d_K(?:_[SMLX]+)?|Q\d_\d|Q\d|F16|BF16|F32|MXFP4)", re.I)


# ------------------------------------------------------------------ GGUF header
def gguf_meta(path):
    """Read just the metadata we need from a GGUF header (fast; skips the vocab)."""
    want_general = {"general.architecture", "general.name", "general.size_label", "general.basename"}
    meta = {}
    sizes = {0: 1, 1: 1, 2: 2, 3: 2, 4: 4, 5: 4, 6: 4, 7: 1, 10: 8, 11: 8, 12: 8}
    fmt = {0: "<B", 1: "<b", 2: "<H", 3: "<h", 4: "<I", 5: "<i", 6: "<f", 7: "<?", 10: "<Q", 11: "<q", 12: "<d"}
    try:
        with open(path, "rb") as f:
            if f.read(4) != b"GGUF":
                return None
            version = struct.unpack("<I", f.read(4))[0]
            if version < 2:
                return None
            _tensors, n_kv = struct.unpack("<QQ", f.read(16))

            def rstr():
                n = struct.unpack("<Q", f.read(8))[0]
                return f.read(n).decode("utf-8", errors="replace")

            for _ in range(n_kv):
                key = rstr()
                t = struct.unpack("<I", f.read(4))[0]
                if t == 8:
                    val = rstr()
                elif t == 9:
                    et, cnt = struct.unpack("<IQ", f.read(12))
                    if et == 8:
                        for _ in range(cnt):
                            f.seek(struct.unpack("<Q", f.read(8))[0], 1)
                    else:
                        f.seek(sizes.get(et, 1) * cnt, 1)
                    continue
                else:
                    val = struct.unpack(fmt[t], f.read(sizes[t]))[0]
                arch = meta.get("general.architecture")
                if key in want_general or (arch and key.startswith(arch + ".")):
                    meta[key] = val
                if arch and all(f"{arch}.{k}" in meta for k in ("block_count", "context_length", "expert_count")):
                    break  # have everything (dense models have no expert_count: we read to the end, it's fast)
    except (OSError, struct.error, KeyError):
        return meta or None
    arch = meta.get("general.architecture", "")
    return {
        "arch": arch,
        "name": meta.get("general.name") or meta.get("general.basename"),
        "size_label": meta.get("general.size_label"),
        "layers": meta.get(f"{arch}.block_count"),
        "experts": meta.get(f"{arch}.expert_count") or 0,
        "experts_used": meta.get(f"{arch}.expert_used_count") or 0,
        "ctx_train": meta.get(f"{arch}.context_length"),
    }


# ------------------------------------------------------------------ hardware
def hardware():
    hw = {"os": platform.system(), "ram_gb": 0.0, "gpu": None, "vram_gb": 0.0, "vram_used_gb": 0.0,
          "cpus": os.cpu_count() or 4}
    try:
        if hw["os"] == "Windows":
            import ctypes

            class MS(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("a", ctypes.c_ulonglong), ("b", ctypes.c_ulonglong), ("c", ctypes.c_ulonglong),
                            ("d", ctypes.c_ulonglong), ("e", ctypes.c_ulonglong)]
            m = MS()
            m.dwLength = ctypes.sizeof(MS)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
            hw["ram_gb"] = m.ullTotalPhys / 2**30
        elif hw["os"] == "Darwin":
            hw["ram_gb"] = int(subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True).stdout) / 2**30
        else:
            for line in open("/proc/meminfo"):
                if line.startswith("MemTotal"):
                    hw["ram_gb"] = int(line.split()[1]) / 2**20
    except Exception:  # noqa: BLE001
        pass
    gpu = nvidia_smi()
    if gpu:
        hw.update(gpu)
    return hw


def nvidia_smi():
    try:
        r = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total,memory.used", "--format=csv,noheader,nounits"],
                           capture_output=True, text=True, timeout=10,
                           creationflags=0x08000000 if os.name == "nt" else 0)  # no console window
        if r.returncode == 0 and r.stdout.strip():
            name, total, used = [x.strip() for x in r.stdout.strip().splitlines()[0].split(",")]
            return {"gpu": name, "vram_gb": float(total) / 1024, "vram_used_gb": float(used) / 1024}
    except (OSError, ValueError, subprocess.TimeoutExpired):
        pass
    return None


def default_threads(hw):
    n = hw["cpus"]
    return 8 if n >= 16 else max(2, n // 2)


# ------------------------------------------------------------------ discovery
def search_dirs(cfg):
    home = Path.home()
    dirs = [config.KIT / "models", *[Path(d) for d in cfg.get("model_dirs", [])],
            home / ".lmstudio" / "models", home / ".cache" / "lm-studio" / "models",
            Path(os.environ.get("HF_HOME", home / ".cache" / "huggingface")) / "hub",
            home / "Downloads", home / "models", home / "AI" / "models"]
    if os.name == "nt":
        la = Path(os.environ.get("LOCALAPPDATA", home / "AppData" / "Local"))
        dirs += [la / "LM Studio" / "models", home / "AppData" / "Roaming" / "LM Studio" / "models"]
    return [d for d in dirs if d.is_dir()]


def ollama_models():
    root = Path(os.environ.get("OLLAMA_MODELS", Path.home() / ".ollama" / "models"))
    man = root / "manifests"
    out = []
    if not man.is_dir():
        return out
    for mf in man.rglob("*"):
        if not mf.is_file():
            continue
        try:
            data = json.loads(mf.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            continue
        for layer in data.get("layers", []):
            if layer.get("mediaType", "").endswith("image.model"):
                blob = root / "blobs" / layer["digest"].replace(":", "-")
                if blob.exists():
                    parts = mf.relative_to(man).parts
                    out.append((blob, f"{parts[-2]}:{parts[-1]}", "Ollama"))
    return out


def discover(cfg=None):
    cfg = cfg or config.load()
    found, seen = [], set()
    cands = []
    for d in search_dirs(cfg):
        depth = 2 if d.name == "Downloads" else 6
        for p in _walk_gguf(d, depth):
            src = "LM Studio" if "lm" in str(d).lower() and "studio" in str(d).lower() else \
                  "Hugging Face" if "huggingface" in str(d) else "Windmill" if d == config.KIT / "models" else d.name
            cands.append((p, None, src))
    cands += ollama_models()
    for p, label, src in cands:
        if "mmproj" in p.name.lower() or re.search(r"-0000[2-9]-of-", p.name):
            continue
        try:
            real = p.resolve()
            if real in seen or real.stat().st_size < 50_000_000:
                continue
        except OSError:
            continue
        seen.add(real)
        meta = gguf_meta(real) or {}
        if not meta and src != "Ollama":
            continue
        size = _total_size(real)
        q = QUANT_RE.search(p.name)
        found.append({
            "path": str(real), "file": p.name, "source": src,
            "name": label or re.sub(r"(-0000\d-of-0000\d)?\.gguf$", "", p.name),
            "size_gb": round(size / 2**30, 1), "quant": q.group(1).upper() if q else "?",
            **{k: meta.get(k) for k in ("arch", "layers", "experts", "experts_used", "ctx_train", "size_label")},
        })
    found.sort(key=lambda m: (-m["size_gb"]))
    return found


def _walk_gguf(d, depth):
    try:
        for root, dirs, files in os.walk(d, followlinks=True):
            if root[len(str(d)):].count(os.sep) >= depth:
                dirs[:] = []
            dirs[:] = [x for x in dirs if not x.startswith(".") or x == ".cache"]
            for f in files:
                if f.lower().endswith(".gguf"):
                    yield Path(root) / f
    except OSError:
        return


def _total_size(p):
    m = re.search(r"-00001-of-(\d{5})\.gguf$", p.name)
    if not m:
        return p.stat().st_size
    return sum(f.stat().st_size for f in p.parent.glob(p.name.replace("00001-of", "*-of")))


# ------------------------------------------------------------------ fit + settings
def fit(model, hw):
    need = model["size_gb"] + 2.5  # + context cache + overhead
    if hw["vram_gb"] and need <= hw["vram_gb"] - 0.5:
        return "fast", "fits fully on your GPU"
    if need <= hw["vram_gb"] + hw["ram_gb"] * 0.8:
        return "ok", "GPU + RAM" + (" (MoE: runs well)" if model.get("experts") else " (dense: slower)")
    return "too big", "needs more memory than this PC has"


def server_params(model, hw, cfg):
    """llama-server flags tuned for this model on this PC."""
    ctx = min(cfg["ctx"], model.get("ctx_train") or cfg["ctx"])
    p = {"ctx": ctx, "threads": cfg.get("threads") or default_threads(hw), "cache": cfg["cache_type"]}
    layers = model.get("layers") or 99
    vram = max(0.0, hw["vram_gb"] - 1.5)
    if model.get("experts"):
        p["ngl"] = 99
        if cfg.get("cpu_moe") is not None:
            p["cpu_moe"] = cfg["cpu_moe"]
        else:
            p["cpu_moe"] = 0 if model["size_gb"] < vram else layers  # all experts in RAM is always safe
    else:
        if cfg.get("ngl") is not None:
            p["ngl"] = cfg["ngl"]
        elif not hw["vram_gb"]:
            p["ngl"] = 0
        else:
            p["ngl"] = 99 if model["size_gb"] < vram else max(0, int(layers * vram / model["size_gb"]))
    return p


def current_model(cfg=None):
    cfg = cfg or config.load()
    if not cfg.get("model_path") or not Path(cfg["model_path"]).exists():
        return None
    p = Path(cfg["model_path"])
    meta = gguf_meta(p) or {}
    q = QUANT_RE.search(p.name)
    return {"path": str(p), "file": p.name, "name": cfg.get("model_name") or p.stem,
            "size_gb": round(_total_size(p) / 2**30, 1), "quant": q.group(1).upper() if q else "?", **meta}
