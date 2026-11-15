//! A generated handler's view of the adapter: it reaches SQLite only through the adapter's re-export, as the
//! builder's server crate (which declares no rusqlite of its own) does. This file names no `rusqlite` crate directly.

use std::sync::atomic::{AtomicUsize, Ordering};

use rose_app_runtime::signin::Principal;
use rose_app_runtime_signin::rusqlite::{self, Connection, OptionalExtension, params};
use rose_app_runtime_signin::{AppData, Migrations};

static NOTES: &[&str] = &["CREATE TABLE notes (id INTEGER PRIMARY KEY, body TEXT NOT NULL);"];

/// What a generated handler writes against `ctx.data`.
fn add_note(data: &AppData, principal: &Principal, body: &str) -> rusqlite::Result<i64> {
    let conn: Connection = data.account(principal).map_err(|_| rusqlite::Error::InvalidQuery)?;
    conn.execute("INSERT INTO notes (body) VALUES (?1)", params![body])?;
    Ok(conn.last_insert_rowid())
}

fn note(data: &AppData, principal: &Principal, id: i64) -> rusqlite::Result<Option<String>> {
    let conn = data.account(principal).map_err(|_| rusqlite::Error::InvalidQuery)?;
    conn.query_row("SELECT body FROM notes WHERE id = ?1", params![id], |r| r.get(0))
        .optional()
}

#[test]
fn handlers_use_sqlite_through_the_adapter() {
    static N: AtomicUsize = AtomicUsize::new(0);
    let dir = std::env::temp_dir().join(format!(
        "rose-app-runtime-signin-handler-{}-{}",
        std::process::id(),
        N.fetch_add(1, Ordering::SeqCst)
    ));
    std::fs::create_dir_all(&dir).unwrap();
    let data = AppData::new(
        &dir,
        "notes",
        Migrations {
            account: NOTES,
            shared: None,
        },
    )
    .unwrap();
    let ana = Principal {
        app: "notes".into(),
        account: "ana".into(),
    };
    let id = add_note(&data, &ana, "hello").unwrap();
    assert_eq!(note(&data, &ana, id).unwrap().as_deref(), Some("hello"));
    assert_eq!(note(&data, &ana, id + 1).unwrap(), None);
    let _ = std::fs::remove_dir_all(&dir);
}
