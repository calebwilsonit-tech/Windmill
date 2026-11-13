use std::collections::{BTreeMap, HashMap};
use std::hash::Hash;
pub struct LruCache<K, V> { cap: usize, tick: u64, map: HashMap<K, (V, u64)>, order: BTreeMap<u64, K> }
impl<K: Eq + Hash + Clone, V> LruCache<K, V> {
    pub fn new(capacity: usize) -> Self { Self { cap: capacity, tick: 0, map: HashMap::new(), order: BTreeMap::new() } }
    fn touch(&mut self, k: &K) {
        self.tick += 1;
        let t = self.tick;
        if let Some(e) = self.map.get_mut(k) { self.order.remove(&e.1); e.1 = t; self.order.insert(t, k.clone()); }
    }
    pub fn get(&mut self, k: &K) -> Option<&V> {
        if !self.map.contains_key(k) { return None; }
        self.touch(k);
        self.map.get(k).map(|e| &e.0)
    }
    pub fn put(&mut self, k: K, v: V) -> Option<V> {
        if self.cap == 0 { return None; }
        if let Some(e) = self.map.get_mut(&k) {
            let old = std::mem::replace(&mut e.0, v);
            self.touch(&k);
            return Some(old);
        }
        if self.map.len() == self.cap {
            if let Some((&t, _)) = self.order.iter().next() { let old = self.order.remove(&t).unwrap(); self.map.remove(&old); }
        }
        self.tick += 1;
        self.order.insert(self.tick, k.clone());
        self.map.insert(k, (v, self.tick));
        None
    }
    pub fn len(&self) -> usize { self.map.len() }
    pub fn is_empty(&self) -> bool { self.len() == 0 }
    pub fn contains(&self, k: &K) -> bool { self.map.contains_key(k) }
}
