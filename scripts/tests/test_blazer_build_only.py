"""FAM-BLAZER-BUILD-004: the blazer build-only recipe (os/build-only/blazer.json) and scripts/blazer-build-only.py.

Exercises the real load, operator, commands, preflight and run functions against synthetic source trees, receipts and a
fake runner. Nothing downloads, generates, runs lunch, compiles or signs, and no host or phone is reached; a passing
run proves admission and rendering logic only, never a real build."""
from pathlib import Path
import fcntl
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import types
import unittest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('blazer_build_only', ROOT/'scripts/blazer-build-only.py')
bb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bb)

RESERVE = 322122547200
ADEVTOOL = '17df3b79a73e67ad68387b35c7d41c85e1d8103c'

def fs(available):
    return lambda path: types.SimpleNamespace(f_bavail=available, f_frsize=1)

class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='blazer-build-only-'))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.recipe = bb.load()
        self.measured = json.loads(json.dumps(self.recipe))
        self.measured['capacity'].update(generateAllowanceBytes=10, compileAllowanceBytes=10)
        self.src = self.tmp/'src'
        for needed in bb.TREE_NEEDS:
            path = self.src/needed
            path.parent.mkdir(parents=True, exist_ok=True)
            if needed.endswith('bin') or needed.endswith('linux-x86'):
                path.mkdir(exist_ok=True)
            else:
                path.write_text('#!/bin/sh\n')
        deps = self.src/'out_adevtool_deps'
        (deps/'host/linux-x86/bin').mkdir(parents=True)
        (deps/'adevtool_revision').write_text(ADEVTOOL + '\n')
        for tool in self.recipe['adevtool']['depsTools']:
            (deps/'host/linux-x86/bin'/tool).write_text('#!/bin/sh\n')
            os.chmod(deps/'host/linux-x86/bin'/tool, 0o755)
        (self.src/self.recipe['kernelDir']).mkdir(parents=True)
        self.lock = self.tmp/'blazer.lock'
        self.download = self.tmp/'dl'

    def op(self, **change):
        values = dict(host='operator-builder-1', source_root=str(self.src), jobs=16, lock=str(self.lock),
            download_dir=str(self.download), integration_dir=None)
        values.update(change)
        return bb.operator(**values)

    def preflight(self, stage, recipe=None, available=RESERVE + 100, lock_free=True, **change):
        return bb.preflight(recipe or self.measured, stage, self.op(**change), statvfs=fs(available),
            lock_probe=lambda path: lock_free)

    def generated(self):
        vendor = self.src/'vendor/google_devices/blazer'
        vendor.mkdir(parents=True)
        (vendor/'blazer.mk').write_text('# generated\n')
        (vendor/'adevtool-version-check.mk').write_text(f'# adevtool {ADEVTOOL}\n')
        return vendor

    def integrated(self, **record):
        apk = self.src/'packages/apps/PixelBackgroundControl/Agent.apk'
        apk.parent.mkdir(parents=True)
        apk.write_bytes(b'synthetic agent')
        receipts = self.tmp/'reconcile'
        receipts.mkdir()
        ours = {name: bb.sha256_file(ROOT/'scripts'/name) for name in self.recipe['integration']['patcher']}
        body = {'receiptType': 'reconcile', 'label': 'blazer-2026103140', 'patcher': {'patcher': 'trusted', 'sha256': ours},
            'entries': {'packages/apps/PixelBackgroundControl/Agent.apk': {'after': hashlib.sha256(b'synthetic agent').hexdigest()}}}
        body.update(record)
        (receipts/'0001-blazer.json').write_text(json.dumps(body))
        return receipts

