"""Build an ordinary Gradle Android project (Kotlin, Jetpack Compose, AndroidX) into an unsigned APK for the builder's
own identity (docs/APP-BUILDER.md §2.4).

A project with settings.gradle(.kts) at its top and the application module in app/ builds with the one pinned stack
(rose.app_builder.gradle_stack): the host's JDK, Gradle distribution, SDK and a read-only dependency cache, offline.
Gradle runs the project's own code (settings and build scripts, plugins), so it runs only inside the host's
``Isolation`` (rose.app_builder.isolation), never without one: it can write only the job's sandbox folder (the
project copy, its own HOME, GRADLE_USER_HOME and TMPDIR), read only the four stack trees and the system's own
programs, and reach no network. The builder's root, its keys, ledger, artifacts and every other job and project stay
outside. Nothing is inherited from the builder's environment; the builder's gradle.properties in GRADLE_USER_HOME
outranks the project's (no daemon, no toolchain download or detection, the stack's JDK).

The identity is the builder's, not the project's: an init script sets every variant's applicationId, versionCode and
versionName from the environment, and whatever the build scripts did, the built APK is held to it on the binary
(rose.app_builder.toolchain.verify_apk), with the stack's SDK level bounds. The output must be one unsigned APK; it
is aligned for 16 KB pages here and signed only afterwards, outside Gradle, with the project's own key
(toolchain.sign). No key, password or signing config ever enters the sandbox. Files the build wrote are read back
without following a link the build could have planted.

A failed build's log is the extracted diagnostic text (what Gradle says went wrong, and the compilers' own error
lines), bounded, so the builder's repair loop can send the writer exactly its own errors.
"""
from __future__ import annotations

import dataclasses
import json
import os
import re
import shutil
import stat
import zipfile
from dataclasses import dataclass
from pathlib import Path

from . import gradle_stack, template, toolchain
from .isolation import BuildFailed
from .toolchain import StepFailed

SETTINGS = ('settings.gradle.kts', 'settings.gradle')
BUILD_FILES = ('build.gradle.kts', 'build.gradle')
WRAPPER_JAR = 'gradle/wrapper/gradle-wrapper.jar'  # Android Studio commits it; tolerated, never run
KEY_SUFFIXES = ('.jks', '.keystore', '.p12', '.pk8', '.pfx', '.pem', '.der', '.key')
LIBRARY_SUFFIXES = ('.jar', '.aar', '.so', '.klib')  # a library comes from the stack's cache, never the source
BINARY_SUFFIXES = ('.dex', '.apk', '.aab', '.class')
NATIVE_SUFFIXES = ('.c', '.cc', '.cpp', '.cxx', '.h', '.hpp', '.mk')
NATIVE_NAMES = ('cmakelists.txt', 'android.mk')
BUILD_STATE = ('build', '.gradle', '.kotlin', '.idea', '.cxx')  # at the top or directly in a module
OUTPUT = 'app/build/outputs/apk/release'  # gradle_stack.APP_MODULE's release APK folder
TASK = f':{gradle_stack.APP_MODULE}:assembleRelease'
INIT = 'rose-identity.init.gradle.kts'
MAX_LOG = 8000  # characters of extracted diagnostics in a failure's log
MAX_METADATA = 1 << 20
APK_SIG_BLOCK = b'APK Sig Block 42'
KOTLIN_KEYWORDS = {'as', 'break', 'class', 'continue', 'do', 'else', 'false', 'for', 'fun', 'if', 'in', 'interface',
                   'is', 'null', 'object', 'package', 'return', 'super', 'this', 'throw', 'true', 'try', 'typealias',
                   'typeof', 'val', 'var', 'when', 'while'}


@dataclass(frozen=True)
class GradleTools:
    """The host's pinned Gradle stack (rose.app_builder.gradle_prepare makes one) and its build isolation."""
    jdk: Path  # an unpacked JDK 21 tree (bin/java)
    gradle: Path  # the unpacked Gradle distribution's root (bin/gradle)
    sdk: Path  # an AGP-layout SDK: platforms/<PLATFORM>, build-tools/<BUILD_TOOLS>, a package.xml in each
    cache: Path  # the read-only dependency cache: holds modules-2/ (GRADLE_RO_DEP_CACHE)
    isolation: object  # a rose.app_builder.isolation.Isolation; mandatory, there is no unsandboxed build
    timeout_s: float = 1800.0

    @property
    def bt(self) -> Path:
        return Path(self.sdk) / 'build-tools' / gradle_stack.BUILD_TOOLS

    def available(self) -> str | None:
        """None when every piece is present, else what is missing (category toolchain)."""
        if self.isolation is None or not callable(getattr(self.isolation, 'command', None)):
            return 'The builder has no build isolation for Gradle projects, so it does not build them.'
        platform = Path(self.sdk) / 'platforms' / gradle_stack.PLATFORM
        for path, what in ((Path(self.jdk) / 'bin/java', 'JDK'), (Path(self.gradle) / 'bin/gradle', 'Gradle'),
                           (platform / 'android.jar', 'SDK platform'), (platform / 'package.xml', 'SDK platform'),
                           *((self.bt / n, 'build tools') for n in ('aapt', 'aapt2', 'zipalign', 'package.xml'))):
            if not path.is_file():
                return f'The builder\'s Gradle stack is missing its {what} ({path.name}).'
        if not (Path(self.cache) / 'modules-2').is_dir():
            return 'The builder\'s Gradle stack has no dependency cache.'
        return None


