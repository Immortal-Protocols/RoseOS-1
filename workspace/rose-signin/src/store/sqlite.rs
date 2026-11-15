//! The reference SQLite store for generated servers (feature "sqlite"). The server owns the connection and the
//! database file; `SqliteStore::new` applies rose-signin's migrations (tables prefixed `auth_`, version kept in
//! `auth_meta`) next to the app's own. Each operation is one `BEGIN IMMEDIATE` transaction.

use rusqlite::{Connection, OptionalExtension, params};

use super::{Account, Store, StoreError, StoreResult};
use crate::rate;
use crate::session::{Principal, Record};

/// The schema version this library writes. Migrations are applied in order and never edited once released.
pub const SCHEMA_VERSION: i64 = 2;

const MIGRATIONS: &[&str] = &[
    // 1: rose-signin-v1
    "CREATE TABLE auth_account (
       app TEXT NOT NULL, account TEXT NOT NULL,
       knowledge_hash TEXT NOT NULL,
       sealed_otp_secret BLOB NOT NULL,
       last_used_step INTEGER,
       confirmed INTEGER NOT NULL DEFAULT 0,
       pending_handle_hash BLOB,
       pending_since INTEGER NOT NULL,
       recovery_reissue INTEGER NOT NULL DEFAULT 0,
       PRIMARY KEY (app, account));
     CREATE TABLE auth_recovery (app TEXT NOT NULL, account TEXT NOT NULL, code_hash BLOB NOT NULL,
       PRIMARY KEY (app, account, code_hash));
     CREATE TABLE auth_rate (key TEXT PRIMARY KEY, failures INTEGER NOT NULL, last_failure INTEGER NOT NULL);
     CREATE TABLE auth_session (token_hash BLOB PRIMARY KEY, app TEXT NOT NULL, account TEXT NOT NULL,
       created INTEGER NOT NULL, last_seen INTEGER NOT NULL, revoked INTEGER NOT NULL DEFAULT 0);
     CREATE INDEX auth_session_principal ON auth_session (app, account);",
    // 2: single-use invites (D7)
    "CREATE TABLE auth_invite (app TEXT NOT NULL, account TEXT NOT NULL, token_hash BLOB NOT NULL,
       expires INTEGER NOT NULL, PRIMARY KEY (app, account, token_hash));
     ALTER TABLE auth_account ADD COLUMN staged_otp_secret BLOB;",
];

fn e(err: rusqlite::Error) -> StoreError {
    StoreError(format!("sqlite: {err}"))
}

pub struct SqliteStore {
    conn: Connection,
}

impl SqliteStore {
    /// Wraps a connection the server opened, applying any pending rose-signin migrations in one transaction.
    pub fn new(conn: Connection) -> StoreResult<Self> {
        conn.busy_timeout(std::time::Duration::from_secs(5))
            .map_err(e)?;
        conn.execute_batch(
            "CREATE TABLE IF NOT EXISTS auth_meta (key TEXT PRIMARY KEY, value INTEGER NOT NULL);",
        )
        .map_err(e)?;
        let mut store = SqliteStore { conn };
        store.migrate()?;
        Ok(store)
    }

    pub fn schema_version(&self) -> StoreResult<i64> {
        self.conn
            .query_row(
                "SELECT value FROM auth_meta WHERE key = 'schema_version'",
                [],
                |r| r.get(0),
            )
            .optional()
            .map_err(e)
            .map(|v| v.unwrap_or(0))
    }

    fn migrate(&mut self) -> StoreResult<()> {
        self.conn.execute_batch("BEGIN IMMEDIATE").map_err(e)?;
        let result = (|| {
            let current = self.schema_version()?;
            if current > MIGRATIONS.len() as i64 {
                return Err(StoreError(format!(
                    "database schema {current} is newer than this library"
                )));
            }
            for (i, sql) in MIGRATIONS.iter().enumerate().skip(current as usize) {
                self.conn.execute_batch(sql).map_err(e)?;
                self.conn
                    .execute(
                        "INSERT INTO auth_meta (key, value) VALUES ('schema_version', ?1)
                         ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                        params![i as i64 + 1],
                    )
                    .map_err(e)?;
            }
            Ok(())
        })();
        match result {
            Ok(()) => self.conn.execute_batch("COMMIT").map_err(e),
            Err(err) => {
                let _ = self.conn.execute_batch("ROLLBACK");
                Err(err)
            }
        }
    }