class RecipeTest(Fixture):
    def test_checked_in_recipe_binds_the_pinned_sources(self):
        recipe = self.recipe
        self.assertEqual((recipe['device'], recipe['lunch']), ('blazer', 'blazer-cur-user'))
        self.assertEqual((recipe['factory']['file'], recipe['factory']['sha256']),
            ('blazer-cp3a.261005.005-factory-cd7d12c2.zip', 'cd7d12c215f7cf1c45d6c77291690bfab1bf360cddd00d557390aed70a3418e3'))
        self.assertEqual(recipe['pins'], {'vendor/adevtool': ADEVTOOL, 'vendor/state': '1469180711293a5852bfda9a5f67b8f53d9828ad',
            'device/google/laguna-kernels/6.6': '275dad3c1f144f6bdd6d2c1ec22eccbfbd467da9'})
        self.assertEqual(recipe['kernelDir'], 'device/google/laguna-kernels/6.6/grapheneos/muzel')
        self.assertTrue(recipe['capacity']['allowanceRequired'])
        self.assertEqual((recipe['capacity']['generateAllowanceBytes'], recipe['capacity']['compileAllowanceBytes']), (None, None))
        self.assertNotIn('host', recipe, 'the recipe binds no host; the operator supplies it')

    def test_rendered_commands(self):
        rendered = bb.commands(self.recipe, self.op(jobs=24))
        self.assertEqual(rendered['generate'], {'cwd': str(self.src), 'env': {'ADEVTOOL_IMG_DOWNLOAD_DIR': str(self.download)},
            'argv': ['vendor/adevtool/bin/run', 'generate-all', '-d', 'blazer']})
        self.assertEqual(rendered['compile'], {'cwd': str(self.src), 'env': {'OUT_DIR': 'out-blazer-build'},
            'argv': ['bash', '-c', 'source build/envsetup.sh && lunch blazer-cur-user && '
                'm -j24 vendorbootimage vendorkernelbootimage target-files-package otatools-package']})

    def test_drifted_recipes_are_refused(self):
        cases = (
            (lambda r: r.update(device='rango'), 'builds blazer only'),
            (lambda r: r.update(lunch='blazer-cur-userdebug'), 'lunch'),
            (lambda r: r['factory'].update(sha256='0' * 64), 'factory image name or sha256'),
            (lambda r: r['factory'].update(file='rango-cp3a.261005.005-factory-7110a4ac.zip'), 'factory image name or sha256'),
            (lambda r: r['factory'].update(url='https://example.com/' + r['factory']['file']), 'factory URL'),
            (lambda r: r['pins'].update({'vendor/adevtool': '0' * 40}), 'pin vendor/adevtool'),
            (lambda r: r['pins'].update({'vendor/state': '0' * 40}), 'pin vendor/state'),
            (lambda r: r['pins'].update({'device/google/laguna-kernels/6.6': '0' * 40}), 'pin device/google/laguna-kernels'),
            (lambda r: r.update(kernelDir='device/google/caimito-kernels/6.1/grapheneos'), 'kernelDir'),
            (lambda r: r['generate'].update(argv=['vendor/adevtool/bin/run', 'generate-all']), '-d blazer'),
            (lambda r: r['compile'].update(outDir='out'), 'out-blazer-build'),
            (lambda r: r['capacity'].update(allowanceRequired=False), 'no isolated-host allowance exception'),
            (lambda r: r['capacity'].update(reserveBytes=1), '300 GiB reserve'),
            (lambda r: r['generate'].update(note='os/fold-port'), 'os/fold-port'))
        for change, pattern in cases:
            recipe = json.loads((ROOT/'os/build-only/blazer.json').read_text())
            change(recipe)
            path = self.tmp/'recipe.json'
            path.write_text(json.dumps(recipe))
            with self.assertRaisesRegex(bb.InvalidInput, pattern):
                bb.load(path)

    def test_operator_must_supply_host_path_and_jobs(self):
        for change, pattern in ((dict(host=None), '--host'), (dict(host='Bad Host!'), '--host'), (dict(jobs=0), '--jobs'),
                (dict(jobs=257), '--jobs'), (dict(jobs=True), '--jobs'), (dict(jobs='8'), '--jobs'),
                (dict(source_root='relative/src'), 'sourceRoot'), (dict(lock='/srv/../lock'), 'lock'),
                (dict(download_dir='dl'), 'downloadDir')):
            with self.assertRaisesRegex(bb.InvalidInput, pattern):
                self.op(**change)
        cli = subprocess.run([sys.executable, str(ROOT/'scripts/blazer-build-only.py'), 'plan', '--stage', 'generate',
            '--source-root', str(self.src), '--lock', str(self.lock)], capture_output=True, text=True)
        self.assertEqual(cli.returncode, 2)
        self.assertIn('--host', cli.stderr)

