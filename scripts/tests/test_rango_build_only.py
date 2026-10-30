"""rango-build-only.py (OS-RANGO-BUILD-001/003) against a synthetic source tree: no real tree, download, generation,
build, lock contention or signer. Commands are recorded by a fake runner instead of executed."""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('rango_build_only', SCRIPTS/'rango-build-only.py')
rb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rb)

BUILD = '2026102999'
GiB = 1 << 30

class FakeFs:
    def __init__(self, available):
        self.f_bavail, self.f_frsize = available // 4096, 4096

class Runner:
    def __init__(self, returncode=0):
        self.calls, self.returncode = [], returncode
    def __call__(self, argv, cwd, env):
        self.calls.append((argv, cwd, {k: env[k] for k in ('ADEVTOOL_IMG_DOWNLOAD_DIR', 'OUT_DIR', 'BUILD_NUMBER') if k in env}))
        return type('R', (), {'returncode': self.returncode})()

class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='rango-'))
        self.addCleanup(__import__('shutil').rmtree, self.tmp, True)
        self.src = self.tmp/'src'
        for d in ('vendor/adevtool/bin', 'prebuilts/build-tools/path/linux-x86', 'prebuilts/jdk/jdk21/linux-x86/bin',
                'out_adevtool_deps/host/linux-x86/bin', 'vendor/google_devices/frankel'):
            (self.src/d).mkdir(parents=True)
        (self.src/'vendor/adevtool/bin/run').write_text('#!/usr/bin/env node\n')
        self.config = copy.deepcopy(rb.load())
        (self.src/'out_adevtool_deps/adevtool_revision').write_text(self.config['adevtool']['revision'])
        for tool in self.config['adevtool']['depsTools']:
            (self.src/'out_adevtool_deps/host/linux-x86/bin'/tool).write_text('')
        (self.tmp/'reconcile').mkdir()
        self.config['host'].update(sourceRoot=str(self.src), lockPath=str(self.tmp/'build.lock'),
            reconcileReceiptDir=str(self.tmp/'reconcile'))
        self.config['capacity'].update(generateAllowanceBytes=40 * GiB, compileAllowanceBytes=250 * GiB)
        self.dl = self.tmp/'dl'
        self.fs = FakeFs(600 * GiB)

    def check(self, stage='generate', config=None, build=BUILD, dl=None, locked=False, inputs=None):
        return rb.preflight(config or self.config, stage, build, dl or self.dl, statvfs=lambda p: self.fs,
            lock_probe=lambda p: not locked, inputs_receipt=inputs or self.tmp/'inputs/local-inputs.json')

    def integrated(self, label='rango-' + BUILD, apks=None, patcher=None):
        """The state host-reconcile.py apply leaves with rango-reconcile.json: the verified APKs and policy pins in the
        tree, an immutable receipt naming them, and the local-inputs.json that verified them."""
        inputs = self.tmp/'inputs'
        (inputs/'signed-apps').mkdir(parents=True, exist_ok=True)
        apps, entries = [], {}
        for role, package in (('agent', 'dev.pixel.backgroundcontrol'), ('voice', 'dev.pixel.voice'),
                ('target', 'dev.pixel.calculator.agent')):
            data = (apks or {}).get(package, f'signed {package}'.encode())
            signed = inputs/'signed-apps'/f'{package}.apk'
            signed.write_bytes(data)
            digest = hashlib.sha256(data).hexdigest()
            apps.append({'role': role, 'package': package, 'sha256': digest, 'signedPath': str(signed)})
            tree = rb.INSTALLED['agent'] if role == 'agent' else \
                rb.INSTALLED['preinstall'].format(module=package.replace('.', '_'))
            (self.src/tree).parent.mkdir(parents=True, exist_ok=True)
            (self.src/tree).write_bytes(data)
            entries[tree] = {'before': None, 'after': digest, 'action': 'installed'}
        cert = 'AB' * 32
        policy = {'agent': {'packageName': 'dev.pixel.backgroundcontrol', 'certificates': [cert]},
            'targets': [{'packageName': 'dev.pixel.calculator.agent', 'certificates': [cert]}]}
        (inputs/'background-policy.json').write_text(json.dumps(policy))
        xml = self.src/rb.POLICY_CONFIG
        xml.parent.mkdir(parents=True, exist_ok=True)
        xml.write_text(''.join(f'<item>{row["packageName"]}:{cert}</item>\n' for row in [policy['agent'], *policy['targets']]))
        entries[rb.POLICY_CONFIG] = {'before': 'aa', 'after': hashlib.sha256(xml.read_bytes()).hexdigest(), 'action': 'installed'}
        (inputs/'local-inputs.json').write_text(json.dumps({'receiptType': 'rango-local-inputs', 'apps': apps,
            'release': {'tag': 'frankel-2026102901'}, 'product': {'commit': '02b21bc6'},
            'policy': {'path': str(inputs/'background-policy.json'),
                'sha256': hashlib.sha256((inputs/'background-policy.json').read_bytes()).hexdigest()}}))
        record = {'receiptType': 'reconcile', 'label': label, 'changed': sorted(entries), 'entries': entries,
            'patcher': patcher or {'patcher': 'trusted', 'sha256': {n: rb.sha256_file(SCRIPTS/n) for n in rb.PATCHER}}}
        receipts = sorted((self.tmp/'reconcile').glob('*.json'))
        (self.tmp/'reconcile'/f'{len(receipts) + 1:04d}-{label}.json').write_text(json.dumps(record))
        return inputs/'local-inputs.json'

    def generated(self):
        vendor = self.src/'vendor/google_devices/rango'
        vendor.mkdir(parents=True)
        (vendor/'rango.mk').write_text('PRODUCT_NAME := rango\n')
        (vendor/'adevtool-version-check.mk').write_text(f'ifneq ($(shell git -C vendor/adevtool rev-parse HEAD),{self.config["adevtool"]["revision"]})\n')

