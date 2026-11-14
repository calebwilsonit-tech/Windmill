"""Paths and persistent settings (~/.windmill/config.json)."""
import json
import time
import threading
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
    "chat_model_path": None,     # optional second model for everyday chats (writing, questions); None = use model_path
    "chat_model_name": None,
    "unload_after_minutes": 30,  # free the model's memory after this long unused (0 = keep it loaded)
    "recent_projects": [],              # folders opened in the app, newest first            # find/repair/install llama.cpp and re-find moved models automatically
}


_LOCK = threading.RLock()


def _read():
    """The saved settings, or None if the file can't be read right now (never mistake that for 'no settings')."""
    for attempt in range(20):
        try:
            return json.loads(CONFIG.read_text(encoding="utf-8")) if CONFIG.exists() else {}
        except (ValueError, OSError):
            time.sleep(0.05)  # another process is replacing it this instant - read again
    return None


def load():
    HOME.mkdir(parents=True, exist_ok=True)
    cfg = dict(DEFAULTS)
    saved = _read()
    if saved:
        cfg.update(saved)
    return cfg


def save(cfg):
    """Write all-or-nothing: a temporary file that replaces the old one in one step, so nothing ever reads half a file."""
    with _LOCK:
        HOME.mkdir(parents=True, exist_ok=True)
        tmp = CONFIG.with_name(f"{CONFIG.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        tmp.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
        for attempt in range(20):
            try:
                os.replace(tmp, CONFIG)
                return
            except PermissionError:  # Windows: someone has it open this instant
                time.sleep(0.05)
        os.replace(tmp, CONFIG)


def set_value(key, value):
    """Change one setting, based on the latest saved settings (others' changes are kept)."""
    with _LOCK:
        saved = _read()
        if saved is None:  # couldn't read the file: don't overwrite good settings with defaults
            raise OSError("Windmill's settings file is busy - try again")
        cfg = dict(DEFAULTS)
        cfg.update(saved)
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
