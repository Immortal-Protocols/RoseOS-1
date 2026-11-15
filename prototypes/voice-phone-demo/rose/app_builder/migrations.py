"""A client-server app's SQL migrations, checked offline before a version is delivered (docs/APP-BUILDER.md §7.6).
Source only and called nowhere yet.

A generated server keeps each user's data in SQLite and changes its shape only through numbered migrations, one
plain SQL file each: server/migrations/account/NNNN_name.sql (every user's own database) and, for an app that
declares it, server/migrations/shared/NNNN_name.sql. Data must survive every update (§5 blocker 5), so:

- every migration of every delivered version stays exactly as delivered, and new ones are only appended
  (contiguous numbers from 0001): a phone's data was migrated by those exact files;
- the migrations run here, in an in-memory SQLite with nothing else attached: from empty, and as an upgrade from
  the delivered set, then an integrity and foreign key check. A migration that fails, or would fail on a real
  upgrade, is refused before anything is built.

Only the generated SQL is executed, never generated Rust. The sandbox refuses what could touch the builder's files or
the server's own settings: ATTACH (and VACUUM INTO, which attaches), extension loading, transaction control (the
runtime runs the pending migrations in one transaction) and any PRAGMA outside a short list. That is isolation, not a
rule on what an app may store.
"""
from __future__ import annotations

import hashlib
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from .appprofile import ProfileRefused

SCOPES = ('account', 'shared')
NAME = re.compile(r'([0-9]{4})_([a-z0-9_]{1,40})\.sql')
MAX_FILE = 256 * 1024
MAX_MIGRATIONS = 1000
MAX_STEPS = 50_000_000  # SQLite VM steps for one whole check: a runaway query is refused, never waited on
READ_PRAGMAS = {'data_version', 'page_size'}  # read (never set) by SQLite's own fts5 and rtree modules
TEMP_CREATES = (sqlite3.SQLITE_CREATE_TEMP_INDEX, sqlite3.SQLITE_CREATE_TEMP_TABLE, sqlite3.SQLITE_CREATE_TEMP_TRIGGER,
                sqlite3.SQLITE_CREATE_TEMP_VIEW)
PRAGMAS = {'foreign_keys', 'defer_foreign_keys', 'recursive_triggers', 'table_info', 'table_xinfo', 'index_list',
           'index_info', 'index_xinfo', 'foreign_key_list', 'foreign_key_check', 'integrity_check', 'quick_check'}


class MigrationRefused(ProfileRefused):
    pass


@dataclass(frozen=True)
class Migration:
    scope: str
    number: int
    name: str
    sha256: str
    sql: str

    @property
    def file(self) -> str:
        return f'server/migrations/{self.scope}/{self.number:04d}_{self.name}.sql'

    def lock(self) -> dict:
        """What the ledger records for a delivered version: enough to hold every later revision to these files."""
        return {'scope': self.scope, 'number': self.number, 'name': self.name, 'sha256': self.sha256}


def read(snapshot: Path) -> dict[str, list[Migration]]:
    """Each scope's migrations, in order. appprofile.check has already refused anything else in these folders."""
    out = {}
    for scope in SCOPES:
        folder = Path(snapshot) / 'server' / 'migrations' / scope
        found = []
        if folder.is_dir():
            for path in folder.iterdir():
                match = NAME.fullmatch(path.name)
                if not match or not path.is_file() or path.is_symlink():
                    raise MigrationRefused(f'{path.name} in migrations/{scope}/ is not a migration (NNNN_name.sql).',
                                           'migration_invalid')
                if path.stat().st_size > MAX_FILE:
                    raise MigrationRefused(f'migrations/{scope}/{path.name} is larger than {MAX_FILE // 1024} KB.',
                                           'migration_invalid')
                data = path.read_bytes()
                try:
                    sql = data.decode('utf-8')
                    if '\x00' in sql:
                        raise UnicodeDecodeError('utf-8', data, 0, 1, 'NUL')
                except UnicodeDecodeError:
                    raise MigrationRefused(f'migrations/{scope}/{path.name} is not UTF-8 text.',
                                           'migration_invalid') from None
                found.append(Migration(scope, int(match.group(1)), match.group(2), hashlib.sha256(data).hexdigest(),
                                       sql))
        found.sort(key=lambda m: m.number)
        if len(found) > MAX_MIGRATIONS:
            raise MigrationRefused(f'migrations/{scope}/ has more than {MAX_MIGRATIONS} migrations.',
                                   'migration_invalid')
        for expected, migration in enumerate(found, 1):
            if migration.number != expected:
                raise MigrationRefused(f'{migration.file}: migrations are numbered 0001, 0002, … with none missing '
                                       f'or repeated (expected {expected:04d}).', 'migration_gap')
        out[scope] = found
    return out


