//! [`SigninAuth`]: the runtime's [`Auth`] over rose-signin's `Authenticator<SqliteStore, AeadSealer>` for one app.
//!
//! Every call is for the app fixed at construction: sessions are checked with `validate_for(app, ..)` and ended
//! with `revoke_for(app, ..)`, so another app's token is never this app's session and is never touched. The
//! authenticator's results map one to one onto the runtime's; nothing here decides about factors, limits, sessions
//! or admission.
//!
//! Handles, session tokens and invites are minted by rose-signin already as text: 52 base32 characters (256 bits;
//! upper case, no padding: letters and digits only, as the runtime's bearer check requires), the format
//! docs/ROSE-SIGNIN-V1.md names. They cross HTTP exactly as minted, never re-encoded, so a value from rose-signin
//! itself (an invite from the owner's tooling, say) is the value the client sends back. A value that is too long or
//! not base32 is answered as rose-signin answers a wrong value: an unknown token is no session, a bad handle does
//! not confirm, a bad invite does not admit. Closed admission with no invite is `Admission::Invite(b"")`, which
//! admits no one.

use rose_app_runtime::signin::{self as rt, Auth};
use rose_signin::flow::{self, Admission};
use rose_signin::secret::{AeadSealer, Secret};
use rose_signin::store::sqlite::SqliteStore;
use rose_signin::{base32, knowledge, session};

/// rose-signin's authenticator as generated servers use it.
pub type Authenticator = flow::Authenticator<SqliteStore, AeadSealer>;

/// The longest value accepted from a request: longer than any handle, token or invite rose-signin mints (52).
const MAX_ENCODED: usize = 256;

pub struct SigninAuth {
    app: String,
    inner: Authenticator,
    /// Held not ready by start-up (a binary the host did not deploy), whatever the sealer says.
    held: bool,
}

impl std::fmt::Debug for SigninAuth {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("SigninAuth")
            .field("app", &self.app)
            .finish_non_exhaustive()
    }
}

/// A request's handle, token or invite as rose-signin's bytes (the text itself), in a `Secret` (redacted `Debug`;
/// rose-signin does not zero it on drop); None when
/// it is too long or not base32, which no minted value is.
fn presented(text: &str) -> Option<Secret> {
    if text.is_empty() || text.len() > MAX_ENCODED || base32::decode(text).is_none() {
        return None;
    }
    Some(Secret::new(text.as_bytes().to_vec()))
}

/// A minted value as the client receives it. rose-signin mints ASCII; anything else is unavailable.
fn text(secret: &Secret) -> Option<String> {
    std::str::from_utf8(secret.expose()).ok().map(str::to_string)
}

fn rejected(r: knowledge::Rejected) -> rt::Rejected {
    match r {
        knowledge::Rejected::TooShort => rt::Rejected::TooShort,
        knowledge::Rejected::TooLong => rt::Rejected::TooLong,
    }
}

fn enroll_error(e: flow::EnrollError) -> rt::EnrollError {
    match e {
        flow::EnrollError::AlreadyEnrolled => rt::EnrollError::AlreadyEnrolled,
        flow::EnrollError::Pending => rt::EnrollError::Pending,
        flow::EnrollError::Knowledge(r) => rt::EnrollError::Knowledge(rejected(r)),
        flow::EnrollError::InvalidName => rt::EnrollError::InvalidName,
        flow::EnrollError::NotConfirmed => rt::EnrollError::NotConfirmed,
        flow::EnrollError::NotAdmitted => rt::EnrollError::NotAdmitted,
        flow::EnrollError::Limited { retry_after } => rt::EnrollError::Limited { retry_after },
        flow::EnrollError::Unavailable => rt::EnrollError::Unavailable,
    }
}

fn recover_error(e: flow::RecoverError) -> rt::RecoverError {
    match e {
        flow::RecoverError::Failed => rt::RecoverError::Failed,
        flow::RecoverError::Limited { retry_after } => rt::RecoverError::Limited { retry_after },
        flow::RecoverError::Knowledge(r) => rt::RecoverError::Knowledge(rejected(r)),
        flow::RecoverError::Unavailable => rt::RecoverError::Unavailable,
    }
}

/// The client's copy of an enrollment. The otpauth URI is ASCII by construction; anything else is unavailable.
fn enrollment(e: flow::Enrollment) -> Option<rt::Enrollment> {
    Some(rt::Enrollment {
        otpauth_uri: std::str::from_utf8(e.otpauth_uri.expose()).ok()?.to_string(),
        handle: text(&e.handle)?,
        recovery_codes: e.recovery_codes,
    })
}

fn principal(p: session::Principal) -> rt::Principal {
    rt::Principal {
        app: p.app,
        account: p.account,
    }
}

impl SigninAuth {
    /// The authenticator for `app` (rose-signin's app name for this server).
    pub fn new(app: &str, inner: Authenticator) -> Result<SigninAuth, String> {
        if !flow::valid_name(app) {
            return Err("the app name is not a valid rose-signin name".to_string());
        }
        Ok(SigninAuth {
            app: app.to_string(),
            inner,
            held: false,
        })
    }

