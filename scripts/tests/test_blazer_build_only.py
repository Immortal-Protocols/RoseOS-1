"""FAM-BLAZER-BUILD-004 / BLAZER-PREP: the blazer recipe (os/build-only/blazer.json) and scripts/blazer-build-only.py.

Exercises the real load, operator, commands, preflight and run functions against synthetic source trees, receipts, a fake
fetcher and a fake runner. Nothing downloads, generates, runs lunch, compiles or signs, and no host or phone is reached;
a passing run proves admission, bounds and rendering logic only, never a real preparation or build."""
from pathlib import Path
import datetime
import hashlib
import importlib.util
import io
import json
import os
import shutil
import socket
import subprocess
import sys
import signal
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
    def __init__(self, data, headers=None):
        super().__init__(data)
        self.headers = headers if headers is not None else {'Content-Length': str(len(data))}

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
        self.synthetic['factory']['sizeBytes'] = len(PAYLOAD)
        self.synthetic['preparation']['download']['boundBytes'] = len(PAYLOAD)
        self.synthetic['capacity']['compileAllowanceBytes'] = 10
        # Test-speed sampling (the checked-in recipe samples every 5 s with a 30 s grace).
        self.synthetic['preparation']['limits'].update(sampleSeconds=0.05, killGraceSeconds=1)
        # The ancestor boundary is the parent of the (temporary) production root, as the recipe derives it.
        self.synthetic['preparation']['candidateLayout']['ancestorBoundary'] = str(self.tmp)
        # inspect-git's fixed targets, under the temporary production root and boundary.
        self.synthetic['preparation']['gitInspection']['targets'] = {
            'production': f'{self.tmp}/production/vendor/adevtool/.git',
            'candidate': f'{self.tmp}/rose-actions/blazer-prep-41473133543-1/source/vendor/adevtool/.git'}
        self.synthetic['preparation']['gitInspection']['targetSets'] = {'adevtool-projects-gitdir': {'projectsGitdir': {
            'root': f'{self.tmp}/production', 'path': '.repo/projects/vendor/adevtool.git'}}}
        self.synthetic['preparation']['gitObjectsInspection']['root'] = f'{self.tmp}/production'
        # A checked-in-source copy whose production source root is a temporary tree, so isolation is exercised for real.
        self.root = self.tmp/'repo'
        (self.root/'os/release-pipeline').mkdir(parents=True)
        self.production = self.tmp/'production'
        self.production.mkdir()
        (self.root/'os/release-pipeline/frankel.json').write_text(json.dumps(
            {'host': {'sourceRoot': str(self.production), 'lockPath': str(self.tmp/'frankel-build.lock')}}))
        # The source stage's exclusion policy is the real checked-in alpha policy.
        shutil.copy(ROOT/'os/release-pipeline/cuttlefish-alpha.json', self.root/'os/release-pipeline/cuttlefish-alpha.json')
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
        # Preflight only checks that adevtool's git metadata exists; vendor run() asks git itself (VendorRunTest).
        (self.src/'vendor/adevtool/.git').mkdir()
        self.lock = self.tmp/'frankel-build.lock'
        self.download = self.tmp/'dl'
        (self.tmp/'nodebin').mkdir()
        self.node = self.tmp/'nodebin/node'
        self.script(self.node, 'print("v24.11.0")')

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
        boom = lambda *a, **k: (_ for _ in ()).throw(AssertionError('a process was started or opened a connection'))
        for owner, name in ((subprocess, 'Popen'), (os, 'fork'), (os, 'posix_spawn'), (os, 'system'),
                            (bb.urllib.request, 'urlopen'), (socket, 'create_connection')):
            original = getattr(owner, name)
            setattr(owner, name, boom)
            self.addCleanup(setattr, owner, name, original)

    def op(self, identity=False, **change):
        values = dict(host='operator-builder-1', source_root=str(self.src), jobs=16, lock=str(self.lock),
            download_dir=str(self.download), integration_dir=None, inputs_receipt=getattr(self, 'inputs', None),
            node=str(self.node))
        if identity:
            values.update(self.identity())
        values.update(change)
        return bb.operator(**values)

    def identity(self):
        """The bound compile identity: the admitted inputs' build and a timestamp at noon UTC of that build's day."""
        build = json.loads(Path(self.inputs).read_text())['candidate']['build'] if getattr(self, 'inputs', None) else '2026103150'
        noon = datetime.datetime.strptime(build[:8], '%Y%m%d').replace(hour=12, tzinfo=datetime.timezone.utc)
        return dict(build_number=build, build_datetime=str(int(noon.timestamp())))

    def preflight(self, stage, recipe=None, available=10 ** 15, lock_free=True, **change):
        return bb.preflight(recipe or self.synthetic, stage, self.op(identity=stage == 'compile', **change), statvfs=fs(available),
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
        self.reserve(receipts)
        return receipts

    def reserve(self, integration, **change):
        """The build's write-once blazer-allocate reservation beside the integration dir, reserved at noon UTC of its day."""
        ident = self.identity()
        noon = datetime.datetime.fromtimestamp(int(ident['build_datetime']), datetime.timezone.utc)
        record = {'schema': 'roseos.blazer-allocation.v1', 'device': 'blazer', 'build': ident['build_number'], 'source': 'a' * 40,
                  'runId': '1', 'runAttempt': '1', 'reservedAt': noon.strftime('%Y-%m-%dT%H:%M:%SZ'),
                  'workDir': str(integration.parent/f'blazer-integrate-{ident["build_number"]}'), 'integrationDir': str(integration),
                  'observed': {}}
        record.update(change)
        path = integration.parent/'blazer-allocations'/f'{ident["build_number"]}.json'
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps(record))
        return path

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
        self.assertEqual((prep['stages'], prep['boundsKind']), (['source', 'download', 'vendor', 'lunch'], 'proposal'))
        self.assertEqual({s: (prep[s]['boundBytes'], prep[s]['timeoutSeconds']) for s in prep['stages']},
            {'source': (150 << 30, 7200), 'download': (11362467847, 3600), 'vendor': (128 << 30, 10800), 'lunch': (8 << 30, 1800)})
        self.assertEqual(recipe['factory']['sizeBytes'], 11362467847, 'the pinned URL\'s HEAD Content-Length')
        self.assertIn('no body transferred', recipe['factory']['sizeBasis'])
        self.assertEqual(prep['totalTimeoutSeconds'], 23400)
        self.assertEqual(prep['source']['cleanupSeconds'], 240)
        objects = dict(prep['gitObjectsInspection'])
        self.assertIsInstance(objects.pop('basis'), str)
        self.assertEqual(objects, {'host': 'immortal-os-builder', 'root': '/home/pixelbuild/grapheneos-2026100600',
            'path': '.repo/project-objects/adevtool.git/objects', 'maxEntries': 100000, 'maxDepth': 3, 'maxSeconds': 120,
            'maxOutputBytes': 65536, 'excludeNames': ['hooks', 'rr-cache'],
            'existenceOnly': ['info/alternates', 'info/http-alternates']})
        self.assertEqual(prep['gitInspection']['targetSets'], {'adevtool-projects-gitdir': {'projectsGitdir': {
            'root': '/home/pixelbuild/grapheneos-2026100600', 'path': '.repo/projects/vendor/adevtool.git'}}})
        self.assertEqual((prep['source']['includePaths'], prep['source']['exclusionsFrom']),
            (['out_adevtool_deps/adevtool_revision', 'out_adevtool_deps/host/linux-x86'],
             'os/release-pipeline/cuttlefish-alpha.json preparation.exclusions'))
        policy = bb.pointer(ROOT, prep['source']['exclusionsFrom'])
        self.assertEqual((policy['rootNames'], policy['rootPrefixes'], policy['anywhereNames']),
            (['.repo', 'keys', 'releases', 'out'], ['out-', 'out_'], ['.git', '.repo']))
        self.assertIn('never used as an estimate', prep['source']['boundBasis'])
        self.assertEqual(bb.pointer(ROOT, prep['productionSourceRootFrom']), '/home/pixelbuild/grapheneos-2026100600')
        self.assertEqual(bb.pointer(ROOT, prep['lockFrom']), '/home/pixelbuild/frankel-build.lock')
        preflight = importlib.util.spec_from_file_location('bpp', ROOT/'scripts/blazer-preparation-preflight.py')
        module = importlib.util.module_from_spec(preflight)
        preflight.loader.exec_module(module)
        self.assertEqual(prep['vendor']['node']['minMajor'], module.NODE_MIN_MAJOR)
        self.assertNotIn('generateAllowanceBytes', recipe['capacity'], 'the circular measured-allowance gate is removed')
        self.assertEqual((recipe['capacity']['compileAllowanceBytes'], recipe['capacity']['compileAllowanceKind']),
                         (250 << 30, 'first-build-proposal'), 'a finite first-build allowance, no prior measurement gate')
        self.assertEqual((recipe['compile']['timeoutSeconds'], recipe['compile']['measure']), (28800, 'filesystem-available-drop'))
        self.assertNotIn('host', recipe, 'the recipe binds no host; the operator supplies it')
        for absent in ('S007', 'S008', 'peak'):
            self.assertNotIn(absent, json.dumps(recipe))

    def test_rendered_commands(self):
        rendered = bb.commands(self.recipe, self.op(build_number='2026103150', build_datetime='1793448000'))
        self.assertEqual(rendered['download'], {'url': 'https://dl.google.com/dl/android/aosp/' + FACTORY,
            'part': str(self.download/(FACTORY + '.part')), 'final': str(self.download/FACTORY), 'sha256': PIN,
            'maxBytes': 11362467847, 'expectedBytes': 11362467847, 'timeoutSeconds': 3600})
        self.assertEqual(rendered['vendor']['env'], {'ADEVTOOL_IMG_DOWNLOAD_DIR': str(self.download),
            'PATH': '<receipt-dir>/node-path:/usr/bin:/bin', 'GIT_CEILING_DIRECTORIES': str(self.src),
            'ADEVTOOL_SKIP_DEP_BUILD': '1'})
        self.assertEqual((rendered['vendor']['adevtoolHead'], rendered['vendor']['adevtoolHeadTimeoutSeconds']),
                         (['git', '-C', 'vendor/adevtool', 'rev-parse', 'HEAD'], 60))
        self.assertEqual(rendered['source'], {'base': '/home/pixelbuild/grapheneos-2026100600', 'target': str(self.src),
            'staging': str(self.tmp/'.source-staging-<nonce>'), 'includePaths': ['out_adevtool_deps/adevtool_revision', 'out_adevtool_deps/host/linux-x86'],
            'exclusionsFrom': 'os/release-pipeline/cuttlefish-alpha.json preparation.exclusions', 'maxBytes': 150 << 30,
            'timeoutSeconds': 7200, 'processes': 0, 'method': self.recipe['preparation']['source']['method']})
        self.assertEqual(rendered['vendor'], {'cwd': str(self.src), 'env': {'ADEVTOOL_IMG_DOWNLOAD_DIR': str(self.download)},
            'argv': ['vendor/adevtool/bin/run', 'generate-all', '-d', 'blazer'], 'timeoutSeconds': 10800,
            'nodeVersion': [str(self.node), '--version'], 'adevtoolHead': rendered['vendor']['adevtoolHead'],
            'adevtoolHeadTimeoutSeconds': 60} | {'env': rendered['vendor']['env']})
        self.assertEqual(rendered['lunch']['env'], {'OUT_DIR': 'out-blazer-lunch', 'PATH': '/usr/bin:/bin',
                                                    'GIT_CEILING_DIRECTORIES': str(self.src)})
        self.assertEqual(rendered['lunch']['argv'][2], 'source build/envsetup.sh && lunch blazer-cur-user && echo TARGET_PRODUCT=$TARGET_PRODUCT'
            ' && echo TARGET_RELEASE=$TARGET_RELEASE && echo TARGET_BUILD_VARIANT=$TARGET_BUILD_VARIANT'
            ' && echo TARGET_DEVICE=$(get_build_var TARGET_DEVICE)')
        self.assertNotIn(' m ', rendered['lunch']['argv'][2] + ' ')
        self.assertEqual(rendered['compile'], {'cwd': str(self.src), 'env': {'OUT_DIR': 'out-blazer-build-2026103150', 'PATH': '/usr/bin:/bin',
            'GIT_CEILING_DIRECTORIES': str(self.src), 'BUILD_NUMBER': '2026103150', 'BUILD_DATETIME': '1793448000'}, 'argv': ['bash', '-c', 'source build/envsetup.sh && lunch blazer-cur-user'
            ' && m -j16 vendorbootimage vendorkernelbootimage target-files-package otatools-package'], 'timeoutSeconds': 28800})

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
            (lambda r: r['capacity'].update(compileAllowanceBytes=None), 'positive finite allowance'),
            (lambda r: r['gitMeta']['projects'][0].update(revision='0' * 40), 'gitMeta.projects must be exactly'),
            (lambda r: r['gitMeta']['projects'].pop(), 'gitMeta.projects must be exactly'),
            (lambda r: r['gitMeta']['projects'].append({'path': 'frameworks/av', 'name': 'x', 'revision': '0' * 40}),
             'gitMeta.projects must be exactly'),
            (lambda r: r['gitMeta'].update(fetchUrlPrefix='https://example.com/'), 'fetchUrlPrefix must be the pinned'),
            (lambda r: r['gitMeta'].update(objectsSource='production-pack'), 'gitMeta.objectsSource must be one of'),
            (lambda r: r['gitMeta'].update(timeoutSeconds=7201), 'gitMeta needs a positive boundBytes'),
            (lambda r: r['gitMeta'].update(projectTimeoutSeconds=9000), 'gitMeta needs a positive boundBytes'),
            (lambda r: r['gitMeta'].update(boundBytes=0), 'gitMeta needs a positive boundBytes'),
            (lambda r: r['gitMeta'].update(boundKind='measured'), 'gitMeta.boundKind must be proposal'),
            (lambda r: r['capacity'].update(compileAllowanceBytes=0), 'positive finite allowance'),
            (lambda r: r['capacity'].update(compileAllowanceKind='measured'), 'positive finite allowance'),
            (lambda r: r['capacity'].pop('compileAllowanceKind'), 'positive finite allowance'),
            (lambda r: r['compile'].update(timeoutSeconds=0), 'compile needs a positive timeoutSeconds'),
            (lambda r: r['compile'].update(timeoutSeconds=43201), 'compile needs a positive timeoutSeconds'),
            (lambda r: r['compile'].update(measure='out-dir-walk'), 'compile needs a positive timeoutSeconds'),
            (lambda r: r['preparation']['vendor'].update(boundBytes=0), 'preparation.vendor needs a positive boundBytes'),
            (lambda r: r['preparation']['download'].update(timeoutSeconds=None), 'preparation.download needs'),
            (lambda r: r['preparation']['download'].update(boundBytes=8 << 30), 'must equal factory.sizeBytes'),
            (lambda r: r['preparation']['download'].update(boundBytes=11362467848), 'must equal factory.sizeBytes'),
            (lambda r: r['factory'].pop('sizeBytes'), 'factory.sizeBytes must be'),
            (lambda r: r['factory'].pop('sizeBasis'), 'factory.sizeBytes must be'),
            (lambda r: r['preparation']['vendor'].update(collisionPaths=['vendor/google_devices/blazer']), 'collisionPaths'),
            (lambda r: r['preparation']['vendor']['node'].update(minMajor=0), 'node needs'),
            (lambda r: r['preparation']['vendor']['adevtoolGit'].update(skipDepBuildEnv={}), 'ADEVTOOL_SKIP_DEP_BUILD=1'),
            (lambda r: r['preparation']['vendor']['adevtoolGit'].update(skipDepBuildEnv={'ADEVTOOL_SKIP_DEP_BUILD': '0'}),
             'ADEVTOOL_SKIP_DEP_BUILD=1'),
            (lambda r: r['preparation']['vendor']['adevtoolGit'].update(argv=['git', 'rev-parse', 'HEAD']), 'rev-parse HEAD'),
            (lambda r: r['preparation']['vendor']['adevtoolGit'].update(metadataPath='.git'), 'vendor/adevtool/.git'),
            (lambda r: r['preparation']['vendor']['adevtoolGit'].update(timeoutSeconds=0), 'positive timeoutSeconds'),
            (lambda r: r['preparation']['gitInspection']['targets'].update(production='/home/pixelbuild/other/vendor/adevtool/.git'),
             'production target must be'),
            (lambda r: r['preparation']['gitInspection']['targets'].update(
                candidate='/home/pixelbuild/grapheneos-2026100600/nested/vendor/adevtool/.git'), 'candidate target must be'),
            (lambda r: r['preparation']['gitInspection']['targets'].update(candidate='/srv/x/source/vendor/adevtool/.git'),
             'candidate target must be'),
            (lambda r: r['preparation']['gitInspection']['targets'].update(
                candidate='/home/pixelbuild/rose-actions/x/../source/vendor/adevtool/.git'), 'candidate target must be'),
            (lambda r: r['preparation']['gitInspection']['targets'].update(candidate='/home/pixelbuild/rose-actions/x/source'),
             'candidate target must be'),
            (lambda r: r['preparation']['gitInspection']['targets'].update(other='/home/pixelbuild/x/vendor/adevtool/.git'),
             'exactly production and candidate'),
            (lambda r: r['preparation']['gitInspection'].update(maxSeconds=600), 'maxSeconds 60'),
            (lambda r: r['preparation']['gitInspection'].pop('targetSets'), 'targetSets must be exactly'),
            (lambda r: r['preparation'].pop('gitObjectsInspection'), 'gitObjectsInspection must be exactly'),
            (lambda r: r['preparation']['gitObjectsInspection'].pop('basis'), 'gitObjectsInspection must be exactly'),
            (lambda r: r['preparation']['gitObjectsInspection'].update(maxEntries=100001), 'gitObjectsInspection must be exactly'),
            (lambda r: r['preparation']['gitObjectsInspection'].update(maxDepth=4), 'gitObjectsInspection must be exactly'),
            (lambda r: r['preparation']['gitObjectsInspection'].update(maxSeconds=180), 'gitObjectsInspection must be exactly'),
            (lambda r: r['preparation']['gitObjectsInspection'].update(maxOutputBytes=1 << 20), 'gitObjectsInspection must be exactly'),
            (lambda r: r['preparation']['gitObjectsInspection'].update(path='.repo/project-objects/adevtool.git'),
             'gitObjectsInspection must be exactly'),
            (lambda r: r['preparation']['gitObjectsInspection'].update(root='/home/pixelbuild/rose-actions/x/source'),
             'gitObjectsInspection must be exactly'),
            (lambda r: r['preparation']['gitObjectsInspection'].update(host='other-builder'), 'gitObjectsInspection must be exactly'),
            (lambda r: r['preparation']['gitObjectsInspection'].update(excludeNames=['hooks']), 'gitObjectsInspection must be exactly'),
            (lambda r: r['preparation']['gitObjectsInspection'].update(existenceOnly=[]), 'gitObjectsInspection must be exactly'),
            (lambda r: r['preparation']['gitObjectsInspection'].update(followLinks=True), 'gitObjectsInspection must be exactly'),
            (lambda r: r['preparation']['gitInspection']['targetSets']['adevtool-projects-gitdir']['projectsGitdir'].update(
                root='/home/pixelbuild/rose-actions/x/source'), 'targetSets must be exactly'),
            (lambda r: r['preparation']['gitInspection']['targetSets']['adevtool-projects-gitdir']['projectsGitdir'].update(
                path='.repo/projects/vendor/adevtool.git/objects'), 'targetSets must be exactly'),
            (lambda r: r['preparation']['gitInspection']['targetSets']['adevtool-projects-gitdir']['projectsGitdir'].update(
                path='.repo/project-objects/adevtool.git'), 'targetSets must be exactly'),
            (lambda r: r['preparation']['gitInspection']['targetSets']['adevtool-projects-gitdir'].update(
                objects={'root': '/home/pixelbuild/grapheneos-2026100600', 'path': '.repo/project-objects/adevtool.git'}),
             'targetSets must be exactly'),
            (lambda r: r['preparation']['gitInspection']['targetSets'].update(
                other={'x': {'root': '/home/pixelbuild/grapheneos-2026100600', 'path': '.repo/projects/vendor/adevtool.git'}}),
             'targetSets must be exactly'),
            (lambda r: r['preparation']['lunch']['expect'].update(TARGET_BUILD_VARIANT='userdebug'), 'lunch.expect'),
            (lambda r: r['preparation']['lunch'].update(outDir='out-blazer-build'), 'its own top-level OUT_DIR'),
            (lambda r: r['preparation']['lunch'].update(outDir='out-blazer-build-2026103150'), 'its own top-level OUT_DIR'),
            (lambda r: r['compile'].update(outDirTemplate='out-blazer-build'), 'compile.outDirTemplate must be out-blazer-build-{build}'),
            (lambda r: r['compile'].update(outDirTemplate='out-{build}'), 'compile.outDirTemplate must be out-blazer-build-{build}'),
            (lambda r: r['compile'].update(outDir='out-blazer-build'), 'compile.outDirTemplate must be out-blazer-build-{build}'),
            (lambda r: r['preparation'].update(totalTimeoutSeconds=2700), 'sum of the stage timeouts'),
            (lambda r: r['preparation']['limits'].update(enforcement='quota'), 'sampled-stop enforcement'),
            (lambda r: r['preparation']['limits'].update(sampleSeconds=0), 'sampled-stop enforcement'),
            (lambda r: r['preparation'].update(productionSourceRootFrom='os/release-pipeline/frankel.json host.missing'), 'must resolve'),
            (lambda r: r['preparation']['candidateLayout']['children'].update(sourceRoot='src'), 'candidateLayout.children'),
            (lambda r: r['preparation']['candidateLayout'].update(ancestorBoundary='/'), 'ancestorBoundary must be the parent'),
            (lambda r: r['preparation'].update(stages=['download', 'vendor', 'lunch']), 'preparation.stages'),
            (lambda r: r['preparation']['source'].update(boundBytes=None), 'preparation.source needs a positive boundBytes'),
            (lambda r: r['preparation']['source'].update(includePaths=['out_adevtool_deps']), 'must include exactly'),
            (lambda r: r['preparation']['source'].update(includePaths=['out_adevtool_deps/adevtool_revision',
                                                                       'out_adevtool_deps/host']), 'must include exactly'),
            (lambda r: r['preparation']['source'].update(stagingPrefix='.tmp-'), 'stage under .source-staging-'),
            (lambda r: r['preparation']['source'].update(cleanupSeconds=300), 'cleanupSeconds must be positive and below'),
            (lambda r: r['preparation']['source'].update(cleanupSeconds=0), 'cleanupSeconds must be positive and below'),
            (lambda r: r['preparation']['source'].update(exclusionsFrom='os/release-pipeline/frankel.json host'),
             'must exclude repo metadata, keys'),
            (lambda r: r['preparation']['source'].update(exclusionsFrom='os/release-pipeline/missing.json x'), 'alpha exclusion policy'),
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
            'boundBytes': len(PAYLOAD), 'boundKind': 'proposal'})
        self.assertIn('below the reserve', self.problems('download', available=RESERVE + len(PAYLOAD) - 1))
        self.assertEqual(self.preflight('download', available=RESERVE + len(PAYLOAD))['problems'], [])
        real = bb.preflight(self.recipe, 'download', self.op(), statvfs=fs(RESERVE + 11362467847 - 1),
                            lock_probe=lambda path: True, root=self.root)
        self.assertIn('below the reserve', ' '.join(real['problems']), 'the checked-in exact size is the admission bound')
        self.archive()
        self.assertIn('never replaced', self.problems('download'))
        (self.download/FACTORY).unlink()
        (self.download/(FACTORY + '.part')).write_bytes(b'x')
        self.assertIn('partial download is never replaced', self.problems('download'))
        self.assertIn('held by another job', self.problems('download', download_dir=str(self.tmp/'dl2'), lock_free=False))
        self.assertIn('download dir parent', self.problems('download', download_dir=str(self.tmp/'no/such/dl')))

    def run_download(self, payload, recipe=None, clock=None, out='r', headers=None):
        recipe = recipe or self.synthetic
        fetched = []
        def fetcher(url, timeout):
            fetched.append((url, timeout))
            return Response(payload, headers)
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
        other = bytes(b ^ 1 for b in PAYLOAD)  # exactly the recorded size, other bytes: only the sha256 refuses it
        code, _, done = self.run_download(other, out='r2')
        self.assertEqual((code, done['ok'], done['bytes']), (1, False, len(PAYLOAD)))
        self.assertIn('is not the pinned', done['error'])
        self.assertEqual(list(self.download.iterdir()), [], 'a failed download leaves no partial or final file')

    def test_byte_and_time_bounds_stop_the_download(self):
        small = json.loads(json.dumps(self.synthetic))
        small['preparation']['download']['boundBytes'] = 4
        code, _, done = self.run_download(PAYLOAD, recipe=small, headers={})
        self.assertEqual(code, 1)
        self.assertIn('exceeded the 4-byte bound', done['error'])
        ticks = iter(range(0, 10 ** 6, 4000))
        code, _, done = self.run_download(PAYLOAD, clock=lambda: next(ticks), out='r2')
        self.assertIn('download exceeded 3600 s', done['error'])
        self.assertEqual(list(self.download.iterdir()), [])

    def test_only_exactly_the_recorded_size_is_accepted(self):
        # A server announcing another size is refused before any byte is written.
        written = []
        class Watched(Response):
            def read(self, n=-1):
                written.append(n)
                return super().read(n)
        def fetcher(url, timeout):
            return Watched(PAYLOAD, {'Content-Length': str(len(PAYLOAD) + 1)})
        code = bb.run(self.synthetic, 'download', self.op(), self.tmp/'r', fetcher=fetcher, clock=lambda: 0, now=lambda: 0,
                      statvfs=fs(10 ** 15), lock_probe=lambda path: True, root=self.root)
        done = json.loads((self.tmp/'r/completed.json').read_text())
        self.assertEqual((code, done['ok'], written), (1, False, []))
        self.assertIn(f'announces {len(PAYLOAD) + 1} bytes, not the recorded {len(PAYLOAD)}', done['error'])
        self.assertEqual(list(self.download.iterdir()), [])
        # A short body (no Content-Length announced) is refused on size, before the sha256 is even considered.
        code, _, done = self.run_download(PAYLOAD[:-1], out='r2', headers={})
        self.assertEqual((code, done['bytes']), (1, len(PAYLOAD) - 1))
        self.assertIn(f'{len(PAYLOAD) - 1} bytes arrived, not the recorded {len(PAYLOAD)}', done['error'])
        self.assertEqual(list(self.download.iterdir()), [], 'no partial or final file is kept')
        # Exactly the recorded size with the pinned sha256 is kept; the receipt records what was announced.
        code, _, done = self.run_download(PAYLOAD, out='r3')
        self.assertEqual((code, done['ok'], done['bytes'], done['announcedBytes'], done['expectedBytes']),
                         (0, True, len(PAYLOAD), len(PAYLOAD), len(PAYLOAD)))

    def test_the_real_pin_refuses_synthetic_bytes(self):
        code, _, done = self.run_download(PAYLOAD, recipe=self.recipe)
        self.assertEqual(code, 1)
        self.assertIn('not the recorded 11362467847', done['error'])
        self.assertEqual(done['expectedSha256'], PIN)

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
        self.assertIn('not exactly the pinned hash and one newline', self.problems('vendor'))
        (self.src/'out_adevtool_deps/adevtool_revision').write_text(ADEVTOOL)  # no newline: adevtool compares exactly
        self.assertIn('not exactly the pinned hash and one newline', self.problems('vendor'))
        (self.src/'out_adevtool_deps/adevtool_revision').write_text(ADEVTOOL + '\n')
        os.rmdir(self.src/'vendor/adevtool/.git')
        self.assertIn('vendor/adevtool/.git is missing: no git version data', self.problems('vendor'))
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
    def git(self, where, *args):
        env = {'PATH': '/usr/bin:/bin', 'HOME': str(self.tmp), 'GIT_CONFIG_NOSYSTEM': '1'}
        return subprocess.run(['git', '-C', str(where), *args], env=env, check=True, capture_output=True, text=True).stdout

    def repository(self, where):
        """A real git repository with one commit (synthetic content); returns its HEAD."""
        self.git(where, 'init', '-q')
        self.git(where, '-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-q', '--allow-empty', '-m', 'synthetic')
        return self.git(where, 'rev-parse', 'HEAD').strip()

    def setUp(self):
        super().setUp()
        self.archive()
        os.rmdir(self.src/'vendor/adevtool/.git')
        self.head = self.repository(self.src/'vendor/adevtool')
        # A synthetic pin: this repository's HEAD (the real pin 17df3b79 cannot be committed here).
        self.synthetic['pins']['vendor/adevtool'] = self.head
        (self.src/'out_adevtool_deps/adevtool_revision').write_text(self.head + '\n')
        self.seen = self.tmp/'node-seen'
        self.env_seen = self.tmp/'env-seen.json'
        self.script(self.src/'vendor/adevtool/bin/run', f"""mode = os.environ.get('FAKE_MODE', 'ok')
open({str(self.seen)!r}, 'w').write(shutil.which('node') or '')
open({str(self.seen) + '.neighbour'!r}, 'w').write(shutil.which('neighbour') or '')
import json
json.dump({{k: v for k, v in os.environ.items() if k.startswith(('GIT_', 'ADEVTOOL_'))}}, open({str(self.env_seen)!r}, 'w'))
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
        # Like /usr/local/bin/node: another tool beside the bound node must not become visible to adevtool.
        self.script(self.node.parent/'neighbour', 'print("shadow")')
        code, done = self.run_vendor()
        started = json.loads((self.tmp/'r/started.json').read_text())
        self.assertEqual(started['command']['env']['PATH'], f'{self.tmp}/r/node-path:/usr/bin:/bin')
        self.assertEqual(os.listdir(self.tmp/'r/node-path'), ['node'])
        self.assertEqual(Path(str(self.seen) + '.neighbour').read_text(), '', 'nothing beside node is on PATH')
        self.assertEqual((code, done['ok'], done['enforcement']), (0, True, 'sampled-stop'))
        private = self.tmp/'r/node-path/node'
        self.assertEqual(self.seen.read_text(), str(private), 'adevtool resolves the private entry')
        self.assertEqual(os.path.realpath(private), str(self.node), 'which is exactly the verified node')
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

    def git_environment(self, **values):
        for key, value in values.items():
            os.environ[key] = value
            self.addCleanup(os.environ.pop, key, None)

    def test_generation_never_rebuilds_deps_and_never_sees_an_inherited_git(self):
        outer = self.tmp/'elsewhere'
        outer.mkdir()
        self.git_environment(GIT_DIR=str(outer/'.git'), GIT_WORK_TREE=str(outer))
        code, done = self.run_vendor()
        self.assertEqual((code, done['ok']), (0, True), done)
        seen = json.loads(self.env_seen.read_text())
        self.assertEqual(seen, {'ADEVTOOL_SKIP_DEP_BUILD': '1', 'GIT_CEILING_DIRECTORIES': str(self.src),
                                'ADEVTOOL_IMG_DOWNLOAD_DIR': str(self.download)},
                         'the pinned skip switch is set and no inherited GIT_* reaches generation')
        self.assertEqual((done['adevtoolHead'], done['skipDepBuild']), (self.head, {'ADEVTOOL_SKIP_DEP_BUILD': '1'}))

    def test_missing_or_ungenuine_adevtool_version_data_refuses_before_generation(self):
        other = json.loads(json.dumps(self.synthetic))
        other['pins']['vendor/adevtool'] = 'f' * 40
        (self.src/'out_adevtool_deps/adevtool_revision').write_text('f' * 40 + '\n')
        code, done = self.run_vendor(recipe=other, out='wrong-head')
        self.assertEqual(code, 1)
        self.assertIn('did not print exactly the pinned revision', done['error'])
        self.assertEqual(done['adevtoolHead']['stdout'], self.head + '\n')
        self.assertFalse(self.seen.exists(), 'nothing is generated')
        # An invalid .git with a genuine repository above the tree, or a GIT_DIR pointing at it: git never answers from it.
        outer = self.repository(self.tmp)
        (self.src/'out_adevtool_deps/adevtool_revision').write_text(outer + '\n')
        above = json.loads(json.dumps(self.synthetic))
        above['pins']['vendor/adevtool'] = outer
        shutil.rmtree(self.src/'vendor/adevtool/.git')
        (self.src/'vendor/adevtool/.git').mkdir()
        code, done = self.run_vendor(recipe=above, out='ceiling')
        self.assertEqual(code, 1, done)
        self.assertNotEqual(done['adevtoolHead']['returncode'], 0)
        self.git_environment(GIT_DIR=str(self.tmp/'.git'))
        code, done = self.run_vendor(recipe=above, out='redirect')
        self.assertEqual(code, 1, done)
        self.assertFalse(self.seen.exists(), 'nothing is generated')
        # No metadata at all: refused in preflight, before the lock, any process or a started receipt.
        shutil.rmtree(self.src/'vendor/adevtool/.git')
        code = bb.run(above, 'vendor', self.op(), self.tmp/'absent', now=lambda: 0, statvfs=fs(10 ** 15),
                      lock_probe=lambda path: True, root=self.root)
        self.assertEqual(code, 1)
        self.assertEqual(sorted(p.name for p in (self.tmp/'absent').iterdir()), ['preflight.json'])
        self.assertIn('vendor/adevtool/.git is missing', (self.tmp/'absent/preflight.json').read_text())

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
        (self.src/'build/envsetup.sh').write_text(f"""lunch() {{ export TARGET_PRODUCT=blazer TARGET_RELEASE=cur TARGET_BUILD_VARIANT={variant}; mkdir -p "$OUT_DIR"; env | grep '^GIT_' | sort > {str(self.tmp/'lunch-git-env')!r}; }}
get_build_var() {{ echo blazer; }}
""")

    def run_lunch(self, out='r'):
        code = bb.run(self.synthetic, 'lunch', self.op(), self.tmp/out, now=lambda: 0,
            statvfs=fs(10 ** 15), lock_probe=lambda path: True, root=self.root)
        return code, json.loads((self.tmp/out/'completed.json').read_text())

    def test_lunch_sees_no_inherited_git_and_is_bounded_at_the_source_root(self):
        """The generated version check runs git at lunch; it must answer from the tree only."""
        self.generated()
        self.envsetup()
        os.environ['GIT_DIR'] = str(self.tmp/'elsewhere/.git')
        self.addCleanup(os.environ.pop, 'GIT_DIR', None)
        code, done = self.run_lunch()
        self.assertEqual((code, done['ok']), (0, True), done)
        self.assertEqual((self.tmp/'lunch-git-env').read_text(), f'GIT_CEILING_DIRECTORIES={self.src}\n')

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

class SourceRunTest(Fixture):
    """The source stage: an ordinary, bounded byte copy of the production root (only read) into a new isolated tree.
    The production root here is synthetic; the exclusion policy is the real checked-in alpha policy."""
    def setUp(self):
        super().setUp()
        self.build_production()
        (self.tmp/'rose-actions').mkdir(mode=0o700)
        self.target = self.tmp/'rose-actions/blazer-prep-1-1/source'

    def build_production(self):
        p = self.production
        if p.exists():
            bb.alpha_source.make_removable(p)
            shutil.rmtree(p)
        shutil.copytree(self.src, p, symlinks=True)
        (p/'build/make').mkdir()
        (p/'build/make/envsetup.sh').write_text('# envsetup\n')
        (p/'build/envsetup.sh').unlink()
        os.symlink('make/envsetup.sh', p/'build/envsetup.sh')  # a repo linkfile: relative, kept as is
        os.symlink(str(p/'build/make/envsetup.sh'), p/'build/absolute')  # absolute into the tree: rebased
        host = p/'out_adevtool_deps/host/linux-x86'
        (host/'lib64').mkdir()
        (host/'lib64/libc++.so').write_bytes(b'\x7fELF synthetic host library')
        (host/'framework').mkdir()
        (host/'framework/apksigner.jar').write_bytes(b'PK synthetic jar')
        os.symlink(str(host/'lib64/libc++.so'), host/'bin/library-link')  # absolute inside the include: rebased
        os.symlink('../lib64/libc++.so', host/'bin/relative-link')  # relative inside the include: kept
        (p/'vendor/adevtool/dl/unpacked/frankel-CP3A/system').mkdir(parents=True)
        (p/'vendor/adevtool/dl/.find-ignore').write_text('')
        os.symlink('/system/bin', p/'vendor/adevtool/dl/unpacked/frankel-CP3A/system/bin')  # the excluded cache boundary
        self.left = ('.repo/manifest.xml', 'keys/blazer/releasekey.pk8', 'releases/2026110201/ota.zip', 'out/soong/huge.bin',
                     'out-blazer-lunch/x', 'out_other/y', 'device/google/blazer/.git/config', 'out_adevtool_deps/host/linux-x86/.git/HEAD',
                     'out_adevtool_deps/soong/build.ninja',
                     'vendor/adevtool/dl/unpacked')
        for rel in self.left[:-1]:
            (p/rel).parent.mkdir(parents=True, exist_ok=True)
            (p/rel).write_bytes(b'never copied ' + rel.encode())
        (p/'device/google/blazer/device.mk').write_text('PRODUCT_NAME := blazer\n')
        os.utime(p/'device/google/blazer/device.mk', ns=(1_600_000_000_000_000_000,) * 2)
        readonly = p/'prebuilts/readonly'
        readonly.mkdir()
        (readonly/'tool').write_bytes(b'\x7fELF')
        os.chmod(readonly/'tool', 0o555)
        os.chmod(readonly, 0o555)
        self.addCleanup(bb.alpha_source.make_removable, self.tmp)

    def run_source(self, recipe=None, out='r', available=10 ** 15, target=None, **op):
        op = dict({'download_dir': None, 'node': None}, **op)  # the source stage depends on sourceRoot only
        code = bb.run(recipe or self.synthetic, 'source', self.op(source_root=str(target or self.target), **op), self.tmp/out,
            now=lambda: 0, statvfs=available if callable(available) else fs(available), lock_probe=lambda path: True,
            root=self.root)
        done = self.tmp/out/'completed.json'
        return code, json.loads(done.read_text()) if done.exists() else None

    def construct(self, recipe=None, available=10 ** 15, **inject):
        op = self.op(source_root=str(self.target))
        return bb.construct(recipe or self.synthetic, op, self.production, self.root,
            statvfs=available if callable(available) else fs(available), **inject)

    def unverified(self, done):
        """The copy is never presented as verified: exact provenance, and no truthy *verified* or resolved manifest."""
        self.assertEqual(done['provenance'], bb.SOURCE_PROVENANCE)
        self.assertEqual((done['provenance']['kind'], done['provenance']['projectRevisionsVerified'],
                          done['provenance']['pinnedManifestVerified'], done['provenance']['resolvedManifest']),
                         ('production-working-tree-byte-snapshot', False, False, None))
        self.assertNotIn('usable', done)
        def walk(value):
            if isinstance(value, dict):
                for key, item in value.items():
                    if 'verified' in key.lower() or 'manifest' in key.lower():
                        self.assertIn(item, (False, None), key)
                    walk(item)
            elif isinstance(value, list):
                for item in value:
                    walk(item)
        walk(done)

    def test_copy_is_ordinary_bounded_and_leaves_production_untouched(self):
        before = self.snapshot(self.production)
        self.guard()
        code, done = self.run_source()
        self.assertEqual((code, done['ok'], done['complete'], done['processesStarted']), (0, True, True, 0), done)
        self.unverified(done)
        self.assertEqual(self.snapshot(self.production), before, 'the production root is only read')
        t = self.target
        for rel in self.left:
            self.assertFalse(os.path.lexists(t/rel), rel)
        self.assertEqual((t/'out_adevtool_deps/adevtool_revision').read_text(), ADEVTOOL + '\n', 'adevtool deps are included')
        self.assertTrue(os.access(t/'out_adevtool_deps/host/linux-x86/bin/aapt2', os.X_OK))
        self.assertEqual(os.readlink(t/'build/envsetup.sh'), 'make/envsetup.sh')
        self.assertEqual(os.readlink(t/'build/absolute'), str(t/'build/make/envsetup.sh'), 'absolute links are rebased')
        deps = t/'out_adevtool_deps/host/linux-x86'
        self.assertEqual(os.readlink(deps/'bin/library-link'), str(deps/'lib64/libc++.so'), 'rebased inside the include')
        self.assertEqual(os.readlink(deps/'bin/relative-link'), '../lib64/libc++.so')
        self.assertEqual(sorted(p.name for p in (t/'out_adevtool_deps').iterdir()), ['adevtool_revision', 'host'],
                         'only the revision marker and the host distribution are copied from the deps')
        self.assertEqual(oct(os.lstat(t/'prebuilts/readonly').st_mode & 0o777), '0o555')
        self.assertEqual(os.lstat(t/'device/google/blazer/device.mk').st_mtime_ns, 1_600_000_000_000_000_000)
        copied, original = os.lstat(t/'device/google/blazer/device.mk'), os.lstat(self.production/'device/google/blazer/device.mk')
        self.assertNotEqual((copied.st_dev, copied.st_ino), (original.st_dev, original.st_ino), 'new inodes, never hardlinks')
        self.assertEqual((oct(t.parent.stat().st_mode & 0o777), done['createdParent']), ('0o700', True))
        self.assertEqual(sorted(n.name for n in t.parent.iterdir()), ['source', 'source.selection.tsv'],
                         'no staging is left; the selection record sits beside the tree, never inside it')
        self.assertEqual(done['partial'] if 'partial' in done else None, None)
        self.assertEqual(done['copied']['entries'], done['selection']['entries'])
        self.assertRegex(done['selectionSha256'], '^[0-9a-f]{64}$')
        self.assertLessEqual(done['selection']['projectedBytes'], done['boundBytes'])
        self.assertTrue(done['availableBytesBefore'] - done['selection']['projectedBytes'] >= RESERVE)
        # The snapshot has no .git anywhere, so the vendor stage refuses it up front (fail-closed), naming the gap;
        # every other vendor tree check passes on it.
        self.archive()
        report = self.preflight('vendor', source_root=str(t))
        self.assertFalse(report['ready'])
        self.assertEqual(len(report['problems']), 1, report['problems'])
        self.assertIn('vendor/adevtool/.git is missing: no git version data for adevtool', report['problems'][0])
        # It is never replaced: a second run refuses before writing.
        self.assertIn('already exists', self.problems('source', source_root=str(t)))

    def test_cleanup_is_bounded_and_a_remainder_is_named_and_blocks(self):
        tree = self.tmp/'own'
        (tree/'a/b').mkdir(parents=True)
        for i in range(5):
            (tree/'a/b'/f'f{i}').write_text('x')
        outside = self.tmp/'outside'
        outside.write_text('kept')
        os.symlink(outside, tree/'a/link')
        os.symlink(self.tmp, tree/'a/dirlink')
        os.chmod(tree/'a/b', 0o555)
        os.chmod(tree/'a', 0o555)
        ticks = iter(range(10 ** 6))
        modes, chmod = [], os.chmod
        def spy(path, mode, *a, **k):  # root (this container) ignores directory modes; the builder's uid does not
            modes.append((Path(path), mode))
            return chmod(path, mode, *a, **k)
        os.chmod = spy
        try:
            self.assertEqual(bb.remove_own(tree, 3, clock=lambda: next(ticks)), (False, 2))
        finally:
            os.chmod = chmod
        self.assertEqual(modes, [(tree/'a/b', 0o555 | 0o700)], 'owner write is added before entries are removed')
        self.assertTrue(tree.exists(), 'the remainder is left in place')
        self.assertEqual(bb.remove_own(tree, 60), (True, 9 - 2 + 1), "the rest plus the root")
        self.assertFalse(os.path.lexists(tree))
        self.assertEqual((outside.read_text(), self.tmp.is_dir()), ('kept', True), 'links are never followed')
        # A stop whose cleanup budget runs out writes its receipt, names the remainder, and the next run refuses.
        stuck = json.loads(json.dumps(self.synthetic))
        stuck['preparation']['source']['cleanupSeconds'] = 0
        def full(source, destination, entry):
            raise OSError(28, 'No space left on device')
        ok, done = self.construct(stuck, copier=full)
        self.assertEqual((ok, done['stagingRemoved'], done['removedEntries'], done['cleanupSeconds']), (False, False, 0, 0))
        self.assertTrue(Path(done['stagingLeft']).is_dir())
        self.assertEqual(Path(done['stagingLeft']).parent, self.target.parent)
        self.assertIn(f'interrupted construction left {Path(done["stagingLeft"]).name}',
                      self.problems('source', source_root=str(self.target)))
        self.unverified(done)

    def records(self, path):
        """Selection-record lines as (path, type, rest...) with the escapes undone; raw bytes for paths."""
        unescape = lambda field: field.replace(b'\\t', b'\t').replace(b'\\n', b'\n').replace(b'\\r', b'\r') \
            .replace(b'\\\\', b'\\')
        lines = Path(path).read_bytes().split(b'\n')
        self.assertEqual(lines[-1], b'', 'every line ends with a newline')
        return [[unescape(f) for f in line.split(b'\t')] for line in lines[:-1]]

    def test_source_stage_needs_only_the_source_root(self):
        bare = self.op(source_root=str(self.target), download_dir=None, node=None, jobs=None)
        report = bb.preflight(self.synthetic, 'source', bare, statvfs=fs(10 ** 15), lock_probe=lambda path: True, root=self.root)
        self.assertEqual((report['ready'], report['problems']), (True, []))
        self.assertIn('source', bb.commands(self.synthetic, bare, self.root))
        code, done = self.run_source()
        self.assertEqual((code, done['complete']), (0, True), done)

    def test_selection_record_keeps_the_copied_bytes_beside_the_tree(self):
        code, done = self.run_source()
        self.assertEqual(code, 0, done)
        self.unverified(done)  # the PR395 guard still holds: no truthy verified or manifest field
        record = done['selectionRecord']
        path = Path(record['path'])
        self.assertEqual(path, self.target.parent/'source.selection.tsv')
        self.assertNotIn(self.target, path.parents, 'never inside the tree it describes')
        self.assertEqual((bb.sha256_file(path), record['sha256'], record['bytes'], record['lines']),
                         (done['selectionSha256'], done['selectionSha256'], path.stat().st_size, done['copied']['entries']))
        self.assertLessEqual(record['bytes'], done['selection']['selectionRecordBoundBytes'])
        self.assertEqual(oct(path.stat().st_mode & 0o777), '0o400', 'write-once: read-only once complete')
        rows = self.records(path)
        self.assertEqual(len(rows), done['selection']['entries'])
        for row in rows:
            rel = Path(os.fsdecode(row[0]))
            copied = self.target/rel
            kind = row[1].decode()
            if kind == 'file':
                self.assertEqual((int(row[3]), row[4].decode()), (copied.stat().st_size, bb.sha256_file(copied)), rel)
                self.assertEqual(int(row[2], 8), os.lstat(copied).st_mode & 0o7777)
            elif kind == 'link':
                self.assertEqual(row[2], os.fsencode(os.readlink(copied)), rel)
            else:
                self.assertEqual((kind, int(row[2], 8)), ('dir', os.lstat(copied).st_mode & 0o7777), rel)
        # Later stages change the tree by design; the record still names the original bytes.
        original = self.records(path)
        (self.target/'device/google/blazer/device.mk').chmod(0o644)
        (self.target/'device/google/blazer/device.mk').write_text('changed by a later stage\n')
        (self.target/'out-blazer-lunch').mkdir()
        self.assertEqual((self.records(path), bb.sha256_file(path)), (original, done['selectionSha256']))
        line = next(r for r in original if r[0] == b'device/google/blazer/device.mk')
        self.assertEqual(line[4].decode(), bb.sha256_file(self.production/'device/google/blazer/device.mk'))
        # Write-once: a second source run refuses on the record even after the tree is moved away.
        self.target.rename(self.target.with_name('moved'))
        self.assertIn('selection record is never replaced', self.problems('source', source_root=str(self.target)))

    def test_selection_record_encodes_awkward_names_without_refusing_them(self):
        names = ['tab\there', 'new\nline', 'back\\slash', 'carriage\rreturn']
        odd = self.production/'odd'
        odd.mkdir()
        for name in names:
            (odd/name).write_text(name)
        raw = os.path.join(os.fsencode(odd), b'latin1-\xff')
        with open(raw, 'wb') as handle:
            handle.write(b'not utf-8 name')
        os.symlink('tab\there', odd/'link\nname')
        code, done = self.run_source()
        self.assertEqual(code, 0, done)
        rows = self.records(done['selectionRecord']['path'])
        self.assertEqual(len(rows), done['selection']['entries'], 'one unambiguous line per entry')
        paths = {row[0] for row in rows}
        for name in names:
            self.assertIn(b'odd/' + name.encode(), paths)
        self.assertIn(b'odd/latin1-\xff', paths)
        link = next(r for r in rows if r[0] == b'odd/link\nname')
        self.assertEqual((link[1], link[2]), (b'link', b'tab\there'))
        self.assertTrue(os.path.exists(os.path.join(os.fsencode(self.target), b'odd/latin1-\xff')))
        self.assertEqual(bb.record_field('a\\b\tc\nd\re'), b'a\\\\b\\tc\\nd\\re')

    def test_record_bound_covers_the_worst_case_line(self):
        entries = [{'type': 'link', 'path': '\t' * 1000, 'text': '\n' * 1000, 'kind': 'relative'},
                   {'type': 'file', 'path': '\\' * 1000, 'mode': 0o644, 'size': 10 ** 12},
                   {'type': 'dir', 'path': 'd', 'mode': 0o755}]
        written = sum(len(bb.record_line(e, e['text'] if e['type'] == 'link' else ('0' * 64 if e['type'] == 'file' else None)))
                      for e in entries)
        self.assertLessEqual(written, bb.record_bound(entries, self.target, 1))
        self.assertEqual(bb.record_bound(entries, self.target, 4096) % 4096, 0)
        code, done = self.run_source(out='p')
        sel = done['selection']
        self.assertEqual(sel['projectedBytes'], sel['selectionRecordBoundBytes']
                         + bb.projected_bytes(bb.source_selection(self.synthetic, self.production, self.root)['entries'], 1))

    def test_the_staged_record_is_owner_only_while_copying(self):
        seen = []
        def look(source, destination, entry):
            staged = list(self.target.parent.glob('.source-staging-*.selection.tsv'))
            seen.extend(oct(p.stat().st_mode & 0o777) for p in staged)
            return bb.alpha_source.copy_file(source, destination, entry)
        ok, done = self.construct(copier=look)
        self.assertTrue(ok, done)
        self.assertTrue(seen)
        self.assertEqual(set(seen), {'0o600'})

    def test_a_stop_after_the_tree_is_placed_keeps_tree_and_record_together(self):
        rename = os.rename
        def then_term(src, dst, *a, **k):
            result = rename(src, dst, *a, **k)
            if Path(dst) == self.target:
                os.kill(os.getpid(), signal.SIGTERM)
            return result
        os.rename = then_term
        try:
            ok, done = self.construct()
        finally:
            os.rename = rename
        self.assertFalse(ok)
        self.assertEqual((done['stopped'], done['targetExists'], done['selectionRecordExists']), ('SIGTERM', True, True), done)
        self.assertTrue((self.target.parent/'source.selection.tsv').is_file())
        self.unverified(done)

    def test_a_failed_tree_rename_leaves_no_record_without_its_tree(self):
        rename = os.rename
        def fail_tree(src, dst, *a, **k):
            if Path(dst) == self.target:
                raise OSError(5, 'synthetic rename failure')
            return rename(src, dst, *a, **k)
        os.rename = fail_tree
        try:
            ok, done = self.construct()
        finally:
            os.rename = rename
        self.assertEqual((ok, done['stagingRemoved'], done['selectionRecordExists'], done['targetExists']), (False, True, False, False))
        self.assertIn('synthetic rename failure', done['stopped'])
        self.assertEqual(sorted(p.name for p in self.target.parent.iterdir()) if self.target.parent.exists() else [], [])
        self.unverified(done)

    SIX = ('.ninja_fifo', *(f'target/product/emu64x/system/lib64/{n}' for n in ('libc.so', 'libdl.so', 'libdl_android.so', 'libm.so')),
           'siso_config/main')

    def attempt_rejects(self):
        """The six entries attempt 41547295738 refused, relative to out_adevtool_deps (a whole build OUT_DIR)."""
        deps = self.production/'out_adevtool_deps'
        os.mkfifo(deps/'.ninja_fifo')
        lib = deps/'target/product/emu64x/system/lib64'
        lib.mkdir(parents=True)
        for name in ('libc.so', 'libdl.so', 'libdl_android.so', 'libm.so'):
            os.symlink(f'/apex/com.android.runtime/lib64/bionic/{name}', lib/name)
        (deps/'siso_config').mkdir()
        os.symlink(str(self.production/'build/soong/siso_config'), deps/'siso_config/main')
        return deps

    def test_attempt_rejects_are_reproduced_then_never_selected_or_touched(self):
        deps = self.attempt_rejects()
        policy = bb.pointer(self.root, self.synthetic['preparation']['source']['exclusionsFrom'])
        inner = {'rootNames': [], 'rootPrefixes': [], 'anywhereNames': list(policy['anywhereNames']), 'externalLinks': [],
                 'cacheBoundaries': []}
        old = bb.alpha_source.plan_copy(deps, inner)  # the former whole-deps selection
        self.assertEqual(len(old['problems']), 6, old['problems'])
        for rel in self.SIX:
            self.assertTrue(any(problem.startswith(rel) for problem in old['problems']), rel)
        self.assertIn('.ninja_fifo: unsupported file type', old['problems'])
        touched, originals = [], {name: getattr(os, name) for name in ('lstat', 'readlink', 'open', 'stat')}
        rejects = {os.fsencode(str(deps/rel)) for rel in self.SIX}
        def spy(name):
            def call(path, *a, **k):
                if os.fsencode(os.fspath(path)) in rejects:
                    touched.append((name, os.fspath(path)))
                return originals[name](path, *a, **k)
            return call
        for name in originals:
            setattr(os, name, spy(name))
        try:
            selection = bb.source_selection(self.synthetic, self.production, self.root)
        finally:
            for name, original in originals.items():
                setattr(os, name, original)
        self.assertEqual((selection['problems'], touched), ([], []), 'none of the six is walked, read or dereferenced')
        paths = {e['path'] for e in selection['entries']}
        self.assertFalse(any(p.startswith(('out_adevtool_deps/target', 'out_adevtool_deps/siso_config', 'out_adevtool_deps/.ninja'))
                             for p in paths))
        code, done = self.run_source()
        self.assertEqual((code, done['complete']), (0, True), done)
        for rel in self.SIX:
            self.assertFalse(os.path.lexists(self.target/'out_adevtool_deps'/rel), rel)
        self.unverified(done)

    def test_the_complete_host_distribution_is_copied_and_recorded(self):
        code, done = self.run_source()
        self.assertEqual(code, 0, done)
        host = 'out_adevtool_deps/host/linux-x86'
        rows = {os.fsdecode(r[0]): r for r in self.records(done['selectionRecord']['path'])}
        for rel in ('out_adevtool_deps/adevtool_revision', f'{host}/lib64/libc++.so', f'{host}/framework/apksigner.jar',
                    *(f'{host}/bin/{tool}' for tool in self.recipe['adevtool']['depsTools'])):
            copied = self.target/rel
            self.assertEqual(copied.read_bytes(), (self.production/rel).read_bytes(), rel)
            self.assertEqual((rows[rel][1], rows[rel][4].decode()), (b'file', bb.sha256_file(copied)), rel)
        for parent in ('out_adevtool_deps', 'out_adevtool_deps/host'):
            self.assertEqual(rows[parent][1], b'dir')
            self.assertEqual(int(rows[parent][2], 8), os.lstat(self.production/parent).st_mode & 0o7777)
        self.assertEqual(rows[f'{host}/bin/library-link'][1], b'link')
        self.assertEqual(bb.tree_problems(self.recipe, self.target), [], 'the vendor stage accepts the copied deps')

    def test_missing_unusable_or_unpinned_deps_refuse_before_writing(self):
        bin_dir = self.production/'out_adevtool_deps/host/linux-x86/bin'
        cases = (
            (lambda: (bin_dir/'aapt2').unlink(), "deps: adevtool host tools missing, linked or not executable: ['aapt2']"),
            (lambda: os.chmod(bin_dir/'lz4', 0o644), "deps: adevtool host tools missing, linked or not executable: ['lz4']"),
            (lambda: ((bin_dir/'toybox').unlink(), os.symlink('aapt2', bin_dir/'toybox')),
             "deps: adevtool host tools missing, linked or not executable: ['toybox']"),
            (lambda: (self.production/'out_adevtool_deps/adevtool_revision').write_text('0' * 40 + '\n'),
             'deps: out_adevtool_deps/adevtool_revision is'),
            (lambda: os.symlink('../../../../build/make/envsetup.sh', bin_dir/'outside'),
             'out_adevtool_deps/host/linux-x86: bin/outside -> ../../../../build/make/envsetup.sh leaves the source tree'),
            (lambda: os.symlink('../../../soong/build.ninja', bin_dir/'deps-only'),
             'out_adevtool_deps/host/linux-x86: bin/deps-only -> ../../../soong/build.ninja leaves the source tree'),
            (lambda: os.symlink(str(self.production/'build/make/envsetup.sh'), bin_dir/'absolute-out'), 'leaves the source tree'),
            (lambda: os.mkfifo(bin_dir/'pipe'), 'out_adevtool_deps/host/linux-x86: bin/pipe: unsupported file type'))
        for index, (change, pattern) in enumerate(cases):
            with self.subTest(pattern):
                change()
                code, done = self.run_source(out=f'deps{index}')
                self.assertEqual((code, done['written'], done['complete']), (1, False, False), done)
                self.assertIn(pattern, ' '.join(done['selection']['problems']))
                self.assertFalse(os.path.lexists(self.target.parent))
                self.build_production()

    def test_unrelated_vendor_lunch_and_kernel_needs_never_gate_the_copy(self):
        """The copy is a preparatory snapshot: only the selected deps are checked; vendor keeps its own admission."""
        for rel in ('vendor/adevtool/bin/run', 'build/envsetup.sh', 'prebuilts/build-tools/path/linux-x86'):
            path = self.production/rel
            shutil.rmtree(path) if path.is_dir() and not path.is_symlink() else path.unlink()
        shutil.rmtree(self.production/self.recipe['kernelDir'])
        self.assertEqual([p for p in bb.source_selection(self.synthetic, self.production, self.root)['problems']], [])
        code, done = self.run_source()
        self.assertEqual((code, done['complete']), (0, True), done)
        vendor = ' '.join(bb.tree_problems(self.recipe, self.target))
        for rel in ('vendor/adevtool/bin/run', 'build/envsetup.sh', self.recipe['kernelDir']):
            self.assertIn(rel, vendor, 'the vendor stage still refuses on its own needs')

    def test_the_revision_marker_is_exactly_the_pinned_hash_and_one_newline(self):
        marker = self.production/'out_adevtool_deps/adevtool_revision'
        pin = self.recipe['pins']['vendor/adevtool']
        self.assertEqual(bb.deps_problems(self.recipe, self.production), [])
        for content in (pin, pin + '\n\n', ' ' + pin + '\n', pin + '\r\n', pin.upper() + '\n', ''):
            with self.subTest(repr(content)):
                marker.write_text(content)
                self.assertIn('not exactly the pinned hash and one newline', ' '.join(bb.deps_problems(self.recipe, self.production)))
        marker.write_text(pin + '\n')
        self.assertEqual(bb.deps_problems(self.recipe, self.production), [])
        real = self.tmp/'marker-target'
        real.write_text(pin + '\n')
        marker.unlink()
        os.symlink(real, marker)
        self.assertIn('is None, not exactly', ' '.join(bb.deps_problems(self.recipe, self.production)), 'never read through a link')

    def test_include_paths_must_be_plain_and_are_never_read_through_links(self):
        deps = self.production/'out_adevtool_deps'
        real = self.tmp/'real-revision'
        real.write_text(self.recipe['adevtool']['revision'] if 'revision' in self.recipe['adevtool'] else 'x')
        original = bb.deps_problems
        calls = []
        bb.deps_problems = lambda *a: calls.append(a) or original(*a)
        self.addCleanup(setattr, bb, 'deps_problems', original)
        cases = (
            (lambda: ((deps/'adevtool_revision').unlink(), os.symlink(real, deps/'adevtool_revision')),
             'out_adevtool_deps/adevtool_revision is not a regular file or real directory'),
            (lambda: (deps/'adevtool_revision').unlink(), 'out_adevtool_deps/adevtool_revision is not a regular file'),
            (lambda: ((deps/'host').rename(self.tmp/'host-real'), os.symlink(self.tmp/'host-real', deps/'host')),
             'out_adevtool_deps/host is not a real directory'),
            (lambda: ((deps/'host/linux-x86').rename(self.tmp/'linux-real'), os.symlink(self.tmp/'linux-real', deps/'host/linux-x86')),
             'out_adevtool_deps/host/linux-x86 is not a regular file or real directory'))
        for index, (change, pattern) in enumerate(cases):
            with self.subTest(pattern):
                calls.clear()
                change()
                selection = bb.source_selection(self.synthetic, self.production, self.root)
                self.assertIn(pattern, ' '.join(selection['problems']))
                self.assertEqual(calls, [], 'deps_problems (which reads adevtool_revision) never runs on an unsafe include')
                shutil.rmtree(self.tmp/'host-real', True)
                shutil.rmtree(self.tmp/'linux-real', True)
                self.build_production()

    def test_projection_is_dense_and_block_rounded(self):
        entries = [{'type': 'file', 'size': 1}, {'type': 'file', 'size': 4096}, {'type': 'file', 'size': 0},
                   {'type': 'dir'}, {'type': 'link'}]
        self.assertEqual(bb.projected_bytes(entries, 4096), 4096 * (1 + 1 + 1 + 0 + 1 + 1))
        self.assertEqual(bb.projected_bytes(entries, 1), 1 + 1 + 4096 + 0 + 1 + 1)

    def test_refusals_before_anything_is_written(self):
        self.assertIn('production source root', self.problems('source', source_root=str(self.production/'nested')))
        self.assertIn('ancestor boundary', self.problems('source', source_root='/var/tmp/blazer/source'))
        self.assertIn('is not the one level', self.problems('source', source_root=str(self.tmp/'a/b/source')))
        (self.tmp/'rose-actions/blazer-prep-1-1').mkdir()
        (self.tmp/'rose-actions/blazer-prep-1-1/.source-staging-0123456789ab').mkdir()
        self.assertIn('interrupted construction left .source-staging-0123456789ab', self.problems('source', source_root=str(self.target)))
        shutil.rmtree(self.tmp/'rose-actions/blazer-prep-1-1')
        self.assertIn('below the reserve', self.problems('source', source_root=str(self.target), available=RESERVE + 10))
        moved = self.tmp/'production-real'
        self.production.rename(moved)
        os.symlink(moved, self.production)
        self.assertIn('is not an existing, link-free directory', self.problems('source', source_root=str(self.target)))
        self.production.unlink()
        moved.rename(self.production)
        cases = (
            (lambda: os.symlink('/etc/hostname', self.production/'escape'), 'leaves the source tree'),
            (lambda: os.symlink('../../../../out/host/aapt2', self.production/'out_adevtool_deps/host/linux-x86/bin/extra'),
             'out_adevtool_deps/host/linux-x86: bin/extra -> ../../../../out/host/aapt2 leaves the source tree'),
            (lambda: shutil.rmtree(self.production/'out_adevtool_deps'), 'out_adevtool_deps is not a real directory'),
            (lambda: (self.production/'vendor/adevtool/dl/.find-ignore').write_text('x'), 'prune marker'))
        for index, (change, pattern) in enumerate(cases):
            with self.subTest(pattern):
                change()
                code, done = self.run_source(out=f'r{index}')
                self.assertEqual((code, done['ok'], done['written'], done['stagingRemoved']), (1, False, False, None))
                self.assertIn('unsupported source dependencies', done['refused'])
                self.assertIn(pattern, ' '.join(done['selection']['problems']))
                self.assertFalse(os.path.lexists(self.target.parent), 'nothing is created, not even the parent')
                self.build_production()
        tight = json.loads(json.dumps(self.synthetic))
        tight['preparation']['source']['boundBytes'] = 64
        code, done = self.run_source(recipe=tight, out='tight')
        self.assertEqual((code, done['written']), (1, False))
        self.assertIn('above the 64-byte bound', done['refused'])
        self.unverified(done)
        self.assertIsNone(done['selectionSha256'])
        # Space shrank between preflight and the copy: the measured projection is checked again before writing.
        calls = []
        def shrinking(path):
            calls.append(path)
            return fs(10 ** 15 if len(calls) == 1 else RESERVE + 10)(path)
        code, done = self.run_source(out='shrunk', available=shrinking)
        self.assertEqual((code, done['written']), (1, False))
        self.assertIn('is below the reserve', done['refused'])
        self.assertFalse(os.path.lexists(self.target.parent))

    def test_stops_remove_only_this_runs_staging_and_report_the_partial_copy(self):
        parent = self.target.parent
        parent.mkdir()
        (parent/'downloads').mkdir()
        (parent/'downloads/keep').write_text('kept')
        before = self.snapshot(self.production)
        quick = json.loads(json.dumps(self.synthetic))
        quick['preparation']['source']['timeoutSeconds'] = 0.3
        def slow(source, destination, entry):
            time.sleep(0.1)
            return bb.alpha_source.copy_file(source, destination, entry)
        def term(source, destination, entry, seen=[]):
            seen.append(entry)
            if len(seen) == 3:
                os.kill(os.getpid(), signal.SIGTERM)
            return bb.alpha_source.copy_file(source, destination, entry)
        def full(source, destination, entry):
            raise OSError(28, 'No space left on device')
        def changed(source, destination, entry):
            os.utime(source, ns=(1, 1))
            return bb.alpha_source.copy_file(source, destination, entry)
        def appear(source, destination, entry):
            self.target.mkdir(exist_ok=True)
            return bb.alpha_source.copy_file(source, destination, entry)
        samples = []
        def dropping(path):
            samples.append(path)
            return fs(10 ** 15 if len(samples) == 1 else RESERVE - 1)(path)
        fast = json.loads(json.dumps(self.synthetic))
        fast['preparation']['limits']['sampleSeconds'] = 0
        previous = signal.getsignal(signal.SIGTERM)
        for recipe, inject, pattern in ((quick, {'copier': slow}, 'exceeded 0.3 s'), (None, {'copier': term}, 'SIGTERM'),
                                        (None, {'copier': full}, 'OSError: [Errno 28]'),
                                        (None, {'copier': changed}, 'changed in the base during the copy'),
                                        (fast, {'available': dropping}, 'fell below the reserve')):
            with self.subTest(pattern):
                ok, done = self.construct(recipe, **inject)
                self.assertEqual((ok, done['complete'], done['written'], done['stagingRemoved']), (False, False, True, True), done)
                self.unverified(done)
                self.assertIsNone(done['selectionSha256'], 'an incomplete copy has no selection identity')
                self.assertIn(pattern, done['stopped'])
                self.assertLess(done['partial']['entries'], done['partial']['of'])
                self.assertEqual(done['partial']['of'], done['selection']['entries'])
                self.assertEqual(sorted(n.name for n in parent.iterdir()), ['downloads'], 'only this run\'s staging is removed')
                self.assertEqual((parent/'downloads/keep').read_text(), 'kept')
                self.assertFalse(done['targetExists'])
                self.assertIs(signal.getsignal(signal.SIGTERM), previous)
                if pattern != 'changed in the base during the copy':
                    self.assertEqual(self.snapshot(self.production), before)
                before = self.snapshot(self.production)
        ok, done = self.construct(copier=appear)
        self.assertEqual((ok, done['stagingRemoved'], done['targetExists']), (False, True, True))
        self.assertIn('appeared during the copy', done['stopped'])
        self.target.rmdir()
        # A timeout while the selection is still being planned stops before anything is written.
        def stalled(base, policy):
            time.sleep(2)
        ok, done = self.construct(quick, planner=stalled)
        self.assertEqual((ok, done['stopped'], done['written'], done['stagingRemoved']), (False, 'exceeded 0.3 s', False, None))
        self.assertNotIn('partial', done)
        # A stop in a parent this run created removes that parent too; an unexpected error still cleans up, then raises.
        shutil.rmtree(parent)
        ok, done = self.construct(quick, copier=slow)
        self.assertEqual((ok, done['createdParentLeft'], os.path.lexists(parent)), (False, False, False))
        # A SIGTERM during cleanup (e.g. the outer timeout) does not interrupt removing this run's own staging.
        original = bb.remove_own
        def interrupted(*args):
            os.kill(os.getpid(), signal.SIGTERM)
            return original(*args)
        bb.remove_own = interrupted
        try:
            ok, done = self.construct(quick, copier=slow)
        finally:
            bb.remove_own = original
        self.assertEqual((ok, done['stagingRemoved'], os.path.lexists(parent)), (False, True, False))
        def broken(source, destination, entry):
            raise KeyError('unexpected')
        with self.assertRaises(KeyError):
            self.construct(copier=broken)
        self.assertFalse(os.path.lexists(parent))
        self.assertEqual(signal.getitimer(signal.ITIMER_REAL), (0.0, 0.0))

class ObjectsInspectTest(Fixture):
    """O2: inspect-git-objects over a synthetic production tree laid out as O1 found it (never the real one)."""

    def objects_tree(self):
        production = self.production
        gitdir = production/'.repo/project-objects/adevtool.git'
        objects = gitdir/'objects'
        for rel, data in (('pack/pack-1.pack', b'P' * 5000), ('pack/pack-1.idx', b'never read'), ('info/packs', b'never read'),
                          ('info/alternates', b'/elsewhere/objects\n'), ('info/commit-graphs/graph-1.graph', b'G' * 300),
                          ('ab/' + 'c' * 38, b'loose'), ('12/' + '3' * 38, b'loose2'),
                          ('hooks/pre-commit', b'never read'), ('rr-cache/x/preimage', b'never read')):
            (objects/rel).parent.mkdir(parents=True, exist_ok=True)
            (objects/rel).write_bytes(data)
        os.link(objects/'pack/pack-1.pack', objects/'pack/pack-1.keep')
        os.symlink('pack', objects/'pack-link')  # in-tree, relative: reported, never followed
        (gitdir/'config').write_text('[core]\n\tsecret = never read\n')
        (gitdir/'HEAD').write_text('d' * 40 + '\n')
        (production/'.repo/projects/vendor/adevtool.git').mkdir(parents=True)
        os.symlink('../../../project-objects/adevtool.git/objects', production/'.repo/projects/vendor/adevtool.git/objects')
        (production/'out').mkdir()
        (production/'out/huge.bin').write_text('never touched')
        return objects

    def run_objects(self, bounds=None, clock=None, **kw):
        text = bb.inspect_git_objects(self.synthetic, 'immortal-os-builder', self.root, clock=clock or time.monotonic,
                                      bounds=bounds, **kw)
        return text, json.loads(text)

    def fd_touches(self, call):
        """Every filesystem primitive CALL uses, descriptor-relative names resolved to absolute paths."""
        import builtins
        names = ('open', 'stat', 'lstat', 'scandir', 'readlink', 'listdir', 'fstat', 'close', 'read', 'walk', 'statvfs', 'access')
        real, real_builtin = {n: getattr(os, n) for n in names}, builtins.open
        fds, seen, reads = {}, [], []
        def where(path, dir_fd=None):
            if isinstance(path, int):
                return fds.get(path, f'<fd {path}>')
            path = os.fsdecode(os.fspath(path))
            return os.path.normpath(os.path.join(fds[dir_fd], path)) if dir_fd is not None else path
        def s_open(path, flags, mode=0o777, *, dir_fd=None):
            full = where(path, dir_fd)
            seen.append(('open', full, flags))
            fd = real['open'](path, flags, mode, dir_fd=dir_fd)
            fds[fd] = full
            return fd
        def s_stat(path, *a, dir_fd=None, follow_symlinks=True):
            seen.append(('stat' if follow_symlinks else 'lstat', where(path, dir_fd)))
            return real['stat'](path, *a, dir_fd=dir_fd, follow_symlinks=follow_symlinks)
        def s_lstat(path, *a, dir_fd=None):
            seen.append(('lstat', where(path, dir_fd)))
            return real['lstat'](path, *a, dir_fd=dir_fd)
        def s_scandir(path='.'):
            seen.append(('scandir', where(path)))
            return real['scandir'](path)
        def s_readlink(path, *a, dir_fd=None):
            seen.append(('readlink', where(path, dir_fd)))
            return real['readlink'](path, *a, dir_fd=dir_fd)
        def s_fstat(fd):
            seen.append(('fstat', fds.get(fd, f'<fd {fd}>')))
            return real['fstat'](fd)
        def s_close(fd):
            fds.pop(fd, None)
            return real['close'](fd)
        def s_read(fd, n):
            reads.append((fds.get(fd), n))
            return real['read'](fd, n)
        def other(name):
            def call_(path='.', *a, **k):
                seen.append((name, where(path)))
                return real[name](path, *a, **k)
            return call_
        def s_builtin(path, *a, **k):
            seen.append(('builtins.open', os.fsdecode(os.fspath(path))))
            return real_builtin(path, *a, **k)
        spies = {'open': s_open, 'stat': s_stat, 'lstat': s_lstat, 'scandir': s_scandir, 'readlink': s_readlink,
                 'fstat': s_fstat, 'close': s_close, 'read': s_read}
        for name in names:
            setattr(os, name, spies.get(name) or other(name))
        builtins.open = s_builtin
        try:
            return call(), seen, reads
        finally:
            for name, original in real.items():
                setattr(os, name, original)
            builtins.open = real_builtin

    def chain(self, path):
        return ['/', *[str(p) for p in reversed(Path(path).parents) if str(p) != '/'], str(path)]

    def test_touches_only_the_fixed_objects_tree_and_opens_no_content(self):
        objects = self.objects_tree()
        before = self.snapshot(self.production)
        self.guard()
        (text, report), touched, reads = self.fd_touches(lambda: self.run_objects())
        self.assertEqual(self.snapshot(self.production), before, 'nothing changes')
        opened = [(p, f) for op, p, *f in touched if op == 'open']
        dirs = [str(objects/d) for d in ('12', 'ab', 'info', 'info/commit-graphs', 'pack')]
        self.assertEqual(sorted(p for p, _ in opened), sorted(self.chain(objects) + dirs), 'only directories are opened')
        self.assertEqual({f[0] for _, f in opened}, {bb.DIR_OPEN})
        self.assertTrue(bb.DIR_OPEN & os.O_NOFOLLOW and bb.DIR_OPEN & os.O_DIRECTORY and bb.DIR_OPEN & os.O_NONBLOCK)
        self.assertEqual((reads, [p for op, p, *_ in touched if op == 'builtins.open']), ([], []), 'no content is read')
        self.assertEqual(sorted({op for op, *_ in touched}), ['fstat', 'lstat', 'open', 'readlink', 'scandir'])
        self.assertEqual([p for op, p, *_ in touched if op == 'readlink'], [str(objects/'pack-link')], 'link text only')
        listed = {str(objects)} | {str(objects/d) for d in ('12', 'ab', 'info', 'info/commit-graphs', 'pack')}
        self.assertEqual({p for op, p, *_ in touched if op == 'scandir'}, listed)
        lstats = {p for op, p, *_ in touched if op == 'lstat'}
        self.assertFalse({p for p in lstats if '/hooks' in p or '/rr-cache' in p}, 'hooks and rr-cache are never admitted')
        self.assertTrue(all(p.startswith(str(objects) + '/') for p in lstats))
        self.assertFalse(any('/pack-link/' in p or 'out/huge' in p or p.endswith('/config') for _, p, *_ in touched))
        self.assertLessEqual(len(text.encode()), 65536)
        self.assertEqual((report['schema'], report['complete'], report['incomplete'], report['metadataOnly'],
                          report['contentsOpened'], report['linksFollowed'], report['filesystemsCrossed'], report['gitProcess'],
                          report['processesStarted'], report['network'], report['writes'], report['enablesPreparation']),
                         ('roseos.blazer-git-objects-inspection.v1', True, [], True, False, False, False, False, 0, False,
                          False, False))
        self.assertEqual(report['bounds'], {'maxEntries': 100000, 'maxDepth': 3, 'maxSeconds': 120, 'maxOutputBytes': 65536})
        self.assertEqual((report['target']['absolutePath'], report['target']['exists']), (str(objects), True))
        by = report['totals']['byType']
        self.assertEqual({k: v['count'] for k, v in by.items()}, {'file': 7, 'dir': 5, 'link': 1, 'special': 0})
        files = [objects/r for r in ('pack/pack-1.pack', 'pack/pack-1.idx', 'pack/pack-1.keep', 'info/packs',
                 'info/commit-graphs/graph-1.graph', 'ab/' + 'c' * 38, '12/' + '3' * 38)]
        self.assertEqual(by['file']['apparentBytes'], sum(os.lstat(f).st_size for f in files))
        self.assertEqual(report['totals']['hardlinkedRepeats'], 1, 'a hardlink is allocated once')
        self.assertEqual(set(report['groups']), {'.', 'pack', 'info', 'info/commit-graphs', '[0-9a-f]{2}'})
        self.assertEqual(report['groups']['[0-9a-f]{2}']['file']['count'], 2)
        self.assertEqual(report['existenceOnly'], {'info/alternates': {'observed': True, 'exists': True, 'type': 'file'},
                                                   'info/http-alternates': {'observed': True, 'exists': False}})
        self.assertEqual((report['excluded'], report['excludedTotal']), (['hooks', 'rr-cache'], 2))
        self.assertEqual(report['exceptions'], [{'path': 'pack-link', 'kind': 'link', 'linkText': 'pack',
                                                 'linkTextTruncated': False, 'unsafe': False}])
        self.assertEqual(report['totals']['deepestEntry'], 3)
        for secret in ('never read', 'elsewhere', 'secret', 'G' * 10):
            self.assertNotIn(secret, text)

    def test_every_limit_yields_complete_false_with_partial_totals(self):
        objects = self.objects_tree()
        base = dict(bb.GIT_OBJECTS)
        _, few = self.run_objects(bounds={**base, 'maxEntries': 4})
        self.assertEqual(([r['reason'] for r in few['incomplete']], few['complete'], few['totals']['listedEntries']),
                         (['entries'], False, 4))
        self.assertEqual(few['incomplete'][0]['detail'], '4 entries listed')
        self.assertLessEqual(sum(v['count'] for v in few['totals']['byType'].values()), 4)
        (objects/'info/commit-graphs/deeper').mkdir()
        (objects/'info/commit-graphs/deeper/never').write_text('x')
        _, deep = self.run_objects()
        self.assertEqual([r['reason'] for r in deep['incomplete']], ['depth'])
        self.assertIn('info/commit-graphs/deeper', deep['incomplete'][0]['detail'])
        self.assertEqual(deep['totals']['deepestEntry'], 3)
        (_, deep2), touched, _ = self.fd_touches(lambda: self.run_objects())
        self.assertFalse(any('deeper/' in p or (op in ('open', 'scandir') and p.endswith('deeper')) for op, p, *_ in touched))
        shutil.rmtree(objects/'info/commit-graphs/deeper')
        ticks = iter([0, 0, 0, 0, 0, 121])
        _, late = self.run_objects(clock=lambda: next(ticks, 121))
        self.assertEqual(([r['reason'] for r in late['incomplete']], late['complete']), (['deadline'], False))
        self.assertGreater(late['totals']['listedEntries'], 0)
        self.assertLess(late['totals']['listedEntries'], 17)
        text, small = self.run_objects(bounds={**base, 'maxOutputBytes': 1200})
        self.assertLessEqual(len(text.encode()), 1200)
        self.assertEqual((small['complete'], [r['reason'] for r in small['incomplete']]), (False, ['output']))
        self.assertTrue(set(small['trimmed']) <= {'exceptions', 'excluded', 'groups'})
        self.assertEqual(small['totals']['byType']['file']['count'], 7, 'partial totals survive trimming')
        text, tiny = self.run_objects(bounds={**base, 'maxOutputBytes': 10})
        self.assertEqual((tiny['complete'], set(tiny)), (False, {'schema', 'complete', 'incomplete', 'totals', 'trimmed'}))

    def test_links_specials_and_other_filesystems_are_reported_never_entered(self):
        objects = self.objects_tree()
        outside = self.tmp/'outside'
        (outside/'objects').mkdir(parents=True)
        (outside/'objects/never').write_text('never touched')
        for name, text in (('abs', str(outside)), ('up', '../../../../../outside')):
            os.symlink(text, objects/name)
            (_, report), touched, _ = self.fd_touches(lambda: self.run_objects())
            self.assertEqual([r['reason'] for r in report['incomplete']], ['unsafe-link'])
            row = [e for e in report['exceptions'] if e['path'] == name][0]
            self.assertEqual((row['kind'], row['unsafe'], row['linkText']), ('link', True, text))
            self.assertFalse(any(str(outside) in p for _, p, *_ in touched), 'the link is never followed')
            os.unlink(objects/name)
        os.mkfifo(objects/'pack/fifo')
        (_, report), touched, _ = self.fd_touches(lambda: self.run_objects())
        self.assertEqual([r['reason'] for r in report['incomplete']], ['special'])
        self.assertFalse(any(op == 'open' and p.endswith('/fifo') for op, p, *_ in touched), 'a special file is never opened')
        os.unlink(objects/'pack/fifo')
        real_stat = os.stat
        def other_device(path, *a, dir_fd=None, follow_symlinks=True):
            st = real_stat(path, *a, dir_fd=dir_fd, follow_symlinks=follow_symlinks)
            if path == 'pack':
                return types.SimpleNamespace(st_mode=st.st_mode, st_dev=st.st_dev + 1, st_ino=st.st_ino, st_size=st.st_size,
                                             st_blocks=st.st_blocks, st_nlink=st.st_nlink)
            return st
        os.stat = other_device
        try:
            (_, report), touched, _ = self.fd_touches(lambda: self.run_objects())
        finally:
            os.stat = real_stat
        self.assertEqual([r['reason'] for r in report['incomplete']], ['cross-filesystem'])
        self.assertFalse(any(op in ('open', 'scandir') and p == str(objects/'pack') for op, p, *_ in touched))

    def test_inaccessible_and_replaced_entries_refuse_without_traversal(self):
        objects = self.objects_tree()
        real_open = os.open
        def denied(path, flags, mode=0o777, *, dir_fd=None):
            if path == 'pack':
                raise PermissionError(13, 'Permission denied')
            return real_open(path, flags, mode, dir_fd=dir_fd)
        os.open = denied
        try:
            _, report = self.run_objects()
        finally:
            os.open = real_open
        self.assertEqual(([r['reason'] for r in report['incomplete']], report['totals']['byType']['file']['count']),
                         (['inaccessible'], 4), 'partial totals from everything still readable')
        # A directory swapped for a link between its lstat and its open: O_NOFOLLOW refuses; the link is not entered.
        outside = self.tmp/'outside'
        outside.mkdir()
        (outside/'never').write_text('never touched')
        def swap(path, flags, mode=0o777, *, dir_fd=None):
            if path == 'pack' and dir_fd is not None and not os.path.islink(objects/'pack'):
                os.rename(objects/'pack', self.tmp/'moved-pack')
                os.symlink(outside, objects/'pack')
            return real_open(path, flags, mode, dir_fd=dir_fd)
        os.open = swap
        try:
            (_, report), touched, _ = self.fd_touches(lambda: self.run_objects())
        finally:
            os.open = real_open
        self.assertEqual([r['reason'] for r in report['incomplete']], ['replaced'])
        self.assertEqual(report['incomplete'][0]['detail'], 'pack: NotADirectoryError opening the admitted directory')
        self.assertFalse(any(str(outside) in p for _, p, *_ in touched))
        os.unlink(objects/'pack')
        os.rename(self.tmp/'moved-pack', objects/'pack')
        # A directory replaced by another directory: the fstat identity differs from the admitting lstat.
        def another(path, flags, mode=0o777, *, dir_fd=None):
            if path == 'pack' and dir_fd is not None and not (self.tmp/'moved-pack').exists():
                os.rename(objects/'pack', self.tmp/'moved-pack')
                (objects/'pack').mkdir()
                (objects/'pack/planted').write_text('never listed')
            return real_open(path, flags, mode, dir_fd=dir_fd)
        os.open = another
        try:
            (_, report), touched, _ = self.fd_touches(lambda: self.run_objects())
        finally:
            os.open = real_open
        self.assertEqual([r['reason'] for r in report['incomplete']], ['replaced'])
        self.assertFalse(any(op == 'scandir' and p == str(objects/'pack') for op, p, *_ in touched))
        shutil.rmtree(objects/'pack')
        os.rename(self.tmp/'moved-pack', objects/'pack')
        # An entry that vanishes between listing and lstat.
        real_stat = os.stat
        def vanish(path, *a, dir_fd=None, follow_symlinks=True):
            if path == 'packs':
                raise FileNotFoundError(2, 'No such file or directory')
            return real_stat(path, *a, dir_fd=dir_fd, follow_symlinks=follow_symlinks)
        os.stat = vanish
        try:
            _, report = self.run_objects()
        finally:
            os.stat = real_stat
        self.assertIn('replaced', [r['reason'] for r in report['incomplete']])
        # A link replaced after its lstat: readlink fails and the entry is reported, the walk goes on.
        real_readlink = os.readlink
        def gone(path, *a, dir_fd=None):
            raise OSError(22, 'Invalid argument')
        os.readlink = gone
        try:
            _, report = self.run_objects()
        finally:
            os.readlink = real_readlink
        self.assertEqual(report['incomplete'], [{'reason': 'replaced', 'detail': 'pack-link: OSError reading the admitted link'}])
        self.assertEqual(report['totals']['byType']['file']['count'], 7)

    def test_a_linked_absent_or_non_directory_root_refuses_before_listing(self):
        objects = self.objects_tree()
        project_objects = self.production/'.repo/project-objects'
        os.rename(project_objects, self.tmp/'elsewhere')
        os.symlink(self.tmp/'elsewhere', project_objects)
        (_, report), touched, _ = self.fd_touches(lambda: self.run_objects())
        self.assertEqual((report['incomplete'], report['target']['exists']),
                         ([{'reason': 'linked-component', 'detail': f'{project_objects}: NotADirectoryError'}], None))
        self.assertFalse(any(op in ('scandir', 'readlink') for op, *_ in touched))
        self.assertEqual([p for op, p, *_ in touched if op == 'lstat'], [str(project_objects)], 'the refused component only')
        self.assertFalse(any('elsewhere' in p for _, p, *_ in touched))
        os.unlink(project_objects)
        os.rename(self.tmp/'elsewhere', project_objects)
        os.rename(objects, self.tmp/'objects-real')
        os.symlink(self.tmp/'objects-real', objects)
        _, report = self.run_objects()
        self.assertEqual(report['incomplete'][0]['reason'], 'linked-component')
        os.unlink(objects)
        objects.write_text('a file')
        _, report = self.run_objects()
        self.assertEqual(report['incomplete'][0]['reason'], 'not-a-directory')
        os.unlink(objects)
        _, report = self.run_objects()
        self.assertEqual((report['incomplete'][0]['reason'], report['target']['exists'], report['complete']),
                         ('absent', False, False))

    def test_the_largest_capped_report_fits_and_names_its_folding(self):
        objects = self.objects_tree()
        long = b'\x01' * 200
        for i in range(40):
            d = objects/f'd{i:02}'
            d.mkdir()
            os.symlink(os.fsdecode(long + b'/x'), os.path.join(os.fsencode(d), long + b'%02d' % i))
        text, report = self.run_objects()
        self.assertLessEqual(len(text.encode()), 65536)
        self.assertNotIn('trimmed', report, 'the caps keep the worst case inside the 64 KiB output limit')
        self.assertEqual((len(report['groups']), report['groupsFolded'] > 0, len(report['exceptions']),
                          report['exceptionsTotal']), (33, True, 32, 41))
        self.assertIn('(other)', report['groups'])
        self.assertTrue(all(len(e['path']) <= 4 * 128 + 4 for e in report['exceptions']))

    def test_cli_takes_only_the_fixed_host_and_runs_no_other_probe(self):
        import contextlib
        with contextlib.redirect_stdout(io.StringIO()) as out:
            code, touched, reads = self.fd_touches(lambda: bb.main(['inspect-git-objects', '--host', 'immortal-os-builder']))
        self.assertEqual(code, 0)
        report = json.loads(out.getvalue())
        target = '/home/pixelbuild/grapheneos-2026100600/.repo/project-objects/adevtool.git/objects'
        self.assertEqual(report['target']['absolutePath'], target)
        host = sorted({(op, p) for op, p, *_ in touched if not p.startswith(str(ROOT) + '/') and op != 'close'
                       and not ('/LC_MESSAGES/' in p and p.endswith('.mo'))})
        allowed = set(self.chain(target))
        self.assertTrue(all(op in ('open', 'fstat', 'scandir', 'lstat', 'readlink') for op, _ in host), host)
        self.assertTrue(all(p in allowed or p.startswith(target + '/') for _, p in host), host)
        if not os.path.lexists('/home/pixelbuild'):
            self.assertEqual([p for op, p in host if op == 'open'], ['/', '/home', '/home/pixelbuild'])
            self.assertEqual((report['incomplete'][0]['reason'], report['complete']), ('absent', False))
        self.assertFalse(any('rose-actions' in p or 'vendor/adevtool/.git' in p for _, p in host), 'no unrelated probe')
        script = str(ROOT/'scripts/blazer-build-only.py')
        for argv in ([], ['--host', 'other-builder'], ['--host', 'immortal-os-builder', '--max-entries', '5'],
                     ['--host', 'immortal-os-builder', '/tmp/x'], ['--host', 'immortal-os-builder', '--target-set', 'x']):
            with self.subTest(argv):
                bad = subprocess.run([sys.executable, script, 'inspect-git-objects', *argv], capture_output=True, text=True)
                self.assertEqual((bad.returncode, bad.stdout), (2, ''))
        ok = subprocess.run([sys.executable, script, 'inspect-git-objects', '--host', 'immortal-os-builder'],
                            capture_output=True, text=True)
        self.assertEqual(ok.returncode, 0, ok.stderr)
        self.assertEqual(len(ok.stdout.splitlines()), 1)
        for old in (['inspect-git', '--host', 'immortal-os-builder'],
                    ['inspect-git-set', '--host', 'immortal-os-builder', '--target-set', 'adevtool-projects-gitdir']):
            prior = subprocess.run([sys.executable, script, *old], capture_output=True, text=True)
            self.assertEqual(prior.returncode, 0, prior.stderr)
            self.assertNotIn('objects', json.loads(prior.stdout)['schema'], 'O1 modes are unchanged')

    def test_alternates_absence_is_observed_only_from_a_complete_listing(self):
        objects = self.objects_tree()
        shutil.rmtree(objects/'info')
        (objects/'alternates').write_text('a root entry of the same name, not info/alternates')
        _, report = self.run_objects()
        self.assertEqual(report['existenceOnly'], {'info/alternates': {'observed': True, 'exists': False},
                                                   'info/http-alternates': {'observed': True, 'exists': False}})
        _, cut = self.run_objects(bounds={**bb.GIT_OBJECTS, 'maxEntries': 2})
        self.assertEqual(cut['existenceOnly'], {'info/alternates': {'observed': False},
                                                'info/http-alternates': {'observed': False}}, 'a cut listing observes nothing')

    def test_a_drifted_recipe_or_another_host_refuses(self):
        with self.assertRaisesRegex(bb.InvalidInput, '--host must be immortal-os-builder'):
            bb.inspect_git_objects(self.synthetic, 'other-builder', self.root)
        drifted = json.loads(json.dumps(self.synthetic))
        drifted['preparation']['gitObjectsInspection']['path'] = '.repo/project-objects'
        with self.assertRaisesRegex(bb.InvalidInput, 'gitObjectsInspection must be exactly'):
            bb.inspect_git_objects(drifted, 'immortal-os-builder', self.root)

class InspectTest(Fixture):
    """inspect is metadata-only: candidates are proposals that never enable preparation, and nothing it scans changes."""
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
        self.assertEqual((c['proposed'], c['verified'], c['binding']), (True, False, False))
        self.assertEqual(c['root']['path'], str(cand))
        self.assertEqual([a['path'] for a in c['root']['ancestors']], [str(cand.parent), str(self.tmp)],
                         'ancestors stop at the boundary, never above it')
        self.assertEqual([a['path'] for a in c['sourceRoot']['ancestors']], [str(cand), str(cand.parent), str(self.tmp)])
        self.assertTrue(c['sourceRoot']['sameDeviceAsProduction'])
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
        for unknown in ('reflink support (never assumed)',
                        'isolated source copy bytes (the source stage measures its full selection before writing; never this walk)',
                        'Pro factory archive content (its size is the recorded HEAD Content-Length; only the sha256 pin identifies it)'):
            self.assertIn(unknown, report['unknowns'])

    def test_node_from_path_reports_the_runner_resolution_as_the_candidate(self):
        nodebin = self.tmp/'runner-bin'
        nodebin.mkdir()
        os.symlink(self.node, nodebin/'node')
        self.guard()
        report = bb.inspect(self.synthetic, self.op(node=None), root=self.root, statvfs=fs(10 ** 15),
                            candidates=bb.candidate_paths(str(self.tmp/'c'), nodeFromPath=True),
                            environ={'PATH': f'/nonexistent:{nodebin}:/usr/bin'})
        node = report['node']['candidate']
        self.assertEqual((node['how'], node['path'], node['pathEntry'], node['realpath']),
                         ('--candidate-node-from-path', str(nodebin/'node'), str(nodebin), str(self.node)))
        self.assertEqual((node['realpathMeta']['uid'], node['realpathMeta']['inode']), (os.getuid(), os.stat(self.node).st_ino))
        self.assertEqual(node['version'], 'not executed (metadata only)')
        self.assertEqual(bb.ancestors('/srv/elsewhere/blazer-prep-1-1', str(self.tmp)), [],
                         'a path outside the boundary reports no ancestors and never walks up to /')
        self.assertEqual([a['path'] for a in bb.ancestors(self.tmp/'x/y', str(self.tmp))], [str(self.tmp)])
        with self.assertRaisesRegex(bb.InvalidInput, 'mutually exclusive'):
            bb.candidate_paths(None, node=str(self.node), nodeFromPath=True)
        with self.assertRaisesRegex(bb.InvalidInput, '--candidate-root must be an absolute path'):
            bb.candidate_paths('/a/../b')

    def git_cmd(self, where, *args):
        env = {'PATH': '/usr/bin:/bin', 'HOME': str(self.tmp), 'GIT_CONFIG_NOSYSTEM': '1'}
        return subprocess.run(['git', '-C', str(where), *args], env=env, check=True, capture_output=True, text=True).stdout

    def git_report(self, tree):
        budget = {'entries': 10 ** 6, 'deadline': time.monotonic() + 60}
        return bb.git_facts(tree, 'vendor/adevtool/.git', budget, time.monotonic)

    def opened_during(self, call):
        """Every path os.open / builtins.open sees while CALL runs (the inspection must open HEAD only)."""
        import builtins
        seen, real_open, real_builtin = [], os.open, builtins.open
        def spy_os(path, *a, **k):
            seen.append(os.fspath(path))
            return real_open(path, *a, **k)
        def spy_builtin(path, *a, **k):
            seen.append(os.fspath(path))
            return real_builtin(path, *a, **k)
        os.open, builtins.open = spy_os, spy_builtin
        try:
            return call(), seen
        finally:
            os.open, builtins.open = real_open, real_builtin

    def test_git_inspection_reads_only_head_from_a_genuine_repository(self):
        trees = {}
        for name in ('ref', 'detached'):
            adevtool = self.tmp/name/'vendor/adevtool'
            adevtool.mkdir(parents=True)
            self.git_cmd(adevtool, 'init', '-q')
            self.git_cmd(adevtool, '-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-q', '--allow-empty', '-m', 'synthetic')
            trees[name] = (self.tmp/name, self.git_cmd(adevtool, 'rev-parse', 'HEAD').strip())
        self.git_cmd(self.tmp/'detached/vendor/adevtool', 'checkout', '-q', '--detach')
        tree, adevtool = self.tmp/'ref', self.tmp/'ref/vendor/adevtool'
        before = self.snapshot(adevtool/'.git')
        self.guard()  # from here on no process may start: the inspection itself never runs git
        facts, opened = self.opened_during(lambda: self.git_report(tree))
        self.assertEqual(self.snapshot(adevtool/'.git'), before, 'nothing under .git changes')
        self.assertEqual(opened, [str(adevtool/'.git/HEAD')], 'HEAD is the only file opened')
        self.assertEqual((facts['meta']['type'], facts['gitProcess'], facts['truncated']), ('dir', False, False))
        names = {row['name'] for row in facts['entries']}
        self.assertTrue({'HEAD', 'config', 'objects', 'refs'} <= names, names)
        self.assertEqual(facts['head']['form'], 'ref')
        self.assertTrue(facts['head']['ref'].startswith('refs/heads/'))
        facts = self.git_report(trees['detached'][0])
        self.assertEqual((facts['head']['form'], facts['head']['revision']), ('detached', trees['detached'][1]))

    def test_repo_style_links_are_reported_never_followed(self):
        tree = self.tmp/'tree'
        git = tree/'vendor/adevtool/.git'
        git.mkdir(parents=True)
        (git/'HEAD').write_text('a' * 40 + '\n')
        os.symlink('../../../.repo/project-objects/adevtool.git/objects', git/'objects')
        os.symlink(str(self.tmp/'outside/config'), git/'config')
        os.mkfifo(self.tmp/'fifo')
        os.symlink(str(self.tmp/'fifo'), git/'packed-refs')  # following this would block
        (git/'refs').mkdir()
        facts, opened = self.opened_during(lambda: self.git_report(tree))
        self.assertEqual(opened, [str(git/'HEAD')])
        rows = {row['name']: row for row in facts['entries']}
        self.assertEqual((rows['objects']['type'], rows['objects']['linkText'], rows['objects']['linkAbsolute'],
                          rows['objects']['linkLeavesTree']),
                         ('link', '../../../.repo/project-objects/adevtool.git/objects', False, False))
        self.assertEqual((rows['config']['linkAbsolute'], rows['config']['linkLeavesTree']), (True, True))
        self.assertEqual(rows['refs']['type'], 'dir')
        self.assertNotIn('entries', rows['refs'], 'one level only')
        self.assertEqual((facts['head']['form'], facts['head']['revision']), ('detached', 'a' * 40))
        os.symlink('../../../../escape', git/'zz-escape')
        self.assertTrue({r['name']: r for r in self.git_report(tree)['entries']}['zz-escape']['linkLeavesTree'])

    def test_git_paths_that_are_links_files_or_unsafe_are_never_read(self):
        tree = self.tmp/'tree'
        (tree/'vendor/adevtool').mkdir(parents=True)
        git = tree/'vendor/adevtool/.git'
        (git).write_text('gitdir: /home/pixelbuild/grapheneos-2026100600/.repo/projects/vendor/adevtool.git\n')
        facts, opened = self.opened_during(lambda: self.git_report(tree))
        self.assertEqual((facts['meta']['type'], opened), ('file', []), 'a gitfile is lstat only')
        self.assertIn('not read', facts['gitfile'])
        self.assertNotIn('pixelbuild', json.dumps(facts))
        git.unlink()
        real = self.tmp/'real-git'
        real.mkdir()
        (real/'HEAD').write_text('b' * 40 + '\n')
        os.symlink(real, git)
        facts, opened = self.opened_during(lambda: self.git_report(tree))
        self.assertEqual((facts['meta']['type'], facts['linkText'], opened), ('link', str(real), []))
        self.assertNotIn('head', facts)
        git.unlink()
        git.mkdir()
        os.symlink(str(real/'HEAD'), git/'HEAD')
        facts, opened = self.opened_during(lambda: self.git_report(tree))
        self.assertEqual((facts['head']['meta']['type'], facts['head']['linkText'], opened), ('link', str(real/'HEAD'), []))
        (git/'HEAD').unlink()
        os.mkfifo(git/'HEAD')
        facts, opened = self.opened_during(lambda: self.git_report(tree))
        self.assertEqual((facts['head']['meta']['type'], opened), ('other', []), 'a FIFO HEAD is never opened')
        (git/'HEAD').unlink()
        for content, form in ((b'c' * 40 + b'\n' + b'x' * 30, 'unrecognised'), (b'secret-looking text\n', 'unrecognised'),
                              (b'ref: refs/heads/main\n', 'ref'), (b'C' * 40 + b'\n', 'unrecognised')):
            (git/'HEAD').write_bytes(content)
            facts = self.git_report(tree)
            self.assertEqual(facts['head']['form'], form, content)
            if form == 'unrecognised':
                self.assertNotIn('secret', json.dumps(facts))
                self.assertEqual(set(facts['head']) - {'meta'}, {'form', 'bytes'}, 'only its length, never its content')
        (git/'HEAD').write_bytes(b'd' * 40 + b'\n' + b'e' * 40)
        self.assertEqual(self.git_report(tree)['head']['bytes'], 'more than 64')
        shutil.rmtree(git)
        moved = self.tmp/'moved-adevtool'
        (tree/'vendor/adevtool').rename(moved)
        os.symlink(moved, tree/'vendor/adevtool')
        (moved/'.git').mkdir()
        facts, opened = self.opened_during(lambda: self.git_report(tree))
        self.assertEqual((facts['symlinkComponent'], opened), (str(tree/'vendor/adevtool'), []))
        self.assertNotIn('entries', facts)
        (tree/'vendor/adevtool').unlink()
        (tree/'vendor/adevtool').mkdir()
        self.assertEqual(self.git_report(tree)['meta']['exists'], False)

    def test_git_inspection_is_bounded_and_escapes_names(self):
        tree = self.tmp/'tree'
        git = tree/'vendor/adevtool/.git'
        git.mkdir(parents=True)
        for i in range(300):
            (git/f'f{i:03d}').write_text('')
        (git/'tab\tname\n').write_text('')
        os.symlink('x' * 2000, git/'long-link')
        facts = self.git_report(tree)
        self.assertEqual((facts['entriesScanned'], len(facts['entries']), facts['truncated']), (256, 64, True))
        shutil.rmtree(git)
        git.mkdir()
        (git/'tab\tname\n').write_text('')
        os.symlink('x' * 2000, git/'long-link')
        rows = {row['name']: row for row in self.git_report(tree)['entries']}
        self.assertIn('tab\\x09name\\x0a', rows)
        self.assertEqual((len(rows['long-link']['linkText']), rows['long-link']['linkTextTruncated']), (1024, True))
        budget = {'entries': 1, 'deadline': time.monotonic() + 60}
        small = bb.git_facts(tree, 'vendor/adevtool/.git', budget, time.monotonic)
        self.assertEqual((small['entriesScanned'], small['truncated']), (1, True), 'the shared inspect budget bounds it')

    def test_inspect_reports_git_facts_for_reference_and_candidate_and_gates_nothing(self):
        ref = self.reference()
        (ref/'vendor/adevtool/.git').mkdir(parents=True)
        (ref/'vendor/adevtool/.git/HEAD').write_text(ADEVTOOL + '\n')
        cand = self.tmp/'rose-actions/blazer-prep-41000000001-1'
        (cand/'source/vendor/adevtool').mkdir(parents=True)
        report = bb.inspect(self.synthetic, self.op(source_root=None, download_dir=None, node=None), root=self.root,
                            statvfs=fs(10 ** 15), candidates=bb.candidate_paths(str(cand)), environ={'PATH': ''})
        self.assertEqual(report['schema'], 'roseos.blazer-preparation-inspection.v2')
        self.assertEqual((report['reference']['adevtoolGit']['head']['form'], report['reference']['adevtoolGit']['head']['revision']),
                         ('detached', ADEVTOOL))
        self.assertEqual(report['candidates']['sourceRoot']['adevtoolGit']['meta']['exists'], False)
        self.assertEqual((report['enablesPreparation'], report['processesStarted'], report['writes']), (False, 0, False))

    def touched_during(self, call):
        """Every path the filesystem primitives see while CALL runs."""
        import builtins
        names = ('lstat', 'stat', 'open', 'scandir', 'readlink', 'listdir', 'walk', 'statvfs', 'access')
        seen, originals = [], {name: getattr(os, name) for name in names}
        reads, real_read, real_builtin = [], os.read, builtins.open
        def spy(name):
            def call_(path, *a, **k):
                if not isinstance(path, int):  # a non-following stat (pathlib's lstat) is recorded as lstat
                    seen.append(('lstat' if name == 'stat' and k.get('follow_symlinks') is False else name,
                                 os.fsdecode(os.fspath(path))))
                return originals[name](path, *a, **k)
            return call_
        def spy_open(path, *a, **k):
            seen.append(('builtins.open', os.fsdecode(os.fspath(path))))
            return real_builtin(path, *a, **k)
        def spy_read(fd, n):
            reads.append(n)
            return real_read(fd, n)
        for name in names:
            setattr(os, name, spy(name))
        builtins.open, os.read = spy_open, spy_read
        try:
            return call(), seen, reads
        finally:
            for name, original in originals.items():
                setattr(os, name, original)
            builtins.open, os.read = real_builtin, real_read

    def git_targets(self):
        """A genuine repository at the production target and a repo-style .git at the candidate, beside other content."""
        production = self.reference()
        adevtool = production/'vendor/adevtool'
        env = {'PATH': '/usr/bin:/bin', 'HOME': str(self.tmp), 'GIT_CONFIG_NOSYSTEM': '1'}
        for args in (('init', '-q'), ('-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-q', '--allow-empty', '-m', 's')):
            subprocess.run(['git', '-C', str(adevtool), *args], env=env, check=True, capture_output=True)
        head = subprocess.run(['git', '-C', str(adevtool), 'rev-parse', 'HEAD'], env=env, check=True, capture_output=True,
                              text=True).stdout.strip()
        for rel in ('out/soong/huge.bin', 'out_adevtool_deps/host/linux-x86/bin/aapt2', 'vendor/adevtool/src/config/paths.ts'):
            (production/rel).parent.mkdir(parents=True, exist_ok=True)
            (production/rel).write_text('never touched')
        candidate = self.tmp/'rose-actions/blazer-prep-41473133543-1/source/vendor/adevtool'
        (candidate/'.git').mkdir(parents=True)
        (candidate/'.git/HEAD').write_text('a' * 40 + '\n')
        os.symlink('../../../.repo/project-objects/adevtool.git/objects', candidate/'.git/objects')
        (candidate/'.git/config').write_text('[core]\n\tsecret = never read\n')
        (candidate/'src').mkdir()
        (candidate/'src/never.ts').write_text('never touched')
        (self.tmp/'rose-actions/blazer-prep-41473133543-1/downloads').mkdir()
        return production, candidate, head

    def test_inspect_git_touches_only_the_two_targets(self):
        production, candidate, head = self.git_targets()
        trees = (production, self.tmp/'rose-actions')
        before = self.snapshot(*trees)
        self.guard()
        report, touched, reads = self.touched_during(lambda: bb.inspect_git(self.synthetic, 'immortal-os-builder', self.root))
        self.assertEqual(self.snapshot(*trees), before, 'nothing in either tree changes')
        targets = [Path(self.synthetic['preparation']['gitInspection']['targets'][n]) for n in ('production', 'candidate')]
        allowed = set()
        for target in targets:
            allowed |= {str(target), str(target/'HEAD')} | {str(target/n) for n in os.listdir(target)}
            allowed |= {str(p) for p in target.parent.parents} | {str(target.parent)}
        stray = sorted({path for _, path in touched} - allowed)
        self.assertEqual(stray, [], 'only the targets, one level inside them, HEAD and the ancestors\' lstat')
        opened = sorted(path for name, path in touched if name in ('open', 'builtins.open'))
        self.assertEqual(opened, sorted(str(t/'HEAD') for t in targets), 'HEAD is the only content opened')
        self.assertTrue(reads and set(reads) == {65}, 'HEAD reads ask for at most 65 bytes')
        self.assertFalse(any(name in ('walk', 'statvfs', 'access') for name, _ in touched), 'no tree walk, size or Node probe')
        self.assertEqual((report['schema'], report['processesStarted'], report['writes'], report['gitProcess'],
                          report['enablesPreparation'], report['truncated']),
                         ('roseos.blazer-git-inspection.v1', 0, False, False, False, False))
        self.assertEqual(report['bounds'], {'scanEntries': 256, 'listedEntries': 64, 'linkTextBytes': 1024,
                                            'headAcceptedBytes': 64, 'headReadBytes': 65, 'maxSeconds': 60})
        self.assertEqual(set(report), {'schema', 'device', 'host', 'metadataOnly', 'processesStarted', 'network', 'writes',
                                       'gitProcess', 'enablesPreparation', 'bounds', 'targets', 'truncated', 'elapsedSeconds'})
        prod, cand = report['targets']['production'], report['targets']['candidate']
        self.assertEqual((prod['absolutePath'], prod['head']['form']), (str(targets[0]), 'ref'))
        self.assertEqual((cand['head']['form'], cand['head']['revision']), ('detached', 'a' * 40))
        self.assertEqual({r['name']: r['linkLeavesTree'] for r in cand['entries'] if r['type'] == 'link'}, {'objects': False})
        self.assertNotIn('secret', json.dumps(report))
        self.assertEqual(prod['contentRead'], 'HEAD only (at most 65 bytes read, 64 accepted)')
        self.assertTrue(head)

    def test_inspect_git_reports_absent_targets_and_refuses_bad_input(self):
        report = bb.inspect_git(self.synthetic, 'immortal-os-builder', self.root)
        self.assertEqual({n: t['meta']['exists'] for n, t in report['targets'].items()}, {'production': False, 'candidate': False})
        with self.assertRaisesRegex(bb.InvalidInput, '--host'):
            bb.inspect_git(self.synthetic, 'Bad Host', self.root)
        moved = json.loads(json.dumps(self.synthetic))
        moved['preparation']['gitInspection']['targets']['candidate'] = f'{self.tmp}/production/x/vendor/adevtool/.git'
        with self.assertRaisesRegex(bb.InvalidInput, 'candidate target must be'):
            bb.inspect_git(moved, 'immortal-os-builder', self.root)

    def test_head_reads_at_most_65_bytes_and_accepts_at_most_64(self):
        tree = self.tmp/'tree'
        git = tree/'vendor/adevtool/.git'
        git.mkdir(parents=True)
        exact = b'ref: refs/heads/' + b'b' * 47 + b'\n'
        self.assertEqual(len(exact), 64)
        (git/'HEAD').write_bytes(exact)
        facts, _, reads = self.touched_during(lambda: self.git_report(tree))
        self.assertEqual((facts['head']['form'], facts['head']['ref'], reads), ('ref', 'refs/heads/' + 'b' * 47, [65]))
        (git/'HEAD').write_bytes(b'ref: refs/heads/' + b'b' * 48 + b'\n')  # 65 bytes: one over
        facts = self.git_report(tree)
        self.assertEqual((facts['head']['form'], facts['head']['bytes']), ('unrecognised', 'more than 64'))
        self.assertNotIn('bbbb', json.dumps(facts['head']), 'the over-limit content is never reported')

    def test_inspect_git_cli_takes_only_the_host(self):
        script = str(ROOT/'scripts/blazer-build-only.py')
        ok = subprocess.run([sys.executable, script, 'inspect-git', '--host', 'immortal-os-builder'], capture_output=True, text=True)
        self.assertEqual(ok.returncode, 0, ok.stderr)
        report = json.loads(ok.stdout)
        self.assertEqual((report['schema'], set(report['targets'])), ('roseos.blazer-git-inspection.v1', {'production', 'candidate'}))
        self.assertEqual(report['targets']['candidate']['absolutePath'],
                         '/home/pixelbuild/rose-actions/blazer-prep-41473133543-1/source/vendor/adevtool/.git')
        for extra in (['--lock', '/tmp/x'], ['--source-root', '/tmp/x'], ['/tmp/x'], ['--max-entries', '5'], []):
            with self.subTest(extra):
                argv = [sys.executable, script, 'inspect-git', *(extra if extra else [])]
                if extra:
                    argv[3:3] = ['--host', 'immortal-os-builder']
                bad = subprocess.run(argv, capture_output=True, text=True)
                self.assertEqual(bad.returncode, 2, (extra, bad.stdout))
        old = subprocess.run([sys.executable, script, 'inspect', '--host', 'immortal-os-builder', '--lock', str(self.lock)],
                             capture_output=True, text=True)
        self.assertEqual((old.returncode, json.loads(old.stdout)['schema']), (0, 'roseos.blazer-preparation-inspection.v2'),
                         'the existing inspect mode is unchanged')

    def projects_gitdir(self):
        """A synthetic production tree laid out as observation 41566744429 found it: vendor/adevtool/.git a link to
        ../../.repo/projects/vendor/adevtool.git, whose objects link to .repo/project-objects, beside other content."""
        production = self.reference()
        gitdir = production/'.repo/projects/vendor/adevtool.git'
        (gitdir/'refs/heads').mkdir(parents=True)
        (gitdir/'refs/heads/main').write_text('b' * 40 + '\n')
        (gitdir/'HEAD').write_text(ADEVTOOL + '\n')
        (gitdir/'packed-refs').write_text('# never read\n')
        (gitdir/'shallow').write_text('c' * 40 + '\n')
        (gitdir/'config').write_text('[core]\n\tsecret = never read\n')
        objects = production/'.repo/project-objects/adevtool.git/objects'
        (objects/'pack').mkdir(parents=True)
        (objects/'pack/pack-1.pack').write_text('never touched')
        os.symlink('../../../project-objects/adevtool.git/objects', gitdir/'objects')
        os.symlink('/etc', gitdir/'outside')
        (production/'vendor/adevtool').mkdir(parents=True, exist_ok=True)
        os.symlink('../../.repo/projects/vendor/adevtool.git', production/'vendor/adevtool/.git')
        (production/'.repo/projects/other.git').mkdir(parents=True)
        (production/'.repo/projects/other.git/HEAD').write_text('d' * 40 + '\n')
        return production, gitdir

    def test_inspect_git_set_touches_only_the_projects_gitdir(self):
        production, gitdir = self.projects_gitdir()
        before = self.snapshot(production)
        self.guard()
        report, touched, reads = self.touched_during(
            lambda: bb.inspect_git_set(self.synthetic, 'immortal-os-builder', 'adevtool-projects-gitdir', self.root))
        self.assertEqual(self.snapshot(production), before, 'nothing in the tree changes')
        allowed = {str(gitdir), str(gitdir/'HEAD')} | {str(gitdir/n) for n in os.listdir(gitdir)}
        allowed |= {str(p) for p in gitdir.parents}
        self.assertEqual(sorted({path for _, path in touched} - allowed), [],
                         'only the target, one level inside it, HEAD and the ancestors\' lstat')
        self.assertEqual(sorted({name for name, _ in touched}), ['lstat', 'open', 'readlink', 'scandir'])
        self.assertEqual(sorted(path for name, path in touched if name == 'scandir'), [str(gitdir)], 'one level only')
        self.assertEqual(sorted(path for name, path in touched if name in ('open', 'builtins.open')), [str(gitdir/'HEAD')],
                         'HEAD is the only content opened')
        self.assertEqual(sorted(path for name, path in touched if name == 'readlink'),
                         [str(gitdir/'objects'), str(gitdir/'outside')], 'link text only, never followed')
        self.assertEqual(reads, [65], 'one HEAD read of at most 65 bytes')
        self.assertFalse(any(str(production/'vendor') in path or 'project-objects' in path for _, path in touched),
                         'neither the vendor/adevtool/.git link nor the object store is touched')
        self.assertEqual(set(report), {'schema', 'device', 'host', 'targetSet', 'metadataOnly', 'processesStarted', 'network',
                                       'writes', 'gitProcess', 'enablesPreparation', 'bounds', 'targets', 'truncated',
                                       'elapsedSeconds'})
        self.assertEqual((report['schema'], report['targetSet'], report['processesStarted'], report['network'],
                          report['writes'], report['gitProcess'], report['enablesPreparation'], report['truncated']),
                         ('roseos.blazer-git-target-set-inspection.v1', 'adevtool-projects-gitdir', 0, False, False, False,
                          False, False))
        self.assertEqual(report['bounds'], {'scanEntries': 256, 'listedEntries': 64, 'linkTextBytes': 1024,
                                            'headAcceptedBytes': 64, 'headReadBytes': 65, 'maxSeconds': 60})
        facts = report['targets']['projectsGitdir']
        self.assertEqual(set(report['targets']), {'projectsGitdir'})
        self.assertEqual((facts['absolutePath'], facts['path'], facts['meta']['type'], facts['symlinkComponent']),
                         (str(gitdir), '.repo/projects/vendor/adevtool.git', 'dir', None))
        self.assertEqual((facts['head']['form'], facts['head']['revision']), ('detached', ADEVTOOL))
        links = {r['name']: (r['linkText'], r['linkAbsolute'], r['linkLeavesTree']) for r in facts['entries'] if r['type'] == 'link'}
        self.assertEqual(links, {'objects': ('../../../project-objects/adevtool.git/objects', False, False),
                                 'outside': ('/etc', True, True)})
        self.assertEqual(sorted(r['name'] for r in facts['entries']),
                         ['HEAD', 'config', 'objects', 'outside', 'packed-refs', 'refs', 'shallow'])
        self.assertEqual((facts['entriesScanned'], facts['entriesListedOf']), (7, 7))
        self.assertNotIn('secret', json.dumps(report))
        self.assertNotIn('b' * 40, json.dumps(report), 'refs contents are never read')

    def test_inspect_git_set_is_bounded_and_refuses_a_linked_component(self):
        production, gitdir = self.projects_gitdir()
        for i in range(300):
            (gitdir/f'n{i:03}').write_text('')
        clock = iter(range(10 ** 6))
        report, _, reads = self.touched_during(lambda: bb.inspect_git_set(
            self.synthetic, 'immortal-os-builder', 'adevtool-projects-gitdir', self.root, clock=lambda: next(clock) * 1e-6))
        facts = report['targets']['projectsGitdir']
        self.assertEqual((facts['entriesScanned'], len(facts['entries']), facts['truncated'], report['truncated']),
                         (256, 64, True, True), 'scan 256, report 64')
        self.assertEqual(reads, [65])
        late = iter([0])
        timed = bb.inspect_git_set(self.synthetic, 'immortal-os-builder', 'adevtool-projects-gitdir', self.root,
                                   clock=lambda: next(late, 61))
        self.assertEqual((timed['targets']['projectsGitdir']['entriesScanned'], timed['truncated']), (0, True),
                         'the 60 s deadline stops the scan')
        # A link anywhere above the target is reported, never traversed into the gitdir.
        shutil.rmtree(production/'.repo/projects')
        (production/'elsewhere/vendor').mkdir(parents=True)
        os.symlink('../elsewhere', production/'.repo/projects')
        report, touched, reads = self.touched_during(
            lambda: bb.inspect_git_set(self.synthetic, 'immortal-os-builder', 'adevtool-projects-gitdir', self.root))
        facts = report['targets']['projectsGitdir']
        self.assertEqual((facts['symlinkComponent'], reads), (str(production/'.repo/projects'), []))
        self.assertFalse(any(name in ('scandir', 'open', 'readlink') for name, _ in touched))
        # A link at the target itself: its text only.
        shutil.rmtree(production/'elsewhere')
        os.unlink(production/'.repo/projects')
        (production/'.repo/projects/vendor').mkdir(parents=True)
        os.symlink('/home/elsewhere/adevtool.git', production/'.repo/projects/vendor/adevtool.git')
        report, touched, reads = self.touched_during(
            lambda: bb.inspect_git_set(self.synthetic, 'immortal-os-builder', 'adevtool-projects-gitdir', self.root))
        facts = report['targets']['projectsGitdir']
        self.assertEqual((facts['meta']['type'], facts['linkText'], reads), ('link', '/home/elsewhere/adevtool.git', []))
        self.assertFalse(any(name in ('scandir', 'open') for name, _ in touched))
        absent = bb.inspect_git_set(self.synthetic, 'immortal-os-builder', 'adevtool-projects-gitdir', self.root)
        self.assertEqual(absent['targets']['projectsGitdir']['meta']['type'], 'link')
        os.unlink(production/'.repo/projects/vendor/adevtool.git')
        absent = bb.inspect_git_set(self.synthetic, 'immortal-os-builder', 'adevtool-projects-gitdir', self.root)
        self.assertEqual(absent['targets']['projectsGitdir']['meta']['exists'], False)

    def test_inspect_git_set_refuses_bad_input_and_leaves_inspect_git_unchanged(self):
        with self.assertRaisesRegex(bb.InvalidInput, '--target-set must be one of'):
            bb.inspect_git_set(self.synthetic, 'immortal-os-builder', 'production', self.root)
        with self.assertRaisesRegex(bb.InvalidInput, '--host'):
            bb.inspect_git_set(self.synthetic, 'Bad Host', 'adevtool-projects-gitdir', self.root)
        drifted = json.loads(json.dumps(self.synthetic))
        drifted['preparation']['gitInspection']['targetSets']['adevtool-projects-gitdir']['projectsGitdir']['path'] = \
            '.repo/project-objects/adevtool.git'
        for call in (lambda r: bb.inspect_git_set(r, 'immortal-os-builder', 'adevtool-projects-gitdir', self.root),
                     lambda r: bb.inspect_git(r, 'immortal-os-builder', self.root)):
            with self.assertRaisesRegex(bb.InvalidInput, 'targetSets must be exactly'):
                call(drifted)
        # inspect-git still inspects exactly its two targets, never the target set.
        production, gitdir = self.projects_gitdir()
        report, touched, _ = self.touched_during(lambda: bb.inspect_git(self.synthetic, 'immortal-os-builder', self.root))
        self.assertEqual((report['schema'], set(report['targets'])), ('roseos.blazer-git-inspection.v1', {'production', 'candidate'}))
        self.assertNotIn('targetSet', report)
        self.assertFalse(any(name == 'scandir' and path == str(gitdir) for name, path in touched))
        self.assertEqual(report['targets']['production']['meta']['type'], 'link', 'the production .git link is not followed')

    def test_inspect_git_set_cli_touches_no_other_host_path(self):
        """In process, against the checked-in recipe: loading and inspecting touch nothing outside this checkout but
        the one target (absent here) and its ancestors' lstat; the old inspect-git touches exactly what it did."""
        import contextlib
        def outside(argv):
            with contextlib.redirect_stdout(io.StringIO()) as out:
                code, touched, reads = self.touched_during(lambda: bb.main(argv))
            self.assertEqual(code, 0)
            # argparse's gettext probes the interpreter's message catalogues (*/LC_MESSAGES/*.mo) when a locale is set;
            # those are the interpreter's own lookups, never a recipe or host path.
            return json.loads(out.getvalue()), sorted({(n, p) for n, p in touched if not p.startswith(str(ROOT) + '/')
                                                       and not ('/LC_MESSAGES/' in p and p.endswith('.mo'))}), reads
        report, touched, reads = outside(['inspect-git-set', '--host', 'immortal-os-builder', '--target-set',
                                          'adevtool-projects-gitdir'])
        target = '/home/pixelbuild/grapheneos-2026100600/.repo/projects/vendor/adevtool.git'
        if report['targets']['projectsGitdir']['meta'].get('exists') is False:
            self.assertEqual(touched, [('lstat', '/home'), ('lstat', '/home/pixelbuild'), ('lstat', target)])
        self.assertTrue(all(n == 'lstat' or p.startswith(target + '/') or p == target for n, p in touched), touched)
        old, old_touched, _ = outside(['inspect-git', '--host', 'immortal-os-builder'])
        self.assertEqual(set(old['targets']), {'production', 'candidate'})
        if all(t['meta'].get('exists') is False for t in old['targets'].values()):
            self.assertEqual(old_touched, [('lstat', '/home'), ('lstat', '/home/pixelbuild'),
                ('lstat', '/home/pixelbuild/grapheneos-2026100600/vendor/adevtool/.git'),
                ('lstat', '/home/pixelbuild/rose-actions/blazer-prep-41473133543-1/source/vendor/adevtool/.git'),
                ('stat', '/home/pixelbuild/rose-actions/blazer-prep-41473133543-1/source')], 'inspect-git is unchanged')

    def test_inspect_git_set_cli_takes_only_the_host_and_one_target_set(self):
        script = str(ROOT/'scripts/blazer-build-only.py')
        ok = subprocess.run([sys.executable, script, 'inspect-git-set', '--host', 'immortal-os-builder',
                             '--target-set', 'adevtool-projects-gitdir'], capture_output=True, text=True)
        self.assertEqual(ok.returncode, 0, ok.stderr)
        report = json.loads(ok.stdout)
        self.assertEqual((report['schema'], report['targetSet'], report['targets']['projectsGitdir']['absolutePath']),
                         ('roseos.blazer-git-target-set-inspection.v1', 'adevtool-projects-gitdir',
                          '/home/pixelbuild/grapheneos-2026100600/.repo/projects/vendor/adevtool.git'))
        base = [sys.executable, script, 'inspect-git-set', '--host', 'immortal-os-builder']
        for argv in ([*base], [*base, '--target-set', 'production'], [*base, '--target-set', 'adevtool-projects-gitdir', '/tmp/x'],
                     [*base, '--target-set', 'adevtool-projects-gitdir', '--lock', '/tmp/x'],
                     [*base, '--target-set', 'adevtool-projects-gitdir', '--max-entries', '5'],
                     [sys.executable, script, 'inspect-git-set', '--target-set', 'adevtool-projects-gitdir'],
                     [sys.executable, script, 'inspect-git', '--host', 'immortal-os-builder', '--target-set',
                      'adevtool-projects-gitdir']):
            with self.subTest(argv[2:]):
                bad = subprocess.run(argv, capture_output=True, text=True)
                self.assertEqual((bad.returncode, bad.stdout), (2, ''))

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
                '--lock', str(self.lock), '--candidate-root', cand, '--candidate-node-from-path', '--receipt-dir', str(self.tmp/f'r-{action}')],
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
            '--candidate-node-from-path', '--reference-source-root', str(self.src), '--max-entries', '1000'],
            capture_output=True, text=True)
        self.assertEqual(cli.returncode, 0, cli.stderr)
        report = json.loads(cli.stdout)
        self.assertEqual(report['schema'], 'roseos.blazer-preparation-inspection.v2')
        self.assertEqual(report['candidates']['receiptsDir']['path'], str(self.tmp/'rose-actions/blazer-prep-1-1/receipts'))
        self.assertEqual(report['node']['candidate']['how'], '--candidate-node-from-path')
        self.assertEqual(report['candidates']['ancestorBoundary'], '/home/pixelbuild')
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

    def test_the_checked_in_first_build_allowance_admits_exactly_reserve_plus_allowance(self):
        self.generated()
        receipts = self.integrated()
        need = self.recipe['capacity']['reserveBytes'] + self.recipe['capacity']['compileAllowanceBytes']
        for available, admitted in ((need, True), (need - 1, False)):
            report = bb.preflight(self.recipe, 'compile', self.op(identity=True, integration_dir=str(receipts)), statvfs=fs(available),
                                  lock_probe=lambda path: True)
            space = [p for p in report['problems'] if 'reserve' in p]
            self.assertEqual(not space, admitted, report['problems'])
            self.assertEqual((report['facts']['capacity']['boundBytes'], report['facts']['capacity']['boundKind']),
                             (250 << 30, 'first-build-proposal'))

