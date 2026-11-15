//! Single-use invites (D7) and recovery with a recovery code plus one remaining factor (D5).

use rose_signin::flow::{
    Admission, Authenticator, EnrollError, INVITE_SECONDS, RecoverError, SignIn,
};
use rose_signin::secret::{AeadSealer, Secret};

use rose_signin::store::{Memory, Store};
use rose_signin::{base32, knowledge, otp};

type Auth = Authenticator<Memory, AeadSealer>;
const T0: u64 = 1_800_000_000;
const PASS: &str = "correct horse";

fn auth() -> Auth {
    let sealer = AeadSealer::new(Some(1), vec![(1, Secret::new(vec![7u8; 32]))]).unwrap();
    Authenticator::new(Memory::default(), sealer, "Rose Notes").unwrap()
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

fn wrong(k: &[u8], t: u64) -> &'static str {
    if code(k, t) == "000000" {
        "111111"
    } else {
        "000000"
    }
}

/// Enrolls (app "notes") and returns the authenticator secret and the recovery codes.
fn enrolled(a: &mut Auth, account: &str) -> (Vec<u8>, Vec<String>) {
    let e = a
        .begin_enrollment("notes", account, PASS, Admission::Open, None, T0)
        .unwrap();
    let k = secret_of(&e.otpauth_uri);
    a.confirm_enrollment("notes", account, e.handle.expose(), &code(&k, T0), T0)
        .unwrap();
    (k, e.recovery_codes)
}

fn signs_in(a: &mut Auth, account: &str, k: &[u8], pass: &str, t: u64) -> bool {
    matches!(
        a.sign_in("notes", account, &code(k, t), pass, "c", t),
        SignIn::Ok { .. }
    )
}

fn token(r: SignIn) -> Vec<u8> {
    match r {
        SignIn::Ok { token, .. } => token.expose().to_vec(),
        other => panic!("{other:?}"),
    }
}

// ---- invites ----

#[test]
fn an_invite_admits_exactly_its_account_once() {
    let mut a = auth();
    let invite = a.create_invite("notes", "ana", T0).unwrap();
    // Not for another account, and nothing without an invite.
    let r = a.begin_enrollment(
        "notes",
        "bo",
        PASS,
        Admission::Invite(invite.expose()),
        None,
        T0,
    );
    assert_eq!(r.unwrap_err(), EnrollError::NotAdmitted);
    let r = a.begin_enrollment("notes", "ana", PASS, Admission::Invite(b"guess"), None, T0);
    assert_eq!(r.unwrap_err(), EnrollError::NotAdmitted);
    // The right account, once.
    let e = a
        .begin_enrollment(
            "notes",
            "ana",
            PASS,
            Admission::Invite(invite.expose()),
            None,
            T0,
        )
        .unwrap();
    // Restarting one's own pending enrollment needs only its handle.
    let again = a.begin_enrollment(
        "notes",
        "ana",
        PASS,
        Admission::Invite(b"x"),
        Some(e.handle.expose()),
        T0 + 5,
    );
    assert!(again.is_ok());
    // Once the pending window is over, the spent invite admits nobody.
    let late = T0 + 5 + rose_signin::flow::PENDING_SECONDS;
    let r = a.begin_enrollment(
        "notes",
        "ana",
        PASS,
        Admission::Invite(invite.expose()),
        None,
        late,
    );
    assert_eq!(r.unwrap_err(), EnrollError::NotAdmitted);
}

#[test]
fn an_invite_expires_and_open_sign_up_needs_none() {
    let mut a = auth();
    let invite = a.create_invite("notes", "ana", T0).unwrap();
    let late = T0 + INVITE_SECONDS;
    let r = a.begin_enrollment(
        "notes",
        "ana",
        PASS,
        Admission::Invite(invite.expose()),
        None,
        late,
    );
    assert_eq!(r.unwrap_err(), EnrollError::NotAdmitted);
    assert!(
        a.begin_enrollment("notes", "bo", PASS, Admission::Open, None, T0)
            .is_ok()
    );
}

#[test]
fn no_invite_for_an_enrolled_account_or_a_bad_name() {
    let mut a = auth();
    enrolled(&mut a, "ana");
    assert_eq!(
        a.create_invite("notes", "ana", T0).unwrap_err(),
        EnrollError::AlreadyEnrolled
    );
    assert_eq!(
        a.create_invite("notes", "a:b", T0).unwrap_err(),
        EnrollError::InvalidName
    );
}

// ---- lost authenticator: recovery code + passphrase ----

