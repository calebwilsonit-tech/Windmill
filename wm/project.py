"""Guided projects for non-technical users: describe it -> pick a look -> Windmill plans and builds it
milestone by milestone, verifying every step. Plus `run` and the design picker."""
import json
import os
import re
import shutil
import subprocess
import sys
import webbrowser
from pathlib import Path

from . import checks, config, design, server, ui
from .llm import LLM, LLMError

ANDROID_COMMON = """Android rules:
- Build/run: `windmill android build` makes the APK, `windmill android install` puts it on a USB-connected phone,
  `windmill android logs` shows crash output. Test logic on desktop first (`cargo run`), then on the phone.
- android/ is the Gradle wrapper (from the matching Bevy release). Only change it for app name/icon/permissions
  (android/app/src/main/AndroidManifest.xml) or versionCode/versionName (android/app/build.gradle*).
  Increase versionCode by 1 for every release you hand out.
- Ask for Android permissions in AndroidManifest.xml only when a feature needs them.
- No desktop-only APIs on Android code paths (no std::process, no arbitrary filesystem paths, no blocking the
  main thread). Gate platform code with #[cfg(target_os = "android")].
- Logging: use `log`/`bevy::log` - it appears in `windmill android logs`. Panics show there too."""

PRESETS = {
    "website": dict(label="Website (works on phones too)", rust=False, game=False,
                    stack="Plain HTML + CSS + JavaScript, no build step. Opens in any browser, easy to host anywhere.",
                    rules="Static site: index.html at the root, css/ and js/ folders, assets/ for images/icons. "
                          "Semantic HTML, mobile-first CSS using the design tokens, vanilla JS modules only."),
    "web-app": dict(label="Web app with a server (accounts, data, forms)", rust=True, game=False,
                    stack="Rust axum server + server-rendered HTML pages + SQLite database.",
                    rules="Rust axum + tokio. HTML templates in templates/, static files in static/. SQLite via sqlx. "
                          "Every route has tests (axum + tower::ServiceExt::oneshot)."),
    "game-2d": dict(label="2D game", rust=True, game=True,
                    stack="Rust + Bevy game engine (desktop; can target web and mobile later).",
                    rules="Rust + Bevy. Organise by plugins (src/player.rs, src/enemy.rs, src/ui.rs ...), one Plugin per "
                          "feature, game states (Menu/Playing/Paused/GameOver) with bevy States. Assets in assets/."),
    "game-3d": dict(label="3D game", rust=True, game=True,
                    stack="Rust + Bevy game engine with 3D rendering.",
                    rules="Rust + Bevy 3D. Plugins per feature, States for menus, assets/ for models (glTF)."),
    "desktop-app": dict(label="Desktop app", rust=True, game=False,
                        stack="Rust + egui/eframe: a fast native app for Windows/Mac/Linux.",
                        rules="Rust + eframe/egui. App state struct, one module per screen/panel, persistence with serde."),
    "android-game": dict(label="Android game (.apk)", rust=True, game=True, android="game",
                         stack="Rust + Bevy, packaged as a real Android app (.apk) - also runs on your PC for testing.",
                         rules=ANDROID_COMMON + """
- Bevy on Android (GameActivity). Entry point: `#[bevy_main] pub fn main()` in src/lib.rs (crate-type lib+cdylib);
  src/main.rs just calls it for desktop testing. Keep that structure.
- Lifecycle: handle `AppLifecycle` (Suspended/WillSuspend/WillResume) - pause audio and game time when suspended;
  never assume the window/surface survives going to the background.
- Use `WinitSettings::mobile()` (saves battery) and `WindowMode::BorderlessFullscreen(MonitorSelection::Primary)`.
- Input is TOUCH: design for `Touches` / touch events and on-screen controls; keyboard/mouse only as desktop extras.
- Assets go in assets/ (packaged into the APK automatically). Keep them small (compressed textures/audio)."""),
    "android-app": dict(label="Android app (.apk)", rust=True, game=False, android="app",
                        stack="Rust + egui, packaged as a real Android app (.apk) - also runs on your PC for testing.",
                        rules=ANDROID_COMMON + """
- egui/eframe on Android (NativeActivity). Entry point in src/lib.rs:
  `#[cfg(target_os = "android")] #[unsafe(no_mangle)] fn android_main(app: winit::platform::android::activity::AndroidApp)`
  which creates `eframe::NativeOptions { android_app: Some(app), ..Default::default() }` and calls
  `eframe::run_native`. All UI lives in a shared `App` struct; src/main.rs runs the same app on desktop.
- egui has no automatic Android safe area yet: reserve space at the top for the status bar (a top panel ~32px)
  and keep primary actions in the bottom half (thumb zone).
- Scale for phones: set `ctx.set_pixels_per_point(...)` / `zoom_factor` so text is >= 16px-equivalent and
  touch targets >= 48dp; use `ui.add_sized` for big buttons; generous spacing via `ctx.style_mut`.
- Apply the design brief through `egui::Visuals` / `Style` (colours, rounding, spacing, fonts via FontDefinitions).
- Persist user data with eframe storage (`persistence` feature) or files in the app's data dir."""),
    "mobile-app": dict(label="Phone app that installs from the web (any phone)", rust=False, game=False,
                       stack="Progressive Web App: HTML/CSS/JS that installs to a phone home screen and works offline.",
                       rules="PWA: index.html, manifest.webmanifest, service-worker.js (offline cache), icons in assets/. "
                             "Touch-first UI, 44px+ targets, safe-area insets, bottom navigation."),
    "cli": dict(label="Command-line tool", rust=True, game=False,
                stack="Rust + clap: a fast, single-file program.",
                rules="Rust + clap derive. Library logic in src/lib.rs with tests; thin src/main.rs."),
}

