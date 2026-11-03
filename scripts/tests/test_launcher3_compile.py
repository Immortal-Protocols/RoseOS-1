"""LAUNCHER3-COMPILE: the fail-closed compile-only adapter (scripts/launcher3-compile.py) against OS Product's input
contract (os/compile-contracts/launcher3-quickstep-lib.json, scripts/launcher3-compile-contract.py).

Synthetic trees, a synthetic contract where bytes are needed, stand-in processes and fake locks only: no host, copy of
a real tree, lunch, build or network; git runs only as hash-object (no -w) and apply on synthetic temporary trees. Exercised: the pinned contract and the binding (unbound exits 4), the
branch classification (verifyIntegrated only on the exact post-state; mixed, pre, unknown and missing refuse; config.xml
only through OS Product's bound-policy check, never normalised on a mismatch), the copy bounds, the contained
environment, the sampled stops, the aconfig reader, the source and classpath proof and the write-once report."""
import contextlib
import gzip
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('launcher3_compile', ROOT/'scripts/launcher3-compile.py')
adapter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adapter)


def contract_module():
    """OS Product's checker, loaded by these tests only: parity and refusal cross-checks against B's own parser."""
    spec = importlib.util.spec_from_file_location('launcher3_compile_contract', ROOT/'scripts/launcher3-compile-contract.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


contract = contract_module()
BINDING = json.loads((ROOT/'os/build-only/launcher3-compile-host.json').read_text())
GIB = 1 << 30


def sha(data):
    return hashlib.sha256(data).hexdigest()


def blob(data):
    return hashlib.sha1(b'blob %d\0' % len(data) + data).hexdigest()


def run_cli(*argv):
    return subprocess.run([sys.executable, str(ROOT/'scripts/launcher3-compile.py'), *argv], capture_output=True, text=True,
                          cwd=ROOT)


POLICY = {'agent': {'packageName': 'dev.pixel.voice', 'certificates': ['A' * 64]},
          'targets': [{'packageName': 'dev.pixel.calculator.agent', 'certificates': ['B' * 64]}], 'prototypeControl': False}


def CONFIG(manifest):
    return next(f for f in manifest['projects'][0]['files'] if 'integrationRewrite' in f)


def config_rules_from_checker():
    """B-normalised rules equal to OS Product's checker templates (the manifest will carry them as data)."""
    rules = []
    for name, marker in contract.CONFIG_MARKERS.items():
        if marker.endswith('/>'):
            short = name[len('config_pixelComputerControl'):]
            rules.append({'name': name, 'kind': 'array', 'marker': marker, 'rows': 'targets' if short == 'Targets' else 'agent',
                          'when': 'prototypeControl' if short == 'PrototypeAgents' else None,
                          'open': f'    <string-array name="{name}" translatable="false">\n',
                          'item': '        <item>{package}:{certificate}</item>\n', 'close': '    </string-array>'})
        elif '<bool' in marker:
            rules.append({'name': name, 'kind': 'bool', 'marker': marker, 'input': 'lockedControl',
                          'template': f'    <bool name="{name}">true</bool>'})
        else:
            rules.append({'name': name, 'kind': 'string', 'marker': marker, 'input': 'referenceDisplayAddress',
                          'template': f'    <string name="{name}" translatable="false">{{value}}</string>'})
    return rules


MANIFEST = adapter.load_manifest()
CONFIG_ENTRY = next(f for f in MANIFEST['projects'][0]['files'] if 'integrationRewrite' in f)
RULES = adapter.config_rules(CONFIG_ENTRY)
EDGE_BASE = b'class Handler {\n    void a() {}\n}\n'
EDGE_AFTER = b'class Handler {\n    void a() {}\n    void edge() {}\n}\n'
L3 = 'packages/apps/Launcher3'
EDGE_FILE = 'quickstep/src/com/android/quickstep/H.java'
FALLBACK = 'quickstep/src/com/android/quickstep/fallback/'


def image(data):
    return None if data is None else {'sha256': sha(data), 'gitBlob': blob(data)}


def edge_patch():
    work = Path(tempfile.mkdtemp())
    (work/EDGE_FILE).parent.mkdir(parents=True)
    (work/EDGE_FILE).write_bytes(EDGE_BASE)
    git('init', '-q', cwd=work)
    git('add', '-A', cwd=work)
    git('commit', '-qm', 'b', cwd=work)
    (work/EDGE_FILE).write_bytes(EDGE_AFTER)
    return git('diff', cwd=work)


class Synthetic:
    """A three-project v2 contract with real bytes: frameworks/base config.xml (policy rewrite) and one Java file;
    Launcher3 one switcher-changed file, one new file and the edge-patched H.java; build/release one file; required
    overlays. The base section is the same with H.java at its base image, main's Gesture overlay and no State overlay;
    the candidate delta is a real edge patch plus the two candidate overlays, under a repository root of its own."""
    def __init__(self, root, repo=None):
        self.root = Path(root)
        markers = '\n'.join(contract.CONFIG_MARKERS.values())
        self.config_post = f'<resources>\n{markers}\n    <bool name="other">true</bool>\n</resources>\n'.encode()
        expected = contract.expected_config_elements(POLICY)
        integrated = self.config_post.decode()
        for name, marker in contract.CONFIG_MARKERS.items():
            integrated = integrated.replace(marker, expected[name])
        self.config_integrated = integrated.encode()
        self.files = {
            'frameworks/base/core/res/res/values/config.xml': (b'<resources/>\n', self.config_integrated, True),
            'frameworks/base/core/java/android/X.java': (b'class X {}\n', b'class X { void onAutomatedDevicesChanged() {} }\n', False),
            f'{L3}/quickstep/src/com/android/quickstep/FallbackSwipeHandler.java':
                (b'class F {}\n', b'class F { /* setShareIdentityEnabled(true) */ }\n', False),
            f'{L3}/quickstep/res/values/rose_agent_strip.xml': (None, b'<resources/>\n', False),
            f'{L3}/{EDGE_FILE}': (EDGE_BASE, EDGE_AFTER, False),
            'build/release/release_configs/cur.textproto': (b'cur\n', b'cur pixel_background_control\n', False),
        }
        self.overlay = (f'{L3}/{FALLBACK}RoseEdgeSwipe.java', b'class RoseEdgeSwipe {}\n')
        self.final_overlays = {self.overlay[0]: self.overlay[1], f'{L3}/{FALLBACK}RoseEdgeGesture.java': b'candidate gesture\n',
                               f'{L3}/{FALLBACK}RoseEdgeState.java': b'candidate state\n'}
        self.base_overlays = {self.overlay[0]: self.overlay[1], f'{L3}/{FALLBACK}RoseEdgeGesture.java': b'main gesture\n'}
        ignored = {'target': 'frameworks/base/Ignored.java', 'sha256': 'f' * 64, 'role': 'notAnInput'}

        def section(base):
            projects = {}
            for path, (pre, post, rewrite) in self.files.items():
                project = next(p for p in ('frameworks/base', L3, 'build/release') if path.startswith(p + '/'))
                if base and path.endswith(EDGE_FILE):
                    post = EDGE_BASE
                entry = {'path': path[len(project) + 1:], 'pre': image(pre),
                         'post': image(self.config_post if rewrite else post)}
                if rewrite:
                    entry['integrationRewrite'] = CONFIG_ENTRY['integrationRewrite']
                projects.setdefault(project, []).append(entry)
            overlays = self.base_overlays if base else self.final_overlays
            return {'projects': [{'path': p, 'files': f, 'treePin': {'base' if base else 'final': p}} for p, f in projects.items()],
                    'overlays': [{'target': t, 'sha256': sha(d), 'role': 'required'} for t, d in overlays.items()] + [ignored]}
        self.repo = Path(repo or tempfile.mkdtemp())
        patch = edge_patch()
        (self.repo/'bundle').mkdir(exist_ok=True)
        (self.repo/'bundle/edge.patch').write_bytes(patch)
        (self.repo/'bundle/Gesture.java').write_bytes(b'candidate gesture\n')
        (self.repo/'bundle/State.java').write_bytes(b'candidate state\n')
        (self.repo/'os/release-pipeline').mkdir(parents=True, exist_ok=True)
        (self.repo/'os/release-pipeline/frankel.json').write_bytes((ROOT/'os/release-pipeline/frankel.json').read_bytes())
        self.bundle_content = {'patches': [{'project': L3, 'name': 'rose-switcher-launcher3.patch', 'sha256': 'c' * 64}],
                               'overlays': [{'target': self.overlay[0], 'sha256': sha(self.overlay[1])}],
                               'notAnInputOverlays': [ignored['target']]}
        final, base = section(False), section(True)
        self.manifest = {'schemaVersion': 2, 'kind': 'rose-launcher3-compile-inputs', 'target': 'Launcher3QuickStepLib',
                         'proves': 'compile only', 'identitySharing': {'appliesWithEdgeOff': True}, **final,
                         'base': {**base, 'bundleContent': self.bundle_content},
                         'candidateDelta': {'commit': 'e' * 40,
                             'patches': [{'name': 'rose-edge-launcher3.patch', 'sha256': sha(patch), 'project': L3,
                                          'strip': 1, 'bundlePath': 'bundle/edge.patch'}],
                             'files': [{'path': f'{L3}/{EDGE_FILE}', 'before': image(EDGE_BASE), 'after': image(EDGE_AFTER)}],
                             'overlays': [{'target': f'{L3}/{FALLBACK}RoseEdgeGesture.java', 'bundlePath': 'bundle/Gesture.java',
                                           'before': sha(b'main gesture\n'), 'after': sha(b'candidate gesture\n')},
                                          {'target': f'{L3}/{FALLBACK}RoseEdgeState.java', 'bundlePath': 'bundle/State.java',
                                           'before': None, 'after': sha(b'candidate state\n')}]},
                         'flagEvidence': MANIFEST['flagEvidence']}
        self.policy_sha = sha(json.dumps(POLICY).encode())

    def write(self, state='post', skip=()):
        for path, (pre, post, _) in self.files.items():
            if path in skip:
                continue
            data = {'post': post, 'pre': pre, 'base': EDGE_BASE if path.endswith(EDGE_FILE) else post}[state]
            if data is None:
                continue
            target = self.root/path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        overlays = {'post': self.final_overlays, 'base': self.base_overlays, 'pre': {}}[state]
        for path, data in overlays.items():
            if path not in skip:
                target = self.root/path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
        return self.root

    def classify(self, section=None, **change):
        args = dict(policy=POLICY, policy_sha256=self.policy_sha, bound_policy_sha256=self.policy_sha)
        args.update(change)
        return adapter.classify(self.root, section or self.manifest, **args)


class Contract(unittest.TestCase):
    def test_the_pinned_contract_is_os_products_reviewed_manifest(self):
        data = (ROOT/'os/compile-contracts/launcher3-quickstep-lib.json').read_bytes()
        self.assertEqual(sha(data), adapter.MANIFEST_SHA256)
        manifest = adapter.load_manifest()
        self.assertEqual({p['path']: len(p['files']) for p in manifest['projects']},
                         {'frameworks/base': 27, 'packages/apps/Launcher3': 14, 'build/release': 1})
        self.assertEqual(sum(o['role'] == 'required' for o in manifest['overlays']), 12)
        self.assertEqual([c['id'] for c in manifest['branches']['verifyBaseApplyCandidate']['conditions']], adapter.CONDITIONS)
        self.assertEqual(manifest['candidateDelta']['commit'], 'e361d1cb15abdb80795569f1558fb7e0bcf5635d')
        self.assertTrue(manifest['identitySharing']['appliesWithEdgeOff'])
        self.assertIn('quickstep/src/com/android/quickstep/FallbackSwipeHandler.java',
                      [f['path'] for f in manifest['projects'][1]['files']])
        tmp = Path(tempfile.mkdtemp())/'m.json'
        tmp.write_bytes(data + b' ')
        with self.assertRaisesRegex(adapter.Invalid, 'not the reviewed contract version'):
            adapter.load_manifest(tmp)

    def test_contract_shape_changes_are_invalid(self):
        changes = [lambda m: m['branches']['verifyBaseApplyCandidate']['conditions'].pop(),
                   lambda m: m['candidateDelta']['application']['steps'].append('git apply --3way'),
                   lambda m: m['projects'][1]['treePin']['blobHash']['argv'].append('-w'),
                   lambda m: m['base']['projects'][0]['treePin'].__setitem__('objectFormat', 'sha256'),
                   lambda m: m['projects'][2]['treePin']['modes'].append('160000'),
                   lambda m: m['candidateDelta']['bundle'].pop(),
                   lambda m: m['candidateDelta']['patches'][0].__setitem__('strip', '1'),
                   lambda m: CONFIG(m)['integrationRewrite']['inputRules'].__setitem__('certificate', '[0-9A-Fa-f]{64}'),
                   lambda m: CONFIG(m)['integrationRewrite']['templates']['arrays'].__setitem__(
                       'config_pixelComputerControlPrototypeAgents', 'PrototypeAgents: the agent row'),
                   lambda m: CONFIG(m)['integrationRewrite']['templates'].__setitem__(
                       'config_pixelComputerControlLockedSessions', '    <bool name="x">true</bool> when lockedControl; otherwise the marker'),
                   lambda m: CONFIG(m)['integrationRewrite']['elements'].pop()]
        for index, change in enumerate(changes):
            with self.subTest(index=index):
                manifest = json.loads(json.dumps(MANIFEST))
                change(manifest)
                with self.assertRaises(adapter.Invalid):
                    adapter.contract_shape(manifest)
        adapter.contract_shape(json.loads(json.dumps(MANIFEST)))

    def test_the_mapped_config_rules_equal_os_products_checker_templates(self):
        self.assertEqual(RULES, adapter.check_rules(config_rules_from_checker()))
        for section in (MANIFEST['projects'], MANIFEST['base']['projects']):
            entry = next(f for f in section[0]['files'] if 'integrationRewrite' in f)
            self.assertEqual(adapter.config_rules(entry), RULES)

    def test_every_committed_listing_matches_its_pin(self):
        for section in (MANIFEST['projects'], MANIFEST['base']['projects']):
            for project in section:
                pin = project['treePin']
                with self.subTest(project=project['path']):
                    records = adapter.load_listing(pin)
                    self.assertEqual(adapter.tree_digest(records), (pin['unlisted']['digest'], pin['unlisted']['count']))
                    self.assertFalse(set(records) & {os.fsencode(p) for p in pin['unlisted']['excludedPaths']})

    def test_a_listing_that_is_not_its_pin_is_invalid(self):
        pin = json.loads(json.dumps(MANIFEST['projects'][2]['treePin']))
        root = Path(tempfile.mkdtemp())
        target = root/pin['listing']['path']
        target.parent.mkdir(parents=True)
        raw = gzip.decompress((ROOT/pin['listing']['path']).read_bytes())
        for name, data in (('quoted', raw.replace(b'\t', b'\t"', 1)), ('tree', raw.replace(b' blob ', b' tree ', 1)),
                           ('gitlink', raw.replace(b'100644 blob', b'160000 blob', 1)), ('extra', raw + raw.split(b'\n')[0] + b'x\n'),
                           ('unpinned mode', raw.replace(b'100644 blob', b'100755 blob', 1))):
            with self.subTest(name=name):
                target.write_bytes(gzip.compress(data))
                bad = dict(pin, listing=dict(pin['listing'], sha256=sha(data), compressedSha256=sha(target.read_bytes())))
                with self.assertRaises(adapter.Invalid):
                    adapter.load_listing(bad, root)
        target.write_bytes(gzip.compress(raw))
        with self.assertRaisesRegex(adapter.Invalid, 'compressed'):
            adapter.load_listing(pin, root)

    def test_committed_binding_is_unbound_and_the_hosted_check_exits_4(self):
        self.assertEqual([BINDING[k] for k in ('sourceTree', 'lunch', 'reconstructSource')], [None, None, None])
        self.assertEqual(BINDING['policyReceipt'], {'path': None, 'sha256': None})
        self.assertEqual(BINDING['bundleReport'], {'path': None, 'sha256': None})
        result = run_cli('check')
        self.assertEqual((result.returncode, result.stdout), (4, ''))
        self.assertIn('sourceTree, lunch, policyReceipt.path, policyReceipt.sha256', result.stderr)

    def binding(self, **change):
        path = Path(tempfile.mkdtemp())/'binding.json'
        record = {**BINDING, 'sourceTree': '/home/pixelbuild/grapheneos-2026100600', 'lunch': 'example-cur-user',
                  'policyReceipt': {'path': '/home/pixelbuild/receipts/policy.json', 'sha256': 'a' * 64}, **change}
        path.write_text(json.dumps(record))
        return path

    def test_a_bound_binding_passes_and_prints_its_bounds(self):
        result = run_cli('check', '--binding', str(self.binding()))
        self.assertEqual((result.returncode, result.stdout), (0, 'outer_seconds=18090\nfinish_seconds=1800\njob_minutes=342\n'))
        result = run_cli('check', '--binding', str(self.binding(branch='verifyBaseApplyCandidate')))
        self.assertEqual(result.returncode, 4)
        self.assertIn('bundleReport.path, bundleReport.sha256', result.stderr)
        result = run_cli('check', '--binding', str(self.binding(branch='verifyBaseApplyCandidate',
                                                                bundleReport={'path': '/home/pixelbuild/receipts/bundle.json', 'sha256': 'b' * 64})))
        self.assertEqual((result.returncode, result.stdout), (0, 'outer_seconds=19890\nfinish_seconds=1800\njob_minutes=372\n'))

    def test_a_version_checked_lunch_is_unbound_before_the_controller(self):
        for branch in ('verifyIntegrated', 'verifyBaseApplyCandidate'):
            result = run_cli('check', '--binding', str(self.binding(lunch='frankel-cur-user', branch=branch,
                             bundleReport={'path': '/home/pixelbuild/receipts/bundle.json', 'sha256': 'b' * 64})))
            self.assertEqual((result.returncode, result.stdout), (4, ''))
            self.assertIn('adevtool_git_missing', result.stderr)

    def test_invalid_bindings_refuse(self):
        for change in ({'host': 'other'}, {'taskParent': '/tmp'}, {'branch': 'mixed'}, {'sourceTree': 'relative'},
                       {'sourceTree': '/home/pixelbuild/rose-actions/blazer-prep-41473133543-1/source'},
                       {'sourceTree': '/home'}, {'lunch': 'frankel'}, {'policyReceipt': {'path': '/p'}},
                       {'policyReceipt': {'path': '/p', 'sha256': 'A' * 64}}, {'extra': 1}, {'schemaVersion': 1},
                       {'bundleReport': {'path': 'rel', 'sha256': None}}, {'bundleReport': None}):
            with self.subTest(change=change):
                self.assertEqual(run_cli('check', '--binding', str(self.binding(**change))).returncode, 2)

    def test_reconstruct_is_refused_without_an_approved_object_source(self):
        for source in (None, '/home/pixelbuild/objects'):
            result = run_cli('check', '--binding', str(self.binding(branch='reconstruct', reconstructSource=source)))
            self.assertEqual(result.returncode, 4, source)

    def test_policy_inputs_derive_from_frankels_apply_args(self):
        self.assertEqual(adapter.frankel_policy_args(), (None, False))
        root = Path(tempfile.mkdtemp())
        (root/'os/release-pipeline').mkdir(parents=True)
        config = json.loads((ROOT/'os/release-pipeline/frankel.json').read_text())
        config['host']['integrate']['reconcile']['applyArgs'] += ['--locked-control', '--reference-display-address', '7']
        (root/'os/release-pipeline/frankel.json').write_text(json.dumps(config))
        self.assertEqual(adapter.frankel_policy_args(root), ('7', True))


class Classify(unittest.TestCase):
    def setUp(self):
        self.s = Synthetic(tempfile.mkdtemp())

    def test_exact_post_state_is_verify_integrated_and_nothing_is_written(self):
        self.s.write('post')
        before = {p: p.stat().st_mtime_ns for p in self.s.root.rglob('*')}
        result = self.s.classify()
        self.assertEqual((result['branch'], result['problems']), ('exact', []))
        self.assertIsNone(self.s.classify(self.s.manifest['base'])['branch'])
        s = Synthetic(tempfile.mkdtemp())
        s.write('base')
        self.assertEqual(s.classify(s.manifest['base'])['branch'], 'exact')
        self.assertIsNone(s.classify()['branch'])
        self.assertEqual({p: p.stat().st_mtime_ns for p in self.s.root.rglob('*')}, before)

    def test_pre_mixed_unknown_and_missing_states_refuse(self):
        cases = (('pre', (), 'pre'), ('post', ('build/release/release_configs/cur.textproto',), 'missing'),
                 ('post', (self.s.overlay[0],), 'missing'))
        for state, skip, word in cases:
            with self.subTest(state=state, skip=skip):
                s = Synthetic(tempfile.mkdtemp())
                s.write(state, skip)
                result = s.classify()
                self.assertIsNone(result['branch'])
                self.assertTrue(any(word in p for p in result['problems']), result['problems'])
        s = Synthetic(tempfile.mkdtemp())
        s.write('post')
        (s.root/'build/release/release_configs/cur.textproto').write_bytes(b'cur\n')  # one pre-image among post-images
        result = s.classify()
        self.assertIsNone(result['branch'])
        self.assertTrue(result['problems'][0].startswith('mixed state'))
        (s.root/'build/release/release_configs/cur.textproto').write_bytes(b'something else\n')
        self.assertTrue(any(p.endswith(': other') for p in s.classify()['problems']))

    def test_config_policy_elements_are_never_normalised_on_a_mismatch(self):
        self.s.write('post')
        config = self.s.root/'frameworks/base/core/res/res/values/config.xml'
        locked = config.read_bytes().replace(b'<bool name="config_pixelComputerControlLockedSessions">false</bool>',
                                             b'<bool name="config_pixelComputerControlLockedSessions">true</bool>')
        config.write_bytes(locked)  # a security-relevant difference
        result = self.s.classify()
        self.assertIsNone(result['branch'])
        row = next(f for f in result['files'] if f['path'].endswith('config.xml'))
        self.assertEqual(row['state'], 'other')
        self.s.write('post')
        for change in ({'bound_policy_sha256': None}, {'bound_policy_sha256': 'b' * 64}, {'policy': None},
                       {'locked': True}, {'reference': '5'}):
            with self.subTest(change=change):
                self.assertIsNone(self.s.classify(**change)['branch'])

    def test_not_an_input_overlays_are_not_required(self):
        self.s.write('post')
        self.assertEqual(len(self.s.classify()['overlays']), 3)


class ConfigParity(unittest.TestCase):
    """B's manifest-data parser against OS Product's checker: the same acceptance and refusal on every case."""
    POLICIES = [POLICY, dict(POLICY, prototypeControl=True),
                dict(POLICY, targets=POLICY['targets'] + [{'packageName': 'dev.pixel.two', 'certificates': ['C' * 64, 'D' * 64]}]),
                dict(POLICY, agent={'packageName': 'dev.pixel.voice', 'certificates': ['a' * 64]}),
                dict(POLICY, agent={'packageName': 'nodot', 'certificates': ['A' * 64]}),
                dict(POLICY, targets=[{'packageName': 'dev.pixel.x', 'certificates': []}])]
    INPUTS = [(None, False), ('7', False), (None, True), ('7', True), ('0', False), ('x', False)]

    def post(self):
        markers = '\n'.join(contract.CONFIG_MARKERS.values())
        return f'<resources>\n{markers}\n    <bool name="other">true</bool>\n</resources>\n'

    def integrated(self, policy, reference, locked):
        text = self.post()
        for name, wanted in contract.expected_config_elements(policy, reference, locked).items():
            text = text.replace(contract.CONFIG_MARKERS[name], wanted)
        return text

    def mutations(self, text):
        yield 'exact', text
        yield 'outside', text.replace('<bool name="other">true', '<bool name="other">false')
        yield 'locked', text.replace('LockedSessions">false', 'LockedSessions">true')
        yield 'unlocked', text.replace('LockedSessions">true', 'LockedSessions">false')
        yield 'cert', text.replace('A' * 64, 'E' * 64)
        yield 'duplicate', text.replace('</resources>', contract.CONFIG_MARKERS['config_pixelComputerControlAgents'] + '\n</resources>')
        yield 'missing', text.replace(contract.CONFIG_MARKERS['config_pixelComputerControlLockedSessions'], '')
        yield 'whitespace', text.replace('        <item>', '         <item>', 1)
        yield 'reference', text.replace('ReferenceDisplayAddress" translatable="false">7<', 'ReferenceDisplayAddress" translatable="false">8<')

    def test_parser_and_checker_agree_on_every_case(self):
        post = hashlib.sha256(self.post().encode()).hexdigest()
        rules = RULES
        accepted = refused = 0
        for policy in self.POLICIES:
            try:
                built = self.integrated(policy, *self.INPUTS[3 if policy is POLICY else 0])
            except ValueError:
                built = self.integrated(POLICY, None, False)
            for reference, locked in self.INPUTS:
                for name, text in self.mutations(built):
                    for bound in ('p', None, 'q'):
                        args = (text.encode(), post, policy, 'p', bound, reference, locked)
                        with self.subTest(policy=policy, inputs=(reference, locked), case=name, bound=bound):
                            ours = adapter.verify_config(args[0], post, rules, *args[2:])
                            theirs = contract.verify_integrated_config(*args)
                            self.assertEqual(not ours, not theirs)
                            accepted += not ours
                            refused += bool(ours)
        self.assertGreater(accepted, 0)
        self.assertGreater(refused, accepted)
        self.assertTrue(adapter.verify_config(b'\xff', post, rules, POLICY, 'p', 'p'))

    def test_a_repeated_element_refuses_even_when_the_post_image_repeats_it(self):
        marker = contract.CONFIG_MARKERS['config_pixelComputerControlAgents']
        post = self.post().replace('</resources>', marker + '\n</resources>')
        text = post.replace(marker, contract.expected_config_elements(POLICY)['config_pixelComputerControlAgents'], 1)
        for name in contract.CONFIG_MARKERS:
            if name != 'config_pixelComputerControlAgents':
                text = text.replace(contract.CONFIG_MARKERS[name], contract.expected_config_elements(POLICY)[name])
        digest = hashlib.sha256(post.encode()).hexdigest()
        args = (text.encode(), digest, POLICY, 'p', 'p')
        self.assertTrue(contract.verify_integrated_config(*args))
        self.assertIn('2 elements, expected exactly one',
                      ' '.join(adapter.verify_config(args[0], digest, RULES, *args[2:])))

    def test_inputs_are_validated_even_when_the_text_matches_them(self):
        def unchecked(policy, reference, locked):
            text = self.post()
            for rule in RULES:
                if rule['kind'] == 'array':
                    if rule['when'] and not policy.get(rule['when']):
                        continue
                    rows = [policy['agent']] if rule['rows'] == 'agent' else policy['targets']
                    wanted = rule['open'] + ''.join(rule['item'].replace('{package}', r['packageName']).replace('{certificate}', c)
                                                    for r in rows for c in r['certificates']) + rule['close']
                elif rule['input'] == 'referenceDisplayAddress':
                    if reference is None:
                        continue
                    wanted = rule['template'].replace('{value}', reference)
                else:
                    if not locked:
                        continue
                    wanted = rule['template'].replace('{value}', 'true')
                text = text.replace(rule['marker'], wanted)
            return text.encode()
        post = hashlib.sha256(self.post().encode()).hexdigest()
        rules = RULES
        self.assertFalse(adapter.verify_config(unchecked(POLICY, '7', True), post, rules, POLICY, 'p', 'p', '7', True))
        for policy, reference in ((self.POLICIES[3], None), (self.POLICIES[4], None), (POLICY, '0'), (POLICY, str(2**63)),
                                  (dict(POLICY, agent={'packageName': 'dev.x', 'certificates': ['A' * 63]}), None)):
            with self.subTest(policy=policy, reference=reference):
                args = (unchecked(policy, reference, False), post, policy, 'p', 'p', reference, False)
                self.assertTrue(contract.verify_integrated_config(*args))
                self.assertIn('policy inputs refused', ' '.join(adapter.verify_config(args[0], post, rules, *args[2:])))

    def test_malformed_rules_are_invalid(self):
        bad = [None, [], [dict(RULES[0], kind='list')], [dict(RULES[0], open=RULES[1]['open'])],
               [dict(next(r for r in RULES if r['kind'] == 'bool'), template='    <bool name="x">true</bool>')], [RULES[0], RULES[0]], [dict(RULES[0], marker='<x/>')],
               [dict(RULES[0], item='<item/>')], [dict(RULES[0], extra=1)], [dict(next(r for r in RULES if r['kind'] == 'string'), input='lockedControl')],
               [dict(next(r for r in RULES if r['kind'] == 'bool'), input='referenceDisplayAddress')],
               [{k: v for k, v in RULES[0].items() if k != 'close'}], [dict(RULES[0], name='a"b')]]
        for rules in bad:
            with self.subTest(rules=rules), self.assertRaises(adapter.Invalid):
                adapter.check_rules(rules)

    def test_the_adapter_never_loads_os_products_checker(self):
        source = (ROOT/'scripts/launcher3-compile.py').read_text()
        code = source.split('\n"""\n', 1)[1]
        self.assertNotIn('launcher3-compile-contract', code)
        self.assertNotIn('importlib', code)
        self.assertFalse([v for v in vars(adapter).values() if getattr(v, '__name__', '') == 'launcher3_compile_contract'])


def pb_varint(value):
    out = b''
    while True:
        byte = value & 0x7F
        value >>= 7
        out += bytes([byte | (0x80 if value else 0)])
        if not value:
            return out


def pb_field(number, wire, value):
    key = pb_varint(number << 3 | wire)
    return key + (pb_varint(value) if wire == 0 else pb_varint(len(value)) + value)


def parsed_flag(package, name, state, permission):
    body = (pb_field(1, 2, package.encode()) + pb_field(2, 2, name.encode()) + pb_field(3, 2, b'ns')
            + pb_field(6, 0, state) + pb_field(7, 0, permission) + pb_field(9, 0, 0))
    return pb_field(1, 2, body)


class Reports(unittest.TestCase):
    def setUp(self):
        self.out = Path(tempfile.mkdtemp())/'out'

    def cache(self, module, data, name='intermediate.pb'):
        path = self.out/'soong/.intermediates'/module/name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def module_output(self, flag, text=None, cache=None):
        manifest = adapter.load_manifest()
        evidence = next(e for e in manifest['flagEvidence']['flags'] if e['flag'] == flag)
        directory = self.out/evidence['expectedOutputDir'][len('out/'):]
        directory.mkdir(parents=True, exist_ok=True)
        if text is not None:
            (directory/'aconfig-flags.txt').write_text(text)
        if cache is not None:
            (directory/'aconfig-cache.pb').write_bytes(cache)
        return manifest

    def test_both_flags_need_their_own_module_text_and_an_agreeing_cache(self):
        self.assertEqual(adapter.flag_values(parsed_flag('com.android.launcher3', 'enable_fallback_overview_in_window', 2, 1)),
                         {('com.android.launcher3', 'enable_fallback_overview_in_window'): ('DISABLED', 'READ_ONLY')})
        launcher = 'com.android.launcher3.enable_fallback_overview_in_window'
        security = 'android.security.content_uri_permission_apis'
        manifest = self.module_output(launcher, f'{launcher}:READ_ONLY=false\ncom.android.launcher3.other:READ_WRITE=true\n',
                                      parsed_flag('com.android.launcher3', 'enable_fallback_overview_in_window', 2, 1))
        self.module_output(security, f'{security}:READ_ONLY=true\n',
                           parsed_flag('android.security', 'content_uri_permission_apis', 1, 1))
        rows = {r['flag']: r for r in adapter.flag_report(self.out, manifest)}
        self.assertEqual({k: (r['ok'], r['module']) for k, r in rows.items()},
                         {launcher: (True, 'com_android_launcher3_flags'), security: (True, 'android.security.flags-aconfig')})
        cases = ((security, f'{security}:READ_ONLY=false\n', parsed_flag('android.security', 'content_uri_permission_apis', 2, 1),
                  'differs from the expected line'),
                 (security, f'{security}:READ_ONLY=true\n{security}:READ_ONLY=true\n', None, '2 lines'),
                 (security, '', None, '0 lines'),
                 (security, f'{security}:READ_ONLY=true\n', parsed_flag('android.security', 'content_uri_permission_apis', 2, 1),
                  'does not agree'),
                 (security, f'{security}:READ_ONLY=true\n', b'\x0a\xff', 'does not agree'))
        for flag, text, cache, reason in cases:
            with self.subTest(reason=reason):
                self.out = Path(tempfile.mkdtemp())/'out'
                manifest = self.module_output(flag, text, cache)
                row = next(r for r in adapter.flag_report(self.out, manifest) if r['flag'] == flag)
                self.assertFalse(row['ok'])
                self.assertIn(reason, row['reason'])
        # the flag's line in another module's output never counts
        self.out = Path(tempfile.mkdtemp())/'out'
        manifest = self.module_output(launcher, f'{security}:READ_ONLY=true\n')
        self.assertFalse(next(r for r in adapter.flag_report(self.out, manifest) if r['flag'] == security)['ok'])

    def test_source_and_classpath_proof(self):
        manifest = adapter.load_manifest()
        module = self.out/'soong/.intermediates/packages/apps/Launcher3/Launcher3QuickStepLib'
        module.mkdir(parents=True)
        launcher = manifest['projects'][1]
        paths = [f'packages/apps/Launcher3/{f["path"]}' for f in launcher['files']]
        paths += [o['target'] for o in manifest['overlays'] if o['role'] == 'required' and o['target'].startswith('packages/apps/Launcher3/')]
        (module/'srcs.rsp').write_text('\n'.join(paths[:-1]))
        proof = adapter.source_proof(self.out, manifest)
        self.assertEqual((proof['ok'], proof['missing']), (False, [paths[-1]]))
        (module/'srcs.rsp').write_text('\n'.join(paths))
        self.assertTrue(adapter.source_proof(self.out, manifest)['ok'])
        jar = self.out/'framework.jar'
        with zipfile.ZipFile(jar, 'w') as archive:
            archive.writestr('android/app/AutomatedPackageListener.class', b'\xca\xfe onAutomatedPackagesChanged')
        (module/'javac.rsp').write_text(f'-classpath {jar}')
        self.assertFalse(adapter.classpath_proof(self.out)['ok'])  # the pinned framework only
        with zipfile.ZipFile(jar, 'w') as archive:
            archive.writestr('android/app/AutomatedPackageListener.class', b'\xca\xfe onAutomatedDevicesChanged')
        self.assertTrue(adapter.classpath_proof(self.out)['ok'])


class Processes(unittest.TestCase):
    def test_git_in_the_copy_never_finds_a_parent_repository(self):
        parent = Path(tempfile.mkdtemp())
        git('init', '-q', cwd=parent)
        run_root = parent/'launcher3-compile-1-1'
        (run_root/'tree/vendor/adevtool').mkdir(parents=True)
        (run_root/'home').mkdir()
        env = adapter.contained_env(run_root)
        self.assertEqual(env['GIT_CEILING_DIRECTORIES'], str(run_root/'tree'))
        found = subprocess.run(['git', '-C', str(run_root/'tree/vendor/adevtool'), 'rev-parse', '--show-toplevel'],
                               env=dict(env, PATH=os.environ['PATH']), capture_output=True)
        self.assertNotEqual(found.returncode, 0)
        unbounded = subprocess.run(['git', '-C', str(run_root/'tree/vendor/adevtool'), 'rev-parse', '--show-toplevel'],
                                   capture_output=True, text=True)
        self.assertEqual(unbounded.stdout.strip(), str(parent))  # what the ceiling prevents

    def test_lunch_runs_on_its_own(self):
        self.assertEqual(adapter.lunch_argv('example-cur-user'), ['bash', '-c', 'set -eo pipefail; source build/envsetup.sh '
                         '>/dev/null; lunch "$1" >/dev/null', 'launcher3-lunch', 'example-cur-user'])
        self.assertTrue(adapter.version_checked('frankel-cur-user'))
        self.assertFalse(adapter.version_checked('frankelx-cur-user') or adapter.version_checked(None))

    def test_compile_command_targets_and_contained_environment(self):
        out = Path('/home/pixelbuild/rose-actions/launcher3-compile-1-1/out')
        targets = adapter.compile_targets(adapter.load_manifest(), out)
        self.assertEqual(targets, ['Launcher3QuickStepLib',
            f'{out}/soong/.intermediates/frameworks/base/android.security.flags-aconfig/aconfig-flags.txt',
            f'{out}/soong/.intermediates/packages/apps/Launcher3/aconfig/com_android_launcher3_flags/aconfig-flags.txt'])
        argv = adapter.compile_argv('frankel-cur-user', targets)
        self.assertEqual(argv, ['bash', '-c', 'set -eo pipefail; source build/envsetup.sh >/dev/null; lunch "$1" >/dev/null; '
                                'shift; m -j8 "$@"', 'launcher3-compile', 'frankel-cur-user', *targets])
        run_root = Path('/home/pixelbuild/rose-actions/launcher3-compile-1-1')
        env = adapter.contained_env(run_root, '/usr/bin:/bin')
        for key in ('HOME', 'TMPDIR', 'OUT_DIR', 'XDG_CACHE_HOME', 'XDG_CONFIG_HOME', 'XDG_DATA_HOME', 'GRADLE_USER_HOME'):
            self.assertTrue(env[key].startswith(str(run_root) + '/'), key)
        self.assertEqual(env['USE_CCACHE'], '0')
        self.assertEqual(sorted(k for k in env if k.startswith('GIT_')), ['GIT_CEILING_DIRECTORIES', 'GIT_CONFIG_GLOBAL', 'GIT_CONFIG_NOSYSTEM'])

    def test_sampled_stops_end_the_group(self):
        saved = adapter.SAMPLE_SECONDS
        adapter.SAMPLE_SECONDS = 0.1
        try:
            argv = [sys.executable, '-c', 'import time; time.sleep(30)']
            start = time.monotonic()
            self.assertEqual(adapter.supervised(argv, dict(os.environ), '/', 1, '/', rss=lambda pid: 0)[1], 'timeout')
            self.assertEqual(adapter.supervised(argv, dict(os.environ), '/', 60, '/',
                                                rss=lambda pid: adapter.MEMORY_STOP_BYTES + 1)[1], 'memory')
            used = iter([0] + [adapter.OUTPUT_STOP_BYTES + 1] * 100)
            self.assertEqual(adapter.supervised(argv, dict(os.environ), '/', 60, '/', rss=lambda pid: 0,
                                                used=lambda path: next(used))[1], 'output')
            self.assertLess(time.monotonic() - start, 20)
            self.assertEqual(adapter.supervised([sys.executable, '-c', 'pass'], dict(os.environ), '/', 60, '/',
                                                rss=lambda pid: 0), (0, None))
        finally:
            adapter.SAMPLE_SECONDS = saved

    def test_copy_refuses_over_the_bound_or_the_reserve_before_writing(self):
        dest = Path(tempfile.mkdtemp())/'tree'

        class FS:
            f_frsize, f_bavail = 1, 500 * GIB
        big = lambda base, policy: {'problems': [], 'excluded': [], 'entries': [{'type': 'file', 'size': 151 * GIB, 'path': 'x'}]}
        with self.assertRaisesRegex(adapter.Refused, 'above'):
            adapter.copy_tree(Path('/src'), dest, statvfs=lambda p: FS, planner=big)
        mid = lambda base, policy: {'problems': [], 'excluded': [], 'entries': [{'type': 'file', 'size': 201 * GIB, 'path': 'x'}]}
        saved = adapter.SOURCE_BOUND_BYTES
        adapter.SOURCE_BOUND_BYTES = 300 * GIB
        try:
            with self.assertRaisesRegex(adapter.Refused, 'reserve'):
                adapter.copy_tree(Path('/src'), dest, statvfs=lambda p: FS, planner=mid)
        finally:
            adapter.SOURCE_BOUND_BYTES = saved
        bad = lambda base, policy: {'problems': ['an external link'], 'excluded': [], 'entries': []}
        with self.assertRaisesRegex(adapter.Refused, 'copy_refused'):
            adapter.copy_tree(Path('/src'), dest, statvfs=lambda p: FS, planner=bad)
        self.assertFalse(dest.exists())

    def test_copy_uses_the_reviewed_alpha_copier_and_stops_at_its_deadline(self):
        source = Path(tempfile.mkdtemp())
        (source/'a').mkdir()
        (source/'a/f.txt').write_text('x')
        (source/'.git').mkdir()
        (source/'out').mkdir()
        (source/'vendor/adevtool/dl/unpacked/blob').mkdir(parents=True)  # the alpha policy's cache boundary
        (source/'vendor/adevtool/dl/.find-ignore').write_bytes(b'')
        dest = Path(tempfile.mkdtemp())/'tree'

        class FS:
            f_frsize, f_bavail = 1, 1 << 50
        result = adapter.copy_tree(source, dest, statvfs=lambda p: FS)
        self.assertEqual(sorted(str(p.relative_to(dest)) for p in dest.rglob('*')),
                         ['a', 'a/f.txt', 'vendor', 'vendor/adevtool', 'vendor/adevtool/dl', 'vendor/adevtool/dl/.find-ignore'])
        self.assertEqual(result['projectedBytes'], 1)
        slow = lambda *args: time.sleep(5)
        with self.assertRaisesRegex(adapter.Refused, 'copy_timeout'):
            adapter.copy_tree(source, Path(tempfile.mkdtemp())/'tree', statvfs=lambda p: FS, copier=slow, seconds=0.2)


def fs_tree():
    class FS:
        f_frsize, f_bavail = 1, 1 << 50
    return lambda p: FS


def git(*args, cwd, data=None):
    env = dict(os.environ, GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL='/dev/null', GIT_AUTHOR_NAME='t',
               GIT_AUTHOR_EMAIL='t@t', GIT_COMMITTER_NAME='t', GIT_COMMITTER_EMAIL='t@t')
    return subprocess.run(['git', *args], cwd=cwd, input=data, capture_output=True, check=True, env=env).stdout


def pinned(project):
    """(records, sha256, count) from a real `git ls-tree -r` of project's committed tree, as A's listing gives them."""
    records = {}
    for line in git('ls-tree', '-r', '-z', 'HEAD', cwd=project).split(b'\0'):
        if line:
            meta, path = line.split(b'\t', 1)
            mode, kind, oid = meta.split()
            records[path] = (mode.decode(), oid.decode())
    return (records, *adapter.tree_digest(records))


class TreePin(unittest.TestCase):
    def project(self):
        project = Path(tempfile.mkdtemp())/'proj'
        (project/'src/deep').mkdir(parents=True)
        (project/'src/A.java').write_bytes(b'class A {}\n')
        (project/'src/deep/B.java').write_bytes(b'class B {}\n')
        (project/'run.sh').write_bytes(b'#!/bin/sh\n')
        os.chmod(project/'run.sh', 0o755)
        os.symlink('src/A.java', project/'link')
        (project/'.gitattributes').write_bytes(b'*.java text eol=lf\n')
        git('init', '-q', cwd=project)
        git('add', '-A', cwd=project)
        git('commit', '-qm', 'base', cwd=project)
        return project

    def test_recomputed_records_equal_a_real_git_tree_and_write_no_objects(self):
        project = self.project()
        objects = sorted(p.name for p in (project/'.git/objects').rglob('*'))
        records, digest, count = pinned(project)
        self.assertEqual(adapter.tree_records(project, ['.git']), records)
        self.assertEqual(adapter.verify_tree_pin(project, records, digest, count, ['.git']), {'sha256': digest, 'count': count})
        self.assertEqual(records[b'run.sh'][0], '100755')
        self.assertEqual(records[b'link'], ('120000', blob(b'src/A.java')))
        (project/'crlf.java').write_bytes(b'class C {}\r\n')  # .gitattributes says eol=lf: the raw bytes still hash
        self.assertEqual(adapter.git_blob_id(b'class C {}\r\n'), blob(b'class C {}\r\n'))
        self.assertEqual(sorted(p.name for p in (project/'.git/objects').rglob('*')), objects)
        calls = []
        real = subprocess.run
        adapter.git_blob_id(b'x', run=lambda argv, **kw: calls.append(argv) or real(argv, **kw))
        self.assertEqual(calls, [MANIFEST['projects'][0]['treePin']['blobHash']['argv']])

    def test_serialization_is_raw_byte_sorted_records(self):
        records = {b'b': ('100644', '1' * 40), b'a/z': ('100755', '2' * 40), b'a-b': ('120000', '3' * 40)}
        self.assertEqual(adapter.tree_serialization(records),
                         b'120000 ' + b'3' * 40 + b'\ta-b\n100755 ' + b'2' * 40 + b'\ta/z\n100644 ' + b'1' * 40 + b'\tb\n')

    def test_extra_missing_mode_content_and_link_differences_refuse(self):
        changes = {'extra': lambda p: (p/'src/New.java').write_bytes(b'x'),
                   'missing': lambda p: (p/'src/deep/B.java').unlink(),
                   'mode': lambda p: os.chmod(p/'run.sh', 0o644),
                   'content': lambda p: (p/'src/A.java').write_bytes(b'class A { }\n'),
                   'link': lambda p: ((p/'link').unlink(), os.symlink('/abs/src/A.java', p/'link'))}
        for name, change in changes.items():
            with self.subTest(name):
                project = self.project()
                records, digest, count = pinned(project)
                change(project)
                with self.assertRaisesRegex(adapter.Refused, 'against the tree pin'):
                    adapter.verify_tree_pin(project, records, digest, count, ['.git'])

    def test_unsupported_types_a_bad_listing_and_unskipped_git_refuse(self):
        project = self.project()
        records, digest, count = pinned(project)
        with self.assertRaises(adapter.Invalid):
            adapter.verify_tree_pin(project, records, 'f' * 64, count, ['.git'])
        with self.assertRaisesRegex(adapter.Refused, 'extra'):
            adapter.verify_tree_pin(project, records, digest, count, [])
        os.mkfifo(project/'fifo')
        with self.assertRaisesRegex(adapter.Refused, 'unsupported file type'):
            adapter.tree_records(project, ['.git'])

    def test_a_file_that_changes_while_it_is_hashed_refuses(self):
        project = self.project()
        real = adapter.read_nofollow
        adapter.read_nofollow = lambda path: (lambda data, info: (data + b'grown', info))(*real(path))
        try:
            with self.assertRaisesRegex(adapter.Refused, 'changed while it was hashed'):
                adapter.tree_records(project, ['.git'])
        finally:
            adapter.read_nofollow = real
        with self.assertRaisesRegex(adapter.Refused, 'pin_timeout'):
            adapter.tree_records(project, ['.git'], deadline=0, clock=lambda: 1)

    def test_skipped_listed_paths_are_outside_the_unlisted_digest(self):
        project = self.project()
        records, _, _ = pinned(project)
        del records[b'src/A.java']
        (project/'src/A.java').write_bytes(b'checked separately by its own image\n')
        self.assertEqual(adapter.tree_records(project, ['.git', 'src/A.java']), records)

    def test_the_reviewed_copy_keeps_execute_bits_and_relative_links(self):
        source = Path(tempfile.mkdtemp())
        project = source/'packages/apps/P'
        (project/'src').mkdir(parents=True)
        (project/'src/A.java').write_bytes(b'class A {}\n')
        (project/'run.sh').write_bytes(b'#!/bin/sh\n')
        os.chmod(project/'run.sh', 0o755)
        os.symlink('src/A.java', project/'link')
        (source/'vendor/adevtool/dl/unpacked/blob').mkdir(parents=True)
        (source/'vendor/adevtool/dl/.find-ignore').write_bytes(b'')
        dest = Path(tempfile.mkdtemp())/'tree'
        old = os.umask(0o077)
        try:
            adapter.copy_tree(source, dest, statvfs=fs_tree())
        finally:
            os.umask(old)
        self.assertEqual(adapter.tree_records(dest/'packages/apps/P', []), adapter.tree_records(project, []))


class Candidate(unittest.TestCase):
    def setUp(self):
        self.tree = Path(tempfile.mkdtemp())/'tree'
        self.project = self.tree/'packages/apps/Launcher3'
        (self.project/'quickstep').mkdir(parents=True)
        (self.project/'quickstep/Handler.java').write_bytes(EDGE_BASE)
        os.chmod(self.project/'quickstep/Handler.java', 0o640)
        (self.project/'quickstep/Gesture.java').write_bytes(b'main gesture\n')
        self.bundle = Path(tempfile.mkdtemp())
        work = Path(tempfile.mkdtemp())
        (work/'quickstep').mkdir()
        (work/'quickstep/Handler.java').write_bytes(EDGE_BASE)
        git('init', '-q', cwd=work)
        git('add', '-A', cwd=work)
        git('commit', '-qm', 'b', cwd=work)
        (work/'quickstep/Handler.java').write_bytes(EDGE_AFTER)
        patch = git('diff', cwd=work)
        (self.bundle/'candidate-pr440').mkdir()
        (self.bundle/'candidate-pr440/rose-edge-launcher3.patch').write_bytes(patch)
        (self.bundle/'candidate-pr440/Gesture.java').write_bytes(b'candidate gesture\n')
        (self.bundle/'candidate-pr440/State.java').write_bytes(b'candidate state\n')
        self.patches = [{'name': 'rose-edge-launcher3.patch', 'sha256': sha(patch), 'project': 'packages/apps/Launcher3',
                         'bundlePath': 'candidate-pr440/rose-edge-launcher3.patch', 'strip': 1}]
        self.files = [{'path': 'packages/apps/Launcher3/quickstep/Handler.java', 'before': image(EDGE_BASE), 'after': image(EDGE_AFTER)}]
        self.overlays = [{'target': 'packages/apps/Launcher3/quickstep/Gesture.java', 'before': sha(b'main gesture\n'),
                          'after': sha(b'candidate gesture\n'), 'bundlePath': 'candidate-pr440/Gesture.java'},
                         {'target': 'packages/apps/Launcher3/quickstep/State.java', 'before': None,
                          'after': sha(b'candidate state\n'), 'bundlePath': 'candidate-pr440/State.java'}]

    def apply(self):
        adapter.delta_preconditions(self.tree, self.files, self.overlays)
        adapter.apply_candidate(self.tree, self.patches, self.overlays, self.bundle, self.files)
        self.assertEqual((self.project/'quickstep/Handler.java').read_bytes(), EDGE_AFTER)
        for item in self.overlays:
            self.assertEqual(sha((self.tree/item['target']).read_bytes()), item['after'])

    def test_base_plus_delta_reaches_the_exact_candidate_images_with_modes_kept(self):
        old = os.umask(0o077)
        try:
            self.apply()
        finally:
            os.umask(old)
        self.assertEqual((self.project/'quickstep/Handler.java').read_bytes(), EDGE_AFTER)
        self.assertEqual(os.stat(self.project/'quickstep/Handler.java').st_mode & 0o7777, 0o640)
        self.assertEqual(sorted(p.name for p in (self.project/'quickstep').iterdir()), ['Gesture.java', 'Handler.java', 'State.java'])

    def test_a_second_application_or_a_partial_state_refuses_before_anything_changes(self):
        self.apply()
        with self.assertRaisesRegex(adapter.Refused, 'already applied or unknown'):
            adapter.delta_preconditions(self.tree, self.files, self.overlays)
        self.setUp()
        (self.project/'quickstep/State.java').write_bytes(b'candidate state\n')
        with self.assertRaisesRegex(adapter.Refused, 'already applied or unknown'):
            adapter.delta_preconditions(self.tree, self.files, self.overlays)
        self.setUp()
        (self.project/'quickstep/Handler.java').write_bytes(EDGE_AFTER)
        with self.assertRaisesRegex(adapter.Refused, 'already applied or unknown'):
            adapter.delta_preconditions(self.tree, self.files, self.overlays)

    def test_wrong_digests_refuse_and_apply_uses_no_fallback(self):
        bad = [dict(self.patches[0], sha256='0' * 64)]
        with self.assertRaisesRegex(adapter.Refused, 'not the bound candidate patch'):
            adapter.apply_candidate(self.tree, bad, self.overlays, self.bundle, self.files)
        self.assertEqual((self.project/'quickstep/Handler.java').read_bytes(), EDGE_BASE)
        calls = []
        real = subprocess.run
        adapter.apply_candidate(self.tree, self.patches, [], self.bundle, self.files,
                                run=lambda argv, **kw: calls.append(argv) or real(argv, **kw))
        patch = str(self.bundle/'candidate-pr440/rose-edge-launcher3.patch')
        self.assertEqual(calls, [['git', '-C', str(self.project), 'apply', '--check', '-p1', patch],
                                 ['git', '-C', str(self.project), 'apply', '-p1', patch]])
        self.assertEqual(adapter.APPLY_STEPS, MANIFEST['candidateDelta']['application']['steps'])
        self.setUp()
        overlays = [dict(self.overlays[0], after='0' * 64)]
        with self.assertRaisesRegex(adapter.Refused, 'not the bound candidate overlay'):
            adapter.apply_candidate(self.tree, [], overlays, self.bundle, [])
        self.assertEqual((self.project/'quickstep/Gesture.java').read_bytes(), b'main gesture\n')

    def test_a_patch_that_does_not_apply_cleanly_refuses(self):
        (self.project/'quickstep/Handler.java').write_bytes(b'class Handler {\n    void b() {}\n}\n')
        with self.assertRaisesRegex(adapter.Refused, 'git apply --check'):
            adapter.apply_candidate(self.tree, self.patches, [], self.bundle, self.files)


class RealDelta(unittest.TestCase):
    """The committed candidate delta (#440 e361d1c, inert under os/tests/fixtures) applied by this adapter's own code to
    OS Product's pinned base fixtures and main's own RoseEdgeGesture: every final image equals the contract's."""
    def test_the_real_delta_reaches_the_contract_final_images_from_the_real_base(self):
        delta = MANIFEST['candidateDelta']
        fixtures = ROOT/'os/tests/fixtures/launcher3-compile-contract'
        tree = Path(tempfile.mkdtemp())/'tree'
        for item in delta['files']:
            (tree/item['path']).parent.mkdir(parents=True, exist_ok=True)
            (tree/item['path']).write_bytes((fixtures/item['path']).read_bytes())
        for item in delta['overlays']:
            if item['before'] is not None:
                (tree/item['target']).parent.mkdir(parents=True, exist_ok=True)
                (tree/item['target']).write_bytes((ROOT/'os/overlays'/item['target']).read_bytes())
        adapter.delta_preconditions(tree, delta['files'], delta['overlays'])
        adapter.apply_candidate(tree, delta['patches'], delta['overlays'], ROOT, delta['files'])
        final = {f'{p["path"]}/{f["path"]}': f['post'] for p in MANIFEST['projects'] for f in p['files']}
        overlays = {o['target']: o['sha256'] for o in MANIFEST['overlays'] if o['role'] == 'required'}
        for item in delta['files']:
            self.assertTrue(adapter.image_is(tree/item['path'], item['after']))
            self.assertEqual(item['after'], final[item['path']])
        for item in delta['overlays']:
            self.assertEqual(sha((tree/item['target']).read_bytes()), item['after'])
            self.assertEqual(item['after'], overlays[item['target']])
        with self.assertRaisesRegex(adapter.Refused, 'delta-preimages'):
            adapter.delta_preconditions(tree, delta['files'], delta['overlays'])


class Run(unittest.TestCase):
    def setUp(self):
        self.parent = Path(tempfile.mkdtemp())
        self.s = Synthetic(tempfile.mkdtemp())
        self.binding = {**BINDING, 'taskParent': str(self.parent), 'sourceTree': '/src', 'lunch': 'example-cur-user',
                        'policyReceipt': {'path': '/p.json', 'sha256': self.s.policy_sha}}
        self.bundle_sha = sha(json.dumps(self.s.bundle_content).encode())
        self.calls = []
        self.lunch_result = (0, None)

    def copier(self, state='post'):
        def copy(source, destination):
            self.calls.append('copy')
            Synthetic(destination, self.s.repo).write(state)
            return {'entries': 1}
        return copy

    def compiler(self, code=0, stopped=None):
        def compile_(argv, env, cwd, seconds, out_dir):
            if 'm -j' not in argv[2]:
                self.calls.append(('lunch', argv[-1], env['GIT_CEILING_DIRECTORIES'], seconds))
                return self.lunch_result
            self.calls.append(('compile', argv[-2:], env['OUT_DIR'], seconds))
            out = Path(out_dir)
            module = out/'soong/.intermediates/packages/apps/Launcher3/Launcher3QuickStepLib'
            module.mkdir(parents=True)
            return code, stopped
        return compile_

    def pins(self, fail=None):
        def pin(tree, projects, root, deadline):
            label = next(iter(projects[0]['treePin']))
            self.calls.append(('pins', label))
            if label == fail:
                raise adapter.Refused(f'{projects[0]["path"]}/x: extra against the tree pin')
            return {p['path']: {'sha256': 'd' * 64, 'count': 1} for p in projects}
        return pin

    def run_adapter(self, state='post', lock=None, code=0, stopped=None, pins=None, bundle=None):
        lock = lock or (lambda path: contextlib.nullcontext())
        return adapter.run(self.binding, self.s.manifest, '41700000000', '1', root=self.s.repo, lock=lock,
                           copier=self.copier(state), compiler=self.compiler(code, stopped), pins=pins or self.pins(),
                           policy_loader=lambda path: (POLICY, self.s.policy_sha),
                           bundle_loader=lambda path: (bundle or self.s.bundle_content, self.bundle_sha))

    def report(self):
        return json.loads((self.parent/'launcher3-compile-41700000000-1/receipts/report.json').read_text())

    def test_verified_state_compiles_and_reports_without_claiming_a_proof_it_lacks(self):
        self.assertEqual(self.run_adapter(), 0)
        report = self.report()
        self.assertEqual(self.calls[:2], ['copy', ('pins', 'final')])
        out = self.parent/'launcher3-compile-41700000000-1/out'
        tree = self.parent/'launcher3-compile-41700000000-1/tree'
        self.assertEqual(self.calls[2], ('lunch', 'example-cur-user', str(tree), adapter.LUNCH_SECONDS))
        self.assertEqual(report['lunch'], {'exit': 0, 'stoppedBy': None})
        self.assertEqual(self.calls[3][1:], ([f'{out}/soong/.intermediates/frameworks/base/android.security.flags-aconfig/'
            'aconfig-flags.txt', f'{out}/soong/.intermediates/packages/apps/Launcher3/aconfig/com_android_launcher3_flags/'
            'aconfig-flags.txt'], str(out), adapter.COMPILE_SECONDS))
        self.assertEqual(report['classification']['branch'], 'verifyIntegrated')
        self.assertEqual([c['id'] for c in report['conditions']], ['verifyIntegrated'])
        self.assertEqual(report['compile'], {'exit': 0, 'stoppedBy': None, 'targets': ['Launcher3QuickStepLib',
            'out/soong/.intermediates/frameworks/base/android.security.flags-aconfig/aconfig-flags.txt',
            'out/soong/.intermediates/packages/apps/Launcher3/aconfig/com_android_launcher3_flags/aconfig-flags.txt']})
        self.assertEqual({f['flag']: f['ok'] for f in report['flags']},
                         {'android.security.content_uri_permission_apis': False,
                          'com.android.launcher3.enable_fallback_overview_in_window': False})
        self.assertEqual(report['result'], 'incomplete')  # no source list, classpath or flag evidence was produced
        self.assertEqual(report['policyInputs'], {'receiptSha256': self.s.policy_sha, 'referenceDisplayAddress': None,
                                                  'lockedControl': False})
        self.assertTrue(report['identitySharing']['appliesWithEdgeOff'])
        self.assertIn('not verified', report['unaffectedProjects'])
        self.assertIn('no hard cap', report['bounds']['kind'])

    def test_mixed_state_refuses_before_any_compile(self):
        self.assertEqual(self.run_adapter('pre'), 3)
        self.assertEqual(self.calls, ['copy'])
        report = self.report()
        self.assertEqual(report['result'], 'refused')
        self.assertTrue(report['refused'].startswith('state_refused'))

    def test_a_tree_pin_difference_refuses_before_any_compile(self):
        self.assertEqual(self.run_adapter(pins=self.pins(fail='final')), 3)
        self.assertEqual(self.calls, ['copy', ('pins', 'final')])
        self.assertIn('extra against the tree pin', self.report()['refused'])

    def test_a_base_tree_is_refused_by_verify_integrated(self):
        self.assertEqual(self.run_adapter('base'), 3)
        self.assertEqual(self.calls, ['copy'])
        self.assertTrue(self.report()['refused'].startswith('state_refused'))

    def vbac(self):
        self.binding = {**self.binding, 'branch': 'verifyBaseApplyCandidate',
                        'bundleReport': {'path': '/bundle.json', 'sha256': self.bundle_sha}}

    def test_base_apply_candidate_runs_the_four_conditions_in_the_copy_only(self):
        self.vbac()
        source = Path(tempfile.mkdtemp())
        Synthetic(source, self.s.repo).write('base')
        before = {p: p.read_bytes() for p in source.rglob('*') if p.is_file()}
        self.assertEqual(self.run_adapter('base'), 0)
        self.assertEqual({p: p.read_bytes() for p in source.rglob('*') if p.is_file()}, before)
        report = self.report()
        self.assertEqual([c['id'] for c in report['conditions']], adapter.CONDITIONS)
        self.assertEqual(self.calls[:3], ['copy', ('pins', 'base'), ('pins', 'final')])
        self.assertEqual([c[0] for c in self.calls[3:5]], ['lunch', 'compile'])
        tree = self.parent/'launcher3-compile-41700000000-1/tree'
        self.assertEqual((tree/L3/EDGE_FILE).read_bytes(), EDGE_AFTER)
        self.assertEqual((tree/L3/FALLBACK/'RoseEdgeState.java').read_bytes(), b'candidate state\n')
        self.assertEqual(report['classification']['branch'], 'verifyBaseApplyCandidate')

    def test_base_apply_candidate_refuses_at_the_first_failed_condition_by_name(self):
        cases = [('post', {}, 'delta-preimages', False), ('pre', {}, 'base-exact', True),
                 ('base', {'bundle': dict(self.s.bundle_content, patches=[])}, 'base-exact: the integrating bundle', True),
                 ('base', {'pins': self.pins(fail='base')}, 'extra against the tree pin', True),
                 ('base', {'pins': self.pins(fail='final')}, 'extra against the tree pin', False)]
        for state, change, word, untouched in cases:
            with self.subTest(state=state, word=word):
                self.setUp()
                self.vbac()
                if state == 'post':  # already at the candidate: the base pins pass on a fake, the preimages do not
                    self.s.manifest['base'] = {**self.s.manifest, 'bundleContent': self.s.bundle_content}
                self.assertEqual(self.run_adapter(state, **change), 3)
                report = self.report()
                self.assertIn(word, report['refused'])
                self.assertEqual(report['nothingApplied'], untouched or state == 'post')
                self.assertFalse([c for c in self.calls if c[0] == 'compile'])
        self.setUp()
        self.vbac()
        self.binding['bundleReport'] = {'path': '/bundle.json', 'sha256': 'b' * 64}
        self.assertEqual(self.run_adapter('base'), 3)
        self.assertIn('not the one bound', self.report()['refused'])

    def test_a_held_lock_refuses_before_the_copy_and_reports_are_write_once(self):
        @contextlib.contextmanager
        def held(path):
            raise adapter.Refused('lock_held: the shared builder lock is held by another job')
            yield
        self.assertEqual(self.run_adapter(lock=held), 3)
        self.assertEqual(self.calls, [])
        self.assertTrue(self.report()['refused'].startswith('lock_held'))
        with self.assertRaises(FileExistsError):
            self.run_adapter()

    def test_finish_uploads_only_the_allowlist_and_removes_only_this_runs_work(self):
        self.assertEqual(self.run_adapter(), 0)
        run_root = self.parent/'launcher3-compile-41700000000-1'
        (run_root/'receipts/extra.log').write_text('never uploaded')
        sibling = self.parent/'launcher3-compile-41700000000-2'
        (sibling/'tree').mkdir(parents=True)
        upload = Path(tempfile.mkdtemp())/'upload'
        self.assertEqual(adapter.finish(self.binding, '41700000000', '1', upload), 0)
        self.assertEqual(sorted(p.name for p in upload.iterdir()), ['classification.json', 'cleanup.json', 'report.json'])
        cleanup = json.loads((upload/'cleanup.json').read_text())
        self.assertEqual((cleanup['removed'], cleanup['left']), (['tree', 'out', 'home', 'tmp'], []))
        self.assertEqual(sorted(p.name for p in run_root.iterdir()), ['receipts'])
        self.assertTrue((sibling/'tree').is_dir())
        with self.assertRaises(FileExistsError):
            adapter.finish(self.binding, '41700000000', '1', upload)

    def test_finish_never_follows_links_and_reports_what_it_left(self):
        run_root = self.parent/'launcher3-compile-41700000000-1'
        (run_root/'receipts').mkdir(parents=True)
        outside = Path(tempfile.mkdtemp())
        (outside/'keep').write_text('x')
        os.symlink(outside, run_root/'tree')
        os.symlink(outside/'keep', run_root/'receipts/report.json')
        (run_root/'out').write_text('not a directory')
        upload = Path(tempfile.mkdtemp())/'upload'
        self.assertEqual(adapter.finish(self.binding, '41700000000', '1', upload), 3)
        cleanup = json.loads((upload/'cleanup.json').read_text())
        self.assertEqual(cleanup['uploaded'], [])
        self.assertEqual([l['path'] for l in cleanup['left']], ['tree', 'out'])
        self.assertTrue((outside/'keep').exists())
        missing = Path(tempfile.mkdtemp())/'upload'
        self.assertEqual(adapter.finish(self.binding, '41700000000', '9', missing), 0)
        self.assertEqual(json.loads((missing/'cleanup.json').read_text())['absent'], [str(self.parent/'launcher3-compile-41700000000-9')])

    def test_finish_and_run_options_are_exclusive(self):
        for argv in (['check', '--upload-dir', '/u'], ['finish', '--run-id', '1', '--attempt', '1'],
                     ['finish', '--run-id', '1', '--attempt', '1', '--upload-dir', 'rel']):
            with self.subTest(argv=argv):
                result = run_cli(*argv, '--binding', str(Contract.binding(Contract())))
                self.assertEqual(result.returncode, 2, result.stderr)

    def test_a_failed_lunch_is_reported_and_m_is_not_run(self):
        for result in ((2, None), (-15, 'deadline')):
            with self.subTest(result=result):
                self.setUp()
                self.lunch_result = result
                self.assertEqual(self.run_adapter(), 0)
                report = self.report()
                self.assertEqual((report['lunch'], report['result']), ({'exit': result[0], 'stoppedBy': result[1]}, 'incomplete'))
                self.assertNotIn('compile', report)
                self.assertFalse([c for c in self.calls if c[0] == 'compile'])

    def test_a_version_checked_lunch_is_refused_before_anything_is_copied(self):
        self.binding['lunch'] = 'frankel-cur-user'
        self.assertEqual(self.run_adapter(), 3)
        self.assertEqual(self.calls, [])
        self.assertTrue(self.report()['refused'].startswith('adevtool_git_missing'))

    def test_a_stopped_compile_is_recorded_and_never_a_pass(self):
        self.assertEqual(self.run_adapter(code=-15, stopped='memory'), 0)
        report = self.report()
        self.assertEqual((report['compile']['stoppedBy'], report['result']), ('memory', 'incomplete'))


if __name__ == '__main__':
    unittest.main()