class ConfigTest(Fixture):
    def test_the_shipped_config_matches_the_device_contract(self):
        config = rb.load()
        self.assertEqual((config['device'], config['lunch']), ('rango', 'rango-cur-user'))
        self.assertEqual(config['capacity']['reserveBytes'], 322122547200)
        self.assertIsNone(config['capacity']['generateAllowanceBytes'], 'no allowance is invented')
        self.assertIsNone(config['factory']['sizeBytes'])

    def test_wrong_device_pin_or_hash_is_invalid(self):
        for mutate, expect in ((lambda c: c.update(device='frankel'), 'rango only'),
                (lambda c: c.update(lunch='rango-trunk_staging-userdebug'), 'lunch'),
                (lambda c: c['factory'].update(sha256='0' * 64), 'factory image'),
                (lambda c: c['factory'].update(url='https://example.com/' + c['factory']['file']), 'index URL'),
                (lambda c: c['adevtool'].update(revision='0' * 40), 'adevtool revision'),
                (lambda c: c['host'].update(patches=['os/fold-port/product-tray.patch']), 'fold-port')):
            config = json.loads((rb.CONFIG).read_text())
            mutate(config)
            path = self.tmp/'bad.json'
            path.write_text(json.dumps(config))
            with self.subTest(expect=expect), self.assertRaises(rb.InvalidInput) as caught:
                rb.load(path)
            self.assertIn(expect, str(caught.exception))

class IsolatedConfigTest(unittest.TestCase):
    def test_the_isolated_config_binds_the_same_target_and_shares_nothing_with_frankel(self):
        shared = rb.load()
        isolated = rb.load(SCRIPTS.parent/'os/build-only/rango-isolated.json')
        frankel = json.loads((SCRIPTS.parent/'os/release-pipeline/frankel.json').read_text())['host']
        for key in ('device', 'lunch', 'factory', 'adevtool', 'deviceContract'):
            self.assertEqual(isolated[key], shared[key], key)
        host = isolated['host']
        self.assertEqual(host['makeGoals'], frankel['makeGoals'])
        self.assertEqual(host['makeJobs'], 32)
        for key in ('sourceRoot', 'lockPath'):
            self.assertNotEqual(host[key], frankel[key], key)
        self.assertNotEqual(host['reconcileReceiptDir'], frankel['integrate']['reconcile']['receiptDir'])
        for path in (host['sourceRoot'], host['lockPath'], host['reconcileReceiptDir']):
            self.assertTrue(path.startswith('/srv/rose-rango/'), path)
        self.assertIsNone(isolated['capacity']['generateAllowanceBytes'])
        self.assertIsNone(isolated['capacity']['compileAllowanceBytes'])
        self.assertIs(isolated['capacity']['allowanceRequired'], False)
        self.assertEqual(isolated['capacity']['reserveBytes'], 322122547200)
        self.assertNotIn('allowanceRequired', shared['capacity'], 'the shared Frankel-builder route keeps its gate')
        self.assertEqual(rb.commands(isolated, BUILD, '/srv/rose-rango/dl')['compile']['argv'][2],
            'source build/envsetup.sh && lunch rango-cur-user && m -j32 vendorbootimage vendorkernelbootimage '
            'target-files-package otatools-package')

    def test_reserve_only_is_refused_for_a_config_sharing_the_frankel_builder(self):
        config = json.loads((SCRIPTS.parent/'os/build-only/rango-isolated.json').read_text())
        config['host']['lockPath'] = rb.load()['host']['lockPath']
        path = Path(tempfile.mkdtemp(prefix='rango-cfg-'))/'shared.json'
        self.addCleanup(__import__('shutil').rmtree, path.parent, True)
        path.write_text(json.dumps(config))
        with self.assertRaises(rb.InvalidInput) as caught:
            rb.load(path)
        self.assertIn("shared with Frankel: ['lockPath']", str(caught.exception))
        config = json.loads((SCRIPTS.parent/'os/build-only/rango-isolated.json').read_text())
        config['capacity']['allowanceRequired'] = 0
        path.write_text(json.dumps(config))
        with self.assertRaises(rb.InvalidInput):
            rb.load(path)

