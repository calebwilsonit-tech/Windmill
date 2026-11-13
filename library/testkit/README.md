# windmill-testkit - how Windmill builds testers

Working Rust for every testing pattern Windmill uses. Copy the pattern that fits; every file is small,
commented, and passes `cargo test` and `cargo clippy -- -D warnings`.

| Pattern | File | Use when |
|---|---|---|
| **Hidden-test grading** | `src/grader.rs` | Judging code you didn't write (AI output, submissions, plugins) |
| **Self-test the grader** | `grader::selftest` | Always, before trusting a grader: reference passes, stub fails |
| **Table-driven** | `tests/table.rs` | Many input -> output cases for one function |
| **Property-based** (proptest) | `tests/property.rs` | Rules for ALL inputs: conservation, all-or-nothing, round-trips |
| **Snapshot** (insta) | `tests/snapshot.rs` | Large outputs: reports, UI text, serialized data |
| **CLI** (assert_cmd) | `tests/cli.rs` | Testing a program exactly the way a user runs it |

## Rules Windmill follows
1. **Test behaviour, not implementation.** Tests call the public API only.
2. **Every bug gets a test that fails without the fix** (write it first, see it fail, then fix).
3. **Graders are tested too** (`selftest`). A grader nobody checked is a random number generator.
4. **Hidden tests for anything gameable.** The code being judged sees examples, never the real tests.
5. **Name the failing case** in every assertion message - "case 'remove too many'", not "assertion failed".
6. **No sleeps in tests.** Inject time (`fn tick(now_ms)`), inject randomness (seeded RNG).

## Recipes (exact commands)

**Mutation testing** - proves your tests would catch bugs: it changes your code (`>` to `>=`, return
values...) and checks some test fails.
```
cargo install cargo-mutants --locked
cargo mutants --in-diff changes.diff      # only the code you changed (what `windmill plan` does)
```
Survivors = untested behaviour -> add a test that kills each one.

**Fuzzing** - throws millions of random inputs at a parser until it panics.
```
cargo install cargo-fuzz        # needs nightly: rustup toolchain install nightly
cargo +nightly fuzz init
cargo +nightly fuzz add parse_report
#   fuzz/fuzz_targets/parse_report.rs:  fuzz_target!(|s: &str| { let _ = Inventory::parse_report(s, 99); });
cargo +nightly fuzz run parse_report -- -max_total_time=60
```

**Bevy game logic, headless** (no window, runs in CI):
```rust
#[test]
fn player_takes_damage() {
    let mut app = App::new();
    app.add_plugins(MinimalPlugins).add_systems(Update, damage_system);
    let player = app.world_mut().spawn((Health(10), Hit(3))).id();
    app.update();                                   // run one frame
    assert_eq!(app.world().get::<Health>(player).unwrap().0, 7);
}
```
Keep game rules in plain functions/systems you can drive like this; keep rendering out of them.

**Browser end-to-end** (websites and web apps):
- From chat: connect the **Browser testing (E2E) - Playwright** connector (`windmill connect`). The AI then
  opens your site in a real browser, clicks through it, and reads the page and console errors.
- In Rust tests: `fantoccini` or `thirtyfour` (WebDriver) or `chromiumoxide` (Chrome DevTools) drive a browser
  from `cargo test`.

**Snapshots**: first run records them; `cargo insta review` to accept intentional changes.

**Benchmarks** (speed regressions): `criterion` in `benches/`, run with `cargo bench`.

## Grading AI or student code - the whole flow
```
tasks/01-roman/
  TASK.md     what to build              visible.rs  examples the coder sees
  stub.rs     signatures to fill in      hidden.rs   the real tests (never shown)
  solution.rs reference solution
```
1. `selftest` every task (reference passes, stub fails).
2. Give the coder TASK.md + stub + visible tests.
3. `grade(task, their_lib_rs, scratch)` -> `Score { passed, total, compile_error }`.
Windmill's own `windmill benchmark` is exactly this, with 10 tasks in `bench/tasks/`.
