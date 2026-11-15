//! The runtime end to end over a fake authenticator: readiness, the account check, the sign-in routes' responses,
//! request rules, and what reaches the log. One test also serves over a real socket on 127.0.0.1.

use std::io::{Read, Write};
use std::net::{TcpListener, TcpStream};
use std::sync::{Arc, Mutex};

use rose_app_runtime::http::Request;
use rose_app_runtime::json::{self, Value};
use rose_app_runtime::server::{Ctx, Identity, LogLine, Reply, Route, RouteAuth, Server};
use rose_app_runtime::signin::{
    Admission, Auth, EnrollError, Enrollment, Principal, RecoverError, Rejected, SignIn, Unavailable,
};

const APP: &str = "notes";
const TOKEN: &str = "GOODTOKEN";
const OTHER_APP_TOKEN: &str = "OTHERAPPTOKEN";

#[derive(Default)]
struct Script {
    calls: Vec<String>,
    ready: bool,
    unavailable: bool,
    enroll: Option<Result<Enrollment, EnrollError>>,
    sign_in: Option<SignIn>,
    recover: Option<RecoverError>,
    confirm: Option<EnrollError>,
    /// The account name the authenticator signs in as (it may differ from what was typed).
    account_as: Option<String>,
    revoke_all_fails: bool,
}

struct Fake(Arc<Mutex<Script>>);

fn enrollment() -> Enrollment {
    Enrollment {
        otpauth_uri: "otpauth://totp/x".into(),
        handle: "HANDLE".into(),
        recovery_codes: vec!["r1".into(), "r2".into()],
    }
}

impl Auth for Fake {
    fn ready(&self) -> bool {
        self.0.lock().unwrap().ready
    }
    fn begin_enrollment(
        &mut self,
        account: &str,
        passphrase: &str,
        invite: Option<&str>,
        open: bool,
        handle: Option<&str>,
        now: u64,
    ) -> Result<Enrollment, EnrollError> {
        let mut s = self.0.lock().unwrap();
        s.calls.push(format!(
            "enroll {account} {passphrase} {invite:?} {open} {handle:?} {now}"
        ));
        s.enroll.clone().unwrap_or(Ok(enrollment()))
    }
    fn confirm_enrollment(&mut self, account: &str, handle: &str, code: &str, now: u64) -> Result<(), EnrollError> {
        self.0
            .lock()
            .unwrap()
            .calls
            .push(format!("confirm {account} {handle} {code} {now}"));
        match self.0.lock().unwrap().confirm.clone() {
            Some(e) => Err(e),
            None => Ok(()),
        }
    }
    fn sign_in(&mut self, account: &str, code: &str, passphrase: &str, client: &str, now: u64) -> SignIn {
        let mut s = self.0.lock().unwrap();
        s.calls
            .push(format!("sign_in {account} {code} {passphrase} {client} {now}"));
        let account = s.account_as.clone().unwrap_or_else(|| account.to_string());
        s.sign_in.clone().unwrap_or(SignIn::Ok {
            token: TOKEN.into(),
            principal: Principal {
                app: APP.into(),
                account,
            },
            new_recovery_codes: vec![],
        })
    }
    fn validate(&mut self, token: &str, now: u64) -> Result<Option<Principal>, Unavailable> {
        if token == "BOOM" {
            panic!("an adapter bug"); // before the script lock, so it stays usable
        }
        let mut s = self.0.lock().unwrap();
        s.calls.push(format!("validate {token} {now}"));
        if s.unavailable {
            return Err(Unavailable);
        }
        Ok(match token {
            TOKEN => Some(Principal {
                app: APP.into(),
                account: "ada".into(),
            }),
            // An adapter that wrongly returned another app's session: the runtime's second check stops it.
            OTHER_APP_TOKEN => Some(Principal {
                app: "other".into(),
                account: "ada".into(),
            }),
            _ => None,
        })
    }
    fn revoke(&mut self, token: &str) -> Result<(), Unavailable> {
        let mut s = self.0.lock().unwrap();
        s.calls.push(format!("revoke {token}"));
        if s.unavailable {
            return Err(Unavailable);
        }
        Ok(())
    }
    fn revoke_all(&mut self, principal: &Principal) -> Result<(), Unavailable> {
        self.0
            .lock()
            .unwrap()
            .calls
            .push(format!("revoke_all {}", principal.account));
        if self.0.lock().unwrap().revoke_all_fails {
            return Err(Unavailable);
        }
        Ok(())
    }
    fn recover_authenticator(
        &mut self,
        account: &str,
        recovery_code: &str,
        passphrase: &str,
        client: &str,
        now: u64,
    ) -> Result<Enrollment, RecoverError> {
        let mut s = self.0.lock().unwrap();
        s.calls.push(format!(
            "recover_authenticator {account} {recovery_code} {passphrase} {client} {now}"
        ));
        match s.recover.clone() {
            Some(e) => Err(e),
            None => Ok(enrollment()),
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
    ) -> Result<Vec<String>, RecoverError> {
        let mut s = self.0.lock().unwrap();
        s.calls.push(format!(
            "recover_passphrase {account} {recovery_code} {code} {new_passphrase} {client} {now}"
        ));
        match s.recover.clone() {
            Some(e) => Err(e),
            None => Ok(vec!["n1".into()]),
        }
    }
}

fn data(ctx: &Ctx, _: Option<&Value>) -> Reply {
    match ctx.data.downcast_ref::<&'static str>() {
        Some(d) => Reply::Ok(Value::str(*d)),
        None => Reply::Refused(404, "no_data"),
    }
}
fn refused_with(status: u16, word: &'static str) -> Reply {
    Reply::Refused(status, word)
}
fn r101(_: &Ctx, _: Option<&Value>) -> Reply {
    refused_with(101, "x")
}
fn r200(_: &Ctx, _: Option<&Value>) -> Reply {
    refused_with(200, "x")
}
fn r302(_: &Ctx, _: Option<&Value>) -> Reply {
    refused_with(302, "x")
}
fn r399(_: &Ctx, _: Option<&Value>) -> Reply {
    refused_with(399, "x")
}
fn r_empty(_: &Ctx, _: Option<&Value>) -> Reply {
    refused_with(400, "")
}
fn r_upper(_: &Ctx, _: Option<&Value>) -> Reply {
    refused_with(400, "Taken")
}
fn r_quote(_: &Ctx, _: Option<&Value>) -> Reply {
    refused_with(400, "a\"b")
}
fn whoami(ctx: &Ctx, _body: Option<&Value>) -> Reply {
    Reply::Ok(Value::obj([(
        "account",
        Value::str(ctx.principal.map(|p| p.account.clone()).unwrap_or_default()),
    )]))
}
fn echo(_: &Ctx, body: Option<&Value>) -> Reply {
    Reply::Ok(body.cloned().unwrap_or(Value::Null))
}
fn hello(ctx: &Ctx, _: Option<&Value>) -> Reply {
    Reply::Ok(Value::obj([
        ("signed_in", Value::Bool(ctx.principal.is_some())),
        ("now", Value::Int(ctx.now as i64)),
    ]))
}
fn refuse(_: &Ctx, _: Option<&Value>) -> Reply {
    Reply::Refused(409, "taken")
}
fn bad_refusal(_: &Ctx, _: Option<&Value>) -> Reply {
    Reply::Refused(503, "busy")
}
fn bad_word(_: &Ctx, _: Option<&Value>) -> Reply {
    Reply::Refused(400, "Bad Word\r\n")
}
fn boom(_: &Ctx, _: Option<&Value>) -> Reply {
    panic!("handler bug")
}
fn nan(_: &Ctx, _: Option<&Value>) -> Reply {
    Reply::Ok(Value::Float(f64::NAN))
}

