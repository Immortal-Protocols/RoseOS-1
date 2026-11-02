"""alpha-apk-diagnostic.py (INFRA-ALPHA-116-DIAG) and its os-release.yml mode: only the three fixed APKs, every
aapt failure kept as its own outcome (never a package), no compile/sign/publish, the image gate unchanged, the shared
lock released and the temporary extraction removed. Fake aapt and synthetic partitions; no builder or image tool."""
import importlib.util
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import textwrap
import types
import unittest
import zipfile

SCRIPTS = Path(__file__).resolve().parents[1]
ROOT = SCRIPTS.parent
sys.path.insert(0, str(SCRIPTS))
import alpha_image  # noqa: E402
spec = importlib.util.spec_from_file_location('alpha_apk_diagnostic', SCRIPTS/'alpha-apk-diagnostic.py')
diag = importlib.util.module_from_spec(spec)
spec.loader.exec_module(diag)
WORKFLOW = (ROOT/'.github/workflows/os-release.yml').read_text()
SHA = 'ae9cfd4d9fafd2db4f2bc6aa76b7d025d6473fb9'
APPS = [['dev.pixel.voice', 302], ['dev.pixel.calculator.agent', 4], ['dev.pixel.backgroundcontrol', 28]]
TS = '1793215000'

# Fake aapt: logs argv, then answers from FAKE_AAPT_<basename> = "rc|stdout|stderr" (\n escaped as \\n).
FAKE_AAPT = textwrap.dedent('''\
    #!/usr/bin/env python3
    import os, sys
    with open(os.environ["FAKE_AAPT_LOG"], "a") as log:
        log.write(" ".join(sys.argv[1:]) + "\\n")
    mode = sys.argv[2]
    apk = os.path.basename(sys.argv[3]).replace(".", "_")
    rc, out, err = os.environ.get(f"FAKE_AAPT_{mode}_{apk}", "0||").split("|", 2)
    sys.stdout.write(out.replace(chr(92) + "n", chr(10)))
    sys.stderr.write(err.replace(chr(92) + "n", chr(10)))
    sys.exit(int(rc))
''')
GOOD = "package: name='com.android.shell' versionCode='37' versionName='17'\\nsdkVersion:'37'"
TREE = ('N: android=http://schemas.android.com/apk/res/android\\n  E: manifest (line=2)\\n'
    '    A: android:versionCode(0x0101021b)=(type 0x10)0x25\\n    A: package="com.android.shell" (Raw: "com.android.shell")\\n'
    '    E: uses-sdk (line=5)\\n      A: package="not.the.root"')

def apk(path, manifest=True, extra=()):
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, 'w') as z:
        if manifest:
            z.writestr('AndroidManifest.xml', b'\x03\x00\x08\x00binary')
        z.writestr('classes.dex', b'dex')
        for name in extra:
            z.writestr(name, b'x')

class Env(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='apk-diag-test-'))
        self.aapt = self.tmp/'aapt'
        self.aapt.write_text(FAKE_AAPT)
        self.aapt.chmod(self.aapt.stat().st_mode | stat.S_IXUSR)
        self.log = self.tmp/'aapt.log'
        self.saved = dict(os.environ)
        os.environ['FAKE_AAPT_LOG'] = str(self.log)
        self.roots = {'system': self.tmp/'fs-system', 'system_ext': self.tmp/'fs-system_ext'}
        apk(self.roots['system']/'system/priv-app/Shell/Shell.apk')
        apk(self.roots['system']/'system/priv-app/IntentResolver/IntentResolver.apk')
        apk(self.roots['system_ext']/'priv-app/Settings/Settings.apk')
        apk(self.roots['system']/'system/priv-app/Other/Other.apk')  # in the image, never read
        apk(self.roots['system_ext']/'priv-app/Launcher/Launcher.apk')

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.saved)
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def answer(self, mode, name, rc, out='', err=''):
        os.environ[f'FAKE_AAPT_{mode}_{name.replace(".", "_")}'] = f'{rc}|{out}|{err}'

    def calls(self):
        return self.log.read_text().splitlines() if self.log.exists() else []

    def inspect(self, location):
        partition = location.split(':', 1)[0]
        return diag.inspect(location, self.roots[partition], str(self.aapt), str(self.tmp))

