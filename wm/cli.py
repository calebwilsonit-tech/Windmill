"""`windmill` command: main menu + every feature as a subcommand."""
import json
import subprocess
import sys
from pathlib import Path

from . import checks, config, models, server, sessions, ui

COMMANDS = [
    ("chat", "[message]", "talk to Windmill and build in the current folder (like Claude Code)"),
    ("new", "", "start a new project from an idea - guided, no tech knowledge needed"),
    ("build", "", "keep building the current project's roadmap on autopilot"),
    ("run", "", "run / open your app"),
    ("debug", "[problem]", "find and fix a problem (reproduce, root cause, fix, test)"),
    ("check", "", "check the project: build, tests, sloppy-code guard"),
    ("plan", "<task>", "big task: plan -> verified steps -> review -> mutation testing"),
    ("best", "<task> [-n N]", "hard task: N independent attempts, keep the best"),
    ("benchmark", "[--tasks 01,03] [--modes plain,windmill] [--api URL --api-model M --api-key-env VAR]",
     "measure the AI on 10 coding tasks with hidden tests"),
    ("history", "[words]", "list / search past chats"),
    ("resume", "[n | words]", "continue a past chat (default: last one in this folder)"),
    ("design", "", "pick or change the project's look & feel"),
    ("android", "setup|status|init|build|install|logs", "native Android apps (.apk) from Rust"),
    ("apk", "[--debug]", "build the Android app (.apk) for this project"),
    ("models", "", "find the AI models on this computer and choose one"),
    ("get-model", "", "recommended new models for your PC (from Hugging Face, refreshed daily)"),
    ("plugins", "catalog [service]|list|add|remove|enable|disable|test|import", "connect apps & services (MCP plugins)"),
    ("connect", "[service]", "browse connectors: browser testing, Blender, Ludo, GitHub, Gmail, Notion..."),
    ("skills", "", "list the skills the AI can use"),
    ("git", "status|sync|pull|push|commit|connect <repo>|clone <repo>", "GitHub: save, get and send changes, change the repo"),
    ("app", "[--install]", "open the Windmill desktop app (--install: create the app + shortcuts)"),
    ("server", "start|stop|status|logs", "control the model server (normally automatic)"),
    ("tune", "", "make the model use your GPU as much as safely possible"),
    ("library", "fetch|index|add|search|stats|pack", "the reference library"),
    ("deps", "", "index this project's exact dependency versions"),
    ("map / find / show / refs / related / context", "...", "look things up in the project index"),
    ("init", "", "set up Windmill in an existing project folder"),
    ("settings", "[key value]", "view or change settings"),
    ("update", "[--rollback | --source URL]", "update Windmill now (it also updates itself daily)"),
    ("version", "", "show version and update settings"),
    ("dev", "setup | publish <git-url>", "make this folder the release source (asks Major/Minor/Fix per commit)"),
    ("release", "[major|minor|fix]", "release manually (normally automatic after each commit)"),
    ("setup", "[--update-llama]", "install / repair everything (run this first)"),
    ("doctor", "", "health check"),
    ("fix", "", "find and repair everything automatically (llama.cpp, moved models...)"),
]


def status_line():
    cfg = config.load()
    m = cfg.get("model_name")
    up = server.health(cfg, timeout=0.5)
    root = config.project_root()
    kinds = ", ".join(checks.kinds(root))
    lib = config.KIT / "corpus" / "corpus.db"
    print(f"  {ui.dim('model')}   {m or ui.yellow('none - choose one in Models')}  "
          f"{ui.green('● ready') if up else ui.dim('○ starts when needed')}")
    print(f"  {ui.dim('folder')}  {root}  {ui.dim('(' + kinds + ')') if kinds else ui.dim('(no project here)')}")
    print(f"  {ui.dim('library')} {'ready' if lib.exists() else ui.dim('not downloaded')}")
    from . import version as _v
    print(f"  {ui.dim('version')} {_v.current()}")
    try:
        from . import recommend
        recommend.refresh_in_background()  # quiet, at most daily; shows up next time
        line = recommend.home_line()
        if line:
            print(f"  {ui.dim('tip')}     {ui.dim(line)}")
    except Exception:  # noqa: BLE001
        pass
    print()