class GenerateTest(Fixture):
    def test_synthetic_tree_is_admitted_only_with_measured_capacity(self):
        self.assertEqual(self.preflight('generate')['problems'], [])
        unmeasured = self.preflight('generate', recipe=self.recipe, available=10 ** 15)
        self.assertEqual(unmeasured['problems'],
            ['capacity.generateAllowanceBytes is unmeasured; a measured allowance is required, never an estimate'])
        self.assertIn('below the reserve', ' '.join(self.preflight('generate', available=RESERVE + 5)['problems']))

    def test_missing_tools_vendor_collisions_symlinks_and_locks_refuse(self):
        tools = self.src/'out_adevtool_deps/host/linux-x86/bin'
        checks = []
        def expect(pattern, **change):
            report = self.preflight('generate', **change)
            self.assertFalse(report['ready'])
            self.assertIn(pattern, ' '.join(report['problems']))
            checks.append(pattern)
        (tools/'lpunpack').unlink()
        expect('adevtool host tools missing')
        (tools/'lpunpack').write_text('x')
        expect('adevtool host tools missing')  # present but not executable
        os.chmod(tools/'lpunpack', 0o755)
        (tools/'simg2img').unlink()
        os.symlink('/bin/true', tools/'simg2img')
        expect('adevtool host tools missing')  # a linked tool is not the built one
        (tools/'simg2img').unlink()
        (tools/'simg2img').write_text('x')
        os.chmod(tools/'simg2img', 0o755)
        (self.src/'out_adevtool_deps/adevtool_revision').write_text('0' * 40)
        expect('not the pinned revision')
        (self.src/'out_adevtool_deps/adevtool_revision').write_text(ADEVTOOL)
        (self.src/'build/envsetup.sh').unlink()
        expect('build/envsetup.sh is missing')
        (self.src/'build/envsetup.sh').write_text('')
        shutil.rmtree(self.src/self.recipe['kernelDir'])
        expect('pinned laguna kernel')
        (self.src/self.recipe['kernelDir']).mkdir()
        self.generated()
        expect('already exists; generate-all would replace it')
        shutil.rmtree(self.src/'vendor/google_devices')
        self.download.mkdir()
        (self.download/'stale.zip').write_bytes(b'x')
        expect('must be absent or empty')
        expect('outside the source tree', download_dir=str(self.src/'dl'))
        expect('generate needs --download-dir', download_dir=None)
        shutil.rmtree(self.download)
        (self.src/'out-blazer-build').mkdir()
        expect('refusing to reuse or overwrite an OUT_DIR')
        (self.src/'out-blazer-build').rmdir()
        expect('lock is held', lock_free=False)
        link = self.tmp/'src-link'
        os.symlink(self.src, link)
        expect('passes through a symlink', source_root=str(link))
        self.assertEqual(self.preflight('generate')['problems'], [])
        self.assertEqual(len(checks), 13)

