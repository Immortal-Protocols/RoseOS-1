//! Store conformance: the same scenarios against every store (memory always; SQLite with feature "sqlite"), plus
//! SQLite-only concurrency, migration and restore checks. A store that passes these meets the flow's contract.

use rose_signin::base32;
use rose_signin::flow::{Authenticator, EnrollError, SignIn};
use rose_signin::otp;
use rose_signin::secret::{
    AeadSealer, OTP_SECRET, OpenError, SealContext, SealError, Sealer, Secret,
};
use rose_signin::session::{self, Principal, Record};
use rose_signin::store::{Memory, Store};

const T0: u64 = 1_800_000_000;
const PASS: &str = "correct horse";

fn sealer() -> AeadSealer {
    AeadSealer::new(Some(1), vec![(1, Secret::new(vec![7u8; 32]))]).unwrap()
}

fn secret_of(uri: &Secret) -> Vec<u8> {
    let uri = std::str::from_utf8(uri.expose()).unwrap();
    base32::decode(
        uri.split("secret=")
            .nth(1)
            .unwrap()
            .split('&')
            .next()
            .unwrap(),
    )
    .unwrap()
}

fn code(k: &[u8], t: u64) -> String {
    otp::totp(k, otp::V1, t)
}

fn enroll<S: Store, K: Sealer>(a: &mut Authenticator<S, K>, account: &str) -> Vec<u8> {
    let e = a
        .begin_enrollment("notes", account, PASS, None, T0)
        .unwrap();
    let k = secret_of(&e.otpauth_uri);
    a.confirm_enrollment("notes", account, e.handle.expose(), &code(&k, T0), T0)
        .unwrap();
    k
}

fn token(r: &SignIn) -> Vec<u8> {
    match r {
        SignIn::Ok { token, .. } => token.expose().to_vec(),
        other => panic!("expected a session, got {other:?}"),
    }
}

/// A sealer whose key can be withdrawn mid-test, to show an unavailable key writes and counts nothing.
struct Switchable {
    inner: AeadSealer,
    on: std::cell::Cell<bool>,
}
impl Sealer for Switchable {
    fn ready(&self) -> bool {
        self.on.get() && self.inner.ready()
    }
    fn seal(&self, c: &SealContext, p: &Secret) -> Result<Vec<u8>, SealError> {
        if self.on.get() {
            self.inner.seal(c, p)
        } else {
            Err(SealError::KeyUnavailable)
        }
    }
    fn open(&self, c: &SealContext, s: &[u8]) -> Result<Secret, OpenError> {
        if self.on.get() {
            self.inner.open(c, s)
        } else {
            Err(OpenError::KeyUnavailable)
        }
    }
}

fn scenario_flow<S: Store>(store: S) {
    let mut a = Authenticator::new(store, sealer(), "Rose Notes").unwrap();
    let k = enroll(&mut a, "ana");
    let t = T0 + 90;
    let tok = token(&a.sign_in("notes", "ana", &code(&k, t), PASS, "c", t));
    assert_eq!(
        a.validate(&tok, t + 1).unwrap(),
        Some(Principal {
            app: "notes".into(),
            account: "ana".into()
        })
    );
    assert_eq!(
        a.sign_in("notes", "ana", &code(&k, t), PASS, "c", t + 2),
        SignIn::Failed,
        "a code is spent once"
    );
    let acct = a.store.account("notes", "ana").unwrap().unwrap();
    assert!(acct.confirmed && acct.recovery_hashes.len() == 10 && acct.last_used_step.is_some());
    a.revoke(&tok).unwrap();
    assert_eq!(a.validate(&tok, t + 3).unwrap(), None);
}

