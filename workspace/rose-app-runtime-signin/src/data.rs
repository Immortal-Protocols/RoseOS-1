//! [`AppData`]: the app's own data, one SQLite file per account plus the declared shared database (§7.4).
//!
//! Layout under `DATA_DIR` (the only writable path, §7.7):
//! - `auth.sqlite3`: rose-signin's store (its own `auth_*` migrations; not opened here);
//! - `accounts/<sha256 hex>.sqlite3`: one file per account, named by SHA-256 over a fixed label, the app and the
//!   account, so no path a request can name reaches another account's file, and a name never appears on disk;
//! - `shared.sqlite3`: only when the app declares shared data.
//!
//! Migrations are the builder-embedded lists (`server/migrations/account/NNNN_*.sql` and `shared/`, already
//! checked offline by §7.6), applied forward only, all pending ones in one transaction with the versions they
//! record (`PRAGMA user_version`). A database whose version is above the list (written by a newer build) is refused
//! and never downgraded or touched.
//!
//! Foreign keys follow SQLite's documented procedure for schema changes: they are switched off outside the
//! transaction while migrations run (inside one, `PRAGMA foreign_keys` does nothing, so a table rebuild would fire
//! `ON DELETE` actions or fail on its own children), and `PRAGMA foreign_key_check` must find no violation before
//! the commit. A violation rolls every pending migration back and refuses the database
//! ([`DataError::ForeignKeys`]): nothing is lost and it stays at its old version (at start-up the server then does
//! not start). The check covers every table, and also refuses a foreign key left pointing at a table that no
//! longer exists. Neither `ON DELETE` nor `ON UPDATE` actions act during migrations, so a migration deletes or
//! re-keys child rows itself. Handlers never get the connection the migrations ran on.

use std::path::{Path, PathBuf};
use std::time::Duration;

use rose_app_runtime::signin::Principal;
use rusqlite::{Connection, OptionalExtension};
use sha2::{Digest, Sha256};

/// The builder-embedded migrations, in order: index `i` takes a database from version `i` to `i + 1`.
#[derive(Clone, Copy, Debug)]
pub struct Migrations {
    pub account: &'static [&'static str],
    /// Present only when the app declares shared data.
    pub shared: Option<&'static [&'static str]>,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum DataError {
    /// The database records `found` migrations but this build knows `known`: a newer build wrote it.
    NewerSchema { found: i64, known: usize },
    /// The principal is another app's.
    NotThisApp,
    /// The app declared no shared data.
    NoShared,
    /// After the pending migrations, `table` references a missing parent (a row with no parent row, whether the
    /// migrations caused it or not, or a foreign key to a table that no longer exists): nothing was committed.
    ForeignKeys { table: String },
    /// SQLite or the file system failed; the message names no account.
    Storage(String),
}

impl std::fmt::Display for DataError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            DataError::NewerSchema { found, known } => {
                write!(
                    f,
                    "database schema {found} is newer than this build's {known} migrations"
                )
            }
            DataError::NotThisApp => f.write_str("the principal belongs to another app"),
            DataError::NoShared => f.write_str("the app declares no shared data"),
            DataError::ForeignKeys { table } => {
                write!(
                    f,
                    "after the migrations {table} references a missing parent; nothing was committed"
                )
            }
            DataError::Storage(m) => write!(f, "storage: {m}"),
        }
    }
}

impl std::error::Error for DataError {}

fn storage(e: impl std::fmt::Display) -> DataError {
    DataError::Storage(e.to_string())
}

/// How long a busy database is retried before the request fails.
const BUSY: Duration = Duration::from_secs(5);

/// The file name (without extension) of `account`'s database in `app`.
pub fn account_key(app: &str, account: &str) -> String {
    let mut h = Sha256::new();
    h.update(b"rose-app-data/v1\0");
    h.update((app.len() as u64).to_be_bytes());
    h.update(app.as_bytes());
    h.update((account.len() as u64).to_be_bytes());
    h.update(account.as_bytes());
    h.finalize().iter().map(|b| format!("{b:02x}")).collect()
}

/// Applies `list` forward from the database's recorded version, in one immediate transaction, with foreign keys
/// off while it runs and checked before it commits; returns how many migrations it applied. The connection's
/// foreign-key setting is restored afterwards on every path, and a restore that did not take (the transaction still
/// open) is reported as an error rather than passed off as success.
pub fn migrate(conn: &Connection, list: &[&str]) -> Result<usize, DataError> {
    conn.busy_timeout(BUSY).map_err(storage)?;
    let enforced: bool = conn
        .query_row("PRAGMA foreign_keys", [], |r| r.get(0))
        .map_err(storage)?;
    // Outside any transaction, where the pragma takes effect.
    conn.execute_batch("PRAGMA foreign_keys = OFF").map_err(storage)?;
    let result = migrate_unenforced(conn, list);
    let restored = conn
        .execute_batch(if enforced {
            "PRAGMA foreign_keys = ON"
        } else {
            "PRAGMA foreign_keys = OFF"
        })
        .map_err(storage)
        .and_then(|()| {
            // Inside a transaction the pragma silently does nothing: check that it took.
            let now: bool = conn
                .query_row("PRAGMA foreign_keys", [], |r| r.get(0))
                .map_err(storage)?;
            if conn.is_autocommit() && now == enforced {
                Ok(())
            } else {
                Err(DataError::Storage(
                    "the foreign-key setting could not be restored".into(),
                ))
            }
        });
    let applied = result?;
    restored?;
    Ok(applied)
}

