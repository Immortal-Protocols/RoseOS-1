"""FOLD-TOOL-PROOF-001 (narrow correction): scripts/fold_tool_proof.py on task-owned fixtures only.

A fake host root holds synthetic pinned scripts and host executables under merged-/usr aliases; the host's official
package tools (dpkg-query/dpkg or rpm) are a mocked runner that records each allowlisted call. Nothing real is queried,
executed, downloaded or signed. A fixture result proves the helper's contract, not any real host."""
import copy
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

SCRIPTS = Path(__file__).resolve().parents[1]
spec_ = importlib.util.spec_from_file_location('fold_tool_proof', SCRIPTS / 'fold_tool_proof.py')
F = importlib.util.module_from_spec(spec_)
spec_.loader.exec_module(F)
REVISION = {'development': '1' * 40, 'external/avb': '2' * 40}
UID = os.getuid()


class Runner:
    """The mocked official package tools: answers keyed by argv, every call recorded."""

    def __init__(self, manager='dpkg', **override):
        self.calls = []
        if manager == 'dpkg':
            self.answers = {
                ('/usr/bin/dpkg-query', '-S', '/usr/bin/openssl'): (0, 'openssl: /usr/bin/openssl\n'),
                ('/usr/bin/dpkg-query', '-W', '-f', '${Package}\\t${Version}\\t${Architecture}\\t${Status}\\n', 'openssl'):
                    (0, 'openssl\t3.0.13-0ubuntu3.4\tamd64\tinstall ok installed\n'),
                ('/usr/bin/dpkg', '--verify', 'openssl'): (0, ''),
            }
        else:
            self.answers = {
                ('/usr/bin/rpm', '-qf', '--qf', '%{NAME}\\t%{VERSION}-%{RELEASE}\\t%{ARCH}\\n', '/usr/bin/openssl'):
                    (0, 'openssl\t3.0.7-27.el9\tx86_64\n'),
                ('/usr/bin/rpm', '-V', 'openssl'): (0, ''),
            }
        self.answers.update(override)

    def __call__(self, argv):
        self.calls.append(tuple(argv))
        return self.answers.get(tuple(argv), (1, ''))


class Fixture:
    def __init__(self, case, manager='dpkg'):
        self.root = Path(tempfile.mkdtemp(prefix='fold-proof-'))
        case.addCleanup(shutil.rmtree, self.root, True)
        self.scripts = {'generator': ('/src/development', 'tools/make_key', b'#!/bin/bash\n# synthetic make_key\n'),
                        'decrypt-keys': ('/src/script', 'decrypt-keys', b'#!/bin/bash\n# synthetic decrypt-keys\n'),
                        'avbtool': ('/src/external/avb', 'avbtool.py', b'#!/usr/bin/env python3\n# synthetic avbtool\n')}
        for alias, target in (('bin', 'usr/bin'), ('lib', 'usr/lib')):
            (self.root / target).mkdir(parents=True, exist_ok=True)
            (self.root / alias).symlink_to(target)
        for name, (root, rel, data) in self.scripts.items():
            self.write(f'{root}/{rel}', data)
        self.write('/usr/bin/bash', b'\x7fELF synthetic bash')
        self.write('/usr/bin/python3.12', b'\x7fELF synthetic python')
        (self.root / 'usr/bin/python3').symlink_to('python3.12')
        self.write('/usr/bin/openssl', b'\x7fELF synthetic openssl')
        for tool in F.MANAGERS[manager]:
            self.write(tool, b'\x7fELF synthetic package tool')
        for directory in [self.root / 'usr', self.root / 'usr/bin', self.root / 'usr/lib']:
            os.chmod(directory, 0o755)
        self.runner = Runner(manager)

    def path(self, logical):
        return self.root / logical.lstrip('/')

    def write(self, logical, data, mode=0o755):
        target = self.path(logical)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.is_symlink():
            target.unlink()
        target.write_bytes(data)
        os.chmod(target, mode)

    def spec(self):
        return {'schema': F.SPEC_SCHEMA, 'profile': F.PROFILE,
                'scripts': {name: {'root': root} for name, (root, _, _) in self.scripts.items()}}

    def profile(self):
        profile = copy.deepcopy(F.default_profile())
        for name, (root, rel, data) in self.scripts.items():
            source = profile['tools'][name]['source']
            source['path'], source['revision'] = rel, REVISION.get(root.split('/src/', 1)[1], '3' * 40)
            source['gitBlob'] = hashlib.sha1(b'blob %d\0' % len(data) + data).hexdigest()
            profile['tools'][name]['sourceSha256'] = hashlib.sha256(data).hexdigest()
        return profile

    def verify(self, spec=None, profile=None, manifest=None, runner=None, owner=UID):
        return F.verify_local(spec or self.spec(), str(self.root), profile or self.profile(),
                              dict(REVISION) if manifest is None else manifest, runner or self.runner, owner)


