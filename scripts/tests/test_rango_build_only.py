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

OLD_BOOT = '0b1d2c3e-4f50-4a6b-8c7d-9e0f1a2b3c4d'
NEW_BOOT = '7a8b9c0d-1e2f-4a3b-9c4d-5e6f7a8b9c0d'
THIRD_BOOT = 'c0ffee00-1234-4abc-8def-0123456789ab'
MANIFEST = ('<?xml version="1.0" encoding="UTF-8"?>\n<manifest>\n'
    '  <project name="platform/build" path="build/make" revision="aaaa111" />\n'
    '  <project name="platform/frameworks/base" path="frameworks/base" revision="cccc222" />\n</manifest>\n')

class ProcGuard:
    """Fails the test on any open, readlink or listing of a per-process /proc path (cmdline, environ, fd, cwd, ...)."""
    ALLOWED = ('/proc/sys/kernel/random/boot_id', '/proc/stat')
    def check(self, path):
        path = os.fspath(path) if not isinstance(path, int) else ''
        path = path.decode() if isinstance(path, bytes) else path
        if path.startswith('/proc') and path not in self.ALLOWED:
            raise AssertionError(f'process file read: {path}')
    def start(self):
        import builtins
        from unittest import mock
        def wrap(real):
            def guarded(path, *a, **kw):
                self.check(path)
                return real(path, *a, **kw)
            return guarded
        self.patches = [mock.patch.object(builtins, 'open', wrap(builtins.open)),
            mock.patch.object(os, 'readlink', wrap(os.readlink)), mock.patch.object(os, 'listdir', wrap(os.listdir)),
            mock.patch.object(os, 'scandir', wrap(os.scandir))]
        for patch in self.patches:
            patch.start()
    def stop(self):
        for patch in self.patches:
            patch.stop()

class Killed(Exception):
    """The supervisor stopping the build: the parent dies before it can write completed.json."""

class CompilingRunner(Runner):
    """Records the call, writes into OUT_DIR like a build would, then either returns or is killed."""
    def __init__(self, src, returncode=0, killed=False):
        super().__init__(returncode)
        self.src, self.killed = src, killed
    def __call__(self, argv, cwd, env):
        super().__call__(argv, cwd, env)
        out = Path(cwd)/env['OUT_DIR']
        out.mkdir(exist_ok=True)
        with open(out/'build.log', 'a') as log:
            log.write(f'call {len(self.calls)}\n')
        if self.killed:
            raise Killed()
        return type('R', (), {'returncode': self.returncode})()