class CommandTest(Fixture):
    def test_commands_are_rango_scoped_and_unsigned(self):
        cmds = rb.commands(self.config, BUILD, self.dl)
        self.assertEqual(cmds['generate']['argv'], ['vendor/adevtool/bin/run', 'generate-all', '-d', 'rango'])
        self.assertEqual(cmds['generate']['env'], {'ADEVTOOL_IMG_DOWNLOAD_DIR': str(self.dl)})
        self.assertEqual(cmds['compile']['env'], {'OUT_DIR': 'out-rango-' + BUILD, 'BUILD_NUMBER': BUILD})
        self.assertEqual(cmds['compile']['argv'][2], 'source build/envsetup.sh && lunch rango-cur-user && '
            'm -j48 vendorbootimage vendorkernelbootimage target-files-package otatools-package')
        text = json.dumps(cmds)
        for banned in ('sign', 'secret', 'publish', 'frankel', 'all'):
            self.assertNotIn(banned, text.replace('generate-all', ''), banned)

    def test_the_module_cannot_reach_signing_secrets_or_publication(self):
        code = (SCRIPTS/'rango-build-only.py').read_text()
        for banned in ('host_secrets', 'host-sign', 'github_release_publisher', 'gcloud', 'secretmanager', 'release-pipeline',
                'fold-port/'):
            self.assertNotIn(banned, code, banned)

    def test_bad_build_number_is_invalid(self):
        for build in ('123', 'main', '0123456789', None):
            with self.subTest(build=build), self.assertRaises(rb.InvalidInput):
                rb.commands(self.config, build, self.dl)

