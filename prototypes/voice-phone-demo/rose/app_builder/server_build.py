"""Build a client-server app's server half (docs/APP-BUILDER.md §7.6). Source only; nothing calls this yet.

The builder writes the server's Cargo files itself (the writer cannot: rose.app_builder.appprofile refuses them) and
builds offline, from the snapshot and the host's crate set only:

1. Nothing runs without build isolation. The host supplies an ``Isolation`` that runs each command where it can write
   only the job's folder, read only the Rust toolchain and the crate set, and reach no network (blocker 3). Without
   one the build stops as ``build_isolation_unavailable``; nothing here provides or imitates one.
2. Every declared crate must be in the set (rose.app_builder.crates), which is unpacked, checked, into a cargo
   directory source that replaces crates.io.
3. ``cargo metadata --offline`` resolves the lock. It starts from the last delivered version's Cargo.lock, so a
   rebuild keeps the versions that version used and only adds or drops what the declaration changed.
4. The lock is held to the set and to every crate a delivered version used (``crate_changed``).
5. ``cargo build --offline --frozen --release --target <target>`` builds the one binary.

Cargo runs with a scrubbed environment: nothing inherited, HOME, CARGO_HOME, the target folder and TMPDIR all inside
the job, and the network off. Cargo also reads a ``.cargo/config.toml`` in any folder above where it runs, ahead of
CARGO_HOME's, so the build refuses to start under one, and the builder's own config is passed with ``--config``,
which outranks every file. The job's and the set's paths are remapped in what rustc compiles for the target; a
crate's procedural macro or build script runs on the build machine and could still write one into the binary, so a
binary that holds one is refused (``build_path_embedded``). Failures map to the job's categories with a stable
``reason``: rustc's diagnostics are read from cargo's JSON messages, by the package they belong to, so text in the
writer's code (a string, a ``compile_error!``) cannot choose the category. The summary is safe to show the owner and
the log has the job's paths redacted. The manifest and remaining ELF checks are the next slice.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from dataclasses import dataclass, field
from pathlib import Path

from . import crates as crate_set
from .toolchain import StepFailed, run

TARGET = 'x86_64-unknown-linux-musl'  # §7.7: a static x86-64 musl ELF
PACKAGE = 'rose-app-server'
EDITION = '2021'
ENTRY = 'rose/main.rs'
GENERATED = re.compile(r'rose/(?:[a-z][a-z0-9_]{0,63}/){0,4}[a-z][a-z0-9_]{0,63}\.rs')
TRIPLE = re.compile(r'[a-z0-9_]+(?:-[a-z0-9_.]+){1,3}')


class BuildFailed(StepFailed):
    """A server build that stopped; ``reason`` is a stable token next to the job's failure category."""

    def __init__(self, category: str, reason: str, summary: str, log: str = '', retryable: bool = False):
        super().__init__(category, summary, retryable=retryable, log=log)
        self.reason = reason


@dataclass
class RustTools:
    """A Rust toolchain's own folder (``bin/cargo``, ``bin/rustc``, ``lib/rustlib/<target>``), not rustup's
    proxies, which would read the builder's home. The host pins it (blocker 6)."""
    toolchain: Path
    target: str = TARGET
    timeout_s: float = 900.0

    @property
    def cargo(self) -> Path:
        return Path(self.toolchain) / 'bin/cargo'

    @property
    def rustc(self) -> Path:
        return Path(self.toolchain) / 'bin/rustc'

    def path(self) -> str:
        return f'{Path(self.toolchain) / "bin"}:/usr/bin:/bin'

    def available(self) -> str | None:
        """None when the toolchain is present, else what is missing (category toolchain)."""
        if not TRIPLE.fullmatch(self.target or ''):
            return 'The builder\'s Rust target is not a target triple.'
        for tool in (self.cargo, self.rustc):
            if not tool.is_file():
                return f'The builder\'s Rust toolchain is missing {tool.name}.'
        if not (Path(self.toolchain) / 'lib/rustlib' / self.target / 'lib').is_dir():
            return f'The builder\'s Rust toolchain has no standard library for {self.target}.'
        return None


class Isolation:
    """Runs one build command so that it can write only ``writable``, read only ``readonly`` (and the system's own
    libraries and linker), and reach no network, with no cargo configuration in any folder above ``writable``. The builder host provides it (docs/APP-BUILDER.md §5, blocker 3);
    returns the command and environment to run in its place."""

    def command(self, argv: list[str], env: dict[str, str], *, writable: Path,
                readonly: tuple[Path, ...]) -> tuple[list[str], dict[str, str]]:
        raise NotImplementedError


