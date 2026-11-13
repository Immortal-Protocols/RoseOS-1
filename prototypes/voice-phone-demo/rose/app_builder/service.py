"""Builder API v1 (docs/APP-BUILDER.md §3) and the worker that runs one build job at a time.

A job moves queued -> preparing (source checked out, generated) -> building -> testing (the APK's identity is
verified) -> signing -> ready, or ends failed/cancelled with a category. Each project's source is a private git
repository; a revision always starts from the commit of the last delivered version, never from a failed attempt,
and each delivered version records the commit it was built from. The generator (Codex in a project-scoped sandbox
on an authorized host) is injected: this module never decides how code is written, only what identity it gets.
"""
from __future__ import annotations

import os
import shutil
import stat
import subprocess
import threading
from pathlib import Path

from . import template, toolchain
from .ledger import Ledger, LedgerError

SCHEMA_VERSION = 1
MAX_TEXT = 4000
INSTALL_STATUSES = ('installed', 'updated', 'refused_signer', 'refused_version', 'cancelled_by_user', 'failed')


class Refused(ValueError):
    """A request that does not match API v1; nothing changed (HTTP 400)."""


def _body(body, keys: dict) -> dict:
    if not isinstance(body, dict) or type(body.get('schemaVersion')) is not int or body['schemaVersion'] != 1:
        raise Refused('schemaVersion must be 1')
    extra = set(body) - set(keys) - {'schemaVersion'}
    if extra:
        raise Refused('unknown field ' + sorted(extra)[0])
    out = {}
    for name, (kind, limit) in keys.items():
        value = body.get(name)
        if kind is str:
            if not isinstance(value, str) or not value.strip() or len(value) > limit:
                raise Refused(f'{name} must be 1 to {limit} characters')
            value = value.strip()
        elif kind is int:
            if not isinstance(value, int) or isinstance(value, bool) or not 0 < value <= limit:
                raise Refused(f'{name} must be a positive integer')
        out[name] = value
    return out


def _git(source: Path, *args) -> str:
    """git on a project's source with the repository kept beside it (repo.git), out of the generator's reach,
    and with hooks, fsmonitor, attributes and excludes switched off: nothing in the tree can make git run code."""
    gitdir = source.parent / 'repo.git'
    env = {'PATH': '/usr/bin:/bin', 'HOME': str(source.parent), 'GIT_CONFIG_NOSYSTEM': '1',
           'GIT_CONFIG_GLOBAL': os.devnull, 'LANG': 'C.UTF-8', 'GIT_DIR': str(gitdir), 'GIT_WORK_TREE': str(source),
           'GIT_AUTHOR_NAME': 'Rose builder', 'GIT_AUTHOR_EMAIL': 'builder@localhost',
           'GIT_COMMITTER_NAME': 'Rose builder', 'GIT_COMMITTER_EMAIL': 'builder@localhost'}
    safe = ['-c', 'core.hooksPath=' + os.devnull, '-c', 'core.fsmonitor=false', '-c', 'core.excludesFile=' + os.devnull,
            '-c', 'core.attributesFile=' + os.devnull, '-c', 'core.symlinks=false', '-c', 'commit.gpgSign=false',
            '-c', 'core.fsync=all', '-c', 'core.fsyncObjectFiles=true']  # a delivered revision survives a crash
    result = subprocess.run(['git', *safe, *args], capture_output=True, text=True, env=env, cwd=source.parent)
    if result.returncode:
        raise toolchain.StepFailed('internal', 'The project source could not be recorded.')
    return result.stdout.strip()


def _place(src: Path, dst: Path):
    """Copy to a temporary name, fsync, then rename: a crash never leaves a partial artifact under its hash."""
    if dst.exists():
        return
    tmp = dst.with_name(dst.name + '.tmp')
    shutil.copyfile(src, tmp)
    with open(tmp, 'rb') as f:
        os.fsync(f.fileno())
    os.replace(tmp, dst)
    toolchain.fsync_path(dst.parent)


