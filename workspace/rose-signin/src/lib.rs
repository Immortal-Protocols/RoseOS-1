//! rose-signin-v1 (docs/ROSE-SIGNIN-V1.md): authenticator-style one-time codes (RFC 6238) plus a server-verified
//! PIN/passphrase for servers that Rose generates, with sessions, rate limits, recovery codes and per-app,
//! per-account isolation. Pure library: no network, no clock and no storage of its own (all injected).

pub mod base32;
pub mod flow;
pub mod knowledge;
pub mod otp;
pub mod rate;
pub mod recovery;
pub mod secret;
pub mod session;
pub mod store;
