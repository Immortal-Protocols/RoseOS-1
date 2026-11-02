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
import socket
import subprocess
import sys
import tempfile
import time
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
    return lambda path: types.SimpleNamespace(f_bavail=available, f_frsize=1, f_blocks=available * 2)

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
        # Test-speed sampling (the checked-in recipe samples every 5 s with a 30 s grace).
        self.synthetic['preparation']['limits'].update(sampleSeconds=0.05, killGraceSeconds=1)
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
        (self.tmp/'nodebin').mkdir()
        self.node = self.tmp/'nodebin/node'
        self.script(self.node, 'print("v24.11.0")')

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

    def script(self, path, body):
        """A real executable synthetic child (Python), never a real tool."""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f'#!{sys.executable}\nimport os, sys, time, shutil, subprocess\n{body}\n')
        os.chmod(path, 0o755)

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
        self.assertEqual(rendered['vendor']['env'], {'ADEVTOOL_IMG_DOWNLOAD_DIR': str(self.download),
            'PATH': f'{self.node.parent}:/usr/bin:/bin'})
        self.assertEqual(rendered['vendor'], {'cwd': str(self.src), 'env': {'ADEVTOOL_IMG_DOWNLOAD_DIR': str(self.download)},
            'argv': ['vendor/adevtool/bin/run', 'generate-all', '-d', 'blazer'], 'timeoutSeconds': 10800,
            'nodeVersion': [str(self.node), '--version']} | {'env': rendered['vendor']['env']})
        self.assertEqual(rendered['lunch']['env'], {'OUT_DIR': 'out-blazer-lunch', 'PATH': '/usr/bin:/bin'})
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
            (lambda r: r['preparation'].update(totalTimeoutSeconds=2700), 'sum of the stage timeouts'),
            (lambda r: r['preparation']['limits'].update(enforcement='quota'), 'sampled-stop enforcement'),
            (lambda r: r['preparation']['limits'].update(sampleSeconds=0), 'sampled-stop enforcement'),
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

class LunchTest(Fixture):
    def test_admission_needs_the_generated_vendor_tree_and_a_fresh_out_dir(self):
        self.assertIn('run the vendor stage first', self.problems('lunch'))
        self.generated()
        self.assertEqual(self.preflight('lunch')['problems'], [])
        (self.src/'out-blazer-lunch').mkdir()
        self.assertIn('out-blazer-lunch already exists', self.problems('lunch'))
        self.assertIn('below the reserve', self.problems('lunch', available=RESERVE))


def alive(pid):
    """True while PID exists and is not a zombie (the container's init may not reap reparented children promptly)."""
    try:
        state = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()[0]
    except (FileNotFoundError, ProcessLookupError, IndexError):
        return False
    return state != 'Z'

def wait_dead(pid, seconds=5):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline and alive(pid):
        time.sleep(0.05)
    return not alive(pid)

LIMITS = {'sampleSeconds': 0.05, 'killGraceSeconds': 1, 'boundBytes': 10 ** 9, 'reserveBytes': 0}

