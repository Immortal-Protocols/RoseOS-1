"""The crate set a generated server is built against (docs/APP-BUILDER.md §7.6). Source only; nothing calls this yet.

A client-server app declares crates in rose-app.json (``server.crates``: exact ``name@version``). It never ships one,
and the builder never downloads one: crates come from an injected, offline ``CrateSource`` the builder host provides
(for example ``RecordedCrates``, a host-owned directory of ``.crate`` files and their sha256), as Level 1's JAR
libraries come from a ``dependencies.Resolver``. Populating that set is the host's job (blocker 6, Local Server
Worker), outside this module.

``vendor`` unpacks the whole set once into a cargo directory source, each crate checked first:
- its file is read without following a link, within size bounds, and must hash to the digest the set recorded;
- it is a gzip tar whose entries are plain files and folders only, all under ``<name>-<version>/``, with no absolute
  path, no ``.`` or ``..`` segment, no name twice and no control character, within bounds on entry count and on
  unpacked bytes (so a small archive cannot fill the disk);
- its own Cargo.toml names exactly that crate and version, so one crate cannot stand in for another.

Cargo then resolves the server's dependencies from that directory with the network off (rose.app_builder.server_build),
and ``check_lock`` holds the resulting Cargo.lock to the set and to every crate an earlier delivered version used: a
crate never changes under an app without its version changing.

What this does not do: decide which crates are safe. A crate's build script and procedural macros run while the
server compiles, like any server operator's dependencies. The boundary for that is build isolation (§5, blocker 3);
the host chooses its set.
"""
from __future__ import annotations

import fcntl
import gzip
import hashlib
import io
import json
import os
import re
import secrets
import shutil
import stat
import tarfile
import tomllib
import zlib
from dataclasses import dataclass
from pathlib import Path

SCHEMA_VERSION = 1
CRATE = re.compile(r'([A-Za-z][A-Za-z0-9_-]{0,63})@(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)'
                   r'(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?')
SHA256 = re.compile(r'[0-9a-f]{64}')
REGISTRY = 'registry+https://github.com/rust-lang/crates.io-index'  # what cargo records for a replaced crates.io
MAX_SET = 2000  # crates in one set
MAX_CRATE = 16 * 1024 * 1024  # bytes of one .crate file (crates.io accepts 10 MB)
MAX_UNPACKED = 128 * 1024 * 1024  # unpacked bytes of one crate
MAX_ENTRIES = 20000  # entries in one crate
MAX_PATH = 512
MAX_KEY = 64  # characters of a crate name, and of a version (the ledger records both)
CHECKSUM_FILE = '.cargo-checksum.json'
VENDOR_RECIPE = 1  # bump when vendor() lays crates out differently, so an older cached directory is never reused


class Refused(ValueError):
    """A crate the builder will not use. ``reason`` is a stable token (crate_unavailable, crate_changed,
    no_crate_source, crate_set_invalid); the message is safe to show the owner."""

    def __init__(self, message: str, reason: str):
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True)
class Crate:
    """What a crate source returns for one ``name@version``: a local .crate file and the sha256 it recorded."""
    name: str
    version: str
    path: Path
    sha256: str

    @property
    def key(self) -> str:
        return f'{self.name}@{self.version}'


@dataclass(frozen=True)
class Locked:
    """A crate a server was built with, as its Cargo.lock records it and the version's ledger entry keeps it."""
    name: str
    version: str
    sha256: str

    @property
    def key(self) -> str:
        return f'{self.name}@{self.version}'


class CrateSource:
    """Offline crate lookup. ``crates`` lists every crate the source provides (the whole set: cargo resolves a
    server's dependencies of dependencies from it). It must never download."""

    def crates(self) -> list[Crate]:
        raise NotImplementedError


