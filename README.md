```
        \\                //
          \\            //
            \\   __   //
              \\(  )//
               (_()_)
              //(  )\\
            //  |  |  \\
          //   /|[]|\   \\
        //    / |  | \    \\
             /  |  |  \
            /   |  |   \
           /____|__|____\
          W I N D M I L L
```

# Windmill: local AI that builds things

Describe what you want in plain words, and Windmill plans it, builds it, tests it, fixes its own
mistakes, and tells you how to see the result. Everything runs **on your own computer**: no
subscription, no cloud, your code never leaves your machine.

---

## Install (once)

**Windows:** in PowerShell:
```
git clone https://github.com/calebwilsonit-tech/windmill C:\windmill
powershell -ExecutionPolicy Bypass -File C:\windmill\install.ps1
```
(No Git yet? Download the ZIP from the GitHub page, unzip it to `C:\windmill`, right-click
**`install.ps1`** and choose **Run with PowerShell**. Auto-updates work either way.)

**Mac / Linux:** `git clone https://github.com/calebwilsonit-tech/windmill ~/windmill && bash ~/windmill/install.sh`

The installer:
1. Installs Python and Git if they're missing (on Mac/Linux it tells you what to install).
2. Adds the `windmill` command to your computer.
3. Starts the **setup wizard**, which:
   - checks your PC (graphics card, memory)
   - downloads the AI engine (llama.cpp), choosing the right version for your PC automatically
   - finds the AI models you already have (LM Studio, Ollama, Downloads...), or offers ones that fit your PC
   - tunes the model to use your graphics card as much as is safe
   - offers to download the reference library (several GB; recommended)
   - runs a test conversation

When it says **"Windmill is ready"**, you're done.

---

## Using it

Open any terminal (on Windows: press Start, type **Terminal**) and type:

```
windmill
```

You get the main menu:

| Choice | What it does |
|---|---|
| **Chat & build in this folder** | Talk to Windmill like you would with Claude Code. It reads, writes and runs your code. |
| **Start a new project** | Describe your idea. Windmill asks two questions, writes a plan, lets you pick a look, and builds it. |
| **Continue building** | Keeps working through the project's roadmap on its own, verifying each milestone. |
| **Recent chats** | Pick up any earlier conversation. |
| **Fix a problem** | Describe what's wrong (or just pick it, and it runs the checks itself) and the debugger fixes it. |
| **Run my app** | Opens your website in the browser, or launches your game/app. |
| **Look & feel** | Choose or change the project's visual identity. |
| **AI models** | See every model on your PC, how well each fits, and switch. |
| **Reference library** | Download or search the library of real-world code. |
| **Setup & health check** | Repair or check everything. |

You never have to start or stop the AI yourself. Windmill starts it when needed and offers to stop it
when you leave, to free up memory.

### Your first project

```
windmill new
```
Example: *"A website for my sourdough bakery where people can see today's bread and order for pickup."*
Windmill turns that into a product plan (features + milestones). You press Enter to accept, then
pick one of three designs, and it builds the whole thing milestone by milestone. When it's finished:
`windmill run`.

### Chatting while you build

Inside a chat, just type. Useful commands:

| Command | |
|---|---|
| `/history` and `/resume 3` or `/resume inventory` | find and reopen any past chat (by number or by words in it) |
| `/check` | run every check on the project |
| `/debug the jump button does nothing` | debugger mode |
| `/undo` | undo Windmill's last change |
| `/run` | run the app |
| `/design` | change the look |
| `/plan <big task>` | the most careful mode for big features (see below) |
| `/best <hard task>` | several independent attempts, keep the best |
| `/new`, `/model`, `/think`, `/ask`, `/exit` | new chat, switch model, show thinking, ask before commands, leave |

Ctrl+C stops what it's doing without quitting. Paste multi-line text normally, or wrap it in `"""` lines.

### Finding old chats

Every chat is saved automatically and named after its first message.

```
windmill history              # list recent chats
windmill history inventory    # search inside all chats
windmill resume               # reopen the last chat in this folder
windmill resume 2             # reopen chat #2 from the list
windmill resume save system   # reopen the best match for those words
```
`/export` saves a chat as a Markdown file.

---

## How it keeps the code clean: the debugger

Local models are smart but sloppy if nobody checks them. Windmill checks **everything**,
automatically, and makes the model call itself out:

1. **Every edit is scanned immediately.** TODOs, "for now", "...", stubs, `todo!()`, lorem ipsum,
   placeholder images, `console.log`, skipped tests: the model is told on the spot and has to fix it.
2. **"Done" isn't accepted until the checks pass:** build, linter, tests and a sloppy-code guard
   for the project's language (Rust, websites, JavaScript/TypeScript, Python, Go).
