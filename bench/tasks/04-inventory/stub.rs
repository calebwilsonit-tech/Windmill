use std::collections::HashMap;

/// A stack of identical items.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ItemStack {
    /// Item id.
    pub id: u32,
    /// How many.
    pub count: u32,
}

/// Inventory errors.
#[derive(Debug, PartialEq, Eq)]
pub enum InvError {
    /// Not enough items to remove.
    NotEnough,
    /// Slot index out of range.
    InvalidSlot,
    /// Slot has no items.
    EmptySlot,
    /// Split amount must be 1..count.
    InvalidAmount,
    /// No empty slot available.
    NoSpace,
}

/// Slot-based inventory.
pub struct Inventory {
    _todo: (),
}

impl Inventory {
    /// `slots` slots; per-item max stack sizes (default 64).
    pub fn new(slots: usize, max_stack: HashMap<u32, u32>) -> Self {
        todo!()
    }
    /// Adds items; returns how many didn't fit.
    pub fn add(&mut self, id: u32, count: u32) -> u32 {
        todo!()
    }
    /// Removes items (last slots first); all-or-nothing.
    pub fn remove(&mut self, id: u32, count: u32) -> Result<(), InvError> {
        todo!()
    }
    /// Total count of an item.
    pub fn count(&self, id: u32) -> u32 {
        todo!()
    }
    /// Contents of a slot.
    pub fn slot(&self, i: usize) -> Option<&ItemStack> {
        todo!()
    }
    /// Moves `amount` from a slot into the first empty slot.
    pub fn split(&mut self, slot: usize, amount: u32) -> Result<(), InvError> {
        todo!()
    }
}
