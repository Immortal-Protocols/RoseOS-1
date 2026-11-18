"""rose.app_builder.isolation: the moved names keep their old homes, and the macOS profile and pre-flight.

SeatbeltIsolation cannot run here (no sandbox-exec off macOS): these tests check the profile text and every
refusal with the platform mocked. They are not evidence that macOS enforces the profile; a Mac must qualify it.
The bubblewrap provider's own tests stay in test_app_host_provider (its real-confinement ones are opt-in).
"""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from rose.app_builder import isolation
from rose.app_builder.isolation import BubblewrapIsolation, BuildFailed, Isolation, SeatbeltIsolation

HERE = Path(__file__).resolve().parents[2]  # prototypes/voice-phone-demo


def sbpl(text: str) -> list:
    """A minimal SBPL reader: nested lists of atoms, ("str", value) for string literals and ("re", value) for
    #"..." regexes, so a test sees exactly what sandbox-exec would parse."""
    stack, i = [[]], 0
    while i < len(text):
        c = text[i]
        if c.isspace():
            i += 1
        elif c == ';':
            i = text.index('\n', i)
        elif c == '(':
            stack.append([])
            i += 1
        elif c == ')':
            done = stack.pop()
            stack[-1].append(done)
            i += 1
        elif c == '"' or text.startswith('#"', i):
            kind = 're' if c == '#' else 'str'
            i += 2 if kind == 're' else 1
            value = ''
            while text[i] != '"':
                if text[i] == '\\':
                    if kind == 're':
                        value += text[i]
                    i += 1
                value += text[i]
                i += 1
            stack[-1].append((kind, value))
            i += 1
        else:
            j = i
            while j < len(text) and not text[j].isspace() and text[j] not in '()':
                j += 1
            stack[-1].append(text[i:j])
            i = j
    assert len(stack) == 1, 'unbalanced profile'
    return stack[0]


def rules(profile: str) -> list[tuple[str, str, list]]:
    """(action, operation, filters) per rule, in order."""
    return [(r[0], r[1], r[2:]) for r in sbpl(profile) if r[0] in ('allow', 'deny')]


def paths(filters, kind) -> list[str]:
    return [f[1][1] for f in filters if isinstance(f, list) and f[0] == kind]


class MovedNames(unittest.TestCase):
    def test_every_old_import_path_names_the_same_object(self):
        from rose.app_builder import server_build
        from rose.app_builder.providers import host
        self.assertIs(server_build.Isolation, Isolation)
        self.assertIs(server_build.BuildFailed, BuildFailed)
        self.assertIs(host.BubblewrapIsolation, BubblewrapIsolation)
        self.assertIs(host.BuildFailed, BuildFailed)
        self.assertIs(host.Isolation, Isolation)
        self.assertTrue(issubclass(BubblewrapIsolation, server_build.Isolation))

    def test_the_module_imports_no_builder(self):
        code = ('import sys, rose.app_builder.isolation; '
                'bad = [m for m in sys.modules if m.endswith(("server_build", "providers.host", "service", "gradle"))]; '
                'assert not bad, bad')
        done = subprocess.run([sys.executable, '-c', code], cwd=HERE, capture_output=True, text=True, timeout=60)
        self.assertEqual(done.returncode, 0, done.stderr)

    def test_the_base_contract(self):
        with self.assertRaises(NotImplementedError):
            Isolation().command(['/bin/true'], {}, writable=Path('/'), readonly=())
        self.assertFalse(Isolation().failed(1, 'anything'))
        failed = BuildFailed('toolchain', 'build_isolation_unavailable', 'why', log='l', retryable=True)
        self.assertEqual((failed.category, failed.reason, failed.summary, failed.log, failed.retryable),
                         ('toolchain', 'build_isolation_unavailable', 'why', 'l', True))

    def test_the_bubblewrap_refusal_names_no_build_system(self):
        with mock.patch('platform.system', return_value='Darwin'), self.assertRaises(BuildFailed) as caught:
            BubblewrapIsolation().command(['/bin/true'], {}, writable=Path('/nonexistent'), readonly=())
        self.assertEqual((caught.exception.category, caught.exception.reason),
                         ('toolchain', 'build_isolation_unavailable'))
        self.assertTrue(caught.exception.summary.startswith('The build was not run: '), caught.exception.summary)
        self.assertNotIn('server', caught.exception.summary)


