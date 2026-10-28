"""os-release app-only mode (host-app-release.py and the app-builder job) with fake gcloud, SDK and build: Rose Agent
alone, signed with the four app secrets in RAM, independently verified, and only the APK, a nonsecret receipt and
SHA256SUMS in the artifact; never a reconcile, make, OS signing secret, controller or Calculator. The OS jobs of the
workflow are byte-for-byte the ones at ae9cfd4 apart from their mode condition."""
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import textwrap
import unittest

SCRIPTS = Path(__file__).resolve().parents[1]
ROOT = SCRIPTS.parent
PY = sys.executable
PIN = 'ab' * 32
OTHER = 'cd' * 32
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location('host_app_release', SCRIPTS/'host-app-release.py')
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)
WORKFLOW = (ROOT/'.github/workflows/os-release.yml').read_text()
# sha256 of the builder and publisher job blocks at ae9cfd4 (2804 candidate), without the added mode condition.
OS_JOBS_AT_BASE = {'builder': '7dc3e244a826bb00bd8d112376c49721d3e18854607b7762b6b42a9a71d730d2',
    'publisher': '9a3950690bead3cdd7ce754cc9d79a0feb7ffbd14979a78a540ef84b38e94513'}

def script(path, body):
    path.write_text('#!/usr/bin/env python3\n' + textwrap.dedent(body))
    path.chmod(0o755)
    return str(path)

def jobs(text):
    body = text.split('\njobs:\n', 1)[1]
    return {m[1]: m[2] for m in re.finditer(r'^  ([\w-]+):\n((?:(?:    .*)?\n)*)', body, re.M)}

class AppReleaseTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        tools, self.secrets = self.root/'tools', self.root/'secrets'
        tools.mkdir()
        self.secrets.mkdir()
        for name, data in (('KEYSTORE', b'synthetic keystore'), ('CONFIG', b'{"keyAlias": "rose"}'),
                           ('STORE', b'synthetic store pass'), ('KEY', b'synthetic key pass'),
                           ('OS_KEYS', b'synthetic os keys'), ('OS_PASS', b'synthetic os pass')):
            (self.secrets/name).write_bytes(data)
        self.log = self.root/'gcloud.log'
        self.gcloud = script(tools/'gcloud', f'''
            import json, os, shutil, sys
            a = sys.argv[1:]; open({str(self.log)!r}, 'a').write(json.dumps(a) + '\\n')
            name, out = a[a.index('--secret') + 1], a[a.index('--out-file') + 1]
            src = os.path.join({str(self.secrets)!r}, name)
            if a[:3] != ['secrets', 'versions', 'access'] or a[3] != '1' or not os.path.exists(src): sys.exit(1)
            shutil.copy(src, out)''')
        sdk = self.root/'sdk/build-tools/android-15'
        sdk.mkdir(parents=True)
        # The signer's own apksigner (used by sign-apk.py) and the independent one (host.tools.apksigner).
        self.signer_cert = self.root/'signer-cert'
        self.signer_cert.write_text(PIN)
        script(sdk/'apksigner', f'''
            import shutil, sys; a = sys.argv[1:]
            if a[0] == 'sign': shutil.copy(a[-1], a[a.index('--out') + 1])
            else: print('Signer #1 certificate SHA-256 digest: ' + open({str(self.signer_cert)!r}).read())''')
        script(sdk/'zipalign', 'pass')
        self.verify_cert = self.root/'verify-cert'
        self.verify_cert.write_text(PIN)
        apksigner = script(tools/'apksigner', f'''
            print('Signer #1 certificate SHA-256 digest: ' + open({str(self.verify_cert)!r}).read())''')
        aapt = script(tools/'aapt', '''
            import sys; pkg, ver = open(sys.argv[-1]).read().split(':')
            print("package: name='%s' versionCode='%s' versionName='%s-app-beta'" % (pkg, ver.strip(), ver.strip()))''')
        # OS entry points that app-only must never reach: each would leave a marker.
        self.markers = self.root/'markers'
        self.markers.mkdir()
        never = {k: [PY, script(tools/f'{k}.py', f'open({str(self.markers/k)!r}, "w").write("ran")')]
            for k in ('integrate', 'make', 'sign', 'reconcile', 'appBuild')}
        self.lock = self.root/'frankel-build.lock'
        self.config_data = {'identities': {'appSignerSha256': PIN}, 'host': {'sourceRoot': str(self.root/'src'),
            'lockPath': str(self.lock), 'tools': {'aapt': aapt, 'apksigner': apksigner},
            'integrateCommand': never['integrate'], 'makeCommand': never['make'], 'signCommand': never['sign'],
            'sign': {'project': 'p', 'keysSecret': 'OS_KEYS', 'passphraseSecret': 'OS_PASS', 'secretVersion': '1'},
            'integrate': {'project': 'p', 'secretVersion': '1', 'sdk': 37, 'reconcileCommand': never['reconcile'],
                'appBuildCommand': never['appBuild'],
                'appSecrets': {'keystore': 'KEYSTORE', 'config': 'CONFIG', 'storePassword': 'STORE', 'keyPassword': 'KEY'},
                'appSdkRoot': str(self.root/'sdk'), 'javaHome': str(self.root/'jdk'), 'publicAssetsDir': str(self.root/'assets'),
                'publicAssetDigests': None}},
            'appRelease': {'packageName': 'dev.pixel.voice', 'mustExceed': 302,
                'minimumTestedOs': {'build': '2026102804', 'controller': 28}}}
        self.config = self.root/'config.json'
        self.config.write_text(json.dumps(self.config_data))
        self.built = 'dev.pixel.voice:304'
        self.calls = []
        self.head = subprocess.run(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'], capture_output=True, text=True).stdout.strip()
        self.work, self.artifact = self.root/'attempt/work', self.root/'attempt/artifact'
        self.env = dict(os.environ, ROSE_CANDIDATE_SHA=self.head, ROSE_RELEASE_DIR=str(self.work),
            APPS=json.dumps([['dev.pixel.voice', 304]]))

    def build(self, candidate, release_dir, sdk, java_home, assets, pinned, voice_only=False):
        self.calls.append({'candidate': Path(candidate), 'voice_only': voice_only, 'sdk': sdk})
        (Path(release_dir)/'unsigned').mkdir(parents=True)
        (Path(release_dir)/'unsigned/voice.apk').write_text(self.built)
        return {}

    def run_release(self):
        return release.main(self.artifact, self.config, self.env, self.gcloud, build=self.build)

    def fetched(self):
        return [json.loads(l) for l in self.log.read_text().splitlines()] if self.log.exists() else []

    def assert_clean(self):
        self.assertFalse(self.work.exists(), 'temporary inputs removed')
        for call in self.fetched():
            self.assertFalse(Path(call[call.index('--out-file') + 1]).exists(), 'RAM secret copy removed')
        worktrees = subprocess.run(['git', '-C', str(ROOT), 'worktree', 'list'], capture_output=True, text=True).stdout
        self.assertNotIn(str(self.work), worktrees)
        self.assertEqual(sorted(p.name for p in self.markers.iterdir()), [], 'no OS entry point ran')
        self.assertTrue({c[c.index('--secret') + 1] for c in self.fetched()} <= {'KEYSTORE', 'CONFIG', 'STORE', 'KEY'},
            'only the four app secrets were read')

    def test_only_the_signed_apk_receipt_and_checksums_leave_the_builder(self):
        receipt = self.run_release()
        self.assertEqual(sorted(p.name for p in self.artifact.iterdir()), ['SHA256SUMS', 'app-release-receipt.json', 'dev.pixel.voice-304.apk'])
        apk = self.artifact/'dev.pixel.voice-304.apk'
        self.assertEqual(receipt, json.loads((self.artifact/'app-release-receipt.json').read_text()))
        self.assertEqual({k: receipt[k] for k in ('receiptType', 'simulated', 'candidate', 'packageName', 'versionCode', 'versionName',
            'apk', 'size', 'sha256', 'signerSha256', 'minimumTestedOs')},
            {'receiptType': 'app-release', 'simulated': False, 'candidate': self.head, 'packageName': 'dev.pixel.voice',
             'versionCode': 304, 'versionName': '304-app-beta', 'apk': apk.name, 'size': apk.stat().st_size,
             'sha256': hashlib.sha256(apk.read_bytes()).hexdigest(), 'signerSha256': PIN,
             'minimumTestedOs': {'build': '2026102804', 'controller': 28}})
        self.assertIn('independent apksigner verification: exactly the pinned signer', receipt['checks'])
        sums = dict(reversed(l.split('  ')) for l in (self.artifact/'SHA256SUMS').read_text().splitlines())
        self.assertEqual(sorted(sums), ['app-release-receipt.json', apk.name])
        for name, digest in sums.items():
            self.assertEqual(hashlib.sha256((self.artifact/name).read_bytes()).hexdigest(), digest)
        artifact_bytes = b''.join(p.read_bytes() for p in self.artifact.iterdir())
        for secret in self.secrets.iterdir():
            self.assertNotIn(secret.read_bytes(), artifact_bytes)
        self.assertEqual([(c['voice_only'], c['candidate']) for c in self.calls], [(True, self.work/'candidate')])
        self.assertEqual(len(self.fetched()), 4)
        self.assert_clean()

    def test_wrong_signer_fails_at_signing_and_at_independent_verification(self):
        self.signer_cert.write_text(OTHER)
        with self.assertRaisesRegex(release.AdapterFailed, 'sign-apk.py exited 1'):
            self.run_release()
        self.assertFalse(self.artifact.exists())
        self.assert_clean()
        self.signer_cert.write_text(PIN)
        self.verify_cert.write_text(OTHER)
        with self.assertRaisesRegex(release.AdapterFailed, 'not signed by exactly the pinned app signer'):
            self.run_release()
        self.assertFalse(self.artifact.exists(), 'no partial artifact to upload')
        self.assert_clean()

    def test_wrong_package_or_built_version_stops_before_any_secret_is_read(self):
        for built, message in (('dev.pixel.other:304', "dev.pixel.other"), ('dev.pixel.voice:303', "303\\), expected dev.pixel.voice 304")):
            self.built = built
            with self.assertRaisesRegex(release.AdapterFailed, message):
                self.run_release()
            self.assertEqual(self.fetched(), [])
            self.assertFalse(self.artifact.exists())
            self.assert_clean()

    def test_dispatch_must_name_exactly_rose_agent_above_the_published_302(self):
        for apps, message in (([['dev.pixel.voice', 302]], 'must exceed 302, the newest built or published'),
                ([['dev.pixel.voice', 301]], 'must exceed 302, the newest built or published'),
                ([['dev.pixel.voice', 304], ['dev.pixel.backgroundcontrol', 28]], 'exactly'),
                ([['dev.pixel.calculator.agent', 5]], 'exactly'), ([['dev.pixel.voice', '304']], 'exactly'), ([], 'exactly')):
            self.env['APPS'] = json.dumps(apps)
            with self.assertRaisesRegex(release.AdapterFailed, message):
                self.run_release()
            self.assertEqual((self.calls, self.fetched()), ([], []))

    def test_held_builder_lock_and_reused_artifact_directory_fail_before_building(self):
        with open(self.lock, 'a') as held:
            fcntl.flock(held, fcntl.LOCK_EX)
            with self.assertRaisesRegex(release.AdapterFailed, 'lock is held'):
                self.run_release()
        self.artifact.mkdir(parents=True)
        with self.assertRaisesRegex(release.AdapterFailed, 'already exists'):
            self.run_release()
        self.assertTrue(self.artifact.exists(), 'an existing directory is never removed')
        self.assertEqual(self.calls, [])

    def test_missing_configuration_fails_closed_by_name(self):
        del self.config_data['appRelease']
        del self.config_data['host']['integrate']['appSecrets']['keyPassword']
        self.config.write_text(json.dumps(self.config_data))
        with self.assertRaises(release.AdapterSetupMissing) as error:
            self.run_release()
        self.assertEqual(error.exception.missing, ['host.integrate.appSecrets.keyPassword', 'appRelease.mustExceed',
            'appRelease.minimumTestedOs'])

    def test_source_never_names_an_os_stage_or_the_os_secrets(self):
        source = (SCRIPTS/'host-app-release.py').read_text().split('"""', 2)[2]  # code only, not the docstring
        for name in ('integrateCommand', 'makeCommand', 'signCommand', 'reconcile', 'keysSecret', 'passphraseSecret',
                     "['sign']", 'os-release-stage', 'calculator', 'controller', 'appBuildCommand'):
            self.assertNotIn(name, source)

class CheckedInAppReleaseTest(unittest.TestCase):
    def test_checked_in_config_has_every_app_only_input(self):
        self.assertEqual(subprocess.run([PY, str(SCRIPTS/'host-app-release.py'), '--check']).returncode, 0)

    def test_app_release_pins_the_published_302_the_delivered_303_and_the_reserved_304_and_305(self):
        config = json.loads((ROOT/'os/release-pipeline/frankel.json').read_text())
        app = config['appRelease']
        self.assertEqual(app['published'], {'versionCode': 302, 'size': 1381533,
            'sha256': '01b184b4a634f0a7843b6fb5ce30dff08f7267ec4e20b5013860b9439dd3a8c6',
            'signerSha256': config['identities']['appSignerSha256']})
        self.assertEqual((app['packageName'], app['mustExceed'], app['minimumTestedOs']),
            ('dev.pixel.voice', 303, {'build': '2026102804', 'controller': 28}))
        built, = app['built']
        self.assertEqual((built['versionCode'], built['sha256'], built['size'], built['run']),
            (303, 'cb11480c88419851ecafee24565d4e86e0e14d5416800e9573f6bc23bfd96f41', 1381533, 40676568143))
        self.assertEqual(app['mustExceed'], max(app['published']['versionCode'], built['versionCode']))
        # 303 reached phones through the authenticated catalog; only a public GitHub release is absent.
        self.assertEqual(built['delivered'], {'route': 'authenticated catalog', 'at': '2026-10-28T14:39Z', 'publicRelease': False})
        self.assertNotIn('never published', built['basis'])
        self.assertEqual([(r['versionCode'], r['versionName'], r['route'], r['candidate']) for r in app['reserved']],
            [(304, '304-app-beta', 'app-only', 'a3d839e6805b514aa6cad6f86c15e5db622e10cc'), (305, '305-home-beta', 'os-release', None)])
        manifest = (ROOT/'workspace/rose-agent/app/AndroidManifest.xml').read_text()
        self.assertIn('package="dev.pixel.voice" android:versionCode="305" android:versionName="305-home-beta"', manifest)

    def test_reserved_versions_belong_to_their_route_and_candidate(self):
        config = json.loads((ROOT/'os/release-pipeline/frankel.json').read_text())
        queued = 'a3d839e6805b514aa6cad6f86c15e5db622e10cc'
        self.assertEqual(release.requested_version(config, [['dev.pixel.voice', 304]], queued), 304)
        self.assertEqual(release.requested_version(config, [['dev.pixel.voice', 306]], 'f' * 40), 306)
        for version, sha, message in ((304, 'f' * 40, 'reserved for the app-only route and candidate ' + queued),
                (305, queued, 'reserved for the os-release route'), (303, queued, 'must exceed 303, the newest built or published')):
            with self.assertRaisesRegex(release.AdapterFailed, message):
                release.requested_version(config, [['dev.pixel.voice', version]], sha)
        gap = json.loads(json.dumps(config))  # an unreserved version below a reservation is refused too
        gap['appRelease']['reserved'] = [dict(gap['appRelease']['reserved'][0], versionCode=306)]
        with self.assertRaisesRegex(release.AdapterFailed, 'must exceed the reserved 306'):
            release.requested_version(gap, [['dev.pixel.voice', 305]], queued)

class WorkflowAppOnlyTest(unittest.TestCase):
    def setUp(self):
        self.jobs = jobs(WORKFLOW)

    def test_os_jobs_are_unchanged_apart_from_their_mode_condition(self):
        self.assertIn("    if: inputs.mode == 'os-release'\n", self.jobs['builder'])
        for name, digest in OS_JOBS_AT_BASE.items():
            block = self.jobs[name].replace("    if: inputs.mode == 'os-release'\n", '')
            self.assertEqual(hashlib.sha256(block.encode()).hexdigest(), digest, name)
        self.assertIn('    needs: builder\n', self.jobs['publisher'])  # skipped whenever builder is skipped
        self.assertIn('    needs: [controller, builder, app-builder, alpha-builder, publisher]\n', self.jobs['cleanup'])
        self.assertIn("    if: always() && needs.controller.result != 'skipped'\n", self.jobs['cleanup'])
        self.assertRegex(WORKFLOW, r"      mode:\n.*\n        type: choice\n        options: \[os-release, app-only, emulator-alpha\]\n        default: os-release\n")

    def test_app_builder_runs_only_the_app_release_and_uploads_only_its_artifact(self):
        app = self.jobs['app-builder']
        self.assertIn("    needs: controller\n    if: inputs.mode == 'app-only'\n    runs-on: [self-hosted, roseos-frankel-builder]\n", app)
        runs = re.findall(r'^      (?:- |  )run: (.+)$', app, re.M)
        self.assertEqual(runs, ['python3 scripts/release-pipeline.py preflight --job builder', 'python3 scripts/host-app-release.py --check',
            '|', 'python3 scripts/actions-gcp-login.py login "$GCP_SIGNER_SERVICE_ACCOUNT"',
            'ROSE_CANDIDATE_SHA="$CANDIDATE_SHA" ROSE_RELEASE_DIR="$ROSE_BASE/work" python3 scripts/host-app-release.py --artifact-dir "$ROSE_BASE/artifact"',
            'python3 scripts/actions-gcp-login.py logout', 'test -z "${ROSE_BASE:-}" || rm -rf "$ROSE_BASE"'])
        for absent in ('os-release-stage', 'secrets.', 'PIXEL_OTA', 'GCP_CONTROLLER', 'release-pipeline.py build', 'release-pipeline.py sign'):
            self.assertNotIn(absent, app)
        self.assertIn('          name: app-release-dev.pixel.voice-${{ github.run_id }}\n          path: ${{ env.ROSE_BASE }}/artifact\n', app)
        # Signer access is dropped and the attempt removed on success or failure.
        self.assertIn('      - name: Drop signer access\n        if: always()\n', app)
        self.assertIn('      - name: Remove the app attempt from the builder\n        if: always()\n', app)

    def test_guard_limits_app_only_to_rose_agent_without_resume_or_promotion(self):
        guard = self.jobs['guard']
        self.assertIn('PROMOTE: ${{ inputs.promote }}', guard)
        self.assertIn('[[ "$MODE" == os-release || ( "$RESUME_STAGE" == none && -z "$RESUME_SOURCE_RUN" && "$PROMOTE" == false ) ]]', guard)
        check = re.search(r"\[\[ \"\$MODE\" != app-only \]\] \|\| python3 -c '(.+)'", guard)[1]
        for apps, ok in (([['dev.pixel.voice', 304]], True), ([['dev.pixel.voice', 304], ['dev.pixel.backgroundcontrol', 28]], False),
                         ([['dev.pixel.calculator.agent', 5]], False)):
            result = subprocess.run([PY, '-c', check], env=dict(os.environ, APPS=json.dumps(apps)), capture_output=True)
            self.assertEqual(result.returncode == 0, ok, apps)

if __name__ == '__main__':
    unittest.main()