class GitMetaTest(Fixture):
    """git-meta against local upstream repositories standing in for the pinned remotes (never a real fetch)."""
    ENV = {'PATH': '/usr/bin:/bin', 'GIT_CONFIG_NOSYSTEM': '1', 'GIT_AUTHOR_NAME': 't', 'GIT_AUTHOR_EMAIL': 't@t',
           'GIT_COMMITTER_NAME': 't', 'GIT_COMMITTER_EMAIL': 't@t'}

    def git(self, *args, cwd=None):
        return subprocess.run(['git', *args], cwd=cwd, env={**self.ENV, 'HOME': str(self.tmp)}, check=True,
                              capture_output=True, text=True).stdout.strip()

    def projects(self):
        """Upstream repos with two commits each (the pin is the second); the isolated tree holds the pinned bytes,
        one integrated (Rose-changed) file, and no .git."""
        up, rows = self.tmp/'up', []
        if os.path.lexists(self.src/'vendor/adevtool/.git'):  # the shared fixture's vendor-stage stand-in
            shutil.rmtree(self.src/'vendor/adevtool/.git')
        for path in bb.GIT_META_PATHS:
            name = 'platform_' + path.replace('/', '_')
            repo = up/name
            (repo/'res').mkdir(parents=True)
            (repo/'res/config.xml').write_text('old\n')
            self.git('init', '-q', str(repo))
            self.git('-C', str(repo), 'add', '-A')
            self.git('-C', str(repo), 'commit', '-qm', 'old')
            (repo/'res/config.xml').write_text('pinned\n')
            (repo/'keep.txt').write_text(path + '\n')
            self.git('-C', str(repo), 'add', '-A')
            self.git('-C', str(repo), 'commit', '-qm', 'pin')
            revision = self.git('-C', str(repo), 'rev-parse', 'HEAD')
            work = self.src/path
            (work/'res').mkdir(parents=True, exist_ok=True)
            (work/'res/config.xml').write_text('integrated by Rose\n' if path == 'frameworks/base' else 'pinned\n')
            (work/'keep.txt').write_text(path + '\n')
            rows.append({'path': path, 'name': name, 'revision': revision})
        spec = self.synthetic['gitMeta']
        spec.update(projects=rows, fetchUrlPrefix=f'file://{up}/')
        return rows

    def run_meta(self, out='r', recipe=None):
        code = bb.run(recipe or self.synthetic, 'git-meta', self.op(), self.tmp/out, now=lambda: 0, statvfs=fs(10 ** 15),
                      lock_probe=lambda path: True, root=self.root)
        done = self.tmp/out/'completed.json'
        return code, (json.loads(done.read_text()) if done.exists() else None), json.loads((self.tmp/out/'preflight.json').read_text())

    def worktree(self):
        return {str(p): p.read_bytes() for p in sorted(self.src.rglob('*')) if p.is_file() and '/.git/' not in str(p)}

    def test_each_project_gets_genuine_shallow_metadata_and_the_working_tree_is_untouched(self):
        rows = self.projects()
        before = self.worktree()
        # Neither inherited GIT_* nor a global insteadOf redirect reaches the helper's git.
        os.environ['GIT_DIR'] = str(self.tmp/'elsewhere/.git')
        self.addCleanup(os.environ.pop, 'GIT_DIR', None)
        (self.tmp/'.gitconfig').write_text(f'[url "file://{self.tmp}/nowhere/"]\n\tinsteadOf = file://{self.tmp}/up/\n')
        code, done, report = self.run_meta()
        self.assertEqual((report['problems'], code, done['ok']), ([], 0, True), done)
        self.assertEqual(self.worktree(), before, 'no command touches the working tree, Rose-integrated bytes included')
        self.assertEqual(done['workingTreeCommands'], [])
        for row in rows:
            work = self.src/row['path']
            self.assertEqual(self.git('-C', str(work), 'rev-parse', 'HEAD'), row['revision'])
            self.assertEqual((work/'.git/shallow').read_text(), row['revision'] + '\n')
            self.assertFalse((work/'.git/objects/info/alternates').exists())
            self.assertFalse((work/'.git/hooks').exists(), 'no template hooks')
            self.assertEqual(self.git('-C', str(work), 'show', 'HEAD:res/config.xml'), 'pinned')
            self.assertEqual(done['projects'][row['path']]['revision'], row['revision'])
        # host-reconcile's own sequence on the result: a shared clone, a sparse checkout of HEAD.
        base = self.src/'frameworks/base'
        shadow = self.tmp/'shadow/frameworks/base'
        self.git('clone', '--quiet', '--shared', '--no-checkout', str(base), str(shadow))
        self.git('-C', str(shadow), 'sparse-checkout', 'set', '--no-cone', 'res/config.xml')
        self.git('-C', str(shadow), 'checkout', '--quiet', '--detach', rows[2]['revision'])
        self.assertEqual((shadow/'res/config.xml').read_text(), 'pinned\n')
        self.assertEqual((base/'res/config.xml').read_text(), 'integrated by Rose\n')

    def test_an_existing_git_dir_or_the_production_tree_refuses_before_any_process(self):
        self.projects()
        (self.src/'build/make/.git').mkdir()
        code, done, report = self.run_meta()
        self.assertEqual((code, done), (1, None))
        self.assertIn('build/make/.git already exists', ' '.join(report['problems']))
        self.assertFalse((self.src/'frameworks/base/.git').exists())
        os.rmdir(self.src/'build/make/.git')
        code = bb.run(self.synthetic, 'git-meta', self.op(source_root=str(self.production)), self.tmp/'r2', now=lambda: 0,
                      statvfs=fs(10 ** 15), lock_probe=lambda path: True, root=self.root)
        self.assertEqual(code, 1)
        self.assertIn('production source root', ' '.join(json.loads((self.tmp/'r2/preflight.json').read_text())['problems']))

    def test_a_wrong_revision_removes_every_git_dir_this_run_created(self):
        rows = self.projects()
        before = self.worktree()
        self.synthetic['gitMeta']['projects'][3]['revision'] = rows[0]['revision']  # not in that repository
        code, done, _ = self.run_meta()
        self.assertEqual((code, done['ok'], done['failedProject']), (1, False, rows[3]['path']))
        self.assertIn('fetch failed', done['error'])
        self.assertEqual([r['removed'] for r in done['createdRemoved']], [True] * 4)
        for row in rows:
            self.assertFalse(os.path.lexists(self.src/row['path']/'.git'))
        self.assertEqual(self.worktree(), before)

    def test_the_byte_bound_and_a_non_shallow_or_foreign_head_refuse(self):
        rows = self.projects()
        tight = json.loads(json.dumps(self.synthetic))
        tight['gitMeta']['boundBytes'] = 1
        code, done, _ = self.run_meta(recipe=tight)
        self.assertEqual((code, done['ok'], done['failedProject']), (1, False, rows[0]['path']))
        self.assertIn('above the 1-byte bound', done['error'])
        self.assertFalse(os.path.lexists(self.src/rows[0]['path']/'.git'), 'its own .git is removed')
        real = bb.supervise
        def unshallow(argv, *a, **k):
            result = real(argv, *a, **k)
            if 'update-ref' in argv:
                (Path(argv[2])/'.git/shallow').unlink()
            return result
        bb.supervise = unshallow
        try:
            code, done, _ = self.run_meta(out='r2')
        finally:
            bb.supervise = real
        self.assertEqual(code, 1)
        self.assertIn('not shallow at exactly the pinned commit', done['error'])
        self.assertFalse(os.path.lexists(self.src/rows[0]['path']/'.git'))
        def foreign_head(argv, *a, **k):
            result = real(argv, *a, **k)
            if 'update-ref' in argv:  # HEAD moved off the pin after it was written; shallow still names the pin
                (Path(argv[2])/'.git/HEAD').write_text('ref: refs/heads/elsewhere\n')
            return result
        bb.supervise = foreign_head
        try:
            code, done, _ = self.run_meta(out='r3')
        finally:
            bb.supervise = real
        self.assertEqual(code, 1)
        self.assertIn('HEAD is not exactly the pinned revision', done['error'])

    def test_the_checked_in_recipe_pins_the_seven_manifest_projects(self):
        spec = self.recipe['gitMeta']
        self.assertEqual([r['path'] for r in spec['projects']], list(bb.GIT_META_PATHS))
        self.assertEqual({r['path']: r['revision'] for r in spec['projects']}['vendor/adevtool'], ADEVTOOL)
        self.assertEqual((spec['objectsSource'], spec['fetchUrlPrefix'], spec['boundBytes'], spec['projectTimeoutSeconds'],
                          spec['timeoutSeconds']), ('public-fetch', 'https://github.com/GrapheneOS/', 8 << 30, 1800, 5400))
        argv = bb.commands(self.recipe, self.op())['git-meta']['projects'][0]['argv']
        self.assertEqual(argv[1][3:], ['fetch', '--quiet', '--depth=1', '--no-tags',
                                       'https://github.com/GrapheneOS/platform_build', spec['projects'][0]['revision']])
        env = bb.git_meta_env(self.src, self.tmp/'h', spec['fetchUrlPrefix'])
        self.assertEqual((env['GIT_ALLOW_PROTOCOL'], env['GIT_CONFIG_GLOBAL'], env['GIT_TERMINAL_PROMPT'], env['PATH']),
                         ('https', os.devnull, '0', '/usr/bin:/bin'))
        self.assertFalse(any(k.startswith('GIT_DIR') for k in env))

