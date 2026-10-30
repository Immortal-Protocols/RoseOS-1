#!/usr/bin/env python3
"""Isolated, pinned Android build-tools r35 staging for Models signing (MODELS-SDK-PREP-001).

Prepares exactly Google's build-tools_r35_linux.zip (the archive scripts/setup-android-sdk.sh pins) in its own cache,
`<root>/downloads/build-tools_r35_linux.zip` plus `<root>/build-tools/android-15`, so scripts/host-models-sign.py can
prove its signing tools before any signer login. The shared Android SDK is never written.

  prepare   reuse a complete cache after full verification, or stage a new one: download the fixed URL once (bounded
            time and bytes, normal TLS/proxy/CA verification, HTTPS redirects only, no retry), verify the pinned
            SHA-256 BEFORE extraction, validate every member (names, duplicates, types, modes, links, sizes), extract
            without following links, prove the whole extracted tree equals the archive, then commit the cache with
            one rename. Unknown or wrong existing files are refused, never overwritten or deleted; only this run's
            own staging directory is removed on failure. Nothing downloaded is executed.

host-models-sign.py calls prepare() only from its preflight, under the shared builder lock and before any login. Its
signed run only revalidates the committed cache (archive_proof) and never reaches this module or the network.
"""
import ctypes
import errno
import hashlib
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import tempfile
import time
import urllib.error
import urllib.request
import zipfile

URL = 'https://dl.google.com/android/repository/build-tools_r35_linux.zip'
SHA256 = 'bd3a4966912eb8b30ed0d00b0cda6b6543b949d5ffe00bea54c04c81e1561d88'
ARCHIVE_NAME = 'build-tools_r35_linux.zip'
PREFIX = 'android-15'
INSTALLED = 'build-tools/android-15'
SHARED_SDK = '/home/pixelbuild/rose-actions/tools/android-sdk'
PINS = {'url': URL, 'sha256': SHA256}


class Refused(Exception):
    """A fixed category plus nonsecret detail."""


# ---------------------------------------------------------------- configuration and paths

def settings(cfg, pins=PINS, shared=SHARED_SDK):
    """The sdk block of os/models-signing/models-sign.json, checked against the fixed pins and layout."""
    sdk = cfg['sdk']
    prep = sdk.get('prepare') or {}
    root = Path(sdk['root'])
    if prep.get('url') != pins['url'] or sdk.get('archiveSha256') != pins['sha256']:
        raise Refused('sdk_config_unpinned: only the pinned build-tools_r35_linux.zip URL and digest are accepted')
    if (sdk.get('archivePrefix') != PREFIX or sdk.get('installedDirectory') != INSTALLED
            or Path(sdk['archive']) != root/'downloads'/ARCHIVE_NAME or not root.is_absolute()
            or sorted(sdk.get('members') or []) != ['aapt', 'lib/apksigner.jar']):
        raise Refused('sdk_config_layout: unexpected cache layout')
    shared = Path(shared)
    if root == shared or shared in root.parents or root in shared.parents:
        raise Refused('sdk_config_shared: the isolated cache must not overlap the shared Android SDK')
    limits = {k: int(prep[k]) for k in ('deadlineSeconds', 'archiveBytes', 'archiveMembers', 'memberBytes', 'expandedBytes')}
    if not (0 < limits['deadlineSeconds'] <= 120 and 0 < limits['archiveBytes'] <= 256 << 20):
        raise Refused('sdk_config_limits: deadline must be at most 120 s and the archive at most 256 MiB')
    return root, limits


def _safe_ancestors(path, uid):
    """Every existing ancestor is a real directory (no symlink is followed); the parent is ours or root's and not
    writable by group/other."""
    parent = path.parent
    for ancestor in [parent] + list(parent.parents):
        try:
            info = os.lstat(ancestor)
        except FileNotFoundError:
            raise Refused(f'sdk_parent_missing: {ancestor}') from None
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise Refused(f'sdk_parent_unsafe: {ancestor} is a symlink or not a directory')
    info = os.lstat(parent)
    if info.st_uid not in (uid, 0) or info.st_mode & 0o022:
        raise Refused('sdk_parent_unsafe: the cache parent must be owner-controlled and not group/other writable')


