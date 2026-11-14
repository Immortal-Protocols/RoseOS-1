//! The rose-signin-v1 server flow and its security properties.

use rose_signin::flow::{Authenticator, EnrollError, PENDING_SECONDS, SignIn};
use rose_signin::secret::{Sealer, Secret};
use rose_signin::session::Principal;
use rose_signin::store::{Memory, Store};
use rose_signin::{base32, knowledge, otp, rate, recovery};

/// Test-only sealer (XOR with a fixed pad plus a marker). Production sealing is decision D9.
struct TestSealer;
impl Sealer for TestSealer {
    fn seal(&self, p: &Secret) -> Vec<u8> {
        let mut v = vec![0xA5];
        v.extend(p.expose().iter().map(|b| b ^ 0x5C));
        v
    }
    fn open(&self, s: &[u8]) -> Option<Secret> {
        (s.first() == Some(&0xA5)).then(|| Secret::new(s[1..].iter().map(|b| b ^ 0x5C).collect()))
    }
}

type Auth = Authenticator<Memory, TestSealer>;
const T0: u64 = 1_800_000_000;
const PASS: &str = "correct horse";

fn auth() -> Auth {
    Authenticator::new(Memory::default(), TestSealer, "Rose Notes").unwrap()
}

/// The secret the user's authenticator app would hold, read back from the otpauth URI.
fn secret_of(uri: &Secret) -> Vec<u8> {
    let uri = std::str::from_utf8(uri.expose()).unwrap();
    let s = uri
        .split("secret=")
        .nth(1)
        .unwrap()
        .split('&')
        .next()
        .unwrap();
    base32::decode(s).unwrap()
}

fn code(k: &[u8], t: u64) -> String {
    otp::totp(k, otp::V1, t)
}

/// A six-digit code that is not the current one for k at t.
fn wrong(k: &[u8], t: u64) -> &'static str {
    if code(k, t) == "000000" {
        "111111"
    } else {
        "000000"
    }
}

fn enrolled(a: &mut Auth, app: &str, account: &str) -> Vec<u8> {
    let e = a.begin_enrollment(app, account, PASS, None, T0).unwrap();
    let k = secret_of(&e.otpauth_uri);
    a.confirm_enrollment(app, account, e.handle.expose(), &code(&k, T0), T0)
        .unwrap();
    k
}

fn token(r: &SignIn) -> Vec<u8> {
    match r {
        SignIn::Ok { token, .. } => token.expose().to_vec(),
        other => panic!("expected a session, got {other:?}"),
    }
}

fn principal(app: &str, account: &str) -> Principal {
    Principal {
        app: app.into(),
        account: account.into(),
    }
}

#[test]
fn both_factors_sign_in_and_the_session_names_its_principal() {
    let mut a = auth();
    let k = enrolled(&mut a, "notes", "ana");
    let t = T0 + 90;
    let tok = token(&a.sign_in("notes", "ana", &code(&k, t), PASS, "10.0.0.1", t));
    assert_eq!(a.validate(&tok, t + 1), Some(principal("notes", "ana")));
}

#[test]
fn each_factor_alone_fails_and_the_answer_does_not_say_which() {
    let mut a = auth();
    let k = enrolled(&mut a, "notes", "ana");
    let t = T0 + 90;
    assert_eq!(
        a.sign_in("notes", "ana", wrong(&k, t), PASS, "c", t),
        SignIn::Failed
    );
    assert_eq!(
        a.sign_in(
            "notes",
            "ana",
            &code(&k, t + 30),
            "wrong passphrase",
            "c",
            t + 30
        ),
        SignIn::Failed
    );
    assert_eq!(
        a.sign_in("notes", "nobody", &code(&k, t + 60), PASS, "c", t + 60),
        SignIn::Failed
    );
}

#[test]
fn a_code_is_never_accepted_twice_even_after_a_wrong_passphrase() {
    let mut a = auth();
    let k = enrolled(&mut a, "notes", "ana");
    let t = T0 + 90;
    let c = code(&k, t);
    assert_eq!(
        a.sign_in("notes", "ana", &c, "wrong passphrase", "c", t),
        SignIn::Failed
    );
    // The same code with the right passphrase is now spent: one code buys one knowledge guess.
    assert_eq!(
        a.sign_in("notes", "ana", &c, PASS, "c", t + 1),
        SignIn::Failed
    );
    let tok = token(&a.sign_in("notes", "ana", &code(&k, t + 30), PASS, "c", t + 30));
    assert!(a.validate(&tok, t + 31).is_some());
    // The enrollment's own confirming code cannot sign in either.
    let mut b = auth();
    let e = b.begin_enrollment("notes", "bo", PASS, None, T0).unwrap();
    let kb = secret_of(&e.otpauth_uri);
    b.confirm_enrollment("notes", "bo", e.handle.expose(), &code(&kb, T0), T0)
        .unwrap();
    assert_eq!(
        b.sign_in("notes", "bo", &code(&kb, T0), PASS, "c", T0 + 1),
        SignIn::Failed
    );
}

