"""release-pipeline.py's supplemental SystemUI identity capture (OS-COMPONENT-ID-003). The aapt and apksigner here are
SYNTHETIC stand-ins that print fixed, invented output in the real line shapes; the APK is a synthetic zip. A pass shows
the wiring (tool provenance, per-tool APK hashes, artifact/source association, fail-closed statuses, unchanged tray
gate), not the identity of any shipped SystemUI and not an installation result."""
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import textwrap
import unittest
import zipfile

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location('release_pipeline_identity', SCRIPTS/'release-pipeline.py')
pipeline = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pipeline)
import systemui_identity  # noqa: E402

CERT = 'c3' * 32  # synthetic
REVISION = '0123456789abcdef0123456789abcdef01234567'
TRAY = json.loads((SCRIPTS.parent/'os/release-pipeline/frankel.json').read_text())['systemUiTray']

MANIFEST = '\n'.join([
    'N: android=http://schemas.android.com/apk/res/android',
    '  E: manifest (line=17)',
    '    A: android:versionCode(0x0101021b)=(type 0x10)0x25',
    '    A: android:versionName(0x0101021c)="17" (Raw: "17")',
    '    A: android:sharedUserId(0x0101000b)="android.uid.systemui" (Raw: "android.uid.systemui")',
    '    A: package="com.android.systemui" (Raw: "com.android.systemui")',
    '    E: application (line=300)',
    '      A: android:persistent(0x0101000d)=(type 0x12)0xffffffff']) + '\n'
SIGNERS = f'Signer #1 certificate DN: CN=Synthetic\nSigner #1 certificate SHA-256 digest: {CERT}\n'

# Each fake prints a file beside itself (or exits/mutates as FAKE_<TOOL>_MODE says); argv is recorded for inspection.
FAKE = textwrap.dedent('''\
    #!/usr/bin/env python3
    import os, pathlib, sys
    here = pathlib.Path(__file__)
    name = here.name
    with open(here.with_name(name + '.argv'), 'a') as log:
        log.write(' '.join(sys.argv[1:]) + '\\n')
    mode = os.environ.get('FAKE_' + name.upper() + '_MODE', 'ok')
    apk = pathlib.Path(sys.argv[3] if name == 'aapt' else sys.argv[-1])
    if mode == 'mutate':
        apk.write_bytes(apk.read_bytes() + b'changed')
    if mode == 'fail':
        sys.exit(2)
    sys.stdout.write(here.with_name(name + '.out').read_text() if mode != 'garbage' else 'not tool output\\n')
''')

def systemui_apk(markers):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as apk:
        apk.writestr('AndroidManifest.xml', b'synthetic binary manifest')
        apk.writestr('classes.dex', b'dex\n' + b'\0'.join(m.encode() for m in markers))
        apk.writestr('classes2.dex', b'dex\n')
    return buffer.getvalue()

class Fixture(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='sysui-identity-'))
        self.addCleanup(__import__('shutil').rmtree, self.root, True)
        for name, out in (('aapt', MANIFEST), ('apksigner', SIGNERS)):
            path = self.root/name
            path.write_text(FAKE)
            path.chmod(path.stat().st_mode | stat.S_IXUSR)
            (self.root/(name + '.out')).write_text(out)
        self.config = {'host': {'sourceRoot': str(self.root), 'tools': {'aapt': str(self.root/'aapt'),
            'apksigner': str(self.root/'apksigner')}}, 'systemUiTray': TRAY}
        self.cand = {'sha': REVISION, 'build': '2026102999', 'timestamp': '1', 'channel': 'beta', 'device': 'frankel'}
        self.data = systemui_apk(TRAY['markers'])
        self.artifact = {'kind': 'target-files', 'name': 'signed-target_files.zip', 'sha256': 'f6' * 32}
        for key in ('FAKE_AAPT_MODE', 'FAKE_APKSIGNER_MODE'):
            self.addCleanup(os.environ.pop, key, None)

    def capture(self, data=None, cand=None, artifact=None, config=None):
        used = {}
        result = pipeline.systemui_identity_capture(data or self.data, TRAY['apkPath'], artifact or self.artifact,
            config or self.config, cand or self.cand, used)
        return result, used

