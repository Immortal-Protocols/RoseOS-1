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
5. ``cargo build --offline --frozen --release --target <target>`` builds the one binary, statically, and it must
   be a static x86-64 Linux executable (rose.app_builder.server_manifest.check_elf).

Cargo runs with a scrubbed environment: nothing inherited, HOME, CARGO_HOME, the target folder and TMPDIR all inside
the job, and the network off. Cargo also reads a ``.cargo/config.toml`` in any folder above where it runs, ahead of
CARGO_HOME's, so the build refuses to start under one, and the builder's own config is passed with ``--config``,
which outranks every file. The job's and the set's paths are remapped in what rustc compiles for the target; a
crate's procedural macro or build script runs on the build machine and could still write one into the binary, so a
binary that holds one is refused (``build_path_embedded``). Failures map to the job's categories with a stable
``reason``: rustc's diagnostics are read from cargo's JSON messages, by the package they belong to, so text in the
writer's code (a string, a ``compile_error!``) cannot choose the category. The summary is safe to show the owner and
the log has the job's paths redacted.
"""
from __future__ import annotations

import json
import os
import re
import stat
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from . import crates as crate_set
from . import server_manifest
from .toolchain import StepFailed, run

TARGET = 'x86_64-unknown-linux-musl'  # §7.7: a static x86-64 musl ELF
PACKAGE = 'rose-app-server'
RUNTIME = 'rose-app-runtime'  # the builder's own rose/meta.rs names it; a host injects it as a path crate
ADAPTER = 'rose-app-runtime-signin'  # the generated rose/main.rs starts the server through it (codegen)
BUILDER_CRATES = (RUNTIME, ADAPTER)  # what the builder's own generated code names, so a host must inject
EDITION = '2021'
ENTRY = 'rose/main.rs'
ASSETS = 'rose-assets'  # the build's copy of the writer's server/assets, next to rose/ and src/
MAX_ASSETS = 1024  # files in server/assets
MAX_ASSET_BYTES = 64 * 1024 * 1024  # all of them together, embedded in the binary
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
    libraries and linker), and reach no network, with no cargo configuration in any folder above ``writable``. The
    builder host provides it (docs/APP-BUILDER.md §5, blocker 3).

    The failure contract: a build that fails because of the isolation (it could not start, or the kernel refused a
    namespace or a mount) is the host's, never the app's. ``command`` raising, or its program missing, stops the
    build as ``build_isolation_unavailable``; ``failed`` saying a non-zero exit was its own stops it as
    ``build_isolation_failed``. Either is category ``toolchain``. A failure no one claims and that shows no compiler
    error in the app's own code is also ``toolchain``: the app's code is blamed only on rustc's own word."""

    def command(self, argv: list[str], env: dict[str, str], *, writable: Path,
                readonly: tuple[Path, ...]) -> tuple[list[str], dict[str, str]]:
        """The command and environment to run in place of ``argv`` and ``env``."""
        raise NotImplementedError

    def failed(self, returncode: int, output: str) -> bool:
        """Whether a run that exited with ``returncode`` (negative: killed by that signal) and printed ``output``
        failed in the isolation itself rather than in the command it ran. The provider knows its own messages and
        exit statuses; the default claims nothing."""
        return False


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


PATH_CRATE = re.compile(r'[a-z][a-z0-9_-]{0,63}')