def main_menu():
    cfg = config.load()
    if cfg.get("auto_fix", True) and not (cfg.get("llama_server") and Path(cfg["llama_server"]).is_file()):
        from . import heal
        heal.ensure_llama(cfg)  # repair before showing the menu, so everything just works
    while True:
        ui.logo()
        status_line()
        root = config.project_root()
        has_roadmap = (root / "ROADMAP.md").exists()
        recent = sessions.recent(4)
        opts = [("chat", "Chat & build in this folder", "like Claude Code"),
                ("new", "Start a new project", "describe your idea - Windmill does the rest")]
        if has_roadmap:
            opts.append(("build", "Continue building this project", "autopilot through the roadmap"))
        if (root / "android" / "app").is_dir():
            opts.append(("android-menu", "Android: build & put on my phone", ".apk"))
        opts += [("resume", "Recent chats", ", ".join(r["title"][:22] for r in recent[:2]) if recent else ""),
                 ("debug", "Fix a problem", "debugger: reproduce -> root cause -> fix"),
                 ("run", "Run my app", ""),
                 ("design", "Look & feel", "unique design for this project"),
                 ("models", "AI models", "switch between the models you have"),
                 ("get-model", "Get a new AI model", "recommended for your PC, updated daily"),
                 ("benchmark", "Test how good the AI is", "10 coding tasks, hidden tests"),
                 ("library", "Reference library", "search or download"),
                 ("setup", "Setup & health check", ""),
                 ("help", "All commands", "")]
        k = ui.menu("Main menu", opts)
        if k is None:
            server_stop_prompt()
            return
        dispatch([k])
        input(ui.dim("\n  Press Enter to return to the menu..."))


def server_stop_prompt():
    cfg = config.load()
    if server.health(cfg, timeout=0.5) and ui.confirm("Stop the AI model to free up memory?", True):
        server.stop()


def models_menu():
    from .setup import choose_model
    cfg = config.load()
    with ui.Spinner("searching this computer for AI models"):
        found = models.discover(cfg)
        hw = models.hardware()
    print(f"  {ui.dim('this PC:')} {hw['ram_gb']:.0f} GB RAM" + (f", {hw['gpu']} {hw['vram_gb']:.0f} GB" if hw['gpu'] else ", no NVIDIA GPU"))
    if not found:
        ui.warn("No models found. Searched: " + ", ".join(str(d) for d in models.search_dirs(cfg)) + ", Ollama")
        if ui.confirm("Download one now?", True):
            from . import recommend
            recommend.menu()
        else:
            ui.info("Or add a folder to search:  windmill settings model_dirs \"D:\\\\my models\"")
        return
    choose_model(found, hw)


def chat(args):
    from .repl import Repl
    root = config.project_root()
    r = Repl(root)
    if args:
        if not server.ensure(r.cfg):
            return
        r.start_agent()
        r.agent.send(" ".join(args))
        print()
    r.run()


def resume(args):
    from .repl import Repl
    from .sessions import Session
    root = config.project_root()
    q = " ".join(args)
    if not q and sys.stdin.isatty():
        rows = sessions.recent(15)
        if not rows:
            ui.info("No saved chats yet.")
            return
        ui.rule("Recent chats")
        from .repl import list_chats
        list_chats()
        q = ui.ask("Number to open (or words to search)", "1")
    sid = sessions.resolve(q, project=root)
    if not sid:
        ui.warn("No matching chat.")
        return
    s = Session.load(sid)
    proj = Path(s.project)
    if proj.exists() and proj.resolve() != root.resolve():
        ui.info(f"That chat belongs to {proj} - switching there.")
        root = proj
    Repl(root, session=s).run()


def history(args):
    from .repl import list_chats
    show_all = "--all" in args
    args = [a for a in args if a != "--all"]
    ui.rule("Chats" + (f" matching '{' '.join(args)}'" if args else ""))
    list_chats(query=" ".join(args) or None, limit=30, include_auto=show_all)
    print(ui.dim("\n  Open one:  windmill resume <number>   or   windmill resume <words>"))


def script(name, args, cwd=None):
    return subprocess.run([sys.executable, str(config.SCRIPTS / name), *args], cwd=cwd or config.project_root()).returncode


