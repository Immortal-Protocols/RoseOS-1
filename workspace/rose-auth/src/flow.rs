//! The rose-auth-v1 server flow: enrollment, sign-in with both factors, session validation and revocation.
//!
//! Both factors are always checked, and a failure never says which one failed. Who may enroll an account (open
//! sign-up or creator invite, decision D7) is the generated app's admission check before `begin_enrollment`; the
//! app owner's deployment authority never passes through this module. `Authenticator` methods take `&mut self`: a
//! store backed by SQLite runs each call in one transaction (docs/ROSE-AUTH-V1.md, Storage).

use crate::otp;
use crate::rate;
use crate::secret::{Sealer, Secret};
use crate::session::{self, Principal};
use crate::store::{Account, Store};
use crate::{knowledge, recovery};

/// How long an unconfirmed enrollment belongs to whoever began it.
pub const PENDING_SECONDS: u64 = 15 * 60;

#[derive(Debug, PartialEq, Eq)]
pub enum EnrollError {
    /// The account already has a confirmed enrollment; changing factors is a separate, signed-in operation.
    AlreadyEnrolled,
    /// Someone else began enrolling this account less than `PENDING_SECONDS` ago.
    Pending,
    Knowledge(knowledge::Rejected),
    /// An issuer or account name that is empty, over 128 bytes, or contains ':' or a control character (the
    /// otpauth label format separates issuer and account with ':').
    InvalidName,
    /// No pending enrollment for this handle, or the code did not match (also for confirmed accounts).
    NotConfirmed,
    Limited {
        retry_after: u64,
    },
}

/// What the enrolling client receives once: the handle that alone can confirm or restart this enrollment, the
/// authenticator URI (shown as text and QR) and the recovery codes.
#[derive(Debug)]
pub struct Enrollment {
    pub handle: Secret,
    pub otpauth_uri: Secret,
    pub recovery_codes: Vec<String>,
}

#[derive(Debug, PartialEq, Eq)]
pub enum SignIn {
    /// The bearer token, shown to the client once.
    Ok { token: Secret, principal: Principal },
    /// Wrong code or knowledge factor, unknown or unconfirmed account: deliberately indistinguishable.
    Failed,
    /// Too many failures; retry after this many seconds.
    Limited { retry_after: u64 },
}

pub fn valid_name(s: &str) -> bool {
    !s.is_empty() && s.len() <= 128 && !s.chars().any(|c| c == ':' || c.is_control())
}

pub struct Authenticator<S: Store, K: Sealer> {
    pub store: S,
    sealer: K,
    issuer: String,
    pub otp: otp::Profile,
    pub rate: rate::Policy,
    pub session: session::Policy,
    decoy: String,
}

impl<S: Store, K: Sealer> Authenticator<S, K> {
    /// A sealer is required: authenticator secrets are never stored in the clear.
    pub fn new(store: S, sealer: K, issuer: &str) -> Result<Self, EnrollError> {
        if !valid_name(issuer) {
            return Err(EnrollError::InvalidName);
        }
        Ok(Authenticator {
            store,
            sealer,
            issuer: issuer.to_string(),
            otp: otp::V1,
            rate: rate::V1,
            session: session::V1,
            decoy: knowledge::decoy(),
        })
    }

    /// Starts an enrollment: a new authenticator secret, the knowledge factor's hash and recovery codes. An
    /// unconfirmed enrollment can be restarted only with its handle, or once it is `PENDING_SECONDS` old, so a second
    /// caller cannot replace someone's enrollment in progress. The account cannot sign in until
    /// `confirm_enrollment` sees a code from the user's authenticator.
    pub fn begin_enrollment(
        &mut self,
        app: &str,
        account: &str,
        passphrase: &str,
        handle: Option<&[u8]>,
        now: u64,
    ) -> Result<Enrollment, EnrollError> {
        if !valid_name(app) || !valid_name(account) {
            return Err(EnrollError::InvalidName);
        }
        if let Some(a) = self.store.account(app, account) {
            if a.confirmed {
                return Err(EnrollError::AlreadyEnrolled);
            }
            let fresh = now < a.pending_since.saturating_add(PENDING_SECONDS);
            if fresh && !Self::handle_matches(&a, handle) {
                return Err(EnrollError::Pending);
            }
        }
        let knowledge_hash = knowledge::hash(passphrase).map_err(EnrollError::Knowledge)?;
        let secret = Secret::random(20);
        let (codes, recovery_hashes) = recovery::generate(app, account);
        let uri = otp::otpauth_uri(&self.issuer, account, secret.expose(), self.otp);
        let (handle, handle_hash) = session::mint();
        self.store.put_account(
            app,
            account,
            Account {
                knowledge_hash,
                sealed_otp_secret: self.sealer.seal(&secret),
                last_used_step: None,
                confirmed: false,
                recovery_hashes,
                pending_handle_hash: Some(handle_hash),
                pending_since: now,
            },
        );
        Ok(Enrollment {
            handle,
            otpauth_uri: Secret::new(uri.into_bytes()),
            recovery_codes: codes,
        })
    }