class Builder:
    def __init__(self, root: Path, tools: toolchain.Tools, generate, ledger: Ledger | None = None):
        """generate(source, kind, request, cancelled) writes the app's code into source; it may raise
        toolchain.StepFailed."""
        self.root = Path(root)
        self.ledger = ledger or Ledger(self.root)
        self.tools, self.generate = tools, generate
        self.wake = threading.Event()
        self.stopping = threading.Event()
        self.ledger.recover()

    # API v1.

    def create(self, device: str, body) -> dict:
        b = _body(body, {'clientRequestId': (str, 64), 'description': (str, MAX_TEXT)})
        name = b['description'].splitlines()[0][:40].strip() or 'New app'
        out = self.ledger.create(device, b['clientRequestId'], b['description'], name)
        self.wake.set()
        return out

    def revise(self, device: str, project: str, body) -> dict:
        b = _body(body, {'clientRequestId': (str, 64), 'request': (str, MAX_TEXT)})
        try:
            out = self.ledger.revise(device, project, b['clientRequestId'], b['request'])
        except LedgerError as error:
            raise Refused(str(error)) from None
        self.wake.set()
        return out

    def retry(self, job_id: str, body) -> dict:
        """Run a failed, retryable job again as its next attempt (same jobId, attempt + 1)."""
        _body(body, {})
        try:
            job = self.ledger.retry(job_id)
        except LedgerError as error:
            raise Refused(str(error)) from None
        self.wake.set()
        return {'jobId': job['jobId'], 'attempt': job['attempt']}

    def cancel(self, job_id: str, body) -> dict:
        _body(body, {})
        return {'status': self.ledger.cancel(job_id)}

    def job(self, job_id: str):
        job = self.ledger.job(job_id)
        if job is None:
            return None
        return {k: job[k] for k in ('jobId', 'projectId', 'attempt', 'state', 'stage', 'message', 'logTail',
                                    'startedAt', 'updatedAt')} | ({'failure': job['failure']} if 'failure' in job else {})

    def projects(self) -> list[dict]:
        return self.ledger.projects()

    def artifact(self, sha256: str) -> Path | None:
        if not isinstance(sha256, str) or len(sha256) != 64 or not all(c in '0123456789abcdef' for c in sha256):
            return None
        path = self.root / 'artifacts' / f'{sha256}.apk'
        return path if self.ledger.artifact(sha256) and path.is_file() else None

    def install(self, device: str, body) -> dict:
        # versionCode: the version this receipt is about (the one attempted). signerSha256: what the phone has for
        # the package afterwards, null when nothing is installed. detail: Android's status message, may be empty.
        body = dict(body) if isinstance(body, dict) else body
        signer = body.pop('signerSha256', 'missing') if isinstance(body, dict) else 'missing'
        detail = body.pop('detail', None) if isinstance(body, dict) else None
        b = _body(body, {'projectId': (str, 32), 'packageName': (str, 200), 'versionCode': (int, 2**31 - 1),
                         'status': (str, 32)})
        if b['status'] not in INSTALL_STATUSES:
            raise Refused('unknown install status')
        if signer is not None and not (isinstance(signer, str) and len(signer) == 64
                                       and all(c in '0123456789abcdef' for c in signer)):
            raise Refused('signerSha256 must be 64 lowercase hex characters or null')
        if detail is not None and (not isinstance(detail, str) or len(detail) > 500):
            raise Refused('detail must be at most 500 characters')
        b['signerSha256'], b['detail'] = signer, detail or ''
        project = self.ledger.project(b['projectId'])
        if project is None or project['packageName'] != b['packageName']:
            raise Refused('unknown project or package')
        self.ledger.record_install(device, b)
        return {'recorded': True}

    # Worker.

    def serve_forever(self):
        while not self.stopping.is_set():
            try:
                ran = self.run_next()
            except Exception:  # one job's failure never stops the worker; _run already recorded it
                ran = True
            if not ran:
                self.wake.wait(5.0)
                self.wake.clear()

    def stop(self):
        self.stopping.set()
        self.wake.set()

    def run_next(self) -> bool:
        job = self.ledger.next_queued()
        if job is None:
            return False
        self._run(job)
        return True

    def _step(self, job, state, stage, message, log=''):
        if not self.ledger.advance(job['jobId'], state, stage, message, log):
            raise toolchain.StepFailed('cancelled', 'Cancelled.')

    def _run(self, job):
        job_id, project_id = job['jobId'], job['projectId']
        project = self.ledger.project(project_id)
        package = project['packageName']
        source = self.root / 'projects' / project_id / 'source'
        work = self.root / 'jobs' / job_id / f'attempt-{job["attempt"]}'
        cancelled = lambda: self.ledger.cancel_requested(job_id) or self.stopping.is_set()
        try:
            self._step(job, 'preparing', 'source', 'Preparing the project source.')
            self._prepare(source, project, job)
            self._step(job, 'preparing', 'generating', 'Writing the app.')
            kind, request = job['kind'], job['request']
            if kind == 'revise' and not self.ledger.versions(project_id):
                # Nothing delivered yet: this starts from the skeleton, so the generator writes the whole app.
                kind, request = 'create', project['description'] + '\n\nChange requested: ' + request
            self.generate(source, kind, request, cancelled)
            if cancelled():
                raise toolchain.StepFailed('cancelled', 'Cancelled.')
            try:  # before git touches anything the generator wrote
                template.check(source, package)
            except template.TemplateRefused as refused:
                raise toolchain.StepFailed('dependency' if refused.dependency else 'compile', str(refused)) from None
            _git(source, 'add', '-A')
            if _git(source, 'status', '--porcelain'):
                _git(source, 'commit', '-q', '-m', f'{job["kind"]} {job_id} attempt {job["attempt"]}')
            revision = _git(source, 'rev-parse', 'HEAD')
            code = self.ledger.allocate_version(job_id)
            name = f'{code}'
            self._step(job, 'building', 'compiling', 'Building the app.')
            if work.exists():
                shutil.rmtree(work)
            unsigned, log = toolchain.build(source, work, package, code, name, self.tools, cancelled)
            self._step(job, 'testing', 'verifying', 'Checking the built app.', log)
            self._step(job, 'signing', 'signing', 'Signing the app.')
            keys = self.root / 'keys' / project_id
            toolchain.ensure_key(keys, package, self.tools, cancelled, signer_bound=bool(project['signerSha256']))
            signed = work / 'signed.apk'
            receipt = toolchain.sign(unsigned, signed, keys, project['signerSha256'], self.tools)
            try:
                self.ledger.bind_signer(project_id, receipt['signerSha256'])
            except LedgerError:
                raise toolchain.StepFailed('signing', 'The build was signed by a different key than this app, so it '
                                           'was not delivered.') from None
            artifacts = self.root / 'artifacts'
            artifacts.mkdir(mode=0o700, exist_ok=True)
            idsig = signed.with_suffix('.apk.idsig')
            if idsig.exists():
                _place(idsig, artifacts / f'{receipt["sha256"]}.apk.idsig')
            _place(signed, artifacts / f'{receipt["sha256"]}.apk')  # durable before the version row exists
            if not self.ledger.deliver(job_id, name, revision, receipt['sha256'], receipt['size'],
                                       job['request'][:200]):
                raise toolchain.StepFailed('cancelled', 'Cancelled.')
        except toolchain.StepFailed as failed:
            self._safe_reset(source, project_id)
            category, summary, retryable = failed.category, failed.summary, failed.retryable
            if category == 'cancelled' and not self.ledger.cancel_requested(job_id):
                # Stopped by a server shutdown, not by the owner: the outcome is unknown and it can be retried.
                category, summary, retryable = 'internal', 'Stopped by a server shutdown; outcome unknown.', True
            self.ledger.finish_failed(job_id, category, summary, retryable, failed.log)
        except Exception as error:  # never leave a job running; the summary carries no paths or tool output
            self._safe_reset(source, project_id)
            self.ledger.finish_failed(job_id, 'internal', f'The builder stopped ({type(error).__name__}).', True)

    def _prepare(self, source: Path, project: dict, job: dict):
        gitdir = source.parent / 'repo.git'
        if gitdir.exists() and not self._has_commit(source):
            # An interrupted first preparation: nothing was delivered from it, so start the project afresh.
            shutil.rmtree(gitdir)
            if source.exists():
                shutil.rmtree(source)
        if not gitdir.exists():
            source.mkdir(mode=0o700, parents=True, exist_ok=True)
            _git(source, 'init', '-q')
            template.write_skeleton(source, project['packageName'], project['name'])
            _git(source, 'add', '-A')
            _git(source, 'commit', '-q', '-m', 'skeleton')
        else:
            self._reset(source, project['projectId'])

    @staticmethod
    def _has_commit(source: Path) -> bool:
        try:
            _git(source, 'rev-parse', '--verify', '-q', 'HEAD')
            return True
        except toolchain.StepFailed:
            return False

    def _safe_reset(self, source: Path, project_id: str):
        try:
            self._reset(source, project_id)
        except Exception:  # the job is still recorded as failed; the next attempt resets again before it starts
            pass

    def _reset(self, source: Path, project_id: str):
        """Back to the last delivered version's commit (or the skeleton): a failed attempt never carries over."""
        if not (source.parent / 'repo.git').exists():
            return
        if (source / '.git').exists() or (source / '.git').is_symlink():  # never the builder's; drop it unread
            path = source / '.git'
            path.unlink() if path.is_symlink() or path.is_file() else shutil.rmtree(path)
        versions = self.ledger.versions(project_id)
        target = versions[-1]['sourceRevision'] if versions else _git(source, 'rev-list', '--max-parents=0', 'HEAD')
        _git(source, 'reset', '-q', '--hard', target)
        _git(source, 'clean', '-q', '-ffdx')  # twice: also nested repositories git would otherwise keep
        for path in sorted(source.rglob('*'), key=lambda p: len(p.parts), reverse=True):
            mode = os.lstat(path).st_mode
            if path.name in template.FORBIDDEN_NAMES or not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
                # whatever git left (metadata, FIFOs, sockets, links), never into the next attempt
                path.unlink() if not stat.S_ISDIR(mode) else shutil.rmtree(path)
