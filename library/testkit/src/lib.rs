//! # windmill-testkit
//! How Windmill builds *testers*, as working Rust you can copy.
//!
//! | Pattern | Where | Use it when |
//! |---|---|---|
//! | Hidden-test grading | [`grader`] | You must judge code you didn't write (AI output, student code, plugins) |
//! | Self-test of a grader | [`grader::selftest`] | Before trusting any grader: reference passes, stub fails |
//! | Table-driven tests | `tests/table.rs` | Many input -> output cases for one function |
//! | Property-based tests | `tests/property.rs` | Rules that must hold for ALL inputs (round-trips, conservation) |
//! | Snapshot tests | `tests/snapshot.rs` | Big outputs (reports, UI text, serialized data) |
//! | CLI tests | `tests/cli.rs` | Testing a program the way a user runs it |
//! | Fuzz / mutation / game / browser E2E | `README.md` | Recipes with the exact commands |
//!
//! The example domain is a tiny game inventory ([`Inventory`]) so the patterns are concrete.

pub mod grader;

use std::collections::BTreeMap;

/// A slot-free inventory: item id -> count, with a per-item stack limit.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct Inventory {
    items: BTreeMap<String, u32>,
    limit: u32,
}

/// Why an inventory operation was refused.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum InvError {
    /// Adding would exceed the per-item limit; nothing was added.
    Full { item: String, room: u32 },
    /// Not enough of the item to remove; nothing was removed.
    NotEnough { item: String, have: u32 },
}

impl Inventory {
    /// Empty inventory where each item can hold up to `limit`.
    pub fn new(limit: u32) -> Self {
        Self { items: BTreeMap::new(), limit }
    }

    /// Adds `n` of `item`, all-or-nothing.
    pub fn add(&mut self, item: &str, n: u32) -> Result<(), InvError> {
        let have = self.count(item);
        let room = self.limit - have;
        if n > room {
            return Err(InvError::Full { item: item.to_string(), room });
        }
        if n > 0 {
            *self.items.entry(item.to_string()).or_insert(0) += n;
        }
        Ok(())
    }

    /// Removes `n` of `item`, all-or-nothing; an item reaching 0 disappears.
    pub fn remove(&mut self, item: &str, n: u32) -> Result<(), InvError> {
        let have = self.count(item);
        if n > have {
            return Err(InvError::NotEnough { item: item.to_string(), have });
        }
        if n == have {
            self.items.remove(item);
        } else if let Some(c) = self.items.get_mut(item) {
            *c -= n;
        }
        Ok(())
    }

    /// How many of `item` there are.
    pub fn count(&self, item: &str) -> u32 {
        self.items.get(item).copied().unwrap_or(0)
    }

    /// Total number of items of every kind.
    pub fn total(&self) -> u32 {
        self.items.values().sum()
    }

    /// Human-readable listing, one `item x count` per line, sorted by name.
    pub fn report(&self) -> String {
        if self.items.is_empty() {
            return "(empty)".to_string();
        }
        self.items.iter().map(|(k, v)| format!("{k} x{v}")).collect::<Vec<_>>().join("\n")
    }

    /// Parses `report()` output back (round-trip partner for property tests).
    pub fn parse_report(text: &str, limit: u32) -> Option<Self> {
        let mut inv = Self::new(limit);
        if text == "(empty)" {
            return Some(inv);
        }
        for line in text.lines() {
            let (name, count) = line.rsplit_once(" x")?;
            inv.add(name, count.parse().ok()?).ok()?;
        }
        Some(inv)
    }
}
