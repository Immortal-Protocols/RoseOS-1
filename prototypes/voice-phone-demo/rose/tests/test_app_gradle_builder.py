"""Ordinary Gradle projects through the whole job (docs/APP-BUILDER.md §2.4): a new app starts as a Kotlin +
Compose project, builds offline inside the build isolation, is signed with its own key after the build and updates
in place; an app in the older flat layout keeps building and can move to Gradle without changing its package or
signer; a Kotlin build error goes back to the writer.

The layout rules run everywhere. The builds need the pinned stack (ROSE_GRADLE_STACK, from
``python -m rose.app_builder.gradle_prepare stack``) and bubblewrap as a non-root Linux user; with
ROSE_REQUIRE_GRADLE_BUILD=1 a missing one fails instead of skipping."""
import os
import platform
import re
import shutil
import stat
import subprocess
import unittest
from pathlib import Path

from rose.app_builder import appprofile, gradle, gradle_stack, template, toolchain
from rose.app_builder.service import Builder
from rose.tests.test_app_builder import HAS_SDK, JAVA_HOME, SDK, Base, fake_backup

STACK = Path(os.environ.get('ROSE_GRADLE_STACK', '/nonexistent'))
REQUIRED = bool(os.environ.get('ROSE_REQUIRE_GRADLE_BUILD'))
CAN_ISOLATE = (platform.system() == 'Linux' and platform.machine() == 'x86_64' and os.geteuid() != 0
               and Path('/usr/bin/bwrap').is_file())
HAS_STACK = all((STACK / d).is_dir() for d in ('jdk', 'gradle', 'sdk', 'cache'))
BUILDS = (HAS_STACK and CAN_ISOLATE and HAS_SDK) or REQUIRED


def gradle_tools() -> gradle.GradleTools:
    from rose.app_builder.isolation import BubblewrapIsolation
    return gradle.GradleTools(STACK / 'jdk', STACK / 'gradle', STACK / 'sdk', STACK / 'cache',
                              BubblewrapIsolation(), timeout_s=1500)


def main_activity(source: Path) -> Path:
    return next(source.glob('app/src/main/java/**/MainActivity.kt'))


def extra(source: Path, text: str) -> Path:
    """A Kotlin file of the writer's own next to the skeleton's activity, in the app's package."""
    activity = main_activity(source)
    package = re.search(r'^package\s+([\w.]+)', activity.read_text(), re.M).group(1)
    path = activity.parent / 'Extra.kt'
    path.write_text(f'package {package}\n\n{text}\n')
    return path


def badging(apk: Path) -> str:
    aapt = SDK / 'build-tools/android-15/aapt'
    return subprocess.run([str(aapt), 'dump', 'badging', str(apk)], capture_output=True, text=True, check=True).stdout


def signer(apk: Path) -> str:
    apksigner = SDK / 'build-tools/android-15/apksigner'
    env = dict(os.environ, **({'JAVA_HOME': str(JAVA_HOME)} if JAVA_HOME else {}))
    out = subprocess.run([str(apksigner), 'verify', '--print-certs', str(apk)], capture_output=True, text=True,
                         check=True, env=env).stdout
    return re.search(r'certificate SHA-256 digest: ([0-9a-f]{64})', out).group(1)