def settings(args):
    cfg = config.load()
    if len(args) >= 2:
        key, val = args[0], " ".join(args[1:])
        if key not in config.DEFAULTS:
            ui.err(f"Unknown setting {key}. Known: {', '.join(config.DEFAULTS)}")
            return
        try:
            v = json.loads(val)
        except ValueError:
            v = val
        if key == "model_dirs":
            v = list(dict.fromkeys(cfg.get("model_dirs", []) + ([v] if isinstance(v, str) else v)))
        config.set_value(key, v)
        ui.ok(f"{key} = {v}")
        return
    for k, v in cfg.items():
        print(f"  {ui.cyan(k.ljust(18))} {v}")
    print(ui.dim("\n  Change one:  windmill settings <key> <value>"))


def init_project():
    root = config.project_root()
    ui.info(f"Setting up Windmill in {root}")
    tpl = config.KIT / "project-template" / "AGENTS.md"
    if not (root / "AGENTS.md").exists() and tpl.exists() and (root / "Cargo.toml").exists():
        (root / "AGENTS.md").write_text(tpl.read_text(encoding="utf-8"), encoding="utf-8")
        ui.ok("Added AGENTS.md (project rules) - edit the [bracketed] parts.")
    if not (root / ".git").exists():
        subprocess.run(["git", "init", "-q"], cwd=root)
    script("ai_index.py", ["--init"], root)
    if (root / "Cargo.lock").exists() or (root / "Cargo.toml").exists():
        script("ai_corpus.py", ["deps"], root)
    from . import design
    if not design.load(root) and ui.confirm("Choose a look & feel for this project's UI?", True):
        from .project import design_menu
        design_menu(root)
    ui.ok("Ready. Run  windmill  in this folder.")


def android_cmd(args):
    from . import android
    root = config.project_root()
    sub, rest = args[0], args[1:]
    if sub == "menu":
        sub = ui.menu("Android", [("install", "Build and install on my phone", "USB cable + USB debugging"),
                                  ("build", "Just build the .apk file", "to share or upload"),
                                  ("logs", "Show the phone's crash log", "after something went wrong"),
                                  ("status", "Toolchain status", "")]) or "status"
    if sub == "setup":
        android.setup(root if (root / "android").is_dir() else None)
    elif sub == "status":
        for name, ok, detail in android.status():
            print(f"  {ui.green('✓') if ok else ui.red('✗')} {name:20} {detail}")
        print(ui.dim("  Phones connected: " + (", ".join(android.devices()) or "none")))
        if not all(r[1] for r in android.status()):
            print(ui.dim("  Install what's missing:  windmill android setup"))
    elif sub == "init":
        android_init(root)
    elif sub == "build":
        android.build_apk(root, release="--debug" not in rest)
    elif sub == "install":
        android.install(root, release="--debug" not in rest)
    elif sub == "logs":
        print(android.logs(root))
    else:
        ui.warn("Usage: windmill android setup|status|init|build|install|logs")


def android_init(root):
    """Add Android packaging to an existing Bevy or egui project."""
    import re as _re
    from . import android, design as _design
    toml = root / "Cargo.toml"
    if not toml.exists():
        return ui.err("This needs a Rust project (Cargo.toml). For a new one: windmill new")
    t = toml.read_text(encoding="utf-8")
    kind = "game" if _re.search(r"^bevy\s*=", t, _re.M) else "app" if _re.search(r"^eframe\s*=", t, _re.M) else None
    if not kind:
        return ui.err("Android packaging is supported for Bevy games and egui/eframe apps.")
    name = _re.search(r'^name\s*=\s*"([^"]+)"', t, _re.M).group(1)
    lib = name.replace("-", "_")
    if "[lib]" not in t:
        t = t.replace("[dependencies]", '[lib]\ncrate-type = ["lib", "cdylib"]\n\n[dependencies]', 1)
        toml.write_text(t, encoding="utf-8")
        ui.ok("Cargo.toml: added [lib] crate-type = [\"lib\", \"cdylib\"]")
    feat = "android-game-activity" if kind == "game" else "android-native-activity"
    if feat not in t:
        ui.warn(f"Add the '{feat}' feature to your {'bevy' if kind == 'game' else 'eframe'} dependency (Windmill's chat can do it).")
    b = root / ".windmill" / "design.json"
    pal = __import__("json").loads(b.read_text(encoding="utf-8"))["palette"] if b.exists() else {}
    app_id = "com.windmill." + _re.sub(r"[^a-z0-9]", "", name.lower())
    android.create_wrapper(root, name.replace("-", " ").replace("_", " ").title(), app_id, lib, kind=kind, palette=pal)
    from .project import PRESETS
    rules = PRESETS["android-game" if kind == "game" else "android-app"]["rules"]
    agents = root / "AGENTS.md"
    cur = agents.read_text(encoding="utf-8") if agents.exists() else "# Project rules for the AI\n"
    if "Android rules" not in cur:
        agents.write_text(cur.rstrip() + "\n\n" + rules + "\n", encoding="utf-8")
        ui.ok("AGENTS.md: added the Android rules for the AI.")
    if not (root / "src" / "lib.rs").exists():
        ui.warn("Next: move your app into src/lib.rs with an Android entry point. Ask Windmill:\n"
                "  windmill \"make this project start on Android (see AGENTS.md android rules)\"")


