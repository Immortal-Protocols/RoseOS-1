//! Secret values that never print, random generation, and sealing of authenticator secrets at rest.
//!
//! Authenticator secrets are symmetric, so the server must be able to recover them: they are encrypted at rest, never
//! hashed. A `Sealer` binds each ciphertext to its (app, account, purpose) context, so a sealed secret copied into
//! another account's row does not open there, and reports two distinct failures: the key is unavailable (an
//! infrastructure fault: answer "unavailable", write nothing, count nothing) or the ciphertext fails its integrity
//! check (tampering or corruption: a generic sign-in failure plus an alert). There is never a fallback to plaintext
//! or to a freshly generated key. Where the key lives (Secret Manager on a Chariot, decision D9) belongs to the
//! host; this crate only defines the format and a reference sealer that is handed its keys.

use std::collections::HashMap;
use std::fmt;

use aes_gcm::aead::{Aead, KeyInit, Payload};
use aes_gcm::{Aes256Gcm, Nonce};

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

/// The purpose of an authenticator secret's ciphertext.
pub const OTP_SECRET: &str = "otp-secret/v1";

/// What a ciphertext belongs to. It is authenticated with the ciphertext and never stored in it.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct SealContext<'a> {
    pub app: &'a str,
    pub account: &'a str,
    pub purpose: &'a str,
}

impl SealContext<'_> {
    /// Length-prefixed (app, account, purpose), so no two contexts share an encoding.
    pub fn encode(&self) -> Vec<u8> {
        let mut out = Vec::new();
        for part in [self.app, self.account, self.purpose] {
            out.extend((part.len() as u64).to_be_bytes());
            out.extend(part.as_bytes());
        }
        out
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum SealError {
    /// No usable current key (missing or not loaded). The operation must fail closed and write nothing.
    KeyUnavailable,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum OpenError {
    /// The key version that sealed this ciphertext is not loaded (missing, or not retained).
    KeyUnavailable,
    /// Malformed, tampered, or sealed for another context.
    Integrity,
}

pub trait Sealer {
    /// Whether the current key is loaded. A server reports itself not ready (and serves no sign-in) until it is.
    fn ready(&self) -> bool;
    fn seal(&self, ctx: &SealContext, plaintext: &Secret) -> Result<Vec<u8>, SealError>;
    fn open(&self, ctx: &SealContext, sealed: &[u8]) -> Result<Secret, OpenError>;
    /// Whether a ciphertext that opened was sealed under an older key version and should be re-sealed.
    fn stale(&self, _sealed: &[u8]) -> bool {
        false
    }
}

const ENVELOPE_V1: u8 = 1;
const HEADER: usize = 1 + 4 + 12;

/// The reference sealer: AES-256-GCM, envelope `01 | key version (u32 BE) | 96-bit nonce | ciphertext+tag`, with
/// the envelope header and the `SealContext` as associated data. It is handed 32-byte keys by version and which
/// version seals new data; older versions stay available to open what they sealed. It never fetches or creates keys.
pub struct AeadSealer {
    current: Option<u32>,
    keys: HashMap<u32, Aes256Gcm>,
}

#[derive(Debug, PartialEq, Eq)]
pub struct BadKey;

impl AeadSealer {
    /// `keys` are (version, 32-byte key); `current` seals new data and must be among them to be ready.
    pub fn new(current: Option<u32>, keys: Vec<(u32, Secret)>) -> Result<Self, BadKey> {
        let mut map = HashMap::new();
        for (version, key) in keys {
            let cipher = Aes256Gcm::new_from_slice(key.expose()).map_err(|_| BadKey)?;
            map.insert(version, cipher);
        }
        Ok(AeadSealer { current, keys: map })
    }

    fn aad(header: &[u8], ctx: &SealContext) -> Vec<u8> {
        let mut aad = b"rose-signin-v1 seal\0".to_vec();
        aad.extend(header);
        aad.extend(ctx.encode());
        aad
    }

    fn version(sealed: &[u8]) -> Option<u32> {
        (sealed.len() >= HEADER && sealed[0] == ENVELOPE_V1)
            .then(|| u32::from_be_bytes([sealed[1], sealed[2], sealed[3], sealed[4]]))
    }
}

impl Sealer for AeadSealer {
    fn ready(&self) -> bool {
        self.current.is_some_and(|v| self.keys.contains_key(&v))
    }

    fn seal(&self, ctx: &SealContext, plaintext: &Secret) -> Result<Vec<u8>, SealError> {
        let version = self.current.ok_or(SealError::KeyUnavailable)?;
        let cipher = self.keys.get(&version).ok_or(SealError::KeyUnavailable)?;
        let nonce = Secret::random(12);
        let mut out = vec![ENVELOPE_V1];
        out.extend(version.to_be_bytes());
        out.extend(nonce.expose());
        let aad = Self::aad(&out, ctx);
        let ct = cipher
            .encrypt(
                Nonce::from_slice(nonce.expose()),
                Payload {
                    msg: plaintext.expose(),
                    aad: &aad,
                },
            )
            .map_err(|_| SealError::KeyUnavailable)?;
        out.extend(ct);
        Ok(out)
    }

    fn open(&self, ctx: &SealContext, sealed: &[u8]) -> Result<Secret, OpenError> {
        let version = Self::version(sealed).ok_or(OpenError::Integrity)?;
        let cipher = self.keys.get(&version).ok_or(OpenError::KeyUnavailable)?;
        let aad = Self::aad(&sealed[..HEADER], ctx);
        cipher
            .decrypt(
                Nonce::from_slice(&sealed[5..HEADER]),
                Payload {
                    msg: &sealed[HEADER..],
                    aad: &aad,
                },
            )
            .map(Secret::new)
            .map_err(|_| OpenError::Integrity)
    }

    fn stale(&self, sealed: &[u8]) -> bool {
        Self::version(sealed).is_some_and(|v| Some(v) != self.current)
    }
}
