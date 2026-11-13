use task::*;
#[test]
fn eviction_order() {
    let mut c = LruCache::new(3);
    for i in 0..3 { c.put(i, i * 10); }
    assert_eq!(c.get(&0), Some(&0));
    c.put(3, 30); // evicts 1
    assert!(!c.contains(&1) && c.contains(&0) && c.contains(&2) && c.contains(&3));
    assert_eq!(c.put(2, 99), Some(20)); // replace refreshes 2
    c.put(4, 40); // evicts 0 (LRU now)
    assert!(!c.contains(&0));
    assert_eq!(c.len(), 3);
}
#[test]
fn contains_does_not_refresh() {
    let mut c = LruCache::new(2);
    c.put("a", 1);
    c.put("b", 2);
    assert!(c.contains(&"a"));
    c.put("c", 3);
    assert!(!c.contains(&"a"));
}
#[test]
fn zero_and_one() {
    let mut z = LruCache::new(0);
    assert_eq!(z.put(1, 1), None);
    assert_eq!(z.len(), 0);
    assert_eq!(z.get(&1), None);
    let mut o = LruCache::new(1);
    o.put(1, 1);
    o.put(2, 2);
    assert_eq!(o.get(&1), None);
    assert_eq!(o.get(&2), Some(&2));
}
#[test]
fn performance() {
    let mut c = LruCache::new(1000);
    let t = std::time::Instant::now();
    for i in 0..200_000u64 {
        c.put(i % 5000, i);
        let _ = c.get(&((i * 7) % 5000));
    }
    assert!(t.elapsed().as_secs_f64() < 5.0, "too slow: {:?}", t.elapsed());
    assert_eq!(c.len(), 1000);
}
