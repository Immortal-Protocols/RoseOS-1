"""LAUNCHER3-COMPILE: the fail-closed compile-only adapter (scripts/launcher3-compile.py) against OS Product's input
contract (os/compile-contracts/launcher3-quickstep-lib.json, scripts/launcher3-compile-contract.py).

Synthetic trees, a synthetic contract where bytes are needed, stand-in processes and fake locks only: no host, copy of
a real tree, lunch, build or network; git runs only as hash-object (no -w) and apply on synthetic temporary trees. Exercised: the pinned contract and the binding (unbound exits 4), the
branch classification (verifyIntegrated only on the exact post-state; mixed, pre, unknown and missing refuse; config.xml
only through OS Product's bound-policy check, never normalised on a mismatch), the copy bounds, the contained
environment, the sampled stops, the aconfig reader, the source and classpath proof and the write-once report.

The lunch inputs (OS Product's frankel-lunch-inputs.json, read as data at its pin) are checked on synthetic copies, and
the adevtool public fetch runs real git against a local stand-in repository over the file protocol, which only these
tests allow: the adapter's own default is https from the pinned manifest's source, and the reviewed contract admits no
public fetch yet, so the route is refused (exit 4) until OS Product's amendment and the re-pin land together."""
import contextlib
import gzip
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import unittest.mock
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
LUNCH_INPUTS = ROOT/'os/compile-contracts/frankel-lunch-inputs.json'
EMPTY_BLOB = 'e69de29bb2d1d6434b8b29ae775ad8c2e48c5391'
def amended(edit=None):
    """(path, sha256) of the pinned contract (OS Product's #531 amendment), optionally edited, in a temporary file."""
    record = json.loads(LUNCH_INPUTS.read_text())
    if edit:
        edit(record)
    target = Path(tempfile.mkdtemp())/'frankel-lunch-inputs.json'
    target.write_bytes(json.dumps(record).encode())
    return target, sha(target.read_bytes())
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

    def test_a_version_checked_lunch_is_unbound_until_adevtool_git_is_bound(self):
        for branch in ('verifyIntegrated', 'verifyBaseApplyCandidate'):
            result = run_cli('check', '--binding', str(self.binding(lunch='frankel-cur-user', branch=branch,
                             bundleReport={'path': '/home/pixelbuild/receipts/bundle.json', 'sha256': 'b' * 64})))
            self.assertEqual((result.returncode, result.stdout), (4, ''))
            self.assertIn('adevtool_git_missing', result.stderr)
        # bound to the public fetch, which the pinned contract admits: the check passes, and its bounds add the lunch
        # inputs (1800 s), the fetch (1800 s) and its kill grace (30 s) to 18090 s (19890 s for the candidate branch)
        result = run_cli('check', '--binding', str(self.binding(lunch='frankel-cur-user', adevtoolGit='public-fetch')))
        self.assertEqual((result.returncode, result.stdout), (0, 'outer_seconds=21720\nfinish_seconds=1800\njob_minutes=403\n'))
        result = run_cli('check', '--binding', str(self.binding(lunch='frankel-cur-user', adevtoolGit='public-fetch',
                         branch='verifyBaseApplyCandidate', bundleReport={'path': '/home/pixelbuild/receipts/bundle.json',
                                                                          'sha256': 'b' * 64})))
        self.assertEqual((result.returncode, result.stdout), (0, 'outer_seconds=23520\nfinish_seconds=1800\njob_minutes=433\n'))
        # a contract without the admission row stays unbound
        contract_path, pin = amended(lambda d: d['gitRequirement'].update(
            options=[o for o in d['gitRequirement']['options'] if o['class'] != 'public-fetch']))
        binding = json.loads(self.binding(lunch='frankel-cur-user', adevtoolGit='public-fetch').read_text())
        with self.assertRaisesRegex(adapter.Unbound, 'adevtool_git_unadmitted: .*https://github.com/GrapheneOS/adevtool'):
            adapter.lunch_plan(binding, ROOT, contract_path, pin)

    def test_the_admitted_plan_names_the_exact_sources_and_only_one_allowed_row_admits(self):
        binding = self.binding(lunch='frankel-cur-user', adevtoolGit='public-fetch')
        manifest, record, plan = adapter.check(binding)
        self.assertEqual(plan['url'], 'https://github.com/GrapheneOS/adevtool')
        self.assertEqual(plan['exact'], ['vendor/adevtool', 'device/google/laguna-kernels/6.6'])  # not build/make
        self.assertEqual(record['adevtoolGit'], 'public-fetch')
        # a wrong url, a status that is not allowed, or two rows: not admitted
        for change in ({'url': 'https://github.com/example/adevtool'}, {'status': 'refused'}):
            with self.subTest(change=change):
                self.assertIsNone(adapter.public_fetch_option(
                    {'gitRequirement': {'options': [{'class': 'public-fetch', 'status': 'allowed', 'url': plan['url'], **change}]}},
                    plan['url']))
        twice = [{'class': 'public-fetch', 'status': 'allowed', 'url': plan['url']}] * 2
        self.assertIsNone(adapter.public_fetch_option({'gitRequirement': {'options': twice}}, plan['url']))

    def test_os_products_amendment_is_admitted_only_as_exactly_this_adapters_mechanism(self):
        binding = json.loads(self.binding(lunch='frankel-cur-user', adevtoolGit='public-fetch').read_text())
        contract_path, pin = amended()
        self.assertEqual(adapter.lunch_plan(binding, ROOT, contract_path, pin)['url'], 'https://github.com/GrapheneOS/adevtool')
        fetch = lambda d: d['gitRequirement']['publicFetch']  # noqa: E731
        tree = lambda d: d['gitRequirement']['workingTree']  # noqa: E731
        cases = [('steps', lambda d: fetch(d)['steps'].__setitem__(1, fetch(d)['steps'][1].replace('--depth=1', '--depth=2')),
                  'steps'),
                 ('environment', lambda d: fetch(d)['environment'].update(GIT_ALLOW_PROTOCOL='https,file'), 'environment'),
                 ('extra variable', lambda d: fetch(d)['environment'].update(LD_PRELOAD='/x.so'), 'environment'),
                 ('ceiling', lambda d: fetch(d)['ceiling'].update(seconds=3600), 'ceiling'),
                 ('url', lambda d: fetch(d).update(url='https://github.com/example/adevtool'), 'url'),
                 ('omitted', lambda d: tree(d)['omittedFromCopy']['gitignore'].append('/vendor-skels/'), 'workingTree'),
                 ('untracked', lambda d: tree(d).update(untrackedAllowed=['/node_modules/']), 'workingTree'),
                 ('unknown check', lambda d: d['sources'][1].update(workingTreeCheck='sometimes'), 'workingTreeCheck'),
                 ('adevtool not exact', lambda d: d['sources'][0].update(workingTreeCheck='derivation only'), 'exactly'),
                 ('status with its own bounds', lambda d: next(o for o in d['gitRequirement']['options']
                                                              if o['class'] == 'public-fetch').update(
                     status='allowed only with a 300 s, 256 MiB bound'), 'public-fetch status'),
                 ('fabrication not carved out', lambda d: next(o for o in d['gitRequirement']['options']
                                                              if o['class'] == 'fabrication').update(
                     means='any HEAD, ref, object or gitdir written rather than copied'), 'fabrication carve-out'),
                 ('redirect allowed', lambda d: d['gitRequirement']['options'][1].update(status='allowed'), 'not refused'),
                 ('an extra option', lambda d: d['gitRequirement']['options'].append({'class': 'mirror', 'status': 'allowed'}),
                  'options are not exactly')]
        for name, edit, word in cases:
            with self.subTest(name=name):
                contract_path, pin = amended(edit)
                with self.assertRaisesRegex(adapter.Invalid, word):
                    adapter.lunch_plan(binding, ROOT, contract_path, pin)
        # the url on the option row decides the admission itself
        row = lambda d: next(o for o in d['gitRequirement']['options'] if o['class'] == 'public-fetch')  # noqa: E731
        contract_path, pin = amended(lambda d: row(d).update(url='https://example.invalid/x'))
        with self.assertRaisesRegex(adapter.Unbound, 'adevtool_git_unadmitted'):
            adapter.lunch_plan(binding, ROOT, contract_path, pin)

    def test_the_omitted_rules_drop_no_tracked_path_of_the_pinned_listing(self):
        listed = adapter.lunch_listing(adapter.load_lunch_inputs(), 'vendor/adevtool')
        dropped = [p for p in listed if any(adapter.adevtool_ignored('/'.join(os.fsdecode(p).split('/')[:i]),
                                                                    i < len(os.fsdecode(p).split('/')))
                                            for i in range(1, len(os.fsdecode(p).split('/')) + 1))]
        self.assertEqual(dropped, [])
        self.assertIn(b'dl/.find-ignore', listed)

    def test_invalid_bindings_refuse(self):
        for change in ({'host': 'other'}, {'taskParent': '/tmp'}, {'branch': 'mixed'}, {'sourceTree': 'relative'},
                       {'sourceTree': '/home/pixelbuild/rose-actions/blazer-prep-41473133543-1/source'},
                       {'sourceTree': '/home'}, {'lunch': 'frankel'}, {'policyReceipt': {'path': '/p'}},
                       {'policyReceipt': {'path': '/p', 'sha256': 'A' * 64}}, {'extra': 1}, {'schemaVersion': 1},
                       {'schemaVersion': 2}, {'adevtoolGit': 'genuine-copy'}, {'adevtoolGit': 'public-fetch'},
                       {'lunch': 'frankel-trunk-user', 'adevtoolGit': 'public-fetch'},
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
    OS Product's pinned base fixtures and the base overlay images the delta names: every final image equals the
    contract's. A base overlay comes from the first source whose sha256 is the delta's `before`: main's own os/overlays
    copy while main is still at the base, else OS Product's pinned base copy in the fixtures' layout. When neither holds
    it, the test fails; it is never skipped."""
    def base_overlay(self, target, before, root=ROOT):
        seen = {}
        for source in (root/'os/overlays'/target, root/'os/tests/fixtures/launcher3-compile-contract'/target):
            data = source.read_bytes() if source.is_file() and not source.is_symlink() else None
            if data is not None and sha(data) == before:
                return data
            seen[str(source.relative_to(root))] = None if data is None else sha(data)
        self.fail(f'no source holds the base image {before} of {target}: {seen}')

    def test_the_base_overlay_comes_from_whichever_source_holds_the_before_image(self):
        """Synthetic images in a temporary root, so this holds on any branch."""
        target = next(o for o in MANIFEST['candidateDelta']['overlays'] if o['before'] is not None)['target']
        base, candidate = b'base image\n', b'candidate image\n'
        root = Path(tempfile.mkdtemp())
        live, pinned = root/'os/overlays'/target, root/'os/tests/fixtures/launcher3-compile-contract'/target
        live.parent.mkdir(parents=True)
        live.write_bytes(base)
        self.assertEqual(self.base_overlay(target, sha(base), root), base)  # main while it is still at the base
        live.write_bytes(candidate)  # os/ at the candidate, as on #440's branch
        with self.assertRaisesRegex(AssertionError, 'no source holds the base image'):
            self.base_overlay(target, sha(base), root)
        pinned.parent.mkdir(parents=True)
        (root/'elsewhere').write_bytes(base)
        pinned.symlink_to(root/'elsewhere')  # a link is never a pinned copy
        with self.assertRaisesRegex(AssertionError, 'no source holds the base image'):
            self.base_overlay(target, sha(base), root)
        pinned.unlink()
        pinned.write_bytes(base)  # OS Product's pinned base copy
        self.assertEqual(self.base_overlay(target, sha(base), root), base)
        pinned.write_bytes(candidate)  # a copy of another image is not the base either
        with self.assertRaisesRegex(AssertionError, 'no source holds the base image'):
            self.base_overlay(target, sha(base), root)

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
                (tree/item['target']).write_bytes(self.base_overlay(item['target'], item['before']))
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
        def copy(source, destination, omit=None):
            self.calls.append('copy' if omit is None else ('copy', omit('vendor/adevtool/node_modules', True),
                                                           omit('vendor/adevtool/dl/.find-ignore', False)))
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

    def admitted(self, inputs_check=None, fetcher=None, unset=('BUILD_ID_frankel', 'TARGET_KERNEL_DIR', 'USE_STOCK_KERNEL'),
                 state='post'):
        self.binding.update(lunch='frankel-cur-user', adevtoolGit='public-fetch')
        plan = {'inputs': {'environment': {'unset': ['GIT_*', *unset]}, 'gitRequirement': {'project': 'vendor/adevtool'}},
                'url': 'https://github.com/GrapheneOS/adevtool', 'exact': ['vendor/adevtool']}

        def checks(tree, inputs, root, deadline, exact=None):
            self.calls.append(('lunch-inputs', tuple(exact)))
            return {'contractSha256': 'checked'}

        def fetch(tree, plan, run_root, record, root):
            self.calls.append('fetch')
            record.update(ok=True)

        lock = lambda path: contextlib.nullcontext()  # noqa: E731
        return adapter.run(self.binding, self.s.manifest, '41700000000', '1', root=self.s.repo, lock=lock,
                           copier=self.copier(state), compiler=self.compiler(), pins=self.pins(),
                           policy_loader=lambda path: (POLICY, self.s.policy_sha),
                           bundle_loader=lambda path: (self.s.bundle_content, self.bundle_sha), plan=plan,
                           inputs_check=inputs_check or checks, fetcher=fetcher or fetch)

    def test_an_admitted_lunch_checks_its_inputs_and_fetches_after_the_branch_and_before_lunch(self):
        self.assertEqual(self.admitted(), 0)
        # the copy omits node_modules but keeps the tracked dl/.find-ignore; the checks get the plan's exact sources
        self.assertEqual(self.calls[:4], [('copy', True, False), ('pins', 'final'), ('lunch-inputs', ('vendor/adevtool',)),
                                          'fetch'])
        self.assertEqual([c[0] for c in self.calls[4:6]], ['lunch', 'compile'])
        report = self.report()
        self.assertEqual((report['lunchInputs'], report['adevtoolGit']),
                         ({'contractSha256': 'checked', 'bounds': {'seconds': adapter.LUNCH_INPUT_SECONDS}}, {'ok': True}))

    def test_an_environment_the_contract_unsets_refuses_before_any_lunch_input_or_fetch(self):
        self.assertEqual(self.admitted(unset=('TMPDIR',)), 3)  # the contained environment does set TMPDIR
        self.assertEqual(self.calls, [('copy', True, False), ('pins', 'final')])
        self.assertEqual(self.report()['refused'], 'lunch-inputs: the lunch environment passes TMPDIR')

    def test_an_os_error_reading_the_copy_is_a_named_refusal_before_lunch(self):
        def unreadable(*args, **kwargs):
            raise PermissionError('denied')
        self.assertEqual(self.admitted(inputs_check=unreadable), 3)
        self.assertEqual(self.report()['refused'], 'lunch-inputs: PermissionError while reading the copy')
        self.assertFalse([c for c in self.calls if c[0] in ('lunch', 'compile')])

    def test_the_candidate_branch_also_checks_its_inputs_and_fetches_after_its_conditions(self):
        self.vbac()
        self.assertEqual(self.admitted(state='base'), 0)
        self.assertEqual(self.calls[:5], [('copy', True, False), ('pins', 'base'), ('pins', 'final'),
                                          ('lunch-inputs', ('vendor/adevtool',)), 'fetch'])
        self.assertEqual([c[0] for c in self.calls[5:7]], ['lunch', 'compile'])

    def test_a_refused_lunch_input_or_fetch_stops_before_lunch(self):
        def refuse(word):
            def fail(*args, **kwargs):
                self.calls.append(word)
                raise adapter.Refused(f'{word}: refused here')
            return fail
        for name, change in (('lunch-inputs', {'inputs_check': refuse('lunch-inputs')}),
                             ('adevtool-git', {'fetcher': refuse('adevtool-git')})):
            with self.subTest(name=name):
                self.setUp()
                self.assertEqual(self.admitted(**change), 3)
                self.assertEqual(self.calls[-1], name)
                self.assertFalse([c for c in self.calls if c[0] in ('lunch', 'compile')])
                self.assertTrue(self.report()['refused'].startswith(name))
        self.setUp()
        self.binding.update(lunch='frankel-cur-user', adevtoolGit=None)
        lock = lambda path: contextlib.nullcontext()  # noqa: E731
        self.assertEqual(adapter.run(self.binding, self.s.manifest, '41700000000', '1', root=self.s.repo, lock=lock,
                                     copier=self.copier(), compiler=self.compiler(), pins=self.pins(),
                                     plan={'inputs': {}, 'url': 'https://x'}), 3)
        self.assertEqual(self.calls, [])  # a plan without the bound fetch: refused before anything is copied

    def test_a_stopped_compile_is_recorded_and_never_a_pass(self):
        self.assertEqual(self.run_adapter(code=-15, stopped='memory'), 0)
        report = self.report()
        self.assertEqual((report['compile']['stoppedBy'], report['result']), ('memory', 'incomplete'))


ADEVTOOL_FILES = {'README.md': (b'adevtool\n', 0o644), 'dl/.find-ignore': (b'', 0o644),
                  'vendor-skels/.find-ignore': (b'', 0o644), 'bin/run': (b'#!/bin/sh\n', 0o755),
                  'vendor-specs/google_devices/frankel.yml': (b'spec\n', 0o644)}
VERSION_ERROR = b'ifneq ($(shell git -C vendor/adevtool rev-parse HEAD),REV)\n  $(error outdated)\nendif\n'


class LunchFixture:
    """A synthetic copy holding every lunch input, a repository root with the pinned listing, a synthetic contract in
    the reviewed contract's shape, and a local upstream repository standing in for the public adevtool source."""

    def __init__(self):
        self.base = Path(tempfile.mkdtemp())
        self.upstream, self.repo, self.tree = self.base/'upstream', self.base/'repo', self.base/'run/tree'
        self.upstream.mkdir()
        git('init', '-q', str(self.upstream), cwd=self.base)
        self.write_files(self.upstream, ADEVTOOL_FILES)
        git('add', '-A', cwd=self.upstream)
        git('commit', '-qm', 'first', cwd=self.upstream)
        (self.upstream/'README.md').write_bytes(b'adevtool pinned\n')
        git('add', '-A', cwd=self.upstream)
        git('commit', '-qm', 'pinned', cwd=self.upstream)
        self.revision = git('rev-parse', 'HEAD', cwd=self.upstream).decode().strip()
        self.root_tree = git('rev-parse', 'HEAD^{tree}', cwd=self.upstream).decode().strip()
        self.files = dict(ADEVTOOL_FILES, **{'README.md': (b'adevtool pinned\n', 0o644)})
        raw = git('ls-tree', '-r', self.revision, cwd=self.upstream)
        self.listing_path = 'fixtures/adevtool.ls-tree.txt.gz'
        self.inputs = self.contract(raw)
        self.make_copy()

    @staticmethod
    def write_files(base, files):
        for relative, (data, mode) in files.items():
            target = Path(base)/relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            os.chmod(target, mode)

    def listing(self, raw, name):
        (self.repo/'fixtures').mkdir(parents=True, exist_ok=True)
        packed = gzip.compress(raw, mtime=0)
        relative = f'fixtures/{name}.ls-tree.txt.gz'
        (self.repo/relative).write_bytes(packed)
        return {'path': relative, 'sha256': sha(raw), 'compressedSha256': sha(packed), 'paths': raw.count(b'\n')}

    def contract(self, raw):
        listing = self.listing(raw, 'adevtool')
        self.raw = raw
        kernel = b'module_a.ko\n'
        return {'schemaVersion': 1, 'kind': 'rose-frankel-lunch-inputs',
                'lunch': {'target': 'frankel-cur-user', 'product': 'frankel', 'release': 'cur', 'variant': 'user'},
                'sources': [{'path': 'vendor/adevtool', 'revision': self.revision, 'rootTree': self.root_tree,
                             'listing': listing, 'workingTreeCheck': 'exact: gitRequirement.workingTree'},
                            {'path': 'device/kernels', 'revision': '2' * 40, 'rootTree': '3' * 40,
                             'listing': self.listing(b'100755 blob %s\tmuzel/vendor_dlkm.modules.load\n' % blob(kernel).encode(),
                                                     'kernels'), 'workingTreeCheck': 'exact: untouched'},
                            {'path': 'build/make', 'revision': '4' * 40, 'rootTree': '5' * 40,
                             'listing': self.listing(b'100644 blob %s\tcore/main.mk\n' % (b'0' * 40), 'make'),
                             'workingTreeCheck': 'derivation only: patched by integration'}],
                'gitRequirement': {'project': 'vendor/adevtool', 'revision': self.revision, 'rootTree': self.root_tree,
                                   'path': 'vendor/adevtool/.git', 'options': []},
                'environment': {'unset': ['GIT_*', 'BUILD_ID_frankel', 'TARGET_KERNEL_DIR', 'USE_STOCK_KERNEL']},
                'versionCheck': {'path': 'vendor/google_devices/frankel/adevtool-version-check.mk',
                                 'expected': {'error': {'sha256': sha(VERSION_ERROR), 'bytes': len(VERSION_ERROR),
                                                        'gitBlob': blob(VERSION_ERROR)}}},
                'generatedVendor': {'observable': [{'path': 'vendor/google_devices/frankel/frankel.mk',
                                                    'sha256': sha(b'frankel product\n')}]},
                'finderPrune': {'markers': [{'path': 'vendor/adevtool/dl/.find-ignore', 'gitBlob': EMPTY_BLOB},
                                            {'path': 'vendor/adevtool/vendor-skels/.find-ignore', 'gitBlob': EMPTY_BLOB}]},
                'kernelModuleLists': {'files': [{'path': 'device/kernels/muzel/vendor_dlkm.modules.load', 'mode': '100755',
                                                 'gitBlob': blob(kernel)}]},
                'manifestFiles': [{'kind': 'linkfile', 'project': 'build/make', 'src': 'core', 'dest': 'build/core'},
                                  {'kind': 'copyfile', 'project': 'trusty/vendor/google/aosp', 'src': 'lk_inc.mk',
                                   'dest': 'lk_inc.mk'}]}

    def make_copy(self):
        if self.tree.exists():
            shutil.rmtree(self.tree.parent)
        (self.tree.parent/'home').mkdir(parents=True)
        self.write_files(self.tree/'vendor/adevtool', self.files)
        self.write_files(self.tree, {'vendor/google_devices/frankel/adevtool-version-check.mk': (VERSION_ERROR, 0o644),
                                     'vendor/google_devices/frankel/frankel.mk': (b'frankel product\n', 0o644),
                                     'device/kernels/muzel/vendor_dlkm.modules.load': (b'module_a.ko\n', 0o755),
                                     'build/make/core/main.mk': (b'main\n', 0o644),
                                     'trusty/vendor/google/aosp/lk_inc.mk': (b'lk\n', 0o644), 'lk_inc.mk': (b'lk\n', 0o644)})
        os.symlink('make/core', self.tree/'build/core')

    def plan(self):
        return {'inputs': self.inputs, 'url': 'file://' + str(self.upstream)}

    def work_state(self):
        work = self.tree/'vendor/adevtool'
        return {str(p.relative_to(work)): (p.read_bytes(), p.stat().st_mode) for p in work.rglob('*')
                if p.is_file() and '.git' not in p.relative_to(work).parts}



class ManifestProject(unittest.TestCase):
    def test_only_a_single_full_commit_on_an_https_remote_is_a_source(self):
        good = ('<manifest><remote name="r" fetch="https://example.invalid/"/><default remote="r"/>'
                '<project name="tool" path="vendor/tool" revision="{rev}"/>{extra}</manifest>')
        root = Path(tempfile.mkdtemp())
        (root/'os').mkdir()
        for name, text, ok in (('ok', good.format(rev='a' * 40, extra=''), True),
                               ('http', good.format(rev='a' * 40, extra='').replace('https://', 'http://'), False),
                               ('short', good.format(rev='a' * 12, extra=''), False),
                               ('twice', good.format(rev='a' * 40, extra='<project name="t2" path="vendor/tool" revision="'
                                                     + 'b' * 40 + '"/>'), False)):
            with self.subTest(name=name):
                (root/'os/pinned-manifest.xml').write_text(text)
                if ok:
                    self.assertEqual(adapter.manifest_project('vendor/tool', root),
                                     {'revision': 'a' * 40, 'url': 'https://example.invalid/tool'})
                else:
                    with self.assertRaises(adapter.Invalid):
                        adapter.manifest_project('vendor/tool', root)


class LunchInputs(unittest.TestCase):
    def test_the_pinned_lunch_inputs_are_os_products_reviewed_contract(self):
        self.assertEqual(sha(LUNCH_INPUTS.read_bytes()), adapter.LUNCH_INPUTS_SHA256)
        inputs = adapter.load_lunch_inputs()
        url = adapter.manifest_project('vendor/adevtool')
        self.assertEqual(url, {'revision': inputs['gitRequirement']['revision'], 'url': 'https://github.com/GrapheneOS/adevtool'})
        self.assertEqual(adapter.public_fetch_option(inputs, url['url'])['class'], 'public-fetch')  # #531 admits it
        self.assertEqual(adapter.admission_problems(inputs, url['url']), [])  # exactly this adapter's mechanism
        self.assertEqual(inputs['gitRequirement']['workingTree']['omittedFromCopy']['gitignore'], list(adapter.ADEVTOOL_IGNORED))
        self.assertEqual(len(adapter.lunch_listing(inputs, 'vendor/adevtool')), 55890)
        changed = Path(tempfile.mkdtemp())/'frankel-lunch-inputs.json'
        changed.write_bytes(LUNCH_INPUTS.read_bytes().replace(b'frankel-cur-user', b'frankel-cur-eng', 1))
        with self.assertRaisesRegex(adapter.Invalid, 'reviewed lunch-input contract'):
            adapter.load_lunch_inputs(changed)

    def test_the_contract_identity_must_agree_with_itself_and_the_manifest(self):
        base = json.loads(LUNCH_INPUTS.read_text())
        for name, edit in (('revision', lambda d: d['gitRequirement'].update(revision='0' * 40)),
                           ('tree', lambda d: d['sources'][0].update(rootTree='0' * 40)),
                           ('path', lambda d: d['gitRequirement'].update(path='vendor/adevtool/.gitfile')),
                           ('env', lambda d: d['environment']['unset'].remove('GIT_*')),
                           ('kind', lambda d: d['manifestFiles'][0].update(kind='hardlink'))):
            with self.subTest(name=name):
                record = json.loads(json.dumps(base))
                edit(record)
                target = Path(tempfile.mkdtemp())/'inputs.json'
                target.write_bytes(json.dumps(record).encode())
                with self.assertRaises(adapter.Invalid):
                    adapter.load_lunch_inputs(target, sha(target.read_bytes()))

    def test_the_lunch_environment_passes_nothing_the_contract_unsets(self):
        inputs = adapter.load_lunch_inputs()
        env = adapter.contained_env(Path('/run'))
        self.assertEqual(adapter.environment_problems(env, inputs), [])
        self.assertEqual(sorted(n for n in env if n.startswith('GIT_')), sorted(adapter.CONTAINED_GIT))
        for name in ('BUILD_ID_frankel', 'TARGET_KERNEL_DIR', 'USE_STOCK_KERNEL', 'GIT_DIR', 'GIT_ALTERNATE_OBJECT_DIRECTORIES'):
            with self.subTest(name=name):
                self.assertEqual(adapter.environment_problems({**env, name: 'x'}, inputs), [name])


class LunchChecks(unittest.TestCase):
    def setUp(self):
        self.f = LunchFixture()

    def check(self, exact=None):
        return adapter.lunch_input_checks(self.f.tree, self.f.inputs, root=self.f.repo, exact=exact)

    def test_an_unlisted_file_is_named_without_reading_its_bytes(self):
        big = self.f.tree/'vendor/adevtool/huge.bin'
        big.write_bytes(b'never read')
        seen = []

        def hasher(data):
            seen.append(data)
            return adapter.git_blob_id(data)
        with self.assertRaisesRegex(adapter.Refused, 'unlisted 1: huge.bin'):
            adapter.exact_tree(self.f.tree/'vendor/adevtool', adapter.lunch_listing(self.f.inputs, 'vendor/adevtool',
                                                                                     self.f.repo), (), hasher)
        self.assertNotIn(b'never read', seen)
        self.assertEqual(len(seen), len(self.f.files))  # only the listed entries were hashed

    def test_the_kernel_module_lists_are_checked_even_where_their_tree_is_not_exact(self):
        for name, change in (('mode', lambda: os.chmod(self.f.tree/'device/kernels/muzel/vendor_dlkm.modules.load', 0o644)),
                             ('empty', lambda: (self.f.tree/'device/kernels/muzel/vendor_dlkm.modules.load').write_bytes(b''))):
            with self.subTest(name=name):
                self.f.make_copy()
                change()
                with self.assertRaisesRegex(adapter.Refused, 'the kernel module list device/kernels/muzel/vendor_dlkm'):
                    self.check(exact=['vendor/adevtool'])

    def test_an_exact_copy_passes_every_check_and_nothing_is_written(self):
        before = self.f.work_state()
        result = self.check()
        self.assertEqual(result['exactTrees'], {'vendor/adevtool': {'paths': len(self.f.files), 'equalsListing': True},
                                                'device/kernels': {'paths': 1, 'equalsListing': True}})
        # build/make is derivation only: its listing (a wrong blob here) is never compared
        self.assertEqual((result['observable'], result['finderMarkers'], result['kernelModuleLists'], result['manifestFiles']),
                         (1, 2, 1, 2))
        self.assertEqual(self.f.work_state(), before)
        self.assertNotIn(self.f.revision, json.dumps(result))  # no expected value is copied into the report

    def test_each_difference_refuses_by_name(self):
        t = self.f.tree
        cases = [
            ('untracked', lambda: (t/'vendor/adevtool/node_modules').mkdir() or (t/'vendor/adevtool/node_modules/x').write_bytes(b'x'),
             r'unlisted 1: node_modules/$|unlisted 1: node_modules/\)'),
            ('empty directory', lambda: (t/'vendor/adevtool/vendor-specs/google_devices/frankel2').mkdir(parents=True),
             r'unlisted 1: vendor-specs/google_devices/frankel2/'),
            ('dangling link', lambda: shutil.rmtree(t/'build/make/core'), 'build/core dangles'),
            ('absolute link inside', lambda: None, None),
            ('edited spec', lambda: (t/'vendor/adevtool/vendor-specs/google_devices/frankel.yml').write_bytes(b'edited\n'),
             r'different 1: vendor-specs/google_devices/frankel.yml'),
            ('removed', lambda: (t/'vendor/adevtool/README.md').unlink(), r'missing 1: README.md'),
            ('mode', lambda: os.chmod(t/'vendor/adevtool/bin/run', 0o644), r'different 1: bin/run'),
            ('kernel tree extra', lambda: (t/'device/kernels/muzel/extra.ko').write_bytes(b'x'),
             r'device/kernels: kernels is not exactly its pinned tree \(unlisted 1: muzel/extra.ko\)'),
            ('git present', lambda: (t/'vendor/adevtool/.git').mkdir(), 'no .git before the fetch'),
            ('warning form', lambda: (t/'vendor/google_devices/frankel/adevtool-version-check.mk').write_bytes(
                VERSION_ERROR.replace(b'error', b'warning')), 'not the error form'),
            ('observable', lambda: (t/'vendor/google_devices/frankel/frankel.mk').write_bytes(b'other\n'),
             'generated vendor file'),
            # the kernel directory is an exact source, so its own tree check names a changed list first
            ('kernel mode', lambda: os.chmod(t/'device/kernels/muzel/vendor_dlkm.modules.load', 0o644),
             'different 1: muzel/vendor_dlkm.modules.load'),
            ('kernel empty', lambda: (t/'device/kernels/muzel/vendor_dlkm.modules.load').write_bytes(b''),
             'different 1: muzel/vendor_dlkm.modules.load'),
            ('link elsewhere', lambda: (t/'build/core').unlink() or os.symlink('/elsewhere/core', t/'build/core'),
             'does not link'),
            ('link a directory', lambda: (t/'build/core').unlink() or (t/'build/core').mkdir(), 'not the manifest link'),
            ('copy changed', lambda: (t/'lk_inc.mk').write_bytes(b'other\n'), 'not the manifest copy'),
            ('linked parent', lambda: (shutil.move(str(t/'vendor/google_devices'), str(t/'elsewhere')),
                                       os.symlink(t/'elsewhere', t/'vendor/google_devices')), 'not the error form')]
        for name, change, word in cases:
            with self.subTest(name=name):
                self.f.make_copy()
                change()
                if word is None:  # the copier's rebased form: an absolute link to the same source inside the copy
                    (t/'build/core').unlink()
                    os.symlink(t/'build/make/core', t/'build/core')
                    self.check()
                    continue
                with self.assertRaisesRegex(adapter.Refused, word):
                    self.check()
        self.f.make_copy()  # every difference is named, each list bounded
        for i in range(adapter.REPORT_ERRORS + 5):
            (t/f'vendor/adevtool/extra-{i:02}').write_bytes(b'x')
        (t/'vendor/adevtool/README.md').write_bytes(b'changed\n')
        with self.assertRaises(adapter.Refused) as caught:
            self.check()
        message = str(caught.exception)
        self.assertIn(f'different 1: README.md; unlisted {adapter.REPORT_ERRORS + 5}: extra-00', message)
        self.assertIn(f'extra-{adapter.REPORT_ERRORS - 1:02}', message)
        self.assertNotIn(f'extra-{adapter.REPORT_ERRORS:02}', message)
        self.f.make_copy()
        marker = dict(self.f.inputs['finderPrune']['markers'][0], path='vendor/adevtool/missing/.find-ignore')
        self.f.inputs['finderPrune']['markers'].append(marker)
        with self.assertRaisesRegex(adapter.Refused, 'finder marker'):
            self.check()


class Omitted(unittest.TestCase):
    def test_the_rules_are_git_check_ignore_of_the_pinned_gitignore(self):
        repo = Path(tempfile.mkdtemp())
        git('init', '-q', str(repo), cwd=repo)
        pinned = (ROOT/'os/tests/fixtures/frankel-lunch-inputs/vendor/adevtool/.gitignore').read_bytes()
        rules = adapter.load_lunch_inputs()['gitRequirement']['workingTree']['omittedFromCopy']
        self.assertEqual((pinned, blob(pinned)), (('\n'.join(adapter.ADEVTOOL_IGNORED) + '\n').encode(), rules['gitBlob']))
        (repo/'.gitignore').write_bytes(pinned)
        cases = {'.idea': True, '.idea/workspace.xml': False, 'dist': True, 'dist/a.js': False, 'lib': True,
                 'lib/x/y.js': False, 'node_modules': True, 'node_modules/p/index.js': False, 'dl': True,
                 'dl/.find-ignore': False, 'dl/frankel-factory.zip': False, 'dl/unpacked': True, 'dl/unpacked/a/b': False,
                 'src/lib': True, 'src/lib/x.ts': False, 'a/node_modules': True, 'dl2': True, 'dist.txt': False,
                 'distfile': False, 'vendor-skels/.find-ignore': False, 'README.md': False}
        for relative, is_dir in cases.items():
            target = repo/relative
            if is_dir:
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b'')
        for relative, is_dir in cases.items():
            with self.subTest(path=relative):
                ignored = subprocess.run(['git', '-C', str(repo), 'check-ignore', '-q', '--no-index', relative],
                                         capture_output=True, env=dict(os.environ, GIT_CONFIG_NOSYSTEM='1',
                                                                       GIT_CONFIG_GLOBAL='/dev/null')).returncode == 0
                parts = relative.split('/')
                ours = any(adapter.adevtool_ignored('/'.join(parts[:i]), i < len(parts) or is_dir)
                           for i in range(1, len(parts) + 1))
                self.assertEqual(ours, ignored)

    def test_the_copy_leaves_out_exactly_the_omitted_paths_before_its_bounds(self):
        entries = [{'path': p, 'type': t, 'size': s} for p, t, s in (
            ('vendor', 'dir', 0), ('vendor/adevtool', 'dir', 0), ('vendor/adevtool/README.md', 'file', 3),
            ('vendor/adevtool/node_modules', 'dir', 0), ('vendor/adevtool/node_modules/big.js', 'file', 10 * GIB),
            ('vendor/adevtool/dl', 'dir', 0), ('vendor/adevtool/dl/.find-ignore', 'file', 0),
            ('vendor/adevtool/dl/image.zip', 'file', 5 * GIB), ('src/lib', 'dir', 0), ('src/lib/x.ts', 'file', 1))]
        planned = {'entries': entries, 'excluded': [], 'problems': []}
        copied = {}

        def copier(source, tree, final, plan):
            copied['paths'] = [e['path'] for e in plan['entries']]
        fs = type('fs', (), {'f_bavail': 1 << 40, 'f_frsize': 1})
        project = 'vendor/adevtool/'
        omit = lambda rel, is_dir: rel.startswith(project) and adapter.adevtool_ignored(rel[len(project):], is_dir)  # noqa: E731
        result = adapter.copy_tree(Path('/src'), Path(tempfile.mkdtemp())/'tree', planner=lambda s, p: planned,
                                   copier=copier, statvfs=lambda p: fs, omit=omit)
        self.assertEqual(copied['paths'], ['vendor', 'vendor/adevtool', 'vendor/adevtool/README.md', 'vendor/adevtool/dl',
                                           'vendor/adevtool/dl/.find-ignore', 'src/lib', 'src/lib/x.ts'])
        self.assertEqual((result['omitted'], result['projectedBytes']), (3, 4))  # the omitted 15 GiB never counts
        linked = dict(planned, entries=entries + [{'path': 'tools/js', 'type': 'link', 'text': '../vendor/adevtool/node_modules',
                                                   'kind': 'relative', 'target': 'vendor/adevtool/node_modules', 'exists': True}])
        with self.assertRaisesRegex(adapter.Refused, 'copy_refused: 1 problems, first: tools/js links into the omitted'):
            adapter.copy_tree(Path('/src'), Path(tempfile.mkdtemp())/'tree', planner=lambda s, p: linked, copier=copier,
                              statvfs=lambda p: fs, omit=omit)


