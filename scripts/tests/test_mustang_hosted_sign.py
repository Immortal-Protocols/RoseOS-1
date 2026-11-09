"""Hosted Mustang signer gate. No live login, secret, VM, dispatch, or publication."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'scripts'), str(Path(__file__).resolve().parent)]
import mustang_hosted_sign as hosted
import os_release_shape as workflow_shape

WORKFLOW = ROOT / '.github/workflows/mustang-ota-dedicated.yml'
ENV = {'GITHUB_RUN_ID': '123', 'GITHUB_RUN_ATTEMPT': '1', 'GITHUB_SHA': 'a' * 40}


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_zip(path, name='payload.txt', data=b'payload', mode=0o644, members=None):
    with zipfile.ZipFile(path, 'w') as archive:
        rows = members if members is not None else [(name, data, mode)]
        for member_name, member_data, member_mode in rows:
            info = zipfile.ZipInfo(member_name)
            info.date_time = (2026, 1, 1, 0, 0, 0)
            info.external_attr = (member_mode & 0o777) << 16
            archive.writestr(info, member_data)


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
        config = hosted.load_capacity()
        self.assertFalse(config['admission'])
        self.assertEqual(config['trust'], 'held')
        self.assertIsNone(config['storage']['accountPlan'])
        self.assertTrue(config['storage']['includedAllowancesAreNotPerArtifactLimits'])
        self.assertFalse(config['proposedRunner']['purchased'])
        self.assertFalse(config['proposedRunner']['configured'])
        self.assertFalse(config['proposedRunner']['selected'])
        self.assertEqual(config['proposedRunner']['sku'], 'linux_4_core')
        self.assertEqual(config['proposedRunner']['usdPerMinute'], '0.012')
        self.assertEqual(config['proposedRunner']['ssdBytes'], 150000000000)
        self.assertGreater(config['proposedRunner']['ssdBytes'], required)
        self.assertEqual(config['activeRunner']['label'], 'ubuntu-24.04')
        self.assertEqual(config['activeRunner']['ssdBytes'], hosted.STOCK_SSD_BYTES)
        return measured, required

    def admitted(self):
        config = hosted.load_capacity()
        config['admission'] = True
        config['activeRunner'] = dict(config['activeRunner'], ssdBytes=10 ** 18)
        config['outputBounds'] = {name: 4096 for name in hosted.OUTPUT_BOUNDS}
        return config

    def larger(self):
        config=self.admitted()
        config['activeRunner']=dict(kind='github-larger-runner',groupName='roseos-mustang-signer',groupId=6,
            label='mustang-signer-4core',image='ubuntu-24.04',vcpu=4,ssdBytes=150000000000,
            configured=True,approved=True,approvalRef='synthetic owner approval',configurationRef='synthetic route observation')
        return config

    def test_larger_profile_requires_configuration_and_matches_only_the_selected_route(self):
        value=self.larger()
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/'capacity.json';path.write_text(json.dumps(value))
            self.assertEqual(hosted.load_capacity(path)['activeRunner']['groupId'],6)
            for field,wrong in (('configured',False),('approvalRef',None),('groupName','other-group'),('vcpu',8)):
                changed=json.loads(json.dumps(value));changed['activeRunner'][field]=wrong
                path.write_text(json.dumps(changed))
                with self.assertRaisesRegex(ValueError,'larger-runner identity/approval'):
                    hosted.load_capacity(path)

    def test_actual_wrong_signer_capacity_stops_before_secret_access(self):
        bundle,files,pin=self.tiny_bundle();root=self.checkout(files['generateRelease'].read_bytes())
        def call(secret_access):
            hosted.evaluate(bundle,root,free=10**18,files=files,pin=pin,
                env=dict(ENV,GITHUB_JOB='signer'),capacity_config=self.larger(),secret_access=secret_access)
        with patch.object(hosted.os,'cpu_count',return_value=8):
            self.assert_secret_closed(call,'actual signer CPU/memory/disk')

    def test_closed_signer_admission_is_known_before_any_native_build(self):
        with self.assertRaisesRegex(ValueError,'hosted signer admission is closed; no native build'):
            hosted.require_delivery_capacity(ROOT)

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

        self.assert_secret_closed(call, 'output bound is missing')
        with self.assertRaises(hosted.CapacityExceeded) as caught:
            hosted.capacity(bundle, free=10 ** 18, config=hosted.load_capacity())
        self.assertIn(f'required {required} bytes', str(caught.exception))
        self.assertIn(f'stock SSD {hosted.STOCK_SSD_BYTES} bytes', str(caught.exception))
        self.assertIn('hosted signer admission is closed', str(caught.exception))

    def test_unmeasured_jdk_and_script_do_not_count_as_zero_when_space_would_fit(self):
        bundle = self.bundle()
        bundle['unsigned'] = {'sha256': 'a' * 64, 'sizeBytes': 100}
        bundle['otatools'] = {'sha256': 'b' * 64, 'sizeBytes': 100}

        def call(secret_access):
            hosted.evaluate(bundle, ROOT, free=10 ** 18, secret_access=secret_access, env=ENV)

        self.assert_secret_closed(call, 'unmeasured signer inputs: jdk,generateRelease')

    def test_missing_script_pin_blocks_secret_access_even_with_capacity(self):
        pin = json.loads((ROOT / hosted.SCRIPT_PIN).read_text())
        self.assertEqual(pin['status'],'qualified')
        pin=dict(pin,generateReleaseSha256=None)
        self.assertEqual(pin['entryPoint'], hosted.ENTRY)
        bundle, files, _ = self.tiny_bundle()

        def call(secret_access):
            hosted.evaluate(bundle, ROOT, free=10 ** 18, files=files, secret_access=secret_access, env=ENV,
                            capacity_config=self.admitted(),pin=pin)

        self.assert_secret_closed(call, 'sign script pin is unbound')

    def test_negative_hash_unsafe_zip_missing_file_and_wrong_run_block_secret_access(self):
        bundle, files, pin = self.tiny_bundle()
        Path(files['unsigned']).write_bytes(b'changed')

        def hashed(secret_access):
            hosted.evaluate(bundle, ROOT, free=10 ** 18, files=files, secret_access=secret_access, pin=pin, env=ENV,
                            capacity_config=self.admitted())

        self.assert_secret_closed(hashed, 'signing bundle hash differs')
        unsafe = self.tiny_bundle(zip_member='../escape')
        def slipped(secret_access):
            hosted.evaluate(unsafe[0], ROOT, free=10 ** 18, files=unsafe[1], secret_access=secret_access,
                            pin=unsafe[2], env=ENV, capacity_config=self.admitted())
        self.assert_secret_closed(slipped, 'unsafe zip member')
        linked = self.tiny_bundle(symlink=True)
        def link(secret_access):
            hosted.evaluate(linked[0], ROOT, free=10 ** 18, files=linked[1], secret_access=secret_access,
                            pin=linked[2], env=ENV, capacity_config=self.admitted())
        self.assert_secret_closed(link, 'unsafe zip symlink')
        absent_bundle, absent_files, absent_pin = self.tiny_bundle()
        absent_files.pop('jdk')
        def absent(secret_access):
            hosted.evaluate(absent_bundle, ROOT, free=10 ** 18, files=absent_files,
                            secret_access=secret_access, pin=absent_pin, env=ENV, capacity_config=self.admitted())
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

    def test_free_space_fails_closed_and_included_storage_is_not_a_file_cap(self):
        bundle, files, pin = self.tiny_bundle()

        def tight(secret_access):
            hosted.evaluate(bundle, ROOT, free=0, files=files, secret_access=secret_access, pin=pin, env=ENV,
                            capacity_config=self.admitted())

        self.assert_secret_closed(tight, 'zip expansion exceeds capacity')
        oversized = self.bundle()
        oversized['unsigned'] = {'sha256': 'c' * 64, 'sizeBytes': 600000000}
        oversized['otatools'] = {'sha256': 'd' * 64, 'sizeBytes': 1}
        oversized['jdk'] = {'sha256': 'e' * 64, 'sizeBytes': 1}
        oversized['generateRelease'] = {'sha256': 'f' * 64, 'sizeBytes': 1}
        measured = {'unsigned': 600000000, 'otatools': 1, 'signedTargetFiles': 1, 'ota': 1}

        def over_allowance(secret_access):
            try:
                hosted.evaluate(oversized, ROOT, free=10 ** 18, secret_access=secret_access, pin=pin, env=ENV,
                                capacity_config=self.admitted(), measured=measured)
            except ValueError as error:
                self.assertNotIn('artifact quota', str(error))
                self.assertNotIn('500', str(error))
                raise

        self.assert_secret_closed(over_allowance, 'signing bundle files are absent')

    def checkout(self, script_bytes):
        root = Path(self.directory.name) / 'checkout'
        bootstrap = root / 'os/signing-bootstrap'
        bootstrap.mkdir(parents=True)
        shutil.copy(ROOT / 'os/signing-bootstrap/mustang-bootstrap-receipt.json', bootstrap / 'mustang-bootstrap-receipt.json')
        scripts = root / 'scripts'
        scripts.mkdir()
        shutil.copy(ROOT / 'scripts/mustang_hosted_sign.py', scripts / 'mustang_hosted_sign.py')
        script = root / 'script/generate-release.sh'
        script.parent.mkdir()
        script.write_bytes(script_bytes)
        return root

    def test_secret_callback_runs_only_after_capacity_pin_hash_extract_and_checkout_bytes(self):
        bundle, files, pin = self.tiny_bundle()
        root = self.checkout(files['generateRelease'].read_bytes())
        called = []
        admitted = hosted.evaluate(bundle, root, free=10 ** 18, files=files,
                                   secret_access=lambda: called.append('secret'), pin=pin, env=ENV,
                                   capacity_config=self.admitted())
        self.assertEqual(called, ['secret'])
        self.assertTrue(admitted['admit'])
        self.assertTrue(os.access(Path(files['otatools']).parent / 'otatools-extracted' / 'bin' / 'delta_generator', os.X_OK))
        self.assertFalse(os.access(Path(files['otatools']).parent / 'otatools-extracted' / 'bin' / 'readme.txt', os.X_OK))
        self.assertTrue(os.access(admitted['javaHome'] / 'bin' / 'java', os.X_OK))
        self.assertLess(admitted['requiredBytes'], 10 ** 18)

    def test_sign_calls_host_sign_only_after_admission_and_writes_the_receipt(self):
        bundle, files, pin = self.tiny_bundle()
        bundle['build'] = '1'
        root = self.checkout(files['generateRelease'].read_bytes())
        calls = []

        def host_sign_main(config_path, env, managed_decrypted_keys=False, **kwargs):
            self.assertTrue(managed_decrypted_keys)
            self.assertEqual(env['ROSE_CANDIDATE_SHA'], ENV['GITHUB_SHA'])
            config = json.loads(Path(config_path).read_text())
            self.assertEqual(config['host']['sign']['keysSecret'], hosted.KEYS)
            self.assertEqual(config['host']['sign']['secretVersion'], '1')
            java = Path(config['host']['sign']['javaHome']) / 'bin' / 'java'
            archive = Path(config['host']['sign']['otatools'])
            self.assertTrue(archive.is_file() and zipfile.is_zipfile(archive))
            self.assertEqual(env['ROSE_SIGN_OTATOOLS'], str(archive))
            self.assertTrue((Path(env['ROSE_RELEASE_DIR']) / 'unsigned-target_files.zip').is_file())
            tool = archive.parent / 'otatools-extracted' / 'bin' / 'delta_generator'
            self.assertTrue(java.is_file() and os.access(java, os.X_OK))
            self.assertEqual(java.read_bytes(), b'java-stub')
            self.assertTrue(tool.is_file() and os.access(tool, os.X_OK))
            calls.append(config_path)
            release = Path(env['ROSE_RELEASE_DIR'])
            (release / 'signed-target_files.zip').write_bytes(b'signed-target')
            public = release / 'public'
            public.mkdir()
            (public / 'mustang-ota_update-1.zip').write_bytes(b'signed-ota')
            return 0

        with self.assertRaisesRegex(ValueError, 'hosted signer admission is closed'):
            hosted.sign(self.write_bundle(bundle, files), Path(self.directory.name) / 'closed-out', root=root,
                        env=ENV, host_sign_main=host_sign_main, free=10 ** 18, pin=pin)
        self.assertEqual(calls, [])
        out = Path(self.directory.name) / 'signed-out'
        receipt = hosted.sign(self.write_bundle(bundle, files), out, root=root, env=ENV,
                              capacity_config=self.admitted(), host_sign_main=host_sign_main, free=10 ** 18, pin=pin)
        self.assertEqual(len(calls), 1)
        self.assertEqual(receipt['sourceSha'], ENV['GITHUB_SHA'])
        self.assertEqual(receipt['runId'], ENV['GITHUB_RUN_ID'])
        hosted.bind_outputs(out, env=ENV)
        escaped = dict(receipt)
        escaped['outputs'] = dict(receipt['outputs'])
        escaped['outputs']['ota'] = dict(receipt['outputs']['ota'], file='../ota.zip')
        (out / 'mustang-hosted-sign-receipt.json').write_text(json.dumps(escaped))
        with self.assertRaisesRegex(ValueError, 'left the receipt directory'):
            hosted.bind_outputs(out, env=ENV)

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
            out = root / 'bundle'
            bundle = hosted.export_bundle(path, out)
            text = (out / 'mustang-signing-bundle.json').read_text()
            self.assertEqual((out / 'inputs/unsigned-target_files.zip').read_bytes(), unsigned.read_bytes())
            self.assertEqual((out / 'inputs/otatools.zip').read_bytes(), tools.read_bytes())
            self.assertFalse((out / 'inputs/jdk.bin').exists())
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
            self.assertIn('hosted signer admission is closed', result.stderr)
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
            self.assertIn('output bound is missing', result.stderr)
            self.assertIn('hosted signer admission is closed', result.stderr)
            self.assertNotIn(str(required), result.stderr)
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
                'secretVersion': hosted.SECRET_VERSION, 'workflow': hosted.WORKFLOW,
                'runId': ENV['GITHUB_RUN_ID'], 'runAttempt': ENV['GITHUB_RUN_ATTEMPT'], 'sourceSha': ENV['GITHUB_SHA'],
                'outputs': {
                    'signedTargetFiles': {'file': 'ota.zip', 'sizeBytes': len(payload), 'sha256': digest(directory / 'ota.zip')},
                    'ota': {'file': 'ota.zip', 'sizeBytes': len(payload), 'sha256': '0' * 64},
                },
            }
            (directory / 'mustang-hosted-sign-receipt.json').write_text(json.dumps(receipt))
            with self.assertRaisesRegex(ValueError, 'hosted signed output hash differs'):
                hosted.bind_outputs(directory, env=ENV)
            receipt['sourceSha'] = 'b' * 40
            (directory / 'mustang-hosted-sign-receipt.json').write_text(json.dumps(receipt))
            with self.assertRaisesRegex(ValueError, 'hosted signing receipt is not this run'):
                hosted.bind_outputs(directory, env=ENV)

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
        self.assertIn('group: roseos-mustang-signer\n', signer)
        self.assertIn('labels: mustang-signer-4core\n', signer)
        self.assertNotIn('runs-on: ubuntu-24.04', signer)
        self.assertNotIn('roseos-mustang-builder', signer)
        self.assertIn('group: ${{ needs.guard.outputs.runner_group }}', builder)
        self.assertIn('group: ${{ needs.guard.outputs.runner_group }}', jobs['source'])
        self.assertNotIn('roseos-mustang-signer', builder)
        self.assertNotIn('roseos-mustang-signer', jobs['source'])
        self.assertNotIn('mustang-signer-4core', builder)
        self.assertNotIn('mustang-signer-4core', jobs['source'])
        self.assertNotIn('mustang_source_sync', signer)
        self.assertNotIn('immortal-os-builder', signer)
        self.assertIn('id-token: write', signer)
        self.assertLess(signer.index('mustang_hosted_sign.py preliminary'), signer.index('mustang-signing-inputs-'))
        self.assertLess(signer.index('mustang-signing-inputs-'), signer.index('mustang_hosted_sign.py preflight'))
        self.assertLess(signer.index('mustang_hosted_sign.py preflight'), signer.index('actions-gcp-login.py login'))
        self.assertIn("steps.preliminary.outputs.allow == 'true'", signer.split('mustang-signing-inputs-', 1)[0][-500:])
        self.assertNotIn("steps.verified.outputs.admit", signer.split('name: Download the same-run input bytes', 1)[1].split('id: verified', 1)[0])
        self.assertEqual(signer.count("steps.verified.outputs.admit == 'true'"), 3)
        self.assertIn("steps.preliminary.outputs.allow == 'true'", builder)
        for flag in ('--checkout', '--capacity', '--pin'):
            self.assertNotIn(flag, workflow)
        self.assertIn('mustang-signing-inputs-', builder)
        self.assertIn('mustang-signing-bundle/inputs', builder)
        self.assertNotIn('mustang-signer-linux-4-core', workflow)
        self.assertIn('--out "$RUNNER_TEMP/mustang-hosted-signed"', signer)
        signer_jobs = [name for name, body in jobs.items()
                       if '/mustang-actions/providers/mustang-main' in body and 'id-token: write' in body]
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
        self.assertEqual(runtime['signingRouteEvidence'],'os/build-only/mustang-dedicated-signing-route.json')
        self.assertEqual(runtime['sourceEvidence'], 'os/build-only/mustang-full-run-source.json')
        self.assertTrue(runtime['publication']['enabled'])

    def test_each_peak_term_and_a_missing_bound_deny_before_secrets(self):
        bundle, files, pin = self.tiny_bundle()
        root = self.checkout(files['generateRelease'].read_bytes())
        config = self.admitted()
        bounds = config['outputBounds']
        otatools_uncompressed, _rows = hosted.zip_members(files['otatools'])
        jdk_uncompressed, _jdk_rows = hosted.zip_members(files['jdk'])
        terms = hosted.current_peak_terms(
            files['unsigned'].stat().st_size, files['otatools'].stat().st_size, otatools_uncompressed,
            files['jdk'].stat().st_size, jdk_uncompressed, files['generateRelease'].stat().st_size, bounds)
        total = sum(terms.values())
        self.assertGreater(total, 0)
        for name, value in terms.items():
            self.assertGreater(value, 0, name)
            without = total - value
            def tight(secret_access, free=without):
                hosted.evaluate(bundle, root, free, files=files, secret_access=secret_access, pin=pin,
                                env=ENV, capacity_config=config)
            self.assert_secret_closed(tight, f'current peak {total} bytes')
        for name in hosted.OUTPUT_BOUNDS:
            broken = self.admitted()
            broken['outputBounds'][name] = None
            def missing(secret_access, broken=broken, missing_name=name):
                try:
                    hosted.evaluate(bundle, root, 10 ** 18, files=files, secret_access=secret_access, pin=pin,
                                    env=ENV, capacity_config=broken)
                except ValueError as error:
                    self.assertNotIn('22868848092', str(error))
                    self.assertIn('output bound is missing: ' + missing_name, str(error))
                    raise
            self.assert_secret_closed(missing, 'output bound is missing: ' + name)

    def test_workflow_cli_downloads_payload_only_after_metadata_gate(self):
        bundle, files, pin = self.tiny_bundle()
        bundle['build'] = '1'
        root = self.checkout(files['generateRelease'].read_bytes())
        collection = {
            'schema': 'roseos.mustang-compile-collection.v1', 'status': 'complete', 'build': '1',
            'runId': ENV['GITHUB_RUN_ID'], 'runAttempt': ENV['GITHUB_RUN_ATTEMPT'],
            'collectorSource': ENV['GITHUB_SHA'], 'generateReleasePath': str(files['generateRelease']),
            'outputs': {'artifacts': {
                'targetFiles': {'state': 'collected', 'path': str(files['unsigned']),
                                'sizeBytes': files['unsigned'].stat().st_size, 'sha256': digest(files['unsigned'])},
                'otatools': {'state': 'collected', 'path': str(files['otatools']),
                             'sizeBytes': files['otatools'].stat().st_size, 'sha256': digest(files['otatools'])},
                'jdk': {'state': 'collected', 'path': str(files['jdk']),
                        'sizeBytes': files['jdk'].stat().st_size, 'sha256': digest(files['jdk'])},
            }},
        }
        source = Path(self.directory.name)
        (source / 'collection.json').write_text(json.dumps(collection))
        hosted.export_bundle(source / 'collection.json', source / 'uploaded')
        signer = source / 'signer'
        signer.mkdir()
        document = json.loads((source / 'uploaded' / 'mustang-signing-bundle.json').read_text())
        document['otatoolsExtractedBytes'] = hosted.zip_members(files['otatools'])[0]
        document['jdkExtractedBytes'] = hosted.zip_members(files['jdk'])[0]
        (signer / 'mustang-signing-bundle.json').write_text(json.dumps(document))
        self.assertFalse((signer / 'inputs').exists())
        script = str(ROOT / 'scripts' / 'mustang_hosted_sign.py')
        output = source / 'github-output.txt'
        output.write_text('')
        closed_env = dict(PATH=os.environ['PATH'], GITHUB_OUTPUT=str(output), **ENV)
        closed = subprocess.run([sys.executable, script, 'preliminary', '--bundle', str(signer)],
                                env=closed_env, capture_output=True, text=True, timeout=30)
        self.assertEqual(closed.returncode, 4, closed.stderr)
        self.assertIn('hosted signer admission is closed', closed.stderr)
        self.assertNotIn('signing bundle hash differs', closed.stderr)
        self.assertNotIn('resumed file', closed.stderr)
        self.assertNotIn('allow=true', output.read_text())
        self.assertFalse((signer / 'inputs').exists())
        for action in ('preliminary', 'gate', 'preflight', 'sign'):
            for flag in ('--capacity', '--pin', '--checkout'):
                refused = subprocess.run(
                    [sys.executable, script, action, '--bundle', str(signer), flag, str(source / 'override')],
                    env=dict(PATH=os.environ['PATH'], **ENV), capture_output=True, text=True, timeout=30)
                self.assertEqual(refused.returncode, 2, refused.stderr)
                self.assertIn('unrecognized arguments', refused.stderr)
                self.assertIn(flag, refused.stderr)
                self.assertNotIn('allow=true', refused.stdout)
                self.assertNotIn('admit=true', refused.stdout)
                self.assertFalse((signer / 'inputs').exists())
        capacity = source / 'capacity.json'
        capacity.write_text(json.dumps(self.admitted()))
        pin_path = source / 'pin.json'
        pin_path.write_text(json.dumps(pin))

        def resolver():
            return root, capacity, pin_path

        def run_parser(action, extra=()):
            output.write_text('')
            with patch.object(hosted, 'trusted_paths', resolver), patch.dict(os.environ, closed_env, clear=False):
                code = hosted.main([action, '--bundle', str(signer), *extra])
            return code, output.read_text()

        code, text = run_parser('preliminary')
        self.assertEqual(code, 0, text)
        self.assertEqual(text, 'allow=true\n')
        self.assertNotIn('admit=true', text)
        self.assertFalse((signer / 'inputs').exists())
        code, text = run_parser('preflight')
        self.assertEqual(code, 4)
        self.assertNotIn('admit=true', text)
        self.assertFalse((signer / 'inputs').exists())
        shutil.copytree(source / 'uploaded' / 'inputs', signer / 'inputs')
        code, text = run_parser('preflight')
        self.assertEqual(code, 0, text)
        self.assertIn('admit=true', text)
        login = ['login']
        calls = []

        def run_host_sign(source_root, release_dir, extracted, jdk_home, bundle, env, host_sign_main=None):
            self.assertEqual(login, ['login'])
            java = Path(jdk_home) / 'bin' / 'java'
            self.assertTrue(java.is_file() and os.access(java, os.X_OK))
            calls.append('sign')
            release = Path(release_dir)
            release.mkdir(parents=True, exist_ok=True)
            (release / 'signed-target_files.zip').write_bytes(b'signed-target')
            public = release / 'public'
            public.mkdir()
            (public / 'mustang-ota_update-1.zip').write_bytes(b'signed-ota')
            return release

        out = source / 'publisher'
        with patch.object(hosted, 'trusted_paths', resolver), patch.object(hosted, 'run_host_sign', run_host_sign), \
                patch.dict(os.environ, closed_env, clear=False):
            code = hosted.main(['sign', '--bundle', str(signer), '--out', str(out)])
        self.assertEqual(code, 0)
        self.assertEqual(calls, ['sign'])
        self.assertEqual(hosted.bind_outputs(out, env=ENV)['runId'], ENV['GITHUB_RUN_ID'])

    def test_preflight_then_sign_resumes_extract_and_publisher_sees_the_receipt(self):
        bundle, files, pin = self.tiny_bundle()
        bundle['build'] = '1'
        root = self.checkout(files['generateRelease'].read_bytes())
        collection = {
            'schema': 'roseos.mustang-compile-collection.v1', 'status': 'complete', 'build': '1',
            'runId': ENV['GITHUB_RUN_ID'], 'runAttempt': ENV['GITHUB_RUN_ATTEMPT'],
            'collectorSource': ENV['GITHUB_SHA'], 'generateReleasePath': str(files['generateRelease']),
            'outputs': {'artifacts': {
                'targetFiles': {'state': 'collected', 'path': str(files['unsigned']),
                                'sizeBytes': files['unsigned'].stat().st_size, 'sha256': digest(files['unsigned'])},
                'otatools': {'state': 'collected', 'path': str(files['otatools']),
                             'sizeBytes': files['otatools'].stat().st_size, 'sha256': digest(files['otatools'])},
                'jdk': {'state': 'collected', 'path': str(files['jdk']),
                        'sizeBytes': files['jdk'].stat().st_size, 'sha256': digest(files['jdk'])},
            }},
        }
        collection_path = Path(self.directory.name) / 'collection.json'
        collection_path.write_text(json.dumps(collection))
        exported = hosted.export_bundle(collection_path, Path(self.directory.name) / 'uploaded')
        downloaded = Path(self.directory.name) / 'downloaded'
        shutil.copytree(Path(self.directory.name) / 'uploaded', downloaded)
        self.assertEqual(exported['otatools']['sha256'], pin['otatoolsSha256'])
        login, secret = [], []
        config = self.admitted()
        lied = json.loads((downloaded / 'mustang-signing-bundle.json').read_text())
        lied['otatools']['sha256'] = '0' * 64
        (downloaded / 'mustang-signing-bundle.json').write_text(json.dumps(lied))

        def denied(secret_access):
            hosted.evaluate(lied, root, 10 ** 18, files=hosted.bundle_files(downloaded, lied),
                            secret_access=secret_access, pin=pin, env=ENV, capacity_config=config)
        self.assert_secret_closed(denied, 'signing bundle hash differs')
        lied['otatools']['sha256'] = pin['otatoolsSha256']
        lied['sourceSha'] = 'b' * 40
        def wrong_run(secret_access):
            hosted.evaluate(lied, root, 10 ** 18, files=hosted.bundle_files(downloaded, lied),
                            secret_access=secret_access, pin=pin, env=dict(ENV, GITHUB_SHA='a' * 40),
                            capacity_config=config)
        self.assert_secret_closed(wrong_run, 'signing bundle is not this run')
        (downloaded / 'mustang-signing-bundle.json').write_text(json.dumps(exported))
        exported['otatoolsExtractedBytes'] = hosted.zip_members(files['otatools'])[0]
        exported['jdkExtractedBytes'] = hosted.zip_members(files['jdk'])[0]
        (downloaded / 'mustang-signing-bundle.json').write_text(json.dumps(exported))
        guest_pin = dict(pin, otatoolsSha256='1' * 64)
        def guest_attested(secret_access):
            hosted.evaluate(exported, root, 10 ** 18, files=hosted.bundle_files(downloaded, exported),
                            secret_access=secret_access, pin=guest_pin, env=ENV, capacity_config=config)
        self.assert_secret_closed(guest_attested, 'otatools provenance is not the trusted pin')
        hosted.preflight(downloaded, root=root, env=ENV, capacity_config=config, pin=pin, free=10 ** 18)
        login.append('login')

        def host_sign_main(config_path, env, managed_decrypted_keys=False, **kwargs):
            self.assertTrue(managed_decrypted_keys)
            config = json.loads(Path(config_path).read_text())
            java = Path(config['host']['sign']['javaHome']) / 'bin' / 'java'
            archive = Path(config['host']['sign']['otatools'])
            self.assertTrue(archive.is_file() and zipfile.is_zipfile(archive))
            self.assertEqual(env['ROSE_SIGN_OTATOOLS'], str(archive))
            self.assertTrue((Path(env['ROSE_RELEASE_DIR']) / 'unsigned-target_files.zip').is_file())
            tool = archive.parent / 'otatools-extracted' / 'bin' / 'delta_generator'
            readme = archive.parent / 'otatools-extracted' / 'bin' / 'readme.txt'
            self.assertTrue(java.is_file() and os.access(java, os.X_OK))
            self.assertTrue(tool.is_file() and os.access(tool, os.X_OK))
            self.assertTrue(readme.is_file() and not os.access(readme, os.X_OK))
            self.assertEqual(digest(downloaded / 'inputs' / 'otatools.zip'), pin['otatoolsSha256'])
            secret.append('sign')
            release = Path(env['ROSE_RELEASE_DIR'])
            (release / 'signed-target_files.zip').write_bytes(b'signed-target')
            public = release / 'public'
            public.mkdir()
            (public / 'mustang-ota_update-1.zip').write_bytes(b'signed-ota')
            return 0

        out = Path(self.directory.name) / 'publisher'
        receipt = hosted.sign(downloaded, out, root=root, env=ENV, capacity_config=config,
                              host_sign_main=host_sign_main, free=10 ** 18, pin=pin)
        self.assertEqual(login, ['login'])
        self.assertEqual(secret, ['sign'])
        self.assertEqual(hosted.bind_outputs(out, env=ENV)['sourceSha'], ENV['GITHUB_SHA'])
        self.assertEqual(receipt['outputs']['signedTargetFiles']['sha256'], digest(out / 'signed-target_files.zip'))

    def test_zip_traversal_symlink_duplicate_bomb_modes_and_resume(self):
        bundle, files, pin = self.tiny_bundle()
        root = self.checkout(files['generateRelease'].read_bytes())
        config = self.admitted()
        called = []

        def attempt(secret_access):
            hosted.evaluate(bundle, root, 10 ** 18, files=files, secret_access=secret_access, pin=pin,
                            env=ENV, capacity_config=config)
        hosted.evaluate(bundle, root, 10 ** 18, files=files, secret_access=lambda: called.append('first'),
                        pin=pin, env=ENV, capacity_config=config)
        hosted.evaluate(bundle, root, 10 ** 18, files=files, secret_access=lambda: called.append('second'),
                        pin=pin, env=ENV, capacity_config=config)
        self.assertEqual(called, ['first', 'second'])
        tool = Path(files['otatools']).parent / 'otatools-extracted' / 'bin' / 'delta_generator'
        tool.write_bytes(b'TAMPERED')
        os.chmod(tool, 0o755)
        self.assertEqual(stat.S_IMODE(tool.stat().st_mode), 0o755)
        duplicate = Path(self.directory.name) / 'duplicate.zip'
        with zipfile.ZipFile(duplicate, 'w') as archive:
            archive.writestr('bin/delta_generator', b'one')
            archive.writestr('bin/delta_generator', b'two')
        with self.assertRaisesRegex(ValueError, 'duplicate zip member'):
            hosted.reject_unsafe_zip(duplicate, 10 ** 9)
        with self.assertRaisesRegex(ValueError, 'zip expansion exceeds capacity'):
            hosted.reject_unsafe_zip(files['otatools'], 1)
        self.assert_secret_closed(attempt, 'resumed file does not match the trusted archive')

    def tiny_bundle(self, zip_member='payload.txt', symlink=False):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        unsigned = root / 'unsigned.zip'
        tools = root / 'otatools.zip'
        script = root / 'generate-release.sh'
        jdk = root / 'jdk.zip'
        if symlink:
            info = zipfile.ZipInfo('link')
            info.date_time = (2026, 1, 1, 0, 0, 0)
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            with zipfile.ZipFile(unsigned, 'w') as archive:
                archive.writestr(info, 'target')
        else:
            write_zip(unsigned, zip_member, b'unsigned')
        write_zip(tools, members=[('bin/delta_generator', b'tool', 0o755), ('bin/readme.txt', b'note', 0o644)])
        script.write_bytes(b'script-bytes')
        write_zip(jdk, members=[('bin/java', b'java-stub', 0o755)])
        files = {'unsigned': unsigned, 'otatools': tools, 'generateRelease': script, 'jdk': jdk}
        bundle = self.bundle()
        bundle['unsigned'] = {'sha256': digest(unsigned), 'sizeBytes': unsigned.stat().st_size}
        bundle['otatools'] = {'sha256': digest(tools), 'sizeBytes': tools.stat().st_size}
        bundle['generateRelease'] = {'sha256': digest(script), 'sizeBytes': script.stat().st_size}
        bundle['jdk'] = {'sha256': digest(jdk), 'sizeBytes': jdk.stat().st_size}
        bundle['otatoolsExtractedBytes'] = hosted.zip_members(tools)[0]
        bundle['jdkExtractedBytes'] = hosted.zip_members(jdk)[0]
        pin = {'entryPoint': hosted.ENTRY, 'generateReleasePath': 'script/generate-release.sh',
               'generateReleaseSha256': digest(script), 'jdkSha256': digest(jdk),
               'otatoolsSha256': digest(tools), 'otatoolsExecutable': 'bin/delta_generator'}
        return bundle, files, pin

    def write_bundle(self, bundle, files):
        directory = Path(self.directory.name) / 'saved-bundle'
        if directory.exists():
            shutil.rmtree(directory)
        inputs = directory / 'inputs'
        inputs.mkdir(parents=True)
        mapping = {
            'unsigned': 'inputs/unsigned-target_files.zip',
            'otatools': 'inputs/otatools.zip',
            'jdk': 'inputs/jdk.zip',
            'generateRelease': 'inputs/generate-release.sh',
        }
        saved = json.loads(json.dumps(bundle))
        for key, relative in mapping.items():
            target = directory / relative
            shutil.copy(files[key], target)
            saved[key] = dict(saved[key], file=relative)
        (directory / 'mustang-signing-bundle.json').write_text(json.dumps(saved))
        return directory


if __name__ == '__main__':
    unittest.main()
