"""One-command setup and health check, written for people who've never used a terminal."""
import json
import os
import platform
import shutil
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path

from . import config, models, server, ui

WINGET = {"git": "Git.Git", "rust": "Rustlang.Rustup", "node": "OpenJS.NodeJS.LTS", "python": "Python.Python.3.12"}
MODEL_SEARCHES = ["Qwen3.6 GGUF", "Qwen3-Coder GGUF", "Qwen3 30B A3B GGUF", "gpt-oss GGUF", "GLM GGUF", "Devstral GGUF"]


def have(cmd):
    return shutil.which(cmd) is not None


def winget_install(key):
    if os.name != "nt" or not have("winget"):
        return False
    ui.info(f"Installing {key} (Windows may ask for permission)...")
    r = subprocess.run(["winget", "install", "-e", "--id", WINGET[key], "--accept-source-agreements",
                        "--accept-package-agreements"])
    return r.returncode == 0


def step_tools():
    ui.rule("1. Basic tools")
    if have("git"):
        ui.ok("Git")
    else:
        ui.warn("Git is missing (Windmill uses it to save checkpoints so nothing is ever lost).")
        if not (ui.confirm("Install Git now?") and winget_install("git")):
            ui.err("Please install Git from https://git-scm.com/downloads and run  windmill setup  again.")
            return False
        ui.warn("Close and reopen this terminal after the install, then run  windmill setup  again.")
        return False
    if have("cargo"):
        ui.ok("Rust (for games, apps and tools)")
    else:
        ui.info("Rust isn't installed. It's needed for games, desktop apps and command-line tools (not for websites).")
        if ui.confirm("Install Rust?", True):
            if os.name == "nt":
                if winget_install("rust"):
                    ui.ok("Rust installed. After setup, reopen the terminal once so it's found.")
            else:
                ui.info("Run this in a terminal, then re-run setup:\n    curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh")
    if have("node") or have("node.exe"):
        ui.ok("Node.js (optional, for some web projects)")
    return True


def step_llama(update=False):
    ui.rule("2. AI engine (llama.cpp)")
    cfg = config.load()
    b = server.find_binary(cfg)
    if b and not update:
        ui.ok(f"Found {b}")
        return True
    try:
        b = server.install_llamacpp()
    except Exception as e:  # noqa: BLE001
        ui.err(f"Download failed: {e}")
        b = None
    if not b:
        ui.info("Manual option: download the right zip from github.com/ggml-org/llama.cpp/releases, unzip it into\n"
                f"    {config.KIT / 'llama.cpp'}\n  then run  windmill setup  again.")
        return False
    return True


def hf_get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "windmill"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def suggest_downloads(hw):
    """Search Hugging Face for GGUF files that fit this PC's memory."""
    budget = hw["vram_gb"] + hw["ram_gb"] * 0.7 - 3
    out, seen = [], set()
    for q in MODEL_SEARCHES:
        try:
            repos = hf_get("https://huggingface.co/api/models?" + urllib.parse.urlencode(
                {"search": q, "sort": "downloads", "limit": 4, "filter": "gguf"}))
        except Exception:  # noqa: BLE001
            continue
        for repo in repos:
            rid = repo["id"]
            if rid in seen:
                continue
            seen.add(rid)
            try:
                files = hf_get(f"https://huggingface.co/api/models/{rid}/tree/main")
            except Exception:  # noqa: BLE001
                continue
            ggufs = [f for f in files if f.get("path", "").endswith(".gguf") and "mmproj" not in f["path"].lower()
                     and f.get("size")]
            for pref in ("Q5_K_M", "Q4_K_M", "Q6_K", "Q4_K_XL", "Q5_K_XL", "MXFP4"):
                fit = [f for f in ggufs if pref in f["path"].upper().replace("-", "_") and f["size"] / 2**30 < budget]
                if fit:
                    f = fit[0]
                    out.append({"repo": rid, "file": f["path"], "gb": round(f["size"] / 2**30, 1)})
                    break
        if len(out) >= 8:
            break
    out.sort(key=lambda x: -x["gb"])
    return out


