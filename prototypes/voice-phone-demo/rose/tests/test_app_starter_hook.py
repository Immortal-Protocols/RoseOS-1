"""The builder's starter hook (docs/APP-BUILDER.md §2.4): a create request may name a Rose starter
{template, theme}; it is checked before anything is recorded, kept with the project, and a new project's first tree
comes from it (rose.app_builder.starters).

The hook and its refusals run everywhere. Pilots built through the whole job (Gradle in bubblewrap, binary checks,
signing) need the pinned stack (ROSE_GRADLE_STACK), bubblewrap as a non-root Linux user and the legacy SDK;
ROSE_REQUIRE_GRADLE_BUILD=1 makes a missing one a failure. One pilot builds by default (about two minutes);
ROSE_STARTER_PILOTS=all builds every template under every theme (nine, about seventeen minutes on four CPUs). They are
compiled starter trees with a scripted writer that changes nothing: evidence that each pairing builds through the
builder, not that Rose's writer made them."""
import os
import re
import unittest
import zipfile

from rose.app_builder import gradle, starters, toolchain
from rose.app_builder.service import Builder, Refused
from rose.tests.test_app_builder import SDK, JAVA_HOME, Base, fake_backup
from rose.tests.test_app_gradle_builder import BUILDS, gradle_tools


class FakeGradle:
    """Stands in for GradleTools where no build runs: the builder only asks whether it has one."""


def nothing(source, kind, request, cancelled):
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

    def test_the_real_starters_resolve_and_refuse(self):
        if not starters.available():
            self.skipTest('this checkout has no workspace/rose-starters')
        catalog = starters.Starters.load()
        for template in catalog.templates:
            for theme in catalog.themes:
                catalog.resolve({'template': template, 'theme': theme})
        with self.assertRaises(starters.UnknownStarter):
            catalog.resolve({'template': 'nonexistent', 'theme': next(iter(catalog.themes))})


# One fixed request per template; only the theme varies (Wave 3's comparison).
REQUESTS = {'feed': 'A feed of short posts to scroll, open and come back to.',
            'sprite-game': 'A short sprite game: move, collide and restart.',
            'player': 'A player for one passage: play, scrub and loop it.'}


@unittest.skipUnless(BUILDS and starters.available(),
                     'needs ROSE_GRADLE_STACK, the legacy SDK, bubblewrap as a non-root Linux user and the starters')
class StarterPilotsTest(Base):
    """Starter pilots through the whole job with the real starters and a scripted writer that changes nothing
    (compiled starter trees, not writer-generated apps)."""

    def test_each_pilot_builds_with_exactly_its_template_and_theme_and_its_own_key(self):
        catalog = starters.Starters.load()
        tools = toolchain.Tools(SDK, JAVA_HOME, timeout_s=900, key_creation=True)
        pilots = [(t, h) for t in catalog.templates for h in catalog.themes]
        self.assertEqual(len(pilots), 9)
        if os.environ.get('ROSE_STARTER_PILOTS') != 'all':
            pilots = pilots[:1]
        signers = set()
        for template, theme in pilots:
            with self.subTest(template=template, theme=theme):
                b = Builder(self.root / f'{template}-{theme}', tools, nothing, backup=fake_backup,
                            gradle=gradle_tools(), skeleton=starters.skeleton,
                            resolve_starter=lambda s: catalog.resolve(s))
                self.addCleanup(b.ledger.close)
                out = b.create('d', {'schemaVersion': 1, 'clientRequestId': 'r',
                                     'description': REQUESTS.get(template, f'A {template} app.'),
                                     'starter': {'template': template, 'theme': theme}})
                b.run_next()
                job = b.job(out['jobId'])
                self.assertEqual(job['state'], 'ready', job)
                project = b.projects()[0]
                version = project['versions'][0]
                apk = b.root / 'artifacts' / f'{version["apk"]["sha256"]}.apk'
                with zipfile.ZipFile(apk) as z:
                    dex = b''.join(z.read(n) for n in z.namelist() if re.fullmatch(r'classes\d*\.dex', n))
                found = {m.decode() for m in re.findall(rb'Lapp/rose/(?:theme|template)/[a-z0-9]+/', dex)}
                tmpl, them = catalog.resolve({'template': template, 'theme': theme})
                wanted = {'L' + tmpl.object.rsplit('.', 1)[0].replace('.', '/') + '/',
                          'L' + them.object.rsplit('.', 1)[0].replace('.', '/') + '/'}
                self.assertEqual(found, wanted)  # exactly that template and that theme
                signers.add(project['signerSha256'])
        self.assertEqual(len(signers), len(pilots))  # each app its own key


if __name__ == '__main__':
    unittest.main()