3. **Repeated failures escalate:**
   - 1st–2nd time: "fix the cause, not the symptom"
   - 3rd time: **stop and diagnose**: three hypotheses, check the real library API, then one focused change
   - 5th time: **rolled back** to the last working version, with a "don't repeat this approach" note
   - 7th time: stops and explains in plain English. Your project is left at the last working version.
4. **Checkpoints:** every time all checks pass, Windmill saves a snapshot (a git commit), so nothing
   working is ever lost. `/undo` steps back one.
5. **Fresh-eyes review:** before it reports "done", a separate review pass with no memory of writing
   the code looks for bugs, missing pieces and placeholders. UI work is reviewed against a
   design-director checklist.

Risky commands (deleting folders, force-pushing, `sudo`...) are blocked or need your OK. The AI can
only touch files inside the project folder.

---

## Design: UI that doesn't all look the same

Every project gets its own **design brief** (`windmill design`, or chosen during `windmill new`):

- one of **16 art directions**: editorial, Swiss, neo-brutalist, soft product, luxury, playful,
  technical, organic, retro-futurist, minimal mono, glass and depth, trustworthy, plus 4 game-UI styles
  (fantasy RPG, sci-fi HUD, pixel, cozy)
- a **unique colour palette**, every combination contrast-checked. Tested across 32,000 random palettes:
  body text never below 14:1, buttons never below 4.5:1, which meets accessibility standards
- a curated **free font pairing**, a type scale, a spacing scale, a corner style, shadows and motion
- **signature moves**: three specific design ideas that make this product recognisably *this* product,
  plus a list of generic patterns to avoid

Windmill gives the AI the brief, a **professional UI rulebook** (hierarchy, every interaction
state, empty/loading/error states, responsive, accessibility, real copy, game-UI rules), and
**reference code from top design systems**, then reviews the result like a design director.
`c` in the picker builds the palette around your own brand colour.

---

## Android apps and games (.apk), written in Rust

Pick **Android app** or **Android game** in `windmill new` (or say "a phone app for..."), and you get a
real Android app you can install on any Android phone or hand out as an `.apk` file.

| | Android app | Android game |
|---|---|---|
| Built with | Rust + **egui** (NativeActivity) | Rust + **Bevy** (GameActivity) |
| Good for | tools, trackers, utilities, dashboards | 2D/3D games, interactive toys |
| Test on PC | `cargo run`, same code in a window | `cargo run` |

**Commands**
```
windmill android setup     install everything needed (once, ~3-4 GB): Java, Android SDK + NDK, cargo-ndk, Rust targets
windmill android status    what's installed + connected phones
windmill apk               build a signed release .apk into dist/
windmill android install   build, install and launch it on your phone (USB)
windmill android logs      the phone's crash / panic / Rust log lines
windmill android init      add Android to an existing Bevy or egui project
```
Or from the main menu: **Android: build & put on my phone**.

**Putting it on a phone:** on the phone open Settings > About phone and tap *Build number* 7 times,
then Settings > Developer options > turn on **USB debugging**. Plug it in, accept the prompt, and run
`windmill android install`. Or send the `.apk` from `dist/` to the phone and open it (allow
"install unknown apps").

**How it works (and why it stays reliable)**
- Rust is compiled for the phone's CPU with **cargo-ndk**, then packaged by a small **Gradle**
  project in `android/`, the approach Bevy officially documents.
- That `android/` project is copied **from the Bevy release your project uses** (for egui apps, from the
  latest Bevy release's NativeActivity template), then renamed to your app: package id, library name,
  label, asset path. The Android-side versions therefore always match the Rust side, even as Bevy releases
  new versions.
- The launcher icon is generated in your design brief's colours (replace
  `android/app/src/main/res/mipmap-*/ic_launcher.png` with your own artwork any time).
- Release APKs are signed with a key created once per app in `~/.windmill/keys/`. **Back up that
  folder**: Android only installs updates signed with the same key.
- Every check also compiles the code **for Android** (`cargo ndk ... clippy`), so phone-only errors are
  caught, and the AI gets a `phone` tool: it can build, install, launch and then **read the phone's
  crash log** to debug real-device problems.
- The AI follows Android rules in `AGENTS.md`: app lifecycle (pause/resume), touch-first controls,
  status-bar space, 48dp touch targets, battery-friendly settings, permissions only when needed.
