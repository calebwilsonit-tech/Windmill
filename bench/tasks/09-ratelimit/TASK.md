# Token-bucket rate limiter (medium)
`RateLimiter::new(capacity: u32, refill_per_sec: f64)` starts **full**. `try_acquire(&mut self, now_ms: u64,
tokens: u32) -> bool`: first refill by `elapsed_seconds * refill_per_sec` since the previous call (continuous,
fractional tokens allowed, never above capacity), then if at least `tokens` are available take them and return
true, else return false and take nothing. Time going backwards counts as zero elapsed (and must not panic).
Asking for more than `capacity` always returns false. The first call has nothing elapsed before it.
