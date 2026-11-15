//! Start-up: what a generated `main` calls before it serves (§7.4, §7.7).
//!
//! [`start`] opens rose-signin's store under `DATA_DIR` (its own migrations), builds the sealer from the keys it is
//! handed, migrates the shared and every existing account database, and measures the identity. [`Started::server`]
//! then builds the runtime's server and marks the migrations done, and the restore done only when no restore is
//! indicated: Chariot's restore marker ([`Restore`]) is absent. A present marker keeps the server not ready; no
//! restore executor or completion is agreed, so the adapter never runs, reads or removes it. Without a delivered
//! keyring ([`Keys::from_env`]) the server has no key and is not ready either.

use std::path::{Path, PathBuf};
use std::sync::atomic::Ordering;

use rose_app_runtime::server::{Identity, Route, Server};
use rose_app_runtime::signin::Admission;
use rose_signin::flow;
use rose_signin::secret::{AeadSealer, Secret};
use rose_signin::store::sqlite::SqliteStore;
use rusqlite::Connection;

use crate::auth::SigninAuth;
use crate::data::{AppData, DataError, Migrations};
use crate::identity::{self, DEPLOYMENT_ENV};

/// The variable naming the server's only writable directory (§7.7).
pub const DATA_DIR_ENV: &str = "DATA_DIR";
/// The variable carrying the host's app id (§7.7 item 2, settled in the Chariot owner's docs).
pub const APP_ID_ENV: &str = "CHARIOT_APP_ID";
/// The optional variable carrying the revision the host deployed.
pub const REVISION_ENV: &str = "CHARIOT_REVISION_SHA256";
/// The variable naming the host's restore marker; unset, it is `DATA_DIR/.chariot-restore.json`.
pub const RESTORE_ENV: &str = "CHARIOT_RESTORE_STATE_PATH";

/// The sealing keys: what [`Keys::from_env`] loads from the host's keyring file (D9, [`crate::keys`]), or
/// [`Keys::Unavailable`]: the sealer then has no key, sign-in answers `auth_unavailable` and the
/// server is not ready. This crate never fetches, derives or creates a key.
pub enum Keys {
    Unavailable,
    /// 32-byte keys by version; `current` seals new data and must be among them.
    Supplied {
        current: u32,
        keys: Vec<(u32, Secret)>,
    },
}

impl std::fmt::Debug for Keys {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Keys::Unavailable => f.write_str("Keys::Unavailable"),
            Keys::Supplied { current, keys } => {
                let versions: Vec<u32> = keys.iter().map(|(v, _)| *v).collect();
                write!(f, "Keys::Supplied(current {current}, versions {versions:?})")
            }
        }
    }
}

/// Whether a restore is indicated (§7.7 item 5, Chariot `e592c4b1`): the host's restore marker at
/// `CHARIOT_RESTORE_STATE_PATH` (default `DATA_DIR/.chariot-restore.json`), looked up once at start without
/// following a link. Only its presence matters: it is never read, parsed, run, removed or completed here.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Restore {
    /// No marker: no restore is indicated, so readiness's `restore_done` is set.
    NotIndicated,
    /// Something is at the marker's path (a file, directory or link, even a dangling one): the server stays not
    /// ready. No executor or completion protocol is agreed, so nothing here acts on it.
    Indicated,
    /// The lookup failed for another reason: the server stays not ready.
    Unknown,
}

/// The marker's file name in `DATA_DIR` when `CHARIOT_RESTORE_STATE_PATH` is not set (Chariot's default).
pub const RESTORE_MARKER: &str = ".chariot-restore.json";

/// Looks the marker up without following a link: only `NotFound` means no restore.
pub fn restore_state(marker: &Path) -> Restore {
    match std::fs::symlink_metadata(marker) {
        Ok(_) => Restore::Indicated,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => Restore::NotIndicated,
        Err(_) => Restore::Unknown,
    }
}

