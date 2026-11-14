"""Durable project, version and job records for the app builder.

One SQLite file (0600, in a 0700 directory outside the repository) holds every identity a generated app must keep:
its package name and signer never change, and its versionCode only grows, allocated here inside a transaction, never
by generated code. A job left running by a restart becomes failed ("interrupted; outcome unknown", retryable) rather
than being run again, because a half-finished build may already have produced side effects.
"""
from __future__ import annotations

import json
import os
import secrets
import sqlite3
import threading
import time
from pathlib import Path

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
CREATE TABLE IF NOT EXISTS installs (
  project_id TEXT NOT NULL, package_name TEXT NOT NULL, version_code INTEGER NOT NULL, signer_sha256 TEXT,
  status TEXT NOT NULL, detail TEXT NOT NULL, device TEXT NOT NULL, created REAL NOT NULL);
"""


def _text(value: str) -> str:
    """Text sqlite can store: anything undecodable (a generated file name, tool output) is escaped, not dropped."""
    return value.encode('utf-8', 'backslashreplace').decode('utf-8')


class LedgerError(ValueError):
    """A request the ledger refuses; nothing changed."""


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
                dependencies=(), api_report: dict | None = None):
        """Record the delivered version and, in the same transaction, the libraries it was built with (its lock)."""
        with self.lock:
            job = self.job(job_id)
            if job['state'] not in ACTIVE or self.cancel_requested(job_id):
                return False
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
        rows = self.db.execute('SELECT version_code, version_name, created, job_id, source_revision, sha256, size, '
                               'notes FROM versions WHERE project_id=? ORDER BY version_code', (project,)).fetchall()
        return [{'versionCode': r[0], 'versionName': r[1], 'createdAt': r[2], 'jobId': r[3], 'sourceRevision': r[4],
                 'apk': {'sha256': r[5], 'size': r[6]}, 'notes': r[7]} for r in rows]

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
