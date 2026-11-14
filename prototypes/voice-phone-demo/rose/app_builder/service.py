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
import time
from pathlib import Path

from . import dependencies, template, toolchain
from .ledger import Ledger, LedgerError

SCHEMA_VERSION = 1
MAX_TEXT = 4000
INSTALL_STATUSES = ('installed', 'updated', 'refused_signer', 'refused_version', 'cancelled_by_user', 'failed')


class Refused(ValueError):
    """A request that does not match API v1; nothing changed (HTTP 400). ``details`` are extra response fields."""

    def __init__(self, message: str, **details):
        super().__init__(message)
        self.details = details


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
            if not _utf8(value):
                raise Refused(f'{name} must be valid text')
            value = value.strip()
        elif kind is int:
            if not isinstance(value, int) or isinstance(value, bool) or not 0 < value <= limit:
                raise Refused(f'{name} must be a positive integer')
        out[name] = value
    return out


def _utf8(text: str) -> bool:
    try:
        text.encode('utf-8')
        return True
    except UnicodeEncodeError:  # a lone surrogate from a \\ud800 escape
        return False


def _git(source: Path, *args, work_tree: Path | None = None) -> str:
    """git on a project's source with the repository kept beside it (repo.git), out of the generator's reach,
    and with hooks, fsmonitor, attributes and excludes switched off: nothing in the tree can make git run code.
    ``work_tree`` records a snapshot of the source instead of the source itself."""
    gitdir = source.parent / 'repo.git'
    env = {'PATH': '/usr/bin:/bin', 'HOME': str(source.parent), 'GIT_CONFIG_NOSYSTEM': '1',
           'GIT_CONFIG_GLOBAL': os.devnull, 'LANG': 'C.UTF-8', 'GIT_DIR': str(gitdir),
           'GIT_WORK_TREE': str(work_tree or source),
           'GIT_AUTHOR_NAME': 'Rose builder', 'GIT_AUTHOR_EMAIL': 'builder@localhost',
           'GIT_COMMITTER_NAME': 'Rose builder', 'GIT_COMMITTER_EMAIL': 'builder@localhost'}
    safe = ['-c', 'core.hooksPath=' + os.devnull, '-c', 'core.fsmonitor=false', '-c', 'core.excludesFile=' + os.devnull,
            '-c', 'core.attributesFile=' + os.devnull, '-c', 'core.symlinks=false', '-c', 'commit.gpgSign=false',
            '-c', 'core.fsync=all', '-c', 'core.fsyncObjectFiles=true']  # a delivered revision survives a crash
    result = subprocess.run(['git', *safe, *args], capture_output=True, text=True, env=env, cwd=source.parent)
    if result.returncode:
        raise toolchain.StepFailed('internal', 'The project source could not be recorded.')
    return result.stdout.strip()


def _owner_access(root: Path):
    """Give the builder (owner) rwx on directories and rw on files, without following links."""
    mode = os.lstat(root).st_mode
    if stat.S_ISDIR(mode):  # the folder itself first: a generator can chmod its own working directory
        os.chmod(root, stat.S_IMODE(mode) | 0o700)
    for top, dirs, files in os.walk(root, followlinks=False):
        for name in dirs + files:
            path = os.path.join(top, name)
            mode = os.lstat(path).st_mode
            if stat.S_ISDIR(mode):
                os.chmod(path, stat.S_IMODE(mode) | 0o700)
            elif stat.S_ISREG(mode):
                os.chmod(path, stat.S_IMODE(mode) | 0o600)
        # os.walk lists a directory before descending, so a directory fixed above is then readable


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


SNAPSHOT_MAX_ENTRIES = 5000
SNAPSHOT_MAX_BYTES = 64 * 1024 * 1024
SNAPSHOT_MAX_DEPTH = 32
NOTE_MAX = 1200