PLAN_PROMPT = """You turn a non-technical person's idea into a concrete product plan. Reply with ONLY a JSON object:
{{"name": "short product name", "slug": "folder-name-in-kebab-case", "preset": one of {presets},
  "one_liner": "what it is in one sentence", "audience": "who it's for",
  "features": ["the must-have features, concrete, 4-8 items"],
  "milestones": ["5-8 milestones in build order. Each is a self-contained, testable chunk of work
                  described concretely (screens, data, behaviour) so an engineer can build it without asking.
                  Milestone 1 sets up the skeleton + main screen. The last one is polish: empty/error states,
                  responsiveness, accessibility, performance."]}}
Pick the preset that best fits; prefer 'website' for sites; for phones prefer 'android-app' / 'android-game'
(real .apk) unless the person needs iPhone too, then 'mobile-app'; a game preset for desktop games."""


def projects_home():
    d = Path.home() / "Windmill Projects"
    d.mkdir(exist_ok=True)
    return d


def extract_json(text):
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except ValueError:
        return None


# ------------------------------------------------------------------ design picker
def show_brief(i, b):
    p = b["palette"]
    print(f"\n  {ui.bcyan(str(i))}  {ui.bold(b['label'])}  {ui.dim('(' + b['mode'] + ')')}")
    print("     " + "  ".join(ui.swatch(p[k], k) for k in ("bg", "surface", "text", "primary", "accent")))
    print(f"     {ui.dim('fonts:')} {b['fonts']['display']} + {b['fonts']['body']}   {ui.dim('corners:')} {b['radius_px']}px")
    for m in b["signature_moves"][:2]:
        print(f"     {ui.dim('•')} {m}")


def pick_design(name, game=False, root=None):
    while True:
        opts = design.options(name, 3, game=game)
        ui.rule("Choose a look")
        for i, b in enumerate(opts, 1):
            show_brief(i, b)
        print(f"\n  {ui.bcyan('r')}  show 3 different ones     {ui.bcyan('c')}  use my brand colour")
        v = ui.ask("Pick 1, 2 or 3", "1").lower()
        if v == "r":
            continue
        if v == "c":
            hexc = ui.ask("Your brand colour as hex (e.g. #2a7f62)")
            if re.match(r"^#?[0-9a-fA-F]{6}$", hexc):
                opts = [design.make_brief(name, b["direction"], seed=b["seed"], brand="#" + hexc.lstrip("#")) for b in opts]
                for i, b in enumerate(opts, 1):
                    show_brief(i, b)
                v = ui.ask("Pick 1, 2 or 3", "1")
        if v in ("1", "2", "3"):
            b = opts[int(v) - 1]
            if root:
                design.save(root, b)
            return b


