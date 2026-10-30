#!/usr/bin/env python3
"""QA-020 signing acceptance (verifier-owned): the Oct 30 plan's signer criteria as black-box fixtures over
signing-job.py and rango-retained.py. Synthetic inputs and a synthetic stand-in signer only: no key, secret, network or
host. Passing here is fixture coverage, never production signing evidence."""
import base64
import fcntl
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
ROOT = SCRIPTS.parent


def load(name, path):
    sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


job = load('signing_job_acceptance', SCRIPTS/'signing-job.py')
rango = load('rango_retained_acceptance', SCRIPTS/'rango-retained.py')
FRANKEL = json.loads((ROOT/'os/release-pipeline/frankel.json').read_text())
PINS = FRANKEL['identities']

DER = b'QA-020 synthetic certificate DER'
CERT = (b'-----BEGIN CERTIFICATE-----\n' + base64.b64encode(DER) + b'\n-----END CERTIFICATE-----\n').decode()
PKMD = b'QA-020 synthetic AVB key'
BUILD, STAMP, SHA = '2026103199', '1793400099', 'a' * 40


def signed_target_files():
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


class Fixture(unittest.TestCase):
    """One frankel job: synthetic inputs, a config with chosen identity pins, and a stand-in for host-sign's layout."""
    PINNED = {'otaCertificateSha256': hashlib.sha256(DER).hexdigest(), 'avbPublicKeySha256': hashlib.sha256(PKMD).hexdigest()}

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='qa020-signing-'))
        self.addCleanup(shutil.rmtree, self.root, True)
        self.source, self.jobs, self.out = self.root/'source', self.root/'jobs', self.root/'out'
        self.source.mkdir()
        self.inputs = self.root/'inputs'
        self.inputs.mkdir()
        (self.inputs/'unsigned.zip').write_bytes(b'QA-020 unsigned target-files')
        (self.inputs/'otatools.zip').write_bytes(b'QA-020 otatools')
        self.calls, self.mode = [], 'ok'
        self.use_config(self.PINNED)

    def use_config(self, identities, device='frankel'):
        config = {'target': {'device': device}, 'host': {'sign': {}}, 'identities': identities,
            'publication': {'channels': {'beta': {'feedChannel': 'stable'}}}}
        self.config = self.root/'config.json'
        self.config.write_text(json.dumps(config))
        self.manifest = {'schema': job.SCHEMA, 'device': 'frankel', 'build': BUILD, 'buildTimestamp': STAMP, 'candidateSha': SHA,
            'config': {'path': str(self.config), 'sha256': job.sha256_file(self.config)},
            'inputs': {key: {'path': str(path), 'size': path.stat().st_size, 'sha256': job.sha256_file(path)}
                       for key, path in (('targetFiles', self.inputs/'unsigned.zip'), ('otatools', self.inputs/'otatools.zip'))}}

    def manifest_file(self, **changes):
        path = self.root/'manifest.json'
        path.write_text(json.dumps(dict(self.manifest, **changes)))
        return path

    def signer(self, config_path, env):
        self.calls.append(env['ROSE_RELEASE_DIR'])
        if self.mode == 'fail':
            # A failure after partial work: a half-written output and a stray key-like file in the job workspace.
            release = Path(env['ROSE_RELEASE_DIR'])
            (release/'public').mkdir()
            (release/'public'/'releasekey.x509.pem').write_text(CERT)
            (release.parent/'releasekey.pk8').write_bytes(b'QA-020 stand-in private key bytes')
            raise job.Refused('sign_failed: stand-in signer exited 1')
        releases = self.source/'releases'/BUILD
        signed_dir = releases/f'release-frankel-{BUILD}'
        signed_dir.mkdir(parents=True)
        shutil.copyfile(Path(env['ROSE_RELEASE_DIR'])/'unsigned-target_files.zip', releases/'frankel-target_files.zip')
        shutil.copyfile(env['ROSE_SIGN_OTATOOLS'], releases/'frankel-otatools.zip')
        (signed_dir/'frankel-target_files.zip').write_bytes(signed_target_files())
        public = {f'frankel-ota_update-{BUILD}.zip': b'QA-020 signed OTA', 'frankel-stable': f'{BUILD} {STAMP} frankel stable\n'.encode(),
                  'releasekey.x509.pem': CERT.encode(), 'avb_pkmd.bin': PKMD, 'UPDATE.md': b'QA-020 notes\n'}
        for name, data in public.items():
            (signed_dir/name).write_bytes(data)
        (releases/'.rose-actions-signed.json').write_text(json.dumps({'candidate': SHA, 'build': BUILD,
            'signedTargetFilesSha256': job.sha256_file(signed_dir/'frankel-target_files.zip'),
            'outputs': {n: hashlib.sha256(d).hexdigest() for n, d in public.items()}}))
        out = Path(env['ROSE_RELEASE_DIR'])
        (out/'public').mkdir()
        shutil.copyfile(signed_dir/'frankel-target_files.zip', out/'signed-target_files.zip')
        for name in public:
            shutil.copyfile(signed_dir/name, out/'public'/name)
        if self.mode == 'crash-after-sign':
            raise OSError('QA-020: worker lost after the signature, before the commit')

    def run_job(self, manifest=None, lock=None):
        return job.run(manifest or self.manifest_file(), self.jobs, self.out, signer=self.signer, source_root=self.source,
                       lock=lock, base_env={})

    def leftovers(self):
        return sorted(p.name for p in self.jobs.iterdir() if p.name != 'signer.lock') if self.jobs.exists() else []


