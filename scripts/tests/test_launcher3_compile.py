"""LAUNCHER3-COMPILE: the fail-closed compile-only adapter (scripts/launcher3-compile.py) against OS Product's input
contract (os/compile-contracts/launcher3-quickstep-lib.json, scripts/launcher3-compile-contract.py).

Synthetic trees, a synthetic contract where bytes are needed, stand-in processes and fake locks only: no host, copy of
a real tree, lunch, build or network; git runs only as hash-object (no -w) and apply on synthetic temporary trees. Exercised: the pinned contract and the committed verifyIntegrated binding (check passes; a missing input still exits 4), the
branch classification (verifyIntegrated only on the exact post-state; mixed, pre, unknown and missing refuse; config.xml
only through OS Product's bound-policy check, never normalised on a mismatch), the copy bounds, the contained
environment, the sampled stops, the aconfig reader, the source and classpath proof and the write-once report.

The lunch inputs (OS Product's frankel-lunch-inputs.json, read as data at its pin) are checked on synthetic copies, and
the adevtool public fetch runs real git against a local stand-in repository over the file protocol, which only these
tests allow: the adapter's own default is https from the pinned manifest's source, and the reviewed contract admits no
public fetch yet, so the route is refused (exit 4) until OS Product's amendment and the re-pin land together."""
import contextlib
import fcntl
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
import signal
import stat
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
BINDING = json.loads((ROOT/'os/build-only/launcher3-compile-lane.json').read_text())
import launcher3_controller as controller
lane = adapter.launcher3_lane


def dedicated_binding():
    """Only synthetic capacity; never a proposed or observed host binding."""
    binding = json.loads(json.dumps(BINDING))
    binding.update(taskParent='/home/pixelbuild/rose-actions', branch='verifyBaseApplyCandidate',
                   sourceTree=None, lunch=None, reconstructSource=None, adevtoolGit=None,
                   policyReceipt={'path': None, 'sha256': None},
                   bundleReport={'path': None, 'sha256': None})
    binding['lane'].update(status='bound', runnerName='synthetic-launcher3', runnerLabel='synthetic-launcher3',
                           vm={'project': 'synthetic-project', 'zone': 'test-zone-a',
                               'instance': 'synthetic-launcher3', 'instanceId': '123456789'},
                           disk={'resource': 'projects/synthetic-project/zones/test-zone-a/disks/synthetic-launcher3',
                                 'id': '987654321'}, storageRoot='/home/pixelbuild',
                           lockPath='/home/pixelbuild/launcher3.lock', queue='synthetic-launcher3',
                           controller={'identityVariable': 'LAUNCHER3_CONTROLLER_SERVICE_ACCOUNT',
                                       'providerVariable': 'LAUNCHER3_WORKLOAD_IDENTITY_PROVIDER'},
                           infraEvidence='Synthetic fixture only; no actual capacity or runtime approval.')
    return binding


def dedicated_env():
    return {'GITHUB_REPOSITORY': lane.REPOSITORY, 'GITHUB_WORKFLOW_REF':
            f'{lane.REPOSITORY}/{lane.WORKFLOW}@refs/heads/main', 'GITHUB_REF': 'refs/heads/main',
            'GITHUB_EVENT_NAME': 'workflow_dispatch', 'GITHUB_SHA': 'a' * 40, 'GITHUB_WORKFLOW_SHA': 'a' * 40,
            'GITHUB_RUN_ID': '12345', 'GITHUB_RUN_ATTEMPT': '1', 'RUNNER_NAME': 'synthetic-launcher3'}
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
                          cwd=ROOT, env={k: v for k, v in os.environ.items() if not k.startswith('GITHUB_')})


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
COPY = ('copy', True, False)  # the run's copy, which omits vendor/adevtool/node_modules but keeps dl/.find-ignore


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


def bundle_report(content, registration, edit=None):
    """A rose-integration-bundle-report v1 in base.bundleReport's own form, observing exactly content with its patches
    in the registration order (observed.patches: base.registration.order), with edit applied to a copy."""
    rows = {(p['project'], p['name']): p for p in content['patches']}
    report = {'kind': 'rose-integration-bundle-report', 'schemaVersion': 1,
              'producer': {'path': 'scripts/integration-bundle-report.py', 'sha256': 'a' * 64, 'roseRevision': 'b' * 40},
              'run': {'runId': '41700000000', 'runAttempt': 1, 'headSha': 'c' * 40, 'label': 'r1',
                      'createdAt': '2026-11-04T00:00:00Z', 'updatedAt': '2026-11-04T01:00:00Z', 'bindingSha256': 'e' * 64},
              'receipt': {'path': '/home/pixelbuild/receipts/0001-r1.json', 'receiptSha256': 'f' * 64,
                          'receiptType': 'reconcile', 'label': 'r1', 'at': '2026-11-04T00:30:00Z',
                          'patcher': json.loads(json.dumps(registration['patcher']))},
              'candidate': {'commit': 'c' * 40, 'onMain': True, 'frankelConfigSha256': '1' * 64,
                            'applyArgs': list(registration['applyArgs']), 'patcherKind': 'trusted'},
              'registration': {'source': 'executed', 'patcherMatchesPin': True, 'applyArgsMatchPin': True},
              'observed': {'patches': [dict(rows[(o['project'], o['name'])]) for o in registration['order']],
                           'overlays': [dict(o) for o in content['overlays']],
                           'notAnInputOverlays': list(content['notAnInputOverlays']), 'unclassified': []}}
    if edit:
        edit(report)
    return report


