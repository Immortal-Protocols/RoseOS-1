"""LAUNCHER3-BUNDLE-REPORT: the producer of base.bundleReport (scripts/launcher3-bundle-report.py).

Synthetic Git repositories, receipt directories and run documents only: no host, network or real receipt. A report
the producer writes from a synthetic integrating run is checked by the adapter's own bundle_report_problems (loaded by
these tests only; the producer never loads it), and every association and observation rule refuses or reports by
name. One test reads this checkout's own os/patches and os/overlays at HEAD against the pinned base.bundleContent."""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT/path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


producer = load('launcher3_bundle_report', 'scripts/launcher3-bundle-report.py')
adapter = load('launcher3_compile', 'scripts/launcher3-compile.py')
CONTRACT = json.loads((ROOT/'os/compile-contracts/launcher3-quickstep-lib.json').read_text())
# OS Product's checker states these prefixes (REQUIRED_OVERLAYS); the contract does not carry them as data yet, so the
# producer refuses the committed contract until it does (see test_the_committed_contract_needs_its_prefixes_as_data).
PREFIXES = ['packages/apps/Launcher3/', 'build/release/']


def sha(data):
    return hashlib.sha256(data).hexdigest()


def git(*args, cwd):
    env = {'PATH': '/usr/bin:/bin', 'HOME': '/nonexistent', 'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': os.devnull,
           'GIT_AUTHOR_NAME': 't', 'GIT_AUTHOR_EMAIL': 't@t', 'GIT_COMMITTER_NAME': 't', 'GIT_COMMITTER_EMAIL': 't@t'}
    return subprocess.run(['git', *args], cwd=cwd, env=env, check=True, capture_output=True).stdout.decode().strip()


L3, BR, FB = 'packages/apps/Launcher3', 'build/release', 'frameworks/base'
PATCHER = {'apply-os-changes.py': b'apply\n', 'background_apks.py': b'background\n', 'integration_copy.py': b'copy\n'}
PATCHES = {'frameworks-base.patch': (FB, b'fb\n'), 'background-control-build-release.patch': (BR, b'br\n'),
           'rose-switcher-launcher3.patch': (L3, b'l3\n')}
OVERLAYS = {f'{L3}/quickstep/A.java': b'a\n', f'{BR}/aconfig/B.bp': b'b\n', f'{FB}/libs/N.java': b'not an input\n',
            'packages/apps/Updater/U.java': b'outside the three projects\n'}
APPLY_ARGS = ['--background-policy', '{policy}', '--agent-tray']


class Integration:
    """A synthetic checkout: the producer and the trusted patcher at HEAD, os/patches, os/overlays and a recipe whose
    receiptDir is a temporary receipt directory; main is HEAD unless moved."""
    def __init__(self, overlays=None, patcher='trusted', patches=None):
        self.root = Path(tempfile.mkdtemp())/'checkout'
        self.receipts = Path(tempfile.mkdtemp())/'reconcile'
        self.receipts.mkdir()
        files = {f'scripts/{n}': d for n, d in PATCHER.items()}
        files['scripts/launcher3-bundle-report.py'] = (ROOT/'scripts/launcher3-bundle-report.py').read_bytes()
        files.update({f'os/patches/{n}': d for n, (_, d) in (patches or PATCHES).items()})
        files.update({f'os/overlays/{t}': d for t, d in (OVERLAYS if overlays is None else overlays).items()})
        files['os/release-pipeline/frankel.json'] = json.dumps({'host': {'integrate': {'reconcile': {
            'applyArgs': APPLY_ARGS, 'patcher': patcher, 'receiptDir': str(self.receipts)}}}}).encode()
        for path, data in files.items():
            (self.root/path).parent.mkdir(parents=True, exist_ok=True)
            (self.root/path).write_bytes(data)
        git('init', '-q', '-b', 'main', str(self.root), cwd='/')
        git('add', '-A', cwd=self.root)
        git('commit', '-qm', 'integrating run', cwd=self.root)
        self.head = git('rev-parse', 'HEAD', cwd=self.root)
        self.contract = copy.deepcopy(CONTRACT)
        self.contract['projects'] = [{'path': p} for p in (FB, L3, BR)]
        self.contract['overlayRoles'] = {'required': {'prefixes': list(PREFIXES), 'targets': [t for t in OVERLAYS
                                                      if t.startswith(tuple(PREFIXES))]},
                                         'notAnInput': {'targets': [f'{FB}/libs/N.java']}}
        base = self.contract['base']
        base['registration'] = {'applyArgs': list(APPLY_ARGS),
                                'patcher': {'patcher': 'trusted', 'sha256': {n: sha(d) for n, d in PATCHER.items()}},
                                'order': [{'project': p, 'name': n} for n, (p, _) in PATCHES.items()]}
        base['bundleContent'] = {'patches': [{'project': p, 'name': n, 'sha256': sha(d)} for n, (p, d) in PATCHES.items()],
                                 'overlays': [{'target': t, 'sha256': sha(d)} for t, d in OVERLAYS.items()
                                              if t.startswith(tuple(PREFIXES))],
                                 'notAnInputOverlays': [f'{FB}/libs/N.java']}
        self.run_path = Path(tempfile.mkdtemp())/'launcher3-bundle-run.json'
        self.write_run()
        self.receipt()

    def write_run(self, **change):
        document = {'schemaVersion': 1, 'kind': 'rose-launcher3-bundle-run', 'basis': 'test', 'runId': '41700000000',
                    'runAttempt': 1, 'headSha': self.head, 'label': '2026110401',
                    'createdAt': '2026-11-04T00:00:00Z', 'updatedAt': '2026-11-04T01:00:00Z', **change}
        if self.run_path.exists():
            self.run_path.unlink()
        self.run_path.write_text(json.dumps(document))

    def receipt(self, name='0001-2026110401.json', **change):
        record = {'receiptType': 'reconcile', 'label': '2026110401', 'changed': ['x'], 'entries': {'x': {}},
                  'at': '2026-11-04T00:30:00Z',
                  'patcher': {'patcher': 'trusted', 'sha256': {n: sha(d) for n, d in PATCHER.items()}}, **change}
        (self.receipts/name).write_text(json.dumps(record))
        return self.receipts/name

    def produce(self, **options):
        return producer.produce(self.run_path, root=self.root, main_ref='main', contract=self.contract, **options)