#[derive(Clone, Debug)]
pub struct Config {
    /// rose-signin's app name for this server: every session must be this app's.
    pub app: String,
    /// The name authenticator apps show beside the code.
    pub issuer: String,
    pub admission: Admission,
    /// The host's app id (the Chariot host's UUID), from `CHARIOT_APP_ID`.
    pub app_id: String,
    pub data_dir: PathBuf,
    /// The host's `CHARIOT_DEPLOYMENT_SHA256`, echoed as it is.
    pub deployment_sha256: String,
    /// The host's `CHARIOT_REVISION_SHA256`, when it passes one: the binary it deployed. A server whose own
    /// measurement differs is not ready (its authenticator is held), so a wrong binary never serves sign-in.
    pub expected_revision: Option<String>,
    /// Where the host's restore marker would be (`CHARIOT_RESTORE_STATE_PATH`, or `DATA_DIR/.chariot-restore.json`).
    pub restore_marker: PathBuf,
    pub migrations: Migrations,
}

impl Config {
    /// The configuration from the environment the host gives the server: `DATA_DIR`, `CHARIOT_APP_ID`,
    /// `CHARIOT_DEPLOYMENT_SHA256` and, when set, `CHARIOT_REVISION_SHA256`. Everything else is the build's.
    pub fn from_env(
        app: &str,
        issuer: &str,
        admission: Admission,
        migrations: Migrations,
    ) -> Result<Config, StartError> {
        // A variable that is set but not UTF-8 is kept as an empty value, which the checks refuse: it never reads
        // as unset (an unreadable CHARIOT_REVISION_SHA256 must not switch the revision check off).
        Self::from_lookup(app, issuer, admission, migrations, |name| match std::env::var(name) {
            Ok(value) => Some(value),
            Err(std::env::VarError::NotPresent) => None,
            Err(std::env::VarError::NotUnicode(_)) => Some(String::new()),
        })
    }

    /// [`Config::from_env`] over any variable lookup.
    pub fn from_lookup(
        app: &str,
        issuer: &str,
        admission: Admission,
        migrations: Migrations,
        lookup: impl Fn(&str) -> Option<String>,
    ) -> Result<Config, StartError> {
        let data_dir = lookup(DATA_DIR_ENV).ok_or(StartError::Missing(DATA_DIR_ENV))?;
        let app_id = lookup(APP_ID_ENV).ok_or(StartError::Missing(APP_ID_ENV))?;
        let deployment = lookup(DEPLOYMENT_ENV).ok_or(StartError::Missing(DEPLOYMENT_ENV))?;
        let data_dir = PathBuf::from(data_dir);
        let restore_marker = lookup(RESTORE_ENV).map_or_else(|| data_dir.join(RESTORE_MARKER), PathBuf::from);
        Ok(Config {
            app: app.to_string(),
            issuer: issuer.to_string(),
            admission,
            app_id,
            restore_marker,
            data_dir,
            deployment_sha256: deployment,
            expected_revision: lookup(REVISION_ENV),
            migrations,
        })
    }
}

#[derive(Debug)]
pub enum StartError {
    /// A required environment variable is absent.
    Missing(&'static str),
    /// A value is malformed; the text names which one, never its content.
    Invalid(&'static str),
    /// rose-signin's store could not be opened or migrated (a newer schema is refused here).
    Store(String),
    Data(DataError),
    /// The server's own binary could not be read for `revision_sha256`.
    Revision(String),
}

impl std::fmt::Display for StartError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            StartError::Missing(name) => write!(f, "{name} is not set"),
            StartError::Invalid(what) => write!(f, "{what} is not valid"),
            StartError::Store(m) => write!(f, "sign-in store: {m}"),
            StartError::Data(e) => write!(f, "app data: {e}"),
            StartError::Revision(m) => write!(f, "revision_sha256: {m}"),
        }
    }
}

impl std::error::Error for StartError {}

