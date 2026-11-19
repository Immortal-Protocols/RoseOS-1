"""rose.app_builder.evidence: Home's behaviour evidence (workspace/rose-starters :evidence) inside the build
isolation, offline. The refusals run everywhere; the runs need the pinned stack with its Robolectric jars
(ROSE_GRADLE_STACK), bubblewrap as a non-root Linux user and the starters, like StarterPilotsTest."""
import hashlib
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from rose.app_builder import evidence, gradle_stack, starters, toolchain, writer_handoff
from rose.tests.test_app_builder import JAVA_HOME, SDK
from rose.tests.test_app_gradle_builder import BUILDS, STACK, gradle_tools

HAS_JARS = all((STACK / gradle_stack.ROBOLECTRIC_DIR / jar.name).is_file() for jar in gradle_stack.ROBOLECTRIC_JARS)


class RefusalsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='rose-evidence-'))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.tools = toolchain.Tools(SDK, JAVA_HOME)

    def test_a_written_template_needs_its_object_and_only_then(self):
        with self.assertRaises(ValueError):
            evidence.run(None, self.tmp, self.tools, self.tmp / 'w', written=self.tmp)
        with self.assertRaises(ValueError):
            evidence.run(None, self.tmp, self.tools, self.tmp / 'w', template_object='app.rose.template.feed.Feed')

    def test_an_object_that_is_not_a_kotlin_class_name_is_refused_before_anything_runs(self):
        for bad in ('Feed', 'app.rose.template.feed.feed', 'app.rose.feed.Feed; rm -rf /', '../Feed'):
            with self.subTest(bad=bad), self.assertRaises(toolchain.StepFailed):
                evidence.run(None, self.tmp, self.tools, self.tmp / 'w', written=self.tmp, template_object=bad)

    def test_a_stack_without_the_robolectric_jars_is_a_toolchain_matter(self):
        class Ready:  # a stack that is otherwise complete
            def available(self):
                return None
            jdk = gradle = sdk = cache = Path('/nonexistent')
        with self.assertRaises(toolchain.StepFailed) as caught:
            evidence.run(Ready(), self.tmp / 'empty', self.tools, self.tmp / 'w')
        self.assertEqual(caught.exception.category, 'toolchain')


@unittest.skipUnless(starters.available(), 'this checkout has no workspace/rose-starters')
class OutputsTest(unittest.TestCase):
    """What the isolated run left behind is read only through link-free paths and never followed out: a stubbed
    Gradle run plants the outputs (no build runs here)."""

    def setUp(self):
        from unittest import mock
        self.mock = mock
        self.tmp = Path(tempfile.mkdtemp(prefix='rose-evidence-out-'))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.tools = toolchain.Tools(SDK, JAVA_HOME)
        self.jars = self.tmp / 'jars'
        self.jars.mkdir()
        for jar in gradle_stack.ROBOLECTRIC_JARS:
            (self.jars / jar.name).write_bytes(b'jar')
        for d in ('jdk', 'gradle', 'sdk', 'cache'):
            (self.tmp / d).mkdir()
        tmp = self.tmp

        class Ready:
            jdk, gradle, sdk, cache = tmp / 'jdk', tmp / 'gradle', tmp / 'sdk', tmp / 'cache'
            def available(self):
                return None
        self.gradle = Ready()
        self.secret = self.tmp / 'host-keys'
        self.secret.mkdir()
        (self.secret / 'app.p12').write_bytes(b'hunter2')

    def run_with(self, plant):
        def fake_run(gradle_tools, tools, argv, env, box, readonly, redact, cancelled):
            plant(box / 'project')
            return 'ok'
        with self.mock.patch.object(evidence.gradle, '_run', fake_run):
            return evidence.run(self.gradle, self.jars, self.tools, self.tmp / 'work')

    def report(self, project, text='[]'):
        out = project / 'evidence/build/evidence'
        out.mkdir(parents=True)
        (out / 'report.json').write_text(text)

    def test_a_linked_results_folder_is_never_followed(self):
        def plant(project):
            self.report(project)
            results = project / 'evidence/build/test-results'
            results.mkdir(parents=True)
            (results / 'testDebugUnitTest').symlink_to(self.secret)
        result = self.run_with(plant)
        self.assertNotIn('test-results/app.p12', result['files'])
        self.assertFalse(list((self.tmp / 'work/evidence').rglob('app.p12')))

    def test_a_linked_report_or_output_folder_is_no_report(self):
        def linked_report(project):
            out = project / 'evidence/build/evidence'
            out.mkdir(parents=True)
            (out / 'report.json').symlink_to(self.secret / 'app.p12')

        def linked_folder(project):
            (project / 'evidence/build').mkdir(parents=True)
            (project / 'evidence/build/evidence').symlink_to(self.secret)
        for plant in (linked_report, linked_folder):
            with self.subTest(plant=plant.__name__), self.assertRaises(toolchain.StepFailed):
                shutil.rmtree(self.tmp / 'work', ignore_errors=True)
                self.run_with(plant)

    def test_a_report_that_is_not_json_is_a_failure_not_a_crash(self):
        with self.assertRaises(toolchain.StepFailed) as caught:
            self.run_with(lambda project: self.report(project, '[{"template":"feed"x"}]'))
        self.assertEqual(caught.exception.category, 'compile')


