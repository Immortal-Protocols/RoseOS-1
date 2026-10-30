#!/usr/bin/env python3
"""SIGN-020 signing-job.py: hash-bound inputs, isolated workspaces, durable commit, reuse and recovery without re-signing.
The signer is a SYNTHETIC stand-in for host-sign.py with its durable layout; no secret, key or network is used."""
import base64
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import struct
import sys
import tempfile
import unittest
import zipfile

SCRIPTS = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('signing_job', SCRIPTS/'signing-job.py')
job = importlib.util.module_from_spec(spec)
spec.loader.exec_module(job)

CERT = b'-----BEGIN CERTIFICATE-----\n' + base64.b64encode(b'SYNTHETIC certificate DER') + b'\n-----END CERTIFICATE-----\n'
PKMD = b'SYNTHETIC avb public key'
BUILD, STAMP, SHA = '2026103101', '1793400000', 'c' * 40


def signed_zip():
    certs = io.BytesIO()
    with zipfile.ZipFile(certs, 'w') as z:
        z.writestr('keys/releasekey.x509.pem', CERT)
    header = (b'AVB0' + struct.pack('>II', 1, 0) + struct.pack('>QQ', 0, len(PKMD)) + struct.pack('>I', 0)
              + struct.pack('>QQQQQQ', 0, 0, 0, 0, 0, len(PKMD))).ljust(256, b'\0')
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w') as z:
        z.writestr('SYSTEM/etc/security/otacerts.zip', certs.getvalue())
        z.writestr('IMAGES/vbmeta.img', header + PKMD)
    return out.getvalue()


