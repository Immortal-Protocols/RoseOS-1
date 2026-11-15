"""Durable project, version and job records for the app builder.

One SQLite file (0600, in a 0700 directory outside the repository) holds every identity a generated app must keep:
its package name and signer never change, and its versionCode only grows, allocated here inside a transaction, never
by generated code. A job left running by a restart becomes failed ("interrupted; outcome unknown", retryable) rather
than being run again, because a half-finished build may already have produced side effects.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import sqlite3
import threading
import time
from pathlib import Path

from .apilevel import wire_summary

STATES = ('queued', 'preparing', 'building', 'testing', 'signing', 'ready', 'failed', 'cancelled')
ACTIVE = ('queued', 'preparing', 'building', 'testing', 'signing')
CATEGORIES = ('toolchain', 'dependency', 'compile', 'test', 'signing', 'timeout', 'cancelled', 'internal')
PACKAGE_PREFIX = 'app.rose.gen.p'
LOG_TAIL = 4000

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
  project_id TEXT PRIMARY KEY, name TEXT NOT NULL, package_name TEXT NOT NULL UNIQUE, signer_sha256 TEXT,
  description TEXT NOT NULL, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS versions (
  project_id TEXT NOT NULL REFERENCES projects(project_id), version_code INTEGER NOT NULL,
  version_name TEXT NOT NULL, job_id TEXT NOT NULL UNIQUE, source_revision TEXT NOT NULL, sha256 TEXT NOT NULL,
  size INTEGER NOT NULL, notes TEXT NOT NULL, created REAL NOT NULL, PRIMARY KEY (project_id, version_code));
CREATE TABLE IF NOT EXISTS jobs (
  job_id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(project_id), kind TEXT NOT NULL,
  request TEXT NOT NULL, client_key TEXT NOT NULL UNIQUE, attempt INTEGER NOT NULL, state TEXT NOT NULL,
  stage TEXT NOT NULL, message TEXT NOT NULL, log_tail TEXT NOT NULL, started REAL NOT NULL, updated REAL NOT NULL,
  failure TEXT, cancel_requested INTEGER NOT NULL DEFAULT 0, version_code INTEGER);
CREATE TABLE IF NOT EXISTS key_backups (
  project_id TEXT PRIMARY KEY REFERENCES projects(project_id), receipt TEXT NOT NULL, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS version_dependencies (
  project_id TEXT NOT NULL, version_code INTEGER NOT NULL, coordinate TEXT NOT NULL, sha256 TEXT NOT NULL,
  size INTEGER NOT NULL, PRIMARY KEY (project_id, version_code, coordinate));
CREATE TABLE IF NOT EXISTS version_api_reports (
  project_id TEXT NOT NULL, version_code INTEGER NOT NULL, report TEXT NOT NULL,
  PRIMARY KEY (project_id, version_code));
-- Level 2 (docs/APP-BUILDER.md §7): a project's profile is bound by its first client-server delivery and never
-- dropped; each such version pairs its APK with one server ELF and records its migration lock.
CREATE TABLE IF NOT EXISTS project_profiles (
  project_id TEXT PRIMARY KEY REFERENCES projects(project_id), profile TEXT NOT NULL, version_code INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS version_servers (
  project_id TEXT NOT NULL, version_code INTEGER NOT NULL, sha256 TEXT NOT NULL, size INTEGER NOT NULL,
  target TEXT NOT NULL, PRIMARY KEY (project_id, version_code));
CREATE TABLE IF NOT EXISTS version_migrations (
  project_id TEXT NOT NULL, version_code INTEGER NOT NULL, scope TEXT NOT NULL, number INTEGER NOT NULL,
  name TEXT NOT NULL, sha256 TEXT NOT NULL, PRIMARY KEY (project_id, version_code, scope, number));
-- The crates each client-server version's server was built with, and its Cargo.lock (rose.app_builder.server_build
-- starts the next build from it, so a rebuild keeps the versions this one used).
CREATE TABLE IF NOT EXISTS version_crates (
  project_id TEXT NOT NULL, version_code INTEGER NOT NULL, name TEXT NOT NULL, version TEXT NOT NULL,
  sha256 TEXT NOT NULL, PRIMARY KEY (project_id, version_code, name, version));
CREATE TABLE IF NOT EXISTS version_cargo_locks (
  project_id TEXT NOT NULL, version_code INTEGER NOT NULL, lock TEXT NOT NULL, PRIMARY KEY (project_id, version_code));
-- The API contract each client-server version was built from (canonical JSON): a revision is checked against all of
-- them (rose.app_builder.contract.check_update).
CREATE TABLE IF NOT EXISTS version_contracts (
  project_id TEXT NOT NULL, version_code INTEGER NOT NULL, contract TEXT NOT NULL, PRIMARY KEY (project_id, version_code));
-- Level 3 (§3.3 share): the hosted app id for a project, recorded only from owner tooling or the publisher.
CREATE TABLE IF NOT EXISTS project_hosting (
  project_id TEXT PRIMARY KEY REFERENCES projects(project_id), hosted_app_id TEXT NOT NULL UNIQUE,
  recorded REAL NOT NULL);
CREATE TABLE IF NOT EXISTS installs (
  project_id TEXT NOT NULL, package_name TEXT NOT NULL, version_code INTEGER NOT NULL, signer_sha256 TEXT,
  status TEXT NOT NULL, detail TEXT NOT NULL, device TEXT NOT NULL, created REAL NOT NULL);
"""


