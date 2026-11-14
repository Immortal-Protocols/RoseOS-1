//! The knowledge factor (PIN/passphrase), verified server-side against an Argon2id hash (decision D1's recommended
//! option; docs/ROSE-AUTH-V1.md). The plaintext is never stored, logged or returned. Clients send it only inside the
//! TLS sign-in request; nothing on the device stores or autofills it.

use argon2::password_hash::{PasswordHash, PasswordHasher, PasswordVerifier, SaltString};
use argon2::{Algorithm, Argon2, Params, Version};

/// Minimum length in Unicode scalar values. 8 follows NIST SP 800-63B for a memorized secret used with a second
/// factor; the final value is decision D2.
pub const MIN_LEN: usize = 8;
/// Upper bound, so a request cannot make the server hash megabytes.
pub const MAX_LEN: usize = 128;

fn argon2() -> Argon2<'static> {
    // OWASP's Argon2id baseline: 19 MiB, 2 passes, 1 lane.
    Argon2::new(
        Algorithm::Argon2id,
        Version::V0x13,
        Params::new(19 * 1024, 2, 1, None).expect("valid parameters"),
    )
}

#[derive(Debug, PartialEq, Eq)]
pub enum Rejected {
    TooShort,
    TooLong,
}

/// Checks the length policy (on the client too, so users learn early; the server is authoritative).
pub fn acceptable(passphrase: &str) -> Result<(), Rejected> {
    let n = passphrase.chars().count();
    if n < MIN_LEN {
        Err(Rejected::TooShort)
    } else if n > MAX_LEN {
        Err(Rejected::TooLong)
    } else {
        Ok(())
    }
}

/// A PHC-format Argon2id hash with a fresh random salt.
pub fn hash(passphrase: &str) -> Result<String, Rejected> {
    acceptable(passphrase)?;
    let salt =
        SaltString::encode_b64(crate::secret::Secret::random(16).expose()).expect("16-byte salt");
    Ok(argon2()
        .hash_password(passphrase.as_bytes(), &salt)
        .expect("hashing succeeds")
        .to_string())
}

/// Whether `passphrase` matches `phc`. Over-long input is refused before hashing.
pub fn verify(passphrase: &str, phc: &str) -> bool {
    if passphrase.chars().count() > MAX_LEN {
        return false;
    }
    match PasswordHash::new(phc) {
        Ok(parsed) => argon2()
            .verify_password(passphrase.as_bytes(), &parsed)
            .is_ok(),
        Err(_) => false,
    }
}

/// A valid hash of a random value, verified against when an account does not exist, so an unknown account costs the
/// same time as a wrong passphrase.
pub fn decoy() -> String {
    hash(&crate::base32::encode(
        crate::secret::Secret::random(20).expose(),
    ))
    .expect("decoy hash")
}
