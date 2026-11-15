//! Migrations and foreign keys (SQLite's documented schema-change procedure): a parent table rebuilt under
//! existing children keeps them, whatever the children's `ON DELETE` action; a migration that leaves an orphan is
//! refused with every pending migration rolled back; the connection's enforcement is restored on every path.

use std::sync::atomic::{AtomicUsize, Ordering};

use rose_app_runtime::signin::Principal;
use rose_app_runtime_signin::data::migrate;
use rose_app_runtime_signin::rusqlite::Connection;
use rose_app_runtime_signin::{AppData, DataError, Migrations};

fn v1(on_delete: &str) -> &'static str {
    Box::leak(
        format!(
            "CREATE TABLE lists (id INTEGER PRIMARY KEY, name TEXT NOT NULL);
             CREATE TABLE items (id INTEGER PRIMARY KEY, list INTEGER NOT NULL REFERENCES lists(id) {on_delete}, body TEXT);"
        )
        .into_boxed_str(),
    )
}

/// SQLite's documented rebuild (new table, copy, drop the old one, rename), here to add a CHECK constraint. The
/// pragmas a writer might add do nothing inside the migration's transaction.
const REBUILD: &str = "PRAGMA foreign_keys = OFF;
    CREATE TABLE lists_new (id INTEGER PRIMARY KEY, name TEXT NOT NULL CHECK (length(name) > 0));
    INSERT INTO lists_new SELECT id, name FROM lists;
    DROP TABLE lists;
    ALTER TABLE lists_new RENAME TO lists;
    PRAGMA foreign_keys = ON;";

/// Deletes a parent and leaves its children: refused.
const ORPHAN: &str = "DELETE FROM lists WHERE id = 1;";

fn seeded(on_delete: &str) -> (Connection, &'static str) {
    let c = Connection::open_in_memory().unwrap();
    c.execute_batch("PRAGMA foreign_keys = ON").unwrap();
    let first = v1(on_delete);
    migrate(&c, &[first]).unwrap();
    c.execute_batch(
        "INSERT INTO lists VALUES (1, 'groceries'), (2, 'chores');
         INSERT INTO items VALUES (1, 1, 'milk'), (2, 1, 'eggs'), (3, 2, 'sweep');",
    )
    .unwrap();
    (c, first)
}

fn count(c: &Connection, table: &str) -> i64 {
    c.query_row(&format!("SELECT count(*) FROM {table}"), [], |r| r.get(0))
        .unwrap()
}

fn int(c: &Connection, pragma: &str) -> i64 {
    c.query_row(&format!("PRAGMA {pragma}"), [], |r| r.get(0)).unwrap()
}

#[test]
fn rebuilding_a_parent_keeps_its_children_whatever_their_action() {
    for action in ["ON DELETE CASCADE", "", "ON DELETE RESTRICT", "ON DELETE SET NULL"] {
        let (c, first) = seeded(action);
        migrate(&c, &[first, REBUILD]).unwrap_or_else(|e| panic!("{action}: {e}"));
        assert_eq!(count(&c, "items"), 3, "{action}: no child row deleted");
        assert_eq!(count(&c, "lists"), 2, "{action}");
        assert_eq!(
            c.query_row("SELECT count(*) FROM items WHERE list IS NULL", [], |r| r
                .get::<_, i64>(0))
                .unwrap(),
            0,
            "{action}: no child row nulled"
        );
        assert_eq!(int(&c, "user_version"), 2, "{action}");
        assert_eq!(int(&c, "foreign_keys"), 1, "{action}: enforcement restored");
        // The rebuilt parent is the one the children reference, and enforcement works on it.
        assert!(
            c.execute("INSERT INTO items VALUES (9, 99, 'nowhere')", []).is_err(),
            "{action}"
        );
        assert!(
            c.execute("INSERT INTO lists VALUES (3, '')", []).is_err(),
            "{action}: the new CHECK applies"
        );
    }
}