fn routes() -> Vec<Route> {
    let r = |method, path, auth, body, handler| Route {
        method,
        path,
        auth,
        body,
        handler,
    };
    vec![
        r("GET", "/app/me", RouteAuth::Account, false, whoami as _),
        r("POST", "/app/echo", RouteAuth::Account, true, echo as _),
        r("GET", "/app/hello", RouteAuth::Public, false, hello as _),
        r("POST", "/app/refuse", RouteAuth::Public, false, refuse as _),
        r("POST", "/app/bad-refusal", RouteAuth::Public, false, bad_refusal as _),
        r("POST", "/app/bad-word", RouteAuth::Public, false, bad_word as _),
        r("POST", "/app/boom", RouteAuth::Public, false, boom as _),
        r("POST", "/app/nan", RouteAuth::Public, false, nan as _),
        r("GET", "/app/data", RouteAuth::Public, false, data as _),
        r("POST", "/app/r101", RouteAuth::Public, false, r101 as _),
        r("POST", "/app/r200", RouteAuth::Public, false, r200 as _),
        r("POST", "/app/r302", RouteAuth::Public, false, r302 as _),
        r("POST", "/app/r399", RouteAuth::Public, false, r399 as _),
        r("POST", "/app/r-empty", RouteAuth::Public, false, r_empty as _),
        r("POST", "/app/r-upper", RouteAuth::Public, false, r_upper as _),
        r("POST", "/app/r-quote", RouteAuth::Public, false, r_quote as _),
    ]
}

fn identity() -> Identity {
    Identity {
        app_id: "11111111-1111-4111-8111-111111111111".into(),
        revision_sha256: "a".repeat(64),
        deployment_sha256: "b".repeat(64),
    }
}

struct Harness {
    server: Server,
    script: Arc<Mutex<Script>>,
    log: Arc<Mutex<Vec<LogLine>>>,
}

fn harness(admission: Admission) -> Harness {
    let script = Arc::new(Mutex::new(Script {
        ready: true,
        ..Default::default()
    }));
    let log = Arc::new(Mutex::new(Vec::new()));
    let sink = Arc::clone(&log);
    let server = Server::new(
        APP,
        admission,
        routes(),
        identity(),
        Box::new(Fake(Arc::clone(&script))),
    )
    .unwrap()
    .with_log(Box::new(move |l: &LogLine| sink.lock().unwrap().push(l.clone())))
    .with_clock(Box::new(|| 1_700_000_000));
    server
        .readiness
        .migrations_done
        .store(true, std::sync::atomic::Ordering::SeqCst);
    server
        .readiness
        .restore_done
        .store(true, std::sync::atomic::Ordering::SeqCst);
    Harness { server, script, log }
}

/// A raw request through the whole connection path (parse, respond, write); returns status, headers and body.
fn send(h: &Harness, raw: &str) -> (u16, String, String) {
    let mut conn = Duplex {
        input: std::io::Cursor::new(raw.as_bytes().to_vec()),
        output: Vec::new(),
    };
    h.server.serve_stream(&mut conn, "203.0.113.7");
    let text = String::from_utf8(conn.output).unwrap();
    let (head, body) = text.split_once("\r\n\r\n").unwrap_or((&text, ""));
    let status = head.split(' ').nth(1).unwrap_or("0").parse().unwrap();
    (status, head.to_string(), body.to_string())
}

