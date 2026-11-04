"""LAUNCHER3-BUNDLE-REPORT: the producer of base.bundleReport (scripts/launcher3-bundle-report.py).

Synthetic Git repositories, receipt directories and run documents only: no host, network or real receipt. The
integrating commit (headSha) and the producer's own HEAD always differ, in patches, overlays, recipe and patcher, so
nothing read at the wrong revision can pass. A report the producer writes is checked by the adapter's own
bundle_report_problems (loaded by these tests only; the producer never loads it), and every association and
observation rule refuses or reports by name. One test reads this checkout's own os/patches and os/overlays at HEAD
against the pinned base.bundleContent."""
import contextlib
import copy
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
import types
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT/path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


producer = load('launcher3_bundle_report', 'scripts/launcher3-bundle-report.py')
adapter = load('launcher3_compile', 'scripts/launcher3-compile.py')
checker = load('launcher3_compile_contract', 'scripts/launcher3-compile-contract.py')  # the tests may read it; the producer never
CONTRACT = json.loads((ROOT/'os/compile-contracts/launcher3-quickstep-lib.json').read_text())
PREFIXES = list(checker.REQUIRED_OVERLAYS)  # the contract carries them as data only from OS Product's re-pin on


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
LABEL = '2026110401'


class Integration:
    """A synthetic checkout. headSha is the integrating commit: the patcher, os/patches, os/overlays and a recipe whose
    receiptDir is a temporary receipt directory. HEAD, a later commit on origin/main, changes every one of them and
    adds the producer and the reviewed run document that names headSha."""
    def __init__(self, overlays=None, patcher='trusted', extra=None, receipt_dir=str):
        self.root = Path(tempfile.mkdtemp())/'checkout'
        self.receipts = Path(tempfile.mkdtemp())/'reconcile'
        self.receipts.mkdir()
        files = {f'scripts/{n}': d for n, d in PATCHER.items()}
        files.update({f'os/patches/{n}': d for n, (_, d) in PATCHES.items()})
        files.update({f'os/overlays/{t}': d for t, d in (OVERLAYS if overlays is None else overlays).items()})
        files['os/release-pipeline/frankel.json'] = self.recipe(patcher, receipt_dir=receipt_dir(self.receipts))
        files.update(extra or {})
        self.write(files)
        git('init', '-q', '-b', 'main', str(self.root), cwd='/')
        self.commit('the integrating run')
        self.head = git('rev-parse', 'HEAD', cwd=self.root)
        # HEAD: everything changes after the integrating commit; only headSha's bytes may be reported
        later = {f'scripts/{n}': d + b'# later\n' for n, d in PATCHER.items()}
        later.update({f'os/patches/{n}': d + b'later\n' for n, (_, d) in PATCHES.items()})
        later[f'os/overlays/{L3}/quickstep/A.java'] = b'a, later\n'
        later['os/release-pipeline/frankel.json'] = self.recipe(patcher, apply_args=['--later'])
        later['scripts/launcher3-bundle-report.py'] = (ROOT/'scripts/launcher3-bundle-report.py').read_bytes()
        self.write(later)
        self.write_run()
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
        self.receipt()

    def recipe(self, patcher, apply_args=APPLY_ARGS, receipt_dir=None):
        return json.dumps({'host': {'integrate': {'reconcile': {
            'applyArgs': apply_args, 'patcher': patcher, 'receiptDir': receipt_dir or str(self.receipts)}}}}).encode()

    def write(self, files):
        for path, data in files.items():
            (self.root/path).parent.mkdir(parents=True, exist_ok=True)
            (self.root/path).write_bytes(data)

    def commit(self, message):
        git('add', '-A', cwd=self.root)
        git('commit', '-qm', message, cwd=self.root)
        git('update-ref', 'refs/remotes/origin/main', 'HEAD', cwd=self.root)
        return git('rev-parse', 'HEAD', cwd=self.root)

    def write_run(self, **change):
        document = {'schemaVersion': 1, 'kind': 'rose-launcher3-bundle-run', 'basis': 'test', 'runId': '41700000000',
                    'runAttempt': 1, 'headSha': self.head, 'label': LABEL,
                    'createdAt': '2026-11-04T00:00:00Z', 'updatedAt': '2026-11-04T01:00:00Z', **change}
        self.write({producer.RUN: json.dumps(document).encode()})
        self.revision = self.commit('the reviewed run document')

    def receipt(self, name=f'0001-{LABEL}.json', raw=None, **change):
        record = {'receiptType': 'reconcile', 'label': LABEL, 'changed': ['x'],
                  'entries': {f'{L3}/quickstep/A.java': {'before': None, 'after': sha(b'a\n'), 'action': 'installed'}},
                  'at': '2026-11-04T00:30:00Z',
                  'patcher': {'patcher': 'trusted', 'sha256': {n: sha(d) for n, d in PATCHER.items()}}, **change}
        (self.receipts/name).write_bytes(raw if raw is not None else json.dumps(record).encode())
        return self.receipts/name

    def produce(self):
        return producer.produce(root=self.root, contract=self.contract)[0]


