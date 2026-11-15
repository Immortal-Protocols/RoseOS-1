//! Start-up: what a generated `main` calls before it serves (§7.4, §7.7).
//!
//! [`start`] opens rose-signin's store under `DATA_DIR` (its own migrations), builds the sealer from the keys it is
//! handed, migrates the shared and every existing account database, and measures the identity. [`Started::server`]
//! then builds the runtime's server and marks the migrations done. It never marks a restore done: the restore
//! marker is not agreed yet (§7.7 item 5), so a server started here is not ready, and says so, until that contract
//! and the key's delivery (D9, item 3) are settled.

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

/// The sealing keys, handed in by whoever resolves them. Their delivery is not agreed (D9), so a generated server
/// passes [`Keys::Unavailable`] today: the sealer then has no key, sign-in answers `auth_unavailable` and the
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

/// What a restore after a backup needs. Only one state exists until the marker's contract is agreed.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Restore {
    /// No restore check has run (§7.7 item 5 is open), so readiness's `restore_done` stays false.
    Unresolved,
}

#[derive(Clone, Debug)]
pub struct Config {
    /// rose-signin's app name for this server: every session must be this app's.
    pub app: String,
    /// The name authenticator apps show beside the code.
    pub issuer: String,
    pub admission: Admission,
    /// The host's app id (the Chariot host's UUID), passed by the caller: its variable is not named yet.
    pub app_id: String,
    pub data_dir: PathBuf,
    /// The host's `CHARIOT_DEPLOYMENT_SHA256`, echoed as it is.
    pub deployment_sha256: String,
    pub migrations: Migrations,
}

impl Config {
    /// The configuration from the environment the host gives the server: `DATA_DIR` and
    /// `CHARIOT_DEPLOYMENT_SHA256`. Everything else is the build's.
    pub fn from_env(
        app: &str,
        issuer: &str,
        admission: Admission,
        app_id: &str,
        migrations: Migrations,
    ) -> Result<Config, StartError> {
        Self::from_lookup(app, issuer, admission, app_id, migrations, |name| {
            std::env::var(name).ok()
        })
    }

    /// [`Config::from_env`] over any variable lookup.
    pub fn from_lookup(
        app: &str,
        issuer: &str,
        admission: Admission,
        app_id: &str,
        migrations: Migrations,
        lookup: impl Fn(&str) -> Option<String>,
    ) -> Result<Config, StartError> {
        let data_dir = lookup(DATA_DIR_ENV).ok_or(StartError::Missing(DATA_DIR_ENV))?;
        let deployment = lookup(DEPLOYMENT_ENV).ok_or(StartError::Missing(DEPLOYMENT_ENV))?;
        Ok(Config {
            app: app.to_string(),
            issuer: issuer.to_string(),
            admission,
            app_id: app_id.to_string(),
            data_dir: PathBuf::from(data_dir),
            deployment_sha256: deployment,
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
    pub restore: Restore,
}

fn auth_store(dir: &Path) -> Result<SqliteStore, StartError> {
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
        return Err(StartError::Invalid("the host's app id"));
    }
    if !identity::is_sha256_hex(&config.deployment_sha256) {
        return Err(StartError::Invalid(DEPLOYMENT_ENV));
    }
    if !config.data_dir.is_absolute() {
        return Err(StartError::Invalid(DATA_DIR_ENV));
    }
    let data = AppData::new(&config.data_dir, &config.app, config.migrations).map_err(StartError::Data)?;
    let store = auth_store(&config.data_dir)?;
    let inner = flow::Authenticator::new(store, sealer(keys)?, &config.issuer)
        .map_err(|_| StartError::Invalid("the issuer"))?;
    let auth = SigninAuth::new(&config.app, inner).map_err(|_| StartError::Invalid("the app name"))?;
    let accounts_migrated = data.migrate_all().map_err(StartError::Data)?;
    let revision = identity::revision_sha256().map_err(|e| StartError::Revision(e.to_string()))?;
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
        restore: Restore::Unresolved,
    })
}

impl Started {
    /// The runtime's server over this authenticator, with the app data attached for handlers (`ctx.data`,
    /// downcast to [`AppData`]) and the migrations marked done. `restore_done` stays false (§7.7 item 5), so the
    /// server answers `503` until the restore contract is settled.
    pub fn server(self, routes: Vec<Route>) -> Result<Server, String> {
        let server = Server::new(&self.app, self.admission, routes, self.identity, Box::new(self.auth))?
            .with_data(Box::new(self.data));
        server.readiness.migrations_done.store(true, Ordering::SeqCst);
        Ok(server)
    }
}
