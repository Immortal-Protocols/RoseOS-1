"""FAM-BLAZER-BUILD-004 / BLAZER-PREP: the blazer recipe (os/build-only/blazer.json) and scripts/blazer-build-only.py.

Exercises the real load, operator, commands, preflight and run functions against synthetic source trees, receipts, a fake
fetcher and a fake runner. Nothing downloads, generates, runs lunch, compiles or signs, and no host or phone is reached;
a passing run proves admission, bounds and rendering logic only, never a real preparation or build."""
from pathlib import Path
import hashlib
import importlib.util
import io
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
sys.path.insert(0, str(ROOT/'scripts/tests'))
import test_blazer_integration as ti  # noqa: E402  (the admit producer for compile's inputs receipt)

RESERVE = 322122547200
ADEVTOOL = '17df3b79a73e67ad68387b35c7d41c85e1d8103c'
FACTORY = 'blazer-cp3a.261005.005-factory-cd7d12c2.zip'
PIN = 'cd7d12c215f7cf1c45d6c77291690bfab1bf360cddd00d557390aed70a3418e3'
PAYLOAD = b'synthetic factory archive bytes'

def fs(available):
    return lambda path: types.SimpleNamespace(f_bavail=available, f_frsize=1)

class Response(io.BytesIO):
    def __enter__(self):
        return self
    def __exit__(self, *exc):
        self.close()

class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='blazer-build-only-'))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.recipe = bb.load()
        # A synthetic archive stands in for the real one: the recipe copy pins its digest (synthetic, never the real pin).
        self.synthetic = json.loads(json.dumps(self.recipe))
        self.synthetic['factory']['sha256'] = hashlib.sha256(PAYLOAD).hexdigest()
        self.synthetic['capacity']['compileAllowanceBytes'] = 10
        # A checked-in-source copy whose production source root is a temporary tree, so isolation is exercised for real.
        self.root = self.tmp/'repo'
        (self.root/'os/release-pipeline').mkdir(parents=True)
        self.production = self.tmp/'production'
        self.production.mkdir()
        (self.root/'os/release-pipeline/frankel.json').write_text(json.dumps(
            {'host': {'sourceRoot': str(self.production), 'lockPath': str(self.tmp/'frankel-build.lock')}}))
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
        self.lock = self.tmp/'frankel-build.lock'
        self.download = self.tmp/'dl'
        self.node = self.tmp/'node'
        self.node.write_text('#!/bin/sh\n')
        os.chmod(self.node, 0o755)

    def op(self, **change):
        values = dict(host='operator-builder-1', source_root=str(self.src), jobs=16, lock=str(self.lock),
            download_dir=str(self.download), integration_dir=None, inputs_receipt=getattr(self, 'inputs', None),
            node=str(self.node))
        values.update(change)
        return bb.operator(**values)

    def preflight(self, stage, recipe=None, available=10 ** 15, lock_free=True, **change):
        return bb.preflight(recipe or self.synthetic, stage, self.op(**change), statvfs=fs(available),
            lock_probe=lambda path: lock_free, root=self.root)

    def archive(self):
        self.download.mkdir(exist_ok=True)
        (self.download/FACTORY).write_bytes(PAYLOAD)

    def generated(self):
        vendor = self.src/'vendor/google_devices/blazer'
        vendor.mkdir(parents=True)
        (vendor/'blazer.mk').write_text('# generated\n')
        (vendor/'adevtool-version-check.mk').write_text(f'# adevtool {ADEVTOOL}\n')
        return vendor

    def problems(self, stage, **change):
        return ' '.join(self.preflight(stage, **change)['problems'])

    def integrated(self, **record):
        """Real admitted inputs (blazer_integration.admit) and the tree and receipt host-reconcile would leave."""
        producer = ti.Fixture('setUp')
        producer.setUp()
        self.addCleanup(producer.doCleanups)
        _, out = producer.admit()
        self.inputs = str(out/'local-inputs.json')
        producer.tmp = self.tmp
        _, body = producer.tree(json.loads(Path(self.inputs).read_text()))
        receipts = self.tmp/'reconcile'
        receipts.mkdir()
        body.update(record)
        (receipts/'0001-blazer.json').write_text(json.dumps(body))
        return receipts