def benchmark(args):
    import os as _os
    from . import bench
    opt = lambda k, d=None: args[args.index(k) + 1] if k in args and args.index(k) + 1 < len(args) else d
    if "--selftest" in args:
        ok = bench.selftest(opt("--tasks"))
        (ui.ok if ok else ui.err)("All tasks are valid." if ok else "Some tasks are broken (see above).")
        return
    if "--history" in args:
        return bench.history()
    api = None
    if opt("--api"):
        key = _os.environ.get(opt("--api-key-env", "OPENAI_API_KEY"), "")
        if not key:
            return ui.err(f"Set your API key in the environment variable {opt('--api-key-env', 'OPENAI_API_KEY')} first "
                          "(it's only read, never saved).")
        if not opt("--api-model"):
            return ui.err("Also give --api-model <model name>.")
        api = {"base": opt("--api"), "key": key, "model": opt("--api-model")}
    modes = opt("--modes") or ("plain,windmill" + (",api-plain,api-windmill" if api else ""))
    bench.run(tuple(m.strip() for m in modes.split(",")), opt("--tasks"), api, int(opt("--max-steps", 50)))


def plugins_cmd(args):
    import json as _json
    from . import mcp
    sub = args[0] if args else "list"
    data = mcp.load()
    if sub == "list":
        ui.rule("Plugins (MCP)")
        for name, spec in data["mcpServers"].items():
            on = name not in data["disabled"]
            what = spec.get("url") or " ".join([spec.get("command", "")] + spec.get("args", []))
            print(f"  {ui.green('on ') if on else ui.dim('off')} {name:20} {ui.dim(what[:80])}")
        if not data["mcpServers"]:
            print("  none. Add one:  windmill plugins add <name> <command> [args...]   or   windmill plugins import")
    elif sub == "add" and len(args) >= 3:
        name, rest = args[1], args[2:]
        data["mcpServers"][name] = {"type": "http", "url": rest[0]} if rest[0].startswith("http") else \
            {"command": rest[0], "args": rest[1:]}
        mcp.save(data)
        ok, info = mcp.test(name)
        (ui.ok if ok else ui.err)(f"{name}: {info}")
    elif sub == "remove" and len(args) > 1:
        data["mcpServers"].pop(args[1], None)
        mcp.save(data)
        ui.ok(f"Removed {args[1]}.")
    elif sub in ("enable", "disable") and len(args) > 1:
        d = set(data["disabled"])
        (d.discard if sub == "enable" else d.add)(args[1])
        data["disabled"] = sorted(d)
        mcp.save(data)
        ui.ok(f"{args[1]} {sub}d.")
    elif sub == "test" and len(args) > 1:
        ok, info = mcp.test(args[1])
        (ui.ok if ok else ui.err)(info)
    elif sub in ("catalog", "browse", "connect"):
        from . import catalog
        catalog.browse(" ".join(args[1:]) or None)
    elif sub == "import":
        n, src = mcp.import_claude_desktop()
        (ui.ok if src else ui.warn)(f"Imported {n} plugin(s) from {src}" if src else "Claude Desktop's config wasn't found.")
    else:
        ui.warn("Usage: windmill plugins list|add <name> <command|url> [args]|remove|enable|disable|test <name>|import")


def help_all():
    ui.rule("Commands")
    for c, a, d in COMMANDS:
        print(f"  {ui.bcyan(('windmill ' + c).ljust(30))} {ui.dim(a.ljust(22))} {d}")