def is_gradle_project(tree: Path) -> bool:
    """Whether ``tree`` is an ordinary Gradle project: a regular settings.gradle.kts or settings.gradle at its top
    (never a link). The old flat layout (AndroidManifest.xml, src, res) has none and builds with toolchain.build."""
    return any(_plain(Path(tree) / name, stat.S_ISREG) for name in SETTINGS)


def _plain(path: Path, kind) -> bool:
    try:
        return kind(os.lstat(path).st_mode)
    except OSError:
        return False


def check(tree: Path, *, extra_top: frozenset = frozenset(), opaque_top: frozenset = frozenset(),
          builder_files: frozenset = frozenset()) -> None:
    """Refuse a Gradle project the builder will not build; raises template.TemplateRefused (``dependency`` where the
    stack lacks what it needs). The integrity rules are template.check's: the builder owns every entry, names are
    UTF-8, no links, hard links or special files, no file over template.MAX_FILE, no git names at any depth except
    ``.gitignore`` files. Then: settings.gradle(.kts) at the top and app/build.gradle(.kts); no key material,
    prebuilt binaries or libraries (gradle/wrapper/gradle-wrapper.jar is tolerated: the builder runs its own
    Gradle), native code (the stack has no NDK) or committed build state (build, .gradle, .kotlin, .idea, .cxx at the
    top or in a module, ``*.iml``). ``opaque_top`` folders (a backend's own source) and ``builder_files`` (paths the
    builder wrote) skip only those content rules. A Gradle project's top level is otherwise free, so ``extra_top``
    changes nothing; it is taken for template.check's signature. Identity, manifest and SDK rules are not here: they
    hold on the built APK (toolchain.verify_apk)."""
    tree = Path(tree)
    if os.lstat(tree).st_mode & 0o700 != 0o700:
        raise template.TemplateRefused('The app\'s source folder is not readable and writable by the builder.')
    modules = {Path('.')}
    for path in sorted(tree.rglob('*')):
        name = _integrity(tree, path)
        if path.name in BUILD_FILES and stat.S_ISREG(os.lstat(path).st_mode):
            modules.add(name.parent)
    for setting in SETTINGS:
        if os.path.lexists(tree / setting) and not _plain(tree / setting, stat.S_ISREG):
            raise template.TemplateRefused(f'{setting} must be a file.')
    if not is_gradle_project(tree):
        raise template.TemplateRefused('A Gradle project needs settings.gradle.kts (or settings.gradle) at its top.')
    if not any(_plain(tree / gradle_stack.APP_MODULE / n, stat.S_ISREG) for n in BUILD_FILES):
        raise template.TemplateRefused(f'The application module {gradle_stack.APP_MODULE}/ needs build.gradle.kts '
                                       '(or build.gradle).')
    for path in sorted(tree.rglob('*')):
        name = path.relative_to(tree)
        if name.parts[0] in opaque_top or name.as_posix() in builder_files:
            continue
        _content(name, stat.S_ISDIR(os.lstat(path).st_mode), modules)


def _integrity(tree: Path, path: Path) -> Path:
    mode = os.lstat(path).st_mode  # never follow or open anything that is not a plain file or directory
    name = path.relative_to(tree)
    try:
        str(name).encode('utf-8')
    except UnicodeEncodeError:
        raise template.TemplateRefused(f'A file name in the app is not valid UTF-8 ({str(path.name)!r}).') from None
    if template.git_name(path.name) and not (path.name == '.gitignore' and stat.S_ISREG(mode)):
        raise template.TemplateRefused(f'{name} is not allowed; the builder keeps the project history itself.')
    if stat.S_ISLNK(mode):
        raise template.TemplateRefused('Source contains a symbolic link; it is not built.')
    if not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
        raise template.TemplateRefused(f'{name} is not a regular file; it is not built.')
    if stat.S_ISDIR(mode) and mode & 0o700 != 0o700 or stat.S_ISREG(mode) and mode & 0o600 != 0o600:
        raise template.TemplateRefused(f'{name} is not readable and writable by the builder; it is not built.')
    if stat.S_ISREG(mode) and os.lstat(path).st_nlink > 1:
        raise template.TemplateRefused(f'{name} is a hard link; it is not built.')
    if stat.S_ISREG(mode) and os.lstat(path).st_size > template.MAX_FILE:
        raise template.TemplateRefused(f'{name} is larger than {template.MAX_FILE // (1024 * 1024)} MB.')
    return name