class ImmutableBinding(Fixture):
    def test_receipt_binds_candidate_inputs_config_and_every_output(self):
        result = self.run_job()
        self.assertEqual(result['verdict'], 'signed')
        receipt = job.check(result['outputDir'])
        self.assertEqual((receipt['candidateSha'], receipt['build'], receipt['buildTimestamp'], receipt['device']),
                         (SHA, BUILD, STAMP, 'frankel'))
        self.assertEqual((receipt['inputs'], receipt['config']), (self.manifest['inputs'], self.manifest['config']))
        self.assertEqual(sorted(receipt['outputs']), sorted(['signed-target_files.zip'] + [f'public/{n}' for n in
                         job.public_assets('frankel', BUILD)]))

    def test_any_change_to_committed_output_is_detected_before_publication(self):
        out = Path(self.run_job()['outputDir'])
        (out/'public'/'UPDATE.md').write_bytes(b'tampered after signing\n')
        with self.assertRaisesRegex(job.Refused, 'output_changed'):
            job.check(out)

    def test_an_extra_file_in_committed_output_is_detected(self):
        out = Path(self.run_job()['outputDir'])
        (out/'public'/'extra.bin').write_bytes(b'x')
        with self.assertRaisesRegex(job.Refused, 'output_changed'):
            job.check(out)


class ChangedDeviceOrHashRejected(Fixture):
    def assert_refused_unsigned(self, pattern, manifest):
        with self.assertRaisesRegex(job.Refused, pattern):
            self.run_job(manifest)
        self.assertEqual(self.calls, [], 'refused before the signer ran')
        self.assertFalse((self.out/f'frankel-{BUILD}').exists())

    def test_input_bytes_changed_after_the_manifest(self):
        (self.inputs/'unsigned.zip').write_bytes(b'QA-020 unsigned target-files, swapped')
        self.assert_refused_unsigned('input_changed', self.manifest_file())

    def test_input_hash_or_size_in_manifest_wrong(self):
        inputs = json.loads(json.dumps(self.manifest['inputs']))
        inputs['otatools']['sha256'] = '0' * 64
        self.assert_refused_unsigned('input_changed', self.manifest_file(inputs=inputs))

    def test_input_replaced_by_a_symlink(self):
        target = self.inputs/'unsigned.zip'
        real = self.inputs/'real.zip'
        target.rename(real)
        target.symlink_to(real)
        self.assert_refused_unsigned('input_missing', self.manifest_file())

    def test_manifest_device_differs_from_config_device(self):
        self.assert_refused_unsigned('another device', self.manifest_file(device='rango'))

    def test_config_hash_differs(self):
        self.assert_refused_unsigned('input_changed', self.manifest_file(config=dict(self.manifest['config'], sha256='1' * 64)))

    def test_malformed_identity_fields(self):
        for change in ({'build': '20261031'}, {'candidateSha': SHA[:12]}, {'device': 'Frankel'}):
            with self.subTest(change):
                self.assert_refused_unsigned('manifest_invalid', self.manifest_file(**change))

    def test_committed_job_never_accepts_other_inputs(self):
        self.run_job()
        calls = len(self.calls)
        with self.assertRaisesRegex(job.Refused, 'job_conflict'):
            self.run_job(self.manifest_file(candidateSha='b' * 40))
        self.assertEqual(len(self.calls), calls)


class RetryWithoutResigning(Fixture):
    def test_retry_of_a_committed_job_reuses_bytes_and_never_signs(self):
        first = self.run_job()
        receipt_bytes = (Path(first['outputDir'])/job.RECEIPT).read_bytes()
        second = self.run_job()
        self.assertEqual((second['verdict'], len(self.calls)), ('reused', 1))
        self.assertEqual((Path(second['outputDir'])/job.RECEIPT).read_bytes(), receipt_bytes)
        self.assertEqual(second['receiptSha256'], first['receiptSha256'])

    def test_lost_worker_after_signature_recovers_the_same_bytes_without_signing_again(self):
        self.mode = 'crash-after-sign'
        with self.assertRaises(OSError):
            self.run_job()
        self.assertFalse((self.out/f'frankel-{BUILD}').exists(), 'nothing committed by the lost attempt')
        self.mode = 'ok'
        result = self.run_job()
        self.assertEqual((result['verdict'], len(self.calls)), ('recovered', 1))
        signed = self.source/'releases'/BUILD/f'release-frankel-{BUILD}'
        out = Path(result['outputDir'])
        self.assertEqual((out/'signed-target_files.zip').read_bytes(), (signed/'frankel-target_files.zip').read_bytes())
        for name in job.public_assets('frankel', BUILD):
            self.assertEqual((out/'public'/name).read_bytes(), (signed/name).read_bytes(), name)
        job.check(out)