class SigningJob(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='signing-job-'))
        self.addCleanup(shutil.rmtree, self.root, True)
        self.source, self.jobs, self.out = self.root/'source', self.root/'jobs', self.root/'out'
        self.source.mkdir()
        inputs = self.root/'inputs'
        inputs.mkdir()
        (inputs/'unsigned.zip').write_bytes(b'SYNTHETIC unsigned target-files')
        (inputs/'otatools').write_bytes(b'SYNTHETIC otatools')
        config = {'target': {'device': 'frankel'}, 'host': {'sign': {'updateNotes': 'os/release-notes/{device}-{build}.md'}},
            'identities': {'otaCertificateSha256': hashlib.sha256(b'SYNTHETIC certificate DER').hexdigest(),
                'avbPublicKeySha256': hashlib.sha256(PKMD).hexdigest()},
            'publication': {'channels': {'beta': {'feedChannel': 'stable'}}}}
        self.config = self.root/'config.json'
        self.config.write_text(json.dumps(config))
        self.manifest = {'schema': job.SCHEMA, 'device': 'frankel', 'build': BUILD, 'buildTimestamp': STAMP, 'candidateSha': SHA,
            'config': {'path': str(self.config), 'sha256': job.sha256_file(self.config)},
            'inputs': {k: {'path': str(p), 'size': p.stat().st_size, 'sha256': job.sha256_file(p)}
                       for k, p in (('targetFiles', inputs/'unsigned.zip'), ('otatools', inputs/'otatools'))}}
        self.calls, self.fail = [], None

    def write_manifest(self, **changes):
        path = self.root/'manifest.json'
        path.write_text(json.dumps(dict(self.manifest, **changes)))
        return path

    def signer(self, config_path, env):
        """host-sign's durable layout: releases/{build} copies, signed release dir and signed marker; public outputs."""
        self.calls.append(dict(env))
        assert Path(env['ROSE_RELEASE_DIR']).parent.name.startswith(f'frankel-{BUILD}.')  # isolated per-job workspace
        if self.fail == 'before':
            raise job.Refused('sign_failed: derived release script exited 1')
        releases = self.source/'releases'/BUILD
        output = releases/f'release-frankel-{BUILD}'
        output.mkdir(parents=True)
        shutil.copyfile(Path(env['ROSE_RELEASE_DIR'])/'unsigned-target_files.zip', releases/'frankel-target_files.zip')
        shutil.copyfile(env['ROSE_SIGN_OTATOOLS'], releases/'frankel-otatools.zip')
        (output/'frankel-target_files.zip').write_bytes(signed_zip())
        (output/f'frankel-ota_update-{BUILD}.zip').write_bytes(b'SYNTHETIC signed OTA')
        release = Path(env['ROSE_RELEASE_DIR'])
        public = release/'public'
        public.mkdir()
        shutil.copyfile(output/'frankel-target_files.zip', release/'signed-target_files.zip')
        shutil.copyfile(output/f'frankel-ota_update-{BUILD}.zip', public/f'frankel-ota_update-{BUILD}.zip')
        (public/'releasekey.x509.pem').write_bytes(CERT)
        (public/'avb_pkmd.bin').write_bytes(PKMD)
        (public/'frankel-stable').write_text(f'{BUILD} {STAMP} frankel stable\n')
        (public/'UPDATE.md').write_text(f'SYNTHETIC notes {BUILD}\n')
        shutil.copytree(public, releases/'public')  # host-sign's durable public copy beside the marker
        (releases/'.rose-actions-signed.json').write_text(json.dumps({'candidate': SHA, 'build': BUILD,
            'outputs': {p.name: job.sha256_file(p) for p in public.iterdir()},
            'signedTargetFilesSha256': job.sha256_file(release/'signed-target_files.zip')}))
        if self.fail == 'after':
            raise job.Refused('sign_failed: interrupted after signing')
        return 0

    def run_job(self, manifest=None):
        return job.run(manifest or self.write_manifest(), self.jobs, self.out, signer=self.signer, source_root=self.source,
            lock=lambda path: __import__('contextlib').nullcontext(), base_env={})

    def workspaces(self):
        return [p for p in self.jobs.iterdir() if p.is_dir()] if self.jobs.exists() else []

    def test_signs_commits_durably_and_cleans_up(self):
        result = self.run_job()
        self.assertEqual(result['verdict'], 'signed')
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.workspaces(), [])
        receipt = job.check(result['outputDir'])
        self.assertEqual(sorted(receipt['outputs']), sorted(['signed-target_files.zip'] + [f'public/{n}' for n in job.public_assets('frankel', BUILD)]))
        self.assertEqual(receipt['inputs'], self.manifest['inputs'])
        self.assertEqual(receipt['identities']['avbPublicKeySha256'], hashlib.sha256(PKMD).hexdigest())
        self.assertFalse((self.out/f'.frankel-{BUILD}.partial').exists())
        self.assertNotIn('ROSE_RETAINED_INPUT', self.calls[0])

    def test_a_committed_job_is_reused_without_signing_and_other_inputs_conflict(self):
        self.run_job()
        self.assertEqual(self.run_job()['verdict'], 'reused')
        self.assertEqual(len(self.calls), 1)
        other = dict(self.manifest['inputs'], otatools=dict(self.manifest['inputs']['otatools'], sha256='0' * 64))
        with self.assertRaisesRegex(job.Refused, 'job_conflict'):
            self.run_job(self.write_manifest(inputs=other))
        (self.out/f'frankel-{BUILD}/public/UPDATE.md').write_text('tampered\n')
        with self.assertRaisesRegex(job.Refused, 'output_changed'):
            self.run_job()

    def test_changed_inputs_or_config_refuse_before_any_signing(self):
        Path(self.manifest['inputs']['targetFiles']['path']).write_bytes(b'SYNTHETIC other unsigned bytes')
        with self.assertRaisesRegex(job.Refused, 'input_changed: inputs.targetFiles'):
            self.run_job()
        self.assertEqual((self.calls, self.workspaces()), ([], []))
        with self.assertRaisesRegex(job.Refused, 'input_changed: the signing config'):
            self.run_job(self.write_manifest(config=dict(self.manifest['config'], sha256='0' * 64)))
        for bad in ({'schema': 'other'}, {'build': '26'}, {'candidateSha': 'main'},
                    {'inputs': dict(self.manifest['inputs'], otatools={'path': 'relative', 'size': 1, 'sha256': '0' * 64})}):
            with self.subTest(bad=bad), self.assertRaisesRegex(job.Refused, 'manifest_invalid'):
                self.run_job(self.write_manifest(**bad))
        self.assertEqual(self.calls, [])

    def test_a_failed_signing_cleans_up_and_commits_nothing(self):
        self.fail = 'before'
        with self.assertRaisesRegex(job.Refused, 'sign_failed'):
            self.run_job()
        self.assertEqual(self.workspaces(), [])
        self.assertEqual(sorted(p.name for p in self.out.iterdir()), [])

    def test_an_interrupted_commit_is_recovered_without_signing_again(self):
        saved = os.rename
        def crash(src, dst):
            raise OSError('SYNTHETIC crash during commit')
        os.rename = crash
        try:
            with self.assertRaises(OSError):
                self.run_job()
        finally:
            os.rename = saved
        self.assertTrue((self.out/f'.frankel-{BUILD}.partial').exists())  # a partial commit never counts
        self.assertEqual(self.workspaces(), [])
        result = self.run_job()
        self.assertEqual((result['verdict'], len(self.calls)), ('recovered', 1))
        receipt = job.check(result['outputDir'])
        self.assertFalse((self.out/f'.frankel-{BUILD}.partial').exists())
        self.assertEqual(receipt['identities']['otaCertificateSha256'], hashlib.sha256(b'SYNTHETIC certificate DER').hexdigest())

    def test_signing_that_failed_after_the_signature_is_recovered_not_resigned(self):
        self.fail = 'after'
        with self.assertRaisesRegex(job.Refused, 'interrupted after signing'):
            self.run_job()
        self.fail = None
        self.assertEqual(self.run_job()['verdict'], 'recovered')
        self.assertEqual(len(self.calls), 1)

    def test_recovery_refuses_a_signed_release_of_other_inputs_or_bytes(self):
        self.fail = 'after'
        with self.assertRaises(job.Refused):
            self.run_job()
        self.fail = None
        unsigned = Path(self.manifest['inputs']['targetFiles']['path'])
        original = unsigned.read_bytes()
        unsigned.write_bytes(b'SYNTHETIC different unsigned')
        changed = dict(self.manifest['inputs'], targetFiles=dict(self.manifest['inputs']['targetFiles'],
            size=unsigned.stat().st_size, sha256=job.sha256_file(unsigned)))
        with self.assertRaisesRegex(job.Refused, "recovery_mismatch: the signed release was not made from this job's targetFiles"):
            self.run_job(self.write_manifest(inputs=changed))
        unsigned.write_bytes(original)
        for copy in (f'releases/{BUILD}/release-frankel-{BUILD}/frankel-ota_update-{BUILD}.zip', f'releases/{BUILD}/public/frankel-ota_update-{BUILD}.zip'):
            (self.source/copy).write_bytes(b'other OTA')
        with self.assertRaisesRegex(job.Refused, 'recovery_missing: no byte-exact source for frankel-ota'):
            self.run_job()
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.workspaces(), [])

    def test_a_signed_identity_other_than_the_permanent_one_refuses(self):
        config = json.loads(self.config.read_text())
        config['identities']['otaCertificateSha256'] = 'f' * 64
        self.config.write_text(json.dumps(config))
        with self.assertRaisesRegex(job.Refused, 'identity_mismatch'):
            self.run_job(self.write_manifest(config=dict(self.manifest['config'], sha256=job.sha256_file(self.config))))
        self.assertFalse((self.out/f'frankel-{BUILD}').exists())

    def test_cli_exit_codes(self):
        self.assertEqual(job.main(['run', '--manifest', str(self.root/'absent.json'), '--jobs-root', str(self.jobs),
            '--out-root', str(self.out)]), 1)
        self.run_job()
        self.assertEqual(job.main(['check', '--output-dir', str(self.out/f'frankel-{BUILD}')]), 0)
        (self.out/f'frankel-{BUILD}/signed-target_files.zip').write_bytes(b'tampered')
        self.assertEqual(job.main(['check', '--output-dir', str(self.out/f'frankel-{BUILD}')]), 1)


