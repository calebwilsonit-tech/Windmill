"""Recommend downloadable AI models that fit THIS PC, refreshed from Hugging Face in the background.

As new models come out (and the community quantizes them to run on smaller PCs), they show up here
automatically: only from trusted quantizers/makers, text/code models only, scored by popularity,
momentum, recency, coding ability and how well they'll run on your hardware. Never a pop-up - just a quiet
line on the home screen and the "Get a new AI model" menu."""
import json
import math
import re
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from . import config, models, ui

CACHE = config.HOME / "model-recs.json"
API = "https://huggingface.co/api/models"
# People/orgs that publish reliable GGUF conversions, or make the models themselves (lower = preferred)
TRUSTED = {"unsloth": 0, "bartowski": 1, "lmstudio-community": 2, "ggml-org": 3, "qwen": 4, "mistralai": 5,
           "google": 6, "microsoft": 7, "ibm-granite": 8, "openai": 9, "nvidia": 10, "deepseek-ai": 11,
           "zai-org": 12, "allenai": 13, "mradermacher": 14}
NOISE = re.compile(r"(abliterat|heretic|uncensor|nsfw|erotic|roleplay|\brp\b|merge|frankenmerge|"
                   r"tts|asr|whisper|audio|speech|voice|music|video|image|diffusion|flux|vision-only|embed|rerank|"
                   r"ocr|clip|sdxl|wan2|yue|zonos|test|tiny-random|draft)", re.I)
TEXT = re.compile(r"(qwen|gemma|llama|mistral|devstral|codestral|magistral|glm|gpt-oss|deepseek|phi|granite|"
                  r"nemotron|olmo|kimi|seed|minimax|hunyuan|ernie|smol|ministral|exaone|falcon|command)", re.I)
CODER = re.compile(r"(coder|code|devstral|codestral)", re.I)
MOE = re.compile(r"(\bA\d+(\.\d+)?B\b|-a\d+b|moe|mixtral|gpt-oss|flash-next|next)", re.I)
# Best balance first: Q5_K_M ~ near-lossless; Q4 leaves room for context; Q6 only if nothing else exists
QUANT_PREF = ["Q5_K_M", "UD-Q4_K_XL", "Q4_K_M", "Q4_K_XL", "MXFP4", "Q6_K", "Q4_K_S", "IQ4_XS", "Q3_K_M", "UD-Q3_K_XL"]
DENSE_PREF = ["Q4_K_M", "UD-Q4_K_XL", "Q4_K_XL", "IQ4_XS", "Q5_K_M", "Q3_K_M"]  # dense + small GPU: smaller = faster


def _get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "windmill-model-recommender"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def _base_name(repo_id):
    name = repo_id.split("/", 1)[1]
    return re.sub(r"[-_.]?(gguf|GGUF)$", "", name).replace("_", "-")


def _family(repo_id):
    """Same model, different packaging (MTP heads, -Instruct/-it suffixes) counts once."""
    return re.sub(r"-(mtp|instruct|it|chat|hf)\b", "", _base_name(repo_id).lower())


def candidates():
    expand = "&".join(f"expand%5B%5D={k}" for k in ("downloads", "likes", "lastModified", "trendingScore", "author"))
    seen, out = set(), []
    for sort in ("trendingScore", "downloads", "likes"):
        for m in _get(f"{API}?filter=gguf&sort={sort}&direction=-1&limit=100&{expand}"):
            rid = m["id"]
            author = rid.split("/")[0].lower()
            if rid in seen or author not in TRUSTED or NOISE.search(rid) or not TEXT.search(rid):
                continue
            seen.add(rid)
            out.append(m)
    return out


