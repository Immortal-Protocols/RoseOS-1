"""Build isolation: where code the builder did not write runs (docs/APP-BUILDER.md §5, blocker 3).

A build runs code the app's writer chose: Gradle settings and build scripts and their plugins, a crate's build
script. The builder never runs it bare. The host supplies an ``Isolation`` that wraps each command so that it can
write only the job's folder, read only the trees the build needs, and reach no network; without one a build does
not start, and nothing here offers a weaker fallback. Signing happens after the build, outside any of this: no
isolation mounts the builder's keys, ledger, artifacts or other jobs.

- ``BubblewrapIsolation``: Linux x86-64, unprivileged bubblewrap with every namespace unshared and an empty
  read-only root. Qualified (rose.app_builder.qualify, test_app_host_provider's opt-in confinement tests).
- ``SeatbeltIsolation``: macOS ``sandbox-exec`` with a deny-by-path profile. Not qualified: it is written to the
  documented SBPL and unit-tested on its profile and pre-flight only, and must be qualified on a Mac before a host
  relies on it. Its guarantees are weaker than bubblewrap's (see its docstring).

Both refuse, as ``BuildFailed('toolchain', 'build_isolation_unavailable', ...)``, to wrap a command they cannot
confine as asked. This module is generic: it names no build system and imports none of the builders.
"""
from __future__ import annotations

import os
import platform
import stat
from dataclasses import dataclass
from pathlib import Path

from .toolchain import StepFailed


class BuildFailed(StepFailed):
    """A build that stopped; ``reason`` is a stable token next to the job's failure category."""

    def __init__(self, category: str, reason: str, summary: str, log: str = '', retryable: bool = False):
        super().__init__(category, summary, retryable=retryable, log=log)
        self.reason = reason


class Isolation:
    """Runs one build command so that it can write only ``writable``, read only ``readonly`` (and the system's own
    executables and libraries), and reach no network. The builder host provides it (docs/APP-BUILDER.md §5,
    blocker 3); a build that has none does not run.

    The failure contract: a build that fails because of the isolation (it could not start, or the kernel refused a
    namespace or a mount) is the host's, never the app's. ``command`` raising, or its program missing, stops the
    build as ``build_isolation_unavailable``; ``failed`` saying a non-zero exit was its own stops it as
    ``build_isolation_failed``. Either is category ``toolchain``. A failure no one claims and that shows no compiler
    error in the app's own code is also ``toolchain``: the app's code is blamed only on its compiler's own word."""

    def command(self, argv: list[str], env: dict[str, str], *, writable: Path,
                readonly: tuple[Path, ...]) -> tuple[list[str], dict[str, str]]:
        """The command and environment to run in place of ``argv`` and ``env``."""
        raise NotImplementedError

    def failed(self, returncode: int, output: str) -> bool:
        """Whether a run that exited with ``returncode`` (negative: killed by that signal) and printed ``output``
        failed in the isolation itself rather than in the command it ran. The provider knows its own messages and
        exit statuses; the default claims nothing."""
        return False


def _canonical(path: Path) -> Path:
    path = Path(path)
    if not path.is_absolute() or path.resolve(strict=True) != path:
        raise ValueError('an existing canonical absolute path is required')
    return path


def _overlap(a: Path, b: Path) -> bool:
    return a == b or a in b.parents or b in a.parents


def _check_env(env: dict[str, str]):
    if any(not isinstance(k, str) or not k or '=' in k or '\0' in k or
           not isinstance(v, str) or '\0' in v for k, v in env.items()):
        raise ValueError('invalid command environment')


def _check_tool(path: Path, what: str) -> Path:
    """An existing canonical, regular, executable, not set-id program."""
    path = _canonical(path)
    mode = path.stat().st_mode
    if not stat.S_ISREG(mode) or not os.access(path, os.X_OK) or mode & (stat.S_ISUID | stat.S_ISGID):
        raise ValueError(f'requires non-setuid executable {what}')
    return path


def _mounts(writable: Path, readonly: tuple[Path, ...]) -> tuple[Path, tuple[Path, ...]]:
    """The job folder and the read-only trees: existing canonical directories, pairwise disjoint."""
    job = _canonical(writable)
    roots = tuple(_canonical(p) for p in readonly)
    mounts = (job, *roots)
    if any(not p.is_dir() for p in mounts):
        raise ValueError('mounts must be directories')
    if any(_overlap(a, b) for i, a in enumerate(mounts) for b in mounts[i + 1:]):
        raise ValueError('job and read-only trees must be disjoint')
    return job, roots


# Executables and dynamic-loader/linker libraries only, never /usr as a whole
# (/usr/local can contain host state), /etc, home, /run, /sys or a host /proc.
_RUNTIME = tuple(Path(p) for p in ('/usr/bin', '/usr/lib', '/usr/lib64', '/bin', '/lib', '/lib64'))
_VIRTUAL = (Path('/dev'), Path('/proc'))
_HIDDEN = (Path('/etc'), Path('/run'), Path('/sys'))


