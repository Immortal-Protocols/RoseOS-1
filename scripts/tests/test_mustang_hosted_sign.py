"""Hosted Mustang signer gate. No live login, secret, VM, dispatch, or publication."""
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'scripts'), str(Path(__file__).resolve().parent)]
import mustang_hosted_sign as hosted
import os_release_shape as workflow_shape

WORKFLOW = ROOT / '.github/workflows/mustang-ota-dedicated.yml'
ENV = {'GITHUB_RUN_ID': '123', 'GITHUB_RUN_ATTEMPT': '1', 'GITHUB_SHA': 'a' * 40}


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_zip(path, name='payload.txt', data=b'payload'):
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr(name, data)


class HostedSignGateTest(unittest.TestCase):
    def measured_peak(self):
        measured = hosted.measured_inputs()
        self.assertEqual(measured['unsigned'], 4340146868)
        self.assertEqual(measured['otatools'], 536807666)
        self.assertEqual(measured['signedTargetFiles'], 4884085713)
        self.assertEqual(measured['ota'], 1673383799)
        required = hosted.peak_bytes(measured['unsigned'], measured['otatools'],
                                     measured['signedTargetFiles'], measured['ota'])
        self.assertEqual(required, 22868848092)
        self.assertGreater(required, hosted.STOCK_SSD_BYTES)
        self.assertGreater(required, 14 * 10 ** 9)
        self.assertEqual(hosted.STOCK_SSD_BYTES, 14 * 1024 ** 3)
        self.assertEqual(hosted.FREE_ARTIFACT_BYTES, 500 * 1000 * 1000)
        for size in measured.values():
            self.assertGreater(size, hosted.FREE_ARTIFACT_BYTES)
        return measured, required

    def bundle(self, **changes):
        measured, _ = self.measured_peak()
        value = {
            'schema': hosted.BUNDLE_SCHEMA,
            'device': 'mustang',
            'runId': ENV['GITHUB_RUN_ID'],
            'runAttempt': ENV['GITHUB_RUN_ATTEMPT'],
            'workflow': hosted.WORKFLOW,
            'sourceSha': ENV['GITHUB_SHA'],
            'unsigned': {'sha256': 'a' * 64, 'sizeBytes': measured['unsigned']},
            'otatools': {'sha256': 'b' * 64, 'sizeBytes': measured['otatools']},
            'jdk': {'sha256': None, 'sizeBytes': None},
            'generateRelease': {'sha256': None, 'sizeBytes': None},
            'otatoolsExtractedBytes': None,
            'identity': hosted.public_identity(),
            'executable': '/tmp/guest-supplied',
        }
        value.update(changes)
        return value

    def assert_secret_closed(self, call, pattern):
        called = []
        with self.assertRaisesRegex(ValueError, pattern):
            call(secret_access=lambda: called.append('secret'))
        self.assertEqual(called, [])

    def test_measured_peak_exceeds_stock_disk_and_free_artifact_quota_before_secrets(self):
        _, required = self.measured_peak()
        bundle = self.bundle()
        bundle['capacity'] = 'ok'

        def call(secret_access):
            hosted.evaluate(bundle, ROOT, free=10 ** 18, secret_access=secret_access, env=ENV)

        self.assert_secret_closed(call, 'hosted signer capacity exceeded')
        with self.assertRaises(hosted.CapacityExceeded) as caught:
            hosted.capacity(bundle, free=10 ** 18)
        self.assertIn(f'required {required} bytes', str(caught.exception))
        self.assertIn(f'stock SSD {hosted.STOCK_SSD_BYTES} bytes', str(caught.exception))
        self.assertIn('unsigned', str(caught.exception))

    def test_unmeasured_jdk_and_script_do_not_count_as_zero_when_space_would_fit(self):
        bundle = self.bundle()
        bundle['unsigned'] = {'sha256': 'a' * 64, 'sizeBytes': 100}
        bundle['otatools'] = {'sha256': 'b' * 64, 'sizeBytes': 100}

        def call(secret_access):
            hosted.evaluate(bundle, ROOT, free=10 ** 18, secret_access=secret_access, env=ENV)

        self.assert_secret_closed(call, 'unmeasured signer inputs: jdk,otatoolsExtractedBytes,generateRelease')

    def test_script_pin_stays_unbound_and_blocks_secret_access(self):
        pin = json.loads((ROOT / hosted.SCRIPT_PIN).read_text())
        self.assertIsNone(pin['generateReleaseSha256'])
        self.assertEqual(pin['entryPoint'], hosted.ENTRY)
        bundle, files, _ = self.tiny_bundle()

        def call(secret_access):
            hosted.evaluate(bundle, ROOT, free=10 ** 18, files=files, secret_access=secret_access, env=ENV)

        self.assert_secret_closed(call, 'sign script pin is unbound')

    def test_negative_hash_unsafe_zip_missing_file_and_wrong_run_block_secret_access(self):
        bundle, files, pin = self.tiny_bundle()
        Path(files['unsigned']).write_bytes(b'changed')

        def hashed(secret_access):
            hosted.evaluate(bundle, ROOT, free=10 ** 18, files=files, secret_access=secret_access, pin=pin, env=ENV)

        self.assert_secret_closed(hashed, 'signing bundle hash differs')
        unsafe = self.tiny_bundle(zip_member='../escape')
        def slipped(secret_access):
            hosted.evaluate(unsafe[0], ROOT, free=10 ** 18, files=unsafe[1], secret_access=secret_access,
                            pin=unsafe[2], env=ENV)
        self.assert_secret_closed(slipped, 'unsafe zip member')
        linked = self.tiny_bundle(symlink=True)
        def link(secret_access):
            hosted.evaluate(linked[0], ROOT, free=10 ** 18, files=linked[1], secret_access=secret_access,
                            pin=linked[2], env=ENV)
        self.assert_secret_closed(link, 'unsafe zip symlink')
        absent_bundle, absent_files, absent_pin = self.tiny_bundle()
        absent_files.pop('jdk')
        def absent(secret_access):
            hosted.evaluate(absent_bundle, ROOT, free=10 ** 18, files=absent_files,
                            secret_access=secret_access, pin=absent_pin, env=ENV)
        self.assert_secret_closed(absent, 'signing bundle files are absent')
        wrong = self.bundle()
        wrong['sourceSha'] = 'b' * 40
        def other_run(secret_access):
            hosted.evaluate(wrong, ROOT, free=10 ** 18, secret_access=secret_access, env=ENV)
        self.assert_secret_closed(other_run, 'signing bundle is not this run')
        incomplete = self.bundle()
        incomplete.pop('unsigned')
        def missing_artifact(secret_access):
            hosted.evaluate(incomplete, ROOT, free=10 ** 18, secret_access=secret_access, env=ENV)
        self.assert_secret_closed(missing_artifact, 'signing bundle is missing unsigned')

    def test_free_space_and_artifact_quota_fail_closed_before_secret_access(self):
        bundle, files, pin = self.tiny_bundle()

        def tight(secret_access):
            hosted.evaluate(bundle, ROOT, free=0, files=files, secret_access=secret_access, pin=pin, env=ENV)

        self.assert_secret_closed(tight, 'hosted signer capacity exceeded')
        oversized = self.bundle()
        oversized['unsigned'] = {'sha256': 'c' * 64, 'sizeBytes': hosted.FREE_ARTIFACT_BYTES + 1}
        oversized['otatools'] = {'sha256': 'd' * 64, 'sizeBytes': 1}
        oversized['jdk'] = {'sha256': 'e' * 64, 'sizeBytes': 1}
        oversized['generateRelease'] = {'sha256': 'f' * 64, 'sizeBytes': 1}
        oversized['otatoolsExtractedBytes'] = 0
        measured = {'unsigned': hosted.FREE_ARTIFACT_BYTES + 1, 'otatools': 1,
                    'signedTargetFiles': 1, 'ota': 1}

        def quota(secret_access):
            hosted.evaluate(oversized, ROOT, free=10 ** 18, secret_access=secret_access, pin=pin, env=ENV,
                            stock_bytes=10 ** 18, measured=measured)

        self.assert_secret_closed(quota, 'artifact quota')

    def test_secret_callback_runs_only_after_capacity_pin_hash_and_zip_checks(self):
        bundle, files, pin = self.tiny_bundle()
        called = []
        admitted = hosted.evaluate(bundle, ROOT, free=10 ** 18, files=files,
                                   secret_access=lambda: called.append('secret'), pin=pin, env=ENV)
        self.assertEqual(called, ['secret'])
        self.assertTrue(admitted['admit'])
        self.assertLess(admitted['requiredBytes'], hosted.STOCK_SSD_BYTES)

    def test_symlink_entry_and_guest_shm_paths_are_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            scripts = root / 'scripts'
            scripts.mkdir()
            link = scripts / 'mustang_hosted_sign.py'
            link.symlink_to(ROOT / 'scripts/mustang_hosted_sign.py')
            with self.assertRaisesRegex(ValueError, 'sign entry point is not the checkout file'):
                hosted.require_entry(root)
            collection = root / 'collection.json'
            collection.write_text('{}')
            shm = Path('/dev/shm/mustang-hosted-sign-test.json')
            self.assertFalse(shm.exists())
            with self.assertRaisesRegex(ValueError, 'signing material is refused on the guest'):
                hosted.export_bundle(collection, shm)
            self.assertFalse(shm.exists())

    def test_export_records_hashes_without_key_material_and_cli_preflight_fails_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            unsigned = root / 'unsigned.zip'
            tools = root / 'otatools.zip'
            write_zip(unsigned, data=b'unsigned')
            write_zip(tools, data=b'tools')
            collection = {
                'schema': 'roseos.mustang-compile-collection.v1', 'status': 'complete',
                'runId': '123', 'runAttempt': '1', 'collectorSource': 'a' * 40,
                'outputs': {'artifacts': {
                    'targetFiles': {'state': 'collected', 'path': str(unsigned), 'sizeBytes': unsigned.stat().st_size,
                                    'sha256': digest(unsigned)},
                    'otatools': {'state': 'collected', 'path': str(tools), 'sizeBytes': tools.stat().st_size,
                                 'sha256': digest(tools)},
                }},
            }
            path = root / 'collection.json'
            path.write_text(json.dumps(collection))
            out = root / 'bundle.json'
            bundle = hosted.export_bundle(path, out)
            text = out.read_text()
            self.assertNotIn('pk8', text)
            self.assertNotIn('"passphrase":', text)
            self.assertEqual(bundle['identity']['passphraseSecret'], hosted.PASSPHRASE)
            self.assertIsNone(bundle['jdk']['sizeBytes'])
            self.assertEqual(bundle['identity']['otaCertificateSha256'], hosted.OTA)
            self.assertEqual(bundle['identity']['avbPublicKeySha256'], hosted.AVB)
            self.assertEqual(bundle['identity']['signer'], hosted.SIGNER)
            env = dict(os.environ, PATH=os.environ['PATH'], **ENV)
            result = subprocess.run([sys.executable, str(ROOT / 'scripts/mustang_hosted_sign.py'),
                                     'preflight', '--bundle', str(out)],
                                    env=env, capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 4, result.stderr)
            self.assertIn('unmeasured signer inputs', result.stderr)
            self.assertNotIn('admit=true', result.stdout)
            self.assertNotIn('gcloud', result.stderr)
            output = root / 'github-output'
            output.write_text('')
            denied = subprocess.run([sys.executable, str(ROOT / 'scripts/mustang_hosted_sign.py'),
                                     'preflight', '--bundle', str(out)],
                                    env=dict(env, GITHUB_OUTPUT=str(output)), capture_output=True, text=True, timeout=20)
            self.assertEqual(denied.returncode, 4)
            self.assertEqual(output.read_text(), '')

    def test_historical_cli_preflight_reports_capacity_and_writes_no_admission(self):
        _, required = self.measured_peak()
        with tempfile.TemporaryDirectory() as temporary:
            bundle = self.bundle()
            path = Path(temporary) / 'bundle.json'
            path.write_text(json.dumps(bundle))
            output = Path(temporary) / 'github-output'
            output.write_text('')
            env = dict(PATH=os.environ['PATH'], GITHUB_OUTPUT=str(output), **ENV)
            result = subprocess.run([sys.executable, str(ROOT / 'scripts/mustang_hosted_sign.py'),
                                     'preflight', '--bundle', str(path)],
                                    env=env, capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 4, result.stderr)
            self.assertIn('hosted signer capacity exceeded', result.stderr)
            self.assertIn(str(required), result.stderr)
            self.assertEqual(output.read_text(), '')
            self.assertNotIn('gcloud', result.stderr)

    def test_guest_cli_cannot_sign_or_execute_key_access(self):
        env = {'PATH': os.environ['PATH']}
        for action in ('sign', '_execute'):
            result = subprocess.run([sys.executable, str(ROOT / 'scripts/mustang-dedicated.py'), action],
                                    env=env, capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 4, result.stderr)
            self.assertIn('guest signing is closed', result.stderr)
            self.assertNotIn('gcloud', result.stderr)
        source = (ROOT / 'scripts/mustang_hosted_sign.py').read_text()
        body = source.split('"""', 2)[-1]
        for forbidden in ('host_secrets', 'gcloud', 'fetch_secret', 'subprocess'):
            self.assertNotIn(forbidden, body)
        self.assertIn('signing material is refused on the guest', body)

    def test_bind_outputs_requires_the_hosted_receipt_bytes(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            with self.assertRaisesRegex(ValueError, 'hosted signing receipt is missing'):
                hosted.bind_outputs(directory)
            payload = b'signed-bytes'
            (directory / 'ota.zip').write_bytes(payload)
            receipt = {
                'schema': hosted.RECEIPT_SCHEMA, 'device': 'mustang', 'signer': hosted.SIGNER,
                'secretVersion': hosted.SECRET_VERSION,
                'outputs': {
                    'signedTargetFiles': {'file': 'ota.zip', 'sizeBytes': len(payload), 'sha256': digest(directory / 'ota.zip')},
                    'ota': {'file': 'ota.zip', 'sizeBytes': len(payload), 'sha256': '0' * 64},
                },
            }
            (directory / 'mustang-hosted-sign-receipt.json').write_text(json.dumps(receipt))
            with self.assertRaisesRegex(ValueError, 'hosted signed output hash differs'):
                hosted.bind_outputs(directory)

    def test_workflow_keeps_signer_login_off_the_guest_and_behind_capacity(self):
        workflow = WORKFLOW.read_text()
        jobs = workflow_shape.jobs(workflow)
        builder = jobs['builder']
        signer = jobs['signer']
        publisher = jobs['publisher']
        cleanup = jobs['cleanup']
        self.assertNotIn('id-token', builder)
        self.assertNotIn('actions-gcp-login', builder)
        self.assertNotIn('mustang-dedicated.py sign', builder)
        self.assertNotIn('GCP_SIGNER', builder)
        self.assertNotIn('MUSTANG_SIGNER', builder)
        self.assertNotIn('PIXEL_OS_SIGNING', builder)
        self.assertNotIn('host-sign', builder)
        self.assertNotIn('/dev/shm', builder)
        self.assertIn('runs-on: ubuntu-24.04', signer)
        self.assertNotIn('\n      group:', signer)
        self.assertNotIn('mustang_source_sync', signer)
        self.assertNotIn('immortal-os-builder', signer)
        self.assertIn('id-token: write', signer)
        self.assertLess(signer.index('mustang_hosted_sign.py preflight'), signer.index('actions-gcp-login.py login'))
        self.assertLess(signer.index("steps.capacity.outputs.admit == 'true'"), signer.index('actions-gcp-login.py login'))
        self.assertEqual(signer.count("steps.capacity.outputs.admit == 'true'"), 3)
        signer_jobs = [name for name, body in jobs.items()
                       if 'MUSTANG_SIGNER_WORKLOAD_IDENTITY_PROVIDER' in body and 'id-token: write' in body]
        self.assertEqual(signer_jobs, ['signer'])
        self.assertNotIn('id-token', jobs['guard'])
        self.assertIn('needs: signer', publisher.split('steps:', 1)[0])
        self.assertLess(publisher.index('mustang_hosted_sign.py bind-outputs'), publisher.index('verify-staged'))
        self.assertLess(publisher.index('verify-staged'), publisher.index('actions/create-github-app-token'))
        self.assertIn('- signer', cleanup.split('steps:', 1)[0])
        self.assertLess(cleanup.index('mustang_controller.py stop'), cleanup.index('sweep --execute'))
        self.assertIn("always() && needs.controller.result != 'skipped'", cleanup)
        runtime = json.loads((ROOT / 'os/build-only/mustang-runtime.json').read_text())
        self.assertEqual(runtime['status'], 'unbound')
        self.assertIsNone(runtime['signingRouteEvidence'])
        self.assertIsNone(runtime['sourceEvidence'])
        self.assertFalse(runtime['publication']['enabled'])

    def tiny_bundle(self, zip_member='payload.txt', symlink=False):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        unsigned = root / 'unsigned.zip'
        tools = root / 'otatools.zip'
        script = root / 'generate-release.sh'
        jdk = root / 'jdk.bin'
        if symlink:
            info = zipfile.ZipInfo('link')
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            with zipfile.ZipFile(unsigned, 'w') as archive:
                archive.writestr(info, 'target')
        else:
            write_zip(unsigned, zip_member, b'unsigned')
        write_zip(tools, data=b'tools')
        script.write_bytes(b'script-bytes')
        jdk.write_bytes(b'jdk-bytes')
        files = {'unsigned': unsigned, 'otatools': tools, 'generateRelease': script, 'jdk': jdk}
        bundle = self.bundle()
        bundle['unsigned'] = {'sha256': digest(unsigned), 'sizeBytes': unsigned.stat().st_size}
        bundle['otatools'] = {'sha256': digest(tools), 'sizeBytes': tools.stat().st_size}
        bundle['generateRelease'] = {'sha256': digest(script), 'sizeBytes': script.stat().st_size}
        bundle['jdk'] = {'sha256': digest(jdk), 'sizeBytes': jdk.stat().st_size}
        bundle['otatoolsExtractedBytes'] = 0
        pin = {'entryPoint': hosted.ENTRY, 'generateReleaseSha256': digest(script)}
        return bundle, files, pin


if __name__ == '__main__':
    unittest.main()
