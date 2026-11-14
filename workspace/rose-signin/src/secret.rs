//! Secret values that never print, random generation, and sealing of authenticator secrets at rest.

use std::fmt;

/// Bytes that must not reach logs: `Debug` shows `<redacted>`.
#[derive(Clone, PartialEq, Eq)]
pub struct Secret(Vec<u8>);

impl Secret {
    pub fn new(bytes: Vec<u8>) -> Self {
        Secret(bytes)
    }
    /// `len` bytes from the operating system's CSPRNG.
    pub fn random(len: usize) -> Self {
        let mut b = vec![0u8; len];
        getrandom::getrandom(&mut b).expect("the OS random source is available");
        Secret(b)
    }
    pub fn expose(&self) -> &[u8] {
        &self.0
    }
}

impl fmt::Debug for Secret {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str("<redacted>")
    }
}

/// Seals authenticator secrets before they are stored, and opens them to verify a code. Authenticator secrets must be
/// recoverable server-side (they are symmetric), so they are encrypted at rest under a key that lives outside the
/// database. rose-signin-v1 ships no production sealer: the key's home on a Chariot is an open infrastructure decision
/// (docs/ROSE-SIGNIN-V1.md, decision D9), and an `Authenticator` cannot be built without a sealer.
pub trait Sealer {
    fn seal(&self, plaintext: &Secret) -> Vec<u8>;
    fn open(&self, sealed: &[u8]) -> Option<Secret>;
}
