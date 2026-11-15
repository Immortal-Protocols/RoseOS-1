"""Offline Linux build facilities, supplied explicitly by the builder owner.

This is not the standalone service in app_builder.host. Nothing constructs these
providers by default, provisions a host, or downloads a toolchain. The host must
keep its toolchain, crate cache and system runtime immutable to jobs, including
while pins are checked and commands run. A digest is a supplied trust decision,
not one learned from the same installation at build time.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import stat
from dataclasses import dataclass
from pathlib import Path

from ..server_build import BuildFailed, Isolation, RustTools


def _canonical(path: Path) -> Path:
    path = Path(path)
    if not path.is_absolute() or path.resolve(strict=True) != path:
        raise ValueError('an existing canonical absolute path is required')
    return path


def toolchain_digest(root: Path) -> str:
    """Pin format v1: SHA-256 of sorted JSON-line [path, mode, content-sha256]
    records, including directories (digest null) and the root (path ""). Paths
    are POSIX relative names, modes are stat.S_IMODE integers. JSON uses ASCII,
    compact separators and a trailing newline per record. Links/special files
    are refused, not followed; nothing in the tree is executed.
    """
    root = _canonical(root)
    if not root.is_dir():
        raise ValueError('a plain toolchain directory is required')
    records = []
    for folder, dirs, files in os.walk(root, followlinks=False, onerror=_walk_error):
        for path in [Path(folder), *(Path(folder) / n for n in files)]:
            mode = path.lstat().st_mode
            if stat.S_ISREG(mode):
                digest = hashlib.sha256()
                with path.open('rb') as stream:
                    for block in iter(lambda: stream.read(1 << 20), b''):
                        digest.update(block)
                content = digest.hexdigest()
            elif stat.S_ISDIR(mode):
                content = None
            else:
                raise ValueError('toolchain links and special files are refused')
            records.append((path.relative_to(root).as_posix() if path != root else '',
                            stat.S_IMODE(mode), content))
        # os.walk does not visit symlink directories; reject those explicitly.
        if any(not stat.S_ISDIR((Path(folder) / n).lstat().st_mode) for n in dirs):
            raise ValueError('toolchain links and special files are refused')
    digest = hashlib.sha256(b'rose-rust-toolchain-v1\n')
    for record in sorted(records):
        digest.update((json.dumps(record, ensure_ascii=True, separators=(',', ':')) + '\n').encode('ascii'))
    return digest.hexdigest()


def _walk_error(error):
    raise error


@dataclass(kw_only=True)
class PinnedRustTools(RustTools):
    """An unpacked, non-proxy RustTools whose entire tree matches an external pin.
    Rechecked on every available() call; no cached successful verification.
    """
    expected_sha256: str

    def available(self) -> str | None:
        if not isinstance(self.expected_sha256, str) or not re.fullmatch('[0-9a-f]{64}', self.expected_sha256):
            return 'The builder has no valid externally supplied Rust toolchain pin.'
        try:
            root = _canonical(self.toolchain)
            if toolchain_digest(root) != self.expected_sha256:
                return 'The builder\'s Rust toolchain does not match its supplied pin.'
            missing = super().available()
            if missing:
                return missing
            if any(not os.access(p, os.X_OK) or p.stat().st_mode & (stat.S_ISUID | stat.S_ISGID)
                   for p in (self.cargo, self.rustc)):
                return 'The builder\'s pinned Rust tools must be executable and unprivileged.'
        except (OSError, ValueError, RuntimeError):
            return 'The builder\'s Rust toolchain could not be verified as a plain pinned tree.'
        return None


# Executables and dynamic-loader/linker libraries only, never /usr as a whole
# (/usr/local can contain host state), /etc, home, /run, /sys or a host /proc.
_RUNTIME = tuple(Path(p) for p in ('/usr/bin', '/usr/lib', '/usr/lib64', '/bin', '/lib', '/lib64'))
_VIRTUAL = (Path('/dev'), Path('/proc'))
_HIDDEN = (Path('/etc'), Path('/run'), Path('/sys'))


def _overlap(a: Path, b: Path) -> bool:
    return a == b or a in b.parents or b in a.parents


@dataclass(frozen=True)
class BubblewrapIsolation(Isolation):
    """Linux x86-64, unprivileged bubblewrap with mandatory namespaces.

    Requires a host-installed non-setuid bubblewrap supporting --disable-userns.
    Unsupported flags/kernel policy fail the command; there is no weaker retry.
    Callers supply server_build's scrubbed environment (no loader overrides), use
    toolchain.run's closed extra fds/pipes, and keep job directories separate from
    all other mounts. PID isolation + --die-with-parent couples all
    children to the bwrap process that run kills on cancellation/timeout.
    """
    bwrap: Path = Path('/usr/bin/bwrap')

    def command(self, argv: list[str], env: dict[str, str], *, writable: Path,
                readonly: tuple[Path, ...]) -> tuple[list[str], dict[str, str]]:
        try:
            if platform.system() != 'Linux' or platform.machine() != 'x86_64' or os.geteuid() == 0:
                raise ValueError('requires unprivileged Linux x86-64')
            bwrap = _canonical(self.bwrap)
            mode = bwrap.stat().st_mode
            if not stat.S_ISREG(mode) or not os.access(bwrap, os.X_OK) or mode & (stat.S_ISUID | stat.S_ISGID):
                raise ValueError('requires non-setuid executable bubblewrap')
            job = _canonical(writable)
            roots = tuple(_canonical(p) for p in readonly)
            mounts = (job, *roots)
            if any(not p.is_dir() for p in mounts):
                raise ValueError('mounts must be directories')
            if any(_overlap(a, b) for i, a in enumerate(mounts) for b in mounts[i + 1:]):
                raise ValueError('job and read-only trees must be disjoint')
            if bwrap == job or job in bwrap.parents:
                raise ValueError('jobs must not be able to replace bubblewrap')
            if any(_overlap(p, reserved) for p in mounts for reserved in (*_RUNTIME, *_VIRTUAL, *_HIDDEN)):
                raise ValueError('build trees must not overlap the system runtime')
            if any(p == Path.home() or p in Path.home().parents for p in mounts):
                raise ValueError('build trees must not expose the host home')
            if not argv or not Path(argv[0]).is_absolute():
                raise ValueError('the command must be absolute')
            if any(not isinstance(k, str) or not k or '=' in k or '\0' in k or
                   not isinstance(v, str) or '\0' in v for k, v in env.items()):
                raise ValueError('invalid command environment')
            runtime = []
            for path in _RUNTIME:
                if path.exists():
                    # Accommodate merged-/usr distributions, without following an
                    # unexpected system alias into a private tree.
                    if path.resolve(strict=True) not in _RUNTIME or not path.is_dir():
                        raise ValueError('unsupported system runtime layout')
                    runtime += ['--ro-bind', str(path), str(path)]
            if not Path('/usr/bin/env').is_file():
                raise ValueError('requires the system env executable')
        except (OSError, ValueError, RuntimeError):
            raise BuildFailed('toolchain', 'build_isolation_unavailable',
                              'The server was not built: Linux build isolation is unavailable or its paths '
                              'are not separate canonical trees.') from None

        command = [str(bwrap), '--unshare-user', '--unshare-pid', '--unshare-net', '--unshare-ipc',
                   '--unshare-uts', '--unshare-cgroup', '--disable-userns', '--cap-drop', 'ALL',
                   '--new-session', '--die-with-parent', *runtime,
                   '--proc', '/proc', '--remount-ro', '/proc', '--dev', '/dev', '--remount-ro', '/dev']
        for root in roots:
            command += ['--ro-bind', str(root), str(root)]
        command += ['--bind', str(job), str(job), '--remount-ro', '/', '--chdir', str(job), '--',
                    # bwrap sets PWD even with a supplied environment. Reset the
                    # child environment whole so the build gets exactly env.
                    '/usr/bin/env', '-i', '--', *(f'{k}={v}' for k, v in env.items()), *argv]
        return command, dict(env)
