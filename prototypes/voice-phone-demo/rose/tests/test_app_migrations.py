"""A client-server app's SQL migrations (docs/APP-BUILDER.md §7.4): delivered ones stay exactly as delivered, new
ones only follow, and every set applies in an in-memory sandbox, fresh and as an upgrade over data, before
anything is built. Every migration is one the test writes; no file is ever created by running one."""
import os
import tempfile
import unittest
from pathlib import Path

from rose.app_builder import migrations as M


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.cwd = os.getcwd()
        os.chdir(self.root)  # anything a migration managed to write would land here
        self.addCleanup(os.chdir, self.cwd)

    def put(self, scope, name, sql):
        path = self.root / 'server' / 'migrations' / scope / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(sql, encoding='utf-8')
        return path

    def refused(self, reason, delivered=()):
        with self.assertRaises(M.MigrationRefused) as caught:
            M.check(M.read(self.root), list(delivered))
        self.assertEqual(caught.exception.reason, reason, str(caught.exception))
        return caught.exception

    def locks(self):
        return [m.lock() for scope in M.read(self.root).values() for m in scope]

    def no_files_written(self):
        written = [p for p in self.root.rglob('*') if p.is_file() and 'migrations' not in p.parts]
        self.assertEqual(written, [])


class Reading(Base):
    def test_an_app_without_migrations(self):
        self.assertEqual(M.check(M.read(self.root), []), {'account': 0, 'shared': 0})

    def test_numbers_are_contiguous_from_one(self):
        self.put('account', '0001_notes.sql', 'CREATE TABLE notes (id INTEGER PRIMARY KEY);')
        self.put('account', '0003_tags.sql', 'CREATE TABLE tags (id INTEGER PRIMARY KEY);')
        self.refused('migration_gap')
        self.put('account', '0002_x.sql', 'CREATE TABLE x (id INTEGER);')
        self.assertEqual(M.check(M.read(self.root), [])['account'], 3)

    def test_unreadable_files(self):
        self.put('account', '0001_a.sql', 'CREATE TABLE a (x);').write_bytes(b'\xff')
        with self.assertRaises(M.MigrationRefused) as caught:
            M.read(self.root)
        self.assertEqual(caught.exception.reason, 'migration_invalid')
        self.put('account', '0001_a.sql', '-- ' + 'x' * M.MAX_FILE)
        with self.assertRaises(M.MigrationRefused):
            M.read(self.root)


class Lock(Base):
    def setUp(self):
        super().setUp()
        self.put('account', '0001_notes.sql', 'CREATE TABLE notes (id INTEGER PRIMARY KEY, body TEXT NOT NULL);')
        self.put('account', '0002_pinned.sql', 'ALTER TABLE notes ADD COLUMN pinned INTEGER NOT NULL DEFAULT 0;')
        self.delivered = self.locks()

    def test_appending_passes(self):
        self.put('account', '0003_tags.sql', 'CREATE TABLE tags (note INTEGER REFERENCES notes(id), tag TEXT);')
        self.assertEqual(M.check(M.read(self.root), self.delivered), {'account': 3, 'shared': 0})

    def test_a_delivered_migration_cannot_change_move_or_go(self):
        path = self.root / 'server/migrations/account/0002_pinned.sql'
        path.write_text('ALTER TABLE notes ADD COLUMN pinned INTEGER NOT NULL DEFAULT 1;')
        self.refused('migration_changed', self.delivered)
        path.write_text('ALTER TABLE notes ADD COLUMN pinned INTEGER NOT NULL DEFAULT 0;')  # as delivered again
        M.check(M.read(self.root), self.delivered)
        path.rename(path.with_name('0002_flag.sql'))
        self.refused('migration_changed', self.delivered)  # the same bytes renamed under the same number
        path.with_name('0002_flag.sql').unlink()
        self.refused('migration_removed', self.delivered)