def cargo_toml(declared, path_crates: dict | None = None) -> str:
    """The server's Cargo.toml: one binary at the builder's entry point, exact versions of the declared crates,
    the builder's own path crates (the runtime, its adapter), no build script and no target found by Cargo's own
    conventions (the writer's files are modules, nothing else)."""
    lines = ['[package]', f'name = "{PACKAGE}"', 'version = "0.0.0"', f'edition = "{EDITION}"', 'publish = false',
             'build = false', 'autolib = false', 'autobins = false', 'autoexamples = false', 'autotests = false',
             'autobenches = false', '', '[[bin]]', f'name = "{PACKAGE}"', f'path = "{ENTRY}"', '', '[dependencies]']
    for key in declared:
        if not crate_set.CRATE.fullmatch(key):
            raise BuildFailed('compile', 'profile_invalid', f'{key!r} is not an exact crate version.')
        name, version = key.split('@', 1)
        lines.append(f'{name} = "={version}"')
    for name, path in sorted((path_crates or {}).items()):
        if not PATH_CRATE.fullmatch(name) or name == PACKAGE:
            raise ValueError(f'{name!r} is not a builder path crate name')
        lines.append(f'{name} = {{ path = {_toml_string(Path(path))} }}')
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
          previous_lock: str | None = None, locked: dict[str, str] | None = None,
          path_crates: dict[str, Path] | None = None, dependent_path_crates: dict[str, Path] | None = None) \
        -> ServerBuild:
    """Build ``snapshot``'s server into ``work`` (a fresh job folder). ``generated`` holds the builder's own files
    (``rose/main.rs``, the entry point, and anything it uses); ``previous_lock`` is the last delivered version's
    Cargo.lock and ``locked`` the crates delivered versions used (rose.app_builder.ledger). ``path_crates`` are the
    builder's own crates the server depends on (name -> folder: the runtime, its adapter), and
    ``dependent_path_crates`` the path crates those depend on (rose-signin): all are read-only to the build, remapped
    out of the binary, and the only packages besides the server allowed without a source."""
    if isolation is None:
        raise BuildFailed('toolchain', 'build_isolation_unavailable',
                          'The server was not built: this builder has no build isolation set up.')
    missing = tools.available() if tools is not None else 'The builder has no Rust toolchain.'
    if missing:
        raise BuildFailed('toolchain', 'rust_toolchain_unavailable', missing)
    if ENTRY not in generated or any(not isinstance(k, str) or not GENERATED.fullmatch(k) for k in generated):
        raise ValueError('generated files are rose/*.rs and include rose/main.rs')
    declared = list(declared)
    # Canonical paths throughout: what cargo and a crate's macros see (getcwd) is what is remapped and scanned for.
    work, cache = Path(os.path.realpath(work)), Path(os.path.realpath(cache))
    for folder in (work / 'server').parents:
        if any(os.path.lexists(folder / '.cargo' / name) for name in ('config', 'config.toml')):
            raise BuildFailed('toolchain', 'build_isolation_unavailable', 'The server was not built: a cargo '
                              'configuration in a folder above the job would apply to it.')
    try:
        by_key = crate_set.check_declared(declared, source)
        vendor = crate_set.vendor(source, cache) if source is not None else None
    except crate_set.Refused as refused:
        raise _refused(refused) from None
    server, home, cargo_home, target_dir, tmp = (work / n for n in ('server', 'home', 'cargo-home', 'target', 'tmp'))
    for folder in (server, home, cargo_home, target_dir, tmp):
        folder.mkdir(mode=0o700, parents=True)
    _copy_tree(Path(snapshot) / 'server/src', server / 'src')
    if os.path.lexists(Path(snapshot) / 'server/assets'):  # what rose/assets.rs (assets_rs) embeds
        _copy_tree(Path(snapshot) / 'server/assets', server / ASSETS, 'server/assets')
    for name, text in generated.items():
        path = server / name
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        _write(path, text)
    path_crates = {k: Path(os.path.realpath(v)) for k, v in (path_crates or {}).items()}
    dependent = {k: Path(os.path.realpath(v)) for k, v in (dependent_path_crates or {}).items()}
    builder_crates = {**path_crates, **dependent}
    mapped = ('/rose-build', *(f'/rose-crates/{n}' for n in builder_crates))  # what the binary names them by
    for name, folder in builder_crates.items():  # so a scan for a crate's folder never meets its remapped name
        if any(str(folder) in m for m in mapped):
            raise BuildFailed('toolchain', 'rust_toolchain_unavailable', f'The builder\'s crate {name} is in a '
                              'folder the build\'s own names for crates would contain.')
    if _check_path_crates(builder_crates) and source is None:
        raise BuildFailed('toolchain', 'rust_toolchain_unavailable', 'The builder\'s own crates use crates.io crates, '
                          'and this builder has no crate set, so the server was not built.')
    _write(server / 'Cargo.toml', cargo_toml(declared, path_crates))
    if previous_lock is not None:
        _write(server / 'Cargo.lock', previous_lock)
    _write(cargo_home / 'config.toml', cargo_config(vendor))
    # Static, as §7.7 agreed (the musl target's default; said once so every target the builder runs is the same).
    remap = ['-Ctarget-feature=+crt-static', f'--remap-path-prefix={work}=/rose-build']
    for name, folder in sorted({**path_crates, **dependent}.items()):
        remap.append(f'--remap-path-prefix={folder}=/rose-crates/{name}')
    if vendor is not None:
        remap.append(f'--remap-path-prefix={vendor}=/rose-crates')
    env = {'PATH': tools.path(), 'HOME': str(home), 'CARGO_HOME': str(cargo_home), 'CARGO_TARGET_DIR': str(target_dir),
           'TMPDIR': str(tmp), 'LANG': 'C.UTF-8', 'RUSTC': str(tools.rustc), 'CARGO_NET_OFFLINE': 'true',
           'CARGO_INCREMENTAL': '0', 'CARGO_TERM_COLOR': 'never', 'CARGO_ENCODED_RUSTFLAGS': '\x1f'.join(remap)}
    own = tuple(sorted(set({**path_crates, **dependent}.values())))
    readonly = (Path(tools.toolchain),) + ((vendor,) if vendor is not None else ()) + own
    redact = (work, Path(tools.toolchain)) + ((vendor,) if vendor is not None else ()) + own
    manifest = str(server / 'Cargo.toml')

    def cargo(*args):
        try:
            argv, wrapped = isolation.command([str(tools.cargo), '--config', str(cargo_home / 'config.toml'), *args],
                                              dict(env), writable=work, readonly=readonly)
        except BuildFailed:
            raise  # the provider's own account of why it cannot isolate (rose.app_builder.providers.host)
        except Exception:
            raise BuildFailed('toolchain', 'build_isolation_unavailable',
                              'The server was not built: the build isolation could not prepare the build.') from None
        try:
            return run(argv, server, home, tools, cancelled, 'cargo', redact=redact, env=wrapped)
        except StepFailed as failed:
            if failed.category != 'cargo':
                raise  # cancelled or timeout, as the Android tools report them
            try:
                own = failed.returncode is not None and isolation.failed(failed.returncode, failed.log)
            except Exception:
                own = True  # a provider that cannot judge its own failure is still not the app's
            if own:
                raise BuildFailed('toolchain', 'build_isolation_failed',
                                  'The server was not built: the build isolation could not run the build.',
                                  log=failed.log[-4000:]) from None
            raise classify(failed.log, failed.returncode) from None
        except OSError:  # the isolation's program (or cargo, for a provider that runs it as is) could not start
            raise BuildFailed('toolchain', 'build_isolation_unavailable',
                              'The server was not built: the build isolation could not be started.') from None

    cargo('metadata', '--offline', '--format-version', '1', '--manifest-path', manifest)
    try:
        lock_text = (server / 'Cargo.lock').read_text(encoding='utf-8')
    except (OSError, UnicodeDecodeError):
        raise BuildFailed('toolchain', 'rust_toolchain_unavailable', 'Cargo did not write the server\'s lock.') \
            from None
    try:
        used = crate_set.check_lock(lock_text, by_key, locked, root=PACKAGE, declared=declared,
                                    path_crates=frozenset(path_crates) | frozenset(dependent))
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
    if own and _holds_any(elf, [str(p).encode() for p in own]):
        raise BuildFailed('toolchain', 'build_path_embedded', 'The server binary holds the folder of one of the '
                          'builder\'s own crates, so it was not used.')
    if _holds_any(elf, [str(p).encode() for p in (work, Path(cache), *((vendor,) if vendor is not None else ()))]):
        raise BuildFailed('dependency', 'build_path_embedded', 'The server binary holds a path from the builder\'s '
                          'machine (a crate\'s macro or build script put it there), so it was not used.')
    try:
        info = server_manifest.check_elf(elf)
    except server_manifest.Refused as refused:
        # Not static: with no crate in the build it is the writer's own code (a link section, say); with crates the
        # builder cannot tell a crate asking for a shared library from the writer, and reports the crate set, the
        # usual cause. Any other mismatch is the toolchain's.
        if refused.reason == 'server_not_static':
            category = 'dependency' if used else 'compile'
        else:
            category = 'toolchain'
        raise BuildFailed(category, refused.reason, str(refused)) from None
    return ServerBuild(elf, info.sha256, info.size, tools.target, lock_text, used)


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