def _snapshot(source: Path, dest: Path):
    """A private copy of the written source, outside the writer's sandbox. The builder checks, records and builds
    only this copy, so anything the writer may have left running can still change ``source`` (which the next job
    resets) but never what is checked, committed and built. Nothing here opens a path that could block or follow a
    link: directories are opened with O_NOFOLLOW, each file through one non-blocking descriptor that is checked as a
    regular file with a single link, and modes are checked on the descriptor (the same owner-access rule as
    ``template.check``), so the source is refused as written. Names are copied as they are and checked afterwards."""
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(mode=0o700, parents=True)
    budget = {'entries': SNAPSHOT_MAX_ENTRIES, 'bytes': SNAPSHOT_MAX_BYTES}
    refuse = lambda why: toolchain.StepFailed('compile', why)
    nofollow = os.O_NOFOLLOW | os.O_CLOEXEC

    def owner_access(st, name):
        if stat.S_ISDIR(st.st_mode) and st.st_mode & 0o700 != 0o700 or \
                stat.S_ISREG(st.st_mode) and st.st_mode & 0o600 != 0o600:
            raise refuse(f'{name} has permissions the builder cannot use.')

    def copy_dir(fd: int, to: Path, depth: int):
        if depth > SNAPSHOT_MAX_DEPTH:
            raise refuse(f'The app\'s folders are nested more than {SNAPSHOT_MAX_DEPTH} deep.')
        owner_access(os.fstat(fd), to.name if depth else 'The app folder')
        for entry in sorted(os.scandir(fd), key=lambda e: e.name):
            budget['entries'] -= 1
            if budget['entries'] < 0:
                raise refuse(f'The app has more than {SNAPSHOT_MAX_ENTRIES} files.')
            mode = entry.stat(follow_symlinks=False).st_mode
            if stat.S_ISDIR(mode):
                child = os.open(entry.name, os.O_RDONLY | os.O_DIRECTORY | nofollow, dir_fd=fd)
                try:
                    (to / entry.name).mkdir(mode=0o700)
                    copy_dir(child, to / entry.name, depth + 1)
                finally:
                    os.close(child)
            elif stat.S_ISREG(mode):
                f = os.open(entry.name, os.O_RDONLY | os.O_NONBLOCK | nofollow, dir_fd=fd)
                try:
                    st = os.fstat(f)
                    if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
                        raise refuse(f'{entry.name} is linked to another file.')
                    owner_access(st, entry.name)
                    with open(to / entry.name, 'xb', opener=lambda p, fl: os.open(p, fl, 0o600)) as out:
                        size = 0
                        while chunk := os.read(f, 1 << 20):
                            size += len(chunk)
                            budget['bytes'] -= len(chunk)
                            if size > template.MAX_FILE:
                                raise refuse(f'{entry.name} is larger than {template.MAX_FILE // (1024 * 1024)} MB.')
                            if budget['bytes'] < 0:
                                raise refuse(f'The app is larger than {SNAPSHOT_MAX_BYTES // (1024 * 1024)} MB.')
                            out.write(chunk)
                finally:
                    os.close(f)
            else:
                raise refuse(f'{entry.name} is a link or special file; only plain files are allowed.')

    try:
        root = os.open(source, os.O_RDONLY | os.O_DIRECTORY | nofollow)
    except OSError:
        raise toolchain.StepFailed('internal', 'The project source could not be read.') from None
    try:
        copy_dir(root, dest, 0)
    except OSError:  # an entry changed or vanished while it was copied
        raise refuse('The app\'s files changed while they were being checked.') from None
    finally:
        os.close(root)


def _note(note, request: str) -> str:
    """The version's display notes: the writer's own summary when it gave one (untrusted text), else the request."""
    if isinstance(note, str) and note.strip() and _utf8(note):
        return note.strip()[:NOTE_MAX]
    return request[:200]