class Produce(unittest.TestCase):
    def setUp(self):
        self.s = Integration()

    def test_a_report_from_history_and_the_receipt_is_what_the_adapter_accepts(self):
        report = self.s.produce()
        self.assertEqual(adapter.bundle_report_problems(report, self.s.contract['base']), [])
        self.assertEqual(report['producer'], {'path': 'scripts/launcher3-bundle-report.py',
                                              'sha256': sha((ROOT/'scripts/launcher3-bundle-report.py').read_bytes()),
                                              'roseRevision': self.s.head})
        self.assertEqual(report['run']['bindingSha256'], sha(self.s.run_path.read_bytes()))
        self.assertEqual(report['receipt']['path'], str(self.s.receipts/'0001-2026110401.json'))
        self.assertEqual(report['receipt']['receiptSha256'], sha((self.s.receipts/'0001-2026110401.json').read_bytes()))
        self.assertNotIn('entries', report['receipt'])  # association only, never copied
        self.assertEqual(report['candidate'], {'commit': self.s.head, 'onMain': True,
            'frankelConfigSha256': sha((self.s.root/'os/release-pipeline/frankel.json').read_bytes()),
            'applyArgs': APPLY_ARGS, 'patcherKind': 'trusted'})
        self.assertEqual(report['registration'], {'source': 'base.registration', 'patcherMatchesPin': True,
                                                  'applyArgsMatchPin': True})
        self.assertEqual(report['observed']['unclassified'], [])
        self.assertNotIn('packages/apps/Updater/U.java', json.dumps(report['observed']))  # outside the three projects
        # an adopt receipt of the same label is not a second reconcile receipt
        self.s.receipt('0002-2026110401.json', receiptType='reconcile-adopt')
        self.assertEqual(self.s.produce()['receipt']['path'], str(self.s.receipts/'0001-2026110401.json'))

    def test_observed_comes_from_history_and_never_from_bundle_content(self):
        before = self.s.produce()['observed']
        self.s.contract['base']['bundleContent'] = {'patches': [], 'overlays': [], 'notAnInputOverlays': ['x']}
        self.assertEqual(self.s.produce()['observed'], before)
        del self.s.contract['base']['bundleContent']
        self.assertEqual(self.s.produce()['observed'], before)
        # the working tree is not history: only headSha's committed bytes are read
        (self.s.root/'os/patches/frameworks-base.patch').write_bytes(b'changed in the working tree\n')
        self.assertEqual(self.s.produce()['observed'], before)

    def test_the_association_refuses_by_name(self):
        cases = [('no receipt', lambda: (self.s.receipts/'0001-2026110401.json').unlink(), '0 reconcile receipts'),
                 ('two receipts', lambda: self.s.receipt('0002-2026110401.json'), '2 reconcile receipts'),
                 ('another label', lambda: self.s.receipt('0001-2026110401.json', label='2026110402'), '0 reconcile'),
                 ('before the run', lambda: self.s.receipt(at='2026-11-03T23:59:59Z'), 'not within'),
                 ('after the run', lambda: self.s.receipt(at='2026-11-04T01:00:01Z'), 'not within'),
                 ('a local time', lambda: self.s.receipt(at='2026-11-04T00:30:00+00:00'), 'not within'),
                 ('a candidate patcher', lambda: self.s.receipt(patcher={'patcher': 'candidate', 'sha256': {
                     'apply-os-changes.py': 'a' * 64}}), 'trusted patcher'),
                 ('not json', lambda: (self.s.receipts/'0003-2026110401.json').write_text('{'), 'not JSON'),
                 ('a link', lambda: os.symlink(self.s.receipts/'0001-2026110401.json',
                                               self.s.receipts/'0009-2026110401.json'), 'not a regular file'),
                 ('a missing head', lambda: self.s.write_run(headSha='1' * 40), 'not a commit')]
        for name, change, word in cases:
            with self.subTest(name=name):
                self.setUp()
                change()
                with self.assertRaisesRegex(producer.Refused, word):
                    self.s.produce()
        self.setUp()
        shutil.rmtree(self.s.receipts)
        os.symlink(tempfile.mkdtemp(), self.s.receipts)
        with self.assertRaisesRegex(producer.Refused, 'not a plain directory'):
            self.s.produce()

    def test_what_differs_is_reported_and_the_adapter_refuses_it(self):
        # the producer reports what it reads; matching is the adapter's (base-exact)
        cases = [('not on main', lambda s: (git('checkout', '-q', '-b', 'side', cwd=s.root),
                                            git('commit', '-q', '--allow-empty', '-m', 'side', cwd=s.root),
                                            s.write_run(headSha=git('rev-parse', 'HEAD', cwd=s.root)),
                                            git('checkout', '-q', 'main', cwd=s.root)),
                  'candidate.onMain is not true'),
                 ('other applyArgs', lambda s: s.contract['base']['registration'].update(applyArgs=['--other']),
                  'candidate.applyArgs are not the registration applyArgs'),
                 ('other patcher pins', lambda s: s.contract['base']['registration']['patcher']['sha256'].update(
                     {'integration_copy.py': '0' * 64}), 'receipt.patcher is not the trusted patcher'),
                 # the receipt and the pins agree, but the patcher at headSha is not those bytes
                 ('another patcher at headSha', lambda s: ((s.root/'scripts/integration_copy.py').write_bytes(b'other\n'),
                                                           git('commit', '-qam', 'patcher', cwd=s.root),
                                                           s.write_run(headSha=git('rev-parse', 'HEAD', cwd=s.root))),
                  'does not state that its patcher and applyArgs match the pins')]
        for name, change, word in cases:
            with self.subTest(name=name):
                self.setUp()
                change(self.s)
                problems = adapter.bundle_report_problems(self.s.produce(), self.s.contract['base'])
                self.assertTrue(any(word in p for p in problems), problems)
        self.setUp()
        report = self.s.produce()
        self.s.contract['base']['registration']['applyArgs'] = ['--other']
        self.assertFalse(self.s.produce()['registration']['applyArgsMatchPin'])
        self.assertTrue(report['registration']['applyArgsMatchPin'])

    def test_overlays_are_classified_by_the_contracts_prefixes_and_allowlist(self):
        s = Integration(overlays={**OVERLAYS, f'{FB}/libs/Other.java': b'unreviewed\n', f'{L3}/res/new.xml': b'new\n'})
        s.contract['base']['bundleContent']['overlays'].append({'target': f'{L3}/res/new.xml', 'sha256': sha(b'new\n')})
        report = s.produce()
        self.assertEqual(report['observed']['unclassified'], [f'{FB}/libs/Other.java'])
        self.assertIn({'target': f'{L3}/res/new.xml', 'sha256': sha(b'new\n')}, report['observed']['overlays'])
        self.assertIn('observed.unclassified is not empty', adapter.bundle_report_problems(report, s.contract['base']))
        # a link among the overlays of the three projects is unclassified, never an overlay
        s = Integration()
        os.symlink('A.java', s.root/f'os/overlays/{L3}/quickstep/Link.java')
        git('add', '-A', cwd=s.root)
        git('commit', '-qm', 'link', cwd=s.root)
        s.write_run(headSha=git('rev-parse', 'HEAD', cwd=s.root))
        self.assertEqual(s.produce()['observed']['unclassified'], [f'{L3}/quickstep/Link.java'])

    def test_inputs_that_are_missing_or_of_the_wrong_form_refuse(self):
        for name, change, error, word in (
                ('no prefixes', lambda s: s.contract['overlayRoles']['required'].pop('prefixes'), producer.Invalid,
                 'no required overlay prefixes'),
                ('a patch absent at headSha', lambda s: s.contract['base']['registration']['order'].append(
                    {'project': L3, 'name': 'rose-edge-launcher3.patch'}), producer.Refused, 'not a regular file'),
                ('an unbound run', lambda s: s.write_run(headSha=None), producer.Unbound, 'headSha'),
                ('a short commit', lambda s: s.write_run(headSha=s.head[:12]), producer.Invalid, 'wrong form'),
                ('times out of order', lambda s: s.write_run(createdAt='2026-11-04T02:00:00Z'), producer.Invalid, 'wrong form'),
                ('a boolean attempt', lambda s: s.write_run(runAttempt=True), producer.Invalid, 'wrong form'),
                ('an extra key', lambda s: s.run_path.write_text(json.dumps({**json.loads(s.run_path.read_text()), 'x': 1})),
                 producer.Invalid, 'exact'),
                ('a producer that is not its committed blob',
                 lambda s: (s.root/'scripts/launcher3-bundle-report.py').write_bytes(b'# edited\n'), producer.Refused,
                 'not the committed producer')):
            with self.subTest(name=name):
                s = Integration()
                change(s)
                with self.assertRaisesRegex(error, word):
                    s.produce()

    def test_the_cli_writes_once_and_exits_by_outcome(self):
        out = Path(tempfile.mkdtemp())/'bundle-report.json'

        def cli(*argv):
            return subprocess.run([sys.executable, str(self.s.root/'scripts/launcher3-bundle-report.py'), *argv],
                                  capture_output=True, text=True, cwd='/')
        # the committed contract in a synthetic checkout is absent, so a run there is invalid; the API is the unit here
        self.assertEqual(cli('produce', '--run', str(self.s.run_path), '--out', 'relative.json').returncode, 2)
        report = self.s.produce()
        digest = producer.write_once(out, report)
        self.assertEqual(digest, sha(out.read_bytes()))
        self.assertEqual(json.loads(out.read_text()), report)
        with self.assertRaises(FileExistsError):
            producer.write_once(out, report)