    fn handle_matches(a: &Account, handle: Option<&[u8]>) -> bool {
        use subtle::ConstantTimeEq;
        match (a.pending_handle_hash, handle) {
            (Some(stored), Some(h)) => bool::from(stored.ct_eq(&session::key(h))),
            _ => false,
        }
    }

    /// Confirms an enrollment with its handle and a code from the user's authenticator (the code's step is
    /// consumed). Every failure, including an already confirmed account, is `NotConfirmed`.
    pub fn confirm_enrollment(
        &mut self,
        app: &str,
        account: &str,
        handle: &[u8],
        code: &str,
        now: u64,
    ) -> Result<(), EnrollError> {
        let key = format!("e\u{0}{app}\u{0}{account}");
        let wait = self.rate.wait(self.store.rate(&key), now);
        if wait > 0 {
            return Err(EnrollError::Limited { retry_after: wait });
        }
        let pending = self.store.account(app, account).filter(|a| {
            !a.confirmed
                && now < a.pending_since.saturating_add(PENDING_SECONDS)
                && Self::handle_matches(a, Some(handle))
        });
        let Some(mut a) = pending else {
            return Err(EnrollError::NotConfirmed);
        };
        let step = self
            .sealer
            .open(&a.sealed_otp_secret)
            .and_then(|s| otp::verify(s.expose(), self.otp, code, now, a.last_used_step));
        let Some(step) = step else {
            let failed = self.rate.failed(self.store.rate(&key), now);
            self.store.put_rate(&key, failed);
            return Err(EnrollError::NotConfirmed);
        };
        a.last_used_step = Some(step);
        a.confirmed = true;
        a.pending_handle_hash = None;
        self.store.put_account(app, account, a);
        self.store.put_rate(&key, rate::State::default());
        Ok(())
    }

    /// Signs in with a one-time code and the knowledge factor. `client` identifies the caller for rate limiting
    /// (for example its address). The client limit always applies; the account limit applies only to attempts
    /// without a valid code, so it slows code guessing without locking out the real user (decision D6).
    pub fn sign_in(
        &mut self,
        app: &str,
        account: &str,
        code: &str,
        passphrase: &str,
        client: &str,
        now: u64,
    ) -> SignIn {
        let account_key = format!("a\u{0}{app}\u{0}{account}");
        let client_key = format!("c\u{0}{app}\u{0}{client}");
        let record = self.store.account(app, account).filter(|a| a.confirmed);
        let code_step = record.as_ref().and_then(|a| {
            self.sealer
                .open(&a.sealed_otp_secret)
                .and_then(|s| otp::verify(s.expose(), self.otp, code, now, a.last_used_step))
        });
        let mut wait = self.rate.wait(self.store.rate(&client_key), now);
        if code_step.is_none() {
            wait = wait.max(self.rate.wait(self.store.rate(&account_key), now));
        }
        if wait > 0 {
            return SignIn::Limited { retry_after: wait };
        }
        // The knowledge factor is evaluated on every attempt that gets this far (a decoy hash for unknown accounts),
        // so neither the answer nor the work done reveals which factor failed or whether the account exists.
        let knowledge_ok = match &record {
            Some(a) => knowledge::verify(passphrase, &a.knowledge_hash),
            None => {
                knowledge::verify(passphrase, &self.decoy);
                false
            }
        };
        if let (Some(mut a), Some(step)) = (record, code_step) {
            // A valid code is consumed even if the knowledge factor was wrong: one code buys one knowledge guess.
            a.last_used_step = Some(step);
            self.store.put_account(app, account, a);
        }
        if code_step.is_some() && knowledge_ok {
            self.store.put_rate(&account_key, rate::State::default());
            let principal = Principal {
                app: app.to_string(),
                account: account.to_string(),
            };
            let (token, key) = session::mint();
            self.store.put_session(
                key,
                session::Record {
                    principal: principal.clone(),
                    created: now,
                    last_seen: now,
                    revoked: false,
                },
            );
            return SignIn::Ok { token, principal };
        }
        let a = self.rate.failed(self.store.rate(&account_key), now);
        self.store.put_rate(&account_key, a);
        let c = self.rate.failed(self.store.rate(&client_key), now);
        self.store.put_rate(&client_key, c);
        SignIn::Failed
    }

    /// The principal of a live session, refreshing its idle timer; None for unknown, expired or revoked tokens.
    pub fn validate(&mut self, token: &[u8], now: u64) -> Option<Principal> {
        let key = session::key(token);
        let mut r = self.store.session(&key)?;
        if !self.session.live(&r, now) {
            return None;
        }
        r.last_seen = now;
        let p = r.principal.clone();
        self.store.put_session(key, r);
        Some(p)
    }

    /// Ends one session (sign out).
    pub fn revoke(&mut self, token: &[u8]) {
        let key = session::key(token);
        if let Some(mut r) = self.store.session(&key) {
            r.revoked = true;
            self.store.put_session(key, r);
        }
    }

    /// Ends every session of an account (sign out everywhere, or after a factor change).
    pub fn revoke_all(&mut self, principal: &Principal) {
        self.store.revoke_all(principal);
    }
}
