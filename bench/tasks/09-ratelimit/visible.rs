use task::*;
#[test]
fn example() {
    let mut r = RateLimiter::new(2, 1.0);
    assert!(r.try_acquire(0, 2));
    assert!(!r.try_acquire(0, 1));
    assert!(r.try_acquire(1000, 1));
}