def design_menu(root):
    cur = design.load(root)
    if cur:
        print(cur[:1500])
        if not ui.confirm("Choose a new look?", False):
            return
    game = any(k in (Path(root) / "BRIEF.md").read_text(encoding="utf-8").lower() for k in ("game",)) if (Path(root) / "BRIEF.md").exists() else False
    b = pick_design(Path(root).name, game=game, root=root)
    ui.ok(f"Saved the design brief ({b['label']}). Windmill will follow it for all UI work.")


# ------------------------------------------------------------------ new project
def new_project(idea=None, who=None, extra=None, parent=None):
    """Guided new project. The desktop app passes the answers it already collected in its form."""
    cfg = config.load()
    ui.rule("New project")
    if not idea:
        print(ui.dim("  Describe what you want in your own words. No technical knowledge needed.\n"))
        idea = ui.ask("What do you want to make?")
        if not idea:
            return
        who = ui.ask("Who is it for? (optional)")
        extra = ui.ask("Anything it must have or must look like? (optional)")
    if not server.ensure(cfg):
        return
    llm = LLM(cfg)
    with ui.Spinner("turning your idea into a plan"):
        try:
            raw = llm.simple(PLAN_PROMPT.format(presets=list(PRESETS)),
                             f"Idea: {idea}\nAudience: {who or '(unspecified)'}\nMust-haves: {extra or '(none)'}",
                             temperature=0.5)
        except LLMError as e:
            ui.err(str(e))
            return
    plan = extract_json(raw)
    if not plan or plan.get("preset") not in PRESETS:
        ui.err("The model didn't return a usable plan. Try describing it again with a bit more detail.")
        return
    preset = PRESETS[plan["preset"]]
    ui.rule(plan["name"])
    print(f"  {plan['one_liner']}\n  {ui.dim('For:')} {plan.get('audience', '')}\n  {ui.dim('Built with:')} {preset['stack']}")
    print(ui.dim("\n  Features:"))
    for f in plan["features"]:
        print(f"   • {f}")
    print(ui.dim("\n  Build plan:"))
    for i, m in enumerate(plan["milestones"], 1):
        print(f"   {i}. {m[:110]}")
    print()
    change = ui.ask("Press Enter to accept, or type what to change")
    if change:
        with ui.Spinner("updating the plan"):
            raw = llm.simple(PLAN_PROMPT.format(presets=list(PRESETS)),
                             f"Current plan:\n{json.dumps(plan)}\n\nChange requested: {change}\nReturn the full updated JSON.")
        plan = extract_json(raw) or plan
        preset = PRESETS.get(plan.get("preset"), preset)
    if preset["rust"] and not shutil.which("cargo"):
        ui.warn("This kind of project needs Rust, which isn't installed. Run  windmill setup  to install it, "
                "or choose a website / phone app instead.")
        return
    slug = re.sub(r"[^a-z0-9-]+", "-", plan.get("slug", plan["name"]).lower()).strip("-") or "my-project"
    if parent:
        root = Path(parent).expanduser() / slug
        n = 2
        while root.exists() and any(root.iterdir()):
            root = Path(parent).expanduser() / f"{slug}-{n}"
            n += 1
    else:
        root = Path(ui.ask("Where should I create it?", str(projects_home() / slug))).expanduser()
    if root.exists() and any(root.iterdir()):
        ui.warn(f"{root} isn't empty - I'll add to it.")
    root.mkdir(parents=True, exist_ok=True)
    pick_design(plan["name"], game=preset["game"], root=root)
    scaffold(root, plan, preset)
    ui.ok(f"Project created at {root}")
    if ui.confirm("Start building it now? (it works through the milestones on its own)", True):
        build(root)
    else:
        ui.info(f"Later:  cd \"{root}\"  then  windmill build")