def _content(name: Path, is_dir: bool, modules: set) -> None:
    lower = name.name.lower()
    if is_dir:
        if name.name in BUILD_STATE and name.parent in modules:
            raise template.TemplateRefused(f'{name} is build output or IDE state; the builder builds from source '
                                           'only, so leave it out.')
        return
    if lower.endswith('.iml'):
        raise template.TemplateRefused(f'{name} is IDE state; leave it out.')
    if lower.endswith(KEY_SUFFIXES):
        raise template.TemplateRefused(f'{name} looks like key material. The builder signs every app with the '
                                       'project\'s own key, outside the build; a project carries no keys.')
    if lower.endswith(NATIVE_SUFFIXES) or lower in NATIVE_NAMES:
        raise template.TemplateRefused(f'{name} is native code; the builder\'s Android stack has no NDK, so apps are '
                                       'Kotlin or Java.', dependency=True)
    if name.as_posix() == WRAPPER_JAR:
        return
    if lower.endswith(LIBRARY_SUFFIXES):
        raise template.TemplateRefused(f'{name} is a prebuilt library; a project uses the libraries the builder '
                                       'provides, by their coordinates.', dependency=True)
    if lower.endswith(BINARY_SUFFIXES):
        raise template.TemplateRefused(f'{name} is a prebuilt binary; the builder builds from source only.')


def _kotlin_package(package: str) -> str:
    return '.'.join(f'`{p}`' if p in KOTLIN_KEYWORDS else p for p in package.split('.'))


def skeleton(package: str, label: str) -> dict[str, str]:
    """A new project's files (relative path -> text): the smallest ordinary Kotlin + Jetpack Compose app on the
    pinned stack, one launcher activity showing the app's name. The builder overrides its identity at build time; its
    versionCode and versionName are placeholders. No theme or template framework: the app uses Material 3 as is."""
    path, sdk = package.replace('.', '/'), gradle_stack
    activity = gradle_stack.LIBRARIES['androidx.activity:activity-compose']
    return {
        'settings.gradle.kts': (
            'pluginManagement {\n'
            '    repositories { google(); mavenCentral(); gradlePluginPortal() }\n'
            '}\n'
            'dependencyResolutionManagement {\n'
            '    repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS)\n'
            '    repositories { google(); mavenCentral() }\n'
            '}\n'
            f'rootProject.name = "{package.rsplit(".", 1)[-1]}"\n'
            f'include(":{sdk.APP_MODULE}")\n'),
        'build.gradle.kts': (
            'plugins {\n'
            f'    id("com.android.application") version "{sdk.PLUGINS["com.android.application"]}" apply false\n'
            '    id("org.jetbrains.kotlin.plugin.compose") version '
            f'"{sdk.PLUGINS["org.jetbrains.kotlin.plugin.compose"]}" apply false\n'
            '}\n'),
        'gradle.properties': (
            'android.useAndroidX=true\n'
            'kotlin.code.style=official\n'
            'org.gradle.jvmargs=-Xmx2g -Dfile.encoding=UTF-8\n'),
        f'{sdk.APP_MODULE}/build.gradle.kts': (
            'plugins {\n'
            '    id("com.android.application")\n'
            '    id("org.jetbrains.kotlin.plugin.compose")\n'
            '}\n\n'
            'android {\n'
            f'    namespace = "{package}"\n'
            f'    compileSdk = {sdk.COMPILE_SDK}\n'
            '    defaultConfig {\n'
            f'        applicationId = "{package}"\n'
            f'        minSdk = {sdk.MIN_SDK}\n'
            f'        targetSdk = {sdk.TARGET_SDK}\n'
            '        versionCode = 1  // the builder sets the real one\n'
            '        versionName = "1"\n'
            '    }\n'
            '    buildFeatures { compose = true }\n'
            '}\n\n'
            'dependencies {\n'
            f'    implementation(platform("androidx.compose:compose-bom:{sdk.COMPOSE_BOM}"))\n'
            f'    implementation("androidx.activity:activity-compose:{activity}")\n'
            '    implementation("androidx.compose.material3:material3")\n'
            '    implementation("androidx.compose.ui:ui")\n'
            '}\n'),
        f'{sdk.APP_MODULE}/src/main/AndroidManifest.xml': (
            '<?xml version="1.0" encoding="utf-8"?>\n'
            '<manifest xmlns:android="http://schemas.android.com/apk/res/android">\n'
            '  <application android:label="@string/app_name" android:allowBackup="true"\n'
            '      android:theme="@android:style/Theme.Material.Light.NoActionBar">\n'
            '    <activity android:name=".MainActivity" android:exported="true">\n'
            '      <intent-filter>\n'
            '        <action android:name="android.intent.action.MAIN"/>\n'
            '        <category android:name="android.intent.category.LAUNCHER"/>\n'
            '      </intent-filter>\n'
            '    </activity>\n'
            '  </application>\n'
            '</manifest>\n'),
        f'{sdk.APP_MODULE}/src/main/res/values/strings.xml': (
            '<?xml version="1.0" encoding="utf-8"?>\n'
            '<resources><string name="app_name" formatted="false">'
            f'{template.android_string(label)}</string></resources>\n'),
        f'{sdk.APP_MODULE}/src/main/java/{path}/MainActivity.kt': (
            f'package {_kotlin_package(package)}\n\n'
            'import android.os.Bundle\n'
            'import androidx.activity.ComponentActivity\n'
            'import androidx.activity.compose.setContent\n'
            'import androidx.compose.material3.MaterialTheme\n'
            'import androidx.compose.material3.Surface\n'
            'import androidx.compose.material3.Text\n'
            'import androidx.compose.ui.res.stringResource\n\n'
            'class MainActivity : ComponentActivity() {\n'
            '    override fun onCreate(savedInstanceState: Bundle?) {\n'
            '        super.onCreate(savedInstanceState)\n'
            '        setContent {\n'
            '            MaterialTheme {\n'
            '                Surface { Text(stringResource(R.string.app_name)) }\n'
            '            }\n'
            '        }\n'
            '    }\n'
            '}\n'),
    }


