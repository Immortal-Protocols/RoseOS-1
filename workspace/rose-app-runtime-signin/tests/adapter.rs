//! The adapter against real rose-signin (SQLite store, AeadSealer) and the real runtime, on temporary
//! directories. Keys are random per test; nothing is fetched and no server listens.

use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicUsize, Ordering};

use rose_app_runtime::http::Request;
use rose_app_runtime::json::{self, Value};
use rose_app_runtime::server::{Ctx, Reply, Route, RouteAuth, Server};
use rose_app_runtime::signin::{self as rt, Admission, Auth};
use rose_app_runtime_signin::data::{self, account_key};
use rose_app_runtime_signin::identity;
use rose_app_runtime_signin::{AppData, Config, DataError, Keys, Migrations, Restore, SigninAuth, StartError, start};
use rose_signin::secret::Secret;
use rose_signin::{base32, otp};
use rusqlite::Connection;

const DEPLOYMENT: &str = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef";
const T0: u64 = 1_800_000_000;
const PASS: &str = "correct horse battery";

static ACCOUNT_V1: &[&str] = &["CREATE TABLE notes (id INTEGER PRIMARY KEY, body TEXT NOT NULL);"];
static ACCOUNT_V2: &[&str] = &[
    "CREATE TABLE notes (id INTEGER PRIMARY KEY, body TEXT NOT NULL);",
    "ALTER TABLE notes ADD COLUMN pinned INTEGER NOT NULL DEFAULT 0;",
];
static SHARED_V1: &[&str] = &["CREATE TABLE board (id INTEGER PRIMARY KEY, title TEXT NOT NULL);"];

/// A fresh directory under the system temp dir, removed when dropped.
struct Dir(PathBuf);

impl Dir {
    fn new() -> Dir {
        static N: AtomicUsize = AtomicUsize::new(0);
        let path = std::env::temp_dir().join(format!(
            "rose-app-runtime-signin-{}-{}",
            std::process::id(),
            N.fetch_add(1, Ordering::SeqCst)
        ));
        let _ = std::fs::remove_dir_all(&path);
        std::fs::create_dir_all(&path).unwrap();
        Dir(path)
    }
}

impl Drop for Dir {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}

fn migrations(account: &'static [&'static str], shared: Option<&'static [&'static str]>) -> Migrations {
    Migrations { account, shared }
}

fn config(dir: &Path, admission: Admission, m: Migrations) -> Config {
    Config {
        app: "notes".into(),
        issuer: "Rose Notes".into(),
        admission,
        app_id: "6f1c2a0e-1b7d-4c55-9a7e-2f3b4c5d6e7f".into(),
        data_dir: dir.to_path_buf(),
        deployment_sha256: DEPLOYMENT.into(),
        expected_revision: None,
        restore_marker: dir.join(".chariot-restore.json"),
        migrations: m,
    }
}

fn keys() -> Keys {
    Keys::Supplied {
        current: 1,
        keys: vec![(1, Secret::random(32))],
    }
}

/// The current code for an enrollment's otpauth URI, as the user's authenticator shows it.
fn code(otpauth_uri: &str, now: u64) -> String {
    let secret = otpauth_uri
        .split(['?', '&'])
        .find_map(|p| p.strip_prefix("secret="))
        .expect("otpauth URI has a secret");
    otp::totp(&base32::decode(secret).unwrap(), otp::V1, now)
}

/// An account route over the account's own database, reached through `ctx.data` as a generated handler does.
fn me(ctx: &Ctx, _: Option<&Value>) -> Reply {
    let (Some(p), Some(data)) = (ctx.principal, ctx.data.downcast_ref::<AppData>()) else {
        return Reply::Refused(400, "no_data");
    };
    let Ok(conn) = data.account(p) else {
        return Reply::Refused(409, "no_database");
    };
    let notes: i64 = conn
        .query_row("SELECT count(*) FROM notes", [], |r| r.get(0))
        .unwrap_or(-1);
    Reply::Ok(Value::obj([
        ("account", Value::str(p.account.clone())),
        ("notes", Value::Int(notes)),
    ]))
}

fn routes() -> Vec<Route> {
    vec![Route {
        method: "GET",
        path: "/app/me",
        auth: RouteAuth::Account,
        body: false,
        handler: me,
    }]
}

fn request(method: &str, target: &str, body: Option<Value>, token: Option<&str>) -> Request {
    let mut headers = Vec::new();
    if let Some(t) = token {
        headers.push(("authorization".to_string(), format!("Bearer {t}")));
    }
    let body = match body {
        Some(v) => {
            headers.push(("content-type".to_string(), "application/json".to_string()));
            json::to_string(&v).unwrap().into_bytes()
        }
        None => Vec::new(),
    };
    Request {
        method: method.into(),
        target: target.into(),
        headers,
        body,
    }
}