# Library focus per project type (keeps unrelated web code out of an Android game's references)
FOCUS = {"game": ["android", "game", "graphics", "physics", "ui-design", "learning", "algorithms", "core", "ecs"],
         "app": ["android", "gui", "ui-design", "mobile", "learning", "algorithms", "core", "async", "db"]}

LIB_TOML = """
[lib]
# lib = desktop testing, cdylib = the Android library loaded by the app
crate-type = ["lib", "cdylib"]
"""

GAME_LIB = """//! {name}
use bevy::{{prelude::*, window::WindowMode, winit::WinitSettings}};

/// Entry point for Android (via `bevy_main`) and desktop (`src/main.rs`).
#[bevy_main]
pub fn main() {{
    App::new()
        .add_plugins(DefaultPlugins.set(WindowPlugin {{
            primary_window: Some(Window {{
                title: "{name}".into(),
                mode: WindowMode::BorderlessFullscreen(MonitorSelection::Primary),
                ..default()
            }}),
            ..default()
        }}))
        .insert_resource(WinitSettings::mobile())
        .add_systems(Startup, setup)
        .run();
}}

fn setup(mut commands: Commands) {{
    commands.spawn(Camera2d);
    commands.spawn(Text::new("{name}"));
}}
"""

APP_LIB = """//! {name}
use eframe::egui;

/// Android entry point: the system starts the app here.
#[cfg(target_os = "android")]
#[unsafe(no_mangle)]
fn android_main(app: winit::platform::android::activity::AndroidApp) {{
    android_logger::init_once(android_logger::Config::default().with_max_level(log::LevelFilter::Info));
    let options = eframe::NativeOptions {{
        android_app: Some(app),
        ..Default::default()
    }};
    if let Err(e) = eframe::run_native("{name}", options, Box::new(|cc| Ok(Box::new(App::new(cc))))) {{
        log::error!("app failed to start: {{e}}");
    }}
}}

/// The whole app; shared by Android and desktop.
pub struct App;

impl App {{
    /// Creates the app and applies the look.
    pub fn new(cc: &eframe::CreationContext<'_>) -> Self {{
        cc.egui_ctx.set_zoom_factor(1.5);
        Self
    }}
}}

impl eframe::App for App {{
    fn ui(&mut self, ui: &mut egui::Ui, _frame: &mut eframe::Frame) {{
        egui::CentralPanel::default().show(ui, |ui| {{
            ui.add_space(32.0);
            ui.heading("{name}");
        }});
    }}
}}
"""

APP_MAIN = """fn main() -> eframe::Result {{
    eframe::run_native(
        "{name}",
        eframe::NativeOptions::default(),
        Box::new(|cc| Ok(Box::new({crate}::App::new(cc)))),
    )
}}
"""