def write_skeleton(source: Path, package: str, label: str) -> None:
    for name, text in skeleton(package, label).items():
        target = Path(source) / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding='utf-8')


def init_script() -> str:
    """The builder's init script: every application variant's applicationId, versionCode and versionName are the
    builder's (ROSE_APPLICATION_ID, ROSE_VERSION_CODE, ROSE_VERSION_NAME), set through AGP's variant API before any
    of the project's own variant callbacks. Values are read inside the action (Gradle 9 isolates lifecycle actions);
    reflection keeps the script free of AGP's classes, which init scripts cannot see. A project can still override
    them after; the built APK is checked, so that only fails its build.

    It also resolves the Kotlin Gradle plugin, wherever a build script's classpath asks for it, at the stack's
    version: AGP alone asks for an older one, which the offline cache does not hold, so a project without the Compose
    compiler plugin (a Java or views app) still gets the stack's Kotlin, as every project the cache was seeded with
    does. Only that plugin's own artifacts are redirected, and nothing is added to a classpath that did not ask for
    it, so a build with no plugin requests at its root, or a buildSrc, resolves exactly as it otherwise would."""
    return '''\
// The Rose builder's own init script: the app's identity is the builder's, whatever the project's build files say.
gradle.lifecycle.beforeProject {
    buildscript.configurations.configureEach {
        resolutionStrategy.eachDependency {
            if (requested.group == "org.jetbrains.kotlin" && requested.name.startsWith("kotlin-gradle-plugin")) {
                useVersion("@KOTLIN@")
            }
        }
    }
    pluginManager.withPlugin("com.android.application") {
        val applicationId = System.getenv("ROSE_APPLICATION_ID")!!
        val versionCode = System.getenv("ROSE_VERSION_CODE")!!.toInt()
        val versionName = System.getenv("ROSE_VERSION_NAME")!!
        val components = extensions.getByName("androidComponents")
        val selector = components.javaClass.getMethod("selector").invoke(components)
        val all = selector.javaClass.getMethod("all").invoke(selector)
        val onVariants = components.javaClass.methods.first {
            it.name == "onVariants" && it.parameterCount == 2 && it.parameterTypes[1] == Action::class.java
        }
        onVariants.invoke(components, all, Action<Any> {
            @Suppress("UNCHECKED_CAST")
            (javaClass.getMethod("getApplicationId").invoke(this) as org.gradle.api.provider.Property<String>)
                .set(applicationId)
            @Suppress("UNCHECKED_CAST")
            for (o in javaClass.getMethod("getOutputs").invoke(this) as List<Any>) {
                (o.javaClass.getMethod("getVersionCode").invoke(o) as org.gradle.api.provider.Property<Int>)
                    .set(versionCode)
                (o.javaClass.getMethod("getVersionName").invoke(o) as org.gradle.api.provider.Property<String>)
                    .set(versionName)
            }
        })
    }
}
'''.replace('@KOTLIN@', gradle_stack.KOTLIN)


