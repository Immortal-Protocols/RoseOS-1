//! The rose-signin-v1 server flow: enrollment, sign-in with both factors, session validation, revocation and the
//! restore procedure.
//!
//! Both factors are always checked, and a failure never says which one failed. Who may enroll an account is the
//! generated app's admission check before `begin_enrollment` (decision D7); the app owner's deployment authority never
//! passes through this module. Every operation is one store transaction: it commits only when it completes, and on a
//! storage fault or an unavailable key it rolls back and reports `Unavailable`, having written and counted nothing.

use crate::otp;
use crate::rate;
use crate::secret::{OTP_SECRET, OpenError, SealContext, SealError, Sealer, Secret};
use crate::session::{self, Principal};
use crate::store::{Account, Store, StoreError};
use crate::{knowledge, recovery};

/// How long an unconfirmed enrollment belongs to whoever began it.
pub const PENDING_SECONDS: u64 = 15 * 60;
/// How long a creator's invite stays usable.
pub const INVITE_SECONDS: u64 = 7 * 86400;

/// Who may start enrolling a new account (decision D7): the generated app passes `Invite` with the
/// token the creator sent (the default), or `Open` when the app opted into open sign-up.
/// Restarting one's own pending enrollment with its handle needs no admission.
#[derive(Clone, Copy, Debug)]
pub enum Admission<'a> {
    Open,
    Invite(&'a [u8]),
}

#[derive(Debug, PartialEq, Eq)]
pub enum RecoverError {
    /// Wrong recovery code or second factor, or no such confirmed account: deliberately
    /// indistinguishable.
    Failed,
    Limited {
        retry_after: u64,
    },
    /// The new passphrase does not meet the length policy.
    Knowledge(knowledge::Rejected),
    /// The sealing key or the store is unavailable; nothing was written. HTTP 503
    /// `auth_unavailable`.
    Unavailable,
}

#[derive(Debug, PartialEq, Eq)]
pub enum EnrollError {
    /// The account already has a confirmed enrollment; changing factors is a separate, signed-in operation.
    AlreadyEnrolled,
    /// Someone else began enrolling this account less than `PENDING_SECONDS` ago.
    Pending,
    Knowledge(knowledge::Rejected),
    /// An issuer or account name that is empty, over 128 bytes, or contains ':' or a control character.
    InvalidName,
    /// No pending enrollment for this handle, or the code did not match (also for confirmed accounts).
    NotConfirmed,
    /// No valid invite for this account (missing, already used, expired or for another account).
    NotAdmitted,
    Limited {
        retry_after: u64,
    },
    /// The sealing key or the store is unavailable; nothing was written. HTTP 503 `auth_unavailable`.
    Unavailable,
}

/// What the enrolling client receives once.
#[derive(Debug)]
pub struct Enrollment {
    pub handle: Secret,
    pub otpauth_uri: Secret,
    pub recovery_codes: Vec<String>,
}

#[derive(Debug, PartialEq, Eq)]
pub enum SignIn {
    /// The bearer token, shown once. `new_recovery_codes` is non-empty only after a restore invalidated the old set.
    Ok {
        token: Secret,
        principal: Principal,
        new_recovery_codes: Vec<String>,
    },
    /// Wrong code or knowledge factor, unknown or unconfirmed account: deliberately indistinguishable.
    Failed,
    /// Too many failures; retry after this many seconds.
    Limited { retry_after: u64 },
    /// The sealing key or the store is unavailable; nothing was written or counted. HTTP 503 `auth_unavailable`.
    Unavailable,
}

/// The sealing key or the store is unavailable.
#[derive(Debug, PartialEq, Eq)]
pub struct Unavailable;

/// Why an operation stops before committing.
enum Abort {
    Unavailable,
    /// Undo the operation's writes (for example an invite spent on an enrollment then refused).
    Rollback,
}