class RecipeTest(Fixture):
    def test_checked_in_recipe_binds_the_pinned_sources_and_proposal_bounds(self):
        recipe = self.recipe
        self.assertEqual((recipe['device'], recipe['lunch']), ('blazer', 'blazer-cur-user'))
        self.assertEqual((recipe['factory']['file'], recipe['factory']['sha256']), (FACTORY, PIN))
        self.assertEqual(recipe['factory']['url'], 'https://dl.google.com/dl/android/aosp/' + FACTORY)
        self.assertEqual(recipe['pins'], {'vendor/adevtool': ADEVTOOL, 'vendor/state': '1469180711293a5852bfda9a5f67b8f53d9828ad',
            'device/google/laguna-kernels/6.6': '275dad3c1f144f6bdd6d2c1ec22eccbfbd467da9'})
        self.assertEqual(recipe['generate']['argv'], ['vendor/adevtool/bin/run', 'generate-all', '-d', 'blazer'])
        prep = recipe['preparation']
        self.assertEqual((prep['stages'], prep['boundsKind']), (['download', 'vendor', 'lunch'], 'proposal'))
        self.assertEqual({s: (prep[s]['boundBytes'], prep[s]['timeoutSeconds']) for s in prep['stages']},
            {'download': (8 << 30, 3600), 'vendor': (128 << 30, 10800), 'lunch': (8 << 30, 1800)})
        self.assertEqual(bb.pointer(ROOT, prep['productionSourceRootFrom']), '/home/pixelbuild/grapheneos-2026100600')
        self.assertEqual(bb.pointer(ROOT, prep['lockFrom']), '/home/pixelbuild/frankel-build.lock')
        preflight = importlib.util.spec_from_file_location('bpp', ROOT/'scripts/blazer-preparation-preflight.py')
        module = importlib.util.module_from_spec(preflight)
        preflight.loader.exec_module(module)
        self.assertEqual(prep['vendor']['node']['minMajor'], module.NODE_MIN_MAJOR)
        self.assertNotIn('generateAllowanceBytes', recipe['capacity'], 'the circular measured-allowance gate is removed')
        self.assertEqual(recipe['capacity']['compileAllowanceBytes'], None)
        self.assertNotIn('host', recipe, 'the recipe binds no host; the operator supplies it')
        for absent in ('S007', 'S008', 'peak'):
            self.assertNotIn(absent, json.dumps(recipe))

    def test_rendered_commands(self):
        rendered = bb.commands(self.recipe, self.op())
        self.assertEqual(rendered['download'], {'url': 'https://dl.google.com/dl/android/aosp/' + FACTORY,
            'part': str(self.download/(FACTORY + '.part')), 'final': str(self.download/FACTORY), 'sha256': PIN,
            'maxBytes': 8 << 30, 'timeoutSeconds': 3600})
        self.assertEqual(rendered['vendor'], {'cwd': str(self.src), 'env': {'ADEVTOOL_IMG_DOWNLOAD_DIR': str(self.download)},
            'argv': ['vendor/adevtool/bin/run', 'generate-all', '-d', 'blazer'], 'timeoutSeconds': 10800,
            'nodeVersion': [str(self.node), '--version']})
        self.assertEqual(rendered['lunch']['env'], {'OUT_DIR': 'out-blazer-lunch'})
        self.assertEqual(rendered['lunch']['argv'][2], 'source build/envsetup.sh && lunch blazer-cur-user && echo TARGET_PRODUCT=$TARGET_PRODUCT'
            ' && echo TARGET_RELEASE=$TARGET_RELEASE && echo TARGET_BUILD_VARIANT=$TARGET_BUILD_VARIANT'
            ' && echo TARGET_DEVICE=$(get_build_var TARGET_DEVICE)')
        self.assertNotIn(' m ', rendered['lunch']['argv'][2] + ' ')
        self.assertEqual(rendered['compile']['env'], {'OUT_DIR': 'out-blazer-build'})

    def test_drifted_recipes_are_refused(self):
        for change, pattern in (
            (lambda r: r.update(lunch='blazer-cur-userdebug'), 'not the contract'),
            (lambda r: r['factory'].update(sha256='0' * 64), 'device contract'),
            (lambda r: r['factory'].update(url='https://example.invalid/' + FACTORY), 'adevtool index URL'),
            (lambda r: r['pins'].update({'vendor/state': '0' * 40}), 'vendor/state'),
            (lambda r: r['generate'].update(argv=['vendor/adevtool/bin/run', 'generate-all']), '-d blazer'),
            (lambda r: r['capacity'].update(reserveBytes=1), '300 GiB'),
            (lambda r: r['preparation'].update(boundsKind='measured'), 'labelled proposal'),
            (lambda r: r['preparation'].update(stages=['download', 'vendor', 'lunch', 'compile']), 'preparation.stages'),
            (lambda r: r['preparation']['vendor'].update(boundBytes=0), 'preparation.vendor needs a positive boundBytes'),
            (lambda r: r['preparation']['download'].update(timeoutSeconds=None), 'preparation.download needs'),
            (lambda r: r['preparation']['vendor'].update(collisionPaths=['vendor/google_devices/blazer']), 'collisionPaths'),
            (lambda r: r['preparation']['vendor']['node'].update(minMajor=0), 'node needs'),
            (lambda r: r['preparation']['lunch']['expect'].update(TARGET_BUILD_VARIANT='userdebug'), 'lunch.expect'),
            (lambda r: r['preparation']['lunch'].update(outDir='out-blazer-build'), 'its own top-level OUT_DIR'),
            (lambda r: r['preparation'].update(productionSourceRootFrom='os/release-pipeline/frankel.json host.missing'), 'must resolve'),
            (lambda r: r['generate'].update(note='os/fold-port'), 'os/fold-port')):
            recipe = json.loads(json.dumps(self.recipe))
            change(recipe)
            path = self.tmp/'recipe.json'
            path.write_text(json.dumps(recipe))
            with self.assertRaisesRegex(bb.InvalidInput, pattern):
                bb.load(path)

    def test_cli_requires_host_lock_and_absolute_paths(self):
        with self.assertRaisesRegex(bb.InvalidInput, '--host'):
            self.op(host='')
        with self.assertRaisesRegex(bb.InvalidInput, 'absolute'):
            self.op(source_root='relative/src')
        with self.assertRaisesRegex(bb.InvalidInput, '--jobs'):
            self.op(jobs=0)
        cli = subprocess.run([sys.executable, str(ROOT/'scripts/blazer-build-only.py'), 'plan', '--stage', 'download',
            '--host', 'operator-builder-1', '--lock', str(self.lock), '--download-dir', str(self.download)],
            capture_output=True, text=True)
        self.assertIn(cli.returncode, (0, 1), cli.stderr)  # 1 when this runner lacks the reserve plus the bound
        report = json.loads(cli.stdout)
        self.assertEqual(report['ready'], cli.returncode == 0)
        self.assertEqual((report['command']['sha256'], report['facts']['capacity']['boundKind']), (PIN, 'proposal'))
        missing = subprocess.run([sys.executable, str(ROOT/'scripts/blazer-build-only.py'), 'preflight', '--stage', 'vendor',
            '--host', 'operator-builder-1', '--lock', str(self.lock)], capture_output=True, text=True)
        self.assertEqual(missing.returncode, 1)
        self.assertIn('vendor needs --download-dir, --source-root, --node', missing.stdout)

