"""emulator-alpha.py and the os-release alpha-builder job against a synthetic retained tree whose build/envsetup.sh
fakes lunch, get_build_var and m (so the real resolve and make command lines run): exact target and binding, disjoint
OUT_DIR, shared lock, no reconcile/signing/publication, fail-closed host evidence and no boot claim."""
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
spec = importlib.util.spec_from_file_location('emulator_alpha', SCRIPTS/'emulator-alpha.py')
alpha = importlib.util.module_from_spec(spec)
spec.loader.exec_module(alpha)
CHECKED_IN = json.loads((ROOT/'os/release-pipeline/cuttlefish-alpha.json').read_text())
FRANKEL = json.loads((ROOT/'os/release-pipeline/frankel.json').read_text())
WORKFLOW = (ROOT/'.github/workflows/os-release.yml').read_text()
SHA = '62286c084e52bc57e41a35333653960844c16ad2'
APPS = [['dev.pixel.voice', 302], ['dev.pixel.calculator.agent', 4], ['dev.pixel.backgroundcontrol', 28]]
TS = '1793180700'
BOUND = 'ae9cfd4d9fafd2db4f2bc6aa76b7d025d6473fb9'  # 2804, the run that wrote the retained tree's latest reconcile receipt
FIXTURES = SCRIPTS/'tests/fixtures/cuttlefish-283645aa'  # unmodified pinned upstream files (see its README)
LUNCH = {'TARGET_PRODUCT': 'aosp_cf_x86_64_only_phone', 'TARGET_RELEASE': 'cur', 'TARGET_BUILD_VARIANT': 'userdebug',
    'TARGET_BOARD_PLATFORM': 'vsoc_x86_64', 'PRODUCT_DEVICE': 'vsoc_x86_64_only', 'PRODUCT_OUT': 'out-rose-cf-alpha/target/product/vsoc_x86_64_only',
    'HOST_OUT': 'out-rose-cf-alpha/host/linux-x86'}

ENVSETUP = r'''
lunch() {
  [ "$1" = "${FAKE_LUNCH_ACCEPT:-aosp_cf_x86_64_only_phone-cur-userdebug}" ] || return 1
  TARGET_PRODUCT=aosp_cf_x86_64_only_phone TARGET_BUILD_VARIANT=userdebug PRODUCT_DEVICE=vsoc_x86_64_only
  TARGET_BOARD_PLATFORM="${FAKE_BOARD:-vsoc_x86_64}"
  # As in the retained tree: lunch exports the release into the shell (build/envsetup.sh)...
  [ -n "${FAKE_RELEASE_UNSET:-}" ] || export TARGET_RELEASE="${FAKE_RELEASE:-cur}"
  PRODUCT_OUT="${FAKE_PRODUCT_OUT:-$OUT_DIR/target/product/vsoc_x86_64_only}" HOST_OUT="$OUT_DIR/host/linux-x86"
}
# ...while get_build_var returns the release variables empty there.
get_build_var() { case "$1" in TARGET_RELEASE|TARGET_RELEASE_CONFIG) echo;; *) eval "echo \$$1";; esac; }
m() {
  echo "m $* OUT_DIR=$OUT_DIR BUILD_NUMBER=$BUILD_NUMBER BUILD_DATETIME=$BUILD_DATETIME" >> "$FAKE_LOG"
  [ -n "${FAKE_MAKE_FAIL:-}" ] && return 2
  mkdir -p "$PRODUCT_OUT/system" "$PRODUCT_OUT/vendor" "$HOST_OUT"
  for image in super boot vendor_boot vbmeta init_boot userdata; do
    case " ${FAKE_SKIP:-} " in *" $image "*) continue;; esac
    echo "$image $BUILD_NUMBER" > "$PRODUCT_OUT/$image.img"
  done
  incremental="${FAKE_INCREMENTAL:-$BUILD_NUMBER}"
  fingerprint="Android/aosp_cf_x86_64_only_phone/vsoc_x86_64_only:17/CUR/$incremental:userdebug/test-keys"
  printf 'ro.build.type=%s\nro.build.version.incremental=%s\nro.build.date.utc=%s\nro.build.fingerprint=%s\nro.product.system.device=vsoc_x86_64_only\n' \
    "${FAKE_TYPE:-userdebug}" "$incremental" "$BUILD_DATETIME" "$fingerprint" > "$PRODUCT_OUT/system/build.prop"
  printf 'ro.vendor.build.version.incremental=%s\nro.vendor.build.fingerprint=%s\nro.product.vendor.device=vsoc_x86_64_only\n' \
    "$incremental" "$fingerprint" > "$PRODUCT_OUT/vendor/build.prop"
  echo host > "$HOST_OUT/cvd-host_package.tar.gz"
}
'''

