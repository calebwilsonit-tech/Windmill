# LRU cache (medium)
`LruCache<K, V>` holding at most `capacity` entries.
- `new(capacity)`; capacity 0 stores nothing.
- `get(&mut self, k: &K) -> Option<&V>` marks the entry most-recently-used.
- `put(&mut self, k: K, v: V) -> Option<V>`: insert or replace; returns the previous value for that key.
  Replacing marks it most-recently-used. Inserting a new key into a full cache evicts the least-recently-used.
- `len()`, `contains(&K)` (contains does NOT change recency).
`get`/`put` should be O(1) on average (it will be tested with 200,000 operations).
