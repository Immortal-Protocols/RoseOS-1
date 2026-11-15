//! The reference AEAD sealer: context binding, the two failure kinds, readiness and key rotation.

use rose_signin::secret::{
    AeadSealer, OTP_SECRET, OpenError, SealContext, SealError, Sealer, Secret,
};

fn key(b: u8) -> Secret {
    Secret::new(vec![b; 32])
}

fn ctx<'a>(app: &'a str, account: &'a str) -> SealContext<'a> {
    SealContext {
        app,
        account,
        purpose: OTP_SECRET,
    }
}

#[test]
fn a_sealed_secret_opens_only_in_its_own_context() {
    let s = AeadSealer::new(Some(1), vec![(1, key(1))]).unwrap();
    let secret = Secret::new(b"12345678901234567890".to_vec());
    let sealed = s.seal(&ctx("notes", "ana"), &secret).unwrap();
    assert_eq!(s.open(&ctx("notes", "ana"), &sealed).unwrap(), secret);
    // Copied into another account's row, another app's, or used for another purpose, it fails integrity.
    assert_eq!(
        s.open(&ctx("notes", "bo"), &sealed),
        Err(OpenError::Integrity)
    );
    assert_eq!(
        s.open(&ctx("recipes", "ana"), &sealed),
        Err(OpenError::Integrity)
    );
    let other = SealContext {
        app: "notes",
        account: "ana",
        purpose: "other/v1",
    };
    assert_eq!(s.open(&other, &sealed), Err(OpenError::Integrity));
    // Length-prefixed context: ("ab", "c") is not ("a", "bc").
    let sealed2 = s.seal(&ctx("ab", "c"), &secret).unwrap();
    assert_eq!(s.open(&ctx("a", "bc"), &sealed2), Err(OpenError::Integrity));
    // The plaintext never appears in the envelope, and two seals differ (fresh nonce).
    assert!(!sealed.windows(20).any(|w| w == secret.expose()));
    assert_ne!(sealed, s.seal(&ctx("notes", "ana"), &secret).unwrap());
}

#[test]
fn tampering_is_an_integrity_failure_and_a_missing_key_is_unavailable() {
    let s = AeadSealer::new(Some(1), vec![(1, key(1))]).unwrap();
    let secret = Secret::new(vec![9; 20]);
    let sealed = s.seal(&ctx("notes", "ana"), &secret).unwrap();
    for i in 0..sealed.len() {
        let mut t = sealed.clone();
        t[i] ^= 1;
        let r = s.open(&ctx("notes", "ana"), &t);
        // Flipping the version bytes names a key that is not loaded; anything else is integrity.
        if (1..5).contains(&i) {
            assert!(
                matches!(
                    r,
                    Err(OpenError::KeyUnavailable) | Err(OpenError::Integrity)
                ),
                "byte {i}: {r:?}"
            );
        } else {
            assert_eq!(r, Err(OpenError::Integrity), "byte {i}");
        }
    }
    assert_eq!(
        s.open(&ctx("notes", "ana"), &sealed[..10]),
        Err(OpenError::Integrity)
    );
    assert_eq!(s.open(&ctx("notes", "ana"), b""), Err(OpenError::Integrity));
    // The version that sealed it is not loaded: unavailable, never a guess with another key.
    let without = AeadSealer::new(Some(2), vec![(2, key(2))]).unwrap();
    assert_eq!(
        without.open(&ctx("notes", "ana"), &sealed),
        Err(OpenError::KeyUnavailable)
    );
}

#[test]
fn without_its_current_key_a_sealer_is_not_ready_and_seals_nothing() {
    let none = AeadSealer::new(None, vec![(1, key(1))]).unwrap();
    assert!(!none.ready());
    assert_eq!(
        none.seal(&ctx("notes", "ana"), &Secret::new(vec![1])),
        Err(SealError::KeyUnavailable)
    );
    let missing = AeadSealer::new(Some(3), vec![(1, key(1))]).unwrap();
    assert!(!missing.ready());
    assert_eq!(
        missing.seal(&ctx("notes", "ana"), &Secret::new(vec![1])),
        Err(SealError::KeyUnavailable)
    );
    assert!(
        AeadSealer::new(Some(1), vec![(1, Secret::new(vec![1; 16]))]).is_err(),
        "keys are 256-bit"
    );
}

#[test]
fn rotation_keeps_old_versions_readable_and_marks_them_stale() {
    let old = AeadSealer::new(Some(1), vec![(1, key(1))]).unwrap();
    let secret = Secret::new(vec![5; 20]);
    let sealed_v1 = old.seal(&ctx("notes", "ana"), &secret).unwrap();
    assert_eq!(
        &sealed_v1[..5],
        &[1, 0, 0, 0, 1],
        "envelope v1, key version 1"
    );
    let rotated = AeadSealer::new(Some(2), vec![(1, key(1)), (2, key(2))]).unwrap();
    assert_eq!(
        rotated.open(&ctx("notes", "ana"), &sealed_v1).unwrap(),
        secret
    );
    assert!(rotated.stale(&sealed_v1));
    let sealed_v2 = rotated.seal(&ctx("notes", "ana"), &secret).unwrap();
    assert!(!rotated.stale(&sealed_v2));
    assert_eq!(&sealed_v2[1..5], &2u32.to_be_bytes());
}