fn call(server: &Server, req: Request) -> (u16, Value) {
    let (r, _) = server.respond(&req, "198.51.100.7");
    let v = if r.body.is_empty() {
        Value::Null
    } else {
        json::parse(&r.body).unwrap()
    };
    (r.status, v)
}

fn field(v: &Value, key: &str) -> String {
    v.get(key).and_then(Value::as_str).unwrap_or_default().to_string()
}

/// A started server with keys and no restore marker: ready.
fn server(dir: &Path, admission: Admission) -> Server {
    let started = start(config(dir, admission, migrations(ACCOUNT_V1, None)), keys()).unwrap();
    started.server(routes()).unwrap()
}

#[test]
fn enroll_confirm_sign_in_and_call_an_account_route_over_http() {
    let dir = Dir::new();
    let server = server(&dir.0, Admission::Open);
    let server = server.with_clock(Box::new(|| T0));
    assert_eq!(call(&server, request("GET", "/health", None, None)).0, 200);

    let enroll = Value::obj([("account", Value::str("ana")), ("passphrase", Value::str(PASS))]);
    let (status, e) = call(&server, request("POST", "/signin/v1/enroll", Some(enroll), None));
    assert_eq!(status, 200, "{e:?}");
    let (uri, handle) = (field(&e, "otpauth_uri"), field(&e, "handle"));
    assert!(uri.starts_with("otpauth://totp/"), "{uri}");
    assert!(
        handle.bytes().all(|b| b.is_ascii_uppercase() || b.is_ascii_digit()),
        "base32, no padding"
    );
    assert_eq!(
        e.get("recovery_codes")
            .map(|c| matches!(c, Value::Arr(a) if a.len() == 10)),
        Some(true)
    );

    let confirm = Value::obj([
        ("account", Value::str("ana")),
        ("handle", Value::str(handle)),
        ("code", Value::str(code(&uri, T0))),
    ]);
    assert_eq!(
        call(
            &server,
            request("POST", "/signin/v1/enroll/confirm", Some(confirm), None)
        )
        .0,
        204
    );

    // The confirming code's step is spent; sign in with the next step's code.
    let next = T0 + 30;
    let server = server.with_clock(Box::new(move || next));
    let sign_in = Value::obj([
        ("account", Value::str("ana")),
        ("code", Value::str(code(&uri, next))),
        ("passphrase", Value::str(PASS)),
    ]);
    let (status, s) = call(&server, request("POST", "/signin/v1/session", Some(sign_in), None));
    assert_eq!(status, 200, "{s:?}");
    let token = field(&s, "token");
    assert_eq!(field(&s, "account"), "ana");
    assert!(token.bytes().all(|b| b.is_ascii_alphanumeric()) && token.len() <= 256);

    let (status, m) = call(&server, request("GET", "/app/me", None, Some(&token)));
    assert_eq!((status, field(&m, "account")), (200, "ana".to_string()), "{m:?}");
    assert_eq!(
        m.get("notes"),
        Some(&Value::Int(0)),
        "the handler opened ana's own migrated database"
    );
    let ana = AppData::new(&dir.0, "notes", migrations(ACCOUNT_V1, None))
        .unwrap()
        .account_path("ana");
    assert!(ana.exists());

    assert_eq!(
        call(&server, request("DELETE", "/signin/v1/session", None, Some(&token))).0,
        204
    );
    assert_eq!(call(&server, request("GET", "/app/me", None, Some(&token))).0, 401);
}

#[test]
fn without_a_key_the_server_is_honestly_not_ready() {
    let dir = Dir::new();
    let started = start(
        config(&dir.0, Admission::Open, migrations(ACCOUNT_V1, None)),
        Keys::Unavailable,
    )
    .unwrap();
    assert_eq!(started.restore, Restore::NotIndicated);
    assert!(!started.auth.ready());
    let server = started.server(routes()).unwrap();
    assert!(
        server.readiness.restore_done.load(Ordering::SeqCst),
        "only the key is missing"
    );
    let (status, h) = call(&server, request("GET", "/health", None, None));
    assert_eq!(status, 503);
    assert_eq!(field(&h, "deployment_sha256"), DEPLOYMENT, "the host's value, echoed");
    assert_eq!(field(&h, "app_id"), "6f1c2a0e-1b7d-4c55-9a7e-2f3b4c5d6e7f");
    let enroll = Value::obj([("account", Value::str("ana")), ("passphrase", Value::str(PASS))]);
    assert_eq!(
        call(&server, request("POST", "/signin/v1/enroll", Some(enroll), None)).0,
        503
    );
}

