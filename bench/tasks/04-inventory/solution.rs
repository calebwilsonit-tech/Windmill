use std::collections::HashMap;
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ItemStack { pub id: u32, pub count: u32 }
#[derive(Debug, PartialEq, Eq)]
pub enum InvError { NotEnough, InvalidSlot, EmptySlot, InvalidAmount, NoSpace }
pub struct Inventory { slots: Vec<Option<ItemStack>>, max: HashMap<u32, u32> }
impl Inventory {
    pub fn new(slots: usize, max_stack: HashMap<u32, u32>) -> Self { Self { slots: vec![None; slots], max: max_stack } }
    fn max(&self, id: u32) -> u32 { *self.max.get(&id).unwrap_or(&64) }
    pub fn add(&mut self, id: u32, mut count: u32) -> u32 {
        let m = self.max(id);
        for s in self.slots.iter_mut().flatten() {
            if s.id == id && s.count < m { let t = (m - s.count).min(count); s.count += t; count -= t; }
        }
        for s in self.slots.iter_mut() {
            if count == 0 { break; }
            if s.is_none() { let t = m.min(count); *s = Some(ItemStack { id, count: t }); count -= t; }
        }
        count
    }
    pub fn remove(&mut self, id: u32, mut count: u32) -> Result<(), InvError> {
        if self.count(id) < count { return Err(InvError::NotEnough); }
        for s in self.slots.iter_mut().rev() {
            if count == 0 { break; }
            if let Some(st) = s { if st.id == id { let t = st.count.min(count); st.count -= t; count -= t; if st.count == 0 { *s = None; } } }
        }
        Ok(())
    }
    pub fn count(&self, id: u32) -> u32 { self.slots.iter().flatten().filter(|s| s.id == id).map(|s| s.count).sum() }
    pub fn slot(&self, i: usize) -> Option<&ItemStack> { self.slots.get(i).and_then(|s| s.as_ref()) }
    pub fn split(&mut self, slot: usize, amount: u32) -> Result<(), InvError> {
        let st = self.slots.get(slot).ok_or(InvError::InvalidSlot)?.clone().ok_or(InvError::EmptySlot)?;
        if amount == 0 || amount >= st.count { return Err(InvError::InvalidAmount); }
        let e = self.slots.iter().position(|s| s.is_none()).ok_or(InvError::NoSpace)?;
        self.slots[e] = Some(ItemStack { id: st.id, count: amount });
        if let Some(s) = self.slots[slot].as_mut() { s.count -= amount; }
        Ok(())
    }
}