class InspectTest(Env):
    def test_a_readable_apk_reports_package_version_and_the_compiled_manifest(self):
        self.answer('badging', 'Shell.apk', 0, GOOD)
        self.answer('xmltree', 'Shell.apk', 0, TREE)
        row = self.inspect(diag.TARGETS[0])
        self.assertEqual((row['status'], row['zip'], row['manifestMember']), ('regular', 'crc-ok', True))
        self.assertEqual(row['badging']['outcome'], 'package-and-version')
        self.assertEqual((row['badging']['packageName'], row['badging']['versionCode']), ('com.android.shell', '37'))
        self.assertEqual(row['badging']['packageLine'], "package: name='com.android.shell' versionCode='37' versionName='17'")
        self.assertEqual(row['compiledManifest']['outcome'], 'package-and-version')
        self.assertEqual((row['compiledManifest']['package'], row['compiledManifest']['versionCode']), ('com.android.shell', 37))
        self.assertEqual(len(row['sha256']), 64)

    def test_every_failure_is_its_own_outcome_and_never_a_package(self):
        cases = {
            'aapt-exit-nonzero': (1, '', 'ERROR: dump failed because no AndroidManifest.xml found'),
            'no-package-name': (0, 'sdkVersion:\'37\'', ''),
            'version-absent': (0, "package: name='com.android.shell' versionCode='' versionName=''", ''),
            'version-non-numeric': (0, "package: name='com.android.shell' versionCode='abc'", ''),
        }
        for outcome, (rc, out, err) in cases.items():
            self.answer('badging', 'Shell.apk', rc, out, err)
            row = self.inspect(diag.TARGETS[0])
            self.assertEqual(row['badging']['outcome'], outcome, outcome)
            self.assertNotEqual(row['badging']['outcome'], 'package-and-version')
        # The unchanged gate reads every one of these as unreadable, exactly as in run 40765631053.
        apkpath = self.roots['system']/'system/priv-app/Shell/Shell.apk'
        for rc, out, err in cases.values():
            self.answer('badging', 'Shell.apk', rc, out, err)
            self.assertIsNone(alpha_image.badging(str(self.aapt), apkpath))

    def test_version_absent_from_the_compiled_manifest_is_reported_not_guessed(self):
        self.answer('badging', 'Shell.apk', 0, "package: name='com.android.shell' versionCode=''")
        self.answer('xmltree', 'Shell.apk', 0, 'N: x\\n  E: manifest (line=2)\\n    A: package="com.android.shell" (Raw: "com.android.shell")')
        row = self.inspect(diag.TARGETS[0])
        self.assertEqual(row['compiledManifest']['outcome'], 'version-absent')
        self.assertIsNone(row['compiledManifest']['versionCode'])
        self.answer('xmltree', 'Shell.apk', 1, '', 'ERROR')
        self.assertEqual(self.inspect(diag.TARGETS[0])['compiledManifest']['outcome'], 'reader-failed')

    def test_links_absent_files_directories_and_non_zips_are_not_read(self):
        shell = self.roots['system']/'system/priv-app/Shell/Shell.apk'
        shell.unlink()
        self.assertEqual(self.inspect(diag.TARGETS[0])['status'], 'absent')
        shell.symlink_to(self.roots['system']/'system/priv-app/Other/Other.apk')
        self.assertEqual(self.inspect(diag.TARGETS[0]), {'location': diag.TARGETS[0], 'status': 'symlink'})
        shell.unlink()
        shell.mkdir()
        self.assertEqual(self.inspect(diag.TARGETS[0])['status'], 'not-a-regular-file')
        self.assertEqual(self.calls(), [])
        settings = self.roots['system_ext']/'priv-app/Settings/Settings.apk'
        settings.write_bytes(b'not a zip at all')
        row = self.inspect(diag.TARGETS[2])
        self.assertEqual((row['zip'], row['compiledManifest']['outcome']), ('not-a-zip', 'no-manifest-member'))
        apk(settings, manifest=False)
        self.assertFalse(self.inspect(diag.TARGETS[2])['manifestMember'])

    def test_stderr_is_bounded_and_sanitized(self):
        noisy = 'E' * 5000 + '\x1b[31m' + str(self.tmp) + '/secretless/path'
        self.answer('badging', 'Shell.apk', 1, '', noisy)
        stderr = self.inspect(diag.TARGETS[0])['badging']['stderr']
        self.assertLessEqual(len(stderr), diag.STDERR_CAP + 40)
        self.assertIn('[truncated', stderr)
        self.assertNotIn('\x1b', diag.sanitize(noisy, 10**6, self.tmp))
        self.assertIn('<extract>/secretless/path', diag.sanitize(noisy, 10**6, self.tmp))