def build(tree: Path, work: Path, package: str, version_code: int, version_name: str, gradle: GradleTools,
          tools: toolchain.Tools, cancelled=lambda: False, builder_files=frozenset()) -> tuple[Path, str]:
    """An unsigned, 16 KB-aligned APK (work/unsigned.apk) of the Gradle project ``tree`` for exactly this identity,
    checked on its binary, and the build log; work/api-report.json is the advisory API level report, as for
    toolchain.build. ``tools`` (the legacy toolchain) gives the run settings and the API database. Gradle runs in
    work/sandbox only; the builder's own files (the copied APK, dex, the verification home) are outside it, so the
    build cannot plant them. Raises StepFailed."""
    missing = gradle.available()
    if missing:
        raise StepFailed('toolchain', missing, retryable=True)
    try:
        check(tree, builder_files=frozenset(builder_files))
    except template.TemplateRefused as refused:
        raise StepFailed('dependency' if refused.dependency else 'compile', str(refused)) from None
    work = Path(os.path.realpath(work))
    jdk, dist, sdk, cache = (Path(os.path.realpath(p)) for p in (gradle.jdk, gradle.gradle, gradle.sdk, gradle.cache))
    box = work / 'sandbox'
    remove_tree(box)
    box.mkdir(mode=0o700, parents=True)
    project, home, gradle_home, tmp = box / 'project', box / 'home', box / 'gradle-home', box / 'tmp'
    _copy_tree(Path(tree), project)
    for folder in (home, gradle_home, tmp):
        folder.mkdir(mode=0o700)
    _write(project / 'local.properties', f'sdk.dir={sdk}\n', replace=True)
    _write(gradle_home / 'gradle.properties', _properties(jdk, home, tmp))
    _write(box / INIT, init_script())
    opts = f'-Djava.io.tmpdir={tmp} -Duser.home={home} -XX:-UsePerfData'
    env = {'PATH': f'{jdk}/bin:/usr/bin:/bin', 'HOME': str(home), 'TMPDIR': str(tmp), 'LANG': 'C.UTF-8',
           'JAVA_HOME': str(jdk), 'GRADLE_USER_HOME': str(gradle_home), 'GRADLE_RO_DEP_CACHE': str(cache),
           'ANDROID_HOME': str(sdk), 'ANDROID_USER_HOME': str(home / '.android'), 'GRADLE_OPTS': opts,
           'ROSE_APPLICATION_ID': package, 'ROSE_VERSION_CODE': str(int(version_code)),
           'ROSE_VERSION_NAME': version_name}
    argv = [str(dist / 'bin/gradle'), '--offline', '--no-daemon', '--console=plain', '--init-script',
            str(box / INIT), '-p', str(project), TASK]
    redact = (project, work, jdk, dist, sdk, cache)
    own, verify_home = work / 'gradle-output.apk', work / 'verify-home'
    try:
        log = _run(gradle, tools, argv, env, box, (jdk, dist, sdk, cache), redact, cancelled)
        for path in (own, work / 'unsigned.apk', work / 'dex', verify_home):
            _discard(path)
        verify_home.mkdir(mode=0o700)
        name = _take_output(box, own, log)
    finally:  # the job's Gradle home, intermediates and outputs: hundreds of MB that nothing reads again
        remove_tree(box)
    _refuse_signed(own, name)
    unsigned = _align(own, work, gradle.bt, tools, verify_home, cancelled, redact)
    try:
        badging = toolchain.verify_apk(unsigned, package, version_code, version_name, tools, work, verify_home,
                                       cancelled, redact, bt=gradle.bt)
    except StepFailed as failed:
        if failed.category == 'internal':
            failed = StepFailed('compile', 'The app\'s build files change its package, versionCode or versionName; '
                                'the builder sets those, so leave them to it.')
        if failed.category != 'compile':
            raise
        # The writer's own build files decide these in a Gradle project, so the refusal goes back to it (a repair).
        raise StepFailed('compile', failed.summary, log=failed.summary) from None
    min_sdk = toolchain.sdk_levels(badging)[0]
    # Advisory only, and not for a Gradle build: its dex is mostly AndroidX and Kotlin (dexdump of a Compose app is
    # hundreds of MB of text), so the report would be about libraries, slowly. The older layout keeps it.
    from .apilevel import unavailable
    report = unavailable('not made for Gradle builds (their dex is mostly AndroidX and Kotlin libraries)', min_sdk)
    toolchain._private(work / 'api-report.json', json.dumps(report, sort_keys=True))
    from .apilevel import summary
    return unsigned, '\n'.join(x for x in (log, summary(report)) if x)


def _properties(jdk: Path, home: Path, tmp: Path) -> str:
    """The builder's gradle.properties in GRADLE_USER_HOME, which outranks the project's own."""
    return ''.join(f'{k}={v}\n' for k, v in (
        ('org.gradle.daemon', 'false'),
        ('org.gradle.java.installations.auto-download', 'false'),
        ('org.gradle.java.installations.auto-detect', 'false'),
        ('org.gradle.java.installations.paths', jdk),
        ('org.gradle.java.home', jdk),
        ('org.gradle.jvmargs', f'-Xmx2g -Djava.io.tmpdir={tmp} -Duser.home={home} -XX:-UsePerfData'),
        ('org.gradle.vfs.watch', 'false'),
        ('org.gradle.welcome', 'never'),
        ('kotlin.compiler.execution.strategy', 'in-process'),
        ('android.builder.sdkDownload', 'false')))