def _check_owned_dir(path, uid):
    info = os.lstat(path)
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise Refused(f'sdk_cache_unsafe: {path.name} is a symlink or not a directory')
    if info.st_uid != uid or info.st_mode & 0o022:
        raise Refused(f'sdk_cache_unsafe: {path.name} has the wrong owner or is group/other writable')


# ---------------------------------------------------------------- archive inspection

def _sha256(path, limit=None):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    digest, total = hashlib.sha256(), 0
    with os.fdopen(fd, 'rb') as f:
        if not stat.S_ISREG(os.fstat(f.fileno()).st_mode):
            raise Refused(f'sdk_cache_unsafe: {Path(path).name} is not a regular file')
        for block in iter(lambda: f.read(8 << 20), b''):
            total += len(block)
            if limit and total > limit:
                raise Refused('sdk_archive_too_large')
            digest.update(block)
    return digest.hexdigest()


def inspect_archive(archive_path, limits):
    """Validated plan of the archive: {relative path: ('dir',) | ('file', bytes, sha256, executable) | ('link', target)}.
    Reads every member (bounded) so sizes and hashes are the real decompressed values, not header claims."""
    plan, total = {}, 0
    try:
        archive = zipfile.ZipFile(archive_path)
    except zipfile.BadZipFile:
        raise Refused('sdk_archive_unsafe: not a zip archive') from None
    with archive:
        infos = archive.infolist()
        if len(infos) > limits['archiveMembers']:
            raise Refused('sdk_archive_unsafe: too many members')
        for info in infos:
            name = info.filename
            if '\\' in name or '\x00' in name or name.startswith('/'):
                raise Refused(f'sdk_archive_unsafe: unusable name {name[:60]!r}')
            parts = PurePosixPath(name).parts
            if not parts or parts[0] != PREFIX or any(p in ('..', '.', '') for p in parts) or \
                    name.rstrip('/') != '/'.join(parts):
                raise Refused(f'sdk_archive_unsafe: unexpected member {name[:60]!r}')
            rel = '/'.join(parts[1:])
            mode = info.external_attr >> 16
            if not rel:
                continue
            if rel in plan:
                raise Refused(f'sdk_archive_unsafe: duplicate member {rel[:60]!r}')
            if mode & (stat.S_ISUID | stat.S_ISGID | stat.S_ISVTX):
                raise Refused(f'sdk_archive_unsafe: {rel[:60]!r} has setuid/setgid/sticky bits')
            kind = stat.S_IFMT(mode)
            if info.is_dir():
                if kind not in (0, stat.S_IFDIR):
                    raise Refused(f'sdk_archive_unsafe: {rel[:60]!r} is a directory with a special type')
                plan[rel] = ('dir',)
                continue
            if kind not in (0, stat.S_IFREG, stat.S_IFLNK):
                raise Refused(f'sdk_archive_unsafe: {rel[:60]!r} is a special file')
            if info.file_size > limits['memberBytes']:
                raise Refused(f'sdk_archive_unsafe: {rel[:60]!r} exceeds the member limit')
            data = _read_bounded(archive, info, limits['memberBytes'])
            total += len(data)
            if total > limits['expandedBytes']:
                raise Refused('sdk_archive_unsafe: expanded size exceeds the limit')
            if kind == stat.S_IFLNK:
                target = data.decode('utf-8', 'strict')
                resolved = os.path.normpath(os.path.join(os.path.dirname(rel), target))
                if not target or target.startswith('/') or resolved == '..' or resolved.startswith('../'):
                    raise Refused(f'sdk_archive_unsafe: link {rel[:60]!r} leaves the tree')
                plan[rel] = ('link', target)
            else:
                plan[rel] = ('file', data, hashlib.sha256(data).hexdigest(), bool(mode & 0o100))
    links = {rel for rel, entry in plan.items() if entry[0] == 'link'}
    for rel in plan:
        parents = PurePosixPath(rel).parents
        if any(str(p) in links for p in parents if str(p) != '.'):
            raise Refused(f'sdk_archive_unsafe: {rel[:60]!r} lies under a link member')
    for member in ('lib/apksigner.jar', 'aapt'):
        if plan.get(member, ('',))[0] != 'file':
            raise Refused(f'sdk_archive_unsafe: required member {member} is not a regular file')
    return plan