    pub fn connection(&self) -> &Connection {
        &self.conn
    }
}

fn hash32(v: Vec<u8>) -> rusqlite::Result<[u8; 32]> {
    v.try_into().map_err(|_| rusqlite::Error::InvalidQuery)
}

impl Store for SqliteStore {
    fn begin(&mut self) -> StoreResult<()> {
        self.conn.execute_batch("BEGIN IMMEDIATE").map_err(e)
    }
    fn commit(&mut self) -> StoreResult<()> {
        self.conn.execute_batch("COMMIT").map_err(e)
    }
    fn rollback(&mut self) -> StoreResult<()> {
        self.conn.execute_batch("ROLLBACK").map_err(e)
    }

    fn account(&self, app: &str, account: &str) -> StoreResult<Option<Account>> {
        let row = self
            .conn
            .query_row(
                "SELECT knowledge_hash, sealed_otp_secret, last_used_step, confirmed, pending_handle_hash,
                        pending_since, recovery_reissue, staged_otp_secret
                 FROM auth_account WHERE app = ?1 AND account = ?2",
                params![app, account],
                |r| {
                    let pending: Option<Vec<u8>> = r.get(4)?;
                    Ok(Account {
                        knowledge_hash: r.get(0)?,
                        sealed_otp_secret: r.get(1)?,
                        last_used_step: r.get::<_, Option<i64>>(2)?.map(|v| v as u64),
                        confirmed: r.get::<_, i64>(3)? != 0,
                        pending_handle_hash: pending.map(hash32).transpose()?,
                        pending_since: r.get::<_, i64>(5)? as u64,
                        recovery_reissue: r.get::<_, i64>(6)? != 0,
                        staged_otp_secret: r.get(7)?,
                        recovery_hashes: vec![],
                    })
                },
            )
            .optional()
            .map_err(e)?;
        let Some(mut a) = row else { return Ok(None) };
        let mut stmt = self
            .conn
            .prepare("SELECT code_hash FROM auth_recovery WHERE app = ?1 AND account = ?2")
            .map_err(e)?;
        a.recovery_hashes = stmt
            .query_map(params![app, account], |r| hash32(r.get(0)?))
            .map_err(e)?
            .collect::<rusqlite::Result<_>>()
            .map_err(e)?;
        Ok(Some(a))
    }