@dataclass(frozen=True)
class BubblewrapIsolation(Isolation):
    """Linux x86-64, unprivileged bubblewrap with mandatory namespaces.

    Requires a host-installed non-setuid bubblewrap supporting --disable-userns.
    Unsupported flags/kernel policy fail the command; there is no weaker retry.
    Callers supply a scrubbed environment (no loader overrides), use
    toolchain.run's closed extra fds/pipes, and keep job directories separate from
    all other mounts. PID isolation + --die-with-parent couples all
    children to the bwrap process that run kills on cancellation/timeout. The
    sandbox's root is empty and read-only: only the system runtime, the read-only
    trees and the job folder exist in it (no /etc, /tmp, home), and its loopback is
    its own.
    """
    bwrap: Path = Path('/usr/bin/bwrap')

    def command(self, argv: list[str], env: dict[str, str], *, writable: Path,
                readonly: tuple[Path, ...]) -> tuple[list[str], dict[str, str]]:
        try:
            if platform.system() != 'Linux' or platform.machine() != 'x86_64' or os.geteuid() == 0:
                raise ValueError('requires unprivileged Linux x86-64')
            bwrap = _check_tool(self.bwrap, 'bubblewrap')
            job, roots = _mounts(writable, readonly)
            mounts = (job, *roots)
            if bwrap == job or job in bwrap.parents:
                raise ValueError('jobs must not be able to replace bubblewrap')
            if any(_overlap(p, reserved) for p in mounts for reserved in (*_RUNTIME, *_VIRTUAL, *_HIDDEN)):
                raise ValueError('build trees must not overlap the system runtime')
            if any(p == Path.home() or p in Path.home().parents for p in mounts):
                raise ValueError('build trees must not expose the host home')
            if not argv or not Path(argv[0]).is_absolute():
                raise ValueError('the command must be absolute')
            _check_env(env)
            # These affect the host loader before bwrap can enter a namespace.
            # The build's scrubbed env never needs them; refuse, do not inherit
            # or silently remove an override and pretend env was unchanged.
            if any(k.startswith('LD_') or k in ('GLIBC_TUNABLES', 'GCONV_PATH', 'LOCPATH') for k in env):
                raise ValueError('host loader overrides are not a build environment')
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
                              'The build was not run: Linux build isolation is unavailable or its paths '
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


# Where macOS keeps people's and the system's private files: read denied unless a mount is inside.
_MAC_PRIVATE = tuple(Path(p) for p in ('/Users', '/Volumes', '/private/var/folders'))
# What a mount must not be or contain: the system itself and the places _MAC_PRIVATE hides.
_MAC_SYSTEM = tuple(Path(p) for p in ('/System', '/usr', '/bin', '/sbin', '/Library', '/Applications', '/dev',
                                      '/private/etc', '/private/var/db', '/private/var/run'))
# Files a confined command may write besides the job folder: the standard streams and the null device.
_MAC_DEVICES = ('/dev/null', '/dev/tty', '/dev/dtracehelper')
# The keychain's daemons: no build may ask them for a password, a key or a certificate's private half.
_KEYCHAIN = ('(global-name "com.apple.SecurityServer")',
             r'(global-name-regex #"^com\.apple\.securityd(\..*)?$")',
             r'(global-name-regex #"^com\.apple\.security\.")')


def _sbpl(path: Path | str) -> str:
    """``path`` as an SBPL string literal. Paths with control characters are refused, not escaped."""
    text = str(path)
    if any(ord(c) < 0x20 or ord(c) == 0x7f for c in text):
        raise ValueError('paths must not hold control characters')
    return '"' + text.replace('\\', '\\\\').replace('"', '\\"') + '"'


