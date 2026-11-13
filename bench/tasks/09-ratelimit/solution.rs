pub struct RateLimiter { cap: f64, rate: f64, tokens: f64, last: Option<u64> }
impl RateLimiter {
    pub fn new(capacity: u32, refill_per_sec: f64) -> Self { Self { cap: capacity as f64, rate: refill_per_sec, tokens: capacity as f64, last: None } }
    pub fn try_acquire(&mut self, now_ms: u64, tokens: u32) -> bool {
        if let Some(last) = self.last {
            if now_ms > last { self.tokens = (self.tokens + (now_ms - last) as f64 / 1000.0 * self.rate).min(self.cap); }
        }
        self.last = Some(self.last.map_or(now_ms, |l| l.max(now_ms)));
        if tokens as f64 > self.cap || (tokens as f64) > self.tokens + 1e-9 { return false; }
        self.tokens -= tokens as f64;
        true
    }
}