def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def jobs(text):
    body = text.split('\njobs:\n', 1)[1]
    return {m[1]: m[2] for m in re.finditer(r'^  ([\w-]+):\n((?:(?:    .*)?\n)*)', body, re.M)}

class AlphaTreeTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        src = self.src = self.root/'src'
        cf = src/'device/google/cuttlefish'
        (cf/'vsoc_x86_64_only/phone').mkdir(parents=True)
        (cf/'vsoc_x86_64_only/phone/aosp_cf.mk').write_text('PRODUCT_NAME := aosp_cf_x86_64_only_phone\n')
        (cf/'AndroidProducts.mk').write_text('PRODUCT_MAKEFILES := \\\n\taosp_cf_x86_64_phone:$(LOCAL_DIR)/vsoc_x86_64/phone/aosp_cf.mk \\\n'
            '\taosp_cf_x86_64_only_phone:$(LOCAL_DIR)/vsoc_x86_64_only/phone/aosp_cf.mk \\\n')
        (cf/'build').mkdir()
        for name in ('Android.bp', 'cvd-host-package.go'):
            (cf/'build'/name).write_bytes((FIXTURES/'build'/name).read_bytes())
        for args in (['init', '-q'], ['add', '.'], ['-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-qm', 'cf']):
            subprocess.run(['git', '-C', str(cf), *args], check=True)
        self.cf_head = subprocess.run(['git', '-C', str(cf), 'rev-parse', 'HEAD'], capture_output=True, text=True).stdout.strip()
        original = alpha.pinned_revision
        alpha.pinned_revision = lambda project: self.cf_head if project == alpha.CUTTLEFISH else original(project)
        self.addCleanup(setattr, alpha, 'pinned_revision', original)
        (src/'build').mkdir()
        (src/'build/envsetup.sh').write_text(ENVSETUP)
        # The retained Pixel output and an integrated file the bound reconcile receipt installed.
        self.pixel_out = src/'out-rose-first-update-2026102701'
        (self.pixel_out/'target').mkdir(parents=True)
        (self.pixel_out/'target/marker').write_text('pixel build output')
        (src/'frameworks/base/core/res/res/values').mkdir(parents=True)
        self.integrated = src/'frameworks/base/core/res/res/values/config.xml'
        self.integrated.write_text('<resources>rose</resources>')
        self.receipts = self.root/'reconcile'
        self.receipts.mkdir()
        (self.receipts/'0007-2026102803.json').write_text('{"entries": {}}')
        self.receipt = self.receipts/'0008-2026102804.json'
        self.receipt.write_text(json.dumps({'receiptType': 'reconcile', 'label': '2026102804',
            'entries': {'frameworks/base/core/res/res/values/config.xml': {'after': sha(self.integrated), 'action': 'installed'}}}))
        self.lock = self.root/'frankel-build.lock'
        shared = self.root/'shared.json'
        shared.write_text(json.dumps({'host': {'sourceRoot': str(src), 'lockPath': str(self.lock),
            'outDir': 'out-rose-first-update-2026102701', 'integrate': {'reconcile': {'receiptDir': str(self.receipts)}}}}))
        self.data = json.loads(json.dumps(CHECKED_IN))
        self.data['sharedHost'] = str(shared)
        self.data['build']['minFreeGiB'] = 0
        self.data['sourceBinding'].update(candidate=SHA, reconcileReceipt=self.receipt.name, reconcileReceiptSha256=sha(self.receipt), apps=APPS)
        self.data['hostEvidence'].update(probeReceiptSha256='e' * 64, lunch=LUNCH)
        self.config_path = self.root/'alpha.json'
        self.log = self.root/'make.log'
        self.env = {'FAKE_LOG': str(self.log)}
        self.write()

    def write(self):
        self.config_path.write_text(json.dumps(self.data))

    def run_build(self, sha=SHA, apps=APPS, run_id='40680000001', receipt='receipt'):
        old = {k: os.environ.get(k) for k in self.env}
        os.environ.update(self.env)
        try:
            return alpha.build(alpha.load(self.config_path), sha, apps, run_id, TS, self.root/receipt)
        finally:
            for k, v in old.items():
                os.environ.pop(k) if v is None else os.environ.__setitem__(k, v)

    def made(self):
        return self.log.read_text().splitlines() if self.log.exists() else []

    def pixel_untouched(self):
        self.assertEqual([p.relative_to(self.pixel_out).as_posix() for p in sorted(self.pixel_out.rglob('*'))], ['target', 'target/marker'])
        self.assertEqual((self.pixel_out/'target/marker').read_text(), 'pixel build output')

    def test_build_writes_an_honest_unsigned_receipt_into_the_alpha_namespace(self):
        receipt = self.run_build()
        self.assertEqual(self.made(), ['m -j48 droid hosttar OUT_DIR=out-rose-cf-alpha BUILD_NUMBER=rose-cf-alpha-40680000001 BUILD_DATETIME=1793180700'])
        out = self.src/LUNCH['PRODUCT_OUT']
        self.assertEqual({k: receipt[k] for k in ('receiptType', 'simulated', 'testOnly', 'signed', 'keys', 'published', 'bootAttempted',
            'bootVerified', 'candidate', 'emulatorBuildId', 'buildDatetime', 'outDir', 'makeGoals', 'lunch')},
            {'receiptType': 'emulator-alpha-build', 'simulated': False, 'testOnly': True, 'signed': False, 'keys': 'test-keys',
             'published': False, 'bootAttempted': False, 'bootVerified': False, 'candidate': SHA,
             'emulatorBuildId': 'rose-cf-alpha-40680000001', 'buildDatetime': TS, 'outDir': 'out-rose-cf-alpha',
             'makeGoals': ['droid', 'hosttar'], 'lunch': LUNCH})
        self.assertEqual(receipt['sourceBinding'], {'candidate': SHA, 'reconcileReceipt': self.receipt.name,
            'reconcileReceiptSha256': sha(self.receipt), 'apps': APPS})
        self.assertEqual(sorted(receipt['images']), ['boot.img', 'init_boot.img', 'super.img', 'userdata.img', 'vbmeta.img', 'vendor_boot.img'])
        self.assertEqual(receipt['images']['super.img'], {'size': (out/'super.img').stat().st_size, 'sha256': sha(out/'super.img')})
        self.assertEqual(receipt['hostOutputs']['cvd-host_package.tar.gz']['sha256'], sha(self.src/LUNCH['HOST_OUT']/'cvd-host_package.tar.gz'))
        self.assertIn('guest boot (sys.boot_completed)', receipt['notProven'])
        self.assertEqual(receipt['buildProps']['ro.build.fingerprint'],
            'Android/aosp_cf_x86_64_only_phone/vsoc_x86_64_only:17/CUR/rose-cf-alpha-40680000001:userdebug/test-keys')
        written = self.root/'receipt'
        self.assertEqual(sorted(p.name for p in written.iterdir()), ['SHA256SUMS', 'emulator-alpha-receipt.json'])
        self.assertEqual((written/'SHA256SUMS').read_text(), f'{sha(written/"emulator-alpha-receipt.json")}  emulator-alpha-receipt.json\n')
        self.pixel_untouched()
        self.assertEqual(self.integrated.read_text(), '<resources>rose</resources>', 'the retained tree is never edited')

    def test_another_candidate_or_app_set_is_refused_before_the_lock_and_make(self):
        with self.assertRaisesRegex(alpha.CheckFailed, 'bound to 62286c0'):
            self.run_build(sha='a' * 40)
        with self.assertRaisesRegex(alpha.CheckFailed, 'differ from the bound integration apps'):
            self.run_build(apps=[['dev.pixel.voice', 303]])
        self.assertEqual(self.made(), [])
        self.assertFalse((self.root/'receipt').exists())

    def test_drift_or_a_newer_reconcile_fails_before_make(self):
        self.integrated.write_text('<resources>owner edit</resources>')
        with self.assertRaisesRegex(alpha.CheckFailed, 'tree drifted from the bound receipt: frameworks/base'):
            self.run_build()
        self.integrated.write_text('<resources>rose</resources>')
        (self.receipts/'0009-2026102805.json').write_text('{"entries": {}}')
        with self.assertRaisesRegex(alpha.CheckFailed, 'latest reconcile receipt is 0009-2026102805.json'):
            self.run_build()
        (self.receipts/'0009-2026102805.json').unlink()
        self.data['sourceBinding']['reconcileReceiptSha256'] = '0' * 64
        self.write()
        with self.assertRaisesRegex(alpha.CheckFailed, 'differs from the bound sha256'):
            self.run_build()
        self.assertEqual(self.made(), [])
        self.pixel_untouched()

    def test_unverified_host_tree_facts_fail_closed_by_name(self):
        subprocess.run(['git', '-C', str(self.src/alpha.CUTTLEFISH), '-c', 'user.name=t', '-c', 'user.email=t@t',
            'commit', '-q', '--allow-empty', '-m', 'moved'], check=True)
        (self.src/'device/google/cuttlefish/AndroidProducts.mk').write_text('PRODUCT_MAKEFILES := \\\n\taosp_cf_x86_64_phone:$(LOCAL_DIR)/x.mk\n')
        (self.src/'device/google/cuttlefish/build/cvd-host-package.go').unlink()
        self.data['build']['minFreeGiB'] = 10 ** 9
        self.write()
        with self.assertRaises(alpha.CheckFailed) as error:
            self.run_build()
        message = str(error.exception)
        for expected in ('is not checked out at the pinned', 'does not map aosp_cf_x86_64_only_phone',
                         'build/cvd-host-package.go is absent; the hosttar goal is unverified', 'below build.minFreeGiB'):
            self.assertIn(expected, message)
        self.assertEqual(self.made(), [])

    def test_lunch_must_resolve_to_the_recorded_target_inside_the_alpha_out_dir(self):
        self.env['FAKE_LUNCH_ACCEPT'] = 'something-else'
        with self.assertRaisesRegex(alpha.CheckFailed, 'did not resolve'):
            self.run_build()
        del self.env['FAKE_LUNCH_ACCEPT']
        self.env['FAKE_PRODUCT_OUT'] = 'out-rose-first-update-2026102701/target/product/vsoc_x86_64_only'
        with self.assertRaisesRegex(alpha.CheckFailed, 'PRODUCT_OUT=out-rose-first-update.* \\(outside out-rose-cf-alpha\\)'):
            self.run_build()
        self.env['FAKE_PRODUCT_OUT'] = 'out-rose-cf-alpha/target/product/other'
        with self.assertRaisesRegex(alpha.CheckFailed, 'not the recorded hostEvidence'):
            self.run_build()
        self.assertEqual(self.made(), [])
        self.pixel_untouched()

    def test_stale_missing_or_misidentified_outputs_are_not_a_build(self):
        out = self.src/LUNCH['PRODUCT_OUT']
        out.mkdir(parents=True)
        (out/'boot.img').write_text('older build')
        os.utime(out/'boot.img', (1, 1))
        self.env['FAKE_SKIP'] = 'boot'
        with self.assertRaisesRegex(alpha.CheckFailed, 'not written by this build: boot.img'):
            self.run_build(receipt='r1')
        del self.env['FAKE_SKIP']
        self.env['FAKE_INCREMENTAL'] = '2026102804'
        with self.assertRaisesRegex(alpha.CheckFailed, 'build.version.incremental=2026102804'):
            self.run_build(receipt='r2')
        del self.env['FAKE_INCREMENTAL']
        self.env['FAKE_TYPE'] = 'user'
        with self.assertRaisesRegex(alpha.CheckFailed, 'ro.build.type is not userdebug'):
            self.run_build(receipt='r3')
        del self.env['FAKE_TYPE']
        self.env['FAKE_MAKE_FAIL'] = '1'
        with self.assertRaises(subprocess.CalledProcessError):
            self.run_build(receipt='r4')
        for name in ('r1', 'r2', 'r3', 'r4'):
            self.assertFalse((self.root/name).exists(), 'no receipt for a failed build')

    def test_held_shared_lock_and_reused_receipt_directory_fail_before_make(self):
        self.assertEqual(alpha.load(self.config_path)['host']['lockPath'], str(self.lock))
        with open(self.lock, 'a') as held:
            fcntl.flock(held, fcntl.LOCK_EX)
            with self.assertRaisesRegex(alpha.CheckFailed, 'shared builder lock is held'):
                self.run_build()
        (self.root/'receipt').mkdir()
        with self.assertRaisesRegex(alpha.CheckFailed, 'already exists'):
            self.run_build()
        self.assertEqual(self.made(), [])

    def test_release_is_the_one_lunch_exports_not_get_build_var(self):
        # Actual tree output: get_build_var TARGET_RELEASE is empty while lunch exported cur; that resolves.
        env = alpha.build_env(alpha.load(self.config_path), 'rose-cf-alpha-1', TS)
        self.assertEqual(alpha.resolve(alpha.load(self.config_path), env)['TARGET_RELEASE'], 'cur')
        for change, message in ((('FAKE_RELEASE_UNSET', '1'), 'TARGET_RELEASE=\\(empty\\)'), (('FAKE_RELEASE', 'trunk_staging'), 'TARGET_RELEASE=trunk_staging')):
            self.env = {'FAKE_LOG': str(self.log), change[0]: change[1]}
            with self.assertRaisesRegex(alpha.CheckFailed, 'lunch resolved ' + message):
                self.run_build()
        self.assertEqual(self.made(), [])

    def test_host_package_goal_is_checked_against_the_pinned_soong_plugin(self):
        cf = self.src/alpha.CUTTLEFISH
        plugin, blueprint = cf/'build/cvd-host-package.go', cf/'build/Android.bp'
        pinned_plugin, pinned_blueprint = plugin.read_bytes(), blueprint.read_bytes()
        cases = (
            (lambda: plugin.write_bytes(pinned_plugin + b'// local edit\n'), ['differs from the pinned plugin']),
            (lambda: plugin.write_bytes(pinned_plugin.replace(b'(board_platform == "vsoc_x86_64")', b'(board_platform == "vsoc_x86_65")')),
                ['differs from the pinned plugin', 'only for other board platforms than vsoc_x86_64']),
            (lambda: plugin.write_bytes(pinned_plugin.replace(b'ctx.Phony("hosttar", info.TarballMetadata)', b'ctx.Phony("hostpkg", info.TarballMetadata)')),
                ['differs from the pinned plugin', 'does not declare the goal']),
            (lambda: plugin.write_bytes(pinned_plugin.replace(b'c.BaseModuleName()+".tar.gz", tarball)', b'"host.zip", tarball)')),
                ['does not declare the installed output']),
            (lambda: blueprint.write_bytes(pinned_blueprint.replace(b'name: "cvd-host_package"', b'name: "cvd-host_package_v2"')),
                ['does not declare cvd_host_package cvd-host_package']),
            (lambda: blueprint.unlink(), ['does not declare cvd_host_package cvd-host_package']),
        )
        for damage, messages in cases:
            damage()
            failures = alpha.tree_failures(alpha.load(self.config_path))
            for message in messages:
                self.assertTrue(any(message in f for f in failures), (message, failures))
            plugin.write_bytes(pinned_plugin)
            blueprint.write_bytes(pinned_blueprint)
        self.assertEqual(alpha.tree_failures(alpha.load(self.config_path)), [])
        self.env['FAKE_BOARD'] = 'vsoc_arm64'
        with self.assertRaisesRegex(alpha.CheckFailed, 'TARGET_BOARD_PLATFORM=vsoc_arm64'):
            self.run_build()
        self.assertEqual(self.made(), [])

    def test_probe_resolves_and_records_without_making(self):
        os.environ['FAKE_LOG'] = str(self.log)
        self.addCleanup(os.environ.pop, 'FAKE_LOG')
        receipt = alpha.probe(alpha.load(self.config_path), '40680000002', TS, self.root/'probe.json')
        self.assertEqual((receipt['executedMake'], receipt['treeFailures'], receipt['lunch']), (False, [], LUNCH))
        self.assertEqual(json.loads((self.root/'probe.json').read_text()), receipt)
        self.assertEqual(self.made(), [])
        with self.assertRaises(alpha.release_receipts.ReceiptExists):
            alpha.probe(alpha.load(self.config_path), '40680000002', TS, self.root/'probe.json')