impl From<StoreError> for Abort {
    fn from(_: StoreError) -> Self {
        Abort::Unavailable
    }
}

impl From<SealError> for Abort {
    fn from(_: SealError) -> Self {
        Abort::Unavailable
    }
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
    /// Ciphertexts that failed their integrity check (tampering or corruption). The host alerts on increases.
    pub integrity_faults: u64,
}

fn ctx<'a>(app: &'a str, account: &'a str) -> SealContext<'a> {
    SealContext {
        app,
        account,
        purpose: OTP_SECRET,
    }
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
            integrity_faults: 0,
        })
    }

    /// Whether sign-in can be served: the sealer's current key is loaded. Servers gate readiness on this.
    pub fn ready(&self) -> bool {
        self.sealer.ready()
    }

    /// Runs `op` as one transaction: commit when it returns Ok, roll back otherwise (and if the commit fails).
    fn transaction<T>(
        &mut self,
        op: impl FnOnce(&mut Self) -> Result<T, Abort>,
    ) -> Result<T, Abort> {
        self.store.begin()?;
        match op(self) {
            Ok(v) => match self.store.commit() {
                Ok(()) => Ok(v),
                Err(err) => {
                    let _ = self.store.rollback();
                    Err(err.into())
                }
            },
            Err(abort) => {
                let _ = self.store.rollback();
                Err(abort)
            }
        }
    }

    /// Opens an account's authenticator secret. An integrity failure is counted for the host and treated as "no
    /// valid code"; an unavailable key aborts the operation.
    fn open_secret(
        &mut self,
        app: &str,
        account: &str,
        a: &Account,
    ) -> Result<Option<Secret>, Abort> {
        self.open_sealed(app, account, &a.sealed_otp_secret)
    }

    fn open_sealed(
        &mut self,
        app: &str,
        account: &str,
        sealed: &[u8],
    ) -> Result<Option<Secret>, Abort> {
        match self.sealer.open(&ctx(app, account), sealed) {
            Ok(s) => Ok(Some(s)),
            Err(OpenError::KeyUnavailable) => Err(Abort::Unavailable),
            Err(OpenError::Integrity) => {
                self.integrity_faults += 1;
                Ok(None)
            }
        }
    }

    /// Starts an enrollment: a new authenticator secret, the knowledge factor's hash and recovery codes. An
    /// unconfirmed enrollment can be restarted only with its handle, or once it is `PENDING_SECONDS` old.
    pub fn begin_enrollment(
        &mut self,
        app: &str,
        account: &str,
        passphrase: &str,
        admission: Admission,
        handle: Option<&[u8]>,
        now: u64,
    ) -> Result<Enrollment, EnrollError> {
        if !valid_name(app) || !valid_name(account) {
            return Err(EnrollError::InvalidName);
        }
        let knowledge_hash = knowledge::hash(passphrase).map_err(EnrollError::Knowledge)?;
        let mut refused_slot: Option<EnrollError> = None;
        let refused = &mut refused_slot;
        let result = self.transaction(|me| {
            let existing = me.store.account(app, account)?;
            let restarting_own = existing.as_ref().is_some_and(|a| {
                !a.confirmed
                    && now < a.pending_since.saturating_add(PENDING_SECONDS)
                    && handle_matches(a, handle)
            });
            // Admission comes before anything that depends on the account, so a caller without a
            // valid invite learns nothing about it (only `NotAdmitted`). The invite is spent here;
            // if the enrollment is then refused or fails, the transaction rolls back and it
            // survives.
            let invited = !restarting_own && matches!(admission, Admission::Invite(_));
            if !restarting_own {
                let admitted = match admission {
                    Admission::Open => true,
                    Admission::Invite(token) => {
                        me.store
                            .take_invite(app, account, session::key(token), now)?
                    }
                };
                if !admitted {
                    return Ok(Err(EnrollError::NotAdmitted));
                }
            }
            if let Some(a) = &existing {
                let refusal = if a.confirmed {
                    Some(EnrollError::AlreadyEnrolled)
                } else if now < a.pending_since.saturating_add(PENDING_SECONDS) && !restarting_own {
                    Some(EnrollError::Pending)
                } else {
                    None
                };
                if let Some(err) = refusal {
                    if invited {
                        *refused = Some(err);
                        return Err(Abort::Rollback);
                    }
                    return Ok(Err(err));
                }
            }
            let secret = Secret::random(20);
            let sealed_otp_secret = me.sealer.seal(&ctx(app, account), &secret)?;
            let (codes, recovery_hashes) = recovery::generate(app, account);
            let uri = otp::otpauth_uri(&me.issuer, account, secret.expose(), me.otp);
            let (handle, handle_hash) = session::mint();
            me.store.put_account(
                app,
                account,
                &Account {
                    knowledge_hash,
                    sealed_otp_secret,
                    last_used_step: None,
                    confirmed: false,
                    recovery_hashes,
                    pending_handle_hash: Some(handle_hash),
                    pending_since: now,
                    recovery_reissue: false,
                    staged_otp_secret: None,
                },
            )?;
            Ok(Ok(Enrollment {
                handle,
                otpauth_uri: Secret::new(uri.into_bytes()),
                recovery_codes: codes,
            }))
        });
        if let Some(err) = refused_slot {
            return Err(err);
        }
        result.unwrap_or(Err(EnrollError::Unavailable))
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
        let result = self.transaction(|me| {
            let wait = me.rate.wait(me.store.rate(&key)?, now);
            if wait > 0 {
                return Ok(Err(EnrollError::Limited { retry_after: wait }));
            }
            // A pending enrollment, or a confirmed account's staged authenticator recovery.
            let pending = me.store.account(app, account)?.filter(|a| {
                (!a.confirmed || a.staged_otp_secret.is_some())
                    && now < a.pending_since.saturating_add(PENDING_SECONDS)
                    && handle_matches(a, Some(handle))
            });
            let Some(mut a) = pending else {
                return Ok(Err(EnrollError::NotConfirmed));
            };
            let target = a
                .staged_otp_secret
                .clone()
                .unwrap_or_else(|| a.sealed_otp_secret.clone());
            let step = me
                .open_sealed(app, account, &target)?
                .and_then(|s| otp::verify(s.expose(), me.otp, code, now, a.last_used_step));
            let Some(step) = step else {
                let failed = me.rate.failed(me.store.rate(&key)?, now);
                me.store.put_rate(&key, failed)?;
                return Ok(Err(EnrollError::NotConfirmed));
            };
            a.last_used_step = Some(step);
            a.confirmed = true;
            a.pending_handle_hash = None;
            if let Some(staged) = a.staged_otp_secret.take() {
                // Recovery completes: the new authenticator replaces the lost one, and every
                // session (including any opened with the old one meanwhile) ends.
                a.sealed_otp_secret = staged;
                me.store.revoke_all(&Principal {
                    app: app.to_string(),
                    account: account.to_string(),
                })?;
            }
            me.store.put_account(app, account, &a)?;
            me.store.put_rate(&key, rate::State::default())?;
            Ok(Ok(()))
        });
        result.unwrap_or(Err(EnrollError::Unavailable))
    }

    /// Signs in with a one-time code and the knowledge factor. `client` identifies the caller for rate limiting.
    /// The client limit always applies; the account limit applies only to attempts without a valid code (D6).
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
        let result = self.transaction(|me| {
            let record = me.store.account(app, account)?.filter(|a| a.confirmed);
            let mut code_step = None;
            if let Some(a) = record
                .as_ref()
                .filter(|a| !old_authenticator_suspended(a, now))
            {
                let faults = me.integrity_faults;
                let secret = me.open_secret(app, account, a)?;
                if me.integrity_faults > faults {
                    // A tampered or corrupt row: a generic failure for the client, an alert for the host, and no
                    // count against the user, who did nothing wrong.
                    return Ok(SignIn::Failed);
                }
                code_step = secret
                    .and_then(|s| otp::verify(s.expose(), me.otp, code, now, a.last_used_step));
            }
            let mut wait = me.rate.wait(me.store.rate(&client_key)?, now);
            if code_step.is_none() {
                wait = wait.max(me.rate.wait(me.store.rate(&account_key)?, now));
            }
            if wait > 0 {
                return Ok(SignIn::Limited { retry_after: wait });
            }
            // The knowledge factor is evaluated on every attempt that gets this far (a decoy hash for unknown
            // accounts), so neither the answer nor the work done reveals which factor failed or whether the account
            // exists.
            let knowledge_ok = match &record {
                Some(a) => knowledge::verify(passphrase, &a.knowledge_hash),
                None => {
                    knowledge::verify(passphrase, &me.decoy);
                    false
                }
            };
            let mut new_recovery_codes = Vec::new();
            if let (Some(mut a), Some(step)) = (record, code_step) {
                // A valid code is consumed even if the knowledge factor was wrong: one code buys one knowledge guess.
                a.last_used_step = Some(step);
                if knowledge_ok {
                    if me.sealer.stale(&a.sealed_otp_secret) {
                        // Rotation: re-seal under the current key once the secret has proved itself.
                        if let Some(s) = me.open_secret(app, account, &a)? {
                            a.sealed_otp_secret = me.sealer.seal(&ctx(app, account), &s)?;
                        }
                    }
                    if a.recovery_reissue {
                        let (codes, hashes) = recovery::generate(app, account);
                        a.recovery_hashes = hashes;
                        a.recovery_reissue = false;
                        new_recovery_codes = codes;
                    }
                }
                me.store.put_account(app, account, &a)?;
            }
            if code_step.is_some() && knowledge_ok {
                me.store.put_rate(&account_key, rate::State::default())?;
                let principal = Principal {
                    app: app.to_string(),
                    account: account.to_string(),
                };
                let (token, key) = session::mint();
                let record = session::Record {
                    principal: principal.clone(),
                    created: now,
                    last_seen: now,
                    revoked: false,
                };
                me.store.put_session(key, &record)?;
                return Ok(SignIn::Ok {
                    token,
                    principal,
                    new_recovery_codes,
                });
            }
            let a = me.rate.failed(me.store.rate(&account_key)?, now);
            me.store.put_rate(&account_key, a)?;
            let c = me.rate.failed(me.store.rate(&client_key)?, now);
            me.store.put_rate(&client_key, c)?;
            Ok(SignIn::Failed)
        });
        result.unwrap_or(SignIn::Unavailable)
    }

    /// The principal of a live session, refreshing its idle timer; None for unknown, expired or revoked tokens.
    pub fn validate(&mut self, token: &[u8], now: u64) -> Result<Option<Principal>, Unavailable> {
        let key = session::key(token);
        self.transaction(|me| {
            let Some(mut r) = me.store.session(&key)? else {
                return Ok(None);
            };
            if !me.session.live(&r, now) {
                return Ok(None);
            }
            r.last_seen = now;
            me.store.put_session(key, &r)?;
            Ok(Some(r.principal))
        })
        .map_err(|_| Unavailable)
    }

    /// Ends one session (sign out).
    pub fn revoke(&mut self, token: &[u8]) -> Result<(), Unavailable> {
        let key = session::key(token);
        self.transaction(|me| {
            if let Some(mut r) = me.store.session(&key)? {
                r.revoked = true;
                me.store.put_session(key, &r)?;
            }
            Ok(())
        })
        .map_err(|_| Unavailable)
    }

    /// `validate` for a server that serves one app: the principal only if the token belongs to `app`.
    /// Another app's token is `None` and is not touched (its idle timer is not refreshed). Generated
    /// servers use this rather than comparing `principal.app` themselves.
    pub fn validate_for(
        &mut self,
        app: &str,
        token: &[u8],
        now: u64,
    ) -> Result<Option<Principal>, Unavailable> {
        let key = session::key(token);
        self.transaction(|me| {
            let Some(mut r) = me.store.session(&key)? else {
                return Ok(None);
            };
            if r.principal.app != app || !me.session.live(&r, now) {
                return Ok(None);
            }
            r.last_seen = now;
            me.store.put_session(key, &r)?;
            Ok(Some(r.principal))
        })
        .map_err(|_| Unavailable)
    }

    /// `revoke` for a server that serves one app: signs out only a session of `app`; another app's
    /// token is left alone.
    pub fn revoke_for(&mut self, app: &str, token: &[u8]) -> Result<(), Unavailable> {
        let key = session::key(token);
        self.transaction(|me| {
            if let Some(mut r) = me.store.session(&key)?.filter(|r| r.principal.app == app) {
                r.revoked = true;
                me.store.put_session(key, &r)?;
            }
            Ok(())
        })
        .map_err(|_| Unavailable)
    }

    /// Ends every session of an account (sign out everywhere, or after a factor change).
    pub fn revoke_all(&mut self, principal: &Principal) -> Result<(), Unavailable> {
        self.transaction(|me| Ok(me.store.revoke_all(principal)?))
            .map_err(|_| Unavailable)
    }

    /// Issues a single-use invite for (app, account), valid for `INVITE_SECONDS` (D7). Only the
    /// app's creator, through the generated server's owner tooling, calls this; the token goes to
    /// the recipient, who enrolls with `Admission::Invite(token)`. An invite admits exactly the
    /// account it names.
    pub fn create_invite(
        &mut self,
        app: &str,
        account: &str,
        now: u64,
    ) -> Result<Secret, EnrollError> {
        if !valid_name(app) || !valid_name(account) {
            return Err(EnrollError::InvalidName);
        }
        let result = self.transaction(|me| {
            if me.store.account(app, account)?.is_some_and(|a| a.confirmed) {
                return Ok(Err(EnrollError::AlreadyEnrolled));
            }
            let (token, hash) = session::mint();
            me.store
                .put_invite(app, account, hash, now.saturating_add(INVITE_SECONDS))?;
            Ok(Ok(token))
        });
        result.unwrap_or(Err(EnrollError::Unavailable))
    }

    /// Whether a recovery code is one of the account's unused set (checked on a copy).
    fn recovery_code_valid(app: &str, account: &str, a: &Account, typed: &str) -> bool {
        let mut copy = a.recovery_hashes.clone();
        recovery::consume(&mut copy, app, account, typed)
    }

    /// Lost authenticator (D5): a recovery code plus the passphrase stage a new authenticator
    /// secret. Returns an `Enrollment` (new URI, handle and recovery codes); `confirm_enrollment`
    /// with a code from the new authenticator completes it, replacing the old secret. The account
    /// stays confirmed throughout (nobody can enroll over it), every session is revoked, and the
    /// recovery code is spent even if the passphrase is wrong (one code buys one guess).
    pub fn recover_authenticator(
        &mut self,
        app: &str,
        account: &str,
        recovery_code: &str,
        passphrase: &str,
        client: &str,
        now: u64,
    ) -> Result<Enrollment, RecoverError> {
        let account_key = format!("r\u{0}{app}\u{0}{account}");
        let client_key = format!("rc\u{0}{app}\u{0}{client}");
        let result = self.transaction(|me| {
            let record = me.store.account(app, account)?.filter(|a| a.confirmed);
            let code_ok = record
                .as_ref()
                .is_some_and(|a| Self::recovery_code_valid(app, account, a, recovery_code));
            if let Some(w) = me.limited(&account_key, &client_key, code_ok, now)? {
                return Ok(Err(RecoverError::Limited { retry_after: w }));
            }
            let knowledge_ok = match &record {
                Some(a) => knowledge::verify(passphrase, &a.knowledge_hash),
                None => {
                    knowledge::verify(passphrase, &me.decoy);
                    false
                }
            };
            let (Some(mut a), true) = (record, code_ok) else {
                me.count_failure(&account_key, &client_key, now)?;
                return Ok(Err(RecoverError::Failed));
            };
            recovery::consume(&mut a.recovery_hashes, app, account, recovery_code);
            if !knowledge_ok {
                me.store.put_account(app, account, &a)?;
                me.count_failure(&account_key, &client_key, now)?;
                return Ok(Err(RecoverError::Failed));
            }
            let secret = Secret::random(20);
            let staged = me.sealer.seal(&ctx(app, account), &secret)?;
            let (codes, hashes) = recovery::generate(app, account);
            let (handle, handle_hash) = session::mint();
            a.staged_otp_secret = Some(staged);
            a.recovery_hashes = hashes;
            a.recovery_reissue = false;
            a.pending_handle_hash = Some(handle_hash);
            a.pending_since = now;
            me.store.put_account(app, account, &a)?;
            me.store.put_rate(&account_key, rate::State::default())?;
            me.store.revoke_all(&Principal {
                app: app.to_string(),
                account: account.to_string(),
            })?;
            let uri = otp::otpauth_uri(&me.issuer, account, secret.expose(), me.otp);
            Ok(Ok(Enrollment {
                handle,
                otpauth_uri: Secret::new(uri.into_bytes()),
                recovery_codes: codes,
            }))
        });
        result.unwrap_or(Err(RecoverError::Unavailable))
    }

    /// Forgotten passphrase (D5): a recovery code plus a fresh one-time code set a new passphrase.
    /// Returns the new recovery codes (shown once); every session is revoked. The recovery code
    /// and the one-time code are each spent whenever valid, even if the other is not.
    #[allow(clippy::too_many_arguments)]
    pub fn recover_passphrase(
        &mut self,
        app: &str,
        account: &str,
        recovery_code: &str,
        code: &str,
        new_passphrase: &str,
        client: &str,
        now: u64,
    ) -> Result<Vec<String>, RecoverError> {
        let knowledge_hash = knowledge::hash(new_passphrase).map_err(RecoverError::Knowledge)?;
        let account_key = format!("r\u{0}{app}\u{0}{account}");
        let client_key = format!("rc\u{0}{app}\u{0}{client}");
        let result = self.transaction(|me| {
            let record = me.store.account(app, account)?.filter(|a| a.confirmed);
            let code_ok = record
                .as_ref()
                .is_some_and(|a| Self::recovery_code_valid(app, account, a, recovery_code));
            if let Some(w) = me.limited(&account_key, &client_key, code_ok, now)? {
                return Ok(Err(RecoverError::Limited { retry_after: w }));
            }
            let Some(mut a) = record else {
                me.count_failure(&account_key, &client_key, now)?;
                return Ok(Err(RecoverError::Failed));
            };
            let faults = me.integrity_faults;
            let step = if old_authenticator_suspended(&a, now) {
                None
            } else {
                me.open_secret(app, account, &a)?
                    .and_then(|s| otp::verify(s.expose(), me.otp, code, now, a.last_used_step))
            };
            if me.integrity_faults > faults {
                return Ok(Err(RecoverError::Failed));
            }
            if code_ok {
                recovery::consume(&mut a.recovery_hashes, app, account, recovery_code);
            }
            if let Some(s) = step {
                a.last_used_step = Some(s);
            }
            if !(code_ok && step.is_some()) {
                me.store.put_account(app, account, &a)?;
                me.count_failure(&account_key, &client_key, now)?;
                return Ok(Err(RecoverError::Failed));
            }
            let (codes, hashes) = recovery::generate(app, account);
            a.knowledge_hash = knowledge_hash;
            a.recovery_hashes = hashes;
            a.recovery_reissue = false;
            me.store.put_account(app, account, &a)?;
            me.store.put_rate(&account_key, rate::State::default())?;
            me.store.revoke_all(&Principal {
                app: app.to_string(),
                account: account.to_string(),
            })?;
            Ok(Ok(codes))
        });
        result.unwrap_or(Err(RecoverError::Unavailable))
    }

    /// The wait before another recovery attempt: the client limit always; the account limit only
    /// without a valid recovery code (an 80-bit secret an attacker cannot hold), as for sign-in.
    fn limited(
        &mut self,
        account_key: &str,
        client_key: &str,
        code_ok: bool,
        now: u64,
    ) -> Result<Option<u64>, Abort> {
        let mut wait = self.rate.wait(self.store.rate(client_key)?, now);
        if !code_ok {
            wait = wait.max(self.rate.wait(self.store.rate(account_key)?, now));
        }
        Ok((wait > 0).then_some(wait))
    }

    fn count_failure(
        &mut self,
        account_key: &str,
        client_key: &str,
        now: u64,
    ) -> Result<(), Abort> {
        let a = self.rate.failed(self.store.rate(account_key)?, now);
        self.store.put_rate(account_key, a)?;
        let c = self.rate.failed(self.store.rate(client_key)?, now);
        self.store.put_rate(client_key, c)?;
        Ok(())
    }

    /// Run once after the database is restored from a backup, before serving requests. A backup brings back state
    /// that later operations had retired, so this, in one transaction: revokes every session (tokens revoked after
    /// the backup would otherwise work again); moves every account's replay marker past every step the server could
    /// have accepted before the restore (the current step plus the skew), so no code used just before it can be
    /// replayed; and invalidates every confirmed account's unused recovery codes (codes spent after the backup would
    /// otherwise work again), flagging it to receive a new set at its next sign-in. A pending enrollment keeps its
    /// recovery codes: it cannot have spent one. Factor
    /// changes made after the backup are not recoverable from inside the database (docs/ROSE-SIGNIN-V1.md).
    pub fn restore_after_backup(&mut self, now: u64) -> Result<(), Unavailable> {
        let current = otp::step(self.otp, now);
        self.transaction(|me| {
            me.store.revoke_every_session()?;
            for (app, account) in me.store.accounts()? {
                if let Some(mut a) = me.store.account(&app, &account)? {
                    // Every step the server could have accepted before the restore, skew included, is spent.
                    let floor = current.saturating_add(me.otp.skew_steps);
                    a.last_used_step = Some(a.last_used_step.map_or(floor, |s| s.max(floor)));
                    if a.confirmed {
                        // An unconfirmed enrollment cannot have spent a recovery code: its codes stay valid.
                        a.recovery_hashes.clear();
                        a.recovery_reissue = true;
                    }
                    me.store.put_account(&app, &account, &a)?;
                }
            }
            Ok(())
        })
        .map_err(|_| Unavailable)
    }
}

/// While an authenticator recovery is staged and fresh, the old authenticator (declared lost) no
/// longer counts as a factor: whoever holds it can neither sign in nor advance the used-code marker
/// that the new authenticator's confirmation checks. If the recovery expires unconfirmed, the old
/// authenticator works again.
fn old_authenticator_suspended(a: &Account, now: u64) -> bool {
    a.staged_otp_secret.is_some() && now < a.pending_since.saturating_add(PENDING_SECONDS)
}

fn handle_matches(a: &Account, handle: Option<&[u8]>) -> bool {
    use subtle::ConstantTimeEq;
    match (a.pending_handle_hash, handle) {
        (Some(stored), Some(h)) => bool::from(stored.ct_eq(&session::key(h))),
        _ => false,
    }
}