class SuperviseTest(Fixture):
    """Real synthetic child processes under supervise(): what is stopped, what is cleaned up, and what is not reached."""
    def child(self, body):
        path = self.tmp/'child'
        self.script(path, body)
        return [str(path)]

    def test_timeout_ends_the_whole_process_group(self):
        pids = self.tmp/'pids'
        argv = self.child(f'p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])\n'
                          f'open({str(pids)!r}, "w").write(str(p.pid))\ntime.sleep(60)')
        result = bb.supervise(argv, str(self.tmp), {'PATH': '/usr/bin:/bin'}, 0.5, LIMITS, statvfs=fs(10 ** 15))
        self.assertEqual(result['stopped'], 'exceeded 0.5 s')
        self.assertEqual(result['enforcement'], 'sampled-stop')
        self.assertTrue(wait_dead(int(pids.read_text())), 'the grandchild in the group is ended too')

    def test_members_left_after_a_normal_exit_are_ended(self):
        pids = self.tmp/'pids'
        argv = self.child(f'p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])\n'
                          f'open({str(pids)!r}, "w").write(str(p.pid))')
        result = bb.supervise(argv, str(self.tmp), {'PATH': '/usr/bin:/bin'}, 30, LIMITS, statvfs=fs(10 ** 15))
        self.assertEqual((result['returncode'], result['stopped']), (0, None))
        self.assertEqual(result['leftoverGroupEnded'], 'SIGTERM')
        self.assertTrue(wait_dead(int(pids.read_text())))

    def test_sigterm_ignoring_members_get_sigkill(self):
        argv = self.child('import signal\nsignal.signal(signal.SIGTERM, signal.SIG_IGN)\ntime.sleep(60)')
        started = time.monotonic()
        result = bb.supervise(argv, str(self.tmp), {'PATH': '/usr/bin:/bin'}, 0.3, LIMITS, statvfs=fs(10 ** 15))
        self.assertEqual(result['returncode'], -9)
        self.assertLess(time.monotonic() - started, 10)

    def test_a_descendant_that_starts_its_own_session_is_not_reached(self):
        pids = self.tmp/'pids'
        argv = self.child(f'p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], start_new_session=True)\n'
                          f'open({str(pids)!r}, "w").write(str(p.pid))')
        bb.supervise(argv, str(self.tmp), {'PATH': '/usr/bin:/bin'}, 30, LIMITS, statvfs=fs(10 ** 15))
        escaped = int(pids.read_text())
        try:
            self.assertTrue(alive(escaped), 'documented gap: setsid descendants need cgroup containment')
        finally:
            os.kill(escaped, 9)

    def test_watched_bytes_and_the_reserve_stop_the_group_while_running(self):
        watched = self.tmp/'watched'
        argv = self.child(f'os.makedirs({str(watched)!r}, exist_ok=True)\n'
                          f'for i in range(10000):\n    open(os.path.join({str(watched)!r}, str(i)), "wb").write(b"x" * 65536)\n'
                          f'    time.sleep(0.01)')
        result = bb.supervise(argv, str(self.tmp), {'PATH': '/usr/bin:/bin'}, 60, dict(LIMITS, boundBytes=1 << 20),
                              watch=[str(watched)], statvfs=fs(10 ** 15))
        self.assertIn('above the 1048576-byte bound', result['stopped'])
        self.assertGreater(result['consumedBytes'], 1 << 20)
        self.assertLess(len(list(watched.iterdir())), 10000, 'stopped while running, not after')
        argv = self.child('time.sleep(60)')
        result = bb.supervise(argv, str(self.tmp), {'PATH': '/usr/bin:/bin'}, 60, dict(LIMITS, reserveBytes=100),
                              statvfs=fs(50))
        self.assertEqual(result['stopped'], 'available 50 fell below the reserve 100')