class LayoutTest(Base):
    """What decides the build path, and the builder-owned parts of a Gradle layout (no stack needed)."""

    def gradle_tree(self) -> Path:
        tree = self.root / 'tree'
        tree.mkdir(mode=0o700, parents=True)
        gradle.write_skeleton(tree, 'app.rose.gen.pabc', 'Notes')
        return tree

    def test_the_skeleton_is_an_ordinary_kotlin_compose_project_with_the_builders_identity(self):
        tree = self.gradle_tree()
        self.assertTrue(gradle.is_gradle_project(tree))
        build = (tree / 'app/build.gradle.kts').read_text()
        self.assertIn('namespace = "app.rose.gen.pabc"', build)
        self.assertIn('applicationId = "app.rose.gen.pabc"', build)
        self.assertIn(f'compileSdk = {gradle_stack.COMPILE_SDK}', build)
        self.assertIn(gradle_stack.COMPOSE_BOM, build)
        self.assertNotIn('package=', (tree / 'app/src/main/AndroidManifest.xml').read_text())
        self.assertIn('setContent', main_activity(tree).read_text())
        self.assertEqual(appprofile.check(tree, 'app.rose.gen.pabc').name, appprofile.APP_V1)
        flat = self.root / 'flat'
        flat.mkdir(mode=0o700, parents=True)
        template.write_skeleton(flat, 'app.rose.gen.pabc', 'Notes')
        self.assertFalse(gradle.is_gradle_project(flat))  # the older layout keeps its own path

    def test_the_builders_rose_package_stays_the_builders_in_a_gradle_project(self):
        for path, text in [
                ('app/src/main/java/app/rose/gen/pabc/rose/Api.kt', 'package app.rose.gen.pabc.rose\n'),
                ('app/src/main/kotlin/app/rose/gen/pabc/rose/Api.kt', 'package app.rose.gen.pabc.rose\n'),
                ('app/src/main/java/app/rose/gen/pabc/Other.kt', '@file:JvmName("X")\npackage app.rose.gen.pabc.rose\n'),
                ('app/src/main/java/app/rose/gen/pabc/Other.java', 'package app.rose.gen.pabc.rose.x;\n')]:
            with self.subTest(path):
                tree = self.gradle_tree()
                target = tree / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(text)
                with self.assertRaises(appprofile.ProfileRefused) as refused:
                    appprofile.check(tree, 'app.rose.gen.pabc')
                self.assertEqual(refused.exception.reason, 'reserved_path')
                shutil.rmtree(tree)
        self.assertEqual(appprofile.client_root(gradle=True), 'app/src/main/java/')
        self.assertEqual(appprofile.client_root(), 'src/')

    def test_a_retired_client_server_app_is_refused_in_either_layout(self):
        tree = self.gradle_tree()
        (tree / 'rose-app.json').write_text('{"schemaVersion": 1, "profile": "client-server-v1", '
                                            '"server": {"crates": []}}')
        with self.assertRaises(appprofile.ProfileRefused) as refused:
            appprofile.check(tree, 'app.rose.gen.pabc')
        self.assertEqual(refused.exception.reason, 'legacy_backend')

    def test_a_gradle_project_on_a_builder_without_gradle_fails_as_a_toolchain_matter(self):
        def to_gradle(source, kind, request, cancelled):
            for entry in list(source.iterdir()):
                shutil.rmtree(entry) if entry.is_dir() else entry.unlink()
            gradle.write_skeleton(source, next(iter(b.projects()))['packageName'], 'x')
        tools = toolchain.Tools(SDK, JAVA_HOME, timeout_s=300, key_creation=True)
        b = Builder(self.root / 'builder', tools, to_gradle, backup=fake_backup)
        self.addCleanup(b.ledger.close)
        out = b.create('d', {'schemaVersion': 1, 'clientRequestId': 'r', 'description': 'x'})
        b.run_next()
        job = b.job(out['jobId'])
        self.assertEqual((job['state'], job['failure']['category']), ('failed', 'toolchain'), job)
        self.assertEqual(b.projects()[0]['versions'], [])


class BinaryChecksTest(unittest.TestCase):
    """verify_apk's reading of ``aapt dump xmltree`` and the repair text, on the shapes the tools print."""

    def test_a_namespace_declaration_does_not_hide_a_top_level_element(self):
        tree = ('N: android=http://schemas.android.com/apk/res/android (line=2)\n'
                '  E: manifest (line=2)\n'
                '    E: uses-sdk (line=7)\n'
                '    N: x=urn:x (line=9)\n'
                '      E: instrumentation (line=9)\n'
                '        E: meta-data (line=10)\n'
                '    E: application (line=17)\n'
                '      N: y=urn:y (line=20)\n'
                '        E: provider (line=20)\n')
        self.assertEqual(toolchain.manifest_children(tree), ['uses-sdk', 'instrumentation', 'application'])

    def test_native_libraries_must_cover_the_phone_and_emulator_abis(self):
        from rose.app_builder.gradle import _missing_native
        every = [f'lib/{abi}/libandroidx.graphics.path.so' for abi in ('arm64-v8a', 'armeabi-v7a', 'x86', 'x86_64')]
        self.assertIsNone(_missing_native(['classes.dex', 'AndroidManifest.xml']))  # no native code: nothing needed
        self.assertIsNone(_missing_native(every))
        self.assertIsNone(_missing_native([n for n in every if '/x86/' not in n]))  # 32-bit ABIs are optional
        self.assertEqual(_missing_native(['lib/x86_64/libandroidx.graphics.path.so']),
                         'lib/arm64-v8a/libandroidx.graphics.path.so')  # abiFilters += "x86_64"
        self.assertEqual(_missing_native([*every, 'lib/arm64-v8a/libother.so']), 'lib/x86_64/libother.so')

    def test_a_moved_namespace_chunk_cannot_shift_elements_out_of_the_manifest(self):
        good = 'N: android=x (line=2)\n  E: manifest (line=2)\n    E: uses-sdk (line=7)\n    E: application\n'
        moved = 'N: android=x\n  N: x=urn:x\n    E: manifest\n    E: overlay\n    E: application\n'
        inner = 'N: android=x\n  E: manifest\n    N: x=urn:x\n      E: instrumentation\n'
        self.assertTrue(toolchain.manifest_well_formed(good))
        self.assertFalse(toolchain.manifest_well_formed(moved))  # overlay would print at the manifest's own depth
        self.assertFalse(toolchain.manifest_well_formed(inner))  # a namespace declared inside the document
        self.assertFalse(toolchain.manifest_well_formed('N: android=x\n'))  # no manifest at all

    def test_the_repair_text_keeps_a_kotlin_errors_file(self):
        from rose.app_builder.service import _repair_text
        failed = toolchain.StepFailed('compile', 'The app\'s Gradle build failed in its Kotlin code.',
                                      log='e: file://<project>/app/src/main/java/a/b/Extra.kt:3:5 Unresolved '
                                          'reference: x\ne: file:///r/jobs/j/snapshot-1/app/B.kt:1:1 x')
        text = _repair_text(failed, ['/r/jobs/j/snapshot-1', '/r/jobs/j', '/r'])
        self.assertIn('e: app/src/main/java/a/b/Extra.kt:3:5 Unresolved reference: x', text)
        self.assertIn('e: app/B.kt:1:1', text)


