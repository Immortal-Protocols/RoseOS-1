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
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
import zipfile

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
# The builder and publisher jobs as of FAM-02-INFRA-S001, whose only change is the admitted device in the attempt
# directory, stage receipt, artifact name and publish call (exact lines pinned in test_device_contract.py).
# OCT30-INFRA-S014 moved both jobs once: the builder allocates the latest-beta candidate and exports its identity, and
# the publisher takes that identity, re-derives the allocation and promotes only latest-beta or an explicit --promote.
# + RELEASE020 M2/M2c: opt-in packaging and the default-off dedicated-signer route (publisher also needs signed-builder)
OS_JOBS_AT_BASE = {'builder': 'ca7c630e8d3c9c82a408fafeecbd6165f594c79b0e6cba150ba3b4340dc0cff4',
    'publisher': '2b8ce2b79cbb9ffe29e05762f46dc3d1f17b385479d06998f538b7926afc61fe'}

def script(path, body):
    path.write_text('#!/usr/bin/env python3\n' + textwrap.dedent(body))
    path.chmod(0o755)
    return str(path)

class Fs:
    """A synthetic os.statvfs result: bytes available to this user (f_bavail), free to root (f_bfree) and total."""
    def __init__(self, available, free=None, total=None, frsize=4096):
        self.f_frsize, self.f_bavail = frsize, available // frsize if frsize > 0 else 0
        self.f_bfree = (free if free is not None else available) // frsize if frsize > 0 else 0
        self.f_blocks = (total if total is not None else 2 * (free or available)) // frsize if frsize > 0 else 0