#[test]
fn an_unconfirmed_enrollment_cannot_sign_in() {
    let mut a = auth();
    let e = a.begin_enrollment("notes", "ana", PASS, None, T0).unwrap();
    let k = secret_of(&e.otpauth_uri);
    assert_eq!(
        a.sign_in("notes", "ana", &code(&k, T0), PASS, "c", T0),
        SignIn::Failed
    );
    assert_eq!(
        a.confirm_enrollment("notes", "ana", e.handle.expose(), wrong(&k, T0), T0),
        Err(EnrollError::NotConfirmed)
    );
}

#[test]
fn a_confirmed_account_cannot_be_re_enrolled_by_anyone() {
    let mut a = auth();
    enrolled(&mut a, "notes", "ana");
    let late = T0 + 10 * PENDING_SECONDS;
    assert_eq!(
        a.begin_enrollment("notes", "ana", "attacker passphrase", None, late)
            .unwrap_err(),
        EnrollError::AlreadyEnrolled
    );
}

#[test]
fn a_pending_enrollment_belongs_to_whoever_began_it() {
    let mut a = auth();
    let victim = a.begin_enrollment("notes", "ana", PASS, None, T0).unwrap();
    // A second caller cannot replace it while it is pending...
    assert_eq!(
        a.begin_enrollment("notes", "ana", "attacker passphrase", None, T0 + 60)
            .unwrap_err(),
        EnrollError::Pending
    );
    let guessed = a.begin_enrollment(
        "notes",
        "ana",
        "attacker passphrase",
        Some(b"guessed handle"),
        T0 + 60,
    );
    assert_eq!(guessed.unwrap_err(), EnrollError::Pending);
    // ...nor confirm it without the handle, even with a valid code.
    let k = secret_of(&victim.otpauth_uri);
    assert_eq!(
        a.confirm_enrollment(
            "notes",
            "ana",
            b"guessed handle",
            &code(&k, T0 + 60),
            T0 + 60
        ),
        Err(EnrollError::NotConfirmed)
    );
    // The enroller restarts with its handle; only the new handle confirms.
    let again = a
        .begin_enrollment("notes", "ana", PASS, Some(victim.handle.expose()), T0 + 90)
        .unwrap();
    let k2 = secret_of(&again.otpauth_uri);
    assert_eq!(
        a.confirm_enrollment(
            "notes",
            "ana",
            victim.handle.expose(),
            &code(&k2, T0 + 90),
            T0 + 90
        ),
        Err(EnrollError::NotConfirmed)
    );
    a.confirm_enrollment(
        "notes",
        "ana",
        again.handle.expose(),
        &code(&k2, T0 + 90),
        T0 + 90,
    )
    .unwrap();
    assert!(matches!(
        a.sign_in("notes", "ana", &code(&k2, T0 + 150), PASS, "c", T0 + 150),
        SignIn::Ok { .. }
    ));
}

#[test]
fn an_abandoned_enrollment_expires() {
    let mut a = auth();
    let first = a.begin_enrollment("notes", "ana", PASS, None, T0).unwrap();
    let late = T0 + PENDING_SECONDS;
    let k = secret_of(&first.otpauth_uri);
    assert_eq!(
        a.confirm_enrollment("notes", "ana", first.handle.expose(), &code(&k, late), late),
        Err(EnrollError::NotConfirmed)
    );
    assert!(
        a.begin_enrollment("notes", "ana", PASS, None, late).is_ok(),
        "anyone admitted may start over once it expired"
    );
}

#[test]
fn confirm_does_not_reveal_which_accounts_exist() {
    let mut a = auth();
    let k = enrolled(&mut a, "notes", "ana");
    let c = code(&k, T0 + 60);
    assert_eq!(
        a.confirm_enrollment("notes", "ana", b"x", &c, T0 + 60),
        Err(EnrollError::NotConfirmed)
    );
    assert_eq!(
        a.confirm_enrollment("notes", "nobody", b"x", &c, T0 + 60),
        Err(EnrollError::NotConfirmed)
    );
}