struct Duplex {
    input: std::io::Cursor<Vec<u8>>,
    output: Vec<u8>,
}
impl Read for Duplex {
    fn read(&mut self, buf: &mut [u8]) -> std::io::Result<usize> {
        self.input.read(buf)
    }
}
impl Write for Duplex {
    fn write(&mut self, buf: &[u8]) -> std::io::Result<usize> {
        self.output.write(buf)
    }
    fn flush(&mut self) -> std::io::Result<()> {
        Ok(())
    }
}

fn get(path: &str, token: Option<&str>) -> String {
    let auth = token
        .map(|t| format!("Authorization: Bearer {t}\r\n"))
        .unwrap_or_default();
    format!("GET {path} HTTP/1.1\r\nHost: x\r\n{auth}\r\n")
}

fn post(path: &str, token: Option<&str>, body: &str) -> String {
    let auth = token
        .map(|t| format!("Authorization: Bearer {t}\r\n"))
        .unwrap_or_default();
    format!(
        "POST {path} HTTP/1.1\r\nHost: x\r\n{auth}Content-Type: application/json\r\nContent-Length: {}\r\n\r\n{body}",
        body.len()
    )
}

fn error_word(body: &str) -> String {
    json::parse(body.as_bytes())
        .unwrap()
        .get("error")
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_string()
}

#[test]
fn health_carries_only_the_identity_and_waits_for_readiness() {
    let h = harness(Admission::Invite);
    let (status, head, body) = send(&h, &get("/health", None));
    assert_eq!(status, 200);
    assert!(head.contains("Cache-Control: no-store"));
    let v = json::parse(body.as_bytes()).unwrap();
    assert_eq!(
        v,
        Value::obj([
            ("app_id", Value::str(identity().app_id)),
            ("revision_sha256", Value::str("a".repeat(64))),
            ("deployment_sha256", Value::str("b".repeat(64)))
        ])
    );
    // Each of the three conditions alone keeps it not ready, with the same body.
    for step in 0..3 {
        let h = harness(Admission::Invite);
        match step {
            0 => h.script.lock().unwrap().ready = false,
            1 => h
                .server
                .readiness
                .migrations_done
                .store(false, std::sync::atomic::Ordering::SeqCst),
            _ => h
                .server
                .readiness
                .restore_done
                .store(false, std::sync::atomic::Ordering::SeqCst),
        }
        let (status, _, not_ready) = send(&h, &get("/health", None));
        assert_eq!((status, not_ready.as_str()), (503, body.as_str()), "step {step}");
        // Nothing else is served until it is ready, and nothing reaches the authenticator.
        let (status, _, other) = send(
            &h,
            &post(
                "/signin/v1/session",
                None,
                r#"{"account":"a","code":"1","passphrase":"p"}"#,
            ),
        );
        assert_eq!((status, error_word(&other).as_str()), (503, "not_ready"));
        let (status, _, _) = send(&h, &get("/app/hello", None));
        assert_eq!(status, 503);
        assert!(h.script.lock().unwrap().calls.is_empty());
    }
    let (status, _, _) = send(&h, &post("/health", None, "{}"));
    assert_eq!(status, 405);
}

