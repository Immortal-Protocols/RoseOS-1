//! The sealing keyring the host hands the server (D9, §7.7 item 3), parsed into rose-signin's `AeadSealer` keys.
//!
//! **Delivery.** `CHARIOT_SEALING_KEY_FILE` names the systemd credential `rose-signin-keyring` (an absolute path,
//! for example under `$CREDENTIALS_DIRECTORY`). The host passes the payload's bytes as they are; this is the only
//! place they are read. The file must be a regular file readable by this user only, at most 16 KiB; it is read once
//! at start and never re-read (a rotation takes effect on restart). Its path and contents never reach a log, an
//! error or `Debug`.
//!
//! **Format** (`rose-signin-keyring/v1`, App Core's spec):
//! `{"format":"rose-signin-keyring/v1","current":<u32>,"keys":[{"version":<u32>,"key":"<base64>"}]}`
//! - strict UTF-8 JSON, standard whitespace between tokens allowed; no unknown or repeated field;
//! - 1 to 64 keys with unique `u32` versions, `current` among them;
//! - each `key` is standard padded base64 with no whitespace, decoding to exactly 32 bytes.
//!
//! **Failure is not ready, never a crash.** [`Keys::from_env`] answers [`Keys::Unavailable`] for any problem and
//! says which in one word ([`KeyError`]), so the server starts and `/health` answers `503`. A retained version must
//! stay in the payload while data sealed under it may exist: dropping it makes those secrets fail to open
//! (`Integrity`); nothing is ever re-keyed silently.

use std::io::Read;
use std::path::Path;

use base64ct::{Base64, Encoding};
use rose_app_runtime::json::{self, Value};
use rose_signin::secret::Secret;

use crate::start::Keys;

/// The variable naming the keyring file (agreed with the Chariot owner).
pub const KEY_FILE_ENV: &str = "CHARIOT_SEALING_KEY_FILE";
pub const FORMAT: &str = "rose-signin-keyring/v1";
pub const MAX_BYTES: u64 = 16 * 1024;
pub const MAX_KEYS: usize = 64;

/// Why no key was loaded. Each names the problem, never the path or a byte of the payload.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum KeyError {
    /// `CHARIOT_SEALING_KEY_FILE` is not set.
    NotConfigured,
    NotAbsolute,
    /// Missing, unreadable, or not a regular file.
    Unreadable,
    /// Readable by the group or others.
    Exposed,
    TooLarge,
    /// Not UTF-8 or not JSON (including a repeated field).
    NotJson,
    /// Valid JSON that is not `rose-signin-keyring/v1`: a field missing, unknown or of the wrong type.
    Shape,
    /// No keys, or more than 64.
    KeyCount,
    DuplicateVersion,
    /// `current` is not among the keys.
    NoCurrent,
    /// A key is not standard padded base64 of exactly 32 bytes.
    BadKey,
}

impl KeyError {
    pub fn word(self) -> &'static str {
        match self {
            KeyError::NotConfigured => "not_configured",
            KeyError::NotAbsolute => "not_absolute",
            KeyError::Unreadable => "unreadable",
            KeyError::Exposed => "exposed",
            KeyError::TooLarge => "too_large",
            KeyError::NotJson => "not_json",
            KeyError::Shape => "shape",
            KeyError::KeyCount => "key_count",
            KeyError::DuplicateVersion => "duplicate_version",
            KeyError::NoCurrent => "no_current",
            KeyError::BadKey => "bad_key",
        }
    }
}

impl std::fmt::Display for KeyError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "sealing keys unavailable: {}", self.word())
    }
}

impl std::error::Error for KeyError {}

/// Bytes overwritten with zeros when dropped (rose-signin's `Secret` only redacts its `Debug`).
struct Wiped(Vec<u8>);

impl Drop for Wiped {
    fn drop(&mut self) {
        self.0.fill(0);
        std::hint::black_box(&self.0);
    }
}

/// Overwrites every string in a parsed value before it is dropped.
fn wipe(value: Value) {
    match value {
        Value::Str(s) => drop(Wiped(s.into_bytes())),
        Value::Arr(items) => items.into_iter().for_each(wipe),
        Value::Obj(members) => members.into_iter().for_each(|(k, v)| {
            drop(Wiped(k.into_bytes()));
            wipe(v);
        }),
        _ => {}
    }
}