def _write(path: Path, text: str, replace: bool = False) -> None:
    if replace and os.path.lexists(path):
        os.unlink(path)  # a checked tree holds no link here, but never write through one
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        f.write(text)


def _copy_tree(source: Path, target: Path) -> None:
    """A fresh copy of a checked tree: plain directories and regular files only, nothing followed."""
    target.mkdir(mode=0o700)
    for entry in sorted(os.scandir(source), key=lambda e: e.name):
        mode = entry.stat(follow_symlinks=False).st_mode
        if stat.S_ISDIR(mode):
            _copy_tree(Path(entry.path), target / entry.name)
        elif stat.S_ISREG(mode):
            fd = os.open(entry.path, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(fd, 'rb') as src, os.fdopen(os.open(target / entry.name, os.O_WRONLY | os.O_CREAT
                                                               | os.O_EXCL | os.O_NOFOLLOW, 0o600), 'wb') as dst:
                shutil.copyfileobj(src, dst)
        else:
            raise StepFailed('compile', 'Source contains a link or special file; it is not built.')


def remove_tree(path: Path) -> None:
    """Remove a tree the build wrote, whatever modes its code left on it: inside the isolation its files are the
    builder's own, so every folder is given back its owner access first (top down, so a folder made unreadable is
    opened again before it is walked), then the tree is removed. Links are never followed."""
    if not os.path.lexists(path):
        return
    if os.path.isdir(path) and not os.path.islink(path):
        os.chmod(path, 0o700)
        for folder, dirs, _ in os.walk(path, topdown=True, followlinks=False):
            for name in dirs:
                child = os.path.join(folder, name)
                if not os.path.islink(child):
                    os.chmod(child, 0o700)
        shutil.rmtree(path)
    else:
        os.unlink(path)


def _discard(path: Path) -> None:
    if not os.path.lexists(path):
        return
    if stat.S_ISDIR(os.lstat(path).st_mode):
        shutil.rmtree(path)
    else:
        os.unlink(path)


def _run(gradle: GradleTools, tools: toolchain.Tools, argv, env, box: Path, readonly, redact, cancelled) -> str:
    """Gradle in the isolation, as server_build runs cargo: the provider's own refusal stands, any other failure to
    prepare or start it is ``build_isolation_unavailable``, a failure it claims is ``build_isolation_failed``; a
    cancel or timeout passes through; anything else is the project's build failing (classify)."""
    try:
        command, wrapped = gradle.isolation.command(argv, dict(env), writable=box, readonly=tuple(readonly))
    except StepFailed:
        raise  # the provider's own account of why it cannot isolate (BuildFailed)
    except Exception:
        raise BuildFailed('toolchain', 'build_isolation_unavailable',
                          'The app was not built: the build isolation could not prepare the build.') from None
    try:
        return toolchain.run(command, box, Path(env['HOME']), dataclasses.replace(tools, timeout_s=gradle.timeout_s),
                             cancelled, 'compile', redact=redact, env=wrapped)
    except StepFailed as failed:
        if failed.category != 'compile':
            raise  # cancelled or timeout
        try:
            own = failed.returncode is not None and gradle.isolation.failed(failed.returncode, failed.log)
        except Exception:
            own = True  # a provider that cannot judge its own failure is still not the app's
        if own:
            raise BuildFailed('toolchain', 'build_isolation_failed',
                              'The app was not built: the build isolation could not run the build.',
                              log=failed.log[-4000:]) from None
        raise classify(failed.log, failed.returncode) from None
    except OSError:
        raise BuildFailed('toolchain', 'build_isolation_unavailable',
                          'The app was not built: the build isolation could not be started.') from None


_STACK_TRACE = re.compile(r'^\s+at [\w$.<>/-]+\(|^\s+\.\.\. \d+ more$|^Caused by: ')
_ERROR_LINE = re.compile(r'^e: |^\S.*:\d+: error: |^ERROR: |AAPT: error: |^\S.*\.(?:kts?|gradle|java|xml):\d+.*error')
_SECTION = re.compile(r'^\* (?:What went wrong|Where):\s*$')


def diagnostics(log: str) -> str:
    """The build's own errors from a Gradle log, bounded to MAX_LOG characters: the compilers' error lines (Kotlin
    ``e:``, javac ``error:``, AAPT2 ``ERROR:``) and the "* Where:" and "* What went wrong:" blocks with their "> ..."
    causes, without "* Try:", BUILD FAILED, stack traces or repeats. Falls back to the log's last lines, so a failed
    build's log is never empty."""
    kept, seen, section = [], set(), False
    for line in log.splitlines():
        line = line.rstrip()
        if _SECTION.match(line):
            section = True
        elif line.startswith('* ') or line.startswith('BUILD FAILED'):
            section = False
            continue
        if _STACK_TRACE.match(line) or not line.strip():
            continue
        if (section or _ERROR_LINE.search(line)) and line not in seen:
            seen.add(line)
            kept.append(line)
    if not kept:
        kept = [x for x in log.splitlines() if x.strip() and not _STACK_TRACE.match(x)][-40:]
    text = '\n'.join(kept)
    return text if len(text) <= MAX_LOG else text[:MAX_LOG - 20].rsplit('\n', 1)[0] + '\n... (truncated)'


_MISSING = (re.compile(r"Plugin \[id: '([^']+)'(?:, version: '([^']+)')?[^\]]*\] was not found"),
            re.compile(r'No cached version of ([\w.-]+:[\w.-]+(?::[\w.+-]+)?) available for offline mode'),
            re.compile(r'Could not find ([\w.-]+:[\w.-]+(?::[\w.+-]+)?)\.?\s*$', re.M),
            re.compile(r'Could not resolve ([\w.-]+:[\w.-]+(?::[\w.+-]+)?)\.?\s*$', re.M),
            re.compile(r'(Could not resolve all (?:files|dependencies|artifacts) for configuration)'))
_SDK = re.compile(r'Failed to find (?:target|Platform SDK|Build Tools)|SDK (?:platform|package)s? .*not installed|'
                  r'licen[cs]es for the following SDK|(?:platforms|build-tools);[\w.-]+')
_JDK = re.compile(r'No matching toolchains? found|Cannot find a Java installation|toolchain.*(?:download|provision)')


def classify(log: str, returncode: int | None = None) -> StepFailed:
    """A failed Gradle build as the job's failure: ``dependency`` when a plugin or library is not in the stack's
    offline cache, ``toolchain`` (retryable) when Gradle was killed or stopped without saying why, else ``compile``
    (whatever the project's code or build files did, an SDK or JDK the stack lacks included). The log is always
    the extracted diagnostics."""
    text = diagnostics(log)
    for pattern in _MISSING:
        found = pattern.search(text)
        if found:
            what = ':'.join(x for x in found.groups() if x)
            what = 'a library' if what.startswith('Could not resolve all') else what
            return StepFailed('dependency', f'The app needs {what}, which this builder does not have offline. '
                              + gradle_stack.summary(), log=text, returncode=returncode)
    if _SDK.search(text):
        return StepFailed('compile', f'The app asks for an Android SDK the builder does not have: use compileSdk '
                          f'{gradle_stack.COMPILE_SDK} and the default build tools (do not set buildToolsVersion).',
                          log=text, returncode=returncode)
    if _JDK.search(text):
        return StepFailed('compile', f'The app asks for a Java toolchain the builder does not have: it builds with '
                          f'JDK {gradle_stack.JDK_FEATURE}; request that or none.', log=text, returncode=returncode)
    if not re.search(r'What went wrong|^e: |error:|ERROR:', log, re.M):
        killed = returncode is not None and returncode < 0
        return StepFailed('toolchain', 'Gradle stopped' + (' (killed)' if killed else '') + ' without saying why.',
                          retryable=True, log=text, returncode=returncode)
    kind = ('its Kotlin code' if re.search(r'^e: ', text, re.M) else
            'its Java code' if re.search(r':\d+: error: ', text) else
            'its resources' if re.search(r'AAPT|ERROR: ', text) else
            'its manifest' if 'Manifest merger failed' in text else 'its build files')
    return StepFailed('compile', f'The app\'s Gradle build failed in {kind}.', log=text, returncode=returncode)


def _open_beneath(root: Path, relative: str) -> int:
    """A read-only fd of ``root/relative`` (a regular file), with no link followed at any step below ``root``. The
    build wrote everything below root, so it may have planted links to the builder's own files."""
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        parts = relative.split('/')
        for part in parts[:-1]:
            next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        file_fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=fd)
    finally:
        os.close(fd)
    if not stat.S_ISREG(os.fstat(file_fd).st_mode):
        os.close(file_fd)
        raise OSError('not a regular file')
    return file_fd