#[test]
fn confirm_attempts_are_limited() {
    let mut a = auth();
    let e = a.begin_enrollment("notes", "ana", PASS, None, T0).unwrap();
    let k = secret_of(&e.otpauth_uri);
    for i in 0..5 {
        let t = T0 + i;
        assert_eq!(
            a.confirm_enrollment("notes", "ana", e.handle.expose(), wrong(&k, t), t),
            Err(EnrollError::NotConfirmed)
        );
    }
    let r = a.confirm_enrollment("notes", "ana", e.handle.expose(), &code(&k, T0 + 6), T0 + 6);
    assert!(matches!(r, Err(EnrollError::Limited { .. })), "{r:?}");
}

#[test]
fn the_knowledge_factor_is_hashed_and_length_checked() {
    assert_eq!(
        knowledge::hash("short").unwrap_err(),
        knowledge::Rejected::TooShort
    );
    assert_eq!(
        knowledge::hash(&"x".repeat(129)).unwrap_err(),
        knowledge::Rejected::TooLong
    );
    let h = knowledge::hash(PASS).unwrap();
    assert!(h.starts_with("$argon2id$v=19$m=19456,t=2,p=1$"), "{h}");
    assert!(!h.contains(PASS));
    assert!(knowledge::verify(PASS, &h) && !knowledge::verify("correct horsf", &h));
    let mut a = auth();
    let r = a.begin_enrollment("notes", "ana", "1234", None, T0);
    assert_eq!(
        r.unwrap_err(),
        EnrollError::Knowledge(knowledge::Rejected::TooShort)
    );
}

#[test]
fn secrets_are_sealed_at_rest_and_never_printed() {
    let mut a = auth();
    let k = enrolled(&mut a, "notes", "ana");
    let stored = a.store.account("notes", "ana").unwrap();
    assert!(
        !stored
            .sealed_otp_secret
            .windows(k.len())
            .any(|w| w == k.as_slice())
    );
    assert_eq!(format!("{:?}", Secret::new(k)), "<redacted>");
}

#[test]
fn failures_back_off_per_account_and_per_client() {
    let mut a = auth();
    let k = enrolled(&mut a, "notes", "ana");
    let mut t = T0 + 90;
    for _ in 0..5 {
        assert_eq!(
            a.sign_in("notes", "ana", "000001", "wrong passphrase", "c1", t),
            SignIn::Failed
        );
        t += 1;
    }
    // Code guesses against the account are slowed, from any client...
    let r = a.sign_in("notes", "ana", "000002", "wrong passphrase", "c2", t);
    assert!(
        matches!(r, SignIn::Limited { retry_after } if retry_after > 0 && retry_after <= 30),
        "{r:?}"
    );
    // ...and the failing client is slowed against other accounts too.
    enrolled(&mut a, "notes", "bo");
    let r2 = a.sign_in("notes", "bo", "000003", "wrong passphrase", "c1", t);
    assert!(matches!(r2, SignIn::Limited { .. }), "{r2:?}");
    // The real user with a valid code is not held by the account counter.
    let ok = a.sign_in("notes", "ana", &code(&k, t + 30), PASS, "c2", t + 30);
    assert!(matches!(ok, SignIn::Ok { .. }), "{ok:?}");
}

#[test]
fn an_attacker_holding_the_account_at_max_backoff_cannot_keep_the_real_user_out() {
    let mut a = auth();
    let k = enrolled(&mut a, "notes", "ana");
    // The attacker knows only the name: failures from fresh clients, waiting out each delay, up to the cap.
    let mut t = T0 + 90;
    for i in 0..14 {
        let client = format!("attacker{i}");
        if let SignIn::Limited { retry_after } =
            a.sign_in("notes", "ana", "000001", "guess", &client, t)
        {
            t += retry_after;
            assert_eq!(
                a.sign_in("notes", "ana", "000001", "guess", &client, t),
                SignIn::Failed
            );
        }
        t += 1;
    }
    assert!(
        matches!(a.sign_in("notes", "ana", "000002", "guess", "attacker99", t), SignIn::Limited { retry_after } if retry_after > 1800)
    );
    // The real user, with a valid code and the passphrase, signs in at once from a clean client.
    t += 30;
    let ok = a.sign_in("notes", "ana", &code(&k, t), PASS, "phone", t);
    assert!(matches!(ok, SignIn::Ok { .. }), "{ok:?}");
}

#[test]
fn failures_are_forgotten_after_a_quiet_period() {
    let p = rate::V1;
    let mut s = rate::State::default();
    for i in 0..12 {
        s = p.failed(s, T0 + i);
    }
    assert!(p.wait(s, T0 + 12) > 0);
    assert_eq!(p.wait(s, T0 + 11 + p.decay_seconds), 0);
    assert_eq!(
        p.failed(s, T0 + 11 + p.decay_seconds).failures,
        1,
        "a later failure starts a new count"
    );
}