class IsolationTest(Fixture):
    def test_preparation_never_uses_the_production_source_root(self):
        inside = self.production/'nested'
        inside.mkdir()
        for path in (self.production, inside, self.tmp):
            for stage in ('vendor', 'lunch'):
                self.assertIn('production source root', self.problems(stage, source_root=str(path)), (stage, path))
        self.assertIn('downloadDir: ', self.problems('download', download_dir=str(self.production/'dl')))
        os.symlink(self.production, self.tmp/'alias')
        self.assertIn('passes through a symlink', self.problems('vendor', source_root=str(self.tmp/'alias')))

    def test_same_tree_under_another_name_is_refused(self):
        os.symlink(self.production, self.tmp/'alias')
        self.assertIn('is the production source root', bb.isolation_problem(self.tmp/'alias', self.production))
        self.assertIsNone(bb.isolation_problem(self.src, self.production))

    def test_checked_in_production_root_is_refused_by_name(self):
        report = bb.preflight(self.synthetic, 'lunch', self.op(source_root='/home/pixelbuild/grapheneos-2026100600'),
            statvfs=fs(10 ** 15), lock_probe=lambda path: True)
        self.assertIn('production source root /home/pixelbuild/grapheneos-2026100600', ' '.join(report['problems']))

class DownloadTest(Fixture):
    def test_admission_space_and_existing_outputs(self):
        report = self.preflight('download')
        self.assertEqual(report['problems'], [])
        self.assertEqual(report['facts']['capacity'], {'availableBytes': 10 ** 15, 'reserveBytes': RESERVE,
            'boundBytes': 8 << 30, 'boundKind': 'proposal'})
        self.assertIn('below the reserve', self.problems('download', available=RESERVE + (8 << 30) - 1))
        self.assertEqual(self.preflight('download', available=RESERVE + (8 << 30))['problems'], [])
        self.archive()
        self.assertIn('never replaced', self.problems('download'))
        (self.download/FACTORY).unlink()
        (self.download/(FACTORY + '.part')).write_bytes(b'x')
        self.assertIn('partial download is never replaced', self.problems('download'))
        self.assertIn('held by another job', self.problems('download', download_dir=str(self.tmp/'dl2'), lock_free=False))
        self.assertIn('download dir parent', self.problems('download', download_dir=str(self.tmp/'no/such/dl')))

    def run_download(self, payload, recipe=None, clock=None, out='r'):
        recipe = recipe or self.synthetic
        fetched = []
        def fetcher(url, timeout):
            fetched.append((url, timeout))
            return Response(payload)
        code = bb.run(recipe, 'download', self.op(), self.tmp/out, fetcher=fetcher, clock=clock or (lambda: 0), now=lambda: 0,
            statvfs=fs(10 ** 15), lock_probe=lambda path: True, root=self.root)
        return code, fetched, json.loads((self.tmp/out/'completed.json').read_text())

    def test_verified_download_is_renamed_and_mismatches_leave_nothing(self):
        code, fetched, done = self.run_download(PAYLOAD)
        self.assertEqual((code, done['ok'], done['bytes']), (0, True, len(PAYLOAD)))
        self.assertEqual(fetched, [('https://dl.google.com/dl/android/aosp/' + FACTORY, 3600)])
        self.assertEqual((self.download/FACTORY).read_bytes(), PAYLOAD)
        self.assertFalse((self.download/(FACTORY + '.part')).exists())
        (self.download/FACTORY).unlink()
        code, _, done = self.run_download(b'other bytes', out='r2')
        self.assertEqual((code, done['ok']), (1, False))
        self.assertIn('is not the pinned', done['error'])
        self.assertEqual(list(self.download.iterdir()), [], 'a failed download leaves no partial or final file')

    def test_byte_and_time_bounds_stop_the_download(self):
        small = json.loads(json.dumps(self.synthetic))
        small['preparation']['download']['boundBytes'] = 4
        code, _, done = self.run_download(PAYLOAD, recipe=small)
        self.assertEqual(code, 1)
        self.assertIn('exceeded the 4-byte bound', done['error'])
        ticks = iter(range(0, 10 ** 6, 4000))
        code, _, done = self.run_download(PAYLOAD, clock=lambda: next(ticks), out='r2')
        self.assertIn('download exceeded 3600 s', done['error'])
        self.assertEqual(list(self.download.iterdir()), [])

    def test_the_real_pin_refuses_synthetic_bytes(self):
        code, _, done = self.run_download(PAYLOAD, recipe=self.recipe)
        self.assertEqual(code, 1)
        self.assertIn(f'is not the pinned {PIN}', done['error'])