def _check_path_crates(crates: dict[str, Path]):
    """Every builder crate is there, and every path its own Cargo.toml depends on is another of them, at the same
    canonical folder: one package each (the adapter's `../rose-app-runtime` is the runtime the server uses), all
    read-only to the build. A crate depending on a folder outside them, by a symlink, or a folder holding another
    package is the host's set-up, refused here. True when any of them uses a crates.io crate (the adapter does)."""
    folders = {folder: name for name, folder in crates.items()}
    registry = False
    for name, folder in crates.items():
        try:
            manifest = tomllib.loads((folder / 'Cargo.toml').read_text(encoding='utf-8'))
        except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
            raise BuildFailed('toolchain', 'rust_toolchain_unavailable',
                              f'The builder\'s crate {name} is missing or unreadable.') from None
        package = manifest.get('package')
        if not isinstance(package, dict) or package.get('name') != name:
            raise BuildFailed('toolchain', 'rust_toolchain_unavailable',
                              f'The builder\'s crate {name} is a folder holding another package.')
        tables = [manifest.get('dependencies'), manifest.get('build-dependencies')]
        tables += [t.get(k) for t in (manifest.get('target') or {}).values() if isinstance(t, dict)
                   for k in ('dependencies', 'build-dependencies')]
        for table in tables:
            for dep, spec in (table or {}).items():
                if isinstance(spec, dict) and 'path' in spec:
                    # Cargo (and so rustc, and the remap) see the path as written; it must be the real folder.
                    target = Path(os.path.realpath(folder / str(spec['path'])))
                    if folders.get(target) is None or Path(os.path.normpath(folder / str(spec['path']))) != target:
                        raise BuildFailed('toolchain', 'rust_toolchain_unavailable',
                                          f'The builder\'s crate {name} depends on {dep} at a folder the build '
                                          'was not given as one of its crates.')
                else:
                    registry = True  # a crates.io crate: from the host's crate set, like the app's own
    return registry