def score(m, hw):
    days = 999
    try:
        days = (datetime.now(timezone.utc) - datetime.fromisoformat(m["lastModified"].replace("Z", "+00:00"))).days
    except (KeyError, ValueError):
        pass
    s = math.log10(1 + m.get("downloads", 0)) + 1.5 * math.log10(1 + m.get("likes", 0)) + (m.get("trendingScore") or 0) / 40
    s += 1.5 if days < 45 else 0.7 if days < 120 else 0
    s += 2.0 if CODER.search(m["id"]) else 0
    if MOE.search(m["id"]) and hw["vram_gb"] < 16:
        s += 1.0  # MoE models run well on gaming laptops (most of the model lives in RAM)
    s -= TRUSTED[m["id"].split("/")[0].lower()] * 0.05
    return s, days


def pick_file(rid, budget, prefs=QUANT_PREF):
    """Best quant of this repo that fits the memory budget."""
    files = _get(f"{API}/{rid}/tree/main")
    groups = {}
    for f in files:
        p = f.get("path", "")
        if not p.lower().endswith(".gguf") or "mmproj" in p.lower() or not f.get("size"):
            continue
        key = re.sub(r"-0000\d-of-0000\d\.gguf$", ".gguf", p)
        g = groups.setdefault(key, {"path": p, "gb": 0.0})
        g["gb"] += f["size"] / 2**30
        if "-00001-of-" in p:
            g["path"] = p
    for q in prefs:
        fits = [g for k, g in groups.items() if q.lower() in k.lower().replace("-", "_").replace("ud_", "ud-")
                or q.lower() in k.lower()]
        fits = [g for g in fits if g["gb"] <= budget]
        if fits:
            g = max(fits, key=lambda g: g["gb"])
            return {"file": g["path"], "gb": round(g["gb"], 1), "quant": q}
    return None


def refresh(limit=8):
    hw = models.hardware()
    budget = hw["vram_gb"] + hw["ram_gb"] * 0.75 - 3
    ranked = sorted(((score(m, hw), m) for m in candidates()), key=lambda x: -x[0][0])
    picked, bases = [], set()
    for (s, days), m in ranked:
        base = _family(m["id"])
        if base in bases:
            continue
        moe = bool(MOE.search(m["id"]))
        try:
            f = pick_file(m["id"], budget, QUANT_PREF if moe else DENSE_PREF)
        except Exception:  # noqa: BLE001
            f = None
        if not f:
            continue
        on_gpu = f["gb"] + 2 < hw["vram_gb"]
        speed = "fast" if on_gpu else "good speed" if moe else "slow on this PC (dense model bigger than the GPU)"
        if not on_gpu and not moe:
            s -= 2.0  # still listed (quality!), just below models that run well here
        picked.append((s, days, m, f, moe, on_gpu, speed))
        bases.add(base)
        if len(picked) >= limit * 2:
            break
    recs = []
    for s, days, m, f, moe, on_gpu, speed in sorted(picked, key=lambda x: -x[0]):
        why = []
        if CODER.search(m["id"]):
            why.append("built for coding")
        if days < 45:
            why.append("new")
        if (m.get("trendingScore") or 0) > 50:
            why.append("trending")
        recs.append({"repo": m["id"], "name": _base_name(m["id"]), **f, "downloads": m.get("downloads", 0),
                     "why": ", ".join(why) or "popular", "speed": speed, "moe": moe,
                     "fit": "GPU" if on_gpu else "GPU + RAM"})
        if len(recs) >= limit:
            break
    data = {"at": time.time(), "budget_gb": round(budget, 1), "recs": recs}
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(data, indent=1), encoding="utf-8")
    return data