class CheckedInAlphaTest(unittest.TestCase):
    def test_checked_in_config_fails_closed_naming_the_missing_evidence(self):
        result = subprocess.run([PY, str(SCRIPTS/'emulator-alpha.py'), 'preflight', '--job', 'guard'], capture_output=True, text=True)
        self.assertEqual(result.returncode, 4)
        self.assertEqual(json.loads(result.stderr)['missing'], ['hostEvidence.probeReceiptSha256', 'hostEvidence.lunch'])
        build = subprocess.run([PY, str(SCRIPTS/'emulator-alpha.py'), 'build', '--candidate-sha', BOUND, '--apps', json.dumps(APPS),
            '--run-id', '1', '--build-timestamp', TS, '--receipt-dir', '/nonexistent/alpha-receipt'], capture_output=True, text=True)
        self.assertEqual(build.returncode, 4, 'a dispatch cannot compile speculatively')

    def test_source_binding_is_the_lead_evidenced_2804_integration(self):
        binding = CHECKED_IN['sourceBinding']
        self.assertEqual({k: binding[k] for k in ('candidate', 'reconcileReceipt', 'reconcileReceiptSha256', 'apps')},
            {'candidate': BOUND, 'reconcileReceipt': '0004-2026102804.json',
             'reconcileReceiptSha256': 'db1cc9a215f92a09d772c01efc15590b37fd6e748f97893908838340c9da9de0', 'apps': APPS})
        # The documented order is the 2804 dispatch contract's.
        self.assertIn("APPS = [['dev.pixel.voice', 302], ['dev.pixel.calculator.agent', 4], ['dev.pixel.backgroundcontrol', 28]]",
            (SCRIPTS/'tests/test_release_candidate_contract.py').read_text())
        self.assertEqual((CHECKED_IN['hostEvidence']['probeReceiptSha256'], CHECKED_IN['hostEvidence']['lunch']), (None, None))
        config = alpha.load()
        with self.assertRaisesRegex(alpha.CheckFailed, 'bound to ae9cfd4d'):
            alpha.check_dispatch(config, SHA, APPS)
        with self.assertRaisesRegex(alpha.CheckFailed, 'differ from the bound'):
            alpha.check_dispatch(config, BOUND, list(reversed(APPS)))
        alpha.check_dispatch(config, BOUND, APPS)

    def test_host_package_pin_matches_the_pinned_upstream_fixture(self):
        package = CHECKED_IN['hostPackage']
        self.assertEqual(hashlib.sha256((FIXTURES/package['plugin']).read_bytes()).hexdigest(), package['pluginSha256'])
        self.assertEqual((package['moduleType'], package['module'], package['goal'], package['output'], package['boardPlatform']),
            ('cvd_host_package', 'cvd-host_package', 'hosttar', 'cvd-host_package.tar.gz', 'vsoc_x86_64'))
        self.assertIn(package['goal'], CHECKED_IN['build']['makeGoals'])
        readme = (FIXTURES/'README.md').read_text()
        self.assertIn(alpha.pinned_revision('device/google/cuttlefish'), readme)
        for name in ('Android.bp', 'cvd-host-package.go'):
            self.assertIn(hashlib.sha256((FIXTURES/'build'/name).read_bytes()).hexdigest(), readme)
            self.assertIn('Licensed under the Apache License', (FIXTURES/'build'/name).read_text())

    def test_target_out_dir_and_lock_are_the_cuttlefish_alpha_ones(self):
        config = alpha.load()
        self.assertEqual(config['target']['lunch'], 'aosp_cf_x86_64_only_phone-cur-userdebug')
        self.assertEqual(alpha.config_failures(config), [])
        self.assertEqual(config['host']['lockPath'], FRANKEL['host']['lockPath'])
        self.assertEqual(config['host']['sourceRoot'], FRANKEL['host']['sourceRoot'])
        self.assertNotEqual(config['build']['outDir'], FRANKEL['host']['outDir'])
        self.assertIn('device/google/cuttlefish', alpha.CUTTLEFISH)
        self.assertEqual(alpha.pinned_revision('device/google/cuttlefish'), '283645aacf6cdb56cc31a9362f54d412dbc132a1')

    def test_unsafe_targets_are_config_failures(self):
        for change, message in ((('build', 'outDir', FRANKEL['host']['outDir']), 'disjoint'), (('build', 'outDir', 'out'), 'plain directory'),
                                (('build', 'outDir', 'a/b'), 'plain directory'), (('target', 'variant', 'user'), 'userdebug test-keys'),
                                (('target', 'lunch', 'frankel-cur-user'), 'does not match'),
                                (('hostPackage', 'output', 'cvd-host_package.zip'), 'module name plus .tar.gz'),
                                (('build', 'makeGoals', ['droid']), 'must include the hostPackage goal')):
            config = alpha.load()
            config[change[0]][change[1]] = change[2]
            self.assertTrue(any(message in f for f in alpha.config_failures(config)), change)

    def test_plan_runs_nothing_and_says_so(self):
        original = subprocess.run
        subprocess.run = lambda *a, **k: (_ for _ in ()).throw(AssertionError('plan ran a command'))
        try:
            result = alpha.plan(alpha.load(), SHA, '40680000003', TS)
        finally:
            subprocess.run = original
        self.assertEqual((result['executed'], result['receiptType'], result['emulatorBuildId']),
            (False, 'emulator-alpha-plan', 'rose-cf-alpha-40680000003'))
        self.assertEqual(result['commands'][1][-1],
            'source build/envsetup.sh && lunch aosp_cf_x86_64_only_phone-cur-userdebug && m -j48 droid hosttar')
        self.assertEqual(result['environment'], {'OUT_DIR': 'out-rose-cf-alpha', 'BUILD_NUMBER': 'rose-cf-alpha-40680000003', 'BUILD_DATETIME': TS})
        self.assertTrue(result['missingEvidence'])

    def test_adapter_never_reconciles_signs_publishes_cleans_or_uses_the_reference_scripts(self):
        code = (SCRIPTS/'emulator-alpha.py').read_text().split('"""', 2)[2]
        for absent in ('host-reconcile', 'host-integrate', 'host-sign', 'gcloud', 'secret', 'apply-os-changes', 'installclean',
                       'clobber', 'rmtree', 'unlink', 'sdk_phone64', 'reference-scripts', 'github_release', 'publish(', 'PIXEL_'):
            self.assertFalse(absent in code, absent)

