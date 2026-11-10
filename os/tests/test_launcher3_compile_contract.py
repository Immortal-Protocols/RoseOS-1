"""#410: os/compile-contracts/launcher3-quickstep-lib.json, OS Product's input contract for Release's compile-only
Launcher3QuickStepLib adapter (split A). Inert: nothing here registers, applies or copies anything into a build. The
candidate (#440, draft) is bound by its reviewed commit and the digests of its inert bundle; every image is recomputed
offline from committed pinned fixtures. Source checks only: no compile, device or host."""
from pathlib import Path
import hashlib
import json
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
CONTRACT_FIXTURES = ROOT/'os/tests/fixtures/launcher3-compile-contract'
# #440 retired (2026-11-07 re-pin): its fixture bundle is gone; the edge patches are the live, registered os/patches.
BUNDLE = CONTRACT_FIXTURES/'candidate-pr440'
SWITCHER_PATCH = ROOT/'os/patches/rose-switcher-launcher3.patch'
EDGE_PATCH = ROOT/'os/patches/rose-edge-launcher3.patch'
RESIZE_PATCH = ROOT/'os/patches/rose-edge-resize-launcher3.patch'
LIVE_PATCHER = ('apply-os-changes.py', 'background_apks.py', 'integration_copy.py')
QUICKSTEP = 'quickstep/src/com/android/quickstep'
EDGE_TARGETS = {'AbsSwipeUpHandler.java': f'{QUICKSTEP}/AbsSwipeUpHandler.java',
    'FallbackSwipeHandler.java': f'{QUICKSTEP}/FallbackSwipeHandler.java',
    'RecentsWindowSwipeHandler.java': f'{QUICKSTEP}/window/RecentsWindowSwipeHandler.java'}
# E2c's wiring (rose-edge-resize-launcher3.patch), which no other Rose patch touches.
RESIZE_TARGETS = {'SystemUiProxy.kt': f'{QUICKSTEP}/SystemUiProxy.kt',
    'TouchInteractionService.kt': f'{QUICKSTEP}/TouchInteractionService.kt'}
CANDIDATE_COMMIT = '2f57a5f28e3c3cc97177bf3f9294ea807d514469'