class Synthetic:
    """A three-project v3 contract with real bytes: frameworks/base config.xml (policy rewrite) and one Java file;
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
        self.registration = {'applyArgs': json.loads((ROOT/'os/release-pipeline/frankel.json').read_text())
                             ['host']['integrate']['reconcile']['applyArgs'],
                             'patcher': {'patcher': 'trusted', 'sha256': {n: 'd' * 64 for n in adapter.PATCHER_FILES}},
                             'order': [{'project': L3, 'name': 'rose-switcher-launcher3.patch'}]}
        self.bundle_report = bundle_report(self.bundle_content, self.registration)
        final, base = section(False), section(True)
        self.manifest = {'schemaVersion': 3, 'kind': 'rose-launcher3-compile-inputs', 'target': 'Launcher3QuickStepLib',
                         'proves': 'compile only', 'identitySharing': {'appliesWithEdgeOff': True}, **final,
                         'base': {**base, 'bundleContent': self.bundle_content, 'registration': self.registration,
                                  'bundleReport': MANIFEST['base']['bundleReport']},
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
                         {'frameworks/base': 34, 'packages/apps/Launcher3': 16, 'build/release': 1})
        self.assertEqual(sum(o['role'] == 'required' for o in manifest['overlays']), 15)
        self.assertEqual(manifest['schemaVersion'], 3)
        # 2026-11-07 re-pin: base is the live patcher's registration; #440's delta is retired (empty).
        self.assertEqual(manifest['candidateDelta']['patches'], [])
        self.assertIn('rose-edge-launcher3.patch', [p['name'] for p in manifest['base']['bundleContent']['patches']])
        self.assertEqual(manifest['lunchInputs'], {'path': adapter.LUNCH_INPUTS, 'sha256': adapter.LUNCH_INPUTS_SHA256})
        self.assertEqual((manifest['branches']['requires'], manifest['branches']['forbids']),
                         (adapter.BRANCH_REQUIRES, adapter.BRANCH_FORBIDS))
        self.assertEqual([c['id'] for c in manifest['branches']['verifyBaseApplyCandidate']['conditions']], adapter.CONDITIONS)
        self.assertEqual(manifest['candidateDelta']['commit'], '2f57a5f28e3c3cc97177bf3f9294ea807d514469')
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
                   lambda m: m['candidateDelta']['bundle'].append({'path': 'os/tests/fixtures/x.patch', 'sha256': '0' * 64}),
                   lambda m: m['candidateDelta']['patches'].append({'name': 'x.patch', 'sha256': '0' * 64, 'strip': '1',
                       'bundlePath': 'os/tests/fixtures/x.patch', 'project': 'packages/apps/Launcher3'}),
                   lambda m: CONFIG(m)['integrationRewrite']['inputRules'].__setitem__('certificate', '[0-9A-Fa-f]{64}'),
                   lambda m: CONFIG(m)['integrationRewrite']['templates']['arrays'].__setitem__(
                       'config_pixelComputerControlPrototypeAgents', 'PrototypeAgents: the agent row'),
                   lambda m: CONFIG(m)['integrationRewrite']['templates'].__setitem__(
                       'config_pixelComputerControlLockedSessions', '    <bool name="x">true</bool> when lockedControl; otherwise the marker'),
                   lambda m: CONFIG(m)['integrationRewrite']['elements'].pop(),
                   lambda m: m.update(lunchInputs={'path': adapter.LUNCH_INPUTS, 'sha256': '0' * 64}),
                   lambda m: m['branches']['requires']['verifyIntegrated'].pop(),
                   lambda m: m['branches']['forbids']['verifyIntegrated'].remove('bundleReport'),
                   lambda m: m['base']['bundleReport'].update(schemaVersion=2),
                   lambda m: m['base']['bundleReport']['fields']['observed'].pop(),
                   lambda m: m['base']['registration']['patcher'].update(patcher='any'),
                   lambda m: m['base']['registration']['patcher']['sha256'].pop('integration_copy.py'),
                   lambda m: m['base']['registration']['order'].reverse(),
                   lambda m: m['base']['registration']['order'].pop(),
                   lambda m: m['base']['registration']['patcher']['sha256'].update({'background_apks.py': 'G' * 64}),
                   lambda m: m['base']['registration']['applyArgsSource'].update(path='os/release-pipeline/other.json')]
        for index, change in enumerate(changes):
            with self.subTest(index=index):
                manifest = json.loads(json.dumps(MANIFEST))
                change(manifest)
                with self.assertRaises(adapter.Invalid):
                    adapter.contract_shape(manifest)
        # every text the adapter implements: a re-pin that changes it is never only a hash bump
        report = lambda m: m['base']['bundleReport']  # noqa: E731
        texts = [lambda m: report(m)['rules'].pop(),
                 lambda m: report(m)['association'].__setitem__(1, 'any receipt of the run'),
                 lambda m: report(m)['observed'].update(unclassified='anything else; may be non-empty'),
                 lambda m: report(m).update(producer='any script'),
                 lambda m: m['branches']['definitions'].update(lunch='any lunch'),
                 lambda m: m['branches']['definitions'].update(policyReceipt='any receipt'),
                 lambda m: m['branches'].update(otherwise='complete any state'),
                 lambda m: m['branches']['verifyBaseApplyCandidate']['conditions'][2].update(text='apply anywhere'),
                 lambda m: m['flagEvidence'].update(role='a precondition of every branch'),
                 lambda m: m['flagEvidence']['flags'][0].update(accept='any line'),
                 lambda m: m['candidateDelta']['application'].update(note='--check failures are ignored'),
                 lambda m: m['candidateDelta'].update(pathConvention='anything'),
                 lambda m: m['base']['registration'].update(derivation='read from the tree'),
                 lambda m: m['overlayRoles']['notAnInput'].update(rule='required'),
                 lambda m: m['unaffected'].pop(),
                 lambda m: m.update(provenance='current bytes are provenance')]
        for index, change in enumerate(texts):
            with self.subTest(text=index):
                manifest = json.loads(json.dumps(MANIFEST))
                change(manifest)
                with self.assertRaisesRegex(adapter.Invalid, 'CONTRACT_TEXT_SHA256'):
                    adapter.contract_shape(manifest)
        self.assertEqual(adapter.contract_text_digest(MANIFEST), adapter.CONTRACT_TEXT_SHA256)
        adapter.contract_shape(json.loads(json.dumps(MANIFEST)))
        # the applyArgs source is bound on its own too, even under a text pin re-reviewed for it
        manifest = json.loads(json.dumps(MANIFEST))
        manifest['base']['registration']['applyArgsSource'].update(key='host.integrate.applyArgs')
        with unittest.mock.patch.object(adapter, 'CONTRACT_TEXT_SHA256', adapter.contract_text_digest(manifest)):
            with self.assertRaisesRegex(adapter.Invalid, 'applyArgsSource'):
                adapter.contract_shape(manifest)

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

    def test_committed_binding_is_verify_integrated_and_the_hosted_check_passes(self):
        self.assertEqual(BINDING['branch'], 'verifyIntegrated')
        self.assertEqual(BINDING['lunch'], 'frankel-cur-user')
        self.assertEqual(BINDING['sourceTree'], '/srv/rose-launcher3/src')
        self.assertEqual(BINDING['adevtoolGit'], 'public-fetch')
        self.assertIsNone(BINDING['reconstructSource'])
        self.assertEqual(BINDING['policyReceipt'], {
            'path': '/srv/rose-launcher3/inputs/export-20261107T204240Z/background-policy.json',
            'sha256': '4b3faede5e309ac71cd00b9a642f84e244eee3186f3c355bf26aea3f5226446c'})
        # verifyIntegrated forbids a bundle report; the candidate delta stays empty and is not applied
        self.assertEqual(BINDING['bundleReport'], {'path': None, 'sha256': None})
        self.assertEqual(BINDING['lane']['status'], 'bound')
        self.assertEqual(BINDING['lane']['queue'], 'launcher3-compile')
        self.assertIn('candidateDelta is retired and empty', MANIFEST['candidatePatches'])
        result = run_cli('check')
        self.assertEqual((result.returncode, result.stdout),
                         (0, 'outer_seconds=21720\nfinish_seconds=1800\njob_minutes=403\n'))

    def binding(self, **change):
        path = Path(tempfile.mkdtemp())/'binding.json'
        record = {**dedicated_binding(), 'branch': 'verifyIntegrated', 'sourceTree': '/home/pixelbuild/grapheneos-2026100600',
                  'lunch': 'frankel-cur-user', 'adevtoolGit': 'public-fetch',
                  'policyReceipt': {'path': '/home/pixelbuild/receipts/policy.json', 'sha256': 'a' * 64}, **change}
        path.write_text(json.dumps(record))
        return path

    def test_a_bound_binding_passes_and_prints_its_bounds(self):
        # every lunch is the contract's, so the bounds always hold the lunch inputs (1800 s), the fetch (1800 s) and its
        # kill grace (30 s) besides the copy, the tree pins (one check, or two for the candidate branch), lunch and m
        result = run_cli('check', '--binding', str(self.binding()))
        self.assertEqual((result.returncode, result.stdout), (0, 'outer_seconds=21720\nfinish_seconds=1800\njob_minutes=403\n'))
        result = run_cli('check', '--binding', str(self.binding(branch='verifyBaseApplyCandidate')))
        self.assertEqual(result.returncode, 4)
        self.assertIn('bundleReport.path, bundleReport.sha256', result.stderr)
        result = run_cli('check', '--binding', str(self.binding(branch='verifyBaseApplyCandidate',
                                                                bundleReport={'path': '/home/pixelbuild/receipts/bundle.json', 'sha256': 'b' * 64})))
        self.assertEqual((result.returncode, result.stdout), (0, 'outer_seconds=23520\nfinish_seconds=1800\njob_minutes=433\n'))

    def test_only_the_lunch_input_contracts_target_is_a_lunch(self):
        """definitions.lunch: the lunch inputs in lunchInputs, satisfied on the copy; their only target is frankel-cur-user."""
        self.assertEqual(adapter.load_lunch_inputs()['lunch']['target'], 'frankel-cur-user')
        for lunch in ('akita-cur-user', 'caiman-bp2a-userdebug', 'example-cur-user', 'frankel-trunk-user', 'frankel-cur-eng'):
            for fetch in ('public-fetch', None):
                with self.subTest(lunch=lunch, fetch=fetch):
                    result = run_cli('check', '--binding', str(self.binding(lunch=lunch, adevtoolGit=fetch)))
                    self.assertEqual(result.returncode, 2, result.stderr)
                    self.assertIn('definitions.lunch', result.stderr)

    def test_no_receipt_or_report_is_a_file_of_a_checked_tree(self):
        tree = '/home/pixelbuild/grapheneos-2026100600'
        roots = '/home/pixelbuild/rose-actions'
        bundle = lambda path: {'branch': 'verifyBaseApplyCandidate', 'bundleReport': {'path': path, 'sha256': 'b' * 64}}  # noqa: E731
        for name, change, word in (
                ('policy in the tree', {'policyReceipt': {'path': f'{tree}/frameworks/base/policy.json', 'sha256': 'a' * 64}},
                 'policyReceipt.path is inside the source tree'),
                ('policy is the tree', {'policyReceipt': {'path': tree, 'sha256': 'a' * 64}}, 'inside the source tree'),
                ('policy in a run root', {'policyReceipt': {'path': f'{roots}/launcher3-compile-1-1/tree/p.json',
                                                            'sha256': 'a' * 64}}, 'inside a launcher3-compile run root'),
                ('report in the tree', bundle(f'{tree}/bundle.json'), 'bundleReport.path is inside the source tree'),
                ('report in a run root', bundle(f'{roots}/launcher3-compile-41700000000-2/receipts/report.json'),
                 'inside a launcher3-compile run root')):
            with self.subTest(name=name):
                result = run_cli('check', '--binding', str(self.binding(**change)))
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn(word, result.stderr)
        # another task's own directory under the run root parent, or a sibling of the tree, is not a checked tree
        for path in (f'{roots}/frankel-integrate-41700000000-1/policy.json', f'{tree}-receipts/policy.json'):
            with self.subTest(path=path):
                result = run_cli('check', '--binding', str(self.binding(policyReceipt={'path': path, 'sha256': 'a' * 64})))
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_a_version_checked_lunch_is_unbound_until_adevtool_git_is_bound(self):
        for branch, bundle in (('verifyIntegrated', {'path': None, 'sha256': None}),
                               ('verifyBaseApplyCandidate', {'path': '/home/pixelbuild/receipts/bundle.json', 'sha256': 'b' * 64})):
            result = run_cli('check', '--binding', str(self.binding(lunch='frankel-cur-user', branch=branch,
                             bundleReport=bundle, adevtoolGit=None)))
            self.assertEqual((result.returncode, result.stdout), (4, ''))
            self.assertIn('adevtool_git_missing', result.stderr)
        # bound to the public fetch, which the pinned contract admits: the check passes with the lunch inputs (1800 s),
        # the fetch (1800 s) and its kill grace (30 s) in its bounds
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
                 ('auto maintenance allowed', lambda d: fetch(d)['steps'].__setitem__(
                     1, fetch(d)['steps'][1].replace(' --no-auto-maintenance', '')), 'steps'),
                 ('maintenance prohibition dropped', lambda d: fetch(d)['never'].pop(), 'reviewed against'),
                 ('maintenance acceptance dropped', lambda d: fetch(d)['acceptance'].remove(
                     'no gc.pid, gc.log or objects/maintenance.lock in the gitdir'), 'reviewed against'),
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
                  'options are not exactly'),
                 # every other meaning-bearing text: the git route's digest
                 ('acceptance item dropped', lambda d: fetch(d)['acceptance'].pop(2), 'reviewed against'),
                 ('never emptied', lambda d: fetch(d).update(never=[]), 'reviewed against'),
                 ('target', lambda d: fetch(d).update(target='the source tree'), 'reviewed against'),
                 ('cleanup', lambda d: fetch(d).update(cleanup='keep the .git'), 'reviewed against'),
                 ('gitRequirement acceptance', lambda d: d['gitRequirement'].update(acceptance=[]), 'reviewed against'),
                 ('rule', lambda d: tree(d).update(rule='node_modules is allowed'), 'reviewed against'),
                 ('onUnlisted', lambda d: tree(d).update(onUnlisted='admit'), 'reviewed against'),
                 ('omitted means', lambda d: tree(d)['omittedFromCopy'].update(means='tracked paths may be omitted'),
                  'reviewed against'),
                 ('gitignore blob', lambda d: tree(d)['omittedFromCopy'].update(gitBlob='0' * 40), 'gitBlob'),
                 ('redirect means', lambda d: d['gitRequirement']['options'][1].update(means=''), 'reviewed against'),
                 ('genuine-copy', lambda d: d['gitRequirement']['options'][0].update(status='allowed'), 'reviewed against'),
                 ('relaxed check text', lambda d: d['sources'][2].update(workingTreeCheck='exactly, extra paths allowed'),
                  'reviewed against'),
                 ('git environment', lambda d: d['gitRequirement']['environment'].update(unset='nothing'), 'reviewed against'),
                 ('protocol', lambda d: fetch(d).update(protocol='https or http'), 'protocol'),
                 ('ceiling bytes', lambda d: fetch(d)['ceiling'].update(bytes=2 << 30), 'ceiling'),
                 ('environment key removed', lambda d: fetch(d)['environment'].pop('GIT_ASKPASS'), 'environment'),
                 ('workingTree project', lambda d: tree(d).update(project='vendor/other'), 'workingTree'),
                 ('option order', lambda d: d['gitRequirement']['options'].reverse(), 'options are not exactly'),
                 ('fabrication allowed', lambda d: d['gitRequirement']['options'][3].update(status='allowed'), 'not refused'),
                 ('relaxation allowed', lambda d: d['gitRequirement']['options'][4].update(status='allowed'), 'not refused'),
                 ('publicFetch absent', lambda d: d['gitRequirement'].pop('publicFetch'), 'publicFetch'),
                 ('kernel revision', lambda d: d['sources'][2].update(revision='0' * 40), 'pinned manifest revision')]
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

    def test_a_contract_whose_omitted_rules_would_drop_a_tracked_path_is_not_admitted(self):
        root = Path(tempfile.mkdtemp())
        record = json.loads(LUNCH_INPUTS.read_text())
        for source in record['sources']:
            original = ROOT/source['listing']['path']
            target = root/source['listing']['path']
            target.parent.mkdir(parents=True, exist_ok=True)
            raw = gzip.decompress(original.read_bytes())
            if source['path'] == 'vendor/adevtool':
                raw += b'100644 blob ' + b'1' * 40 + b'\tnode_modules/tracked.js\n'
                source['listing'].update(paths=source['listing']['paths'] + 1, sha256=sha(raw))
            packed = gzip.compress(raw, mtime=0)
            target.write_bytes(packed)
            source['listing']['compressedSha256'] = sha(packed)
        (root/'os').mkdir(exist_ok=True)
        (root/'os/pinned-manifest.xml').write_bytes((ROOT/'os/pinned-manifest.xml').read_bytes())
        problems = adapter.admission_problems(record, 'https://github.com/GrapheneOS/adevtool', root)
        self.assertIn('the omitted rules would drop the tracked node_modules/tracked.js', problems)

    def test_the_omitted_rules_drop_no_tracked_path_of_the_pinned_listing(self):
        listed = adapter.lunch_listing(adapter.load_lunch_inputs(), 'vendor/adevtool')
        dropped = [p for p in listed if any(adapter.adevtool_ignored('/'.join(os.fsdecode(p).split('/')[:i]),
                                                                    i < len(os.fsdecode(p).split('/')))
                                            for i in range(1, len(os.fsdecode(p).split('/')) + 1))]
        self.assertEqual(dropped, [])
        self.assertIn(b'dl/.find-ignore', listed)

    def test_each_branch_refuses_what_the_contract_forbids(self):
        bundle = {'path': '/home/pixelbuild/receipts/bundle.json', 'sha256': 'b' * 64}
        for branch, change, word in (('verifyIntegrated', {'bundleReport': bundle}, 'verifyIntegrated forbids bundleReport'),
                                     ('verifyIntegrated', {'bundleReport': {'path': None, 'sha256': 'b' * 64}},
                                      'verifyIntegrated forbids bundleReport'),
                                     ('verifyIntegrated', {'reconstructSource': '/home/pixelbuild/objects'},
                                      'verifyIntegrated forbids reconstructSource'),
                                     ('verifyBaseApplyCandidate', {'bundleReport': bundle, 'reconstructSource': '/o'},
                                      'verifyBaseApplyCandidate forbids reconstructSource'),
                                     ('reconstruct', {'reconstructSource': '/o', 'bundleReport': bundle},
                                      'reconstruct forbids bundleReport')):
            with self.subTest(branch=branch, change=change):
                result = run_cli('check', '--binding', str(self.binding(branch=branch, **change)))
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn(word, result.stderr)

    def test_the_registration_applyargs_and_the_copys_git_exclusion_are_checked_against_the_repository(self):
        binding = self.binding()
        for name, edit, word in (('applyArgs', lambda c: c['host']['integrate']['reconcile']['applyArgs'].append('--extra'),
                                  'registration.applyArgs'), ('exclusion', '.git', 'exclude every .git and .repo'),
                                 ('repo exclusion', '.repo', 'exclude every .git and .repo')):
            with self.subTest(name=name):
                root = Path(tempfile.mkdtemp())
                (root/'os/release-pipeline').mkdir(parents=True)
                config = json.loads((ROOT/'os/release-pipeline/frankel.json').read_text())
                alpha = json.loads((ROOT/'os/release-pipeline/cuttlefish-alpha.json').read_text())
                if callable(edit):
                    edit(config)
                else:
                    alpha['preparation']['exclusions']['anywhereNames'].remove(edit)
                (root/'os/release-pipeline/frankel.json').write_text(json.dumps(config))
                (root/'os/release-pipeline/cuttlefish-alpha.json').write_text(json.dumps(alpha))
                with self.assertRaisesRegex(adapter.Invalid, word):
                    adapter.check(binding, root)

    def test_invalid_bindings_refuse(self):
        for change in ({'host': 'other'}, {'taskParent': '/tmp'}, {'branch': 'mixed'}, {'sourceTree': 'relative'},
                       {'sourceTree': '/home/pixelbuild/rose-actions/blazer-prep-41473133543-1/source'},
                       {'sourceTree': '/home'}, {'lunch': 'frankel'}, {'policyReceipt': {'path': '/p'}},
                       {'policyReceipt': {'path': '/p', 'sha256': 'A' * 64}}, {'extra': 1}, {'schemaVersion': 1},
                       {'schemaVersion': 2}, {'adevtoolGit': 'genuine-copy'}, {'lunch': 'example-cur-user'},
                       {'lunch': 'frankel-trunk-user', 'adevtoolGit': 'public-fetch'}, {'lunch': 'akita-cur-user', 'adevtoolGit': None},
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


class BundleReport(unittest.TestCase):
    def setUp(self):
        self.s = Synthetic(tempfile.mkdtemp())
        self.base = self.s.manifest['base']

    def problems(self, edit=None, report=None):
        return adapter.bundle_report_problems(report if report is not None else
                                              bundle_report(self.s.bundle_content, self.s.registration, edit), self.base)

    def test_a_report_observing_exactly_the_bundle_passes(self):
        self.assertEqual(self.problems(), [])

    def test_every_rule_names_its_own_problem(self):
        first = lambda r, key: r['observed'][key][0]  # noqa: E731
        cases = [('bare bundleContent', None, self.s.bundle_content, 'sections are not exactly'),
                 ('kind', lambda r: r.update(kind='other'), None, 'not a rose-integration-bundle-report v1'),
                 ('missing section', lambda r: r.pop('producer'), None, 'sections are not exactly'),
                 ('extra field', lambda r: r['run'].update(extra=1), None, 'run keys are not exactly'),
                 ('missing field', lambda r: r['candidate'].pop('onMain'), None, 'candidate keys are not exactly'),
                 ('nested field', lambda r: r['receipt']['patcher']['sha256'].pop('background_apks.py'), None,
                  'receipt.patcher.sha256 keys'),
                 ('patch row', lambda r: first(r, 'patches').pop('sha256'), None, 'observed.patches[0] keys'),
                 ('untrusted patcher', lambda r: r['receipt']['patcher'].update(patcher='other'), None, 'receipt.patcher'),
                 ('patcher pin', lambda r: r['receipt']['patcher']['sha256'].update({'apply-os-changes.py': '0' * 64}), None,
                  'receipt.patcher'),
                 ('applyArgs', lambda r: r['candidate']['applyArgs'].append('--extra'), None, 'candidate.applyArgs'),
                 ('pin flags', lambda r: r['registration'].update(applyArgsMatchPin=False), None, 'match the pins'),
                 ('unclassified', lambda r: r['observed'].update(unclassified=['frameworks/base/x']), None, 'unclassified'),
                 ('order', lambda r: r['observed']['patches'].append({'project': L3, 'name': 'extra.patch', 'sha256': '9' * 64}),
                  None, 'registration order'),
                 ('patch bytes', lambda r: first(r, 'patches').update(sha256='9' * 64), None, 'observed patches differ'),
                 ('overlay', lambda r: first(r, 'overlays').update(sha256='9' * 64), None, 'observed overlays differ'),
                 ('notAnInput', lambda r: r['observed'].update(notAnInputOverlays=[]), None, 'observed overlays differ')]
        for name, edit, report, word in cases:
            with self.subTest(name=name):
                problems = self.problems(edit, report)
                self.assertTrue(any(word in p for p in problems), problems)

    def test_the_committed_contract_spec_is_what_the_adapter_reads(self):
        self.assertEqual(MANIFEST['base']['bundleReport']['fields'], adapter.BUNDLE_REPORT_FIELDS)
        self.assertEqual(sorted(MANIFEST['base']['registration']['patcher']['sha256']), sorted(adapter.PATCHER_FILES))

        def names(spec):
            return {item if isinstance(item, str) else next(iter(item)): None if isinstance(item, str) else next(iter(item.values()))
                    for item in spec}
        # every field of the spec has exactly one type, and the row specs are the spec's own
        for section, spec in adapter.BUNDLE_REPORT_FIELDS.items():
            self.assertEqual(set(names(spec)), set(adapter.REPORT_TYPES[section]), section)
        observed = names(adapter.BUNDLE_REPORT_FIELDS['observed'])
        self.assertEqual({k: list(v) for k, v in adapter.ROW_TYPES.items()}, {k: observed[k] for k in ('patches', 'overlays')})
        self.assertEqual(sorted(adapter.REPORT_ROWS), ['observed.overlays', 'observed.patches'])
        self.assertTrue(set(adapter.KIND_NAMES) >= {k for kinds in adapter.REPORT_TYPES.values() for k in kinds.values()})

    def test_the_association_the_report_itself_shows_is_checked(self):
        cases = [('candidate is not the run commit', lambda r: r['candidate'].update(commit='d' * 40), 'is not run.headSha'),
                 ('not on main', lambda r: r['candidate'].update(onMain=False), 'onMain is not true'),
                 ('an adopt receipt', lambda r: r['receipt'].update(receiptType='reconcile-adopt'), 'receiptType'),
                 ('another label', lambda r: r['receipt'].update(label='r2'), "run's label"),
                 ('a name of another label', lambda r: r['receipt'].update(path='/home/pixelbuild/receipts/0001-r2.json'),
                  "run's label"),
                 ('a bare label name', lambda r: r['receipt'].update(path='/home/pixelbuild/receipts/-r1.json'), "run's label"),
                 ('not json', lambda r: r['receipt'].update(path='/home/pixelbuild/receipts/0001-r1.txt'), "run's label"),
                 ('before the run', lambda r: r['receipt'].update(at='2026-11-03T23:59:59Z'), 'within'),
                 ('after the run', lambda r: r['receipt'].update(at='2026-11-04T01:00:01Z'), 'within'),
                 ('a run that ends before it starts', lambda r: r['run'].update(updatedAt='2026-11-03T00:00:00Z'), 'within'),
                 ('an untrusted kind', lambda r: r['candidate'].update(patcherKind='untrusted'), 'patcherKind'),
                 ('the adapter as producer', lambda r: r['producer'].update(path='scripts/launcher3-compile.py'),
                  'never the stated producer'),
                 ('the checker as producer', lambda r: r['producer'].update(path='scripts/launcher3-compile-contract.py'),
                  'never the stated producer')]
        for name, edit, word in cases:
            with self.subTest(name=name):
                problems = self.problems(edit)
                self.assertTrue(any(word in p for p in problems), problems)
        # the receipt may be at either end of the run
        for at in ('2026-11-04T00:00:00Z', '2026-11-04T01:00:00Z'):
            self.assertEqual(self.problems(lambda r: r['receipt'].update(at=at)), [])

    def test_malformed_values_are_named_problems_never_a_crash_or_a_pass(self):
        first = lambda r, key: r['observed'][key][0]  # noqa: E731
        cases = [('patches as one object', lambda r: r['observed'].update(patches=first(r, 'patches')), 'observed.patches is not a list'),
                 ('overlays as one object', lambda r: r['observed'].update(overlays=first(r, 'overlays')), 'observed.overlays is not a list'),
                 ('a list project', lambda r: first(r, 'patches').update(project=[L3]), 'observed.patches[0].project is not'),
                 ('a null overlay target', lambda r: first(r, 'overlays').update(target=None), 'observed.overlays[0].target is not'),
                 ('mixed notAnInput', lambda r: r['observed'].update(notAnInputOverlays=[1, 'x']), 'notAnInputOverlays is not'),
                 ('notAnInput keyed by target', lambda r: r['observed'].update(
                     notAnInputOverlays={t: 1 for t in r['observed']['notAnInputOverlays']}), 'notAnInputOverlays is not'),
                 ('a pin flag 1', lambda r: r['registration'].update(patcherMatchesPin=1), 'patcherMatchesPin is not a boolean'),
                 ('a pin flag 1.0', lambda r: r['registration'].update(applyArgsMatchPin=1.0), 'applyArgsMatchPin is not a boolean'),
                 ('schemaVersion true', lambda r: r.update(schemaVersion=True), 'not a rose-integration-bundle-report v1'),
                 ('a numeric head', lambda r: r['run'].update(headSha=12), 'run.headSha is not'),
                 ('an attempt true', lambda r: r['run'].update(runAttempt=True), 'run.runAttempt is not'),
                 ('a zero run id', lambda r: r['run'].update(runId='0'), 'run.runId is not'),
                 ('a local time', lambda r: r['run'].update(createdAt='2026-11-04T00:00:00+00:00'), 'run.createdAt is not a UTC time'),
                 ('an unpadded time', lambda r: r['receipt'].update(at='2026-11-4T00:30:00Z'), 'receipt.at is not a UTC time'),
                 ('a relative receipt', lambda r: r['receipt'].update(path='receipts/0001-r1.json'), 'receipt.path is not'),
                 ('an escaping producer', lambda r: r['producer'].update(path='../scripts/x.py'), 'producer.path is not'),
                 ('an uppercase sha256', lambda r: r['producer'].update(sha256='A' * 64), 'producer.sha256 is not'),
                 ('a label with a slash', lambda r: (r['run'].update(label='a/b'), r['receipt'].update(label='a/b')), 'label is not'),
                 ('a null source', lambda r: r['registration'].update(source=None), 'registration.source is not'),
                 ('a patch sha of another type', lambda r: first(r, 'patches').update(sha256=7), 'observed.patches[0].sha256 is not')]
        for name, edit, word in cases:
            with self.subTest(name=name):
                problems = self.problems(edit)
                self.assertTrue(any(word in p for p in problems), problems)
        # no value of any field raises: each wrong type is a named problem
        for section, kinds in adapter.REPORT_TYPES.items():
            for field, kind in kinds.items():
                for value in (None, 1.5, {}, [None], 1 if kind == 'bool' else True):
                    with self.subTest(field=f'{section}.{field}', value=value):
                        self.assertTrue(self.problems(lambda r: r[section].__setitem__(field, value)))
        for key, fields in adapter.ROW_TYPES.items():
            for field in fields:
                for value in (None, 1.5, {}, []):
                    with self.subTest(row=f'{key}.{field}', value=value):
                        self.assertTrue(self.problems(lambda r: r['observed'][key][0].__setitem__(field, value)))
        for report in ([], 'report', None, {'kind': 'rose-integration-bundle-report'}):
            self.assertTrue(adapter.bundle_report_problems(report, self.base))

    def test_an_unreadable_report_is_a_named_base_exact_refusal(self):
        with unittest.mock.patch.object(adapter, 'bundle_report_problems', side_effect=TypeError('defect')):
            with self.assertRaisesRegex(adapter.Refused, r'base-exact: the bundle report cannot be read as base.bundleReport \(TypeError\)'):
                adapter.bundle_matches({}, 'a' * 64, 'a' * 64, self.base)

    def test_the_committed_base_needs_the_registration_order_across_projects(self):
        base = MANIFEST['base']
        content, registration = base['bundleContent'], base['registration']
        problems = lambda edit=None: adapter.bundle_report_problems(bundle_report(content, registration, edit), base)  # noqa: E731
        self.assertEqual(problems(), [])
        self.assertNotEqual([p['project'] for p in content['patches']], [o['project'] for o in registration['order']])
        bundle_order = lambda r: r['observed'].update(patches=[dict(p) for p in content['patches']])  # noqa: E731
        self.assertIn('observed.patches are not the registration order', problems(bundle_order))

        def swap(r):
            patches = r['observed']['patches']
            patches[0], patches[1] = patches[1], patches[0]
        self.assertEqual(problems(swap)[:2], ['observed.patches are not the registration order',
                                              'observed patches differ from base.bundleContent'])
        self.assertEqual(problems(lambda r: r['observed']['patches'][3].update(sha256='9' * 64)),
                         ['observed patches differ from base.bundleContent'])


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
        # named only as a producer base-exact refuses (NEVER_PRODUCERS), never loaded
        self.assertEqual([line for line in code.splitlines() if 'launcher3-compile-contract' in line],
                         ["NEVER_PRODUCERS = ('scripts/launcher3-compile.py', 'scripts/launcher3-compile-contract.py')"])
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
                                                rss=lambda pid: 0), (0, None, True))
        finally:
            adapter.SAMPLE_SECONDS = saved

    # Cleanup pins the process identity before signalling, including after a passing test. A recycled PID is
    # never killed: each child writes a birth stat next to its pid file, and pidfd binds the final check to the signal.
    def reap_later(self, *pid_files, pids=()):
        births = {pid: adapter.proc_stat(Path('/proc')/str(pid)) for pid in pids}

        def reap():
            expected = dict(births)
            for path in pid_files:
                try:
                    pid = int(path.read_text())
                    fields = Path(str(path) + '.stat').read_bytes().rsplit(b')', 1)[1].split()
                    expected[pid] = (None, None, None, int(fields[19]))
                except (OSError, ValueError, IndexError):
                    continue
            for pid, birth in expected.items():
                if birth is None:
                    continue
                with contextlib.suppress(ProcessLookupError, PermissionError):
                    fd = os.pidfd_open(pid)
                    try:
                        current = adapter.proc_stat(Path('/proc')/str(pid))
                        if current is not None and current[3] == birth[3]:
                            signal.pidfd_send_signal(fd, signal.SIGKILL)
                    finally:
                        os.close(fd)
        self.addCleanup(reap)

    def member_pid(self, path):
        end = time.monotonic() + 20
        while not (path.exists() and path.read_text().strip()):
            self.assertLess(time.monotonic(), end, 'the stage never started')
            time.sleep(0.05)
        return int(path.read_text())

    @staticmethod
    def live(pid):
        """Whether pid is a live process (a zombie is not: it waits only for its reaper)."""
        try:
            return Path(f'/proc/{pid}/stat').read_bytes().rsplit(b')', 1)[1].split()[0] != b'Z'
        except (FileNotFoundError, ProcessLookupError):
            return False

    def start(self, script, work):
        proc = subprocess.Popen(['bash', '-c', script], cwd=work, start_new_session=True, stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.reap_later(work/'member', pids=(proc.pid,))
        return proc

    def test_a_member_that_outlives_its_leader_is_killed_too(self):
        work = Path(tempfile.mkdtemp())
        # the member ignores TERM; the leader does not, so it exits first and the member is left running
        proc = self.start('trap "" TERM; sleep 600 & echo $! > member; cat /proc/$!/stat > member.stat; trap - TERM; wait', work)
        member = self.member_pid(work/'member')
        start = time.monotonic()
        self.assertTrue(adapter.end_session(proc, grace=1))
        self.assertFalse(self.live(member))
        self.assertLess(time.monotonic() - start, 10)
        self.assertEqual(adapter.session_members(proc.pid), {})

    def test_a_member_in_another_process_group_of_the_session_is_ended(self):
        """ninja gives each job its own process group inside the stage's session; set -m does the same here."""
        work = Path(tempfile.mkdtemp())
        proc = self.start('set -m; (trap "" TERM; exec sleep 600) & echo $! > member; cat /proc/$!/stat > member.stat; wait', work)
        member = self.member_pid(work/'member')
        stat = adapter.proc_stat(Path(f'/proc/{member}'))
        self.assertEqual(stat[2], proc.pid)  # the same session
        self.assertNotEqual(stat[1], proc.pid)  # its own process group
        self.assertTrue(adapter.end_session(proc, grace=1))
        self.assertFalse(self.live(member))

    def test_a_stopped_session_is_woken_to_its_term(self):
        """TERM stays pending on a stopped process; the CONT after it lets it take TERM now, not at KILL after the grace."""
        work = Path(tempfile.mkdtemp())
        proc = self.start('sleep 600 & echo $! > member; cat /proc/$!/stat > member.stat; wait', work)
        member = self.member_pid(work/'member')
        for pid in (member, proc.pid):
            os.kill(pid, signal.SIGSTOP)
        start = time.monotonic()
        self.assertTrue(adapter.end_session(proc, grace=6))
        self.assertLess(time.monotonic() - start, 4)  # well before the 6 s grace would have run out
        self.assertFalse(self.live(member))

    def test_a_gone_session_is_never_signalled_and_an_unreadable_table_fails_closed(self):
        proc = subprocess.Popen(['true'], start_new_session=True)
        proc.wait()
        with unittest.mock.patch.object(adapter.os, 'killpg') as killpg:
            self.assertTrue(adapter.end_session(proc, grace=1))
        killpg.assert_not_called()
        work = Path(tempfile.mkdtemp())
        proc = self.start('sleep 600', work)
        clock = iter(range(0, 1000, 5))
        self.assertFalse(adapter.end_session(proc, clock=lambda: next(clock), sleep=lambda s: None, grace=1,
                                             members=lambda sid: None))  # never confirmed gone
        proc.wait(timeout=10)  # the leader's own group was still sent TERM and KILL

    def test_the_process_table_is_read_as_bytes_whatever_the_names(self):
        proc_dir = Path(tempfile.mkdtemp())
        for pid, comm, state, pgid, sid in ((101, b'n\xc3\xa9\xc3\xa9) (x', b'S', 101, 100), (102, b'\xff\xfe', b'R', 102, 100),
                                            (103, b'zombie', b'Z', 100, 100), (104, b'other', b'S', 104, 104)):
            (proc_dir/str(pid)).mkdir()
            rest = b' '.join([state, b'1', str(pgid).encode(), str(sid).encode()] + [b'0'] * 15 + [b'777'])  # field 22
            (proc_dir/str(pid)/'stat').write_bytes(str(pid).encode() + b' (' + comm + b') ' + rest)
        self.assertEqual(adapter.session_members(100, proc_dir), {101: 101, 102: 102})
        self.assertEqual(adapter.proc_stat(proc_dir/'101'), ('S', 101, 100, 777))
        (proc_dir/'105').mkdir()
        (proc_dir/'105'/'stat').write_bytes(b'105 (cut')  # neither gone nor readable: the table fails closed
        self.assertIsNone(adapter.session_members(100, proc_dir))

    def test_the_memory_stop_counts_every_process_group_of_the_session(self):
        proc_dir, page = Path(tempfile.mkdtemp()), os.sysconf('SC_PAGE_SIZE')
        for pid, pgid, sid, pages in ((101, 101, 101, 10), (102, 102, 101, 20), (103, 103, 101, 30), (104, 104, 104, 999)):
            (proc_dir/str(pid)).mkdir()
            rest = b' '.join([b'S', b'1', str(pgid).encode(), str(sid).encode()] + [b'0'] * 15 + [b'777'])
            (proc_dir/str(pid)/'stat').write_bytes(str(pid).encode() + b' (job) ' + rest)
            (proc_dir/str(pid)/'statm').write_bytes(f'{2 * pages} {pages} 0 0 0 0 0\n'.encode())
        self.assertEqual(adapter.session_rss(101, proc_dir), 60 * page)  # the leader's group and ninja's job groups

    def test_every_scan_signals_the_groups_it_finds_and_a_refusing_group_stops_none_of_the_rest(self):
        class Leader:
            pid, returncode = 4242, None

            def poll(self):
                return self.returncode
        scans = iter([{4242: 4242}, {4242: 4242, 4300: 4300}, {4242: 4242, 4300: 4300, 4400: 4400}]
                     + [{4300: 4300, 4400: 4400}] * 100)
        sent, tick = [], iter(range(1000))

        def killpg(pgid, sig):
            sent.append((pgid, sig))
            if pgid == 4300:  # a group none of whose members may be signalled
                raise PermissionError(1, 'Operation not permitted')
        with unittest.mock.patch.object(adapter.os, 'killpg', side_effect=killpg):
            self.assertFalse(adapter.end_session(Leader(), clock=lambda: next(tick) * 0.25, sleep=lambda s: None,
                                                 grace=1, members=lambda sid: next(scans)))
        for sig in (signal.SIGTERM, signal.SIGKILL):  # 4400 appeared after the first scan, beside a group that refuses
            self.assertTrue({(4242, sig), (4300, sig), (4400, sig)} <= set(sent), sig)
        self.assertGreater(sent.count((4400, signal.SIGKILL)), 1)  # KILL again at every scan
        self.assertEqual(sent.count((4400, signal.SIGTERM)), 1)  # TERM once to each group, as it is found

    def test_any_failure_during_a_stage_ends_its_session_and_an_interrupt_records_it(self):
        for name, stage in (('supervised', lambda argv, sleep: adapter.supervised(argv, dict(os.environ), '/', 600, '/',
                                                                                  rss=lambda pid: 0, sleep=sleep)),
                            ('bounded_git', lambda argv, sleep: adapter.bounded_git(argv, dict(os.environ), '/',
                                                                                    time.monotonic() + 600, '/', 1 << 40,
                                                                                    measure=lambda path: 0, sleep=sleep))):
            for failure in (adapter.Interrupted('SIGTERM'), RuntimeError('a defect')):
                with self.subTest(stage=name, failure=type(failure).__name__):
                    work = Path(tempfile.mkdtemp())
                    self.reap_later(work/'member')
                    calls, end = [], time.monotonic() + 20

                    def sleep(seconds):
                        calls.append(seconds)
                        if len(calls) == 1:  # the first sample: the failure arrives here, once the member exists
                            while not (work/'member').exists():
                                self.assertLess(time.monotonic(), end, 'the stage never started')
                                time.sleep(0.05)
                            raise failure
                        time.sleep(min(seconds, 0.2))
                    with unittest.mock.patch.object(adapter, 'INTERRUPT_GRACE_SECONDS', 1), \
                            self.assertRaises(type(failure)) as raised:
                        stage(['bash', '-c', f'set -m; (trap "" TERM; exec sleep 600) & echo $! > {work}/member; cat /proc/$!/stat > {work}/member.stat; wait'],
                              sleep)
                    self.assertFalse(self.live(int((work/'member').read_text())))
                    if isinstance(failure, adapter.Interrupted):
                        self.assertEqual(raised.exception.stages[-1]['sessionEnded'], True)

    def stage_with(self, work, on_first_sleep, **patches):
        """supervised on a stage whose leader and member ignore TERM, with on_first_sleep() run at its first poll once
        the member exists, under a fresh interrupts() state."""
        self.reap_later(work/'member')
        calls, end = [], time.monotonic() + 20

        def sleep(seconds):
            calls.append(seconds)
            if len(calls) == 1:
                while not (work/'member').exists():
                    self.assertLess(time.monotonic(), end, 'the stage never started')
                    time.sleep(0.05)
                on_first_sleep()
            time.sleep(min(seconds, 0.05))
        with contextlib.ExitStack() as stack:
            stack.enter_context(unittest.mock.patch.dict(adapter._INTERRUPT, {'deferring': 0, 'pending': None,
                                                                                'raised': False}))
            stack.enter_context(unittest.mock.patch.object(adapter, 'INTERRUPT_GRACE_SECONDS', 1))
            for name, value in patches.items():
                stack.enter_context(unittest.mock.patch.object(adapter, name, value))
            try:
                return adapter.supervised(['bash', '-c', f'trap "" TERM; sleep 600 & echo $! > {work}/member; cat /proc/$!/stat > {work}/member.stat; wait'],
                                          dict(os.environ), '/', 600, '/', rss=lambda pid: 0, sleep=sleep)
            finally:
                self.assertEqual(adapter._INTERRUPT['deferring'], 0)

    def hold(self, name='SIGINT'):
        adapter._INTERRUPT['pending'] = name  # what interrupts() does with a signal while a stage runs

    def test_a_signal_held_while_a_stage_runs_is_taken_at_its_next_poll_and_its_session_ended(self):
        work = Path(tempfile.mkdtemp())
        start = time.monotonic()
        with self.assertRaises(adapter.Interrupted) as raised:
            self.stage_with(work, self.hold)
        self.assertEqual((str(raised.exception), raised.exception.stages),
                         ('SIGINT', [{'stage': 'stage', 'started': True, 'sessionEnded': True}]))
        self.assertFalse(self.live(int((work/'member').read_text())))
        self.assertLess(time.monotonic() - start, 15)

    def test_a_failure_while_an_interrupt_is_held_leaves_as_that_interrupt(self):
        def fail():
            self.hold('SIGTERM')
            raise OSError(28, 'No space left on device')  # the stage fails just as the outer timeout's TERM is held
        work = Path(tempfile.mkdtemp())
        with self.assertRaises(adapter.Interrupted) as raised:
            self.stage_with(work, fail)
        self.assertIsInstance(raised.exception.__cause__, OSError)
        self.assertEqual(raised.exception.stages, [{'stage': 'stage', 'started': True, 'sessionEnded': True}])
        self.assertFalse(self.live(int((work/'member').read_text())))

    def test_an_interrupt_pending_during_a_stop_cuts_its_graces_to_the_interrupt_budget(self):
        class Leader:
            pid, returncode = 4242, None

            def poll(self):
                return self.returncode
        now = [0.0]

        def sleep(seconds):
            now[0] += seconds
        with unittest.mock.patch.dict(adapter._INTERRUPT, {'deferring': 1, 'pending': 'SIGINT', 'raised': False}), \
                unittest.mock.patch.object(adapter.os, 'killpg'):
            self.assertFalse(adapter.end_session(Leader(), clock=lambda: now[0], sleep=sleep, grace=30,
                                                 members=lambda sid: {4242: 4242}))  # a member that never goes
        self.assertLessEqual(now[0], 2 * (adapter.INTERRUPT_GRACE_SECONDS + adapter.POLL_SECONDS))  # not 2 x 30 s

    def test_a_signal_during_a_failed_stages_cleanup_cannot_cut_it_short(self):
        """The stage fails (here ENOSPC at its first poll), and the outer timeout's TERM arrives while its session is
        being ended: the cleanup still TERMs and KILLs the whole session, and the run leaves as that interrupt."""
        work = Path(tempfile.mkdtemp())
        script = self.driver(work, """
calls = []
def sleep(seconds):
    calls.append(seconds)
    if len(calls) == 1:
        end = time.monotonic() + 20
        while not os.path.exists(os.path.join(WORK, 'member')) and time.monotonic() < end:
            time.sleep(0.02)
        raise OSError(28, 'No space left on device')
    if len(calls) == 2:  # the cleanup's first poll
        os.kill(os.getpid(), signal.SIGTERM)
    time.sleep(min(seconds, 0.05))
try:
    with a.interrupts():
        a.supervised(STAGE, dict(os.environ), '/', 600, '/', rss=lambda pid: 0, sleep=sleep, stage=stage)
except a.Interrupted as error:
    print(json.dumps([str(error), type(error.__cause__).__name__, error.stages]))
    sys.exit(3)
""")
        result = subprocess.run([sys.executable, str(script)], capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertEqual(json.loads(result.stdout),
                         ['SIGTERM', 'OSError', [{'stage': 'stage', 'started': True, 'sessionEnded': True}]])
        self.assertFalse(self.live(int((work/'member').read_text())))

    def test_a_stage_that_ends_normally_is_recorded_then_marked_ended(self):
        calls = []
        self.assertEqual(adapter.supervised([sys.executable, '-c', 'pass'], dict(os.environ), '/', 60, '/',
                                            rss=lambda pid: 0, label='m',
                                            stage=lambda pid, label, ended=False: calls.append((label, ended))),
                         (0, None, True))
        self.assertEqual(calls, [('m', False), ('m', True)])

    def test_an_unreadable_process_table_never_confirms_a_stage_ended(self):
        work = Path(tempfile.mkdtemp())
        with self.assertRaises(adapter.Interrupted) as raised:  # an interrupted stage
            self.stage_with(work, self.hold, session_members=lambda sid, proc=None: None)
        self.assertEqual(raised.exception.stages[-1]['sessionEnded'], False)
        self.assertFalse(self.live(int((work/'member').read_text())))  # its leader's group was still TERMed and KILLed
        # a stage that exits normally: a table that cannot be read is a leftover, never confirmed ended
        with unittest.mock.patch.object(adapter, 'session_members', lambda sid, proc=None: None), \
                unittest.mock.patch.object(adapter, 'KILL_GRACE_SECONDS', 0.5):
            self.assertEqual(adapter.supervised([sys.executable, '-c', 'pass'], dict(os.environ), '/', 60, '/',
                                                rss=lambda pid: 0), (0, 'leftover', False))
            self.assertEqual(adapter.bounded_git(['true'], dict(os.environ), '/', time.monotonic() + 60, '/', 1 << 40,
                                                 measure=lambda path: 0), (0, 'leftover', False))

    def test_a_session_that_outlives_a_normal_exit_is_ended_and_stops_the_stage(self):
        work = Path(tempfile.mkdtemp())
        self.reap_later(work/'member')
        argv = ['bash', '-c', f'(trap "" TERM; exec sleep 600) & echo $! > {work}/member; cat /proc/$!/stat > {work}/member.stat; exit 0']
        with unittest.mock.patch.object(adapter, 'KILL_GRACE_SECONDS', 1):  # it ignores TERM: KILL after the grace
            self.assertEqual(adapter.supervised(argv, dict(os.environ), '/', 600, '/', rss=lambda pid: 0),
                             (0, 'leftover', True))
        self.assertFalse(self.live(int((work/'member').read_text())))
        work = Path(tempfile.mkdtemp())
        self.reap_later(work/'member')
        argv = ['bash', '-c', f'sleep 600 & echo $! > {work}/member; cat /proc/$!/stat > {work}/member.stat; exit 0']
        self.assertEqual(adapter.bounded_git(argv, dict(os.environ), '/', time.monotonic() + 600, '/', 1 << 40,
                                             measure=lambda path: 0), (0, 'leftover', True))
        self.assertFalse(self.live(int((work/'member').read_text())))

    def driver(self, work, body, grace=1):
        """A python script that loads the adapter (at INTERRUPT_GRACE_SECONDS grace, or its own with None) and runs body;
        it names its own pid for the cleanup (never its parent's, which may be this test process)."""
        script = work/'driver.py'
        script.write_text(f"""import importlib.util, json, os, signal, sys, time
spec = importlib.util.spec_from_file_location('a', {str(ROOT/'scripts/launcher3-compile.py')!r})
a = importlib.util.module_from_spec(spec); spec.loader.exec_module(a)
if {grace!r} is not None:
    a.INTERRUPT_GRACE_SECONDS = {grace!r}
WORK = {str(work)!r}
open(os.path.join(WORK, 'python'), 'w').write(str(os.getpid()))
open(os.path.join(WORK, 'python.stat'), 'wb').write(open('/proc/self/stat', 'rb').read())
STAGE = ['bash', '-c', 'trap "" TERM; sleep 600 & echo $! > ' + WORK + '/member; cat /proc/$!/stat > ' + WORK + '/member.stat; wait']
def stage(pid, label, ended=False):
    open(os.path.join(WORK, 'ended' if ended else 'leader'), 'w').write(str(pid))
    if not ended:
        try:
            open(os.path.join(WORK, 'leader.stat'), 'wb').write(open('/proc/' + str(pid) + '/stat', 'rb').read())
        except FileNotFoundError:
            pass
""" + body)
        self.reap_later(work/'member', work/'leader', work/'python')
        return script

    INTERRUPTED_STAGE = """
try:
    with a.interrupts():
        a.supervised(STAGE, dict(os.environ), '/', 600, '/', rss=lambda pid: 0, stage=stage)
except a.Interrupted as error:
    open(os.path.join(WORK, 'interrupted'), 'w').write(str(error) + json.dumps(error.stages))
    sys.exit(3)
"""

    def step(self, work, script, exec_timeout=True):
        """The run step as the runner runs it: its text in a script file run by bash -e (shell: bash), started in its
        own session here so the test can clean it up; the runner signals that one process."""
        text = work/'step.sh'
        text.write_text(f'{"exec " if exec_timeout else ""}timeout --kill-after=30s 600s {sys.executable} {script}\n')
        step = subprocess.Popen(['bash', '--noprofile', '--norc', '-eo', 'pipefail', str(text)], start_new_session=True,
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.reap_later(pids=(step.pid,))
        self.addCleanup(lambda: step.poll() is None and (step.kill(), step.wait()))
        return step

    def test_a_signal_while_a_stage_starts_is_held_until_its_session_is_recorded(self):
        work = Path(tempfile.mkdtemp())
        script = self.driver(work, """
real = a.subprocess.Popen
def popen(*args, **kwargs):  # the signal lands between the fork and the record
    proc = real(*args, **kwargs)
    os.kill(os.getpid(), signal.SIGTERM)
    end = time.monotonic() + 20
    while not os.path.exists(os.path.join(WORK, 'member')) and time.monotonic() < end:
        time.sleep(0.02)  # the stage is under way (its member exists) before the record
    return proc
try:
    with a.interrupts():
        a.supervised(STAGE, dict(os.environ), '/', 600, '/', popen=popen, rss=lambda pid: 0, stage=stage)
except a.Interrupted as error:
    print(json.dumps(error.stages))
    sys.exit(3)
""")
        result = subprocess.run([sys.executable, str(script)], capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertEqual(json.loads(result.stdout), [{'stage': 'stage', 'started': True, 'sessionEnded': True}])
        self.assertTrue((work/'leader').exists())  # recorded before the interrupt was taken
        self.assertTrue((work/'ended').exists())  # and marked ended once its session was confirmed gone
        self.assertFalse(self.live(int((work/'member').read_text())))

    def test_the_outer_timeout_ends_the_running_stage(self):
        """The workflow's own `timeout --kill-after=30s` around a stage whose leader and member both ignore TERM."""
        work = Path(tempfile.mkdtemp())
        script = self.driver(work, self.INTERRUPTED_STAGE)
        start = time.monotonic()
        result = subprocess.run(['timeout', '--kill-after=30s', '8s', sys.executable, str(script)],
                                stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 124, result.stderr)  # timeout's own status: the command timed out
        self.assertTrue((work/'interrupted').read_text().startswith('SIGTERM'), result.stderr)
        self.assertFalse(self.live(int((work/'member').read_text())))
        self.assertLess(time.monotonic() - start, 30)  # inside the kill-after: python was never KILLed

    def test_a_cancel_reaches_the_helper_only_because_the_step_execs_timeout(self):
        """The runner's cancel signals the step's entry process, the bash running the step's script file. The step
        execs timeout, so that process is timeout, which forwards the signal to python. Without exec, bash waits for
        its child and python never hears of the cancel."""
        work = Path(tempfile.mkdtemp())
        step = self.step(work, self.driver(work, self.INTERRUPTED_STAGE), exec_timeout=False)
        member = self.member_pid(work/'member')
        step.send_signal(signal.SIGINT)
        time.sleep(2)
        self.assertFalse((work/'interrupted').exists())
        self.assertTrue(self.live(member))
        work = Path(tempfile.mkdtemp())
        step = self.step(work, self.driver(work, self.INTERRUPTED_STAGE))
        member = self.member_pid(work/'member')
        step.send_signal(signal.SIGINT)
        self.assertEqual(step.wait(timeout=30), 3)  # timeout passes on python's own status
        self.assertTrue((work/'interrupted').read_text().startswith('SIGINT'))
        self.assertFalse(self.live(member))

    def test_a_cancel_ends_the_stage_before_the_runner_would_send_sigterm(self):
        """GitHub's cancel: SIGINT to the step's entry process, SIGTERM 7.5 s later, then the process tree is killed
        2.5 s after that. With the adapter's own graces, a stage whose leader and member ignore TERM is gone, and python
        has recorded the interruption and exited, before that SIGTERM."""
        work = Path(tempfile.mkdtemp())
        step = self.step(work, self.driver(work, self.INTERRUPTED_STAGE, grace=None))
        member = self.member_pid(work/'member')
        start = time.monotonic()
        step.send_signal(signal.SIGINT)
        try:
            code = step.wait(timeout=adapter.CANCEL_SIGTERM_SECONDS)
        except subprocess.TimeoutExpired:
            step.send_signal(signal.SIGTERM)  # what the runner sends next; python would have no more time to clean up
            self.fail(f'the step outlived the {adapter.CANCEL_SIGTERM_SECONDS} s before the runner\'s SIGTERM')
        self.assertEqual(code, 3)
        self.assertLess(time.monotonic() - start, adapter.CANCEL_SIGTERM_SECONDS)
        self.assertTrue((work/'interrupted').read_text().startswith('SIGINT'))
        self.assertFalse(self.live(member))

    def test_cancel_during_unconfirmed_stop_writes_run_report_before_sigterm(self):
        """Real exec-timeout, signal and clock; only the stuck session and native inputs are synthetic."""
        work = Path(tempfile.mkdtemp())
        script = self.driver(work, f"""
spec = importlib.util.spec_from_file_location('fixtures', {str(ROOT/'scripts/tests/test_launcher3_compile.py')!r})
f = importlib.util.module_from_spec(spec); spec.loader.exec_module(f); f.adapter = a
t = f.Run(); t.setUp()
open(os.path.join(WORK, 'report-path'), 'w').write(str(t.parent/'launcher3-compile-41700000000-1/receipts/report.json'))
class Stuck:
    pid, returncode = 424242, None
    def poll(self):
        return None
def sent(*args):
    open(os.path.join(WORK, 'stopping'), 'w').write('ready')
a.session_members = lambda sid: {{424242: 424242}}
a.signal_session = sent
def compiler(*args, **kwargs):
    return a.supervised(['stand-in'], {{}}, '/', 100, '/', popen=lambda *a, **k: Stuck(),
                        used=lambda path: 0, rss=lambda sid: a.MEMORY_STOP_BYTES + 1, **kwargs)
t.compiler = lambda *args, **kwargs: compiler
with a.interrupts():
    sys.exit(t.run_adapter())
""", grace=None)
        step = self.step(work, script)
        end = time.monotonic() + 20
        while not (work/'stopping').exists():
            self.assertIsNone(step.poll(), 'the driver exited before the stop')
            self.assertLess(time.monotonic(), end, 'the memory stop never started')
            time.sleep(0.02)
        start = time.monotonic()
        os.kill(step.pid, signal.SIGINT)
        step.wait(timeout=adapter.CANCEL_SIGTERM_SECONDS)
        self.assertLess(time.monotonic() - start, adapter.CANCEL_SIGTERM_SECONDS)
        report = json.loads(Path((work/'report-path').read_text()).read_text())
        self.assertEqual(report['result'], 'interrupted')
        self.assertEqual(report['interrupted']['stages'], [{'stage': 'lunch', 'started': True, 'sessionEnded': False}])

    def test_only_the_first_signal_interrupts_and_the_handlers_come_back(self):
        for first in ('SIGTERM', 'SIGINT', 'SIGHUP', 'SIGQUIT'):
            with self.subTest(first=first):
                script = (f"import importlib.util, os, signal, time\n"
                          f"spec = importlib.util.spec_from_file_location('a', {str(ROOT/'scripts/launcher3-compile.py')!r})\n"
                          "a = importlib.util.module_from_spec(spec); spec.loader.exec_module(a)\n"
                          "before = {s: signal.getsignal(s) for s in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP, signal.SIGQUIT)}\n"
                          "with a.interrupts():\n"
                          "    try:\n"
                          f"        os.kill(os.getpid(), signal.{first}); time.sleep(5)\n"
                          "    except a.Interrupted as error:  # the cleanup, still inside run\n"
                          "        print('first', error)\n"
                          "        print('dropped by a handler, not ignored:', all(signal.getsignal(s) not in (signal.SIG_IGN, signal.SIG_DFL) for s in before))\n"
                          "        for s in before:\n"
                          "            os.kill(os.getpid(), s)\n"
                          "        time.sleep(0.2)\n"
                          "        print('later dropped')\n"
                          "print('restored', all(signal.getsignal(s) == h for s, h in before.items()))\n")
                result = subprocess.run([sys.executable, '-c', script], capture_output=True, text=True, timeout=60)
                self.assertEqual((result.returncode, result.stdout),
                                 (0, f'first {first}\ndropped by a handler, not ignored: True\nlater dropped\nrestored True\n'),
                                 result.stderr)

    def test_main_runs_the_run_command_inside_interrupts_and_exits_3_when_interrupted(self):
        before = signal.getsignal(signal.SIGTERM)
        seen = []

        def fake_run(*args, **kwargs):
            seen.append(signal.getsignal(signal.SIGTERM))
            raise adapter.Interrupted('SIGTERM')
        binding = {**dedicated_binding(), 'branch': 'verifyIntegrated', 'sourceTree': '/home/pixelbuild/source'}
        with unittest.mock.patch.object(adapter, 'check', return_value=(MANIFEST, binding, None)), \
                unittest.mock.patch.object(adapter, 'run', side_effect=fake_run), \
                unittest.mock.patch.dict(os.environ, {**dedicated_env(), 'GITHUB_RUN_ID': '1'}, clear=True):
            self.assertEqual(adapter.main(['run', '--run-id', '1', '--attempt', '1']), 3)
        self.assertNotIn(seen[0], (signal.SIG_DFL, signal.SIG_IGN))
        self.assertEqual(signal.getsignal(signal.SIGTERM), before)

    def test_the_stages_carry_the_runners_tracking_id_as_a_backstop(self):
        with unittest.mock.patch.dict(os.environ, {'RUNNER_TRACKING_ID': 'github_x'}):
            self.assertEqual(adapter.contained_env(Path('/r'))['RUNNER_TRACKING_ID'], 'github_x')
        with unittest.mock.patch.dict(os.environ, clear=False) as env:
            env.pop('RUNNER_TRACKING_ID', None)
            self.assertNotIn('RUNNER_TRACKING_ID', adapter.contained_env(Path('/r')))

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


class SafetyRegressions(unittest.TestCase):
    """Deterministic fault injection: no real compile, host paths or process signals."""
    class Leader:
        pid = 424242
        returncode = None

        def poll(self):
            return self.returncode

    def exercise(self, kind, proc, clock, sleep, **kwargs):
        if kind == 'git':
            return adapter.bounded_git(['git'], {}, '/', 100, '/', 1, popen=lambda *a, **k: proc,
                                       clock=clock, sleep=sleep, measure=lambda p: 2, **kwargs)
        return adapter.supervised(['m'], {}, '/', 100, '/', popen=lambda *a, **k: proc,
                                  clock=clock, sleep=sleep, used=lambda p: 0,
                                  rss=lambda pid: adapter.MEMORY_STOP_BYTES + 1, **kwargs)

    def test_unconfirmed_stops_never_mark_or_return_ended(self):
        for kind in ('git', 'compile'):
            calls = []
            with self.subTest(kind=kind), unittest.mock.patch.object(adapter, 'end_session', return_value=False):
                result = self.exercise(kind, self.Leader(), lambda: 0, lambda s: None,
                                       stage=lambda pid, label, ended=False: calls.append(ended))
            self.assertEqual(result[2], False)
            self.assertEqual(calls, [False])

    def test_cancel_during_stop_or_leftover_has_only_one_shutdown_budget(self):
        for kind in ('git', 'compile'):
            for leftover in (False, True):
                now, calls = [0.0], []
                proc = self.Leader()
                if leftover:
                    proc.returncode = 0

                def sleep(seconds):
                    now[0] += seconds
                    adapter._INTERRUPT['pending'] = 'SIGINT'
                with self.subTest(kind=kind, leftover=leftover), \
                        unittest.mock.patch.dict(adapter._INTERRUPT, deferring=0, pending=None, raised=False), \
                        unittest.mock.patch.object(adapter, 'session_members', return_value={proc.pid: proc.pid}), \
                        unittest.mock.patch.object(adapter, 'signal_session') as sent, \
                        self.assertRaises(adapter.Interrupted) as raised:
                    self.exercise(kind, proc, lambda: now[0], sleep,
                                  stage=lambda pid, label, ended=False: calls.append(ended))
                self.assertLessEqual(now[0], adapter.POLL_SECONDS + 2 * adapter.INTERRUPT_GRACE_SECONDS)
                self.assertEqual(raised.exception.stages[0]['sessionEnded'], False)
                self.assertEqual(calls, [False])
                terms = [call for call in sent.call_args_list if call.args[1] == signal.SIGTERM and call.args[0]]
                self.assertEqual(len(terms), 1)

    def test_fetch_takes_the_interrupt_at_its_poll(self):
        now = [0.0]

        def sleep(seconds):
            now[0] += seconds
            adapter._INTERRUPT['pending'] = 'SIGINT'
        with unittest.mock.patch.dict(adapter._INTERRUPT, deferring=0, pending=None, raised=False), \
                unittest.mock.patch.object(adapter, 'end_session', return_value=True), \
                self.assertRaises(adapter.Interrupted) as raised:
            adapter.bounded_git(['git'], {}, '/', 100, '/', 1, popen=lambda *a, **k: self.Leader(),
                                clock=lambda: now[0], sleep=sleep, measure=lambda p: 0)
        self.assertLessEqual(now[0], adapter.POLL_SECONDS)
        self.assertEqual(raised.exception.stages, [{'stage': 'adevtool-git', 'started': True, 'sessionEnded': True}])

    def test_fetch_defers_the_signal_between_fork_and_record(self):
        calls = []

        def popen(*args, **kwargs):
            os.kill(os.getpid(), signal.SIGTERM)
            return self.Leader()
        with adapter.interrupts(), unittest.mock.patch.object(adapter, 'end_session', return_value=True) as end, \
                self.assertRaises(adapter.Interrupted) as raised:
            adapter.bounded_git(['git'], {}, '/', 100, '/', 1, popen=popen, clock=lambda: 0,
                                measure=lambda p: 0, stage=lambda *a, **k: calls.append((a, k)))
        self.assertTrue(raised.exception.stages[0]['started'])
        self.assertTrue(calls)
        end.assert_called_once()

    def test_signal_at_stage_deferral_exit_retains_the_stage_result(self):
        original = adapter.deferred.__exit__

        def late(scope, *args):
            if scope.on_interrupt:
                adapter._INTERRUPT['pending'] = 'SIGTERM'
            return original(scope, *args)
        for kind in ('git', 'compile'):
            with self.subTest(kind=kind), \
                    unittest.mock.patch.dict(adapter._INTERRUPT, deferring=0, pending=None, raised=False), \
                    unittest.mock.patch.object(adapter.deferred, '__exit__', late), \
                    unittest.mock.patch.object(adapter, 'end_session', return_value=False) as end, \
                    self.assertRaises(adapter.Interrupted) as raised:
                self.exercise(kind, self.Leader(), lambda: 0, lambda s: None)
            end.assert_called_once()
            self.assertEqual(raised.exception.stages[0]['sessionEnded'], False)

    def test_cleanup_error_preserves_original_failure_and_unconfirmed_status(self):
        error = OSError('original failure')
        with unittest.mock.patch.object(adapter, 'end_session', side_effect=ValueError('cleanup failure')):
            result = adapter.stage_failed(self.Leader(), error, 'm', None, lambda: 0, lambda s: None)
        self.assertIs(result, error)
        self.assertEqual(result.stage_status, {'stage': 'm', 'started': True, 'sessionEnded': False})

    def test_ordinary_failure_cleanup_uses_the_short_graces(self):
        now = [0.0]
        error = OSError('stage failed')
        with unittest.mock.patch.object(adapter, 'session_members', return_value={424242: 424242}), \
                unittest.mock.patch.object(adapter, 'signal_session'):
            result = adapter.stage_failed(self.Leader(), error, 'm', None, lambda: now[0],
                                          lambda seconds: now.__setitem__(0, now[0] + seconds))
        self.assertIs(result, error)
        self.assertLessEqual(now[0], 2 * adapter.INTERRUPT_GRACE_SECONDS)
        self.assertFalse(result.stage_status['sessionEnded'])

    def test_confirmation_requires_empty_scans_a_poll_apart(self):
        for second, expected in (({}, True), ({424243: 424242}, False), (None, False)):
            scans = iter([{}, second])
            sleeps = []
            self.assertEqual(adapter.session_gone(424242, lambda sid: next(scans), sleeps.append), expected)
            self.assertEqual(sleeps, [adapter.POLL_SECONDS])

    def test_finish_rescans_new_groups_and_terms_each_only_once(self):
        receipts = Path(tempfile.mkdtemp())
        (receipts/'stage.json').write_text(json.dumps({'sid': 424242, 'stage': 'm', 'startTime': 7}))
        scans, now, sent = [0], [0.0], []

        def members(sid):
            scans[0] += 1
            return {424242: 424242, **({424243: 424243} if scans[0] > 1 else {})}
        with unittest.mock.patch.object(adapter, 'proc_stat', return_value=None), \
                unittest.mock.patch.object(adapter.os, 'killpg', side_effect=lambda pg, sig: sent.append((pg, sig))):
            result = adapter.end_recorded(receipts, clock=lambda: now[0],
                                          sleep=lambda s: now.__setitem__(0, now[0] + s), grace=1, members=members)
        self.assertFalse(result['ended'])
        self.assertGreater(scans[0], 2)
        for pg in (424242, 424243):
            self.assertEqual(sent.count((pg, signal.SIGTERM)), 1)
            self.assertEqual(sent.count((pg, signal.SIGCONT)), 1)
            self.assertGreater(sent.count((pg, signal.SIGKILL)), 1)

    def test_interrupt_state_is_fresh_on_entry_and_restored_on_exit(self):
        saved = {'deferring': 9, 'pending': 'SIGQUIT', 'raised': True}
        with unittest.mock.patch.dict(adapter._INTERRUPT, saved):
            with adapter.interrupts(), self.assertRaises(adapter.Interrupted) as raised:
                os.kill(os.getpid(), signal.SIGTERM)
            self.assertEqual(str(raised.exception), 'SIGTERM')
            self.assertEqual(adapter._INTERRUPT, saved)


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

    def second_patch(self, before=b'class Proxy {}\n', after=b'class Proxy { void edge() {} }\n'):
        """A second patch on a disjoint file of the same project (as E2c's SystemUiProxy beside E2a's handlers), or with
        before None one that creates its file; (patch, file entry)."""
        work = Path(tempfile.mkdtemp())
        (work/'quickstep').mkdir()
        if before is not None:
            (work/'quickstep/Proxy.java').write_bytes(before)
        (work/'quickstep/Keep.java').write_bytes(b'keep\n')
        git('init', '-q', cwd=work)
        git('add', '-A', cwd=work)
        git('commit', '-qm', 'b', cwd=work)
        (work/'quickstep/Proxy.java').write_bytes(after)
        git('add', '-A', cwd=work)
        patch = git('diff', '--cached', cwd=work)
        (self.bundle/'candidate-pr440/rose-edge-resize-launcher3.patch').write_bytes(patch)
        if before is not None:
            (self.project/'quickstep/Proxy.java').write_bytes(before)
        return ({'name': 'rose-edge-resize-launcher3.patch', 'sha256': sha(patch), 'project': 'packages/apps/Launcher3',
                 'bundlePath': 'candidate-pr440/rose-edge-resize-launcher3.patch', 'strip': 1},
                {'path': 'packages/apps/Launcher3/quickstep/Proxy.java', 'before': image(before), 'after': image(after)})

    def test_every_check_passes_before_the_first_patch_is_applied(self):
        patch, entry = self.second_patch()
        (self.project/'quickstep/Proxy.java').write_bytes(b'class Proxy { /* drifted */ }\n')  # the second --check fails
        applied, calls = [], []
        real = subprocess.run
        with self.assertRaisesRegex(adapter.Refused, 'apply --check of .*rose-edge-resize-launcher3.patch .*nothing was applied'):
            adapter.apply_candidate(self.tree, [*self.patches, patch], self.overlays, self.bundle, [*self.files, entry],
                                    run=lambda argv, **kw: calls.append(argv[4]) or real(argv, **kw), applied=applied)
        self.assertEqual((calls, applied), (['--check', '--check'], []))  # both checks ran on the unchanged copy
        self.assertEqual((self.project/'quickstep/Handler.java').read_bytes(), EDGE_BASE)
        self.assertEqual((self.project/'quickstep/Gesture.java').read_bytes(), b'main gesture\n')
        self.assertFalse((self.project/'quickstep/State.java').exists())
        # a bad source refuses before any check or write
        for bad in ([dict(patch, sha256='0' * 64)], [dict(self.overlays[0], after='0' * 64)]):
            calls.clear()
            patches, overlays = ([*self.patches, *bad], self.overlays) if 'strip' in bad[0] else (self.patches, bad)
            with self.assertRaisesRegex(adapter.Refused, 'nothing was applied'):
                adapter.apply_candidate(self.tree, patches, overlays, self.bundle, self.files, applied=applied,
                                        run=lambda argv, **kw: calls.append(argv[4]) or real(argv, **kw))
            self.assertEqual((calls, applied), ([], []))

    def test_two_disjoint_patches_reach_their_images_and_a_failure_after_the_checks_says_what_started(self):
        patch, entry = self.second_patch()
        applied = []
        adapter.delta_preconditions(self.tree, [*self.files, entry], self.overlays)
        adapter.apply_candidate(self.tree, [*self.patches, patch], self.overlays, self.bundle, [*self.files, entry],
                                applied=applied)
        self.assertTrue(adapter.image_is(self.project/'quickstep/Proxy.java', entry['after']))
        self.assertEqual((self.project/'quickstep/Handler.java').read_bytes(), EDGE_AFTER)
        self.assertEqual(applied, ['rose-edge-launcher3.patch', 'rose-edge-resize-launcher3.patch',
                                   *[o['target'] for o in self.overlays]])
        self.setUp()
        patch, entry = self.second_patch()
        real = subprocess.run

        def failing(argv, **kw):  # the second patch's own apply fails after both checks passed
            if argv[4] != '--check' and argv[-1].endswith('rose-edge-resize-launcher3.patch'):
                return subprocess.CompletedProcess(argv, 1)
            return real(argv, **kw)
        applied = []
        with self.assertRaisesRegex(adapter.Refused, 'after every --check passed; started in the copy: '
                                    'rose-edge-launcher3.patch, rose-edge-resize-launcher3.patch'):
            adapter.apply_candidate(self.tree, [*self.patches, patch], self.overlays, self.bundle, [*self.files, entry],
                                    run=failing, applied=applied)
        self.assertEqual(applied, ['rose-edge-launcher3.patch', 'rose-edge-resize-launcher3.patch'])

    def test_a_delta_file_with_no_before_image_is_created_by_its_patch(self):
        patch, entry = self.second_patch(before=None, after=b'class Proxy {}\n')
        self.assertIsNone(entry['before'])
        adapter.delta_preconditions(self.tree, [*self.files, entry], self.overlays)
        adapter.apply_candidate(self.tree, [*self.patches, patch], self.overlays, self.bundle, [*self.files, entry])
        self.assertTrue(adapter.image_is(self.project/'quickstep/Proxy.java', entry['after']))


class RealDelta(unittest.TestCase):
    """The committed candidate delta (#440 2f57a5f: E2a and E2c, inert under os/tests/fixtures) applied by this adapter's own code to
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
        target = 'packages/apps/Launcher3/quickstep/src/com/android/quickstep/fallback/RoseEdgeGesture.java'
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

    def test_the_retired_delta_is_empty_and_base_is_already_final(self):
        """2026-11-07 re-pin: the live patcher registers #440's edge, so the delta is empty; applying it changes nothing
        and every base image already equals its final image."""
        delta = MANIFEST['candidateDelta']
        self.assertEqual((delta['files'], delta['overlays'], delta['patches'], delta['bundle']), ([], [], [], []))
        tree = Path(tempfile.mkdtemp())/'tree'
        tree.mkdir()
        adapter.delta_preconditions(tree, delta['files'], delta['overlays'])
        adapter.apply_candidate(tree, delta['patches'], delta['overlays'], ROOT, delta['files'])
        self.assertEqual(list(tree.iterdir()), [])
        final = {f'{p["path"]}/{f["path"]}': f['post'] for p in MANIFEST['projects'] for f in p['files']}
        base = {f'{p["path"]}/{f["path"]}': f['post'] for p in MANIFEST['base']['projects'] for f in p['files']}
        self.assertEqual(base, final)

class Run(unittest.TestCase):
    def setUp(self):
        self.parent = Path(tempfile.mkdtemp())
        self.s = Synthetic(tempfile.mkdtemp())
        self.binding = {**dedicated_binding(), 'branch': 'verifyIntegrated', 'taskParent': str(self.parent), 'sourceTree': '/src',
                        'lunch': 'frankel-cur-user', 'adevtoolGit': 'public-fetch',
                        'policyReceipt': {'path': '/p.json', 'sha256': self.s.policy_sha}}
        self.bundle_sha = sha(json.dumps(self.s.bundle_report).encode())
        self.calls = []
        self.lunch_result = (0, None, True)

    def copier(self, state='post'):
        def copy(source, destination, omit=None):
            self.calls.append('copy' if omit is None else ('copy', omit('vendor/adevtool/node_modules', True),
                                                           omit('vendor/adevtool/dl/.find-ignore', False)))
            Synthetic(destination, self.s.repo).write(state)
            return {'entries': 1}
        return copy

    def compiler(self, code=0, stopped=None, ended=True):
        def compile_(argv, env, cwd, seconds, out_dir, **stage):
            if 'm -j' not in argv[2]:
                self.calls.append(('lunch', argv[-1], env['GIT_CEILING_DIRECTORIES'], seconds))
                return self.lunch_result
            self.calls.append(('compile', argv[-2:], env['OUT_DIR'], seconds))
            out = Path(out_dir)
            module = out/'soong/.intermediates/packages/apps/Launcher3/Launcher3QuickStepLib'
            module.mkdir(parents=True)
            return code, stopped, ended
        return compile_

    def pins(self, fail=None):
        def pin(tree, projects, root, deadline):
            label = next(iter(projects[0]['treePin']))
            self.calls.append(('pins', label))
            if label == fail:
                raise adapter.Refused(f'{projects[0]["path"]}/x: extra against the tree pin')
            return {p['path']: {'sha256': 'd' * 64, 'count': 1} for p in projects}
        return pin

    def plan(self, unset=('BUILD_ID_frankel', 'TARGET_KERNEL_DIR', 'USE_STOCK_KERNEL')):
        return {'inputs': {'lunch': {'target': 'frankel-cur-user'}, 'environment': {'unset': ['GIT_*', *unset]},
                           'gitRequirement': {'project': 'vendor/adevtool'}},
                'url': 'https://github.com/GrapheneOS/adevtool', 'exact': ['vendor/adevtool']}

    def checks(self, tree, inputs, root, deadline, exact=None):
        self.calls.append(('lunch-inputs', tuple(exact)))
        return {'contractSha256': 'checked'}

    def fetch(self, tree, plan, run_root, record, root, stage=None):
        self.calls.append('fetch')
        record.update(ok=True)

    def run_adapter(self, state='post', lock=None, code=0, stopped=None, pins=None, bundle=None, plan=None,
                    inputs_check=None, fetcher=None, applier=None):
        lock = lock or (lambda path: contextlib.nullcontext())
        return adapter.run(self.binding, self.s.manifest, '41700000000', '1', root=self.s.repo, lock=lock,
                           copier=self.copier(state), compiler=self.compiler(code, stopped), pins=pins or self.pins(),
                           policy_loader=lambda path: (POLICY, self.s.policy_sha),
                           bundle_loader=lambda path: (bundle or self.s.bundle_report, self.bundle_sha),
                           plan=plan or self.plan(), inputs_check=inputs_check or self.checks, fetcher=fetcher or self.fetch,
                           applier=applier)

    def report(self):
        return json.loads((self.parent/'launcher3-compile-41700000000-1/receipts/report.json').read_text())

    def test_verified_state_compiles_and_reports_without_claiming_a_proof_it_lacks(self):
        self.assertEqual(self.run_adapter(), 0)
        report = self.report()
        self.assertEqual(self.calls[:4], [COPY, ('pins', 'final'), ('lunch-inputs', ('vendor/adevtool',)), 'fetch'])
        out = self.parent/'launcher3-compile-41700000000-1/out'
        tree = self.parent/'launcher3-compile-41700000000-1/tree'
        self.assertEqual(self.calls[4], ('lunch', 'frankel-cur-user', str(tree), adapter.LUNCH_SECONDS))
        self.assertEqual(report['lunch'], {'exit': 0, 'stoppedBy': None, 'sessionEnded': True})
        self.assertEqual(self.calls[5][1:], ([f'{out}/soong/.intermediates/frameworks/base/android.security.flags-aconfig/'
            'aconfig-flags.txt', f'{out}/soong/.intermediates/packages/apps/Launcher3/aconfig/com_android_launcher3_flags/'
            'aconfig-flags.txt'], str(out), adapter.COMPILE_SECONDS))
        self.assertEqual(report['classification']['branch'], 'verifyIntegrated')
        self.assertEqual([c['id'] for c in report['conditions']], ['verifyIntegrated'])
        self.assertEqual(report['compile'], {'exit': 0, 'stoppedBy': None, 'sessionEnded': True, 'targets': ['Launcher3QuickStepLib',
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
        self.assertEqual(self.calls, [COPY])
        report = self.report()
        self.assertEqual(report['result'], 'refused')
        self.assertTrue(report['refused'].startswith('state_refused'))

    def test_a_tree_pin_difference_refuses_before_any_compile(self):
        self.assertEqual(self.run_adapter(pins=self.pins(fail='final')), 3)
        self.assertEqual(self.calls, [COPY, ('pins', 'final')])
        self.assertIn('extra against the tree pin', self.report()['refused'])

    def test_a_base_tree_is_refused_by_verify_integrated(self):
        self.assertEqual(self.run_adapter('base'), 3)
        self.assertEqual(self.calls, [COPY])
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
        self.assertEqual(self.calls[:5], [COPY, ('pins', 'base'), ('pins', 'final'), ('lunch-inputs', ('vendor/adevtool',)),
                                          'fetch'])
        self.assertEqual([c[0] for c in self.calls[5:7]], ['lunch', 'compile'])
        # the bundle report base-exact used, and what of its association was taken on trust
        self.assertEqual(report['bundleReport'], {'sha256': self.bundle_sha,
            'run': {'runId': '41700000000', 'runAttempt': 1, 'headSha': 'c' * 40, 'label': 'r1'},
            'receipt': {'path': '/home/pixelbuild/receipts/0001-r1.json', 'receiptSha256': 'f' * 64, 'at': '2026-11-04T00:30:00Z'},
            'producer': self.s.bundle_report['producer'], 'takenOnTrust': list(adapter.BUNDLE_TRUSTED)})
        self.assertIn('headSha reachable from main (only candidate.onMain is read)', report['bundleReport']['takenOnTrust'])
        tree = self.parent/'launcher3-compile-41700000000-1/tree'
        self.assertEqual((tree/L3/EDGE_FILE).read_bytes(), EDGE_AFTER)
        self.assertEqual((tree/L3/FALLBACK/'RoseEdgeState.java').read_bytes(), b'candidate state\n')
        self.assertEqual(report['classification']['branch'], 'verifyBaseApplyCandidate')

    def test_base_apply_candidate_refuses_at_the_first_failed_condition_by_name(self):
        cases = [('post', {}, 'delta-preimages', False), ('pre', {}, 'base-exact', True),
                 ('base', {'bundle': bundle_report(self.s.bundle_content, self.s.registration,
                                                   lambda r: r['observed'].update(patches=[]))},
                  'base-exact: the integrating bundle report does not match', True),
                 ('base', {'bundle': self.s.bundle_content}, 'the bundle report sections are not exactly', True),
                 ('base', {'pins': self.pins(fail='base')}, 'extra against the tree pin', True),
                 ('base', {'pins': self.pins(fail='final')}, 'extra against the tree pin', False)]
        for state, change, word, untouched in cases:
            with self.subTest(state=state, word=word):
                self.setUp()
                self.vbac()
                if state == 'post':  # already at the candidate: the base pins pass on a fake, the preimages do not
                    self.s.manifest['base'] = {**self.s.manifest, 'bundleContent': self.s.bundle_content,
                                               'registration': self.s.registration}
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
        for result in ((2, None, True), (-15, 'deadline', True)):
            with self.subTest(result=result):
                self.setUp()
                self.lunch_result = result
                self.assertEqual(self.run_adapter(), 0)
                report = self.report()
                self.assertEqual((report['lunch'], report['result']),
                                 ({'exit': result[0], 'stoppedBy': result[1], 'sessionEnded': True}, 'incomplete'))
                self.assertNotIn('compile', report)
                self.assertFalse([c for c in self.calls if c[0] == 'compile'])

    def test_without_the_bound_fetch_or_the_contracts_lunch_nothing_is_copied(self):
        lock = lambda path: contextlib.nullcontext()  # noqa: E731
        for name, change, plan, word in (('no fetch', {'adevtoolGit': None}, self.plan(), 'adevtool_git_missing'),
                                         ('no plan', {}, None, 'lunch-inputs: no admitted lunch-input plan'),
                                         ('another lunch', {'lunch': 'example-cur-user'}, self.plan(),
                                          'lunch-inputs: no admitted lunch-input plan for this lunch (definitions.lunch')):
            with self.subTest(name=name):
                self.setUp()
                self.binding.update(change)
                self.assertEqual(adapter.run(self.binding, self.s.manifest, '41700000000', '1', root=self.s.repo, lock=lock,
                                             copier=self.copier(), compiler=self.compiler(), pins=self.pins(), plan=plan,
                                             inputs_check=self.checks, fetcher=self.fetch), 3)
                self.assertEqual(self.calls, [])
                self.assertTrue(self.report()['refused'].startswith(word), self.report()['refused'])
                self.assertTrue(self.report()['nothingApplied'])

    def test_a_receipt_that_resolves_into_the_source_tree_refuses_before_the_copy(self):
        source = Path(tempfile.mkdtemp())
        (source/'frameworks').mkdir()
        outside = Path(tempfile.mkdtemp())
        os.symlink(source/'frameworks', outside/'receipts')  # a linked directory: the path itself is outside the tree
        for name, change in (('policyReceipt', {'policyReceipt': {'path': str(outside/'receipts/policy.json'),
                                                                  'sha256': self.s.policy_sha}}),
                             ('bundleReport', {'branch': 'verifyBaseApplyCandidate',
                                               'bundleReport': {'path': str(outside/'receipts/bundle.json'),
                                                                'sha256': self.bundle_sha}})):
            with self.subTest(name=name):
                self.setUp()
                self.binding.update(sourceTree=str(source), **change)
                self.assertIsNone(adapter.tree_file_problem(self.binding[name]['path'], str(source), str(self.parent)))
                self.assertEqual(self.run_adapter(), 3)
                self.assertEqual(self.calls, [])
                self.assertEqual(self.report()['refused'],
                                 f'{name}: the bound path is inside the source tree once resolved; nothing copied')

    def test_a_refusal_part_way_through_the_apply_says_what_the_copy_may_hold(self):
        self.vbac()

        def applier(tree, patches, overlays, bundle, files, applied):
            applied.append(patches[0]['name'])
            raise adapter.Refused('isolated-apply: git apply of x failed after every --check passed')
        self.assertEqual(self.run_adapter('base', applier=applier), 3)
        report = self.report()
        self.assertEqual((report['nothingApplied'], report['applied']), (False, ['rose-edge-launcher3.patch']))
        self.assertEqual([c['id'] for c in report['conditions']], ['base-exact', 'delta-preimages'])
        self.assertFalse([c for c in self.calls if c[0] in ('lunch', 'compile')])

    def admitted(self, inputs_check=None, fetcher=None, unset=('BUILD_ID_frankel', 'TARGET_KERNEL_DIR', 'USE_STOCK_KERNEL'),
                 state='post'):
        return self.run_adapter(state, plan=self.plan(unset), inputs_check=inputs_check, fetcher=fetcher)

    def test_an_admitted_lunch_checks_its_inputs_and_fetches_after_the_branch_and_before_lunch(self):
        self.assertEqual(self.admitted(), 0)
        # the copy omits node_modules but keeps the tracked dl/.find-ignore; the checks get the plan's exact sources
        self.assertEqual(self.calls[:4], [COPY, ('pins', 'final'), ('lunch-inputs', ('vendor/adevtool',)), 'fetch'])
        self.assertEqual([c[0] for c in self.calls[4:6]], ['lunch', 'compile'])
        report = self.report()
        self.assertEqual((report['lunchInputs'], report['adevtoolGit']),
                         ({'contractSha256': 'checked', 'bounds': {'seconds': adapter.LUNCH_INPUT_SECONDS}}, {'ok': True}))

    def test_an_environment_the_contract_unsets_refuses_before_any_lunch_input_or_fetch(self):
        self.assertEqual(self.admitted(unset=('TMPDIR',)), 3)  # the contained environment does set TMPDIR
        self.assertEqual(self.calls, [COPY, ('pins', 'final')])
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
        self.assertEqual(self.calls[:5], [COPY, ('pins', 'base'), ('pins', 'final'), ('lunch-inputs', ('vendor/adevtool',)),
                                          'fetch'])
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
                                     plan=self.plan()), 3)
        self.assertEqual(self.calls, [])  # a plan without the bound fetch: refused before anything is copied

    def test_an_interrupted_stage_is_reported_and_its_session_is_gone_before_the_lock_is_released(self):
        work = Path(tempfile.mkdtemp())
        events, started = [], []

        @contextlib.contextmanager
        def lock(path):
            events.append('locked')
            try:
                yield
            finally:  # the real stage's session must already be gone here
                events.append(('released', [adapter.session_members(pid) for pid in started]))

        real = subprocess.Popen

        def popen(*args, **kwargs):
            proc = real(*args, **kwargs)
            started.append(proc.pid)
            Processes.reap_later(self, pids=(proc.pid,))
            return proc

        calls = []

        def interrupting(seconds):  # the first sample takes the outer timeout's TERM; later sleeps are real
            calls.append(seconds)
            if len(calls) > 1:
                return time.sleep(min(seconds, 0.2))
            end = time.monotonic() + 20
            while not (work/'member').exists():
                self.assertLess(time.monotonic(), end, 'the stage never started')
                time.sleep(0.05)
            raise adapter.Interrupted('SIGTERM')

        def compile_(argv, env, cwd, seconds, out_dir, **stage):
            return adapter.supervised(['bash', '-c', f'trap "" TERM; sleep 600 & echo $! > {work}/member; cat /proc/$!/stat > {work}/member.stat; wait'], env,
                                      '/', 600, out_dir, popen=popen, sleep=interrupting, rss=lambda pid: 0, **stage)
        Processes.reap_later(self, work/'member')
        with unittest.mock.patch.object(adapter, 'INTERRUPT_GRACE_SECONDS', 1):
            self.assertEqual(adapter.run(self.binding, self.s.manifest, '41700000000', '1', root=self.s.repo, lock=lock,
                                         copier=self.copier(), compiler=compile_, pins=self.pins(),
                                         policy_loader=lambda path: (POLICY, self.s.policy_sha), plan=self.plan(),
                                         inputs_check=self.checks, fetcher=self.fetch), 3)
        report = self.report()
        self.assertEqual((report['result'], report['nothingApplied']), ('interrupted', True))
        self.assertEqual(report['interrupted']['stages'], [{'stage': 'lunch', 'started': True, 'sessionEnded': True}])
        self.assertEqual(events, ['locked', ('released', [{}])])
        stage = json.loads((self.parent/'launcher3-compile-41700000000-1/receipts/stage.json').read_text())
        self.assertEqual((stage['sid'], stage['stage'], stage['ended']), (started[0], 'lunch', True))
        self.assertIsInstance(stage['startTime'], int)

    def test_a_stage_whose_session_is_not_confirmed_ended_stops_the_run(self):
        self.lunch_result = (0, None, False)
        self.assertEqual(self.run_adapter(), 3)
        report = self.report()
        self.assertEqual((report['lunch'], report['result']), ({'exit': 0, 'stoppedBy': None, 'sessionEnded': False},
                                                               'refused'))
        self.assertTrue(report['refused'].startswith("containment: lunch's session could not be confirmed ended"))
        self.assertFalse([c for c in self.calls if c[0] == 'compile'])
        self.setUp()
        self.compiler = lambda code, stopped: Run.compiler(self, ended=False)
        self.assertEqual(self.run_adapter(), 3)
        report = self.report()
        self.assertEqual((report['compile']['sessionEnded'], report['result']), (False, 'refused'))
        self.assertTrue(report['refused'].startswith("containment: m's session"))
        self.assertNotIn('sourceProof', report)  # no output of a session that may still run is read

    def test_run_holds_its_lock_through_every_stage_and_until_its_report_is_whole(self):
        receipts = self.parent/'launcher3-compile-41700000000-1/receipts'

        def held():
            fd = os.open(receipts/'run.lock', os.O_RDWR)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return False
            except BlockingIOError:
                return True
            finally:
                os.close(fd)
        seen, stages, real = [], Run.compiler(self), adapter.write_once

        def compiler(*args, **kwargs):
            seen.append(('stage', held()))
            return stages(*args, **kwargs)

        def write_once(path, document):
            if Path(path).name == 'report.json':
                seen.append(('report', held()))
            return real(path, document)
        self.compiler = lambda code, stopped: compiler
        with unittest.mock.patch.object(adapter, 'write_once', side_effect=write_once):
            self.assertEqual(self.run_adapter(), 0)
        self.assertEqual(seen, [('stage', True), ('stage', True), ('report', True)])
        self.assertFalse(held())  # released once run returns

    def test_an_interrupt_while_a_refusal_is_recorded_is_recorded_beside_it(self):
        self.binding['adevtoolGit'] = None  # a refusal before anything is copied
        real, calls = adapter.record_refusal, []

        def record(report, error, applied):
            calls.append(str(error))
            if len(calls) == 1:
                raise adapter.Interrupted('SIGTERM')  # the first signal lands as the refusal is recorded
            return real(report, error, applied)
        with unittest.mock.patch.object(adapter, 'record_refusal', side_effect=record), \
                self.assertRaises(adapter.Interrupted):  # main exits 3
            self.run_adapter()
        report = self.report()
        self.assertEqual((report['result'], report['interrupted']['signal']), ('refused', 'SIGTERM'))
        self.assertTrue(report['refused'].startswith('adevtool_git_missing'))

    def test_the_report_is_written_whole_when_the_first_signal_lands_just_as_it_is_due(self):
        path, entered = Path(tempfile.mkdtemp())/'report.json', []
        enter = adapter.deferred.__enter__

        def interrupted_enter(deferral):
            if not entered:
                entered.append(True)
                raise adapter.Interrupted('SIGTERM')  # before the deferral holds anything
            return enter(deferral)
        with unittest.mock.patch.object(adapter.deferred, '__enter__', interrupted_enter), \
                self.assertRaises(adapter.Interrupted):
            adapter.write_report(path, {'result': 'incomplete'}, [])
        written = json.loads(path.read_text())
        self.assertEqual((written['result'], written['interrupted']['signal'], written['nothingApplied']),
                         ('incomplete', 'SIGTERM', True))

    def test_a_signal_while_the_report_is_written_waits_until_it_is_whole(self):
        work = Path(tempfile.mkdtemp())
        script = work/'report.py'
        script.write_text(f"""import importlib.util, json, os, signal, sys, time
spec = importlib.util.spec_from_file_location('a', {str(ROOT/'scripts/launcher3-compile.py')!r})
a = importlib.util.module_from_spec(spec); spec.loader.exec_module(a)
real = a.write_once
def write_once(path, document):
    os.kill(os.getpid(), signal.SIGTERM)  # arrives while the report is written
    time.sleep(0.2)
    return real(path, document)
a.write_once = write_once
try:
    with a.interrupts():
        a.write_report({str(work/'report.json')!r}, {{'result': 'compiled-and-proven'}}, [])
except a.Interrupted as error:
    print('after', error)
""")
        result = subprocess.run([sys.executable, str(script)], capture_output=True, text=True, timeout=60)
        self.assertEqual((result.returncode, result.stdout), (0, 'after SIGTERM\n'), result.stderr)
        self.assertEqual(json.loads((work/'report.json').read_text()), {'result': 'compiled-and-proven'})

    def test_an_interrupt_after_the_result_keeps_it_and_still_exits_3(self):
        @contextlib.contextmanager
        def lock(path):  # the outer timeout's TERM lands while the lock is released, after the compile
            yield
            raise adapter.Interrupted('SIGTERM')
        self.assertEqual(adapter.run(self.binding, self.s.manifest, '41700000000', '1', root=self.s.repo, lock=lock,
                                     copier=self.copier(), compiler=self.compiler(), pins=self.pins(),
                                     policy_loader=lambda path: (POLICY, self.s.policy_sha), plan=self.plan(),
                                     inputs_check=self.checks, fetcher=self.fetch), 3)
        report = self.report()
        self.assertEqual((report['result'], report['interrupted']['signal'], report['interrupted']['stages']),
                         ('incomplete', 'SIGTERM', []))  # the compile's own result stays; the interruption is beside it

    def test_finish_waits_for_the_run_and_ends_a_stage_session_that_outlived_it(self):
        run_root = self.parent/'launcher3-compile-41700000000-1'
        receipts = run_root/'receipts'
        receipts.mkdir(parents=True)
        (run_root/'tree').mkdir()
        lock_fd = adapter.hold_run_lock(receipts)  # a run that still holds its lock: nothing is removed
        upload = Path(tempfile.mkdtemp())/'upload'
        self.assertEqual(adapter.finish(self.binding, '41700000000', '1', upload, wait=1), 3)
        cleanup = json.loads((upload/'cleanup.json').read_text())
        self.assertEqual((cleanup['run']['lock'], cleanup['removed']), ('held', []))
        self.assertIn('run may still use the tree', cleanup['left'][0]['reason'])
        self.assertTrue((run_root/'tree').is_dir())
        os.close(lock_fd)
        # run is gone, its recorded stage session is not: finish ends it, then removes
        work = Path(tempfile.mkdtemp())
        proc = subprocess.Popen(['bash', '-c', f'trap "" TERM; sleep 600 & echo $! > {work}/member; cat /proc/$!/stat > {work}/member.stat; wait'],
                                start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        self.addCleanup(lambda: proc.wait(timeout=30))
        Processes.reap_later(self, work/'member', pids=(proc.pid,))
        end = time.monotonic() + 20
        while not ((work/'member').exists() and (work/'member').read_text().strip()):
            self.assertLess(time.monotonic(), end, 'the stage never started')
            time.sleep(0.05)
        adapter.stage_recorder(receipts)(proc.pid, 'm')
        upload = Path(tempfile.mkdtemp())/'upload'
        with unittest.mock.patch.object(adapter, 'KILL_GRACE_SECONDS', 1):
            self.assertEqual(adapter.finish(self.binding, '41700000000', '1', upload, wait=1), 0)
        cleanup = json.loads((upload/'cleanup.json').read_text())
        self.assertEqual((cleanup['run']['lock'], cleanup['stage']['stage'], cleanup['stage']['ended']),
                         ('released', 'm', True))
        self.assertIn('tree', cleanup['removed'])
        self.assertFalse(Processes.live(int((work/'member').read_text())))

    def finish_run_root(self):
        run_root = self.parent/'launcher3-compile-41700000000-1'
        (run_root/'receipts').mkdir(parents=True)
        (run_root/'tree').mkdir()
        (run_root/'receipts/report.json').write_text('{}')
        return run_root, run_root/'receipts', Path(tempfile.mkdtemp())/'upload'

    def test_finish_removes_nothing_while_a_recorded_session_is_not_confirmed_ended(self):
        run_root, receipts, upload = self.finish_run_root()
        unconfirmed = {'stage': 'm', 'sid': 4242, 'ended': False}
        with unittest.mock.patch.object(adapter, 'end_recorded', return_value=unconfirmed):
            self.assertEqual(adapter.finish(self.binding, '41700000000', '1', upload, wait=1), 3)
        cleanup = json.loads((upload/'cleanup.json').read_text())
        self.assertEqual((cleanup['run']['lock'], cleanup['stage'], cleanup['removed'], cleanup['uploaded']),
                         ('absent', unconfirmed, [], ['report.json']))
        self.assertIn('could not be confirmed ended', cleanup['left'][0]['reason'])
        self.assertTrue((run_root/'tree').is_dir())

    def test_finish_still_uploads_when_its_lock_or_stage_record_cannot_be_used(self):
        run_root, receipts, upload = self.finish_run_root()
        os.symlink(receipts/'elsewhere', receipts/'run.lock')  # opened without following: ELOOP
        self.assertEqual(adapter.finish(self.binding, '41700000000', '1', upload, wait=1), 3)
        cleanup = json.loads((upload/'cleanup.json').read_text())
        self.assertEqual((cleanup['run']['lock'], cleanup['removed'], cleanup['uploaded']),
                         ('unreadable (OSError)', [], ['report.json']))
        self.assertTrue((run_root/'tree').is_dir())
        (receipts/'run.lock').unlink()
        upload = Path(tempfile.mkdtemp())/'upload'
        with unittest.mock.patch.object(adapter, 'end_recorded', side_effect=PermissionError(1, 'not permitted')):
            self.assertEqual(adapter.finish(self.binding, '41700000000', '1', upload, wait=1), 3)
        cleanup = json.loads((upload/'cleanup.json').read_text())
        self.assertEqual((cleanup['stage']['ended'], cleanup['stage']['why'], cleanup['uploaded'], cleanup['removed']),
                         (False, 'PermissionError while ending it', ['report.json'], []))

    def test_a_stage_record_run_marked_ended_or_that_names_no_session_is_never_signalled(self):
        receipts = Path(tempfile.mkdtemp())
        tick = iter(range(1000))
        cases = (({'sid': os.getpid(), 'stage': 'm', 'startTime': None, 'ended': True}, False, 'the leader start time was not recorded'),
                 ({'sid': 0, 'stage': 'm', 'startTime': None}, False, 'the stage record names no stage session'),
                 ({'sid': 1, 'stage': 'm', 'startTime': None}, False, 'the stage record names no stage session'))
        for record, ended, why in cases:
            with self.subTest(record=record):
                (receipts/'stage.json').write_text(json.dumps(record))
                with unittest.mock.patch.object(adapter.os, 'killpg') as killpg:
                    self.assertEqual(adapter.end_recorded(receipts, clock=lambda: next(tick), sleep=lambda s: None),
                                     {'stage': 'm', 'sid': record['sid'], 'ended': ended, 'why': why})
                killpg.assert_not_called()
        # an unreadable process table never confirms a recorded session gone
        gone = subprocess.Popen(['true'])
        gone.wait()
        (receipts/'stage.json').write_text(json.dumps({'sid': gone.pid, 'stage': 'm', 'startTime': None}))
        self.assertEqual(adapter.end_recorded(receipts, clock=lambda: next(tick), sleep=lambda s: None, grace=1,
                                              members=lambda sid: None), {'stage': 'm', 'sid': gone.pid, 'ended': False})

    def test_finish_never_signals_a_recorded_id_that_now_names_another_process(self):
        run_root = self.parent/'launcher3-compile-41700000000-1'
        receipts = run_root/'receipts'
        receipts.mkdir(parents=True)
        (receipts/'stage.json').write_text(json.dumps({'sid': os.getpid(), 'stage': 'm', 'startTime': 1}))
        upload = Path(tempfile.mkdtemp())/'upload'
        with unittest.mock.patch.object(adapter.os, 'killpg') as killpg:
            self.assertEqual(adapter.finish(self.binding, '41700000000', '1', upload, wait=1), 0)
        killpg.assert_not_called()
        self.assertEqual(json.loads((upload/'cleanup.json').read_text())['stage']['why'], 'its ID now names another process')

    def test_signal_at_first_finalizer_instruction_still_reports_and_releases_lock(self):
        import inspect
        lines, first = inspect.getsourcelines(adapter.run)
        target = first + next(i for i, line in enumerate(lines) if 'late = sys.exc_info()[1]' in line)
        fired = []

        def trace(frame, event, arg):
            if event == 'line' and frame.f_code is adapter.run.__code__ and frame.f_lineno == target and not fired:
                fired.append(True)
                os.kill(os.getpid(), signal.SIGTERM)
            return trace
        try:
            with adapter.interrupts(), self.assertRaises(adapter.Interrupted):
                sys.settrace(trace)
                self.run_adapter()
        finally:
            sys.settrace(None)
        self.assertEqual(fired, [True])
        self.assertEqual(self.report()['interrupted']['signal'], 'SIGTERM')
        receipts = self.parent/'launcher3-compile-41700000000-1/receipts'
        fd, _ = adapter.wait_for_run(receipts, 0)
        self.assertNotIn(fd, (None, -1))
        os.close(fd)

    def test_ordinary_stage_failure_is_a_sanitized_refusal_with_containment(self):
        def compiler(*args, **kwargs):
            def fail(path):
                raise OSError('do not publish this raw error')
            with unittest.mock.patch.object(adapter, 'end_session', return_value=False):
                return adapter.supervised(['stand-in'], {}, '/', 10, '/',
                                          popen=lambda *a, **k: SafetyRegressions.Leader(), used=fail)
        self.compiler = lambda *a, **k: compiler
        self.assertEqual(self.run_adapter(), 3)
        report = self.report()
        self.assertEqual(report['result'], 'refused')
        self.assertEqual(report['stageFailure'], {'stage': 'stage', 'started': True, 'sessionEnded': False})
        self.assertIn('OSError', report['refused'])
        self.assertNotIn('do not publish', json.dumps(report))
        self.assertNotIn('sourceProof', report)

    def test_failed_lunch_still_requires_confirmed_containment(self):
        self.lunch_result = (1, 'timeout', False)
        self.assertEqual(self.run_adapter(), 3)
        self.assertEqual(self.report()['result'], 'refused')
        self.assertIn('containment: lunch', self.report()['refused'])
        self.assertFalse([c for c in self.calls if c[0] == 'compile'])

    def test_finish_preserves_work_when_an_ended_mark_is_not_confirmed(self):
        run_root, receipts, upload = self.finish_run_root()
        (receipts/'stage.json').write_text(json.dumps({'sid': 424242, 'stage': 'm', 'startTime': 7, 'ended': True}))
        with unittest.mock.patch.object(adapter, 'proc_stat', return_value=None), \
                unittest.mock.patch.object(adapter, 'session_members', return_value={424243: 424242}), \
                unittest.mock.patch.object(adapter.os, 'killpg') as kill:
            self.assertEqual(adapter.finish(self.binding, '41700000000', '1', upload), 3)
        kill.assert_not_called()
        report = json.loads((upload/'cleanup.json').read_text())
        self.assertEqual((report['removed'], report['uploaded'], report['stage']['ended']), ([], ['report.json'], False))
        self.assertTrue((run_root/'tree').is_dir())

    def test_finish_upload_error_keeps_cleanup_and_other_receipts(self):
        run_root, receipts, upload = self.finish_run_root()
        (receipts/'classification.json').write_text('{}')
        original = adapter.write_bytes_once

        def write(path, data):
            if path.name == 'report.json':
                raise OSError('injected upload failure')
            return original(path, data)
        with unittest.mock.patch.object(adapter, 'write_bytes_once', side_effect=write):
            self.assertEqual(adapter.finish(self.binding, '41700000000', '1', upload), 3)
        report = json.loads((upload/'cleanup.json').read_text())
        self.assertEqual(report['uploaded'], ['classification.json'])
        self.assertIn('tree', report['removed'])
        self.assertEqual(report['left'][0]['path'], 'receipts/report.json')

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

    def test_the_environment_unsets_only_plain_names_besides_git(self):
        record = json.loads(LUNCH_INPUTS.read_text())
        record['environment']['unset'].append('LD_*')
        target = Path(tempfile.mkdtemp())/'inputs.json'
        target.write_bytes(json.dumps(record).encode())
        with self.assertRaisesRegex(adapter.Invalid, 'plain variables only'):
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

    def test_an_exact_source_that_is_not_a_plain_directory_refuses(self):
        shutil.move(str(self.f.tree/'device/kernels'), str(self.f.base/'kernels-elsewhere'))
        os.symlink(self.f.base/'kernels-elsewhere', self.f.tree/'device/kernels')
        with self.assertRaisesRegex(adapter.Refused, 'device/kernels is not a plain directory in the copy'):
            self.check()

    def test_a_file_changed_between_the_walk_and_its_read_refuses_before_hashing(self):
        target = self.f.tree/'vendor/adevtool/bin/run'
        walked = os.lstat(target)
        os.chmod(target, 0o644)
        with self.assertRaisesRegex(adapter.Refused, 'changed while it was hashed'):
            adapter.read_walked(target, walked, 'adevtool/bin/run')
        expected = adapter.lunch_listing(self.f.inputs, 'vendor/adevtool', self.f.repo)
        with self.assertRaisesRegex(adapter.Refused, 'pin_timeout'):
            adapter.exact_tree(self.f.tree/'vendor/adevtool', expected, deadline=time.monotonic() - 1)

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

    def test_the_find_ignore_marker_keeps_everything_below_it_as_git_does(self):
        repo = Path(tempfile.mkdtemp())
        git('init', '-q', str(repo), cwd=repo)
        (repo/'.gitignore').write_bytes((ROOT/'os/tests/fixtures/frankel-lunch-inputs/vendor/adevtool/.gitignore').read_bytes())
        (repo/'dl/.find-ignore').mkdir(parents=True)
        (repo/'dl/.find-ignore/x').write_bytes(b'')
        ignored = subprocess.run(['git', '-C', str(repo), 'check-ignore', '-q', '--no-index', 'dl/.find-ignore/x'],
                                 capture_output=True).returncode == 0
        self.assertEqual((ignored, adapter.adevtool_ignored('dl/.find-ignore/x', False)), (False, False))

    def test_the_real_copy_omits_the_ignored_paths_and_refuses_a_link_that_would_dangle(self):
        source = Path(tempfile.mkdtemp())/'source'
        for relative, data in (('vendor/adevtool/README.md', b'r'), ('vendor/adevtool/dl/.find-ignore', b''),
                               ('vendor/adevtool/node_modules/p/index.js', b'js'), ('vendor/adevtool/dl/frankel.zip', b'zip'),
                               ('vendor/adevtool/lib/x.js', b'lib'), ('other/kept.txt', b'k')):
            (source/relative).parent.mkdir(parents=True, exist_ok=True)
            (source/relative).write_bytes(data)
        (source/'vendor/adevtool/dl/unpacked').mkdir()
        fs = type('fs', (), {'f_bavail': 1 << 42, 'f_frsize': 1})
        project = 'vendor/adevtool/'
        omit = lambda rel, is_dir: rel.startswith(project) and adapter.adevtool_ignored(rel[len(project):], is_dir)  # noqa: E731
        copy = Path(tempfile.mkdtemp())/'tree'
        result = adapter.copy_tree(source, copy, statvfs=lambda p: fs, omit=omit)
        kept = sorted(str(p.relative_to(copy)) for p in copy.rglob('*') if p.is_file())
        self.assertEqual(kept, ['other/kept.txt', 'vendor/adevtool/README.md', 'vendor/adevtool/dl/.find-ignore'])
        # node_modules, node_modules/p, node_modules/p/index.js, dl/frankel.zip, lib and lib/x.js
        self.assertEqual(result['omitted'], 6)
        os.symlink('../vendor/adevtool/node_modules/../README.md', source/'other/via-node-modules')
        with self.assertRaisesRegex(adapter.Refused, 'kept links dangle in the copy, first: other/via-node-modules'):
            adapter.copy_tree(source, Path(tempfile.mkdtemp())/'tree', statvfs=lambda p: fs, omit=omit)

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

    def test_the_fetch_disables_auto_maintenance_observed_with_a_positive_control(self):
        """Trace real Git against the synthetic local source; the unprotected control must exercise maintenance."""
        traces = []

        def traced(argv, env=None, **kwargs):
            trace = self.f.base/f'trace-{len(traces)}'
            traces.append((argv, trace))
            return subprocess.Popen(argv, env={**env, 'GIT_TRACE': str(trace)}, **kwargs)

        steps = adapter.fetch_steps
        for protected in (True, False):
            with self.subTest(protected=protected):
                self.f.make_copy()
                self.record = {}
                traces.clear()
                for trace in self.f.base.glob('trace-*'):
                    trace.unlink()
                control = lambda *args: [[a for a in step if a != '--no-auto-maintenance'] for step in steps(*args)]
                with unittest.mock.patch.object(adapter, 'fetch_steps', steps if protected else control):
                    self.fetch(popen=traced)
                (fetch_trace,) = [trace for argv, trace in traces if 'fetch' in argv]
                self.assertEqual('maintenance run --auto' in fetch_trace.read_text(), not protected)

    def test_maintenance_markers_refuse_acceptance_and_remove_only_the_created_git(self):
        real = adapter.gitdir_problems
        dotgit = self.f.tree/'vendor/adevtool/.git'
        for name in ('gc.pid', 'gc.log', 'objects/maintenance.lock'):
            for kind in ('file', 'directory', 'dangling-link'):
                with self.subTest(mark=name, kind=kind):
                    self.f.make_copy()
                    self.record = {}
                    before = self.f.work_state()

                    def marked(*args, **kwargs):
                        mark = dotgit/name
                        if kind == 'file':
                            mark.write_bytes(b'')  # presence alone refuses, including an empty marker
                        elif kind == 'directory':
                            mark.mkdir()
                        else:
                            mark.symlink_to(self.f.base/'absent')
                        return real(*args, **kwargs)

                    with unittest.mock.patch.object(adapter, 'gitdir_problems', side_effect=marked):
                        with self.assertRaisesRegex(adapter.Refused, f'{name} exists: maintenance/gc marker is forbidden'):
                            self.fetch()
                    self.assertEqual((self.record['ok'], self.record['createdRemoved'], self.record['sessionEnded']),
                                     (False, True, True))
                    self.assertFalse(os.path.lexists(dotgit))
                    self.assertEqual(self.f.work_state(), before)
                    self.assertEqual(git('rev-parse', 'HEAD', cwd=self.f.upstream).decode().strip(), self.f.revision)

    def test_git_without_the_maintenance_option_refuses_without_retry_or_acceptance(self):
        calls = []
        before = self.f.work_state()

        def old_git(argv, **kwargs):
            calls.append(argv)
            if 'fetch' in argv:
                # A synthetic Git that rejects the new option. Use a real child so containment still runs.
                return subprocess.Popen([sys.executable, '-c',
                                         'import sys; sys.exit(129 if "--no-auto-maintenance" in sys.argv else 0)',
                                         *argv], **kwargs)
            return subprocess.Popen(argv, **kwargs)

        with unittest.mock.patch.object(adapter, 'gitdir_problems', side_effect=AssertionError('acceptance ran')):
            with self.assertRaisesRegex(adapter.Refused, 'fetch failed: exit 129'):
                self.fetch(popen=old_git)
        self.assertEqual([argv[3] if argv[1] == '-C' else argv[1] for argv in calls], ['init', 'fetch'])
        self.assertEqual((self.record['ok'], self.record['createdRemoved'], self.record['sessionEnded']),
                         (False, True, True))
        self.assertFalse(os.path.lexists(self.f.tree/'vendor/adevtool/.git'))
        self.assertEqual(self.f.work_state(), before)

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

    def test_the_fetch_refuses_before_writing_without_git_or_with_a_used_home(self):
        with unittest.mock.patch.object(adapter, 'GIT_PATH', str(self.f.base/'no-git-here')):
            with self.assertRaisesRegex(adapter.Refused, 'no git on'):
                self.fetch()
        self.assertFalse(os.path.lexists(self.f.tree/'vendor/adevtool/.git'))
        (self.f.tree.parent/'tmp/adevtool-git-home').mkdir(parents=True)
        with self.assertRaisesRegex(adapter.Refused, 'fetch home already exists'):
            self.fetch()
        self.assertFalse(os.path.lexists(self.f.tree/'vendor/adevtool/.git'))

    def test_a_success_needs_its_final_evidence_or_the_git_is_removed(self):
        real, calls = adapter.git_output, []

        def flaky(argv, *args, **kwargs):
            if argv[-2:] == ['rev-parse', 'HEAD']:
                calls.append(argv)
                if len(calls) == 2:  # the final one, after every acceptance item passed
                    return None, b''
            return real(argv, *args, **kwargs)
        with unittest.mock.patch.object(adapter, 'git_output', flaky):
            with self.assertRaisesRegex(adapter.Refused, 'final rev-parse HEAD'):
                self.fetch()
        self.assertFalse(os.path.lexists(self.f.tree/'vendor/adevtool/.git'))
        self.assertEqual((self.record['ok'], self.record['createdRemoved']), (False, True))

    def test_the_mount_table_names_bind_mounts_too(self):
        text = ('36 35 98:0 /mnt1 /mnt/parent rw,noatime master:1 - ext3 /dev/root rw\n'
                '37 36 98:0 /x /run/tree/vendor/adevtool/.git/objects rw - ext4 /dev/sda rw\n'
                '38 36 98:0 /y /with\\040space rw - ext4 /dev/sda rw\n')
        self.assertEqual(adapter.mount_points(text), ['/mnt/parent', '/run/tree/vendor/adevtool/.git/objects', '/with space'])

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

    def test_an_interrupt_during_the_fetch_removes_its_git_and_goes_on(self):
        def interrupt(*args, **kwargs):
            raise adapter.Interrupted('SIGTERM')
        with unittest.mock.patch.object(adapter, 'gitdir_problems', side_effect=interrupt):
            with self.assertRaises(adapter.Interrupted):
                self.fetch()
        self.assertFalse(os.path.lexists(self.f.tree/'vendor/adevtool/.git'))
        self.assertEqual((self.record['ok'], self.record['createdRemoved']), (False, True))
        self.assertIn('interrupted', self.record['error'])

    def test_bounded_git_stops_at_its_deadline_or_its_bytes(self):
        clock = time.monotonic
        self.assertEqual(adapter.bounded_git(['sleep', '30'], dict(os.environ), '/', clock() - 1, self.f.base, 1 << 30),
                         (None, 'timeout', True))  # nothing was started
        code, stopped, ended = adapter.bounded_git(['sleep', '30'], dict(os.environ), '/', clock() + 60, self.f.base, 10,
                                                   measure=lambda path: 11)
        self.assertEqual((stopped, ended), ('gitBytes', True))
        self.assertEqual(adapter.bounded_git(['true'], dict(os.environ), '/', clock() + 60, self.f.base, 10,
                                             measure=lambda path: 0), (0, None, True))

    def test_each_fetch_step_records_its_session_and_marks_it_ended(self):
        calls = []
        self.fetch(stage=lambda pid, label, ended=False: calls.append((label, ended)))
        steps = adapter.fetch_steps(self.f.tree/'vendor/adevtool', self.f.plan()['url'],
                                    self.f.inputs['gitRequirement']['revision'])
        self.assertEqual(calls, [('adevtool-git', False), ('adevtool-git', True)] * len(steps))

    def test_a_fetch_step_whose_session_is_not_confirmed_ended_preserves_its_git(self):
        dotgit = self.f.tree/'vendor/adevtool/.git'

        def step(*args, **kwargs):
            dotgit.mkdir()
            (dotgit/'still-owned').write_text('keep')
            return 0, None, False
        with unittest.mock.patch.object(adapter, 'bounded_git', side_effect=step):
            with self.assertRaisesRegex(adapter.Refused, 'its session could not be confirmed ended'):
                self.fetch()
        self.assertFalse(self.record['ok'])
        self.assertIsNone(self.record['createdRemoved'])
        self.assertEqual((dotgit/'still-owned').read_text(), 'keep')

    def test_an_interrupted_unconfirmed_fetch_preserves_its_git(self):
        dotgit = self.f.tree/'vendor/adevtool/.git'

        def step(*args, **kwargs):
            dotgit.mkdir()
            error = adapter.Interrupted('SIGTERM')
            error.stages = [{'stage': 'adevtool-git', 'started': True, 'sessionEnded': False}]
            raise error
        with unittest.mock.patch.object(adapter, 'bounded_git', side_effect=step), self.assertRaises(adapter.Interrupted):
            self.fetch()
        self.assertTrue(dotgit.is_dir())
        self.assertIsNone(self.record['createdRemoved'])
        self.assertFalse(self.record['sessionEnded'])


class DedicatedLane(unittest.TestCase):
    def test_committed_capacity_validates_and_legacy_binding_cannot_activate_the_adapter(self):
        self.assertEqual(lane.validate(BINDING)['status'], 'bound')
        self.assertEqual(lane.validate(BINDING)['runnerLabel'], 'roseos-launcher3-compile-01')
        legacy = ROOT/'os/build-only/launcher3-compile-host.json'
        with self.assertRaisesRegex(adapter.Invalid, 'legacy host binding is retired'):
            adapter.check(legacy)
        # Historical task roots and WORK_DIRS remain usable by Release's old-run reclaim policy.
        old = json.loads(legacy.read_text())
        self.assertEqual(str(adapter.task_root(old, '1', '1')), '/home/pixelbuild/rose-actions/launcher3-compile-1-1')
        self.assertEqual(adapter.WORK_DIRS, ('tree', 'out', 'home', 'tmp'))

    def test_every_capacity_field_is_required_even_with_status_bound(self):
        template = dedicated_binding()
        fields = [('taskParent',)] + [('lane', k) for k, value in template['lane'].items()
                                      if not isinstance(value, dict) and k not in ('status', 'workflow')]
        fields += [('lane', group, k) for group in ('vm', 'disk', 'controller') for k in template['lane'][group]]
        for path in fields:
            with self.subTest(field=path):
                binding = dedicated_binding()
                parent = binding
                for key in path[:-1]:
                    parent = parent[key]
                parent[path[-1]] = None
                with self.assertRaises(lane.Unbound):
                    lane.validate(binding)
        self.assertEqual(lane.validate(template), template['lane'])
        template['lane']['status'] = 'unbound'
        with self.assertRaises(lane.Unbound):
            lane.validate(template)

    def test_capacity_shape_paths_and_controller_variables_are_strict(self):
        edits = [lambda b: b['lane'].update(extra=True), lambda b: b['lane']['vm'].update(instanceId=123),
                 lambda b: b['lane']['vm'].update(instance='../foreign'), lambda b: b['lane']['disk'].update(id='0'),
                 lambda b: b['lane']['disk'].update(resource='projects/elsewhere/zones/x/disks/y'),
                 lambda b: b['lane'].update(infraEvidence=''), lambda b: b['lane'].update(queue='${{ inputs.queue }}'),
                 lambda b: b['lane']['controller'].update(identityVariable='GCP_CONTROLLER_SERVICE_ACCOUNT'),
                 lambda b: b['lane']['controller'].update(providerVariable='GCP_WORKLOAD_IDENTITY_PROVIDER'),
                 lambda b: b.update(taskParent='/elsewhere'), lambda b: b.update(taskParent='/home/pixelbuild/../x'),
                 lambda b: b['lane'].update(lockPath=b['taskParent']+'/shared.lock'),
                 lambda b: b['lane'].update(lockPath='/home'), lambda b: b['lane'].update(storageRoot='/'),
                 lambda b: b.update(sourceTree='/production/source'),
                 lambda b: b['lane'].update(workflow='.github/workflows/os-release.yml')]
        for edit in edits:
            with self.subTest(edit=edits.index(edit)):
                binding = dedicated_binding()
                edit(binding)
                with self.assertRaises(lane.Invalid):
                    lane.validate(binding)

    def test_production_vm_aliases_labels_queues_and_disk_ids_refuse(self):
        capacity = json.loads((ROOT/'os/release-pipeline/lanes/capacity.json').read_text())
        for host in capacity['hosts'].values():
            for field in ('vm', 'runnerLabel', 'queue'):
                with self.subTest(host=host['vm']['instance'], field=field):
                    binding = dedicated_binding()
                    if field == 'vm':
                        binding['lane']['vm'].update(host['vm'])
                        vm = binding['lane']['vm']
                        binding['lane']['disk']['resource'] = f'projects/{vm["project"]}/zones/{vm["zone"]}/disks/test-disk'
                    else:
                        binding['lane'][field] = host[field]
                    with self.assertRaisesRegex(lane.Invalid, 'shares another lane'):
                        lane.validate(binding)
            for field in ('runnerLabel', 'queue'):
                with self.subTest(case_alias=field, host=host['vm']['instance']):
                    binding = dedicated_binding()
                    binding['lane'][field] = host[field].swapcase()
                    with self.assertRaisesRegex(lane.Invalid, 'shares another lane'):
                        lane.validate(binding)
        yogi = json.loads((ROOT/'os/ota/yogi.json').read_text())['builder']
        for field, value in (('instanceId', lane.FRANKEL_INSTANCE_ID), ('instanceId', yogi['instanceId']),
                             ('instanceId', yogi['retired']['instanceId']),
                             ('id', yogi['restoredDiskId'])):
            with self.subTest(alias=field, value=value):
                binding = dedicated_binding()
                binding['lane']['disk' if field == 'id' else 'vm'][field] = value
                with self.assertRaises(lane.Invalid):
                    lane.validate(binding)
        for label in yogi['evidence']['runner']['labels']:
            with self.subTest(alias=label):
                binding = dedicated_binding()
                binding['lane']['runnerLabel'] = label
                with self.assertRaises(lane.Invalid):
                    lane.validate(binding)

    def test_future_agent_capacity_is_also_reserved_without_changing_its_api(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = ['os/release-pipeline/lanes/capacity.json', 'os/build-only/mustang-lane.json', 'os/ota/yogi.json']
            for path in paths:
                target = root/path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes((ROOT/path).read_bytes())
            path = root/paths[0]
            capacity = json.loads(path.read_text())
            other = dedicated_binding()['lane']
            capacity['hosts']['agent-synthetic'] = other
            capacity['lanes']['agent']['host'] = 'agent-synthetic'
            path.write_text(json.dumps(capacity))
            with self.assertRaisesRegex(lane.Invalid, 'shares another lane'):
                lane.validate(dedicated_binding(), root)

    def test_legacy_guard_refuses_even_before_reading_a_fully_bound_override(self):
        for workflow in ('.github/workflows/os-release.yml', '.github/workflows/yogi-ota.yml'):
            env = dedicated_env()
            env['GITHUB_WORKFLOW_REF'] = f'{lane.REPOSITORY}/{workflow}@refs/heads/main'
            with self.subTest(workflow=workflow), unittest.mock.patch.dict(os.environ, env, clear=True), \
                    unittest.mock.patch.object(adapter, 'check') as checked:
                self.assertEqual(adapter.main(['check', '--binding', '/never-read.json']), 2)
                checked.assert_not_called()

    def test_worker_requires_its_exact_runner_workflow_source_and_fresh_attempt(self):
        env = dedicated_env()
        self.assertEqual(lane.invocation(env)['runId'], '12345')
        for key, value in (('GITHUB_REPOSITORY', 'foreign/repo'), ('GITHUB_REF', 'refs/heads/topic'),
                           ('GITHUB_EVENT_NAME', 'push'), ('GITHUB_WORKFLOW_SHA', 'b' * 40),
                           ('GITHUB_RUN_ID', '0'), ('GITHUB_RUN_ATTEMPT', '2'), ('GITHUB_SHA', 'tag')):
            with self.subTest(field=key), self.assertRaises(lane.Invalid):
                lane.invocation({**env, key: value})
        binding = dedicated_binding()
        binding['sourceTree'] = '/home/pixelbuild/source'
        for run_id, attempt, name in (('other', '1', 'synthetic-launcher3'), ('12345', '2', 'synthetic-launcher3'),
                                      ('12345', '1', 'immortal-os-builder')):
            with self.subTest(worker=name, run=run_id, attempt=attempt), self.assertRaises(lane.Invalid):
                lane.worker(binding, {**env, 'RUNNER_NAME': name}, run_id, attempt)

    def test_worker_refuses_linked_storage_paths_before_run_or_cleanup(self):
        with tempfile.TemporaryDirectory() as tmp:
            storage = Path(tmp)/'storage'
            storage.mkdir()
            linked = Path(tmp)/'linked'
            linked.symlink_to(storage, target_is_directory=True)
            binding = dedicated_binding()
            binding['lane'].update(storageRoot=str(linked), lockPath=str(linked/'build.lock'))
            binding.update(taskParent=str(linked/'runs'), sourceTree=str(linked/'source'))
            with self.assertRaisesRegex(lane.Invalid, 'resolve through links'):
                lane.worker(binding, dedicated_env(), '12345', '1')

    def test_lock_is_only_the_dedicated_binding_and_json_duplicates_refuse(self):
        binding = dedicated_binding()
        self.assertEqual(adapter.lock_path(binding), '/home/pixelbuild/launcher3.lock')
        with self.assertRaisesRegex(lane.Invalid, 'duplicate'):
            lane.strict_json('{"lane":null,"lane":{}}')

    def test_run_and_finish_cannot_bypass_worker_admission(self):
        binding = {**dedicated_binding(), 'sourceTree': '/home/pixelbuild/source'}
        for action in ('run', 'finish'):
            args = [action, '--run-id', '12345', '--attempt', '1']
            if action == 'finish':
                args += ['--upload-dir', '/not-created']
            with self.subTest(action=action), \
                    unittest.mock.patch.dict(os.environ, {**dedicated_env(), 'RUNNER_NAME': 'foreign'}, clear=True), \
                    unittest.mock.patch.object(adapter, 'check', return_value=(MANIFEST, binding, None)), \
                    unittest.mock.patch.object(adapter, action) as execute:
                self.assertEqual(adapter.main(args), 2)
                execute.assert_not_called()


class DedicatedController(unittest.TestCase):
    class Common:
        def __init__(self, started=True):
            self.calls = []
            self.started = started

        def control_builder(self, action, claim=None, *, builder):
            if claim:
                claim(self.started)
            self.calls.append((action, builder))
            return 'RUNNING' if action == 'start' else 'TERMINATED'

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.record = Path(self.tmp.name)/'ownership.json'
        self.binding, self.env, self.api = dedicated_binding(), dedicated_env(), self.Common()

    def test_start_records_exact_ownership_before_request_and_stop_uses_only_that_vm(self):
        observed = []

        def control(action, claim=None, *, builder=None):
            if claim:
                claim(True)
            observed.append(controller.read_ownership(self.record, self.binding, lane.invocation(self.env)))
            self.assertEqual(builder, self.binding['lane']['vm'])
            return action

        api = unittest.mock.Mock(control_builder=control)
        controller.start(api, self.binding, self.env, self.record)
        controller.stop(api, self.binding, self.env, self.record)
        self.assertEqual(observed, [True, True])

    def test_incomplete_or_foreign_capacity_never_reaches_common_control(self):
        unbound = json.loads(json.dumps(BINDING))
        unbound['lane']['status'] = 'unbound'
        for binding in (unbound, {**self.binding, 'lane': {**self.binding['lane'],
                            'vm': {**self.binding['lane']['vm'], 'instanceId': lane.FRANKEL_INSTANCE_ID}}}):
            with self.subTest(binding=binding['lane']['status']), self.assertRaises(ValueError):
                controller.start(self.api, binding, self.env, self.record)
        self.assertEqual(self.api.calls, [])

    def test_old_implicit_api_is_rejected_without_calling_it(self):
        class Old:
            def control_builder(self, action, claim=None):
                raise AssertionError('implicit API was called')
        with self.assertRaisesRegex(lane.Invalid, 'no fallback'):
            controller.start(Old(), self.binding, self.env, self.record)
        self.assertFalse(self.record.exists())

    def test_the_current_common_api_is_consumed_without_a_global_override(self):
        common = controller.load_module('launcher3_api_shape_test', 'scripts/release-pipeline.py')
        before = dict(common.BUILDER)
        api = common.Production()
        self.assertIs(controller.require_explicit_target(api), api)
        self.assertEqual(common.BUILDER, before)

    def test_missing_or_repeated_claims_are_refused(self):
        def absent(action, claim=None, *, builder):
            return 'RUNNING'
        with self.assertRaisesRegex(lane.Invalid, 'without a durable ownership claim'):
            controller.start(unittest.mock.Mock(control_builder=absent), self.binding, self.env, self.record)
        self.assertFalse(self.record.exists())

        def twice(action, claim=None, *, builder):
            claim(True)
            claim(True)
        with self.assertRaisesRegex(lane.Invalid, 'repeated ownership claim'):
            controller.start(unittest.mock.Mock(control_builder=twice), self.binding, self.env, self.record)
        self.assertTrue(controller.read_ownership(self.record, self.binding, lane.invocation(self.env)))

    def test_failed_ownership_write_prevents_the_start_request(self):
        for directory in (False, True):
            with self.subTest(directory=directory):
                self.record.unlink(missing_ok=True)

                def failed(fd):
                    if stat.S_ISDIR(os.fstat(fd).st_mode) == directory:
                        raise OSError('synthetic durability failure')

                with unittest.mock.patch.object(controller.os, 'fsync', side_effect=failed):
                    with self.assertRaises(OSError):
                        controller.start(self.api, self.binding, self.env, self.record)
                self.assertEqual(self.api.calls, [])
                with self.assertRaisesRegex(lane.Invalid, 'already exists'):
                    controller.start(self.api, self.binding, self.env, self.record)

    def test_existing_running_vm_is_never_stopped(self):
        self.api.started = False
        controller.start(self.api, self.binding, self.env, self.record)
        self.assertIn('skipped', controller.stop(self.api, self.binding, self.env, self.record))
        self.assertEqual([c[0] for c in self.api.calls], ['start'])

    def test_foreign_run_source_vm_or_binding_record_refuses_stop(self):
        controller.start(self.api, self.binding, self.env, self.record)
        good = self.record.read_bytes()
        edits = [lambda r: r['run'].update(runId='999'), lambda r: r['run'].update(runAttempt='2'),
                 lambda r: r['run'].update(source='f' * 40), lambda r: r['run'].update(workflow='foreign'),
                 lambda r: r['vm'].update(instance='immortal-os-builder'), lambda r: r['vm'].update(instanceId='999'),
                 lambda r: r.update(bindingSha256='b' * 64), lambda r: r.update(startedByThisOperation='true'),
                 lambda r: r.update(extra=True), lambda r: r.update(schema='foreign')]
        for edit in edits:
            with self.subTest(edit=edits.index(edit)):
                record = json.loads(good)
                edit(record)
                self.record.write_text(json.dumps(record))
                with self.assertRaisesRegex(lane.Invalid, 'STOP refused'):
                    controller.stop(self.api, self.binding, self.env, self.record)
        self.assertEqual([c[0] for c in self.api.calls], ['start'])

    def test_missing_linked_or_nonregular_ownership_never_calls_stop(self):
        with self.assertRaises(OSError):
            controller.stop(self.api, self.binding, self.env, self.record)
        self.record.symlink_to(Path(self.tmp.name)/'absent')
        with self.assertRaises(OSError):
            controller.stop(self.api, self.binding, self.env, self.record)
        self.record.unlink()
        os.mkfifo(self.record)
        with self.assertRaises(lane.Invalid):
            controller.stop(self.api, self.binding, self.env, self.record)
        self.assertEqual(self.api.calls, [])

    def test_uncertain_start_retains_claim_and_is_not_retried(self):
        def uncertain(action, claim=None, *, builder):
            claim(True)
            self.api.calls.append((action, builder))
            raise RuntimeError('synthetic uncertain result')
        api = unittest.mock.Mock(control_builder=uncertain)
        with self.assertRaises(RuntimeError):
            controller.start(api, self.binding, self.env, self.record)
        self.assertTrue(controller.read_ownership(self.record, self.binding, lane.invocation(self.env)))
        with self.assertRaisesRegex(lane.Invalid, 'already exists'):
            controller.start(api, self.binding, self.env, self.record)
        self.assertEqual([c[0] for c in self.api.calls], ['start'])


if __name__ == '__main__':
    unittest.main()