def _refused(refused: crate_set.Refused) -> BuildFailed:
    category = 'toolchain' if refused.reason == 'crate_set_invalid' else 'dependency'
    return BuildFailed(category, refused.reason, str(refused))


# Cargo's own wording. Only cargo's own lines and rustc's spanless messages are read as text: rustc quotes the
# writer's source in its diagnostics, so a diagnostic counts by the package it belongs to, never by its words.
_CRATE_NAME = r'`([A-Za-z0-9_-]{1,64})`'
_TOOLCHAIN = re.compile(r"can't find crate for `(?:std|core)`|linker `[^`]*` not found|linking with `[^`]*` failed|"
                        r'may not be installed')
_MISSING_STD = re.compile(r"can't find crate for `(?:std|core|alloc)`")
# What a linker says about the code it links (the package's own fault), as opposed to a linker or C library missing.
_CODE_LINK = re.compile(r'undefined symbol|undefined reference|multiple definition|duplicate symbol|relocation')


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
                    code = body.get('code')
                    errors.append({'package': str(message.get('package_id', '')), 'message': body['message'],
                                   'code': str(code.get('code', '')) if isinstance(code, dict) else '',
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


def classify(log: str, returncode: int | None = None) -> BuildFailed:
    """The job's failure for a cargo run that failed, from what cargo printed. The app's code is blamed only on
    rustc's own error in its package (or cargo's own `could not compile` for it); anything unexplained is the
    toolchain's, never the app's (an isolation that failed to start, a killed process, a crash)."""
    text, errors = _messages(log)
    shown = '\n'.join(text + [e['rendered'] for e in errors])[-4000:]
    cargo_text = '\n'.join(text)
    for error in errors:
        # Only rustc's own words count, never text the writer can put in a message or a note (a string, a
        # compile_error!, a diagnostic attribute): a standard library missing (E0463, whatever its span), a linker
        # missing, or linking failing for a reason that is not the code's (undefined or duplicate symbols are).
        missing_std = error['code'] == 'E0463' and _MISSING_STD.fullmatch(error['message'])
        no_linker = error['spanless'] and re.fullmatch(r'linker `[^`]*` not found', error['message'])
        link_failed = error['spanless'] and re.fullmatch(r'linking with `[^`]*` failed: .*|linking with `[^`]*` failed',
                                                          error['message'], re.S) \
            and not _CODE_LINK.search(error['message'] + ' ' + error['notes'])
        if missing_std or no_linker or link_failed:
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
    killed = ' (it was stopped by a signal)' if returncode is not None and returncode < 0 else ''
    return BuildFailed('toolchain', 'server_build', f'The server build failed, not in the app\'s code{killed}.',
                       log=shown)


def _copy_tree(src: Path, dst: Path, label: str = 'server/src'):
    """The writer's server/src (or server/assets), plain files and folders only (rose.app_builder.appprofile has
    checked the tree)."""
    dst.mkdir(mode=0o700)
    for here, dirs, files in os.walk(src):
        rel = Path(here).relative_to(src)
        for d in dirs:
            if not stat.S_ISDIR(os.lstat(Path(here) / d).st_mode):
                raise BuildFailed('compile', 'layout_invalid', f'{label}/{rel / d} is not a plain folder.')
            (dst / rel / d).mkdir(mode=0o700)
        for f in files:
            path = Path(here) / f
            try:
                fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
            except OSError:
                raise BuildFailed('compile', 'layout_invalid', f'{label}/{rel / f} is not a plain file.') from None
            try:
                if not stat.S_ISREG(os.fstat(fd).st_mode):
                    raise BuildFailed('compile', 'layout_invalid', f'{label}/{rel / f} is not a plain file.')
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


_ESCAPES = {'\\': '\\\\', '"': '\\"', '\n': '\\n', '\r': '\\r', '\t': '\\t'}


def _str(text: str) -> str:
    """A Rust string literal holding exactly ``text``, on one line. Not a raw string: rustc turns a raw string's CRLF
    into LF and refuses a bare CR, so SQL holding either would not be the SQL that was checked and locked."""
    return '"' + ''.join(_ESCAPES.get(c) or (c if c.isprintable() else f'\\u{{{ord(c):x}}}') for c in text) + '"'


def migrations_rs(current: dict, shared: bool) -> str:
    """``rose/migrations.rs``: the server's migrations embedded by the builder (§7.6) in the adapter's shape
    (rose-app-runtime-signin ``Migrations``): ``ACCOUNT: &[&str]`` and ``SHARED: Option<&[&str]>``, where the
    statement at index ``i`` takes a database from version ``i`` to ``i + 1``. Numbers run 1, 2, ... with no gap
    (rose.app_builder.migrations.read refuses one), so position is number. ``SHARED`` is ``None`` unless the app
    declares shared data. Built from the checked files, never from the writer's Rust, which may not read files."""
    out = ['//! The server\'s migrations, embedded by the builder. Generated; not the app\'s code.', '']
    for scope, const in (('account', 'ACCOUNT'), ('shared', 'SHARED')):
        listed = sorted(current.get(scope, []), key=lambda m: m.number)
        if [m.number for m in listed] != list(range(1, len(listed) + 1)):
            raise ValueError(f'{scope} migrations must be numbered 1..n')
        rows = ''.join(f'    // {m.number:04d}_{m.name}.sql\n    {_str(m.sql)},\n' for m in listed)
        if const == 'SHARED':
            value = f'Some(&[\n{rows}])' if shared else 'None'
            out.append(f'pub const SHARED: Option<&[&str]> = {value};')
        else:
            out.append(f'pub const ACCOUNT: &[&str] = &[\n{rows}];')
        out.append('')
    return '\n'.join(out)


def _issuer(name: str) -> str:
    """What authenticator apps show beside the code: the app's name, as rose-signin's names allow (no ':' or
    control character, at most 128 bytes), or "Rose app" when nothing is left."""
    clean = ''.join(c for c in name if c != ':' and c.isprintable()).strip()
    while len(clean.encode('utf-8')) > 128:
        clean = clean[:-1]
    return clean or 'Rose app'


def meta_rs(app: str, issuer: str, admission: str) -> str:
    """``rose/meta.rs``: what the server's start-up needs from the builder: ``APP`` (rose-signin's app name: the
    app's package, stable and unique to the project), ``ISSUER`` (the name an authenticator shows) and
    ``ADMISSION`` (the app's declaration in rose-app.json, never a request's)."""
    if admission not in ('invite', 'open'):
        raise ValueError('admission is invite or open')
    variant = 'Invite' if admission == 'invite' else 'Open'
    return ('//! The server\'s identity for sign-in, written by the builder. Generated; not the app\'s code.\n\n'
            f'pub const APP: &str = {_str(app)};\n'
            f'pub const ISSUER: &str = {_str(_issuer(issuer))};\n'
            f'pub const ADMISSION: rose_app_runtime::signin::Admission = rose_app_runtime::signin::Admission::{variant};\n')


def assets_rs(snapshot: Path) -> str:
    """``rose/assets.rs``: the writer's ``server/assets/`` embedded in the server, written by the builder on every
    client-server build (none: ``get`` answers ``None`` for every path), so the generated ``main.rs`` can always
    declare it. ``get("data/x.json")`` is the file's bytes; paths are relative to ``server/assets`` with ``/``.
    Each file is ``include_bytes!`` of the build's own copy (``build`` copies the folder), so the bytes are exact."""
    root = Path(snapshot) / 'server/assets'
    files = []
    if os.path.lexists(root):
        for here, dirs, names in os.walk(root):
            dirs.sort()
            for name in sorted(names):
                path = Path(here) / name
                files.append((path.relative_to(root).as_posix(), os.lstat(path).st_size))
    if len(files) > MAX_ASSETS:
        raise BuildFailed('compile', 'assets_too_large', f'server/assets holds more than {MAX_ASSETS} files.')
    if sum(size for _, size in files) > MAX_ASSET_BYTES:
        raise BuildFailed('compile', 'assets_too_large',
                          f'server/assets holds more than {MAX_ASSET_BYTES // (1024 * 1024)} MB.')
    arms = ''.join(f'        {_str(rel)} => Some(&include_bytes!({_str(f"../{ASSETS}/{rel}")})[..]),\n'
                   for rel, _ in sorted(files))
    return ('//! The app\'s server/assets, embedded by the builder. Generated; not the app\'s code.\n'
            '#![allow(dead_code)]\n\n'
            '/// The bytes of `server/assets/<path>`, or `None` when there is no such file.\n'
            'pub fn get(path: &str) -> Option<&\'static [u8]> {\n'
            f'    match path {{\n{arms}        _ => None,\n    }}\n}}\n')
