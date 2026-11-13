/// Token-bucket rate limiter with an injectable clock.
pub struct RateLimiter {
    _todo: (),
}

impl RateLimiter {
    /// Creates a full bucket.
    pub fn new(capacity: u32, refill_per_sec: f64) -> Self {
        todo!()
    }
    /// Tries to take `tokens` at time `now_ms`.
    pub fn try_acquire(&mut self, now_ms: u64, tokens: u32) -> bool {
        todo!()
    }
}
