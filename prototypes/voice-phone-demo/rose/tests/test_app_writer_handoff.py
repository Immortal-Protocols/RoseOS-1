"""rose.app_builder.writer_handoff: stage 1 (one writer turn, source only) and stage 2 (that source through the
builder). These tests use a test writer, so nothing here is, or is labelled, Rose's writer. The end-to-end builds
need the Gradle stack and bubblewrap like StarterPilotsTest."""
import hashlib
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from rose.app_builder import device_check, starters, toolchain, writer_handoff
from rose.app_builder.starter_pilots import REQUESTS
from rose.app_builder.writer_handoff import Refused, TEST_LABEL, TRANSPLANT_LABEL, WRITER_LABEL
from rose.tests.test_app_builder import SDK, JAVA_HOME
from rose.tests.test_app_gradle_builder import BUILDS, gradle_tools

ENFORCED = lambda source, protected: ['{"probe": "read-protected", "denied": true}', '{"enforced": true}']
HAS_STARTERS = starters.available()


def kotlin_in(source: Path, template: str) -> Path:
    return sorted((source / 'templates' / template).rglob('*.kt'))[0]


def appending(text: str, calls=None):
    """A test writer: appends ``text`` to the template module's first Kotlin file."""
    def writer(source, kind, request, cancelled, **turn):
        if calls is not None:
            calls.append((kind, request, turn))
        path = kotlin_in(source, turn['starter']['template'])
        path.write_text(path.read_text() + text)
        return 'test writer: appended'
    return writer


@unittest.skipUnless(HAS_STARTERS, 'this checkout has no workspace/rose-starters')
class StageOneTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='rose-handoff-test-'))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.catalog = starters.Starters.load()

    def write(self, writer=None, check=ENFORCED, pilot=('feed', 'minimal'), **kwargs):
        return writer_handoff.write(pilot, self.tmp / 'scratch', self.tmp / 'out',
                                    writer=writer or appending('\n// stage-1 test writer\n'), sandbox_check=check,
                                    catalog=self.catalog, log=lambda m: None, _allow_temp=True, **kwargs)

    def test_one_turn_writes_source_only_with_its_manifest_and_digests(self):
        calls = []
        handoff = self.write(appending('\n// stage-1 test writer\n', calls))
        self.assertEqual(calls, [('create', REQUESTS['feed'], {'starter': {'template': 'feed', 'theme': 'minimal'}})])
        out = self.tmp / 'out'
        self.assertFalse((self.tmp / 'scratch').exists())  # removed
        self.assertEqual(handoff['label'], TEST_LABEL)
        self.assertEqual(handoff['writer']['kind'], 'test')
        self.assertTrue(writer_handoff.PACKAGE.fullmatch(handoff['packageName']))
        self.assertNotEqual(handoff['skeletonTree'], handoff['writerTree'])
        self.assertEqual(handoff['sandboxCheck'][-1], '{"enforced": true}')
        sums = dict(reversed(l.split('  ')) for l in (out / 'SHA256SUMS').read_text().splitlines())
        self.assertEqual(sums.pop('handoff.json'), hashlib.sha256((out / 'handoff.json').read_bytes()).hexdigest())
        self.assertEqual(sums, {f'tree/{p}': d for p, d in handoff['files'].items()})
        names = [p.name for p in out.rglob('*')]
        self.assertFalse([n for n in names if n.endswith(('.p12', '.jks', '.keystore', '.sqlite', '.db'))
                          or n in ('.git', 'repo.git', 'ledger')], names)  # no keys, ledger or git
        self.assertIn('// stage-1 test writer', kotlin_in(out / 'tree', 'feed').read_text())

    def test_a_place_that_exists_overlaps_or_is_protected_is_refused_before_any_turn(self):
        calls = []
        (self.tmp / 'scratch').mkdir()
        with self.assertRaises(Refused):
            self.write(appending('x', calls))
        shutil.rmtree(self.tmp / 'scratch')
        with self.assertRaises(Refused):  # out inside scratch
            writer_handoff.write(('feed', 'minimal'), self.tmp / 's', self.tmp / 's' / 'out', writer=appending('x', calls),
                                 sandbox_check=ENFORCED, catalog=self.catalog, log=lambda m: None, _allow_temp=True)
        with self.assertRaises(Refused):
            self.write(appending('x', calls), protected=[self.tmp])
        os.environ['ROSE_CONFIG_DIR'] = str(self.tmp)
        try:
            with self.assertRaises(Refused):
                self.write(appending('x', calls))
        finally:
            del os.environ['ROSE_CONFIG_DIR']
        self.assertEqual(calls, [])

    def test_temp_folders_are_refused_for_a_real_run(self):
        with self.assertRaises(Refused):  # the writer's profile denies temp folders
            writer_handoff.write(('feed', 'minimal'), self.tmp / 'scratch', Path.home() / 'nope-out-xyz',
                                 writer=appending('x'), sandbox_check=ENFORCED, catalog=self.catalog,
                                 log=lambda m: None)
        with self.assertRaises(Refused):  # and only a test writer may lift that
            writer_handoff.write(('feed', 'minimal'), self.tmp / 'scratch', self.tmp / 'out', catalog=self.catalog,
                                 log=lambda m: None, _allow_temp=True)
        self.assertFalse((Path.home() / 'nope-out-xyz').exists())

    def test_a_check_that_is_not_enforced_stops_before_the_turn(self):
        calls = []

        def not_enforced(source, protected):
            raise Refused('the writer\'s sandbox check is not enforced here')
        with self.assertRaises(Refused):
            self.write(appending('x', calls), check=not_enforced)
        self.assertEqual(calls, [])
        self.assertFalse((self.tmp / 'out').exists())
        self.assertFalse((self.tmp / 'scratch').exists())

    def test_git_metadata_the_writer_plants_never_runs_and_is_never_handed_off(self):
        pwned = self.tmp / 'PWNED'

        def planting(source, kind, request, cancelled, **turn):
            (source / '.git').mkdir()
            (source / '.git' / 'config').write_text(f'[core]\n\tfsmonitor = touch {pwned}\n\thooksPath = /\n')
            return 'planted'
        with self.assertRaises(Refused):
            self.write(planting)
        self.assertFalse(pwned.exists())
        self.assertFalse((self.tmp / 'scratch').exists())

    def test_a_link_the_writer_leaves_is_refused(self):
        def linking(source, kind, request, cancelled, **turn):
            (source / 'escape').symlink_to('/etc/passwd')
            return 'linked'
        with self.assertRaises(Refused):
            self.write(linking)