    /// Keeps this authenticator not ready for the rest of the process (no sign-in route serves).
    pub fn hold(&mut self) {
        self.held = true;
    }

    pub fn app(&self) -> &str {
        &self.app
    }

    /// A single-use invite for `account` (D7), as the base32 the client sends back. Only the creator's owner
    /// tooling calls this; no route reaches it.
    pub fn create_invite(&mut self, account: &str, now: u64) -> Result<String, rt::EnrollError> {
        self.inner
            .create_invite(&self.app, account, now)
            .map_err(enroll_error)
            .and_then(|t| text(&t).ok_or(rt::EnrollError::Unavailable))
    }

    /// Ciphertexts that failed their integrity check since start; the host alerts on increases.
    pub fn integrity_faults(&self) -> u64 {
        self.inner.integrity_faults
    }
}

impl Auth for SigninAuth {
    fn ready(&self) -> bool {
        !self.held && self.inner.ready()
    }

    fn begin_enrollment(
        &mut self,
        account: &str,
        passphrase: &str,
        invite: Option<&str>,
        open: bool,
        handle: Option<&str>,
        now: u64,
    ) -> Result<rt::Enrollment, rt::EnrollError> {
        let invite = invite.and_then(presented);
        let admission = if open {
            Admission::Open
        } else {
            Admission::Invite(invite.as_ref().map_or(&[][..], Secret::expose))
        };
        // A handle that does not decode is a handle that matches nothing.
        let handle = handle.map(|h| presented(h).unwrap_or_else(|| Secret::new(Vec::new())));
        let started = self.inner.begin_enrollment(
            &self.app,
            account,
            passphrase,
            admission,
            handle.as_ref().map(Secret::expose),
            now,
        );
        match started {
            Ok(e) => enrollment(e).ok_or(rt::EnrollError::Unavailable),
            Err(e) => Err(enroll_error(e)),
        }
    }

    fn confirm_enrollment(&mut self, account: &str, handle: &str, code: &str, now: u64) -> Result<(), rt::EnrollError> {
        // Counted against the account's limit like any wrong handle.
        let handle = presented(handle).unwrap_or_else(|| Secret::new(Vec::new()));
        self.inner
            .confirm_enrollment(&self.app, account, handle.expose(), code, now)
            .map_err(enroll_error)
    }

    fn sign_in(&mut self, account: &str, code: &str, passphrase: &str, client: &str, now: u64) -> rt::SignIn {
        match self.inner.sign_in(&self.app, account, code, passphrase, client, now) {
            flow::SignIn::Ok {
                token,
                principal: p,
                new_recovery_codes,
            } => match text(&token) {
                Some(token) => rt::SignIn::Ok {
                    token,
                    principal: principal(p),
                    new_recovery_codes,
                },
                None => rt::SignIn::Unavailable,
            },
            flow::SignIn::Failed => rt::SignIn::Failed,
            flow::SignIn::Limited { retry_after } => rt::SignIn::Limited { retry_after },
            flow::SignIn::Unavailable => rt::SignIn::Unavailable,
        }
    }

    fn validate(&mut self, token: &str, now: u64) -> Result<Option<rt::Principal>, rt::Unavailable> {
        let Some(token) = presented(token) else {
            return Ok(None);
        };
        self.inner
            .validate_for(&self.app, token.expose(), now)
            .map(|p| p.map(principal))
            .map_err(|_| rt::Unavailable)
    }

    fn revoke(&mut self, token: &str) -> Result<(), rt::Unavailable> {
        let Some(token) = presented(token) else {
            return Ok(());
        };
        self.inner
            .revoke_for(&self.app, token.expose())
            .map_err(|_| rt::Unavailable)
    }

    fn revoke_all(&mut self, p: &rt::Principal) -> Result<(), rt::Unavailable> {
        // Only this app's accounts: another app's principal has no sessions here to end.
        if p.app != self.app {
            return Ok(());
        }
        let p = session::Principal {
            app: p.app.clone(),
            account: p.account.clone(),
        };
        self.inner.revoke_all(&p).map_err(|_| rt::Unavailable)
    }

    fn recover_authenticator(
        &mut self,
        account: &str,
        recovery_code: &str,
        passphrase: &str,
        client: &str,
        now: u64,
    ) -> Result<rt::Enrollment, rt::RecoverError> {
        match self
            .inner
            .recover_authenticator(&self.app, account, recovery_code, passphrase, client, now)
        {
            Ok(e) => enrollment(e).ok_or(rt::RecoverError::Unavailable),
            Err(e) => Err(recover_error(e)),
        }
    }

    fn recover_passphrase(
        &mut self,
        account: &str,
        recovery_code: &str,
        code: &str,
        new_passphrase: &str,
        client: &str,
        now: u64,
    ) -> Result<Vec<String>, rt::RecoverError> {
        self.inner
            .recover_passphrase(&self.app, account, recovery_code, code, new_passphrase, client, now)
            .map_err(recover_error)
    }
}
