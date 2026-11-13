//! Every line of vectors/rose-auth-v1.txt (shared with the Java client core).

use rose_auth::{base32, otp};

fn hex(s: &str) -> Vec<u8> {
    if s == "-" {
        return vec![];
    }
    (0..s.len())
        .step_by(2)
        .map(|i| u8::from_str_radix(&s[i..i + 2], 16).unwrap())
        .collect()
}

#[test]
fn shared_vectors() {
    let text = std::fs::read_to_string(concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/vectors/rose-auth-v1.txt"
    ))
    .unwrap();
    let mut n = 0;
    for line in text
        .lines()
        .filter(|l| !l.starts_with('#') && !l.is_empty())
    {
        let f: Vec<&str> = line.split(' ').collect();
        match f[0] {
            "hotp" => assert_eq!(
                otp::format(
                    otp::hotp(&hex(f[1]), f[2].parse().unwrap(), f[3].parse().unwrap()),
                    f[3].parse().unwrap()
                ),
                f[4],
                "{line}"
            ),
            "totp" => {
                let p = otp::Profile {
                    digits: f[3].parse().unwrap(),
                    ..otp::V1
                };
                assert_eq!(
                    otp::totp(&hex(f[1]), p, f[2].parse().unwrap()),
                    f[4],
                    "{line}"
                );
            }
            "verify" => {
                let last = if f[4] == "-" {
                    None
                } else {
                    Some(f[4].parse().unwrap())
                };
                let want = if f[6] == "-" {
                    None
                } else {
                    Some(f[6].parse().unwrap())
                };
                assert_eq!(
                    otp::verify(&hex(f[1]), otp::V1, f[3], f[2].parse().unwrap(), last),
                    want,
                    "{line}"
                );
            }
            "base32" => {
                let want = if f[2] == "-" { "" } else { f[2] };
                assert_eq!(base32::encode(&hex(f[1])), want, "{line}");
                assert_eq!(base32::decode(want).unwrap(), hex(f[1]), "{line}");
            }
            other => panic!("unknown vector kind {other}"),
        }
        n += 1;
    }
    assert_eq!(
        n, 43,
        "every vector line is checked (10 hotp, 12 totp, 13 verify, 8 base32)"
    );
}

#[test]
fn base32_is_forgiving_only_where_users_type() {
    assert_eq!(base32::decode("mzxw 6ytb oi======").unwrap(), b"foobar");
    assert!(base32::decode("MZXW6YTBO1").is_none(), "1 is not base32");
    assert!(
        base32::decode("MZXW6YTBOJ").is_none(),
        "non-zero trailing bits"
    );
}

#[test]
fn otpauth_uri_is_what_authenticator_apps_read() {
    let uri = otp::otpauth_uri(
        "Rose Notes",
        "ana@example.com",
        b"12345678901234567890",
        otp::V1,
    );
    assert_eq!(
        uri,
        "otpauth://totp/Rose%20Notes:ana%40example.com?secret=GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ&issuer=Rose%20Notes&algorithm=SHA1&digits=6&period=30"
    );
}