@dataclass(frozen=True)
class ServerBuild:
    elf: Path
    sha256: str
    size: int
    target: str
    cargo_lock: str
    crates: list = field(default_factory=list)  # crate_set.Locked, what the ledger records for the version

    def record(self) -> dict:
        """The version's server as rose.app_builder.ledger.deliver records it."""
        return {'sha256': self.sha256, 'size': self.size, 'target': self.target}


def cargo_toml(declared) -> str:
    """The server's Cargo.toml: one binary at the builder's entry point, exact versions of the declared crates,
    no build script and no target found by Cargo's own conventions (the writer's files are modules, nothing else)."""
    lines = ['[package]', f'name = "{PACKAGE}"', 'version = "0.0.0"', f'edition = "{EDITION}"', 'publish = false',
             'build = false', 'autolib = false', 'autobins = false', 'autoexamples = false', 'autotests = false',
             'autobenches = false', '', '[[bin]]', f'name = "{PACKAGE}"', f'path = "{ENTRY}"', '', '[dependencies]']
    for key in declared:
        if not crate_set.CRATE.fullmatch(key):
            raise BuildFailed('compile', 'profile_invalid', f'{key!r} is not an exact crate version.')
        name, version = key.split('@', 1)
        lines.append(f'{name} = "={version}"')
    lines += ['', '[profile.release]', 'codegen-units = 1', 'incremental = false', 'debug = false',
              'strip = "debuginfo"', '', '[workspace]', '']
    return '\n'.join(lines)


def _toml_string(path: Path) -> str:
    return json.dumps(str(path))  # a JSON string is a TOML basic string for these (paths the builder made)


def cargo_config(vendor: Path | None) -> str:
    """CARGO_HOME's config.toml: the network off and, with a crate set, crates.io replaced by it."""
    lines = ['[net]', 'offline = true', '', '[term]', 'color = "never"', 'progress.when = "never"', '']
    if vendor is not None:
        lines += ['[source.crates-io]', 'replace-with = "rose-crates"', '', '[source.rose-crates]',
                  f'directory = {_toml_string(vendor)}', '']
    return '\n'.join(lines)


