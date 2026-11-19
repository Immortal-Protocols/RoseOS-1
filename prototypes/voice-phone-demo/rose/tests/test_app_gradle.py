"""Ordinary Gradle Android projects (rose.app_builder.gradle) and the APK identity checks (toolchain.verify_apk).

Unit tests need no stack: the source rules, the skeleton, diagnostics and classification, the output and signature
checks (planted links included), and build()'s isolation contract with a fake isolation and a fake stack.

The integration tests build real projects with the pinned stack inside BubblewrapIsolation. They run when
ROSE_GRADLE_STACK names a folder holding jdk/ gradle/ sdk/ cache/ (python -m rose.app_builder.gradle_prepare stack),
ANDROID_SDK_ROOT is the legacy SDK (signing and the API database) and bubblewrap can run (non-root Linux x86-64,
/usr/bin/bwrap); with ROSE_REQUIRE_GRADLE_BUILD=1 they fail instead of skipping. Bubblewrap refuses root, so run them
as an unprivileged user with TMPDIR in a folder that user owns and every mount can be reached, e.g.
``setpriv --reuid 65534 --regid 65534 --clear-groups env TMPDIR=<dir> python3 -m unittest rose.tests.test_app_gradle``.
They prove compilation, confinement of the build's own code and signing here, not installation on a phone.
"""
import concurrent.futures
import io
import json
import os
import platform
import re
import secrets
import shutil
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from unittest import mock

from rose.app_builder import gradle, gradle_stack, template, toolchain
from rose.app_builder.isolation import BuildFailed
from rose.app_builder.isolation import BubblewrapIsolation

PACKAGE = 'com.example.app'