    fn put_account(&mut self, app: &str, account: &str, a: &Account) -> StoreResult<()> {
        self.conn
            .execute(
                "INSERT INTO auth_account (app, account, knowledge_hash, sealed_otp_secret, last_used_step, confirmed,
                   pending_handle_hash, pending_since, recovery_reissue, staged_otp_secret)
                 VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10)
                 ON CONFLICT(app, account) DO UPDATE SET knowledge_hash = excluded.knowledge_hash,
                   sealed_otp_secret = excluded.sealed_otp_secret, last_used_step = excluded.last_used_step,
                   confirmed = excluded.confirmed, pending_handle_hash = excluded.pending_handle_hash,
                   pending_since = excluded.pending_since, recovery_reissue = excluded.recovery_reissue,
                   staged_otp_secret = excluded.staged_otp_secret",
                params![
                    app,
                    account,
                    a.knowledge_hash,
                    a.sealed_otp_secret,
                    a.last_used_step.map(|v| v as i64),
                    a.confirmed as i64,
                    a.pending_handle_hash.map(|h| h.to_vec()),
                    a.pending_since as i64,
                    a.recovery_reissue as i64,
                    a.staged_otp_secret
                ],
            )
            .map_err(e)?;
        self.conn
            .execute(
                "DELETE FROM auth_recovery WHERE app = ?1 AND account = ?2",
                params![app, account],
            )
            .map_err(e)?;
        for h in &a.recovery_hashes {
            self.conn
                .execute(
                    "INSERT INTO auth_recovery (app, account, code_hash) VALUES (?1, ?2, ?3)",
                    params![app, account, h.to_vec()],
                )
                .map_err(e)?;
        }
        Ok(())
    }

    fn accounts(&self) -> StoreResult<Vec<(String, String)>> {
        let mut stmt = self
            .conn
            .prepare("SELECT app, account FROM auth_account")
            .map_err(e)?;
        stmt.query_map([], |r| Ok((r.get(0)?, r.get(1)?)))
            .map_err(e)?
            .collect::<rusqlite::Result<_>>()
            .map_err(e)
    }

    fn rate(&self, key: &str) -> StoreResult<rate::State> {
        self.conn
            .query_row(
                "SELECT failures, last_failure FROM auth_rate WHERE key = ?1",
                params![key],
                |r| {
                    Ok(rate::State {
                        failures: r.get::<_, i64>(0)? as u32,
                        last_failure: r.get::<_, i64>(1)? as u64,
                    })
                },
            )
            .optional()
            .map_err(e)
            .map(Option::unwrap_or_default)
    }

    fn put_rate(&mut self, key: &str, s: rate::State) -> StoreResult<()> {
        self.conn
            .execute(
                "INSERT INTO auth_rate (key, failures, last_failure) VALUES (?1, ?2, ?3)
                 ON CONFLICT(key) DO UPDATE SET failures = excluded.failures, last_failure = excluded.last_failure",
                params![key, s.failures as i64, s.last_failure as i64],
            )
            .map(|_| ())
            .map_err(e)
    }

    fn session(&self, key: &[u8; 32]) -> StoreResult<Option<Record>> {
        self.conn
            .query_row(
                "SELECT app, account, created, last_seen, revoked FROM auth_session WHERE token_hash = ?1",
                params![key.to_vec()],
                |r| {
                    Ok(Record {
                        principal: Principal { app: r.get(0)?, account: r.get(1)? },
                        created: r.get::<_, i64>(2)? as u64,
                        last_seen: r.get::<_, i64>(3)? as u64,
                        revoked: r.get::<_, i64>(4)? != 0,
                    })
                },
            )
            .optional()
            .map_err(e)
    }

    fn put_session(&mut self, key: [u8; 32], r: &Record) -> StoreResult<()> {
        self.conn
            .execute(
                "INSERT INTO auth_session (token_hash, app, account, created, last_seen, revoked)
                 VALUES (?1, ?2, ?3, ?4, ?5, ?6)
                 ON CONFLICT(token_hash) DO UPDATE SET last_seen = excluded.last_seen,
                   revoked = MAX(auth_session.revoked, excluded.revoked)",
                params![
                    key.to_vec(),
                    r.principal.app,
                    r.principal.account,
                    r.created as i64,
                    r.last_seen as i64,
                    r.revoked as i64
                ],
            )
            .map(|_| ())
            .map_err(e)
    }

    fn revoke_all(&mut self, p: &Principal) -> StoreResult<()> {
        self.conn
            .execute(
                "UPDATE auth_session SET revoked = 1 WHERE app = ?1 AND account = ?2",
                params![p.app, p.account],
            )
            .map(|_| ())
            .map_err(e)
    }

    fn revoke_every_session(&mut self) -> StoreResult<()> {
        self.conn
            .execute("UPDATE auth_session SET revoked = 1", [])
            .map(|_| ())
            .map_err(e)
    }

    fn put_invite(
        &mut self,
        app: &str,
        account: &str,
        token_hash: [u8; 32],
        expires: u64,
    ) -> StoreResult<()> {
        self.conn
            .execute(
                "INSERT INTO auth_invite (app, account, token_hash, expires) VALUES (?1, ?2, ?3, ?4)",
                params![app, account, token_hash.to_vec(), expires as i64],
            )
            .map(|_| ())
            .map_err(e)
    }

    fn take_invite(
        &mut self,
        app: &str,
        account: &str,
        token_hash: [u8; 32],
        now: u64,
    ) -> StoreResult<bool> {
        let expires: Option<i64> = self
            .conn
            .query_row(
                "DELETE FROM auth_invite WHERE app = ?1 AND account = ?2 AND token_hash = ?3 RETURNING expires",
                params![app, account, token_hash.to_vec()],
                |r| r.get(0),
            )
            .optional()
            .map_err(e)?;
        Ok(expires.is_some_and(|x| (now as i64) < x))
    }
}