#[test]
fn with_a_key_and_no_restore_marker_the_server_is_ready() {
    let dir = Dir::new();
    let started = start(config(&dir.0, Admission::Open, migrations(ACCOUNT_V1, None)), keys()).unwrap();
    assert!(started.auth.ready());
    assert_eq!(started.restore, Restore::NotIndicated);
    let server = started.server(routes()).unwrap();
    assert!(server.readiness.migrations_done.load(Ordering::SeqCst));
    assert!(
        server.readiness.restore_done.load(Ordering::SeqCst),
        "no marker: no restore indicated"
    );
    assert_eq!(call(&server, request("GET", "/health", None, None)).0, 200);
}

/// What can be at the marker's path, each of which indicates a restore (Chariot `e592c4b1`: any marker blocks).
fn markers(dir: &Path) -> Vec<(&'static str, PathBuf)> {
    let file = dir.join("file-marker");
    std::fs::write(&file, b"{}").unwrap();
    let empty = dir.join("empty-marker");
    std::fs::write(&empty, b"").unwrap();
    let not_json = dir.join("not-json-marker");
    std::fs::write(&not_json, b"\xff not json").unwrap();
    let directory = dir.join("dir-marker");
    std::fs::create_dir(&directory).unwrap();
    let mut out = vec![
        ("file", file),
        ("empty", empty),
        ("not json", not_json),
        ("directory", directory),
    ];
    #[cfg(unix)]
    {
        let dangling = dir.join("dangling-marker");
        std::os::unix::fs::symlink(dir.join("nowhere"), &dangling).unwrap();
        out.push(("dangling link", dangling));
    }
    out
}

#[test]
fn any_restore_marker_keeps_the_server_not_ready_and_is_left_alone() {
    let markers_dir = Dir::new();
    for (what, marker) in markers(&markers_dir.0) {
        let dir = Dir::new();
        let before = std::fs::symlink_metadata(&marker).unwrap();
        let bytes = std::fs::read(&marker).ok();
        let mut c = config(&dir.0, Admission::Open, migrations(ACCOUNT_V1, None));
        c.restore_marker = marker.clone();
        let started = start(c, keys()).unwrap();
        assert_eq!(started.restore, Restore::Indicated, "{what}");
        let server = started.server(routes()).unwrap();
        assert!(!server.readiness.restore_done.load(Ordering::SeqCst), "{what}");
        let (status, h) = call(&server, request("GET", "/health", None, None));
        assert_eq!(status, 503, "{what}");
        assert_eq!(
            field(&h, "deployment_sha256"),
            DEPLOYMENT,
            "identity is still reported: {what}"
        );
        // Never read, parsed, run, removed or completed: still there, same type, same bytes.
        let after = std::fs::symlink_metadata(&marker).unwrap();
        assert_eq!(after.file_type(), before.file_type(), "{what}");
        assert_eq!(std::fs::read(&marker).ok(), bytes, "{what}");
    }
}

#[test]
fn the_default_marker_is_in_the_data_dir() {
    let env = |dir: PathBuf, restore: Option<&'static str>| {
        move |name: &str| match name {
            "DATA_DIR" => Some(dir.to_str().unwrap().to_string()),
            "CHARIOT_APP_ID" => Some("app-1".into()),
            "CHARIOT_DEPLOYMENT_SHA256" => Some(DEPLOYMENT.into()),
            "CHARIOT_RESTORE_STATE_PATH" => restore.map(str::to_string),
            _ => None,
        }
    };
    let dir = Dir::new();
    let m = migrations(ACCOUNT_V1, None);
    let c = Config::from_lookup("notes", "Rose Notes", Admission::Open, m, env(dir.0.clone(), None)).unwrap();
    assert_eq!(c.restore_marker, dir.0.join(".chariot-restore.json"));
    assert_eq!(start(c.clone(), keys()).unwrap().restore, Restore::NotIndicated);
    std::fs::write(dir.0.join(".chariot-restore.json"), b"{}").unwrap();
    assert_eq!(start(c, keys()).unwrap().restore, Restore::Indicated);

    let named = Config::from_lookup(
        "notes",
        "Rose Notes",
        Admission::Open,
        m,
        env(dir.0.clone(), Some("/run/x/state")),
    )
    .unwrap();
    assert_eq!(named.restore_marker, PathBuf::from("/run/x/state"));
    let relative = Config::from_lookup(
        "notes",
        "Rose Notes",
        Admission::Open,
        m,
        env(dir.0.clone(), Some("state")),
    )
    .unwrap();
    assert!(matches!(
        start(relative, keys()),
        Err(StartError::Invalid("CHARIOT_RESTORE_STATE_PATH"))
    ));
    // What from_env keeps for a set but non-UTF-8 value: empty, refused, never the default.
    let unreadable =
        Config::from_lookup("notes", "Rose Notes", Admission::Open, m, env(dir.0.clone(), Some(""))).unwrap();
    assert!(matches!(
        start(unreadable, keys()),
        Err(StartError::Invalid("CHARIOT_RESTORE_STATE_PATH"))
    ));
}