class Produce(unittest.TestCase):
    def setUp(self):
        self.s = Integration()

    def test_a_report_from_headshas_history_and_the_receipt_is_what_the_adapter_accepts(self):
        report, main = producer.produce(root=self.s.root, contract=self.s.contract)
        self.assertEqual(adapter.bundle_report_problems(report, self.s.contract['base']), [])
        self.assertEqual(main, self.s.revision)  # onMain is read against this origin/main commit
        self.assertNotEqual(self.s.head, self.s.revision)
        self.assertEqual(report['producer'], {'path': 'scripts/launcher3-bundle-report.py',
                                              'sha256': sha((ROOT/'scripts/launcher3-bundle-report.py').read_bytes()),
                                              'roseRevision': self.s.revision})
        self.assertEqual(report['run']['bindingSha256'], sha((self.s.root/producer.RUN).read_bytes()))
        self.assertEqual(report['receipt']['path'], f'{self.s.receipts}/0001-{LABEL}.json')
        self.assertEqual(report['receipt']['receiptSha256'], sha((self.s.receipts/f'0001-{LABEL}.json').read_bytes()))
        self.assertNotIn('entries', report['receipt'])  # association only, never copied
        recipe = git('show', f'{self.s.head}:os/release-pipeline/frankel.json', cwd=self.s.root).encode()
        self.assertEqual(report['candidate'], {'commit': self.s.head, 'onMain': True, 'frankelConfigSha256': sha(recipe),
                                               'applyArgs': APPLY_ARGS, 'patcherKind': 'trusted'})
        self.assertEqual(report['registration'], {'source': 'base.registration', 'patcherMatchesPin': True,
                                                  'applyArgsMatchPin': True})
        self.assertIn({'target': f'{L3}/quickstep/A.java', 'sha256': sha(b'a\n')}, report['observed']['overlays'])
        self.assertEqual(report['observed']['patches'][0]['sha256'], sha(b'fb\n'))  # headSha's bytes, not HEAD's
        self.assertEqual(report['observed']['unclassified'], [])
        self.assertNotIn('packages/apps/Updater/U.java', json.dumps(report['observed']))  # outside the three projects
        # an adopt receipt of the same label, a backup, a bare name and another suffix are not the reconcile receipt
        self.s.receipt(f'0002-{LABEL}.json', receiptType='reconcile-adopt')
        self.s.receipt(f'-{LABEL}.json')
        self.s.receipt(f'0003-{LABEL}.json.bak')
        (self.s.receipts/f'0004-{LABEL}-backup').mkdir()
        self.s.receipt('0005-another.json')  # the run's label inside, but not a *-<label>.json name
        self.assertEqual(self.s.produce()['receipt']['path'], f'{self.s.receipts}/0001-{LABEL}.json')
        # the window is inclusive at both ends
        for at in ('2026-11-04T00:00:00Z', '2026-11-04T01:00:00Z'):
            self.s.receipt(at=at)
            self.assertEqual(self.s.produce()['receipt']['at'], at)

    def test_observed_comes_from_headshas_history_only(self):
        before = self.s.produce()['observed']
        self.s.contract['base']['bundleContent'] = {'patches': [], 'overlays': [], 'notAnInputOverlays': ['x']}
        self.assertEqual(self.s.produce()['observed'], before)
        del self.s.contract['base']['bundleContent']
        self.assertEqual(self.s.produce()['observed'], before)
        for path in ('os/patches/frameworks-base.patch', f'os/overlays/{L3}/quickstep/A.java'):
            (self.s.root/path).write_bytes(b'changed in the working tree\n')  # the working tree is not history
        self.assertEqual(self.s.produce()['observed'], before)

    def test_replace_objects_and_grafts_never_stand_in_for_history(self):
        real = self.s.produce()['observed']
        # a replacement for headSha whose tree has another overlay: ignored
        git('checkout', '-q', '-b', 'fake', self.s.head, cwd=self.s.root)
        (self.s.root/f'os/overlays/{L3}/quickstep/A.java').write_bytes(b'replaced\n')
        git('commit', '-qam', 'fake', cwd=self.s.root)
        fake = git('rev-parse', 'HEAD', cwd=self.s.root)
        git('checkout', '-q', 'main', cwd=self.s.root)
        git('replace', self.s.head, fake, cwd=self.s.root)
        self.assertEqual(self.s.produce()['observed'], real)
        # a side commit grafted onto main (a replace graft, or an info/grafts line): never on main
        git('replace', '-d', self.s.head, cwd=self.s.root)
        git('checkout', '-q', '-b', 'side', self.s.head, cwd=self.s.root)
        git('commit', '-q', '--allow-empty', '-m', 'side', cwd=self.s.root)
        side = git('rev-parse', 'HEAD', cwd=self.s.root)
        git('checkout', '-q', 'main', cwd=self.s.root)
        self.s.write_run(headSha=side)
        git('replace', '--graft', self.s.revision, side, cwd=self.s.root)
        self.assertFalse(self.s.produce()['candidate']['onMain'])
        git('replace', '-d', self.s.revision, cwd=self.s.root)
        (self.s.root/'.git/info').mkdir(exist_ok=True)
        (self.s.root/'.git/info/grafts').write_text(f'{self.s.revision} {side}\n')
        self.assertFalse(self.s.produce()['candidate']['onMain'])

    def test_the_association_refuses_by_name(self):
        receipts = lambda s: s.receipts  # noqa: E731
        cases = [('no receipt', lambda s: (receipts(s)/f'0001-{LABEL}.json').unlink(), '0 reconcile receipts'),
                 ('two receipts', lambda s: s.receipt(f'0002-{LABEL}.json'), '2 reconcile receipts'),
                 ('another label', lambda s: s.receipt(label='2026110402'), '0 reconcile receipts'),
                 ('before the run', lambda s: s.receipt(at='2026-11-03T23:59:59Z'), 'not within'),
                 ('after the run', lambda s: s.receipt(at='2026-11-04T01:00:01Z'), 'not within'),
                 ('a local time', lambda s: s.receipt(at='2026-11-04T00:30:00+00:00'), 'not within'),
                 ('a candidate patcher', lambda s: s.receipt(patcher={'patcher': 'candidate', 'sha256': {
                     'apply-os-changes.py': 'a' * 64}}), 'trusted patcher'),
                 ('other patcher files', lambda s: s.receipt(patcher={'patcher': 'trusted', 'sha256': {
                     'apply-os-changes.py': 'a' * 64}}), 'trusted patcher'),
                 ('not json', lambda s: s.receipt(f'0003-{LABEL}.json', raw=b'{'), 'not a JSON document'),
                 ('not an object', lambda s: s.receipt(f'0003-{LABEL}.json', raw=b'[]'), 'not a JSON object'),
                 ('a duplicate key', lambda s: s.receipt(f'0003-{LABEL}.json', raw=b'{"receiptType": "reconcile-adopt", '
                                                         b'"receiptType": "reconcile"}'), 'duplicate key'),
                 ('nested too deeply', lambda s: s.receipt(f'0003-{LABEL}.json', raw=b'[' * 100000), 'nested too deeply'),
                 ('a link', lambda s: os.symlink(receipts(s)/f'0001-{LABEL}.json', receipts(s)/f'0009-{LABEL}.json'),
                  'not a regular file'),
                 ('a fifo', lambda s: os.mkfifo(receipts(s)/f'0009-{LABEL}.json'), 'not a regular file'),
                 ('too large', lambda s: s.receipt(f'0009-{LABEL}.json', raw=b' ' * (producer.RECEIPT_BYTES + 1)),
                  'larger than'),
                 ('installed other bytes', lambda s: s.receipt(entries={f'{L3}/quickstep/A.java': {
                     'after': sha(b'other\n'), 'action': 'installed'}}), 'installed other bytes'),
                 ('a missing head', lambda s: s.write_run(headSha='1' * 40), 'not exactly a commit'),
                 ('a tree as head', lambda s: s.write_run(headSha=git('rev-parse', f'{s.head}^{{tree}}', cwd=s.root)),
                  'not exactly a commit'),
                 ('a tag as head', lambda s: s.write_run(headSha=(git('tag', '-a', '-m', 'v1', 'v1', s.head, cwd=s.root),
                                                                  git('rev-parse', 'refs/tags/v1', cwd=s.root))[1]),
                  'not exactly a commit'),
                 ('a malformed patcher hash', lambda s: s.receipt(patcher={'patcher': 'trusted', 'sha256': {
                     n: 'x' for n in PATCHER}}), 'trusted patcher'),
                 ('entries not an object', lambda s: s.receipt(entries=[]), 'entries are not an object')]
        for name, change, word in cases:
            with self.subTest(name=name):
                s = Integration()
                change(s)
                with self.assertRaisesRegex(producer.Refused, word):
                    s.produce()
        for name, setup, word in (('a link', lambda s: (shutil.rmtree(s.receipts), os.symlink(tempfile.mkdtemp(), s.receipts)),
                                   'not a plain directory'),
                                  ('missing', lambda s: shutil.rmtree(s.receipts), 'does not exist')):
            with self.subTest(directory=name):
                s = Integration()
                setup(s)
                with self.assertRaisesRegex(producer.Refused, word):
                    s.produce()
        for name, receipt_dir in (('relative', lambda r: r.name), ('not normalized', lambda r: f'{r}/.')):
            with self.subTest(receiptDir=name):
                with self.assertRaisesRegex(producer.Refused, 'not a normalized absolute path'):
                    Integration(receipt_dir=receipt_dir).produce()
        # headSha's recipe is read as strictly as the receipt
        for name, raw in (('a duplicate key', b'{"host": {"integrate": {"reconcile": {"applyArgs": [], "patcher": '
                                               b'"trusted", "receiptDir": "/x"}}}, "host": {}}'),
                          ('nested too deeply', b'[' * 100000), ('another shape', b'{"host": []}')):
            with self.subTest(recipe=name):
                with self.assertRaisesRegex(producer.Refused, 'expected shape'):
                    Integration(extra={producer.RECIPE: raw}).produce()
        # entries that name no overlay, or record a removal, are association only
        s = Integration()
        s.receipt(entries={f'{FB}/libs/N.java': {'after': sha(b'x'), 'action': 'installed'},
                           f'{L3}/quickstep/A.java': {'after': None, 'action': 'removed'}})
        self.assertEqual(adapter.bundle_report_problems(s.produce(), s.contract['base']), [])

    def test_the_run_document_is_the_reviewed_one(self):
        cases = [('an uncommitted edit', lambda s: (s.root/producer.RUN).write_text(
                     (s.root/producer.RUN).read_text().replace(LABEL, '2026110402')), producer.Refused, 'committed blob'),
                 ('an unbound run', lambda s: s.write_run(headSha=None), producer.Unbound, 'headSha'),
                 ('a short commit', lambda s: s.write_run(headSha=s.head[:12]), producer.Invalid, 'wrong form'),
                 ('times out of order', lambda s: s.write_run(createdAt='2026-11-04T02:00:00Z'), producer.Invalid, 'wrong form'),
                 ('a boolean attempt', lambda s: s.write_run(runAttempt=True), producer.Invalid, 'wrong form'),
                 ('a zero run id', lambda s: s.write_run(runId='0'), producer.Invalid, 'wrong form'),
                 ('a label that is a path', lambda s: s.write_run(label=f'../{LABEL}'), producer.Invalid, 'wrong form'),
                 ('a boolean version', lambda s: s.write_run(schemaVersion=True), producer.Invalid, 'exact'),
                 ('an extra key', lambda s: s.write_run(x=1), producer.Invalid, 'exact')]
        for name, change, error, word in cases:
            with self.subTest(name=name):
                s = Integration()
                change(s)
                with self.assertRaisesRegex(error, word):
                    s.produce()
        s = Integration()
        text = (s.root/producer.RUN).read_text()
        s.write({producer.RUN: text.replace('"label"', '"label": "x", "label"', 1).encode()})
        s.revision = s.commit('a duplicate key')
        with self.assertRaisesRegex(producer.Invalid, 'duplicate key'):
            s.produce()

    def test_the_producer_runs_at_a_reviewed_commit_and_reads_main_as_pinned(self):
        cases = [('an edited producer', lambda s: (s.root/producer.PRODUCER).write_bytes(b'# edited\n'),
                  'not the committed producer'),
                 ('HEAD off main', lambda s: (git('commit', '-q', '--allow-empty', '-m', 'unreviewed', cwd=s.root)),
                  'not a reviewed commit'),
                 ('no origin/main', lambda s: git('update-ref', '-d', 'refs/remotes/origin/main', cwd=s.root),
                  'no refs/remotes/origin/main')]
        for name, change, word in cases:
            with self.subTest(name=name):
                s = Integration()
                change(s)
                with self.assertRaisesRegex(producer.Refused, word):
                    s.produce()
        # a side headSha is reported off main, and the adapter refuses it
        s = Integration()
        git('checkout', '-q', '-b', 'side', s.head, cwd=s.root)
        git('commit', '-q', '--allow-empty', '-m', 'side', cwd=s.root)
        side = git('rev-parse', 'HEAD', cwd=s.root)
        git('checkout', '-q', 'main', cwd=s.root)
        s.write_run(headSha=side)
        report = s.produce()
        self.assertFalse(report['candidate']['onMain'])
        self.assertIn('candidate.onMain is not true: headSha is reachable from main (association)',
                      adapter.bundle_report_problems(report, s.contract['base']))
        # a directory inside the checkout that holds copies of the producer and the run document is not the checkout
        s = Integration()
        for path in (producer.PRODUCER, producer.RUN):
            (s.root/'nested'/path).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(s.root/path, s.root/'nested'/path)
        with self.assertRaisesRegex(producer.Refused, 'not the top of its own checkout'):
            producer.produce(root=s.root/'nested', contract=s.contract)

    def test_history_that_cannot_be_read_whole_refuses(self):
        # an object missing between headSha and main: git would answer no, or yes with an error
        s = self.s
        middle = s.revision
        s.write_run(basis='a later review')
        (s.root/'.git/objects'/middle[:2]/middle[2:]).unlink()
        with self.assertRaisesRegex(producer.Refused, 'could not tell'):
            s.produce()
        # a shallow clone, where git could answer no for a commit beyond its boundary
        s = Integration()
        clone = Path(tempfile.mkdtemp())/'checkout'
        git('clone', '-q', '--depth', '2', f'file://{s.root}', str(clone), cwd='/')
        with self.assertRaisesRegex(producer.Refused, 'shallow'):
            producer.produce(root=clone, contract=s.contract)

    def test_bytes_a_checkout_would_change_refuse(self):
        for name, files in (('root', {'.gitattributes': b'* text eol=crlf\n'}),
                            ('os', {'os/.gitattributes': b'* ident\n'}),
                            ('overlays', {'os/overlays/packages/.gitattributes': b'* text eol=crlf\n'}),
                            ('patches', {'os/patches/.gitattributes': b'*.patch filter=x\n'})):
            with self.subTest(at=name):
                s = Integration(extra=files)
                with self.assertRaisesRegex(producer.Refused, 'attribute files'):
                    s.produce()
        s = Integration(extra={'os/.gitattributes': b'* ident\n'})  # at headSha, though HEAD removes it
        (s.root/'os/.gitattributes').unlink()
        s.revision = s.commit('no attributes at HEAD')
        with self.assertRaisesRegex(producer.Refused, 'attribute files'):
            s.produce()
        s = Integration(extra={'workspace/app/.gitattributes': b'* text\n'})  # nothing reported lies under it
        self.assertEqual(s.produce()['observed']['unclassified'], [])
        s = Integration()
        git('config', 'core.autocrlf', 'true', cwd=s.root)
        with self.assertRaisesRegex(producer.Refused, 'configures line endings'):
            s.produce()
        s = Integration()
        (s.root/'.git/info').mkdir(exist_ok=True)
        (s.root/'.git/info/attributes').write_text('* text eol=crlf\n')
        with self.assertRaisesRegex(producer.Refused, 'its own attributes file'):
            s.produce()

    def test_the_contracts_names_and_order_are_used_only_once_the_pins_hold(self):
        for name, change in (('patcher pins', lambda s: s.contract['base']['registration']['patcher']['sha256'].update(
                                 {'integration_copy.py': '0' * 64})),
                             ('applyArgs', lambda s: s.contract['base']['registration'].update(applyArgs=['--other'])),
                             ('a patcher at headSha', lambda s: s.receipt(patcher={'patcher': 'trusted', 'sha256': {
                                 n: sha(d + b'x') for n, d in PATCHER.items()}})),
                             ('headSha\'s patcher files', lambda s: (
                                 s.contract['base']['registration']['patcher']['sha256'].update(
                                     {'integration_copy.py': '0' * 64}),
                                 s.receipt(patcher=copy.deepcopy(s.contract['base']['registration']['patcher'])))),
                             ):
            with self.subTest(name=name):
                s = Integration()
                change(s)
                with self.assertRaisesRegex(producer.Refused, 'contractDerived'):
                    s.produce()

    def test_overlays_are_classified_by_the_contracts_prefixes_and_allowlist(self):
        s = Integration(overlays={**OVERLAYS, f'{FB}/libs/Other.java': b'unreviewed\n', f'{L3}/res/new.xml': b'new\n'})
        s.contract['base']['bundleContent']['overlays'].append({'target': f'{L3}/res/new.xml', 'sha256': sha(b'new\n')})
        report = s.produce()
        self.assertEqual(report['observed']['unclassified'], [f'{FB}/libs/Other.java'])
        self.assertIn({'target': f'{L3}/res/new.xml', 'sha256': sha(b'new\n')}, report['observed']['overlays'])
        self.assertEqual(report['observed']['notAnInputOverlays'], [f'{FB}/libs/N.java'])
        self.assertNotIn('packages/apps/Updater/U.java', json.dumps(report['observed']))  # outside the three projects
        self.assertIn('observed.unclassified is not empty', adapter.bundle_report_problems(report, s.contract['base']))
        # a file at a project's own path is unclassified, never an overlay or skipped as outside
        s = Integration(overlays={f'{L3}/quickstep/A.java': b'a\n', f'{BR}/aconfig/B.bp': b'b\n', FB: b'a file\n'})
        self.assertEqual(s.produce()['observed']['unclassified'], [FB])
        # a link or an executable among the overlays of the three projects is unclassified, never an overlay
        s = Integration()
        git('checkout', '-q', s.head, cwd=s.root)
        os.symlink('A.java', s.root/f'os/overlays/{L3}/quickstep/Link.java')
        (s.root/f'os/overlays/{BR}/aconfig/B.bp').chmod(0o755)
        git('add', '-A', cwd=s.root)
        git('commit', '-qm', 'link and mode', cwd=s.root)
        changed = git('rev-parse', 'HEAD', cwd=s.root)
        git('checkout', '-q', 'main', cwd=s.root)
        git('merge', '-q', '--no-edit', changed, cwd=s.root)
        s.head = changed
        s.write_run()
        self.assertEqual(s.produce()['observed']['unclassified'], [f'{BR}/aconfig/B.bp', f'{L3}/quickstep/Link.java'])

    def test_the_contract_is_checked_before_anything_else_is_read(self):
        for name, change in (('no prefixes', lambda c: c['overlayRoles']['required'].pop('prefixes')),
                             ('a prefix without its slash', lambda c: c['overlayRoles']['required'].update(prefixes=[L3])),
                             ('an empty prefix', lambda c: c['overlayRoles']['required'].update(prefixes=['/'])),
                             ('another source', lambda c: c['base']['bundleReport'].update(
                                 values={'registration.source': {'equals': 'elsewhere'}})),
                             ('another report version', lambda c: c['base']['bundleReport'].update(schemaVersion=2)),
                             ('no trusted patcher pin', lambda c: c['base']['registration']['patcher'].update(
                                 patcher='candidate'))):
            with self.subTest(name=name):
                s = Integration()
                change(s.contract)
                shutil.rmtree(s.receipts)  # it is never reached
                with self.assertRaises(producer.Invalid):
                    s.produce()

    def test_the_output_is_written_whole_once_and_never_through_a_link(self):
        out = Path(tempfile.mkdtemp())/'bundle-report.json'
        report = self.s.produce()
        self.assertEqual(producer.write_once(out, report), sha(out.read_bytes()))
        self.assertEqual(json.loads(out.read_text()), report)
        with self.assertRaises(FileExistsError):
            producer.write_once(out, report)
        link = out.with_name('link.json')
        os.symlink(Path(tempfile.mkdtemp())/'elsewhere.json', link)
        with self.assertRaises(FileExistsError):
            producer.write_once(link, report)
        self.assertEqual(sorted(p.name for p in out.parent.iterdir()), ['bundle-report.json', 'link.json'])

    def test_the_cli_is_isolated_and_exits_by_outcome(self):
        out = Path(tempfile.mkdtemp())/'r.json'

        def cli(*flags, root=self.s.root, out=out):
            return subprocess.run([sys.executable, *flags, str(root/'scripts/launcher3-bundle-report.py'), 'produce',
                                   '--out', str(out)], capture_output=True, text=True, cwd='/')
        self.assertEqual(cli().returncode, 2)  # not isolated
        self.assertIn('python3 -I', cli().stderr)
        self.assertEqual(cli('-I', out='relative.json').returncode, 2)
        self.assertIn('--out must be a normalized absolute path', cli('-I', out='relative.json').stderr)
        # in the synthetic checkout the committed contract is absent: invalid, not a traceback
        self.assertEqual(cli('-I').returncode, 2)
        self.assertFalse(out.exists())

    def test_main_exits_by_outcome_and_names_the_origin_main_commit(self):
        out = Path(tempfile.mkdtemp())/'r.json'

        def main(**produce):
            stdout, stderr = io.StringIO(), io.StringIO()
            isolated = types.SimpleNamespace(flags=types.SimpleNamespace(isolated=1), stderr=stderr)
            with mock.patch.object(producer, 'sys', isolated), mock.patch.object(producer, 'produce', **produce), \
                    contextlib.redirect_stdout(stdout):
                code = producer.main(['produce', '--out', str(out)])
            return code, json.loads(stdout.getvalue() or stderr.getvalue())
        for error, code, verdict in ((producer.Unbound('x'), 4, 'SETUP_MISSING'), (producer.Refused('x'), 3, 'REFUSED'),
                                     (producer.Invalid('x'), 2, 'INVALID'), (OSError('x'), 2, 'INVALID')):
            with self.subTest(verdict=verdict):
                self.assertEqual(main(side_effect=error), (code, {'verdict': verdict, 'error': mock.ANY}))
                self.assertFalse(out.exists())
        report, origin = producer.produce(root=self.s.root, contract=self.s.contract)
        code, line = main(return_value=(report, origin))
        self.assertEqual((code, line), (0, {'verdict': 'WRITTEN', 'path': str(out), 'sha256': sha(out.read_bytes()),
                                            'originMain': self.s.revision}))
        self.assertEqual(json.loads(out.read_bytes()), report)


