#!/usr/bin/env python3
"""SSH-KEYGEN-OBSERVE: synthetic files, a synthetic dpkg database and a temporary lock only. Nothing here executes a
process, touches a host, a real package database or any credential; it proves the source contract, not a builder
observation."""
import contextlib
import copy
import fcntl
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT/path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


OBS = load('ssh_keygen_observe', 'scripts/ssh-keygen-observe.py')
SIGN = load('factory_archive_sign_for_ssh_keygen_test', 'scripts/factory-archive-sign.py')
BOOT = load('signing_bootstrap_for_ssh_keygen_test', 'scripts/signing-bootstrap.py')
PACKAGE_VERSION = '1:9.6p1-3ubuntu13.5'
TOOL_BYTES = b'synthetic ssh-keygen bytes, never executed\n'


def status(version=PACKAGE_VERSION, state='install ok installed', extra=''):
    return (f'Package: libc6\nStatus: install ok installed\nVersion: 2.39-0ubuntu8\nArchitecture: amd64\n\n'
            f'Package: {OBS.PACKAGE}\nStatus: {state}\nPriority: standard\nArchitecture: amd64\nVersion: {version}\n'
            f'Description: secure shell (SSH) client\n Package: not-a-field continuation\n{extra}\n'
            f'Package: openssh-server\nStatus: install ok installed\nVersion: {version}\nArchitecture: amd64\n').encode()