@dataclass(frozen=True)
class SeatbeltIsolation(Isolation):
    """macOS, ``sandbox-exec`` with a generated profile, modelled on Bazel's darwin sandbox (allow by default, then
    deny). NOT QUALIFIED: it cannot run on Linux, so only its profile and pre-flight are tested; a host must qualify
    it on the Mac (the same probe as rose.app_builder.qualify's confinement check) before relying on it.

    The confined command:
    - writes only ``writable`` and the standard devices (``_MAC_DEVICES``, ``/dev/fd``);
    - reads nothing in ``hidden`` (the builder root, the owner's home, Rose's config folder: whatever the host names)
      nor anything under /Users, /Volumes or /private/var/folders, except inside ``writable`` and ``readonly``; the
      folders above those stay stat-able (``file-read-metadata`` on each ancestor, so realpath works) but not
      listable;
    - reaches no network but localhost (``loopback``; Gradle's client, its single-use daemon and its lock
      contention use loopback TCP/UDP), and no Unix socket;
    - cannot look up the keychain's services;
    - gets exactly ``env`` (``/usr/bin/env -i``), with no DYLD_* override reaching the loader.

    Weaker than bubblewrap, by construction: the rest of the file system stays readable (/etc, /Library,
    /Applications), localhost is the Mac's own, so a confined build can connect to any service listening there, and
    mach services other than the keychain's stay reachable, so a launchd service could act for it. Descendants are
    not tied to the process toolchain.run kills on cancellation or timeout (no PID namespace). ``sandbox-exec`` is
    deprecated by Apple, though still shipped and used by Bazel, Chromium and Nix. Hosts that need bubblewrap's
    guarantees build on Linux."""
    hidden: tuple[Path, ...] = ()
    sandbox_exec: Path = Path('/usr/bin/sandbox-exec')
    loopback: bool = True

    def command(self, argv: list[str], env: dict[str, str], *, writable: Path,
                readonly: tuple[Path, ...]) -> tuple[list[str], dict[str, str]]:
        try:
            if platform.system() != 'Darwin' or os.geteuid() == 0:
                raise ValueError('requires unprivileged macOS')
            tool = _check_tool(self.sandbox_exec, 'sandbox-exec')
            job, roots = _mounts(writable, readonly)
            mounts = (job, *roots)
            hidden = tuple(Path(p) for p in self.hidden)
            if any(not p.is_absolute() or Path(os.path.realpath(p)) != p for p in hidden):
                raise ValueError('hidden folders must be canonical absolute paths')
            if tool == job or job in tool.parents:
                raise ValueError('jobs must not be able to replace sandbox-exec')
            # A mount may lie inside a hidden folder (jobs live in the builder root), never be one or hold one.
            if any(p == h or p in h.parents for p in mounts for h in (*hidden, *_MAC_PRIVATE, Path.home())):
                raise ValueError('build trees must not expose a hidden folder')
            if any(_overlap(p, reserved) for p in mounts for reserved in _MAC_SYSTEM):
                raise ValueError('build trees must not overlap the system')
            if not argv or not Path(argv[0]).is_absolute() or '=' in argv[0]:
                raise ValueError('the command must be absolute')
            _check_env(env)
            # dyld reads these before sandbox-exec applies the profile; the build's scrubbed env never needs them.
            if any(k.startswith(('DYLD_', 'LD_')) for k in env):
                raise ValueError('host loader overrides are not a build environment')
            if not Path('/usr/bin/env').is_file():
                raise ValueError('requires the system env executable')
            profile = self.profile(job, roots)
        except (OSError, ValueError, RuntimeError):
            raise BuildFailed('toolchain', 'build_isolation_unavailable',
                              'The build was not run: macOS build isolation is unavailable or its paths '
                              'are not separate canonical trees.') from None
        command = [str(tool), '-p', profile, '/usr/bin/env', '-i', '--',
                   *(f'{k}={v}' for k, v in env.items()), *argv]
        return command, dict(env)

    def profile(self, job: Path, roots: tuple[Path, ...]) -> str:
        """The SBPL profile for one command. The last matching rule wins, so each allow follows the deny it
        narrows."""
        mounts = (job, *roots)
        ancestors = sorted({a for p in mounts for a in p.parents if a != Path('/')} - set(mounts))
        hidden = sorted({*(Path(p) for p in self.hidden), *_MAC_PRIVATE})
        lines = ['(version 1)', '(allow default)',
                 '(deny network*)']
        if self.loopback:
            lines += ['(allow network-bind (local ip "localhost:*"))',
                      '(allow network-inbound (local ip "localhost:*"))',
                      '(allow network-outbound (remote ip "localhost:*"))']
        lines += ['(deny file-write*)',
                  '(allow file-write*',
                  f'    (subpath {_sbpl(job)})',
                  *(f'    (literal {_sbpl(d)})' for d in _MAC_DEVICES),
                  '    (subpath "/dev/fd"))',
                  '(deny file-read*',
                  *(f'    (subpath {_sbpl(h)})' for h in hidden[:-1]),
                  f'    (subpath {_sbpl(hidden[-1])}))',
                  '(allow file-read*',
                  *(f'    (subpath {_sbpl(p)})' for p in mounts[:-1]),
                  f'    (subpath {_sbpl(mounts[-1])}))']
        if ancestors:
            lines += ['(allow file-read-metadata',
                      *(f'    (literal {_sbpl(a)})' for a in ancestors[:-1]),
                      f'    (literal {_sbpl(ancestors[-1])}))']
        lines += ['(deny mach-lookup', *(f'    {name}' for name in _KEYCHAIN[:-1]), f'    {_KEYCHAIN[-1]})']
        return '\n'.join(lines) + '\n'
