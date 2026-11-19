"""Builder API v1 (docs/APP-BUILDER.md §3) and the worker that runs one build job at a time.

A job moves queued -> preparing (source checked out, generated) -> building -> testing (the APK's identity is
verified) -> signing -> ready, or ends failed/cancelled with a category. Each project's source is a private git
repository; a revision always starts from the commit of the last delivered version, never from a failed attempt,
and each delivered version records the commit it was built from. The generator (Codex in a project-scoped sandbox
on an authorized host) is injected: this module never decides how code is written, only what identity it gets.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import re
import shutil
import sqlite3
import stat
import subprocess
import threading
import time
from pathlib import Path

from . import appprofile, contract, dependencies, gradle, legacy_backend, template, toolchain
from .ledger import CLIENT_BACKEND, CLIENT_SERVER, HOSTED_URL, MAX_CONTRACT, Ledger, LedgerError

SCHEMA_VERSION = 1
MAX_TEXT = 4000
INSTALL_STATUSES = ('installed', 'updated', 'refused_signer', 'refused_version', 'cancelled_by_user', 'failed')


class Refused(ValueError):
    """A request that does not match API v1; nothing changed (HTTP 400). ``details`` are extra response fields."""

    def __init__(self, message: str, **details):
        super().__init__(message)
        self.details = details


class Unavailable(Exception):
    """A valid request the builder cannot do now (HTTP 409): ``reason`` is one word, ``retryable`` whether asking
    again later can work."""

    def __init__(self, message: str, reason: str, retryable: bool):
        super().__init__(message)
        self.reason, self.retryable = reason, retryable


INVITE_REASONS = {  # the agent's word -> (the wire's reason, retryable, the sentence)
    'already_enrolled': ('already_enrolled', False, 'That account is already signed up to this app.'),
    'invalid_name': ('invalid_account', False, 'That account name cannot be used in this app.'),
    'busy': ('server_unavailable', True, 'This app\'s server is being set up; ask again in a moment.'),
    'unsupported': ('invites_unsupported', False, 'This app\'s backend has no sign-in invites.'),
    'not_set_up': ('not_hosted', False, 'This app\'s server is not set up in its agent container.'),
    'not_hosted': ('not_hosted', False, 'This app\'s server is not set up in its agent container.'),
}


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


def _api_report(work: Path) -> dict | None:
    """The build's advisory API level report, if it wrote one (it never decides delivery)."""
    try:
        return json.loads((work / 'api-report.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None


def _note(note, request: str) -> str:
    """The version's display notes: the writer's own summary when it gave one (untrusted text), else the request."""
    if isinstance(note, str) and note.strip() and _utf8(note):
        return note.strip()[:NOTE_MAX]
    return request[:200]


ROSE_SERVER = 'RoseServer.java'  # the builder's client file under src/<package>/rose/: the hosted base URL
# HOSTED_URL (from the ledger, which checks a recorded endpoint with it too): what the generated client accepts.


def rose_server_java(package: str, url: str | None) -> str:
    """``src/<package>/rose/RoseServer.java``, written by the builder on every client-server build: the hosted base
    URL, or null when the app is not hosted yet. The generated client reads it (``RoseApi.serverUrl()``)."""
    value = 'null' if url is None else '"' + url.replace('\\', '\\\\').replace('"', '\\"') + '"'
    return (f'package {package}.rose;\n\n'
            '/** Written by the Rose app builder from the hosting record. Generated; not the app\'s code. */\n'
            'final class RoseServer {\n'
            '    private RoseServer() {}\n'
            f'    static final String URL = {value};\n'
            '}\n')


def client_tree_ignore(snapshot: Path, top_names=appprofile.BACKEND_TOP):
    """copytree's ignore for a client-server app's client tree: the server's parts at the top of the snapshot only
    (a writer's own folder named server/ or contract/ further down is the app's)."""
    top = Path(snapshot)
    return lambda d, names: [n for n in names if Path(d) == top and n in top_names]


REPAIRS = 2  # repairs of the writer's own build errors in one job, after its first turn
REPAIR_MIN_S = 60.0  # writing time a repair needs left in the job's one writer budget to be worth starting
REPAIR_TEXT_MAX = 4000
_ABSOLUTE_PATH = re.compile(r'(?<![\w.~-])/(?:[\w.@+~-]+/)*[\w.@+~-]+')
_REDACTED_FOLDER = re.compile(r'<[\w.@+~-]+>/')  # toolchain.run's redaction of the folder it built in


def _repairable(failed: toolchain.StepFailed) -> bool:
    """A failure the writer can fix by editing its own source: a build tool (aapt, javac, d8, the server compiler)
    rejected what it wrote, with that tool's output. Policy refusals (an identity, package or profile the builder will
    not take) carry no tool output and are never retried, nor is any other category (dependency, toolchain, signing,
    hosting, timeout, cancelled, internal)."""
    return failed.category == 'compile' and bool((failed.log or '').strip())


def _repair_text(failed: toolchain.StepFailed, folders) -> str:
    """What the writer is told about its build error: the failure's summary and the tail of the build tool's own
    output, bounded. That output can quote any file the build read: the writer's own source, the builder's generated
    files (rose.app_builder.codegen: no key, token or account; the app's hosted URL), the pinned libraries and SDK. It
    cannot hold an inherited environment value: toolchain.run gives each tool a fixed environment (paths and
    LANG) and nothing else. The signing key and its password are not read until a build has passed, and the
    server's keyring only at its runtime. A Gradle project's build output also holds whatever its own build scripts
    print: they run inside the build isolation (rose.app_builder.gradle), which sees only the job's copy of the
    source, the pinned JDK, Gradle, SDK and dependency cache, and no network, so they have nothing else to print.
    What is removed here is location: file URIs become paths, the builder's folders (and toolchain.run's
    ``<folder>/`` redactions) become relative, and every other absolute path is replaced with ``<path>``."""
    text = f'{failed.summary}\n{(failed.log or "")[-REPAIR_TEXT_MAX * 2:]}'
    # Kotlin and Gradle name files as file:///path, or file://<folder>/path once toolchain.run redacted the folder.
    text = re.sub(r'\bfile:(?://)?(?=[/<])', '', text)
    for folder in sorted({str(f) for f in folders}, key=len, reverse=True):
        text = text.replace(folder + '/', '').replace(folder, '.')
    return _ABSOLUTE_PATH.sub('<path>', _REDACTED_FOLDER.sub('', text))[-REPAIR_TEXT_MAX:]


class BackendSession:
    """The building agent's backend for one job attempt (§7.7a, client-backend-v1): the release folder it builds
    in, in the project's own agent container, and the one tool it has there. The container is recorded and the
    folder made on the first call, so a client-only app never takes an agent from the pool."""

    def __init__(self, hosting, project_id: str, release: str, legacy: bool = False):
        self.hosting, self.project_id, self.release = hosting, project_id, release
        self.folder = hosting.release_folder(release)
        self.used = False
        self.legacy = legacy

    def exec(self, command: str, stdin: bytes = b'', timeout_s: float = 1800.0, stopped=lambda: False):
        if not self.used:
            if self.legacy:
                self.hosting.check_legacy(self.project_id)
            self.hosting.ensure(self.project_id)
            self.hosting.prepare(self.project_id, self.release)  # identity and owner checked there first
            self.used = True
        return self.hosting.exec(self.project_id, command, stdin, timeout_s, stopped)


class Builder:
    def __init__(self, root: Path, tools: toolchain.Tools, generate, ledger: Ledger | None = None, backup=None,
                 resolver=None, *, codegen=None, server_url=None, hosting=None, repairs: int = REPAIRS,
                 require_key_backup=True, gradle=None, skeleton=None, resolve_starter=None):
        """generate(source, kind, request, cancelled) writes the app's code into source; it may raise
        toolchain.StepFailed. backup(keys_dir, project_id, package) -> receipt copies a project's signing key off the
        host; it must succeed once before the project's signer is bound unless the owner explicitly permits
        local-only key persistence with require_key_backup=False. Signing identity checks always apply.

        ``gradle`` (rose.app_builder.gradle.GradleTools, docs/APP-BUILDER.md §2.4): ordinary Gradle projects (Kotlin,
        Compose) build with it, offline and inside its isolation, and new apps start from its Kotlin skeleton. Without
        it a new app starts from the older flat Java skeleton, and a Gradle project fails as a toolchain matter.
        Either way the identity, signing and delivery below are the same, so an app can move from one to the other.

        ``skeleton(source, package, label, starter)`` writes a new Gradle project's first tree (once, as its root
        commit), with ``starter`` the {template, theme} the create request named or None: rose.app_builder.starters
        .skeleton, which writes the builder's own skeleton for None. ``resolve_starter(starter)`` checks a create
        request's starter before anything is recorded and raises ValueError for one it does not have
        (rose.app_builder.starters.Starters.resolve). Without them, a create request with a starter is refused
        (``unknown_starter``).

        Backend apps use the injected client code generator and hosting connection. The writing agent builds
        their release inside the existing Chariot agent container; the host builds only the Android client."""
        self.root = Path(root)
        self.ledger = ledger or Ledger(self.root)
        if tools.cache is None:  # the Java system image, made once per toolchain, in this builder's own storage
            tools = dataclasses.replace(tools, cache=self.root / 'toolchain')  # the caller's Tools is not changed
        self.tools, self.generate = tools, generate
        self.backup = backup
        self.require_key_backup = require_key_backup
        self.resolver = resolver  # an offline dependencies.Resolver; None: an app that declares libraries fails
        self.codegen = codegen
        self.server_url = server_url
        self.hosting = hosting
        self.gradle = gradle
        self.skeleton, self.resolve_starter = skeleton, resolve_starter
        # A build tool's rejection of the writer's own source goes back to the writer as a repair turn, at most this
        # many times in a job, before anything is signed: the same project, job and version code.
        self.repairs = max(0, int(repairs))
        self.wake = threading.Event()
        self.unrecorded: set[str] = set()  # failures the ledger could not write; retried before the next job
        self.stopping = threading.Event()
        self.ledger.recover()

    # API v1.

    def create(self, device: str, body) -> dict:
        starter = body.get('starter') if isinstance(body, dict) else None
        if isinstance(body, dict) and 'starter' in body:
            body = {k: v for k, v in body.items() if k != 'starter'}
        b = _body(body, {'clientRequestId': (str, 64), 'description': (str, MAX_TEXT)})
        if starter is not None:
            starter = self._starter(starter)
        name = b['description'].splitlines()[0][:40].strip() or 'New app'
        try:
            out = self.ledger.create(device, b['clientRequestId'], b['description'], name, starter)
        except LedgerError as error:
            raise Refused(str(error)) from None
        self.wake.set()
        return {**out, 'notes': []}

    def _starter(self, starter) -> dict:
        """A create request's starter: exactly {template, theme}, two lowercase ids this builder has."""
        if (not isinstance(starter, dict) or set(starter) != {'template', 'theme'}
                or not all(isinstance(v, str) and re.fullmatch(r'[a-z][a-z0-9-]{0,39}', v) for v in starter.values())):
            raise Refused('starter must be {template, theme}, two lowercase ids', reason='unknown_starter')
        if self.resolve_starter is None or self.gradle is None:
            raise Refused('This builder has no starters.', reason='unknown_starter')
        try:
            self.resolve_starter(starter)
        except ValueError as error:
            raise Refused(str(error) or 'There is no such starter.', reason='unknown_starter') from None
        return {'template': starter['template'], 'theme': starter['theme']}

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

    def invite(self, device: str, project_id: str, body):
        """One single-use invite for the creator (§7.7a): the project's server, as set up in its agent container,
        runs its ``invite <account>``. The token is returned once and never stored or logged; asking again issues a
        new one. None for an unknown project."""
        from .app_host import InviteRefused, valid_account
        if self.ledger.project(project_id) is None:
            return None
        if not isinstance(body, dict) or type(body.get('schemaVersion')) is not int or body['schemaVersion'] != 1:
            raise Refused('schemaVersion must be 1')
        if set(body) - {'schemaVersion', 'account'}:
            raise Refused('unknown field ' + sorted(set(body) - {'schemaVersion', 'account'})[0])
        account = body.get('account')
        if not valid_account(account):
            raise Refused('account must be 1 to 128 bytes of text with no ":" or control character',
                          reason='invalid_account')
        setup = self.ledger.server_setup(project_id)
        server = None if setup is None else self.ledger.server(project_id, setup['versionCode'])
        if self.hosting is None or server is None:
            raise Unavailable(INVITE_REASONS['not_hosted'][2], 'not_hosted', False)
        try:
            token = self.hosting.invite(project_id, server['sha256'], account)
        except InviteRefused as refused:
            reason, retryable, message = INVITE_REASONS.get(refused.word, (
                'server_unavailable', True, 'This app\'s server could not issue an invite now; try again.'))
            raise Unavailable(message, reason, retryable) from None
        return {'projectId': project_id, 'versionCode': setup['versionCode'], 'account': account, 'invite': token,
                'expiresInS': 7 * 24 * 3600}

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
        undo = None  # a set-up server whose version is not (yet) delivered
        try:
            self._step(job, 'preparing', 'source', 'Preparing the project source.')
            self._prepare(source, project, job)
            legacy = self.ledger.profile(project_id) == CLIENT_SERVER
            if legacy:
                legacy_backend.prepare(source, self.ledger, project_id)
            self._step(job, 'preparing', 'generating', 'Writing the app.')
            kind, request = job['kind'], job['request']
            if kind == 'revise' and not self.ledger.versions(project_id):
                # Nothing delivered yet: this starts from the skeleton, so the generator writes the whole app.
                kind, request = 'create', project['description'] + '\n\nChange requested: ' + request
            backend = None
            # The tool only for an app that is, or may become, client-backend-v1: never for one bound to another kind.
            if self.hosting is not None and getattr(self.generate, 'backend', False) \
                    and self.ledger.profile(project_id) in ('app-v1', CLIENT_BACKEND, CLIENT_SERVER):
                release = hashlib.sha256(f'{project_id}:{job_id}:{job["attempt"]}'.encode()).hexdigest()
                backend = BackendSession(self.hosting, project_id, release, legacy=legacy)
            repairs, notes = 0, []
            # One writer budget for the job: repairs share the writer's own turn timeout with the first turn (and the
            # builds between), so repairing never makes the job's writing take longer than one turn could.
            budget = getattr(self.generate, 'timeout_s', None)
            deadline = time.monotonic() + budget if isinstance(budget, (int, float)) and budget > 0 else None
            while True:  # one writer turn and build, then at most self.repairs repairs of the writer's own build errors
                try:
                    (note, snapshot, profile, revision, code, name, libraries, agent, unsigned,
                     log) = self._write_and_build(job, project, source, work, kind, request, cancelled, backend,
                                                  repairs, notes, deadline if repairs else None)
                    break
                except toolchain.StepFailed as failed:
                    if not _repairable(failed) or repairs >= self.repairs or cancelled() or (
                            deadline is not None and deadline - time.monotonic() < REPAIR_MIN_S):
                        raise
                    repairs += 1
                    self._step(job, 'building', 'repairing',
                               f'Fixing a build error ({repairs} of {self.repairs}).')
                    kind, request = 'repair', _repair_text(failed, (work / 'client', work, work.parent, source,
                                                                    self.root))
            note = notes[0]  # the version's notes describe what the owner asked for, not the last repair
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
            server_records, hosted = {}, None
            if agent is not None:  # §7.7a: the release the building agent made, switched to before any phone gets it
                server_records = dict(profile=CLIENT_BACKEND, server={'release': backend.release},
                                      contract=agent['contract'], migrated_from=legacy_backend.base_version(self.ledger, project_id)
                                      if legacy else None)
                self._step(job, 'signing', 'hosting', 'Setting up the app\'s backend.')
                if self.ledger.cancel_requested(job_id):  # the last point a cancel stops it
                    raise toolchain.StepFailed('cancelled', 'Cancelled.')
                hosted = self._host_release(project_id, code, backend.release, agent['url'])
                undo = ('release', project_id, code, backend.release)
            # Once its server is set up, delivery is the commit point: a cancel no longer stops it.
            if not self.ledger.deliver(job_id, name, revision, receipt['sha256'], receipt['size'],
                                       _note(note, job['request']), libraries, _api_report(work),
                                       committed=hosted is not None, **server_records):
                raise toolchain.StepFailed('cancelled', 'Cancelled.')
            undo = None  # delivered: its server stays
            if hosted is not None:  # §7.7a: the library says this version's server is the one set up
                try:
                    self.ledger.record_server_setup(project_id, code, *hosted)
                except (LedgerError, sqlite3.Error):
                    pass  # delivered all the same; the library then claims no setup rather than a wrong one
        except toolchain.StepFailed as failed:
            self._undo_hosting(undo)
            self._safe_reset(source, project_id)
            category, summary, retryable = failed.category, failed.summary, failed.retryable
            if category == 'cancelled' and not self.ledger.cancel_requested(job_id):
                # Stopped by a server shutdown, not by the owner: the outcome is unknown and it can be retried.
                category, summary, retryable = 'internal', 'Stopped by a server shutdown; outcome unknown.', True
            self._record_failure(job_id, category, summary, retryable, failed.log)
        except Exception as error:  # never leave a job running; the summary carries no paths or tool output
            self._undo_hosting(undo)
            self._safe_reset(source, project_id)
            self._record_failure(job_id, 'internal', f'The builder stopped ({type(error).__name__}).', True)

    def _undo_hosting(self, undo) -> None:
        """A version whose server was set up but which was not delivered: put the agent back (best effort; a later
        setup reverts it anyway, as the agent's release is not a delivered one)."""
        if undo is None or self.hosting is None:
            return
        try:
            self.hosting.revert_release(*undo[1:])
        except Exception:
            pass

    def _write_and_build(self, job, project, source: Path, work: Path, kind: str, request: str, cancelled, backend,
                         repairs: int, notes: list, deadline: float | None):
        """One writer turn (or a repair of the writer's own build error), then the checks and the unsigned build of
        exactly what it wrote. Raises StepFailed; nothing here signs, delivers or uses a version up."""
        job_id, project_id, package = job['jobId'], job['projectId'], project['packageName']
        extra = {'backend': backend} if backend is not None else {}
        if deadline is not None:  # a repair turn: what is left of the job's writer budget
            extra['deadline'] = deadline
        note = self.generate(source, kind, request, cancelled, **extra)
        notes.append(note)
        if cancelled():
            raise toolchain.StepFailed('cancelled', 'Cancelled.')
        # From here on only the snapshot is read: checked, recorded and built are the same files. The live
        # source is never opened by path here, since something the writer left running may still change it.
        snapshot = work.parent / f'snapshot-{job["attempt"]}'
        try:  # before git touches anything the generator wrote
            _snapshot(source, snapshot)
            profile = appprofile.check(snapshot, package, bound=self.ledger.profile(project_id))
            if self.ledger.profile(project_id) == CLIENT_SERVER:
                legacy_backend.check(snapshot, self.ledger, project_id)
        except template.TemplateRefused as refused:  # ProfileRefused too
            raise toolchain.StepFailed('dependency' if refused.dependency else 'compile', str(refused)) from None
        layout = gradle.is_gradle_project(snapshot)
        if layout and self.gradle is None:
            raise toolchain.StepFailed('toolchain', 'This builder has no Gradle toolchain set up, so a Gradle project '
                                       '(settings.gradle.kts) cannot be built here yet.')
        # -f: a Gradle project's .gitignore never keeps a file out of the record; what is built is what is recorded.
        _git(source, 'add', '-A', '-f', work_tree=snapshot)
        if _git(source, 'status', '--porcelain', work_tree=snapshot):
            _git(source, 'commit', '-q', '-m', f'{job["kind"]} {job_id} attempt {job["attempt"]}'
                 + (f' repair {repairs}' if repairs else ''), work_tree=snapshot)
        revision = _git(source, 'rev-parse', 'HEAD')
        code = self.ledger.allocate_version(job_id)
        name = f'{code}'
        self._step(job, 'building', 'compiling', 'Building the app.')
        gradle.remove_tree(work)  # a build's own folders too, whatever modes it left them in
        # A Gradle project's libraries are its own build files' business, resolved offline from the host's cache.
        libraries = [] if layout else self._libraries(snapshot, work, project_id, package)
        agent = None
        if profile.name == CLIENT_BACKEND:
            if backend is None:
                raise toolchain.StepFailed('toolchain', 'This builder has no agent container set up for app '
                                           'backends, so the app was not built.')
            agent = self._client_backend(snapshot, work, project, layout)
            if layout:
                unsigned, log = gradle.build(agent['client_source'], work, package, code, name, self.gradle,
                                             self.tools, cancelled, builder_files=agent['builder_files'])
            else:
                unsigned, log = toolchain.build(agent['client_source'], work, package, code, name, self.tools,
                                                cancelled, libraries, builder_files=agent['builder_files'],
                                                builder_packages=agent['packages'])
        elif layout:
            unsigned, log = gradle.build(snapshot, work, package, code, name, self.gradle, self.tools, cancelled)
        else:  # a plain app's build is called exactly as before Level 2
            unsigned, log = toolchain.build(snapshot, work, package, code, name, self.tools, cancelled, libraries)
        return note, snapshot, profile, revision, code, name, libraries, agent, unsigned, log

    def _host_release(self, project_id: str, code: int, release: str, url: str) -> tuple[str, float]:
        """Switch to the building agent's release; returns ``(app URL, when)`` once it answered its health check."""
        from .app_host import SetupFailed
        try:
            if self.ledger.profile(project_id) == CLIENT_SERVER:
                self.hosting.check_legacy(project_id)
            self.hosting.deploy_release(project_id, code, release, url)
        except SetupFailed as failed:
            if failed.step == 'switch' and '(release)' in failed.message:
                raise toolchain.StepFailed('compile', 'The app\'s backend release was not finished: its release '
                                           'folder has no start script, so the app was not delivered.') from None
            raise toolchain.StepFailed('toolchain', f'The app\'s backend could not be set up in its Chariot agent '
                                       f'container ({failed.step}), so the app was not delivered.', retryable=True,
                                       log=failed.message[:2000]) from None
        self.ledger.clear_server_setup(project_id)  # another release runs there now, until this one is delivered
        return self.ledger.agent_container(project_id)['appUrl'], self.ledger.clock()

    def _client_backend(self, snapshot: Path, work: Path, project: dict, layout: bool = False) -> dict:
        """An app whose backend the building agent set up (§7.7a): the contract against every delivered version's,
        and the client's tree with the builder's generated client files (the API client from the contract, sign-in,
        RoseInvite, RoseServer.URL). Nothing of backend/ is built here, or goes into the APK."""
        project_id, package = project['projectId'], project['packageName']
        try:
            api = contract.load(snapshot)
            canonical = contract.canonical_bytes(api.data)
            if len(canonical) > MAX_CONTRACT:
                raise contract.ContractRefused(f'contract/api.json is too large once normalised ({len(canonical)} '
                                               f'bytes; at most {MAX_CONTRACT}).', 'contract_invalid')
            delivered = [contract.parse(json.loads(text)) for text in self.ledger.contracts(project_id)]
            contract.check_update(delivered, api)
        except appprofile.ProfileRefused as refused:
            raise toolchain.StepFailed('dependency' if refused.dependency else 'compile', str(refused)) from None
        if self.codegen is None:
            raise toolchain.StepFailed('toolchain', 'This builder has no code generator for the app\'s API client, '
                                       'so the app was not built.')
        try:
            client_files = dict(self.codegen.client_files(api, package))
        except Exception:
            raise toolchain.StepFailed('toolchain', 'The builder could not generate the app\'s API client code.') \
                from None
        prefix = f'src/{package.replace(".", "/")}/rose/'
        if prefix + ROSE_SERVER in client_files or any(
                not isinstance(k, str) or not isinstance(v, str) or not k.startswith(prefix)
                or any(part in ('', '.', '..') for part in k[len(prefix):].split('/')) for k, v in client_files.items()):
            raise toolchain.StepFailed('toolchain', 'The builder\'s client code generator wrote outside its own '
                                       'folder.')
        url = self._hosted_url(project_id)
        client_files[prefix + ROSE_SERVER] = rose_server_java(package, url)
        if layout:  # the same Java files, in the Gradle application module's source set
            root = appprofile.client_root(gradle=True)
            client_files = {root + k[len('src/'):]: v for k, v in client_files.items()}
        client = work / 'client'
        shutil.copytree(snapshot, client, symlinks=True, ignore=client_tree_ignore(snapshot, appprofile.BACKEND_TOP))
        try:
            for path, text in client_files.items():
                target = client / path
                target.parent.mkdir(parents=True, exist_ok=True)
                with open(target, 'x', encoding='utf-8') as stream:
                    stream.write(text)
        except (OSError, ValueError, UnicodeError):
            raise toolchain.StepFailed('toolchain', 'The builder\'s client code generator wrote files that could not '
                                       'be written.') from None
        packages = set()
        for path, text in client_files.items():
            declared = re.match(r'\s*package\s+([A-Za-z_][\w.]*)\s*;', text) if path.endswith('.java') else None
            if declared and declared.group(1) != package and not declared.group(1).startswith(package + '.'):
                packages.add(declared.group(1))
        return {'client_source': client, 'builder_files': frozenset(client_files), 'packages': frozenset(packages),
                'url': url, 'contract': canonical.decode('utf-8')}

    def _hosted_url(self, project_id: str) -> str | None:
        """The app's hosted base URL from the injected hosting record, or None when it is not hosted yet. A record
        the generated client would refuse is the builder's failure, here, never the user's first tap. With
        ``hosting``, the app's agent container is recorded first (§7.7a)."""
        if self.hosting is not None:
            from .app_host import SetupFailed
            try:
                self.hosting.ensure(project_id)
            except SetupFailed as failed:
                raise toolchain.StepFailed('toolchain', f'No Chariot agent container could be set aside for this '
                                           f'app ({failed.step}).', retryable=True, log=failed.message[:2000]) from None
        read = self.server_url
        if read is None and self.hosting is not None:  # the agent container just recorded is the hosting record
            read = self.ledger.backend_endpoint
        if read is None:
            return None
        try:
            url = read(project_id)
        except Exception:
            raise toolchain.StepFailed('toolchain', 'The builder could not read this app\'s hosting record.',
                                       retryable=True) from None
        if url is not None and not (isinstance(url, str) and HOSTED_URL.fullmatch(url)):
            raise toolchain.StepFailed('toolchain', 'This app\'s hosting record is not an https address the app '
                                       'can use, so the app was not built.')
        if url is None and self.hosting is not None:  # a hosted app's client must know its server
            raise toolchain.StepFailed('toolchain', 'This app has no agent container recorded, so it was not built.',
                                       retryable=True)
        return url

    def _libraries(self, snapshot: Path, work: Path, project_id: str, package: str) -> list:
        """The verified libraries the app declares (none when it declares none). A coordinate the last delivered
        version used must resolve to the same bytes it was built with."""
        try:
            coordinates = dependencies.declared(snapshot)
            if not coordinates:
                return []
            framework = frozenset()
            if self.resolver is not None:
                try:
                    framework = dependencies.framework_classes(self.tools.android_jar)
                except Exception:
                    raise toolchain.StepFailed('toolchain', 'The builder could not read its Android platform '
                                               'classes.', retryable=True) from None
            return dependencies.resolve(coordinates, self.resolver, work / 'libs', package, framework,
                                        self.ledger.library_lock(project_id))
        except dependencies.Refused as refused:
            raise toolchain.StepFailed('compile' if refused.malformed else 'dependency', str(refused)) from None
        except OSError:  # the job's own disk, not the library: retryable
            raise toolchain.StepFailed('internal', 'The builder could not prepare the app\'s libraries.',
                                       retryable=True) from None

    def _back_up_key(self, keys: Path, project_id: str, package: str):
        """Once per project: the injected backup must return a receipt before the first delivery."""
        if self.ledger.key_backup(project_id) is not None:
            return
        if self.backup is None:
            if not self.require_key_backup:
                return  # owner accepted local key persistence only; never invent a backup receipt
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
            starter = self.ledger.starter(project['projectId'])
            if starter is not None and (self.gradle is None or self.skeleton is None):
                raise toolchain.StepFailed('toolchain', 'This app was asked for from a Rose starter, which this '
                                           'builder cannot make (no starters or no Gradle toolchain).')
            if self.gradle is not None:
                if self.skeleton is not None:
                    try:
                        self.skeleton(source, project['packageName'], project['name'], starter)
                    except ValueError as error:  # the catalog no longer has it; the next attempt starts afresh
                        raise toolchain.StepFailed('toolchain', f'This app\'s Rose starter cannot be made: {error}') \
                            from None
                else:
                    gradle.write_skeleton(source, project['packageName'], project['name'])
            else:
                template.write_skeleton(source, project['packageName'], project['name'])
            _git(source, 'add', '-A', '-f')
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
            recorded_ignore = path.name == '.gitignore' and stat.S_ISREG(mode)  # a Gradle project's own, as recorded
            if (template.git_name(path.name) and not recorded_ignore) or not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
                # whatever git left (metadata, FIFOs, sockets, links), never into the next attempt
                path.unlink() if not stat.S_ISDIR(mode) else shutil.rmtree(path)