#[test]
fn account_routes_need_a_session_of_this_app() {
    let h = harness(Admission::Invite);
    let (status, _, body) = send(&h, &get("/app/me", Some(TOKEN)));
    assert_eq!((status, body.as_str()), (200, r#"{"account":"ada"}"#));
    for token in [None, Some("UNKNOWN"), Some(OTHER_APP_TOKEN)] {
        let (status, _, body) = send(&h, &get("/app/me", token));
        assert_eq!(
            (status, error_word(&body).as_str()),
            (401, "session_invalid"),
            "{token:?}"
        );
    }
    for header in [
        "Authorization: Basic abc",
        "Authorization: Bearer",
        "Authorization: Bearer a b",
        "Authorization: Bearer tok-en",
        "Authorization: Bearer GOODTOKEN\r\nAuthorization: Bearer GOODTOKEN",
    ] {
        let (status, _, _) = send(&h, &format!("GET /app/me HTTP/1.1\r\n{header}\r\n\r\n"));
        assert_eq!(status, 401, "{header}");
    }
    h.script.lock().unwrap().unavailable = true;
    let (status, _, body) = send(&h, &get("/app/me", Some(TOKEN)));
    assert_eq!((status, error_word(&body).as_str()), (503, "auth_unavailable"));
    // A public route needs nothing and sees no principal, even with a token.
    let h = harness(Admission::Invite);
    let (status, _, body) = send(&h, &get("/app/hello", Some(TOKEN)));
    assert_eq!(
        (status, body.as_str()),
        (200, r#"{"signed_in":false,"now":1700000000}"#)
    );
    assert!(!h.script.lock().unwrap().calls.iter().any(|c| c.starts_with("validate")));
}

#[test]
fn request_rules() {
    let h = harness(Admission::Invite);
    let (status, _, body) = send(&h, &post("/app/echo", Some(TOKEN), r#"{"a":[1,2]}"#));
    assert_eq!((status, body.as_str()), (200, r#"{"a":[1,2]}"#));
    let cases = [
        (post("/app/echo", Some(TOKEN), "{"), 400),
        (post("/app/echo", Some(TOKEN), r#"{"a":1,"a":2}"#), 400),
        (
            post("/app/echo", Some(TOKEN), "").replace("application/json", "text/plain"),
            415,
        ),
        (
            format!("POST /app/echo HTTP/1.1\r\nAuthorization: Bearer {TOKEN}\r\nContent-Length: 2\r\n\r\n{{}}"),
            415,
        ),
        (
            "GET /app/hello HTTP/1.1\r\nContent-Length: 2\r\n\r\n{}".to_string(),
            400,
        ),
        (get("/app/nowhere", None), 404),
        (get("/app/echo", Some(TOKEN)), 405),
        (get("/app/hello?x=1", None), 404),
        (get("/signin/v1/session", None), 405),
        (get("/signin/v1/other", None), 404),
        ("GET /x HTTP/1.1\r\nTransfer-Encoding: chunked\r\n\r\n".to_string(), 501),
        ("BROKEN\r\n\r\n".to_string(), 400),
    ];
    for (raw, want) in cases {
        let (status, _, _) = send(&h, &raw);
        assert_eq!(status, want, "{raw:?}");
    }
    let (status, _, body) = send(
        &h,
        &post("/app/echo", Some(TOKEN), "{}").replace("application/json", "Application/JSON; charset=utf-8"),
    );
    assert_eq!((status, body.as_str()), (200, "{}"));
}

#[test]
fn handler_outcomes() {
    let h = harness(Admission::Invite);
    let cases = [
        ("/app/refuse", 409, "taken"),
        ("/app/bad-refusal", 500, "internal"),
        ("/app/bad-word", 500, "internal"),
        ("/app/boom", 500, "internal"),
        ("/app/nan", 500, "internal"),
    ];
    for (path, status, word) in cases {
        let (got, _, body) = send(&h, &format!("POST {path} HTTP/1.1\r\n\r\n"));
        assert_eq!((got, error_word(&body).as_str()), (status, word), "{path}");
    }
    // After a handler panicked the server still serves.
    let (status, _, _) = send(&h, &get("/app/me", Some(TOKEN)));
    assert_eq!(status, 200);
}

#[test]
fn signin_routes_map_to_the_documented_responses() {
    let h = harness(Admission::Invite);
    let (status, _, body) = send(
        &h,
        &post(
            "/signin/v1/enroll",
            None,
            r#"{"account":"ada","passphrase":"12345678","invite":"INV","extra":1}"#,
        ),
    );
    assert_eq!(
        (status, body.as_str()),
        (
            200,
            r#"{"otpauth_uri":"otpauth://totp/x","handle":"HANDLE","recovery_codes":["r1","r2"]}"#
        )
    );
    let (status, _, _) = send(
        &h,
        &post(
            "/signin/v1/enroll/confirm",
            None,
            r#"{"account":"ada","handle":"HANDLE","code":"123456"}"#,
        ),
    );
    assert_eq!(status, 204);
    let (status, _, body) = send(
        &h,
        &post(
            "/signin/v1/session",
            None,
            r#"{"account":"ada","code":"123456","passphrase":"12345678"}"#,
        ),
    );
    assert_eq!(
        (status, body.as_str()),
        (200, r#"{"token":"GOODTOKEN","account":"ada"}"#)
    );
    let (status, _, body) = send(
        &h,
        &post(
            "/signin/v1/recover/authenticator",
            None,
            r#"{"account":"ada","recovery_code":"r1","passphrase":"12345678"}"#,
        ),
    );
    assert_eq!((status, error_word(&body).as_str()), (200, ""));
    let (status, _, body) = send(
        &h,
        &post(
            "/signin/v1/recover/passphrase",
            None,
            r#"{"account":"ada","recovery_code":"r1","code":"1","new_passphrase":"87654321"}"#,
        ),
    );
    assert_eq!((status, body.as_str()), (200, r#"{"recovery_codes":["n1"]}"#));
    let (status, _, _) = send(
        &h,
        &format!("DELETE /signin/v1/session HTTP/1.1\r\nAuthorization: Bearer {TOKEN}\r\n\r\n"),
    );
    assert_eq!(status, 204);
    let (status, _, _) = send(
        &h,
        "DELETE /signin/v1/session HTTP/1.1\r\nAuthorization: Bearer UNKNOWN\r\n\r\n",
    );
    assert_eq!(status, 204); // sign-out reveals nothing about the token
    let (status, _, _) = send(
        &h,
        &format!("DELETE /signin/v1/sessions HTTP/1.1\r\nAuthorization: Bearer {TOKEN}\r\n\r\n"),
    );
    assert_eq!(status, 204);
    let (status, _, _) = send(
        &h,
        &format!("DELETE /signin/v1/sessions HTTP/1.1\r\nAuthorization: Bearer {OTHER_APP_TOKEN}\r\n\r\n"),
    );
    assert_eq!(status, 401);
    let calls = h.script.lock().unwrap().calls.clone();
    assert_eq!(
        calls,
        vec![
            "enroll ada 12345678 Some(\"INV\") false None 1700000000",
            "confirm ada HANDLE 123456 1700000000",
            "sign_in ada 123456 12345678 203.0.113.7 1700000000",
            "recover_authenticator ada r1 12345678 203.0.113.7 1700000000",
            "recover_passphrase ada r1 1 87654321 203.0.113.7 1700000000",
            "revoke GOODTOKEN",
            "revoke UNKNOWN",
            "validate GOODTOKEN 1700000000",
            "revoke_all ada",
            "validate OTHERAPPTOKEN 1700000000",
        ]
    );
    // Open admission is the app's declaration, never the request's.
    let h = harness(Admission::Open);
    send(
        &h,
        &post(
            "/signin/v1/enroll",
            None,
            r#"{"account":"ada","passphrase":"12345678","open":true}"#,
        ),
    );
    assert!(h.script.lock().unwrap().calls[0].contains(" true None"));
}

#[test]
fn signin_errors() {
    let enroll = [
        (EnrollError::AlreadyEnrolled, 409, "already_enrolled"),
        (EnrollError::Pending, 409, "enrollment_pending"),
        (EnrollError::Knowledge(Rejected::TooShort), 400, "passphrase_too_short"),
        (EnrollError::Knowledge(Rejected::TooLong), 400, "passphrase_too_long"),
        (EnrollError::InvalidName, 400, "invalid_name"),
        (EnrollError::NotConfirmed, 401, "not_confirmed"),
        (EnrollError::NotAdmitted, 403, "not_admitted"),
        (EnrollError::Unavailable, 503, "auth_unavailable"),
        (EnrollError::Limited { retry_after: 30 }, 429, "try_later"),
    ];
    for (e, status, word) in enroll {
        let h = harness(Admission::Invite);
        h.script.lock().unwrap().enroll = Some(Err(e.clone()));
        let (got, head, body) = send(
            &h,
            &post("/signin/v1/enroll", None, r#"{"account":"a","passphrase":"p"}"#),
        );
        assert_eq!((got, error_word(&body).as_str()), (status, word), "{e:?}");
        if status == 429 {
            assert!(head.contains("Retry-After: 30"));
            assert_eq!(body, r#"{"error":"try_later","retry_after":30}"#);
        }
    }
    let sign_in = [
        (SignIn::Failed, 401, "sign_in_failed"),
        (SignIn::Unavailable, 503, "auth_unavailable"),
        (SignIn::Limited { retry_after: 3600 }, 429, "try_later"),
    ];
    for (r, status, word) in sign_in {
        let h = harness(Admission::Invite);
        h.script.lock().unwrap().sign_in = Some(r.clone());
        let (got, _, body) = send(
            &h,
            &post(
                "/signin/v1/session",
                None,
                r#"{"account":"a","code":"1","passphrase":"p"}"#,
            ),
        );
        assert_eq!((got, error_word(&body).as_str()), (status, word), "{r:?}");
    }
    let h = harness(Admission::Invite);
    h.script.lock().unwrap().sign_in = Some(SignIn::Ok {
        token: "T".into(),
        principal: Principal {
            app: APP.into(),
            account: "a".into(),
        },
        new_recovery_codes: vec!["x".into()],
    });
    let (_, _, body) = send(
        &h,
        &post(
            "/signin/v1/session",
            None,
            r#"{"account":"a","code":"1","passphrase":"p"}"#,
        ),
    );
    assert_eq!(body, r#"{"token":"T","account":"a","recovery_codes":["x"]}"#);
    let recover = [
        (RecoverError::Failed, 401, "recovery_failed"),
        (RecoverError::Unavailable, 503, "auth_unavailable"),
        (RecoverError::Knowledge(Rejected::TooShort), 400, "passphrase_too_short"),
        (RecoverError::Limited { retry_after: 60 }, 429, "try_later"),
    ];
    for (e, status, word) in recover {
        let h = harness(Admission::Invite);
        h.script.lock().unwrap().recover = Some(e.clone());
        for (path, body) in [
            (
                "/signin/v1/recover/authenticator",
                r#"{"account":"a","recovery_code":"r","passphrase":"p"}"#,
            ),
            (
                "/signin/v1/recover/passphrase",
                r#"{"account":"a","recovery_code":"r","code":"1","new_passphrase":"p"}"#,
            ),
        ] {
            let (got, _, out) = send(&h, &post(path, None, body));
            assert_eq!((got, error_word(&out).as_str()), (status, word), "{path} {e:?}");
        }
    }
    // Missing or mistyped fields are a bad request, and never reach the authenticator.
    let h = harness(Admission::Invite);
    for body in [
        "[]",
        "{}",
        r#"{"account":"a"}"#,
        r#"{"account":1,"passphrase":"p"}"#,
        r#"{"account":"a","passphrase":"p","invite":5}"#,
        "not json",
    ] {
        let (got, _, out) = send(&h, &post("/signin/v1/enroll", None, body));
        assert_eq!((got, error_word(&out).as_str()), (400, "bad_request"), "{body}");
    }
    assert!(h.script.lock().unwrap().calls.is_empty());
}

#[test]
fn logs_hold_no_private_data() {
    let h = harness(Admission::Invite);
    send(
        &h,
        &post(
            "/signin/v1/session",
            None,
            r#"{"account":"secret-account","code":"999999","passphrase":"hunter22"}"#,
        ),
    );
    send(&h, &get("/app/me", Some(TOKEN)));
    send(&h, &get("/app/secret-account-path", None));
    send(&h, "WEIRDMETHOD /app/me HTTP/1.1\r\n\r\n");
    let lines = h.log.lock().unwrap().clone();
    assert_eq!(
        lines.iter().map(|l| (l.method, l.route, l.status)).collect::<Vec<_>>(),
        vec![
            ("POST", "/signin/v1", 200),
            ("GET", "/app/me", 200),
            ("GET", "-", 404),
            ("OTHER", "-", 405),
        ]
    );
    let all = format!("{lines:?}");
    for private in ["secret-account", "999999", "hunter22", TOKEN, "203.0.113.7"] {
        assert!(!all.contains(private), "{private}");
    }
    // Debug output of what carries secrets is redacted too.
    let principal = Principal {
        app: APP.into(),
        account: "secret-account".into(),
    };
    assert!(!format!("{principal:?}").contains("secret-account"));
    assert!(!format!("{:?}", enrollment()).contains("HANDLE"));
}

#[test]
fn routes_are_checked_when_the_server_is_made() {
    let fake = || Box::new(Fake(Arc::new(Mutex::new(Script::default())))) as Box<dyn Auth>;
    let r = |method, path, body| Route {
        method,
        path,
        auth: RouteAuth::Public,
        body,
        handler: hello as _,
    };
    for routes in [
        vec![r("GET", "/app/a", false), r("GET", "/app/a", false)],
        vec![r("GET", "/app/a", true)],
        vec![r("DELETE", "/app/a", true)],
        vec![r("GET", "/signin/v1/x", false)],
        vec![r("PATCH", "/app/a", false)],
        vec![r("GET", "/health", false)],
    ] {
        assert!(Server::new(APP, Admission::Invite, routes, identity(), fake()).is_err());
    }
    assert!(Server::new(
        APP,
        Admission::Invite,
        vec![r("GET", "/app/a", false), r("POST", "/app/a", true)],
        identity(),
        fake()
    )
    .is_ok());
}

#[test]
fn a_real_socket() {
    let h = harness(Admission::Invite);
    let server = Arc::new(h.server);
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let addr = listener.local_addr().unwrap();
    let serving = Arc::clone(&server);
    std::thread::spawn(move || serving.serve(listener));
    let mut s = TcpStream::connect(addr).unwrap();
    s.write_all(get("/app/me", Some(TOKEN)).as_bytes()).unwrap();
    let mut out = String::new();
    s.read_to_string(&mut out).unwrap();
    assert!(
        out.starts_with("HTTP/1.1 200 OK\r\n") && out.ends_with(r#"{"account":"ada"}"#),
        "{out}"
    );
    // The client the rate limits see is the peer's address.
    let mut s = TcpStream::connect(addr).unwrap();
    s.write_all(
        post(
            "/signin/v1/session",
            None,
            r#"{"account":"a","code":"1","passphrase":"p"}"#,
        )
        .as_bytes(),
    )
    .unwrap();
    s.read_to_string(&mut String::new()).unwrap();
    assert!(h
        .script
        .lock()
        .unwrap()
        .calls
        .iter()
        .any(|c| c.starts_with("sign_in a 1 p 127.0.0.1 ")));
}

#[test]
fn over_the_connection_limit_is_busy() {
    let mut h = harness(Admission::Invite);
    h.server.max_connections = 0;
    let server = Arc::new(h.server);
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let addr = listener.local_addr().unwrap();
    std::thread::spawn(move || server.serve(listener));
    let mut s = TcpStream::connect(addr).unwrap();
    let mut out = String::new();
    s.read_to_string(&mut out).unwrap();
    assert!(out.starts_with("HTTP/1.1 503") && out.contains("busy"), "{out}");
}

#[test]
fn a_request_struct_is_what_the_server_reads() {
    let h = harness(Admission::Invite);
    let req = Request {
        method: "GET".into(),
        target: "/app/me".into(),
        headers: vec![("authorization".into(), format!("Bearer {TOKEN}"))],
        body: vec![],
    };
    let (r, route) = h.server.respond(&req, "c");
    assert_eq!((r.status, route), (200, "/app/me"));
}

fn calls(h: &Harness) -> Vec<String> {
    h.script.lock().unwrap().calls.clone()
}

#[test]
fn the_bearer_token_is_read_strictly_before_anything_is_asked() {
    let h = harness(Admission::Invite);
    let long = "A".repeat(257);
    for header in [
        format!("Authorization: Basic {TOKEN}"),
        format!("Authorization: Token {TOKEN}"),
        format!("Authorization: Bearer {TOKEN}-"),
        format!("Authorization: Bearer  {TOKEN}"),
        format!("Authorization: Bearer {long}"),
    ] {
        let (status, _, body) = send(&h, &format!("GET /app/me HTTP/1.1\r\n{header}\r\n\r\n"));
        assert_eq!(
            (status, error_word(&body).as_str()),
            (401, "session_invalid"),
            "{header}"
        );
    }
    assert!(calls(&h).is_empty(), "{:?}", calls(&h));
    // The scheme's case does not matter; a valid token passes with the clock's time, never another.
    let (status, _, _) = send(
        &h,
        &format!("GET /app/me HTTP/1.1\r\nAuthorization: bearer {TOKEN}\r\n\r\n"),
    );
    assert_eq!(status, 200);
    assert_eq!(calls(&h), vec![format!("validate {TOKEN} 1700000000")]);
}

#[test]
fn handler_refusals_outside_4xx_or_with_bad_words_are_internal() {
    let h = harness(Admission::Invite);
    for path in [
        "/app/r101",
        "/app/r200",
        "/app/r302",
        "/app/r399",
        "/app/r-empty",
        "/app/r-upper",
        "/app/r-quote",
    ] {
        let (status, _, body) = send(&h, &format!("POST {path} HTTP/1.1\r\n\r\n"));
        assert_eq!((status, error_word(&body).as_str()), (500, "internal"), "{path}");
    }
}

#[test]
fn methods_and_paths_match_exactly() {
    let h = harness(Admission::Invite);
    for (raw, status) in [
        ("HEAD /app/hello HTTP/1.1\r\n\r\n".to_string(), 405),
        ("HEAD /health HTTP/1.1\r\n\r\n".to_string(), 405),
        (get("/app/me/", Some(TOKEN)), 404),
        (get("/APP/me", Some(TOKEN)), 404),
        (get("/app/%6de", Some(TOKEN)), 404),
        (get("/app//me", Some(TOKEN)), 404),
    ] {
        let (got, _, _) = send(&h, &raw);
        assert_eq!(got, status, "{raw:?}");
    }
    assert!(!calls(&h).iter().any(|c| c.starts_with("validate")));
    let fake = || Box::new(Fake(Arc::new(Mutex::new(Script::default())))) as Box<dyn Auth>;
    let head = Route {
        method: "HEAD",
        path: "/app/a",
        auth: RouteAuth::Public,
        body: false,
        handler: hello as _,
    };
    assert!(Server::new(APP, Admission::Invite, vec![head], identity(), fake()).is_err());
}

#[test]
fn every_signin_route_and_failure() {
    // Only DELETE on the session routes; the method is checked before any body is read.
    let h = harness(Admission::Invite);
    for body in ["", "{}"] {
        let (status, _, out) = send(&h, &post("/signin/v1/sessions", Some(TOKEN), body));
        assert_eq!(
            (status, error_word(&out).as_str()),
            (405, "method_not_allowed"),
            "{body:?}"
        );
    }
    let (status, _, _) = send(&h, "DELETE /signin/v1/session HTTP/1.1\r\n\r\n");
    assert_eq!(status, 401);
    let (status, _, _) = send(&h, "DELETE /signin/v1/sessions HTTP/1.1\r\n\r\n");
    assert_eq!(status, 401);
    let with_body =
        format!("DELETE /signin/v1/session HTTP/1.1\r\nAuthorization: Bearer {TOKEN}\r\nContent-Length: 2\r\n\r\n{{}}");
    let (status, _, _) = send(&h, &with_body);
    assert_eq!(status, 400);
    assert!(calls(&h).is_empty());
    // Sign-out when the authenticator is unavailable says so, both ways.
    h.script.lock().unwrap().unavailable = true;
    let (status, _, out) = send(
        &h,
        &format!("DELETE /signin/v1/session HTTP/1.1\r\nAuthorization: Bearer {TOKEN}\r\n\r\n"),
    );
    assert_eq!((status, error_word(&out).as_str()), (503, "auth_unavailable"));
    let (status, _, _) = send(
        &h,
        &format!("DELETE /signin/v1/sessions HTTP/1.1\r\nAuthorization: Bearer {TOKEN}\r\n\r\n"),
    );
    assert_eq!(status, 503);
    let h = harness(Admission::Invite);
    h.script.lock().unwrap().revoke_all_fails = true;
    let (status, _, _) = send(
        &h,
        &format!("DELETE /signin/v1/sessions HTTP/1.1\r\nAuthorization: Bearer {TOKEN}\r\n\r\n"),
    );
    assert_eq!(status, 503);
    // Confirmation's failures.
    for (e, status, word) in [
        (EnrollError::NotConfirmed, 401, "not_confirmed"),
        (EnrollError::Limited { retry_after: 30 }, 429, "try_later"),
        (EnrollError::Unavailable, 503, "auth_unavailable"),
    ] {
        let h = harness(Admission::Invite);
        h.script.lock().unwrap().confirm = Some(e.clone());
        let (got, head, out) = send(
            &h,
            &post(
                "/signin/v1/enroll/confirm",
                None,
                r#"{"account":"a","handle":"H","code":"1"}"#,
            ),
        );
        assert_eq!((got, error_word(&out).as_str()), (status, word), "{e:?}");
        if status == 429 {
            assert!(head.contains("Retry-After: 30"));
        }
    }
    // Enrollment: no invite under invite admission is passed as none (the authenticator refuses it), and a
    // restart's handle is passed through; a handle that is not a string is a bad request.
    let h = harness(Admission::Invite);
    send(
        &h,
        &post(
            "/signin/v1/enroll",
            None,
            r#"{"account":"ada","passphrase":"12345678"}"#,
        ),
    );
    send(
        &h,
        &post(
            "/signin/v1/enroll",
            None,
            r#"{"account":"ada","passphrase":"12345678","handle":"H"}"#,
        ),
    );
    let (status, _, _) = send(
        &h,
        &post(
            "/signin/v1/enroll",
            None,
            r#"{"account":"ada","passphrase":"p","handle":5}"#,
        ),
    );
    assert_eq!(status, 400);
    assert_eq!(
        calls(&h),
        vec![
            "enroll ada 12345678 None false None 1700000000".to_string(),
            "enroll ada 12345678 None false Some(\"H\") 1700000000".to_string(),
        ]
    );
    // Recovery's too-long passphrase, on both routes.
    let h = harness(Admission::Invite);
    h.script.lock().unwrap().recover = Some(RecoverError::Knowledge(Rejected::TooLong));
    for (path, body) in [
        (
            "/signin/v1/recover/authenticator",
            r#"{"account":"a","recovery_code":"r","passphrase":"p"}"#,
        ),
        (
            "/signin/v1/recover/passphrase",
            r#"{"account":"a","recovery_code":"r","code":"1","new_passphrase":"p"}"#,
        ),
    ] {
        let (got, _, out) = send(&h, &post(path, None, body));
        assert_eq!((got, error_word(&out).as_str()), (400, "passphrase_too_long"), "{path}");
    }
    // The account in a sign-in answer is the authenticator's, not what was typed.
    let h = harness(Admission::Invite);
    h.script.lock().unwrap().account_as = Some("Ada".into());
    let (_, _, out) = send(
        &h,
        &post(
            "/signin/v1/session",
            None,
            r#"{"account":"ada","code":"1","passphrase":"p"}"#,
        ),
    );
    assert_eq!(out, r#"{"token":"GOODTOKEN","account":"Ada"}"#);
}

#[test]
fn handlers_reach_the_servers_data() {
    let h = harness(Admission::Invite);
    let (_, _, out) = send(&h, &get("/app/data", None));
    assert_eq!(error_word(&out), "no_data");
    let h = Harness {
        server: h.server.with_data(Box::new("the adapter's databases")),
        ..h
    };
    let (status, _, out) = send(&h, &get("/app/data", None));
    assert_eq!((status, out.as_str()), (200, r#""the adapter's databases""#));
}

#[test]
fn logs_name_the_route_template_whatever_the_outcome() {
    let h = harness(Admission::Invite);
    send(&h, &get("/app/me", None));
    send(&h, "POST /app/boom HTTP/1.1\r\n\r\n");
    let lines: Vec<_> = h
        .log
        .lock()
        .unwrap()
        .iter()
        .map(|l| (l.method, l.route, l.status))
        .collect();
    assert_eq!(lines, vec![("GET", "/app/me", 401), ("POST", "/app/boom", 500)]);
}

fn listen(server: Server) -> (Arc<Server>, std::net::SocketAddr) {
    let server = Arc::new(server);
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let addr = listener.local_addr().unwrap();
    let serving = Arc::clone(&server);
    std::thread::spawn(move || serving.serve(listener));
    (server, addr)
}

fn exchange(addr: std::net::SocketAddr, raw: &[u8]) -> String {
    let mut s = TcpStream::connect(addr).unwrap();
    s.write_all(raw).unwrap();
    let mut out = Vec::new();
    s.read_to_end(&mut out).unwrap();
    String::from_utf8_lossy(&out).into_owned()
}

fn wait_until(what: impl Fn() -> bool) -> bool {
    let by = std::time::Instant::now() + std::time::Duration::from_secs(5);
    while std::time::Instant::now() < by {
        if what() {
            return true;
        }
        std::thread::sleep(std::time::Duration::from_millis(10));
    }
    false
}

#[test]
fn a_slow_client_is_cut_off_at_the_deadline_not_per_read() {
    let mut h = harness(Admission::Invite);
    h.server.max_connections = 1;
    h.server.timeout = std::time::Duration::from_millis(300);
    let (server, addr) = listen(h.server);
    let mut slow = TcpStream::connect(addr).unwrap();
    slow.write_all(b"GET /app/hello HTTP/1.1\r\nX: ").unwrap();
    let started = std::time::Instant::now();
    // One byte every 100 ms keeps each read under the timeout; the deadline still ends it.
    let trickle = std::thread::spawn(move || {
        for _ in 0..30 {
            if slow.write_all(b"a").is_err() {
                return true;
            }
            std::thread::sleep(std::time::Duration::from_millis(100));
        }
        false
    });
    assert!(wait_until(|| server.open_connections() == 1));
    assert!(
        wait_until(|| server.open_connections() == 0),
        "the slow connection still holds its slot"
    );
    assert!(
        started.elapsed() < std::time::Duration::from_secs(2),
        "{:?}",
        started.elapsed()
    );
    let out = exchange(addr, get("/app/hello", None).as_bytes());
    assert!(out.starts_with("HTTP/1.1 200"), "{out}");
    let _ = trickle.join();
}

#[test]
fn a_panic_anywhere_answers_500_and_frees_its_slot() {
    let mut h = harness(Admission::Invite);
    h.server.max_connections = 1;
    let (server, addr) = listen(h.server);
    for _ in 0..3 {
        let out = exchange(addr, get("/app/me", Some("BOOM")).as_bytes());
        assert!(out.starts_with("HTTP/1.1 500") && out.contains("internal"), "{out}");
    }
    assert!(wait_until(|| server.open_connections() == 0));
    let out = exchange(addr, get("/app/hello", None).as_bytes());
    assert!(out.starts_with("HTTP/1.1 200"), "{out}");
    // A log sink that panics is contained too.
    let h = harness(Admission::Invite);
    let server = h.server.with_log(Box::new(|_: &LogLine| panic!("log sink bug")));
    let mut server = server;
    server.max_connections = 1;
    let (server, addr) = listen(server);
    for _ in 0..3 {
        assert!(exchange(addr, get("/app/hello", None).as_bytes()).starts_with("HTTP/1.1 200"));
    }
    assert!(wait_until(|| server.open_connections() == 0));
}

#[test]
fn refusals_reach_the_client_even_with_bytes_still_unread() {
    let h = harness(Admission::Invite);
    let (_, addr) = listen(h.server);
    // A body far over the limit, partly sent: the 413 arrives whole, not as a reset.
    let mut raw = format!(
        "POST /app/echo HTTP/1.1\r\nContent-Type: application/json\r\nContent-Length: {}\r\n\r\n",
        2_000_000
    )
    .into_bytes();
    raw.extend(std::iter::repeat_n(b'x', 200_000));
    let out = exchange(addr, &raw);
    assert!(out.starts_with("HTTP/1.1 413"), "{out}");
    // A valid request followed by more bytes: its answer arrives whole.
    let mut raw = get("/app/hello", None).into_bytes();
    raw.extend(std::iter::repeat_n(b'y', 50_000));
    let out = exchange(addr, &raw);
    assert!(out.starts_with("HTTP/1.1 200") && out.ends_with("}"), "{out}");
    // Over-limit heads answer 431.
    let raw = format!("GET /app/hello HTTP/1.1\r\nX: {}\r\n\r\n", "a".repeat(20_000));
    assert!(exchange(addr, raw.as_bytes()).starts_with("HTTP/1.1 431"));
}

#[test]
fn failing_to_accept_never_stops_the_server() {
    // A non-blocking listener answers every accept with WouldBlock until a client comes: each is an error the
    // loop must survive.
    let h = harness(Admission::Invite);
    let server = Arc::new(h.server);
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    listener.set_nonblocking(true).unwrap();
    let addr = listener.local_addr().unwrap();
    let serving = Arc::clone(&server);
    std::thread::spawn(move || serving.serve(listener));
    std::thread::sleep(std::time::Duration::from_millis(100));
    let mut s = TcpStream::connect(addr).unwrap();
    s.set_read_timeout(Some(std::time::Duration::from_secs(5))).unwrap();
    s.write_all(get("/app/hello", None).as_bytes()).unwrap();
    let mut out = String::new();
    let _ = s.read_to_string(&mut out);
    assert!(out.starts_with("HTTP/1.1 200"), "{out}");
}