def cached():
    try:
        return json.loads(CACHE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def refresh_in_background(max_age_h=24):
    """Never blocks the menu; results show next time."""
    c = cached()
    if c and time.time() - c.get("at", 0) < max_age_h * 3600:
        return
    threading.Thread(target=lambda: _quiet(refresh), daemon=True).start()


def _quiet(fn):
    try:
        fn()
    except Exception:  # noqa: BLE001 - offline etc.: just try again next time
        pass


NOISE_TOKENS = {"instruct", "it", "chat", "gguf", "hf", "mtp", "latest", "base", "ud", "q4", "q5", "q6", "q8", "k", "m",
                "s", "l", "xl", "xs", "km", "ks", "f16", "bf16", "mxfp4", "iq4", "q3", "a3b", "a4b", "a22b"}


def _tokens(name):
    t = re.findall(r"[a-z]+\d*(?:\.\d+)?|\d+(?:\.\d+)?b?", Path(name).name.lower().replace(".gguf", ""))
    return {x for x in t if x not in NOISE_TOKENS and not re.fullmatch(r"q\d.*|\d{5}|of", x)}


def same_model(a, b):
    """'qwen3-coder:30b' (Ollama) == 'Qwen3-Coder-30B-A3B-Instruct' (Hugging Face) == '...-Q5_K_M.gguf'."""
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return False
    sizes_a, sizes_b = {x for x in ta if re.fullmatch(r"\d+(\.\d+)?b", x)}, {x for x in tb if re.fullmatch(r"\d+(\.\d+)?b", x)}
    if sizes_a and sizes_b and not sizes_a & sizes_b:
        return False
    small, big = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    return small <= big


def installed_names():
    names = set()
    for m in models.discover():
        names.add(Path(m["path"]).name.lower())
        names.add(str(m.get("name", "")).lower())
    return names


def is_installed(rec, have):
    return any(same_model(rec["name"], h) or same_model(Path(rec["file"]).name, h) for h in have if h)


def home_line():
    """One quiet line for the home screen (or None)."""
    c = cached()
    if not c or not c.get("recs"):
        return None
    have = installed_names()
    for r in c["recs"]:
        if not is_installed(r, have):
            if not r.get("speed", "").startswith(("fast", "good")):
                continue  # only nudge toward models that will actually run well here
            return f"new AI model you could try: {r['name']} ({r['gb']} GB, {r['why']}) - see \"Get a new AI model\""
    return None


def menu():
    """Browse and download recommendations (only when the user asks)."""
    c = cached()
    if not c or time.time() - c.get("at", 0) > 24 * 3600:
        with ui.Spinner("checking Hugging Face for the best models for your PC"):
            try:
                c = refresh()
            except Exception as e:  # noqa: BLE001
                if not c:
                    ui.err(f"Couldn't reach Hugging Face ({e}).")
                    return
    have = installed_names()
    hw = models.hardware()
    print(ui.dim(f"  Your PC can run models up to about {c['budget_gb']} GB "
                 f"({hw['ram_gb']:.0f} GB RAM" + (f" + {hw['vram_gb']:.0f} GB GPU" if hw["vram_gb"] else "") + ")."))
    opts = []
    for i, r in enumerate(c["recs"]):
        tag = ui.green(" installed") if is_installed(r, have) else ""
        col = ui.green if r.get("speed", "").startswith(("fast", "good")) else ui.yellow
        opts.append((i, f"{r['name'][:38]:38} {r['gb']:5.1f} GB {r['quant']:10}",
                     f"{col(r.get('speed', ''))}  {r['why']}  [{r['repo'].split('/')[0]}]{tag}"))
    k = ui.menu("Get a new AI model", opts)
    if k is None:
        return
    r = c["recs"][k]
    if not ui.confirm(f"Download {r['name']} ({r['gb']} GB)? It goes in the windmill/models folder.", True):
        return
    from .server import download
    files = [r["file"]]
    m = re.search(r"-00001-of-(\d{5})\.gguf$", r["file"])
    if m:
        files = [r["file"].replace("-00001-of-", f"-{i:05d}-of-") for i in range(1, int(m.group(1)) + 1)]
    for f in files:
        url = f"https://huggingface.co/{r['repo']}/resolve/main/{urllib.parse.quote(f)}"
        download(url, config.KIT / "models" / Path(f).name)
    found = [x for x in models.discover() if Path(x["path"]).name == Path(files[0]).name]
    if found:
        from .setup import select
        select(found[0])
        ui.info("Tip: run  windmill benchmark  to see how it compares with your previous model.")