- The library gains Android references: Google's NDK, platform and architecture samples, Now in Android
  (Google's flagship well-designed app), Material Design component guidance, `android-activity`, `ndk`,
  `jni-rs` (calling Android/Java APIs from Rust), cargo-ndk, Bevy's mobile example and egui. Android projects
  automatically focus the library on these.

**Limits:** the app is a single native screen drawn by Rust (egui/Bevy), not Android's own UI toolkit, so
it looks like *your* design rather than stock Android. Text-heavy apps work, but the on-screen keyboard
is more basic than in Java/Kotlin apps. Bevy games need Android 12+ (the GameActivity minimum in the
current template). Publishing on Google Play additionally needs a Play developer account and an `.aab`
bundle (`cd android && gradlew bundleRelease`). iPhone apps need a Mac with Xcode; for iPhone + Android
from one codebase without a Mac, use the *installable web app* option.

## The reference library

A searchable library of real, well-built, permissively licensed code that Windmill consults while
it works (`windmill library fetch` downloads it; `windmill library stats` shows it):

| Area | Examples |
|---|---|
| **UI and design systems** | shadcn/ui, Radix, Headless UI, GOV.UK Design System, GitHub Primer, IBM Carbon, Material Web, Chakra, Mantine, daisyUI, Open Props, Tailwind, iced |
| **Websites and web apps** | Next.js examples, Astro, Svelte, htmx, Leptos, Dioxus, Yew, axum, actix, FastAPI, Flask |
| **Games** | Bevy (+ ecosystem), macroquad, Fyrox, Godot demos, three.js, rapier physics |
| **Mobile** | Jetpack Compose samples, Flutter samples, Expo, Tauri, Bevy mobile |
| **Android (Rust + platform)** | android-activity, ndk-rs, jni-rs, cargo-ndk, Google NDK / platform / architecture samples, Now in Android, Material components docs |
| **Data, async, CLI, GUI, algorithms** | sqlx, SeaORM, Diesel, tokio, clap, ratatui, egui, TheAlgorithms (Rust/Python/TS) |
| **Learning** | The Rust Book, Rust by Example, Rust Cookbook, Async Book |

- Every repo's licence is **verified from its files when downloaded**. Code with no licence or a
  copyleft licence is skipped (opt in with `--include-copyleft`, e.g. for the Veloren RPG or the MDN docs).
- Plus, per project, the **exact API of the library versions your project uses**, so the AI doesn't write
  code for the wrong version.
- `windmill library add <git-url-or-folder> --cat mine` adds your own projects: the best references of all.

---

### Growing the library automatically: every public Bevy project

```
windmill library discover bevy           # find them all (takes a while; polite, 1 request/second)
windmill library fetch --discovered bevy # download them (licence-checked, 6 at a time; --max N to limit)
windmill library index
```
Discovery combines four sources: the official **Bevy Assets** catalog, every crate on **crates.io** that
depends on Bevy (with download counts as a quality signal), Bevy games on **itch.io** that publish their
source, and GitHub's **`bevy` topic**. A test run found **2,308 unique repositories** (about 590 of them
games). A sample of 150 downloaded in 42 seconds (3.6 GB; 8 rejected for no or copyleft licence) and
indexed into 206,000 searchable pieces. Everything is roughly 55 GB, which is fine if you have the disk.

**More code doesn't mean more tokens.** The model never sees the library, only a fixed-size reference
pack (~1,800 tokens) per step, so a bigger library just means *better* matches in the same space. Three
things keep that pack sharp:
- **Bevy-version aware:** every repo is tagged with the Bevy version it targets, and search prefers code
  for *your* version. Bevy's API changes every release, so a 0.14-era example would teach a 0.20 project
  the wrong code. In testing, "inventory slots with item stacking" returned 0.20/0.19 inventory systems
  for a 0.20 project and 0.14-era ones for a 0.14 project.
- **Duplicates removed:** forks and copied templates (5,600+ duplicated pieces in the sample) never take
  up the budget twice.
- **Best lines, not first lines:** each snippet is the section with the most relevant lines.

The library also **keeps growing by itself**: every 30 days (`library_refresh_days`) Windmill updates
the library, re-runs discovery and downloads new projects in the background.

## Models

`windmill models` lists every GGUF model on your PC (LM Studio, Ollama, Hugging Face cache, Downloads,
the `windmill\models` folder, plus any folder you add with `windmill settings model_dirs "D:\models"`),
with size, quantisation, MoE/dense, and whether it fits your PC. Pick one and Windmill configures the
server for it. `windmill tune` finds the best GPU split automatically.

MoE models (for example Qwen 30B/35B "A3B") run well on gaming laptops because most of the model can
sit in normal RAM.

---

## Updates and versions (automatic)

**Installed copies update themselves.** Once a day, Windmill checks for a newer release. Fixes and minor
updates install automatically: you'll see a one-line "Updated to 1.2.0" plus what changed, and your
command then carries on. **Major** updates ask first. Your models, library and Android tools are never
touched by an update. Rolled back? That version won't auto-install again; only a newer one will.

```
windmill version              # version, update source, last check
windmill update               # check and install now
windmill update --rollback    # undo the last update
windmill settings auto_update ask    # or: auto (default) / off
```

When an update adds new library sources, they download in the background automatically.