class RecordedCrates(CrateSource):
    """A host-owned directory: ``index.json`` ``{"schemaVersion": 1, "crates": {"<name>@<version>": "<sha256>"}}``
    and one file ``<sha256>.crate`` per crate. No index is a set with nothing in it; an index that cannot be read,
    or that lists an entry this cannot use, is a broken set (ValueError, ``crate_set_invalid``), never a smaller one."""

    def __init__(self, root: Path):
        self.root = Path(root)

    def crates(self) -> list[Crate]:
        try:
            text = (self.root / 'index.json').read_text(encoding='utf-8')
        except FileNotFoundError:
            return []
        data = json.loads(text)  # anything unreadable raises, and _index reports the set as broken
        crates = data.get('crates') if isinstance(data, dict) and data.get('schemaVersion') == SCHEMA_VERSION else None
        if not isinstance(crates, dict):
            raise ValueError('index.json is not {"schemaVersion": 1, "crates": {...}}')
        out = []
        for key, digest in sorted(crates.items()):
            if not (isinstance(key, str) and _key_ok(key) and isinstance(digest, str) and SHA256.fullmatch(digest)):
                raise ValueError('index.json lists an entry without a valid name, version or digest')
            name, version = key.split('@', 1)
            out.append(Crate(name, version, self.root / f'{digest}.crate', digest))
        return out


def _key_ok(key: str) -> bool:
    if not CRATE.fullmatch(key):
        return False
    name, version = key.split('@', 1)
    return len(name) <= MAX_KEY and len(version) <= MAX_KEY


def check_declared(declared, source: CrateSource | None) -> dict[str, Crate]:
    """The set by ``name@version``, once every declared crate is known to be in it. Refused with
    ``no_crate_source`` when crates are declared and there is no set, ``crate_unavailable`` for one it lacks."""
    if source is None:
        if declared:
            raise Refused('The server declares crates, and this builder has no crate set.', 'no_crate_source')
        return {}
    by_key = _index(source)
    for key in declared:
        if key not in by_key:
            raise Refused(f'The crate {key} is not in this builder\'s crate set.', 'crate_unavailable')
    return by_key


def _index(source: CrateSource) -> dict[str, Crate]:
    try:
        crates = list(source.crates())
    except Exception:
        raise Refused('The builder\'s crate set could not be read.', 'crate_set_invalid') from None
    if len(crates) > MAX_SET:
        raise Refused(f'The builder\'s crate set has more than {MAX_SET} crates.', 'crate_set_invalid')
    by_key = {}
    for crate in crates:
        if not isinstance(crate, Crate) or not isinstance(crate.name, str) or not isinstance(crate.version, str) \
                or not _key_ok(crate.key) or not isinstance(crate.sha256, str) \
                or not SHA256.fullmatch(crate.sha256):
            raise Refused('The builder\'s crate set has an entry without a valid name, version or digest.',
                          'crate_set_invalid')
        if crate.key in by_key:
            raise Refused(f'The builder\'s crate set lists {crate.key} twice.', 'crate_set_invalid')
        by_key[crate.key] = crate
    return by_key