#[test]
fn a_recovery_code_and_the_passphrase_replace_a_lost_authenticator() {
    let mut a = auth();
    let (old, codes) = enrolled(&mut a, "ana");
    let t = T0 + 90;
    let tok = token(a.sign_in("notes", "ana", &code(&old, t), PASS, "c", t));
    let e = a
        .recover_authenticator("notes", "ana", &codes[0], PASS, "phone", t + 30)
        .unwrap();
    assert_eq!(e.recovery_codes.len(), 10);
    assert_eq!(
        a.validate(&tok, t + 31).unwrap(),
        None,
        "every session is revoked"
    );
    // Nobody can enroll over the account while the new authenticator is pending, nor after.
    for when in [t + 40, t + 10 * rose_signin::flow::PENDING_SECONDS] {
        let r = a.begin_enrollment("notes", "ana", "attacker pass", Admission::Open, None, when);
        assert_eq!(r.unwrap_err(), EnrollError::AlreadyEnrolled);
    }
    // A code from the new authenticator, with the handle, completes it; the old secret stops working.
    let new = secret_of(&e.otpauth_uri);
    let c = t + 60;
    assert_eq!(
        a.confirm_enrollment("notes", "ana", b"wrong handle", &code(&new, c), c),
        Err(EnrollError::NotConfirmed)
    );
    a.confirm_enrollment("notes", "ana", e.handle.expose(), &code(&new, c), c)
        .unwrap();
    assert!(signs_in(&mut a, "ana", &new, PASS, c + 30));
    assert!(!signs_in(&mut a, "ana", &old, PASS, c + 60));
    // The old recovery set is gone; the new one works.
    assert!(
        a.recover_authenticator("notes", "ana", &codes[1], PASS, "phone", c + 90)
            .is_err()
    );
    assert!(
        a.recover_authenticator("notes", "ana", &e.recovery_codes[0], PASS, "phone", c + 120)
            .is_ok()
    );
}

#[test]
fn a_wrong_passphrase_spends_the_recovery_code() {
    let mut a = auth();
    let (_, codes) = enrolled(&mut a, "ana");
    let r = a.recover_authenticator("notes", "ana", &codes[0], "wrong passphrase", "c", T0 + 30);
    assert_eq!(r.unwrap_err(), RecoverError::Failed);
    let r = a.recover_authenticator("notes", "ana", &codes[0], PASS, "c", T0 + 31);
    assert_eq!(
        r.unwrap_err(),
        RecoverError::Failed,
        "one code buys one passphrase guess"
    );
    assert_eq!(
        a.store
            .account("notes", "ana")
            .unwrap()
            .unwrap()
            .recovery_hashes
            .len(),
        9
    );
}

#[test]
fn recovery_failures_look_alike_and_are_rate_limited() {
    let mut a = auth();
    let (_, codes) = enrolled(&mut a, "ana");
    let bad = "AAAA-AAAA-AAAA-AAAA";
    assert_eq!(
        a.recover_authenticator("notes", "nobody", &codes[0], PASS, "x", T0)
            .unwrap_err(),
        RecoverError::Failed
    );
    for i in 0..5 {
        let r = a.recover_authenticator("notes", "ana", bad, PASS, &format!("x{i}"), T0 + i);
        assert_eq!(r.unwrap_err(), RecoverError::Failed);
    }
    let r = a.recover_authenticator("notes", "ana", bad, PASS, "y", T0 + 6);
    assert!(matches!(r, Err(RecoverError::Limited { .. })), "{r:?}");
    // The real user, holding a valid recovery code, is not held by the account counter.
    assert!(
        a.recover_authenticator("notes", "ana", &codes[0], PASS, "phone", T0 + 7)
            .is_ok()
    );
}

// ---- forgotten passphrase: recovery code + one-time code ----

#[test]
fn a_recovery_code_and_a_fresh_code_set_a_new_passphrase() {
    let mut a = auth();
    let (k, codes) = enrolled(&mut a, "ana");
    let t = T0 + 90;
    let tok = token(a.sign_in("notes", "ana", &code(&k, t), PASS, "c", t));
    let fresh = t + 30;
    let new_codes = a
        .recover_passphrase(
            "notes",
            "ana",
            &codes[0],
            &code(&k, fresh),
            "a new passphrase",
            "c",
            fresh,
        )
        .unwrap();
    assert_eq!(new_codes.len(), 10);
    assert_eq!(
        a.validate(&tok, fresh).unwrap(),
        None,
        "every session is revoked"
    );
    assert!(!signs_in(&mut a, "ana", &k, PASS, fresh + 30));
    assert!(signs_in(&mut a, "ana", &k, "a new passphrase", fresh + 60));
    let r = a.recover_passphrase(
        "notes",
        "ana",
        &codes[1],
        &code(&k, fresh + 90),
        "another passphrase",
        "c",
        fresh + 90,
    );
    assert_eq!(
        r.unwrap_err(),
        RecoverError::Failed,
        "the old recovery set is gone"
    );
}

#[test]
fn each_recovery_factor_is_spent_when_valid() {
    let mut a = auth();
    let (k, codes) = enrolled(&mut a, "ana");
    let t = T0 + 90;
    // A valid one-time code with a wrong recovery code: fails, and the code is spent.
    let c = code(&k, t);
    let r = a.recover_passphrase(
        "notes",
        "ana",
        "AAAA-AAAA-AAAA-AAAA",
        &c,
        "a new passphrase",
        "x",
        t,
    );
    assert_eq!(r.unwrap_err(), RecoverError::Failed);
    assert_eq!(
        a.sign_in("notes", "ana", &c, PASS, "c", t + 1),
        SignIn::Failed,
        "the one-time code is spent"
    );
    // A valid recovery code with a wrong one-time code: fails, and the recovery code is spent.
    let r = a.recover_passphrase(
        "notes",
        "ana",
        &codes[0],
        wrong(&k, t + 30),
        "a new passphrase",
        "x",
        t + 30,
    );
    assert_eq!(r.unwrap_err(), RecoverError::Failed);
    assert_eq!(
        a.store
            .account("notes", "ana")
            .unwrap()
            .unwrap()
            .recovery_hashes
            .len(),
        9
    );
}