def _text(value: str) -> str:
    """Text sqlite can store: anything undecodable (a generated file name, tool output) is escaped, not dropped."""
    return value.encode('utf-8', 'backslashreplace').decode('utf-8')


class LedgerError(ValueError):
    """A request the ledger refuses; nothing changed."""


CLIENT_SERVER = 'client-server-v1'
HEX64 = re.compile(r'[0-9a-f]{64}')
UUID = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}')
SCOPES = ('account', 'shared')
MIGRATION_NAME = re.compile(r'[a-z0-9_]{1,40}')
CRATE_NAME = re.compile(r'[A-Za-z0-9_-]{1,64}')
CRATE_VERSION = re.compile(r'[0-9A-Za-z.+-]{1,64}')
MAX_CARGO_LOCK = 1024 * 1024
MAX_CONTRACT = 512 * 1024


def _server(server) -> tuple:
    if not isinstance(server, dict) or set(server) != {'sha256', 'size', 'target'} \
            or not isinstance(server['sha256'], str) or not HEX64.fullmatch(server['sha256']) \
            or type(server['size']) is not int or server['size'] < 1 \
            or not isinstance(server['target'], str) or not 1 <= len(server['target']) <= 64:
        raise LedgerError('a client-server version needs its server as {sha256, size, target}')
    return server['sha256'], server['size'], server['target']


def _migration(lock) -> tuple:
    if not isinstance(lock, dict) or set(lock) != {'scope', 'number', 'name', 'sha256'} \
            or lock['scope'] not in SCOPES or type(lock['number']) is not int or not 1 <= lock['number'] <= 9999 \
            or not isinstance(lock['name'], str) or not MIGRATION_NAME.fullmatch(lock['name']) \
            or not isinstance(lock['sha256'], str) or not HEX64.fullmatch(lock['sha256']):
        raise LedgerError('a migration lock entry is {scope, number, name, sha256}')
    return lock['scope'], lock['number'], lock['name'], lock['sha256']


def _crate(crate) -> tuple:
    name, version, digest = (getattr(crate, a, None) for a in ('name', 'version', 'sha256'))
    if not isinstance(name, str) or not CRATE_NAME.fullmatch(name) or not isinstance(version, str) \
            or not CRATE_VERSION.fullmatch(version) or not isinstance(digest, str) or not HEX64.fullmatch(digest):
        raise LedgerError('a crate entry is {name, version, sha256}')
    return name, version, digest


def _id() -> str:
    return secrets.token_hex(8)