class Committed(unittest.TestCase):
    def test_the_producer_reads_the_contract_the_adapter_pins_and_loads_neither_peer(self):
        self.assertEqual(producer.CONTRACT_SHA256, adapter.MANIFEST_SHA256)
        self.assertEqual(producer.load_contract()['base']['bundleReport']['kind'], 'rose-integration-bundle-report')
        source = (ROOT/'scripts/launcher3-bundle-report.py').read_text()
        code = source.split('\n"""\n', 1)[1]
        self.assertNotIn('importlib', code)
        self.assertNotIn('launcher3-compile', code)
        self.assertNotIn(producer.PRODUCER, adapter.NEVER_PRODUCERS)

    def test_the_committed_run_document_is_unbound(self):
        with self.assertRaisesRegex(producer.Unbound, 'runId, runAttempt, headSha, label, createdAt, updatedAt'):
            producer.load_run(ROOT/producer.RUN)
        result = subprocess.run([sys.executable, str(ROOT/'scripts/launcher3-bundle-report.py'), 'produce', '--out',
                                 str(Path(tempfile.mkdtemp())/'r.json')], capture_output=True, text=True, cwd=ROOT)
        self.assertEqual((result.returncode, result.stdout), (4, ''))

    def test_the_committed_contract_needs_its_prefixes_as_data(self):
        with self.assertRaisesRegex(producer.Invalid, 'overlayRoles.required.prefixes'):
            producer.observed(producer.load_contract(), ROOT, 'HEAD')

    def test_this_checkouts_overlays_and_patches_are_the_pinned_base_bundle(self):
        """While main's os/ is at the base (until #440's candidate lands, when OS Product regenerates the contract):
        observed at HEAD, with the checker's prefixes, equals base.bundleContent and leaves nothing unclassified."""
        contract = producer.load_contract()
        contract['overlayRoles']['required']['prefixes'] = list(PREFIXES)
        seen = producer.observed(contract, ROOT, 'HEAD')
        content = contract['base']['bundleContent']
        self.assertEqual(seen['unclassified'], [])
        self.assertEqual([(p['project'], p['name']) for p in seen['patches']],
                         [(o['project'], o['name']) for o in contract['base']['registration']['order']])
        self.assertEqual(sorted((p['project'], p['name'], p['sha256']) for p in seen['patches']),
                         sorted((p['project'], p['name'], p['sha256']) for p in content['patches']))
        self.assertEqual(sorted((o['target'], o['sha256']) for o in seen['overlays']),
                         sorted((o['target'], o['sha256']) for o in content['overlays']))
        self.assertEqual(sorted(seen['notAnInputOverlays']), sorted(content['notAnInputOverlays']))


if __name__ == '__main__':
    unittest.main()