class VendorTest(Fixture):
    def test_admission_needs_the_verified_archive_tools_and_absent_collisions(self):
        self.assertIn('run the download stage first', self.problems('vendor'))
        self.archive()
        report = self.preflight('vendor')
        self.assertEqual(report['problems'], [])
        self.assertEqual(report['facts']['factorySha256'], hashlib.sha256(PAYLOAD).hexdigest())
        self.assertIn(f'is not the pinned {PIN}', ' '.join(bb.preflight(self.recipe, 'vendor', self.op(), statvfs=fs(10 ** 15),
            lock_probe=lambda path: True, root=self.root)['problems']))
        for rel in self.recipe['preparation']['vendor']['collisionPaths']:
            (self.src/rel).mkdir(parents=True)
            self.assertIn(f'{rel} already exists', self.problems('vendor'))
            shutil.rmtree(self.src/rel)
        self.assertIn('below the reserve', self.problems('vendor', available=RESERVE + (128 << 30) - 1))
        os.chmod(self.node, 0o644)
        self.assertIn('not an executable regular file', self.problems('vendor'))
        os.chmod(self.node, 0o755)
        (self.src/'out_adevtool_deps/adevtool_revision').write_text('0' * 40)
        self.assertIn('not the pinned revision', self.problems('vendor'))
        self.assertIn('outside the source tree', self.problems('vendor', download_dir=str(self.src/'dl')))

    def fake(self, node='v24.11.0\n', returncode=0, grow=0, timeout=False):
        calls = []
        def runner(argv, cwd, env, capture_output, text, timeout):
            calls.append((argv, cwd, env.get('ADEVTOOL_IMG_DOWNLOAD_DIR'), timeout))
            if argv[-1] == '--version':
                return types.SimpleNamespace(returncode=0, stdout=node)
            if timeout_flag:
                raise subprocess.TimeoutExpired(argv, timeout)
            vendor = self.src/'vendor/google_devices/blazer'
            vendor.mkdir(parents=True)
            (vendor/'blazer.mk').write_bytes(b'x' * grow)
            return types.SimpleNamespace(returncode=returncode, stdout='')
        timeout_flag = timeout
        return runner, calls

    def run_vendor(self, runner, recipe=None, out='r'):
        code = bb.run(recipe or self.synthetic, 'vendor', self.op(), self.tmp/out, runner=runner, now=lambda: 0,
            statvfs=fs(10 ** 15), lock_probe=lambda path: True, root=self.root)
        return code, json.loads((self.tmp/out/'completed.json').read_text())

    def test_fake_generation_checks_node_then_runs_isolated_and_measures(self):
        self.archive()
        runner, calls = self.fake(grow=10)
        code, done = self.run_vendor(runner)
        self.assertEqual((code, done['ok'], done['boundKind']), (0, True, 'proposal'))
        self.assertGreater(done['consumedBytes'], 0)
        self.assertEqual(calls, [([str(self.node), '--version'], str(self.src), str(self.download), 60),
            (['vendor/adevtool/bin/run', 'generate-all', '-d', 'blazer'], str(self.src), str(self.download), 10800)])
        self.assertEqual((self.download/FACTORY).read_bytes(), PAYLOAD, 'the verified archive is preserved')

    def test_old_node_timeouts_failures_and_bound_overruns_refuse(self):
        self.archive()
        runner, calls = self.fake(node='v20.1.0\n')
        code, done = self.run_vendor(runner)
        self.assertEqual((code, len(calls)), (1, 1), 'generation never starts with an old Node')
        self.assertIn('node must be >= 24', done['error'])
        runner, _ = self.fake(timeout=True)
        code, done = self.run_vendor(runner, out='r2')
        self.assertIn('exceeded 10800 s', done['error'])
        runner, _ = self.fake(returncode=3)
        code, done = self.run_vendor(runner, out='r3')
        self.assertEqual((code, done['returncode']), (1, 3))
        shutil.rmtree(self.src/'vendor/google_devices/blazer')
        tight = json.loads(json.dumps(self.synthetic))
        tight['preparation']['vendor']['boundBytes'] = 1
        runner, _ = self.fake(grow=100000)
        code, done = self.run_vendor(runner, recipe=tight, out='r4')
        self.assertEqual(code, 1)
        self.assertIn('above the 1-byte bound (outputs are kept, not deleted)', done['error'])
        self.assertTrue((self.src/'vendor/google_devices/blazer/blazer.mk').exists())