class ResumeTest(Fixture):
    """OS-RANGO-RESUME-005(-F1), all synthetic: a fake compile is killed after writing into OUT_DIR; the cutoff proof,
    supplemental provenance, boot witness, current boot and stop evidence are synthetic stand-ins for operator data.
    Every test runs with process-file reads trapped (see ProcGuard): no /proc/<pid> path may be opened or listed."""
    def setUp(self):
        super().setUp()
        self.generated()
        self.inputs = self.integrated()
        self.config_path = self.tmp/'rango-isolated.json'
        self.config_path.write_text(json.dumps(self.config))
        self.prior = self.tmp/'receipts'/(BUILD + '-compile')
        with self.assertRaises(Killed):
            rb.run(self.config, 'compile', BUILD, self.dl, self.prior, runner=CompilingRunner(self.src, killed=True),
                statvfs=lambda p: self.fs, lock_probe=lambda p: True, inputs_receipt=self.inputs, now=lambda: 1793330947)
        self.out = self.src/('out-rango-' + BUILD)
        (self.out/'soong').mkdir()
        (self.out/'soong/.cache-sentinel').write_bytes(b'\x00incremental cache\xff')
        (self.out/'soong/host-link').symlink_to('/usr/bin')  # build-internal links are not walked or refused
        self.boot = {'bootId': NEW_BOOT, 'bootTime': '2026-10-30T04:35:00Z'}
        self.evidence = self.tmp/'evidence/supervisor.log'
        self.evidence.parent.mkdir()
        self.evidence.write_text('02:49:07 compile started\n04:29:15 supervisor deadline reached; stopping build\n')
        self.cut = self.cutoff()
        (self.tmp/'evidence/manifest-at-start.xml').write_text(MANIFEST)
        (self.tmp/'evidence/manifest-now.xml').write_text(MANIFEST.replace('" />', '"/>'))  # other bytes, same pins
        self.prov = self.provenance()
        self.witness = self.boot_witness()
        guard = ProcGuard()
        guard.start()
        self.addCleanup(guard.stop)

    def boot_witness(self, name='witness.json', **changes):
        doc = {'receiptType': 'rango-compile-boot-witness', 'schemaVersion': 1,
            'attempt': {'receiptDir': str(self.prior), 'startedSha256': rb.sha256_file(self.prior/'started.json')},
            'buildNumber': BUILD, 'bootId': OLD_BOOT, 'observedAt': '2026-10-30T03:55:00Z',
            'observedBy': 'synthetic operator', 'method': 'cat /proc/sys/kernel/random/boot_id while the compile ran'}
        doc.update(changes)
        (self.tmp/name).write_text(json.dumps(doc))
        return self.tmp/name

    def cutoff(self, attempt=None, name='cut.json', **changes):
        attempt = attempt or self.prior
        proof = {'receiptType': 'rango-compile-cutoff', 'schemaVersion': 1,
            'prior': {'receiptDir': str(attempt), 'startedSha256': rb.sha256_file(attempt/'started.json'),
                'preflightSha256': rb.sha256_file(attempt/'preflight.json')},
            'build': {'buildNumber': BUILD, 'outDir': 'out-rango-' + BUILD}, 'cause': 'supervisor-deadline',
            'stoppedAt': '2026-10-30T04:29:15Z', 'observedAt': '2026-10-30T04:31:00Z',
            'evidence': [{'path': str(self.evidence), 'sha256': rb.sha256_file(self.evidence),
                'line': '04:29:15 supervisor deadline reached; stopping build'}],
            'observedBy': 'synthetic operator'}
        proof.update(changes)
        (self.tmp/name).write_text(json.dumps(proof))
        return self.tmp/name

    def provenance(self, **changes):
        doc = {'receiptType': 'rango-compile-provenance', 'schemaVersion': 1,
            'prior': {'startedSha256': rb.sha256_file(self.prior/'started.json')},
            'configSha256': rb.sha256_file(self.config_path),
            'recipe': {'base': '1b4c8419a406ce2f0c402245f8f298c669e864bd',
                'overlays': ['bc855f1a178d11f1615ebd538799b9e69ccd24e3', '3a780935458180e763738f71f608db1b78322156']},
            'projects': {'recordedPath': str(self.tmp/'evidence/manifest-at-start.xml'),
                'recordedSha256': rb.sha256_file(self.tmp/'evidence/manifest-at-start.xml'),
                'currentPath': str(self.tmp/'evidence/manifest-now.xml')},
            'output': {'uid': os.geteuid()},
            'sources': {'configSha256': 'synthetic', 'projects': 'synthetic', 'output': 'synthetic'}}
        doc.update(changes)
        (self.tmp/'prov.json').write_text(json.dumps(doc))
        return self.tmp/'prov.json'

    def provenance_doc(self):
        return json.loads(self.provenance().read_text())

    def probes(self):
        return {'statvfs': lambda p: self.fs, 'boot': lambda: self.boot}

    def admission(self, cut=None, prov=None, config=None, build=BUILD, witness=None):
        return rb.resume_admission(config or self.config, self.config_path, build, self.dl, self.prior,
            cut or self.cut, prov or self.prov, self.inputs, witness or self.witness, **self.probes())

    def resume(self, runner=None, cut=None, config=None, witness=None):
        runner = runner or CompilingRunner(self.src)
        code = rb.resume(config or self.config, self.config_path, BUILD, self.dl, self.prior, cut or self.cut, self.prov,
            self.inputs, witness or self.witness, runner=runner, now=lambda: 1793335200, **self.probes())
        return code, runner

    def snapshot(self):
        return {str(p): rb.sha256_file(p) for p in sorted(self.prior.glob('*'))}

    def refused(self, expect, **kw):
        report = self.admission(**kw)
        self.assertFalse(report['admitted'])
        self.assertIn(expect, ' '.join(report['problems']))
        return report

    def test_a_proven_cutoff_resumes_the_same_out_dir_once_and_keeps_history(self):
        before, sentinel = self.snapshot(), (self.out/'soong/.cache-sentinel').read_bytes()
        original = json.loads((self.prior/'started.json').read_text())
        self.assertEqual(self.admission()['problems'], [])
        code, runner = self.resume()
        self.assertEqual(code, 0)
        self.assertEqual(runner.calls, [(original['command']['argv'], str(self.src),
            {'OUT_DIR': 'out-rango-' + BUILD, 'BUILD_NUMBER': BUILD})], 'the same command, cwd, OUT_DIR and BUILD_NUMBER')
        self.assertEqual(self.snapshot(), before, 'the original receipts are untouched')
        self.assertEqual((self.out/'soong/.cache-sentinel').read_bytes(), sentinel, 'the cache is reused, not rebuilt')
        self.assertEqual((self.out/'build.log').read_text(), 'call 1\ncall 1\n', 'the same OUT_DIR, appended to')
        attempt = Path(f'{self.prior}.resume-1')
        started = json.loads((attempt/'started.json').read_text())
        completed = json.loads((attempt/'completed.json').read_text())
        self.assertEqual(started['command'], original['command'])
        self.assertEqual(started['resumeOf']['receipts'], {Path(p).name: h for p, h in before.items()})
        self.assertEqual(started['resumeOf']['cutoffSha256'], rb.sha256_file(self.cut))
        self.assertEqual((started['signed'], completed['returncode'], completed['incremental'], completed['releaseReady']),
            (False, 0, True, False))
        self.assertEqual(json.loads((attempt/'preflight.json').read_text())['resume']['output']['uid'], os.geteuid())
        again = CompilingRunner(self.src)
        self.assertEqual(self.resume(runner=again)[0], 0, 'a completed compile is a no-op')
        self.assertEqual(again.calls, [])
        self.assertFalse(Path(f'{self.prior}.resume-2').exists())
        self.assertEqual(self.admission()['state'], 'completed')

    def test_a_second_cutoff_needs_a_proof_bound_to_the_resume_attempt(self):
        with self.assertRaises(Killed):
            self.resume(runner=CompilingRunner(self.src, killed=True))
        first = Path(f'{self.prior}.resume-1')
        self.assertFalse((first/'completed.json').exists())
        self.assertEqual(json.loads((first/'started.json').read_text())['boot'], self.boot, 'resume records its boot')
        self.assertIn('not bound to', ' '.join(self.admission()['problems']), 'the original proof is spent')
        cut2 = self.cutoff(first, 'cut2.json', stoppedAt='2026-10-30T05:00:00Z', observedAt='2026-10-30T05:01:00Z')
        self.assertIn('same boot as the live attempt', ' '.join(self.admission(cut=cut2)['problems']),
            "resume-1's own recorded boot is the witness, and this boot is that boot")
        self.boot = {'bootId': THIRD_BOOT, 'bootTime': '2026-10-30T05:10:00Z'}
        code, runner = self.resume(cut=cut2)
        self.assertEqual((code, len(runner.calls)), (0, 1))
        self.assertEqual(json.loads((Path(f'{self.prior}.resume-2')/'started.json').read_text())['resumeOf']['receiptDir'],
            str(first))
        Path(f'{self.prior}.resume-2').rename(Path(f'{self.prior}.resume-3'))
        with self.assertRaises(rb.InvalidInput):
            self.admission()

    def test_ordinary_failure_missing_completion_and_never_started_are_not_cutoffs(self):
        (self.prior/'completed.json').write_text(json.dumps({'stage': 'compile', 'returncode': 2, 'signed': False}))
        self.assertEqual(self.refused('ordinary failure, returncode 2')['state'], 'failed')
        for code in (-15, 143, 137):
            (self.prior/'completed.json').write_text(json.dumps({'stage': 'compile', 'returncode': code}))
            self.assertEqual(self.admission()['problems'], [], f'signal {code} with a bound proof')
            self.assertEqual(self.refused('cutoff cause', cut=self.cutoff(cause='exit-143', name='bad.json'))['state'],
                'interrupted-unproven', 'a signal alone is not proof')
        (self.prior/'completed.json').unlink()
        self.assertEqual(self.refused('rango-compile-cutoff', cut=self.tmp/'absent.json')['state'], 'interrupted-unproven')
        (self.prior/'started.json').rename(self.tmp/'started.json')
        self.assertEqual(self.refused('never ran')['state'], 'never-started')
        (self.tmp/'started.json').rename(self.prior/'started.json')
        (self.prior/'completed.json').write_text('{not json')
        self.assertEqual(self.refused('unreadable')['state'], 'malformed')

    def test_the_cutoff_proof_must_bind_this_attempt_build_and_a_real_stop(self):
        self.refused('names another build', cut=self.cutoff(build={'buildNumber': '2026103001', 'outDir': 'out-rango-2026103001'}))
        self.refused('not bound to', cut=self.cutoff(prior={'receiptDir': str(self.prior), 'startedSha256': '0' * 64,
            'preflightSha256': rb.sha256_file(self.prior/'preflight.json')}))
        self.refused('a failure is not a cutoff', cut=self.cutoff(cause='compiler-error'))
        self.refused('after the attempt started', cut=self.cutoff(stoppedAt='2026-10-30T02:00:00Z'))
        self.refused('no supervisor or VM stop evidence', cut=self.cutoff(evidence=[]))
        self.refused('quoted line', cut=self.cutoff(evidence=[{'path': str(self.evidence),
            'sha256': rb.sha256_file(self.evidence), 'line': 'build failed'}]))
        good = self.cutoff(name='good.json')
        self.evidence.write_text('edited after the fact\n')
        self.refused('not the recorded bytes', cut=good)

    def test_old_format_provenance_must_be_supplied_and_still_match(self):
        self.refused("missing ['projects']", prov=self.provenance(projects=None))
        self.refused('without a recorded source', prov=self.provenance(sources={'configSha256': 'x'}))
        self.refused('config differs', prov=self.provenance(configSha256='0' * 64))
        (self.tmp/'evidence/manifest-now.xml').write_text(MANIFEST.replace('aaaa', 'bbbb'))
        self.refused('pins differ from the retained map: 2 entries', prov=self.provenance())
        (self.tmp/'evidence/manifest-now.xml').write_text(MANIFEST.replace('<project name="platform/build"', '<project'))
        self.refused('has no pinned revision', prov=self.provenance())
        (self.tmp/'evidence/manifest-now.xml').write_text(MANIFEST)
        self.refused('not the retained project map', prov=self.provenance(projects={**self.provenance_doc()['projects'],
            'recordedSha256': '0' * 64}))
        self.provenance()
        self.assertEqual(self.admission()['facts']['provenance']['projectPins'], 2)
        self.refused('not bound to', prov=self.provenance(prior={'startedSha256': '0' * 64}))
        self.refused('owned by uid', prov=self.provenance(output={'uid': os.geteuid() + 1}))

    def test_a_different_target_command_or_input_is_refused(self):
        other = copy.deepcopy(self.config)
        other['host']['makeJobs'] = 16
        self.refused('ran another command', config=other)
        other = copy.deepcopy(self.config)
        other['host']['makeGoals'] = ['droid']
        self.refused('ran another command', config=other)
        self.refused('names another build', build='2026103001')
        self.integrated(label='rango-again')
        self.refused('reconcile receipt receipt differs')
        product = json.loads(self.inputs.read_text())
        product['product'] = {'commit': 'other'}
        self.inputs.write_text(json.dumps(product))
        self.refused('local-inputs product differs')
        (self.src/rb.INSTALLED['agent']).write_bytes(b'drifted')
        self.refused('no longer matches')

    def test_the_output_must_be_this_builds_own_directory(self):
        moved = self.tmp/'elsewhere'
        self.out.rename(moved)
        self.refused('not an existing ordinary directory')
        self.out.symlink_to(moved)
        self.refused('not an existing ordinary directory')
        self.out.unlink()
        moved.rename(self.out)
        linked = self.tmp/'linked-src'
        linked.symlink_to(self.src)
        config = copy.deepcopy(self.config)
        config['host']['sourceRoot'] = str(linked)
        self.refused('linked component', config=config)
        inside = self.out/'receipts'
        self.assertIn('collides with', ' '.join(rb.check_output(self.src, 'out-rango-' + BUILD, os.geteuid(), [inside])[0]))

    def test_only_a_later_boot_than_the_witnessed_live_boot_proves_the_old_tree_gone(self):
        self.boot = {'bootId': OLD_BOOT, 'bootTime': '2026-10-30T02:00:00Z'}
        report = self.refused('same boot as the live attempt')
        self.assertIn('a same-boot resume is unsupported', ' '.join(report['problems']))
        self.boot = None
        self.refused('unreadable; termination is unknown, not proven')
        self.boot = {'bootId': NEW_BOOT, 'bootTime': '2026-10-30T04:20:00Z'}
        self.refused('did not begin after the witnessed boot and the recorded stop')
        self.boot = {'bootId': NEW_BOOT, 'bootTime': '2026-10-30T04:35:00Z'}
        self.assertEqual(self.admission()['problems'], [])

    def test_the_boot_witness_must_be_genuine_bound_and_contemporaneous(self):
        report = rb.resume_admission(self.config, self.config_path, BUILD, self.dl, self.prior, self.cut, self.prov,
            self.inputs, None, **self.probes())
        self.assertIn('was not recorded at launch', ' '.join(report['problems']))
        self.refused('rango-compile-boot-witness', witness=self.boot_witness(name='w.json', schemaVersion=2))
        self.refused('not bound to', witness=self.boot_witness(name='w.json', attempt={'receiptDir': str(self.prior),
            'startedSha256': '0' * 64}))
        self.refused('not bound to', witness=self.boot_witness(name='w.json', buildNumber='2026103002'))
        self.refused('not observed between', witness=self.boot_witness(name='w.json', observedAt='2026-10-30T02:00:00Z'),
            ) # backdated before the attempt started
        self.refused('not observed between', witness=self.boot_witness(name='w.json', observedAt='2026-10-30T04:30:00Z'))
        self.refused('not a kernel boot id', witness=self.boot_witness(name='w.json', bootId='boot-of-the-compile'))
        self.refused('no observer', witness=self.boot_witness(name='w.json', method=''))
        (self.tmp/'broken.json').write_text('{')
        self.refused('rango-compile-boot-witness', witness=self.tmp/'broken.json')
        self.refused('collides with', witness=self.boot_witness(name='w.json').rename(self.out/'w.json'))

    def test_no_process_file_is_ever_read(self):
        """Admission, resume and the boot reader touch no /proc/<pid> path; the guard fails any such access."""
        self.assertEqual(self.resume()[0], 0)
        code = (SCRIPTS/'rango-build-only.py').read_text()
        for banned in ("'cmdline'", "'environ'", "'fd'", '/cmdline', '/environ', '/fd', 'live_writers', 'pid_alive', 'sudo'):
            self.assertNotIn(banned, code, banned)
        guard = ProcGuard()
        with self.assertRaises(AssertionError):
            guard.check('/proc/3056/cmdline')
        for allowed in ('/proc/sys/kernel/random/boot_id', '/proc/stat'):
            guard.check(allowed)

    def test_held_lock_stops_before_anything_runs(self):
        import fcntl
        with open(self.config['host']['lockPath'], 'a') as held:
            fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
            code, runner = self.resume()
        self.assertEqual((code, runner.calls), (1, []))
        self.assertFalse(Path(f'{self.prior}.resume-1').exists())

    def test_current_boot_reads_only_the_two_kernel_files(self):
        reads = []
        real = Path.read_text
        def spy(path, *a, **kw):
            reads.append(str(path))
            return real(path, *a, **kw)
        from unittest import mock
        with mock.patch.object(Path, 'read_text', spy):
            boot = rb.current_boot()
        self.assertEqual(reads, ['/proc/sys/kernel/random/boot_id', '/proc/stat'][:len(reads)])
        if boot is not None:
            self.assertRegex(boot['bootId'], rb.BOOT_ID)
            self.assertRegex(boot['bootTime'], rb.STAMP)

    def test_admission_is_redone_under_the_lock(self):
        self.assertTrue(self.admission()['admitted'])
        (self.tmp/'inputs/signed-apps/dev.pixel.voice.apk').write_bytes(b'changed after the check')
        code, runner = self.resume()
        self.assertEqual((code, runner.calls), (1, []))
        self.assertFalse(Path(f'{self.prior}.resume-1').exists())

    def test_fresh_runs_still_refuse_the_existing_out_dir(self):
        self.assertIn('needs a new OUT_DIR', ' '.join(self.check('compile', inputs=self.inputs)['problems']))
        runner = Runner()
        self.assertEqual(rb.run(self.config, 'compile', BUILD, self.dl, self.tmp/'fresh', runner=runner,
            statvfs=lambda p: self.fs, lock_probe=lambda p: True, inputs_receipt=self.inputs), 1)
        self.assertEqual(runner.calls, [])

    def test_cli_has_no_receipt_dir_force_or_non_compile_resume(self):
        self.assertEqual(rb.main(['resume-check', '--config', str(self.config_path), '--build-number', BUILD,
            '--download-dir', str(self.dl), '--prior-receipt-dir', str(self.prior), '--cutoff-proof', str(self.cut),
            '--provenance', str(self.prov), '--inputs-receipt', str(self.inputs)]), 1,
            'an old-format run without a --boot-witness is refused (the check itself runs nothing)')
        base = ['--config', str(self.config_path), '--build-number', BUILD, '--download-dir', str(self.dl),
            '--prior-receipt-dir', str(self.prior), '--cutoff-proof', str(self.cut), '--provenance', str(self.prov),
            '--inputs-receipt', str(self.inputs)]
        self.assertEqual(rb.main(['resume', *base, '--receipt-dir', str(self.tmp/'r')]), 2)
        self.assertEqual(rb.main(['resume', '--stage', 'generate', *base]), 2)
        self.assertEqual(rb.main(['resume', *base[:-2]]), 2)
        with self.assertRaises(SystemExit):
            rb.main(['resume', *base, '--force'])

if __name__ == '__main__':
    unittest.main()
