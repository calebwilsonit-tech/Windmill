# Project rules for the AI (Rust)

Fill in the bracketed parts once per project. This file is loaded into every session.

## Stack
- Rust edition: [2021 / 2024], MSRV: [e.g. 1.80]
- Kind: [binary / library / workspace with crates: ...]
- Async runtime: [tokio / none]
- Error handling: [`thiserror` for library errors, `anyhow` in the binary / ...]
- Key crates: [serde, clap, axum, sqlx, ...]

## Commands
- Full check (what "done" means): `windmill check`
  (runs `cargo fmt`, `cargo clippy --all-targets -- -D warnings`, `cargo test`)
- Run: [`cargo run -- <args>`]

## Project memory (auto-maintained)
- `.ai/map.md` is the project map (crates, modules, public API, hub files, recent work). Trust it
  instead of opening files to look around.
- `.ai/journal.md` is the log of past AI tasks.

## Layout
- [`src/main.rs` - CLI entry; `src/lib.rs` - core logic; `src/<module>/`]
- Unit tests in `#[cfg(test)] mod tests` at the bottom of each file
- Integration tests in `tests/`

## Finishing rules (enforced automatically - the check fails if you break them)
- Finish everything you start. No `todo!()`, `unimplemented!()`, `TODO`/`FIXME` comments,
  "for now", "simplified", "rest of the logic" or other placeholder comments, and no stubs.
- Work on ONE thing at a time. Don't start a second change before the first one passes the check.
- Don't wander: only touch files the current task needs. No drive-by refactors.
- Every new behaviour gets a test that would FAIL if the behaviour broke, and that covers edge
  cases and error paths, not just the happy path. Mutation testing checks this.
- Never delete, rename away, or `#[ignore]` an existing test.

## Rules
- A task is NOT done until the full check passes: no clippy warnings, all tests green.
- Write or update tests for every behaviour change, including error cases.
- No `unwrap()`/`expect()` outside tests unless the invariant is truly guaranteed; then
  `expect("why this can't fail")`. Propagate errors with `?`.
- Never use `unsafe` unless asked; if used, add a `// SAFETY:` comment explaining why it's sound.
- Prefer borrowing (`&str`, `&[T]`) over cloning. Don't `.clone()` just to satisfy the borrow checker
  without thinking about ownership first.
- No blocking calls inside async functions; never hold a `std::sync::Mutex` guard across `.await`.
- Don't add dependencies without saying why; prefer std and crates already in Cargo.toml.
- Public items get `///` doc comments.
- Make the smallest change that solves the task. Don't refactor unrelated code.
- Never delete, `#[ignore]`, or weaken a test to make it pass. Never add `#[allow(...)]` to silence clippy
  unless the lint is genuinely wrong here, with a comment saying why.

## Things to never do
- [e.g. never change the public API of `crate::api` without being asked]
- [e.g. never commit `.env` or secrets]
