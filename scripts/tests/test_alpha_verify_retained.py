"""alpha-verify-retained.py (INFRA-ALPHA-117) and its os-release.yml mode: bound to run 40765631053 and the measured
super.img, refuses any other preimage before the gate, runs emulator-alpha.py's full image_content, writes one
write-once receipt (PASS or FAIL) that relabels nothing, releases the lock, and never builds, signs or publishes.
The image is a small fixture standing in for the measured one (its pins are swapped for the fixture's)."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import os_release_shape as shape  # noqa: E402

SCRIPTS = Path(__file__).resolve().parents[1]
ROOT = SCRIPTS.parent
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location('alpha_verify_retained', SCRIPTS/'alpha-verify-retained.py')
verify = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verify)
WORKFLOW = (ROOT/'.github/workflows/os-release.yml').read_text()
SHA = 'ae9cfd4d9fafd2db4f2bc6aa76b7d025d6473fb9'
APPS = [['dev.pixel.voice', 302], ['dev.pixel.calculator.agent', 4], ['dev.pixel.backgroundcontrol', 28]]
LUNCH = {'PRODUCT_OUT': 'out/product', 'HOST_OUT': 'out/host', 'PRODUCT_DEVICE': 'vsoc_x86_64_only', 'BOARD_SYSTEMIMAGE_FILE_SYSTEM_TYPE': 'erofs'}
CONFIG = {'sourceBinding': {'candidate': SHA, 'reconcileReceipt': 'r.json', 'reconcileReceiptSha256': 'a' * 64, 'apps': APPS},
    'prepared': {'preparationId': 'p' * 64, 'receiptSha256': 'b' * 64,
        'successor': {'successorId': '1497d4930e2de56c1b1350cbee4dc7d47e035bf605d3d17c71029d6da1e38623', 'receiptSha256': 'c' * 64}},
    'hostEvidence': {'lunch': LUNCH}, 'build': {'outDir': 'out'}, 'target': {'keys': 'test-keys'},
    'hostPackage': {'output': 'cvd-host_package.tar.gz'}}

class FakeAlpha:
    class CheckFailed(Exception): pass
    class InvalidInput(Exception): pass
    class SetupMissing(Exception):
        def __init__(self, missing):
            super().__init__(missing)
            self.missing = missing

    def __init__(self, root, identity=(), pinned=(), gate=None):
        self.root, self.identity, self.pinned, self.gate, self.events = root, list(identity), list(pinned), gate, []
    def guard(self, config): self.events.append('guard')
    def check_dispatch(self, config, sha, apps):
        if sha != SHA or apps != APPS:
            raise self.CheckFailed('not the bound base')
    def build_id(self, config, run): return 'rose-cf-alpha-' + run
    def locked(self, config):
        self.events.append('lock')
        return types.SimpleNamespace(close=lambda: self.events.append('unlock'))
    def pinned_failures(self, config): return {}, self.root/'tree', self.pinned
    def read_props(self, product_out):
        return {'system/build.prop': {'ro.build.version.incremental': 'rose-cf-alpha-40765631053', 'ro.build.date.utc': '1793220134',
            'ro.build.type': 'userdebug'}, 'vendor/build.prop': {'ro.product.device': 'vsoc_x86_64_only'}}
    def identity_failures(self, props, config, build, timestamp, device):
        self.events.append(('identity', build, timestamp, device))
        return self.identity
    def image_content(self, config, preparation_id, product_out):
        self.events.append(('image_content', preparation_id, product_out))
        if self.gate:
            return self.gate(product_out)
        return {'verified': True, 'updaterAbsent': {'package': 'app.seamlessupdate.client', 'apksInspected': 211}}

class Env(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='verify-retained-'))
        self.out = self.tmp/'tree/out/product'
        self.out.mkdir(parents=True)
        self.image = self.out/'super.img'
        self.image.write_bytes(b'measured super image')
        (self.out/'boot.img').write_bytes(b'boot')
        (self.tmp/'tree/out/host').mkdir(parents=True)
        (self.tmp/'tree/out/host/cvd-host_package.tar.gz').write_bytes(b'host')
        self.saved = (verify.SUPER_SHA256, verify.SUPER_SIZE)
        verify.SUPER_SHA256 = hashlib.sha256(b'measured super image').hexdigest()
        verify.SUPER_SIZE = len(b'measured super image')

    def tearDown(self):
        verify.SUPER_SHA256, verify.SUPER_SIZE = self.saved
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_verify(self, ea, receipt=None, **kw):
        receipt = receipt or self.tmp/'receipt'
        return verify.run(CONFIG, ea, kw.get('sha', SHA), kw.get('apps', APPS), '41000000002', kw.get('ts', '1793220134'), receipt), receipt

    def receipt(self, directory=None):
        return json.loads(((directory or self.tmp/'receipt')/verify.RECEIPT).read_text())

class BindingTest(Env):
    def test_the_full_gate_runs_once_on_the_exact_retained_output_and_the_receipt_relabels_nothing(self):
        ea = FakeAlpha(self.tmp)
        before = self.image.read_bytes()
        result, directory = self.run_verify(ea)
        self.assertEqual([e for e in ea.events if isinstance(e, tuple) and e[0] == 'image_content'],
            [('image_content', 'p' * 64, self.out)])
        self.assertIn(('identity', 'rose-cf-alpha-40765631053', '1793220134', 'vsoc_x86_64_only'), ea.events)
        self.assertEqual(ea.events[-1], 'unlock')
        self.assertEqual(self.image.read_bytes(), before)
        document = self.receipt()
        self.assertEqual(document, result)
        self.assertEqual((document['verdict'], document['imageVerified']), ('PASS', True))
        for key, value in (('executedMake', False), ('signed', False), ('published', False), ('testOnly', True), ('bootAttempted', False)):
            self.assertIs(document[key], value, key)
        self.assertIsNone(document['originalCompile']['buildReceipt'])
        self.assertEqual(document['originalCompile']['runId'], '40765631053')
        self.assertEqual(document['diagnosticProvenance']['runId'], '40771878744')
        self.assertIn('measured by this verification run', document['newObservations'])
        self.assertEqual(sorted(document['images']), ['boot.img', 'super.img'])
        self.assertEqual(document['images']['super.img']['sha256'], verify.SUPER_SHA256)
        # A fingerprint the props do not carry is recorded as absent, not as checked.
        self.assertIsNone(document['buildProps']['system/build.prop']['ro.build.fingerprint'])
        self.assertNotIn('ro.build.fingerprint', document['identityChecked'])
        self.assertIn(f'{hashlib.sha256((directory/verify.RECEIPT).read_bytes()).hexdigest()}  {verify.RECEIPT}',
            (directory/'SHA256SUMS').read_text())

    def test_an_altered_or_linked_preimage_is_refused_before_the_gate(self):
        for change in (lambda: self.image.write_bytes(b'measured super image!'),   # size
                       lambda: self.image.write_bytes(b'Measured super image'),    # same size, other bytes
                       lambda: (self.image.unlink(), self.image.symlink_to(self.out/'boot.img'))):
            self.setUp()
            change()
            ea = FakeAlpha(self.tmp)
            with self.assertRaisesRegex(ea.CheckFailed, 'preimage refused'):
                self.run_verify(ea)
            self.assertFalse(any(isinstance(e, tuple) and e[0] == 'image_content' for e in ea.events))
            self.assertEqual((self.receipt()['verdict'], self.receipt()['imageVerified']), ('FAIL', False))
            self.assertEqual(ea.events[-1], 'unlock')

    def test_a_super_image_that_changes_during_the_gate_fails(self):
        ea = FakeAlpha(self.tmp, gate=lambda out: (out/'super.img').write_bytes(b'xeasured super image'))
        with self.assertRaisesRegex(ea.CheckFailed, 'changed during verification'):
            self.run_verify(ea)
        self.assertEqual(self.receipt()['verdict'], 'FAIL')

    def test_other_builds_trees_inputs_and_existing_receipts_are_refused(self):
        ea = FakeAlpha(self.tmp, identity=['system/build.prop: ro.build.date.utc=1'])
        with self.assertRaisesRegex(ea.CheckFailed, "not run 40765631053's build"):
            self.run_verify(ea, self.tmp/'r1')
        ea = FakeAlpha(self.tmp, pinned=['tree drift'])
        with self.assertRaisesRegex(ea.CheckFailed, 'tree drift'):
            self.run_verify(ea, self.tmp/'r2')
        for directory in (self.tmp/'r1', self.tmp/'r2'):
            self.assertEqual(self.receipt(directory)['verdict'], 'FAIL')
        ea = FakeAlpha(self.tmp)
        with self.assertRaises(ea.InvalidInput):
            self.run_verify(ea, self.tmp/'r3', ts='1793220135')
        with self.assertRaises(ea.CheckFailed):
            self.run_verify(ea, self.tmp/'r4', sha='0' * 40)
        self.assertNotIn('lock', ea.events)
        with self.assertRaises(ea.CheckFailed):
            self.run_verify(ea, self.tmp/'r1')  # receipts are never overwritten

    def test_a_gate_failure_is_recorded_and_the_lock_released(self):
        def gate(out):
            raise FakeAlpha.CheckFailed('image content: system:app/X.apk could not be read; Updater absence is unproven')
        ea = FakeAlpha(self.tmp, gate=gate)
        with self.assertRaises(ea.CheckFailed):
            self.run_verify(ea)
        document = self.receipt()
        self.assertEqual((document['verdict'], document['imageVerified']), ('FAIL', False))
        self.assertIn('Updater absence is unproven', document['error'])
        self.assertNotIn('images', document)
        self.assertEqual(ea.events[-1], 'unlock')

class SourceTest(unittest.TestCase):
    def test_no_build_sign_publish_and_the_real_gate(self):
        body = (SCRIPTS/'alpha-verify-retained.py').read_text().split('"""', 2)[2]
        for forbidden in ('steps(', 'ea.resolve(', "'make'", 'm -j', 'envsetup', 'sign-apk', 'host-sign', 'publish(', 'gcloud',
                'actions-gcp-login', 'adb', 'shell=True', 'alpha_image.', 'os.environ['):
            self.assertNotIn(forbidden, body, forbidden)
        self.assertIn("content = ea.image_content(config, config['prepared']['preparationId'], product_out)", body)
        self.assertEqual((verify.ORIGINAL_RUN, verify.ORIGINAL_BUILD_DATETIME, verify.DIAGNOSTIC_RUN), ('40765631053', '1793220134', '40771878744'))
        self.assertEqual((self.pins()), ('e543c1301c3f999db5d051bd85ff9cc3a006e47a8d2a8cfd1f484c339d909ed3', 2410014072))

    def pins(self):
        code = (SCRIPTS/'alpha-verify-retained.py').read_text()
        import re
        return re.search(r"SUPER_SHA256 = '([0-9a-f]{64})'", code)[1], int(re.search(r'SUPER_SIZE = (\d+)', code)[1])