fn scenario_unavailable_key_writes_and_counts_nothing<S: Store>(store: S) {
    let sw = Switchable {
        inner: sealer(),
        on: std::cell::Cell::new(true),
    };
    let mut a = Authenticator::new(store, sw, "Rose Notes").unwrap();
    let k = enroll(&mut a, "ana");
    let before = a.store.account("notes", "ana").unwrap();
    // Withdraw the key: a fresh authenticator over the same store, with the key gone.
    let store = a.store;
    let sw = Switchable {
        inner: sealer(),
        on: std::cell::Cell::new(false),
    };
    let mut b = Authenticator::new(store, sw, "Rose Notes").unwrap();
    assert!(!b.ready());
    let t = T0 + 90;
    assert_eq!(
        b.sign_in("notes", "ana", &code(&k, t), PASS, "c", t),
        SignIn::Unavailable
    );
    assert_eq!(
        b.begin_enrollment("notes", "bo", PASS, None, t)
            .unwrap_err(),
        EnrollError::Unavailable
    );
    assert_eq!(
        b.store.account("notes", "ana").unwrap(),
        before,
        "the code was not consumed"
    );
    assert_eq!(
        b.store.account("notes", "bo").unwrap(),
        None,
        "the failed enrollment wrote nothing"
    );
    assert_eq!(
        b.store.rate("a\u{0}notes\u{0}ana").unwrap(),
        Default::default(),
        "nothing was counted"
    );
    // With the key back, the same code still works: nothing was spent while it was missing.
    let store = b.store;
    let mut c = Authenticator::new(store, sealer(), "Rose Notes").unwrap();
    assert!(matches!(
        c.sign_in("notes", "ana", &code(&k, t), PASS, "c", t),
        SignIn::Ok { .. }
    ));
}

fn scenario_tampered_row_is_a_generic_failure_not_counted<S: Store>(store: S) {
    let mut a = Authenticator::new(store, sealer(), "Rose Notes").unwrap();
    let ka = enroll(&mut a, "ana");
    enroll(&mut a, "bo");
    // Someone with database write access copies Bo's sealed secret into Ana's row.
    a.store.begin().unwrap();
    let bo = a.store.account("notes", "bo").unwrap().unwrap();
    let mut ana = a.store.account("notes", "ana").unwrap().unwrap();
    ana.sealed_otp_secret = bo.sealed_otp_secret;
    a.store.put_account("notes", "ana", &ana).unwrap();
    a.store.commit().unwrap();
    let t = T0 + 90;
    assert_eq!(
        a.sign_in("notes", "ana", &code(&ka, t), PASS, "c", t),
        SignIn::Failed
    );
    assert_eq!(a.integrity_faults, 1);
    assert_eq!(
        a.store.rate("a\u{0}notes\u{0}ana").unwrap(),
        Default::default(),
        "not the user's failure"
    );
}

fn scenario_revocation_is_monotonic<S: Store>(mut store: S) {
    let (_, key) = session::mint();
    let p = Principal {
        app: "notes".into(),
        account: "ana".into(),
    };
    store.begin().unwrap();
    store
        .put_session(
            key,
            &Record {
                principal: p.clone(),
                created: T0,
                last_seen: T0,
                revoked: false,
            },
        )
        .unwrap();
    store.revoke_all(&p).unwrap();
    // A stale write (a validate that read the session before the revocation) cannot revive it.
    store
        .put_session(
            key,
            &Record {
                principal: p,
                created: T0,
                last_seen: T0 + 5,
                revoked: false,
            },
        )
        .unwrap();
    store.commit().unwrap();
    assert!(store.session(&key).unwrap().unwrap().revoked);
}

fn scenario_rollback_discards_writes<S: Store>(mut store: S) {
    store.begin().unwrap();
    store
        .put_rate(
            "k",
            rose_signin::rate::State {
                failures: 3,
                last_failure: T0,
            },
        )
        .unwrap();
    store.rollback().unwrap();
    assert_eq!(store.rate("k").unwrap(), Default::default());
}