class VendorRunTest(Fixture):
    def setUp(self):
        super().setUp()
        self.archive()
        self.seen = self.tmp/'node-seen'
        self.script(self.src/'vendor/adevtool/bin/run', f"""mode = os.environ.get('FAKE_MODE', 'ok')
open({str(self.seen)!r}, 'w').write(shutil.which('node') or '')
vendor = os.path.join('vendor', 'google_devices', 'blazer')
os.makedirs(vendor)
open(os.path.join(vendor, 'blazer.mk'), 'w').write('# synthetic')
if mode == 'grow':
    for i in range(100000):
        open(os.path.join(vendor, f'blob{{i}}'), 'wb').write(b'x' * 65536)
        time.sleep(0.005)
if mode == 'sleep':
    time.sleep(60)
sys.exit(3 if mode == 'fail' else 0)""")

    def run_vendor(self, mode='ok', recipe=None, out='r'):
        os.environ['FAKE_MODE'] = mode
        self.addCleanup(os.environ.pop, 'FAKE_MODE', None)
        code = bb.run(recipe or self.synthetic, 'vendor', self.op(), self.tmp/out, now=lambda: 0,
            statvfs=fs(10 ** 15), lock_probe=lambda path: True, root=self.root)
        return code, json.loads((self.tmp/out/'completed.json').read_text())

    def test_generation_runs_isolated_with_the_verified_node_on_path(self):
        code, done = self.run_vendor()
        self.assertEqual((code, done['ok'], done['enforcement']), (0, True, 'sampled-stop'))
        self.assertEqual(self.seen.read_text(), str(self.node), 'adevtool resolves exactly the verified node')
        self.assertEqual(done['node']['inode'], os.stat(self.node).st_ino)
        self.assertGreater(done['consumedBytes'], 0)
        self.assertEqual((self.download/FACTORY).read_bytes(), PAYLOAD, 'the verified archive is preserved')

    def test_old_node_failures_timeouts_and_bound_overruns_refuse(self):
        self.script(self.node, 'print("v20.1.0")')
        code, done = self.run_vendor()
        self.assertEqual(code, 1)
        self.assertIn('node must be >= 24', done['error'])
        self.assertFalse(self.seen.exists(), 'generation never starts with an old Node')
        self.script(self.node, 'print("v24.11.0")')
        code, done = self.run_vendor('fail', out='r2')
        self.assertEqual((code, done['returncode']), (1, 3))
        shutil.rmtree(self.src/'vendor/google_devices/blazer')
        quick = json.loads(json.dumps(self.synthetic))
        quick['preparation']['vendor']['timeoutSeconds'] = 0.5
        code, done = self.run_vendor('sleep', recipe=quick, out='r3')
        self.assertIn('stopped: exceeded 0.5 s', done['error'])
        shutil.rmtree(self.src/'vendor/google_devices/blazer')
        tight = json.loads(json.dumps(self.synthetic))
        tight['preparation']['vendor']['boundBytes'] = 1 << 20
        code, done = self.run_vendor('grow', recipe=tight, out='r4')
        self.assertEqual(code, 1)
        self.assertIn('above the 1048576-byte bound (outputs are kept, not deleted)', done['error'])
        self.assertTrue((self.src/'vendor/google_devices/blazer/blazer.mk').exists())

    def test_overshoot_between_samples_is_still_refused_after_exit(self):
        slow = json.loads(json.dumps(self.synthetic))
        slow['preparation']['limits']['sampleSeconds'] = 30
        slow['preparation']['vendor']['boundBytes'] = 1 << 20
        self.script(self.src/'vendor/adevtool/bin/run', """vendor = os.path.join('vendor', 'google_devices', 'blazer')
os.makedirs(vendor)
open(os.path.join(vendor, 'blob'), 'wb').write(b'x' * (4 << 20))""")
        code, done = self.run_vendor(recipe=slow)
        self.assertEqual((code, done['stopped']), (1, None))
        self.assertIn('bound between samples (outputs are kept, not deleted)', done['error'])

    def test_node_must_be_named_node_resolve_through_path_and_not_change(self):
        other = self.tmp/'nodebin/nodejs'
        self.script(other, 'print("v24.11.0")')
        self.assertIn('must be named node', self.problems('vendor', node=str(other)))
        report = self.preflight('vendor')
        self.assertEqual(report['facts']['node']['inode'], os.stat(self.node).st_ino)
        self.assertIsNone(bb.node_binding_problem(self.node, f'{self.node.parent}:/usr/bin:/bin'))
        self.assertIn('not the verified', bb.node_binding_problem(self.node, '/usr/bin:/bin'))
        # Replaced between preflight and run: refused before anything starts.
        original = bb.preflight
        def swapped(*a, **k):
            report = original(*a, **k)
            self.node.unlink()
            self.script(self.node, 'print("v24.11.0")')
            return report
        bb.preflight = swapped
        self.addCleanup(setattr, bb, 'preflight', original)
        code, done = self.run_vendor(out='r5')
        self.assertEqual(code, 1)
        self.assertIn('node changed', done['error'])
        self.assertFalse(self.seen.exists())