/// Everything a generated server needs, opened and migrated.
#[derive(Debug)]
pub struct Started {
    pub app: String,
    pub admission: Admission,
    pub auth: SigninAuth,
    pub data: AppData,
    pub identity: Identity,
    /// Account databases checked and migrated at start-up.
    pub accounts_migrated: usize,
    /// False when the host named a revision and this binary is another: the server is then not ready.
    pub revision_matches: bool,
    pub restore: Restore,
}

pub(crate) fn auth_store(dir: &Path) -> Result<SqliteStore, StartError> {
    let conn = Connection::open(dir.join("auth.sqlite3")).map_err(|e| StartError::Store(e.to_string()))?;
    SqliteStore::new(conn).map_err(|e| StartError::Store(e.0))
}

fn sealer(keys: Keys) -> Result<AeadSealer, StartError> {
    match keys {
        Keys::Unavailable => AeadSealer::new(None, Vec::new()),
        Keys::Supplied { current, keys } => AeadSealer::new(Some(current), keys),
    }
    .map_err(|_| StartError::Invalid("a sealing key"))
}

/// Opens and migrates everything under `config.data_dir` and measures the identity.
pub fn start(config: Config, keys: Keys) -> Result<Started, StartError> {
    if !flow::valid_name(&config.app) {
        return Err(StartError::Invalid("the app name"));
    }
    if !flow::valid_name(&config.issuer) {
        return Err(StartError::Invalid("the issuer"));
    }
    if !identity::is_app_id(&config.app_id) {
        return Err(StartError::Invalid(APP_ID_ENV));
    }
    if config
        .expected_revision
        .as_deref()
        .is_some_and(|r| !identity::is_sha256_hex(r))
    {
        return Err(StartError::Invalid(REVISION_ENV));
    }
    if !identity::is_sha256_hex(&config.deployment_sha256) {
        return Err(StartError::Invalid(DEPLOYMENT_ENV));
    }
    if !config.data_dir.is_absolute() {
        return Err(StartError::Invalid(DATA_DIR_ENV));
    }
    if !config.restore_marker.is_absolute() {
        return Err(StartError::Invalid(RESTORE_ENV));
    }
    // Looked up once, first: a restore indicated now keeps the server not ready for this whole run.
    let restore = restore_state(&config.restore_marker);
    let data = AppData::new(&config.data_dir, &config.app, config.migrations).map_err(StartError::Data)?;
    let store = auth_store(&config.data_dir)?;
    let inner = flow::Authenticator::new(store, sealer(keys)?, &config.issuer)
        .map_err(|_| StartError::Invalid("the issuer"))?;
    let mut auth = SigninAuth::new(&config.app, inner).map_err(|_| StartError::Invalid("the app name"))?;
    let accounts_migrated = data.migrate_all().map_err(StartError::Data)?;
    let revision = identity::revision_sha256().map_err(|e| StartError::Revision(e.to_string()))?;
    let revision_matches = config.expected_revision.as_deref().is_none_or(|r| r == revision);
    if !revision_matches {
        auth.hold();
    }
    Ok(Started {
        app: config.app,
        admission: config.admission,
        auth,
        data,
        identity: Identity {
            app_id: config.app_id,
            revision_sha256: revision,
            deployment_sha256: config.deployment_sha256,
        },
        accounts_migrated,
        revision_matches,
        restore,
    })
}

impl Started {
    /// The runtime's server over this authenticator, with the app data attached for handlers (`ctx.data`,
    /// downcast to [`AppData`]) and the migrations marked done. `restore_done` is set only when no restore is
    /// indicated ([`Restore::NotIndicated`]); otherwise the server answers `503`.
    pub fn server(self, routes: Vec<Route>) -> Result<Server, String> {
        let restore_done = self.restore == Restore::NotIndicated;
        let server = Server::new(&self.app, self.admission, routes, self.identity, Box::new(self.auth))?
            .with_data(Box::new(self.data));
        server.readiness.migrations_done.store(true, Ordering::SeqCst);
        server.readiness.restore_done.store(restore_done, Ordering::SeqCst);
        Ok(server)
    }
}
