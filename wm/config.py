"""Paths and persistent settings (~/.windmill/config.json)."""
import json
import os
import subprocess
from pathlib import Path

KIT = Path(__file__).resolve().parent.parent           # the windmill install folder
SCRIPTS = KIT / "scripts"
HOME = Path(os.environ.get("WINDMILL_HOME", Path.home() / ".windmill"))
CONFIG = HOME / "config.json"
LOGS = HOME / "logs"

DEFAULTS = {
    "model_path": None,          # chosen .gguf
    "model_name": None,
    "llama_server": None,        # path to llama-server binary
    "port": 8080,
    "ctx": 65536,
    "cpu_moe": None,             # tuned value; None = auto
    "ngl": None,                 # dense models: layers on GPU; None = auto
    "cache_type": "q8_0",
    "threads": None,
    "model_dirs": [],            # extra folders to search for models
    "auto_start_server": True,
    "show_thinking": False,
    "permission": "auto",        # auto | ask  (ask = confirm every command)
    "engine": "windmill",        # windmill | aider (pipeline edit engine)
    "max_steps": 80,             # agent tool calls per message before it pauses
    "auto_update": "auto",       # auto (fixes+minor install themselves, major asks) | ask | off
    "update_check_hours": 24,
    "update_source": None,       # git URL the updates come from (set automatically by the publisher)
    "last_update_check": 0,
    "library_refresh_days": 30,  # re-discover + download new public projects in the background (0 = off)
    "last_library_refresh": 0,
    "auto_fix": True,
    "app_setup_done": False,
    "web_access": True,          # the AI may search the web and open links (web_search / fetch_url)
    "recent_projects": [],              # folders opened in the app, newest first            # find/repair/install llama.cpp and re-find moved models automatically
}


def load():
    HOME.mkdir(parents=True, exist_ok=True)
    cfg = dict(DEFAULTS)
    if CONFIG.exists():
        try:
            cfg.update(json.loads(CONFIG.read_text(encoding="utf-8")))
        except ValueError:
            pass
    return cfg


def save(cfg):
    HOME.mkdir(parents=True, exist_ok=True)
    CONFIG.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


def set_value(key, value):
    cfg = load()
    cfg[key] = value
    save(cfg)
    return cfg


def api_base(cfg=None):
    cfg = cfg or load()
    return os.environ.get("AI_KIT_API_BASE", f"http://127.0.0.1:{cfg['port']}/v1")


def project_root(start=None):
    """Git root if inside a repo, else the current folder."""
    start = Path(start or Path.cwd())
    r = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=start, capture_output=True, text=True)
    return Path(r.stdout.strip()) if r.returncode == 0 else start.resolve()


def is_project(root):
    markers = ("Cargo.toml", "package.json", "pyproject.toml", "requirements.txt", "go.mod",
               "index.html", "BRIEF.md", ".git")
    return any((Path(root) / m).exists() for m in markers)
