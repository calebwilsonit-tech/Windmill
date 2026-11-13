//! Snapshot tests (insta): the first run records the output in tests/snapshots/; later runs fail if it
//! changes. Review changes with `cargo insta review` (install: cargo install cargo-insta).
use windmill_testkit::Inventory;

#[test]
fn report_snapshot() {
    let mut inv = Inventory::new(99);
    inv.add("sword", 1).unwrap();
    inv.add("arrow", 40).unwrap();
    inv.add("potion", 3).unwrap();
    insta::assert_snapshot!(inv.report(), @r"
    arrow x40
    potion x3
    sword x1
    ");
}