def build(snapshot: Path, work: Path, declared, source: crate_set.CrateSource | None, tools: RustTools | None,
          isolation: Isolation | None, cancelled=lambda: False, *, cache: Path, generated: dict[str, str],
          previous_lock: str | None = None, locked: dict[str, str] | None = None) -> ServerBuild:
    """Build ``snapshot``'s server into ``work`` (a fresh job folder). ``generated`` holds the builder's own files
    (``rose/main.rs``, the entry point, and anything it uses); ``previous_lock`` is the last delivered version's
    Cargo.lock and ``locked`` the crates delivered versions used (rose.app_builder.ledger)."""
    if isolation is None:
        raise BuildFailed('toolchain', 'build_isolation_unavailable',
                          'The server was not built: this builder has no build isolation set up.')
    missing = tools.available() if tools is not None else 'The builder has no Rust toolchain.'
    if missing:
        raise BuildFailed('toolchain', 'rust_toolchain_unavailable', missing)
    if ENTRY not in generated or any(not isinstance(k, str) or not GENERATED.fullmatch(k) for k in generated):
        raise ValueError('generated files are rose/*.rs and include rose/main.rs')
    declared = list(declared)
    try:
        by_key = crate_set.check_declared(declared, source)
        vendor = crate_set.vendor(source, cache) if source is not None else None
    except crate_set.Refused as refused:
        raise _refused(refused) from None
    work = Path(work)
    for folder in (work / 'server').resolve().parents:
        if any(os.path.lexists(folder / '.cargo' / name) for name in ('config', 'config.toml')):
            raise BuildFailed('toolchain', 'build_isolation_unavailable', 'The server was not built: a cargo '
                              'configuration in a folder above the job would apply to it.')
    server, home, cargo_home, target_dir, tmp = (work / n for n in ('server', 'home', 'cargo-home', 'target', 'tmp'))
    for folder in (server, home, cargo_home, target_dir, tmp):
        folder.mkdir(mode=0o700, parents=True)
    _copy_tree(Path(snapshot) / 'server/src', server / 'src')
    for name, text in generated.items():
        path = server / name
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        _write(path, text)
    _write(server / 'Cargo.toml', cargo_toml(declared))
    if previous_lock is not None:
        _write(server / 'Cargo.lock', previous_lock)
    _write(cargo_home / 'config.toml', cargo_config(vendor))
    remap = [f'--remap-path-prefix={work}=/rose-build']
    if vendor is not None:
        remap.append(f'--remap-path-prefix={vendor}=/rose-crates')
    env = {'PATH': tools.path(), 'HOME': str(home), 'CARGO_HOME': str(cargo_home), 'CARGO_TARGET_DIR': str(target_dir),
           'TMPDIR': str(tmp), 'LANG': 'C.UTF-8', 'RUSTC': str(tools.rustc), 'CARGO_NET_OFFLINE': 'true',
           'CARGO_INCREMENTAL': '0', 'CARGO_TERM_COLOR': 'never', 'CARGO_ENCODED_RUSTFLAGS': '\x1f'.join(remap)}
    readonly = (Path(tools.toolchain),) + ((vendor,) if vendor is not None else ())
    redact = (work, Path(tools.toolchain)) + ((vendor,) if vendor is not None else ())
    manifest = str(server / 'Cargo.toml')

    def cargo(*args):
        argv, wrapped = isolation.command([str(tools.cargo), '--config', str(cargo_home / 'config.toml'), *args],
                                          dict(env), writable=work, readonly=readonly)
        try:
            return run(argv, server, home, tools, cancelled, 'cargo', redact=redact, env=wrapped)
        except StepFailed as failed:
            if failed.category != 'cargo':
                raise  # cancelled or timeout, as the Android tools report them
            raise classify(failed.log) from None

    cargo('metadata', '--offline', '--format-version', '1', '--manifest-path', manifest)
    try:
        lock_text = (server / 'Cargo.lock').read_text(encoding='utf-8')
    except (OSError, UnicodeDecodeError):
        raise BuildFailed('toolchain', 'rust_toolchain_unavailable', 'Cargo did not write the server\'s lock.') \
            from None
    try:
        used = crate_set.check_lock(lock_text, by_key, locked, root=PACKAGE, declared=declared)
    except crate_set.Refused as refused:
        raise _refused(refused) from None
    cargo('build', '--offline', '--frozen', '--release', '--target', tools.target, '--message-format', 'json',
          '--manifest-path', manifest)
    elf = target_dir / tools.target / 'release' / PACKAGE
    try:
        st = os.lstat(elf)
    except OSError:
        st = None
    if st is None or not stat.S_ISREG(st.st_mode):
        raise BuildFailed('toolchain', 'rust_toolchain_unavailable', 'Cargo finished without the server binary.')
    if _holds_any(elf, [str(p).encode() for p in (work, Path(cache), *((vendor,) if vendor is not None else ()))]):
        raise BuildFailed('dependency', 'build_path_embedded', 'The server binary holds a path from the builder\'s '
                          'machine (a crate\'s macro or build script put it there), so it was not used.')
    return ServerBuild(elf, _sha256(elf), st.st_size, tools.target, lock_text, used)


def _holds_any(path: Path, needles: list[bytes]) -> bool:
    keep = max(len(n) for n in needles) - 1
    tail = b''
    with open(path, 'rb') as f:
        while chunk := f.read(1 << 20):
            data = tail + chunk
            if any(n in data for n in needles):
                return True
            tail = data[-keep:] if keep else b''
    return False


def _refused(refused: crate_set.Refused) -> BuildFailed:
    category = 'toolchain' if refused.reason == 'crate_set_invalid' else 'dependency'
    return BuildFailed(category, refused.reason, str(refused))


# Cargo's own wording. Only cargo's own lines and rustc's spanless messages are read as text: rustc quotes the
# writer's source in its diagnostics, so a diagnostic counts by the package it belongs to, never by its words.
_CRATE_NAME = r'`([A-Za-z0-9_-]{1,64})`'
_TOOLCHAIN = re.compile(r"can't find crate for `(?:std|core)`|linker `[^`]*` not found|linking with `[^`]*` failed|"
                        r'may not be installed')


def _messages(log: str) -> tuple[list[str], list[dict]]:
    """Cargo's own text lines, and rustc's errors from cargo's JSON messages (``--message-format json``)."""
    text, errors = [], []
    for line in log.splitlines():
        if line.startswith('{'):
            try:
                message = json.loads(line)
            except ValueError:
                message = None
            if isinstance(message, dict) and 'reason' in message:
                body = message.get('message')
                if message['reason'] == 'compiler-message' and isinstance(body, dict) \
                        and str(body.get('level', '')).startswith('error') and isinstance(body.get('message'), str):
                    errors.append({'package': str(message.get('package_id', '')), 'message': body['message'],
                                   'spanless': not body.get('spans'), 'notes': ' '.join(
                                       str(c.get('message', '')) for c in body.get('children') or []
                                       if isinstance(c, dict)),
                                   'rendered': str(body.get('rendered') or body['message'])})
                continue
        text.append(line)
    return text, errors


