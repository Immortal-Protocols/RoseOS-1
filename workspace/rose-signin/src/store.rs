//! What the flow persists, behind a trait so a generated server backs it with SQLite (schema in
//! docs/ROSE-SIGNIN-V1.md) and tests use memory. Every account key is (app, account): accounts of different apps, or
//! different accounts of one app, never share a row.

use std::collections::HashMap;

use crate::rate;
use crate::session;

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Account {
    /// Argon2id PHC hash of the knowledge factor.
    pub knowledge_hash: String,
    /// The authenticator secret, sealed (crate::secret::Sealer).
    pub sealed_otp_secret: Vec<u8>,
    /// The last TOTP step accepted, so no code is accepted twice.
    pub last_used_step: Option<u64>,
    /// False until a first code proves the authenticator holds the secret.
    pub confirmed: bool,
    /// While unconfirmed: SHA-256 of the enrollment handle returned to whoever began the enrollment, and when it
    /// began. Only that caller can confirm or restart it until it expires.
    pub pending_handle_hash: Option<[u8; 32]>,
    pub pending_since: u64,
    pub recovery_hashes: Vec<[u8; 32]>,
}

pub trait Store {
    fn account(&self, app: &str, account: &str) -> Option<Account>;
    fn put_account(&mut self, app: &str, account: &str, record: Account);
    fn rate(&self, key: &str) -> rate::State;
    fn put_rate(&mut self, key: &str, state: rate::State);
    fn session(&self, key: &[u8; 32]) -> Option<session::Record>;
    fn put_session(&mut self, key: [u8; 32], record: session::Record);
    /// Revokes every session of one principal.
    fn revoke_all(&mut self, principal: &session::Principal);
}

#[derive(Default)]
pub struct Memory {
    accounts: HashMap<(String, String), Account>,
    rates: HashMap<String, rate::State>,
    sessions: HashMap<[u8; 32], session::Record>,
}

impl Store for Memory {
    fn account(&self, app: &str, account: &str) -> Option<Account> {
        self.accounts
            .get(&(app.to_string(), account.to_string()))
            .cloned()
    }
    fn put_account(&mut self, app: &str, account: &str, record: Account) {
        self.accounts
            .insert((app.to_string(), account.to_string()), record);
    }
    fn rate(&self, key: &str) -> rate::State {
        self.rates.get(key).copied().unwrap_or_default()
    }
    fn put_rate(&mut self, key: &str, state: rate::State) {
        self.rates.insert(key.to_string(), state);
    }
    fn session(&self, key: &[u8; 32]) -> Option<session::Record> {
        self.sessions.get(key).cloned()
    }
    fn put_session(&mut self, key: [u8; 32], record: session::Record) {
        self.sessions.insert(key, record);
    }
    fn revoke_all(&mut self, principal: &session::Principal) {
        for r in self
            .sessions
            .values_mut()
            .filter(|r| &r.principal == principal)
        {
            r.revoked = true;
        }
    }
}