class Positive(unittest.TestCase):
    def test_resolves_all_four_tools_with_managed_package_trust(self):
        fx = Fixture(self)
        proof = fx.verify()
        self.assertEqual(set(proof['tools']), {'generator', 'decrypt-keys', 'avbtool', 'openssl'})
        self.assertEqual(proof['environment'], {'PATH': '/usr/bin:/bin', 'LC_ALL': 'C'})
        self.assertEqual(proof['tools']['generator']['invocation'], ['/bin/bash', '/src/development/tools/make_key'])
        self.assertEqual(proof['tools']['generator']['interpreter']['realPath'], '/usr/bin/bash')
        self.assertEqual(proof['tools']['avbtool']['interpreter'],
                         {'path': '/usr/bin/python3', 'realPath': '/usr/bin/python3.12',
                          'sha256': hashlib.sha256(b'\x7fELF synthetic python').hexdigest()})
        openssl = proof['tools']['openssl']
        self.assertEqual((openssl['path'], openssl['realPath'], openssl['trustBasis']),
                         ('/usr/bin/openssl', '/usr/bin/openssl', 'managed-host-package'))
        self.assertEqual(openssl['package'], {'manager': 'dpkg', 'name': 'openssl', 'version': '3.0.13-0ubuntu3.4',
                                              'architecture': 'amd64'})
        self.assertEqual(openssl['integrity'], {'tool': 'dpkg --verify', 'result': 'clean'})
        self.assertEqual(proof['independentPackageProof']['performed'], False)
        self.assertEqual(len(proof['independentPackageProof']['requires']), 5)
        self.assertEqual(fx.runner.calls, list(fx.runner.answers))  # exactly the allowlisted queries, in order
        self.assertTrue(F.recheck(proof, str(fx.root), UID))
        text = json.dumps(proof)
        self.assertNotIn(str(fx.root), text)
        self.assertNotIn('synthetic make_key', text)

    def test_rpm_host_without_distribution_assumption(self):
        fx = Fixture(self, 'rpm')
        proof = fx.verify()
        self.assertEqual(proof['tools']['openssl']['package'],
                         {'manager': 'rpm', 'name': 'openssl', 'version': '3.0.7-27.el9', 'architecture': 'x86_64'})
        self.assertEqual(proof['tools']['openssl']['integrity'], {'tool': 'rpm -V', 'result': 'clean'})

    def test_dpkg_owner_recorded_under_the_unmerged_path(self):
        fx = Fixture(self)
        del fx.runner.answers[('/usr/bin/dpkg-query', '-S', '/usr/bin/openssl')]
        fx.runner.answers[('/usr/bin/dpkg-query', '-S', '/bin/openssl')] = (0, 'openssl: /bin/openssl\n')
        self.assertEqual(fx.verify()['tools']['openssl']['package']['name'], 'openssl')

    def test_validate_spec(self):
        fx = Fixture(self)
        self.assertEqual(F.validate_spec(fx.spec()), [])
        for name, change in {'extra field': lambda s: s.update(vendor={}),
                             'relative root': lambda s: s['scripts']['avbtool'].update(root='src/avb'),
                             'missing script': lambda s: s['scripts'].pop('decrypt-keys'),
                             'wrong schema': lambda s: s.update(schema='roseos.fold-tool-proof-spec.v1')}.items():
            with self.subTest(name):
                spec = fx.spec()
                change(spec)
                self.assertTrue(F.validate_spec(spec))
                with self.assertRaisesRegex(F.Refused, '^spec_invalid$'):
                    fx.verify(spec=spec)