class LunchRunTest(Fixture):
    def envsetup(self, variant='user'):
        (self.src/'build/envsetup.sh').write_text(f"""lunch() {{ export TARGET_PRODUCT=blazer TARGET_RELEASE=cur TARGET_BUILD_VARIANT={variant}; mkdir -p "$OUT_DIR"; }}
get_build_var() {{ echo blazer; }}
""")

    def run_lunch(self, out='r'):
        code = bb.run(self.synthetic, 'lunch', self.op(), self.tmp/out, now=lambda: 0,
            statvfs=fs(10 ** 15), lock_probe=lambda path: True, root=self.root)
        return code, json.loads((self.tmp/out/'completed.json').read_text())

    def test_lunch_targets_must_match(self):
        self.generated()
        self.envsetup()
        code, done = self.run_lunch()
        self.assertEqual((code, done['ok']), (0, True), done)
        self.assertEqual(done['targets'], {'TARGET_PRODUCT': 'blazer', 'TARGET_RELEASE': 'cur', 'TARGET_BUILD_VARIANT': 'user',
            'TARGET_DEVICE': 'blazer'})
        self.assertTrue((self.src/'out-blazer-lunch').is_dir())
        shutil.rmtree(self.src/'out-blazer-lunch')
        self.envsetup('userdebug')
        code, done = self.run_lunch(out='r2')
        self.assertEqual(code, 1)
        self.assertIn('lunch targets', done['error'])