#[test]
fn sessions_expire_idle_and_absolute_and_can_be_revoked() {
    let mut a = auth();
    let k = enrolled(&mut a, "notes", "ana");
    let sign = |a: &mut Auth, t: u64| token(&a.sign_in("notes", "ana", &code(&k, t), PASS, "c", t));
    let mut t = T0 + 90;
    let idle = sign(&mut a, t);
    assert!(a.validate(&idle, t + 7 * 86400 - 1).is_some());
    assert!(
        a.validate(&idle, t + 2 * 7 * 86400).is_none(),
        "idle expiry"
    );
    t += 30;
    let abs = sign(&mut a, t);
    let mut now = t;
    while now < t + 29 * 86400 {
        now += 86400;
        assert!(a.validate(&abs, now).is_some());
    }
    assert!(
        a.validate(&abs, t + 30 * 86400).is_none(),
        "absolute expiry even when active"
    );
    t = now + 30;
    let one = sign(&mut a, t);
    let two = sign(&mut a, t + 30);
    a.revoke(&one);
    assert!(a.validate(&one, t + 31).is_none() && a.validate(&two, t + 31).is_some());
    a.revoke_all(&principal("notes", "ana"));
    assert!(a.validate(&two, t + 32).is_none());
    assert!(a.validate(b"not a token", t + 32).is_none());
}

#[test]
fn accounts_and_apps_are_isolated() {
    let mut a = auth();
    let ka = enrolled(&mut a, "notes", "ana");
    let kb = enrolled(&mut a, "notes", "bo");
    let kc = enrolled(&mut a, "recipes", "ana");
    let t = T0 + 90;
    // Ana's code opens neither Bo's account nor Ana's account in another app.
    assert_eq!(
        a.sign_in("notes", "bo", &code(&ka, t), PASS, "c", t),
        SignIn::Failed
    );
    assert_eq!(
        a.sign_in("recipes", "ana", &code(&ka, t), PASS, "c", t),
        SignIn::Failed
    );
    let tb = token(&a.sign_in("notes", "bo", &code(&kb, t + 30), PASS, "c2", t + 30));
    let tc = token(&a.sign_in("recipes", "ana", &code(&kc, t + 30), PASS, "c2", t + 30));
    assert_eq!(a.validate(&tb, t + 31).unwrap(), principal("notes", "bo"));
    assert_eq!(
        a.validate(&tc, t + 31).unwrap(),
        principal("recipes", "ana")
    );
    a.revoke_all(&principal("notes", "ana"));
    assert!(
        a.validate(&tc, t + 32).is_some(),
        "revoking one app's account leaves the other app's"
    );
}

#[test]
fn recovery_codes_are_single_use_bound_to_their_account_and_stored_hashed() {
    let (codes, mut hashes) = recovery::generate("notes", "ana");
    assert_eq!(codes.len(), 10);
    // 80 bits each: XXXX-XXXX-XXXX-XXXX in base32.
    assert!(
        codes
            .iter()
            .all(|c| c.len() == 19 && c.as_bytes()[4] == b'-' && c.as_bytes()[14] == b'-')
    );
    assert_ne!(
        recovery::hash("notes", "ana", &codes[0]),
        recovery::hash("notes", "bo", &codes[0])
    );
    assert_ne!(
        recovery::hash("notes", "ana", &codes[0]),
        recovery::hash("recipes", "ana", &codes[0])
    );
    assert!(
        !recovery::consume(&mut hashes, "notes", "bo", &codes[3]),
        "another account's code does not match"
    );
    assert!(recovery::consume(
        &mut hashes,
        "notes",
        "ana",
        &codes[3].to_lowercase().replace('-', " ")
    ));
    assert!(
        !recovery::consume(&mut hashes, "notes", "ana", &codes[3]),
        "spent"
    );
    assert!(!recovery::consume(
        &mut hashes,
        "notes",
        "ana",
        "AAAA-AAAA-AAAA-AAAA"
    ));
    assert_eq!(hashes.len(), 9);
}

#[test]
fn names_that_break_the_otpauth_label_are_refused() {
    assert!(Authenticator::new(Memory::default(), TestSealer, "Rose: Notes").is_err());
    let mut a = auth();
    let long = "x".repeat(129);
    for bad in ["", "ana:admin", "line\nbreak", long.as_str()] {
        assert_eq!(
            a.begin_enrollment("notes", bad, PASS, None, T0)
                .unwrap_err(),
            EnrollError::InvalidName,
            "{bad:?}"
        );
    }
}