class Temp(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(os.path.realpath(self.temp.name))


class CheckTest(Temp):
    def setUp(self):
        super().setUp()
        self.tree = self.root / 'src'
        self.tree.mkdir()
        gradle.write_skeleton(self.tree, PACKAGE, 'App')

    def put(self, name, text='x'):
        path = self.tree / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def refused(self, **kwargs) -> template.TemplateRefused:
        with self.assertRaises(template.TemplateRefused) as caught:
            gradle.check(self.tree, **kwargs)
        return caught.exception

    def test_skeleton_passes_and_is_a_gradle_project(self):
        gradle.check(self.tree)
        self.assertTrue(gradle.is_gradle_project(self.tree))
        flat = self.root / 'flat'
        template.write_skeleton(flat, PACKAGE, 'App')
        self.assertFalse(gradle.is_gradle_project(flat))

    def test_groovy_build_files_are_ordinary_too(self):
        os.rename(self.tree / 'settings.gradle.kts', self.tree / 'settings.gradle')
        os.rename(self.tree / 'app/build.gradle.kts', self.tree / 'app/build.gradle')
        gradle.check(self.tree)
        self.assertTrue(gradle.is_gradle_project(self.tree))

    def test_layout_needs_settings_and_the_app_module(self):
        (self.tree / 'app/build.gradle.kts').unlink()
        self.assertIn('app/', str(self.refused()))
        self.put('app/build.gradle.kts')
        (self.tree / 'settings.gradle.kts').unlink()
        self.assertIn('settings.gradle', str(self.refused()))
        os.symlink(self.tree / 'build.gradle.kts', self.tree / 'settings.gradle.kts')
        self.assertFalse(gradle.is_gradle_project(self.tree))
        self.refused()

    def test_gitignore_files_are_allowed_other_git_names_are_not(self):
        self.put('.gitignore', '/build\n')
        self.put('app/.gitignore', '/build\n')
        gradle.check(self.tree)
        for name in ('.git/config', '.gitattributes', 'app/.GIT', 'app/src/.gitmodules'):
            path = self.put(name)
            self.assertIn('history', str(self.refused()), name)
            shutil.rmtree(self.tree / '.git') if name.startswith('.git/') else path.unlink()
        (self.tree / 'app/.gitignore').unlink()
        (self.tree / 'app/.gitignore').mkdir()  # a .gitignore that is not a file is a git name like any other
        self.refused()

    def test_key_material_is_refused_anywhere(self):
        for name in ('release.jks', 'app/upload.keystore', 'app/src/main/assets/k.p12', 'k.pk8', 'a/b.pfx',
                     'cert.PEM', 'x.der', 'private.key'):
            path = self.put(name)
            refused = self.refused()
            self.assertIn('key', str(refused), name)
            self.assertFalse(refused.dependency)
            path.unlink()

    def test_libraries_and_binaries_are_refused_but_the_wrapper_jar_is_tolerated(self):
        self.put(gradle.WRAPPER_JAR)
        self.put('gradle/wrapper/gradle-wrapper.properties', 'distributionUrl=https://example.invalid/x.zip\n')
        self.put('gradlew', '#!/bin/sh\nexit 1\n')
        gradle.check(self.tree)
        for name, dependency in (('app/libs/lib.jar', True), ('app/libs/lib.aar', True), ('gradle/other.jar', True),
                                 ('app/src/main/jniLibs/arm64-v8a/libx.so', True), ('x.klib', True),
                                 ('classes.dex', False), ('app/A.class', False), ('old.apk', False),
                                 ('bundle.aab', False)):
            path = self.put(name)
            self.assertEqual(self.refused().dependency, dependency, name)
            path.unlink()

    def test_native_code_is_a_stack_matter(self):
        for name in ('app/src/main/cpp/a.cpp', 'app/src/main/cpp/a.h', 'app/CMakeLists.txt', 'jni/Android.mk',
                     'n.c', 'n.cc', 'n.cxx', 'n.hpp', 'Application.mk'):
            path = self.put(name)
            refused = self.refused()
            self.assertTrue(refused.dependency, name)
            self.assertIn('NDK', str(refused))
            path.unlink()

    def test_committed_build_state_is_refused_at_the_top_and_in_modules(self):
        self.put('lib/build.gradle.kts')  # a second module
        for name in ('build/x', '.gradle/x', '.kotlin/x', '.idea/x', '.cxx/x', 'app/build/x', 'app/.gradle/x',
                     'lib/build/x', 'lib/.kotlin/x'):
            self.put(name)
            self.assertIn('leave it out', str(self.refused()), name)
            shutil.rmtree(self.tree / Path(name).parent)
        for name in ('app.iml', 'app/app.iml'):
            path = self.put(name)
            self.refused()
            path.unlink()
        # Below a module, these names are ordinary source folders.
        self.put('app/src/main/java/com/example/app/build/Build.kt', 'package com.example.app.build\n')
        self.put('docs/build/notes.md')
        gradle.check(self.tree)

    def test_integrity_rules_are_template_checks(self):
        target = self.put('app/src/main/res/raw/a.txt')
        os.symlink(target, self.tree / 'app/src/main/res/raw/b.txt')
        self.assertIn('symbolic link', str(self.refused()))
        (self.tree / 'app/src/main/res/raw/b.txt').unlink()
        os.link(target, self.tree / 'app/src/main/res/raw/c.txt')
        self.assertIn('hard link', str(self.refused()))
        (self.tree / 'app/src/main/res/raw/c.txt').unlink()
        os.mkfifo(self.tree / 'pipe')
        self.assertIn('not a regular file', str(self.refused()))
        (self.tree / 'pipe').unlink()
        target.write_bytes(b'0' * (template.MAX_FILE + 1))
        self.assertIn('larger', str(self.refused()))
        target.write_text('x')
        target.chmod(0o400)
        self.assertIn('readable and writable', str(self.refused()))
        target.chmod(0o600)
        os.mkdir(os.path.join(os.fsencode(self.tree), b'\xff'))
        self.assertIn('UTF-8', str(self.refused()))

    def test_opaque_folders_and_builder_files_skip_only_the_content_rules(self):
        self.put('server/src/native.c')
        self.put('server/target/build/x')
        gradle.check(self.tree, opaque_top=frozenset({'server'}))
        self.refused()
        os.symlink('/etc/passwd', self.tree / 'server/link')
        self.refused(opaque_top=frozenset({'server'}))
        (self.tree / 'server/link').unlink()
        self.put('app/libs/rose-core.jar')
        gradle.check(self.tree, opaque_top=frozenset({'server'}), builder_files=frozenset({'app/libs/rose-core.jar'}))
        self.refused(opaque_top=frozenset({'server'}))


class SkeletonTest(Temp):
    def test_pins_come_from_the_stack(self):
        files = gradle.skeleton(PACKAGE, 'App')
        text = '\n'.join(files.values())
        for pin in (gradle_stack.AGP, gradle_stack.KOTLIN, gradle_stack.COMPOSE_BOM,
                    gradle_stack.LIBRARIES['androidx.activity:activity-compose'], f'compileSdk = {gradle_stack.COMPILE_SDK}',
                    f'minSdk = {gradle_stack.MIN_SDK}', f'targetSdk = {gradle_stack.TARGET_SDK}',
                    f'namespace = "{PACKAGE}"', f'applicationId = "{PACKAGE}"', 'compose = true',
                    'FAIL_ON_PROJECT_REPOS', 'android.useAndroidX=true'):
            self.assertIn(pin, text)
        self.assertNotIn('kotlin-android', text)  # AGP 9 compiles Kotlin itself
        self.assertNotIn('signingConfig', text)
        self.assertIn('app/src/main/java/com/example/app/MainActivity.kt', files)

    def test_manifest_and_label(self):
        label = 'Tom\'s "best" <app> & @home'
        files = gradle.skeleton(PACKAGE, label)
        manifest = ET.fromstring(files['app/src/main/AndroidManifest.xml'].encode())
        self.assertIsNone(manifest.get('package'))  # the namespace is in build.gradle.kts, the id is the builder's
        activity = manifest.find('application/activity')
        self.assertEqual(activity.get(template.ANDROID + 'name'), '.MainActivity')
        self.assertEqual(manifest.find('application').get(template.ANDROID + 'theme'),
                         '@android:style/Theme.Material.Light.NoActionBar')
        strings = ET.fromstring(files['app/src/main/res/values/strings.xml'].encode())
        self.assertEqual(strings.find('string').text, template.android_string(label).replace('&amp;', '&')
                         .replace('&lt;', '<').replace('&gt;', '>'))

    def test_kotlin_keywords_in_the_package_are_escaped(self):
        files = gradle.skeleton('com.example.fun.in', 'App')
        self.assertTrue(files['app/src/main/java/com/example/fun/in/MainActivity.kt']
                        .startswith('package com.example.`fun`.`in`\n'))

    def test_write_skeleton_writes_a_checked_tree(self):
        gradle.write_skeleton(self.root / 'p', PACKAGE, 'App')
        written = {p.relative_to(self.root / 'p').as_posix() for p in (self.root / 'p').rglob('*') if p.is_file()}
        self.assertEqual(written, set(gradle.skeleton(PACKAGE, 'App')))
        gradle.check(self.root / 'p')

    def test_init_script_sets_identity_from_the_environment(self):
        script = gradle.init_script()
        for name in ('ROSE_APPLICATION_ID', 'ROSE_VERSION_CODE', 'ROSE_VERSION_NAME', 'withPlugin("com.android.application")',
                     'onVariants', 'getApplicationId', 'getVersionCode', 'getVersionName'):
            self.assertIn(name, script)


KOTLIN_FAILURE = '''\
> Task :app:compileReleaseKotlin FAILED
e: file://<project>/app/src/main/java/com/example/app/MainActivity.kt:4:12 Syntax error: Expecting ')'.
w: file://<project>/app/src/main/java/com/example/app/Other.kt:1:1 Something deprecated.

FAILURE: Build failed with an exception.

* What went wrong:
Execution failed for task ':app:compileReleaseKotlin'.
> A failure occurred while executing org.jetbrains.kotlin.compilerRunner.btapi.BuildToolsApiCompilationWork
   > Kotlin compiler: SYNTAX

* Try:
> Run with --stacktrace option to get the stack trace.
> Get more help at https://help.gradle.org.

* Exception is:
org.gradle.api.tasks.TaskExecutionException: Execution failed
\tat org.gradle.api.internal.tasks.execution.ExecuteActionsTaskExecuter.lambda$executeIfValid$1(ExecuteActionsTaskExecuter.java:135)
Caused by: java.lang.RuntimeException: x
\t... 12 more

BUILD FAILED in 31s
12 actionable tasks: 12 executed
'''


class DiagnosticsTest(unittest.TestCase):
    def test_kotlin_errors_and_what_went_wrong_without_noise(self):
        failed = gradle.classify(KOTLIN_FAILURE, 1)
        self.assertEqual(failed.category, 'compile')
        self.assertIn('Kotlin', failed.summary)
        self.assertIn('MainActivity.kt:4:12 Syntax error', failed.log)
        self.assertIn('> Kotlin compiler: SYNTAX', failed.log)
        for noise in ('* Try', 'stacktrace', 'BUILD FAILED', '\tat ', 'Caused by', 'Exception is', 'w: ',
                      '> Task', 'actionable'):
            self.assertNotIn(noise, failed.log)

    def test_dependencies_the_cache_lacks(self):
        cases = {
            'androidx.core:core-ktx:0.0.1': '* What went wrong:\nExecution failed for task \':app:x\'.\n> Could not '
            'resolve all files for configuration \':app:releaseRuntimeClasspath\'.\n   > Could not resolve '
            'androidx.core:core-ktx:0.0.1.\n      > No cached version of androidx.core:core-ktx:0.0.1 available for '
            'offline mode.\n',
            'com.example.plugin:1.0': '* What went wrong:\nPlugin [id: \'com.example.plugin\', version: \'1.0\'] was '
            'not found in any of the following sources:\n',
            'org.example:lib:2.0': '* What went wrong:\n> Could not find org.example:lib:2.0.\n',
        }
        for what, log in cases.items():
            failed = gradle.classify(log, 1)
            self.assertEqual(failed.category, 'dependency', what)
            self.assertIn(what, failed.summary)
            self.assertIn(gradle_stack.GRADLE, failed.summary)  # names what the builder does provide
            self.assertTrue(failed.log)
        self.assertEqual(gradle.classify('* What went wrong:\nCould not find method implementation() for '
                                         'arguments [x]\n', 1).category, 'compile')

    def test_sdk_and_jdk_the_stack_lacks_are_the_projects_to_fix(self):
        sdk = gradle.classify("* What went wrong:\n> Failed to find target with hash string 'android-36' in: <sdk>\n", 1)
        self.assertEqual(sdk.category, 'compile')
        self.assertIn(f'compileSdk {gradle_stack.COMPILE_SDK}', sdk.summary)
        jdk = gradle.classify('* What went wrong:\n> No matching toolchains found for requested specification: '
                              '{languageVersion=17}\n', 1)
        self.assertEqual(jdk.category, 'compile')
        self.assertIn(f'JDK {gradle_stack.JDK_FEATURE}', jdk.summary)

    def test_other_compilers(self):
        javac = gradle.classify('<project>/app/src/main/java/A.java:3: error: cannot find symbol\n', 1)
        self.assertIn('Java', javac.summary)
        aapt = gradle.classify('ERROR: <project>/app/src/main/res/values/strings.xml:2: AAPT: error: bad.\n'
                               '* What went wrong:\nExecution failed for task \':app:mergeReleaseResources\'.\n', 1)
        self.assertIn('resources', aapt.summary)
        self.assertIn('strings.xml:2', aapt.log)
        merger = gradle.classify('* What went wrong:\nExecution failed for task.\n> Manifest merger failed : x\n', 1)
        self.assertIn('manifest', merger.summary)
        for failed in (javac, aapt, merger):
            self.assertEqual(failed.category, 'compile')

    def test_a_build_that_says_nothing_is_the_toolchains(self):
        quiet = gradle.classify('Starting a Gradle Daemon\n', -9)
        self.assertEqual((quiet.category, quiet.retryable), ('toolchain', True))
        self.assertIn('killed', quiet.summary)
        self.assertIn('Starting', quiet.log)  # never an empty log

    def test_log_is_bounded_and_deduplicated(self):
        log = ''.join(f'e: file://<project>/app/A.kt:{n}:1 Unresolved reference \'x{n}\'.\n' for n in range(2000))
        text = gradle.diagnostics(log + log)
        self.assertLessEqual(len(text), gradle.MAX_LOG)
        self.assertTrue(text.endswith('(truncated)'))
        self.assertEqual(gradle.diagnostics('e: one\ne: one\n'), 'e: one')


BADGING = ("package: name='com.example.app' versionCode='3' versionName='3' platformBuildVersionName='17'\n"
           "sdkVersion:'{min}'\ntargetSdkVersion:'{target}'\napplication-label:'App'\n")
TREE = ('N: android=http://schemas.android.com/apk/res/android\n  E: manifest (line=2)\n'
        '    A: package="com.example.app" (Raw: "com.example.app")\n    E: application (line=5)\n{extra}')


class VerifyApkTest(Temp):
    def verify(self, min_sdk='26', target='37', extra='', bt=None, version_code=3):
        calls = []

        def run(args, *rest, **kwargs):
            calls.append(args)
            if args[2] == 'badging':
                text = BADGING.format(min=min_sdk, target=target)
                return '\n'.join(x for x in text.splitlines() if not x.endswith("''")) + '\n'
            return TREE.format(extra=extra)
        tools = toolchain.Tools(self.root)
        with mock.patch.object(toolchain, 'run', side_effect=run):
            badging = toolchain.verify_apk(self.root / 'a.apk', PACKAGE, version_code, '3', tools, self.root,
                                           self.root, lambda: False, bt=bt)
        return badging, calls

    def refused(self, **kwargs) -> toolchain.StepFailed:
        with self.assertRaises(toolchain.StepFailed) as caught:
            self.verify(**kwargs)
        return caught.exception

    def test_legacy_and_gradle_levels_pass(self):
        for levels in (('26', '35'), ('26', '37'), ('29', '24'), ('21', '34')):
            badging, calls = self.verify(*levels)
            self.assertIn("versionCode='3'", badging)
        self.assertEqual(Path(calls[0][0]), toolchain.Tools(self.root).bt / 'aapt')

    def test_build_tools_are_chosen(self):
        _, calls = self.verify(bt=self.root / 'bt')
        self.assertEqual({Path(c[0]) for c in calls}, {self.root / 'bt/aapt'})

    def test_identity_mismatch_is_internal(self):
        self.assertEqual(self.refused(version_code=4).category, 'internal')

    def test_test_only_and_debuggable_are_refused(self):
        failed = self.refused(extra='      A: android:testOnly(0x01010272)=(type 0x12)0xffffffff\n')
        self.assertEqual((failed.category, failed.log), ('compile', ''))
        self.assertIn('test-only', failed.summary)
        self.assertEqual(self.refused(extra='      A: android:debuggable(0x0101000f)=(type 0x12)0xffffffff\n')
                         .category, 'compile')

    def test_sdk_levels_must_install_on_every_rose_phone_and_current_android(self):
        self.assertIn(f'at most {gradle_stack.MAX_MIN_SDK}', self.refused(min_sdk='30').summary)
        self.assertIn(f'at least {gradle_stack.MIN_TARGET_SDK}', self.refused(target='23').summary)
        self.assertIn('minSdk', self.refused(min_sdk='VanillaIceCream').summary)
        self.assertIn('targetSdk', self.refused(target='Baklava').summary)
        self.verify(min_sdk='26', target='')  # no targetSdk: it is the minSdk
        self.assertIn('targetSdk', self.refused(min_sdk='', target='').summary)  # no levels at all: minSdk and target 1

    def test_sdk_levels(self):
        self.assertEqual(toolchain.sdk_levels("sdkVersion:'26'\ntargetSdkVersion:'37'\n"), (26, 37))
        self.assertEqual(toolchain.sdk_levels("sdkVersion:'28'\n"), (28, 28))
        self.assertEqual(toolchain.sdk_levels(''), (1, 1))
        self.assertEqual(toolchain.sdk_levels("sdkVersion:'S'\ntargetSdkVersion:'037'\n"), (None, None))


def apk_bytes(entries=(('classes.dex', b'dex\n035\0'), ('AndroidManifest.xml', b'x'))) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as z:
        for name, data in entries:
            z.writestr(name, data)
    return buffer.getvalue()


def with_signing_block(data: bytes) -> bytes:
    """``data`` with an (empty) APK Signing Block before its central directory, as apksigner places one."""
    end = data.rfind(b'PK\x05\x06')
    central = int.from_bytes(data[end + 16:end + 20], 'little')
    block = (24).to_bytes(8, 'little') + (24).to_bytes(8, 'little') + gradle.APK_SIG_BLOCK
    moved = data[end:end + 16] + (central + len(block)).to_bytes(4, 'little') + data[end + 20:]
    return data[:central] + block + data[central:end] + moved


class OutputTest(Temp):
    def setUp(self):
        super().setUp()
        self.box = self.root / 'box'
        self.release = self.box / 'project' / gradle.OUTPUT
        self.release.mkdir(parents=True)
        self.target = self.root / 'copy.apk'

    def metadata(self, *elements, folder=None):
        (folder or self.release).joinpath('output-metadata.json').write_text(json.dumps({
            'version': 3, 'artifactType': {'type': 'APK', 'kind': 'Directory'}, 'elements': list(elements)}))

    @staticmethod
    def element(name='app-release-unsigned.apk', **extra):
        return {'type': 'SINGLE', 'filters': [], 'attributes': [], 'outputFile': name, **extra}

    def take(self) -> toolchain.StepFailed:
        with self.assertRaises(toolchain.StepFailed) as caught:
            gradle._take_output(self.box, self.target, 'BUILD SUCCESSFUL')
        self.assertEqual(caught.exception.category, 'compile')
        self.assertFalse(self.target.exists())
        return caught.exception

    def test_one_apk_is_copied(self):
        (self.release / 'app-release-unsigned.apk').write_bytes(apk_bytes())
        self.metadata(self.element())
        self.assertEqual(gradle._take_output(self.box, self.target, ''), 'app-release-unsigned.apk')
        self.assertEqual(self.target.read_bytes(), apk_bytes())
        gradle._refuse_signed(self.target, 'app-release-unsigned.apk')

    def test_splits_and_odd_entries_are_refused(self):
        (self.release / 'a.apk').write_bytes(apk_bytes())
        self.metadata(self.element('app-x86_64-release.apk', type='ONE_OF_MANY',
                                   filters=[{'filterType': 'ABI', 'value': 'x86_64'}]),
                      self.element('app-arm64-v8a-release.apk', type='ONE_OF_MANY'))
        failed = self.take()
        self.assertIn('one APK', failed.summary)
        self.assertIn('lists 2 APK(s)', failed.log)
        self.assertIn('app-arm64-v8a-release.apk', failed.log)
        for element in (self.element('a.apk', filters=[{'filterType': 'DENSITY', 'value': 'xxhdpi'}]),
                        self.element('a.apk', type='UNIVERSAL'), self.element('../a.apk'), self.element('x/a.apk'),
                        self.element('.apk'), self.element(None), self.element('a.aab')):
            self.metadata(element)
            self.assertIn('one APK', self.take().summary, element)
        self.metadata()
        self.take()
        (self.release / 'output-metadata.json').write_text('[1]')
        self.take()

    def test_no_link_the_build_planted_is_followed(self):
        outside = self.root / 'outside'
        outside.mkdir()
        (outside / 'app-release-unsigned.apk').write_bytes(apk_bytes())
        self.metadata(self.element(), folder=outside)
        os.symlink(outside / 'app-release-unsigned.apk', self.release / 'app-release-unsigned.apk')
        self.metadata(self.element())
        self.assertIn('not a plain file', self.take().summary)
        shutil.rmtree(self.release)
        os.symlink(outside, self.release)  # the whole release folder
        self.assertIn('did not make a release APK', self.take().summary)
        self.release.unlink()
        shutil.rmtree(self.box / 'project')
        os.symlink(outside, self.box / 'project')  # or the project itself
        self.take()

    def test_special_files_do_not_block(self):
        os.mkfifo(self.release / 'output-metadata.json')
        self.take()
        (self.release / 'output-metadata.json').unlink()
        os.mkfifo(self.release / 'app-release-unsigned.apk')
        self.metadata(self.element())
        self.assertIn('not a plain file', self.take().summary)

    def test_signed_apks_are_refused(self):
        signed = self.root / 'signed.apk'
        signed.write_bytes(apk_bytes((('classes.dex', b'x'), ('META-INF/CERT.RSA', b'x'), ('META-INF/CERT.SF', b'x'))))
        with self.assertRaises(toolchain.StepFailed) as caught:
            gradle._refuse_signed(signed, 'app-release.apk')
        self.assertIn('signs it', caught.exception.summary)
        self.assertIn('META-INF/CERT.RSA', caught.exception.log)
        signed.write_bytes(with_signing_block(apk_bytes()))
        with zipfile.ZipFile(signed) as z:  # still a valid archive
            self.assertEqual(z.read('classes.dex'), b'dex\n035\0')
        with self.assertRaises(toolchain.StepFailed) as caught:
            gradle._refuse_signed(signed, 'app-release.apk')
        self.assertIn('APK Signing Block', caught.exception.log)
        signed.write_bytes(b'not a zip')
        with self.assertRaises(toolchain.StepFailed) as caught:
            gradle._refuse_signed(signed, 'app-release.apk')
        self.assertIn('valid APK', caught.exception.summary)


class FakeIsolation:
    """Records what build() asks for and runs ``script`` (sh) in Gradle's place, unconfined: unit tests only."""

    def __init__(self, script='exit 0', raises=None, failed=False, keep=None):
        self.script, self.raises, self.own, self.calls, self.keep = script, raises, failed, [], keep

    def command(self, argv, env, *, writable, readonly):
        self.calls.append((argv, env, writable, readonly))
        if self.keep is not None:  # what Gradle is given, as it starts: build() removes the box afterwards
            shutil.copytree(writable, self.keep, symlinks=True)
        if self.raises is not None:
            raise self.raises
        return ['/bin/sh', '-c', self.script], env

    def failed(self, returncode, output):
        if isinstance(self.own, Exception):
            raise self.own
        return self.own


class BuildContractTest(Temp):
    """build() up to and around Gradle, with a fake stack: no Gradle runs."""

    def setUp(self):
        super().setUp()
        stack = self.root / 'stack'
        for name in ('jdk/bin/java', 'gradle/bin/gradle', f'sdk/platforms/{gradle_stack.PLATFORM}/android.jar',
                     f'sdk/platforms/{gradle_stack.PLATFORM}/package.xml',
                     *(f'sdk/build-tools/{gradle_stack.BUILD_TOOLS}/{n}' for n in ('aapt', 'aapt2', 'zipalign',
                                                                                  'package.xml'))):
            (stack / name).parent.mkdir(parents=True, exist_ok=True)
            (stack / name).write_text('')
        (stack / 'cache/modules-2').mkdir(parents=True)
        self.stack = stack
        self.tree = self.root / 'src'
        gradle.write_skeleton(self.tree, PACKAGE, 'App')
        (self.tree / 'local.properties').write_text('sdk.dir=/somewhere/else\n')
        self.work = self.root / 'work'
        self.work.mkdir()
        self.tools = toolchain.Tools(self.root / 'no-legacy-sdk')

    def gradle_tools(self, isolation, **kwargs):
        s = self.stack
        return gradle.GradleTools(s / 'jdk', s / 'gradle', s / 'sdk', s / 'cache', isolation, **kwargs)

    def build(self, isolation, **kwargs) -> toolchain.StepFailed:
        with self.assertRaises(toolchain.StepFailed) as caught:
            gradle.build(self.tree, self.work, PACKAGE, 7, '7', self.gradle_tools(isolation), self.tools, **kwargs)
        return caught.exception

    def test_available_names_the_missing_piece(self):
        self.assertIsNone(self.gradle_tools(FakeIsolation()).available())
        self.assertIn('isolation', self.gradle_tools(None).available())
        for name, what in (('jdk/bin/java', 'JDK'), ('gradle/bin/gradle', 'Gradle'),
                           (f'sdk/build-tools/{gradle_stack.BUILD_TOOLS}/zipalign', 'build tools'),
                           (f'sdk/platforms/{gradle_stack.PLATFORM}/package.xml', 'SDK platform')):
            (self.stack / name).rename(self.root / 'aside')
            self.assertIn(what, self.gradle_tools(FakeIsolation()).available())
            (self.root / 'aside').rename(self.stack / name)
        shutil.rmtree(self.stack / 'cache/modules-2')
        self.assertIn('cache', self.gradle_tools(FakeIsolation()).available())
        failed = self.build(FakeIsolation())
        self.assertEqual((failed.category, failed.retryable), ('toolchain', True))

    def test_source_refusals_stop_before_anything_runs(self):
        isolation = FakeIsolation()
        (self.tree / 'app/release.jks').write_text('x')
        self.assertEqual(self.build(isolation).category, 'compile')
        (self.tree / 'app/release.jks').unlink()
        (self.tree / 'app/x.so').write_text('x')
        self.assertEqual(self.build(isolation).category, 'dependency')
        self.assertEqual(isolation.calls, [])

    def test_what_gradle_is_given(self):
        seen = self.root / 'seen'
        isolation = FakeIsolation('exit 0', keep=seen)
        with mock.patch.dict(os.environ, {'ROSE_TEST_SECRET': 'inherited-value'}):
            failed = self.build(isolation)
        self.assertIn('did not make a release APK', failed.summary)
        [(argv, env, writable, readonly)] = isolation.calls
        box, s = self.work / 'sandbox', self.stack
        self.assertFalse(os.path.lexists(box))  # the job's Gradle home and outputs are gone once Gradle is
        self.assertEqual(writable, box)
        self.assertEqual(readonly, (s / 'jdk', s / 'gradle', s / 'sdk', s / 'cache'))
        self.assertEqual(argv, [str(s / 'gradle/bin/gradle'), '--offline', '--no-daemon', '--console=plain',
                                '--init-script', str(box / gradle.INIT), '-p', str(box / 'project'), ':app:assembleRelease'])
        self.assertEqual(set(env), {'PATH', 'HOME', 'TMPDIR', 'LANG', 'JAVA_HOME', 'GRADLE_USER_HOME',
                                    'GRADLE_RO_DEP_CACHE', 'ANDROID_HOME', 'ANDROID_USER_HOME', 'GRADLE_OPTS',
                                    'ROSE_APPLICATION_ID', 'ROSE_VERSION_CODE', 'ROSE_VERSION_NAME'})
        self.assertNotIn('inherited-value', json.dumps(env))
        self.assertEqual((env['ROSE_APPLICATION_ID'], env['ROSE_VERSION_CODE'], env['ROSE_VERSION_NAME']),
                         (PACKAGE, '7', '7'))
        self.assertEqual(env['GRADLE_RO_DEP_CACHE'], str(s / 'cache'))
        self.assertEqual(env['PATH'], f'{s / "jdk"}/bin:/usr/bin:/bin')
        for key in ('HOME', 'TMPDIR', 'GRADLE_USER_HOME', 'ANDROID_USER_HOME'):
            self.assertTrue(Path(env[key]).is_relative_to(box), key)
        for folder in ('home', 'gradle-home', 'tmp', 'project'):
            self.assertEqual((seen / folder).stat().st_mode & 0o777, 0o700)
        properties = (seen / 'gradle-home/gradle.properties').read_text()
        for line in ('org.gradle.daemon=false', 'org.gradle.java.installations.auto-download=false',
                     'org.gradle.java.installations.auto-detect=false', f'org.gradle.java.home={s / "jdk"}',
                     f'org.gradle.java.installations.paths={s / "jdk"}', 'android.builder.sdkDownload=false',
                     'kotlin.compiler.execution.strategy=in-process'):
            self.assertIn(line + '\n', properties)
        self.assertEqual((seen / 'project/local.properties').read_text(), f'sdk.dir={s / "sdk"}\n')
        self.assertEqual((seen / gradle.INIT).read_text(), gradle.init_script())
        self.assertEqual((seen / 'project/app/build.gradle.kts').read_text(),
                         (self.tree / 'app/build.gradle.kts').read_text())
        self.assertEqual((self.tree / 'local.properties').read_text(), 'sdk.dir=/somewhere/else\n')  # source untouched
        # A second build in the same job starts from a fresh sandbox, whatever a first one left.
        box.mkdir()
        (box / 'stale').write_text('x')
        again = self.root / 'again'
        self.build(FakeIsolation('exit 0', keep=again))
        self.assertFalse((again / 'stale').exists())

    def test_a_failed_build_is_classified_with_its_extracted_log(self):
        script = (f'echo "e: file://{self.work}/sandbox/project/app/src/main/java/A.kt:1:1 Syntax error"; '
                  'echo "* Try:"; echo "> Run with --stacktrace"; echo "BUILD FAILED in 1s"; exit 1')
        failed = self.build(FakeIsolation(script))
        self.assertEqual(failed.category, 'compile')
        self.assertEqual(failed.log, 'e: file://<project>/app/src/main/java/A.kt:1:1 Syntax error')
        self.assertNotIn(str(self.root), failed.log)

    def test_isolation_failures_are_the_hosts(self):
        refusal = BuildFailed('toolchain', 'build_isolation_unavailable', 'The build was not run: no bubblewrap.')
        self.assertIs(self.build(FakeIsolation(raises=refusal)), refusal)
        failed = self.build(FakeIsolation(raises=ValueError('x')))
        self.assertEqual((failed.category, failed.reason), ('toolchain', 'build_isolation_unavailable'))
        for own in (True, RuntimeError('cannot tell')):
            failed = self.build(FakeIsolation('echo bwrap: no; exit 1', failed=own))
            self.assertEqual((failed.category, failed.reason), ('toolchain', 'build_isolation_failed'))

        class Missing(FakeIsolation):
            def command(self, argv, env, *, writable, readonly):
                return [str(Path(writable) / 'no-such-program')], env
        failed = self.build(Missing())
        self.assertEqual((failed.category, failed.reason), ('toolchain', 'build_isolation_unavailable'))

    def test_cancel_and_timeout_pass_through(self):
        self.assertEqual(self.build(FakeIsolation('sleep 5'), cancelled=lambda: True).category, 'cancelled')
        with self.assertRaises(toolchain.StepFailed) as caught:
            gradle.build(self.tree, self.work, PACKAGE, 7, '7', self.gradle_tools(FakeIsolation('sleep 5'),
                                                                                  timeout_s=0.5), self.tools)
        self.assertEqual(caught.exception.category, 'timeout')


# ---------------------------------------------------------------------------------------------------------------
# Integration: real Gradle builds in BubblewrapIsolation.

STACK = Path(os.environ.get('ROSE_GRADLE_STACK', '/nonexistent'))
LEGACY_SDK = Path(os.environ.get('ANDROID_SDK_ROOT', '/nonexistent'))
JAVA_HOME = Path(os.environ['JAVA_HOME']) if os.environ.get('JAVA_HOME') else None
REQUIRE = os.environ.get('ROSE_REQUIRE_GRADLE_BUILD') == '1'


def _why_not() -> str | None:
    if not all((STACK / n).is_dir() for n in ('jdk', 'gradle', 'sdk', 'cache')):
        return 'needs ROSE_GRADLE_STACK with jdk/ gradle/ sdk/ cache/'
    if not (LEGACY_SDK / 'build-tools/android-15/apksigner').exists() or not (shutil.which('keytool') or JAVA_HOME):
        return 'needs the legacy Android SDK (ANDROID_SDK_ROOT) and a JDK, to sign'
    if platform.system() != 'Linux' or platform.machine() != 'x86_64' or os.geteuid() == 0:
        return 'needs unprivileged Linux x86-64 (bubblewrap refuses root)'
    if not Path('/usr/bin/bwrap').is_file():
        return 'needs /usr/bin/bwrap'
    return None


# What a build script can try: read a file in the builder's root (outside every mount) and reach the network.
PROBE = '''
val probe = StringBuilder()
probe.append("read=" + runCatching { java.io.File("@CANARY@").readText() }.fold({ "ok:" + it }, { "denied" }) + "\\n")
probe.append("net=" + runCatching {
    java.net.Socket().use { it.connect(java.net.InetSocketAddress("1.1.1.1", 443), 3000) }
}.fold({ "connected" }, { "denied" }) + "\\n")
println("ROSE-PROBE " + probe.toString().replace("\\n", " "))
java.io.File(rootDir, "probe.txt").writeText(probe.toString())
'''


def plain_project(tree: Path, app_extra: str = '', kotlin_extra: str = '') -> None:
    """The smallest Kotlin app (framework Activity, no libraries). The compose plugin is on the root classpath
    (apply false) because AGP 9.5.1 alone asks for kotlin-gradle-plugin 2.2.10, which the stack's cache lacks; with
    it, Gradle resolves the stack's Kotlin."""
    files = {
        'settings.gradle.kts': gradle.skeleton(PACKAGE, 'x')['settings.gradle.kts'],
        'build.gradle.kts': gradle.skeleton(PACKAGE, 'x')['build.gradle.kts'],
        'gradle.properties': 'android.useAndroidX=true\n',
        'app/build.gradle.kts': (
            'plugins { id("com.android.application") }\n'
            f'android {{\n    namespace = "{PACKAGE}"\n    compileSdk = {gradle_stack.COMPILE_SDK}\n'
            f'    defaultConfig {{ applicationId = "{PACKAGE}"; minSdk = 26; targetSdk = {gradle_stack.TARGET_SDK}; '
            'versionCode = 1; versionName = "1" }\n}\n' + app_extra),
        'app/src/main/AndroidManifest.xml': (
            '<manifest xmlns:android="http://schemas.android.com/apk/res/android"><application android:label="P">'
            '<activity android:name=".MainActivity" android:exported="true"><intent-filter>'
            '<action android:name="android.intent.action.MAIN"/><category android:name="android.intent.category.LAUNCHER"/>'
            '</intent-filter></activity></application></manifest>\n'),
        'app/src/main/java/com/example/app/MainActivity.kt': (
            'package com.example.app\n\nclass MainActivity : android.app.Activity()\n' + kotlin_extra),
    }
    for name, text in files.items():
        (tree / name).parent.mkdir(parents=True, exist_ok=True)
        (tree / name).write_text(text)


class GradleBuildTest(unittest.TestCase):
    """Real builds, made once in setUpClass (a few at a time) and inspected by the tests."""
    identity = ('app.rose.gen.pgradletest', 7, '7')

    @classmethod
    def setUpClass(cls):
        why = _why_not()
        if why:
            if REQUIRE:
                raise AssertionError(f'ROSE_REQUIRE_GRADLE_BUILD=1 but the Gradle build {why}')
            raise unittest.SkipTest(why)
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(os.path.realpath(cls.temp.name))
        cls.tools = toolchain.Tools(LEGACY_SDK, JAVA_HOME, timeout_s=600, key_creation=True)
        stack = Path(os.path.realpath(STACK))
        cls.gradle = gradle.GradleTools(stack / 'jdk', stack / 'gradle', stack / 'sdk', stack / 'cache',
                                        BubblewrapIsolation(), timeout_s=1200)
        cls.canary_text = 'rose-canary-' + secrets.token_hex(8)
        canary = cls.root / 'builder/keys/p1/canary.txt'  # where the builder's own secrets live
        canary.parent.mkdir(parents=True)
        canary.write_text(cls.canary_text)
        cls.canary = canary

        def skeleton(tree):
            gradle.write_skeleton(tree, cls.identity[0], 'Gradle test')
            with open(tree / 'settings.gradle.kts', 'a') as f:
                f.write(PROBE.replace('@CANARY@', str(canary)))
        projects = {
            'skeleton': skeleton,
            'override': lambda t: plain_project(t, 'androidComponents { onVariants { v -> '
                                                   'v.applicationId.set("com.example.other"); '
                                                   'v.outputs.forEach { it.versionCode.set(1) } } }\n'),
            'signed': lambda t: plain_project(t, 'android { buildTypes { getByName("release") { '
                                                 'signingConfig = signingConfigs.getByName("debug") } } }\n'),
            'splits': lambda t: plain_project(t, 'android { splits { abi { isEnable = true; reset(); '
                                                 'include("x86_64", "arm64-v8a"); isUniversalApk = false } } }\n'),
            'syntax': lambda t: plain_project(t, kotlin_extra='fun broken( {\n'),
            'missing': lambda t: plain_project(t, 'dependencies { implementation("androidx.core:core-ktx:0.0.1") }\n'),
        }
        cls.results = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
            futures = {pool.submit(cls._build, name, make): name for name, make in projects.items()}
            for future in concurrent.futures.as_completed(futures):
                cls.results[futures[future]] = future.result()

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    @classmethod
    def _build(cls, name, make):
        tree, work = cls.root / name / 'src', cls.root / name / 'work'
        tree.mkdir(parents=True)
        work.mkdir()
        make(tree)
        try:
            return work, gradle.build(tree, work, *cls.identity, cls.gradle, cls.tools)
        except toolchain.StepFailed as failed:
            return work, failed

    def result(self, name):
        work, result = self.results[name]
        return work, result

    def failure(self, name) -> toolchain.StepFailed:
        _, result = self.result(name)
        self.assertIsInstance(result, toolchain.StepFailed, result)
        self.assertNotIn(self.canary_text, result.log)
        return result

    def aapt(self, *args) -> str:
        return subprocess.run([str(self.gradle.bt / 'aapt'), *map(str, args)], capture_output=True, text=True,
                              check=True).stdout

    def test_skeleton_builds_with_the_builders_identity_and_signs(self):
        work, result = self.result('skeleton')
        self.assertNotIsInstance(result, toolchain.StepFailed, getattr(result, 'log', '')[-4000:])
        apk, log = result
        self.assertEqual(apk, Path(os.path.realpath(work)) / 'unsigned.apk')
        badging = self.aapt('dump', 'badging', apk)
        self.assertRegex(badging, r"package: name='app\.rose\.gen\.pgradletest' versionCode='7' versionName='7'")
        self.assertEqual(toolchain.sdk_levels(badging), (gradle_stack.MIN_SDK, gradle_stack.TARGET_SDK))
        self.assertIn("application-label:'Gradle test'", badging)
        with zipfile.ZipFile(apk) as z:
            names = z.namelist()
            dex = b''.join(z.read(n) for n in names if re.fullmatch(r'classes\d*\.dex', n))
        self.assertIn(b'Landroidx/compose/material3/', dex)  # a Compose app
        self.assertIn(b'Lapp/rose/gen/pgradletest/MainActivity;', dex)
        self.assertFalse([n for n in names if n.startswith('META-INF/') and n.endswith(('.SF', '.RSA', '.EC'))])
        subprocess.run([str(self.gradle.bt / 'zipalign'), '-c', '-P', '16', '4', str(apk)], check=True,
                       capture_output=True)
        report = json.loads((work / 'api-report.json').read_text())
        self.assertEqual((report['advisory'], report['minSdk']), (True, gradle_stack.MIN_SDK))
        self.assertIn('API level report', log)
        # The project's key, made and used outside Gradle; a second signing holds to the first one's certificate.
        # scripts/sign-apk.py takes key files only from outside the repository, where a builder root is; TMPDIR may
        # be inside it (an out/ folder), so the keys get a folder of their own.
        outside = None if not self.root.is_relative_to(toolchain.REPO) else '/var/tmp'
        keys_root = tempfile.TemporaryDirectory(dir=outside)
        self.addCleanup(keys_root.cleanup)
        keys = Path(keys_root.name) / 'keys/pgradletest'
        toolchain.ensure_key(keys, self.identity[0], self.tools)
        first = toolchain.sign(apk, work / 'signed-1.apk', keys, None, self.tools)
        second = toolchain.sign(apk, work / 'signed-2.apk', keys, first['signerSha256'], self.tools)
        self.assertEqual(first['signerSha256'], second['signerSha256'])
        self.assertIn("versionCode='7'", self.aapt('dump', 'badging', work / 'signed-1.apk'))

    def test_build_scripts_reach_neither_the_builders_files_nor_the_network(self):
        work, result = self.result('skeleton')
        self.assertNotIsInstance(result, toolchain.StepFailed, getattr(result, 'log', '')[-4000:])
        _, log = result
        self.assertIn('ROSE-PROBE read=denied net=denied', log)  # the probe ran, and got neither
        self.assertFalse(os.path.lexists(work / 'sandbox'))  # nothing the build wrote is left behind
        self.assertNotIn(self.canary_text, log)
        self.assertNotIn(str(self.root), log)  # the builder's paths are redacted

    def test_identity_set_by_the_build_files_is_refused_on_the_apk(self):
        failed = self.failure('override')
        self.assertEqual(failed.category, 'compile')
        self.assertIn('versionCode', failed.summary)

    def test_an_apk_the_build_signs_is_refused(self):
        failed = self.failure('signed')
        self.assertEqual(failed.category, 'compile')
        self.assertIn('signs it', failed.summary)
        self.assertTrue(failed.log)

    def test_splits_are_refused(self):
        failed = self.failure('splits')
        self.assertIn('one APK', failed.summary)
        self.assertIn('lists 2 APK(s)', failed.log)

    def test_a_kotlin_error_is_the_writers_to_repair(self):
        failed = self.failure('syntax')
        self.assertEqual(failed.category, 'compile')
        self.assertIn('Kotlin', failed.summary)
        self.assertRegex(failed.log, r'(?m)^e: file://<project>/app/src/main/java/com/example/app/MainActivity\.kt:\d+')
        self.assertNotIn(str(self.root), failed.log)
        self.assertLessEqual(len(failed.log), gradle.MAX_LOG)
        self.assertNotIn('* Try:', failed.log)

    def test_a_library_the_stack_lacks_is_a_dependency(self):
        failed = self.failure('missing')
        self.assertEqual(failed.category, 'dependency')
        self.assertIn('androidx.core:core-ktx:0.0.1', failed.summary)
        self.assertIn('No cached version', failed.log)


if __name__ == '__main__':
    unittest.main()
