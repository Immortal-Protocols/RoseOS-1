"""rose.app_builder.starters: a new app's first tree from one Rose starter template under one theme."""
import importlib.util
import os
import re
import shutil
import tempfile
import unittest
from pathlib import Path

from rose.app_builder import starters

PACKAGE = 'app.rose.gen.p0123456789abcdef'
ROOT = starters.ROOT
PILOTS = ROOT / 'matrix/src/main/kotlin/app/rose/starters/matrix/Pilots.kt'


def combos(s):
    return [(t, h) for t in s.templates for h in s.themes]


class Manifest(unittest.TestCase):
    def setUp(self):
        self.s = starters.Starters.load()

    def test_every_entry_is_a_real_kotlin_object_with_that_id(self):
        for kind in (self.s.templates, self.s.themes):
            for entry in kind.values():
                folder = ROOT / entry.dir
                self.assertTrue((folder / 'build.gradle.kts').is_file(), entry.dir)
                pkg, name = entry.object.rsplit('.', 1)
                sources = list((folder / 'src/main/kotlin' / pkg.replace('.', '/')).glob('*.kt'))
                declared = [f for f in sources if re.search(rf'^object {name}\b', f.read_text(), re.M)]
                self.assertEqual(1, len(declared), f'{entry.object} is declared once in {entry.dir}')
                self.assertIn(f'override val id = "{entry.id}"', declared[0].read_text(), f'{entry.object} has id {entry.id}')

    def test_the_manifest_matches_the_matrix_pilots(self):
        pilots = PILOTS.read_text()
        for entry in (*self.s.templates.values(), *self.s.themes.values()):
            self.assertIn(f'import {entry.object}', pilots, f'{entry.object} is in the matrix')
        self.assertEqual(3, len(self.s.templates))
        self.assertEqual(3, len(self.s.themes))

    def test_shared_modules_exist(self):
        for folder in self.s.shared:
            self.assertTrue((ROOT / folder / 'build.gradle.kts').is_file(), folder)


class Tree(unittest.TestCase):
    def setUp(self):
        self.s = starters.Starters.load()

    def test_all_nine_combinations(self):
        for t, h in combos(self.s):
            with self.subTest(template=t, theme=h):
                files = self.s.files(PACKAGE, 'Notes & "things"', {'template': t, 'theme': h})
                tmpl, theme = self.s.templates[t], self.s.themes[h]
                settings = files['settings.gradle.kts'].decode()
                for module in (':app', ':design', ':design-testing', ':examples', tmpl.module, theme.module):
                    self.assertIn(f'"{module}"', settings)
                others = [e.module for e in (*self.s.templates.values(), *self.s.themes.values())
                          if e not in (tmpl, theme)]
                for module in others:
                    self.assertNotIn(f'"{module}"', settings, 'only the chosen template and theme are included')
                    self.assertFalse(any(n.startswith(module[1:].replace(':', '/') + '/') for n in files))
                activity = files[f'app/src/main/java/{PACKAGE.replace(".", "/")}/MainActivity.kt'].decode()
                self.assertIn(f'package {PACKAGE}\n', activity)
                self.assertIn(f'import {tmpl.object}\n', activity)
                self.assertIn(f'import {theme.object}\n', activity)
                self.assertIn(f'RoseTheme({theme.simple}) {{\n                {tmpl.simple}.App()', activity)
                app = files['app/build.gradle.kts'].decode()
                self.assertIn(f'namespace = "{PACKAGE}"', app)
                self.assertIn(f'implementation(project("{tmpl.module}"))', app)
                self.assertIn(f'implementation(project("{theme.module}"))', app)
                self.assertIn(b'&amp; \\"things\\"', files['app/src/main/res/values/strings.xml'])
                self.assertEqual((ROOT / 'gradle/libs.versions.toml').read_bytes(), files['gradle/libs.versions.toml'])
                self.assertIn('alias(libs.plugins.android.library) apply false', files['build.gradle.kts'].decode())
                for name in files:
                    parts = Path(name).parts
                    self.assertFalse(set(parts) & starters.SKIP_DIRS, name)
                    self.assertFalse(name.endswith(('.jar', '.aar', '.so', '.dex', '.apk', '.iml', 'local.properties')), name)
                    self.assertFalse(name.startswith(('matrix/', 'sample/', 'gradle/wrapper/')), name)

    def test_skeleton_writes_the_tree(self):
        with tempfile.TemporaryDirectory() as d:
            starters.skeleton(Path(d), PACKAGE, 'Feed', {'template': 'feed', 'theme': 'minimal'})
            self.assertTrue((Path(d) / 'templates/feed/build.gradle.kts').is_file())
            self.assertTrue((Path(d) / 'themes/minimal/build.gradle.kts').is_file())
            self.assertFalse((Path(d) / 'themes/material3').exists())
            self.assertTrue((Path(d) / 'app/src/main/AndroidManifest.xml').is_file())

    def test_unknown_or_malformed_starters_are_refused(self):
        for bad in ({'template': 'feed', 'theme': 'paper'}, {'template': 'tally', 'theme': 'minimal'},
                    {'template': 'feed'}, {'template': 'feed', 'theme': 'minimal', 'extra': 1},
                    {'template': 1, 'theme': 'minimal'}, 'feed', None, []):
            with self.subTest(starter=bad), self.assertRaises(starters.UnknownStarter):
                self.s.resolve(bad)


