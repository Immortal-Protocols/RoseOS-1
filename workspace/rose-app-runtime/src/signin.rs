//! The `rose-signin-v1` routes (docs/ROSE-SIGNIN-V1.md, "Flows") over an injected [`Auth`].
//!
//! [`Auth`] is the shape of rose-signin's `flow::Authenticator` for one app. The adapter that binds the real
//! authenticator (with its SQLite store and context-bound sealer) implements it, and fixes the app: every call
//! is for this server's app, and `validate` is rose-signin's `validate_for(app, ..)`. This module only maps HTTP
//! to those calls and their results to the documented responses; it holds no secret and decides nothing about
//! factors, limits or sessions.
//!
//! The documented error words and statuses are the contract. Where the document names no status or body, this
//! module's choice is App Core's, and the generated client reads exactly it:
//! - enrollment and authenticator recovery answer `{"otpauth_uri","handle","recovery_codes"}`;
//! - sign-in answers `{"token","account"}`, plus `"recovery_codes"` when a restore issued new ones;
//! - passphrase recovery answers `{"recovery_codes"}`; confirmation and sign-out answer `204`;
//! - `passphrase_too_short`, `passphrase_too_long`, `invalid_name` and `bad_request` are 400, `not_admitted` 403,
//!   `enrollment_pending` and `already_enrolled` 409, `not_confirmed`, `sign_in_failed`, `recovery_failed` and
//!   `session_invalid` 401, `try_later` 429 with `Retry-After`, and `auth_unavailable` 503.

use crate::http::Response;
use crate::json::{self, Value};

/// Who a valid session belongs to.
#[derive(Clone, PartialEq, Eq)]
pub struct Principal {
    pub app: String,
    pub account: String,
}

impl std::fmt::Debug for Principal {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "Principal({}, <account>)", self.app)
    }
}

/// What enrollment (and authenticator recovery) hands the client once.
#[derive(Clone, PartialEq, Eq)]
pub struct Enrollment {
    pub otpauth_uri: String,
    pub handle: String,
    pub recovery_codes: Vec<String>,
}