sys.path.insert(0, str(SCRIPTS))
hs_spec = importlib.util.spec_from_file_location('host_sign_for_notes', SCRIPTS/'host-sign.py')
host_sign = importlib.util.module_from_spec(hs_spec)
hs_spec.loader.exec_module(host_sign)
NOTES = f'# RoseOS frankel {BUILD}\nSYNTHETIC notes of an unpushed derived candidate\n'.encode()


class NotesInput(SigningJob):
    """SIGN-022: the derived candidate is never pushed, so its notes are a third hash-bound input whose verified bytes
    reach host-sign through the file mode (no git-show); the legacy commit pin is unchanged."""

    def setUp(self):
        super().setUp()
        notes = self.root/'inputs/UPDATE.md'
        notes.write_bytes(NOTES)
        self.manifest['inputs']['notes'] = {'path': str(notes), 'size': len(NOTES), 'sha256': hashlib.sha256(NOTES).hexdigest()}

    def signer(self, config_path, env):
        sign = json.loads(Path(config_path).read_text())['host']['sign']
        self.notes_mode = sign['updateNotes']
        self.notes_bytes = host_sign.release_notes(sign, 'd' * 40, 'frankel', BUILD)  # host-sign's own byte branch
        fail, self.fail = self.fail, (None if self.fail == 'after' else self.fail)
        result = super().signer(config_path, env)
        self.fail = fail
        public = Path(env['ROSE_RELEASE_DIR'])/'public'
        (public/'UPDATE.md').write_bytes(self.notes_bytes)
        marker = self.source/'releases'/BUILD/'.rose-actions-signed.json'
        data = json.loads(marker.read_text())
        data['outputs']['UPDATE.md'] = job.sha256_file(public/'UPDATE.md')
        marker.write_text(json.dumps(data))
        shutil.copyfile(public/'UPDATE.md', self.source/'releases'/BUILD/'public/UPDATE.md')
        if self.fail == 'after':
            raise job.Refused('sign_failed: interrupted after signing')
        return result

    def test_unpushed_candidate_notes_reach_host_sign_as_verified_bytes(self):
        result = self.run_job()
        self.assertEqual(sorted(self.notes_mode), ['build', 'device', 'file', 'sha256'])
        self.assertEqual(self.notes_bytes, NOTES)
        receipt = job.check(result['outputDir'])
        self.assertEqual(receipt['inputs']['notes'], self.manifest['inputs']['notes'])
        self.assertEqual((Path(result['outputDir'])/'public/UPDATE.md').read_bytes(), NOTES)

    def test_changed_notes_refuse_and_other_notes_conflict_on_retry(self):
        Path(self.manifest['inputs']['notes']['path']).write_bytes(NOTES + b'edited\n')
        with self.assertRaisesRegex(job.Refused, 'input_changed: inputs.notes'):
            self.run_job()
        Path(self.manifest['inputs']['notes']['path']).write_bytes(NOTES)
        self.run_job()
        other = dict(self.manifest['inputs'], notes=dict(self.manifest['inputs']['notes'], sha256='0' * 64))
        with self.assertRaisesRegex(job.Refused, 'job_conflict'):
            self.run_job(self.write_manifest(inputs=other))
        self.assertEqual(len(self.calls), 1)

    def test_notes_modes_are_exclusive_and_inputs_are_closed(self):
        for bad in ({'notes': {'commit': 'a' * 40, 'path': 'os/release-notes/frankel-x.md', 'sha256': '0' * 64}},
                    {'inputs': dict(self.manifest['inputs'], extra=self.manifest['inputs']['notes'])},
                    {'inputs': {'targetFiles': self.manifest['inputs']['targetFiles'], 'notes': self.manifest['inputs']['notes']}}):
            with self.subTest(bad=sorted(bad)), self.assertRaisesRegex(job.Refused, 'manifest_invalid'):
                self.run_job(self.write_manifest(**bad))
        missing = dict(self.manifest['inputs'], notes=dict(self.manifest['inputs']['notes'], path=str(self.root/'absent.md')))
        with self.assertRaisesRegex(job.Refused, 'input_missing: inputs.notes'):
            self.run_job(self.write_manifest(inputs=missing))
        self.assertEqual(self.calls, [])

    def test_recovery_binds_the_notes_input(self):
        self.fail = 'after'
        with self.assertRaises(job.Refused):
            self.run_job()
        self.fail = None
        marker = self.source/'releases'/BUILD/'.rose-actions-signed.json'
        good = marker.read_text()
        data = json.loads(good)
        data['outputs']['UPDATE.md'] = '0' * 64
        marker.write_text(json.dumps(data))
        with self.assertRaisesRegex(job.Refused, "recovery_mismatch: the signed release was not made from this job's notes"):
            self.run_job()
        marker.write_text(good)
        self.assertEqual(self.run_job()['verdict'], 'recovered')
        self.assertEqual(len(self.calls), 1)

    def test_host_sign_file_mode_refuses_other_bytes(self):
        notes = self.root/'n.md'
        notes.write_bytes(NOTES)
        pin = {'file': str(notes), 'sha256': hashlib.sha256(NOTES).hexdigest(), 'build': BUILD, 'device': 'frankel'}
        self.assertEqual(host_sign.release_notes({'updateNotes': pin}, 'd' * 40, 'frankel', BUILD), NOTES)
        link = self.root/'link.md'
        link.symlink_to(notes)
        for changes, message in (({'sha256': '0' * 64}, 'differ from their sha256'), ({'build': '2026103102'}, 'not frankel'),
                                 ({'file': str(link)}, 'regular file'), ({'file': 'relative.md'}, 'regular file'),
                                 ({'extra': 1}, 'exactly file, sha256, build and device')):
            with self.subTest(changes=changes), self.assertRaisesRegex(host_sign.AdapterFailed, message):
                host_sign.release_notes({'updateNotes': dict(pin, **changes)}, 'd' * 40, 'frankel', BUILD)
        other = self.root/'other.md'
        other.write_bytes(b'notes without the build number\n')
        with self.assertRaisesRegex(host_sign.AdapterFailed, 'do not name build'):
            host_sign.release_notes({'updateNotes': dict(pin, file=str(other), sha256=hashlib.sha256(other.read_bytes()).hexdigest())},
                'd' * 40, 'frankel', BUILD)


if __name__ == '__main__':
    unittest.main()
