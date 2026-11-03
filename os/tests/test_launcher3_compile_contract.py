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
BUNDLE = CONTRACT_FIXTURES/'candidate-pr440'
EDGE_PATCH = BUNDLE/'patches/rose-edge-launcher3.patch'
QUICKSTEP = 'quickstep/src/com/android/quickstep'
EDGE_TARGETS = {'AbsSwipeUpHandler.java': f'{QUICKSTEP}/AbsSwipeUpHandler.java',
    'FallbackSwipeHandler.java': f'{QUICKSTEP}/FallbackSwipeHandler.java',
    'RecentsWindowSwipeHandler.java': f'{QUICKSTEP}/window/RecentsWindowSwipeHandler.java'}


class Launcher3CompileContractTest(unittest.TestCase):
    """#410: os/compile-contracts/launcher3-quickstep-lib.json, the inputs for Release's compile-only adapter
    (verify-integrated or reconstruct; anything else refused). Every pre-image, post-image and blob chain is recomputed
    offline from committed pinned fixtures and patches; config.xml is accepted only with its bound generating inputs."""
    SCRIPT = ROOT/'scripts/launcher3-compile-contract.py'
    MANIFEST = ROOT/'os/compile-contracts/launcher3-quickstep-lib.json'
    # The agreed version (#410). Changing the manifest changes this, and the change needs re-agreement with Release.
    AGREED_SHA256 = '84282f95989151bc290f13ef225440bfc6cb2332d991e8c05b91cf8f6c889c65'

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
        self.assertEqual((manifest['kind'], manifest['schemaVersion']), ('rose-launcher3-compile-inputs', 2))
        result = subprocess.run(['python3', str(self.SCRIPT)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('42 files recomputed from pinned fixtures', result.stdout)

    def test_the_patch_order_is_the_patchers_plus_the_unregistered_edge_patch(self):
        contract = self.contract()
        patcher = (ROOT/'scripts/apply-os-changes.py').read_text()
        background = (ROOT/'scripts/background_apks.py').read_text()
        base = re.search(r"projects = \{'frameworks/base': (\[[^\]]*\])\}", patcher).group(1)
        extra = re.search(r'BACKGROUND_FRAMEWORK_PATCHES = (\[[^\]]*\])', background).group(1)
        import ast
        order = dict((p, patches) for p, _, patches in contract.PROJECTS)
        self.assertEqual(order['frameworks/base'], ast.literal_eval(base) + ast.literal_eval(extra))
        self.assertIn("projects['build/release'] = " + repr(order['build/release']), patcher)
        self.assertIn("projects['packages/apps/Launcher3'] = " + repr(order['packages/apps/Launcher3'][:1]), patcher)
        self.assertEqual(order['packages/apps/Launcher3'][1:], ['rose-edge-launcher3.patch'])
        manifest = self.manifest()
        self.assertIn('which apply-os-changes.py does not register', manifest['candidatePatches'])
        self.assertEqual(contract.CANDIDATE_PATCHES, order['packages/apps/Launcher3'][1:])
        self.assertNotIn('rose-edge-launcher3.patch', patcher, 'the candidate patch is never registered by this contract')
        self.assertIn('must not be completed by applying rose-edge-launcher3.patch alone', manifest['branches']['otherwise'])

    def test_the_candidate_is_inert_and_bound_by_its_reviewed_commit_and_digests(self):
        contract = self.contract()
        manifest = self.manifest()
        delta = manifest['candidateDelta']
        self.assertEqual((delta['pr'], delta['commit']), (440, 'e361d1cb15abdb80795569f1558fb7e0bcf5635d'))
        self.assertEqual(sorted(str(f.relative_to(BUNDLE)) for f in BUNDLE.rglob('*') if f.is_file()),
            ['overlays/packages/apps/Launcher3/quickstep/src/com/android/quickstep/fallback/RoseEdgeGesture.java',
             'overlays/packages/apps/Launcher3/quickstep/src/com/android/quickstep/fallback/RoseEdgeState.java',
             'overlays/packages/apps/Launcher3/quickstep/src/com/android/quickstep/fallback/RoseEdgeSwipe.java',
             'patches/rose-edge-launcher3.patch'])
        for row in delta['bundle']:
            self.assertTrue(row['path'].startswith('os/tests/fixtures/'), 'outside os/patches and os/overlays: inert')
            self.assertEqual(hashlib.sha256((ROOT/row['path']).read_bytes()).hexdigest(), row['sha256'])
        self.assertEqual([(p['name'], p['sha256']) for p in delta['patches']], [('rose-edge-launcher3.patch',
            hashlib.sha256(EDGE_PATCH.read_bytes()).hexdigest())])
        # The committed os/ holds either the base or the candidate bytes for each candidate overlay, never anything else.
        for o in delta['overlays']:
            committed = ROOT/'os/overlays'/o['target']
            now = hashlib.sha256(committed.read_bytes()).hexdigest() if committed.exists() else None
            self.assertIn(now, (o['before'], o['after']), o['target'])

    def test_base_plus_the_delta_is_exactly_the_candidate(self):
        """The base is the patcher's own output at this bundle (Launcher3: switcher only); the delta is disjoint from it,
        so base plus delta equals an integration with the edge patch registered after the switcher patch."""
        manifest = self.manifest()
        base, delta = manifest['base'], manifest['candidateDelta']
        self.assertEqual([p['name'] for p in base['projects'][1]['patches']], ['rose-switcher-launcher3.patch'])
        self.assertEqual(base['projects'][0], manifest['projects'][0], 'frameworks/base: the candidate does not change it')
        self.assertEqual(base['projects'][2], manifest['projects'][2], 'build/release: the candidate does not change it')
        launcher = {f'packages/apps/Launcher3/{r["path"]}': r for r in manifest['projects'][1]['files']}
        base_files = {f'packages/apps/Launcher3/{r["path"]}': r['post'] for r in base['projects'][1]['files']}
        delta_files = {f['path']: f for f in delta['files']}
        self.assertEqual(sorted(delta_files), sorted(f'packages/apps/Launcher3/{p}' for p in EDGE_TARGETS.values()))
        for path, row in launcher.items():
            post = {k: row['post'][k] for k in ('sha256', 'gitBlob')}
            if path in delta_files:
                self.assertEqual(delta_files[path]['before'], base_files[path], f'{path}: delta starts at the base')
                self.assertEqual(delta_files[path]['after'], post, f'{path}: delta ends at the candidate')
                self.assertEqual(base_files[path], {k: row['pre'][k] for k in ('sha256', 'gitBlob')},
                    f'{path}: the switcher patch never touches the edge files')
            else:
                self.assertEqual(base_files[path], post, f'{path}: outside the delta the base is already final')
        candidate_overlays = {o['target']: o['sha256'] for o in manifest['overlays']}
        base_overlays = {o['target']: o['sha256'] for o in base['overlays']}
        for o in delta['overlays']:
            self.assertEqual(candidate_overlays[o['target']], o['after'])
            self.assertEqual(base_overlays.get(o['target']), o['before'])
        for target, digest in candidate_overlays.items():
            if target not in {o['target'] for o in delta['overlays']}:
                self.assertEqual(base_overlays[target], digest, f'{target}: unchanged by the candidate')
        self.assertIn('verifyBaseApplyCandidate', manifest['branches'])
        self.assertIn('proposal, stated as data only', manifest['branches']['verifyBaseApplyCandidate']['status'])
        self.assertIn('partial, already applied or unknown state refuses', manifest['branches']['verifyBaseApplyCandidate']['otherwise'])
        result = subprocess.run(['python3', str(self.SCRIPT)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_a_tampered_delta_or_base_is_refused(self):
        import copy
        contract = self.contract()
        manifest = self.manifest()
        for edit in (lambda m: m['candidateDelta'].__setitem__('commit', '0' * 40),
                lambda m: m['candidateDelta']['overlays'][0].__setitem__('before', '1' * 64),
                lambda m: m['candidateDelta']['files'][0]['after'].__setitem__('sha256', '2' * 64),
                lambda m: m['base']['projects'][1]['files'][0]['post'].__setitem__('sha256', '3' * 64),
                lambda m: m['base']['projects'][1].__setitem__('patches', []),
                lambda m: m['base']['bundleContent']['patches'].pop(),
                lambda m: m['candidateDelta']['patches'][0].__setitem__('strip', 0),
                lambda m: m['base']['projects'][0]['treePin']['unlisted'].__setitem__('digest', '4' * 64)):
            changed = copy.deepcopy(manifest)
            edit(changed)
            self.assertTrue(contract.check_candidate(changed), 'a tampered candidate binding must be refused')
        self.assertEqual(contract.check_candidate(manifest), [])

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
        self.assertNotIn('rose-edge-launcher3.patch', [p['name'] for p in content['patches']])
        self.assertEqual({o['target'] for o in content['overlays']}, {o['target'] for o in manifest['base']['overlays']})
        self.assertNotIn('commit', ' '.join(manifest['base']['match'].split()[:3]), 'bound by content, not a commit')

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
        for step in ('--check', None):
            args = ['git', '-C', str(tree), 'apply'] + ([step] if step else []) + ['-p1', str(ROOT/delta['patches'][0]['bundlePath'])]
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
        self.assertEqual(set(manifest['branches']), {'verifyIntegrated', 'verifyBaseApplyCandidate', 'reconstruct', 'otherwise'})
        self.assertIn('no patch is applied again', manifest['branches']['verifyIntegrated'])
        self.assertIn('every overlay target are absent beforehand', manifest['branches']['reconstruct'])
        self.assertIn('Never reset or trust a snapshot', manifest['branches']['otherwise'])
        self.assertIn('any extra file is refused', manifest['unaffected'])
        self.assertIn('never records current bytes as provenance', manifest['provenance'])
        for project in manifest['projects']:
            for row in project['files']:
                if row['pre'] is not None:
                    self.assertNotEqual(row['pre']['sha256'], row['post']['sha256'], f'{row["path"]}: pre and post '
                        'are distinguishable, so mixed state is always detectable')
        required = [o['target'] for o in manifest['overlays'] if o['role'] == 'required']
        self.assertEqual(len([t for t in required if t.startswith('packages/apps/Launcher3/')]), 8)
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
            self.assertIn('refuse (identity flag unresolved, result incomplete)', row['accept'])
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
        subprocess.run(['git', '-C', str(tree), 'apply', str(EDGE_PATCH)], check=True)
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