def _read_bounded(archive, info, limit):
    with archive.open(info) as source:
        data = source.read(limit + 1)
    if len(data) > limit:
        raise Refused(f'sdk_archive_unsafe: {info.filename[:60]!r} expands beyond the member limit')
    return data


def tree_manifest(installed):
    """('file', sha256, executable) / ('link', target) for every non-directory entry; refuses special files."""
    observed = {}
    for dirpath, dirnames, filenames in os.walk(installed, followlinks=False):
        for name in dirnames + filenames:
            path = Path(dirpath)/name
            rel = str(path.relative_to(installed))
            info = os.lstat(path)
            if stat.S_ISLNK(info.st_mode):
                observed[rel] = ('link', os.readlink(path))
            elif stat.S_ISREG(info.st_mode):
                observed[rel] = ('file', _sha256(path), bool(info.st_mode & stat.S_IXUSR))
            elif not stat.S_ISDIR(info.st_mode):
                raise Refused(f'sdk_cache_unsafe: special file {rel[:60]!r}')
    return observed


def expected_manifest(plan):
    return {rel: (('link', e[1]) if e[0] == 'link' else ('file', e[2], e[3]))
            for rel, e in plan.items() if e[0] != 'dir'}


def prove_cache(root, limits, uid, pins=PINS):
    """Full proof of a committed or staged cache: ownership, archive digest, safe archive, and installed tree equal
    to the archive file by file. Returns tool identities derived from the verified archive members."""
    archive, installed = root/'downloads'/ARCHIVE_NAME, root/INSTALLED
    for directory in (root, root/'downloads', root/'build-tools', installed):
        _check_owned_dir(directory, uid)
    if sorted(os.listdir(root)) != ['build-tools', 'downloads'] or os.listdir(root/'downloads') != [ARCHIVE_NAME] \
            or os.listdir(root/'build-tools') != ['android-15']:
        raise Refused('sdk_cache_unexpected: the cache holds files outside the fixed layout')
    if _sha256(archive, limits['archiveBytes']) != pins['sha256']:
        raise Refused('sdk_cache_mismatch: the cached archive is not the pinned build-tools_r35_linux.zip')
    plan = inspect_archive(archive, limits)
    observed, expected = tree_manifest(installed), expected_manifest(plan)
    if observed != expected:
        drift = sorted(set(observed) ^ set(expected)) + sorted(k for k in set(observed) & set(expected) if observed[k] != expected[k])
        raise Refused(f'sdk_cache_mismatch: installed tree differs from the archive ({drift[:5]})')
    return {'archiveSha256': pins['sha256'], 'apksignerSha256': plan['lib/apksigner.jar'][2],
            'aaptSha256': plan['aapt'][2], 'files': len(expected),
            'expandedBytes': sum(len(e[1]) for e in plan.values() if e[0] == 'file')}


# ---------------------------------------------------------------- download and extraction

class _HttpsOnlyRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not newurl.startswith('https://'):
            raise Refused('sdk_download_failed: redirect away from HTTPS')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def https_download(url, out, deadline_seconds, max_bytes, clock=time.monotonic):
    """One bounded GET with the platform's normal TLS, CA and proxy settings. No retry. Writes into the open file."""
    opener = urllib.request.build_opener(_HttpsOnlyRedirect)
    start, total = clock(), 0
    try:
        # Per-read socket timeout is short so the whole transfer stays close to the deadline.
        with opener.open(url, timeout=min(10, deadline_seconds)) as response:
            while True:
                if clock() - start > deadline_seconds:
                    raise Refused('sdk_download_timeout: the download exceeded its deadline')
                block = response.read(1 << 20)
                if not block:
                    return total
                total += len(block)
                if total > max_bytes:
                    raise Refused('sdk_download_too_large: the archive exceeded its byte limit')
                out.write(block)
    except urllib.error.HTTPError as error:
        kind = 'denied' if error.code in (401, 403, 451) else 'failed'
        raise Refused(f'sdk_download_{kind}: HTTP {error.code} for the pinned archive (not retried)') from None
    except TimeoutError:
        raise Refused('sdk_download_timeout: the server stopped responding (not retried)') from None
    except OSError as error:
        raise Refused(f'sdk_download_failed: {type(error).__name__} (not retried)') from None