class Negative(unittest.TestCase):
    def refuse(self, fx, code, **kw):
        with self.assertRaisesRegex(F.Refused, f'^{code}$'):
            fx.verify(**kw)

    def test_wrong_source(self):
        fx = Fixture(self)
        fx.write('/src/development/tools/make_key', b'#!/bin/bash\n# replaced\n')
        self.refuse(fx, 'source_mismatch')
        fx = Fixture(self)
        self.refuse(fx, 'source_revision_mismatch', manifest=dict(REVISION, development='9' * 40))
        profile = fx.profile()
        profile['tools']['avbtool']['source']['gitBlob'] = 'f' * 40
        self.refuse(fx, 'source_mismatch', profile=profile)
        spec = fx.spec()
        spec['scripts']['generator']['root'] = '/src/script'
        self.refuse(fx, 'source_root_mismatch', spec=spec)
        profile = fx.profile()
        profile['tools']['generator']['source']['revision'] = 'short'
        self.refuse(fx, 'profile_identity_missing', profile=profile)

    def test_wrong_path(self):
        fx = Fixture(self)
        os.unlink(fx.path('/src/script/decrypt-keys'))
        self.refuse(fx, 'file_missing')
        fx = Fixture(self)
        shutil.move(fx.path('/src/development'), fx.root / 'elsewhere')
        fx.path('/src/development').symlink_to(fx.root / 'elsewhere')
        self.refuse(fx, 'symlink_ancestor')
        fx = Fixture(self)
        script = fx.path('/src/script/decrypt-keys')
        shutil.move(script, fx.root / 'moved')
        script.symlink_to(fx.root / 'moved')
        self.refuse(fx, 'file_missing')  # O_NOFOLLOW: the script itself may not be a symlink
        fx = Fixture(self)
        (fx.root / 'usr/bin').rename(fx.root / 'other-bin')
        (fx.root / 'usr/bin').symlink_to(fx.root / 'other-bin')
        self.refuse(fx, 'symlink_ancestor')

    def test_wrong_package(self):
        fx = Fixture(self)
        fx.runner.answers[('/usr/bin/dpkg-query', '-S', '/usr/bin/openssl')] = (1, '')
        self.refuse(fx, 'package_owner_missing')
        for name, answer in {'two owners': 'openssl, libssl3: /usr/bin/openssl\n',
                             'two lines': 'openssl: /usr/bin/openssl\nother: /usr/bin/openssl\n',
                             'diversion': 'diversion by local-openssl from: /usr/bin/openssl\n',
                             'other path': 'openssl: /usr/bin/openssl.dist\n'}.items():
            with self.subTest(name):
                fx = Fixture(self)
                fx.runner.answers[('/usr/bin/dpkg-query', '-S', '/usr/bin/openssl')] = (0, answer)
                self.refuse(fx, 'package_owner_ambiguous')
        fx = Fixture(self)
        key = ('/usr/bin/dpkg-query', '-W', '-f', '${Package}\\t${Version}\\t${Architecture}\\t${Status}\\n', 'openssl')
        fx.runner.answers[key] = (0, 'openssl\t3.0\tamd64\tdeinstall ok config-files\n')
        self.refuse(fx, 'package_query_failed')
        fx = Fixture(self, 'rpm')
        fx.runner.answers[('/usr/bin/rpm', '-qf', '--qf', '%{NAME}\\t%{VERSION}-%{RELEASE}\\t%{ARCH}\\n',
                           '/usr/bin/openssl')] = (0, 'openssl\t1\tx86_64\nopenssl-compat\t1\tx86_64\n')
        self.refuse(fx, 'package_owner_ambiguous')

    def test_integrity_failure(self):
        fx = Fixture(self)
        fx.runner.answers[('/usr/bin/dpkg', '--verify', 'openssl')] = (0, '??5??????   /usr/bin/openssl\n')
        self.refuse(fx, 'package_integrity_failed')
        fx = Fixture(self)
        fx.runner.answers[('/usr/bin/dpkg', '--verify', 'openssl')] = (1, '')
        self.refuse(fx, 'package_integrity_failed')
        fx = Fixture(self, 'rpm')
        fx.runner.answers[('/usr/bin/rpm', '-V', 'openssl')] = (1, 'S.5....T.    /usr/bin/openssl\n')
        self.refuse(fx, 'package_integrity_failed')

    def test_override(self):
        fx = Fixture(self)
        fx.write('/src/external/avb/avb_openssl', b'#!/bin/sh\n')
        self.refuse(fx, 'openssl_override')
        fx = Fixture(self)
        os.chmod(fx.path('/usr/bin/openssl'), 0o775)
        self.refuse(fx, 'host_file_unsafe')
        fx = Fixture(self)
        self.refuse(fx, 'host_file_unsafe', owner=UID + 1)  # not owned by the expected (root) owner
        fx = Fixture(self)
        fx.write('/opt/python3', b'\x7fELF writable python', 0o777)
        os.unlink(fx.path('/usr/bin/python3'))
        fx.path('/usr/bin/python3').symlink_to('/opt/python3')
        self.refuse(fx, 'host_file_unsafe')
        fx = Fixture(self)
        os.chmod(fx.path('/usr/bin'), 0o777)
        self.refuse(fx, 'host_file_unsafe')

    def test_missing_tool(self):
        fx = Fixture(self)
        os.unlink(fx.path('/usr/bin/openssl'))
        self.refuse(fx, 'tool_missing')
        fx = Fixture(self)
        os.unlink(fx.path('/usr/bin/dpkg'))
        self.refuse(fx, 'package_manager_missing')
        fx = Fixture(self)
        fx.write('/usr/bin/rpm', b'\x7fELF rpm')
        self.refuse(fx, 'package_manager_ambiguous')
        fx = Fixture(self)
        os.unlink(fx.path('/usr/bin/bash'))
        self.refuse(fx, 'file_missing')
        fx = Fixture(self)
        fx.write('/src/external/avb/avbtool.py', b'#!/usr/bin/env python2\n')
        profile = fx.profile()
        data = b'#!/usr/bin/env python2\n'
        profile['tools']['avbtool']['source']['gitBlob'] = hashlib.sha1(b'blob %d\0' % len(data) + data).hexdigest()
        profile['tools']['avbtool']['sourceSha256'] = hashlib.sha256(data).hexdigest()
        self.refuse(fx, 'interpreter_unsupported', profile=profile)

    def test_caller_path_cannot_substitute_a_tool(self):
        fx = Fixture(self)
        fx.write('/hijack/openssl', b'#!/bin/sh\necho hijacked\n')  # inside the fixture root, first on the caller PATH
        with mock.patch.dict(os.environ, {'PATH': f'/hijack:{os.environ.get("PATH", "")}',
                                          'OPENSSL_CONF': '/hijack/openssl.cnf'}):
            proof = fx.verify()
        self.assertEqual(proof['tools']['openssl']['realPath'], '/usr/bin/openssl')
        self.assertEqual(proof['environment'], {'PATH': '/usr/bin:/bin', 'LC_ALL': 'C'})

    def test_changed_after_proof(self):
        changes = {
            'openssl bytes': lambda fx: fx.write('/usr/bin/openssl', b'\x7fELF replaced'),
            'script bytes': lambda fx: fx.write('/src/external/avb/avbtool.py', b'#!/usr/bin/env python3\n# x\n'),
            'interpreter retarget': lambda fx: (os.unlink(fx.path('/usr/bin/python3')),
                                                fx.path('/usr/bin/python3').symlink_to('bash')),
            'avb_openssl appears': lambda fx: fx.write('/src/external/avb/avb_openssl', b'#!/bin/sh\n'),
        }
        for name, change in changes.items():
            with self.subTest(name):
                fx = Fixture(self)
                proof = fx.verify()
                change(fx)
                with self.assertRaisesRegex(F.Refused, '^changed_after_proof$'):
                    F.recheck(proof, str(fx.root), UID)