impl std::fmt::Debug for Enrollment {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str("Enrollment(<redacted>)")
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Rejected {
    TooShort,
    TooLong,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum EnrollError {
    AlreadyEnrolled,
    Pending,
    Knowledge(Rejected),
    InvalidName,
    NotConfirmed,
    NotAdmitted,
    Limited { retry_after: u64 },
    Unavailable,
}

#[derive(Clone, PartialEq, Eq)]
pub enum SignIn {
    Ok {
        token: String,
        principal: Principal,
        new_recovery_codes: Vec<String>,
    },
    Failed,
    Limited {
        retry_after: u64,
    },
    Unavailable,
}

impl std::fmt::Debug for SignIn {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            SignIn::Ok { .. } => f.write_str("SignIn::Ok(<redacted>)"),
            SignIn::Failed => f.write_str("SignIn::Failed"),
            SignIn::Limited { retry_after } => write!(f, "SignIn::Limited({retry_after})"),
            SignIn::Unavailable => f.write_str("SignIn::Unavailable"),
        }
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum RecoverError {
    Failed,
    Limited { retry_after: u64 },
    Knowledge(Rejected),
    Unavailable,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Unavailable;

/// Who may start enrolling (rose-signin D7): the app's declaration in rose-app.json.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Admission {
    Invite,
    Open,
}

/// rose-signin's authenticator for this server's one app.
pub trait Auth: Send {
    /// The sealer's current key is loaded.
    fn ready(&self) -> bool;
    fn begin_enrollment(
        &mut self,
        account: &str,
        passphrase: &str,
        invite: Option<&str>,
        open: bool,
        handle: Option<&str>,
        now: u64,
    ) -> Result<Enrollment, EnrollError>;
    fn confirm_enrollment(&mut self, account: &str, handle: &str, code: &str, now: u64) -> Result<(), EnrollError>;
    fn sign_in(&mut self, account: &str, code: &str, passphrase: &str, client: &str, now: u64) -> SignIn;
    /// `validate_for(app, token, now)`: another app's token is `None`.
    fn validate(&mut self, token: &str, now: u64) -> Result<Option<Principal>, Unavailable>;
    /// `revoke_for(app, token)`.
    fn revoke(&mut self, token: &str) -> Result<(), Unavailable>;
    fn revoke_all(&mut self, principal: &Principal) -> Result<(), Unavailable>;
    fn recover_authenticator(
        &mut self,
        account: &str,
        recovery_code: &str,
        passphrase: &str,
        client: &str,
        now: u64,
    ) -> Result<Enrollment, RecoverError>;
    #[allow(clippy::too_many_arguments)]
    fn recover_passphrase(
        &mut self,
        account: &str,
        recovery_code: &str,
        code: &str,
        new_passphrase: &str,
        client: &str,
        now: u64,
    ) -> Result<Vec<String>, RecoverError>;
}

pub fn error(status: u16, word: &str) -> Response {
    let body = json::to_string(&Value::obj([("error", Value::str(word))])).unwrap_or_default();
    Response::json(status, body.into_bytes())
}

fn limited(retry_after: u64) -> Response {
    let body = Value::obj([
        ("error", Value::str("try_later")),
        ("retry_after", Value::Int(retry_after.min(i64::MAX as u64) as i64)),
    ]);
    let mut r = Response::json(429, json::to_string(&body).unwrap_or_default().into_bytes());
    r.headers.push(("Retry-After", retry_after.to_string()));
    r
}

fn ok(body: Value) -> Response {
    match json::to_string(&body) {
        Some(text) => Response::json(200, text.into_bytes()),
        None => error(500, "internal"),
    }
}

fn codes(codes: &[String]) -> Value {
    Value::Arr(codes.iter().map(|c| Value::str(c.clone())).collect())
}

fn enrollment(e: Enrollment) -> Response {
    ok(Value::obj([
        ("otpauth_uri", Value::str(e.otpauth_uri)),
        ("handle", Value::str(e.handle)),
        ("recovery_codes", codes(&e.recovery_codes)),
    ]))
}

fn enroll_error(e: EnrollError) -> Response {
    match e {
        EnrollError::AlreadyEnrolled => error(409, "already_enrolled"),
        EnrollError::Pending => error(409, "enrollment_pending"),
        EnrollError::Knowledge(Rejected::TooShort) => error(400, "passphrase_too_short"),
        EnrollError::Knowledge(Rejected::TooLong) => error(400, "passphrase_too_long"),
        EnrollError::InvalidName => error(400, "invalid_name"),
        EnrollError::NotConfirmed => error(401, "not_confirmed"),
        EnrollError::NotAdmitted => error(403, "not_admitted"),
        EnrollError::Limited { retry_after } => limited(retry_after),
        EnrollError::Unavailable => error(503, "auth_unavailable"),
    }
}

fn recover_error(e: RecoverError) -> Response {
    match e {
        RecoverError::Failed => error(401, "recovery_failed"),
        RecoverError::Limited { retry_after } => limited(retry_after),
        RecoverError::Knowledge(Rejected::TooShort) => error(400, "passphrase_too_short"),
        RecoverError::Knowledge(Rejected::TooLong) => error(400, "passphrase_too_long"),
        RecoverError::Unavailable => error(503, "auth_unavailable"),
    }
}

/// The request's fields, each a string; extra fields are ignored, as generated code does (§7.3).
struct Fields(Value);

impl Fields {
    fn parse(body: &[u8]) -> Option<Fields> {
        match json::parse(body) {
            Ok(v @ Value::Obj(_)) => Some(Fields(v)),
            _ => None,
        }
    }
    fn required(&self, key: &str) -> Option<String> {
        self.0.get(key).and_then(Value::as_str).map(str::to_string)
    }
    fn optional(&self, key: &str) -> Option<Option<String>> {
        self.0.optional_str(key).ok().map(|v| v.map(str::to_string))
    }
}

/// The bearer token of `Authorization: Bearer <token>`, if the header is exactly that once.
pub fn bearer(authorization: Option<&str>) -> Option<&str> {
    let value = authorization?;
    let (scheme, token) = value.split_once(' ')?;
    if !scheme.eq_ignore_ascii_case("bearer") || token.is_empty() || token.len() > 256 {
        return None;
    }
    token.bytes().all(|b| b.is_ascii_alphanumeric()).then_some(token)
}

/// The session's principal for a token, or the response that refuses it. The principal must be this app's:
/// rose-signin's `validate_for` already says so, and this is checked again here.
pub fn principal_for(auth: &mut dyn Auth, app: &str, token: &str, now: u64) -> Result<Principal, Response> {
    match auth.validate(token, now) {
        Ok(Some(p)) if p.app == app => Ok(p),
        Ok(_) => Err(error(401, "session_invalid")),
        Err(Unavailable) => Err(error(503, "auth_unavailable")),
    }
}

/// `principal_for` from an `Authorization` header.
pub fn principal(auth: &mut dyn Auth, app: &str, authorization: Option<&str>, now: u64) -> Result<Principal, Response> {
    match bearer(authorization) {
        Some(token) => principal_for(auth, app, token, now),
        None => Err(error(401, "session_invalid")),
    }
}

/// Whether `/signin/v1/...` is one of the sign-in routes (any method), so the server never sends it to the app.
pub fn is_signin_path(path: &str) -> bool {
    path == "/signin/v1" || path.starts_with("/signin/v1/")
}

/// One sign-in request, read and checked before the authenticator is touched (so parsing a body never holds
/// it). Its `Debug` names only the call.
pub enum Call {
    Enroll {
        account: String,
        passphrase: String,
        invite: Option<String>,
        handle: Option<String>,
    },
    Confirm {
        account: String,
        handle: String,
        code: String,
    },
    SignIn {
        account: String,
        code: String,
        passphrase: String,
    },
    SignOut {
        token: String,
    },
    SignOutEverywhere {
        token: String,
    },
    RecoverAuthenticator {
        account: String,
        recovery_code: String,
        passphrase: String,
    },
    RecoverPassphrase {
        account: String,
        recovery_code: String,
        code: String,
        new_passphrase: String,
    },
}

impl std::fmt::Debug for Call {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(match self {
            Call::Enroll { .. } => "Call::Enroll",
            Call::Confirm { .. } => "Call::Confirm",
            Call::SignIn { .. } => "Call::SignIn",
            Call::SignOut { .. } => "Call::SignOut",
            Call::SignOutEverywhere { .. } => "Call::SignOutEverywhere",
            Call::RecoverAuthenticator { .. } => "Call::RecoverAuthenticator",
            Call::RecoverPassphrase { .. } => "Call::RecoverPassphrase",
        })
    }
}

const ROUTES: [(&str, &str); 7] = [
    ("POST", "/signin/v1/enroll"),
    ("POST", "/signin/v1/enroll/confirm"),
    ("POST", "/signin/v1/session"),
    ("DELETE", "/signin/v1/session"),
    ("DELETE", "/signin/v1/sessions"),
    ("POST", "/signin/v1/recover/authenticator"),
    ("POST", "/signin/v1/recover/passphrase"),
];

/// Reads a sign-in request: the route by method and path first (`404`, `405`), then its token or body (`401`,
/// `400`). Nothing here touches the authenticator.
pub fn parse(method: &str, path: &str, body: &[u8], authorization: Option<&str>) -> Result<Call, Response> {
    if !ROUTES.iter().any(|(_, p)| *p == path) {
        return Err(error(404, "not_found"));
    }
    if !ROUTES.contains(&(method, path)) {
        return Err(error(405, "method_not_allowed"));
    }
    if method == "DELETE" {
        if !body.is_empty() {
            return Err(error(400, "bad_request"));
        }
        let token = bearer(authorization)
            .ok_or_else(|| error(401, "session_invalid"))?
            .to_string();
        return Ok(if path == "/signin/v1/session" {
            Call::SignOut { token }
        } else {
            Call::SignOutEverywhere { token }
        });
    }
    let bad = || error(400, "bad_request");
    let f = Fields::parse(body).ok_or_else(bad)?;
    let call = match path {
        "/signin/v1/enroll" => Call::Enroll {
            account: f.required("account").ok_or_else(bad)?,
            passphrase: f.required("passphrase").ok_or_else(bad)?,
            invite: f.optional("invite").ok_or_else(bad)?,
            handle: f.optional("handle").ok_or_else(bad)?,
        },
        "/signin/v1/enroll/confirm" => Call::Confirm {
            account: f.required("account").ok_or_else(bad)?,
            handle: f.required("handle").ok_or_else(bad)?,
            code: f.required("code").ok_or_else(bad)?,
        },
        "/signin/v1/session" => Call::SignIn {
            account: f.required("account").ok_or_else(bad)?,
            code: f.required("code").ok_or_else(bad)?,
            passphrase: f.required("passphrase").ok_or_else(bad)?,
        },
        "/signin/v1/recover/authenticator" => Call::RecoverAuthenticator {
            account: f.required("account").ok_or_else(bad)?,
            recovery_code: f.required("recovery_code").ok_or_else(bad)?,
            passphrase: f.required("passphrase").ok_or_else(bad)?,
        },
        _ => Call::RecoverPassphrase {
            account: f.required("account").ok_or_else(bad)?,
            recovery_code: f.required("recovery_code").ok_or_else(bad)?,
            code: f.required("code").ok_or_else(bad)?,
            new_passphrase: f.required("new_passphrase").ok_or_else(bad)?,
        },
    };
    Ok(call)
}

/// Runs a parsed sign-in call. `client` is what rate limits count by.
pub fn run(auth: &mut dyn Auth, app: &str, admission: Admission, call: Call, client: &str, now: u64) -> Response {
    let open = admission == Admission::Open;
    match call {
        Call::Enroll {
            account,
            passphrase,
            invite,
            handle,
        } => match auth.begin_enrollment(&account, &passphrase, invite.as_deref(), open, handle.as_deref(), now) {
            Ok(e) => enrollment(e),
            Err(e) => enroll_error(e),
        },
        Call::Confirm { account, handle, code } => match auth.confirm_enrollment(&account, &handle, &code, now) {
            Ok(()) => Response::empty(204),
            Err(e) => enroll_error(e),
        },
        Call::SignIn {
            account,
            code,
            passphrase,
        } => match auth.sign_in(&account, &code, &passphrase, client, now) {
            SignIn::Ok {
                token,
                principal,
                new_recovery_codes,
            } => {
                let mut members = vec![
                    ("token".to_string(), Value::str(token)),
                    ("account".to_string(), Value::str(principal.account)),
                ];
                if !new_recovery_codes.is_empty() {
                    members.push(("recovery_codes".to_string(), codes(&new_recovery_codes)));
                }
                ok(Value::Obj(members))
            }
            SignIn::Failed => error(401, "sign_in_failed"),
            SignIn::Limited { retry_after } => limited(retry_after),
            SignIn::Unavailable => error(503, "auth_unavailable"),
        },
        // Sign-out answers the same whether or not the token was live, so it reveals nothing.
        Call::SignOut { token } => match auth.revoke(&token) {
            Ok(()) => Response::empty(204),
            Err(Unavailable) => error(503, "auth_unavailable"),
        },
        Call::SignOutEverywhere { token } => match principal_for(auth, app, &token, now) {
            Ok(p) => match auth.revoke_all(&p) {
                Ok(()) => Response::empty(204),
                Err(Unavailable) => error(503, "auth_unavailable"),
            },
            Err(r) => r,
        },
        Call::RecoverAuthenticator {
            account,
            recovery_code,
            passphrase,
        } => match auth.recover_authenticator(&account, &recovery_code, &passphrase, client, now) {
            Ok(e) => enrollment(e),
            Err(e) => recover_error(e),
        },
        Call::RecoverPassphrase {
            account,
            recovery_code,
            code,
            new_passphrase,
        } => match auth.recover_passphrase(&account, &recovery_code, &code, &new_passphrase, client, now) {
            Ok(c) => ok(Value::obj([("recovery_codes", codes(&c))])),
            Err(e) => recover_error(e),
        },
    }
}
