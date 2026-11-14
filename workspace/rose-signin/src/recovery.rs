//! One-time recovery codes: generated at enrollment, shown once, and consumed on use. Each carries 80 random bits and
//! is stored as SHA-256 over (app, account, code), so a leaked table cannot be attacked across accounts at once and a
//! single code is beyond brute force. What a recovery code may reset is decision D5; this module only issues and
//! consumes codes.

use sha2::{Digest, Sha256};
use subtle::ConstantTimeEq;

use crate::secret::Secret;

pub const COUNT: usize = 10;

/// `COUNT` codes for (app, account) as shown to the user (`XXXX-XXXX-XXXX-XXXX`, base32) and their stored hashes.
pub fn generate(app: &str, account: &str) -> (Vec<String>, Vec<[u8; 32]>) {
    (0..COUNT)
        .map(|_| {
            let raw = crate::base32::encode(Secret::random(10).expose());
            let code = format!(
                "{}-{}-{}-{}",
                &raw[0..4],
                &raw[4..8],
                &raw[8..12],
                &raw[12..16]
            );
            let hash = hash(app, account, &code).expect("well-formed");
            (code, hash)
        })
        .unzip()
}

/// The stored hash of a typed code for (app, account) (case, spaces and dashes ignored), or None if it cannot be one.
pub fn hash(app: &str, account: &str, typed: &str) -> Option<[u8; 32]> {
    let norm: String = typed
        .chars()
        .filter(|c| !matches!(c, '-' | ' '))
        .collect::<String>()
        .to_ascii_uppercase();
    if norm.len() != 16
        || !norm
            .bytes()
            .all(|b| b.is_ascii_uppercase() || (b'2'..=b'7').contains(&b))
    {
        return None;
    }
    let mut h = Sha256::new();
    for part in [app.as_bytes(), account.as_bytes(), norm.as_bytes()] {
        h.update((part.len() as u64).to_be_bytes());
        h.update(part);
    }
    Some(h.finalize().into())
}

/// Removes and reports the matching unused code of (app, account). Every stored hash is compared.
pub fn consume(stored: &mut Vec<[u8; 32]>, app: &str, account: &str, typed: &str) -> bool {
    let Some(h) = hash(app, account, typed) else {
        return false;
    };
    let mut at = None;
    for (i, s) in stored.iter().enumerate() {
        if bool::from(s.ct_eq(&h)) {
            at = Some(i);
        }
    }
    match at {
        Some(i) => {
            stored.swap_remove(i);
            true
        }
        None => false,
    }
}