class LunchTest(Fixture):
    def test_admission_needs_the_generated_vendor_tree_and_a_fresh_out_dir(self):
        self.assertIn('run the vendor stage first', self.problems('lunch'))
        self.generated()
        self.assertEqual(self.preflight('lunch')['problems'], [])
        (self.src/'out-blazer-lunch').mkdir()
        self.assertIn('out-blazer-lunch already exists', self.problems('lunch'))
        self.assertIn('below the reserve', self.problems('lunch', available=RESERVE))

    def run_lunch(self, stdout, returncode=0, out='r'):
        calls = []
        def runner(argv, cwd, env, capture_output, text, timeout):
            calls.append((argv[:2], env.get('OUT_DIR'), capture_output, timeout))
            return types.SimpleNamespace(returncode=returncode, stdout=stdout)
        code = bb.run(self.synthetic, 'lunch', self.op(), self.tmp/out, runner=runner, now=lambda: 0,
            statvfs=fs(10 ** 15), lock_probe=lambda path: True, root=self.root)
        return code, calls, json.loads((self.tmp/out/'completed.json').read_text())

    def test_lunch_targets_must_match(self):
        self.generated()
        good = 'TARGET_PRODUCT=blazer\nTARGET_RELEASE=cur\nTARGET_BUILD_VARIANT=user\nTARGET_DEVICE=blazer\n'
        code, calls, done = self.run_lunch(good)
        self.assertEqual((code, done['ok']), (0, True))
        self.assertEqual(calls, [(['bash', '-c'], 'out-blazer-lunch', True, 1800)])
        code, _, done = self.run_lunch(good.replace('=user', '=userdebug'), out='r2')
        self.assertEqual(code, 1)
        self.assertIn('lunch targets', done['error'])
        code, _, done = self.run_lunch('', returncode=1, out='r3')
        self.assertEqual((code, done['returncode']), (1, 1))

