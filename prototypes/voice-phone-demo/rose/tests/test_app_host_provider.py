"""Host provider policy/pin tests. No install, download, model or Rose startup.

Linux confinement tests are explicit opt-in: ROSE_TEST_LINUX_ISOLATION=1 on an
already prepared, unprivileged Linux x86-64 host. They fail, rather than silently
skip, if that host cannot use its bubblewrap. Ordinary tests inspect argv with
platform checks mocked; those are not evidence of Linux kernel confinement.
"""
import hashlib
import json
import os
import platform
import shutil
import signal
import socket
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from rose.app_builder.providers.host import BubblewrapIsolation, PinnedRustTools, toolchain_digest
from rose.app_builder.server_build import BuildFailed, TARGET


class Trees(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.job = self.root / 'job'
        self.tools = self.root / 'rust'
        self.vendor = self.root / 'vendor'
        for path in (self.job, self.tools / 'bin', self.tools / 'lib/rustlib' / TARGET / 'lib', self.vendor):
            path.mkdir(parents=True)
        for name in ('cargo', 'rustc'):
            path = self.tools / 'bin' / name
            path.write_text('#!/bin/sh\nexit 99\n')  # pin checks must never execute it
            path.chmod(0o755)
        self.bwrap = self.root / 'bwrap'
        self.bwrap.write_text('#!/bin/sh\nexit 98\n')
        self.bwrap.chmod(0o755)
        self.env = {'HOME': str(self.job / 'home'), 'PATH': f'{self.tools}/bin:/usr/bin:/bin',
                    'LANG': 'C.UTF-8', 'TEST_VALUE': 'spaces = --options\nremain literal'}


class Pins(Trees):
    def pinned(self):
        return PinnedRustTools(self.tools, expected_sha256=toolchain_digest(self.tools))

    def test_v1_digest_independent_recipe_and_no_execution(self):
        records = []
        for path in [self.tools, *self.tools.rglob('*')]:
            records.append([path.relative_to(self.tools).as_posix() if path != self.tools else '',
                            path.stat().st_mode & 0o7777,
                            hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None])
        serialized = b'rose-rust-toolchain-v1\n' + b''.join(
            (json.dumps(r, separators=(',', ':')) + '\n').encode() for r in sorted(records))
        self.assertEqual(toolchain_digest(self.tools), hashlib.sha256(serialized).hexdigest())
        with mock.patch('subprocess.Popen', side_effect=AssertionError('pin must not execute tools')):
            self.assertIsNone(self.pinned().available())

    def test_pin_required_and_external_not_accepted_from_tree(self):
        for pin in ('', 'A' * 64, 'f' * 63, None):
            self.assertIsNotNone(PinnedRustTools(self.tools, expected_sha256=pin).available())
        tools = self.pinned()
        (self.tools / 'self-reported-pin.json').write_text(json.dumps({'sha256': tools.expected_sha256}))
        self.assertIsNotNone(tools.available())

    def test_every_file_mode_name_directory_and_extra_entry_is_pinned(self):
        tools = self.pinned()
        cargo = self.tools / 'bin/cargo'
        original = cargo.read_bytes()
        cargo.write_bytes(original + b'# changed\n')
        self.assertIsNotNone(tools.available())
        cargo.write_bytes(original)
        self.assertIsNone(tools.available())
        cargo.chmod(0o644)
        self.assertIsNotNone(tools.available())
        cargo.chmod(0o755)
        cargo.rename(cargo.with_name('other'))
        self.assertIsNotNone(tools.available())
        cargo.with_name('other').rename(cargo)
        (self.tools / 'empty-extra').mkdir()
        self.assertIsNotNone(tools.available())

    def test_link_file_link_directory_fifo_refused(self):
        tools = self.pinned()
        extra = self.tools / 'extra'
        for target in (self.tools / 'bin/cargo', self.tools / 'bin', self.root / 'not-present'):
            extra.symlink_to(target)
            self.assertIsNotNone(tools.available())
            extra.unlink()
        os.mkfifo(extra)
        self.assertIsNotNone(tools.available())

    def test_canonical_root_read_errors_missing_target_and_unprivileged_tools(self):
        tools = self.pinned()
        alias = self.root / 'alias'
        alias.symlink_to(self.tools, target_is_directory=True)
        self.assertIsNotNone(PinnedRustTools(alias, expected_sha256=tools.expected_sha256).available())
        with mock.patch('os.walk', side_effect=OSError('unreadable')):
            self.assertIsNotNone(tools.available())
        shutil.rmtree(self.tools / 'lib/rustlib' / TARGET)
        self.assertIsNotNone(self.pinned().available())
        (self.tools / 'lib/rustlib' / TARGET / 'lib').mkdir(parents=True)
        (self.tools / 'bin/cargo').chmod(0o4755)
        self.assertIsNotNone(self.pinned().available())


class Policy(Trees):
    def setUp(self):
        super().setUp()
        for name, value in (('platform.system', 'Linux'), ('platform.machine', 'x86_64'), ('os.geteuid', 1000)):
            patch = mock.patch(name, return_value=value)
            patch.start()
            self.addCleanup(patch.stop)

    def command(self, **overrides):
        args = dict(argv=[str(self.tools / 'bin/cargo'), '--offline', 'literal;$(no shell)'], env=self.env,
                    writable=self.job, readonly=(self.tools, self.vendor))
        args.update(overrides)
        return BubblewrapIsolation(self.bwrap).command(**args)

    def refused(self, **overrides):
        with self.assertRaises(BuildFailed) as caught:
            self.command(**overrides)
        self.assertEqual((caught.exception.category, caught.exception.reason),
                         ('toolchain', 'build_isolation_unavailable'))

    def test_mandatory_namespaces_lifetime_and_exact_environment(self):
        before = dict(self.env)
        command, env = self.command()
        for option in ('--unshare-user', '--unshare-pid', '--unshare-net', '--unshare-ipc', '--unshare-uts',
                       '--unshare-cgroup', '--disable-userns', '--new-session', '--die-with-parent'):
            self.assertIn(option, command)
        self.assertEqual(command[command.index('--cap-drop') + 1], 'ALL')
        self.assertFalse(any(arg.endswith('-try') for arg in command))
        boundary = command.index('--')
        self.assertEqual(command[boundary + 1:], ['/usr/bin/env', '-i', '--',
                         *(f'{k}={v}' for k, v in before.items()), str(self.tools / 'bin/cargo'), '--offline',
                         'literal;$(no shell)'])
        self.assertEqual(env, before)
        self.assertEqual(self.env, before)
        env['EXTRA'] = 'does not mutate caller'
        self.assertEqual(self.env, before)

    def test_only_job_host_writable_minimal_readonly_runtime_and_no_host_proc(self):
        command, _ = self.command()
        binds = [(command[i + 1], command[i + 2]) for i, v in enumerate(command) if v == '--bind']
        self.assertEqual(binds, [(str(self.job), str(self.job))])
        readonly = [(command[i + 1], command[i + 2]) for i, v in enumerate(command) if v == '--ro-bind']
        allowed = {'/usr/bin', '/usr/lib', '/usr/lib64', '/bin', '/lib', '/lib64', str(self.tools), str(self.vendor)}
        self.assertTrue(all(a == b and a in allowed for a, b in readonly))
        self.assertIn((str(self.tools), str(self.tools)), readonly)
        self.assertIn((str(self.vendor), str(self.vendor)), readonly)
        self.assertNotIn('/usr', command)
        self.assertNotIn(str(Path.home()), command)
        self.assertNotIn('/etc', command)
        self.assertEqual([command[i + 1] for i, v in enumerate(command) if v == '--remount-ro'],
                         ['/proc', '/dev', '/'])
        self.assertEqual(command[command.index('--chdir') + 1], str(self.job))

    def test_unsupported_platform_root_and_missing_or_privileged_bwrap_fail_closed(self):
        for name, value in (('platform.system', 'Darwin'), ('platform.machine', 'aarch64'), ('os.geteuid', 0)):
            with mock.patch(name, return_value=value):
                self.refused()
        self.bwrap.chmod(0o4755)
        self.refused()
        self.bwrap.chmod(0o644)
        self.refused()
        self.bwrap.unlink()
        self.refused()

    def test_canonical_disjoint_existing_directories_required(self):
        alias = self.root / 'alias'
        alias.symlink_to(self.job, target_is_directory=True)
        for job in (alias, Path('relative'), self.root / 'missing', Path('/'), Path('/usr'), Path('/proc/job')):
            self.refused(writable=job)
        for roots in ((self.root,), (self.job,), (self.tools, self.tools / 'bin'),
                      (self.tools / 'bin/cargo',), (Path('/usr/bin'),), (self.tools, self.tools)):
            self.refused(readonly=roots)
        self.refused(argv=['cargo'])
        self.refused(env={'invalid=key': 'value'})
        self.refused(env={'KEY': 'invalid\0value'})
        self.refused(readonly=(Path.home(),))
        self.refused(writable=self.root)  # contains the bwrap executable

    def test_system_alias_cannot_expose_private_tree(self):
        from rose.app_builder.providers import host
        private = self.root / 'private-system-alias'
        private.symlink_to(self.vendor, target_is_directory=True)
        with mock.patch.object(host, '_RUNTIME', (private,)):
            self.refused(readonly=(self.tools,))

    def test_host_loader_cannot_run_code_before_confinement(self):
        for name in ('LD_PRELOAD', 'LD_LIBRARY_PATH', 'LD_AUDIT', 'GLIBC_TUNABLES', 'GCONV_PATH', 'LOCPATH'):
            with self.subTest(name=name):
                self.refused(env={**self.env, name: '/host/untrusted'})


@unittest.skipUnless(os.environ.get('ROSE_TEST_LINUX_ISOLATION') == '1',
                     'Linux confinement not requested; argv policy checks do not prove confinement')
class LinuxConfinement(Trees):
    """Uses only preinstalled Linux tools; no install or kernel-policy changes."""
    def setUp(self):
        super().setUp()
        self.assertEqual((platform.system(), platform.machine()), ('Linux', 'x86_64'))
        self.assertNotEqual(os.geteuid(), 0)
        self.provider = BubblewrapIsolation()
        self.env = {'PATH': '/usr/bin:/bin', 'HOME': str(self.job / 'home'), 'LANG': 'C.UTF-8',
                    'KEEP': 'exact supplied value'}

    def wrapped(self, code, *args):
        return self.provider.command(['/usr/bin/python3', '-c', code, *map(str, args)], self.env,
                                     writable=self.job, readonly=(self.tools, self.vendor))

    def test_real_filesystem_environment_and_network_denials(self):
        secret = self.root / 'other-job-secret'
        secret.write_text('must stay hidden')
        (self.job / 'outward-link').symlink_to(secret)
        (self.root / '.cargo').mkdir()
        config = self.root / '.cargo/config.toml'
        config.write_text('[build]\nrustc-wrapper="must-not-run"\n')
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            listener.listen()
            port = listener.getsockname()[1]
            code = '''
import json, os, pathlib, socket, sys
job, tools, vendor, secret, config, host_home = map(pathlib.Path, sys.argv[1:7])
assert dict(os.environ) == json.loads(sys.argv[7]), dict(os.environ)
assert not secret.exists() and not config.exists()
assert not (host_home / '.ssh').exists() and not (host_home / '.config').exists()
assert not (job / 'outward-link').exists()
for path in (tools / 'bin/cargo', vendor / 'new', pathlib.Path('/usr/bin/rose-test'), pathlib.Path('/outside')):
    try:
        path.write_text('escape')
    except OSError:
        pass
    else:
        raise AssertionError(str(path))
with socket.socket() as s:
    s.settimeout(1)
    assert s.connect_ex(('127.0.0.1', int(sys.argv[8]))) != 0
(job / 'written').write_text('job writable')
'''
            command, env = self.wrapped(code, self.job, self.tools, self.vendor, secret, config, Path.home(),
                                        json.dumps(self.env), port)
            result = subprocess.run(command, env=env, cwd=self.job, stdin=subprocess.DEVNULL,
                                    capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.job / 'written').read_text(), 'job writable')
        self.assertEqual(secret.read_text(), 'must stay hidden')

    def test_real_timeout_and_cancellation_kill_detached_descendants(self):
        # Detached child keeps stdout open and writes a heartbeat. A surviving
        # child makes communicate hang or continues changing the heartbeat.
        code = '''
import os, pathlib, time
if os.fork() == 0:
    os.setsid()
    while True:
        pathlib.Path('heartbeat').write_text(str(time.monotonic_ns()))
        time.sleep(.02)
while True: time.sleep(1)
'''
        for cancel in (False, True):
            with self.subTest(cancel=cancel):
                heartbeat = self.job / 'heartbeat'
                heartbeat.unlink(missing_ok=True)
                command, env = self.wrapped(code)
                # Run in a supervisor with a hard deadline so a broken cleanup
                # cannot hang the test runner itself. No shell or real build.
                harness = '''
import json, sys
from pathlib import Path
from rose.app_builder.toolchain import run, StepFailed
from rose.app_builder.server_build import RustTools
args, env, job, cancel = json.loads(sys.argv[1])
try:
    run(args, Path(job), Path(job), RustTools(Path(job), timeout_s=1),
        lambda: cancel and (Path(job) / 'heartbeat').exists(), 'cargo', env=env)
except StepFailed as failed:
    assert failed.category == ('cancelled' if cancel else 'timeout'), failed.category
else:
    raise AssertionError('command did not stop')
'''
                import sys
                supervisor = subprocess.Popen([sys.executable, '-c', harness,
                    json.dumps([command, env, str(self.job), cancel])], stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
                try:
                    out, error = supervisor.communicate(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(supervisor.pid, signal.SIGKILL)
                    supervisor.communicate()
                    self.fail('confined descendants outlived timeout/cancellation')
                self.assertEqual(supervisor.returncode, 0, error.decode())
                self.assertTrue(heartbeat.is_file(), 'the detached child actually ran')
                previous = heartbeat.read_bytes()
                time.sleep(.15)
                self.assertEqual(heartbeat.read_bytes(), previous)


if __name__ == '__main__':
    unittest.main()