def _package_name(package_id: str) -> tuple[str, bool]:
    """(crate name, is the server itself) from a cargo package id, in its current (``source#name@version``) or
    older (``name version (source)``) form."""
    if '#' in package_id:
        source, _, rest = package_id.partition('#')
        name = rest.rsplit('@', 1)[0] if '@' in rest else source.rstrip('/').rsplit('/', 1)[-1]
    else:
        name, _, rest = package_id.partition(' ')
        source = rest.rsplit('(', 1)[-1]
    return name, source.startswith('path+') and name == PACKAGE


def classify(log: str) -> BuildFailed:
    """The job's failure for a cargo run that failed, from what cargo printed."""
    text, errors = _messages(log)
    shown = '\n'.join(text + [e['rendered'] for e in errors])[-4000:]
    cargo_text = '\n'.join(text)
    for error in errors:
        if error['spanless'] and _TOOLCHAIN.search(error['message'] + ' ' + error['notes']):
            return BuildFailed('toolchain', 'rust_toolchain_unavailable',
                               'The builder\'s Rust toolchain could not build for its target.', log=shown)
    for error in errors:
        name, own = _package_name(error['package'])
        if not own:
            shown_name = name if re.fullmatch(r'[A-Za-z0-9_-]{1,64}', name) else 'a crate'
            return BuildFailed('dependency', 'crate_compile', f'The crate {shown_name} did not compile.', log=shown)
    if errors:
        return BuildFailed('compile', 'server_compile', 'The server\'s Rust code did not compile.', log=shown)
    if m := re.search(r'no matching package named ' + _CRATE_NAME, cargo_text):
        return BuildFailed('dependency', 'crate_unavailable',
                           f'The crate {m.group(1)} is not in this builder\'s crate set.', log=shown)
    if m := re.search(r'failed to select a version for (?:the requirement )?`([A-Za-z0-9_-]{1,64})[` ]', cargo_text):
        return BuildFailed('dependency', 'crate_unavailable',
                           f'No version of the crate {m.group(1)} in this builder\'s crate set fits.', log=shown)
    if re.search(r'checksum for `[^`]*` changed between lock files|the listed checksum of .* has changed', cargo_text):
        return BuildFailed('dependency', 'crate_changed',
                           'A crate is not the one this server\'s lock names, so it was not used.', log=shown)
    if m := re.search(r'failed to run custom build command for `([A-Za-z0-9_-]{1,64}) ', cargo_text):
        return BuildFailed('dependency', 'crate_compile', f'The crate {m.group(1)}\'s build script failed.', log=shown)
    if _TOOLCHAIN.search(cargo_text):
        return BuildFailed('toolchain', 'rust_toolchain_unavailable',
                           'The builder\'s Rust toolchain could not build for its target.', log=shown)
    if m := re.search(r'could not compile `([A-Za-z0-9_-]{1,64})`', cargo_text):
        if m.group(1) == PACKAGE:
            return BuildFailed('compile', 'server_compile', 'The server\'s Rust code did not compile.', log=shown)
        return BuildFailed('dependency', 'crate_compile', f'The crate {m.group(1)} did not compile.', log=shown)
    return BuildFailed('compile', 'server_build', 'The server did not build.', log=shown)


def _copy_tree(src: Path, dst: Path):
    """The writer's server/src, plain files and folders only (rose.app_builder.appprofile has checked the tree)."""
    dst.mkdir(mode=0o700)
    for here, dirs, files in os.walk(src):
        rel = Path(here).relative_to(src)
        for d in dirs:
            if not stat.S_ISDIR(os.lstat(Path(here) / d).st_mode):
                raise BuildFailed('compile', 'layout_invalid', f'server/src/{rel / d} is not a plain folder.')
            (dst / rel / d).mkdir(mode=0o700)
        for f in files:
            path = Path(here) / f
            try:
                fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
            except OSError:
                raise BuildFailed('compile', 'layout_invalid', f'server/src/{rel / f} is not a plain file.') from None
            try:
                if not stat.S_ISREG(os.fstat(fd).st_mode):
                    raise BuildFailed('compile', 'layout_invalid', f'server/src/{rel / f} is not a plain file.')
                with os.fdopen(os.dup(fd), 'rb') as stream:
                    data = stream.read()
            finally:
                os.close(fd)
            out = os.open(dst / rel / f, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(out, 'wb') as stream:
                stream.write(data)


def _write(path: Path, text: str):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as stream:
        stream.write(text)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()