#[test]
fn the_new_passphrase_meets_the_policy() {
    let mut a = auth();
    let (k, codes) = enrolled(&mut a, "ana");
    let r = a.recover_passphrase(
        "notes",
        "ana",
        &codes[0],
        &code(&k, T0 + 30),
        "short",
        "c",
        T0 + 30,
    );
    assert_eq!(
        r.unwrap_err(),
        RecoverError::Knowledge(knowledge::Rejected::TooShort)
    );
    assert_eq!(
        a.store
            .account("notes", "ana")
            .unwrap()
            .unwrap()
            .recovery_hashes
            .len(),
        10,
        "nothing spent"
    );
}

#[test]
fn recovery_is_scoped_to_its_app_and_account() {
    let mut a = auth();
    let (_, codes) = enrolled(&mut a, "ana");
    enrolled(&mut a, "bo");
    assert_eq!(
        a.recover_authenticator("notes", "bo", &codes[0], PASS, "c", T0 + 30)
            .unwrap_err(),
        RecoverError::Failed
    );
    // Ana's codes are all still there: the failed attempt against Bo did not touch her account.
    let acct = a.store.account("notes", "ana").unwrap().unwrap();
    assert_eq!(acct.recovery_hashes.len(), 10);
}

// ---- regressions from the adversarial review ----

#[test]
fn a_lost_authenticator_is_suspended_while_its_replacement_is_pending() {
    let mut a = auth();
    let (old, codes) = enrolled(&mut a, "ana");
    let t = T0 + 90;
    let e = a
        .recover_authenticator("notes", "ana", &codes[0], PASS, "phone", t)
        .unwrap();
    // A thief holding the old device and the passphrase can no longer sign in...
    assert!(!signs_in(&mut a, "ana", &old, PASS, t + 30));
    // ...nor spend the old device's next-step code to block the new authenticator's confirmation.
    let ahead = code(&old, t + 60);
    assert_eq!(
        a.sign_in("notes", "ana", &ahead, PASS, "thief", t + 31),
        SignIn::Failed
    );
    let new = secret_of(&e.otpauth_uri);
    a.confirm_enrollment(
        "notes",
        "ana",
        e.handle.expose(),
        &code(&new, t + 32),
        t + 32,
    )
    .unwrap();
    assert!(signs_in(&mut a, "ana", &new, PASS, t + 62));
    assert!(!signs_in(&mut a, "ana", &old, PASS, t + 92));
}

#[test]
fn an_unconfirmed_recovery_expires_and_the_old_authenticator_works_again() {
    let mut a = auth();
    let (old, codes) = enrolled(&mut a, "ana");
    let t = T0 + 90;
    a.recover_authenticator("notes", "ana", &codes[0], PASS, "phone", t)
        .unwrap();
    let late = t + rose_signin::flow::PENDING_SECONDS;
    assert!(signs_in(&mut a, "ana", &old, PASS, late));
}

#[test]
fn without_a_valid_invite_enrollment_reveals_nothing_about_the_account() {
    let mut a = auth();
    enrolled(&mut a, "ana");
    a.begin_enrollment("notes", "bo", PASS, Admission::Open, None, T0)
        .unwrap();
    for name in ["ana", "bo", "nobody"] {
        let r = a.begin_enrollment(
            "notes",
            name,
            PASS,
            Admission::Invite(b"bogus"),
            None,
            T0 + 1,
        );
        assert_eq!(r.unwrap_err(), EnrollError::NotAdmitted, "{name}");
    }
}

#[test]
fn an_invite_refused_for_its_account_is_not_spent() {
    let mut a = auth();
    let invite = a.create_invite("notes", "cy", T0).unwrap();
    // The account is enrolled some other way (an app with open sign-up) before the invite is used.
    let e = a
        .begin_enrollment("notes", "cy", PASS, Admission::Open, None, T0)
        .unwrap();
    // While it is pending, the invite holder is refused, and the invite is kept.
    let r = a.begin_enrollment(
        "notes",
        "cy",
        PASS,
        Admission::Invite(invite.expose()),
        None,
        T0 + 1,
    );
    assert_eq!(r.unwrap_err(), EnrollError::Pending);
    // Abandoned: once the pending enrollment expires, the same invite still admits.
    let late = T0 + rose_signin::flow::PENDING_SECONDS;
    let r = a.begin_enrollment(
        "notes",
        "cy",
        PASS,
        Admission::Invite(invite.expose()),
        None,
        late,
    );
    assert!(r.is_ok(), "{r:?}");
    let _ = e;
}