class Observe(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        (self.tmp/'usr/bin').mkdir(parents=True)
        self.tool = self.tmp/'usr/bin/ssh-keygen'
        self.tool.write_bytes(TOOL_BYTES)
        self.tool.chmod(0o755)
        self.dpkg = self.tmp/'var/lib/dpkg'
        (self.dpkg/'info').mkdir(parents=True)
        (self.dpkg/'status').write_bytes(status())
        (self.dpkg/'info/openssh-client.list').write_text(f'/.\n/usr\n/usr/bin\n{self.tool}\n/usr/bin/ssh\n')
        self.write_md5sums(hashlib.md5(TOOL_BYTES).hexdigest())
        (self.dpkg/'diversions').write_text('/usr/bin/other\n/usr/bin/other.distrib\nsomepkg\n')
        self.lock = self.tmp/'frankel-build.lock'
        self.lock.write_bytes(b'')

    def write_md5sums(self, digest):
        relative = str(self.tool).lstrip('/')
        (self.dpkg/'info/openssh-client.md5sums').write_text(f'{"0" * 32}  usr/bin/ssh\n{digest}  {relative}\n')

    def observe(self, **kw):
        options = dict(tool=self.tool, dpkg=self.dpkg, lock=self.lock, root_owned=False)
        options.update(kw)
        return OBS.observe(OBS.HOST, **options)

    def test_complete_observation_is_bindable_and_reads_only_the_fixed_files(self):
        with mock.patch.object(subprocess, 'Popen', side_effect=AssertionError('executed')), \
                mock.patch.object(os, 'execv', side_effect=AssertionError('executed')):
            report = self.observe()
        self.assertEqual(report['problems'], [])
        self.assertTrue(report['complete'] and report['bindable'])
        self.assertEqual(sorted(report['files']), ['diversions', 'list', 'md5sums', 'status', 'tool'])
        self.assertEqual(report['files']['tool']['sha256'], hashlib.sha256(TOOL_BYTES).hexdigest())
        self.assertEqual(report['package'], {'manager': 'dpkg', 'source': 'database', 'name': 'openssh-client',
                                             'version': PACKAGE_VERSION, 'architecture': 'amd64',
                                             'status': 'install ok installed', 'listsTool': True,
                                             'md5sumMatches': True, 'diverted': False})
        self.assertEqual(report['version'], {'source': 'package-database', 'upstream': '9.6p1', 'meetsMinimum': True})
        sizes = sum(row['size'] for row in report['files'].values())
        self.assertEqual(report['bytesRead'], 2 * sizes)  # one read and one recheck of each file
        self.assertLessEqual(len(OBS.render(report).encode()), OBS.LIMITS['outputBytes'])
        self.assertNotIn('Description', OBS.render(report))  # no database content beyond the parsed fields

    def test_the_cli_paths_and_limits_are_fixed_in_source(self):
        self.assertEqual({role: str(path) for role, (path, _) in OBS.files().items()}, {
            'tool': '/usr/bin/ssh-keygen', 'status': '/var/lib/dpkg/status',
            'list': '/var/lib/dpkg/info/openssh-client.list', 'md5sums': '/var/lib/dpkg/info/openssh-client.md5sums',
            'diversions': '/var/lib/dpkg/diversions'})
        self.assertEqual(OBS.LIMITS['seconds'], 120)
        first_pass = OBS.LIMITS['toolBytes'] + OBS.LIMITS['statusBytes'] + 3 * OBS.LIMITS['recordBytes']
        self.assertGreaterEqual(OBS.LIMITS['totalBytes'], 2 * first_pass)
        pipeline = json.loads((ROOT/'os/release-pipeline/frankel.json').read_text())
        self.assertEqual(OBS.lock_path(), Path(pipeline['host']['lockPath']))
        self.assertEqual(str(OBS.lock_path()), '/home/pixelbuild/frankel-build.lock')
        source = (ROOT/'scripts/ssh-keygen-observe.py').read_text()
        for executes in ('subprocess', 'os.system', 'os.exec', 'Popen', 'run_bounded'):
            self.assertNotIn(executes, source.split('"""', 2)[2])

    def test_the_shared_lock_is_held_while_reading_and_released_after(self):
        real = OBS.read_file

        def held(*args, **kwargs):
            fd = os.open(self.lock, os.O_RDONLY)
            try:
                with self.assertRaises(BlockingIOError):
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            finally:
                os.close(fd)
            return real(*args, **kwargs)
        with mock.patch.object(OBS, 'read_file', side_effect=held):
            self.assertTrue(self.observe()['complete'])
        fd = os.open(self.lock, os.O_RDONLY)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            os.close(fd)

    def test_a_held_absent_or_linked_lock_reads_nothing(self):
        holder = os.open(self.lock, os.O_RDONLY)
        fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            with mock.patch.object(OBS, 'read_file', side_effect=AssertionError('read')):
                report = self.observe()
        finally:
            os.close(holder)
        self.assertEqual((report['complete'], report['lock']['held']), (False, False))
        self.assertIn('lock_held', report['problems'][0])
        self.assertNotIn('files', report)
        self.assertFalse(self.observe(lock=self.tmp/'absent.lock')['complete'])
        (self.tmp/'linked.lock').symlink_to(self.lock)
        self.assertIn('link', self.observe(lock=self.tmp/'linked.lock')['problems'][0])

    def test_links_are_reported_never_followed(self):
        real = self.tmp/'real-ssh-keygen'
        self.tool.rename(real)
        self.tool.symlink_to(real)
        report = self.observe()
        self.assertEqual(report['files']['tool'], {'path': str(self.tool), 'state': 'link'})
        self.assertIn('tool: link', report['problems'])
        self.assertFalse(report['bindable'])
        self.assertNotIn('md5sumMatches', report['package'])
        shutil.move(self.dpkg/'info', self.tmp/'info-real')
        (self.dpkg/'info').symlink_to(self.tmp/'info-real')
        linked = self.observe()
        self.assertEqual(linked['files']['list']['state'], 'link')  # a linked ancestor is never traversed
        self.assertEqual(linked['files']['md5sums']['state'], 'link')

    def test_missing_or_wrong_database_records_are_truthful_refusals(self):
        cases = {
            'no status': (lambda: (self.dpkg/'status').unlink(), 'status: absent'),
            'not installed': (lambda: (self.dpkg/'status').write_bytes(status(state='deinstall ok config-files')),
                              'package: status_not_installed'),
            'ambiguous': (lambda: (self.dpkg/'status').write_bytes(status(extra=f'\nPackage: {OBS.PACKAGE}\n'
                                                                              'Status: install ok installed\n')),
                          'package: status_package_ambiguous'),
            'missing': (lambda: (self.dpkg/'status').write_bytes(b'Package: other\nStatus: install ok installed\n'),
                        'package: status_package_missing'),
            'list': (lambda: (self.dpkg/'info/openssh-client.list').write_text('/usr/bin/ssh\n'),
                     'package: openssh-client.list does not name the tool'),
            'md5 mismatch': (lambda: self.write_md5sums('f' * 32), 'package: md5sums_mismatch'),
            'md5 missing': (lambda: (self.dpkg/'info/openssh-client.md5sums').write_text('x  usr/bin/ssh\n'),
                            'package: md5sums_missing_tool'),
            'diverted': (lambda: (self.dpkg/'diversions').write_text(f'{self.tool}\n{self.tool}.real\nlocal\n'),
                         'package: tool_diverted'),
            'old': (lambda: (self.dpkg/'status').write_bytes(status(version='1:8.3p1-1')), 'version: 8.3p1 is below 8.4'),
            'shape': (lambda: (self.dpkg/'status').write_bytes(status(version='garbage')),
                      'version: not an OpenSSH upstream version'),
            'tool absent': (lambda: self.tool.unlink(), 'tool: absent'),
        }
        for name, (change, expected) in cases.items():
            with self.subTest(case=name):
                self.setUp()
                change()
                report = self.observe()
                self.assertIn(expected, report['problems'])
                self.assertEqual((report['complete'], report['bindable']), (False, False))
        self.setUp()
        (self.dpkg/'diversions').unlink()
        self.assertTrue(self.observe()['complete'])  # no diversions file means no diversions
        for version in ('8.4p1-5+deb11u3', '10.0p1-1'):
            (self.dpkg/'status').write_bytes(status(version=version))
            self.assertTrue(self.observe()['complete'], version)

    def test_byte_caps_and_the_deadline_are_enforced_before_reading(self):
        with mock.patch.dict(OBS.LIMITS, statusBytes=16):
            report = self.observe()
        self.assertEqual(report['files']['status']['state'], 'too-large')
        self.assertNotIn('sha256', report['files']['status'])
        with mock.patch.dict(OBS.LIMITS, totalBytes=len(TOOL_BYTES)):
            self.assertIn('status: too-large', self.observe()['problems'])
        ticks = iter([0] + [OBS.LIMITS['seconds'] + 1] * 200)
        late = self.observe(clock=lambda: next(ticks))
        self.assertFalse(late['complete'])
        self.assertEqual(late['files']['tool']['state'], 'deadline')
        self.assertEqual(late['bytesRead'], 0)

    def test_a_file_changed_during_the_observation_is_reported(self):
        real = OBS.read_file
        calls = []

        def change(path, *args, **kwargs):
            calls.append(path)
            if len(calls) == 6:  # first recheck read, of the tool
                self.tool.write_bytes(b'replaced during observation\n')
            return real(path, *args, **kwargs)
        with mock.patch.object(OBS, 'read_file', side_effect=change):
            report = self.observe()
        self.assertEqual(report['problems'], ['recheck: tool changed'])
        self.assertFalse(report['bindable'])

    def test_root_ownership_is_required_of_the_tool_and_its_ancestors(self):
        budget = OBS.Budget()
        row, _ = OBS.read_file(self.tool, OBS.LIMITS['toolBytes'], budget, owner=True)
        self.assertEqual(row['state'], 'present' if os.getuid() == 0 else 'unsafe-ancestor')
        self.tool.chmod(0o775)
        row, _ = OBS.read_file(self.tool, OBS.LIMITS['toolBytes'], budget, owner=True)
        self.assertIn(row['state'], ('unsafe-file', 'unsafe-ancestor'))
        self.tool.chmod(0o644)
        row, _ = OBS.read_file(self.tool, OBS.LIMITS['toolBytes'], budget, owner=True)
        self.assertIn(row['state'], ('unsafe-file', 'unsafe-ancestor'))
        self.tool.chmod(0o755)
        (self.tmp/'usr').chmod(0o777)
        self.assertFalse(OBS.ancestors_safe(self.tool))
        (self.tmp/'usr').chmod(0o755)
        report = self.observe(root_owned=True)  # the temporary tree sits under a world-writable /tmp
        if os.stat('/tmp').st_mode & 0o022 or os.getuid() != 0:
            self.assertEqual(report['files']['tool']['state'], 'unsafe-ancestor')
            self.assertFalse(report['bindable'])

    def test_only_the_named_builder_and_exit_codes(self):
        with self.assertRaises(ValueError):
            OBS.observe('another-host', lock=self.lock)
        with contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(OBS.main(['observe', '--host', 'another-host']), 2)
        self.assertIn('INVALID', err.getvalue())
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            OBS.main(['observe', '--host', OBS.HOST, '--path', '/tmp/x'])
        complete, partial = self.observe(), self.observe(lock=self.tmp/'absent.lock')
        for report, code in ((complete, 0), (partial, 3)):
            with mock.patch.object(OBS, 'observe', return_value=report), \
                    contextlib.redirect_stdout(io.StringIO()) as out:
                self.assertEqual(OBS.main(['observe', '--host', OBS.HOST]), code)
            self.assertEqual(json.loads(out.getvalue())['complete'], code == 0)

    def test_the_version_route_is_established_from_openssh_source(self):
        self.assertIn('-V is parse_cert_times', OBS.SOURCE_BASIS)
        self.assertIn('V_8_4_P1', OBS.SOURCE_BASIS)
        self.assertEqual(OBS.MINIMUM, (8, 4))
        self.assertIn('PACKAGE-REPORTED version', OBS.__doc__)
        self.assertEqual(OBS.upstream_version('1:9.6p1-3ubuntu13.5'), ('9.6p1', (9, 6)))
        self.assertIsNone(OBS.upstream_version('9'))


class Bind(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.report = {'schema': OBS.SCHEMA, 'host': OBS.HOST, 'complete': True, 'bindable': True, 'problems': [],
                       'lock': {'held': True, 'path': '/home/pixelbuild/frankel-build.lock'},
                       'files': {'tool': {'path': '/usr/bin/ssh-keygen', 'state': 'present', 'sha256': 'a' * 64}},
                       'package': {'manager': 'dpkg', 'source': 'database', 'name': 'openssh-client',
                                   'version': PACKAGE_VERSION, 'architecture': 'amd64', 'status': 'install ok installed',
                                   'listsTool': True, 'md5sumMatches': True, 'diverted': False},
                       'version': {'source': 'package-database', 'upstream': '9.6p1', 'meetsMinimum': True}}

    def data(self, report=None):
        return json.dumps(report or self.report).encode()

    def test_a_complete_observation_yields_the_profile_and_binding_fields(self):
        fields, problems = OBS.binding_fields(self.data())
        self.assertEqual(problems, [])
        self.assertEqual(fields['installed'], {'path': '/usr/bin/ssh-keygen', 'sha256': 'a' * 64})
        self.assertEqual(fields['binding'], {'sshKeygen': {'path': '/usr/bin/ssh-keygen', 'sha256': 'a' * 64}})
        self.assertEqual(fields['observedVersion']['source'], 'package-database')
        self.assertEqual(fields['observedVersion']['upstream'], '9.6p1')
        self.assertEqual(fields['observationSha256'], hashlib.sha256(self.data()).hexdigest())

    def test_the_fields_satisfy_the_existing_consumers(self):
        fields, _ = OBS.binding_fields(self.data())
        binding = json.loads((ROOT/'os/retained-rango/factory-signing.json').read_text())
        self.assertIn('binding.sshKeygen.path/sha256 (measured installed identity)', SIGN.binding_problems(binding))
        binding.update(fields['binding'])
        self.assertNotIn('binding.sshKeygen.path/sha256 (measured installed identity)', SIGN.binding_problems(binding))
        profiles = json.loads((ROOT/'os/signing-bootstrap/profiles.json').read_text())['profiles']
        for name in ('fold-rango-factory-v1', 'pro-blazer-factory-v1'):
            identity = copy.deepcopy(profiles[name]['tools']['ssh-keygen'])
            self.assertFalse(BOOT.tool_approved(identity))
            identity['installed'] = dict(fields['installed'])
            self.assertTrue(BOOT.tool_approved(identity))
            self.assertEqual(BOOT.tool_path(identity), '/usr/bin/ssh-keygen')

    def test_the_committed_identities_stay_unbound_until_measured(self):
        binding = json.loads((ROOT/'os/retained-rango/factory-signing.json').read_text())
        self.assertEqual((binding['sshKeygen']['path'], binding['sshKeygen']['sha256']), (None, None))
        profiles = json.loads((ROOT/'os/signing-bootstrap/profiles.json').read_text())['profiles']
        for name in ('fold-rango-factory-v1', 'pro-blazer-factory-v1'):
            self.assertEqual(profiles[name]['tools']['ssh-keygen']['installed'], {'path': None, 'sha256': None})

    def test_anything_less_than_a_complete_consistent_observation_is_refused(self):
        cases = {
            'incomplete': lambda r: r.update(complete=False),
            'problems': lambda r: r.update(problems=['tool: absent']),
            'host': lambda r: r.update(host='elsewhere'),
            'lock': lambda r: r.update(lock={'held': False}),
            'path': lambda r: r['files']['tool'].update(path='/usr/local/bin/ssh-keygen'),
            'sha': lambda r: r['files']['tool'].update(sha256='A' * 64),
            'state': lambda r: r['files']['tool'].update(state='link'),
            'md5': lambda r: r['package'].update(md5sumMatches=False),
            'list': lambda r: r['package'].update(listsTool=False),
            'diverted': lambda r: r['package'].update(diverted=True),
            'status': lambda r: r['package'].update(status='deinstall ok config-files'),
            'old': lambda r: (r['package'].update(version='1:8.3p1-1'), r['version'].update(upstream='8.3p1')),
            'inconsistent': lambda r: r['version'].update(upstream='9.9p1'),
            'schema': lambda r: r.update(schema='other'),
        }
        for name, change in cases.items():
            with self.subTest(case=name):
                report = copy.deepcopy(self.report)
                change(report)
                fields, problems = OBS.binding_fields(self.data(report))
                self.assertIsNone(fields)
                self.assertTrue(problems)
        self.assertEqual(OBS.binding_fields(b'not json'), (None, ['observation: not JSON']))
        self.assertEqual(OBS.binding_fields(b' ' * (OBS.LIMITS['observationBytes'] + 1)),
                         (None, ['observation: larger than observationBytes']))

    def test_cli_exit_codes(self):
        good, bad = self.tmp/'good.json', self.tmp/'bad.json'
        good.write_bytes(self.data())
        bad.write_bytes(self.data(dict(self.report, complete=False)))
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(OBS.main(['bind', '--observation', str(good)]), 0)
        self.assertEqual(json.loads(out.getvalue())['binding']['sshKeygen']['sha256'], 'a' * 64)
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(OBS.main(['bind', '--observation', str(bad)]), 4)
        self.assertFalse(json.loads(out.getvalue())['bindable'])
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(OBS.main(['bind', '--observation', str(self.tmp/'missing.json')]), 2)


if __name__ == '__main__':
    unittest.main()