class Vendoring(unittest.TestCase):
    """Only source is copied: never build output, Gradle/IDE state or local paths; links are refused."""

    def copy(self, d):
        root = Path(d) / 'rose-starters'
        shutil.copytree(ROOT, root, ignore=shutil.ignore_patterns('build', '.gradle', '.kotlin', 'local.properties'))
        return root

    def test_build_state_and_local_files_are_left_behind(self):
        with tempfile.TemporaryDirectory() as d:
            root = self.copy(d)
            for junk in ('design/build/outputs/x.apk', 'design/.gradle/state.bin', 'themes/minimal/.kotlin/a',
                         'templates/feed/local.properties', 'templates/feed/feed.iml'):
                (root / junk).parent.mkdir(parents=True, exist_ok=True)
                (root / junk).write_text('x')
            files = starters.Starters.load(root).files(PACKAGE, 'x', {'template': 'feed', 'theme': 'minimal'})
            for folder in ('design/build/', 'design/.gradle/', 'themes/minimal/.kotlin/'):
                self.assertFalse(any(n.startswith(folder) for n in files), folder)
            self.assertFalse(any(n.endswith(('local.properties', '.iml')) for n in files))
            self.assertIn('design/build.gradle.kts', files, 'a module build file is source, not build state')

    def test_a_link_in_a_vendored_module_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            root = self.copy(d)
            os.symlink('/etc/hostname', root / 'design/src/main/kotlin/link.kt')
            with self.assertRaises(ValueError):
                starters.Starters.load(root).files(PACKAGE, 'x', {'template': 'feed', 'theme': 'minimal'})


class Prompt(unittest.TestCase):
    """The writer's turn on a starter project names the starter and where to work, on every kind of turn."""

    def test_create_repair_and_revise_carry_the_starter_guidance(self):
        from rose.app_builder import generator
        starter = {'template': 'sprite-game', 'theme': 'skeuomorphic'}
        for kind in ('create', 'repair', 'revise'):
            with self.subTest(kind=kind):
                text = generator.prompt(kind, 'REQUEST', starter=starter)
                self.assertIn('Rose starter template "sprite-game" under the "skeuomorphic" theme', text)
                self.assertIn('templates/sprite-game/', text)
                self.assertIn('themes/skeuomorphic/', text)
                self.assertIn('never Material or a theme directly', text)
                self.assertTrue(text.endswith('REQUEST'))

    def test_no_starter_is_the_unchanged_prompt(self):
        from rose.app_builder import generator
        for kind in ('create', 'repair', 'revise'):
            self.assertEqual(generator.prompt(kind, 'R'), generator.prompt(kind, 'R', starter=None))
            self.assertNotIn('Rose starter', generator.prompt(kind, 'R'))