class PreflightTest(Fixture):
    def test_ready_generate_then_compile(self):
        report = self.check('generate')
        self.assertEqual(report['problems'], [], 'generation needs no Rose integration receipt')
        self.assertNotIn('roseIntegration', report['facts'])
        self.assertTrue(report['facts']['frankelVendorPresent'])
        self.assertIn('rango.mk is missing', ' '.join(self.check('compile')['problems']))
        self.generated()
        self.assertIn('no Rose integration (reconcile) receipt', ' '.join(self.check('compile')['problems']))
        inputs = self.integrated()
        report = self.check('compile', inputs=inputs)
        self.assertEqual(report['problems'], [])
        self.assertEqual(report['facts']['roseIntegration']['receipt'], f'0001-rango-{BUILD}.json')
        self.assertEqual(report['facts']['roseIntegration']['inputs']['release'], {'tag': 'frankel-2026102901'})
        self.assertIn('already exists; generate-all would rm -rf it', ' '.join(self.check('generate')['problems']))

    def test_unsafe_paths(self):
        with self.assertRaises(rb.InvalidInput):
            self.check(dl=Path('relative/dl'))
        self.assertIn('outside the source tree', ' '.join(self.check(dl=self.src/'dl')['problems']))
        self.dl.mkdir()
        (self.dl/'leftover.tmp').write_text('x')
        self.assertIn('absent or an empty directory', ' '.join(self.check()['problems']))
        (self.src/('out-rango-' + BUILD)).mkdir()
        self.assertIn('needs a new OUT_DIR', ' '.join(self.check()['problems']))

    def test_missing_tools_vendor_inputs_or_integration(self):
        (self.src/'out_adevtool_deps/adevtool_revision').write_text('0' * 40)
        (self.src/'out_adevtool_deps/host/linux-x86/bin/debugfs').unlink()
        (self.src/'vendor/adevtool/bin/run').unlink()
        problems = ' '.join(self.check()['problems'])
        for expect in ('Soong host-tool build', "missing: ['debugfs']", 'vendor/adevtool/bin/run is missing'):
            self.assertIn(expect, problems)
        self.assertNotIn('reconcile', problems)
        self.generated()
        (self.src/'vendor/google_devices/rango/adevtool-version-check.mk').write_text('other revision')
        self.assertIn('not from the pinned adevtool revision', ' '.join(self.check('compile')['problems']))

    def test_capacity_and_lock(self):
        self.fs = FakeFs(322122547200 + 40 * GiB - 1)
        self.assertIn('below the reserve', ' '.join(self.check()['problems']))
        self.fs = FakeFs(322122547200 + 40 * GiB)
        self.assertEqual(self.check()['problems'], [])
        unmeasured = copy.deepcopy(self.config)
        unmeasured['capacity']['compileAllowanceBytes'] = None
        self.generated()
        self.assertIn('unmeasured', ' '.join(self.check('compile', config=unmeasured)['problems']))
        self.assertIn('lock is held', ' '.join(self.check(locked=True)['problems']))

    def test_isolated_reserve_only_without_inventing_an_allowance(self):
        isolated = rb.load(SCRIPTS.parent/'os/build-only/rango-isolated.json')
        isolated['host'].update({k: self.config['host'][k] for k in ('sourceRoot', 'lockPath', 'reconcileReceiptDir')})
        self.fs = FakeFs(322122547200)
        report = self.check(config=isolated)
        self.assertEqual(report['problems'], [])
        self.assertEqual(report['facts']['capacity'], {'availableBytes': 322122547200, 'reserveBytes': 322122547200,
            'allowanceBytes': None, 'allowanceRequired': False,
            'allowance': 'unmeasured; reserve-only observation on the isolated host'})
        self.fs = FakeFs(322122547200 - 4096)
        self.assertIn('is below the reserve', ' '.join(self.check(config=isolated)['problems']))
        self.fs = FakeFs(600 * GiB)
        self.assertIn('lock is held', ' '.join(self.check(config=isolated, locked=True)['problems']))
        self.assertIn('outside the source tree', ' '.join(self.check(config=isolated, dl=self.src/'dl')['problems']))
        measured = copy.deepcopy(isolated)
        measured['capacity']['generateAllowanceBytes'] = 301 * GiB
        self.assertIn('minus allowance', ' '.join(self.check(config=measured)['problems']), 'a measured allowance still counts')
        self.generated()
        self.assertIn('already exists; generate-all would rm -rf it', ' '.join(self.check(config=isolated)['problems']))
        self.assertIn('no Rose integration', ' '.join(self.check('compile', config=isolated)['problems']))