class CaptureTest(Fixture):
    def test_observed_signed_identity_with_tool_provenance(self):
        result, used = self.capture()
        self.assertEqual(result['status'], 'observed', result)
        r = result['receipt']
        self.assertEqual((r['package'], r['versionCode'], r['signingCertificateSha256'], r['certificatePinned']),
            ('com.android.systemui', 37, CERT, False))
        self.assertEqual(r['persistent'], {'state': 'present', 'value': True})
        self.assertEqual(r['apk'], {'path': TRAY['apkPath'], 'sha256': hashlib.sha256(self.data).hexdigest()})
        self.assertEqual(r['artifact'], self.artifact)
        self.assertEqual(r['source'], {'buildId': '2026102999', 'revision': REVISION})
        for name in ('aapt', 'apksigner'):
            self.assertEqual(r['tools'][name], {'path': str(self.root/name),
                'sha256': hashlib.sha256((self.root/name).read_bytes()).hexdigest()})
            self.assertEqual(used[name], r['tools'][name])
        # xmltree, never badging; apksigner --print-certs on the same extracted copy.
        aapt_args = (self.root/'aapt.argv').read_text().split()
        self.assertEqual((aapt_args[0], aapt_args[1], aapt_args[3]), ('dump', 'xmltree', 'AndroidManifest.xml'))
        self.assertEqual((self.root/'apksigner.argv').read_text().split()[:2], ['verify', '--print-certs'])
        self.assertEqual(aapt_args[2], (self.root/'apksigner.argv').read_text().split()[2])
        self.assertEqual(systemui_identity.serialize(r), systemui_identity.serialize(json.loads(systemui_identity.serialize(r))))

    def test_apk_mutated_by_or_between_tool_runs_is_rejected(self):
        for tool in ('AAPT', 'APKSIGNER'):
            with self.subTest(tool=tool):
                os.environ['FAKE_%s_MODE' % tool] = 'mutate'
                result, _ = self.capture()
                os.environ.pop('FAKE_%s_MODE' % tool)
                self.assertEqual(result['status'], 'failed')
                self.assertTrue(any('output is for APK' in p for p in result['problems']), result)

    def test_tool_exit_or_unparseable_output_fails(self):
        for env, mode, expect in (('FAKE_AAPT_MODE', 'fail', 'aapt xmltree exited 2'),
                ('FAKE_APKSIGNER_MODE', 'fail', 'apksigner exited 2'),
                ('FAKE_AAPT_MODE', 'garbage', 'content before the manifest'),
                ('FAKE_APKSIGNER_MODE', 'garbage', 'exactly one (#1)')):
            with self.subTest(env=env, mode=mode):
                os.environ[env] = mode
                result, _ = self.capture()
                os.environ.pop(env)
                self.assertEqual(result['status'], 'failed')
                self.assertTrue(any(expect in p for p in result['problems']), result)

    def test_wrong_source_artifact_or_tools_fail_or_are_unavailable(self):
        result, _ = self.capture(cand=dict(self.cand, sha='main'))
        self.assertEqual(result['status'], 'failed')
        self.assertTrue(any('40-hex revision' in p for p in result['problems']))
        result, _ = self.capture(artifact=dict(self.artifact, sha256=None))
        self.assertEqual(result['status'], 'failed')
        self.assertTrue(any('artifact needs' in p for p in result['problems']))
        missing = json.loads(json.dumps(self.config))
        del missing['host']['tools']['apksigner']
        result, _ = self.capture(config=missing)
        self.assertEqual(result, {'status': 'unavailable', 'problems': ['tool host.tools.apksigner is not configured']})
        (self.root/'aapt').chmod(0o644)  # present but not executable: the tool cannot run
        result, _ = self.capture()
        self.assertEqual(result['status'], 'unavailable')
        self.assertTrue(result['problems'][0].startswith('xmltree could not run'))
        (self.root/'aapt').chmod(0o755)
        other = systemui_apk(['other'])
        result, _ = self.capture(data=other)
        self.assertEqual(result['receipt']['apk']['sha256'], hashlib.sha256(other).hexdigest())  # bound to the bytes given

class TrayGateTest(Fixture):
    def target_files(self, data):
        path = self.root/'target_files.zip'
        with zipfile.ZipFile(path, 'w') as archive:
            archive.writestr(TRAY['apkPath'], data)
        return path

    def test_identity_is_supplemental_and_the_tray_gate_is_unchanged(self):
        calls = []
        def identity(data, apk_path):
            calls.append((hashlib.sha256(data).hexdigest(), apk_path))
            return {'status': 'failed', 'problems': ['synthetic']}
        path = self.target_files(self.data)
        failures, receipt = pipeline.check_systemui_tray(path, self.config, identity)
        self.assertEqual(failures, [], 'an identity failure never becomes a release failure')
        self.assertEqual(receipt['identity'], {'status': 'failed', 'problems': ['synthetic']})
        self.assertEqual(calls, [(hashlib.sha256(self.data).hexdigest(), TRAY['apkPath'])])
        self.assertEqual(receipt['markers'], TRAY['markers'])
        # Without an identity hook (the unsigned stage) the receipt is exactly as before.
        failures, plain = pipeline.check_systemui_tray(path, self.config)
        self.assertEqual((failures, sorted(plain)), ([], ['apkPath', 'dexFiles', 'markers', 'sha256']))
        # A tray failure stays a failure whatever the identity result.
        stale = self.target_files(systemui_apk(TRAY['markers'][:2]))
        failures, receipt = pipeline.check_systemui_tray(stale, self.config,
            lambda data, apk_path: {'status': 'observed', 'receipt': {}})
        self.assertTrue(failures and 'not compiled in' in failures[0])
        self.assertEqual(receipt['identity']['status'], 'observed')

    def test_only_the_signed_stage_captures_identity(self):
        code = (SCRIPTS/'release-pipeline.py').read_text()
        start = code.index('def verify_signed(')
        signed = code[start:code.index('\n# ----', start)]
        build = code[code.index('def verify_build('):code.index('def cert_sha256(')]
        self.assertIn('systemui_identity_capture(data, apk_path, artifact, config, cand, identity_tools)', signed)
        self.assertIn("'signedTargetFilesSha256': signed_sha256", signed)
        self.assertIn('check_systemui_tray(target_files, config)\n', build)
        self.assertNotIn('systemui_identity', build)
        capture = code[code.index('def systemui_identity_capture('):code.index('def check_apps(')]
        self.assertIn("'dump', 'xmltree'", capture)
        self.assertNotIn('badging', capture)
        self.assertNotIn('Pinned', capture.replace('certificatePinned', ''))

if __name__ == '__main__':
    unittest.main()