#[test]
fn a_marker_that_cannot_be_looked_up_is_not_ready() {
    let dir = Dir::new();
    // A path through a regular file is an error (not a directory), not absence.
    let plain = dir.0.join("plain");
    std::fs::write(&plain, b"").unwrap();
    assert_eq!(
        rose_app_runtime_signin::start::restore_state(&plain.join("marker")),
        Restore::Unknown
    );
    let mut c = config(&dir.0, Admission::Open, migrations(ACCOUNT_V1, None));
    c.restore_marker = plain.join("marker");
    let server = start(c, keys()).unwrap().server(routes()).unwrap();
    assert!(!server.readiness.restore_done.load(Ordering::SeqCst));
    assert_eq!(call(&server, request("GET", "/health", None, None)).0, 503);
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let locked = dir.0.join("locked");
        std::fs::create_dir(&locked).unwrap();
        std::fs::set_permissions(&locked, std::fs::Permissions::from_mode(0o000)).unwrap();
        let state = rose_app_runtime_signin::start::restore_state(&locked.join(".chariot-restore.json"));
        std::fs::set_permissions(&locked, std::fs::Permissions::from_mode(0o700)).unwrap();
        // As root, permissions do not stop the lookup and the answer is absence; otherwise it is an error.
        assert!(matches!(state, Restore::Unknown | Restore::NotIndicated), "{state:?}");
    }
}

#[test]
fn revision_is_the_binary_and_the_identity_is_never_made_up() {
    let dir = Dir::new();
    let started = start(config(&dir.0, Admission::Open, migrations(ACCOUNT_V1, None)), keys()).unwrap();
    let exe = std::env::current_exe().unwrap();
    assert_eq!(started.identity.revision_sha256, identity::file_sha256(&exe).unwrap());
    assert!(identity::is_sha256_hex(&started.identity.revision_sha256));

    let bad = |f: &dyn Fn(&mut Config)| {
        let mut c = config(&dir.0, Admission::Open, migrations(ACCOUNT_V1, None));
        f(&mut c);
        start(c, Keys::Unavailable).unwrap_err().to_string()
    };
    assert!(bad(&|c| c.deployment_sha256 = "".into()).contains("CHARIOT_DEPLOYMENT_SHA256"));
    assert!(bad(&|c| c.deployment_sha256 = DEPLOYMENT.to_uppercase()).contains("CHARIOT_DEPLOYMENT_SHA256"));
    assert!(bad(&|c| c.app_id = "".into()).contains("CHARIOT_APP_ID"));
    assert!(bad(&|c| c.app_id = "has space".into()).contains("CHARIOT_APP_ID"));
    assert!(bad(&|c| c.data_dir = "relative/dir".into()).contains("DATA_DIR"));
    assert!(bad(&|c| c.app = "a:b".into()).contains("app name"));
    assert!(bad(&|c| c.issuer = "".into()).contains("issuer"));
    assert!(matches!(
        start(
            config(&dir.0, Admission::Open, migrations(ACCOUNT_V1, None)),
            Keys::Supplied {
                current: 1,
                keys: vec![(1, Secret::new(vec![0; 16]))]
            }
        ),
        Err(StartError::Invalid("a sealing key"))
    ));
}