class Builder:
    def __init__(self, root: Path, tools: toolchain.Tools, generate, ledger: Ledger | None = None, backup=None,
                 resolver=None):
        """generate(source, kind, request, cancelled) writes the app's code into source; it may raise
        toolchain.StepFailed. backup(keys_dir, project_id, package) -> receipt copies a project's signing key off the
        host; it must succeed once before the project's signer is bound, so no app is delivered whose key could be
        lost. Without one, nothing is delivered."""
        self.root = Path(root)
        self.ledger = ledger or Ledger(self.root)
        self.tools, self.generate = tools, generate
        self.backup = backup
        self.resolver = resolver  # an offline dependencies.Resolver; None: an app that declares libraries fails
        self.wake = threading.Event()
        self.unrecorded: set[str] = set()  # failures the ledger could not write; retried before the next job
        self.stopping = threading.Event()
        self.ledger.recover()

    # API v1.

    def create(self, device: str, body) -> dict:
        b = _body(body, {'clientRequestId': (str, 64), 'description': (str, MAX_TEXT)})
        name = b['description'].splitlines()[0][:40].strip() or 'New app'
        try:
            out = self.ledger.create(device, b['clientRequestId'], b['description'], name)
        except LedgerError as error:
            raise Refused(str(error)) from None
        self.wake.set()
        return {**out, 'notes': []}

    def revise(self, device: str, project: str, body) -> dict:
        b = _body(body, {'clientRequestId': (str, 64), 'request': (str, MAX_TEXT)})
        try:
            out = self.ledger.revise(device, project, b['clientRequestId'], b['request'])
        except LedgerError as error:
            raise Refused(str(error)) from None
        self.wake.set()
        return {**out, 'notes': []}

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
        if detail is not None and (not isinstance(detail, str) or len(detail) > 500 or not _utf8(detail)):
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
        for job_id in list(self.unrecorded):  # never start new work while a failure is still unwritten
            try:
                self.ledger.finish_failed(job_id, 'internal', 'The builder could not record this build\'s outcome; '
                                          'outcome unknown.', True)
                self.unrecorded.discard(job_id)
            except Exception:
                return False
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
            note = self.generate(source, kind, request, cancelled)
            if cancelled():
                raise toolchain.StepFailed('cancelled', 'Cancelled.')
            # From here on only the snapshot is read: checked, recorded and built are the same files. The live
            # source is never opened by path here, since something the writer left running may still change it.
            snapshot = work.parent / f'snapshot-{job["attempt"]}'
            try:  # before git touches anything the generator wrote
                _snapshot(source, snapshot)
                template.check(snapshot, package)
            except template.TemplateRefused as refused:
                raise toolchain.StepFailed('dependency' if refused.dependency else 'compile', str(refused)) from None
            _git(source, 'add', '-A', work_tree=snapshot)
            if _git(source, 'status', '--porcelain', work_tree=snapshot):
                _git(source, 'commit', '-q', '-m', f'{job["kind"]} {job_id} attempt {job["attempt"]}',
                     work_tree=snapshot)
            revision = _git(source, 'rev-parse', 'HEAD')
            code = self.ledger.allocate_version(job_id)
            name = f'{code}'
            self._step(job, 'building', 'compiling', 'Building the app.')
            if work.exists():
                shutil.rmtree(work)
            libraries = self._libraries(snapshot, work, project_id, package)
            unsigned, log = toolchain.build(snapshot, work, package, code, name, self.tools, cancelled, libraries)
            self._step(job, 'testing', 'verifying', 'Checking the built app.', log)
            self._step(job, 'signing', 'signing', 'Signing the app.')
            keys = self.root / 'keys' / project_id
            toolchain.ensure_key(keys, package, self.tools, cancelled, signer_bound=bool(project['signerSha256']))
            signed = work / 'signed.apk'
            receipt = toolchain.sign(unsigned, signed, keys, project['signerSha256'], self.tools)
            self._back_up_key(keys, project_id, package)  # before the signer is bound or anything is delivered
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
                                       _note(note, job['request']), libraries):
                raise toolchain.StepFailed('cancelled', 'Cancelled.')
        except toolchain.StepFailed as failed:
            self._safe_reset(source, project_id)
            category, summary, retryable = failed.category, failed.summary, failed.retryable
            if category == 'cancelled' and not self.ledger.cancel_requested(job_id):
                # Stopped by a server shutdown, not by the owner: the outcome is unknown and it can be retried.
                category, summary, retryable = 'internal', 'Stopped by a server shutdown; outcome unknown.', True
            self._record_failure(job_id, category, summary, retryable, failed.log)
        except Exception as error:  # never leave a job running; the summary carries no paths or tool output
            self._safe_reset(source, project_id)
            self._record_failure(job_id, 'internal', f'The builder stopped ({type(error).__name__}).', True)

    def _libraries(self, snapshot: Path, work: Path, project_id: str, package: str) -> list:
        """The verified libraries the app declares (none when it declares none). A coordinate the last delivered
        version used must resolve to the same bytes it was built with."""
        try:
            coordinates = dependencies.declared(snapshot)
            if not coordinates:
                return []
            locked = {d['coordinate']: d['sha256'] for d in self.ledger.dependencies(project_id)}
            framework = dependencies.framework_classes(self.tools.android_jar) if self.resolver is not None else None
            return dependencies.resolve(coordinates, self.resolver, work / 'libs', package, framework or frozenset(),
                                        locked)
        except dependencies.Refused as refused:
            raise toolchain.StepFailed('compile' if refused.malformed else 'dependency', str(refused)) from None
        except OSError:
            raise toolchain.StepFailed('toolchain', 'The builder could not read its Android platform classes.',
                                       retryable=True) from None

    def _back_up_key(self, keys: Path, project_id: str, package: str):
        """Once per project: the injected backup must return a receipt before the first delivery."""
        if self.ledger.key_backup(project_id) is not None:
            return
        if self.backup is None:
            raise toolchain.StepFailed('signing', 'This builder has no signing-key backup set up, so the app was not '
                                       'delivered.')
        try:
            receipt = self.backup(keys, project_id, package)
        except toolchain.StepFailed:
            raise
        except Exception:
            receipt = None
        if not (isinstance(receipt, str) and 0 < len(receipt) <= 200 and receipt.isprintable()):  # = host.valid_receipt
            raise toolchain.StepFailed('signing', 'This app\'s signing key could not be backed up, so the app was '
                                       'not delivered.', retryable=True)
        self.ledger.record_key_backup(project_id, receipt)

    def _record_failure(self, job_id, category, summary, retryable, log=''):
        """Record a failure, retrying a failed write (a full disk often caused the failure itself). If it still
        cannot be written, the job is failed by recover() at the next start, never left to look running for good."""
        for delay in (0, 0.5, 2.0, 5.0):
            time.sleep(delay)
            try:
                self.ledger.finish_failed(job_id, category, summary, retryable, log)
                return
            except Exception:
                continue
        self.unrecorded.add(job_id)

    def _prepare(self, source: Path, project: dict, job: dict):
        gitdir = source.parent / 'repo.git'
        if source.exists():
            _owner_access(source)
        if gitdir.exists():
            # One builder process runs git at a time, so a lock here was left by a crash: clear it.
            for lock in [*gitdir.glob('*.lock'), *gitdir.glob('refs/**/*.lock')]:
                lock.unlink()
        if gitdir.exists() and not self._has_commit(source) and not self.ledger.versions(project['projectId']):
            # An interrupted first preparation: nothing was delivered from it, so start the project afresh.
            shutil.rmtree(gitdir)
            if source.exists():
                shutil.rmtree(source)
        if not gitdir.exists() and self.ledger.versions(project['projectId']):
            # Delivered versions without their history: never rebuild them from the skeleton and ship a regression.
            raise toolchain.StepFailed('internal', 'This app\'s source history is missing, so it cannot be changed '
                                       'safely; nothing was built.')
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
        _owner_access(source)  # whatever modes the generator left, git and the cleanup below can work
        for entry in list(source.iterdir()):  # never let the attempt's git metadata act during the reset
            if template.git_name(entry.name):
                entry.unlink() if entry.is_symlink() or not entry.is_dir() else shutil.rmtree(entry)
        versions = self.ledger.versions(project_id)
        target = versions[-1]['sourceRevision'] if versions else _git(source, 'rev-list', '--max-parents=0', 'HEAD')
        try:
            _git(source, 'cat-file', '-e', target + '^{commit}')
        except toolchain.StepFailed:
            raise toolchain.StepFailed('internal', 'The delivered version\'s source is missing from this app\'s '
                                       'history, so it cannot be changed safely; nothing was built.') from None
        _git(source, 'reset', '-q', '--hard', target)
        _git(source, 'clean', '-q', '-ffdx')  # twice: also nested repositories git would otherwise keep
        for path in sorted(source.rglob('*'), key=lambda p: len(p.parts), reverse=True):
            mode = os.lstat(path).st_mode
            if template.git_name(path.name) or not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
                # whatever git left (metadata, FIFOs, sockets, links), never into the next attempt
                path.unlink() if not stat.S_ISDIR(mode) else shutil.rmtree(path)