class CollectTest(Env):
    def patched_collect(self, workdir):
        unpacked = self.tmp/'unpacked'
        unpacked.mkdir()
        for name in diag.PARTITIONS:
            (unpacked/f'{name}.img').write_bytes(name.encode())
        product_out = self.tmp/'product'
        product_out.mkdir()
        (product_out/'super.img').write_bytes(b'super')
        spec = {'tools': {'aapt': str(self.aapt)}, 'toolSha256': {}}
        saved = (alpha_image.unpack_super, alpha_image.extract)
        alpha_image.unpack_super = lambda image, wd, tools, used: unpacked
        alpha_image.extract = lambda image, dest, fs, tools, used: self.roots[image.stem]
        try:
            used = {}
            return diag.collect(product_out, spec, 'erofs', workdir, used), used
        finally:
            alpha_image.unpack_super, alpha_image.extract = saved

    def test_only_the_three_fixed_paths_are_read_and_hashes_are_new_observations(self):
        (observed, apks), used = self.patched_collect(str(self.tmp))
        self.assertEqual([a['location'] for a in apks], list(diag.TARGETS))
        read = sorted({c.split()[2] for c in self.calls()})  # dump <badging|xmltree> <apk> [member]
        self.assertEqual(read, sorted(str(self.roots[t.split(':')[0]]/t.split(':', 1)[1]) for t in diag.TARGETS))
        self.assertNotIn('Other.apk', '\n'.join(self.calls()))
        self.assertNotIn('Launcher.apk', '\n'.join(self.calls()))
        self.assertEqual(sorted(observed['partitions']), ['system', 'system_ext'])
        self.assertEqual(used['aapt']['path'], str(self.aapt))
        self.assertEqual(len(used['aapt']['sha256']), 64)

    def test_a_tool_that_differs_from_its_pin_stops_collection(self):
        product_out = self.tmp/'product'
        product_out.mkdir()
        (product_out/'super.img').write_bytes(b'super')
        with self.assertRaisesRegex(alpha_image.ImageCheckFailed, 'differs from its pinned sha256'):
            diag.collect(product_out, {'tools': {'aapt': str(self.aapt)}, 'toolSha256': {'aapt': '0' * 64}}, 'erofs', str(self.tmp), {})
        (product_out/'super.img').unlink()
        (product_out/'super.img').symlink_to(self.aapt)
        with self.assertRaisesRegex(alpha_image.ImageCheckFailed, 'not a regular file'):
            diag.collect(product_out, {'tools': {'aapt': str(self.aapt)}}, 'erofs', str(self.tmp), {})

class FakeAlpha:
    """emulator-alpha.py's checks, recording calls; the lock records that it was released."""
    class CheckFailed(Exception): pass
    class InvalidInput(Exception): pass
    class SetupMissing(Exception):
        def __init__(self, missing):
            super().__init__(missing)
            self.missing = missing

    def __init__(self, root, identity=(), pinned=()):
        self.root, self.identity, self.pinned, self.events = root, list(identity), list(pinned), []
    def guard(self, config): self.events.append('guard')
    def check_dispatch(self, config, sha, apps):
        if sha != SHA or apps != APPS:
            raise self.CheckFailed('not the bound base')
    def build_id(self, config, run): return 'rose-cf-alpha-' + run
    def locked(self, config):
        self.events.append('lock')
        return types.SimpleNamespace(close=lambda: self.events.append('unlock'))
    def pinned_failures(self, config): return {}, self.root/'tree', self.pinned
    def read_props(self, product_out): return {'system/build.prop': {'ro.build.version.incremental': 'rose-cf-alpha-40765631053',
        'ro.build.date.utc': TS, 'ro.build.type': 'userdebug', 'ro.build.fingerprint': 'f/rose-cf-alpha-40765631053:userdebug/test-keys'}}
    def identity_failures(self, props, config, build, timestamp, device):
        self.events.append(('identity', build, timestamp))
        return self.identity
    def preparation_dir(self, config, pid):
        directory = self.root/'prep'
        directory.mkdir(exist_ok=True)
        return directory