def step_model():
    ui.rule("3. AI model")
    cfg = config.load()
    found = models.discover(cfg)
    hw = models.hardware()
    if found:
        ui.ok(f"Found {len(found)} model(s) on this computer.")
        if not cfg.get("model_path") or not Path(cfg["model_path"]).exists():
            choose_model(found, hw)
        else:
            ui.ok(f"Using {cfg.get('model_name')}")
            if ui.confirm("Choose a different one?", False):
                choose_model(found, hw)
        return True
    ui.info("No AI models found yet. Here are good ones that fit your computer:")
    from . import recommend
    recommend.menu()
    if models.current_model(config.load()):
        return True
    with ui.Spinner("searching more widely"):
        sugg = suggest_downloads(hw)
    if not sugg:
        ui.err("Couldn't reach Hugging Face. Download a .gguf model manually into "
               f"{config.KIT / 'models'} and run setup again.")
        return False
    opts = [(i, f"{s['repo']}  {ui.dim(s['file'])}", f"{s['gb']} GB") for i, s in enumerate(sugg)]
    k = ui.menu("Pick a model to download (bigger = smarter but slower)", opts)
    if k is None:
        return False
    s = sugg[k]
    dest = config.KIT / "models" / Path(s["file"]).name
    url = f"https://huggingface.co/{s['repo']}/resolve/main/{urllib.parse.quote(s['file'])}"
    ui.info(f"Downloading {s['gb']} GB - this can take a while. You can leave it running.")
    server.download(url, dest)
    found = models.discover(cfg)
    m = next((x for x in found if Path(x["path"]).name == dest.name), None)
    if m:
        select(m)
    return True


def choose_model(found, hw):
    opts = []
    try:
        from .bench import best_scores
        scores = best_scores()
    except Exception:  # noqa: BLE001
        scores = {}
    for i, m in enumerate(found):
        tag, why = models.fit(m, hw)
        if m["name"] in scores:
            s, n = scores[m["name"]]
            why += ui.dim(f"  benchmark {s}/{n}")
        col = ui.green if tag == "fast" else ui.yellow if tag == "ok" else ui.red
        moe = f"MoE {m['experts']}x" if m.get("experts") else "dense"
        opts.append((i, f"{m['name'][:44]:44} {m['size_gb']:5.1f} GB {m['quant']:7} {moe:9}",
                     col(why) + ui.dim(f"  [{m['source']}]")))
    k = ui.menu("Your models", opts)
    if k is not None:
        select(found[k])
        return found[k]
    return None


def select(m):
    cfg = config.load()
    changed = cfg.get("model_path") != m["path"]
    cfg.update({"model_path": m["path"], "model_name": m["name"], "cpu_moe": None if changed else cfg.get("cpu_moe"),
                "ngl": None if changed else cfg.get("ngl")})
    if m.get("ctx_train"):
        cfg["ctx"] = min(cfg.get("ctx", 65536), m["ctx_train"])
    config.save(cfg)
    ui.ok(f"Selected {m['name']}.")
    if changed and server.health(cfg):
        server.stop(quiet=True)


def step_tune():
    hw = models.hardware()
    m = models.current_model()
    if hw["vram_gb"] and m and ui.confirm(f"Tune the model for your {hw['gpu']}? (takes a few minutes, makes it faster)", True):
        server.tune()


def step_library():
    ui.rule("4. Reference library")
    db = config.KIT / "corpus" / "corpus.db"
    if db.exists():
        ui.ok(f"Library ready ({db.stat().st_size / 2**30:.1f} GB index).")
        return
    ui.info("The library is thousands of real, well-built projects (websites, apps, games, UI design systems,\n"
            "  docs) that Windmill looks things up in while it works. It's big (several GB) but makes the AI much better.")
    choice = ui.menu("Download the library?", [
        ("all", "Everything", "recommended if you have the disk space"),
        ("web", "Websites, apps and UI design", "web-frontend,web-backend,ui-design,mobile,learning"),
        ("game", "Games and UI design", "game,ui-design,graphics,learning"),
        ("android", "Android apps & games + UI design", "android,game,gui,ui-design,learning"),
        ("skip", "Not now", "you can run  windmill library fetch  later")], allow_back=False)
    if choice == "skip":
        return
    cats = {"all": None, "web": "web-frontend,web-backend,ui-design,mobile,learning,db",
            "game": "game,ui-design,graphics,physics,learning",
            "android": "android,game,gui,ui-design,graphics,mobile,learning,core"}[choice]
    cmd = [sys.executable, str(config.SCRIPTS / "ai_corpus.py"), "fetch"] + (["--cats", cats] if cats else [])
    subprocess.run(cmd)
    subprocess.run([sys.executable, str(config.SCRIPTS / "ai_corpus.py"), "index"])


