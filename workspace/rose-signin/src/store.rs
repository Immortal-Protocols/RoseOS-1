//! What the flow persists, behind a transactional trait. Every `Authenticator` operation runs as exactly one
//! transaction (`begin` .. `commit`, or `rollback` on any failure), so two concurrent requests can never both spend
//! one code and a sign-in can never undo a concurrent revocation. Generated servers use the reference SQLite store
//! (`sqlite`, feature "sqlite"; schema in docs/ROSE-SIGNIN-V1.md); tests also use `Memory`. Every account key is
//! (app, account): accounts of different apps, or different accounts of one app, never share a row.

use std::collections::HashMap;
use std::fmt;

use crate::rate;
use crate::session;

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Account {
    /// Argon2id PHC hash of the knowledge factor.
    pub knowledge_hash: String,
    /// The authenticator secret, sealed for (app, account, OTP_SECRET).
    pub sealed_otp_secret: Vec<u8>,
    /// The last TOTP step accepted, so no code is accepted twice.
    pub last_used_step: Option<u64>,
    /// False until a first code proves the authenticator holds the secret.
    pub confirmed: bool,
    pub recovery_hashes: Vec<[u8; 32]>,
    /// While unconfirmed: SHA-256 of the enrollment handle returned to whoever began the enrollment, and when it
    /// began. Only that caller can confirm or restart it until it expires.
    pub pending_handle_hash: Option<[u8; 32]>,
    pub pending_since: u64,
    /// Set by a restore: unused recovery codes were invalidated, and the next sign-in issues a new set.
    pub recovery_reissue: bool,
    /// Authenticator recovery: the new secret, sealed, staged until a code from the new authenticator confirms it
    /// (with the pending handle). The account stays confirmed meanwhile, so nobody can enroll over it.
    pub staged_otp_secret: Option<Vec<u8>>,
}

/// A storage failure (I/O, a locked or corrupt database). The operation rolls back and reports "unavailable".
#[derive(Clone, PartialEq, Eq)]
pub struct StoreError(pub String);

impl fmt::Debug for StoreError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "StoreError({})", self.0)
    }
}

pub type StoreResult<T> = Result<T, StoreError>;

pub trait Store {
    /// Starts the operation's transaction; for SQLite, `BEGIN IMMEDIATE` (the write lock is taken up front).
    fn begin(&mut self) -> StoreResult<()>;
    fn commit(&mut self) -> StoreResult<()>;
    /// Undoes everything since `begin`. Called on every failure, so it must not itself fail silently.
    fn rollback(&mut self) -> StoreResult<()>;

    fn account(&self, app: &str, account: &str) -> StoreResult<Option<Account>>;
    fn put_account(&mut self, app: &str, account: &str, record: &Account) -> StoreResult<()>;
    /// Every (app, account), for the restore procedure.
    fn accounts(&self) -> StoreResult<Vec<(String, String)>>;
    fn rate(&self, key: &str) -> StoreResult<rate::State>;
    fn put_rate(&mut self, key: &str, state: rate::State) -> StoreResult<()>;
    fn session(&self, key: &[u8; 32]) -> StoreResult<Option<session::Record>>;
    fn put_session(&mut self, key: [u8; 32], record: &session::Record) -> StoreResult<()>;
    /// Revokes every session of one principal.
    fn revoke_all(&mut self, principal: &session::Principal) -> StoreResult<()>;
    /// Revokes every session of every principal (restore).
    fn revoke_every_session(&mut self) -> StoreResult<()>;
    /// Stores an invite for (app, account): the SHA-256 of its token and when it expires.
    fn put_invite(
        &mut self,
        app: &str,
        account: &str,
        token_hash: [u8; 32],
        expires: u64,
    ) -> StoreResult<()>;
    /// Deletes and reports an unexpired invite for (app, account) with this token hash (single use).
    fn take_invite(
        &mut self,
        app: &str,
        account: &str,
        token_hash: [u8; 32],
        now: u64,
    ) -> StoreResult<bool>;
}

#[derive(Clone, Default)]
struct MemoryData {
    accounts: HashMap<(String, String), Account>,
    rates: HashMap<String, rate::State>,
    sessions: HashMap<[u8; 32], session::Record>,
    invites: HashMap<(String, String, [u8; 32]), u64>,
}

/// An in-memory store. Transactions are snapshots: `rollback` restores the state at `begin`.
#[derive(Default)]
pub struct Memory {
    data: MemoryData,
    snapshot: Option<MemoryData>,
}

impl Store for Memory {
    fn begin(&mut self) -> StoreResult<()> {
        if self.snapshot.is_some() {
            return Err(StoreError("transaction already open".into()));
        }
        self.snapshot = Some(self.data.clone());
        Ok(())
    }
    fn commit(&mut self) -> StoreResult<()> {
        self.snapshot
            .take()
            .map(|_| ())
            .ok_or_else(|| StoreError("no transaction".into()))
    }
    fn rollback(&mut self) -> StoreResult<()> {
        self.data = self
            .snapshot
            .take()
            .ok_or_else(|| StoreError("no transaction".into()))?;
        Ok(())
    }
    fn account(&self, app: &str, account: &str) -> StoreResult<Option<Account>> {
        Ok(self
            .data
            .accounts
            .get(&(app.to_string(), account.to_string()))
            .cloned())
    }
    fn put_account(&mut self, app: &str, account: &str, record: &Account) -> StoreResult<()> {
        self.data
            .accounts
            .insert((app.to_string(), account.to_string()), record.clone());
        Ok(())
    }
    fn accounts(&self) -> StoreResult<Vec<(String, String)>> {
        Ok(self.data.accounts.keys().cloned().collect())
    }
    fn rate(&self, key: &str) -> StoreResult<rate::State> {
        Ok(self.data.rates.get(key).copied().unwrap_or_default())
    }
    fn put_rate(&mut self, key: &str, state: rate::State) -> StoreResult<()> {
        self.data.rates.insert(key.to_string(), state);
        Ok(())
    }
    fn session(&self, key: &[u8; 32]) -> StoreResult<Option<session::Record>> {
        Ok(self.data.sessions.get(key).cloned())
    }
    fn put_session(&mut self, key: [u8; 32], record: &session::Record) -> StoreResult<()> {
        // Revocation is monotonic, as in the SQLite store: a later write never un-revokes a session.
        let mut r = record.clone();
        r.revoked |= self.data.sessions.get(&key).is_some_and(|old| old.revoked);
        self.data.sessions.insert(key, r);
        Ok(())
    }
    fn revoke_all(&mut self, principal: &session::Principal) -> StoreResult<()> {
        for r in self
            .data
            .sessions
            .values_mut()
            .filter(|r| &r.principal == principal)
        {
            r.revoked = true;
        }
        Ok(())
    }
    fn revoke_every_session(&mut self) -> StoreResult<()> {
        for r in self.data.sessions.values_mut() {
            r.revoked = true;
        }
        Ok(())
    }
    fn put_invite(
        &mut self,
        app: &str,
        account: &str,
        token_hash: [u8; 32],
        expires: u64,
    ) -> StoreResult<()> {
        self.data
            .invites
            .insert((app.to_string(), account.to_string(), token_hash), expires);
        Ok(())
    }
    fn take_invite(
        &mut self,
        app: &str,
        account: &str,
        token_hash: [u8; 32],
        now: u64,
    ) -> StoreResult<bool> {
        let key = (app.to_string(), account.to_string(), token_hash);
        Ok(self
            .data
            .invites
            .remove(&key)
            .is_some_and(|expires| now < expires))
    }
}

#[cfg(feature = "sqlite")]
pub mod sqlite;