class CompileTest(Fixture):
    def test_integrated_tree_is_admitted(self):
        self.generated()
        report = self.preflight('compile', integration_dir=str(self.integrated()))
        self.assertEqual(report['problems'], [])
        self.assertEqual(report['facts']['roseIntegration']['receipt'], '0001-blazer.json')

    def test_missing_vendor_or_integration_and_foreign_provenance_refuse(self):
        receipts = self.integrated()
        self.assertIn('run the vendor stage first', ' '.join(self.preflight('compile', integration_dir=str(receipts))['problems']))
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
        agent = self.src/'packages/apps/PixelBackgroundControl/Agent.apk'
        original = agent.read_bytes()
        agent.write_bytes(b'changed')
        self.assertIn('no longer matches', ' '.join(self.preflight('compile', integration_dir=str(receipts))['problems']))
        agent.write_bytes(original)
        self.assertIn('blazer local-inputs.json',
            ' '.join(self.preflight('compile', integration_dir=str(receipts), inputs_receipt=None)['problems']))
        body = json.loads((receipts/'0001-blazer.json').read_text())
        (receipts/'0002-other-build.json').write_text(json.dumps(dict(body, label='blazer-2026103199')))
        self.assertIn('not blazer-2026103150', ' '.join(self.preflight('compile', integration_dir=str(receipts))['problems']))
        (receipts/'0002-other-build.json').unlink()
        elsewhere = self.tmp/'foreign.json'
        shutil.copyfile(receipts/'0001-blazer.json', elsewhere)
        os.symlink(elsewhere, receipts/'0003-link.json')
        self.assertIn('is a link out of', ' '.join(self.preflight('compile', integration_dir=str(receipts))['problems']))

class RunTest(Fixture):
    def test_not_ready_and_existing_receipts_never_run(self):
        ran = []
        runner = lambda *a, **k: ran.append(a) or types.SimpleNamespace(returncode=0, stdout='')
        code = bb.run(self.synthetic, 'vendor', self.op(), self.tmp/'r1', runner=runner, now=lambda: 0,
            statvfs=fs(10 ** 15), lock_probe=lambda path: True, root=self.root)
        self.assertEqual((code, ran), (1, []))
        self.assertFalse(json.loads((self.tmp/'r1/preflight.json').read_text())['ready'])
        with self.assertRaisesRegex(bb.InvalidInput, 'already exists'):
            bb.run(self.synthetic, 'vendor', self.op(), self.tmp/'r1', runner=runner, root=self.root)

    def test_compile_still_needs_a_measured_allowance(self):
        self.generated()
        report = bb.preflight(self.recipe, 'compile', self.op(integration_dir=str(self.integrated())), statvfs=fs(10 ** 15),
            lock_probe=lambda path: True)
        self.assertIn('compileAllowanceBytes is unmeasured', ' '.join(report['problems']))

if __name__ == '__main__':
    unittest.main()
