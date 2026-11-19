"""The builder's starter hook (docs/APP-BUILDER.md §2.4): a create request may name a Rose starter
{template, theme}; it is checked before anything is recorded, kept with the project, and a new project's first tree
comes from it (rose.app_builder.starters).

The hook and its refusals run everywhere. Pilots built through the whole job (Gradle in bubblewrap, binary checks,
signing) need the pinned stack (ROSE_GRADLE_STACK), bubblewrap as a non-root Linux user and the legacy SDK;
ROSE_REQUIRE_GRADLE_BUILD=1 makes a missing one a failure. One pilot builds by default (about two minutes);
ROSE_STARTER_PILOTS=all builds every template under every theme (nine, about seventeen minutes on four CPUs). They are
compiled starter trees with a scripted writer that changes nothing: evidence that each pairing builds through the
builder, not that Rose's writer made them."""
import json
import shutil
import os
import re
import unittest
from unittest import mock
import zipfile

from rose.app_builder import gradle, gradle_stack, starter_pilots, starters, toolchain
from rose.app_builder.service import Builder, Refused
from rose.tests.test_app_builder import SDK, JAVA_HOME, Base, fake_backup
from rose.tests.test_app_gradle_builder import BUILDS, gradle_tools


class FakeGradle:
    """Stands in for GradleTools where no build runs: the builder only asks whether it has one."""


def nothing(source, kind, request, cancelled, **turn):
    return 'unchanged'