def scaffold_android(root, plan, preset, crate):
    """Rust crate laid out for Android + the Gradle wrapper from the matching Bevy release."""
    from . import android, design
    run = lambda *c: subprocess.run(list(c), cwd=root, capture_output=True, text=True)
    toml = root / "Cargo.toml"
    lib = root / "src" / "lib.rs"
    if not lib.exists():  # cargo refuses to run if [lib] points at a missing file
        lib.parent.mkdir(exist_ok=True)
        lib.write_text("//! (replaced below)\n", encoding="utf-8")
    if "[lib]" not in toml.read_text(encoding="utf-8"):
        t = toml.read_text(encoding="utf-8")
        toml.write_text(t.replace("[dependencies]", LIB_TOML.strip() + "\n\n[dependencies]", 1), encoding="utf-8")
    name = plan["name"].replace('"', "")
    with ui.Spinner("setting up the Rust side of the Android app"):
        if preset["android"] == "game":
            run("cargo", "add", "bevy", "--features", "android-game-activity")
            (root / "src" / "lib.rs").write_text(GAME_LIB.format(name=name), encoding="utf-8")
            (root / "src" / "main.rs").write_text(f"fn main() {{\n    {crate}::main();\n}}\n", encoding="utf-8")
        else:
            run("cargo", "add", "eframe", "--no-default-features", "--features", "default_fonts,glow,android-native-activity")
            ver = re.search(r'eframe = \{ version = "([^"]+)"', toml.read_text(encoding="utf-8"))
            # Linux desktops need a window system; added only for Linux so the Android build stays lean
            run("cargo", "add", f"eframe@{ver.group(1) if ver else '*'}", "--no-default-features", "--features",
                "x11,wayland", "--target", 'cfg(target_os = "linux")')
            run("cargo", "add", "log")
            run("cargo", "add", "android_logger", "--target", 'cfg(target_os = "android")')
            run("cargo", "generate-lockfile")
            lock = (root / "Cargo.lock").read_text(encoding="utf-8") if (root / "Cargo.lock").exists() else ""
            m = re.search(r'name = "winit"\nversion = "([^"]+)"', lock)
            run("cargo", "add", f"winit@={m.group(1)}" if m else "winit", "--target", 'cfg(target_os = "android")')
            (root / "src" / "lib.rs").write_text(APP_LIB.format(name=name), encoding="utf-8")
            (root / "src" / "main.rs").write_text(APP_MAIN.format(name=name, crate=crate), encoding="utf-8")
        run("cargo", "generate-lockfile")
    slug = re.sub(r"[^a-z0-9]", "", plan.get("slug", crate).lower()) or "app"
    app_id = f"com.windmill.{slug}"
    b = (root / ".windmill" / "design.json")
    palette = json.loads(b.read_text(encoding="utf-8"))["palette"] if b.exists() else {}
    android.create_wrapper(root, name, app_id, crate, kind=preset["android"], palette=palette)


def scaffold(root, plan, preset):
    root = Path(root)
    run = lambda *c: subprocess.run(list(c), cwd=root, capture_output=True, text=True)
    (root / "BRIEF.md").write_text(
        f"# {plan['name']}\n{plan['one_liner']}\n\n**For:** {plan.get('audience', '')}\n\n**Built with:** {preset['stack']}\n\n"
        "## Features\n" + "\n".join(f"- {f}" for f in plan["features"]) + "\n", encoding="utf-8")
    (root / "ROADMAP.md").write_text(
        f"# Roadmap - {plan['name']}\nWindmill builds these in order (`windmill build`). Edit freely.\n\n"
        + "\n".join(f"- [ ] {m}" for m in plan["milestones"]) + "\n", encoding="utf-8")
    agents = (config.KIT / "project-template" / "AGENTS.md")
    rules = agents.read_text(encoding="utf-8") if preset["rust"] and agents.exists() else \
        "# Project rules for the AI\n\n## Finishing rules\n- Finish everything you start: no TODOs, placeholders, lorem ipsum.\n" \
        "- Every interactive element has hover, focus-visible, active and disabled states.\n- Follow .windmill/design.md exactly.\n"
    (root / "AGENTS.md").write_text(f"{rules}\n\n## Stack for this project\n{preset['rules']}\n", encoding="utf-8")
    if not (root / ".git").exists():
        run("git", "init", "-q")
    if preset["rust"] and not (root / "Cargo.toml").exists():
        name = re.sub(r"[^a-z0-9_]", "_", root.name.lower()) or "app"
        run("cargo", "init", "--name", name, "--vcs", "none")
        deps = {"game-2d": ["bevy"], "game-3d": ["bevy"], "web-app": ["axum", "tokio --features full", "serde --features derive", "serde_json"],
                "desktop-app": ["eframe", "serde --features derive"], "cli": ["clap --features derive", "anyhow"]}
        for k, ds in deps.items():
            if PRESETS.get(k) is preset:
                with ui.Spinner(f"adding {', '.join(d.split()[0] for d in ds)}"):
                    for d in ds:
                        run("cargo", "add", *d.split())
        if preset.get("android"):
            scaffold_android(root, plan, preset, name)
    (root / ".gitignore").write_text("target/\nnode_modules/\ndist/\n.env\n", encoding="utf-8") if not (root / ".gitignore").exists() else None
    (root / "ai-kit.json").write_text(json.dumps({"test_cmd": None, "max_rounds": 4, "step_retries": 3,
                                                  "mutation_testing": preset["rust"], "best_of_n": 3,
                                                  "corpus_categories": FOCUS.get(preset.get("android") or "", []),
                                                  "reference_budget": 1800}, indent=2), encoding="utf-8")
    run("git", "add", "-A")
    run("git", "-c", "user.name=Windmill", "-c", "user.email=windmill@localhost", "commit", "-q", "-m", "windmill: new project")
    subprocess.run([sys.executable, str(config.SCRIPTS / "ai_index.py"), "--init", "--quiet"], cwd=root, capture_output=True)
    run("git", "add", "-A")
    run("git", "-c", "user.name=Windmill", "-c", "user.email=windmill@localhost", "commit", "-q", "-m", "windmill: project memory")