class GradleConfigTest(Base):
    """The builder config's ``gradle`` object (rose.app_builder.host): the pinned stack and the only qualified isolation."""

    def stack(self) -> dict:
        folders = {}
        for key in ('jdk', 'distribution', 'sdk', 'dependencyCache'):
            path = (self.root / 'stack' / key).resolve()
            path.mkdir(parents=True, exist_ok=True)
            folders[key] = str(path)
        return dict(folders, isolation='bubblewrap')

    def parse(self, gradle_value):
        from rose.app_builder import host
        return host.parse_builder_config({'schemaVersion': 1, 'root': str(self.root / 'builder-root'),
                                          'sdk': str(SDK), 'gradle': gradle_value})

    def test_a_stack_and_bubblewrap_are_accepted_with_their_defaults(self):
        config = self.parse(self.stack()).gradle
        self.assertEqual((config.isolation, config.bubblewrap, config.build_timeout_s),
                         ('bubblewrap', Path('/usr/bin/bwrap'), 1800.0))
        self.assertIsNone(__import__('rose.app_builder.host', fromlist=['x']).parse_builder_config(
            {'schemaVersion': 1, 'root': str(self.root / 'r'), 'sdk': str(SDK)}).gradle)

    def test_refusals(self):
        from rose.app_builder.host import ConfigError
        good = self.stack()
        for name, value, words in [
                ('the unqualified macOS isolation', dict(good, isolation='seatbelt'), 'not qualified'),
                ('an unknown isolation', dict(good, isolation='none'), 'must be bubblewrap'),
                ('a missing part', {k: v for k, v in good.items() if k != 'dependencyCache'}, 'gradle must be'),
                ('an unknown key', dict(good, extra='x'), 'gradle must be'),
                ('a relative path', dict(good, jdk='jdk'), 'absolute path'),
                ('a missing folder', dict(good, sdk=str(self.root / 'nowhere')), 'gradle.sdk must be'),
                ('a timeout out of range', dict(good, buildTimeoutS=0), 'buildTimeoutS'),
                ('not an object', 'stack', 'gradle must be')]:
            with self.subTest(name), self.assertRaises(ConfigError) as refused:
                self.parse(value)
            self.assertIn(words, str(refused.exception))