CONFIG = {'sourceBinding': {'candidate': SHA, 'reconcileReceipt': 'r.json', 'reconcileReceiptSha256': 'a' * 64, 'apps': APPS},
    'prepared': {'preparationId': 'p' * 64, 'receiptSha256': 'b' * 64, 'successor': {'successorId': 's' * 64, 'receiptSha256': 'c' * 64}},
    'hostEvidence': {'lunch': {'PRODUCT_OUT': 'out/product', 'PRODUCT_DEVICE': 'vsoc_x86_64_only', 'BOARD_SYSTEMIMAGE_FILE_SYSTEM_TYPE': 'erofs'}},
    'imageVerification': {'tools': {}}}

class RunTest(Env):
    def run_diag(self, ea, collector, evidence=None, **kw):
        evidence = evidence or self.tmp/'evidence'
        return diag.run(CONFIG, ea, kw.get('sha', SHA), kw.get('apps', APPS), '41000000001', kw.get('ts', TS), evidence, collector=collector), evidence

    def test_collected_evidence_is_marked_unverified_unsigned_unpublished_without_make(self):
        ea, seen = FakeAlpha(self.tmp), {}
        def collector(product_out, spec, fs, workdir, used):
            seen['workdir'] = workdir
            self.assertTrue(Path(workdir).is_dir())
            self.assertEqual(Path(workdir).parent, self.tmp/'prep')
            return {'superImage': {'sha256': 'd' * 64}, 'partitions': {}}, [{'location': t, 'status': 'absent'} for t in diag.TARGETS]
        document, evidence = self.run_diag(ea, collector)
        self.assertFalse(Path(seen['workdir']).exists(), 'temporary extraction must be removed')
        self.assertEqual(ea.events[-1], 'unlock')
        self.assertIn(('identity', 'rose-cf-alpha-40765631053', TS), ea.events)
        self.assertEqual(document['verdict'], 'COLLECTED')
        for key in ('executedMake', 'imageVerified', 'signed', 'published'):
            self.assertIs(document[key], False, key)
        self.assertEqual(document['originalRun']['runId'], '40765631053')
        self.assertIn('not taken from run 40765631053', document['newObservations']['note'])
        self.assertEqual(json.loads((evidence/'diagnostic.json').read_text())['verdict'], 'COLLECTED')
        self.assertIn('diagnostic.json', (evidence/'SHA256SUMS').read_text())

    def test_a_different_build_or_tree_fails_closed_with_evidence_and_cleanup(self):
        for ea in (FakeAlpha(self.tmp, identity=['system/build.prop: ro.build.date.utc=1']), FakeAlpha(self.tmp, pinned=['tree drift'])):
            evidence = self.tmp/f'evidence-{len(ea.identity)}'
            with self.assertRaises(ea.CheckFailed):
                self.run_diag(ea, lambda *a: self.fail('must not extract'), evidence)
            self.assertEqual(ea.events[-1], 'unlock')
            document = json.loads((evidence/'diagnostic.json').read_text())
            self.assertEqual((document['verdict'], document['apks'], document['imageVerified']), ('FAIL', None, False))

    def test_extraction_failure_removes_the_temporary_directory_and_releases_the_lock(self):
        ea, seen = FakeAlpha(self.tmp), {}
        def collector(product_out, spec, fs, workdir, used):
            seen['workdir'] = workdir
            raise alpha_image.ImageCheckFailed('lpunpack of super.img failed (exit 1)')
        with self.assertRaises(ea.CheckFailed):
            self.run_diag(ea, collector)
        self.assertFalse(Path(seen['workdir']).exists())
        self.assertEqual(ea.events[-1], 'unlock')

    def test_inputs_are_the_bound_base_and_fixed_identity(self):
        ea = FakeAlpha(self.tmp)
        with self.assertRaises(ea.CheckFailed):
            self.run_diag(ea, lambda *a: None, sha='0' * 40)
        with self.assertRaises(ea.InvalidInput):
            self.run_diag(ea, lambda *a: None, ts='now')
        self.assertNotIn('lock', ea.events)
        (self.tmp/'evidence').mkdir()
        with self.assertRaises(ea.CheckFailed):
            self.run_diag(ea, lambda *a: None)