#[test]
fn an_orphan_refuses_the_database_and_rolls_back_every_pending_migration() {
    for action in ["ON DELETE CASCADE", ""] {
        let (c, first) = seeded(action);
        // Two pending migrations, the second leaving orphans: neither is kept.
        let got = migrate(&c, &[first, REBUILD, ORPHAN]);
        assert_eq!(got, Err(DataError::ForeignKeys { table: "items".into() }), "{action}");
        assert_eq!(int(&c, "user_version"), 1, "{action}: still at its old version");
        assert_eq!(
            (count(&c, "lists"), count(&c, "items")),
            (2, 3),
            "{action}: nothing lost or changed"
        );
        assert!(
            c.execute("INSERT INTO lists VALUES (3, '')", []).is_ok(),
            "{action}: the rebuild was rolled back"
        );
        assert_eq!(
            int(&c, "foreign_keys"),
            1,
            "{action}: enforcement restored after the refusal"
        );
        // Printing the refusal names the table, never a row.
        let shown = DataError::ForeignKeys { table: "items".into() }.to_string();
        assert!(shown.contains("items") && !shown.contains("milk"));
    }
}

#[test]
fn enforcement_is_restored_on_every_path() {
    // A failing migration.
    let (c, first) = seeded("");
    assert!(matches!(
        migrate(&c, &[first, "SELECT * FROM missing;"]),
        Err(DataError::Storage(_))
    ));
    assert_eq!(int(&c, "foreign_keys"), 1);
    // A newer schema.
    let (c, first) = seeded("");
    c.execute_batch("PRAGMA user_version = 7").unwrap();
    assert!(matches!(
        migrate(&c, &[first]),
        Err(DataError::NewerSchema { found: 7, known: 1 })
    ));
    assert_eq!(int(&c, "foreign_keys"), 1);
    // Nothing pending.
    let (c, first) = seeded("");
    migrate(&c, &[first]).unwrap();
    assert_eq!(int(&c, "foreign_keys"), 1);
    // A connection that did not enforce keeps not enforcing.
    let (c, first) = seeded("");
    c.execute_batch("PRAGMA foreign_keys = OFF").unwrap();
    migrate(&c, &[first, REBUILD]).unwrap();
    assert_eq!(int(&c, "foreign_keys"), 0);
}

#[test]
fn an_account_database_is_rebuilt_without_losing_rows() {
    static N: AtomicUsize = AtomicUsize::new(0);
    let dir = std::env::temp_dir().join(format!(
        "rose-app-runtime-signin-fk-{}-{}",
        std::process::id(),
        N.fetch_add(1, Ordering::SeqCst)
    ));
    std::fs::create_dir_all(&dir).unwrap();
    let ana = Principal {
        app: "notes".into(),
        account: "ana".into(),
    };
    let first = v1("ON DELETE CASCADE");
    let old = AppData::new(
        &dir,
        "notes",
        Migrations {
            account: Box::leak(vec![first].into_boxed_slice()),
            shared: None,
        },
    )
    .unwrap();
    old.account(&ana)
        .unwrap()
        .execute_batch("INSERT INTO lists VALUES (1, 'a'); INSERT INTO items VALUES (1, 1, 'x'), (2, 1, 'y');")
        .unwrap();
    let new = AppData::new(
        &dir,
        "notes",
        Migrations {
            account: Box::leak(vec![first, REBUILD].into_boxed_slice()),
            shared: None,
        },
    )
    .unwrap();
    assert_eq!(new.migrate_all(), Ok(1));
    let conn = new.account(&ana).unwrap();
    assert_eq!(count(&conn, "items"), 2);
    assert_eq!(int(&conn, "foreign_keys"), 1, "handlers get an enforcing connection");
    // ON DELETE CASCADE still acts outside migrations.
    conn.execute("DELETE FROM lists WHERE id = 1", []).unwrap();
    assert_eq!(count(&conn, "items"), 0);
    let _ = std::fs::remove_dir_all(&dir);
}
