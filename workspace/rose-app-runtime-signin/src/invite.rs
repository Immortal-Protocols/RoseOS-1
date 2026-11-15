//! The creator's invite (rose-signin D7): the owner tooling a default `invite` app needs to admit anyone.
//!
//! [`invite`] opens the app's existing sign-in store under `DATA_DIR` and calls rose-signin's `create_invite`: a single-use
//! token, valid 7 days, that admits exactly the account it names. It needs no sealing key (an invite is only its
//! hash and expiry in the store), so none is read. Each store call is one transaction and the store waits for a busy
//! database (5 s), so it runs safely beside a serving server. There is no route: a generated server exposes it only
//! as its `invite <account>` command, run by the creator.
//!
//! The token is a credential. It is returned once and appears in no error, log or `Debug`; whoever runs the
//! command must hand its output only to the person who ran it, never to the service's log capture (§7.7 item 8).

use rose_signin::flow::{self, EnrollError};
use rose_signin::secret::AeadSealer;
use rose_signin::store::sqlite::SqliteStore;
use rusqlite::{Connection, OpenFlags};

use crate::start::{AUTH_STORE, Config, Restore, restore_state};

/// Why no invite was issued. It names the problem, never the account or a token. The words are what the generated
/// server's `invite` command prints (App Core maps each to an exit code).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum InviteError {
    /// The account is not a valid rose-signin name (empty, over 128 bytes, or with `:` or a control character).
    InvalidName,
    /// The account is already enrolled: it needs no invite.
    AlreadyEnrolled,
    /// `DATA_DIR` holds no sign-in store yet: the server has never started there, and none is created here.
    NotFound,
    /// A restore marker is present (or its lookup failed): the store may be about to be replaced, and the server
    /// would not start either.
    RestorePending,
    /// The store could not be opened or written, or the build's app name, issuer or `DATA_DIR` is not valid.
    StoreUnavailable,
}

impl InviteError {
    pub fn word(self) -> &'static str {
        match self {
            InviteError::InvalidName => "invalid_name",
            InviteError::AlreadyEnrolled => "already_enrolled",
            InviteError::NotFound => "not_found",
            InviteError::RestorePending => "restore_pending",
            InviteError::StoreUnavailable => "store_unavailable",
        }
    }
}

impl std::fmt::Display for InviteError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "no invite: {}", self.word())
    }
}

impl std::error::Error for InviteError {}

/// A single-use invite for `account` in the app `config` describes, as the text the invitee enrolls with. It reads
/// no keyring (`CHARIOT_SEALING_KEY_FILE` is never consulted), refuses while a restore is indicated, and opens only an
/// existing store. Admission is not consulted: an invite under open sign-up is harmless and simply unused.
pub fn invite(config: &Config, account: &str, now: u64) -> Result<String, InviteError> {
    if !flow::valid_name(account) {
        return Err(InviteError::InvalidName);
    }
    if !flow::valid_name(&config.app)
        || !flow::valid_name(&config.issuer)
        || !config.data_dir.is_absolute()
        || !config.restore_marker.is_absolute()
    {
        return Err(InviteError::StoreUnavailable);
    }
    // The start-up rule: any marker, or a failed lookup, means the store may be replaced.
    if restore_state(&config.restore_marker) != Restore::NotIndicated {
        return Err(InviteError::RestorePending);
    }
    let path = config.data_dir.join(AUTH_STORE);
    // Never create the store: an absent one is `NotFound`.
    let conn = match Connection::open_with_flags(
        &path,
        OpenFlags::SQLITE_OPEN_READ_WRITE | OpenFlags::SQLITE_OPEN_NO_MUTEX,
    ) {
        Ok(conn) => conn,
        Err(_) if !path.exists() => return Err(InviteError::NotFound),
        Err(_) => return Err(InviteError::StoreUnavailable),
    };
    // rose-signin's store sets its own busy timeout (5 s) first, so a serving server's write is waited for.
    let store = SqliteStore::new(conn).map_err(|_| InviteError::StoreUnavailable)?;
    // No key: create_invite writes only the invite's hash and expiry.
    let sealer = AeadSealer::new(None, Vec::new()).map_err(|_| InviteError::StoreUnavailable)?;
    let mut auth =
        flow::Authenticator::new(store, sealer, &config.issuer).map_err(|_| InviteError::StoreUnavailable)?;
    match auth.create_invite(&config.app, account, now) {
        Ok(token) => String::from_utf8(token.expose().to_vec()).map_err(|_| InviteError::StoreUnavailable),
        Err(EnrollError::AlreadyEnrolled) => Err(InviteError::AlreadyEnrolled),
        Err(EnrollError::InvalidName) => Err(InviteError::InvalidName),
        Err(_) => Err(InviteError::StoreUnavailable),
    }
}
