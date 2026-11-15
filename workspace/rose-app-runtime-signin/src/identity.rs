//! The host identity fields `/health` carries (§7.7): `app_id` from the host, `revision_sha256` measured from the
//! server's own binary, `deployment_sha256` echoed from the host's `CHARIOT_DEPLOYMENT_SHA256`. None is made up.

use std::io::Read;
use std::path::Path;

use sha2::{Digest, Sha256};

/// The variable the host passes the deployment hash in (§7.7, agreed with the Chariot owner).
pub const DEPLOYMENT_ENV: &str = "CHARIOT_DEPLOYMENT_SHA256";

/// SHA-256 of a file, lower-case hex.
pub fn file_sha256(path: &Path) -> std::io::Result<String> {
    let mut file = std::fs::File::open(path)?;
    let mut hasher = Sha256::new();
    let mut buf = vec![0u8; 64 * 1024];
    loop {
        let n = file.read(&mut buf)?;
        if n == 0 {
            break;
        }
        hasher.update(&buf[..n]);
    }
    Ok(hasher.finalize().iter().map(|b| format!("{b:02x}")).collect())
}

/// `revision_sha256`: the running binary's own bytes.
pub fn revision_sha256() -> std::io::Result<String> {
    file_sha256(&std::env::current_exe()?)
}

/// A SHA-256 as the host writes one: 64 lower-case hex digits.
pub fn is_sha256_hex(value: &str) -> bool {
    value.len() == 64 && value.bytes().all(|b| matches!(b, b'0'..=b'9' | b'a'..=b'f'))
}

/// The host's app id (`CHARIOT_APP_ID`): a non-empty token of at most 128 visible ASCII characters (the Chariot
/// host's UUID).
pub fn is_app_id(value: &str) -> bool {
    !value.is_empty() && value.len() <= 128 && value.bytes().all(|b| b.is_ascii_graphic())
}