# ------------------------------------------------------------------ autopilot
def milestones(root):
    f = Path(root) / "ROADMAP.md"
    if not f.exists():
        return []
    return [(i, line, line.startswith("- [x]")) for i, line in enumerate(f.read_text(encoding="utf-8").splitlines())
            if re.match(r"^- \[( |x)\] ", line)]


def mark(root, line_no, done=True, replace_with=None):
    f = Path(root) / "ROADMAP.md"
    lines = f.read_text(encoding="utf-8").splitlines()
    if replace_with is not None:
        lines[line_no:line_no + 1] = replace_with
    else:
        lines[line_no] = lines[line_no].replace("- [ ]", "- [x]", 1) if done else lines[line_no]
    f.write_text("\n".join(lines) + "\n", encoding="utf-8")
    subprocess.run(["git", "add", "ROADMAP.md"], cwd=root, capture_output=True)
    subprocess.run(["git", "-c", "user.name=Windmill", "-c", "user.email=windmill@localhost", "commit", "-q", "-m",
                    "windmill: roadmap update"], cwd=root, capture_output=True)


def _save_outside_changes(root, n):
    """Autopilot needs a clean starting point. Save anything changed outside the build (your edits, files an
    editor or Windows created) as its own commit instead of stopping with 'uncommitted changes'."""
    st = subprocess.run(["git", "status", "--porcelain"], cwd=root, capture_output=True, text=True)
    changed = [l for l in st.stdout.splitlines() if l.strip() and not l[3:].startswith(".ai")]
    if st.returncode == 0 and changed:
        subprocess.run(["git", "add", "-A"], cwd=root, capture_output=True)
        subprocess.run(["git", "-c", "user.name=Windmill", "-c", "user.email=windmill@localhost", "commit", "-q", "-m",
                        f"windmill: save changes made before milestone {n}"], cwd=root, capture_output=True)
        ui.info(f"Saved {len(changed)} changed file(s) first (so the build can be undone cleanly).")