class StarterHookTest(Base):
    def builder(self, **kwargs) -> Builder:
        tools = toolchain.Tools(SDK, JAVA_HOME, timeout_s=60, key_creation=True)
        b = Builder(self.root, tools, nothing, backup=fake_backup, **kwargs)
        self.addCleanup(b.ledger.close)
        return b

    def hooked(self, written=None) -> Builder:
        def skeleton(source, package, label, starter):
            (written if written is not None else []).append(starter)
            gradle.write_skeleton(source, package, label)
        known = {('feed', 'minimal'), ('feed', 'material3')}

        def resolve(starter):
            if (starter['template'], starter['theme']) not in known:
                raise starters.UnknownStarter('There is no such starter.')
        return self.builder(gradle=FakeGradle(), skeleton=skeleton, resolve_starter=resolve)

    def create(self, b, starter=None, rid='r'):
        body = {'schemaVersion': 1, 'clientRequestId': rid, 'description': 'A feed'}
        if starter is not None:
            body['starter'] = starter
        return b.create('d', body)

    def refused(self, b, starter) -> Refused:
        with self.assertRaises(Refused) as caught:
            self.create(b, starter)
        self.assertEqual(caught.exception.details.get('reason'), 'unknown_starter')
        self.assertEqual(b.projects(), [])  # nothing was recorded
        return caught.exception

    def test_a_starter_is_checked_before_anything_is_recorded(self):
        b = self.hooked()
        for starter in ({'template': 'feed'}, {'template': 'feed', 'theme': 'minimal', 'x': 1}, 'feed',
                        {'template': 'Feed', 'theme': 'minimal'}, {'template': 'feed', 'theme': 3},
                        {'template': 'player', 'theme': 'minimal'}):
            with self.subTest(starter=starter):
                self.refused(b, starter)

    def test_a_builder_without_starters_refuses_one(self):
        self.refused(self.builder(), {'template': 'feed', 'theme': 'minimal'})
        self.refused(self.builder(skeleton=lambda *a: None, resolve_starter=lambda s: None),
                     {'template': 'feed', 'theme': 'minimal'})  # no Gradle toolchain: no starters

    def test_the_starter_is_the_projects_and_its_first_tree_comes_from_it(self):
        written = []
        b = self.hooked(written)
        out = self.create(b, {'template': 'feed', 'theme': 'minimal'})
        self.assertEqual(b.ledger.starter(out['projectId']), {'template': 'feed', 'theme': 'minimal'})
        self.assertEqual(self.create(b, {'template': 'feed', 'theme': 'minimal'}), out)  # the same request again
        with self.assertRaises(Refused):  # the same clientRequestId for another starter
            self.create(b, {'template': 'feed', 'theme': 'material3'})
        with self.assertRaises(Refused):  # ... or for none
            self.create(b)
        project, job = b.ledger.project(out['projectId']), b.ledger.job(out['jobId'])
        source = self.root / 'projects' / out['projectId'] / 'source'
        b._prepare(source, project, job)
        self.assertEqual(written, [{'template': 'feed', 'theme': 'minimal'}])
        self.assertTrue(gradle.is_gradle_project(source))
        b._prepare(source, project, job)  # the tree exists: a later job resets to it, never writes it again
        self.assertEqual(len(written), 1)

    def test_a_recorded_create_is_retried_to_the_same_ids_whatever_changed_since(self):
        b = self.hooked()
        out = self.create(b, {'template': 'feed', 'theme': 'minimal'})

        def gone(starter):
            raise starters.UnknownStarter('There is no starter template feed.')
        b.resolve_starter = gone  # the catalog changed
        self.assertEqual(self.create(b, {'template': 'feed', 'theme': 'minimal'}), out)
        b.ledger.close()
        restarted = self.builder()  # the same root, restarted without Gradle or starters
        self.assertEqual(self.create(restarted, {'template': 'feed', 'theme': 'minimal'}), out)
        with self.assertRaises(Refused) as caught:  # a new request is still checked
            self.create(restarted, {'template': 'feed', 'theme': 'minimal'}, rid='r2')
        self.assertEqual(caught.exception.details.get('reason'), 'unknown_starter')
        self.assertEqual(len(restarted.projects()), 1)

    def test_a_starter_the_catalog_no_longer_has_fails_as_a_toolchain_matter_and_starts_afresh(self):
        written = []
        b = self.hooked(written)
        out = self.create(b, {'template': 'feed', 'theme': 'minimal'})
        project, job = b.ledger.project(out['projectId']), b.ledger.job(out['jobId'])
        source = self.root / 'projects' / out['projectId'] / 'source'
        hook = b.skeleton

        def gone(*args):
            raise starters.UnknownStarter('There is no starter template feed.')
        b.skeleton = gone
        with self.assertRaises(toolchain.StepFailed) as caught:
            b._prepare(source, project, job)
        self.assertEqual(caught.exception.category, 'toolchain')
        b.skeleton = hook  # the catalog has it again: the next attempt writes the whole first tree
        b._prepare(source, project, job)
        self.assertEqual(written, [{'template': 'feed', 'theme': 'minimal'}])
        self.assertTrue(gradle.is_gradle_project(source))

    def test_a_project_without_a_starter_gets_the_plain_skeleton_through_the_hook(self):
        written = []
        b = self.hooked(written)
        out = self.create(b)
        self.assertIsNone(b.ledger.starter(out['projectId']))
        b._prepare(self.root / 'projects' / out['projectId'] / 'source', b.ledger.project(out['projectId']),
                   b.ledger.job(out['jobId']))
        self.assertEqual(written, [None])

    def test_every_writer_turn_on_a_starter_project_is_given_its_starter(self):
        turns = []

        def writer(source, kind, request, cancelled, **turn):
            turns.append((kind, turn.get('starter', 'absent')))
            return 'unchanged'
        b = self.hooked()
        b.generate = writer

        def turn_for(project_id, kind):
            job = {'jobId': 'j', 'projectId': project_id, 'attempt': 1}
            project = b.ledger.project(project_id)
            work = self.root / 'work' / project_id
            work.mkdir(parents=True, exist_ok=True)
            try:
                b._write_and_build(job, project, work / 'source', work, kind, 'a request', lambda: False, None, 0,
                                   [], None)
            except Exception:
                pass  # what follows the turn (the snapshot and build) does not matter here
        started = self.create(b, {'template': 'feed', 'theme': 'minimal'})['projectId']
        plain = self.create(b, rid='r2')['projectId']
        b.gradle = None  # stop before any build: only the turn's arguments matter here
        turn_for(started, 'create')
        turn_for(started, 'revise')
        turn_for(plain, 'create')
        self.assertEqual(turns, [('create', {'template': 'feed', 'theme': 'minimal'}),
                                 ('revise', {'template': 'feed', 'theme': 'minimal'}), ('create', 'absent')])

    def starter_project(self):
        """A builder on the real starters and one feed/minimal project prepared through it (its root commit is the
        starter's tree), with a snapshot of that source to edit as a writer turn would."""
        if not starters.available():
            self.skipTest('this checkout has no workspace/rose-starters')
        catalog = starters.Starters.load()
        b = self.builder(gradle=FakeGradle(), skeleton=starters.skeleton, resolve_starter=catalog.resolve)
        created = self.create(b, {'template': 'feed', 'theme': 'minimal'})
        project, job = b.ledger.project(created['projectId']), b.ledger.job(created['jobId'])
        source = self.root / 'projects' / created['projectId'] / 'source'
        b._prepare(source, project, job)
        snapshot = self.root / 'snapshot'
        shutil.copytree(source, snapshot)
        check = lambda job_id='job': b._check_starter(job_id, created['projectId'], project['packageName'], snapshot,
                                                      source)
        return b, snapshot, check

    def test_a_turn_that_changes_the_theme_or_framework_is_repaired_once_then_refused(self):
        b, snapshot, check = self.starter_project()
        check()  # the starter's own tree
        kt = next((snapshot / 'templates/feed').rglob('*.kt'))
        kt.write_text(kt.read_text() + '\n// the owner\'s app\n')
        check()  # template edits are fine
        theme = next((snapshot / 'themes/minimal').rglob('*.kt'))
        theme.write_text(theme.read_text() + '\n// forked\n')
        with self.assertRaises(toolchain.StepFailed) as first:
            check()
        self.assertEqual(first.exception.category, 'compile')
        self.assertIn(theme.relative_to(snapshot).as_posix(), first.exception.log)  # named for the repair turn
        self.assertIn('templates/feed/', first.exception.log)
        with self.assertRaises(toolchain.StepFailed) as second:
            check()
        self.assertEqual(second.exception.log, '')  # no tool output: never repaired again
        plain = self.create(b, rid='plain')  # a project without a starter is never checked
        b._check_starter('job2', plain['projectId'], b.ledger.project(plain['projectId'])['packageName'], snapshot,
                         snapshot)

    def test_a_catalog_update_after_the_project_was_made_is_not_the_writers_change(self):
        _, snapshot, check = self.starter_project()
        theme = next(p for p in sorted((snapshot / 'themes/minimal').rglob('*.kt')))
        name = theme.relative_to(snapshot).as_posix()
        real = starters.Starters.files

        def updated(catalog, package, label, starter):
            files = real(catalog, package, label, starter)
            files[name] = files[name] + b'\n// the starter, updated upstream\n'
            return files
        with mock.patch.object(starters.Starters, 'files', updated):
            self.assertIn(name, starters.outside_template(snapshot, {'template': 'feed', 'theme': 'minimal'},
                                                          'unused.package.check') or [name])
            check()  # the project's own first tree is the baseline: nothing the writer changed
            theme.write_text(theme.read_text() + '\n// forked\n')
            with self.assertRaises(toolchain.StepFailed):
                check()  # a real edit is still caught

    def test_a_backend_apps_internet_permission_is_the_one_manifest_change_allowed(self):
        _, snapshot, check = self.starter_project()
        manifest = snapshot / 'app/src/main/AndroidManifest.xml'
        text = manifest.read_text()
        manifest.write_text(text.replace('  <application', '  <uses-permission android:name="android.permission.INTERNET" />\n  <application', 1))
        check()
        manifest.write_text(text.replace('  <application', '  <uses-permission android:name="android.permission.CAMERA" />\n  <application', 1))
        with self.assertRaises(toolchain.StepFailed):
            check('job-camera')

    def test_a_starter_the_catalog_no_longer_has_is_not_checked(self):
        _, snapshot, check = self.starter_project()
        theme = next((snapshot / 'themes/minimal').rglob('*.kt'))
        theme.write_text(theme.read_text() + '\n// forked\n')

        def gone(catalog, starter):
            raise starters.UnknownStarter('There is no starter theme minimal.')
        with mock.patch.object(starters.Starters, 'resolve', gone):
            check()  # nothing to compare with: the project still builds

    def test_the_prompt_takes_the_starter_and_is_unchanged_without_one(self):
        from rose.app_builder import generator
        for kind in ('create', 'revise', 'repair'):
            self.assertEqual(generator.prompt(kind, 'x', starter=None), generator.prompt(kind, 'x'))
            generator.prompt(kind, 'x', starter={'template': 'feed', 'theme': 'minimal'})

    def test_the_real_starters_resolve_and_refuse(self):
        if not starters.available():
            self.skipTest('this checkout has no workspace/rose-starters')
        catalog = starters.Starters.load()
        for template in catalog.templates:
            for theme in catalog.themes:
                catalog.resolve({'template': template, 'theme': theme})
        with self.assertRaises(starters.UnknownStarter):
            catalog.resolve({'template': 'nonexistent', 'theme': next(iter(catalog.themes))})