class Seatbelt(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.builder = self.root / 'builder'  # hidden, holds the jobs, keys and ledger
        self.job = self.builder / 'jobs/j1'
        self.stack = self.root / 'stack'
        self.cache = self.root / 'cache'
        self.home = self.root / 'home'
        for path in (self.job, self.stack, self.cache, self.home, self.builder / 'keys'):
            path.mkdir(parents=True)
        self.tool = self.root / 'bin/sandbox-exec'
        self.tool.parent.mkdir()
        self.tool.write_text('#!/bin/sh\nexit 97\n')
        self.tool.chmod(0o755)
        self.env = {'HOME': str(self.job / 'home'), 'PATH': f'{self.stack}/bin:/usr/bin:/bin', 'LANG': 'C.UTF-8',
                    'GRADLE_OPTS': '-Dx=a b'}
        for name, value in (('platform.system', 'Darwin'), ('platform.machine', 'arm64'), ('os.geteuid', 501)):
            patch = mock.patch(name, return_value=value)
            patch.start()
            self.addCleanup(patch.stop)
        patch = mock.patch.object(Path, 'home', return_value=self.home)
        patch.start()
        self.addCleanup(patch.stop)

    def provider(self, **fields):
        return SeatbeltIsolation(**{'hidden': (self.builder, self.home), 'sandbox_exec': self.tool, **fields})

    def command(self, provider=None, **overrides):
        args = dict(argv=[str(self.stack / 'bin/gradle'), '--offline', 'literal;$(no shell)'], env=self.env,
                    writable=self.job, readonly=(self.stack, self.cache))
        args.update(overrides)
        return (provider or self.provider()).command(**args)

    def refused(self, provider=None, **overrides):
        with self.assertRaises(BuildFailed) as caught:
            self.command(provider, **overrides)
        self.assertEqual((caught.exception.category, caught.exception.reason),
                         ('toolchain', 'build_isolation_unavailable'))
        self.assertTrue(caught.exception.summary.startswith('The build was not run: macOS build isolation'))

    def test_the_command_is_sandbox_exec_then_an_exact_environment(self):
        before = dict(self.env)
        command, env = self.command()
        self.assertEqual(command[:2], [str(self.tool), '-p'])
        self.assertEqual(command[3:], ['/usr/bin/env', '-i', '--', *(f'{k}={v}' for k, v in before.items()),
                                       str(self.stack / 'bin/gradle'), '--offline', 'literal;$(no shell)'])
        self.assertEqual(env, before)
        env['EXTRA'] = 'does not mutate caller'
        self.assertEqual(self.env, before)

    def test_the_profile_allows_by_default_then_denies_and_each_allow_narrows_its_deny(self):
        profile = self.command()[0][2]
        got = rules(profile)
        self.assertEqual(sbpl(profile)[0], ['version', '1'])
        self.assertEqual(got[0], ('allow', 'default', []))
        order = [(a, op) for a, op, _ in got]
        self.assertEqual(order, [('allow', 'default'), ('deny', 'network*'), ('allow', 'network-bind'),
                                 ('allow', 'network-inbound'), ('allow', 'network-outbound'), ('deny', 'file-write*'),
                                 ('allow', 'file-write*'), ('deny', 'file-read*'), ('allow', 'file-read*'),
                                 ('allow', 'file-read-metadata'), ('deny', 'mach-lookup')])
        by = {(a, op): f for a, op, f in got}
        for op, side in (('network-bind', 'local'), ('network-inbound', 'local'), ('network-outbound', 'remote')):
            self.assertEqual(by[('allow', op)], [[side, 'ip', ('str', 'localhost:*')]])
        self.assertEqual(by[('deny', 'network*')], [])  # everything, Unix sockets included

    def test_only_the_job_and_standard_devices_are_writable(self):
        by = {(a, op): f for a, op, f in rules(self.command()[0][2])}
        self.assertEqual(by[('deny', 'file-write*')], [])
        allowed = by[('allow', 'file-write*')]
        self.assertEqual(paths(allowed, 'subpath'), [str(self.job), '/dev/fd'])
        self.assertEqual(paths(allowed, 'literal'), ['/dev/null', '/dev/tty', '/dev/dtracehelper'])

    def test_hidden_and_private_folders_are_unreadable_except_the_mounts(self):
        by = {(a, op): f for a, op, f in rules(self.command()[0][2])}
        self.assertEqual(set(paths(by[('deny', 'file-read*')], 'subpath')),
                         {str(self.builder), str(self.home), '/Users', '/Volumes', '/private/var/folders'})
        self.assertEqual(paths(by[('allow', 'file-read*')], 'subpath'), [str(self.job), str(self.stack),
                                                                       str(self.cache)])
        # The folders above a mount answer stat (realpath, getCanonicalPath) but cannot be listed or read.
        metadata = paths(by[('allow', 'file-read-metadata')], 'literal')
        self.assertIn(str(self.builder), metadata)
        self.assertIn(str(self.builder / 'jobs'), metadata)
        self.assertNotIn(str(self.builder / 'keys'), metadata)
        self.assertNotIn('/', metadata)
        self.assertEqual(paths(by[('allow', 'file-read-metadata')], 'subpath'), [])

    def test_the_keychain_services_cannot_be_looked_up(self):
        by = {(a, op): f for a, op, f in rules(self.command()[0][2])}
        names = by[('deny', 'mach-lookup')]
        self.assertIn(['global-name', ('str', 'com.apple.SecurityServer')], names)
        regexes = [f[1][1] for f in names if f[0] == 'global-name-regex']
        import re
        for service in ('com.apple.securityd', 'com.apple.securityd.xpc', 'com.apple.security.agent',
                        'com.apple.security.keychain-circle-notification'):
            self.assertTrue(any(re.search(r, service) for r in regexes), service)
        self.assertFalse(any(re.search(r, 'com.apple.securitydx') for r in regexes))

    def test_no_loopback_when_the_host_says_so(self):
        got = rules(self.command(self.provider(loopback=False))[0][2])
        self.assertIn(('deny', 'network*', []), got)
        self.assertFalse([r for r in got if r[0] == 'allow' and r[1].startswith('network')])

    def test_paths_are_quoted_as_sbpl_strings(self):
        odd = self.root / 'odd "quoted" \\ back) (allow file-write* (subpath "x"))'
        odd.mkdir()
        profile = self.command(readonly=(self.stack, odd))[0][2]
        self.assertEqual(len(rules(profile)), 11)  # the name stayed one string, it added no rule
        by = {(a, op): f for a, op, f in rules(profile)}
        self.assertIn(str(odd), paths(by[('allow', 'file-read*')], 'subpath'))
        self.assertEqual(paths(by[('allow', 'file-write*')], 'subpath'), [str(self.job), '/dev/fd'])
        for bad in ('line\nbreak', 'tab\there', 'del\x7f'):
            with self.subTest(bad=bad):
                folder = self.root / bad
                folder.mkdir()
                self.refused(readonly=(folder,))

    def test_unsupported_platform_root_and_missing_or_privileged_tool_fail_closed(self):
        for name, value in (('platform.system', 'Linux'), ('os.geteuid', 0)):
            with mock.patch(name, return_value=value):
                self.refused()
        self.tool.chmod(0o4755)
        self.refused()
        self.tool.chmod(0o644)
        self.refused()
        self.tool.unlink()
        self.refused()

    def test_mounts_must_be_canonical_disjoint_directories(self):
        alias = self.root / 'alias'
        alias.symlink_to(self.job, target_is_directory=True)
        for job in (alias, Path('relative'), self.root / 'missing', self.stack / 'bin' / '..'):
            self.refused(writable=job)
        for roots in ((self.job,), (self.stack, self.stack), (self.stack, self.stack / 'x'),
                      (self.tool,), (self.root,)):
            if roots[-1].name == 'x':
                roots[-1].mkdir()
            self.refused(readonly=roots)
        self.refused(writable=self.tool.parent)  # holds sandbox-exec

    def test_a_mount_may_sit_in_a_hidden_folder_but_never_be_or_hold_one(self):
        self.command()  # the job inside the hidden builder root is the ordinary case
        self.refused(readonly=(self.builder,))
        self.refused(readonly=(self.home,))
        outer = SeatbeltIsolation(hidden=(self.cache / 'secret',), sandbox_exec=self.tool)
        (self.cache / 'secret').mkdir()
        self.refused(outer)  # the read-only cache would expose the hidden folder inside it
        for system in ('/usr/bin', '/'):
            with self.subTest(system=system):
                self.refused(readonly=(Path(system),))
        with mock.patch.object(isolation, '_MAC_SYSTEM', (self.cache / 'lib',)):
            self.refused()  # the read-only cache holds a system folder
        with mock.patch.object(isolation, '_MAC_PRIVATE', (self.stack / 'Users',)):
            self.refused()  # the read-only stack holds /Users
        with mock.patch.object(isolation, '_MAC_PRIVATE', (self.root,)):
            self.command()  # inside it is the ordinary case: /Users/<owner>/...

    def test_hidden_folders_must_be_canonical(self):
        self.refused(SeatbeltIsolation(hidden=(Path('relative'),), sandbox_exec=self.tool))
        alias = self.root / 'builder-alias'
        alias.symlink_to(self.builder, target_is_directory=True)
        self.refused(SeatbeltIsolation(hidden=(alias,), sandbox_exec=self.tool))
        SeatbeltIsolation(hidden=(self.root / 'not-yet-made',), sandbox_exec=self.tool).command(
            [str(self.stack / 'bin/gradle')], {}, writable=self.job, readonly=())

    def test_the_command_and_environment_are_checked(self):
        self.refused(argv=['gradle'])
        self.refused(argv=[])
        self.refused(argv=['/opt/x=y/gradle'])  # env would take it as a variable
        self.refused(env={'invalid=key': 'value'})
        self.refused(env={'': 'value'})
        self.refused(env={'KEY': 'invalid\0value'})
        for name in ('DYLD_INSERT_LIBRARIES', 'DYLD_LIBRARY_PATH', 'DYLD_FRAMEWORK_PATH', 'LD_PRELOAD'):
            with self.subTest(name=name):
                self.refused(env={**self.env, name: '/host/untrusted'})


if __name__ == '__main__':
    unittest.main()