def _take_output(box: Path, target: Path, log: str) -> str:
    """Copy the build's one release APK to ``target`` and return its name: output-metadata.json must list exactly one
    element, of type SINGLE with no filters, whose outputFile is a plain name of a regular file beside it. A refusal's
    log says what the build made instead, so the writer can fix its build files."""
    folder = f'project/{OUTPUT}'
    one = 'Rose installs one APK: the release build must make a single APK, with no ABI or density splits.'
    try:
        with os.fdopen(_open_beneath(box, f'{folder}/output-metadata.json'), 'rb') as f:
            metadata = json.loads(f.read(MAX_METADATA + 1)[:MAX_METADATA].decode('utf-8'))
    except (OSError, ValueError):
        raise StepFailed('compile', f'The app\'s build did not make a release APK ({TASK} wrote no '
                         f'{OUTPUT}/output-metadata.json).', log=diagnostics(log)) from None
    elements = metadata.get('elements') if isinstance(metadata, dict) else None
    if not isinstance(elements, list) or not all(isinstance(e, dict) for e in elements):
        raise StepFailed('compile', one, log=f'{OUTPUT}/output-metadata.json lists no APK elements.')
    made = '\n'.join(f'{e.get("outputFile")!s:.200} (type {e.get("type")!s:.40}, filters '
                     f'{json.dumps(e.get("filters"))[:200]})' for e in elements[:20])
    evidence = f'{OUTPUT}/output-metadata.json lists {len(elements)} APK(s):\n{made}'
    if len(elements) != 1:
        raise StepFailed('compile', one, log=evidence)
    name = elements[0].get('outputFile')
    if (elements[0].get('filters') or elements[0].get('type', 'SINGLE') != 'SINGLE' or not isinstance(name, str)
            or not re.fullmatch(r'[\w+-][\w.+-]*\.apk', name)):
        raise StepFailed('compile', one, log=evidence)
    try:
        with os.fdopen(_open_beneath(box, f'{folder}/{name}'), 'rb') as src, \
                os.fdopen(os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600), 'wb') as dst:
            shutil.copyfileobj(src, dst)
    except OSError:
        raise StepFailed('compile', f'The app\'s build listed {name}, which is not a plain file.',
                         log=evidence) from None
    return name


