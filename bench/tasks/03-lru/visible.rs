use task::*;
#[test]
fn basic() {
    let mut c = LruCache::new(2);
    c.put(1, "a");
    c.put(2, "b");
    assert_eq!(c.get(&1), Some(&"a"));
    c.put(3, "c"); // evicts 2
    assert!(!c.contains(&2));
}
