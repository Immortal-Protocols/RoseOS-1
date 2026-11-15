"""A client-server app's SQL migrations, checked offline before a version is delivered (docs/APP-BUILDER.md §7.4).
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
runtime gives each migration its own transaction) and any PRAGMA outside a short list. That is isolation, not a rule
on what an app may store.
"""
from __future__ import annotations

import hashlib
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from .appprofile import ProfileRefused

SCOPES = ('account', 'shared')
NAME = re.compile(r'(\d{4})_([a-z0-9_]{1,40})\.sql')
MAX_FILE = 256 * 1024
MAX_MIGRATIONS = 1000
MAX_STEPS = 50_000_000  # SQLite VM steps for one whole check: a runaway query is refused, never waited on
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
                except UnicodeDecodeError:
                    raise MigrationRefused(f'migrations/{scope}/{path.name} is not UTF-8.',
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
                  sqlite3.SQLITE_SAVEPOINT):
        return sqlite3.SQLITE_DENY
    if action == sqlite3.SQLITE_PRAGMA and (arg1 or '').lower() not in PRAGMAS:
        return sqlite3.SQLITE_DENY
    if action == sqlite3.SQLITE_FUNCTION and (arg2 or '').lower() == 'load_extension':
        return sqlite3.SQLITE_DENY
    if database not in (None, 'main', 'temp'):
        return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK


def _statements(sql: str):
    """Split a migration into statements the way sqlite3 reads them (a ';' inside a string or trigger body is not an
    end). Raises SyntaxError for text left over that is not a complete statement."""
    start = 0
    for i, c in enumerate(sql):
        if c == ';' and sqlite3.complete_statement(sql[start:i + 1]):
            statement = sql[start:i + 1].strip()
            if statement.strip(';').strip():
                yield statement
            start = i + 1
    rest = sql[start:]
    if re.sub(r'--[^\n]*|/\*.*?\*/', '', rest, flags=re.S).strip():
        raise SyntaxError('the last statement does not end with ;')


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

    def apply(self, migration: Migration):
        try:
            statements = list(_statements(migration.sql))
        except SyntaxError as error:
            raise MigrationRefused(f'{migration.file}: {error.msg}.', 'migration_sql') from None
        self.db.execute('BEGIN IMMEDIATE')  # as the runtime does: one transaction per migration
        self.db.set_authorizer(_authorizer)
        try:
            for statement in statements:
                self.db.execute(statement)
            self.db.set_authorizer(None)
            self.db.execute('COMMIT')  # deferred constraints are checked here, and can fail here
        except sqlite3.Error as error:
            self.db.set_authorizer(None)
            if self.db.in_transaction:
                self.db.execute('ROLLBACK')
            message = 'it ran too long' if self.steps > MAX_STEPS else str(error).split('\n')[0][:200]
            raise MigrationRefused(f'{migration.file} failed: {message}.', 'migration_sql') from None

    def verify(self, label: str):  # a second check: enforcement inside each migration normally refuses first
        problems = [r[0] for r in self.db.execute('PRAGMA integrity_check').fetchall() if r[0] != 'ok']
        problems += [f'{r[0]} row {r[1]} references {r[2]}' for r in self.db.execute('PRAGMA foreign_key_check')]
        if problems:
            raise MigrationRefused(f'After the {label}, the database is not consistent: {problems[0][:200]}.',
                                   'migration_sql')

    def seed(self) -> int:
        """Rows like a phone's: two of default values in every table that takes them, so an upgrade runs over data
        (a unique index over duplicates, an update, a rebuilt table) and not only over empty tables. Returns how
        many were added; a table whose constraints refuse default rows is left empty."""
        added = 0
        tables = [r[0] for r in self.db.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT "
                                                "LIKE 'sqlite\\_%' ESCAPE '\\'")]
        self.db.set_authorizer(_authorizer)  # a migration's triggers fire here too, under the same rules (a second
        # line: SQLite itself keeps ATTACH, PRAGMA and transactions out of triggers)
        try:
            for table in tables:
                for _ in range(2):
                    try:
                        self.db.execute(f'INSERT INTO "{table.replace(chr(34), chr(34) * 2)}" DEFAULT VALUES')
                        added += 1
                    except sqlite3.Error:
                        if self.steps > MAX_STEPS:
                            raise MigrationRefused('A trigger in these migrations ran too long.',
                                                   'migration_sql') from None
                        break
        finally:
            self.db.set_authorizer(None)
        return added


def check(current: dict[str, list[Migration]], delivered: list[dict]) -> dict[str, int]:
    """Refuse the migrations unless they keep every delivered one and apply cleanly: from an empty database, and as
    an upgrade of a delivered database that holds rows. Returns each scope's schema version (its migration count)
    for the version's record."""
    check_lock(current, delivered)
    for scope, migrations in current.items():
        if not migrations:
            continue
        fresh = _Sandbox()
        for migration in migrations:
            fresh.apply(migration)
        fresh.verify(f'{scope} migrations from an empty database')
        upto = max((d['number'] for d in delivered if d['scope'] == scope), default=0)
        if 0 < upto < len(migrations):  # the delivered set, as phones have it, with data in it; then the new ones
            upgrade = _Sandbox()
            for migration in migrations[:upto]:
                upgrade.apply(migration)
            upgrade.seed()
            try:
                for migration in migrations[upto:]:
                    upgrade.apply(migration)
                upgrade.verify(f'{scope} upgrade from schema {upto}')
            except MigrationRefused as refused:
                raise MigrationRefused(f'{refused} This happens when upgrading a database from schema {upto} that '
                                       'already has rows, as phones that installed it do.', refused.reason) from None
    return {scope: len(migrations) for scope, migrations in current.items()}