def vendor(source: CrateSource, cache: Path) -> Path:
    """A cargo directory source holding every crate of the set, each checked (module docstring), built once per set
    under ``cache`` and then reused read-only. A damaged crate refuses the whole set (``crate_set_invalid``): the
    host fixes its set rather than the builder quietly building without it."""
    by_key = _index(source)
    digest = hashlib.sha256(json.dumps([VENDOR_RECIPE, sorted((k, c.sha256) for k, c in by_key.items())])
                            .encode()).hexdigest()
    cache = Path(cache)
    cache.mkdir(mode=0o700, parents=True, exist_ok=True)
    final = cache / f'vendor-{digest}'
    if _valid_vendor(final, digest):
        return final
    # One builder of a set at a time, threads and processes alike: each open of the lock file is its own flock.
    fd = os.open(cache / f'.lock-{digest}', os.O_RDWR | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        if _valid_vendor(final, digest):  # another build finished it while this one waited
            return final
        _discard(final)  # a folder without its marker is a build that never finished
        for left in cache.glob(f'.staging-{digest}-*'):  # only the lock holder writes one: these were abandoned
            _discard(left)
        staging = cache / f'.staging-{digest}-{secrets.token_hex(8)}'
        staging.mkdir(mode=0o700)
        try:
            for key in sorted(by_key):
                _unpack(by_key[key], staging)
            (staging / '.rose-vendor').write_text(digest, encoding='utf-8')
            _read_only(staging)
            os.rename(staging, final)
        except BaseException:
            _discard(staging)
            raise
    finally:
        os.close(fd)  # releases the lock
    return final


def _valid_vendor(path: Path, digest: str) -> bool:
    try:
        st = os.lstat(path)
        return stat.S_ISDIR(st.st_mode) and (path / '.rose-vendor').read_text(encoding='utf-8') == digest
    except (OSError, UnicodeDecodeError):
        return False


def _discard(path: Path):
    if not os.path.lexists(path):
        return
    if os.path.islink(path) or not os.path.isdir(path):
        os.unlink(path)
        return
    for root, dirs, _files in os.walk(path):  # the vendored tree is read-only; make it removable first
        os.chmod(root, 0o700)
        for d in dirs:
            if not os.path.islink(os.path.join(root, d)):
                os.chmod(os.path.join(root, d), 0o700)
    shutil.rmtree(path)


def _read_only(root: Path):
    for here, dirs, files in os.walk(root, topdown=False):
        for f in files:
            os.chmod(os.path.join(here, f), 0o444)
        os.chmod(here, 0o555)


def _read_crate(crate: Crate) -> bytes:
    try:
        fd = os.open(crate.path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
    except OSError:
        raise Refused(f'The crate {crate.key} is missing from the builder\'s crate set.', 'crate_set_invalid') from None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise Refused(f'The crate {crate.key} is not a regular file in the crate set.', 'crate_set_invalid')
        chunks, size = [], 0
        while chunk := os.read(fd, 1 << 20):
            size += len(chunk)
            if size > MAX_CRATE:
                raise Refused(f'The crate {crate.key} is larger than this builder accepts.', 'crate_set_invalid')
            chunks.append(chunk)
        return b''.join(chunks)
    finally:
        os.close(fd)


class _Bounded:
    """A reader that refuses to yield more than ``limit`` bytes (gzip output, so a small file cannot fill the disk)."""

    def __init__(self, raw, limit: int, refuse):
        self.raw, self.left, self.refuse = raw, limit, refuse

    def read(self, n=-1):
        data = self.raw.read(self.left + 1 if n is None or n < 0 else min(n, self.left + 1))
        self.left -= len(data)
        if self.left < 0:
            self.refuse()
        return data


def _unpack(crate: Crate, vendor_dir: Path):
    data = _read_crate(crate)
    if hashlib.sha256(data).hexdigest() != crate.sha256:
        raise Refused(f'The crate {crate.key} does not match its recorded digest.', 'crate_set_invalid')

    def bad(why: str):
        raise Refused(f'The crate {crate.key} in the builder\'s crate set {why}.', 'crate_set_invalid')

    def too_big():
        bad(f'unpacks to more than {MAX_UNPACKED // (1024 * 1024)} MB')

    top = f'{crate.name}-{crate.version}'
    root = vendor_dir / top
    if os.path.lexists(root):
        bad('is unpacked twice')
    root.mkdir(mode=0o700)
    files: dict[str, str] = {}
    seen = set()
    count = written = 0
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(data)) as gz, \
                tarfile.open(fileobj=_Bounded(gz, MAX_UNPACKED + 1024 * 1024, too_big), mode='r|') as tar:
            for member in tar:
                count += 1
                if count > MAX_ENTRIES:
                    bad(f'has more than {MAX_ENTRIES} entries')
                name = member.name
                if len(name) > MAX_PATH or any(ord(c) < 0x20 or ord(c) == 0x7f for c in name) or '\\' in name:
                    bad('has an entry with an unusable name')
                parts = name.rstrip('/').split('/') if member.isdir() else name.split('/')
                if name.startswith('/') or parts[0] != top or any(p in ('', '.', '..') for p in parts):
                    bad('has an entry outside its own folder')
                rel = '/'.join(parts[1:])
                if rel in seen:
                    bad('has an entry twice')
                seen.add(rel)
                if not rel:
                    if not member.isdir():
                        bad('has a file where its folder should be')
                    continue
                if rel == CHECKSUM_FILE:
                    bad(f'carries its own {CHECKSUM_FILE}')
                target = root.joinpath(*parts[1:])
                if member.isdir():
                    target.mkdir(mode=0o700, parents=True, exist_ok=True)
                    continue
                if member.type not in (tarfile.REGTYPE, tarfile.AREGTYPE) or member.issparse():
                    bad('has a link, sparse or special file')  # a sparse file's holes are never read, only written
                target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                h = hashlib.sha256()
                fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), 0o600)
                try:
                    stream = tar.extractfile(member)
                    while chunk := stream.read(1 << 20):
                        written += len(chunk)
                        if written > MAX_UNPACKED:  # what reaches the disk, not only what the stream held
                            too_big()
                        h.update(chunk)
                        os.write(fd, chunk)
                finally:
                    os.close(fd)
                files[rel] = h.hexdigest()
    except Refused:
        raise
    except (tarfile.TarError, OSError, EOFError, gzip.BadGzipFile, ValueError, zlib.error):
        bad('is not a readable .crate archive')
    _check_manifest(root / 'Cargo.toml', crate, bad)
    (root / CHECKSUM_FILE).write_text(json.dumps({'files': files, 'package': crate.sha256}, sort_keys=True),
                                      encoding='utf-8')