/// The first table holding a foreign key whose parent table does not exist (a reference left to a dropped or
/// renamed table, which `foreign_key_check` does not report while the child table is empty).
fn dangling_parent(conn: &Connection) -> Result<Option<String>, DataError> {
    conn.query_row(
        "SELECT m.name FROM sqlite_schema AS m, pragma_foreign_key_list(m.name) AS f
         WHERE m.type = 'table'
           AND NOT EXISTS (SELECT 1 FROM sqlite_schema AS p
                           WHERE p.type = 'table' AND p.name = f.\"table\" COLLATE NOCASE)
         LIMIT 1",
        [],
        |r| r.get(0),
    )
    .optional()
    .map_err(storage)
}

fn migrate_unenforced(conn: &Connection, list: &[&str]) -> Result<usize, DataError> {
    conn.execute_batch("BEGIN IMMEDIATE").map_err(storage)?;
    let result = (|| {
        let found: i64 = conn
            .query_row("PRAGMA user_version", [], |r| r.get(0))
            .map_err(storage)?;
        if found < 0 || found as u64 > list.len() as u64 {
            return Err(DataError::NewerSchema {
                found,
                known: list.len(),
            });
        }
        let mut applied = 0;
        for (i, sql) in list.iter().enumerate().skip(found as usize) {
            conn.execute_batch(sql).map_err(storage)?;
            conn.execute_batch(&format!("PRAGMA user_version = {}", i + 1))
                .map_err(storage)?;
            applied += 1;
        }
        if applied > 0 {
            // Every table, not only those the migrations name: a migration can break a reference another table
            // holds. The first violating table only, never a row's contents.
            let violation: Option<String> = conn
                .query_row("PRAGMA foreign_key_check", [], |r| r.get(0))
                .optional()
                .map_err(storage)?;
            let violation = match violation {
                Some(table) => Some(table),
                None => dangling_parent(conn)?,
            };
            if let Some(table) = violation {
                return Err(DataError::ForeignKeys { table });
            }
        }
        Ok(applied)
    })();
    let result = result.and_then(|applied| conn.execute_batch("COMMIT").map(|()| applied).map_err(storage));
    if result.is_err() && !conn.is_autocommit() {
        // A refused migration, or a COMMIT that failed (BUSY leaves the transaction open): undo it all.
        let _ = conn.execute_batch("ROLLBACK");
    }
    result
}

fn open(path: &Path, list: &[&str]) -> Result<Connection, DataError> {
    let conn = Connection::open(path).map_err(storage)?;
    conn.execute_batch("PRAGMA foreign_keys = ON").map_err(storage)?;
    if migrate(&conn, list)? == 0 {
        return Ok(conn);
    }
    // A migration may have changed connection settings (recursive_triggers and the like): handlers get a fresh,
    // enforcing connection, never the one the migrations ran on.
    drop(conn);
    let fresh = Connection::open(path).map_err(storage)?;
    fresh.busy_timeout(BUSY).map_err(storage)?;
    fresh.execute_batch("PRAGMA foreign_keys = ON").map_err(storage)?;
    Ok(fresh)
}

/// The app's data. Shared across request threads (`Send + Sync`): it holds paths and the static migration lists,
/// and opens a connection per call.
#[derive(Clone, Debug)]
pub struct AppData {
    dir: PathBuf,
    app: String,
    migrations: Migrations,
}

impl AppData {
    /// The data under `dir` for `app`; creates `dir/accounts`, readable by this user only.
    pub fn new(dir: &Path, app: &str, migrations: Migrations) -> Result<AppData, DataError> {
        let accounts = dir.join("accounts");
        let mut builder = std::fs::DirBuilder::new();
        builder.recursive(true);
        #[cfg(unix)]
        std::os::unix::fs::DirBuilderExt::mode(&mut builder, 0o700);
        builder.create(&accounts).map_err(storage)?;
        Ok(AppData {
            dir: dir.to_path_buf(),
            app: app.to_string(),
            migrations,
        })
    }

    pub fn app(&self) -> &str {
        &self.app
    }

    /// Where `account`'s database lives.
    pub fn account_path(&self, account: &str) -> PathBuf {
        self.dir
            .join("accounts")
            .join(format!("{}.sqlite3", account_key(&self.app, account)))
    }

    /// The signed-in principal's own database, created and migrated on first use. Only this app's principal.
    pub fn account(&self, principal: &Principal) -> Result<Connection, DataError> {
        if principal.app != self.app {
            return Err(DataError::NotThisApp);
        }
        open(&self.account_path(&principal.account), self.migrations.account)
    }

    /// The shared database, when the app declares one.
    pub fn shared(&self) -> Result<Connection, DataError> {
        let list = self.migrations.shared.ok_or(DataError::NoShared)?;
        open(&self.dir.join("shared.sqlite3"), list)
    }

    /// Start-up: migrates the shared database and every existing account database, so the server is ready only
    /// once all of its data is at this build's schema. Returns how many account files were checked.
    pub fn migrate_all(&self) -> Result<usize, DataError> {
        if self.migrations.shared.is_some() {
            self.shared()?;
        }
        let mut paths = Vec::new();
        for entry in std::fs::read_dir(self.dir.join("accounts")).map_err(storage)? {
            let path = entry.map_err(storage)?.path();
            let name = path.file_name().and_then(|n| n.to_str()).unwrap_or("");
            let Some(stem) = name.strip_suffix(".sqlite3") else {
                continue; // SQLite's own -wal, -shm and -journal files, and anything else, are not databases here
            };
            if stem.len() == 64 && stem.bytes().all(|b| b.is_ascii_hexdigit()) {
                paths.push(path);
            }
        }
        paths.sort();
        for path in &paths {
            open(path, self.migrations.account)?;
        }
        Ok(paths.len())
    }
}