class CompileTest(Fixture):
    def test_integrated_tree_is_admitted(self):
        self.generated()
        report = self.preflight('compile', integration_dir=str(self.integrated()))
        self.assertEqual(report['problems'], [])
        self.assertEqual(report['facts']['roseIntegration']['receipt'], '0001-blazer.json')

    def test_missing_vendor_or_integration_and_foreign_provenance_refuse(self):
        receipts = self.integrated()
        self.assertIn('run the generate stage first', ' '.join(self.preflight('compile', integration_dir=str(receipts))['problems']))
        vendor = self.generated()
        (vendor/'adevtool-version-check.mk').write_text('# adevtool 0000\n')
        self.assertIn('foreign provenance', ' '.join(self.preflight('compile', integration_dir=str(receipts))['problems']))
        (vendor/'adevtool-version-check.mk').write_text(f'# adevtool {ADEVTOOL}\n')
        self.assertIn('needs --integration-dir', ' '.join(self.preflight('compile')['problems']))
        empty = self.tmp/'empty'
        empty.mkdir()
        self.assertIn('no Rose integration', ' '.join(self.preflight('compile', integration_dir=str(empty))['problems']))
        for record, pattern in (({'label': 'rango-2026103001'}, 'foreign provenance'),
                ({'receiptType': 'reconcile-adopt'}, 'not a completed reconcile'),
                ({'patcher': {'patcher': 'candidate', 'sha256': {}}}, 'foreign recipe'),
                ({'entries': {}}, 'records no installed entries')):
            body = json.loads((receipts/'0001-blazer.json').read_text())
            body.update(record)
            (receipts/'0002-later.json').write_text(json.dumps(body))
            self.assertIn(pattern, ' '.join(self.preflight('compile', integration_dir=str(receipts))['problems']), pattern)
            (receipts/'0002-later.json').unlink()
        (self.src/'packages/apps/PixelBackgroundControl/Agent.apk').write_bytes(b'changed')
        self.assertIn('no longer matches', ' '.join(self.preflight('compile', integration_dir=str(receipts))['problems']))
        (self.src/'packages/apps/PixelBackgroundControl/Agent.apk').write_bytes(b'synthetic agent')
        elsewhere = self.tmp/'foreign.json'
        shutil.copyfile(receipts/'0001-blazer.json', elsewhere)
        os.symlink(elsewhere, receipts/'0003-link.json')
        self.assertIn('is a link out of', ' '.join(self.preflight('compile', integration_dir=str(receipts))['problems']))

class RunTest(Fixture):
    def test_fake_execution_writes_receipts_and_runs_the_rendered_command(self):
        calls = []
        def runner(argv, cwd, env):
            calls.append((argv, cwd, {k: env[k] for k in ('ADEVTOOL_IMG_DOWNLOAD_DIR',) if k in env}))
            return types.SimpleNamespace(returncode=0)
        out = self.tmp/'receipts/generate'
        code = bb.run(self.measured, 'generate', self.op(), out, runner=runner, now=lambda: 0,
            statvfs=fs(RESERVE + 100), lock_probe=lambda path: True)
        self.assertEqual(code, 0)
        self.assertEqual(calls, [(['vendor/adevtool/bin/run', 'generate-all', '-d', 'blazer'], str(self.src),
            {'ADEVTOOL_IMG_DOWNLOAD_DIR': str(self.download)})])
        self.assertEqual(sorted(p.name for p in out.iterdir()), ['completed.json', 'preflight.json', 'started.json'])
        completed = json.loads((out/'completed.json').read_text())
        self.assertEqual((completed['signed'], completed['releaseReady'], completed['returncode']), (False, False, 0))
        self.assertEqual(json.loads((out/'started.json').read_text())['host'], 'operator-builder-1')

    def test_not_ready_collisions_and_held_locks_never_run(self):
        ran = []
        runner = lambda *a, **k: ran.append(a) or types.SimpleNamespace(returncode=0)
        code = bb.run(self.recipe, 'generate', self.op(), self.tmp/'r1', runner=runner, now=lambda: 0,
            statvfs=fs(10 ** 15), lock_probe=lambda path: True)
        self.assertEqual((code, ran), (1, []))
        self.assertEqual(sorted(p.name for p in (self.tmp/'r1').iterdir()), ['preflight.json'])
        with self.assertRaisesRegex(bb.InvalidInput, 'already exists'):
            bb.run(self.measured, 'generate', self.op(), self.tmp/'r1', runner=runner)
        with open(self.lock, 'a') as held:
            fcntl.flock(held, fcntl.LOCK_EX)
            code = bb.run(self.measured, 'generate', self.op(), self.tmp/'r2', runner=runner, now=lambda: 0,
                statvfs=fs(RESERVE + 100))
        self.assertEqual((code, ran), (1, []))
        self.assertIn('lock is held', ' '.join(json.loads((self.tmp/'r2/preflight.json').read_text())['problems']))

if __name__ == '__main__':
    unittest.main()