#[test]
fn the_environment_supplies_the_data_dir_app_id_deployment_and_revision() {
    let m = migrations(ACCOUNT_V1, None);
    let env = |pairs: &'static [(&'static str, &'static str)]| {
        move |name: &str| pairs.iter().find(|(k, _)| *k == name).map(|(_, v)| v.to_string())
    };
    let all: &'static [(&'static str, &'static str)] = &[
        ("DATA_DIR", "/data"),
        ("CHARIOT_APP_ID", "app-1"),
        ("CHARIOT_DEPLOYMENT_SHA256", DEPLOYMENT),
    ];
    let c = Config::from_lookup("notes", "Rose Notes", Admission::Invite, m, env(all)).unwrap();
    assert_eq!(
        (c.data_dir, c.deployment_sha256, c.app_id, c.expected_revision),
        (PathBuf::from("/data"), DEPLOYMENT.into(), "app-1".into(), None)
    );
    let with_revision = Config::from_lookup(
        "notes",
        "Rose Notes",
        Admission::Invite,
        m,
        env(&[
            ("DATA_DIR", "/data"),
            ("CHARIOT_APP_ID", "app-1"),
            ("CHARIOT_DEPLOYMENT_SHA256", DEPLOYMENT),
            ("CHARIOT_REVISION_SHA256", DEPLOYMENT),
        ]),
    )
    .unwrap();
    assert_eq!(with_revision.expected_revision.as_deref(), Some(DEPLOYMENT));
    let missing = |pairs| Config::from_lookup("notes", "Rose Notes", Admission::Invite, m, env(pairs));
    for (pairs, name) in [
        (&all[1..], "DATA_DIR"),
        (&[all[0], all[2]][..], "CHARIOT_APP_ID"),
        (&all[..2], "CHARIOT_DEPLOYMENT_SHA256"),
    ] {
        let pairs: &'static [(&'static str, &'static str)] = Box::leak(pairs.to_vec().into_boxed_slice());
        assert!(
            matches!(missing(pairs), Err(StartError::Missing(n)) if n == name),
            "{name}"
        );
    }
}

#[test]
fn a_binary_the_host_did_not_deploy_is_not_ready() {
    let dir = Dir::new();
    let exe = identity::file_sha256(&std::env::current_exe().unwrap()).unwrap();
    let mut c = config(&dir.0, Admission::Open, migrations(ACCOUNT_V1, None));
    c.expected_revision = Some(exe.clone());
    let same = start(c, keys()).unwrap();
    assert!(same.revision_matches && same.auth.ready());

    let mut c = config(&dir.0, Admission::Open, migrations(ACCOUNT_V1, None));
    c.expected_revision = Some(DEPLOYMENT.into());
    let other = start(c, keys()).unwrap();
    assert!(
        !other.revision_matches && !other.auth.ready(),
        "held whatever the sealer says"
    );
    assert_eq!(
        other.identity.revision_sha256, exe,
        "the measured revision is still reported"
    );
    let server = other.server(routes()).unwrap();
    server.readiness.restore_done.store(true, Ordering::SeqCst);
    assert_eq!(call(&server, request("GET", "/health", None, None)).0, 503);

    let mut c = config(&dir.0, Admission::Open, migrations(ACCOUNT_V1, None));
    c.expected_revision = Some("not a hash".into());
    assert!(matches!(
        start(c, keys()),
        Err(StartError::Invalid("CHARIOT_REVISION_SHA256"))
    ));
    // What from_env keeps for a set but non-UTF-8 value: refused, never read as "no revision check".
    let mut c = config(&dir.0, Admission::Open, migrations(ACCOUNT_V1, None));
    c.expected_revision = Some(String::new());
    assert!(matches!(
        start(c, keys()),
        Err(StartError::Invalid("CHARIOT_REVISION_SHA256"))
    ));
}

/// A started authenticator used directly through the runtime's `Auth`.
fn auth(dir: &Path, app: &str, admission: Admission) -> SigninAuth {
    let mut c = config(dir, admission, migrations(ACCOUNT_V1, None));
    c.app = app.into();
    start(c, keys()).unwrap().auth
}

fn enrolled(a: &mut SigninAuth, account: &str, invite: Option<&str>, open: bool) -> (String, String) {
    let e = a.begin_enrollment(account, PASS, invite, open, None, T0).unwrap();
    a.confirm_enrollment(account, &e.handle, &code(&e.otpauth_uri, T0), T0)
        .unwrap();
    let rt::SignIn::Ok { token, .. } = a.sign_in(account, &code(&e.otpauth_uri, T0 + 30), PASS, "c", T0 + 30) else {
        panic!("sign-in failed");
    };
    (token, e.otpauth_uri)
}

#[test]
fn closed_admission_needs_the_creators_invite() {
    let dir = Dir::new();
    let mut a = auth(&dir.0, "notes", Admission::Invite);
    for invite in [None, Some(""), Some("not base32!"), Some("AAAAAAAA")] {
        assert_eq!(
            a.begin_enrollment("ana", PASS, invite, false, None, T0).unwrap_err(),
            rt::EnrollError::NotAdmitted,
            "{invite:?}"
        );
    }
    let invite = a.create_invite("ana", T0).unwrap();
    assert!(base32::decode(&invite).is_some());
    assert_eq!(
        a.begin_enrollment("bob", PASS, Some(&invite), false, None, T0)
            .unwrap_err(),
        rt::EnrollError::NotAdmitted,
        "an invite admits only the account it names"
    );
    let (token, _) = enrolled(&mut a, "ana", Some(&invite), false);
    assert_eq!(
        a.validate(&token, T0 + 31).unwrap(),
        Some(rt::Principal {
            app: "notes".into(),
            account: "ana".into()
        })
    );
}

