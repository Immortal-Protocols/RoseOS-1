"""rango-build-only.py (OS-RANGO-BUILD-001) against a synthetic source tree: no real tree, download, generation, build,
lock contention or signer. Commands are recorded by a fake runner instead of executed."""
import copy
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
        (self.tmp/'reconcile/0007-integrate.json').write_text('{"entries": {}}')
        self.config['host'].update(sourceRoot=str(self.src), lockPath=str(self.tmp/'build.lock'),
            reconcileReceiptDir=str(self.tmp/'reconcile'))
        self.config['capacity'].update(generateAllowanceBytes=40 * GiB, compileAllowanceBytes=250 * GiB)
        self.dl = self.tmp/'dl'
        self.fs = FakeFs(600 * GiB)

    def check(self, stage='generate', config=None, build=BUILD, dl=None, locked=False):
        return rb.preflight(config or self.config, stage, build, dl or self.dl, statvfs=lambda p: self.fs,
            lock_probe=lambda p: not locked)

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
        self.assertEqual(report['problems'], [])
        self.assertEqual(report['facts']['roseIntegration']['receipt'], '0007-integrate.json')
        self.assertTrue(report['facts']['frankelVendorPresent'])
        self.assertIn('rango.mk is missing', ' '.join(self.check('compile')['problems']))
        self.generated()
        self.assertEqual(self.check('compile')['problems'], [])
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
        (self.tmp/'reconcile/0007-integrate.json').unlink()
        problems = ' '.join(self.check()['problems'])
        for expect in ('Soong host-tool build', "missing: ['debugfs']", 'vendor/adevtool/bin/run is missing', 'reconcile'):
            self.assertIn(expect, problems)
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
            statvfs=lambda p: self.fs, lock_probe=lambda p: True)
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
