#!/usr/bin/env python3
"""RELEASE020 M2 transport: the builder's signing-job package and acceptance of the signer's outputs (no network)."""
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import shutil
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT/path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


rt = load('release_transport', 'scripts/release-transport.py')
sj = load('signing_job', 'scripts/signing-job.py')
OS_SIGN = (ROOT/'.github/workflows/os-sign.yml').read_text()
CANDIDATE = '5dd3d54ff84ff76646a01a5dad01929d0ecf7710'


class Fixture:
    def __init__(self, test):
        self.dir = Path(tempfile.mkdtemp(prefix='rose-transport-'))
        test.addCleanup(shutil.rmtree, self.dir, True)
        (self.dir/'release').mkdir()
        (self.dir/'release/unsigned-target_files.zip').write_bytes(b'unsigned target files')
        (self.dir/'otatools.zip').write_bytes(b'otatools')
        (self.dir/'UPDATE.md').write_text('RoseOS Pixel 10 (frankel) Beta build 2026103004\n')
        self.out = self.dir/'out'

    def package(self, **change):
        args = dict(release_dir=self.dir/'release', otatools=self.dir/'otatools.zip', device='frankel', build='2026103004',
            timestamp='1793390000', candidate=CANDIDATE, notes=self.dir/'UPDATE.md', config='os/release-pipeline/frankel.json',
            job_id='frankel-2026103004-41112579187', out=self.out)
        args.update(change)
        return rt.package(**args)


class Package(unittest.TestCase):
    def test_manifest_is_a_signing_job_with_signer_inbox_paths(self):
        f = Fixture(self)
        result = f.package()
        job = f.out/'frankel-2026103004-41112579187'
        data = (job/'manifest.json').read_bytes()
        self.assertEqual(result['manifestSha256'], hashlib.sha256(data).hexdigest())
        m = json.loads(data)
        for key, name in rt.INPUTS:
            self.assertEqual(m['inputs'][key]['path'], f'/srv/rose-signing/inbox/frankel-2026103004-41112579187/{name}')
            self.assertEqual(m['inputs'][key]['sha256'], hashlib.sha256((job/name).read_bytes()).hexdigest())
        self.assertEqual(m['config'], {'path': 'os/release-pipeline/frankel.json',
            'sha256': hashlib.sha256((ROOT/'os/release-pipeline/frankel.json').read_bytes()).hexdigest()})
        # signing-job.py's own validator and config binding accept it (the signer-side contract, unchanged).
        manifest = job/'manifest.json'
        self.assertEqual(sj.load_manifest(manifest)['build'], '2026103004')
        self.assertEqual(sj.load_config(sj.load_manifest(manifest))['target']['device'], 'frankel')
        self.assertEqual(oct(job.stat().st_mode & 0o777), '0o700')

    def test_paths_match_the_os_sign_inbox_check(self):
        f = Fixture(self)
        f.package()
        m = json.loads((f.out/'frankel-2026103004-41112579187/manifest.json').read_text())
        pattern = re.escape('/srv/rose-signing/inbox') + r'/[A-Za-z0-9._-]+/manifest\.json'
        self.assertIn("re.fullmatch(re.escape(str(inbox)) + r'/[A-Za-z0-9._-]+/manifest\\.json', manifest)", OS_SIGN)
        self.assertIn('SIGNING_ROOT: /srv/rose-signing', OS_SIGN)
        manifest_path = '/srv/rose-signing/inbox/frankel-2026103004-41112579187/manifest.json'
        self.assertRegex(manifest_path, pattern)
        for spec in m['inputs'].values():
            self.assertEqual(str(Path(spec['path']).parent), str(Path(manifest_path).parent))

    def test_refusals(self):
        f = Fixture(self)
        for change, error in (({'device': 'Frankel'}, rt.Invalid), ({'build': '20261030'}, rt.Invalid),
                              ({'candidate': 'x' * 40}, rt.Invalid), ({'job_id': '../x'}, rt.Invalid),
                              ({'job_id': '..'}, rt.Invalid), ({'config': '/etc/passwd'}, rt.Invalid),
                              ({'signing_root': 'srv'}, rt.Invalid), ({'device': 'rango'}, rt.Refused),
                              ({'otatools': f.dir/'missing.zip'}, rt.Refused), ({'build': '2026103009'}, rt.Refused)):
            with self.subTest(change=change), self.assertRaises(error):
                f.package(**change)
        f.package()
        with self.assertRaisesRegex(rt.Refused, 'job_exists'):
            f.package()