class Fetch(unittest.TestCase):
    def setUp(self):
        self.f = LunchFixture()
        self.record = {}

    def fetch(self, plan=None, **options):
        return adapter.adevtool_public_fetch(self.f.tree, plan or self.f.plan(), self.f.tree.parent, self.record,
                                             root=self.f.repo, protocol=options.pop('protocol', 'file'), **options)

    def test_the_admitted_fetch_makes_exactly_the_pinned_commit_and_nothing_else(self):
        before = self.f.work_state()
        self.fetch()
        dotgit = self.f.tree/'vendor/adevtool/.git'
        self.assertEqual((self.record['ok'], self.record['observedHead']), (True, self.f.revision))
        self.assertEqual((dotgit/'HEAD').read_bytes(), (self.f.revision + '\n').encode())
        self.assertEqual((dotgit/'shallow').read_bytes(), (self.f.revision + '\n').encode())
        self.assertFalse((dotgit/'hooks').exists() or (dotgit/'objects/info/alternates').exists())
        self.assertEqual(self.f.work_state(), before)  # no command checks out or writes the working tree
        self.assertLessEqual(self.record['gitDirBytes'], adapter.ADEVTOOL_GIT_BYTES)
        # in the contained environment, git in the copy answers exactly what the version check reads
        env = adapter.contained_env(self.f.tree.parent)
        head = subprocess.run(['git', '-C', str(self.f.tree/'vendor/adevtool'), 'rev-parse', 'HEAD'], env=env,
                              capture_output=True, text=True)
        self.assertEqual(head.stdout.strip(), self.f.revision)
        self.assertTrue(self.record['workingTreeHolds'])
        self.assertTrue(self.record['git']['fetch']['path'].startswith(('/usr/bin/', '/bin/')))
        self.assertTrue(self.record['git']['fetch']['version'].startswith('git version'))
        self.assertEqual(self.problems(), [])

    def problems(self):
        return adapter.gitdir_problems(self.f.tree, self.f.tree/'vendor/adevtool', self.f.inputs, self.f.raw,
                                       adapter.fetch_env(self.f.tree, self.f.tree.parent/'home', 'file'),
                                       adapter.contained_env(self.f.tree.parent), time.monotonic() + 60,
                                       adapter.ADEVTOOL_GIT_BYTES)

    def test_a_failed_fetch_or_check_removes_only_the_git_it_created(self):
        other = json.loads(json.dumps(self.f.inputs))
        other['gitRequirement']['rootTree'] = other['sources'][0]['rootTree'] = '0' * 40
        unknown = json.loads(json.dumps(self.f.inputs))
        unknown['gitRequirement']['revision'] = unknown['sources'][0]['revision'] = '1' * 40
        cases = [({'plan': {'inputs': other, 'url': self.f.plan()['url']}}, 'the commit tree is not the pinned rootTree', True),
                 ({'plan': {'inputs': unknown, 'url': self.f.plan()['url']}}, 'fetch failed', True),
                 ({'limit': 64}, 'init failed: gitBytes', True),
                 ({'seconds': 0}, 'init failed: timeout', None)]  # the deadline passed before git started: nothing made
        for options, word, removed in cases:
            with self.subTest(word=word):
                self.f.make_copy()
                self.record = {}
                with self.assertRaisesRegex(adapter.Refused, word):
                    self.fetch(**options)
                self.assertFalse(os.path.lexists(self.f.tree/'vendor/adevtool/.git'))
                self.assertEqual((self.record['ok'], self.record['createdRemoved']), (False, removed))

    def test_a_listing_that_is_not_the_commit_tree_refuses(self):
        raw = git('ls-tree', '-r', self.f.revision, cwd=self.f.upstream)
        self.f.inputs = self.f.contract(raw.replace(b'README.md', b'README.txt'))
        with self.assertRaisesRegex(adapter.Refused, 'not byte for byte the pinned listing'):
            self.fetch()
        self.assertFalse(os.path.lexists(self.f.tree/'vendor/adevtool/.git'))

    def test_nothing_is_written_through_a_linked_parent_and_any_other_failure_removes_the_git(self):
        outside = self.f.base/'outside'
        shutil.move(str(self.f.tree/'vendor'), str(outside))
        os.symlink(outside, self.f.tree/'vendor')
        with self.assertRaisesRegex(adapter.Refused, 'vendor is a link'):
            self.fetch()
        self.assertFalse(os.path.lexists(outside/'adevtool/.git'))
        self.f.make_copy()
        self.record = {}
        real = subprocess.Popen

        def popen(argv, **kwargs):
            if 'fetch' in argv:
                raise FileNotFoundError('git vanished')
            return real(argv, **kwargs)
        with self.assertRaisesRegex(adapter.Refused, 'FileNotFoundError during the fetch or its checks'):
            self.fetch(popen=popen)
        self.assertFalse(os.path.lexists(self.f.tree/'vendor/adevtool/.git'))
        self.assertEqual((self.record['ok'], self.record['createdRemoved']), (False, True))

    def test_nothing_runs_for_another_protocol_or_an_existing_git(self):
        with self.assertRaisesRegex(adapter.Refused, 'is not https'):
            self.fetch(protocol='https')
        self.assertFalse(os.path.lexists(self.f.tree/'vendor/adevtool/.git'))
        (self.f.tree/'vendor/adevtool/.git').mkdir()
        (self.f.tree/'vendor/adevtool/.git/HEAD').write_text('not ours\n')
        with self.assertRaisesRegex(adapter.Refused, 'no .git before the fetch'):
            self.fetch()
        self.assertEqual((self.f.tree/'vendor/adevtool/.git/HEAD').read_text(), 'not ours\n')  # never removed

    def test_every_redirect_or_change_in_the_new_git_is_a_problem(self):
        self.fetch()
        work, dotgit = self.f.tree/'vendor/adevtool', self.f.tree/'vendor/adevtool/.git'
        pristine = self.f.base/'pristine-git'
        shutil.copytree(dotgit, pristine, symlinks=True)
        outside = self.f.base/'outside'
        outside.write_bytes(b'x')
        cases = [('alternates', lambda: (dotgit/'objects/info').mkdir(exist_ok=True) or
                  (dotgit/'objects/info/alternates').write_text('/elsewhere/objects\n'), 'objects/info/alternates exists'),
                 ('commondir', lambda: (dotgit/'commondir').write_text('/elsewhere\n'), 'commondir exists'),
                 ('hooks', lambda: (dotgit/'hooks').mkdir(), '.git/hooks exists'),
                 ('link', lambda: os.symlink('/elsewhere', dotgit/'refs/elsewhere'), 'a link is inside the .git'),
                 ('hard link', lambda: os.link(outside, dotgit/'refs/shared'), 'second hard link'),
                 ('config', lambda: git('config', '--local', 'core.hooksPath', '/elsewhere', cwd=work),
                  'configuration is not exactly'),
                 ('remote', lambda: git('remote', 'add', 'origin', 'https://example.invalid/x', cwd=work),
                  'configuration is not exactly'),
                 ('symbolic head', lambda: (dotgit/'HEAD').write_text('ref: refs/heads/main\n'), 'HEAD is not exactly'),
                 ('branch', lambda: git('update-ref', 'refs/heads/main', self.f.revision, cwd=work), 'a ref other than HEAD'),
                 ('tag', lambda: git('update-ref', 'refs/tags/v1', self.f.revision, cwd=work), 'a ref other than HEAD'),
                 ('packed refs', lambda: (dotgit/'packed-refs').write_text(f'{self.f.revision} refs/heads/x\n'),
                  'a ref other than HEAD'),
                 ('not shallow', lambda: (dotgit/'shallow').unlink(), 'shallow is not exactly')]
        for name, change, word in cases:
            with self.subTest(name=name):
                shutil.rmtree(dotgit)
                shutil.copytree(pristine, dotgit, symlinks=True)
                change()
                problems = self.problems()
                self.assertTrue(any(word in p for p in problems), problems)
        shutil.rmtree(dotgit)
        dotgit.write_text(f'gitdir: {pristine}\n')  # a gitfile pointing outside
        self.assertEqual(adapter.gitdir_problems(self.f.tree, work, self.f.inputs, b'', {}, {}, time.monotonic() + 60, 1),
                         ['vendor/adevtool/.git is not a directory'])

    def test_a_working_tree_that_no_longer_holds_after_the_fetch_refuses_and_removes_the_git(self):
        def hasher(data):
            return adapter.git_blob_id(data) if data != b'adevtool pinned\n' else '0' * 40
        with self.assertRaisesRegex(adapter.Refused, r'working tree no longer holds: .*different 1: README.md'):
            self.fetch(hasher=hasher)
        self.assertFalse(os.path.lexists(self.f.tree/'vendor/adevtool/.git'))
        self.assertEqual(self.record['createdRemoved'], True)

    def test_the_steps_are_os_products_and_blazers(self):
        revision = '17df3b79a73e67ad68387b35c7d41c85e1d8103c'
        fetch = adapter.load_lunch_inputs()['gitRequirement']['publicFetch']
        self.assertEqual([' '.join(s) for s in adapter.fetch_steps('<copy>/vendor/adevtool', '<url>', revision)], fetch['steps'])
        spec = importlib.util.spec_from_file_location('blazer_build_only', ROOT/'scripts/blazer-build-only.py')
        blazer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(blazer)
        self.assertEqual(adapter.fetch_steps('/w', 'https://u', revision), blazer.git_meta_argv('/w', 'https://u', revision))
        self.assertEqual(adapter.FETCH_ENV_SPEC, fetch['environment'])

    def test_the_fetch_environment_inherits_no_git_and_only_the_source_protocol(self):
        with unittest.mock.patch.dict(os.environ, {'GIT_DIR': '/elsewhere', 'GIT_CONFIG_PARAMETERS': "'core.x=1'",
                                                   'HTTPS_PROXY': 'http://proxy.invalid:3128', 'LD_PRELOAD': '/x.so',
                                                   'OPENSSL_CONF': '/x.cnf'}):
            env = adapter.fetch_env(Path('/run/tree'), Path('/run/home'))
        self.assertEqual(sorted(k for k in env if k.startswith('GIT_')),
                         sorted(k for k in adapter.FETCH_ENV_SPEC if k.startswith('GIT_')))
        self.assertEqual((env['HOME'], env['XDG_CONFIG_HOME']), ('/run/home', '/run/home'))
        self.assertNotIn('GIT_DIR', env)
        self.assertFalse({'LD_PRELOAD', 'OPENSSL_CONF'} & set(env))  # only the job's network setup is kept
        self.assertNotIn('GIT_CONFIG_PARAMETERS', env)
        self.assertEqual(env['HTTPS_PROXY'], 'http://proxy.invalid:3128')  # the job's network setup is kept
        self.assertEqual((env['GIT_ALLOW_PROTOCOL'], env['GIT_CEILING_DIRECTORIES'], env['PATH'], env['GIT_CONFIG_GLOBAL']),
                         ('https', '/run/tree', '/usr/bin:/bin', os.devnull))
        self.assertEqual((env['GIT_TERMINAL_PROMPT'], env['GIT_ASKPASS'], env['GIT_CONFIG_NOSYSTEM']), ('0', '/bin/false', '1'))

    def test_bounded_git_stops_at_its_deadline_or_its_bytes(self):
        clock = time.monotonic
        code, stopped = adapter.bounded_git(['sleep', '30'], dict(os.environ), '/', clock() - 1, self.f.base, 1 << 30)
        self.assertEqual(stopped, 'timeout')
        code, stopped = adapter.bounded_git(['sleep', '30'], dict(os.environ), '/', clock() + 60, self.f.base, 10,
                                            measure=lambda path: 11)
        self.assertEqual(stopped, 'gitBytes')
        self.assertEqual(adapter.bounded_git(['true'], dict(os.environ), '/', clock() + 60, self.f.base, 10,
                                             measure=lambda path: 0), (0, None))


if __name__ == '__main__':
    unittest.main()
