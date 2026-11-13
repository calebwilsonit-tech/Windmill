---
name: testing
description: How to build good tests and testers (graders, property/snapshot/CLI tests, fuzzing, mutation testing, game and browser E2E). Use whenever writing tests, a test harness, a grader, or when asked to make code "robust" or "verified".
---
# Building tests and testers the Windmill way

Look up the working Rust patterns with references("windmill testkit <pattern>") - the `windmill-testkit`
crate in the library has a small, clippy-clean example of each.

Choose the pattern:
- Many input/output cases for one function -> table-driven test with named cases.
- A rule that must hold for all inputs (round-trip, conservation, all-or-nothing on failure) -> proptest.
- Large output (report, UI text, JSON) -> insta snapshot.
- A command-line program -> assert_cmd: check stdout, stderr and exit code.
- Judging code someone else wrote -> hidden-test grader + selftest (reference passes, stub fails) FIRST.
- Parsers / anything reading untrusted input -> also fuzz (cargo-fuzz) for crashes.
- After writing tests for new code -> mutation testing (cargo mutants --in-diff) to prove they catch bugs.
- Bevy game rules -> headless App with MinimalPlugins, call app.update(), assert on components.
- Websites/web apps -> the Playwright connector (if connected) to click through the real page; check the
  browser console for errors; test every state (loading, empty, error, success).

Rules: test public behaviour; every bug fix comes with a test that fails without it; name the failing case in
assertion messages; no sleeps - inject time and seeded randomness; never weaken a test to make it pass.