@unittest.skipUnless(BUILDS and HAS_JARS and starters.available(),
                     'needs ROSE_GRADLE_STACK with its Robolectric jars, bubblewrap as a non-root user and the starters')
class EvidenceRunTest(unittest.TestCase):
    """The behaviour evidence offline in the isolation: the pilots, and a written template from a handoff."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='rose-evidence-run-'))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.tools = toolchain.Tools(SDK, JAVA_HOME, timeout_s=900, key_creation=True)
        self.jars = STACK / gradle_stack.ROBOLECTRIC_DIR

    def test_the_pilot_templates_behave_identically_under_every_theme(self):
        result = evidence.run(gradle_tools(), self.jars, self.tools, self.tmp / 'work')
        self.assertTrue(result['passed'], result['log'])
        self.assertEqual(len(result['report']), 6)  # one row per template and API
        self.assertTrue(all(row['identical'] for row in result['report']), result['report'])
        snapshots = sorted((self.tmp / 'work/evidence/snapshots').rglob('*.txt'))
        self.assertEqual(len(snapshots), 18)  # 3 templates x 3 themes x API 34 and 37
        by_template = {}
        for path in snapshots:
            by_template.setdefault(path.parent.name, set()).add(hashlib.sha256(path.read_bytes()).hexdigest())
        self.assertEqual({t: len(d) for t, d in by_template.items()}, {t: 1 for t in by_template})
        self.assertTrue(list((self.tmp / 'work/evidence/test-results').glob('*.xml')))  # kept for the record

    def test_a_handoffs_written_template_gets_its_evidence_in_the_isolation(self):
        catalog = starters.Starters.load()

        def appending(source, kind, request, cancelled, **turn):
            path = sorted((source / 'templates/feed').rglob('*.kt'))[0]
            path.write_text(path.read_text() + '\n// stage-1 test writer\n')
            return 'appended'
        writer_handoff.write(('feed', 'minimal'), self.tmp / 'scratch', self.tmp / 'h', writer=appending,
                             sandbox_check=lambda s, p: ['{"enforced": true}'], catalog=catalog,
                             log=lambda m: None, _allow_temp=True)
        manifest = writer_handoff.build([self.tmp / 'h'], self.tmp / 'built', self.tools, gradle_tools(), catalog,
                                        work=self.tmp / 'work', log=lambda m: None, robolectric=self.jars)
        [record] = manifest['pilots']
        self.assertEqual(record['state'], 'ready', record)
        self.assertEqual(record['label'], writer_handoff.TEST_LABEL)  # a test writer's tree, whatever it shows
        ev = record['evidence']
        self.assertTrue(ev['ran'], ev)
        self.assertTrue(ev['identicalAcrossThemes'], ev)
        self.assertEqual(len(ev['snapshots']), 6)  # one template: 3 themes x 2 APIs
        self.assertTrue((self.tmp / 'built/evidence/feed-minimal/snapshots/report.json').is_file())
        source = json.loads((self.tmp / 'built/evidence/feed-minimal/source.json').read_text())
        self.assertEqual(source['label'], writer_handoff.TEST_LABEL)  # B1, B2 and test imports stay apart
        self.assertEqual(ev['sourceLabel'], writer_handoff.TEST_LABEL)
        self.assertEqual(json.loads((self.tmp / 'built/pilots.json').read_text())['pilots'][0]['evidence'], ev)


if __name__ == '__main__':
    unittest.main()
