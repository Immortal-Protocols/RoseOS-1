//! Sessions: an opaque random bearer token shown once to the client; the server stores only its SHA-256, with
//! absolute and idle expiry and revocation (one session or every session of an account).

use sha2::{Digest, Sha256};

use crate::secret::Secret;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Policy {
    pub absolute_seconds: u64,
    pub idle_seconds: u64,
}

/// 30 days absolute, 7 days idle (decision D10 sets the final values).
pub const V1: Policy = Policy {
    absolute_seconds: 30 * 86400,
    idle_seconds: 7 * 86400,
};

/// Who a valid session belongs to. Every data query a generated server makes is scoped by both fields.
#[derive(Clone, Debug, PartialEq, Eq, Hash)]
pub struct Principal {
    pub app: String,
    pub account: String,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Record {
    pub principal: Principal,
    pub created: u64,
    pub last_seen: u64,
    pub revoked: bool,
}

impl Policy {
    pub fn live(&self, r: &Record, now: u64) -> bool {
        !r.revoked
            && now < r.created + self.absolute_seconds
            && now < r.last_seen + self.idle_seconds
    }
}

/// A new token (52 base32 characters, 256 bits) and the key it is stored under.
pub fn mint() -> (Secret, [u8; 32]) {
    let token = Secret::new(crate::base32::encode(Secret::random(32).expose()).into_bytes());
    let key = key(token.expose());
    (token, key)
}

/// The storage key for a presented token.
pub fn key(token: &[u8]) -> [u8; 32] {
    Sha256::digest(token).into()
}