def _check_manifest(path: Path, crate: Crate, bad):
    try:
        manifest = tomllib.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        bad('has no Cargo.toml')
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError, RecursionError):
        bad('has a Cargo.toml that cannot be read')
    package = manifest.get('package')
    if not isinstance(package, dict) or package.get('name') != crate.name or package.get('version') != crate.version:
        bad(f'has a Cargo.toml that does not name {crate.key}')


def check_lock(lock_text: str, by_key: dict[str, Crate], locked: dict[str, str] | None = None, *, root: str,
               declared=(), path_crates=frozenset()) -> list[Locked]:
    """The crates a server's Cargo.lock uses, once each is in the set with the set's digest and, for one an earlier
    delivered version used (``locked``: ``name@version`` -> sha256), with that version's digest (``crate_changed``).
    The only packages without a source are ``root`` (the server) and ``path_crates`` (the builder's own); anything
    else replaced a crate from outside the set. Every ``declared`` crate must be in the lock as the set's."""
    locked = locked or {}
    try:
        lock = tomllib.loads(lock_text)
    except (tomllib.TOMLDecodeError, RecursionError):
        raise Refused('The server\'s Cargo.lock could not be read.', 'crate_set_invalid') from None
    packages = lock.get('package', [])
    if not isinstance(packages, list):
        raise Refused('The server\'s Cargo.lock could not be read.', 'crate_set_invalid')
    out = []
    for package in packages:
        if not isinstance(package, dict) or not isinstance(package.get('name'), str) \
                or not isinstance(package.get('version'), str):
            raise Refused('The server\'s Cargo.lock could not be read.', 'crate_set_invalid')
        key = f'{package["name"]}@{package["version"]}'
        if 'source' not in package:
            if package['name'] != root and package['name'] not in path_crates:
                raise Refused(f'The crate {key} comes from somewhere other than this builder\'s crate set.',
                              'crate_unavailable')
            continue
        if package['source'] != REGISTRY:
            raise Refused(f'The crate {key} comes from somewhere other than this builder\'s crate set.',
                          'crate_unavailable')
        crate = by_key.get(key)
        if crate is None:
            raise Refused(f'The crate {key} is not in this builder\'s crate set.', 'crate_unavailable')
        if package.get('checksum') != crate.sha256:
            raise Refused(f'The crate {key} is not the one in this builder\'s crate set.', 'crate_changed')
        if key in locked and locked[key] != crate.sha256:
            raise Refused(f'The crate {key} is not the one this app\'s server was last built with, so it was not '
                          'used.', 'crate_changed')
        out.append(Locked(package['name'], package['version'], crate.sha256))
    used = {c.key for c in out}
    for key in declared:
        if key not in used:
            raise Refused(f'The crate {key} is declared and was not built from this builder\'s crate set.',
                          'crate_unavailable')
    return sorted(out, key=lambda c: (c.name, c.version))
