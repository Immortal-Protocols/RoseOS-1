"""Builder API v1 (docs/APP-BUILDER.md §3) and the worker that runs one build job at a time.

A job moves queued -> preparing (source checked out, generated) -> building -> testing (the APK's identity is
verified) -> signing -> ready, or ends failed/cancelled with a category. Each project's source is a private git
repository; a revision always starts from the commit of the last delivered version, never from a failed attempt,
and each delivered version records the commit it was built from. The generator (Codex in a project-scoped sandbox
on an authorized host) is injected: this module never decides how code is written, only what identity it gets.
"""
from __future__ import annotations

import shutil
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
    if not isinstance(body, dict) or body.get('schemaVersion') != SCHEMA_VERSION:
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
    env = {'PATH': '/usr/bin:/bin', 'HOME': str(source.parent), 'GIT_CONFIG_NOSYSTEM': '1', 'LANG': 'C.UTF-8',
           'GIT_AUTHOR_NAME': 'Rose builder', 'GIT_AUTHOR_EMAIL': 'builder@localhost',
           'GIT_COMMITTER_NAME': 'Rose builder', 'GIT_COMMITTER_EMAIL': 'builder@localhost'}
    result = subprocess.run(['git', '-C', str(source), *args], capture_output=True, text=True, env=env)
    if result.returncode:
        raise toolchain.StepFailed('internal', 'The project source could not be recorded.')
    return result.stdout.strip()


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
            if not self.run_next():
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
            self.generate(source, job['kind'], job['request'], cancelled)
            if cancelled():
                raise toolchain.StepFailed('cancelled', 'Cancelled.')
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
            toolchain.ensure_key(keys, package, self.tools, cancelled)
            signed = work / 'signed.apk'
            receipt = toolchain.sign(unsigned, signed, keys, project['signerSha256'], self.tools)
            try:
                self.ledger.bind_signer(project_id, receipt['signerSha256'])
            except LedgerError:
                raise toolchain.StepFailed('signing', 'The build was signed by a different key than this app, so it '
                                           'was not delivered.') from None
            artifacts = self.root / 'artifacts'
            artifacts.mkdir(mode=0o700, exist_ok=True)
            final = artifacts / f'{receipt["sha256"]}.apk'
            if not final.exists():
                shutil.copyfile(signed, final)
                idsig = signed.with_suffix('.apk.idsig')
                if idsig.exists():
                    shutil.copyfile(idsig, artifacts / f'{receipt["sha256"]}.apk.idsig')
            if not self.ledger.deliver(job_id, name, revision, receipt['sha256'], receipt['size'],
                                       job['request'][:200]):
                raise toolchain.StepFailed('cancelled', 'Cancelled.')
        except toolchain.StepFailed as failed:
            self._reset(source, project_id)
            self.ledger.finish_failed(job_id, failed.category, failed.summary, failed.retryable, failed.log)
        except Exception as error:  # never leave a job running; the summary carries no paths or tool output
            self._reset(source, project_id)
            self.ledger.finish_failed(job_id, 'internal', f'The builder stopped ({type(error).__name__}).', True)

    def _prepare(self, source: Path, project: dict, job: dict):
        if not (source / '.git').exists():
            source.mkdir(mode=0o700, parents=True, exist_ok=True)
            _git(source, 'init', '-q')
            template.write_skeleton(source, project['packageName'], project['name'])
            _git(source, 'add', '-A')
            _git(source, 'commit', '-q', '-m', 'skeleton')
        else:
            self._reset(source, project['projectId'])

    def _reset(self, source: Path, project_id: str):
        """Back to the last delivered version's commit (or the skeleton): a failed attempt never carries over."""
        if not (source / '.git').exists():
            return
        versions = self.ledger.versions(project_id)
        target = versions[-1]['sourceRevision'] if versions else _git(source, 'rev-list', '--max-parents=0', 'HEAD')
        _git(source, 'reset', '-q', '--hard', target)
        _git(source, 'clean', '-q', '-fdx')