class OutsideTemplate(unittest.TestCase):
    """After a writer turn: anything changed outside the template and app's resources is reported."""

    def make(self, d, starter={'template': 'feed', 'theme': 'minimal'}):
        starters.skeleton(Path(d), PACKAGE, 'Feed', starter)
        return starter

    def test_a_fresh_tree_and_template_or_resource_edits_are_clean(self):
        with tempfile.TemporaryDirectory() as d:
            starter = self.make(d)
            self.assertEqual([], starters.outside_template(Path(d), starter))
            kt = next((Path(d) / 'templates/feed/src/main/kotlin').rglob('FeedTemplate.kt'))
            kt.write_text(kt.read_text() + '\n// the owner\'s notice board\n')
            (Path(d) / 'templates/feed/src/main/kotlin/New.kt').write_text('package x\n')
            (Path(d) / 'app/src/main/res/values/strings.xml').write_text('<resources/>')
            (Path(d) / 'templates/feed/build').mkdir()
            self.assertEqual([], starters.outside_template(Path(d), starter))

    def test_theme_framework_catalog_and_wiring_edits_are_reported(self):
        with tempfile.TemporaryDirectory() as d:
            starter = self.make(d)
            theme = next((Path(d) / 'themes/minimal/src/main/kotlin').rglob('*.kt'))
            theme.write_text(theme.read_text().replace('Minimal', 'Forked', 1))
            (Path(d) / 'design/src/main/kotlin/app/rose/design/Extra.kt').write_text('package app.rose.design\n')
            (Path(d) / 'gradle/libs.versions.toml').write_text('# changed\n')
            activity = next((Path(d) / 'app/src/main/java').rglob('MainActivity.kt'))
            activity.write_text(activity.read_text().replace('RoseTheme(MinimalTheme)', 'RoseTheme(BaselineTheme)'))
            removed = Path(d) / 'examples/build.gradle.kts'
            removed.unlink()
            found = starters.outside_template(Path(d), starter)
            self.assertIn(theme.relative_to(d).as_posix(), found)
            self.assertIn('design/src/main/kotlin/app/rose/design/Extra.kt', found)
            self.assertIn('gradle/libs.versions.toml', found)
            self.assertIn(activity.relative_to(d).as_posix(), found)
            self.assertIn('examples/build.gradle.kts', found)
            self.assertFalse([f for f in found if f.startswith('templates/')])


HAS_GRADLE_BUILDER = importlib.util.find_spec('rose.app_builder.gradle') is not None


@unittest.skipUnless(HAS_GRADLE_BUILDER, "needs the builder's Gradle support (rose.app_builder.gradle)")
class WithTheGradleBuilder(unittest.TestCase):
    def test_no_starter_is_the_builders_plain_skeleton(self):
        from rose.app_builder import gradle
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            starters.skeleton(Path(a), PACKAGE, 'Plain', None)
            gradle.write_skeleton(Path(b), PACKAGE, 'Plain')
            listing = lambda p: sorted(str(f.relative_to(p)) for f in Path(p).rglob('*') if f.is_file())
            self.assertEqual(listing(b), listing(a))

    def test_every_combination_passes_the_builders_source_checks(self):
        from rose.app_builder import gradle
        s = starters.Starters.load()
        for t, h in combos(s):
            with self.subTest(template=t, theme=h), tempfile.TemporaryDirectory() as d:
                os.chmod(d, 0o700)
                starters.skeleton(Path(d), PACKAGE, 'App', {'template': t, 'theme': h})
                gradle.check(Path(d))


if __name__ == '__main__':
    unittest.main()