/// Values cross HTTP exactly as rose-signin mints them (docs/ROSE-SIGNIN-V1.md: 256 random bits, base32), so a
/// value from rose-signin itself, not only from this adapter, is accepted.
#[test]
fn handles_tokens_and_invites_are_rose_signins_own_text() {
    let dir = Dir::new();
    let mut a = auth(&dir.0, "notes", Admission::Invite);
    // An invite minted by rose-signin directly (as owner tooling would), on the same store.
    let store =
        rose_signin::store::sqlite::SqliteStore::new(Connection::open(dir.0.join("auth.sqlite3")).unwrap()).unwrap();
    let sealer = rose_signin::secret::AeadSealer::new(None, Vec::new()).unwrap();
    let mut owner = rose_signin::flow::Authenticator::new(store, sealer, "Rose Notes").unwrap();
    let minted = owner.create_invite("notes", "ana", T0).unwrap();
    let minted = std::str::from_utf8(minted.expose()).unwrap().to_string();
    assert_eq!(minted.len(), 52);
    let e = a.begin_enrollment("ana", PASS, Some(&minted), false, None, T0).unwrap();
    for value in [&e.handle, &a.create_invite("bob", T0).unwrap()] {
        assert_eq!(value.len(), 52, "{value}");
        assert_eq!(base32::decode(value).map(|b| b.len()), Some(32));
    }
    a.confirm_enrollment("ana", &e.handle, &code(&e.otpauth_uri, T0), T0)
        .unwrap();
    let rt::SignIn::Ok { token, .. } = a.sign_in("ana", &code(&e.otpauth_uri, T0 + 30), PASS, "c", T0 + 30) else {
        panic!("sign-in failed");
    };
    assert_eq!(token.len(), 52);
    assert_eq!(base32::decode(&token).map(|b| b.len()), Some(32));
    assert!(a.validate(&token, T0 + 31).unwrap().is_some());
    // The same bits in another spelling are another value.
    assert_eq!(a.validate(&token.to_lowercase(), T0 + 31).unwrap(), None);
}

#[test]
fn another_apps_session_is_no_session_and_is_left_alone() {
    let dir = Dir::new();
    let mut notes = auth(&dir.0, "notes", Admission::Open);
    let mut other = auth(&dir.0, "other", Admission::Open);
    let (theirs, _) = enrolled(&mut other, "ana", None, true);
    assert_eq!(notes.validate(&theirs, T0 + 31).unwrap(), None);
    notes.revoke(&theirs).unwrap();
    notes
        .revoke_all(&rt::Principal {
            app: "other".into(),
            account: "ana".into(),
        })
        .unwrap();
    assert!(
        other.validate(&theirs, T0 + 32).unwrap().is_some(),
        "untouched by the other app"
    );
}

#[test]
fn malformed_values_are_wrong_values_never_errors() {
    let dir = Dir::new();
    let mut a = auth(&dir.0, "notes", Admission::Open);
    for token in ["", "0189", "abc=", "!!", &"A".repeat(300)] {
        assert_eq!(a.validate(token, T0).unwrap(), None, "{token}");
        a.revoke(token).unwrap();
    }
    let e = a.begin_enrollment("ana", PASS, None, true, None, T0).unwrap();
    assert_eq!(
        a.confirm_enrollment("ana", "not base32!", &code(&e.otpauth_uri, T0), T0),
        Err(rt::EnrollError::NotConfirmed)
    );
    // Restarting someone else's pending enrollment needs its handle: a bad one is refused.
    assert_eq!(
        a.begin_enrollment("ana", PASS, None, true, Some("0189"), T0)
            .unwrap_err(),
        rt::EnrollError::Pending
    );
    assert!(
        a.begin_enrollment("ana", PASS, None, true, Some(&e.handle), T0).is_ok(),
        "own handle restarts"
    );
    assert_eq!(
        a.begin_enrollment("bad:name", PASS, None, true, None, T0).unwrap_err(),
        rt::EnrollError::InvalidName
    );
    assert_eq!(
        a.begin_enrollment("carl", "short", None, true, None, T0).unwrap_err(),
        rt::EnrollError::Knowledge(rt::Rejected::TooShort)
    );
}

