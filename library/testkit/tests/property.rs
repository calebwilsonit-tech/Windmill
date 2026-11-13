//! Property-based tests (proptest): state rules that must hold for EVERY input, let the tool search for
//! counter-examples (and shrink them to the smallest failing case).
use proptest::prelude::*;
use windmill_testkit::Inventory;

fn ops() -> impl Strategy<Value = Vec<(bool, u8, u32)>> {
    prop::collection::vec((any::<bool>(), 0u8..4, 0u32..15), 0..60)
}

proptest! {
    /// Conservation: total == sum of successful adds - sum of successful removes.
    #[test]
    fn total_is_conserved(ops in ops()) {
        let mut inv = Inventory::new(20);
        let mut expected: i64 = 0;
        for (add, item, n) in ops {
            let name = format!("item{item}");
            if add { if inv.add(&name, n).is_ok() { expected += n as i64; } }
            else if inv.remove(&name, n).is_ok() { expected -= n as i64; }
        }
        prop_assert_eq!(inv.total() as i64, expected);
    }

    /// Failed operations change nothing (all-or-nothing).
    #[test]
    fn failures_are_atomic(start in 0u32..20, n in 0u32..40) {
        let mut inv = Inventory::new(20);
        inv.add("x", start).unwrap();
        let before = inv.clone();
        if inv.add("x", n).is_err() { prop_assert_eq!(&inv, &before); }
        let before = inv.clone();
        if inv.remove("x", n).is_err() { prop_assert_eq!(&inv, &before); }
    }

    /// Round-trip: parse(report(inv)) == inv.
    #[test]
    fn report_round_trips(ops in ops()) {
        let mut inv = Inventory::new(20);
        for (_, item, n) in ops { let _ = inv.add(&format!("item {item}"), n); }
        prop_assert_eq!(Inventory::parse_report(&inv.report(), 20), Some(inv));
    }
}
