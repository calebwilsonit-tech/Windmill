use std::collections::HashMap;
use task::*;
fn inv(n: usize) -> Inventory { Inventory::new(n, HashMap::from([(1, 10), (2, 1)])) }
#[test]
fn tops_up_before_new_slots() {
    let mut i = inv(4);
    i.add(1, 7);
    i.add(3, 5); // default max 64
    i.add(1, 6); // tops up slot 0 to 10, then 3 into slot 2
    assert_eq!(i.slot(0), Some(&ItemStack { id: 1, count: 10 }));
    assert_eq!(i.slot(1), Some(&ItemStack { id: 3, count: 5 }));
    assert_eq!(i.slot(2), Some(&ItemStack { id: 1, count: 3 }));
}
#[test]
fn overflow_returns_leftover() {
    let mut i = inv(2);
    assert_eq!(i.add(2, 5), 3); // max stack 1, 2 slots
    assert_eq!(i.count(2), 2);
    assert_eq!(i.add(1, 1), 1);
    assert_eq!(i.add(1, 0), 0);
}
#[test]
fn remove_last_first_and_atomic() {
    let mut i = inv(3);
    i.add(1, 25); // 10,10,5
    assert_eq!(i.remove(1, 7), Ok(())); // slot2: 5 -> gone, slot1: 10 -> 8
    assert_eq!(i.slot(2), None);
    assert_eq!(i.slot(1), Some(&ItemStack { id: 1, count: 8 }));
    assert_eq!(i.remove(1, 100), Err(InvError::NotEnough));
    assert_eq!(i.count(1), 18);
    assert_eq!(i.remove(9, 0), Ok(()));
}
#[test]
fn split_rules() {
    let mut i = inv(3);
    i.add(1, 10);
    assert_eq!(i.split(0, 4), Ok(()));
    assert_eq!(i.slot(0), Some(&ItemStack { id: 1, count: 6 }));
    assert_eq!(i.slot(1), Some(&ItemStack { id: 1, count: 4 }));
    assert_eq!(i.split(0, 6), Err(InvError::InvalidAmount));
    assert_eq!(i.split(0, 0), Err(InvError::InvalidAmount));
    assert_eq!(i.split(2, 1), Err(InvError::EmptySlot));
    assert_eq!(i.split(9, 1), Err(InvError::InvalidSlot));
    assert_eq!(i.split(0, 1), Ok(()));
    assert_eq!(i.split(0, 1), Err(InvError::NoSpace));
    assert_eq!(i.count(1), 10);
    assert_eq!(i.slot(5), None);
}