#[test]
fn recovery_maps_onto_the_runtime() {
    let dir = Dir::new();
    let mut a = auth(&dir.0, "notes", Admission::Open);
    let e = a.begin_enrollment("ana", PASS, None, true, None, T0).unwrap();
    a.confirm_enrollment("ana", &e.handle, &code(&e.otpauth_uri, T0), T0)
        .unwrap();
    assert_eq!(
        a.recover_passphrase(
            "ana",
            "WRONG-CODE",
            &code(&e.otpauth_uri, T0 + 30),
            "a new passphrase",
            "c",
            T0 + 30
        ),
        Err(rt::RecoverError::Failed)
    );
    let fresh = a
        .recover_passphrase(
            "ana",
            &e.recovery_codes[0],
            &code(&e.otpauth_uri, T0 + 60),
            "a new passphrase",
            "c",
            T0 + 60,
        )
        .unwrap();
    assert_eq!(fresh.len(), 10);
    // The new set replaces the old one.
    assert_eq!(
        a.recover_authenticator("ana", &e.recovery_codes[1], "a new passphrase", "c", T0 + 90)
            .unwrap_err(),
        rt::RecoverError::Failed
    );
    let again = a
        .recover_authenticator("ana", &fresh[0], "a new passphrase", "c", T0 + 90)
        .unwrap();
    assert!(again.otpauth_uri.starts_with("otpauth://totp/") && !again.handle.is_empty());
}

#[test]
fn secrets_never_print() {
    let dir = Dir::new();
    let k = Keys::Supplied {
        current: 7,
        keys: vec![(7, Secret::new(vec![0xAB; 32]))],
    };
    assert_eq!(format!("{k:?}"), "Keys::Supplied(current 7, versions [7])");
    let mut a = auth(&dir.0, "notes", Admission::Open);
    let e = a.begin_enrollment("ana", PASS, None, true, None, T0).unwrap();
    let printed = format!("{a:?} {e:?}");
    assert!(!printed.contains(&e.handle) && !printed.contains(&e.recovery_codes[0]) && !printed.contains("secret="));
}

#[test]
fn each_account_has_its_own_file_named_by_a_hash() {
    let dir = Dir::new();
    let d = AppData::new(&dir.0, "notes", migrations(ACCOUNT_V1, None)).unwrap();
    let ana = rt::Principal {
        app: "notes".into(),
        account: "ana".into(),
    };
    let bob = rt::Principal {
        app: "notes".into(),
        account: "bob".into(),
    };
    d.account(&ana)
        .unwrap()
        .execute("INSERT INTO notes (body) VALUES ('mine')", [])
        .unwrap();
    let n: i64 = d
        .account(&bob)
        .unwrap()
        .query_row("SELECT count(*) FROM notes", [], |r| r.get(0))
        .unwrap();
    assert_eq!(n, 0, "bob's file is not ana's");
    let path = d.account_path("ana");
    assert!(path.exists() && !path.to_string_lossy().contains("ana"));
    assert_eq!(path.file_stem().unwrap().to_str().unwrap(), account_key("notes", "ana"));
    assert_ne!(account_key("notes", "ana"), account_key("other", "ana"));
    // Length-prefixed: no (app, account) split collides with another.
    assert_ne!(account_key("ab", "c"), account_key("a", "bc"));
    assert_eq!(
        d.account(&rt::Principal {
            app: "other".into(),
            account: "ana".into()
        })
        .unwrap_err(),
        DataError::NotThisApp
    );
    assert_eq!(d.shared().unwrap_err(), DataError::NoShared);
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let mode = std::fs::metadata(dir.0.join("accounts")).unwrap().permissions().mode();
        assert_eq!(mode & 0o077, 0, "accounts/ is the server's only");
    }
}

fn version(path: &Path) -> i64 {
    Connection::open(path)
        .unwrap()
        .query_row("PRAGMA user_version", [], |r| r.get(0))
        .unwrap()
}

#[test]
fn migrations_go_forward_only_and_a_newer_schema_is_refused() {
    let dir = Dir::new();
    let ana = rt::Principal {
        app: "notes".into(),
        account: "ana".into(),
    };
    let v1 = AppData::new(&dir.0, "notes", migrations(ACCOUNT_V1, None)).unwrap();
    v1.account(&ana).unwrap();
    assert_eq!(version(&v1.account_path("ana")), 1);

    // A later build migrates the existing file at start-up, once.
    let started = start(
        config(&dir.0, Admission::Open, migrations(ACCOUNT_V2, Some(SHARED_V1))),
        keys(),
    )
    .unwrap();
    assert_eq!(started.accounts_migrated, 1);
    assert_eq!(version(&v1.account_path("ana")), 2);
    assert_eq!(version(&dir.0.join("shared.sqlite3")), 1);
    started
        .data
        .account(&ana)
        .unwrap()
        .execute("INSERT INTO notes (body, pinned) VALUES ('x', 1)", [])
        .unwrap();

    // The older build refuses the newer file, on open and at start-up, and leaves it untouched.
    assert_eq!(
        v1.account(&ana).unwrap_err(),
        DataError::NewerSchema { found: 2, known: 1 }
    );
    match start(config(&dir.0, Admission::Open, migrations(ACCOUNT_V1, None)), keys()) {
        Err(StartError::Data(DataError::NewerSchema { found: 2, known: 1 })) => {}
        other => panic!("{other:?}"),
    }
    assert_eq!(version(&v1.account_path("ana")), 2);
}