fn u32_of(v: &Value) -> Result<u32, KeyError> {
    match v {
        Value::Int(i) => u32::try_from(*i).map_err(|_| KeyError::Shape),
        _ => Err(KeyError::Shape),
    }
}

/// The members of an object that has exactly the fields `names`, in any order.
fn exactly<'a>(v: &'a Value, names: &[&str]) -> Result<Vec<&'a Value>, KeyError> {
    let Value::Obj(members) = v else {
        return Err(KeyError::Shape);
    };
    if members.len() != names.len() {
        return Err(KeyError::Shape);
    }
    names.iter().map(|n| v.get(n).ok_or(KeyError::Shape)).collect()
}

fn keys_of(root: &Value) -> Result<Keys, KeyError> {
    let fields = exactly(root, &["format", "current", "keys"])?;
    if fields[0].as_str() != Some(FORMAT) {
        return Err(KeyError::Shape);
    }
    let current = u32_of(fields[1])?;
    let Value::Arr(entries) = fields[2] else {
        return Err(KeyError::Shape);
    };
    if entries.is_empty() || entries.len() > MAX_KEYS {
        return Err(KeyError::KeyCount);
    }
    let mut keys: Vec<(u32, Secret)> = Vec::with_capacity(entries.len());
    for entry in entries {
        let f = exactly(entry, &["version", "key"])?;
        let version = u32_of(f[0])?;
        let text = f[1].as_str().ok_or(KeyError::Shape)?;
        // Standard padded base64 only: base64ct's `Base64` refuses whitespace, missing or extra padding and the
        // URL alphabet; exactly 32 bytes decode from exactly 44 characters.
        let mut buf = Wiped(vec![0u8; 32]);
        if text.len() != 44 || Base64::decode(text, &mut buf.0).map(|b| b.len()) != Ok(32) {
            return Err(KeyError::BadKey);
        }
        if keys.iter().any(|(v, _)| *v == version) {
            return Err(KeyError::DuplicateVersion);
        }
        keys.push((version, Secret::new(buf.0.clone())));
    }
    if !keys.iter().any(|(v, _)| *v == current) {
        return Err(KeyError::NoCurrent);
    }
    Ok(Keys::Supplied { current, keys })
}

/// Parses a payload already read into memory.
pub fn parse(payload: &[u8]) -> Result<Keys, KeyError> {
    if payload.len() as u64 > MAX_BYTES {
        return Err(KeyError::TooLarge);
    }
    std::str::from_utf8(payload).map_err(|_| KeyError::NotJson)?;
    let root = json::parse(payload).map_err(|_| KeyError::NotJson)?;
    let result = keys_of(&root);
    wipe(root);
    result
}

/// Reads and parses the keyring file at `path`.
pub fn load(path: &Path) -> Result<Keys, KeyError> {
    if !path.is_absolute() {
        return Err(KeyError::NotAbsolute);
    }
    let file = std::fs::File::open(path).map_err(|_| KeyError::Unreadable)?;
    // Checked on the opened file, so what is checked is what is read.
    let meta = file.metadata().map_err(|_| KeyError::Unreadable)?;
    if !meta.is_file() {
        return Err(KeyError::Unreadable);
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        if meta.permissions().mode() & 0o077 != 0 {
            return Err(KeyError::Exposed);
        }
    }
    if meta.len() > MAX_BYTES {
        return Err(KeyError::TooLarge);
    }
    let mut payload = Wiped(Vec::with_capacity(meta.len() as usize));
    file.take(MAX_BYTES + 1)
        .read_to_end(&mut payload.0)
        .map_err(|_| KeyError::Unreadable)?;
    parse(&payload.0)
}

impl Keys {
    /// The keys the host delivered, or [`Keys::Unavailable`] with the reason: never an error that stops start-up.
    pub fn from_env() -> (Keys, Option<KeyError>) {
        Self::from_lookup(|name| std::env::var_os(name))
    }

    /// [`Keys::from_env`] over any variable lookup.
    pub fn from_lookup(lookup: impl Fn(&str) -> Option<std::ffi::OsString>) -> (Keys, Option<KeyError>) {
        let loaded = match lookup(KEY_FILE_ENV) {
            None => Err(KeyError::NotConfigured),
            Some(path) => load(Path::new(&path)),
        };
        match loaded {
            Ok(keys) => (keys, None),
            Err(e) => (Keys::Unavailable, Some(e)),
        }
    }
}