class CompileIntegrationTest(Fixture):
    def setUp(self):
        super().setUp()
        self.generated()

    def problems(self, inputs):
        return ' '.join(self.check('compile', inputs=inputs)['problems'])

    def test_absent_inputs_receipt_or_adopt_only_receipt_is_refused(self):
        self.integrated()
        self.assertIn('compile needs --inputs-receipt', self.problems(self.tmp/'missing.json'))
        (self.tmp/'reconcile/0002-adopt.json').write_text(json.dumps({'receiptType': 'reconcile-adopt', 'entries': {},
            'patcher': {'patcher': 'trusted', 'sha256': {n: rb.sha256_file(SCRIPTS/n) for n in rb.PATCHER}}}))
        self.assertIn("'reconcile-adopt' receipt", self.problems(self.tmp/'inputs/local-inputs.json'))

    def test_wrong_tree_is_refused(self):
        inputs = self.integrated()
        (self.src/rb.INSTALLED['agent']).write_bytes(b'owner rebuilt this')
        problems = self.problems(inputs)
        self.assertIn('no longer matches', problems)
        self.assertIn(rb.INSTALLED['agent'], problems)

    def test_copied_frankel_receipt_with_other_apks_is_refused(self):
        inputs = self.integrated()
        # The Frankel builder's receipt names its own (2701) APK bytes, and the tree matches that receipt.
        frankel = json.loads((self.tmp/'inputs/local-inputs.json').read_text())
        self.integrated(label='frankel-2026102701', apks={'dev.pixel.voice': b'frankel 2701 voice 298'})
        (self.tmp/'inputs/local-inputs.json').write_text(json.dumps(frankel))
        (self.tmp/'inputs/signed-apps/dev.pixel.voice.apk').write_bytes(b'signed dev.pixel.voice')
        problems = self.problems(inputs)
        self.assertIn('did not install the verified bytes at packages/apps/RosePreinstall/RosePreinstall_dev_pixel_voice/app.apk',
            problems)

    def test_receipt_linked_from_another_builder_is_refused(self):
        inputs = self.integrated()
        elsewhere = self.tmp/'frankel-reconcile'
        elsewhere.mkdir()
        receipt = next((self.tmp/'reconcile').glob('*.json'))
        receipt.rename(elsewhere/receipt.name)
        (self.tmp/'reconcile'/receipt.name).symlink_to(elsewhere/receipt.name)
        self.assertIn('is a link out of', self.problems(inputs))

    def test_wrong_inputs_policy_or_recipe_is_refused(self):
        inputs = self.integrated()
        (self.tmp/'inputs/signed-apps/dev.pixel.calculator.agent.apk').write_bytes(b'rebuilt calculator')
        self.assertIn('dev.pixel.calculator.agent: signed APK bytes differ', self.problems(inputs))
        inputs = self.integrated()
        (self.tmp/'inputs/background-policy.json').write_text('{}')
        self.assertIn('background policy differs', self.problems(inputs))
        inputs = self.integrated()
        (self.src/rb.POLICY_CONFIG).write_text('<item>dev.pixel.backgroundcontrol:' + 'CD' * 32 + '</item>\n')
        self.assertIn('does not carry the local policy pin', self.problems(inputs))
        inputs = self.integrated(patcher={'patcher': 'candidate', 'sha256': {'apply-os-changes.py': '0' * 64}})
        self.assertIn('not this checkout', self.problems(inputs))
        (self.tmp/'inputs/local-inputs.json').write_text(json.dumps({'receiptType': 'reconcile'}))
        self.assertIn('not a rango-local-inputs receipt', self.problems(inputs))

class RunTest(Fixture):
    def test_run_records_preflight_start_and_completion_and_runs_one_command(self):
        runner = Runner()
        code = rb.run(self.config, 'generate', BUILD, self.dl, self.tmp/'receipt', runner=runner,
            statvfs=lambda p: self.fs, lock_probe=lambda p: True)
        self.assertEqual(code, 0)
        self.assertEqual(runner.calls, [(['vendor/adevtool/bin/run', 'generate-all', '-d', 'rango'], str(self.src),
            {'ADEVTOOL_IMG_DOWNLOAD_DIR': str(self.dl)})])
        started = json.loads((self.tmp/'receipt/started.json').read_text())
        completed = json.loads((self.tmp/'receipt/completed.json').read_text())
        self.assertEqual((started['signed'], completed['returncode'], completed['releaseReady']), (False, 0, False))
        with self.assertRaises(rb.InvalidInput):
            rb.run(self.config, 'generate', BUILD, self.dl, self.tmp/'receipt', runner=runner)

    def test_not_ready_or_failing_command_never_succeeds(self):
        runner = Runner()
        code = rb.run(self.config, 'compile', BUILD, self.dl, self.tmp/'r1', runner=runner,
            statvfs=lambda p: self.fs, lock_probe=lambda p: True, inputs_receipt=self.integrated())
        self.assertEqual((code, runner.calls), (1, []), 'compile before generation runs nothing')
        self.assertFalse((self.tmp/'r1/started.json').exists())
        self.assertFalse(json.loads((self.tmp/'r1/preflight.json').read_text())['ready'])
        failing = Runner(returncode=2)
        self.assertEqual(rb.run(self.config, 'generate', BUILD, self.dl, self.tmp/'r2', runner=failing,
            statvfs=lambda p: self.fs, lock_probe=lambda p: True), 1)
        self.assertEqual(json.loads((self.tmp/'r2/completed.json').read_text())['returncode'], 2)

    def test_cli_rejects_missing_stage_and_plan_runs_nothing(self):
        self.assertEqual(rb.main(['preflight', '--build-number', BUILD, '--download-dir', str(self.dl)]), 2)
        self.assertEqual(rb.main(['run', '--stage', 'compile', '--build-number', BUILD, '--download-dir', str(self.dl)]), 2)

if __name__ == '__main__':
    unittest.main()