### Making changes to Windmill (the release flow)
In your own Windmill source folder (your clone of the GitHub repo), run once:
```
windmill dev setup
```
From then on, **every commit asks**:
```
  1  Major  -> 2.0.0   big or breaking change
  2  Minor  -> 1.1.0   new features / additions
  3  Fix    -> 1.0.1   bug fixes
  4  Not yet  (keep working; release later)
```
Your answer sets the version, writes `CHANGELOG.md` from the commit messages, tags the release and
pushes it, and every installed Windmill picks it up within a day. This applies whether the change came
from you, from Claude or from any other tool. Commits made without a terminal (some GUI apps) are
released as a fix. `windmill release [major|minor|fix]` releases by hand.

## All commands

```
windmill                      main menu
windmill chat [message]       chat & build in this folder         (alias: windmill c, or just: windmill <message>)
windmill new                  new project from an idea
windmill build                build the roadmap on autopilot
windmill run                  run / open the app
windmill debug [problem]      debugger
windmill check                run all checks
windmill plan "<task>"        careful mode: plan -> verified steps -> review -> mutation tests
windmill best "<task>" -n 4   several independent attempts, keep the best
windmill history [words]      list / search chats   (--all includes internal ones)
windmill resume [n | words]   reopen a chat
windmill design               look & feel
windmill models               choose a model
windmill server start|stop|status|logs
windmill tune                 GPU auto-tuning
windmill library fetch|index|add|search|stats|pack
windmill deps                 index this project's exact dependency versions
windmill map | find | show | refs | related | context    project index lookups
windmill init                 set up Windmill in an existing project
windmill settings [key value] view / change settings
windmill update [--rollback]  update now / undo the last update
windmill version              version and update settings
windmill dev setup            (Windmill developers) release on every commit
windmill library discover bevy   find every public Bevy project
windmill setup                install / repair everything
windmill doctor               health check
```

### Careful mode, in detail (`windmill plan`, also used by `windmill build`)
1. **Plan:** numbered requirements plus 2–8 small steps, using the project map and the library.
2. **Steps:** each runs in a fresh session that may only do that step, and must pass every check before
   the next one starts (retried up to 3 times, otherwise it stops rather than build on broken code).
3. **Review:** an independent reviewer marks every requirement DONE / PARTIAL / MISSING.
4. **Fix**, then review again.
5. **Mutation testing** (Rust, with cargo-mutants): deliberately breaks the new code to prove the
   tests actually catch problems.

Every run is logged in the project's `.ai/journal.md`.

### Where things live
- `~/.windmill/`: settings, chats (`sessions.db`), logs
- `windmill/models/`, `windmill/llama.cpp/`, `windmill/corpus/`: models, AI engine, library
- In each project: `.ai/` (project map, symbol graph, dependency index, journal), `.windmill/design.md`
  (design brief), `BRIEF.md`, `ROADMAP.md`, `AGENTS.md` (rules for the AI; edit freely)

### Settings (`windmill settings <key> <value>`)
`ctx` (context size), `port`, `cache_type`, `cpu_moe` / `ngl` (GPU split; `windmill tune` sets
these), `threads`, `model_dirs`, `show_thinking`, `permission` (`auto` or `ask`), `max_steps`,
`engine` (`windmill` or `aider`).

---

## Honest limits

- **A local model is not a frontier cloud model.** Windmill's checking, references and design system
  close much of the gap on clear, well-scoped work, but big vague requests are harder. If it stops and
  explains, give it a smaller or more specific request, or let `windmill build` split the milestone.
- **Speed depends on your PC.** On an RTX 4060 laptop with 32 GB RAM, a 30–35B MoE model runs at a
  usable speed. Careful mode (planning, review, mutation tests) trades time for quality on purpose.
- **Phones:** native Android apps/games (.apk) are built in Rust with full automatic setup (see above).
  iPhone apps need a Mac with Xcode; the installable-web-app option covers iPhone + Android without one.
- **Games** use Rust + Bevy. The first build downloads and compiles the engine, which takes a few minutes once.

## Troubleshooting

| Problem | Fix |
|---|---|
| Anything odd | `windmill doctor`, then `windmill setup` |
| "llama.cpp too old" / unknown model architecture | `windmill setup --update-llama` |
| Out of memory | `windmill tune`, or `windmill settings ctx 32768`, or a smaller model |
| Model server won't start | `windmill server logs` |
| `windmill` not found | open a new terminal (or run `install.ps1` again) |
| `windmill apk` fails | `windmill android status`, then `windmill android setup`. Gradle errors are explained by `windmill debug` |
| App crashes on the phone | `windmill android logs` (or ask in chat: the AI can read the log itself) |
| Wrong folder | `cd` into your project folder first; Windmill works on the folder you're in |