class Sandbox(Base):
    def test_bad_sql_is_refused_with_its_file(self):
        self.put('account', '0001_a.sql', 'CREATE TABLE a (x);\nINSERT INTO missing VALUES (1);')
        error = self.refused('migration_sql')
        self.assertIn('0001_a.sql', str(error))
        self.put('account', '0001_a.sql', 'CREATE TABLE a (x)')  # no closing ;
        self.refused('migration_sql')

    def test_nothing_reaches_files_or_settings(self):
        for sql in ("ATTACH 'stolen.db' AS s;", "VACUUM INTO 'copy.db';", "SELECT load_extension('evil');",
                    'PRAGMA writable_schema = 1;', 'PRAGMA user_version = 7;', 'PRAGMA journal_mode = WAL;',
                    'PRAGMA temp_store_directory = \'/tmp\';', 'BEGIN; CREATE TABLE a (x); COMMIT;',
                    'SAVEPOINT s; CREATE TABLE a (x); RELEASE s;', 'DETACH temp;'):
            with self.subTest(sql=sql):
                self.put('account', '0001_a.sql', sql)
                self.refused('migration_sql')
                self.no_files_written()

    def test_ordinary_sql_is_allowed(self):
        self.put('account', '0001_a.sql', """
            PRAGMA foreign_keys = ON;
            CREATE TABLE notes (id INTEGER PRIMARY KEY, body TEXT NOT NULL DEFAULT '', created INTEGER DEFAULT 0);
            CREATE INDEX notes_created ON notes(created);
            CREATE TRIGGER notes_touch AFTER INSERT ON notes BEGIN UPDATE notes SET created = 1 WHERE id = new.id; END;
            CREATE VIEW recent AS SELECT * FROM notes WHERE created > 0;
            INSERT INTO notes (body) VALUES ('a; b -- not a comment');
            WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n WHERE i < 100) SELECT count(*) FROM n;
        """)
        M.check(M.read(self.root), [])
        self.no_files_written()

    def test_a_deferred_violation_is_refused_at_commit(self):
        self.put('account', '0001_a.sql', 'PRAGMA defer_foreign_keys = ON;\nCREATE TABLE p (id INTEGER PRIMARY KEY);'
                                          '\nCREATE TABLE c (p INTEGER REFERENCES p(id));\nINSERT INTO c VALUES (5);')
        self.refused('migration_sql')

    def test_the_authorizer_refuses_extensions_even_if_loading_were_on(self):
        import sqlite3
        self.assertEqual(M._authorizer(sqlite3.SQLITE_FUNCTION, None, 'load_extension', None, None),
                         sqlite3.SQLITE_DENY)
        self.assertEqual(M._authorizer(sqlite3.SQLITE_FUNCTION, None, 'lower', None, None), sqlite3.SQLITE_OK)
        self.assertEqual(M._authorizer(sqlite3.SQLITE_READ, 't', 'c', 'other', None), sqlite3.SQLITE_DENY)

    def test_a_runaway_query_is_stopped(self):
        self.put('account', '0001_a.sql', 'WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n) '
                                          'SELECT count(*) FROM n;')
        self.assertIn('too long', str(self.refused('migration_sql')))

    def test_foreign_keys_and_integrity_are_checked(self):
        self.put('account', '0001_a.sql', 'PRAGMA foreign_keys = OFF;\nCREATE TABLE p (id INTEGER PRIMARY KEY);\n'
                                          'CREATE TABLE c (p INTEGER REFERENCES p(id));\nINSERT INTO c VALUES (5);')
        self.refused('migration_sql')  # enforced inside the migration's transaction, where foreign_keys = OFF is a no-op

    def test_an_upgrade_runs_over_existing_rows(self):
        self.put('account', '0001_a.sql', 'CREATE TABLE a (x INTEGER DEFAULT 0);')
        delivered = self.locks()
        # Fine on an empty database, but not on a phone's: two rows with the same x.
        self.put('account', '0002_unique.sql', 'CREATE UNIQUE INDEX a_x ON a(x);')
        M.check(M.read(self.root), [])  # fresh install: nothing to collide
        self.assertIn('upgrading a database from schema 1 that already has rows', str(self.refused('migration_sql',
                                                                                                 delivered)))

    def test_a_trigger_fired_by_upgrade_rows_is_sandboxed_too(self):
        self.put('account', '0001_a.sql', 'CREATE TABLE a (x INTEGER DEFAULT 0);\n'
                                          'CREATE TABLE b (n INTEGER);\n'
                                          'CREATE TRIGGER t AFTER INSERT ON a BEGIN '
                                          'INSERT INTO b WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 '
                                          'FROM n) SELECT i FROM n; END;')
        delivered = self.locks()
        self.put('account', '0002_b.sql', 'CREATE INDEX b_n ON b(n);')
        self.assertIn('too long', str(self.refused('migration_sql', delivered)))

    def test_shared_scope_is_checked_on_its_own(self):
        self.put('account', '0001_a.sql', 'CREATE TABLE a (x);')
        self.put('shared', '0001_s.sql', 'CREATE TABLE a (y);')  # the same name, another database
        self.assertEqual(M.check(M.read(self.root), []), {'account': 1, 'shared': 1})


if __name__ == '__main__':
    unittest.main()