@unittest.skipUnless(HAS_STARTERS, 'this checkout has no workspace/rose-starters')
class StageTwoChecksTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='rose-handoff-test-'))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.catalog = starters.Starters.load()
        self.handoff = writer_handoff.write(('feed', 'minimal'), self.tmp / 'scratch', self.tmp / 'out',
                                            writer=appending('\n// test\n'), sandbox_check=ENFORCED,
                                            catalog=self.catalog, log=lambda m: None, _allow_temp=True)
        self.out = self.tmp / 'out'

    def load(self, name='private'):
        return writer_handoff.load(self.out, self.tmp / name, self.catalog)

    def rewrite_manifest(self, change):
        manifest = json.loads((self.out / 'handoff.json').read_text())
        change(manifest)
        data = (json.dumps(manifest) + '\n').encode()
        (self.out / 'handoff.json').write_bytes(data)
        lines = [l for l in (self.out / 'SHA256SUMS').read_text().splitlines() if not l.endswith('  handoff.json')]
        (self.out / 'SHA256SUMS').write_text('\n'.join(lines) + f'\n{hashlib.sha256(data).hexdigest()}  handoff.json\n')

    def test_a_verified_handoff_loads_into_a_private_copy(self):
        h = self.load()
        self.assertEqual(h.pilot, ('feed', 'minimal'))
        self.assertEqual(writer_handoff._tree_files(h.tree), self.handoff['files'])

    def test_tampering_is_refused(self):
        spoilers = {
            'edited file': lambda: kotlin_in(self.out / 'tree', 'feed').write_text('changed'),
            'extra file': lambda: (self.out / 'tree' / 'extra.txt').write_text('x'),
            'missing file': lambda: kotlin_in(self.out / 'tree', 'feed').unlink(),
            'manifest': lambda: (self.out / 'handoff.json').write_text('{}'),
            'link': lambda: (self.out / 'tree' / 'link').symlink_to('/etc/passwd'),
            'package': lambda: self.rewrite_manifest(lambda m: m.update(packageName='com.example.other')),
            'request': lambda: self.rewrite_manifest(lambda m: m.update(request='Something else.')),
            'starter': lambda: self.rewrite_manifest(lambda m: m.update(theme='nonexistent')),
        }
        pristine = self.tmp / 'pristine'
        shutil.copytree(self.out, pristine, symlinks=True)
        for i, (what, spoil) in enumerate(spoilers.items()):
            with self.subTest(what=what):
                shutil.rmtree(self.out)
                shutil.copytree(pristine, self.out, symlinks=True)
                spoil()
                with self.assertRaises(Refused):
                    self.load(f'private-{i}')

    def test_the_applier_mirrors_exactly_and_answers_only_its_own_create(self):
        h = self.load()
        repairs = []
        apply = writer_handoff.applier(h, repairs)
        source = self.tmp / 'source'
        source.mkdir()
        (source / 'stale.txt').write_text('left by the skeleton')
        starter = {'template': 'feed', 'theme': 'minimal'}
        apply(source, 'create', REQUESTS['feed'], lambda: False, starter=starter)
        self.assertEqual(writer_handoff._tree_files(source), self.handoff['files'])
        with self.assertRaises(toolchain.StepFailed):
            apply(source, 'create', REQUESTS['feed'], lambda: False, starter={'template': 'feed', 'theme': 'material3'})
        with self.assertRaises(toolchain.StepFailed):
            apply(source, 'revise', REQUESTS['feed'], lambda: False, starter=starter)
        with self.assertRaises(toolchain.StepFailed) as caught:
            apply(source, 'repair', 'e: Unresolved reference', lambda: False, starter=starter)
        self.assertEqual(caught.exception.category, 'compile')
        self.assertEqual(repairs, ['e: Unresolved reference'])

    def test_a_transplant_runs_the_same_template_code_under_another_theme(self):
        h = self.load()
        t = writer_handoff.transplanted(h, 'material3', self.tmp / 'transplant', self.catalog)
        self.assertEqual(t.pilot, ('feed', 'material3'))
        self.assertEqual(t.transplant, {'theme': 'minimal', 'handoffSha256': h.digest})
        package, name = h.manifest['packageName'], h.manifest['name']
        self.assertEqual(starters.outside_template(t.tree, {'template': 'feed', 'theme': 'material3'}, package), [])
        self.assertEqual(starters.template_edits(t.tree, {'template': 'feed', 'theme': 'material3'}, package, name),
                         starters.template_edits(h.tree, {'template': 'feed', 'theme': 'minimal'}, package, name))
        with self.assertRaises(Refused):
            writer_handoff.transplanted(h, 'minimal', self.tmp / 'same', self.catalog)

    def test_writer_runs_and_transplants_are_compared_separately(self):
        edits = {'templates/feed/x.kt': 'a'}
        source, other = 'a' * 64, 'b' * 64
        records = [{'template': 'feed', 'theme': 'minimal', 'templateEdits': edits, 'handoffSha256': source},
                   {'template': 'feed', 'theme': 'material3', 'templateEdits': edits, 'handoffSha256': other}]
        records += [{'template': 'feed', 'theme': t, 'templateEdits': edits, 'handoffSha256': source,
                     'transplantOf': {'theme': 'minimal', 'handoffSha256': source}}
                    for t in ('material3', 'skeuomorphic')]
        report = writer_handoff.cross_theme(records)['feed']
        self.assertTrue(report['writerRuns']['equivalent'])  # B1: the two independent runs
        self.assertEqual(report['writerRuns']['themes'], ['material3', 'minimal'])
        b2 = report['transplants'][source[:12]]  # B2: the source run with its two transplants
        self.assertEqual(b2['themes'], ['material3', 'minimal', 'skeuomorphic'])
        self.assertTrue(b2['equivalent'])
        records[2]['templateEdits'] = dict(edits, extra='b')  # a copy that was not exact
        self.assertFalse(writer_handoff.cross_theme(records)['feed']['transplants'][source[:12]]['equivalent'])

    def test_two_handoffs_for_one_pilot_are_refused(self):
        with self.assertRaises(Refused):
            writer_handoff.build([self.out, self.out], self.tmp / 'built', None, None, self.catalog,
                                 work=self.tmp / 'work', log=lambda m: None)
        self.assertFalse((self.tmp / 'built').exists())

    def test_only_the_harness_reaches_the_identity_binding(self):
        here = Path(writer_handoff.__file__).parent
        for path in here.rglob('*.py'):
            if path.name in ('writer_handoff.py', 'device_check.py'):
                continue
            text = path.read_text()
            self.assertNotRegex(text, r'(import|from)\s+[\w.]*writer_handoff|from \.\s+import[^\n]*writer_handoff', path)
            self.assertNotIn('_bind_identity', text, path)