def signed_outputs(test, f, **receipt_change):
    """The os-sign artifact layout: signing-receipt.json plus the public files, as signing-job.py commits them."""
    m = json.loads((f.out/'frankel-2026103004-41112579187/manifest.json').read_text())
    signed = f.dir/'signed'
    signed.mkdir()
    for name in rt.public_assets('frankel', '2026103004'):
        (signed/name).write_bytes((f.dir/'UPDATE.md').read_bytes() if name == 'UPDATE.md' else name.encode())
    receipt = {'schema': rt.RECEIPT_SCHEMA, 'jobId': 'frankel-2026103004', 'verdict': 'signed', **{k: m[k] for k in
        ('device', 'build', 'buildTimestamp', 'candidateSha', 'config', 'inputs')}, 'identities': {'otaCertificateSha256': 'a' * 64},
        'outputs': {'signed-target_files.zip': 'b' * 64, **{f'public/{p.name}': rt.sha256_file(p) for p in signed.iterdir()}}}
    receipt.update(receipt_change)
    (signed/'signing-receipt.json').write_text(json.dumps(receipt))
    return signed


class Accept(unittest.TestCase):
    def test_bound_outputs_are_accepted(self):
        f = Fixture(self)
        f.package()
        result = rt.accept(f.out/'frankel-2026103004-41112579187', signed_outputs(self, f))
        self.assertEqual((result['build'], result['identities']), ('2026103004', {'otaCertificateSha256': 'a' * 64}))

    def test_unbound_or_changed_outputs_refuse(self):
        f = Fixture(self)
        f.package()
        job = f.out/'frankel-2026103004-41112579187'
        m = json.loads((job/'manifest.json').read_text())
        for change, error in (({'verdict': 'FAIL'}, 'verdict'), ({'build': '2026103005'}, 'binding'),
                              ({'candidateSha': 'c' * 40}, 'binding'),
                              ({'inputs': dict(m['inputs'], otatools=dict(m['inputs']['otatools'], sha256='d' * 64))}, 'binding'),
                              ({'schema': 'other'}, 'schema')):
            signed = signed_outputs(self, f, **change)
            with self.subTest(change=list(change)), self.assertRaisesRegex(rt.Refused, error):
                rt.accept(job, signed)
            shutil.rmtree(signed)
        signed = signed_outputs(self, f)
        (signed/'frankel-stable').write_text('tampered')
        with self.assertRaisesRegex(rt.Refused, 'output_changed'):
            rt.accept(job, signed)
        shutil.rmtree(signed)
        signed = signed_outputs(self, f)
        (signed/'extra.bin').write_text('x')
        with self.assertRaisesRegex(rt.Refused, 'outputs'):
            rt.accept(job, signed)

    def test_main_exit_codes(self):
        f = Fixture(self)
        self.assertEqual(rt.main(['accept', '--job', str(f.dir/'none'), '--signed', str(f.dir/'none')]), 1)
        self.assertEqual(rt.main(['package', '--release-dir', str(f.dir/'release'), '--otatools', str(f.dir/'otatools.zip'),
            '--device', 'frankel', '--build', 'bad', '--timestamp', '1793390000', '--candidate', CANDIDATE, '--notes',
            str(f.dir/'UPDATE.md'), '--config', 'os/release-pipeline/frankel.json', '--job-id', 'j', '--out', str(f.out)]), 2)


class Workflow(unittest.TestCase):
    def test_packaging_is_opt_in_local_and_before_signing(self):
        wf = (ROOT/'.github/workflows/os-release.yml').read_text()
        builder = wf.split('\n  builder:\n', 1)[1].split('\n  app-builder:\n', 1)[0]
        step = builder.split('      - name: Package the signing job for the dedicated signer (opt-in)\n', 1)[1].split('\n      - name:', 1)[0]
        self.assertIn("        if: inputs.mode == 'latest-beta' && vars.ROSE_SIGNING_TRANSPORT == 'package'\n", step)
        self.assertIn('python3 scripts/release-transport.py package', step)
        for forbidden in ('secrets.', 'gcp-login', 'upload-artifact', 'scp ', 'ssh ', 'curl'):
            self.assertNotIn(forbidden, step)
        order = [builder.index(n) for n in ('- name: Verify the unsigned build', '- name: Package the signing job',
                                            '- name: Signer access for OS signing', '- name: Sign\n')]
        self.assertEqual(order, sorted(order))


if __name__ == '__main__':
    unittest.main()