class SourceTest(unittest.TestCase):
    def test_the_module_runs_no_build_sign_publish_or_install_and_reads_only_fixed_paths(self):
        code = (SCRIPTS/'alpha-apk-diagnostic.py').read_text()
        body = code.split('"""', 2)[2]
        for forbidden in ('steps(', 'ea.resolve(', "'make'", 'm -j', 'envsetup', 'sign-apk', 'apksigner', 'host-sign', 'publish(', 'gcloud',
                'actions-gcp-login', 'adb', 'pm install', 'shell=True', 'os.environ['):
            self.assertNotIn(forbidden, body, forbidden)
        self.assertEqual(diag.TARGETS, ('system:system/priv-app/Shell/Shell.apk',
            'system:system/priv-app/IntentResolver/IntentResolver.apk', 'system_ext:priv-app/Settings/Settings.apk'))
        self.assertEqual(diag.ORIGINAL_RUN, '40765631053')

    def test_the_image_gate_keeps_requiring_a_numeric_version(self):
        gate = (SCRIPTS/'alpha_image.py').read_text()
        self.assertIn('''match = re.search(r"^package: name='([^']+)' versionCode='(\\d+)'", out.stdout, re.M)''', gate)
        self.assertIn("return (match[1], int(match[2])) if out.returncode == 0 and match else None", gate)
        self.assertIn("could not be read; Updater absence is unproven", gate)

class WorkflowTest(unittest.TestCase):
    def job(self):
        return WORKFLOW[WORKFLOW.index('  alpha-apk-diagnostic:'):WORKFLOW.index('  publisher:')]

    def test_the_mode_is_explicit_read_only_and_cleaned_up(self):
        self.assertIn('emulator-alpha-apk-diagnostic, emulator-alpha-verify-retained, app-reuse, emulator-alpha-export, rango-factory-observe]', WORKFLOW)
        job = self.job()
        self.assertIn("if: inputs.mode == 'emulator-alpha-apk-diagnostic'", job)
        self.assertIn('runs-on: [self-hosted, roseos-frankel-builder]', job)
        self.assertIn('    permissions:\n      contents: read\n    steps:', job)
        for forbidden in ('id-token', 'secrets.', 'actions-gcp-login', 'GCP_SIGNER', 'os-release-stage', 'release-pipeline.py publish',
                'emulator-alpha.py build', 'host-app-release', 'resume'):
            self.assertNotIn(forbidden, job, forbidden)
        self.assertIn('python3 scripts/alpha-apk-diagnostic.py run --candidate-sha "$CANDIDATE_SHA" --apps "$APPS" '
            '--run-id "$GITHUB_RUN_ID" --build-timestamp "$BUILD_TIMESTAMP" --evidence-dir "$ROSE_BASE/evidence"', job)
        self.assertIn('if: always()\n        run: test -z "${ROSE_BASE:-}" || rm -rf "$ROSE_BASE"', job)
        self.assertIn('alpha-apk-diagnostic, alpha-verify-retained, alpha-export, app-reuse, request-signing, signed-builder, publisher]', WORKFLOW)  # the builder is always stopped afterwards
        guard = WORKFLOW[WORKFLOW.index('  guard:'):WORKFLOW.index('  controller:')]
        self.assertIn("if: inputs.mode == 'emulator-alpha-apk-diagnostic'\n        run: python3 scripts/emulator-alpha.py preflight --job guard", guard)
        # Promotion, resume, app publication and alpha recovery stay outside this mode (existing guard rules).
        self.assertIn('[[ "$MODE" == os-release || ( "$RESUME_STAGE" == none && -z "$RESUME_SOURCE_RUN" && "$PROMOTE" == false ) ]]', guard)

if __name__ == '__main__':
    unittest.main()
