//! Attempt limiting with exponential backoff that decays (decision D6). The flow applies it per client always, and
//! per account only to attempts without a valid one-time code: an attacker who knows an account name can slow down
//! code guessing against it, but cannot keep out the real user, who presents a valid code (no hard lockout).

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Policy {
    /// Failures allowed before any delay.
    pub free_failures: u32,
    /// First delay, doubled on each further failure.
    pub base_delay_seconds: u64,
    pub max_delay_seconds: u64,
    /// Failures are forgotten after this long without another one.
    pub decay_seconds: u64,
}

pub const V1: Policy = Policy {
    free_failures: 5,
    base_delay_seconds: 30,
    max_delay_seconds: 3600,
    decay_seconds: 7200,
};

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct State {
    pub failures: u32,
    pub last_failure: u64,
}

impl Policy {
    /// Seconds until the next attempt is allowed (0 = now).
    pub fn wait(&self, state: State, now: u64) -> u64 {
        let state = self.decayed(state, now);
        if state.failures < self.free_failures {
            return 0;
        }
        let exp = (state.failures - self.free_failures).min(30);
        let delay = self
            .base_delay_seconds
            .saturating_mul(1u64 << exp)
            .min(self.max_delay_seconds);
        (state.last_failure + delay).saturating_sub(now)
    }

    pub fn failed(&self, state: State, now: u64) -> State {
        State {
            failures: self.decayed(state, now).failures.saturating_add(1),
            last_failure: now,
        }
    }

    fn decayed(&self, state: State, now: u64) -> State {
        if now.saturating_sub(state.last_failure) >= self.decay_seconds {
            State::default()
        } else {
            state
        }
    }
}
