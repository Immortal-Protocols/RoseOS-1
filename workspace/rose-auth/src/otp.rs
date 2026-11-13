//! HOTP (RFC 4226) and TOTP (RFC 6238) with the rose-auth-v1 profile: HMAC-SHA-1, 6 digits, 30 s steps, one step of
//! skew either way, and each accepted step usable once per account (replay rejection).

use hmac::{Hmac, Mac};
use sha1::Sha1;
use subtle::ConstantTimeEq;

/// The rose-auth-v1 profile. SHA-1/6/30 is what standard authenticator apps generate; see docs/ROSE-AUTH-V1.md.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Profile {
    pub digits: u32,
    pub step_seconds: u64,
    /// Steps accepted before and after the current one.
    pub skew_steps: u64,
}

pub const V1: Profile = Profile {
    digits: 6,
    step_seconds: 30,
    skew_steps: 1,
};

/// The HOTP value for `counter`, as a number below 10^digits.
pub fn hotp(secret: &[u8], counter: u64, digits: u32) -> u32 {
    let mut mac = Hmac::<Sha1>::new_from_slice(secret).expect("HMAC accepts any key length");
    mac.update(&counter.to_be_bytes());
    let h = mac.finalize().into_bytes();
    let offset = (h[19] & 0x0f) as usize;
    let binary = u32::from_be_bytes([
        h[offset] & 0x7f,
        h[offset + 1],
        h[offset + 2],
        h[offset + 3],
    ]);
    binary % 10u32.pow(digits)
}

/// The code as the user sees it, zero-padded.
pub fn format(value: u32, digits: u32) -> String {
    format!("{:0width$}", value, width = digits as usize)
}

/// The TOTP step containing `unix_seconds`.
pub fn step(profile: Profile, unix_seconds: u64) -> u64 {
    unix_seconds / profile.step_seconds
}

/// The code for the step containing `unix_seconds`.
pub fn totp(secret: &[u8], profile: Profile, unix_seconds: u64) -> String {
    format(
        hotp(secret, step(profile, unix_seconds), profile.digits),
        profile.digits,
    )
}

/// Checks `code` against the steps around `unix_seconds`. Returns the matching step, or None. Only steps after
/// `last_used_step` are eligible, so a code (or an earlier one) is never accepted twice; the caller records the
/// returned step as the new `last_used_step`. Every candidate step is compared in constant time.
pub fn verify(
    secret: &[u8],
    profile: Profile,
    code: &str,
    unix_seconds: u64,
    last_used_step: Option<u64>,
) -> Option<u64> {
    let code = code.trim();
    if code.len() != profile.digits as usize || !code.bytes().all(|b| b.is_ascii_digit()) {
        return None;
    }
    let now = step(profile, unix_seconds);
    let mut found = None;
    for s in now.saturating_sub(profile.skew_steps)..=now + profile.skew_steps {
        let candidate = format(hotp(secret, s, profile.digits), profile.digits);
        let fresh = last_used_step.is_none_or(|last| s > last);
        if bool::from(candidate.as_bytes().ct_eq(code.as_bytes())) && fresh && found.is_none() {
            found = Some(s);
        }
    }
    found
}

/// The `otpauth://` URI an authenticator app enrolls from (issuer and account are percent-encoded; the flow refuses
/// names containing ':', which the label format reserves as their separator).
pub fn otpauth_uri(issuer: &str, account: &str, secret: &[u8], profile: Profile) -> String {
    let label = format!("{}:{}", pct(issuer), pct(account));
    format!(
        "otpauth://totp/{label}?secret={}&issuer={}&algorithm=SHA1&digits={}&period={}",
        crate::base32::encode(secret),
        pct(issuer),
        profile.digits,
        profile.step_seconds
    )
}

fn pct(s: &str) -> String {
    s.bytes()
        .map(|b| match b {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'.' | b'_' | b'~' => {
                (b as char).to_string()
            }
            _ => format!("%{b:02X}"),
        })
        .collect()
}