class CompileRunTest(Fixture):
    @property
    def out(self):
        return self.src/f'out-blazer-build-{self.identity()["build_number"]}'

    def envsetup(self, make='mkdir -p "$OUT_DIR"; echo "$@" > "$OUT_DIR/goals"'):
        (self.src/'build').mkdir(exist_ok=True)
        (self.src/'build/envsetup.sh').write_text(f"""lunch() {{ export TARGET_PRODUCT=blazer; }}
m() {{ env | grep '^GIT_' | sort > {str(self.tmp/'compile-git-env')!r}; echo "$PATH" > {str(self.tmp/'compile-path')!r}; {make}; }}
""")

    def compile(self, recipe=None, statvfs=None, out='r', **change):
        if not (self.src/'vendor/google_devices/blazer').exists():
            self.generated()
        if not hasattr(self, 'receipts'):
            self.receipts = self.integrated()
        receipts = self.receipts
        code = bb.run(recipe or self.synthetic, 'compile', self.op(identity=True, integration_dir=str(receipts), **change), self.tmp/out,
                      now=lambda: 0, statvfs=statvfs or fs(10 ** 15), lock_probe=lambda path: True, root=self.root)
        done = self.tmp/out/'completed.json'
        return code, (json.loads(done.read_text()) if done.exists() else None), json.loads((self.tmp/out/'preflight.json').read_text())

    OUTPUTS = ('p=$OUT_DIR/target/product/blazer; h=$OUT_DIR/host/linux-x86/obj/ETC/otatools-packagelinux_glibc_x86_64_intermediates; '
               'mkdir -p $p/obj/PACKAGING/target_files_intermediates $h; printf VNDRBOOT > $p/vendor_boot.img; '
               'printf VNDRBOOT > $p/vendor_kernel_boot.img; '
               'python3 -c "import zipfile,sys; z=zipfile.ZipFile(sys.argv[1],\'w\'); [z.writestr(n,\'x\') for n in sys.argv[2:]]" '
               '$h/otatools-packagelinux_glibc_x86_64 bin/x releasetools/y; '
               'python3 -c "import zipfile,sys; z=zipfile.ZipFile(sys.argv[1],\'w\'); [z.writestr(n,\'x\') for n in sys.argv[2:]]" '
               '$p/obj/PACKAGING/target_files_intermediates/blazer-target_files.zip META/misc_info.txt SYSTEM/build.prop')

    def test_compile_collects_signing_inputs_and_refuses_missing_outputs(self):
        self.envsetup(self.OUTPUTS)
        code, done, _ = self.compile()
        self.assertEqual((code, done['ok'], done['outputs']['verdict'], done['outputs']['signed']), (0, True, 'complete', False), done)
        arts = done['outputs']['artifacts']
        tf = self.out/'target/product/blazer/obj/PACKAGING/target_files_intermediates/blazer-target_files.zip'
        self.assertEqual((arts['targetFiles']['path'], arts['targetFiles']['sha256']),
                         (str(tf), hashlib.sha256(tf.read_bytes()).hexdigest()))
        self.assertEqual({k: a['state'] for k, a in arts.items()}, dict.fromkeys(
            ('vendorBootImage', 'vendorKernelBootImage', 'otatools', 'targetFiles'), 'collected'))
        self.assertEqual(arts['otatools']['format'], 'zip')
        tf.unlink()
        shutil.rmtree(self.out/'target/product/blazer/obj')
        self.out.rename(self.tmp/'kept-out')
        self.envsetup(self.OUTPUTS + '; rm $p/obj/PACKAGING/target_files_intermediates/blazer-target_files.zip')
        code, done, _ = self.compile(out='r2')
        self.assertEqual((code, done['ok'], done['outputs']['artifacts']['targetFiles']['state']), (1, False, 'missing'))
        self.assertIn('expected outputs are not all collected', done['error'])

    def test_compile_runs_isolated_with_its_goals_and_records_the_honest_measure(self):
        self.envsetup(self.OUTPUTS + '; echo "$@" > "$OUT_DIR/goals"')
        self.generated()
        self.receipts = self.integrated()
        # An inherited GIT_* (an inherited GIT_DIR would also redirect the integration check's own lookup of this
        # checkout, which then refuses) never reaches the build.
        os.environ['GIT_INHERITED_BY_THE_HELPER'] = '1'
        self.addCleanup(os.environ.pop, 'GIT_INHERITED_BY_THE_HELPER', None)
        code, done, report = self.compile()
        self.assertEqual(report['problems'], [])
        self.assertEqual((code, done['ok'], done['stopped'], done['signed'], done['releaseReady']), (0, True, None, False, False), done)
        self.assertEqual((self.out/'goals').read_text(),
                         '-j16 vendorbootimage vendorkernelbootimage target-files-package otatools-package\n')
        self.assertEqual((self.tmp/'compile-git-env').read_text(), f'GIT_CEILING_DIRECTORIES={self.src}\n')
        self.assertEqual((self.tmp/'compile-path').read_text(), '/usr/bin:/bin\n')
        self.assertEqual((done['boundBytes'], done['boundKind'], done['measure'], done['enforcement'], done['outDir']),
                         (10, 'first-build-proposal', 'filesystem-available-drop', 'sampled-stop', self.out.name))
        self.assertEqual((done['startAvailableBytes'], done['consumedBytes']), (10 ** 15, 0))

    def test_the_allowance_reserve_and_timeout_stop_the_build_and_keep_outputs(self):
        self.envsetup('mkdir -p "$OUT_DIR"; touch "$OUT_DIR/partial"; sleep 30')
        calls = []
        def shrinking(path):
            calls.append(path)
            return types.SimpleNamespace(f_bavail=10 ** 15 - 100 * (len(calls) - 1), f_frsize=1, f_blocks=2 * 10 ** 15)
        started = time.monotonic()
        code, done, _ = self.compile(statvfs=shrinking)
        self.assertLess(time.monotonic() - started, 20, 'the sampled stop ends the group long before the child would')
        self.assertEqual((code, done['ok']), (1, False))
        self.assertIn('above the 10-byte bound', done['stopped'])
        self.assertIn('outputs are kept, not deleted', done['error'])
        self.assertTrue((self.out/'partial').exists())
        # Admission needs reserve + allowance free, so a drop below the reserve normally trips the allowance first; the
        # reserve stop matters when another writer took space between preflight and start.
        roomy = json.loads(json.dumps(self.synthetic))
        roomy['capacity']['compileAllowanceBytes'] = 10 ** 6
        reserve = roomy['capacity']['reserveBytes']
        falling = iter([reserve + 10 ** 7, reserve + 5] + [reserve - 1] * 10 ** 6)
        shutil.rmtree(self.out)
        code, done, _ = self.compile(recipe=roomy, out='r2', statvfs=lambda path: types.SimpleNamespace(
            f_bavail=next(falling), f_frsize=1, f_blocks=2 * 10 ** 15))
        self.assertEqual(code, 1)
        self.assertIn('fell below the reserve', done['stopped'])
        self.assertEqual(done['startAvailableBytes'], reserve + 5)
        brief = json.loads(json.dumps(self.synthetic))
        brief['compile']['timeoutSeconds'] = 1
        shutil.rmtree(self.out)
        code, done, _ = self.compile(recipe=brief, out='r3')
        self.assertEqual((code, done['stopped']), (1, 'exceeded 1 s'))

    def test_compile_never_runs_in_the_production_tree_or_on_a_full_filesystem(self):
        self.envsetup()
        code, done, report = self.compile(source_root=str(self.production))
        self.assertEqual((code, done), (1, None), 'refused before any process starts')
        self.assertIn('production source root', ' '.join(report['problems']))
        self.assertFalse((self.production/self.out.name).exists())
        tight = self.synthetic['capacity']['reserveBytes'] + self.synthetic['capacity']['compileAllowanceBytes'] - 1
        code, done, report = self.compile(statvfs=fs(tight), out='r2')
        self.assertEqual((code, done), (1, None))
        self.assertIn('below the reserve', ' '.join(report['problems']))
        self.assertFalse(self.out.exists())

    def test_the_build_identity_is_bound_to_the_admitted_inputs(self):
        self.envsetup()
        self.generated()
        self.receipts = self.integrated()
        bound = self.identity()
        self.assertEqual(self.preflight('compile', integration_dir=str(self.receipts))['problems'], [])
        report = self.preflight('compile', integration_dir=str(self.receipts))
        reservation = self.tmp/'blazer-allocations'/f'{bound["build_number"]}.json'
        self.assertEqual(report['facts']['buildIdentity'], {'buildNumber': bound['build_number'], 'buildDatetime': bound['build_datetime'],
            'inputsReceiptBuild': bound['build_number'], 'reservation': str(reservation), 'reservedAtEpoch': int(bound['build_datetime']),
            'buildDatetimeUtc': f'{bound["build_number"][:4]}-{bound["build_number"][4:6]}-{bound["build_number"][6:8]}T12:00:00Z'})
        self.assertEqual(report['buildIdentity'], {'buildNumber': bound['build_number'], 'buildDatetime': bound['build_datetime']})
        # T is bound to the build's own reservation, not merely accepted by range: a plausible other second refuses.
        self.assertIn(f'is not the reservation reservedAt of build {bound["build_number"]}', ' '.join(self.preflight(
            'compile', integration_dir=str(self.receipts), build_datetime=str(int(bound['build_datetime']) + 1))['problems']))
        original = reservation.read_text()
        for change, pattern in (
                (dict(integrationDir='/elsewhere/blazer-reconcile-x'), 'names integration /elsewhere'),
                (dict(build='2026103199'), 'is not exactly the roseos.blazer-allocation.v1 reservation'),
                (dict(schema='other'), 'is not exactly the roseos.blazer-allocation.v1 reservation'),
                (dict(extra=1), 'is not exactly the roseos.blazer-allocation.v1 reservation'),
                (dict(reservedAt='2026-10-31 12:00'), 'has no UTC reservedAt')):
            with self.subTest(pattern):
                self.reserve(self.receipts, **change)
                self.assertIn(pattern, ' '.join(self.preflight('compile', integration_dir=str(self.receipts))['problems']))
        reservation.unlink()
        self.assertIn('no readable reservation', ' '.join(self.preflight('compile', integration_dir=str(self.receipts))['problems']))
        (self.tmp/'elsewhere.json').write_text(original)
        os.symlink(self.tmp/'elsewhere.json', reservation)
        self.assertIn('no readable reservation', ' '.join(self.preflight('compile', integration_dir=str(self.receipts))['problems']),
                      'a linked reservation is never followed')
        reservation.unlink()
        reservation.write_text(original)
        day = int(bound['build_datetime']) - 12 * 3600
        other = str(int(bound['build_number']) + 1)
        for change, pattern in (
                (dict(build_number=other), f'--build-number {other} is not the admitted inputs build {bound["build_number"]}'),
                (dict(build_datetime=str(day - 1)), 'is before build'),
                (dict(build_datetime=str(day)), 'is not the reservation reservedAt'),
                (dict(build_datetime=str(int(time.time()) + 3600)), 'is in the future'),
                (dict(build_number=None), 'compile needs --build-number'),
                (dict(build_datetime=None), 'compile needs --build-datetime')):
            with self.subTest(pattern):
                self.assertIn(pattern, ' '.join(self.preflight('compile', integration_dir=str(self.receipts), **change)['problems']))
        for value in ('202611031', '0026110301', '2026110301x', ''):
            with self.subTest(value), self.assertRaisesRegex(bb.InvalidInput, 'must be a ten-digit YYYYMMDDNN'):
                self.op(build_number=value)
        for value in ('0', '01793718000', '1793718000.5', '-1', '1234567890123'):
            with self.subTest(value), self.assertRaisesRegex(bb.InvalidInput, r'must be epoch seconds \(1-12 digits\)'):
                self.op(build_datetime=value)
        self.assertEqual(self.op(build_datetime='1793718000')['buildDatetime'], '1793718000')
        self.assertIn('only compile takes --build-number and --build-datetime',
                      ' '.join(bb.preflight(self.synthetic, 'lunch', self.op(identity=True), statvfs=fs(10 ** 15),
                                            lock_probe=lambda path: True, root=self.root)['problems']))
        code, done, _ = self.compile(build_number=other)
        self.assertEqual((code, done), (1, None), 'a mismatched identity starts nothing')

    def test_the_runner_identity_never_reaches_the_build(self):
        self.envsetup('env | grep -E "^BUILD_(NUMBER|DATETIME)" | sort > ' + str(self.tmp/'identity-env') + '; ' + self.OUTPUTS)
        for name, value in (('BUILD_NUMBER', '1111111111'), ('BUILD_DATETIME', '1'), ('BUILD_DATETIME_FILE', '/tmp/runner-date')):
            os.environ[name] = value
            self.addCleanup(os.environ.pop, name, None)
        code, done, _ = self.compile()
        bound = self.identity()
        self.assertEqual((code, done['ok']), (0, True), done)
        self.assertEqual((self.tmp/'identity-env').read_text(),
                         f'BUILD_DATETIME={bound["build_datetime"]}\nBUILD_NUMBER={bound["build_number"]}\n')
        started = json.loads((self.tmp/'r/started.json').read_text())
        self.assertEqual((started['command']['env']['BUILD_NUMBER'], started['command']['env']['BUILD_DATETIME'],
                          started['command']['env']['OUT_DIR']), (bound['build_number'], bound['build_datetime'], self.out.name))
        pair = {'buildNumber': bound['build_number'], 'buildDatetime': bound['build_datetime']}
        self.assertEqual((done['buildIdentity'], started['buildIdentity'], done['outDir']), (pair, pair, self.out.name))
        self.assertEqual(json.loads((self.tmp/'r/preflight.json').read_text())['buildIdentity'], pair)
        self.assertTrue(done['outputs']['outDir'].endswith(self.out.name))

    def test_each_build_writes_a_fresh_out_dir_and_earlier_outputs_are_preserved(self):
        legacy = self.src/'out-blazer-build'
        (legacy/'target').mkdir(parents=True)
        (legacy/'target/kept.img').write_bytes(b'an earlier build')
        before = self.snapshot(legacy)
        self.envsetup(self.OUTPUTS)
        code, done, _ = self.compile()
        self.assertEqual((code, done['ok'], done['outDir']), (0, True, self.out.name), done)
        self.assertEqual(self.snapshot(legacy), before, 'an earlier OUT_DIR is never read, moved or written')
        code, done, report = self.compile(out='r2')
        self.assertEqual((code, done), (1, None))
        self.assertIn(f'{self.out.name} already exists; refusing to reuse or overwrite an OUT_DIR', ' '.join(report['problems']))
        self.assertEqual(self.snapshot(legacy), before)

if __name__ == '__main__':
    unittest.main()