def check_lock(current: dict[str, list[Migration]], delivered: list[dict]) -> None:
    """Every delivered migration is still there, unchanged and under its own number; new ones only follow them."""
    by_key = {(m.scope, m.number): m for scope in current.values() for m in scope}
    for locked in sorted(delivered, key=lambda d: (d['scope'], d['number'])):
        mine = by_key.get((locked['scope'], locked['number']))
        where = f'server/migrations/{locked["scope"]}/{locked["number"]:04d}_{locked["name"]}.sql'
        if mine is None:
            raise MigrationRefused(f'{where} was delivered and has migrated users\' data, so it must stay.',
                                   'migration_removed')
        if (mine.name, mine.sha256) != (locked['name'], locked['sha256']):
            raise MigrationRefused(f'{where} was delivered and must stay exactly as it was; put a change in a new '
                                   'migration after the last one.', 'migration_changed')


def _authorizer(action, arg1, arg2, database, trigger):
    if action in (sqlite3.SQLITE_ATTACH, sqlite3.SQLITE_DETACH, sqlite3.SQLITE_TRANSACTION,
                  sqlite3.SQLITE_SAVEPOINT) + TEMP_CREATES:
        return sqlite3.SQLITE_DENY  # temp objects outlive a migration on this connection, never on a phone's
    if action == sqlite3.SQLITE_PRAGMA:
        name = (arg1 or '').lower()
        return sqlite3.SQLITE_OK if name in PRAGMAS or name in READ_PRAGMAS and arg2 is None else sqlite3.SQLITE_DENY
    if action == sqlite3.SQLITE_FUNCTION and (arg2 or '').lower() == 'load_extension':
        return sqlite3.SQLITE_DENY
    if action == sqlite3.SQLITE_ALTER_TABLE:  # (schema, table) here; the last argument is a column, not a database
        return sqlite3.SQLITE_OK if arg1 == 'main' else sqlite3.SQLITE_DENY
    if database == 'temp' and action in (sqlite3.SQLITE_READ, sqlite3.SQLITE_UPDATE) \
            and arg1 in ('sqlite_temp_master', 'sqlite_temp_schema'):
        return sqlite3.SQLITE_OK  # ALTER TABLE reads and rewrites SQLite's own temp catalog, always empty here
    if database not in (None, 'main'):
        return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK


# sqlite3_complete's own state machine (SQLite complete.c), so statements end exactly where SQLite says, in one pass.
_SEMI, _WS, _OTHER, _EXPLAIN, _CREATE, _TEMP, _TRIGGER, _END = range(8)
_TRANS = ((1, 0, 2, 3, 4, 2, 2, 2), (1, 1, 2, 3, 4, 2, 2, 2), (1, 2, 2, 2, 2, 2, 2, 2), (1, 3, 3, 2, 4, 2, 2, 2),
          (1, 4, 2, 2, 2, 4, 5, 2), (6, 5, 5, 5, 5, 5, 5, 5), (6, 6, 5, 5, 5, 5, 5, 7), (1, 7, 5, 5, 5, 5, 5, 5))
_KEYWORDS = {'create': _CREATE, 'trigger': _TRIGGER, 'temp': _TEMP, 'temporary': _TEMP, 'end': _END,
             'explain': _EXPLAIN}


def _statements(sql: str):
    """Split a migration into statements the way SQLite reads them (a ';' inside a string, comment or trigger body
    is not an end), in one linear pass. Raises SyntaxError for an unterminated literal or comment, or for text left
    over that is not a complete statement."""
    state, start, i, n = 0, 0, 0, len(sql)
    while i < n:
        c = sql[i]
        if c == ';':
            token, i = _SEMI, i + 1
        elif c in ' \t\n\r\f':
            token, i = _WS, i + 1
        elif sql.startswith('/*', i):
            end = sql.find('*/', i + 2)
            if end < 0:
                raise SyntaxError('a comment does not end')
            token, i = _WS, end + 2
        elif sql.startswith('--', i):
            end = sql.find('\n', i)
            token, i = _WS, n if end < 0 else end + 1
        elif c in '\'"`[':
            close = ']' if c == '[' else c
            end = sql.find(close, i + 1)
            if end < 0:
                raise SyntaxError('a quoted name or string does not end')
            token, i = _OTHER, end + 1
        elif c.isalnum() or c in '_$' or ord(c) >= 0x80:
            j = i + 1
            while j < n and (sql[j].isalnum() or sql[j] in '_$' or ord(sql[j]) >= 0x80):
                j += 1
            token, i = _KEYWORDS.get(sql[i:j].lower(), _OTHER), j
        else:
            token, i = _OTHER, i + 1
        state = _TRANS[state][token]
        if token == _SEMI and state == 1:
            statement = sql[start:i].strip()
            if statement.strip(';').strip():
                yield statement
            start = i
    if state not in (0, 1):
        raise SyntaxError('the last statement does not end with ;')