#[test]
fn a_failing_migration_changes_nothing() {
    let dir = Dir::new();
    static BROKEN: &[&str] = &[
        "CREATE TABLE notes (id INTEGER PRIMARY KEY, body TEXT NOT NULL);",
        "CREATE TABLE extra (id INTEGER); SELECT * FROM no_such_table;",
    ];
    let ana = rt::Principal {
        app: "notes".into(),
        account: "ana".into(),
    };
    let d = AppData::new(&dir.0, "notes", migrations(BROKEN, None)).unwrap();
    assert!(matches!(d.account(&ana), Err(DataError::Storage(_))));
    let conn = Connection::open(d.account_path("ana")).unwrap();
    assert_eq!(version(&d.account_path("ana")), 0);
    let tables: i64 = conn
        .query_row("SELECT count(*) FROM sqlite_master", [], |r| r.get(0))
        .unwrap();
    assert_eq!(tables, 0, "the first migration rolled back with the failing one");
}

#[test]
fn start_up_checks_only_account_databases_and_refuses_a_newer_sign_in_store() {
    let dir = Dir::new();
    let started = start(config(&dir.0, Admission::Open, migrations(ACCOUNT_V1, None)), keys()).unwrap();
    drop(started);
    std::fs::write(dir.0.join("accounts").join("README"), "not a database").unwrap();
    std::fs::write(
        dir.0.join("accounts").join(format!("{}.sqlite3-wal", "a".repeat(64))),
        "",
    )
    .unwrap();
    let started = start(config(&dir.0, Admission::Open, migrations(ACCOUNT_V1, None)), keys()).unwrap();
    assert_eq!(started.accounts_migrated, 0);
    drop(started);

    let conn = Connection::open(dir.0.join("auth.sqlite3")).unwrap();
    conn.execute("UPDATE auth_meta SET value = 99 WHERE key = 'schema_version'", [])
        .unwrap();
    drop(conn);
    match start(config(&dir.0, Admission::Open, migrations(ACCOUNT_V1, None)), keys()) {
        Err(StartError::Store(m)) => assert!(m.contains("newer"), "{m}"),
        other => panic!("{other:?}"),
    }
}

#[test]
fn app_data_is_shareable_across_request_threads() {
    fn shareable<T: Send + Sync + 'static>() {}
    shareable::<AppData>();
    fn sendable<T: Send>() {}
    sendable::<SigninAuth>();
    // Concurrent first opens of one account migrate it once.
    let dir = Dir::new();
    let d = std::sync::Arc::new(AppData::new(&dir.0, "notes", migrations(ACCOUNT_V2, None)).unwrap());
    let threads: Vec<_> = (0..8)
        .map(|_| {
            let d = d.clone();
            std::thread::spawn(move || {
                d.account(&rt::Principal {
                    app: "notes".into(),
                    account: "ana".into(),
                })
                .map(|_| ())
            })
        })
        .collect();
    for t in threads {
        t.join().unwrap().unwrap();
    }
    assert_eq!(version(&d.account_path("ana")), 2);
    data::migrate(&Connection::open(d.account_path("ana")).unwrap(), ACCOUNT_V2).unwrap();
    assert_eq!(version(&d.account_path("ana")), 2, "idempotent");
}

/// The adapter adds no crate: its lock is rose-signin's plus the two path crates.
#[test]
fn the_lock_adds_no_crate() {
    let packages = |path: &str| -> Vec<String> {
        let text = std::fs::read_to_string(Path::new(env!("CARGO_MANIFEST_DIR")).join(path)).unwrap();
        let mut out: Vec<String> = text
            .split("[[package]]")
            .skip(1)
            .map(|block| {
                let keep = ["name = ", "version = ", "source = ", "checksum = "];
                let lines: Vec<&str> = block
                    .lines()
                    .filter(|l| keep.iter().any(|k| l.starts_with(k)))
                    .collect();
                lines.join("\n")
            })
            .filter(|b| !b.starts_with("name = \"rose-app-runtime"))
            .collect();
        out.sort();
        out
    };
    let ours = packages("Cargo.lock");
    let theirs = packages("../rose-signin/Cargo.lock");
    assert_eq!(ours.len(), 52);
    assert_eq!(ours, theirs);
}
