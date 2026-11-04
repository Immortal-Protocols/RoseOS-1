"""FAM-02-INFRA-S001: the explicit device delivery chain admits only frankel. Unknown, unsafe or mismatched devices are
refused by the workflow guard and the pipeline before any host action; receipts, build identity, OTA metadata and
publication are bound to the dispatched device; no device other than frankel can ever move the pixel-ota Latest; and
every frankel name, tag, feed and URL is byte-identical to before. Synthetic fixtures only; nothing is built or sent."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import types
import unittest
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
import os_release_shape as shape  # noqa: E402

SCRIPTS = Path(__file__).resolve().parents[1]
ROOT = SCRIPTS.parent
PY = sys.executable
WORKFLOW = (ROOT/'.github/workflows/os-release.yml').read_text()
FRANKEL = json.loads((ROOT/'os/release-pipeline/frankel.json').read_text())

def load(name, file):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS/file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

pipeline = load('release_pipeline_device', 'release-pipeline.py')

def jobs(text):
    body = text.split('\njobs:\n', 1)[1]
    return {m[1]: m[2] for m in re.finditer(r'^  ([\w-]+):\n((?:(?:    .*)?\n)*)', body, re.M)}

JOBS = jobs(WORKFLOW)

class AllowlistTest(unittest.TestCase):
    def test_only_frankel_is_admitted_and_malformed_names_never_reach_a_path(self):
        self.assertEqual((pipeline.SUPPORTED_DEVICES, pipeline.LATEST_DEVICES), (('frankel',), ('frankel',)))
        self.assertEqual(pipeline.resolve_device('frankel'), 'frankel')
        for device in ('blazer', 'rango', 'mustang', 'tokay'):
            with self.subTest(device=device), self.assertRaisesRegex(pipeline.InvalidInput, f'device {device} is not admitted'):
                pipeline.resolve_device(device)
        for device in ('FRANKEL', 'frankel ', '../frankel', 'frankel/../x', 'f', '', None, 'frankel;rm', 'frankel-1'):
            with self.subTest(device=device), self.assertRaisesRegex(pipeline.InvalidInput, 'lowercase device codename'):
                pipeline.resolve_device(device)

    def test_the_pipeline_refuses_a_device_before_reading_config_or_starting_anything(self):
        env = {k: v for k, v in os.environ.items() if k != 'ROSE_DEVICE'}
        for args, extra in ((['--device', 'blazer'], {}), ([], {'ROSE_DEVICE': 'rango'}), (['--device', '../frankel'], {})):
            result = subprocess.run([PY, str(SCRIPTS/'release-pipeline.py'), 'start-builder', *args], capture_output=True, text=True,
                env=dict(env, **extra))
            self.assertEqual(result.returncode, 2, (args, extra, result.stderr))
            self.assertEqual(json.loads(result.stderr)['verdict'], 'INVALID')
        with tempfile.TemporaryDirectory() as tmp:
            mismatched = Path(tmp)/'config.json'
            mismatched.write_text(json.dumps(dict(FRANKEL, target=dict(FRANKEL['target'], device='blazer'))))
            result = subprocess.run([PY, str(SCRIPTS/'release-pipeline.py'), 'preflight', '--job', 'guard', '--device', 'frankel',
                '--config', str(mismatched)], capture_output=True, text=True, env=env)
            self.assertEqual(result.returncode, 2)
            self.assertIn("config target.device is 'blazer', not the dispatched frankel", result.stderr)
        # The default is still frankel's own config, unchanged.
        self.assertEqual(pipeline.device_config('frankel')['target'], FRANKEL['target'])

class WorkflowTest(unittest.TestCase):
    def guard_device_lines(self):
        block = re.search(r'- name: Candidate is an exact commit on main\n(?:.*\n)*?        run: \|\n((?: {10}.*\n)+)', JOBS['guard'])[1]
        lines = [l[10:] for l in block.splitlines()]
        start = lines.index('# The device names attempt directories and artifacts: a plain codename, admitted by the pipeline\'s allowlist.')
        return '\n'.join(['set -euo pipefail', *lines[start:start + 5]])

    def test_the_guard_admits_only_frankel_before_the_controller(self):
        # The admitted-device property is the choice itself: exactly frankel, defaulting to frankel (its wording may change).
        device = shape.inputs()['device']
        self.assertEqual((device['type'], device['options'], device['default']), ('choice', ['frankel'], 'frankel'))
        self.assertIn('frankel only', re.search(r'(?m)^      device:\n        description: (.*)$', WORKFLOW)[1])
        self.assertIn("  ROSE_DEVICE: ${{ inputs.device || 'frankel' }}\n", WORKFLOW)
        self.assertIn('    needs: guard\n', JOBS['controller'])
        script = self.guard_device_lines()
        for device, mode, ok in (('frankel', 'os-release', True), ('frankel', 'app-reuse', True), ('blazer', 'os-release', False),
                                 ('rango', 'os-release', False), ('../x', 'os-release', False), ('frankel;id', 'os-release', False),
                                 ('', 'os-release', False)):
            result = subprocess.run(['bash', '-c', script], cwd=ROOT, env=dict(os.environ, ROSE_DEVICE=device, MODE=mode),
                capture_output=True, text=True)
            self.assertEqual(result.returncode == 0, ok, (device, mode, result.stderr))

    def test_frankel_names_are_byte_identical_and_the_shared_runner_lock_and_group_stay(self):
        builder, publisher = JOBS['builder'], JOBS['publisher']
        self.assertIn('          base="$HOME/rose-actions/$ROSE_DEVICE-$BUILD_NUMBER"\n', builder)
        self.assertIn('--source-revision "$CANDIDATE_SHA" --device "$ROSE_DEVICE" --build-number "$BUILD_NUMBER"', builder)
        self.assertIn('          name: os-release-${{ env.ROSE_DEVICE }}-${{ steps.candidate.outputs.build_number }}\n', builder)
        self.assertIn('          name: os-release-${{ env.ROSE_DEVICE }}-${{ needs.builder.outputs.build_number }}\n', publisher)
        self.assertIn('python3 scripts/release-pipeline.py publish --device "$ROSE_DEVICE" ', publisher)
        self.assertNotIn('frankel-$BUILD_NUMBER', WORKFLOW)
        self.assertIn('  group: os-release-frankel\n', WORKFLOW)
        self.assertEqual(sorted(set(re.findall(r'runs-on: \[self-hosted, ([\w-]+)\]', WORKFLOW))), ['roseos-frankel-builder'])
        self.assertEqual(FRANKEL['host']['lockPath'], '/home/pixelbuild/frankel-build.lock')
        # The real prepare block, run with ROSE_DEVICE=frankel, creates exactly the historical attempt directory.
        prepare = re.search(r'- name: Prepare stage receipts\n\s+run: \|\n((?: {10}.*\n)+)', builder)[1]
        with tempfile.TemporaryDirectory() as tmp:
            env = dict(os.environ, HOME=tmp, ROSE_DEVICE='frankel', CANDIDATE_SHA='3' * 40, BUILD_NUMBER='2026102901', RESUME_STAGE='none',
                RESUME_SOURCE_RUN='', RESUME_UNSIGNED_SHA256='', GITHUB_ENV=str(Path(tmp)/'env'), GITHUB_RUN_ID='7')
            result = subprocess.run(['bash', '-c', prepare], cwd=ROOT, env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((Path(tmp)/'env').read_text().strip(), f'ROSE_BASE={tmp}/rose-actions/frankel-2026102901')
            init = json.loads((Path(tmp)/'rose-actions/frankel-2026102901/stages/000-init.json').read_text())
            self.assertEqual(init['candidate']['device'], 'frankel')
        # Frankel's tag, feed, Updater URL and Latest preservation are unchanged.
        self.assertEqual((FRANKEL['publication']['tag'], FRANKEL['publication']['channels']['beta']), ('frankel-{build}', {'promoteLatest': True, 'feedChannel': 'stable'}))
        self.assertIn('https://github.com/Immortal-Protocols/pixel-ota/releases/latest/download/', FRANKEL['host']['integrate']['reconcile']['applyArgs'])
        self.assertEqual(FRANKEL['appRelease']['publication']['latestTagPattern'], FRANKEL['publication']['tag'])
        self.assertEqual(pipeline.public_assets('frankel', '2026102901')[:2], ['frankel-ota_update-2026102901.zip', 'frankel-stable'])
        self.assertEqual(sorted(p.name for p in (ROOT/'os/release-pipeline').glob('*.json')), ['alpha-runtime.json', 'cuttlefish-alpha.json', 'frankel.json'],
            'no blazer or rango config is admitted in this slice')

class BindingTest(unittest.TestCase):
    def args(self, **kw):
        return types.SimpleNamespace(candidate_sha='a' * 40, build_number='2026102901', build_timestamp='1793300000', channel='beta', **kw)

    def test_the_candidate_and_every_receipt_carry_the_device(self):
        cand = pipeline.candidate(self.args(), 'frankel')
        self.assertEqual(cand['device'], 'frankel')
        with tempfile.TemporaryDirectory() as tmp:
            release = Path(tmp)/'release'
            (release/'public').mkdir(parents=True)
            for device in ('blazer', None):
                receipt = {'candidate': dict(cand, device=device) if device else {k: v for k, v in cand.items() if k != 'device'}, 'simulated': True}
                (release/'public/verification.json').write_text(json.dumps(receipt))
                a = types.SimpleNamespace(release_dir=str(release), artifact_dir=str(Path(tmp)/f'artifact-{device}'), apps='[]')
                with self.subTest(device=device), self.assertRaisesRegex(pipeline.CheckFailed, 'does not belong to this candidate'):
                    pipeline.stage_artifact(a, FRANKEL, cand, types.SimpleNamespace(simulated=True))

    def target(self, tmp, system, vendor, product=None, misc_system=None, vendor_device='frankel'):
        cand = pipeline.candidate(self.args(), 'frankel')
        path = Path(tmp)/'target.zip'
        tail = f':17/CP3A.261005.005/{cand["build"]}:user/release-keys'
        with zipfile.ZipFile(path, 'w') as z:
            z.writestr('SYSTEM/build.prop', f'ro.build.version.incremental={cand["build"]}\nro.build.date.utc={cand["timestamp"]}\n'
                f'ro.build.type=user\nro.product.system.device=generic\nro.system.build.fingerprint={system}{tail}\n')
            z.writestr('VENDOR/build.prop', f'ro.product.vendor.device={vendor_device}\nro.vendor.build.fingerprint={vendor}{tail}\n')
            if product:
                z.writestr('PRODUCT/etc/build.prop', f'ro.product.build.fingerprint={product}{tail}\n')
            z.writestr('META/misc_info.txt', f'avb_system_add_hashtree_footer_args=--prop com.android.build.system.fingerprint:{misc_system or system}{tail}\n'
                f'avb_vendor_add_hashtree_footer_args=--prop com.android.build.vendor.fingerprint:{vendor}{tail}\n')
        return pipeline.check_build_identity(path, FRANKEL, cand, 'release-keys'), tail

    def test_the_generic_system_image_passes_and_wrong_device_artifacts_fail(self):
        generic, frankel, blazer = 'Android/generic_system/generic', 'google/frankel/frankel', 'google/blazer/blazer'
        with tempfile.TemporaryDirectory() as tmp:
            # Positive: exactly the 2806 shape (run 40778491188): generic system partition, frankel everywhere else.
            failures, _ = self.target(tmp, generic, frankel, product=frankel)
            self.assertEqual(failures, [])
            failures, _ = self.target(tmp, frankel, frankel)  # a device-named system partition is fine too
            self.assertEqual(failures, [])
            # A blazer build: its vendor/product fingerprints and vendor device are refused even with a generic system.
            failures, tail = self.target(tmp, generic, blazer, product=blazer, vendor_device='blazer')
            self.assertIn('VENDOR/build.prop: ro.product.vendor.device=blazer', failures)
            self.assertIn('VENDOR/build.prop: ro.vendor.build.fingerprint does not name device frankel', failures)
            self.assertIn('PRODUCT/etc/build.prop: ro.product.build.fingerprint does not name device frankel', failures)
            self.assertIn(f'META/misc_info.txt fingerprint {blazer}{tail} does not name device frankel', failures)
            self.assertIn('no partition fingerprint names device frankel', failures)
            # A system partition naming another device is not the generic image: refused.
            failures, tail = self.target(tmp, blazer, frankel)
            self.assertIn('SYSTEM/build.prop: ro.system.build.fingerprint does not name device frankel', failures)
            self.assertIn(f'META/misc_info.txt fingerprint {blazer}{tail} does not name device frankel', failures)
            # The generic identity is only accepted on the system partition, and an artifact naming no device fails.
            failures, _ = self.target(tmp, generic, generic)
            self.assertIn('VENDOR/build.prop: ro.vendor.build.fingerprint does not name device frankel', failures)
            self.assertIn('no partition fingerprint names device frankel', failures)
            # A look-alike prefix is not the generic system image.
            failures, _ = self.target(tmp, 'Android/generic_system_x/generic', frankel)
            self.assertIn('SYSTEM/build.prop: ro.system.build.fingerprint does not name device frankel', failures)

    def test_ota_metadata_names_the_dispatched_device(self):
        cand = pipeline.candidate(self.args(), 'frankel')
        fp = f'google/blazer/blazer:17/CP3A/{cand["build"]}:user/release-keys'
        with tempfile.TemporaryDirectory() as tmp:
            ota, public = Path(tmp)/'ota.zip', Path(tmp)/'public'
            public.mkdir()
            (public/'releasekey.x509.pem').write_text('-----BEGIN CERTIFICATE-----\nAAAA\n-----END CERTIFICATE-----\n')
            (public/'avb_pkmd.bin').write_bytes(b'key')
            with zipfile.ZipFile(ota, 'w') as z:
                z.writestr('META-INF/com/android/metadata', f'pre-device=blazer\npost-timestamp={cand["timestamp"]}\npost-build={fp}\n')
            failures = pipeline.check_ota(dict(FRANKEL, host=dict(FRANKEL['host'], sourceRoot=tmp)), cand, ota, public, {})
            self.assertIn('OTA pre-device is blazer', failures)

class AppCompatibilityTest(unittest.TestCase):
    def test_direct_app_compatibility_stays_frankel_only_and_within_delivered_builds(self):
        # The app receipt may only declare admitted devices, and its OS floor must be a delivered frankel build. Schema 2
        # (APP-IDSIG-1030-M005) floors at 2026103012, the first delivered build whose client reads it.
        compatibility = FRANKEL['appRelease']['compatibility']
        self.assertEqual((compatibility['schemaVersion'], compatibility['minOsBuild']), (2, '2026103012'))
        self.assertEqual(compatibility['deviceCodenames'], ['frankel'])
        self.assertTrue(set(compatibility['deviceCodenames']) <= set(pipeline.SUPPORTED_DEVICES))
        delivered = {r['build'] for r in FRANKEL['versionBaselines']['retained']}
        self.assertIn(compatibility['minOsBuild'], delivered)
        self.assertLessEqual(int(compatibility['minOsBuild']), max(int(b) for b in delivered))

    def test_the_delivered_308_record_binds_its_bytes_and_keeps_every_floor_above_it(self):
        app = FRANKEL['appRelease']
        row = {r['versionCode']: r for r in app['reserved']}[308]
        self.assertEqual((row['route'], row['sha256'], row['osRun'], row['run'], row['release']),
            ('os-release', 'f4163ca86cd836861206139be74cc2cbe6a82f644bc8afd5533ab1dc6c6e7a41', 40778491188, 40787416518, 427306781))
        # An OS candidate must exceed 308 (retained 2806); the app-only route must exceed the 308 row.
        retained = {r['build']: r for r in FRANKEL['versionBaselines']['retained']}['2026102806']
        self.assertEqual(retained['mustExceed'], {'dev.pixel.voice': 308})
        release = load('host_app_release_device', 'host-app-release.py')
        with self.assertRaisesRegex(release.AdapterFailed, 'reserved for the os-release route'):
            release.requested_version(FRANKEL, [['dev.pixel.voice', 308]], 'a' * 40)
        self.assertEqual(release.requested_version(FRANKEL, [['dev.pixel.voice', 309]], 'a' * 40), 309)

class LatestTest(unittest.TestCase):
    """publish() with a second device admitted only inside this test: its config asks for Latest; it is refused."""
    def setUp(self):
        self.saved = pipeline.SUPPORTED_DEVICES
        pipeline.SUPPORTED_DEVICES = ('frankel', 'blazer')
        self.addCleanup(setattr, pipeline, 'SUPPORTED_DEVICES', self.saved)
        self.tmp = Path(tempfile.mkdtemp())

    def publish(self, device, promote, tag=None):
        config = json.loads(json.dumps(FRANKEL))
        config['target']['device'] = device
        config['publication']['tag'] = tag or f'{device}-{{build}}'
        config['publication']['channels']['beta'] = {'promoteLatest': True, 'feedChannel': 'stable'}
        cand = {'sha': 'a' * 40, 'build': '2026102901', 'timestamp': '1793300000', 'channel': 'beta', 'device': device}
        artifact = self.tmp/f'artifact-{device}-{promote}-{tag}'
        artifact.mkdir()
        (artifact/'manifest.json').write_text('{}')
        (artifact/'pipeline-receipt.json').write_text(json.dumps({'candidate': cand, 'simulated': True,
            'manifestSha256': hashlib.sha256(b'{}').hexdigest()}))
        a = types.SimpleNamespace(artifact_dir=str(artifact), promote=promote)
        adapter = types.SimpleNamespace(simulated=True, publish=lambda *x: self.fail('nothing may be published'),
            promote=lambda *x: self.fail('Latest may not move'))
        return pipeline.publish(a, config, cand, adapter)

    def test_a_non_frankel_device_can_never_move_latest_even_if_its_config_asks(self):
        with self.assertRaisesRegex(pipeline.CheckFailed, 'blazer may never become the repository Latest; only frankel is discovered there'):
            self.publish('blazer', True)
        with self.assertRaisesRegex(pipeline.CheckFailed, 'the release tag and candidate must name the dispatched device blazer'):
            self.publish('blazer', False, tag='frankel-{build}')
        # frankel's own promotion path is reached (here it stops at the synthetic manifest, before any publish or promote).
        with self.assertRaises(pipeline.CheckFailed) as caught:
            self.publish('frankel', True)
        self.assertNotIn('Latest', str(caught.exception))

if __name__ == '__main__':
    unittest.main()