class Ledger:
    def __init__(self, root: Path, clock=time.time):
        self.root = Path(root)
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.root, 0o700)
        self.path = self.root / 'builder.sqlite3'
        fresh = not self.path.exists()
        self.db = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        if fresh:
            os.chmod(self.path, 0o600)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.executescript(SCHEMA)
        self.lock = threading.RLock()
        self.clock = clock

    def close(self):
        self.db.close()

    # Projects and jobs.

    def _existing(self, client_key):
        row = self.db.execute('SELECT job_id, project_id, request FROM jobs WHERE client_key=?',
                              (client_key,)).fetchone()
        return None if row is None else {'jobId': row[0], 'projectId': row[1], 'request': row[2]}

    def create(self, device: str, client_request_id: str, description: str, name: str) -> dict:
        """A new project and its first job; retrying the same request returns the same ids."""
        key = f'{device}\n{client_request_id}'
        with self.lock:
            existing = self._existing(key)
            if existing:
                if self.job(existing['jobId'])['kind'] != 'create' or existing['request'] != description:
                    raise LedgerError('clientRequestId was used for another request')
                return {'projectId': existing['projectId'], 'jobId': existing['jobId']}
            project, job, now = _id(), _id(), self.clock()
            self.db.execute('BEGIN IMMEDIATE')
            try:
                self.db.execute('INSERT INTO projects VALUES (?,?,?,?,?,?)',
                                (project, name, PACKAGE_PREFIX + project, None, description, now))
                self._insert_job(job, project, 'create', description, key, now)
                self.db.execute('COMMIT')
            except Exception:
                self.db.execute('ROLLBACK')
                raise
            return {'projectId': project, 'jobId': job}

    def revise(self, device: str, project: str, client_request_id: str, request: str) -> dict:
        key = f'{device}\n{client_request_id}'
        with self.lock:
            existing = self._existing(key)
            if existing:
                if (existing['projectId'] != project or self.job(existing['jobId'])['kind'] != 'revise'
                        or existing['request'] != request):
                    raise LedgerError('clientRequestId was used for another request')
                return {'jobId': existing['jobId']}
            if self.project(project) is None:
                raise LedgerError('unknown project')
            if self._active(project):
                raise LedgerError('this project already has a build in progress')
            # A project whose first build failed can still be revised: it starts again from the skeleton.
            job = _id()
            self.db.execute('BEGIN IMMEDIATE')
            try:
                self._insert_job(job, project, 'revise', request, key, self.clock())
                self.db.execute('COMMIT')
            except Exception:
                self.db.execute('ROLLBACK')
                raise
            return {'jobId': job}

    def retry(self, job_id: str) -> dict:
        """Run a failed, retryable job again as the next attempt of the same job."""
        with self.lock:
            job = self.job(job_id)
            if job is None:
                raise LedgerError('unknown job')
            if job['state'] != 'failed' or not (job.get('failure') or {}).get('retryable'):
                raise LedgerError('only a failed, retryable job can be retried')
            if self._active(job['projectId']):
                raise LedgerError('this project already has a build in progress')
            if self.db.execute('SELECT 1 FROM versions WHERE project_id=? AND created > ?',
                               (job['projectId'], job['startedAt'])).fetchone():
                # Another job delivered a version since: re-running this one would build over that version.
                raise LedgerError('a later version superseded this build; ask for the change again')
            now = self.clock()  # queued again now, so it runs in submission order and is the project's activeJob
            self.db.execute("UPDATE jobs SET attempt=attempt+1, state='queued', stage='queued', message='Queued.', "
                            "failure=NULL, cancel_requested=0, version_code=NULL, started=?, updated=? "
                            "WHERE job_id=?", (now, now, job_id))
            return self.job(job_id)

    def _active(self, project: str) -> bool:
        return self.db.execute('SELECT 1 FROM jobs WHERE project_id=? AND state IN (%s)' % ','.join('?' * len(ACTIVE)),
                               (project, *ACTIVE)).fetchone() is not None

    def _insert_job(self, job, project, kind, request, key, now):
        self.db.execute('INSERT INTO jobs (job_id, project_id, kind, request, client_key, attempt, state, stage, '
                        'message, log_tail, started, updated) VALUES (?,?,?,?,?,1,?,?,?,?,?,?)',
                        (job, project, kind, request, key, 'queued', 'queued', 'Queued.', '', now, now))

    def next_queued(self):
        with self.lock:
            return self._next_queued()

    def _next_queued(self):
        row = self.db.execute("SELECT job_id FROM jobs WHERE state='queued' ORDER BY started LIMIT 1").fetchone()
        return None if row is None else self.job(row[0])

    def advance(self, job_id: str, state: str, stage: str, message: str, log: str = '') -> bool:
        """Move a job forward; False (and no change) when it was cancelled or already finished."""
        if state not in ACTIVE:
            raise ValueError(state)
        with self.lock:
            cur = self.db.execute("UPDATE jobs SET state=?, stage=?, message=?, log_tail=substr(log_tail || ?, -?), "
                                  "updated=? WHERE job_id=? AND cancel_requested=0 AND state IN (%s)"
                                  % ','.join('?' * len(ACTIVE)),
                                  (state, stage, _text(message), _text(log), LOG_TAIL, self.clock(), job_id,
                                   *ACTIVE))
            return cur.rowcount == 1

    def finish_failed(self, job_id: str, category: str, summary: str, retryable: bool, log: str = ''):
        if category not in CATEGORIES:
            raise ValueError(category)
        summary, log = _text(summary), _text(log)
        failure = json.dumps({'category': category, 'summary': summary[:500], 'retryable': bool(retryable)})
        state = 'cancelled' if category == 'cancelled' else 'failed'
        with self.lock:
            self.db.execute("UPDATE jobs SET state=?, stage=?, message=?, failure=?, "
                            "log_tail=substr(log_tail || ?, -?), updated=? WHERE job_id=? AND state IN (%s)"
                            % ','.join('?' * len(ACTIVE)),
                            (state, state, summary[:500], failure, log, LOG_TAIL, self.clock(), job_id, *ACTIVE))

    def cancel(self, job_id: str) -> str:
        """accepted (stopping, not stopped), not_running or unknown_job."""
        with self.lock:
            job = self.job(job_id)
            if job is None:
                return 'unknown_job'
            if job['state'] not in ACTIVE:
                return 'not_running'
            if job['state'] == 'queued':
                self.db.execute("UPDATE jobs SET cancel_requested=1 WHERE job_id=?", (job_id,))
                self.finish_failed(job_id, 'cancelled', 'Cancelled before it started.', False)
            else:
                self.db.execute("UPDATE jobs SET cancel_requested=1, updated=? WHERE job_id=?",
                                (self.clock(), job_id))
            return 'accepted'

    def cancel_requested(self, job_id: str) -> bool:
        row = self.db.execute('SELECT cancel_requested FROM jobs WHERE job_id=?', (job_id,)).fetchone()
        return bool(row and row[0])

    def recover(self) -> int:
        """After a restart: every job that was running is failed, outcome unknown, retryable. Queued jobs stay."""
        with self.lock:
            rows = self.db.execute("SELECT job_id, cancel_requested FROM jobs WHERE state IN (%s) AND state!='queued'"
                                   % ','.join('?' * len(ACTIVE)), ACTIVE).fetchall()
            for job_id, cancelled in rows:
                if cancelled:
                    self.finish_failed(job_id, 'cancelled', 'Cancelled; the server restarted while it stopped.', False)
                else:
                    self.finish_failed(job_id, 'internal', 'Interrupted by a server restart; outcome unknown.', True)
            return len(rows)

    # Identity.

    def allocate_version(self, job_id: str) -> int:
        """The job's versionCode: one more than the project's newest delivered version, fixed for this attempt."""
        with self.lock:
            job = self.job(job_id)
            if job.get('versionCode'):
                return job['versionCode']
            newest = self.db.execute('SELECT MAX(version_code) FROM versions WHERE project_id=?',
                                     (job['projectId'],)).fetchone()[0] or 0
            code = newest + 1
            self.db.execute('UPDATE jobs SET version_code=? WHERE job_id=?', (code, job_id))
            return code

    def key_backup(self, project: str) -> str | None:
        """The receipt of the project's signing-key backup, or None if it was never backed up."""
        with self.lock:
            row = self.db.execute('SELECT receipt FROM key_backups WHERE project_id=?', (project,)).fetchone()
        return None if row is None else row[0]

    def record_key_backup(self, project: str, receipt: str) -> None:
        with self.lock:
            self.db.execute('INSERT OR REPLACE INTO key_backups VALUES (?,?,?)', (project, receipt, self.clock()))

    def bind_signer(self, project: str, signer_sha256: str):
        """The project's signer is set once, by its first signed build, and never changes."""
        with self.lock:
            current = self.project(project)['signerSha256']
            if current is None:
                self.db.execute('UPDATE projects SET signer_sha256=? WHERE project_id=? AND signer_sha256 IS NULL',
                                (signer_sha256, project))
            elif current != signer_sha256:
                raise LedgerError('the signer changed')

    def deliver(self, job_id: str, version_name: str, source_revision: str, sha256: str, size: int, notes: str,
                dependencies=(), api_report: dict | None = None, profile: str | None = None, server=None,
                migrations=(), crates=(), cargo_lock: str | None = None, contract: str | None = None):
        """Record the delivered version and, in the same transaction, the libraries it was built with (its lock).
        A client-server version (profile CLIENT_SERVER) also records its server ELF, its migration lock, the crates
        its server was built with and its Cargo.lock, and binds the project's profile; once bound, a version without
        its server is refused. Nothing is half recorded."""
        if profile not in (None, CLIENT_SERVER):
            raise LedgerError('unknown profile')
        if profile == CLIENT_SERVER:
            server_row = _server(server)
            migration_rows = [_migration(m) for m in migrations]
            if len({(r[0], r[1]) for r in migration_rows}) != len(migration_rows):
                raise LedgerError('a migration is listed twice')
            crate_rows = [_crate(c) for c in crates]
            if len({(r[0], r[1]) for r in crate_rows}) != len(crate_rows):
                raise LedgerError('a crate is listed twice')
            if not isinstance(cargo_lock, str) or not cargo_lock or len(cargo_lock.encode('utf-8', 'replace')) \
                    > MAX_CARGO_LOCK:
                raise LedgerError('a client-server version needs its Cargo.lock')
            if contract is not None and (not isinstance(contract, str) or not contract
                                         or len(contract.encode('utf-8', 'replace')) > MAX_CONTRACT):
                raise LedgerError('a contract is its canonical JSON text')
        elif server is not None or migrations or crates or cargo_lock is not None or contract is not None:
            raise LedgerError('only a client-server version has a server, migrations, crates and a contract')
        with self.lock:
            job = self.job(job_id)
            if job['state'] not in ACTIVE or self.cancel_requested(job_id):
                return False
            if profile is None and self._profile(job['projectId']) == CLIENT_SERVER:
                raise LedgerError('this project has a server; a version without it is refused')
            if profile == CLIENT_SERVER:  # the lock, kept by the ledger itself and not only by its callers
                locked = {(r[0], r[1]): (r[2], r[3]) for r in self.db.execute(
                    'SELECT DISTINCT scope, number, name, sha256 FROM version_migrations WHERE project_id=?',
                    (job['projectId'],))}
                mine = {(r[0], r[1]): (r[2], r[3]) for r in migration_rows}
                for key, delivered in locked.items():
                    if mine.get(key) != delivered:
                        raise LedgerError(f'migration {key[0]}/{key[1]:04d} was delivered and must stay exactly as '
                                          'it was')
                crate_lock = self._crate_lock(job['projectId'])
                for name, version, digest in crate_rows:  # a crate version never changes under an app
                    if crate_lock.get(f'{name}@{version}', digest) != digest:
                        raise LedgerError(f'the crate {name}@{version} was delivered with other contents')
            newest = self.db.execute('SELECT MAX(version_code) FROM versions WHERE project_id=?',
                                     (job['projectId'],)).fetchone()[0] or 0
            if job['versionCode'] is None or job['versionCode'] <= newest:
                raise LedgerError('versionCode must exceed the newest delivered version')
            self.db.execute('BEGIN IMMEDIATE')
            try:
                self.db.execute('INSERT INTO versions VALUES (?,?,?,?,?,?,?,?,?)',
                                (job['projectId'], job['versionCode'], version_name, job_id, source_revision, sha256,
                                 size, notes, self.clock()))
                for lib in dependencies:
                    self.db.execute('INSERT INTO version_dependencies VALUES (?,?,?,?,?)',
                                    (job['projectId'], job['versionCode'], lib.coordinate, lib.sha256, lib.size))
                if api_report is not None:  # advisory, kept with the version it describes
                    self.db.execute('INSERT INTO version_api_reports VALUES (?,?,?)',
                                    (job['projectId'], job['versionCode'], json.dumps(api_report, sort_keys=True)))
                if profile == CLIENT_SERVER:
                    self.db.execute('INSERT OR IGNORE INTO project_profiles VALUES (?,?,?)',
                                    (job['projectId'], CLIENT_SERVER, job['versionCode']))
                    self.db.execute('INSERT INTO version_servers VALUES (?,?,?,?,?)',
                                    (job['projectId'], job['versionCode'], *server_row))
                    for row in migration_rows:
                        self.db.execute('INSERT INTO version_migrations VALUES (?,?,?,?,?,?)',
                                        (job['projectId'], job['versionCode'], *row))
                    for row in crate_rows:
                        self.db.execute('INSERT INTO version_crates VALUES (?,?,?,?,?)',
                                        (job['projectId'], job['versionCode'], *row))
                    self.db.execute('INSERT INTO version_cargo_locks VALUES (?,?,?)',
                                    (job['projectId'], job['versionCode'], _text(cargo_lock)))
                    if contract is not None:
                        self.db.execute('INSERT INTO version_contracts VALUES (?,?,?)',
                                        (job['projectId'], job['versionCode'], contract))
                self.db.execute("UPDATE jobs SET state='ready', stage='ready', message='Ready to install.', "
                                "updated=? WHERE job_id=?", (self.clock(), job_id))
                self.db.execute('COMMIT')
            except Exception:
                self.db.execute('ROLLBACK')
                raise
            return True

    def record_install(self, device: str, receipt: dict):
        with self.lock:
            self.db.execute('INSERT INTO installs VALUES (?,?,?,?,?,?,?,?)',
                            (receipt['projectId'], receipt['packageName'], receipt['versionCode'],
                             receipt.get('signerSha256'), receipt['status'], receipt.get('detail', '')[:500],
                             device, self.clock()))

    # Reads.

    # Reads take the same lock as writes: the one shared connection never shows another thread's open transaction.

    def project(self, project: str):
        with self.lock:
            return self._project(project)

    def _project(self, project: str):
        row = self.db.execute('SELECT project_id, name, package_name, signer_sha256, description FROM projects '
                              'WHERE project_id=?', (project,)).fetchone()
        if row is None:
            return None
        return {'projectId': row[0], 'name': row[1], 'packageName': row[2], 'signerSha256': row[3],
                'description': row[4]}

    def dependencies(self, project: str, version_code: int | None = None) -> list[dict]:
        """The libraries a delivered version was built with (the newest one when version_code is None)."""
        with self.lock:
            if version_code is None:
                version_code = self.db.execute('SELECT MAX(version_code) FROM versions WHERE project_id=?',
                                               (project,)).fetchone()[0]
            rows = self.db.execute('SELECT coordinate, sha256, size FROM version_dependencies WHERE project_id=? AND '
                                   'version_code=? ORDER BY coordinate', (project, version_code)).fetchall()
        return [{'coordinate': c, 'sha256': h, 'size': n} for c, h, n in rows]

    def api_report(self, project: str, version_code: int) -> dict | None:
        """The advisory API level report recorded for a delivered version (rose.app_builder.apilevel)."""
        with self.lock:
            row = self.db.execute('SELECT report FROM version_api_reports WHERE project_id=? AND version_code=?',
                                  (project, version_code)).fetchone()
        return json.loads(row[0]) if row else None

    def profile(self, project: str) -> str:
        """The project's bound profile: CLIENT_SERVER once a client-server version was delivered, else app-v1."""
        with self.lock:
            return self._profile(project)

    def _profile(self, project: str) -> str:
        row = self.db.execute('SELECT profile FROM project_profiles WHERE project_id=?', (project,)).fetchone()
        return row[0] if row else 'app-v1'

    def server(self, project: str, version_code: int) -> dict | None:
        """The server ELF a delivered client-server version pairs with its APK."""
        with self.lock:
            row = self.db.execute('SELECT sha256, size, target FROM version_servers WHERE project_id=? AND '
                                  'version_code=?', (project, version_code)).fetchone()
        return {'sha256': row[0], 'size': row[1], 'target': row[2]} if row else None

    def migration_lock(self, project: str) -> list[dict]:
        """Every migration any delivered version ran, once each (rose.app_builder.migrations.check holds every later
        revision to these). Two versions disagreeing about one would be a ledger fault, refused here."""
        with self.lock:
            rows = self.db.execute('SELECT DISTINCT scope, number, name, sha256 FROM version_migrations WHERE '
                                   'project_id=? ORDER BY scope, number', (project,)).fetchall()
        if len({(r[0], r[1]) for r in rows}) != len(rows):
            raise LedgerError('delivered versions disagree about a migration')
        return [{'scope': r[0], 'number': r[1], 'name': r[2], 'sha256': r[3]} for r in rows]

    def crate_lock(self, project: str) -> dict:
        """name@version -> sha256 for every crate any delivered version's server used
        (rose.app_builder.crates.check_lock holds later builds to it; the ledger refuses a contradicting one)."""
        with self.lock:
            return self._crate_lock(project)

    def _crate_lock(self, project: str) -> dict:
        rows = self.db.execute('SELECT DISTINCT name, version, sha256 FROM version_crates WHERE project_id=?',
                               (project,)).fetchall()
        return {f'{n}@{v}': h for n, v, h in rows}

    def contracts(self, project: str) -> list[str]:
        """The contracts of the project's delivered client-server versions, oldest first (canonical JSON text)."""
        with self.lock:
            rows = self.db.execute('SELECT contract FROM version_contracts WHERE project_id=? ORDER BY version_code',
                                   (project,)).fetchall()
        return [r[0] for r in rows]

    def cargo_lock(self, project: str) -> str | None:
        """The newest delivered client-server version's Cargo.lock, which the next server build starts from."""
        with self.lock:
            row = self.db.execute('SELECT lock FROM version_cargo_locks WHERE project_id=? ORDER BY version_code DESC '
                                  'LIMIT 1', (project,)).fetchone()
        return row[0] if row else None

    def crates(self, project: str, version_code: int) -> list[dict]:
        """The crates a delivered client-server version's server was built with."""
        with self.lock:
            rows = self.db.execute('SELECT name, version, sha256 FROM version_crates WHERE project_id=? AND '
                                   'version_code=? ORDER BY name, version', (project, version_code)).fetchall()
        return [{'name': n, 'version': v, 'sha256': h} for n, v, h in rows]

    def hosted_app(self, project: str) -> str | None:
        """The hosted app id owner tooling or the publisher recorded for the project (never derived from it)."""
        with self.lock:
            row = self.db.execute('SELECT hosted_app_id FROM project_hosting WHERE project_id=?', (project,)).fetchone()
        return row[0] if row else None

    def record_hosted_app(self, project: str, hosted_app_id: str) -> None:
        """Record the mapping once: the same id again is a no-op; another id, or an id another project has, is
        refused (a mapping that moved would send one app's phones another app's links)."""
        if not isinstance(hosted_app_id, str) or not UUID.fullmatch(hosted_app_id):
            raise LedgerError('a hosted app id is a lower-case UUID')
        with self.lock:
            if self._project(project) is None:
                raise LedgerError('unknown project')
            current = self.hosted_app(project)
            if current == hosted_app_id:
                return
            if current is not None:
                raise LedgerError('this project already has a hosted app id')
            try:
                self.db.execute('INSERT INTO project_hosting VALUES (?,?,?)', (project, hosted_app_id, self.clock()))
            except sqlite3.IntegrityError:
                raise LedgerError('that hosted app id belongs to another project') from None

    def library_lock(self, project: str) -> dict:
        """coordinate -> sha256 for every library any delivered version of the project used (the newest digest if
        a coordinate was ever recorded twice), so a library dropped and declared again must still match."""
        with self.lock:
            rows = self.db.execute('SELECT coordinate, sha256 FROM version_dependencies WHERE project_id=? '
                                   'ORDER BY version_code', (project,)).fetchall()
        return dict(rows)

    def versions(self, project: str) -> list[dict]:
        with self.lock:
            return self._versions(project)

    def _versions(self, project: str) -> list[dict]:
        # Each version's advisory API level report summary (apilevel.wire_summary): `none` when it has no report.
        rows = self.db.execute('SELECT v.version_code, v.version_name, v.created, v.job_id, v.source_revision, '
                               'v.sha256, v.size, v.notes, r.report FROM versions v LEFT JOIN version_api_reports r '
                               'ON r.project_id=v.project_id AND r.version_code=v.version_code WHERE v.project_id=? '
                               'ORDER BY v.version_code', (project,)).fetchall()
        return [{'versionCode': r[0], 'versionName': r[1], 'createdAt': r[2], 'jobId': r[3], 'sourceRevision': r[4],
                 'apk': {'sha256': r[5], 'size': r[6]}, 'notes': r[7], 'apiReport': wire_summary(r[8])} for r in rows]

    def projects(self) -> list[dict]:
        with self.lock:
            return self._projects()

    def _projects(self) -> list[dict]:
        out = []
        for (project,) in self.db.execute('SELECT project_id FROM projects ORDER BY created').fetchall():
            p = self.project(project)
            active = self.db.execute('SELECT job_id, state FROM jobs WHERE project_id=? AND state IN (%s) '
                                     'ORDER BY started DESC LIMIT 1' % ','.join('?' * len(ACTIVE)),
                                     (project, *ACTIVE)).fetchone()
            out.append({k: p[k] for k in ('projectId', 'name', 'packageName', 'signerSha256')}
                       | {'activeJob': {'jobId': active[0], 'state': active[1]} if active else None,
                          'versions': self.versions(project)})
        return out

    def job(self, job_id: str):
        with self.lock:
            return self._job(job_id)

    def _job(self, job_id: str):
        row = self.db.execute('SELECT job_id, project_id, kind, request, attempt, state, stage, message, log_tail, '
                              'started, updated, failure, version_code FROM jobs WHERE job_id=?',
                              (job_id,)).fetchone()
        if row is None:
            return None
        out = {'jobId': row[0], 'projectId': row[1], 'kind': row[2], 'request': row[3], 'attempt': row[4],
               'state': row[5], 'stage': row[6], 'message': row[7], 'logTail': row[8], 'startedAt': row[9],
               'updatedAt': row[10], 'versionCode': row[12]}
        if row[11]:
            out['failure'] = json.loads(row[11])
        return out

    def artifact(self, sha256: str):
        with self.lock:
            return self._artifact(sha256)

    def _artifact(self, sha256: str):
        row = self.db.execute('SELECT project_id, version_code FROM versions WHERE sha256=?', (sha256,)).fetchone()
        return None if row is None else {'projectId': row[0], 'versionCode': row[1]}