def build(root=None):
    root = Path(root or config.project_root())
    ms = milestones(root)
    if not ms:
        ui.warn("No ROADMAP.md here. Start with  windmill new  (or write a ROADMAP.md with '- [ ] ...' lines).")
        return
    cfg = config.load()
    if not server.ensure(cfg):
        return
    while True:
        ms = milestones(root)
        todo = [(i, l) for i, l, d in ms if not d]
        if not todo:
            ui.ok("Every milestone is built and verified!  Try it:  windmill run")
            return
        done_n = len(ms) - len(todo)
        line_no, line = todo[0]
        task = line[6:].strip()
        ui.rule(f"Milestone {done_n + 1}/{len(ms)}")
        print(f"  {task}\n")
        env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
        _save_outside_changes(root, done_n + 1)
        r = subprocess.run([sys.executable, str(config.SCRIPTS / "pipeline.py"), task], cwd=root, env=env)
        if r.returncode == 4:  # stopped on purpose; its message is printed just above
            ui.warn("The build stopped (the reason is just above). Fix that, then continue with  windmill build "
                    "(or the Build button).")
            return
        if r.returncode not in (0, 2):  # 2 = "verification didn't pass"; anything else = Windmill itself crashed
            ui.warn("Windmill hit an internal error (not a problem with your project). Retrying once...")
            r = subprocess.run([sys.executable, str(config.SCRIPTS / "pipeline.py"), task], cwd=root, env=env)
            if r.returncode not in (0, 2):
                ui.err("Windmill's build step keeps crashing - that's a Windmill bug, not your project. The error is "
                       f"above and in {Path.home() / '.windmill' / 'logs' / 'build-crash.log'}. Run  windmill update  "
                       "(the fix may already be out), then  windmill build  to continue where you left off.")
                return
        if r.returncode == 0:
            mark(root, line_no)
            ui.ok(f"Milestone {done_n + 1} done and verified.")
            continue
        ui.warn("This milestone didn't fully pass verification. Your project is safe (nothing broken was kept "
                "on top of working code).")
        choice = ui.menu("What now?", [
            ("best", "Try again with several independent attempts", "slower, often works"),
            ("split", "Break it into smaller milestones and continue", "recommended"),
            ("skip", "Skip it for now", ""),
            ("stop", "Stop here", "continue later with: windmill build")], allow_back=False)
        if choice == "best":
            r = subprocess.run([sys.executable, str(config.SCRIPTS / "best_of_n.py"), task, "--apply"], cwd=root, env=env)
            if r.returncode == 0:
                subprocess.run(["git", "-c", "user.name=Windmill", "-c", "user.email=windmill@localhost", "commit",
                                "-q", "-m", f"windmill: {task[:60]}"], cwd=root, capture_output=True)
                mark(root, line_no)
        elif choice == "split":
            with ui.Spinner("splitting into smaller steps"):
                raw = LLM(cfg).simple("Split this software milestone into 2-4 smaller, independently testable milestones. "
                                      "Reply with ONLY a JSON list of strings.", task)
            m = re.search(r"\[.*\]", raw, re.S)
            parts = json.loads(m.group(0)) if m else []
            if parts:
                mark(root, line_no, replace_with=[f"- [ ] {p}" for p in parts])
        elif choice == "skip":
            mark(root, line_no, replace_with=[line.replace("- [ ]", "- [x] (skipped)", 1)])
        else:
            return


# ------------------------------------------------------------------ run the app
def run_app(root=None):
    root = Path(root or config.project_root())
    k = checks.kinds(root)
    try:
        if "android" in k:
            from . import android
            c = ui.menu("Run where?", [("phone", "On my phone", "builds the APK and installs it over USB"),
                                       ("pc", "On this PC", "quick test window")], allow_back=False)
            if c == "phone":
                android.install(root)
                return
            subprocess.run(["cargo", "run"], cwd=root)
        elif "rust" in k:
            ui.info("Starting your app (first build can take a few minutes; Ctrl+C to stop)...")
            subprocess.run(["cargo", "run", "--release" if "bevy" in (root / "Cargo.toml").read_text() else "--quiet"], cwd=root)
        elif "node" in k:
            pkg = json.loads((root / "package.json").read_text(encoding="utf-8"))
            script = next((s for s in ("dev", "start", "preview") if s in pkg.get("scripts", {})), None)
            npm = "npm.cmd" if os.name == "nt" else "npm"
            subprocess.run([npm, "run", script] if script else [npm, "start"], cwd=root)
        elif "python" in k and (root / "main.py").exists():
            subprocess.run([sys.executable, "main.py"], cwd=root)
        elif list(root.glob("*.html")) or (root / "public" / "index.html").exists():
            serve_static(root)
        else:
            ui.warn("I can't tell how to run this project yet. Ask in chat: \"how do I run this?\"")
    except KeyboardInterrupt:
        print()
        ui.info("Stopped.")


def serve_static(root):
    import functools
    import http.server
    import threading
    web_root = root / "public" if (root / "public" / "index.html").exists() else root
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(web_root))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    url = f"http://127.0.0.1:{httpd.server_address[1]}/"
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    ui.ok(f"Your site is live at {ui.bold(url)}  (opening your browser - Ctrl+C to stop)")
    webbrowser.open(url)
    try:
        while True:
            import time
            time.sleep(1)
    except KeyboardInterrupt:
        httpd.shutdown()
        raise
