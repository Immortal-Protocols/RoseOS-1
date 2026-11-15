//! The keyring loader (`rose-signin-keyring/v1`, App Core's spec) against files on temporary directories. Keys are
//! random per test; nothing is fetched and no server listens.

use std::ffi::OsString;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicUsize, Ordering};

use base64ct::{Base64, Encoding};
use rose_app_runtime::signin::Admission;
use rose_app_runtime_signin::keys::{self, KEY_FILE_ENV, KeyError};
use rose_app_runtime_signin::{Config, Keys, Migrations, start};

struct Dir(PathBuf);

impl Dir {
    fn new() -> Dir {
        static N: AtomicUsize = AtomicUsize::new(0);
        let path = std::env::temp_dir().join(format!(
            "rose-app-runtime-signin-keys-{}-{}",
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

fn key(fill: u8) -> String {
    Base64::encode_string(&[fill; 32])
}

fn ring(current: u32, versions: &[u32]) -> String {
    let keys: Vec<String> = versions
        .iter()
        .map(|v| format!(r#"{{"version":{v},"key":"{}"}}"#, key(*v as u8)))
        .collect();
    format!(
        r#"{{"format":"rose-signin-keyring/v1","current":{current},"keys":[{}]}}"#,
        keys.join(",")
    )
}

fn versions(k: &Keys) -> Option<(u32, Vec<u32>)> {
    match k {
        Keys::Supplied { current, keys } => Some((*current, keys.iter().map(|(v, _)| *v).collect())),
        Keys::Unavailable => None,
    }
}

fn parsed(text: &str) -> Result<Option<(u32, Vec<u32>)>, KeyError> {
    keys::parse(text.as_bytes()).map(|k| versions(&k))
}

/// Writes `text` to a file readable by this user only.
fn file(dir: &Path, text: &[u8]) -> PathBuf {
    let path = dir.join("rose-signin-keyring");
    std::fs::write(&path, text).unwrap();
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o400)).unwrap();
    }
    path
}

#[test]
fn the_v1_keyring_parses_into_the_sealers_keys() {
    assert_eq!(parsed(&ring(1, &[1])), Ok(Some((1, vec![1]))));
    assert_eq!(
        parsed(&ring(3, &[1, 2, 3])),
        Ok(Some((3, vec![1, 2, 3]))),
        "retained versions are kept"
    );
    assert_eq!(parsed(&ring(0, &[0])), Ok(Some((0, vec![0]))), "any u32");
    assert_eq!(
        parsed(&ring(u32::MAX, &[u32::MAX])),
        Ok(Some((u32::MAX, vec![u32::MAX])))
    );
    // Fields in any order, standard JSON whitespace between tokens and a trailing newline.
    let spaced = format!(
        "{{\n  \"keys\": [ {{ \"key\": \"{}\" , \"version\": 7 }} ],\r\n\t\"current\": 7,\n  \"format\": \"rose-signin-keyring/v1\"\n}}\n",
        key(9)
    );
    assert_eq!(parsed(&spaced), Ok(Some((7, vec![7]))));
    let max: Vec<u32> = (1..=64).collect();
    assert_eq!(parsed(&ring(64, &max)).map(|k| k.map(|(_, v)| v.len())), Ok(Some(64)));
}

#[test]
fn anything_else_is_refused_with_a_reason() {
    let k = key(1);
    let cases: Vec<(String, KeyError)> = vec![
        ("".into(), KeyError::NotJson),
        ("\u{feff}{}".into(), KeyError::NotJson),
        (
            format!(
                r#"{{"format":"rose-signin-keyring/v1","current":1,"current":1,"keys":[{{"version":1,"key":"{k}"}}]}}"#
            ),
            KeyError::NotJson,
        ),
        (
            format!(r#"{{"format":"rose-signin-keyring/v2","current":1,"keys":[{{"version":1,"key":"{k}"}}]}}"#),
            KeyError::Shape,
        ),
        (
            format!(r#"{{"schema_version":1,"current":1,"keys":[{{"version":1,"key_base64":"{k}"}}]}}"#),
            KeyError::Shape,
        ),
        (
            format!(
                r#"{{"format":"rose-signin-keyring/v1","current":1,"keys":[{{"version":1,"key":"{k}"}}],"note":""}}"#
            ),
            KeyError::Shape,
        ),
        (
            format!(
                r#"{{"format":"rose-signin-keyring/v1","current":1,"keys":[{{"version":1,"key":"{k}","alg":"x"}}]}}"#
            ),
            KeyError::Shape,
        ),
        (
            format!(r#"{{"format":"rose-signin-keyring/v1","keys":[{{"version":1,"key":"{k}"}}]}}"#),
            KeyError::Shape,
        ),
        (
            format!(r#"{{"format":"rose-signin-keyring/v1","current":"1","keys":[{{"version":1,"key":"{k}"}}]}}"#),
            KeyError::Shape,
        ),
        (
            format!(r#"{{"format":"rose-signin-keyring/v1","current":-1,"keys":[{{"version":1,"key":"{k}"}}]}}"#),
            KeyError::Shape,
        ),
        (
            format!(
                r#"{{"format":"rose-signin-keyring/v1","current":4294967296,"keys":[{{"version":1,"key":"{k}"}}]}}"#
            ),
            KeyError::Shape,
        ),
        (
            format!(r#"{{"format":"rose-signin-keyring/v1","current":1,"keys":[{{"version":1.0,"key":"{k}"}}]}}"#),
            KeyError::Shape,
        ),
        (
            r#"{"format":"rose-signin-keyring/v1","current":1,"keys":[]}"#.into(),
            KeyError::KeyCount,
        ),
        (ring(65, &(1..=65).collect::<Vec<_>>()), KeyError::KeyCount),
        (ring(1, &[1, 1]), KeyError::DuplicateVersion),
        (ring(2, &[1]), KeyError::NoCurrent),
        ("[]".into(), KeyError::Shape),
    ];
    for (text, want) in cases {
        assert_eq!(parsed(&text), Err(want), "{text}");
    }
    // The key itself: standard padded base64 of exactly 32 bytes, no whitespace.
    let with = |key: &str| {
        format!(r#"{{"format":"rose-signin-keyring/v1","current":1,"keys":[{{"version":1,"key":"{key}"}}]}}"#)
    };
    let short = Base64::encode_string(&[1; 31]);
    let long = Base64::encode_string(&[1; 33]);
    let url = key(0xfb).replace('+', "-").replace('/', "_");
    assert!(key(0xfb).contains(['+', '/']), "the URL-alphabet case is meaningful");
    let unpadded = key(1).trim_end_matches('=').to_string();
    let spaced = format!("{} {}", &key(1)[..20], &key(1)[20..]);
    let newline = format!("{}\\n", key(1));
    for bad in [short, long, url, unpadded, spaced, newline, "".into(), "!".repeat(44)] {
        assert_eq!(parsed(&with(&bad)), Err(KeyError::BadKey), "{bad}");
    }
}

#[test]
fn the_file_is_absolute_regular_private_and_small() {
    let dir = Dir::new();
    let path = file(&dir.0, ring(2, &[1, 2]).as_bytes());
    assert_eq!(keys::load(&path).map(|k| versions(&k)), Ok(Some((2, vec![1, 2]))));
    assert_eq!(
        keys::load(Path::new("rose-signin-keyring")).err(),
        Some(KeyError::NotAbsolute)
    );
    assert_eq!(keys::load(&dir.0.join("absent")).err(), Some(KeyError::Unreadable));
    assert_eq!(
        keys::load(&dir.0).err(),
        Some(KeyError::Unreadable),
        "a directory is not the keyring"
    );
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        for mode in [0o440, 0o404, 0o644, 0o640, 0o604] {
            std::fs::set_permissions(&path, std::fs::Permissions::from_mode(mode)).unwrap();
            assert_eq!(keys::load(&path).err(), Some(KeyError::Exposed), "{mode:o}");
        }
        // Only reading by others is refused (the spec's rule); write or execute bits alone expose nothing.
        for mode in [0o620, 0o602, 0o610, 0o601, 0o630] {
            std::fs::set_permissions(&path, std::fs::Permissions::from_mode(mode)).unwrap();
            assert!(keys::load(&path).is_ok(), "{mode:o}");
        }
        std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o600)).unwrap();
        assert!(keys::load(&path).is_ok(), "owner read-write is private");
    }
    let mut big = ring(1, &[1]).into_bytes();
    big.extend(std::iter::repeat_n(b' ', 16 * 1024 + 1 - big.len()));
    let dir2 = Dir::new();
    assert_eq!(keys::load(&file(&dir2.0, &big)).err(), Some(KeyError::TooLarge));
    big.truncate(16 * 1024);
    let dir3 = Dir::new();
    assert!(keys::load(&file(&dir3.0, &big)).is_ok(), "exactly 16 KiB is allowed");
    let dir4 = Dir::new();
    assert_eq!(keys::load(&file(&dir4.0, &[0xff, 0xfe])).err(), Some(KeyError::NotJson));
}

/// A FIFO at the path is refused at once: an open that waited for a writer would hang start-up.
#[cfg(target_os = "linux")]
#[test]
fn a_fifo_is_refused_without_waiting() {
    let dir = Dir::new();
    let fifo = dir.0.join("rose-signin-keyring");
    let made = std::process::Command::new("mkfifo")
        .arg("-m")
        .arg("600")
        .arg(&fifo)
        .status();
    if !made.is_ok_and(|s| s.success()) {
        eprintln!("mkfifo is not available; skipped");
        return;
    }
    let (tx, rx) = std::sync::mpsc::channel();
    let path = fifo.clone();
    std::thread::spawn(move || tx.send(keys::load(&path).err()).unwrap());
    let got = rx
        .recv_timeout(std::time::Duration::from_secs(5))
        .expect("load returned instead of blocking");
    assert_eq!(got, Some(KeyError::Unreadable));
}

#[test]
fn from_env_never_fails_start_up_and_says_why() {
    let none = |_: &str| None::<OsString>;
    let (k, why) = Keys::from_lookup(none);
    assert!(matches!(k, Keys::Unavailable));
    assert_eq!(why, Some(KeyError::NotConfigured));
    let dir = Dir::new();
    let bad = file(&dir.0, b"{}");
    let (k, why) = Keys::from_lookup(|n: &str| (n == KEY_FILE_ENV).then(|| bad.clone().into_os_string()));
    assert!(matches!(k, Keys::Unavailable));
    assert_eq!(why, Some(KeyError::Shape));
    // The reason names the problem, never the path or a byte of the payload.
    let shown = format!("{} {:?}", why.unwrap(), why.unwrap());
    assert!(
        !shown.contains(dir.0.to_str().unwrap()) && !shown.contains('{'),
        "{shown}"
    );
}

#[test]
fn printing_the_loaded_keys_shows_no_key() {
    let dir = Dir::new();
    let path = file(&dir.0, ring(5, &[4, 5]).as_bytes());
    let k = keys::load(&path).unwrap();
    let shown = format!("{k:?}");
    assert_eq!(shown, "Keys::Supplied(current 5, versions [4, 5])");
    assert!(!shown.contains(&key(5)));
}

#[test]
fn a_delivered_keyring_makes_the_sealer_ready() {
    let dir = Dir::new();
    let keyring = Dir::new();
    let path = file(&keyring.0, ring(2, &[1, 2]).as_bytes());
    let data = dir.0.clone();
    let lookup = move |name: &str| -> Option<String> {
        match name {
            "DATA_DIR" => Some(data.to_str().unwrap().to_string()),
            "CHARIOT_APP_ID" => Some("6f1c2a0e-1b7d-4c55-9a7e-2f3b4c5d6e7f".into()),
            "CHARIOT_DEPLOYMENT_SHA256" => Some("ab".repeat(32)),
            _ => None,
        }
    };
    let m = Migrations {
        account: &[],
        shared: None,
    };
    let c = Config::from_lookup("notes", "Rose Notes", Admission::Open, m, lookup).unwrap();
    let (k, why) = Keys::from_lookup(|n: &str| (n == KEY_FILE_ENV).then(|| path.clone().into_os_string()));
    assert_eq!(why, None);
    use rose_app_runtime::signin::Auth;
    let started = start(c.clone(), k).unwrap();
    assert!(started.auth.ready(), "the sealer has its current key");
    drop(started);
    let (k, _) = Keys::from_lookup(|_: &str| None);
    assert!(!start(c, k).unwrap().auth.ready(), "no keyring, not ready");
}
