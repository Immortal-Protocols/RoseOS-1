#!/usr/bin/env python3
"""SSH-KEYGEN-OBSERVE: synthetic files, a temporary lock and mocked package queries only. Nothing here runs ssh-keygen,
a package manager, a host or any credential; it proves the source contract, not a builder observation."""
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
import sys
import tempfile
import unittest

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


class Observe(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        (self.tmp/'usr/bin').mkdir(parents=True)
        self.tool = self.tmp/'usr/bin/ssh-keygen'
        self.tool.write_bytes(b'synthetic ssh-keygen bytes, never executed\n')
        self.lock = self.tmp/'frankel-build.lock'
        self.lock.write_bytes(b'')
        self.calls, self.version, self.verify_output, self.category = [], PACKAGE_VERSION, b'', 'ok'
        self.on_run = None

    def sha(self):
        return hashlib.sha256(self.tool.read_bytes()).hexdigest()

    def safety(self, path, root, owner):
        self.assertEqual((root, owner), ('/', 0))
        return {'path': path, 'realPath': path, 'sha256': hashlib.sha256(Path(path).read_bytes()).hexdigest()}

    def run_query(self, argv, env, cwd, seconds, keep_output=False):
        self.calls.append({'argv': argv, 'env': env, 'cwd': cwd, 'seconds': seconds, 'keep': keep_output})
        if self.on_run:
            self.on_run(argv)
        if self.category != 'ok':
            return {'exit': None, 'category': self.category, 'outputBytes': 0, 'outputSha256': None}
        if argv[:2] == ['/usr/bin/dpkg-query', '-S']:
            out = f'openssh-client: {argv[2]}\n'.encode() if argv[2] == str(self.tool) else b''
            return {'exit': 0 if out else 1, 'category': 'ok' if out else 'nonzero_exit', 'output': out}
        if argv[:2] == ['/usr/bin/dpkg-query', '-W']:
            return {'exit': 0, 'category': 'ok',
                    'output': f'openssh-client\t{self.version}\tamd64\tinstall ok installed\n'.encode()}
        if argv[:2] == ['/usr/bin/dpkg', '--verify']:
            return {'exit': 0, 'category': 'ok', 'output': self.verify_output}
        raise AssertionError(f'unexpected argv {argv}')

    def observe(self, **kw):
        options = dict(tool=self.tool, lock=self.lock, safety=self.safety, manager=lambda root, owner: 'dpkg',
                       run=self.run_query)
        options.update(kw)
        return OBS.observe(OBS.HOST, **options)

    def test_complete_observation_is_bindable_with_exactly_the_allowlisted_queries(self):
        report = self.observe()
        self.assertEqual(report['problems'], [])
        self.assertTrue(report['complete'] and report['bindable'])
        self.assertEqual(report['tool']['sha256'], self.sha())
        self.assertEqual(report['tool']['state'], 'present')
        self.assertEqual(report['package'], {'manager': 'dpkg', 'name': 'openssh-client', 'version': PACKAGE_VERSION,
                                             'architecture': 'amd64', 'integrity': 'clean', 'integrityTool': 'dpkg --verify'})
        self.assertEqual(report['version'], {'source': 'package-manager', 'upstream': '9.6p1', 'meetsMinimum': True})
        self.assertEqual([c['argv'] for c in self.calls], [
            ['/usr/bin/dpkg-query', '-S', str(self.tool)],
            ['/usr/bin/dpkg-query', '-W', '-f', '${Package}\\t${Version}\\t${Architecture}\\t${Status}\\n', 'openssh-client'],
            ['/usr/bin/dpkg', '--verify', 'openssh-client']])
        for call in self.calls:
            self.assertEqual(call['env'], {'PATH': '/usr/bin:/bin', 'LC_ALL': 'C'})
            self.assertEqual(call['cwd'], '/')
            self.assertTrue(0 < call['seconds'] <= OBS.QUERY_SECONDS)
            self.assertTrue(call['keep'])
            self.assertNotIn('ssh-keygen', Path(call['argv'][0]).name)  # ssh-keygen itself is never run
        self.assertLessEqual(len(OBS.render(report).encode()), OBS.LIMITS['outputBytes'])

    def test_the_shared_lock_is_held_during_every_query_and_released_after(self):
        def held(argv):
            fd = os.open(self.lock, os.O_RDONLY)
            try:
                with self.assertRaises(BlockingIOError):
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            finally:
                os.close(fd)
        self.on_run = held
        self.assertTrue(self.observe()['complete'])
        fd = os.open(self.lock, os.O_RDONLY)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            os.close(fd)

    def test_a_held_absent_or_linked_lock_observes_nothing(self):
        holder = os.open(self.lock, os.O_RDONLY)
        fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            report = self.observe(safety=lambda *a: self.fail('read'))
        finally:
            os.close(holder)
        self.assertEqual(report['lock']['held'], False)
        self.assertIn('lock_held', report['problems'][0])
        self.assertNotIn('tool', report)
        self.assertEqual(self.calls, [])
        self.assertFalse(self.observe(lock=self.tmp/'absent.lock')['complete'])
        (self.tmp/'linked.lock').symlink_to(self.lock)
        linked = self.observe(lock=self.tmp/'linked.lock')
        self.assertEqual((linked['complete'], linked['bindable']), (False, False))
        self.assertIn('link', linked['problems'][0])
        self.assertEqual(self.calls, [])

    def test_a_linked_or_absent_tool_is_reported_and_not_followed_or_queried(self):
        real = self.tmp/'real-ssh-keygen'
        self.tool.rename(real)
        self.tool.symlink_to(real)
        report = self.observe(safety=lambda *a: self.fail('followed'))
        self.assertEqual(report['tool']['state'], 'link')
        self.assertNotIn('sha256', report['tool'])
        self.assertFalse(report['bindable'])
        self.assertEqual(self.calls, [])
        self.tool.unlink()
        self.assertEqual(self.observe()['problems'][0], 'tool: absent')
        shutil.rmtree(self.tmp/'usr/bin')
        (self.tmp/'usr/bin').symlink_to(self.tmp)
        self.assertEqual(self.observe()['tool']['state'], 'link')  # a linked ancestor is never traversed

    def test_an_unsafe_or_differently_hashed_tool_is_not_bindable(self):
        def unsafe(*a):
            raise OBS.PROOF.Refused('host_file_unsafe')
        report = self.observe(safety=unsafe)
        self.assertEqual(report['problems'], ['tool: host_file_unsafe'])
        self.assertEqual(self.calls, [])
        other = self.observe(safety=lambda path, root, owner: {'realPath': path, 'sha256': '0' * 64})
        self.assertEqual(other['problems'], ['tool: changed'])

    def test_versions_below_or_outside_the_openssh_shape_are_not_bindable(self):
        for version, expected in (('1:8.3p1-1', 'version: 8.3p1 is below 8.4'), ('8.4p1-5+deb11u3', None),
                                  ('8.7p1-38.el9', None), ('10.0p1-1', None), ('garbage', 'version: not an OpenSSH upstream version')):
            with self.subTest(version=version):
                self.version, self.calls = version, []
                report = self.observe()
                self.assertEqual(report['problems'], [expected] if expected else [])
                self.assertEqual(report['bindable'], expected is None)

    def test_a_failed_integrity_check_or_bounded_query_is_a_truthful_refusal(self):
        self.verify_output = b'??5?????? /usr/bin/ssh-keygen\n'
        self.assertEqual(self.observe()['problems'], ['package: package_integrity_failed'])
        self.verify_output = b''
        for category in ('timeout', 'output_overflow', 'spawn_failed'):
            with self.subTest(category=category):
                self.category, self.calls = category, []
                report = self.observe()
                self.assertEqual(report['problems'], [f'package: query_{category}'])
                self.assertEqual(report['package'], {'refused': f'query_{category}'})
                self.assertEqual(len(self.calls), 1)
                self.assertNotIn('version', report)

    def test_a_tool_changed_during_the_observation_is_reported(self):
        self.on_run = lambda argv: argv[1] == '--verify' and self.tool.write_bytes(b'replaced during observation\n')
        report = self.observe()
        self.assertEqual(report['problems'], ['recheck: changed'])
        self.assertFalse(report['bindable'])

    def test_the_deadline_stops_queries_from_starting(self):
        ticks = iter([0, 0, 0, OBS.LIMITS['seconds'] + 1] + [OBS.LIMITS['seconds'] + 1] * 50)
        report = self.observe(clock=lambda: next(ticks))
        self.assertFalse(report['complete'])
        self.assertIn('deadline: the observation exceeded its time bound', report['problems'])
        self.assertEqual(self.calls, [])

    def test_only_the_named_builder_and_the_fixed_path(self):
        with self.assertRaises(ValueError):
            OBS.observe('another-host', lock=self.lock)
        self.assertEqual(str(OBS.TOOL), '/usr/bin/ssh-keygen')
        with contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(OBS.main(['observe', '--host', 'another-host']), 2)
        self.assertIn('INVALID', err.getvalue())
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            OBS.main(['observe', '--host', OBS.HOST, '--path', '/tmp/x'])

    def test_the_lock_path_is_the_committed_shared_builder_lock(self):
        pipeline = json.loads((ROOT/'os/release-pipeline/frankel.json').read_text())
        self.assertEqual(OBS.lock_path(), Path(pipeline['host']['lockPath']))
        self.assertEqual(str(OBS.lock_path()), '/home/pixelbuild/frankel-build.lock')

    def test_the_version_route_is_established_from_openssh_source(self):
        self.assertIn('-V is parse_cert_times', OBS.SOURCE_BASIS)
        self.assertIn('V_8_4_P1', OBS.SOURCE_BASIS)
        self.assertEqual(OBS.MINIMUM, (8, 4))
        self.assertIn('SSH_ASKPASS_REQUIRE', OBS.__doc__)
        self.assertEqual(OBS.upstream_version('1:9.6p1-3ubuntu13.5'), ('9.6p1', (9, 6)))
        self.assertIsNone(OBS.upstream_version('9'))

    def test_bounded_runner_maps_run_bounded_results(self):
        budget = OBS.Budget()
        seen = []
        runner = OBS.bounded_runner(budget, lambda argv, env, cwd, seconds, keep_output: seen.append(seconds) or
                                    {'exit': 1, 'category': 'nonzero_exit', 'outputBytes': 0})
        self.assertEqual(runner(['/usr/bin/dpkg-query', '-S', '/x']), (1, ''))
        self.assertLessEqual(seen[0], OBS.QUERY_SECONDS)
        self.assertEqual(OBS.QUERY_OUTPUT_BYTES, OBS.SIGNING.LIMITS['outputBytes'])  # run_bounded's own cap
        expired = OBS.Budget(clock=iter([0] + [10 ** 6] * 5).__next__)
        with self.assertRaises(OBS.PROOF.Refused):
            OBS.bounded_runner(expired, lambda *a, **k: self.fail('started'))(['/usr/bin/dpkg', '--verify', 'x'])


class Bind(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.report = {'schema': OBS.SCHEMA, 'host': OBS.HOST, 'complete': True, 'bindable': True, 'problems': [],
                       'lock': {'held': True, 'path': '/home/pixelbuild/frankel-build.lock'},
                       'tool': {'path': '/usr/bin/ssh-keygen', 'state': 'present', 'sha256': 'a' * 64},
                       'package': {'manager': 'dpkg', 'name': 'openssh-client', 'version': PACKAGE_VERSION,
                                   'architecture': 'amd64', 'integrity': 'clean'},
                       'version': {'source': 'package-manager', 'upstream': '9.6p1', 'meetsMinimum': True}}

    def data(self, report=None):
        return json.dumps(report or self.report).encode()

    def test_a_complete_observation_yields_the_profile_and_binding_fields(self):
        fields, problems = OBS.binding_fields(self.data())
        self.assertEqual(problems, [])
        self.assertEqual(fields['installed'], {'path': '/usr/bin/ssh-keygen', 'sha256': 'a' * 64})
        self.assertEqual(fields['binding'], {'sshKeygen': {'path': '/usr/bin/ssh-keygen', 'sha256': 'a' * 64}})
        self.assertEqual(fields['observedVersion']['upstream'], '9.6p1')
        self.assertEqual(fields['observationSha256'], hashlib.sha256(self.data()).hexdigest())

    def test_the_fields_satisfy_the_existing_consumers(self):
        fields, _ = OBS.binding_fields(self.data())
        binding = json.loads((ROOT/'os/retained-rango/factory-signing.json').read_text())
        before = SIGN.binding_problems(binding)
        self.assertIn('binding.sshKeygen.path/sha256 (measured installed identity)', before)
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
        self.assertIsNone(binding['sshKeygen']['path'])
        self.assertIsNone(binding['sshKeygen']['sha256'])
        profiles = json.loads((ROOT/'os/signing-bootstrap/profiles.json').read_text())['profiles']
        for name in ('fold-rango-factory-v1', 'pro-blazer-factory-v1'):
            self.assertEqual(profiles[name]['tools']['ssh-keygen']['installed'], {'path': None, 'sha256': None})

    def test_anything_less_than_a_complete_consistent_observation_is_refused(self):
        cases = {
            'incomplete': lambda r: r.update(complete=False),
            'problems': lambda r: r.update(problems=['tool: absent']),
            'host': lambda r: r.update(host='elsewhere'),
            'lock': lambda r: r.update(lock={'held': False}),
            'path': lambda r: r['tool'].update(path='/usr/local/bin/ssh-keygen'),
            'sha': lambda r: r['tool'].update(sha256='A' * 64),
            'state': lambda r: r['tool'].update(state='link'),
            'integrity': lambda r: r['package'].update(integrity='failed'),
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