class WorkflowTest(unittest.TestCase):
    def test_the_mode_is_explicit_read_only_and_cleaned_up(self):
        job = WORKFLOW[WORKFLOW.index('  alpha-verify-retained:'):WORKFLOW.index('  publisher:')]
        self.assertIn('emulator-alpha-verify-retained', shape.modes())
        self.assertIn("if: inputs.mode == 'emulator-alpha-verify-retained'", job)
        self.assertIn('    permissions:\n      contents: read\n    steps:', job)
        for forbidden in ('id-token', 'secrets.', 'actions-gcp-login', 'GCP_SIGNER', 'os-release-stage', 'release-pipeline.py publish',
                'emulator-alpha.py build', 'host-app-release'):
            self.assertNotIn(forbidden, job, forbidden)
        self.assertIn('python3 scripts/alpha-verify-retained.py run --candidate-sha "$CANDIDATE_SHA" --apps "$APPS" '
            '--run-id "$GITHUB_RUN_ID" --build-timestamp "$BUILD_TIMESTAMP" --receipt-dir "$ROSE_BASE/receipt"', job)
        self.assertIn('if: always()\n        run: test -z "${ROSE_BASE:-}" || rm -rf "$ROSE_BASE"', job)
        self.assertIn('alpha-verify-retained', shape.cleanup_needs())
        guard = WORKFLOW[WORKFLOW.index('  guard:'):WORKFLOW.index('  controller:')]
        self.assertIn("if: inputs.mode == 'emulator-alpha-verify-retained'\n        run: python3 scripts/emulator-alpha.py preflight --job guard", guard)

if __name__ == '__main__':
    unittest.main()