def _quote(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


class _Sandbox:
    def __init__(self):
        self.db = sqlite3.connect(':memory:', isolation_level=None)
        self.db.enable_load_extension(False)
        self.steps = 0

        def progress():
            self.steps += 1000
            return 1 if self.steps > MAX_STEPS else 0
        self.db.set_progress_handler(progress, 1000)
        self.db.execute('PRAGMA foreign_keys = ON')
        self.db.execute('PRAGMA temp_store = MEMORY')  # sorts and temporary results never spill to temporary files

    def apply(self, batch: list[Migration]):
        """Pending migrations as the runtime applies them (SQLite's schema-change procedure): foreign keys off, all
        of them in one transaction, then every reference checked before the commit. So a table rebuild keeps its
        children, ON DELETE actions do not fire, and a reference left broken refuses the whole batch."""
        parsed = []
        for migration in batch:
            try:
                parsed.append((migration, list(_statements(migration.sql))))
            except SyntaxError as error:
                raise MigrationRefused(f'{migration.file}: {error.msg}.', 'migration_sql') from None
        self.db.execute('PRAGMA foreign_keys = OFF')  # outside the transaction, where SQLite honours it
        try:
            self.db.execute('BEGIN IMMEDIATE')
            for migration, statements in parsed:
                self.db.set_authorizer(_authorizer)
                try:
                    for statement in statements:
                        self.db.execute(statement)
                except sqlite3.Error as error:
                    raise self._refused(migration.file, error) from None
                finally:
                    self.db.set_authorizer(None)
            try:
                broken = self.db.execute('PRAGMA foreign_key_check').fetchone()
            except sqlite3.Error as error:
                raise self._refused(f'Checking references after {batch[-1].file}', error) from None
            if broken is not None:
                raise MigrationRefused(f'After {batch[-1].file}, a row of table {str(broken[0])[:64]} references a '
                                       'row that is gone; the update would be refused at start-up.', 'migration_sql')
            self.db.execute('COMMIT')
        finally:
            if self.db.in_transaction:
                self.db.execute('ROLLBACK')
            self.db.execute('PRAGMA foreign_keys = ON')

    def _refused(self, what: str, error: sqlite3.Error) -> MigrationRefused:
        reason = 'it ran too long' if self.steps > MAX_STEPS else str(error).split('\n')[0][:200]
        return MigrationRefused(f'{what} failed: {reason}.', 'migration_sql')

    def verify(self, label: str):  # a second check: enforcement inside each migration normally refuses first
        try:
            problems = [r[0] for r in self.db.execute('PRAGMA integrity_check').fetchall() if r[0] != 'ok']
            problems += [f'{r[0]} row {r[1]} references {r[2]}' for r in self.db.execute('PRAGMA foreign_key_check')]
        except sqlite3.Error as error:
            raise self._refused(f'Checking the database after the {label}', error) from None
        if problems:
            raise MigrationRefused(f'After the {label}, the database is not consistent: {problems[0][:200]}.',
                                   'migration_sql')

    # Values by declared type affinity (SQLite's rules): a phone's rows have values, not only defaults and NULLs.
    _VALUES = (("INT", (1, 2)), ("CHAR", ("a", "b")), ("CLOB", ("a", "b")), ("TEXT", ("a", "b")),
               ("BLOB", (b"\x00", b"\x01")), ("REAL", (1.5, 2.5)), ("FLOA", (1.5, 2.5)), ("DOUB", (1.5, 2.5)))

    def _value(self, declared: str, variant: int):
        upper = (declared or '').upper()
        for marker, values in self._VALUES:
            if marker in upper:
                return values[variant]
        return (1, 2)[variant] if upper else ('a', 'b')[variant]  # NUMERIC, or no type at all

    def _references(self, table: str) -> dict:
        """Values for the table's foreign-key columns that point at a row its parent already holds (a self
        reference, or a parent with no row yet, gives none, and the column is left to SQLite)."""
        groups: dict = {}
        for row in self.db.execute(f'PRAGMA foreign_key_list({_quote(table)})'):
            groups.setdefault(row[0], (row[2], []))[1].append((row[3], row[4]))
        values = {}
        for parent, pairs in groups.values():
            if parent == table:
                continue
            targets = [to for _, to in pairs]
            if None in targets:  # the parent's primary key, in key order (or its rowid)
                key = sorted((c for c in self.db.execute(f'PRAGMA table_info({_quote(parent)})') if c[5]),
                             key=lambda c: c[5])
                targets = [c[1] for c in key] if key else ['rowid']
                if len(targets) != len(pairs):
                    continue
            try:
                row = self.db.execute(f'SELECT {", ".join(_quote(t) for t in targets)} FROM {_quote(parent)} '
                                      'LIMIT 1').fetchone()
            except sqlite3.Error:
                continue
            if row is not None:
                values.update({column: value for (column, _), value in zip(pairs, row)})
        return values

    def seed(self) -> int:
        """Rows like a phone's, so an upgrade runs over data (a unique index over duplicates, a NOT NULL rebuild,
        an update, a parent table rebuilt under its children) and not only over empty tables. Each table gets two
        rows of typed values: identical ones where its constraints allow, else two different ones, else default
        rows. Rowid keys are left to SQLite, and foreign keys point at a row the parent already holds: parents are
        filled first, a table at a time until no more can be. Best effort: a table whose constraints refuse every
        try stays empty. Returns the rows added."""
        added = 0
        self.db.set_authorizer(_authorizer)  # a migration's triggers fire here too, under the same rules
        try:
            pending = [r[0] for r in self.db.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND sql "
                                                     "NOT LIKE 'CREATE VIRTUAL%' AND name NOT LIKE 'sqlite\\_%' "
                                                     "ESCAPE '\\'")]
            parents = {t: {r[2] for r in self.db.execute(f'PRAGMA foreign_key_list({_quote(t)})')} - {t}
                       for t in pending}
            while pending:  # parents first, so a child's references point at rows; a cycle is taken as it comes
                ready = [t for t in pending if not parents[t] & set(pending)] or pending
                counts = [self._seed_table(table) for table in ready]
                added += sum(counts)
                done = set(ready)
                if not any(counts) and ready == pending:
                    break
                pending = [t for t in pending if t not in done]
        except sqlite3.Error as error:
            raise self._refused('Filling the delivered tables with sample rows', error) from None
        finally:
            self.db.set_authorizer(None)
        return added

    def _seed_table(self, table: str) -> int:
        """Up to two sample rows in one table, foreign keys pointing at the parents' rows; the rows added."""
        quoted = _quote(table)
        columns = self.db.execute(f'PRAGMA table_xinfo({quoted})').fetchall()
        keys = {r[3] for r in self.db.execute(f'PRAGMA foreign_key_list({quoted})')}
        refs = self._references(table)
        pk = [c for c in columns if c[5]]
        rowid = len(pk) == 1 and (pk[0][2] or '').upper() == 'INTEGER'
        cols = [c for c in columns if c[6] == 0 and (c[1] not in keys or c[1] in refs) and not (rowid and c[5])]
        names = ', '.join(_quote(c[1]) for c in cols)

        def insert(variant):
            if variant is None or not cols:
                self.db.execute(f'INSERT INTO {quoted} DEFAULT VALUES')
            else:
                values = [refs[c[1]] if c[1] in refs else self._value(c[2], variant) for c in cols]
                self.db.execute(f'INSERT INTO {quoted} ({names}) VALUES ({", ".join("?" * len(values))})', values)
        for rows in ((0, 0), (0, 1), None):
            inserted = 0
            try:
                for variant in (rows or (None, None)):
                    insert(variant)
                    inserted += 1
            except sqlite3.Error:
                if self.steps > MAX_STEPS:
                    raise MigrationRefused('A trigger in these migrations ran too long.', 'migration_sql') from None
            if inserted == 1 and cols:  # one row went in: try for a second, different one, then stop
                try:
                    insert(1)
                    inserted += 1
                except sqlite3.Error:
                    pass
            if inserted:
                return inserted
        return 0


def check(current: dict[str, list[Migration]], delivered: list[dict]) -> dict[str, int]:
    """Refuse the migrations unless they keep every delivered one and apply cleanly: from an empty database, and as
    an upgrade of a delivered database that holds rows. Returns each scope's schema version (its migration count)
    for the version's record."""
    check_lock(current, delivered)
    for scope, migrations in current.items():
        if not migrations:
            continue
        fresh = _Sandbox()
        fresh.apply(migrations)  # a new install: every migration pending at once
        fresh.verify(f'{scope} migrations from an empty database')
        upto = max((d['number'] for d in delivered if d['scope'] == scope), default=0)
        if 0 < upto < len(migrations):  # the delivered set, as phones have it, with data in it; then the new ones
            upgrade = _Sandbox()
            upgrade.apply(migrations[:upto])
            upgrade.seed()
            try:
                upgrade.apply(migrations[upto:])  # the delivered schema's phones, updated at once
                upgrade.verify(f'{scope} upgrade from schema {upto}')
            except MigrationRefused as refused:
                raise MigrationRefused(f'{refused} This happens when upgrading a database from schema {upto} that '
                                       'already has rows, as phones that installed it do.', refused.reason) from None
    return {scope: len(migrations) for scope, migrations in current.items()}
