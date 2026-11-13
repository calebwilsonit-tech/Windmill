use std::hash::Hash;

/// Least-recently-used cache.
pub struct LruCache<K, V> {
    _todo: std::marker::PhantomData<(K, V)>,
}

impl<K: Eq + Hash + Clone, V> LruCache<K, V> {
    /// Creates a cache holding at most `capacity` entries.
    pub fn new(capacity: usize) -> Self {
        todo!()
    }
    /// Looks up a key and marks it most-recently-used.
    pub fn get(&mut self, k: &K) -> Option<&V> {
        todo!()
    }
    /// Inserts or replaces; returns the previous value for the key.
    pub fn put(&mut self, k: K, v: V) -> Option<V> {
        todo!()
    }
    /// Number of entries.
    pub fn len(&self) -> usize {
        todo!()
    }
    /// True if empty.
    pub fn is_empty(&self) -> bool {
        self.len() == 0
    }
    /// Whether the key is present (does not change recency).
    pub fn contains(&self, k: &K) -> bool {
        todo!()
    }
}