def dispatch(argv):
    if not argv:
        return main_menu()
    cmd, args = argv[0], argv[1:]
    from . import project, setup
    if cmd in ("chat", "c"):
        chat(args)
    elif cmd == "new":
        opts, k = {}, 0
        while k < len(args):
            if args[k] in ("--idea", "--who", "--extra", "--in") and k + 1 < len(args):
                opts[args[k][2:].replace("in", "parent")] = args[k + 1]
                k += 2
            else:
                opts.setdefault("idea", " ".join(args[k:]))
                break
        project.new_project(**opts)
    elif cmd == "build":
        project.build()
    elif cmd == "run":
        project.run_app()
    elif cmd == "debug":
        from .repl import DEBUG_PROMPT, Repl
        root = config.project_root()
        problem = " ".join(args)
        if not problem:
            ok, out = checks.run(root)
            problem = ("The project's checks fail:\n" + out) if not ok else ui.ask(
                "What's going wrong? (what you did, what happened, what you expected)")
        r = Repl(root)
        if server.ensure(r.cfg):
            r.start_agent()
            r.agent.send(DEBUG_PROMPT.format(problem=problem))
            if sys.stdin.isatty() and ui.confirm("\nKeep chatting about it?", False):
                r.run()
    elif cmd == "check":
        root = config.project_root()
        ok, out = checks.run(root)
        print(out)
        sys.exit(0 if ok else 1)
    elif cmd == "plan":
        if not args:
            return ui.warn('Usage: windmill plan "what to build"')
        server.ensure() and script("pipeline.py", args)
    elif cmd == "best":
        if not args:
            return ui.warn('Usage: windmill best "what to build" [-n 4]')
        server.ensure() and script("best_of_n.py", args)
    elif cmd == "git":
        from . import gitops
        gitops.cmd(args)
    elif cmd == "plugins":
        plugins_cmd(args)
    elif cmd == "connect":
        plugins_cmd(["catalog", *args])
    elif cmd == "skills":
        from . import skills
        sk = skills.discover(config.project_root())
        ui.rule("Skills")
        for s in sk:
            print(f"  {ui.cyan(s['name'][:28].ljust(28))} {s['description'][:80]}  {ui.dim('[' + s['source'] + ']')}")
        if not sk:
            print(f"  none yet. Make one: {config.HOME / 'skills' / '<name>' / 'SKILL.md'}")
    elif cmd in ("app", "gui"):
        from . import gui
        gui.main(args)
    elif cmd in ("get-model", "recommend"):
        from . import recommend
        recommend.menu()
    elif cmd in ("benchmark", "bench"):
        benchmark(args)
    elif cmd == "history":
        history(args)
    elif cmd == "resume":
        resume(args)
    elif cmd == "design":
        project.design_menu(config.project_root())
    elif cmd == "models":
        models_menu()
    elif cmd in ("android", "apk", "android-menu"):
        android_cmd(["build"] + args if cmd == "apk" else (args or (["menu"] if cmd == "android-menu" else ["status"])))
    elif cmd == "server":
        sub = args[0] if args else "status"
        if sub == "start":
            server.start()
        elif sub == "stop":
            server.stop()
        elif sub == "logs":
            print(server.tail_log(60))
        else:
            st = server.status()
            print(f"  {'running' if st['healthy'] else 'stopped'}" + (f" - {st['model']} (pid {st['pid']})" if st["pid"] else ""))
    elif cmd == "tune":
        server.tune()
    elif cmd in ("library", "lib"):
        script("ai_corpus.py", args or ["stats"])
    elif cmd == "deps":
        script("ai_corpus.py", ["deps"])
    elif cmd in ("map",):
        script("ai_index.py", [])
        p = config.project_root() / ".ai" / "map.md"
        print(p.read_text(encoding="utf-8") if p.exists() else "")
    elif cmd in ("find", "show", "refs", "related", "context", "file"):
        script("ai_query.py", [cmd, *args])
    elif cmd == "mark":
        b = checks.mark_base(config.project_root())
        ui.ok(f"Sloppy-code guard now checks everything changed since {b[:8] if b else '?'}")
    elif cmd == "init":
        init_project()
    elif cmd == "settings":
        settings(args)
    elif cmd == "setup":
        setup.run_setup(update_llama="--update-llama" in args)
    elif cmd == "doctor":
        setup.doctor()
    elif cmd == "fix":
        from . import heal
        b = heal.ensure_llama()
        heal.ensure_model()
        heal.report()
        (ui.ok if b else ui.err)("llama.cpp is ready." if b else "llama.cpp still isn't working - see above.")
    elif cmd == "update":
        from . import update
        update.cmd_update(args)
    elif cmd in ("version", "--version", "-v"):
        from . import update
        update.cmd_version()
    elif cmd == "dev":
        from . import update
        if args[:1] == ["publish"] and len(args) > 1:
            update.dev_setup()
            update.dev_publish(args[1])
        else:
            update.dev_setup()
    elif cmd == "release":
        subprocess.run([sys.executable, str(config.SCRIPTS / "release.py"), *args], cwd=config.KIT)
    elif cmd in ("help", "-h", "--help"):
        help_all()
    else:  # treat anything else as a chat message: `windmill make the button blue`
        chat(argv)


