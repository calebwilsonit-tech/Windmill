# Changelog

## 1.13.0 - 2026-11-14 (minor)
- feat: built-in Tailscale setup is automatic - sign-in and Tailscale's one-click approval page open by themselves, and Windmill connects the moment you approve (no restart)
- (released automatically from the commit messages)

## 1.12.0 - 2026-11-14 (minor)
- feat: Tailscale built in - reach Windmill from your phone anywhere with nothing to install (sign in once); pairing stays home-only; fix: runtime files in bin/ never block updates
- (released automatically from the commit messages)

## 1.11.0 - 2026-11-14 (minor)
- feat: the phone app updates itself from your computer (one tap, only when the app itself changed); message box hint always fits
- (released automatically from the commit messages)

## 1.10.0 - 2026-11-14 (minor)
- feat: Windmill on your phone - Android app that pairs with your computer (Wi-Fi discovery + 6-digit code, or Tailscale), phone-sized interface, Phone page with QR install
- (released automatically from the commit messages)

## 1.9.0 - 2026-11-14 (minor)
- feat: stricter website quality - catches template copy, dead links, alert() stubs, leftover test pages, unused files and theme toggles that do nothing; look picker fits the idea; chat work ticks roadmap milestones
- (released automatically from the commit messages)

## 1.8.2 - 2026-11-14 (fix)
- fix: website projects search the whole library, the same as Rust projects
- (released automatically from the commit messages)

## 1.8.1 - 2026-11-14 (fix)
- fix: llama.cpp install works again on clean PCs (reads GitHub's release page when the API refuses), picks CUDA 12 vs 13 by NVIDIA driver, re-fetches when the GPU build doesn't load
- (released automatically from the commit messages)

## 1.8.0 - 2026-11-14 (minor)
- feat: websites get a real UI stack - Tailwind CSS + daisyUI (no build step) themed from the design brief, like Rust apps get egui/Bevy
- (released automatically from the commit messages)

## 1.7.0 - 2026-11-13 (minor)
- feat: first-start setup check in the app - one popup installs what's missing (llama.cpp, Git, Rust, C++ build tools) silently in the background; sturdier llama.cpp download
- (released automatically from the commit messages)

## 1.6.0 - 2026-11-13 (minor)
- feat: web access - the AI can search the web and read pages, and always opens links you give it (Settings: web_access)
- (released automatically from the commit messages)

## 1.5.1 - 2026-11-13 (fix)
- fix: use the NVIDIA graphics card - prefer (or download) the CUDA build of llama.cpp when the current one is CPU-only; doctor and start-up check it
- (released automatically from the commit messages)

## 1.5.0 - 2026-11-13 (minor)
- feat: builds and new projects run in a chat (plan, thinking, narration, code being written, questions as buttons), message queue, Projects page, Code library page
- (released automatically from the commit messages)

## 1.4.7 - 2026-11-13 (fix)
- fix: build steps started from the app crashed (they ran on pythonw with nowhere to print); Windows release test now runs tasks the way the app does
- (released automatically from the commit messages)

## 1.4.6 - 2026-11-13 (fix)
- fix: builds started from the app lost their output and failed; build stops now show the real reason, and changes made outside the build are saved first
- (released automatically from the commit messages)

## 1.4.5 - 2026-11-13 (fix)
- fix: no flashing git/console windows, including after an in-app update restart (hide child windows whenever there's no console window)
- (released automatically from the commit messages)

## 1.4.4 - 2026-11-13 (fix)
- fix: stop console windows flashing in the desktop app (no more cmd.exe start-up trick; windowless GPU check)
- (released automatically from the commit messages)

## 1.4.3 - 2026-11-13 (fix)
- fix: no more popups when you click things - background tasks show as a quiet indicator in the top bar instead of flash messages and corner cards
- (released automatically from the commit messages)

## 1.4.2 - 2026-11-13 (fix)
- fix: questions pop up as a clean chooser (no terminal text), with a close button; closed questions wait in the corner tray
- (released automatically from the commit messages)

## 1.4.1 - 2026-11-13 (fix)
- fix: in-app updates always restart (no more stuck on 'Updating'), show each step, and the tray icon updates itself
- (released automatically from the commit messages)

## 1.4.0 - 2026-11-13 (minor)
- feat: sign in to GitHub inside Windmill - your repos list, open/connect in one click, GitHub connector uses the sign-in
- (released automatically from the commit messages)

## 1.3.0 - 2026-11-13 (minor)
- feat: background tasks with question popups, new-project form, folder & GitHub menu, tray icon; fix folder picker, flashing windows and the windmill command
- (released automatically from the commit messages)

## 1.2.4 - 2026-11-13 (fix)
- fix: desktop app engine crashed at start under pythonw (no console); now logs to ~/.windmill/logs/app.log and hides helper windows
- (released automatically from the commit messages)

## 1.2.3 - 2026-11-13 (fix)
- fix: the windmill command repairs itself on Windows (no more 'select an app' box); doctor checks it
- (released automatically from the commit messages)

## 1.2.2 - 2026-11-13 (fix)
- fix: desktop app setup retries, shows progress and logs failures; doctor shows app status
- (released automatically from the commit messages)

## 1.2.1 - 2026-11-13 (fix)
- fix: make the desktop app easy to find and always installable
- (released automatically from the commit messages)

## 1.2.0 - 2026-11-13 (minor)
- feat: desktop app, connectors (MCP plugins), skills, and the Rust testkit
- (released automatically from the commit messages)

## 1.1.4 - 2026-11-13 (fix)
- fix: build crash "database is locked", MoE models from Ollama, wrong model tip
- (released automatically from the commit messages)

## 1.1.3 - 2026-11-13 (fix)
- fix: recognise wrong llama.cpp downloads and replace them automatically
- (released automatically from the commit messages)

## 1.1.2 - 2026-11-13 (fix)
- fix: find and repair llama.cpp automatically wherever it was installed
- (released automatically from the commit messages)

## 1.1.1 - 2026-11-13 (fix)
- fix: point updates at the repository's exact name (Windmill)
- (released automatically from the commit messages)

## 1.1.0 - 2026-11-13 (minor)
- Add benchmark and daily model recommendations
- (released automatically from the commit messages)

## 1.0.0 - 2026-11-13 (major)
- First release: chat agent, debugger, project memory, reference library, design engine, Android apps, auto-updates