@unittest.skipUnless(BUILDS and starters.available(),
                     'needs ROSE_GRADLE_STACK, the legacy SDK, bubblewrap as a non-root Linux user and the starters')
class StarterPilotsTest(Base):
    """Starter pilots through the whole job with the real starters and a scripted writer that changes nothing
    (rose.app_builder.starter_pilots: compiled starter trees, not writer-generated apps)."""

    def test_each_pilot_builds_with_exactly_its_template_and_theme_and_its_own_key(self):
        catalog = starters.Starters.load()
        pilots = starter_pilots.all_pilots(catalog)
        self.assertEqual(len(pilots), 9)
        if os.environ.get('ROSE_STARTER_PILOTS') != 'all':
            pilots = pilots[:1]
        tools = toolchain.Tools(SDK, JAVA_HOME, timeout_s=900, key_creation=True)
        self.root.mkdir(parents=True, exist_ok=True)
        out = self.root / 'pilots'
        manifest = starter_pilots.build(out, pilots, tools, gradle_tools(), catalog, work=self.root / 'work',
                                        log=lambda m: None)
        self.assertEqual(manifest['label'], starter_pilots.LABEL)
        self.assertEqual([(p['template'], p['theme'], p['state']) for p in manifest['pilots']],
                         [(t, h, 'ready') for t, h in pilots])
        self.assertEqual(json.loads((out / 'pilots.json').read_text()), manifest)
        sums = dict(reversed(line.split('  ')) for line in (out / 'SHA256SUMS').read_text().splitlines())
        self.assertEqual(set(sums), {p['apk'] for p in manifest['pilots']} | {'pilots.json'})
        self.assertFalse(any(path.suffix in ('.p12', '.jks', '.keystore') for path in out.rglob('*')))  # no keys
        for record in manifest['pilots']:
            apk = out / record['apk']
            self.assertEqual(sums[record['apk']], record['apkSha256'])
            self.assertEqual(starter_pilots.sha256(apk), record['apkSha256'])
            self.assertTrue(record['launchableActivity'].startswith(record['packageName'] + '.'), record)
            # Installs on every phone Rose runs on and on current Android (the starter chooses its own levels).
            self.assertLessEqual(record['minSdk'], gradle_stack.MAX_MIN_SDK)
            self.assertGreaterEqual(record['targetSdk'], gradle_stack.MIN_TARGET_SDK)
            with zipfile.ZipFile(apk) as z:
                dex = b''.join(z.read(n) for n in z.namelist() if re.fullmatch(r'classes\d*\.dex', n))
            found = {m.decode() for m in re.findall(rb'Lapp/rose/(?:theme|template)/[a-z0-9]+/', dex)}
            tmpl, them = catalog.resolve({'template': record['template'], 'theme': record['theme']})
            wanted = {'L' + tmpl.object.rsplit('.', 1)[0].replace('.', '/') + '/',
                      'L' + them.object.rsplit('.', 1)[0].replace('.', '/') + '/'}
            self.assertEqual(found, wanted)  # exactly that template and that theme
        self.assertEqual(len({p['signerSha256'] for p in manifest['pilots']}), len(pilots))  # each its own key


if __name__ == '__main__':
    unittest.main()
