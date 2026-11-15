"""A client-server app's SQL migrations (docs/APP-BUILDER.md §7.6): delivered ones stay exactly as delivered, new
ones only follow, and every set applies in an in-memory sandbox, fresh and as an upgrade over data, before
anything is built. Every migration is one the test writes; no file is ever created by running one."""
import os
import shutil
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
                    'SAVEPOINT s; CREATE TABLE a (x); RELEASE s;', 'DETACH temp;',
                    'PRAGMA page_size = 1024;', 'PRAGMA data_version = 5;'):  # read-only for fts5/rtree, never set
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

    def rebuild(self, children, copy=True):
        # SQLite's documented rebuild of a referenced table, as the runtime applies it (foreign keys off, then
        # every reference checked): it keeps the children only when the new table keeps their parents' rows.
        shutil.rmtree(self.root / 'server', ignore_errors=True)
        self.put('account', '0001_a.sql', 'CREATE TABLE lists (id INTEGER PRIMARY KEY, name TEXT NOT NULL);\n'
                                          f'CREATE TABLE items (id INTEGER PRIMARY KEY, list_id INTEGER NOT NULL '
                                          f'REFERENCES lists(id){children}, body TEXT);')
        delivered = self.locks()
        self.put('account', '0002_rebuild.sql',
                 'PRAGMA foreign_keys = OFF;\n'  # no effect inside the transaction, here as on a phone
                 'CREATE TABLE lists_new (id INTEGER PRIMARY KEY, name TEXT NOT NULL, colour TEXT);\n'
                 + ('INSERT INTO lists_new (id, name) SELECT id, name FROM lists;\n' if copy else '')
                 + 'DROP TABLE lists;\nALTER TABLE lists_new RENAME TO lists;\nPRAGMA foreign_keys = ON;')
        return delivered

    def test_a_referenced_table_rebuilt_with_its_rows_keeps_its_children(self):
        for children in ('', ' ON DELETE CASCADE'):
            with self.subTest(children=children):
                delivered = self.rebuild(children)
                self.assertEqual(M.check(M.read(self.root), delivered), {'account': 2, 'shared': 0})

    def test_a_rebuild_that_loses_the_childrens_parents_is_refused(self):
        # On a phone with an item, the runtime refuses this at start-up (with ON DELETE CASCADE it would have
        # deleted the items); here the seeded item, which points at a seeded list, shows it before anything ships.
        for children in ('', ' ON DELETE CASCADE'):
            with self.subTest(children=children):
                delivered = self.rebuild(children, copy=False)
                M.check(M.read(self.root), [])  # a new install has no item to lose
                refused = self.refused('migration_sql', delivered)
                self.assertIn('a row of table items references a row that is gone', str(refused))

    def test_seeded_children_point_at_seeded_parents(self):
        self.put('account', '0001_a.sql', 'CREATE TABLE items (id INTEGER PRIMARY KEY, list_id TEXT '
                                          'REFERENCES lists(code), body TEXT);\n'  # the child first, nullable
                                          'CREATE TABLE lists (code TEXT PRIMARY KEY, name TEXT);\n'
                                          'CREATE TABLE tags (item INTEGER NOT NULL REFERENCES items, '
                                          'list TEXT NOT NULL REFERENCES lists(code));')
        box = M._Sandbox()
        box.apply(M.read(self.root)['account'])
        box.seed()
        db = box.db
        self.assertEqual(db.execute('SELECT count(*) FROM items WHERE list_id IS NULL').fetchone()[0], 0)
        self.assertGreater(db.execute('SELECT count(*) FROM tags').fetchone()[0], 0)
        self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(), [])

    def test_a_trigger_fired_by_upgrade_rows_is_sandboxed_too(self):
        self.put('account', '0001_a.sql', 'CREATE TABLE a (x INTEGER DEFAULT 0);\n'
                                          'CREATE TABLE b (n INTEGER);\n'
                                          'CREATE TRIGGER t AFTER INSERT ON a BEGIN '
                                          'INSERT INTO b WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 '
                                          'FROM n) SELECT i FROM n; END;')
        delivered = self.locks()
        self.put('account', '0002_b.sql', 'CREATE INDEX b_n ON b(n);')
        self.assertIn('too long', str(self.refused('migration_sql', delivered)))

    def test_temp_objects_are_refused(self):
        # A temp trigger would outlive its migration on this connection (never on a phone) and could, for one,
        # drop the rows the upgrade run seeds.
        for sql in ('CREATE TABLE a (x INTEGER DEFAULT 0);\nCREATE TEMP TRIGGER g BEFORE INSERT ON a BEGIN SELECT '
                    'RAISE(IGNORE); END;', 'CREATE TEMP TABLE t (x);', 'CREATE TEMPORARY VIEW v AS SELECT 1;',
                    'CREATE TABLE a (x);\nCREATE TEMP INDEX i ON a(x);'):
            with self.subTest(sql=sql):
                self.put('account', '0001_a.sql', sql)
                self.refused('migration_sql')

    def test_running_out_of_steps_while_verifying_is_a_refusal(self):
        import unittest.mock
        self.put('account', '0001_a.sql', 'CREATE TABLE t (x TEXT UNIQUE);\nWITH RECURSIVE n(i) AS (SELECT 1 UNION '
                                          'ALL SELECT i + 1 FROM n WHERE i < 3000) INSERT INTO t SELECT hex(i) FROM n;')
        migration = M.read(self.root)['account'][0]
        box = M._Sandbox()
        box.apply([migration])
        box.steps = M.MAX_STEPS - 1000  # as if applying had used almost all of it
        with self.assertRaises(M.MigrationRefused) as caught:
            box.verify('account migrations from an empty database')
        self.assertIn('ran too long', str(caught.exception))

    def test_splitting_is_linear_and_matches_sqlite(self):
        import sqlite3
        import time
        sql = "SELECT '" + ';' * 250000 + "';"
        start = time.monotonic()
        self.assertEqual(list(M._statements(sql)), [sql])
        self.assertLess(time.monotonic() - start, 5)
        cases = {"CREATE TABLE a (x); INSERT INTO a VALUES ('; -- not a comment'); /* ; */ SELECT 1;": 3,
                 'CREATE TRIGGER t AFTER INSERT ON a BEGIN UPDATE a SET x = 1; DELETE FROM a; END; SELECT 2;': 2,
                 'SELECT "a;b", [c;d], `e;f`; -- trailing ;\n': 1}
        for case, count in cases.items():
            with self.subTest(case=case):
                parts = list(M._statements(case))
                self.assertEqual(len(parts), count, parts)
                self.assertTrue(all(sqlite3.complete_statement(p) for p in parts), parts)
        for bad in ("SELECT 'open;", 'SELECT 1 /* open;', 'CREATE TRIGGER t AFTER INSERT ON a BEGIN SELECT 1;'):
            with self.subTest(bad=bad):
                with self.assertRaises(SyntaxError):
                    list(M._statements(bad))

    def test_common_ddl_is_allowed(self):
        self.put('account', '0001_a.sql', """
            CREATE TABLE notes (id INTEGER PRIMARY KEY, body TEXT NOT NULL, extra TEXT) STRICT;
            ALTER TABLE notes DROP COLUMN extra;
            ALTER TABLE notes RENAME COLUMN body TO text;
            ALTER TABLE notes ADD COLUMN size INTEGER GENERATED ALWAYS AS (length(text)) VIRTUAL;
            CREATE TABLE kv (k TEXT PRIMARY KEY, v BLOB) WITHOUT ROWID;
            INSERT INTO kv VALUES ('a', x'00') ON CONFLICT(k) DO UPDATE SET v = excluded.v;
            CREATE INDEX IF NOT EXISTS notes_text ON notes(text);
            ALTER TABLE notes RENAME TO memos;
        """)
        M.check(M.read(self.root), [])

    def test_virtual_tables_from_sqlites_own_modules_are_allowed(self):
        import sqlite3
        for module, sql in (('fts5', 'CREATE VIRTUAL TABLE f USING fts5(body);'),
                            ('rtree', 'CREATE VIRTUAL TABLE r USING rtree(id, a, b);')):
            with self.subTest(module=module):
                try:
                    sqlite3.connect(':memory:').execute(sql)
                except sqlite3.OperationalError:
                    self.skipTest(f'this SQLite has no {module}')
                self.put('account', '0001_a.sql', sql)
                M.check(M.read(self.root), [])

    def test_upgrades_run_over_typed_rows(self):
        cases = {
            # Two users with the same email, as on a phone: the new unique index fails there.
            'unique over duplicates': ('CREATE TABLE users (id INTEGER PRIMARY KEY, email TEXT);',
                                       'CREATE UNIQUE INDEX users_email ON users(email);'),
            # A NOT NULL table, then a rebuild that loses its values.
            'not null rebuild': ('CREATE TABLE notes (id INTEGER PRIMARY KEY, body TEXT NOT NULL);',
                                 'CREATE TABLE n2 (id INTEGER PRIMARY KEY, body TEXT NOT NULL, n INTEGER NOT NULL);\n'
                                 'INSERT INTO n2 SELECT id, body, NULL FROM notes;\nDROP TABLE notes;\n'
                                 'ALTER TABLE n2 RENAME TO notes;'),
        }
        for name, (first, second) in cases.items():
            with self.subTest(name):
                self.put('account', '0001_a.sql', first)
                delivered = self.locks()
                self.put('account', '0002_b.sql', second)
                M.check(M.read(self.root), [])  # fine on a fresh install
                self.assertIn('already has rows', str(self.refused('migration_sql', delivered)))
                (self.root / 'server/migrations/account/0002_b.sql').unlink()
        # A table whose key forbids duplicates still gets two different rows.
        self.put('account', '0001_a.sql', 'CREATE TABLE k (code TEXT PRIMARY KEY, n INTEGER NOT NULL);')
        box = M._Sandbox()
        box.apply([M.read(self.root)["account"][0]])
        self.assertEqual(box.seed(), 2)
        self.assertEqual(box.db.execute('SELECT count(DISTINCT code) FROM k').fetchone()[0], 2)

    def test_temporary_data_stays_in_memory(self):
        box = M._Sandbox()
        self.assertEqual(box.db.execute('PRAGMA temp_store').fetchone()[0], 2)  # MEMORY: sorts never spill to files

    def test_a_nul_byte_is_refused(self):
        self.put('account', '0001_a.sql', 'CREATE TABLE a (x);\x00')
        with self.assertRaises(M.MigrationRefused) as caught:
            M.read(self.root)
        self.assertEqual(caught.exception.reason, 'migration_invalid')

    def test_shared_scope_is_checked_on_its_own(self):
        self.put('account', '0001_a.sql', 'CREATE TABLE a (x);')
        self.put('shared', '0001_s.sql', 'CREATE TABLE a (y);')  # the same name, another database
        self.assertEqual(M.check(M.read(self.root), []), {'account': 1, 'shared': 1})


if __name__ == '__main__':
    unittest.main()