def ensure_command():
    """Windows: (re)write the `windmill` terminal command so it always runs this Windmill with this Python,
    and put it first on the user PATH (so a stray windmill.py on PATH can't open a 'select an app' box)."""
    import os
    try:
        exe = sys.executable
        if exe.lower().endswith("pythonw.exe"):
            exe = exe[:-11] + "python.exe"
        bindir = Path.home() / ".windmill" / "bin"
        bindir.mkdir(parents=True, exist_ok=True)
        shim = bindir / "windmill.cmd"
        script = config.KIT / "windmill.py"
        body = f'@echo off\r\n"{exe}" "{script}" %*\r\n'
        if not shim.exists() or shim.read_text(encoding="ascii", errors="ignore") != body:
            shim.write_bytes(body.encode("utf-8"))
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_READ | winreg.KEY_WRITE) as k:
            try:
                cur, kind = winreg.QueryValueEx(k, "Path")
            except FileNotFoundError:
                cur, kind = "", winreg.REG_EXPAND_SZ
            kit = str(config.KIT).rstrip("\\").lower()

            def stray(x):  # a folder holding windmill.py on PATH makes `windmill` open the .py ("select an app")
                x = os.path.expandvars(x).rstrip("\\").lower()
                return x == str(bindir).lower() or x == kit or x == kit + "\\bin" or \
                    os.path.isfile(os.path.join(x, "windmill.py")) or os.path.isfile(os.path.join(x, "windmill"))
            parts = [x for x in cur.split(";") if x and not stray(x)]
            new = ";".join([str(bindir), *parts])
            if new != cur:
                winreg.SetValueEx(k, "Path", 0, kind, new)
                import ctypes
                ctypes.windll.user32.SendMessageTimeoutW(0xFFFF, 0x1A, 0, "Environment", 2, 2000, None)
        machine = os.environ.get("PATH", "")
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment") as k:
                machine = winreg.QueryValueEx(k, "Path")[0]
        except OSError:
            pass
        bad = [x for x in machine.split(";") if x and (os.path.isfile(os.path.join(os.path.expandvars(x), "windmill.py"))
                                                     or os.path.isfile(os.path.join(os.path.expandvars(x), "windmill")))]
        if bad:
            ui.warn("The system PATH contains " + ", ".join(bad) + " - that makes typing `windmill` open a file "
                    "instead of Windmill. Remove it: Start > 'Edit the system environment variables' > "
                    "Environment Variables > System variables > Path.")
    except Exception:  # noqa: BLE001
        pass


def ensure_app():
    """Windows: build the desktop app + shortcuts the first time (and after updates that change the launcher)."""
    import os
    if os.name != "nt" or os.environ.get("WINDMILL_GUI"):
        return
    ensure_command()
    try:
        from . import gui
        if not gui.app_installed() and gui.LAUNCHER_SRC.exists():
            ui.info("Setting up the Windmill desktop app...")
            if gui.install(quiet=False):
                ui.ok("The Windmill desktop app is ready - it's on your Desktop, in the Start menu and in the "
                      "Windmill folder.")
            else:
                ui.warn(f"The desktop app couldn't be set up (details: {gui.INSTALL_LOG}). Windmill will try again "
                        "next time; the command line works normally.")
        else:
            gui.after_update()
    except Exception as e:  # noqa: BLE001
        ui.warn(f"(desktop app setup skipped: {e})")


def main():
    argv = sys.argv[1:]
    ensure_app()
    if not argv or argv[0] not in ("update", "version", "--version", "-v", "dev", "release", "check"):
        try:
            from . import update
            update.maybe_auto_update(argv)
        except Exception as e:  # noqa: BLE001 - an update problem must never stop Windmill from working
            ui.warn(f"(update check skipped: {e})")
    try:
        dispatch(argv)
    except KeyboardInterrupt:
        print()