class Launcher3CompileContractTest(unittest.TestCase):
    """#410: os/compile-contracts/launcher3-quickstep-lib.json, the inputs for Release's compile-only adapter
    (verify-integrated, reconstruct, or the proposed verify-base-apply-candidate; anything else refused). Every pre-image, post-image and blob chain is recomputed
    offline from committed pinned fixtures and patches; config.xml is accepted only with its bound generating inputs."""
    SCRIPT = ROOT/'scripts/launcher3-compile-contract.py'
    MANIFEST = ROOT/'os/compile-contracts/launcher3-quickstep-lib.json'
    # The agreed version (#410). Changing the manifest changes this, and the change needs re-agreement with Release.
    AGREED_SHA256 = '6d36629819b7c560b99506b01944d10c22d363b3db6870710ea98be26565c787'

    def contract(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location('launcher3_compile_contract', self.SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def manifest(self):
        import json
        return json.loads(self.MANIFEST.read_text())

    def test_the_manifest_is_the_agreed_version_and_recomputes_offline(self):
        self.assertEqual(hashlib.sha256(self.MANIFEST.read_bytes()).hexdigest(), self.AGREED_SHA256)
        manifest = self.manifest()
        self.assertEqual((manifest['kind'], manifest['schemaVersion']), ('rose-launcher3-compile-inputs', 3))
        result = subprocess.run(['python3', str(self.SCRIPT)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('51 files recomputed from pinned fixtures', result.stdout)

    def test_the_patch_order_is_the_live_patchers_own_registration(self):
        """Re-pinned model: the pinned patcher copy is byte-identical to the live patcher, and every contract project's
        patch list is exactly what it registers for frankel's flags (edge, WMShell edge and shade included)."""
        contract = self.contract()
        for name in LIVE_PATCHER:
            self.assertEqual((ROOT/'os/tests/fixtures/launcher3-compile-contract/patcher'/name).read_bytes(),
                (ROOT/'scripts'/name).read_bytes(), f'{name}: the pinned copy is the live patcher')
        registered = {}
        for project, name in contract.registered_order(contract.frankel_apply_args()):
            registered.setdefault(project, []).append(name)
        order = dict((p, patches) for p, _, patches in contract.PROJECTS)
        for project, patches in order.items():
            self.assertEqual(patches, registered[project], f'{project}: exactly the patcher\'s registration')
        self.assertEqual(order['packages/apps/Launcher3'], ['rose-switcher-launcher3.patch', 'rose-edge-launcher3.patch',
            'rose-edge-resize-launcher3.patch'])
        self.assertEqual(order['frameworks/base'][-2:], ['rose-edge-wmshell.patch', 'rose-shade-systemui.patch'])
        self.assertEqual(contract.CANDIDATE_PATCHES, [], '#440 is retired: nothing outside the registration')

    def test_the_registration_is_the_patchers_own_execution_for_frankels_flags(self):
        contract = self.contract()
        manifest = self.manifest()
        registration = manifest['base']['registration']
        self.assertEqual(registration, contract.registration())
        recipe = json.loads((ROOT/'os/release-pipeline/frankel.json').read_text())
        self.assertEqual(registration['applyArgs'], recipe['host']['integrate']['reconcile']['applyArgs'])
        self.assertEqual(registration['patcher'], {'patcher': 'trusted', 'sha256': {name: hashlib.sha256(
            (ROOT/'os/tests/fixtures/launcher3-compile-contract/patcher'/name).read_bytes()).hexdigest() for name in ('apply-os-changes.py', 'background_apks.py',
            'integration_copy.py')}})
        def per_project(rows):
            out = {}
            for row in rows:
                out.setdefault(row['project'], []).append(row['name'])
            return out
        self.assertEqual(per_project(registration['order']), per_project(manifest['base']['bundleContent']['patches']),
            'the executed registration is exactly the base bundle\'s patches, in each project\'s order')
        # Really executed: other flags register other patches.
        self.assertEqual(contract.registered_order(['--updater-url', 'https://example.invalid/']),
            [('frameworks/base', 'frameworks-base.patch'), ('frameworks/base', 'rose-agent-self-update-frameworks-base.patch'),
             ('packages/apps/Updater', 'updater-rose-channels.patch'), ('build/make', 'updater-product-build-make.patch')])
        import sys
        sys.path.insert(0, str(ROOT/'scripts'))
        try:
            import background_apks
        finally:
            sys.path.pop(0)
        home = background_apks.rose_home_projects([{'packageName': background_apks.ROSE_HOME_PACKAGE}])
        self.assertEqual(set(home), {'packages/modules/Permission'}, 'rose_home_projects never touches these projects')
        # A patcher that is not the pinned one refuses instead of deriving.
        contract.PATCHER_SHA256 = dict(contract.PATCHER_SHA256, **{'integration_copy.py': '0' * 64})
        with self.assertRaises(ValueError):
            contract.registration()
        self.assertIn('only contract-derived input', registration['contractDerived'])

    def test_the_bundle_report_is_its_own_schema_never_bundle_content(self):
        report = self.manifest()['base']['bundleReport']
        self.assertEqual((report['kind'], report['schemaVersion']), ('rose-integration-bundle-report', 1))
        self.assertEqual(list(report['fields']), ['producer', 'run', 'receipt', 'candidate', 'registration', 'observed'])
        self.assertEqual(report['fields']['run'], ['runId', 'runAttempt', 'headSha', 'label', 'createdAt', 'updatedAt',
            'bindingSha256'])
        self.assertIn('never the adapter, the observer or this checker', report['producer'])
        rules = ' '.join(report['rules'])
        for rule in ('unclassified must be empty', 'tampering bundleContent leaves it unchanged', 'a bare bundleContent, is '
                'refused', 'association only, never observed', 'never stands in for base-exact'):
            self.assertIn(rule, rules)
        self.assertNotEqual(set(report), set(self.manifest()['base']['bundleContent']))

    def test_each_branch_states_what_it_requires_and_forbids(self):
        branches = self.manifest()['branches']
        self.assertEqual(branches['requires'], {'verifyIntegrated': ['sourceTree', 'lunch', 'policyReceipt'],
            'reconstruct': ['sourceTree', 'lunch', 'reconstructSource'],
            'verifyBaseApplyCandidate': ['sourceTree', 'lunch', 'policyReceipt', 'bundleReport']})
        self.assertIn('bundleReport', branches['forbids']['verifyIntegrated'])
        self.assertIn('Git metadata or objects of the three contract projects in the checked tree (the lunch inputs\' '
            'admitted gitRequirement.publicFetch of vendor/adevtool/.git, outside them, excepted)',
            branches['forbids']['verifyIntegrated'])
        definitions = branches['definitions']
        self.assertEqual(set(definitions), {'sourceTree', 'lunch', 'policyReceipt', 'reconstructSource', 'bundleReport'})
        self.assertIn('reconstructed from config.xml', definitions['policyReceipt'])
        self.assertIn('none is approved', definitions['reconstructSource'])
        self.assertIn('every unlisted path matches its treePin', branches['reconstruct'])
        manifest = self.manifest()
        self.assertIn('verifyBaseApplyCandidate\'s base-exact and final-exact', ' '.join(manifest['notInputs']))
        self.assertEqual(len(manifest['unaffected']), 3)
        self.assertEqual(manifest['flagEvidence']['role'], 'output: never a precondition of any branch')

    def test_the_lunch_inputs_are_referenced_by_path_and_sha256(self):
        manifest = self.manifest()
        lunch = ROOT/'os/compile-contracts/frankel-lunch-inputs.json'
        self.assertEqual(manifest['lunchInputs'], {'path': 'os/compile-contracts/frankel-lunch-inputs.json',
            'sha256': hashlib.sha256(lunch.read_bytes()).hexdigest()})
        import copy
        contract = self.contract()
        for change in (lambda m: m['lunchInputs'].update(sha256='0' * 64),
                lambda m: m['notInputs'].append('anything'),
                lambda m: m['branches']['requires']['verifyIntegrated'].pop(),
                lambda m: m['base']['registration']['order'].pop(),
                lambda m: m['base']['bundleReport']['rules'].pop(),
                lambda m: m.__setitem__('schemaVersion', 2)):
            changed = copy.deepcopy(manifest)
            change(changed)
            self.assertTrue(contract.check(changed), 'a changed schema-3 field must be refused')

    def test_the_retired_candidate_is_empty_and_keeps_its_provenance(self):
        manifest = self.manifest()
        delta = manifest['candidateDelta']
        self.assertEqual((delta['pr'], delta['commit']), (440, CANDIDATE_COMMIT), 'provenance kept')
        self.assertEqual((delta['patches'], delta['files'], delta['overlays'], delta['bundle']), ([], [], [], []))
        self.assertFalse(BUNDLE.exists(), 'no inert candidate copies remain to drift from os/')

    def test_base_plus_the_delta_is_exactly_the_candidate(self):
        """Re-pinned model: base is the live patcher's whole output, the delta is empty, so base equals final in every
        project, file and overlay (and verifyBaseApplyCandidate degenerates to base-exact plus final-exact)."""
        manifest = self.manifest()
        base, delta = manifest['base'], manifest['candidateDelta']
        self.assertEqual(delta['files'], [])
        for b, f in zip(base['projects'], manifest['projects']):
            self.assertEqual([p['name'] for p in b['patches']], [p['name'] for p in f['patches']], b['path'])
            self.assertEqual([(r['path'], r['post']) for r in b['files']], [(r['path'], r['post']) for r in f['files']],
                f'{b["path"]}: the base is already final')
        self.assertEqual({o['target']: o.get('sha256') for o in base['overlays']},
            {o['target']: o.get('sha256') for o in manifest['overlays']})
        for o in manifest['overlays'] + base['overlays']:
            self.assertEqual('sha256' in o, o['role'] == 'required', f'{o["target"]}: hashed exactly when required')
        result = subprocess.run(['python3', str(self.SCRIPT)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_a_tampered_delta_or_base_is_refused(self):
        import copy
        contract = self.contract()
        manifest = self.manifest()
        for edit in (lambda m: m['candidateDelta'].__setitem__('commit', '0' * 40),
                lambda m: m['candidateDelta']['patches'].append({'name': 'x.patch', 'sha256': '1' * 64}),
                lambda m: m['base']['projects'][1]['files'][0]['post'].__setitem__('sha256', '3' * 64),
                lambda m: m['base']['projects'][1].__setitem__('patches', []),
                lambda m: m['base']['bundleContent']['patches'].pop(),
                lambda m: m['base']['projects'][0]['treePin']['unlisted'].__setitem__('digest', '4' * 64)):
            changed = copy.deepcopy(manifest)
            edit(changed)
            self.assertTrue(contract.check_candidate(changed), 'a tampered base or delta must be refused')
            self.assertTrue(contract.check(changed), 'and the whole check refuses it too')
        self.assertEqual(contract.check_candidate(manifest), [])
        self.assertEqual(contract.check(manifest), [])

    def test_the_committed_os_is_the_pinned_base(self):
        """With the delta empty the committed os/ is the base itself: the check passes on it and refuses when a
        registered patch the base pins is changed or removed."""
        manifest = self.manifest()
        work = Path(tempfile.mkdtemp(prefix='contract-os-'))
        self.addCleanup(shutil.rmtree, work, True)
        shutil.copytree(ROOT/'os/overlays', work/'os/overlays')
        shutil.copytree(ROOT/'os/patches', work/'os/patches')
        def run():
            contract = self.contract()
            contract.OS = work/'os'
            return contract.check(manifest)
        self.assertEqual(run(), [])
        edge = work/'os/patches/rose-edge-launcher3.patch'
        edge.write_bytes(edge.read_bytes() + b'\n')
        self.assertTrue(run(), 'a changed registered patch refuses')

    def test_no_base_overlay_copy_is_needed_without_a_delta(self):
        self.assertEqual(self.manifest()['candidateDelta']['overlays'], [])

    def test_every_project_is_pinned_by_its_own_tree_and_base_is_bound_by_content(self):
        import gzip
        contract = self.contract()
        manifest = self.manifest()
        for project in manifest['projects'] + manifest['base']['projects']:
            pin = project['treePin']
            listing = contract.read_listing(project['path'])
            self.assertEqual(contract.git_tree_hash(listing), pin['rootTree'], f'{project["path"]}: the listing is the '
                'pinned commit\'s own tree')
            raw = gzip.decompress((ROOT/pin['listing']['path']).read_bytes())
            self.assertEqual(hashlib.sha256(raw).hexdigest(), pin['listing']['sha256'])
            entries = {path: (mode, blob) for mode, _, blob, path in listing}
            listed = {r['path'] for r in project['files']} | {o['target'][len(project['path']) + 1:]
                for o in manifest['overlays'] if o['target'].startswith(project['path'] + '/')}
            lines = sorted((path.encode(), f'{mode} {blob}\t{path}\n'.encode()) for path, (mode, blob) in entries.items()
                if path not in listed)
            self.assertEqual(hashlib.sha256(b''.join(l for _, l in lines)).hexdigest(), pin['unlisted']['digest'])
            self.assertEqual(len(lines), pin['unlisted']['count'])
            for row in project['files']:
                self.assertEqual(entries.get(row['path'], (None, None))[1], row['pre'] and row['pre']['gitBlob'],
                    f'{row["path"]}: the pre-image is the pinned tree\'s own blob, a new file is absent')
            self.assertIn('per-path sha256 would need every file\'s contents', pin['limitation'])
        content = manifest['base']['bundleContent']
        self.assertEqual([(p['project'], p['name']) for p in content['patches']],
            [(p['path'], x['name']) for p in manifest['base']['projects'] for x in p['patches']])
        self.assertIn('rose-edge-launcher3.patch', [p['name'] for p in content['patches']], 'registered: in base')
        self.assertEqual({o['target'] for o in content['overlays']},
            {o['target'] for o in manifest['base']['overlays'] if o['role'] == 'required'})
        self.assertEqual(set(content['notAnInputOverlays']),
            {o['target'] for o in manifest['base']['overlays'] if o['role'] == 'notAnInput'})
        self.assertNotIn('commit', ' '.join(manifest['base']['match'].split()[:3]), 'bound by content, not a commit')

    HASH_OBJECT = ['git', 'hash-object', '-t', 'blob', '--no-filters', '--stdin']

    def git_isolated(self, argv, data=b'', cwd=None):
        """Run git without the caller's environment, config or HOME; with no cwd, no repository is in reach."""
        import os
        with tempfile.TemporaryDirectory() as empty:
            env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
            env.update(GIT_CEILING_DIRECTORIES=str(Path(empty).parent), GIT_CONFIG_NOSYSTEM='1', HOME=empty)
            result = subprocess.run(argv, input=data, capture_output=True, cwd=cwd or empty, env=env)
            self.assertEqual(result.returncode, 0, result.stderr.decode())
            return result.stdout.decode()

    def test_the_tree_pins_state_their_object_format_serialization_and_exact_exclusions(self):
        """M1, M2, M3, M5: sha1 object format; records <mode> SP <oid> TAB <path> LF sorted by raw path bytes; listing
        bound by both its decompressed and its compressed sha256; exclusions an exact per-project path list; no gitlink;
        the commit and root tree are pinned values reconstructed offline, never host facts."""
        import gzip
        contract = self.contract()
        manifest = self.manifest()
        revisions = contract.pins()
        for project in manifest['projects'] + manifest['base']['projects']:
            path, pin = project['path'], project['treePin']
            self.assertEqual(pin['objectFormat'], 'sha1')
            self.assertEqual(pin['revision'], project['revision'])
            self.assertEqual(pin['revision'], revisions[path], f'{path}: the commit is the pinned manifest\'s')
            self.assertIn('neither is a host fact', pin['source'])
            compressed = (ROOT/pin['listing']['path']).read_bytes()
            self.assertEqual(hashlib.sha256(compressed).hexdigest(), pin['listing']['compressedSha256'])
            self.assertEqual(gzip.decompress(compressed)[-1:], b'\n')
            listing = contract.read_listing(path)
            self.assertEqual(len(listing), pin['listing']['paths'])
            self.assertEqual(pin['modes'], sorted({mode for mode, _, _, _ in listing}))
            self.assertNotIn('160000', pin['modes'])
            self.assertEqual({kind for _, kind, _, _ in listing}, {'blob'})
            listed = sorted({r['path'] for r in project['files']} | {o['target'][len(path) + 1:]
                for o in manifest['overlays'] + manifest['base']['overlays'] if o['target'].startswith(path + '/')})
            self.assertEqual(pin['unlisted']['excludedPaths'], listed, f'{path}: exactly the listed paths')
            for excluded in listed:
                self.assertFalse(set('*?[') & set(excluded), f'{excluded}: an exact path, not a glob')
            entries = {p: (mode, blob) for mode, _, blob, p in listing}
            records = [f'{entries[p][0]} {entries[p][1]}\t{p}\n'.encode()
                for p in sorted((p for p in entries if p not in listed), key=str.encode)]
            self.assertEqual(hashlib.sha256(b''.join(records)).hexdigest(), pin['unlisted']['digest'])
            self.assertEqual(len(records), pin['unlisted']['count'])
            self.assertEqual(pin['unlisted']['count'], len(entries) - len(set(listed) & set(entries)))
            self.assertEqual(pin['gitlinks'], 'none: generation refuses any mode 160000 entry')

    def test_a_gitlink_or_quoted_path_is_refused_at_generation_and_check(self):
        """M3: generation reads NUL-terminated ls-tree records and refuses a gitlink or any path a text listing would
        quote; the checker reports such an entry, or a non-blob one, in a fixture instead of hashing it."""
        import gzip
        contract = self.contract()
        listing = b'100644 blob ' + b'1' * 40 + b'\tREADME\x00' + b'160000 commit ' + b'2' * 40 + b'\tsub\x00'
        calls = []

        class Fake:
            def run(self, argv, **k):
                calls.append(argv)
                return subprocess.CompletedProcess(argv, 0)

            def check_output(self, argv, **k):
                calls.append(argv)
                return ('3' * 40 + '\n') if 'rev-parse' in argv else listing
        real = contract.subprocess
        contract.subprocess = Fake()
        try:
            with self.assertRaises(SystemExit) as refused:
                contract.fetch_tree('platform_build_release', '0' * 40)
        finally:
            contract.subprocess = real
        self.assertIn('gitlink', str(refused.exception))
        ls_tree = [argv for argv in calls if 'ls-tree' in argv]
        self.assertEqual(len(ls_tree), 1)
        self.assertEqual(ls_tree[0][-5:], ['ls-tree', '-r', '-z', '--full-tree', '0' * 40])
        plain = b'100644 blob ' + b'1' * 40 + b'\tdir/README\0' + b'120000 blob ' + b'5' * 40 + b'\tlink\0'
        self.assertEqual(contract.text_listing(plain, 'x'), plain.replace(b'\0', b'\n'))
        for path in ('é.txt'.encode(), b'a"b', b'a\\b', b'a\nb', b'a\tb', b''):
            with self.assertRaises(SystemExit, msg=path):
                contract.text_listing(b'100644 blob ' + b'1' * 40 + b'\t' + path + b'\0', 'x')
        with self.assertRaises(SystemExit):
            contract.text_listing(plain[:-1], 'x')
        project = self.manifest()['projects'][2]
        pinned = gzip.decompress((ROOT/project['treePin']['listing']['path']).read_bytes())
        with tempfile.TemporaryDirectory() as scratch:
            fixture = Path(scratch)/contract.tree_fixture(project['path']).name
            contract.TREES = Path(scratch)
            for extra, error in ((b'160000 commit ' + b'2' * 40 + b'\tsub\n',
                    'build/release/sub: a gitlink (mode 160000), which the pin does not support'),
                    (b'040000 tree ' + b'3' * 40 + b'\tdir\n',
                    'build/release/dir: a tree entry, which a recursive blob listing never has'),
                    (b'100644 blob ' + b'4' * 40 + b'\t"\\303\\251.txt"\n',
                    'build/release/"\\303\\251.txt": a quoted path, which the pin does not support')):
                fixture.write_bytes(gzip.compress(pinned + extra, 9, mtime=0))
                self.assertEqual(contract.check_tree_pin(project), [error])

    def test_blob_ids_come_from_the_exact_hash_object_invocation(self):
        """M1: the manifest's git hash-object -t blob --no-filters --stdin (collision-detecting, no attribute filters,
        no object write), run outside any repository, gives every pinned pre-image's gitBlob; inside a repository whose
        attributes would rewrite the bytes it still hashes them raw; a symlink hashes its readlink bytes."""
        import os
        contract = self.contract()
        manifest = self.manifest()
        for project in manifest['projects']:
            blob = project['treePin']['blobHash']
            self.assertEqual(blob['argv'], self.HASH_OBJECT)
            self.assertEqual(blob['never'], ['-w (no object writes)', '--literally', '--path (no attribute filters)',
                'a plain SHA-1'])
            for flag in ('-w', '--literally', '--path'):
                self.assertNotIn(flag, blob['argv'])
            for row in project['files']:
                if row['pre'] is None:
                    continue
                data = contract.fixture_path(project['path'], row['path']).read_bytes()
                self.assertEqual(self.git_isolated(blob['argv'], data).strip(), row['pre']['gitBlob'])
                self.assertEqual(contract.git_blob(data), row['pre']['gitBlob'])
        argv = manifest['projects'][0]['treePin']['blobHash']['argv']
        crlf = b'a\r\nb\r\n'
        with tempfile.TemporaryDirectory() as repo:
            subprocess.run(['git', 'init', '-q', repo], check=True)
            # These attributes turn CRLF into LF on the way in, so a filtered hash differs from the raw one.
            Path(repo, '.gitattributes').write_text('* text=auto eol=crlf\n')
            Path(repo, 'target.txt').write_bytes(crlf)
            os.symlink('target.txt', Path(repo, 'link'))
            before = sorted(str(p) for p in Path(repo, '.git/objects').rglob('*') if p.is_file())
            raw = contract.git_blob(crlf)
            filtered = self.git_isolated(['git', 'hash-object', '-t', 'blob', '--stdin', '--path', 'target.txt'], crlf,
                cwd=repo).strip()
            self.assertNotEqual(filtered, raw, 'the fixture really has a filter to suppress')
            self.assertEqual(self.git_isolated(argv, crlf, cwd=repo).strip(), raw)
            # The same flags over the file itself: without --no-filters git filters it, with it the bytes stay raw.
            paths_argv = [a for a in argv if a != '--stdin'] + ['--stdin-paths']
            self.assertEqual(self.git_isolated(paths_argv, b'target.txt\n', cwd=repo).strip(), raw)
            self.assertEqual(self.git_isolated([a for a in paths_argv if a != '--no-filters'], b'target.txt\n',
                cwd=repo).strip(), filtered)
            refused = subprocess.run(argv + ['--path', 'target.txt'], input=crlf, capture_output=True, cwd=repo)
            self.assertNotEqual(refused.returncode, 0, 'git refuses to combine --no-filters with --path')
            linked = self.git_isolated(argv, os.readlink(Path(repo, 'link')).encode(), cwd=repo).strip()
            self.assertEqual(sorted(str(p) for p in Path(repo, '.git/objects').rglob('*') if p.is_file()), before,
                'hash-object wrote no object')
            subprocess.run(['git', '-C', repo, 'add', 'link'], check=True)
            staged = subprocess.check_output(['git', '-C', repo, 'ls-files', '-s', 'link'], text=True).split()
            self.assertEqual(staged[:2], ['120000', linked], 'a symlink is its readlink bytes, mode 120000')

    @staticmethod
    def images_after_each_patch(contract, pre, patches):
        """[{path: bytes or None}] before the first patch and after each one, applied in order with plain git apply."""
        with tempfile.TemporaryDirectory() as tree:
            for f, data in pre.items():
                if data is not None:
                    Path(tree, f).parent.mkdir(parents=True, exist_ok=True)
                    Path(tree, f).write_bytes(data)
            subprocess.run(['git', 'init', '-q', tree], check=True)
            images = [dict(pre)]
            for patch in patches:
                subprocess.run(['git', '-C', tree, 'apply', str(contract.bundle_or_os('patches/' + patch))], check=True)
                images.append({f: Path(tree, f).read_bytes() if Path(tree, f).exists() else None for f in pre})
            return images

    def test_every_generated_blob_id_is_confirmed_by_git(self):
        """blobHash.implementation: every pre, post, chain, base and delta gitBlob the offline generator records equals
        what the manifest's no-filters hash-object gives for those bytes (one --stdin-paths call per project)."""
        contract = self.contract()
        manifest = self.manifest()
        argv = [a for a in manifest['projects'][0]['treePin']['blobHash']['argv'] if a != '--stdin'] + ['--stdin-paths']
        base = {p['path']: p for p in manifest['base']['projects']}
        delta = {f['path']: f for f in manifest['candidateDelta']['files']}
        checked = 0
        for project, (path, _, patches) in zip(manifest['projects'], contract.PROJECTS):
            pre = {r['path']: None if r['pre'] is None else contract.fixture_path(path, r['path']).read_bytes()
                for r in project['files']}
            images = self.images_after_each_patch(contract, pre, patches)
            base_patches = [p['name'] for p in base[path]['patches']]
            self.assertEqual(patches[:len(base_patches)], base_patches, f'{path}: the base is a prefix of the candidate')
            base_post = images[len(base_patches)]
            claims = []
            for row in project['files']:
                f = row['path']
                if row['pre'] is not None:
                    claims.append((pre[f], row['pre']['gitBlob']))
                claims.append((images[-1][f], row['post']['gitBlob']))
                for c in row['chain']:
                    k = patches.index(c['patch'])
                    if c['before'] is not None:
                        claims.append((images[k][f], c['before']))
                    claims.append((images[k + 1][f], c['after']))
                post = next(r for r in base[path]['files'] if r['path'] == f)['post']
                claims.append((base_post[f], post['gitBlob']))
                if f'{path}/{f}' in delta:
                    claims.append((base_post[f], delta[f'{path}/{f}']['before']['gitBlob']))
                    claims.append((images[-1][f], delta[f'{path}/{f}']['after']['gitBlob']))
            with tempfile.TemporaryDirectory() as scratch:
                names = []
                for i, (data, _) in enumerate(claims):
                    Path(scratch, str(i)).write_bytes(data)
                    names.append(str(Path(scratch, str(i))))
                ids = self.git_isolated(argv, ('\n'.join(names) + '\n').encode()).split()
            self.assertEqual(ids, [claimed for _, claimed in claims], path)
            checked += len(claims)
        self.assertGreater(checked, 100)

    def test_a_malformed_manifest_is_reported_not_raised(self):
        import copy
        contract = self.contract()
        manifest = self.manifest()
        edits = (lambda m: m['projects'][0].__setitem__('treePin', None),
            lambda m: m['projects'][0]['treePin']['unlisted'].__setitem__('excludedPaths', None),
            lambda m: m['projects'][0]['treePin']['unlisted']['excludedPaths'].append([1]),
            lambda m: m['flagEvidence'].__setitem__('flags', None),
            lambda m: m['flagEvidence']['flags'][0].__setitem__('definedIn', 'x'),
            lambda m: m.__setitem__('projects', None),
            lambda m: m.__setitem__('overlayRoles', 7))
        for edit in edits:
            changed = copy.deepcopy(manifest)
            edit(changed)
            errors = contract.check(changed)
            self.assertTrue(errors and all(isinstance(e, str) for e in errors), errors)

    def test_only_reviewed_targets_can_be_not_an_input(self):
        """M4: notAnInput is an exact allowlist; any other overlay outside Launcher3 and build/release is refused, so
        location alone never makes an overlay an unpinned input."""
        contract = self.contract()
        self.assertEqual(list(contract.NOT_AN_INPUT), self.manifest()['overlayRoles']['notAnInput']['targets'])
        real = contract.overlay_targets
        added = 'frameworks/base/core/java/android/companion/virtual/computercontrol/RoseInjected.java'
        contract.overlay_targets = lambda: dict(real(), **{added: b'class RoseInjected {}\n'})
        try:
            with self.assertRaises(ValueError) as refused:
                contract.overlays()
            self.assertIn('neither required nor a reviewed notAnInput target', str(refused.exception))
            errors = contract.check(self.manifest())
            self.assertTrue(any(added in e for e in errors), errors)
        finally:
            contract.overlay_targets = real

    def test_each_root_tree_is_reconstructed_offline_and_confirmed_by_git(self):
        """M5: every tree object of each pinned listing is rebuilt offline; git hash-object -t tree (its own SHA-1 and
        tree checks, no write) confirms each one, and the root is the manifest's rootTree."""
        contract = self.contract()
        manifest = self.manifest()
        for project in manifest['projects']:
            root, bodies = {}, []
            for mode, _, blob, path in contract.read_listing(project['path']):
                node = root
                for part in path.split('/')[:-1]:
                    node = node.setdefault(part, {})
                node[path.split('/')[-1]] = (mode, blob)

            def write(node):
                entries = []
                for name, value in node.items():
                    if isinstance(value, dict):
                        entries.append((name + '/', b'40000 ' + name.encode() + b'\0' + bytes.fromhex(write(value))))
                    else:
                        entries.append((name, value[0].encode() + b' ' + name.encode() + b'\0' + bytes.fromhex(value[1])))
                body = b''.join(e for _, e in sorted(entries, key=lambda e: e[0].encode()))
                bodies.append((body, hashlib.sha1(b'tree %d\0' % len(body) + body).hexdigest()))
                return bodies[-1][1]
            self.assertEqual(write(root), project['treePin']['rootTree'])
            self.assertEqual(contract.git_tree_hash(contract.read_listing(project['path'])), project['treePin']['rootTree'])
            with tempfile.TemporaryDirectory() as scratch:
                names = []
                for i, (body, _) in enumerate(bodies):
                    Path(scratch, str(i)).write_bytes(body)
                    names.append(str(Path(scratch, str(i))))
                ids = self.git_isolated(['git', 'hash-object', '-t', 'tree', '--no-filters', '--stdin-paths'],
                    ('\n'.join(names) + '\n').encode()).split()
            self.assertEqual(ids, [i for _, i in bodies], f'{project["path"]}: every tree object as git hashes it')

    def test_not_an_input_overlays_are_exact_excluded_and_unused(self):
        """M4: the sixteen notAnInput overlays (seven, then 1G's nine roseshade sources) keep exact targets, are excluded from frameworks/base's treePin by exact
        path, and none is a compile goal, a flagEvidence input or inside Launcher3 or build/release. Required overlays
        stay pinned by sha256."""
        manifest = self.manifest()
        roles = manifest['overlayRoles']
        not_input = roles['notAnInput']['targets']
        self.assertEqual(len(not_input), 16)
        self.assertEqual(not_input, [o['target'] for o in manifest['overlays'] if o['role'] == 'notAnInput'])
        self.assertEqual(not_input, [o['target'] for o in manifest['base']['overlays'] if o['role'] == 'notAnInput'])
        self.assertEqual(not_input, manifest['base']['bundleContent']['notAnInputOverlays'])
        self.assertEqual(roles['required']['targets'], [o['target'] for o in manifest['overlays']
            if o['role'] == 'required'])
        for o in manifest['overlays']:
            self.assertEqual(set(o), {'target', 'sha256', 'role'} if o['role'] == 'required' else {'target', 'role'})
        excluded = set(manifest['projects'][0]['treePin']['unlisted']['excludedPaths'])
        evidence = json.dumps(manifest['flagEvidence'])
        self.assertEqual(roles['compileGoals'], ['Launcher3QuickStepLib'] + [row['expectedOutputDir'] +
            '/aconfig-flags.txt' for row in manifest['flagEvidence']['flags']])
        for target in not_input:
            self.assertTrue(target.startswith('frameworks/base/'), target)
            self.assertIn(target[len('frameworks/base/'):], excluded)
            self.assertNotIn(target, roles['compileGoals'])
            self.assertNotIn(target[len('frameworks/base/'):], evidence)
            self.assertNotIn(target.rsplit('/', 1)[1], json.dumps(manifest['candidateDelta']))
            self.assertFalse(set('*?[') & set(target))
        self.assertEqual(subprocess.run(['python3', str(self.SCRIPT)], capture_output=True).returncode, 0)
        import copy
        contract = self.contract()
        changed = copy.deepcopy(manifest)
        changed['overlayRoles']['notAnInput']['targets'].pop()
        self.assertIn('overlayRoles differ from the overlays, treePins and flagEvidence; regenerate with --write',
            contract.check(changed))

    def test_the_delta_application_and_branch_conditions_are_exact(self):
        manifest = self.manifest()
        delta = manifest['candidateDelta']
        for patch in delta['patches']:
            self.assertEqual((patch['project'], patch['strip']), ('packages/apps/Launcher3', 1))
            self.assertEqual(hashlib.sha256((ROOT/patch['bundlePath']).read_bytes()).hexdigest(), patch['sha256'])
        for o in delta['overlays']:
            self.assertEqual(hashlib.sha256((ROOT/o['bundlePath']).read_bytes()).hexdigest(), o['after'])
        app = delta['application']
        self.assertEqual(app['steps'], ['git -C <copy>/<project> apply --check -p<strip> <bundlePath>',
            'git -C <copy>/<project> apply -p<strip> <bundlePath>'])
        for flag in ('--3way', '--reject', '--whitespace=fix', '--recount', '--unidiff-zero'):
            self.assertIn(flag, ' '.join(app['forbidden']))
        branch = manifest['branches']['verifyBaseApplyCandidate']
        self.assertEqual([c['id'] for c in branch['conditions']], ['base-exact', 'delta-preimages', 'isolated-apply',
            'final-exact'])
        self.assertIn('proposal', branch['status'])
        # The edge patch applies with exactly those steps to the base images and gives the candidate's images.
        contract = self.contract()
        tree = Path(tempfile.mkdtemp(prefix='launcher3-delta-'))
        self.addCleanup(shutil.rmtree, tree, True)
        for f in delta['files']:
            relative = f['path'][len('packages/apps/Launcher3/'):]
            (tree/relative).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(CONTRACT_FIXTURES/f['path'], tree/relative)
            self.assertEqual(contract.image((tree/relative).read_bytes()), f['before'])
        subprocess.run(['git', 'init', '-q', str(tree)], check=True)
        for patch in delta['patches']:
            for step in ('--check', None):
                args = ['git', '-C', str(tree), 'apply'] + ([step] if step else []) + ['-p1', str(ROOT/patch['bundlePath'])]
                subprocess.run(args, check=True)
        for f in delta['files']:
            self.assertEqual(contract.image((tree/f['path'][len('packages/apps/Launcher3/'):]).read_bytes()), f['after'])

    def test_the_config_xml_rule_is_complete_as_data(self):
        """An adapter that reads only the manifest (as Release's does) reaches the same verdict as the reference."""
        contract = self.contract()
        rule = [r for p in self.manifest()['projects'] for r in p['files'] if 'integrationRewrite' in r][0]['integrationRewrite']

        def from_data(data, post, policy, bound_ok, address, locked):
            if not bound_ok or policy is None:
                return False
            t = rule['templates']
            rows = [policy['agent']] + policy['targets']
            if not all(re.fullmatch(rule['inputRules']['packageName'], r['packageName']) and r['certificates'] and all(
                    re.fullmatch(rule['inputRules']['certificate'], c) for c in r['certificates']) for r in rows):
                return False
            if address is not None and not (str(address).isdecimal() and 0 < int(address) < 2**63):
                return False
            def array(name, items):
                return t['array'].replace('{name}', name).replace('{entries}', ''.join(t['entry'].replace(
                    '{packageName}', r['packageName']).replace('{certificate}', c) for r in items for c in r['certificates']))
            want = dict(rule['markers'])
            want['config_pixelComputerControlAgents'] = array('Agents', [policy['agent']])
            want['config_pixelComputerControlTargets'] = array('Targets', policy['targets'])
            if policy.get('prototypeControl'):
                want['config_pixelComputerControlPrototypeAgents'] = array('PrototypeAgents', [policy['agent']])
            if address is not None:
                want['config_pixelComputerControlReferenceDisplayAddress'] = t[
                    'config_pixelComputerControlReferenceDisplayAddress'].split(' when given')[0].replace('{address}', address)
            if locked:
                want['config_pixelComputerControlLockedSessions'] = t['config_pixelComputerControlLockedSessions'].split(
                    ' when lockedControl')[0]
            text = data.decode()
            for name, expected in want.items():
                if len(re.findall(r'<(?:string-array|string|bool) name="%s"' % name, text)) != 1 or text.count(expected) != 1:
                    return False
                text = text.replace(expected, rule['markers'][name])
            return hashlib.sha256(text.encode()).hexdigest() == post

        cert, other = 'AB' * 32, 'CD' * 32
        policy = {'agent': {'packageName': 'dev.pixel.backgroundcontrol', 'certificates': [cert]},
            'targets': [{'packageName': 'dev.pixel.calculator.agent', 'certificates': [other]}], 'prototypeControl': True}
        integrated, post = self.integrated(policy, address='4619827259835644672', locked=True)
        plain, _ = self.integrated(dict(policy, prototypeControl=False))
        cases = [(integrated, policy, True, '4619827259835644672', True), (integrated, policy, False, '4619827259835644672', True),
            (integrated, dict(policy, prototypeControl=False), True, '4619827259835644672', True),
            (integrated, policy, True, None, True), (integrated, policy, True, '4619827259835644672', False),
            (plain, dict(policy, prototypeControl=False), True, None, False), (plain, policy, True, None, False),
            (integrated.replace(b'<resources', b'<resources ', 1), policy, True, '4619827259835644672', True)]
        verdicts = []
        for data, pol, bound_ok, address, locked in cases:
            sha = hashlib.sha256(b'r').hexdigest()
            reference = not contract.verify_integrated_config(data, post, pol, sha, sha if bound_ok else None, address, locked)
            self.assertEqual(from_data(data, post, pol, bound_ok, address, locked), reference)
            verdicts.append(reference)
        self.assertEqual(verdicts, [True, False, False, False, False, True, False, False])

    def test_the_branches_refuse_anything_else(self):
        manifest = self.manifest()
        self.assertEqual(set(manifest['branches']), {'verifyIntegrated', 'verifyBaseApplyCandidate', 'reconstruct', 'otherwise',
            'requires', 'forbids', 'definitions'})
        self.assertIn('no patch is applied again', manifest['branches']['verifyIntegrated'])
        self.assertIn('every overlay target are absent beforehand', manifest['branches']['reconstruct'])
        self.assertIn('Never reset or trust a snapshot', manifest['branches']['otherwise'])
        self.assertIn('any extra file is refused', manifest['unaffected'][0])
        self.assertIn('never records current bytes as provenance', manifest['provenance'])
        for project in manifest['projects']:
            for row in project['files']:
                if row['pre'] is not None:
                    self.assertNotEqual(row['pre']['sha256'], row['post']['sha256'], f'{row["path"]}: pre and post '
                        'are distinguishable, so mixed state is always detectable')
        required = [o['target'] for o in manifest['overlays'] if o['role'] == 'required']
        self.assertEqual(len([t for t in required if t.startswith('packages/apps/Launcher3/')]), 11)
        self.assertEqual(len([t for t in required if t.startswith('build/release/')]), 4)
        self.assertNotIn('Agent.apk', ' '.join(o['target'] for o in manifest['overlays']))

    def test_both_flags_bind_to_their_own_aconfig_declarations_modules(self):
        """The identity flag is in frameworks/base's android.security.flags-aconfig, not Launcher3's flag cache; each
        flag's evidence is the expected line in its own module's decoded aconfig-flags.txt (build/soong aconfigTextRule)."""
        evidence = self.manifest()['flagEvidence']
        flags = {row['flag']: row for row in evidence['flags']}
        security = flags['android.security.content_uri_permission_apis']
        self.assertEqual((security['module'], security['package'], security['container']),
            ('android.security.flags-aconfig', 'android.security', 'system'))
        self.assertEqual(security['expectedLine'], 'android.security.content_uri_permission_apis:READ_ONLY=true')
        self.assertEqual(security['definedIn']['path'], 'AconfigFlags.bp')
        self.assertEqual(security['declaredIn']['path'], 'core/java/android/security/responsible_apis_flags.aconfig')
        self.assertEqual(security['expectedOutputDir'], 'out/soong/.intermediates/frameworks/base/android.security.flags-aconfig')
        launcher = flags['com.android.launcher3.enable_fallback_overview_in_window']
        self.assertEqual((launcher['module'], launcher['container']), ('com_android_launcher3_flags', 'system_ext'))
        self.assertEqual(launcher['expectedLine'], 'com.android.launcher3.enable_fallback_overview_in_window:READ_ONLY=false')
        for row in flags.values():
            self.assertEqual(row['outputs'], ['aconfig-cache.pb', 'aconfig-flags.txt'])
            self.assertIn('result incomplete (identity flag unresolved); not a refusal of the inputs', row['accept'])
            for key in ('definedIn', 'declaredIn'):
                fixture = ROOT/'os/tests/fixtures/launcher3-compile-contract/flag-evidence'/row[key]['project']/row[key]['path']
                self.assertEqual(hashlib.sha256(fixture.read_bytes()).hexdigest(), row[key]['sha256'])
        self.assertEqual([r['path'] for r in evidence['producingRules']], ['aconfig/init.go', 'aconfig/aconfig_declarations.go'])
        for patch in (ROOT/'os/patches').glob('*.patch'):
            text = patch.read_text()
            for path in ('AconfigFlags.bp', 'responsible_apis_flags.aconfig', 'aconfig/launcher.aconfig', 'aconfig/Android.bp'):
                self.assertNotIn(path, text, f'{patch.name} touches {path}: the evidence definitions are pinned upstream')

    def integrated(self, policy, address=None, locked=False):
        """The real post-image config.xml, rewritten by apply-os-changes.py's own policy block (executed as written)."""
        contract = self.contract()
        project = self.manifest()['projects'][0]
        pre = {r['path']: None if r['pre'] is None else contract.fixture_path('frameworks/base', r['path']).read_bytes()
            for r in project['files']}
        post, _ = contract.apply_in_scratch(pre, contract.PROJECTS[0][2])
        patcher = (ROOT/'scripts/apply-os-changes.py').read_text()
        block = patcher[patcher.index("    config = source/'frameworks/base/core/res/res/values/config.xml'"):
            patcher.index("    config.write_text(text)")]
        import textwrap, types
        code = textwrap.dedent(block.replace("config = source/'frameworks/base/core/res/res/values/config.xml'", '')
            .replace('text = config.read_text()', 'text = original'))
        scope = {'policy': policy, 'a': types.SimpleNamespace(reference_display_address=address, locked_control=locked),
            'original': post['core/res/res/values/config.xml'].decode()}
        exec(code, scope)
        row = [r for r in project['files'] if r['path'] == 'core/res/res/values/config.xml'][0]
        return scope['text'].encode(), row['post']['sha256']

    def test_config_xml_is_accepted_only_as_exactly_rewritten_from_bound_inputs(self):
        contract = self.contract()
        cert, other = 'AB' * 32, 'CD' * 32
        policy = {'agent': {'packageName': 'dev.pixel.backgroundcontrol', 'certificates': [cert]},
            'targets': [{'packageName': 'dev.pixel.calculator.agent', 'certificates': [other]}], 'prototypeControl': True}
        bound = hashlib.sha256(b'the receipt').hexdigest()
        integrated, post = self.integrated(policy, address='4619827259835644672', locked=True)
        verify = lambda data, **kw: contract.verify_integrated_config(data, post, kw.get('policy', policy),
            kw.get('sha', bound), kw.get('bound', bound), kw.get('address', '4619827259835644672'), kw.get('locked', True))
        self.assertEqual(verify(integrated), [], 'the producer\'s exact output with its bound inputs is accepted')
        # The patcher's defaults (no prototype, address or locked control) are accepted with matching inputs only.
        plain_policy = dict(policy, prototypeControl=False)
        plain, _ = self.integrated(plain_policy)
        self.assertEqual(contract.verify_integrated_config(plain, post, plain_policy, bound, bound), [])
        refusals = {
            'unbound receipt': verify(integrated, bound=None),
            'a different receipt': verify(integrated, sha='0' * 64),
            'missing receipt': verify(integrated, policy=None),
            'another certificate': verify(integrated, policy=dict(policy, agent={'packageName': 'dev.pixel.backgroundcontrol',
                'certificates': ['EF' * 32]})),
            'prototype not bound': verify(integrated, policy=dict(policy, prototypeControl=False)),
            'locked not bound': verify(integrated, locked=False),
            'address not bound': verify(integrated, address=None),
            'another address': verify(integrated, address='12'),
            'an extra item': verify(integrated.replace(b'</item>\n    </string-array>', b'</item>\n        <item>x.y:' +
                b'12' * 32 + b'</item>\n    </string-array>', 1)),
            'a change elsewhere': verify(integrated.replace(b'<resources', b'<resources ', 1)),
            'CRLF': verify(integrated.replace(b'\n', b'\r\n')),
            'a duplicated element': verify(integrated + contract.CONFIG_MARKERS['config_pixelComputerControlLockedSessions'].encode()),
            'not rewritten at all': verify(plain),
            'a lowercase certificate': verify(integrated, policy=dict(policy, targets=[{'packageName': 'dev.pixel.calculator.agent',
                'certificates': [other.lower()]}])),
        }
        for case, errors in refusals.items():
            self.assertTrue(errors, f'{case}: must be refused')
        self.assertEqual(list(contract.CONFIG_MARKERS), [r['integrationRewrite']['elements'] for p in self.manifest()['projects']
            for r in p['files'] if 'integrationRewrite' in r][0])
        self.assertNotIn('def normalize_config', self.SCRIPT.read_text(), 'no pattern-based normalisation')

    def test_identity_sharing_is_explicit_and_independent_of_the_edge_switch(self):
        sharing = self.manifest()['identitySharing']
        self.assertTrue(sharing['appliesWithEdgeOff'])
        self.assertIn('setShareIdentityEnabled(true)', sharing['change'])
        self.assertIn('no dedicated exported entry', sharing['consumer'])
        self.assertTrue(sharing['consumerImplemented'])
        # Home's gate (#442), read here, never edited: the caller is checked before any of the sender's extras, against
        # the platform's recents package with a shared uid, only on SDK 35+, and the reply is a RectF only.
        app = ROOT/'workspace/rose-agent/app/src/dev/pixel/voice'
        main, rules = (app/'MainActivity.java').read_text(), (app/'GestureContract.java').read_text()
        gate = main[main.index(' void gestureContract(Intent i,boolean homeShown){'):main.index(' void sendGestureReply(')]
        self.assertLess(gate.index('getCurrentCaller()'), gate.index('getBundleExtra(GestureContract.EXTRA)'),
            'identity before any extra is read')
        self.assertIn('GestureContract.caller(caller.getPackage(),caller.getUid(),recentsPackage())', gate)
        self.assertIn('return recentsPackage != null && callerUid >= 0 && recentsPackage.equals(callerPackage);', rules)
        self.assertIn('static final int MIN_SDK = 35;', rules)
        self.assertIn('getIdentifier("config_recentsComponentName","string","android")', main)
        reply = main[main.index(' void sendGestureReply('):main.index(' static String recentsPackage()')]
        self.assertIn('data.putParcelable(GestureContract.ICON_POSITION,target);', reply)
        for never in ('surface_control', 'finish_callback', 'ICON_SURFACE', 'ON_FINISH'):
            self.assertNotIn(never, main + rules, f'RectF only: no {never}')
        self.assertTrue((ROOT/'workspace/rose-agent/app/tests/GestureContractTest.java').exists(), 'Home tests its rules')
        tree = Path(tempfile.mkdtemp(prefix='launcher3-identity-'))
        self.addCleanup(shutil.rmtree, tree, True)
        for name, relative in EDGE_TARGETS.items():
            (tree/relative).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(CONTRACT_FIXTURES/'packages/apps/Launcher3'/relative, tree/relative)
        subprocess.run(['git', 'init', '-q', str(tree)], check=True)
        # The edge patch now applies after the registered switcher patch, on exactly the edge targets.
        includes = [f'--include={relative}' for relative in EDGE_TARGETS.values()]
        for patch in (SWITCHER_PATCH, EDGE_PATCH):
            subprocess.run(['git', '-C', str(tree), 'apply', *includes, str(patch)], check=True)
        checked = 0
        for name, relative in EDGE_TARGETS.items():
            code = (tree/relative).read_text()
            if 'private void startHomeIntent(' not in code:
                continue
            start = code[code.index('private void startHomeIntent('):]
            start = start[:start.index('\n    }\n')]
            self.assertIn('.setShareIdentityEnabled(true);', start, name)
            self.assertNotIn('mRoseEdge', start, f'{name}: not gated by the edge switch')
            checked += 1
        self.assertEqual(checked, 2)



if __name__ == '__main__':
    unittest.main()