def _rename_noreplace(src, dst):
    """Atomic directory commit that never replaces an existing destination (renameat2 RENAME_NOREPLACE). Falls back
    to a recheck plus rename, which the caller's shared builder lock serializes."""
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        renameat2 = libc.renameat2
    except (OSError, AttributeError):
        renameat2 = None
    if renameat2 is not None:
        at_fdcwd, noreplace = -100, 1
        if renameat2(at_fdcwd, os.fsencode(src), at_fdcwd, os.fsencode(dst), noreplace) == 0:
            return
        code = ctypes.get_errno()
        if code == errno.EEXIST:
            raise Refused('sdk_cache_collision: the cache appeared while staging; nothing was replaced')
        if code not in (errno.ENOSYS, errno.EINVAL):
            raise Refused(f'sdk_commit_failed: {errno.errorcode.get(code, code)}')
    if os.path.lexists(dst):
        raise Refused('sdk_cache_collision: the cache appeared while staging; nothing was replaced')
    os.rename(src, dst)


def _extract(plan, installed):
    """Writes the validated plan below a fresh directory: O_EXCL|O_NOFOLLOW files, links last, nothing followed."""
    os.mkdir(installed, 0o755)
    for rel, entry in sorted(plan.items()):
        if entry[0] == 'dir':
            os.makedirs(installed/rel, 0o755, exist_ok=True)
    for rel, entry in sorted(plan.items()):
        if entry[0] != 'file':
            continue
        path = installed/rel
        os.makedirs(path.parent, 0o755, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o755 if entry[3] else 0o644)
        with os.fdopen(fd, 'wb') as f:
            os.fchmod(f.fileno(), 0o755 if entry[3] else 0o644)  # exact mode, independent of umask
            f.write(entry[1])
    for rel, entry in sorted(plan.items()):
        if entry[0] == 'link':
            os.makedirs((installed/rel).parent, 0o755, exist_ok=True)
            os.symlink(entry[1], installed/rel)


def prepare(cfg, transport=https_download, pins=PINS, shared=SHARED_SDK, uid=None):
    """Ensure the isolated cache exists and is proven. Returns the proof with 'reused' or 'prepared'."""
    uid = os.getuid() if uid is None else uid
    root, limits = settings(cfg, pins, shared)
    _safe_ancestors(root, uid)
    if os.path.lexists(root):
        try:
            proof = prove_cache(root, limits, uid, pins)
        except (Refused, OSError) as error:
            detail = str(error) if isinstance(error, Refused) else type(error).__name__
            raise Refused(f'sdk_cache_unexpected: an existing cache is not the verified pinned tree; it is left untouched '
                          f'({detail})') from None
        return dict(proof, state='reused')
    staging = Path(tempfile.mkdtemp(prefix=f'.{root.name}.staging-', dir=root.parent))
    committed = False
    try:
        os.chmod(staging, 0o755)
        (staging/'downloads').mkdir(0o755)
        (staging/'build-tools').mkdir(0o755)
        archive = staging/'downloads'/ARCHIVE_NAME
        fd = os.open(archive, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
        with os.fdopen(fd, 'wb') as out:
            transport(pins['url'], out, limits['deadlineSeconds'], limits['archiveBytes'])
            out.flush()
            os.fsync(out.fileno())
        if _sha256(archive, limits['archiveBytes']) != pins['sha256']:
            raise Refused('sdk_archive_mismatch: the downloaded archive is not the pinned digest; nothing was extracted')
        plan = inspect_archive(archive, limits)
        _extract(plan, staging/INSTALLED)
        proof = prove_cache(staging, limits, uid, pins)
        _rename_noreplace(staging, root)
        committed = True
        return dict(proof, state='prepared')
    finally:
        if not committed:
            shutil.rmtree(staging, ignore_errors=True)
