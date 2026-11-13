use std::collections::HashMap;
use task::*;
#[test]
fn stacking() {
    let mut inv = Inventory::new(3, HashMap::from([(1, 10)]));
    assert_eq!(inv.add(1, 15), 0);
    assert_eq!(inv.slot(0), Some(&ItemStack { id: 1, count: 10 }));
    assert_eq!(inv.slot(1), Some(&ItemStack { id: 1, count: 5 }));
    assert_eq!(inv.count(1), 15);
}
