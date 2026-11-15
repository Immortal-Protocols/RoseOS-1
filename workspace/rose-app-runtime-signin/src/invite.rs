//! The creator's invite (rose-signin D7): the owner tooling a default `invite` app needs to admit anyone.
//!
//! [`invite`] opens the app's sign-in store under `DATA_DIR` and calls rose-signin's `create_invite`: a single-use
//! token, valid 7 days, that admits exactly the account it names. It needs no sealing key (an invite is only its
//! hash and expiry in the store), so none is read. Each store call is one transaction and the store waits for a busy
//! database (5 s), so it runs safely beside a serving server. There is no route: a generated server exposes it only
//! as its `invite <account>` command, run by the creator.
//!
//! The token is a credential. It is returned once and appears in no error, log or `Debug`; whoever runs the
//! command must hand its output only to the person who ran it, never to the service's log capture (§7.7 item 8).

use rose_signin::flow::{self, EnrollError};
use rose_signin::secret::AeadSealer;

use crate::start::{Config, auth_store};

/// Why no invite was issued. It names the problem, never the account or a token.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum InviteError {
    /// The account is not a valid rose-signin name (empty, over 128 bytes, or with `:` or a control character).
    InvalidName,
    /// The app name or issuer in the build is not valid.
    InvalidApp,
    /// The account is already enrolled: it needs no invite.
    AlreadyEnrolled,
    /// The sign-in store could not be opened or written.
    Unavailable,
}

impl InviteError {
    pub fn word(self) -> &'static str {
        match self {
            InviteError::InvalidName => "invalid_name",
            InviteError::InvalidApp => "invalid_app",
            InviteError::AlreadyEnrolled => "already_enrolled",
            InviteError::Unavailable => "unavailable",
        }
    }
}

impl std::fmt::Display for InviteError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "no invite: {}", self.word())
    }
}

impl std::error::Error for InviteError {}

/// A single-use invite for `account` in the app `config` describes, as the text the invitee enrolls with. Admission
/// is not consulted: an invite under open sign-up is harmless and simply unused.
pub fn invite(config: &Config, account: &str, now: u64) -> Result<String, InviteError> {
    if !flow::valid_name(account) {
        return Err(InviteError::InvalidName);
    }
    if !flow::valid_name(&config.app) || !flow::valid_name(&config.issuer) || !config.data_dir.is_absolute() {
        return Err(InviteError::InvalidApp);
    }
    let store = auth_store(&config.data_dir).map_err(|_| InviteError::Unavailable)?;
    // No key: create_invite writes only the invite's hash and expiry.
    let sealer = AeadSealer::new(None, Vec::new()).map_err(|_| InviteError::Unavailable)?;
    let mut auth = flow::Authenticator::new(store, sealer, &config.issuer).map_err(|_| InviteError::InvalidApp)?;
    match auth.create_invite(&config.app, account, now) {
        Ok(token) => String::from_utf8(token.expose().to_vec()).map_err(|_| InviteError::Unavailable),
        Err(EnrollError::AlreadyEnrolled) => Err(InviteError::AlreadyEnrolled),
        Err(EnrollError::InvalidName) => Err(InviteError::InvalidName),
        Err(_) => Err(InviteError::Unavailable),
    }
}
