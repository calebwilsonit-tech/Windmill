//! Table-driven tests: one table of named cases, one loop, a message naming the failing case.
use windmill_testkit::{InvError, Inventory};

/// One row of the table: a sequence of operations and what should be true afterwards.
struct Case {
    name: &'static str,
    ops: &'static [(&'static str, u32)],
    count: u32,
    last_error: Option<InvError>,
}

#[test]
fn add_remove_table() {
    let full = InvError::Full { item: "gem".into(), room: 0 };
    let short = InvError::NotEnough { item: "gem".into(), have: 2 };
    let cases = [
        Case { name: "add", ops: &[("add", 3)], count: 3, last_error: None },
        Case { name: "add then remove all", ops: &[("add", 3), ("remove", 3)], count: 0, last_error: None },
        Case { name: "over the limit", ops: &[("add", 10), ("add", 1)], count: 10, last_error: Some(full) },
        Case { name: "remove too many", ops: &[("add", 2), ("remove", 5)], count: 2, last_error: Some(short) },
        Case { name: "add nothing", ops: &[("add", 0)], count: 0, last_error: None },
    ];
    for c in cases {
        let mut inv = Inventory::new(10);
        let mut last = Ok(());
        for (op, n) in c.ops {
            last = if *op == "add" { inv.add("gem", *n) } else { inv.remove("gem", *n) };
        }
        assert_eq!(inv.count("gem"), c.count, "case '{}'", c.name);
        assert_eq!(last.err(), c.last_error, "case '{}'", c.name);
    }
}