fn scenario_restore<S: Store>(store: S) {
    let mut a = Authenticator::new(store, sealer(), "Rose Notes").unwrap();
    let k = enroll(&mut a, "ana");
    let t = T0 + 600;
    let tok = token(&a.sign_in("notes", "ana", &code(&k, t), PASS, "c", t));
    // A backup is restored; the procedure runs before serving.
    let now = t + 15;
    a.restore_after_backup(now).unwrap();
    assert_eq!(
        a.validate(&tok, now).unwrap(),
        None,
        "every session is revoked"
    );
    let acct = a.store.account("notes", "ana").unwrap().unwrap();
    assert!(
        acct.recovery_hashes.is_empty() && acct.recovery_reissue,
        "recovery codes invalidated"
    );
    assert_eq!(
        a.sign_in("notes", "ana", &code(&k, now), PASS, "c", now),
        SignIn::Failed,
        "no replay at or before now"
    );
    assert_eq!(
        a.sign_in("notes", "ana", &code(&k, now + 30), PASS, "c2", now + 1),
        SignIn::Failed,
        "nor of a step-ahead code a fast clock may have used before the restore"
    );
    let next = now + 60;
    match a.sign_in("notes", "ana", &code(&k, next), PASS, "c", next) {
        SignIn::Ok {
            new_recovery_codes, ..
        } => assert_eq!(new_recovery_codes.len(), 10, "a new set is issued"),
        other => panic!("{other:?}"),
    }
    assert!(
        !a.store
            .account("notes", "ana")
            .unwrap()
            .unwrap()
            .recovery_reissue
    );
}

fn scenario_restore_keeps_a_pending_enrollments_recovery_codes<S: Store>(store: S) {
    let mut a = Authenticator::new(store, sealer(), "Rose Notes").unwrap();
    let e = a.begin_enrollment("notes", "bo", PASS, None, T0).unwrap();
    let k = secret_of(&e.otpauth_uri);
    a.restore_after_backup(T0 + 60).unwrap();
    // The enroller confirms after the restore (past the skew), within the pending window.
    let t = T0 + 120;
    a.confirm_enrollment("notes", "bo", e.handle.expose(), &code(&k, t), t)
        .unwrap();
    let acct = a.store.account("notes", "bo").unwrap().unwrap();
    assert_eq!(
        acct.recovery_hashes.len(),
        10,
        "the codes shown at enrollment still work"
    );
    assert!(!acct.recovery_reissue);
    let mut hashes = acct.recovery_hashes.clone();
    assert!(rose_signin::recovery::consume(
        &mut hashes,
        "notes",
        "bo",
        &e.recovery_codes[0]
    ));
}

fn scenario_rotation_reseals<S: Store>(store: S) {
    let mut a = Authenticator::new(store, sealer(), "Rose Notes").unwrap();
    let k = enroll(&mut a, "ana");
    let store = a.store;
    let rotated = AeadSealer::new(
        Some(2),
        vec![
            (1, Secret::new(vec![7u8; 32])),
            (2, Secret::new(vec![8u8; 32])),
        ],
    )
    .unwrap();
    let mut b = Authenticator::new(store, rotated, "Rose Notes").unwrap();
    let t = T0 + 90;
    assert!(matches!(
        b.sign_in("notes", "ana", &code(&k, t), PASS, "c", t),
        SignIn::Ok { .. }
    ));
    let sealed = b
        .store
        .account("notes", "ana")
        .unwrap()
        .unwrap()
        .sealed_otp_secret;
    assert_eq!(
        &sealed[1..5],
        &2u32.to_be_bytes(),
        "re-sealed under the current key after a sign-in"
    );
    let ctx = SealContext {
        app: "notes",
        account: "ana",
        purpose: OTP_SECRET,
    };
    let only_v2 = AeadSealer::new(Some(2), vec![(2, Secret::new(vec![8u8; 32]))]).unwrap();
    assert!(
        only_v2.open(&ctx, &sealed).is_ok(),
        "version 1 can now be retired for this account"
    );
}

