//! The creator's invite (D7) against real rose-signin on temporary directories: no keyring is read, the token admits
//! exactly its account once, only an existing store is used, no invite is issued while a restore is indicated, and
//! it can be issued while a serving server holds the store.

use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicUsize, Ordering};
use std::time::Duration;

use rose_app_runtime::signin::{self as rt, Admission, Auth};
use rose_app_runtime_signin::rusqlite::Connection;
use rose_app_runtime_signin::{Config, InviteError, Keys, Migrations, SigninAuth, invite, start};
use rose_signin::secret::Secret;

const T0: u64 = 1_800_000_000;
const PASS: &str = "correct horse battery";

struct Dir(PathBuf);

impl Dir {
    fn new() -> Dir {
        static N: AtomicUsize = AtomicUsize::new(0);
        let path = std::env::temp_dir().join(format!(
            "rose-app-runtime-signin-invite-{}-{}",
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

fn config(dir: &Path, admission: Admission) -> Config {
    Config {
        app: "notes".into(),
        issuer: "Rose Notes".into(),
        admission,
        app_id: "app-1".into(),
        data_dir: dir.to_path_buf(),
        deployment_sha256: "ab".repeat(32),
        expected_revision: None,
        restore_marker: dir.join(".chariot-restore.json"),
        migrations: Migrations {
            account: &[],
            shared: None,
        },
    }
}

fn keys() -> Keys {
    Keys::Supplied {
        current: 1,
        keys: vec![(1, Secret::random(32))],
    }
}

/// The server has started here once, which creates the sign-in store.
fn started(dir: &Path, admission: Admission) -> SigninAuth {
    start(config(dir, admission), keys()).unwrap().auth
}

fn code(otpauth_uri: &str, now: u64) -> String {
    let secret = otpauth_uri
        .split(['?', '&'])
        .find_map(|p| p.strip_prefix("secret="))
        .unwrap();
    rose_signin::otp::totp(&rose_signin::base32::decode(secret).unwrap(), rose_signin::otp::V1, now)
}

#[test]
fn an_invite_admits_its_account_once_and_needs_no_keyring() {
    let dir = Dir::new();
    let mut auth = started(&dir.0, Admission::Invite);
    // The invite itself is given no keys and reads no keyring.
    let token = invite(&config(&dir.0, Admission::Invite), "ana", T0)
        .unwrap()
        .as_str()
        .to_string();
    assert_eq!(token.len(), 52);
    assert!(token.bytes().all(|b| b.is_ascii_uppercase() || b.is_ascii_digit()));
    assert_eq!(
        auth.begin_enrollment("bob", PASS, Some(&token), false, None, T0)
            .unwrap_err(),
        rt::EnrollError::NotAdmitted,
        "only the account it names"
    );
    assert!(
        auth.begin_enrollment("ana", PASS, Some(&token), false, None, T0)
            .is_ok()
    );
    // Spent by that enrollment: it does not admit again once the pending enrollment lapses.
    assert_eq!(
        auth.begin_enrollment("ana", PASS, Some(&token), false, None, T0 + 16 * 60)
            .unwrap_err(),
        rt::EnrollError::NotAdmitted
    );
}

#[test]
fn an_invite_expires_after_seven_days() {
    let dir = Dir::new();
    let mut auth = started(&dir.0, Admission::Invite);
    let token = invite(&config(&dir.0, Admission::Invite), "ana", T0)
        .unwrap()
        .as_str()
        .to_string();
    assert_eq!(
        auth.begin_enrollment("ana", PASS, Some(&token), false, None, T0 + 7 * 86400 + 1)
            .unwrap_err(),
        rt::EnrollError::NotAdmitted
    );
}

#[test]
fn no_store_is_not_found_and_none_is_created() {
    let dir = Dir::new();
    let c = config(&dir.0, Admission::Invite);
    assert_eq!(invite(&c, "ana", T0), Err(InviteError::NotFound));
    assert!(
        !dir.0.join("auth.sqlite3").exists(),
        "the invite never creates the store"
    );
    let mut missing = c.clone();
    missing.data_dir = dir.0.join("no/such/dir");
    missing.restore_marker = missing.data_dir.join(".chariot-restore.json");
    assert_eq!(invite(&missing, "ana", T0), Err(InviteError::NotFound));
    std::fs::create_dir(dir.0.join("auth.sqlite3")).unwrap();
    assert_eq!(
        invite(&c, "ana", T0),
        Err(InviteError::StoreUnavailable),
        "a directory is no store"
    );
}

#[test]
fn no_invite_while_a_restore_is_indicated() {
    let dir = Dir::new();
    let c = config(&dir.0, Admission::Invite);
    started(&dir.0, Admission::Invite);
    std::fs::write(&c.restore_marker, b"{}").unwrap();
    assert_eq!(invite(&c, "ana", T0), Err(InviteError::RestorePending));
    std::fs::remove_file(&c.restore_marker).unwrap();
    #[cfg(unix)]
    {
        std::os::unix::fs::symlink(dir.0.join("nowhere"), &c.restore_marker).unwrap();
        assert_eq!(
            invite(&c, "ana", T0),
            Err(InviteError::RestorePending),
            "a dangling link is a marker too"
        );
        std::fs::remove_file(&c.restore_marker).unwrap();
    }
    assert_eq!(
        invite(&c, "ana", T0).map(|t| t.as_str().len()),
        Ok(52),
        "issued once the marker is gone"
    );
}

#[test]
fn refusals_name_the_problem_and_nothing_else() {
    let dir = Dir::new();
    let c = config(&dir.0, Admission::Invite);
    let mut auth = started(&dir.0, Admission::Invite);
    for bad in ["", "a:b", "line\nbreak", &"x".repeat(129)] {
        assert_eq!(invite(&c, bad, T0), Err(InviteError::InvalidName), "{bad:?}");
    }
    let mut relative = c.clone();
    relative.data_dir = "data".into();
    assert_eq!(invite(&relative, "ana", T0), Err(InviteError::StoreUnavailable));

    // An enrolled account needs no invite.
    let token = invite(&c, "ana", T0).unwrap().as_str().to_string();
    let e = auth
        .begin_enrollment("ana", PASS, Some(&token), false, None, T0)
        .unwrap();
    auth.confirm_enrollment("ana", &e.handle, &code(&e.otpauth_uri, T0), T0)
        .unwrap();
    assert_eq!(invite(&c, "ana", T0 + 60), Err(InviteError::AlreadyEnrolled));

    let words = [
        (InviteError::InvalidName, "invalid_name"),
        (InviteError::AlreadyEnrolled, "already_enrolled"),
        (InviteError::NotFound, "not_found"),
        (InviteError::RestorePending, "restore_pending"),
        (InviteError::StoreUnavailable, "store_unavailable"),
    ];
    for (e, word) in words {
        assert_eq!(e.word(), word);
        let shown = format!("{e} {e:?}");
        assert!(!shown.contains("ana") && !shown.contains(&token), "{shown}");
    }
}

#[test]
fn the_token_redacts_itself() {
    let dir = Dir::new();
    started(&dir.0, Admission::Invite);
    let token = invite(&config(&dir.0, Admission::Invite), "ana", T0).unwrap();
    let shown = format!("{token:?} {:?}", Ok::<_, InviteError>(token.clone()));
    assert!(!shown.contains(token.as_str()), "{shown}");
    assert_eq!(format!("{token:?}"), "InviteToken(<redacted>)");
}

#[test]
fn open_sign_up_accepts_an_invite_harmlessly() {
    let dir = Dir::new();
    started(&dir.0, Admission::Open);
    assert_eq!(
        invite(&config(&dir.0, Admission::Open), "ana", T0).map(|t| t.as_str().len()),
        Ok(52)
    );
}

#[test]
fn an_invite_waits_for_a_busy_store_instead_of_failing() {
    let dir = Dir::new();
    started(&dir.0, Admission::Invite);
    let holder = Connection::open(dir.0.join("auth.sqlite3")).unwrap();
    holder.execute_batch("BEGIN IMMEDIATE").unwrap();
    let release = std::thread::spawn(move || {
        std::thread::sleep(Duration::from_millis(700));
        holder.execute_batch("COMMIT").unwrap();
    });
    let issued = invite(&config(&dir.0, Admission::Invite), "bob", T0);
    release.join().unwrap();
    assert_eq!(
        issued.map(|t| t.as_str().len()),
        Ok(52),
        "waited for the writer instead of failing"
    );
}

#[test]
fn a_store_no_server_set_up_is_not_found_and_left_alone() {
    let dir = Dir::new();
    let c = config(&dir.0, Admission::Invite);
    let path = dir.0.join("auth.sqlite3");
    // An empty file (a first start that died between creating the file and writing the schema).
    std::fs::write(&path, b"").unwrap();
    assert_eq!(invite(&c, "ana", T0), Err(InviteError::NotFound));
    assert_eq!(std::fs::metadata(&path).unwrap().len(), 0, "nothing written");
    // A SQLite file without the sign-in tables.
    std::fs::remove_file(&path).unwrap();
    Connection::open(&path)
        .unwrap()
        .execute_batch("CREATE TABLE other (x INTEGER);")
        .unwrap();
    assert_eq!(invite(&c, "ana", T0), Err(InviteError::NotFound));
    let auth_tables: i64 = Connection::open(&path)
        .unwrap()
        .query_row("SELECT count(*) FROM sqlite_master WHERE name LIKE 'auth_%'", [], |r| {
            r.get(0)
        })
        .unwrap();
    assert_eq!(auth_tables, 0, "no sign-in table created");
}

#[test]
fn a_store_at_another_schema_is_not_migrated() {
    let dir = Dir::new();
    let c = config(&dir.0, Admission::Invite);
    started(&dir.0, Admission::Invite);
    let path = dir.0.join("auth.sqlite3");
    let db = || Connection::open(&path).unwrap();
    let version = || -> i64 {
        db().query_row("SELECT value FROM auth_meta WHERE key = 'schema_version'", [], |r| {
            r.get(0)
        })
        .unwrap()
    };
    let has_invites = || -> bool {
        db().query_row(
            "SELECT EXISTS (SELECT 1 FROM sqlite_master WHERE name = 'auth_invite')",
            [],
            |r| r.get(0),
        )
        .unwrap()
    };
    assert_eq!(
        version(),
        2,
        "this test builds the store one version older than rose-signin 23062a04's"
    );
    // A genuine version-1 store (what an older build left): no invite table, no staged secret.
    db().execute_batch(
        "DROP TABLE auth_invite;
         ALTER TABLE auth_account DROP COLUMN staged_otp_secret;
         UPDATE auth_meta SET value = 1 WHERE key = 'schema_version';",
    )
    .unwrap();
    assert_eq!(invite(&c, "ana", T0), Err(InviteError::StoreUnavailable));
    assert_eq!(
        version(),
        1,
        "not migrated: the server migrates at its start, not the invite"
    );
    assert!(!has_invites(), "no table created");
    // A newer store is refused as well (rose-signin itself would refuse it too).
    db().execute_batch("UPDATE auth_meta SET value = 3 WHERE key = 'schema_version';")
        .unwrap();
    assert_eq!(invite(&c, "ana", T0), Err(InviteError::StoreUnavailable));
    assert_eq!(version(), 3);
}

#[test]
fn a_restore_marker_that_cannot_be_looked_up_refuses() {
    let dir = Dir::new();
    started(&dir.0, Admission::Invite);
    let plain = dir.0.join("plain");
    std::fs::write(&plain, b"").unwrap();
    let mut c = config(&dir.0, Admission::Invite);
    c.restore_marker = plain.join("marker"); // through a regular file: ENOTDIR, not absence
    assert_eq!(invite(&c, "ana", T0), Err(InviteError::RestorePending));
}
