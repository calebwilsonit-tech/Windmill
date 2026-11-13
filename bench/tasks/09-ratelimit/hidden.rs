use task::*;
#[test]
fn fractional_refill_and_cap() {
    let mut r = RateLimiter::new(10, 2.0);
    assert!(r.try_acquire(5_000, 10));
    assert!(!r.try_acquire(5_250, 1)); // 0.5 tokens
    assert!(r.try_acquire(5_500, 1)); // 1.0 token
    assert!(r.try_acquire(100_000, 10)); // capped at 10
    assert!(!r.try_acquire(100_000, 1));
}
#[test]
fn failed_attempt_takes_nothing() {
    let mut r = RateLimiter::new(5, 1.0);
    assert!(r.try_acquire(0, 3));
    assert!(!r.try_acquire(0, 3));
    assert!(r.try_acquire(0, 2));
}
#[test]
fn backwards_time_and_oversize() {
    let mut r = RateLimiter::new(3, 1.0);
    assert!(r.try_acquire(10_000, 3));
    assert!(!r.try_acquire(5_000, 1)); // clock went back: no refill, no panic
    assert!(r.try_acquire(11_000, 1)); // 1s after the last *latest* time
    assert!(!r.try_acquire(1_000_000, 4)); // more than capacity
    let mut z = RateLimiter::new(0, 5.0);
    assert!(!z.try_acquire(0, 1));
    assert!(z.try_acquire(0, 0));
}