macro_rules! conformance {
    ($m:ident, $make:expr) => {
        mod $m {
            use super::*;
            #[test]
            fn flow() {
                scenario_flow($make);
            }
            #[test]
            fn unavailable_key_writes_and_counts_nothing() {
                scenario_unavailable_key_writes_and_counts_nothing($make);
            }
            #[test]
            fn tampered_row_is_a_generic_failure_not_counted() {
                scenario_tampered_row_is_a_generic_failure_not_counted($make);
            }
            #[test]
            fn revocation_is_monotonic() {
                scenario_revocation_is_monotonic($make);
            }
            #[test]
            fn rollback_discards_writes() {
                scenario_rollback_discards_writes($make);
            }
            #[test]
            fn restore() {
                scenario_restore($make);
            }
            #[test]
            fn restore_keeps_a_pending_enrollments_recovery_codes() {
                scenario_restore_keeps_a_pending_enrollments_recovery_codes($make);
            }
            #[test]
            fn rotation_reseals() {
                scenario_rotation_reseals($make);
            }
        }
    };
}

conformance!(memory, Memory::default());

#[cfg(feature = "sqlite")]
mod sqlite_store {
    use super::*;
    use rose_signin::store::sqlite::{SCHEMA_VERSION, SqliteStore};
    use rusqlite::Connection;

    pub fn fresh() -> SqliteStore {
        SqliteStore::new(Connection::open_in_memory().unwrap()).unwrap()
    }

    conformance!(conf, super::fresh());

    #[test]
    fn migrations_are_recorded_and_idempotent() {
        let dir = tempdir();
        let path = dir.join("app.db");
        let s = SqliteStore::new(Connection::open(&path).unwrap()).unwrap();
        assert_eq!(s.schema_version().unwrap(), SCHEMA_VERSION);
        drop(s);
        let again = SqliteStore::new(Connection::open(&path).unwrap()).unwrap();
        assert_eq!(again.schema_version().unwrap(), SCHEMA_VERSION);
        // A database from a newer library is refused rather than misread.
        again
            .connection()
            .execute(
                "UPDATE auth_meta SET value = 99 WHERE key = 'schema_version'",
                [],
            )
            .unwrap();
        drop(again);
        assert!(SqliteStore::new(Connection::open(&path).unwrap()).is_err());
    }

    fn tempdir() -> std::path::PathBuf {
        let d = std::env::temp_dir().join(format!(
            "rose-signin-{}-{}",
            std::process::id(),
            base32::encode(Secret::random(5).expose())
        ));
        std::fs::create_dir_all(&d).unwrap();
        d
    }

    #[test]
    fn two_connections_cannot_both_spend_one_code() {
        let dir = tempdir();
        let path = dir.join("app.db");
        let mut a = Authenticator::new(
            SqliteStore::new(Connection::open(&path).unwrap()).unwrap(),
            sealer(),
            "Rose Notes",
        )
        .unwrap();
        let k = enroll(&mut a, "ana");
        drop(a);
        let t = T0 + 90;
        let c = code(&k, t);
        let barrier = std::sync::Arc::new(std::sync::Barrier::new(2));
        let handles: Vec<_> = (0..2)
            .map(|i| {
                let (path, c, barrier) = (path.clone(), c.clone(), barrier.clone());
                std::thread::spawn(move || {
                    let store = SqliteStore::new(Connection::open(&path).unwrap()).unwrap();
                    let mut a = Authenticator::new(store, sealer(), "Rose Notes").unwrap();
                    barrier.wait();
                    a.sign_in("notes", "ana", &c, PASS, &format!("c{i}"), t)
                })
            })
            .collect();
        let results: Vec<SignIn> = handles.into_iter().map(|h| h.join().unwrap()).collect();
        let ok = results
            .iter()
            .filter(|r| matches!(r, SignIn::Ok { .. }))
            .count();
        assert_eq!(ok, 1, "exactly one request spends the code: {results:?}");
        assert!(results.contains(&SignIn::Failed));
        let _ = std::fs::remove_dir_all(dir);
    }
}