# The ABIs every native library the APK ships must be there for: Rose's phones (arm64-v8a) and the emulators an app
# is checked on (x86_64). The libraries come from AndroidX (the stack has no NDK), which ships these and more.
REQUIRED_ABIS = ('arm64-v8a', 'x86_64')


def _missing_native(names) -> str | None:
    """The first ``lib/<abi>/<name>.so`` a required ABI lacks while another ABI has it (an ABI filter or an
    exclude), or None. An APK with no native libraries needs none."""
    found = {}
    for entry in names:
        parts = entry.split('/')
        if len(parts) == 3 and parts[0] == 'lib' and parts[2].endswith('.so'):
            found.setdefault(parts[2], set()).add(parts[1])
    for library, abis in sorted(found.items()):
        for abi in REQUIRED_ABIS:
            if abi not in abis:
                return f'lib/{abi}/{library}'
    return None


def _refuse_signed(apk: Path, name: str) -> None:
    """The build's APK must be unsigned: no v1 signature files and no APK Signing Block. The builder signs it with
    the project's own key afterwards and never signs over another signature."""
    signed = ('The app\'s build signs it; remove the signingConfig from the release build. The builder signs every '
              'app with the project\'s own key.')
    invalid = StepFailed('compile', 'The app\'s build did not make a valid APK.', log=f'{name} is not a valid APK.')
    try:
        with zipfile.ZipFile(apk) as z:
            names = z.namelist()
    except (zipfile.BadZipFile, OSError, ValueError):
        raise invalid from None
    v1 = [n for n in names if re.fullmatch(r'META-INF/[^/]+\.(?:SF|RSA|DSA|EC)', n, re.I)]
    if v1:
        raise StepFailed('compile', signed, log=f'{name} holds signature files ({", ".join(v1[:5])[:400]}).')
    missing = _missing_native(names)
    if missing:
        raise StepFailed('compile', 'The app\'s build leaves out native libraries an Android phone or emulator needs '
                         f'({missing}); remove abiFilters, splits and jniLibs excludes, so one APK installs everywhere.',
                         log=f'{name} has no {missing}.')
    with open(apk, 'rb') as f:
        f.seek(0, os.SEEK_END)
        size = f.tell()
        f.seek(max(0, size - 65557))  # the end of central directory record: 22 bytes and a comment of up to 64 KB
        tail = f.read()
        end = tail.rfind(b'PK\x05\x06')
        if end < 0 or end + 22 > len(tail):
            raise invalid
        central = int.from_bytes(tail[end + 16:end + 20], 'little')
        if central == 0xFFFFFFFF or central < 16 or central > size:  # Zip64, or no room for a block before it
            raise invalid
        f.seek(central - 16)
        if f.read(16) == APK_SIG_BLOCK:
            raise StepFailed('compile', signed, log=f'{name} carries an APK Signing Block (it is already signed).')


def _align(apk: Path, work: Path, bt: Path, tools: toolchain.Tools, home: Path, cancelled, redact) -> Path:
    """work/unsigned.apk: the build's APK as is when zipalign says it is aligned (4 bytes, native libraries on 16 KB
    pages), else realigned."""
    unsigned = work / 'unsigned.apk'
    try:
        toolchain.run([bt / 'zipalign', '-c', '-P', '16', '4', apk], work, home, tools, cancelled, 'compile', redact)
        os.rename(apk, unsigned)
    except StepFailed as failed:
        if failed.category != 'compile':
            raise
        try:
            toolchain.run([bt / 'zipalign', '-P', '16', '-f', '4', apk, unsigned], work, home, tools, cancelled,
                          'compile', redact)
        except StepFailed as again:
            if again.category != 'compile':
                raise
            raise StepFailed('compile', 'The app\'s APK could not be aligned.', log=again.log[-MAX_LOG:]) from None
    return unsigned