class Boundary(unittest.TestCase):
    def test_run_tool_is_fixed_bounded_and_closed(self):
        with mock.patch.object(F.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, b'ok\n')) as run:
            self.assertEqual(F.run_tool(['/usr/bin/dpkg', '--verify', 'openssl']), (0, 'ok\n'))
        kwargs = run.call_args.kwargs
        self.assertEqual((kwargs['env'], kwargs['stdin'], kwargs['stderr'], kwargs['timeout']),
                         (F.ENVIRONMENT, subprocess.DEVNULL, subprocess.DEVNULL, F.TOOL_SECONDS))
        with mock.patch.object(F.subprocess, 'run', side_effect=subprocess.TimeoutExpired('x', 1)):
            with self.assertRaisesRegex(F.Refused, '^package_query_failed$'):
                F.run_tool(['/usr/bin/rpm', '-V', 'openssl'])
        with mock.patch.object(F.subprocess, 'run',
                               return_value=subprocess.CompletedProcess([], 0, b'x' * (F.TOOL_OUTPUT_BYTES + 1))):
            with self.assertRaisesRegex(F.Refused, '^bound_exceeded$'):
                F.run_tool(['/usr/bin/rpm', '-V', 'openssl'])

    def test_cli_reports_only_a_fixed_code(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        (tmp / 'spec.json').write_text('{"schema": "SENTINEL_TOKEN"}')
        err = io.StringIO()
        with mock.patch('sys.stderr', err):
            self.assertEqual(F.main(['--spec', str(tmp / 'spec.json')]), 1)
        self.assertEqual(err.getvalue(), 'refused: spec_invalid\n')

    def test_no_custom_cryptography_or_package_parsing(self):
        source = (SCRIPTS / 'fold_tool_proof.py').read_text()
        for word in ('pow(', 'lzma', 'tarfile', 'PGP', 'DT_NEEDED', 'gzip'):
            self.assertNotIn(word, source, word)


if __name__ == '__main__':
    unittest.main()