NEEDED = release.RESERVE_BYTES + release.APP_WORKSPACE_ALLOWANCE

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
        # Signing with --v4-signing-enabled also writes <out>.idsig; this stand-in binds it to the APK's bytes.
        self.idsig_override = self.root/'idsig-override'
        script(sdk/'apksigner', f'''
            import hashlib, os, shutil, sys; a = sys.argv[1:]
            if a[0] == 'sign':
                out = a[a.index('--out') + 1]; shutil.copy(a[-1], out)
                if a[a.index('--v4-signing-enabled') + 1] == 'true':
                    data = open({str(self.idsig_override)!r}, 'rb').read() if os.path.exists({str(self.idsig_override)!r}) \
                        else b'v4:' + hashlib.sha256(open(out, 'rb').read()).hexdigest().encode()
                    if data != b'none': open(out + '.idsig', 'wb').write(data)
            else: print('Signer #1 certificate SHA-256 digest: ' + open({str(self.signer_cert)!r}).read())''')
        script(sdk/'zipalign', 'pass')
        self.verify_cert = self.root/'verify-cert'
        self.verify_cert.write_text(PIN)
        apksigner = script(tools/'apksigner', f'''
            import hashlib, sys; a = sys.argv[1:]
            if '--v4-signature-file' in a:  # v4 pairing: the sidecar must be this APK's
                idsig = open(a[a.index('--v4-signature-file') + 1], 'rb').read()
                sys.exit(0 if idsig == b'v4:' + hashlib.sha256(open(a[-1], 'rb').read()).hexdigest().encode() else 1)
            print('Signer #1 certificate SHA-256 digest: ' + open({str(self.verify_cert)!r}).read())''')
        # aapt dump badging over a stand-in APK (a ZIP whose 'identity' entry names package:version): the identity line,
        # sdkVersion (from self.sdk_file) and native-code for its lib/<abi>/ entries (or self.native_file when written).
        self.sdk_file, self.native_file = self.root/'aapt-sdk', self.root/'aapt-native'
        self.sdk_file.write_text('29')
        aapt = script(tools/'aapt', f'''
            import os, re, sys, zipfile; z = zipfile.ZipFile(sys.argv[-1]); pkg, ver = z.read('identity').decode().split(':')
            print("package: name='%s' versionCode='%s' versionName='%s-app-beta'" % (pkg, ver.strip(), ver.strip()))
            print("sdkVersion:'%s'" % open({str(self.sdk_file)!r}).read())
            abis = sorted({{n.split('/')[1] for n in z.namelist() if re.fullmatch(r'lib/[^/]+/.+\\.so', n)}})
            if os.path.exists({str(self.native_file)!r}): abis = open({str(self.native_file)!r}).read().split()
            if abis: print('native-code: ' + ' '.join("'%s'" % a for a in abis))''')
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
        self.built, self.libs = 'dev.pixel.voice:306', ()
        self.fs, self.statted = Fs(NEEDED + 4096), []
        # The trusted checkout and the attempt directory sit on two separate synthetic filesystems.
        self.device = lambda path: 2 if Path(path).is_relative_to(self.root) else 1
        self.calls = []
        self.head = subprocess.run(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'], capture_output=True, text=True).stdout.strip()
        self.work, self.artifact = self.root/'attempt/work', self.root/'attempt/artifact'
        self.env = dict(os.environ, ROSE_CANDIDATE_SHA=self.head, ROSE_RELEASE_DIR=str(self.work),
            APPS=json.dumps([['dev.pixel.voice', 306]]))

    def build(self, candidate, release_dir, sdk, java_home, assets, pinned, voice_only=False):
        self.calls.append({'candidate': Path(candidate), 'voice_only': voice_only, 'sdk': sdk})
        (Path(release_dir)/'unsigned').mkdir(parents=True)
        with zipfile.ZipFile(Path(release_dir)/'unsigned/voice.apk', 'w') as apk:
            apk.writestr('identity', self.built)
            for lib in self.libs:
                apk.writestr(lib, b'synthetic native library')
        return {}

    def statvfs(self, path):
        self.statted.append(Path(path))
        fs = self.fs(path) if callable(self.fs) else self.fs
        if isinstance(fs, BaseException):
            raise fs
        return fs

    def run_release(self):
        return release.main(self.artifact, self.config, self.env, self.gcloud, build=self.build, statvfs=self.statvfs,
            stat=lambda path: type('Stat', (), {'st_dev': self.device(path)})())

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
        self.assertEqual(sorted(p.name for p in self.artifact.iterdir()), ['SHA256SUMS', 'app-release-receipt.json',
            'dev.pixel.voice-306.apk', 'dev.pixel.voice-306.apk.idsig'])
        idsig = self.artifact/'dev.pixel.voice-306.apk.idsig'
        self.assertEqual(receipt['v4Signature'], {'asset': idsig.name, 'size': idsig.stat().st_size,
            'sha256': hashlib.sha256(idsig.read_bytes()).hexdigest()})
        self.assertIn('v4 signature (.idsig) verified by the independent apksigner for this APK', receipt['checks'])
        apk = self.artifact/'dev.pixel.voice-306.apk'
        self.assertEqual(receipt, json.loads((self.artifact/'app-release-receipt.json').read_text()))
        self.assertEqual({k: receipt[k] for k in ('receiptType', 'simulated', 'candidate', 'packageName', 'versionCode', 'versionName',
            'apk', 'size', 'sha256', 'signerSha256', 'minimumTestedOs', 'apkFacts')},
            {'receiptType': 'app-release', 'simulated': False, 'candidate': self.head, 'packageName': 'dev.pixel.voice',
             'versionCode': 306, 'versionName': '306-app-beta', 'apk': apk.name, 'size': apk.stat().st_size,
             'sha256': hashlib.sha256(apk.read_bytes()).hexdigest(), 'signerSha256': PIN,
             'minimumTestedOs': {'build': '2026102804', 'controller': 28}, 'apkFacts': {'minSdk': 29, 'nativeAbis': []}})
        self.assertIn('independent apksigner verification: exactly the pinned signer', receipt['checks'])
        sums = dict(reversed(l.split('  ')) for l in (self.artifact/'SHA256SUMS').read_text().splitlines())
        self.assertEqual(sorted(sums), ['app-release-receipt.json', apk.name, idsig.name])
        for name, digest in sums.items():
            self.assertEqual(hashlib.sha256((self.artifact/name).read_bytes()).hexdigest(), digest)
        artifact_bytes = b''.join(p.read_bytes() for p in self.artifact.iterdir())
        for secret in self.secrets.iterdir():
            self.assertNotIn(secret.read_bytes(), artifact_bytes)
        self.assertEqual([(c['voice_only'], c['candidate']) for c in self.calls], [(True, self.work/'candidate')])
        self.assertEqual(len(self.fetched()), 4)
        self.assert_clean()

    def test_missing_empty_oversize_or_unpaired_v4_signature_fails_and_leaves_nothing(self):
        for data, error in ((b'none', 'missing'), (b'', 'outside 1..'), (b'x' * (release.V4_MAX_BYTES + 1), 'outside 1..'),
                            (b'v4:' + b'0' * 64, 'does not verify as the v4 signature')):
            self.idsig_override.write_bytes(data)
            with self.subTest(error=error), self.assertRaisesRegex(release.AdapterFailed, error):
                self.run_release()
            self.assertFalse(self.artifact.exists(), 'no partial artifact')
            self.assert_clean()

    def test_symlinked_v4_signature_fails(self):
        target = self.root/'elsewhere.idsig'
        target.write_text('x')
        (self.root/'link.idsig').symlink_to(target)
        with self.assertRaisesRegex(release.AdapterFailed, 'missing or not a regular file'):
            release.v4_signature('apksigner', self.root/'a.apk', self.root/'link.idsig')

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

    def test_apk_facts_come_from_the_signed_apk_and_the_source(self):
        self.libs = ('lib/arm64-v8a/libx.so', 'lib/x86_64/libx.so')
        self.assertEqual(self.run_release()['apkFacts'], {'minSdk': 29, 'nativeAbis': ['arm64-v8a', 'x86_64']})
        self.assert_clean()
        for setup, message in ((lambda: self.sdk_file.write_text('30'), 'minSdk 30 does not match the source minSdkVersion 29'),
                (lambda: self.native_file.write_text('arm64-v8a'), "native libraries \\['arm64-v8a', 'x86_64'\\] do not match aapt native-code")):
            setup()
            shutil.rmtree(self.root/'attempt', ignore_errors=True)
            with self.assertRaisesRegex(release.AdapterFailed, message):
                self.run_release()
            self.assertFalse(self.artifact.exists(), 'no artifact without exact APK facts')
            self.assert_clean()
            self.sdk_file.write_text('29')
            self.native_file.unlink(missing_ok=True)

    def test_wrong_package_or_built_version_stops_before_any_secret_is_read(self):
        for built, message in (('dev.pixel.other:306', "dev.pixel.other"), ('dev.pixel.voice:303', "303\\), expected dev.pixel.voice 306")):
            self.built = built
            with self.assertRaisesRegex(release.AdapterFailed, message):
                self.run_release()
            self.assertEqual(self.fetched(), [])
            self.assertFalse(self.artifact.exists())
            self.assert_clean()

    def test_dispatch_must_name_exactly_rose_agent_above_the_published_302(self):
        for apps, message in (([['dev.pixel.voice', 302]], 'must exceed 302, the newest built or published'),
                ([['dev.pixel.voice', 301]], 'must exceed 302, the newest built or published'),
                ([['dev.pixel.voice', 306], ['dev.pixel.backgroundcontrol', 28]], 'exactly'),
                ([['dev.pixel.calculator.agent', 5]], 'exactly'), ([['dev.pixel.voice', '306']], 'exactly'), ([], 'exactly')):
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

    def test_disk_reserve_is_checked_on_available_bytes_before_any_work(self):
        # Exactly the reserve plus the app workspace allowance, available to this user, is enough.
        self.fs = Fs(NEEDED)
        self.assertEqual(self.run_release()['versionCode'], 306)
        self.assertEqual(self.statted, [ROOT, self.root], 'the trusted checkout, then the not-yet-created workspace and artifact '
            'on their nearest existing ancestor; their shared filesystem is measured once')
        self.assert_clean()
        shutil.rmtree(self.root/'attempt', ignore_errors=True)
        self.log.unlink()  # secret reads of the successful run above
        lacking = (
            (Fs(NEEDED - 4096), 'has %d bytes available; %d are required' % (NEEDED - 4096, NEEDED)),
            # Free-to-root and total space are not headroom: only f_bavail counts.
            (Fs(NEEDED - 4096, free=10 * NEEDED, total=100 * NEEDED), 'has %d bytes available' % (NEEDED - 4096)),
            (Fs(release.RESERVE_BYTES), 'reserve plus %d app workspace' % release.APP_WORKSPACE_ALLOWANCE),
            (Fs(0), 'has 0 bytes available'),
            (Fs(NEEDED, frsize=0), 'could not be established: no fragment size'),
            (FileNotFoundError(2, 'gone'), 'could not be established: FileNotFoundError'),
            (PermissionError(13, 'denied'), 'could not be established: PermissionError'),
        )
        for fs, message in lacking:
            self.fs, self.calls = fs, []
            with self.assertRaisesRegex(release.AdapterFailed, message):
                self.run_release()
            self.assertEqual((self.calls, self.fetched()), ([], []), 'nothing was built and no secret was read')
            self.assertFalse(self.artifact.exists())
            self.assertFalse((self.root/'attempt').exists(), 'no workspace was created')
            worktrees = subprocess.run(['git', '-C', str(ROOT), 'worktree', 'list'], capture_output=True, text=True).stdout
            self.assertNotIn(str(self.work), worktrees)
        # Any one short filesystem stops the run: here only the workspace's.
        self.root.joinpath('attempt').mkdir()
        self.fs = lambda path: Fs(NEEDED - 4096) if Path(path) == self.root/'attempt' else Fs(NEEDED)
        with self.assertRaisesRegex(release.AdapterFailed, 'filesystem has %d bytes available' % (NEEDED - 4096)):
            self.run_release()
        self.assertTrue((self.root/'attempt').exists(), 'an existing directory is left in place, never deleted or resized')
        self.assertEqual(self.calls, [])
        # The guard does not run before the lock: a held lock is still reported as such.
        self.fs = Fs(0)
        with open(self.lock, 'a') as held:
            fcntl.flock(held, fcntl.LOCK_EX)
            with self.assertRaisesRegex(release.AdapterFailed, 'lock is held'):
                self.run_release()

    def test_disk_reserve_is_the_packet_value_and_counts_each_filesystem_once(self):
        self.assertEqual(release.RESERVE_BYTES, 322122547200)
        self.assertEqual(release.RESERVE_BYTES, 300 * 1024**3)
        self.assertTrue(0 < release.APP_WORKSPACE_ALLOWANCE <= 64 * 1024**3, 'the app workspace allowance is bounded')
        seen = []
        same = lambda path: type('S', (), {'st_dev': 7})()
        release.disk_preflight({'a': self.root, 'b': self.root/'missing/child'}, statvfs=lambda p: seen.append(p) or Fs(NEEDED), stat=same)
        self.assertEqual(seen, [self.root], 'one filesystem, measured once')
        with self.assertRaisesRegex(release.AdapterFailed, 'available space for the b'):
            release.disk_preflight({'a': self.root, 'b': Path('/nonexistent-root-for-test')},
                statvfs=lambda p: Fs(NEEDED), stat=lambda p: (_ for _ in ()).throw(OSError('no mount')) if str(p) == '/' else type('S', (), {'st_dev': 7})())

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

    def test_app_release_pins_302_the_delivered_303_304_306_and_307_the_reserved_305_and_308_and_the_app_only_309(self):
        config = json.loads((ROOT/'os/release-pipeline/frankel.json').read_text())
        app = config['appRelease']
        self.assertEqual(app['published'], {'versionCode': 302, 'size': 1381533,
            'sha256': '01b184b4a634f0a7843b6fb5ce30dff08f7267ec4e20b5013860b9439dd3a8c6',
            'signerSha256': config['identities']['appSignerSha256']})
        self.assertEqual((app['packageName'], app['mustExceed'], app['minimumTestedOs']),
            ('dev.pixel.voice', 307, {'build': '2026102804', 'controller': 28}))
        built, public, direct, dock = app['built']
        self.assertEqual((built['versionCode'], built['sha256'], built['size'], built['run']),
            (303, 'cb11480c88419851ecafee24565d4e86e0e14d5416800e9573f6bc23bfd96f41', 1381533, 40676568143))
        self.assertEqual(app['mustExceed'], max(app['published']['versionCode'], *(b['versionCode'] for b in app['built'])))
        # 303 reached phones through the authenticated catalog; only a public GitHub release is absent.
        self.assertEqual(built['delivered'], {'route': 'authenticated catalog', 'at': '2026-10-28T14:39Z', 'publicRelease': False})
        self.assertNotIn('never published', built['basis'])
        # 304 is public on GitHub and catalog-delivered, read back anonymously: APK d8f69efc... with no native libraries.
        self.assertEqual((public['versionCode'], public['sha256'], public['size'], public['candidate'], public['delivered']),
            (304, 'd8f69efcdcbc209ec5fd16badcaaf4c98fa614d7eb5fc54c1431a6f098d26966', 1385629, 'a3d839e6805b514aa6cad6f86c15e5db622e10cc',
             {'route': 'public GitHub prerelease rose-agent-304 and authenticated catalog', 'at': '2026-10-28T17:27Z', 'publicRelease': True}))
        # 306 is the first direct-GitHub client, public as rose-agent-306 (release 427075167, run 40750475915): delivered and immutable.
        self.assertEqual((direct['versionCode'], direct['versionName'], direct['sha256'], direct['size'], direct['signerSha256'], direct['candidate'], direct['delivered']),
            (306, '306-direct-github-beta', '23874658c1590472a1b687b198cb5d81f76134ee11b1a46bd5ba5611cfb3a2eb', 1393821,
             config['identities']['appSignerSha256'], 'c095566b159b61417a7e9906408a50a518e69924',
             {'route': 'public GitHub prerelease rose-agent-306', 'at': '2026-10-28T19:04Z', 'publicRelease': True}))
        # 307 (dock-label fix) is public as rose-agent-307 (release 427169403, run 40765131690): delivered and immutable.
        self.assertEqual((dock['versionCode'], dock['versionName'], dock['sha256'], dock['size'], dock['signerSha256'], dock['candidate'],
            dock['run'], dock['release'], dock['delivered']),
            (307, '307-dock-label-beta', '8fc7e46e32408a57d3f1f616d5c81a4b88aaa96b2f703b731bc79014bdfc4f60', 1393821,
             config['identities']['appSignerSha256'], 'd27dded6acac8567ebe0084d928145521da4d3bf', 40765131690, 427169403,
             {'route': 'public GitHub prerelease rose-agent-307', 'at': '2026-10-28T20:40Z', 'publicRelease': True}))
        self.assertEqual([(r['versionCode'], r['versionName'], r['route'], r['candidate']) for r in app['reserved']],
            [(305, '305-home-beta', 'os-release', '7fb94ae6ed535066d50879a99b7c8e427da8e853'), (308, '308-home-tray-beta', 'os-release', '93ad1c2bd7094d6b65c695ba239d05d272b87c57'),
             (309, '309-hifi-beta', 'app-only', None), (310, '310-hifi-os-beta', 'os-release', '02b21bc69f9afd281422d42e3dcf8a456e471de1'),
             (311, '311-hifi-visual-beta', 'app-only', 'd4bb0fd23741ef472689e3878e6d3aa4f6331d23')])
        self.assertEqual({k: v for k, v in app['compatibility'].items() if k != 'basis'},
            {'schemaVersion': 2, 'deviceCodenames': ['frankel'], 'minOsBuild': '2026103012', 'minControllerVersion': 28})
        manifest = (ROOT/'workspace/rose-agent/app/AndroidManifest.xml').read_text()
        # FRANKEL310-ALLOC-001 reserved 310 for the os-release route; 309 stays app-only and 308 the app-reuse binding.
        # AGENT311-APP-ONLY: the source declares 311 for the owner-requested app-only build, above every reservation.
        self.assertIn('package="dev.pixel.voice" android:versionCode="311" android:versionName="311-hifi-visual-beta"', manifest)

    def test_reserved_versions_belong_to_their_route_and_candidate(self):
        config = json.loads((ROOT/'os/release-pipeline/frankel.json').read_text())
        queued = 'a3d839e6805b514aa6cad6f86c15e5db622e10cc'
        # 308 is the 2806 OS candidate's; 309 is reserved for the app-only route, with its candidate recorded at dispatch.
        self.assertEqual(release.requested_version(config, [['dev.pixel.voice', 309]], 'f' * 40), 309)
        appres = json.loads(json.dumps(config))
        next(r for r in appres['appRelease']['reserved'] if r['versionCode'] == 309)['candidate'] = queued
        self.assertEqual(release.requested_version(appres, [['dev.pixel.voice', 309]], queued), 309)
        for cfg, version, sha, message in ((appres, 309, 'f' * 40, 'reserved for the app-only route and candidate ' + queued),
                (config, 308, queued, 'reserved for the os-release route'),
                (config, 310, queued, 'reserved for the os-release route'),
                (config, 310, 'f' * 40, 'reserved for the os-release route'),
                (config, 305, queued, 'must exceed 307, the newest built or published'),
                (config, 307, queued, 'must exceed 307, the newest built or published')):
            with self.assertRaisesRegex(release.AdapterFailed, message):
                release.requested_version(cfg, [['dev.pixel.voice', version]], sha)
        gap = json.loads(json.dumps(config))  # an unreserved version below a reservation is refused too
        gap['appRelease']['reserved'] = [dict(gap['appRelease']['reserved'][1], versionCode=310, route='app-only')]
        with self.assertRaisesRegex(release.AdapterFailed, 'must exceed the reserved 310'):
            release.requested_version(gap, [['dev.pixel.voice', 309]], queued)

class WorkflowAppOnlyTest(unittest.TestCase):
    def setUp(self):
        self.jobs = jobs(WORKFLOW)

    def test_os_jobs_are_unchanged_apart_from_their_mode_condition(self):
        self.assertIn("    if: inputs.mode == 'os-release' || inputs.mode == 'latest-beta'\n", self.jobs['builder'])
        for name, digest in OS_JOBS_AT_BASE.items():
            block = self.jobs[name].replace("    if: inputs.mode == 'os-release' || inputs.mode == 'latest-beta'\n", '')
            self.assertEqual(hashlib.sha256(block.encode()).hexdigest(), digest, name)
        self.assertIn('    needs: [builder, signed-builder]\n', self.jobs['publisher'])
        self.assertIn("    if: always() && needs.builder.result == 'success' && ", self.jobs['publisher'])  # skipped whenever builder is
        self.assertIn('    needs: [controller, device-readiness, builder, signing-bootstrap, models-signer, rango-builder, rango-publisher, app-builder, alpha-builder, alpha-successor, alpha-apk-diagnostic, alpha-verify-retained, alpha-export, app-reuse, request-signing, signed-builder, publisher]\n', self.jobs['cleanup'])
        self.assertIn("    if: always() && needs.controller.result != 'skipped'\n", self.jobs['cleanup'])
        self.assertRegex(WORKFLOW, r"      mode:\n.*\n        type: choice\n        options: \[latest-beta, os-release, device-readiness, app-only, emulator-alpha, signing-bootstrap, models-sign, rango-retained, emulator-alpha-successor, emulator-alpha-apk-diagnostic, emulator-alpha-verify-retained, app-reuse, emulator-alpha-export\]\n        default: latest-beta\n")

    def test_app_builder_runs_only_the_app_release_and_uploads_only_its_artifact(self):
        app = self.jobs['app-builder']
        self.assertIn("    needs: controller\n    if: inputs.mode == 'app-only'\n    runs-on: [self-hosted, roseos-frankel-builder]\n", app)
        runs = re.findall(r'^      (?:- |  )run: (.+)$', app, re.M)
        self.assertEqual(runs, ['python3 scripts/release-pipeline.py preflight --job builder', 'python3 scripts/host-app-release.py --check',
            '|', 'python3 scripts/release-allocate.py app --out "$ROSE_BASE/app-allocation.json"',
            'python3 scripts/actions-gcp-login.py login "$GCP_SIGNER_SERVICE_ACCOUNT"',
            'ROSE_CANDIDATE_SHA="$CANDIDATE_SHA" ROSE_RELEASE_DIR="$ROSE_BASE/work" python3 scripts/host-app-release.py --artifact-dir "$ROSE_BASE/artifact"',
            'python3 scripts/actions-gcp-login.py logout', 'test -z "${ROSE_BASE:-}" || rm -rf "$ROSE_BASE"'])
        for absent in ('os-release-stage', 'secrets.', 'PIXEL_OTA', 'GCP_CONTROLLER', 'release-pipeline.py build', 'release-pipeline.py sign'):
            self.assertNotIn(absent, app)
        self.assertIn('          name: app-release-dev.pixel.voice-${{ github.run_id }}\n          path: ${{ env.ROSE_BASE }}/artifact\n', app)
        # Signer access is dropped and the attempt removed on success or failure.
        self.assertIn('      - name: Drop signer access\n        if: always()\n', app)
        self.assertIn('      - name: Remove the app attempt from the builder\n        if: always()\n', app)
        # AGENT-BETA-AUTO: apps=auto allocates before any signer access; a skip (already published) builds and signs nothing.
        self.assertLess(app.index('release-allocate.py app'), app.index('actions-gcp-login.py login'))
        self.assertIn("      - name: Allocate Rose Agent (apps=auto)\n        id: allocate\n        if: env.APPS == 'auto'\n", app)
        for step in ('Signer access for app signing', 'Build, sign and verify Rose Agent', 'Private Actions artifact'):
            self.assertIn(f"      - name: {step}\n        if: env.APP_SKIP != 'true'\n", app)
        self.assertIn('      app_skip: ${{ steps.allocate.outputs.app_skip }}\n', app)

    def test_guard_limits_app_only_to_rose_agent_without_resume_or_promotion(self):
        guard = self.jobs['guard']
        # AGENT-BETA-AUTO: the literal apps=auto is admitted only for app-only (the builder allocates the version).
        self.assertIn('[[ "$MODE" == latest-beta || "$MODE" == rango-retained || ( "$MODE" == app-only && "$APPS" == auto ) ]] || python3', guard)
        publisher = self.jobs['app-publisher']
        self.assertIn('      CANDIDATE_SHA: ${{ needs.app-builder.outputs.candidate_sha || inputs.candidate_sha }}\n', publisher)
        self.assertIn('      APPS: ${{ needs.app-builder.outputs.apps || inputs.apps }}\n', publisher)
        self.assertIn('PROMOTE: ${{ inputs.promote }}', guard)
        self.assertIn('[[ "$MODE" == os-release || ( "$RESUME_STAGE" == none && -z "$RESUME_SOURCE_RUN" && "$PROMOTE" == false ) ]]', guard)
        check = re.search(r"\[\[ \"\$MODE\" != app-only \|\| \"\$APPS\" == auto \]\] \|\| python3 -c '(.+)'", guard)[1]
        for apps, ok in (([['dev.pixel.voice', 304]], True), ([['dev.pixel.voice', 304], ['dev.pixel.backgroundcontrol', 28]], False),
                         ([['dev.pixel.calculator.agent', 5]], False)):
            result = subprocess.run([PY, '-c', check], env=dict(os.environ, APPS=json.dumps(apps)), capture_output=True)
            self.assertEqual(result.returncode == 0, ok, apps)

if __name__ == '__main__':
    unittest.main()