class WorkflowAlphaTest(unittest.TestCase):
    def setUp(self):
        self.jobs = jobs(WORKFLOW)

    def test_alpha_builder_has_no_identity_secrets_or_os_stages(self):
        job = self.jobs['alpha-builder']
        self.assertIn("    needs: controller\n    if: inputs.mode == 'emulator-alpha'\n    runs-on: [self-hosted, roseos-frankel-builder]\n", job)
        self.assertIn('    permissions:\n      contents: read\n    steps:\n', job)
        runs = re.findall(r'^      (?:- |  )run: (.+)$', job, re.M)
        self.assertEqual(runs, ['python3 scripts/emulator-alpha.py preflight --job builder', '|',
            'python3 scripts/emulator-alpha.py build --candidate-sha "$CANDIDATE_SHA" --apps "$APPS" --run-id "$GITHUB_RUN_ID" '
            '--build-timestamp "$BUILD_TIMESTAMP" --receipt-dir "$ROSE_BASE/receipt"', 'test -z "${ROSE_BASE:-}" || rm -rf "$ROSE_BASE"'])
        for absent in ('id-token', 'secrets.', 'GCP_', 'actions-gcp-login', 'os-release-stage', 'host-app-release', 'PIXEL_OTA'):
            self.assertNotIn(absent, job)
        self.assertIn('          name: emulator-alpha-receipt-${{ github.run_id }}\n          path: ${{ env.ROSE_BASE }}/receipt\n', job)

    def test_guard_checks_alpha_evidence_before_the_builder_starts(self):
        guard = self.jobs['guard']
        self.assertIn("      - name: Emulator-alpha preflight\n        if: inputs.mode == 'emulator-alpha'\n"
            "        run: python3 scripts/emulator-alpha.py preflight --job guard\n", guard)
        self.assertIn('[[ "$MODE" == os-release || ( "$RESUME_STAGE" == none && -z "$RESUME_SOURCE_RUN" && "$PROMOTE" == false ) ]]', guard)
        self.assertIn('    needs: guard\n', self.jobs['controller'])
        self.assertIn('    needs: [controller, builder, app-builder, alpha-builder, publisher]\n', self.jobs['cleanup'])
        self.assertIn("    if: inputs.mode == 'os-release'\n", self.jobs['builder'])
        self.assertIn('    needs: builder\n', self.jobs['publisher'])

if __name__ == '__main__':
    unittest.main()