def step_android():
    ui.rule("Android (optional)")
    from . import android
    if all(r[1] for r in android.status()):
        ui.ok("Android toolchain ready - Windmill can build .apk files.")
        return
    ui.info("To make real Android apps (.apk) Windmill needs Java, the Android SDK/NDK and two Rust tools.\n"
            "  It installs them for you (about 3-4 GB). You can also do this later:  windmill android setup")
    if have("cargo") and ui.confirm("Set up Android app building now?", False):
        android.setup()


def step_test():
    ui.rule("5. Test drive")
    cfg = config.load()
    if not server.start(cfg):
        return False
    from .llm import LLM
    try:
        with ui.Spinner("asking the model to say hello"):
            reply = LLM(cfg).simple("You are a friendly assistant.", "Say hello in one short sentence.", max_tokens=200)
        ui.ok(f"The model says: {reply.strip()[:200]}")
        return True
    except Exception as e:  # noqa: BLE001
        ui.err(f"Test failed: {e}")
        return False


def run_setup(update_llama=False):
    ui.logo("setup")
    hw = models.hardware()
    ui.rule("Your computer")
    print(f"  {platform.system()} {platform.release()} | {hw['cpus']} CPU threads | {hw['ram_gb']:.0f} GB RAM | "
          + (f"{hw['gpu']} ({hw['vram_gb']:.0f} GB)" if hw["gpu"] else "no NVIDIA GPU (CPU mode)"))
    if not step_tools():
        return
    if not step_llama(update_llama):
        return
    if not step_model():
        return
    step_tune()
    step_library()
    step_android()
    if step_test():
        ui.rule()
        ui.ok(ui.bold("Windmill is ready."))
        print(f"  Start it any time by typing  {ui.bcyan('windmill')}")
        print(f"  Make something new:          {ui.bcyan('windmill new')}")


def _android_ok():
    from . import android
    try:
        return all(r[1] for r in android.status())
    except Exception:  # noqa: BLE001
        return False


def doctor():
    ui.rule("Windmill health check")
    cfg = config.load()
    hw = models.hardware()
    checks = [
        ("Python", sys.version.split()[0], sys.version_info >= (3, 9)),
        ("Git", "installed" if have("git") else "missing - run windmill setup", have("git")),
        ("Rust", "installed" if have("cargo") else "not installed (only needed for games/apps/tools)", True),
        ("GPU", f"{hw['gpu']} {hw['vram_gb']:.1f} GB" if hw["gpu"] else "none detected (CPU mode)", True),
        ("RAM", f"{hw['ram_gb']:.0f} GB", hw["ram_gb"] >= 8),
        ("llama.cpp", server.find_binary(cfg) or "missing - run windmill setup", bool(server.find_binary(cfg))),
        ("Model", cfg.get("model_name") or "none selected - run windmill models", bool(models.current_model(cfg))),
        ("Model server", "running" if server.health(cfg) else "stopped (starts automatically when needed)", True),
        ("Library", "ready" if (config.KIT / "corpus" / "corpus.db").exists() else "not downloaded (windmill library fetch)", True),
        ("Android", "ready (.apk builds)" if _android_ok() else "not set up (optional: windmill android setup)", True),
    ]
    for name, val, ok in checks:
        print(f"  {ui.green('✓') if ok else ui.red('✗')} {name:13} {val}")
    m = models.current_model(cfg)
    if m:
        tag, why = models.fit(m, hw)
        print(f"  {ui.dim('model fit:')} {why}")
        print(f"  {ui.dim('server settings:')} {models.server_params(m, hw, cfg)}")