class InspectTest(Fixture):
    """inspect is metadata-only: candidates are proposals that never enable preparation, and nothing it scans changes."""
    def snapshot(self, *roots):
        state = []
        for root in roots:
            for path in sorted([root, *root.rglob('*')]) if root.exists() else []:
                st = os.lstat(path)
                content = path.read_bytes() if path.is_file() and not path.is_symlink() else None
                state.append((str(path), st.st_mode, st.st_size, st.st_mtime_ns, st.st_ctime_ns, content))
        return state

    def guard(self):
        """Any process start or network connection fails the test."""
        boom = lambda *a, **k: (_ for _ in ()).throw(AssertionError('inspect started a process or opened a connection'))
        for owner, name in ((subprocess, 'Popen'), (os, 'fork'), (os, 'posix_spawn'), (os, 'system'),
                            (bb.urllib.request, 'urlopen'), (socket, 'create_connection')):
            original = getattr(owner, name)
            setattr(owner, name, boom)
            self.addCleanup(setattr, owner, name, original)

    def reference(self):
        """A synthetic stand-in for the historical production tree (never the real one)."""
        ref = self.production
        for rel in bb.TREE_NEEDS + ('vendor/state/README', self.recipe['kernelDir'] + '/.keep', '.repo/manifest.xml'):
            (ref/rel).parent.mkdir(parents=True, exist_ok=True)
            (ref/rel).write_text('synthetic\n')
        (ref/'out_adevtool_deps').mkdir(exist_ok=True)
        (ref/'out_adevtool_deps/adevtool_revision').write_text(ADEVTOOL + '\n')
        return ref

    def test_candidates_are_reported_but_never_bound(self):
        ref = self.reference()
        cand = self.tmp/'rose-actions/blazer-prep-41000000001-1'
        (cand/'downloads').mkdir(parents=True)
        (cand/'downloads'/FACTORY).write_bytes(PAYLOAD)
        nodebin = self.tmp/'runner-bin'
        nodebin.mkdir()
        os.symlink(self.node, nodebin/'node')
        before = self.snapshot(ref, cand, self.tmp/'nodebin', self.lock.parent/'dl') + [os.lstat(self.lock).st_mtime_ns if self.lock.exists() else None]
        self.guard()
        unbound = self.op(source_root=None, download_dir=None, node=None)
        report = bb.inspect(self.synthetic, unbound, root=self.root, statvfs=fs(10 ** 15),
                            candidates=bb.candidate_paths(str(cand)), environ={'PATH': f'{nodebin}:/usr/bin:/bin'})
        self.assertEqual(self.snapshot(ref, cand, self.tmp/'nodebin', self.lock.parent/'dl')
                         + [os.lstat(self.lock).st_mtime_ns if self.lock.exists() else None], before, 'inspect changes nothing')
        self.assertFalse((cand/'source').exists() or (cand/'receipts').exists(), 'inspect creates no candidate path')
        self.assertEqual((report['schema'], report['metadataOnly'], report['processesStarted'], report['network'],
                          report['writes'], report['enablesPreparation']),
                         ('roseos.blazer-preparation-inspection.v2', True, 0, False, False, False))
        self.assertEqual(report['bound'], {'sourceRoot': None, 'downloadDir': None, 'node': None})
        self.assertIn('bound sourceRoot is null: preparation stays refused until the binding is supplied', report['unknowns'])
        c = report['candidates']
        self.assertTrue(c['proposed'])
        self.assertEqual(c['sourceRoot']['path'], str(cand/'source'))
        self.assertFalse(c['sourceRoot']['meta']['exists'])
        self.assertEqual(c['sourceRoot']['filesystem']['queried'], str(cand))
        self.assertIsNone(c['sourceRoot']['isolation'])
        self.assertEqual(c['downloadDir']['archive']['size'], len(PAYLOAD))
        self.assertFalse(c['downloadDir']['hashed'])
        self.assertTrue(c['downloadDir']['sameFilesystemAsReference'])
        self.assertIn(f'candidate sourceRoot {cand/"source"} does not exist (proposal only)', report['unknowns'])
        r = report['reference']
        self.assertEqual((r['path'], r['isProduction']), (str(ref), True))
        self.assertEqual(r['adevtoolRevision'], ADEVTOOL)
        self.assertTrue(r['projects']['build/envsetup.sh']['exists'])
        self.assertEqual((r['projects']['vendor/adevtool']['exists'], r['projects']['vendor/adevtool']['type']), (True, 'dir'))
        self.assertGreater(r['bytes']['bytes'], 0)
        n = report['node']['runner']
        self.assertEqual((n['path'], n['realpath'], n['nameIsNode'], n['executable']), (str(nodebin/'node'), str(self.node), True, True))
        self.assertEqual(n['pathMeta']['type'], 'link')
        self.assertEqual(n['realpathMeta']['inode'], os.stat(self.node).st_ino)
        self.assertEqual(n['version'], 'not executed (metadata only)')
        for unknown in ('reflink support (never assumed)', 'isolated source copy byte and time cost (separately scoped)',
                        'Pro factory archive size (8 GiB cap unverified)'):
            self.assertIn(unknown, report['unknowns'])

    def test_production_candidates_are_flagged_and_walks_are_bounded(self):
        self.reference()
        report = bb.inspect(self.synthetic, self.op(), root=self.root, statvfs=fs(10 ** 15),
                            candidates=bb.candidate_paths(None, sourceRoot=str(self.production/'nested')), environ={'PATH': ''})
        self.assertIn('production source root', report['candidates']['sourceRoot']['isolation'])
        self.assertFalse(report['node']['runner']['found'])
        self.assertIn('no node on the runner PATH', report['unknowns'])
        bounded = bb.inspect(self.synthetic, self.op(), root=self.root, statvfs=fs(10 ** 15), max_entries=1, environ={'PATH': ''})
        self.assertTrue(bounded['truncated'])
        self.assertLessEqual(bounded['reference']['bytes']['entries'] + sum(
            c['entries'] for c in bounded['reference']['collisions'].values()), 1)
        self.assertIn('a bounded walk stopped at --max-entries or --max-seconds: sizes are lower bounds', bounded['unknowns'])
        with self.assertRaisesRegex(bb.InvalidInput, 'max-seconds'):
            bb.inspect(self.synthetic, self.op(), root=self.root, max_seconds=1201)
        with self.assertRaisesRegex(bb.InvalidInput, 'absolute'):
            bb.candidate_paths('relative/root')

    def test_candidates_never_enable_preparation(self):
        cand = str(self.tmp/'cand')
        for action in ('plan', 'preflight', 'run'):
            cli = subprocess.run([sys.executable, str(ROOT/'scripts/blazer-build-only.py'), action, '--stage', 'vendor', '--host', 'h',
                '--lock', str(self.lock), '--candidate-root', cand, '--receipt-dir', str(self.tmp/f'r-{action}')],
                capture_output=True, text=True)
            self.assertEqual(cli.returncode, 2, action)
            self.assertIn('inspect-only', cli.stderr)
            self.assertFalse((self.tmp/f'r-{action}').exists())
        unbound = subprocess.run([sys.executable, str(ROOT/'scripts/blazer-build-only.py'), 'preflight', '--stage', 'vendor', '--host',
            'h', '--lock', str(self.lock)], capture_output=True, text=True)
        self.assertEqual(unbound.returncode, 1)
        self.assertIn('vendor needs --download-dir, --source-root, --node', unbound.stdout)

    def test_cli_inspect_needs_no_stage_and_accepts_candidates(self):
        cli = subprocess.run([sys.executable, str(ROOT/'scripts/blazer-build-only.py'), 'inspect', '--host', 'operator-builder-1',
            '--lock', str(self.lock), '--candidate-root', str(self.tmp/'rose-actions/blazer-prep-1-1'),
            '--candidate-node', str(self.node), '--reference-source-root', str(self.src), '--max-entries', '1000'],
            capture_output=True, text=True)
        self.assertEqual(cli.returncode, 0, cli.stderr)
        report = json.loads(cli.stdout)
        self.assertEqual(report['schema'], 'roseos.blazer-preparation-inspection.v2')
        self.assertEqual(report['candidates']['receiptsDir']['path'], str(self.tmp/'rose-actions/blazer-prep-1-1/receipts'))
        self.assertEqual(report['node']['candidate']['realpath'], str(self.node))
        self.assertFalse(report['reference']['isProduction'])
        bad = subprocess.run([sys.executable, str(ROOT/'scripts/blazer-build-only.py'), 'preflight', '--host', 'h', '--lock',
            str(self.lock)], capture_output=True, text=True)
        self.assertEqual(bad.returncode, 2)
        self.assertIn('needs --stage', bad.stderr)

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
        marker = self.tmp/'ran'
        self.script(self.src/'vendor/adevtool/bin/run', f'open({str(marker)!r}, "w").write("ran")')
        code = bb.run(self.synthetic, 'vendor', self.op(), self.tmp/'r1', now=lambda: 0,
            statvfs=fs(10 ** 15), lock_probe=lambda path: True, root=self.root)
        self.assertEqual(code, 1)
        self.assertFalse(marker.exists(), 'a not-ready stage starts no process')
        self.assertFalse(json.loads((self.tmp/'r1/preflight.json').read_text())['ready'])
        with self.assertRaisesRegex(bb.InvalidInput, 'already exists'):
            bb.run(self.synthetic, 'vendor', self.op(), self.tmp/'r1', root=self.root)

    def test_compile_still_needs_a_measured_allowance(self):
        self.generated()
        report = bb.preflight(self.recipe, 'compile', self.op(integration_dir=str(self.integrated())), statvfs=fs(10 ** 15),
            lock_probe=lambda path: True)
        self.assertIn('compileAllowanceBytes is unmeasured', ' '.join(report['problems']))

if __name__ == '__main__':
    unittest.main()