class Committed(unittest.TestCase):
    def test_the_producer_reads_the_contract_the_adapter_pins_and_loads_neither_peer(self):
        self.assertEqual(producer.CONTRACT_SHA256, adapter.MANIFEST_SHA256)
        source = (ROOT/'scripts/launcher3-bundle-report.py').read_text()
        code = source.split('\n"""\n', 1)[1]
        self.assertNotIn('importlib', code)
        self.assertNotIn('launcher3-compile', code)
        self.assertNotIn(producer.PRODUCER, adapter.NEVER_PRODUCERS)

    def test_the_committed_run_document_is_reviewed_and_unbound(self):
        with self.assertRaisesRegex(producer.Unbound, 'runId, runAttempt, headSha, label, createdAt, updatedAt'):
            producer.load_run(ROOT, 'HEAD')

    def test_the_committed_contract_needs_its_prefixes_as_data(self):
        with self.assertRaisesRegex(producer.Invalid, 'overlayRoles.required.prefixes'):
            producer.load_contract()

    def test_this_checkouts_overlays_and_patches_are_the_pinned_base_bundle(self):
        """While main's os/ is at the base (until #440's candidate lands, when OS Product regenerates the contract):
        observed at HEAD, with the checker's prefixes, equals base.bundleContent and leaves nothing unclassified."""
        contract = copy.deepcopy(CONTRACT)
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