@unittest.skipUnless(BUILDS, 'needs ROSE_GRADLE_STACK, the legacy SDK and bubblewrap as a non-root Linux user')
class GradleJobTest(Base):
    def builder(self, generate, with_gradle=True, root=None) -> Builder:
        tools = toolchain.Tools(SDK, JAVA_HOME, timeout_s=600, key_creation=True)
        b = Builder(root or self.root / 'builder', tools, generate, backup=fake_backup,
                    gradle=gradle_tools() if with_gradle else None)
        self.addCleanup(b.ledger.close)
        return b

    def delivered(self, b: Builder, version: int) -> Path:
        v = next(v for v in b.projects()[0]['versions'] if v['versionCode'] == version)
        return b.root / 'artifacts' / f'{v["apk"]["sha256"]}.apk'

    def test_a_new_kotlin_compose_app_builds_signs_and_updates_in_place(self):
        def write(source, kind, request, cancelled):
            extra(source, f'const val NOTE = "{request}"')
            return f'{kind}: {request}'
        b = self.builder(write)
        out = b.create('d', {'schemaVersion': 1, 'clientRequestId': 'r1', 'description': 'A counter'})
        b.run_next()
        job = b.job(out['jobId'])
        self.assertEqual(job['state'], 'ready', job)
        project = b.projects()[0]
        first = self.delivered(b, 1)
        found = badging(first)
        self.assertIn(f"package: name='{project['packageName']}' versionCode='1' versionName='1'", found)
        self.assertIn(f"targetSdkVersion:'{gradle_stack.TARGET_SDK}'", found)
        self.assertIn(f"sdkVersion:'{gradle_stack.MIN_SDK}'", found)
        self.assertEqual(signer(first), project['signerSha256'])
        source = b.root / 'projects' / project['projectId'] / 'source'
        self.assertTrue((source / 'app/build.gradle.kts').is_file())

        out = b.revise('d', project['projectId'], {'schemaVersion': 1, 'clientRequestId': 'r2', 'request': 'Bigger'})
        b.run_next()
        self.assertEqual(b.job(out['jobId'])['state'], 'ready', b.job(out['jobId']))
        second = self.delivered(b, 2)
        self.assertIn(f"package: name='{project['packageName']}' versionCode='2' versionName='2'", badging(second))
        self.assertEqual(signer(second), project['signerSha256'])  # the same key: Android updates it in place
        self.assertFalse(any((b.root / 'jobs').rglob('modules-2')), 'a job keeps no Gradle cache behind')

    def test_an_app_in_the_older_layout_moves_to_gradle_with_the_same_package_and_signer(self):
        def label(source, kind, request, cancelled):
            (source / 'res/values/notes.xml').write_text(
                '<resources><string name="note">' + template.android_string(request) + '</string></resources>')
        legacy = self.builder(label, with_gradle=False)
        out = legacy.create('d', {'schemaVersion': 1, 'clientRequestId': 'r1', 'description': 'Notes'})
        legacy.run_next()
        self.assertEqual(legacy.job(out['jobId'])['state'], 'ready', legacy.job(out['jobId']))
        project = legacy.projects()[0]
        legacy.ledger.close()

        def convert(source, kind, request, cancelled):  # what a writer may do in a revise
            for entry in list(source.iterdir()):
                shutil.rmtree(entry) if entry.is_dir() else entry.unlink()
            gradle.write_skeleton(source, project['packageName'], 'Notes')
            (source / '.gitignore').write_text('/build\n/local.properties\n')
        b = self.builder(convert, root=legacy.root)
        out = b.revise('d', project['projectId'], {'schemaVersion': 1, 'clientRequestId': 'r2', 'request': 'Compose'})
        b.run_next()
        self.assertEqual(b.job(out['jobId'])['state'], 'ready', b.job(out['jobId']))
        self.assertIn(f"package: name='{project['packageName']}' versionCode='2'", badging(self.delivered(b, 2)))
        self.assertEqual(signer(self.delivered(b, 2)), signer(self.delivered(b, 1)))
        source = b.root / 'projects' / project['projectId'] / 'source'
        self.assertTrue((source / '.gitignore').is_file(), 'a recorded .gitignore survives the reset')

    def test_a_kotlin_build_error_goes_back_to_the_writer(self):
        calls = []

        def write(source, kind, request, cancelled):
            calls.append((kind, request))
            extra(source, 'val answer: Int = 42' if kind == 'repair' else 'val answer: Int = "not a number"')
        b = self.builder(write)
        out = b.create('d', {'schemaVersion': 1, 'clientRequestId': 'r', 'description': 'x'})
        b.run_next()
        self.assertEqual(b.job(out['jobId'])['state'], 'ready', b.job(out['jobId']))
        self.assertEqual([k for k, _ in calls], ['create', 'repair'])
        self.assertRegex(calls[1][1], r'app/src/main/java/\S*Extra\.kt:\d+')  # its own file, relative
        self.assertNotIn(str(b.root), calls[1][1])
        self.assertNotIn(str(STACK), calls[1][1])


if __name__ == '__main__':
    unittest.main()
