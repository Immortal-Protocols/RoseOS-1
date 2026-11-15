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
        match self.sealer.open(&ctx(app, account), &a.sealed_otp_secret) {
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
        handle: Option<&[u8]>,
        now: u64,
    ) -> Result<Enrollment, EnrollError> {
        if !valid_name(app) || !valid_name(account) {
            return Err(EnrollError::InvalidName);
        }
        let knowledge_hash = knowledge::hash(passphrase).map_err(EnrollError::Knowledge)?;
        let result = self.transaction(|me| {
            if let Some(a) = me.store.account(app, account)? {
                if a.confirmed {
                    return Ok(Err(EnrollError::AlreadyEnrolled));
                }
                if now < a.pending_since.saturating_add(PENDING_SECONDS)
                    && !handle_matches(&a, handle)
                {
                    return Ok(Err(EnrollError::Pending));
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
                },
            )?;
            Ok(Ok(Enrollment {
                handle,
                otpauth_uri: Secret::new(uri.into_bytes()),
                recovery_codes: codes,
            }))
        });
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
            let pending = me.store.account(app, account)?.filter(|a| {
                !a.confirmed
                    && now < a.pending_since.saturating_add(PENDING_SECONDS)
                    && handle_matches(a, Some(handle))
            });
            let Some(mut a) = pending else {
                return Ok(Err(EnrollError::NotConfirmed));
            };
            let step = me
                .open_secret(app, account, &a)?
                .and_then(|s| otp::verify(s.expose(), me.otp, code, now, a.last_used_step));
            let Some(step) = step else {
                let failed = me.rate.failed(me.store.rate(&key)?, now);
                me.store.put_rate(&key, failed)?;
                return Ok(Err(EnrollError::NotConfirmed));
            };
            a.last_used_step = Some(step);
            a.confirmed = true;
            a.pending_handle_hash = None;
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
            if let Some(a) = &record {
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

    /// Ends every session of an account (sign out everywhere, or after a factor change).
    pub fn revoke_all(&mut self, principal: &Principal) -> Result<(), Unavailable> {
        self.transaction(|me| Ok(me.store.revoke_all(principal)?))
            .map_err(|_| Unavailable)
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

fn handle_matches(a: &Account, handle: Option<&[u8]>) -> bool {
    use subtle::ConstantTimeEq;
    match (a.pending_handle_hash, handle) {
        (Some(stored), Some(h)) => bool::from(stored.ct_eq(&session::key(h))),
        _ => false,
    }
}