class FailureCleanup(Fixture):
    def test_failed_signing_leaves_no_workspace_partial_output_or_key_material(self):
        self.mode = 'fail'
        with self.assertRaisesRegex(job.Refused, 'sign_failed'):
            self.run_job()
        self.assertEqual(self.leftovers(), [], 'per-job workspace (with its stand-in key) removed')
        self.assertEqual(sorted(p.name for p in self.out.iterdir()), [], 'no committed or .partial output')
        self.assertEqual([p for p in self.root.rglob('*.pk8')], [])

    def test_signing_lock_is_held_only_while_signing_and_released_after_failure(self):
        lock_path = self.jobs/'signer.lock'
        observed = []

        def probe_lock():
            fd = os.open(lock_path, os.O_RDWR)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(fd, fcntl.LOCK_UN)
                return 'free'
            except BlockingIOError:
                return 'held'
            finally:
                os.close(fd)

        original = self.signer

        def signer(config_path, env):
            observed.append(probe_lock())
            return original(config_path, env)

        self.signer = signer
        self.mode = 'fail'
        with self.assertRaises(job.Refused):
            self.run_job(lock=job.signer_lock)
        self.assertEqual(observed, ['held'])
        self.assertEqual(probe_lock(), 'free', 'a failed job releases the signing-only lock')

    def test_an_interrupted_commit_is_discarded_before_signing(self):
        partial = self.out/f'.frankel-{BUILD}.partial'
        partial.mkdir(parents=True)
        (partial/'signed-target_files.zip').write_bytes(b'half-written')
        result = self.run_job()
        self.assertEqual(result['verdict'], 'signed')
        self.assertFalse(partial.exists())
        job.check(result['outputDir'])


class SignerIdentity(Fixture):
    def test_real_frankel_pins_refuse_any_other_signed_identity_and_commit_nothing(self):
        self.use_config(PINS)
        with self.assertRaisesRegex(job.Refused, 'identity_mismatch'):
            self.run_job()
        self.assertFalse((self.out/f'frankel-{BUILD}').exists())
        self.assertEqual(self.leftovers(), [])

    def test_receipt_identity_equals_the_permanent_pins(self):
        receipt = job.check(self.run_job()['outputDir'])
        self.assertEqual(receipt['identities'], self.PINNED)

    def test_repository_frankel_pins_are_the_published_identities(self):
        self.assertEqual(PINS, {'appSignerSha256': '650d47f91b7f5af3c530fef53fe92018aa4521efba065bcb1d6fc9b699060bfb',
            'otaCertificateSha256': '2ce41f9e7efb04cd7fa61b32e09091719304f7101405c3ef7ffcf0f0f5ec268a',
            'avbPublicKeySha256': '7c19d7fa197a2febb9b472fd27059443be9bfd4f20c22909966ebd7206168822'})


class RangoFirstUseIdentity(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix='qa020-rango-')) / 'identity'
        self.addCleanup(shutil.rmtree, self.dir.parent, True)
        self.binding = {'policy': 'first-use-containers', 'bootstrapRun': '1', 'bootstrapRecipe': 'b' * 40,
            'versions': {'keys': '3', 'passphrase': '3'}, 'otaCertificateSha256': 'c' * 64, 'avbPublicKeySha256': 'd' * 64}

    def test_enrol_once_then_only_the_same_identity_matches(self):
        self.assertEqual(rango.first_use_record(self.dir, self.binding, enroll=True, frankel=PINS), 'enrolled')
        self.assertEqual(rango.first_use_record(self.dir, self.binding, enroll=False, frankel=PINS), 'matched')
        with self.assertRaisesRegex(rango.Refused, 'identity_conflict'):
            rango.first_use_record(self.dir, dict(self.binding, avbPublicKeySha256='e' * 64), enroll=False, frankel=PINS)
        with self.assertRaisesRegex(rango.Refused, 'identity_already_enrolled'):
            rango.first_use_record(self.dir, self.binding, enroll=True, frankel=PINS)

    def test_no_record_means_no_signing_identity(self):
        with self.assertRaisesRegex(rango.Refused, 'identity_record_missing'):
            rango.first_use_record(self.dir, self.binding, enroll=False, frankel=PINS)

    def test_rango_may_never_reuse_the_frankel_ota_or_avb_identity(self):
        for key in ('otaCertificateSha256', 'avbPublicKeySha256'):
            with self.subTest(key):
                with self.assertRaisesRegex(rango.Refused, 'signer_collision'):
                    rango.first_use_record(self.dir, dict(self.binding, **{key: PINS[key]}), enroll=True, frankel=PINS)
        self.assertFalse(self.dir.exists(), 'nothing enrolled')


if __name__ == '__main__':
    unittest.main()
