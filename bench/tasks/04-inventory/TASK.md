# Game inventory with stacking (medium)
An `Inventory` has a fixed number of slots; each slot is empty or holds an `ItemStack { id, count }`.
Each item id has a max stack size (`max_stack` map; ids not in the map stack to 64).
- `add(id, count) -> u32`: first top up existing stacks of that id (in slot order), then fill empty slots
  (in slot order). Returns how many did NOT fit (0 if all fit).
- `remove(id, count) -> Result<(), InvError>`: removes from the **last** matching slots first; emptied slots
  become empty. If the inventory holds fewer than `count`, return `Err(NotEnough)` and change **nothing**.
- `count(id) -> u32` total of that id. `slot(i) -> Option<&ItemStack>`.
- `split(slot, amount) -> Result<(), InvError>`: move `amount` items from that slot into the first empty slot.
  Errors: `InvalidSlot` (index out of range), `EmptySlot`, `InvalidAmount` (0 or >= the stack's count),
  `NoSpace` (no empty slot). On error nothing changes.
- `count == 0` in add/remove is a no-op success (add returns 0).