@unittest.skipUnless(BUILDS and HAS_STARTERS,
                     'needs ROSE_GRADLE_STACK, the legacy SDK, bubblewrap as a non-root Linux user and the starters')
class StageTwoBuildTest(unittest.TestCase):
    """A test writer's handoff through the whole job: it builds, keeps its package and both trees, and is labelled
    a test writer's whatever is attested; a handoff that does not compile fails with its repair request."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='rose-handoff-build-'))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.catalog = starters.Starters.load()
        self.tools = toolchain.Tools(SDK, JAVA_HOME, timeout_s=900, key_creation=True)

    def handoff(self, name, text):
        writer_handoff.write(('feed', 'minimal'), self.tmp / f'scratch-{name}', self.tmp / name,
                             writer=appending(text), sandbox_check=ENFORCED, catalog=self.catalog,
                             log=lambda m: None, _allow_temp=True)
        return self.tmp / name

    def test_a_handoff_builds_with_its_own_package_and_trees_and_a_broken_one_returns_its_repair(self):
        good = self.handoff('good', '\n// stage-1 test writer\n')
        good_digest = hashlib.sha256((good / 'handoff.json').read_bytes()).hexdigest()
        manifest = writer_handoff.build([good], self.tmp / 'built', self.tools, gradle_tools(), self.catalog,
                                        attested=[good_digest], work=self.tmp / 'work', log=lambda m: None)
        [record] = manifest['pilots']
        self.assertEqual(record['state'], 'ready', record)
        handed = json.loads((good / 'handoff.json').read_text())
        self.assertEqual(record['packageName'], handed['packageName'])
        self.assertTrue(record['skeletonMatch'] and record['sourceMatch'], record)
        self.assertEqual(record['label'], TEST_LABEL)  # a test writer's, though attested
        self.assertNotEqual(record['label'], WRITER_LABEL)
        self.assertEqual(len(device_check.load(self.tmp / 'built')), 1)  # the device check reads it as it is
        self.assertEqual(record['outsideTemplate'], [])
        self.assertEqual(len(record['templateEdits']), 1)

        # The same handoff's template code under another theme: built, labelled a transplant, compared apart.
        manifest = writer_handoff.build([good], self.tmp / 'built-t', self.tools, gradle_tools(), self.catalog,
                                        attested=[good_digest], transplant_to=['material3'], work=self.tmp / 'work-t',
                                        log=lambda m: None)
        by_theme = {r['theme']: r for r in manifest['pilots']}
        self.assertEqual(set(by_theme), {'minimal', 'material3'})
        self.assertEqual(by_theme['material3']['state'], 'ready', by_theme['material3'])
        self.assertEqual(by_theme['material3']['label'], TRANSPLANT_LABEL)
        self.assertEqual(by_theme['material3']['templateEdits'], by_theme['minimal']['templateEdits'])
        cross = json.loads((self.tmp / 'built-t' / 'cross-theme.json').read_text())
        b2 = cross['feed']['transplants'][good_digest[:12]]  # the source run compared with its transplant
        self.assertEqual(b2['themes'], ['material3', 'minimal'])
        self.assertTrue(b2['equivalent'], b2)

        broken = self.handoff('broken', '\nfun broken( {\n')
        manifest = writer_handoff.build([broken], self.tmp / 'built2', self.tools, gradle_tools(), self.catalog,
                                        work=self.tmp / 'work2', log=lambda m: None)
        [record] = manifest['pilots']
        self.assertEqual(record['state'], 'failed', record)
        self.assertIn('repairRequest', record)
        self.assertEqual(record['label'], TEST_LABEL)


if __name__ == '__main__':
    unittest.main()
