"""Provision the one pinned Gradle stack ordinary Gradle projects build with (docs/APP-BUILDER.md §2.4).

    python -m rose.app_builder.gradle_prepare stack --out DIR [--downloads DIR]
    python -m rose.app_builder.gradle_prepare measure [--downloads DIR]     (also: stack --measure)

``stack`` makes DIR/{jdk,gradle,sdk,cache,stack.json} for this host, exactly what rose.app_builder.gradle_stack pins:

- every archive (the JDK, the Gradle distribution, each SDK package) is downloaded into ``--downloads`` (kept there;
  a file already there is only checked) and its SHA-256 checked against the pin BEFORE it is opened. Archives are
  untrusted data: each is unpacked into a fresh empty folder, entries with an absolute or ``..`` path, links
  leaving the tree (zip: any link) and special files are refused, and nothing in them runs until all are checked;
- each SDK package goes to its own directory (platforms/<PLATFORM>, build-tools/<BUILD_TOOLS>) with the
  ``package.xml`` sdkmanager would write, generated here from fixed data (Google's repository manifest entry, see
  ``SDK_DETAILS``) and checked against the archive's own source.properties: AGP needs it to see the package as
  installed (without it AGP tries to download the package and fails on its licence), and no manifest is fetched;
- the dependency cache is seeded by running the pinned Gradle ONLINE, as the operator, on the builder's own catalog
  project (``catalog()``: written here, never generated code): it applies every ``PLUGINS`` entry, depends on every
  ``LIBRARIES`` entry (the managed ones through the Compose BOM) and runs ``:app:assembleRelease`` (release minified)
  and the skeleton's own build, and resolves the release classpaths of one module per library alone and one per
  library next to the skeleton's, so an app using any one of them (or all) finds the versions conflict resolution
  picks for it. Gradle's ``caches/modules-2`` is then copied to DIR/cache/modules-2 without its lock files and
  ``gc.properties``;
- the result is proven before it is kept: the catalog builds again ``--offline`` from a fresh Gradle home with only
  the read-only copy as GRADLE_RO_DEP_CACHE and no proxy settings;
- DIR is read-only and world-readable, and ``stack.json`` records ``schemaVersion``, the pinned versions, the host
  ``os``/``arch`` and ``cacheSha256`` (``tree_digest`` of DIR/cache).

It is idempotent (a DIR that already is this stack, digest included, is left as is) and atomic: everything is built
in a sibling stage folder renamed into place only once proven; a failure leaves DIR exactly as it was. A DIR that
is not a stack this tool made is never replaced. It prints one JSON line holding the builder's ``gradle`` config
object for DIR (rose.app_builder.host). ``measure`` downloads the macOS archives whose SHA-256 pin is still None,
checks each against the size and SHA-1 Google's manifest records (``MEASURE``) and prints its SHA-256 for
gradle_stack.py; ``stack`` refuses a host whose archive is not pinned yet. Exit 0 ok, 1 refused, 2 usage.

Only provisioning uses the network; every build afterwards is offline, inside the host's isolation.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import platform
import posixpath
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath
from xml.sax.saxutils import escape

from . import gradle_stack
from .gradle_stack import Archive

SCHEMA_VERSION = 1
PARTS = ('jdk', 'gradle', 'sdk', 'cache')
CACHE = 'modules-2'
CACHE_SKIP_SUFFIXES = ('.lock',)
CACHE_SKIP_NAMES = ('gc.properties',)
SEED_TIMEOUT_S = 3600.0
DOWNLOAD_TIMEOUT_S = 120.0
# The operator's network settings the ONLINE seed build may use (the JVM reads its proxy and trust store from
# JAVA_TOOL_OPTIONS); the offline proof and every later build get none of them.
NETWORK_ENV = ('JAVA_TOOL_OPTIONS', 'HTTPS_PROXY', 'https_proxy', 'HTTP_PROXY', 'http_proxy', 'NO_PROXY', 'no_proxy')
CATALOG_PACKAGE = 'app.rose.catalog'

_NS = ('xmlns:common="http://schemas.android.com/repository/android/common/02" '
       'xmlns:generic="http://schemas.android.com/repository/android/generic/02" '
       'xmlns:sdk="http://schemas.android.com/sdk/android/repo/repository2/03" '
       'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"')
# What sdkmanager writes into each package's package.xml, from the package's entry in Google's repository manifest
# (repository2-3.xml): its type details, revision and display name. ``source`` is what the archive's own
# source.properties must say, so a pin moved without this table refuses instead of describing the wrong package.
SDK_DETAILS = {
    'platforms;android-37.0': {
        'type': 'sdk:platformDetailsType',
        'details': ('<api-level>37.0</api-level><codename/><extension-level>22</extension-level>'
                    '<base-extension>true</base-extension><layoutlib api="15"/>'),
        'revision': (2,), 'display': 'Android SDK Platform 37.0',
        'source': {'Pkg.Revision': '2', 'AndroidVersion.ApiLevel': '37.0', 'AndroidVersion.ExtensionLevel': '22'}},
    'build-tools;36.0.0': {
        'type': 'generic:genericDetailsType', 'details': '',
        'revision': (36, 0, 0), 'display': 'Android SDK Build-Tools 36',
        'source': {'Pkg.Revision': '36.0.0'}},
}
# macOS archives gradle_stack has no SHA-256 for yet: (name, size, SHA-1) as Google's repository manifest lists them.
MEASURE = {
    'build-tools;36.0.0': ('build-tools_r36_macosx.zip', 79121749, '199ae0047ee61e842f8ee0c6d3918e44fb9a1f83'),
}


class Refused(Exception):
    pass


# --- host and archives -------------------------------------------------------------------------------------------

def host() -> tuple[str, str]:
    """(os, arch) as gradle_stack.JDKS names them: ('linux', 'x86_64'), ('darwin', 'arm64')."""
    system, machine = platform.system().lower(), platform.machine().lower()
    return system, {'amd64': 'x86_64', 'aarch64': 'arm64'}.get(machine, machine)


def archives(system: str, machine: str) -> dict[str, Archive]:
    """Every archive the stack for this host is made of: 'jdk', 'gradle' and one per SDK package path. A host with
    no pinned JDK, or an SDK archive whose pin is still None, is refused (``measure`` produces the missing pin)."""
    jdk = gradle_stack.JDKS.get((system, machine))
    if jdk is None:
        raise Refused(f'the Gradle stack pins no JDK for {system}/{machine}')
    sdk_os = {'linux': 'linux', 'darwin': 'macosx'}.get(system)
    out = {'jdk': jdk, 'gradle': gradle_stack.GRADLE_DISTRIBUTION}
    for path, _target, per_os in gradle_stack.SDK_PACKAGES:
        archive = per_os.get(sdk_os) if sdk_os else None
        if archive is None:
            raise Refused(f'gradle_stack.py pins no {system} archive of {path} yet: run '
                          f'`python -m rose.app_builder.gradle_prepare measure` and pin its SHA-256')
        out[path] = archive
    return out


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, 'rb') as stream:
        for block in iter(lambda: stream.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()


def _download(url: str, target: Path) -> None:
    """``url`` into ``target`` (a new file). urllib uses the operator's HTTPS_PROXY and the system trust store."""
    with urllib.request.urlopen(url, timeout=DOWNLOAD_TIMEOUT_S) as response, open(target, 'xb') as out:
        shutil.copyfileobj(response, out, 1 << 20)


def fetch(archive: Archive, downloads: Path, log=sys.stderr) -> Path:
    """The archive's file in ``downloads``, downloaded if absent, and only if its SHA-256 is the pin. A file already
    there that is not the pinned archive is refused, not replaced (the operator put it there)."""
    downloads.mkdir(parents=True, exist_ok=True)
    path = downloads / archive.name
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise Refused(f'{path} is not a regular file')
    if not path.exists():
        partial = downloads / f'.{archive.name}.partial-{os.getpid()}'
        with contextlib.suppress(FileNotFoundError):
            partial.unlink()
        print(f'gradle_prepare: downloading {archive.url}', file=log, flush=True)
        try:
            _download(archive.url, partial)
            if sha256_file(partial) != archive.sha256:
                raise Refused(f'the download of {archive.name} is not the pinned archive (SHA-256 differs)')
            os.replace(partial, path)
        except OSError as exc:
            raise Refused(f'{archive.name} could not be downloaded ({type(exc).__name__}: {exc})') from None
        finally:
            with contextlib.suppress(FileNotFoundError):
                partial.unlink()
    if sha256_file(path) != archive.sha256:
        raise Refused(f'{path} is not the pinned archive (SHA-256 differs): delete it and run this again')
    return path


# --- unpacking untrusted archives --------------------------------------------------------------------------------

def _member(name: str) -> PurePosixPath:
    """A safe relative archive path: not absolute, no ``..``, no backslash or NUL, not empty."""
    if '\\' in name or '\0' in name:
        raise Refused(f'an archive entry has an unsafe name: {name!r}')
    path = PurePosixPath(name)
    if path.is_absolute() or not path.parts or '..' in path.parts:
        raise Refused(f'an archive entry leaves its folder: {name!r}')
    return path


def _plain_mode(mode: int) -> int:
    """Owner-writable, world-readable; executable for everyone if any execute bit was set; never set-id."""
    return 0o755 if mode & 0o111 else 0o644


def _unzip(archive: Path, into: Path) -> None:
    with zipfile.ZipFile(archive) as z:
        infos = z.infolist()
        for info in infos:  # every entry checked before anything is written
            _member(info.filename)
            kind = stat.S_IFMT(info.external_attr >> 16)
            if kind not in (0, stat.S_IFREG, stat.S_IFDIR):
                raise Refused(f'{archive.name} holds a link or special file: {info.filename!r}')
        for info in infos:
            target = into.joinpath(*_member(info.filename).parts)
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, 'wb') as out, z.open(info) as data:
                shutil.copyfileobj(data, out, 1 << 20)
            os.chmod(target, _plain_mode(info.external_attr >> 16))


def _untar(archive: Path, into: Path) -> None:
    with tarfile.open(archive) as t:
        members = t.getmembers()
        for m in members:
            _member(m.name)
            if not (m.isreg() or m.isdir() or m.issym() or m.islnk()):
                raise Refused(f'{archive.name} holds a special file: {m.name!r}')
            if m.issym() or m.islnk():  # only a relative link within the tree (the JDK's legal/ notices)
                base = PurePosixPath(m.name).parent if m.issym() else PurePosixPath()
                resolved = posixpath.normpath(str(base / m.linkname))
                if PurePosixPath(m.linkname).is_absolute() or resolved == '..' or resolved.startswith('../'):
                    raise Refused(f'{archive.name} holds a link out of its folder: {m.name!r}')
        try:
            t.extractall(into, members=members, filter='data')  # refuses again what leaves ``into``
        except tarfile.FilterError as exc:
            raise Refused(f'{archive.name} holds an unsafe entry ({exc})') from None


def unpack(archive: Path, into: Path) -> Path:
    """Unpack ``archive`` (.zip, .tar.gz) into ``into``, which must not exist yet, and return its one top-level
    directory (every archive the stack pins has exactly one)."""
    into.mkdir(mode=0o755)
    if archive.name.endswith('.zip'):
        _unzip(archive, into)
    elif archive.name.endswith(('.tar.gz', '.tgz')):
        _untar(archive, into)
    else:
        raise Refused(f'{archive.name} is not an archive this tool unpacks')
    tops = list(into.iterdir())
    if len(tops) != 1 or tops[0].is_symlink() or not tops[0].is_dir():
        raise Refused(f'{archive.name} does not hold exactly one top-level folder')
    return tops[0]


# --- SDK packages ------------------------------------------------------------------------------------------------

def package_xml(path: str) -> str:
    """The package.xml sdkmanager writes for the installed SDK package ``path`` (e.g. 'platforms;android-37.0')."""
    details = SDK_DETAILS.get(path)
    if details is None:
        raise Refused(f'gradle_prepare has no package details for the SDK package {path}')
    revision = ''.join(f'<{k}>{v}</{k}>' for k, v in zip(('major', 'minor', 'micro'), details['revision']))
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            f'<common:repository {_NS}>'
            f'<localPackage path="{escape(path, {chr(34): "&quot;"})}" obsolete="false">'
            f'<type-details xsi:type="{details["type"]}">{details["details"]}</type-details>'
            f'<revision>{revision}</revision>'
            f'<display-name>{escape(details["display"])}</display-name>'
            '</localPackage></common:repository>\n')


def _properties(path: Path) -> dict[str, str]:
    out = {}
    for line in path.read_text(encoding='utf-8', errors='replace').splitlines():
        line = line.strip()
        if line and not line.startswith(('#', '!')) and '=' in line:
            key, value = line.split('=', 1)
            out[key.strip()] = value.strip()
    return out


def install_sdk_package(path: str, top: Path, sdk: Path, target: str) -> Path:
    """Move the unpacked package folder ``top`` to ``sdk/target`` with its package.xml, once its source.properties
    says it is the package SDK_DETAILS describes."""
    details = SDK_DETAILS.get(path)
    if details is None:
        raise Refused(f'gradle_prepare has no package details for the SDK package {path}')
    try:
        source = _properties(top / 'source.properties')
    except OSError:
        raise Refused(f'the {path} archive has no source.properties') from None
    for key, value in details['source'].items():
        if source.get(key) != value:
            raise Refused(f'the {path} archive says {key}={source.get(key)!r}, not {value!r}')
    destination = sdk.joinpath(*PurePosixPath(target).parts)
    destination.parent.mkdir(parents=True, exist_ok=True)
    top.rename(destination)
    (destination / 'package.xml').write_text(package_xml(path), encoding='utf-8')
    return destination


def _install_jdk(top: Path, target: Path) -> None:
    home = top / 'Contents' / 'Home' if (top / 'Contents' / 'Home' / 'bin' / 'java').is_file() else top
    try:
        release = _properties(home / 'release')
    except OSError:
        raise Refused('the JDK archive holds no JDK (no release file)') from None
    version = release.get('JAVA_VERSION', '').strip('"')
    if version.split('.')[0] != str(gradle_stack.JDK_FEATURE) or not (home / 'bin' / 'java').is_file():
        raise Refused(f'the JDK archive is not JDK {gradle_stack.JDK_FEATURE} ({version or "no version"})')
    home.rename(target)


def _install_gradle(top: Path, target: Path) -> None:
    if top.name != f'gradle-{gradle_stack.GRADLE}' or not (top / 'bin' / 'gradle').is_file() \
            or not (top / 'lib').is_dir():
        raise Refused(f'the Gradle archive holds no Gradle {gradle_stack.GRADLE} distribution')
    top.rename(target)


def install(stage: Path, downloads: Path, system: str, machine: str, log=sys.stderr) -> None:
    """stage/{jdk,gradle,sdk} from the pinned archives, every one checked before any is unpacked."""
    pinned = archives(system, machine)
    files = {key: fetch(archive, downloads, log) for key, archive in pinned.items()}
    unpacked = stage / '.unpacked'
    unpacked.mkdir()
    for n, (key, file) in enumerate(files.items()):
        top = unpack(file, unpacked / str(n))
        if key == 'jdk':
            _install_jdk(top, stage / 'jdk')
        elif key == 'gradle':
            _install_gradle(top, stage / 'gradle')
        else:
            target = next(t for p, t, _ in gradle_stack.SDK_PACKAGES if p == key)
            install_sdk_package(key, top, stage / 'sdk', target)
    _remove(unpacked)


# --- the catalog project -----------------------------------------------------------------------------------------

def _settings(modules: list[str]) -> str:
    """The skeleton's repositories (gradle.skeleton), so the cache answers for exactly what a new project declares."""
    return ('pluginManagement {\n'
            '    repositories { google(); mavenCentral(); gradlePluginPortal() }\n'
            '}\n'
            'dependencyResolutionManagement {\n'
            '    repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS)\n'
            '    repositories { google(); mavenCentral() }\n'
            '}\n'
            'rootProject.name = "rose-catalog"\n'
            + ''.join(f'include(":{m}")\n' for m in modules))


def _dependency(coordinate: str, version) -> str:
    return f'    implementation("{coordinate}")\n' if version is None else \
        f'    implementation("{coordinate}:{version}")\n'


def _module(name: str, libraries: dict, *, serialization: bool, minify: bool = False, resolve: bool = False) -> str:
    """One application module's build file: the stack's plugins, ``libraries`` (the Compose BOM as a platform when
    any of them is managed by it), the skeleton's levels."""
    bom = 'androidx.compose:compose-bom'
    plugins = ['com.android.application', 'org.jetbrains.kotlin.plugin.compose']
    if serialization:
        plugins.append('org.jetbrains.kotlin.plugin.serialization')
    deps = ''
    if any(v is None for v in libraries.values()):
        deps += f'    implementation(platform("{bom}:{gradle_stack.LIBRARIES[bom]}"))\n'
    deps += ''.join(_dependency(c, v) for c, v in libraries.items() if c != bom)
    release = ('    buildTypes {\n        release {\n            isMinifyEnabled = true\n'
               '            isShrinkResources = true\n            proguardFiles(getDefaultProguardFile('
               '"proguard-android-optimize.txt"))\n        }\n    }\n') if minify else ''
    text = ('plugins {\n' + ''.join(f'    id("{p}")\n' for p in plugins) + '}\n\n'
            'android {\n'
            f'    namespace = "{CATALOG_PACKAGE}.{name}"\n'
            f'    compileSdk = {gradle_stack.COMPILE_SDK}\n'
            '    defaultConfig {\n'
            f'        applicationId = "{CATALOG_PACKAGE}.{name}"\n'
            f'        minSdk = {gradle_stack.MIN_SDK}\n'
            f'        targetSdk = {gradle_stack.TARGET_SDK}\n'
            '        versionCode = 1\n'
            '        versionName = "1"\n'
            '    }\n'
            '    buildFeatures { compose = true }\n'
            + release +
            '}\n\n'
            'dependencies {\n' + deps + '}\n')
    if resolve:  # downloads what this module's release build would compile and package against
        text += ('\ntasks.register("roseResolve") {\n'
                 '    val classpaths = configurations.matching {\n'
                 '        it.name == "releaseCompileClasspath" || it.name == "releaseRuntimeClasspath"\n'
                 '    }\n'
                 '    inputs.files(classpaths)\n'
                 '    doLast { println("roseResolve " + path + " " + inputs.files.files.size) }\n'
                 '}\n')
    return text


def _sources(name: str, label: str, *, serialization: bool) -> dict[str, str]:
    package = f'{CATALOG_PACKAGE}.{name}'
    imports = ('import kotlinx.serialization.Serializable\nimport kotlinx.serialization.json.Json\n'
               if serialization else '')
    note = ('@Serializable\ndata class Note(val text: String)\n\n' if serialization else '')
    text = ('Json.encodeToString(Note.serializer(), Note(getString(R.string.app_name)))' if serialization
            else 'getString(R.string.app_name)')
    return {
        f'{name}/src/main/AndroidManifest.xml': (
            '<?xml version="1.0" encoding="utf-8"?>\n'
            '<manifest xmlns:android="http://schemas.android.com/apk/res/android">\n'
            '  <application android:label="@string/app_name"\n'
            '      android:theme="@android:style/Theme.Material.Light.NoActionBar">\n'
            '    <activity android:name=".MainActivity" android:exported="true">\n'
            '      <intent-filter>\n'
            '        <action android:name="android.intent.action.MAIN"/>\n'
            '        <category android:name="android.intent.category.LAUNCHER"/>\n'
            '      </intent-filter>\n'
            '    </activity>\n'
            '  </application>\n'
            '</manifest>\n'),
        f'{name}/src/main/res/values/strings.xml': (
            '<?xml version="1.0" encoding="utf-8"?>\n'
            f'<resources><string name="app_name">{label}</string></resources>\n'),
        f'{name}/src/main/java/{package.replace(".", "/")}/MainActivity.kt': (
            f'package {package}\n\n'
            'import android.os.Bundle\n'
            'import androidx.activity.ComponentActivity\n'
            'import androidx.activity.compose.setContent\n'
            'import androidx.compose.material3.MaterialTheme\n'
            'import androidx.compose.material3.Surface\n'
            'import androidx.compose.material3.Text\n'
            + imports + '\n' + note +
            'class MainActivity : ComponentActivity() {\n'
            '    override fun onCreate(savedInstanceState: Bundle?) {\n'
            '        super.onCreate(savedInstanceState)\n'
            f'        val text = {text}\n'
            '        setContent { MaterialTheme { Surface { Text(text) } } }\n'
            '    }\n'
            '}\n'),
    }


# The skeleton's own libraries (rose.app_builder.gradle.skeleton): what every new project starts with.
BASE = ('androidx.compose:compose-bom', 'androidx.activity:activity-compose', 'androidx.compose.material3:material3',
        'androidx.compose.ui:ui')
SERIALIZATION = 'org.jetbrains.kotlinx:kotlinx-serialization-json'


def catalog() -> dict[str, str]:
    """The builder's catalog project (relative path -> text) the dependency cache is seeded from:

    - ``app``: every plugin and every library, release minified and resource-shrunk (R8), built;
    - ``base``: the skeleton's libraries, built as the skeleton is (not minified);
    - ``x<n>``: library n alone (with the BOM when it manages it), ``s<n>``: the skeleton's libraries and library n;
      their release compile and runtime classpaths are resolved (``roseResolve``), so the versions conflict
      resolution picks for any one library, alone or next to the skeleton's, are in the cache too;
    - ``lib``: a Compose Android library module (com.android.library), and ``multi``: an application on the skeleton's
      libraries that depends on it, built, as a project with its own library modules (Home's starters) builds."""
    libs = gradle_stack.LIBRARIES
    each = [c for c in libs if not c.endswith(':compose-bom')]
    base = {c: libs[c] for c in BASE}
    modules = {'app': _module('app', dict(libs), serialization=True, minify=True),
               'base': _module('base', base, serialization=False)}
    for n, coordinate in enumerate(each):
        uses = coordinate == SERIALIZATION
        modules[f'x{n}'] = _module(f'x{n}', {coordinate: libs[coordinate]}, serialization=uses, resolve=True)
        modules[f's{n}'] = _module(f's{n}', {**base, coordinate: libs[coordinate]}, serialization=uses, resolve=True)
    files = {
        'settings.gradle.kts': _settings(list(modules)),
        'build.gradle.kts': ('plugins {\n' + ''.join(f'    id("{p}") version "{v}" apply false\n'
                                                     for p, v in gradle_stack.PLUGINS.items()) + '}\n'),
        'gradle.properties': 'android.useAndroidX=true\nkotlin.code.style=official\n',
    }
    for name, text in modules.items():
        files[f'{name}/build.gradle.kts'] = text
    files.update(_sources('app', 'Rose catalog', serialization=True))
    files.update(_sources('base', 'Rose skeleton', serialization=False))
    files['settings.gradle.kts'] += 'include(":lib")\ninclude(":multi")\n'
    files['lib/build.gradle.kts'] = _library_module(base)
    files['lib/src/main/java/app/rose/catalog/lib/Greeting.kt'] = (
        'package app.rose.catalog.lib\n\n'
        'import androidx.compose.material3.Text\n'
        'import androidx.compose.runtime.Composable\n\n'
        '@Composable\n'
        'fun Greeting(text: String) = Text(text)\n')
    files['multi/build.gradle.kts'] = _module('multi', base, serialization=False).replace(
        'dependencies {\n', 'dependencies {\n    implementation(project(":lib"))\n', 1)
    files.update(_sources('multi', 'Rose modules', serialization=False))
    return files


def _library_module(libraries: dict) -> str:
    """An Android library module's build file with the Compose plugin and ``libraries`` (the BOM as a platform)."""
    bom = 'androidx.compose:compose-bom'
    deps = f'    implementation(platform("{bom}:{gradle_stack.LIBRARIES[bom]}"))\n'
    deps += ''.join(_dependency(c, v) for c, v in libraries.items() if c != bom)
    return ('plugins {\n    id("com.android.library")\n    id("org.jetbrains.kotlin.plugin.compose")\n}\n\n'
            'android {\n'
            f'    namespace = "{CATALOG_PACKAGE}.lib"\n'
            f'    compileSdk = {gradle_stack.COMPILE_SDK}\n'
            f'    defaultConfig {{ minSdk = {gradle_stack.MIN_SDK} }}\n'
            '    buildFeatures { compose = true }\n'
            '}\n\n'
            'dependencies {\n' + deps + '}\n')


TASKS = (':app:assembleRelease', ':base:assembleRelease', ':multi:assembleRelease', 'roseResolve')


def _write_tree(root: Path, files: dict[str, str]) -> None:
    for name, text in files.items():
        target = root.joinpath(*PurePosixPath(name).parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding='utf-8')


def _user_properties(jdk: Path, home: Path, tmp: Path) -> str:
    """GRADLE_USER_HOME/gradle.properties: the builder's own settings (rose.app_builder.gradle), so the seed resolves
    what a builder build resolves (the in-process Kotlin compiler, the stack's JDK, no toolchain download)."""
    return ''.join(f'{k}={v}\n' for k, v in (
        ('org.gradle.daemon', 'false'),
        ('org.gradle.java.installations.auto-download', 'false'),
        ('org.gradle.java.installations.auto-detect', 'false'),
        ('org.gradle.java.installations.paths', jdk),
        ('org.gradle.java.home', jdk),
        ('org.gradle.jvmargs', f'-Xmx3g -Djava.io.tmpdir={tmp} -Duser.home={home} -XX:-UsePerfData'),
        ('org.gradle.vfs.watch', 'false'),
        ('org.gradle.welcome', 'never'),
        ('kotlin.compiler.execution.strategy', 'in-process'),
        ('android.builder.sdkDownload', 'false')))


def run_catalog(stage: Path, work: Path, *, online: bool, log=sys.stderr) -> Path:
    """Build the catalog in ``work`` (a fresh folder) with the stack in ``stage``. Online: as the operator, with
    their network settings, filling work/gradle-home. Offline: ``--offline``, no network settings, a fresh Gradle
    home and stage/cache as the read-only dependency cache. Returns the Gradle home."""
    project, home, tmp, user = work / 'project', work / 'home', work / 'tmp', work / 'gradle-home'
    for d in (project, home, tmp, user):
        d.mkdir(parents=True, mode=0o700)
    jdk, sdk = stage / 'jdk', stage / 'sdk'
    _write_tree(project, catalog())
    (project / 'local.properties').write_text(f'sdk.dir={sdk}\n', encoding='utf-8')
    (user / 'gradle.properties').write_text(_user_properties(jdk, home, tmp), encoding='utf-8')
    env = {'PATH': f'{jdk}/bin:/usr/bin:/bin', 'HOME': str(home), 'TMPDIR': str(tmp), 'LANG': 'C.UTF-8',
           'JAVA_HOME': str(jdk), 'GRADLE_USER_HOME': str(user), 'ANDROID_HOME': str(sdk),
           'ANDROID_USER_HOME': str(home / '.android'),
           'GRADLE_OPTS': f'-Djava.io.tmpdir={tmp} -Duser.home={home} -XX:-UsePerfData'}
    argv = [str(stage / 'gradle' / 'bin' / 'gradle'), '--no-daemon', '--console=plain', '-p', str(project)]
    if online:
        env.update({k: os.environ[k] for k in NETWORK_ENV if k in os.environ})
    else:
        env['GRADLE_RO_DEP_CACHE'] = str(stage / 'cache')
        argv.append('--offline')
    argv += list(TASKS)
    what = 'seeding the dependency cache (online)' if online else 'proving the stack builds offline'
    print(f'gradle_prepare: {what}', file=log, flush=True)
    output = work / 'gradle.log'
    try:
        with open(output, 'wb') as out:
            done = subprocess.run(argv, env=env, cwd=project, stdin=subprocess.DEVNULL, stdout=out,
                                  stderr=subprocess.STDOUT, timeout=SEED_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        raise Refused(f'{what}: Gradle did not finish in {SEED_TIMEOUT_S:.0f} s') from None
    except OSError as exc:
        raise Refused(f'{what}: Gradle could not run ({type(exc).__name__}: {exc})') from None
    text = output.read_text(encoding='utf-8', errors='replace')
    if done.returncode != 0:
        tail = '\n'.join(text.splitlines()[-60:])
        raise Refused(f'{what}: the catalog build failed (exit {done.returncode}):\n{tail}')
    return user


# --- the cache, its digest and the stack record ------------------------------------------------------------------

def copy_cache(source: Path, target: Path) -> int:
    """Copy Gradle's caches/modules-2 to ``target`` without lock files and gc.properties, refusing links; returns
    the number of files copied."""
    if source.is_symlink() or not source.is_dir():
        raise Refused('the seed build left no dependency cache')
    count = 0
    target.mkdir(parents=True)
    for folder, dirs, files in os.walk(source, followlinks=False):
        rel = Path(folder).relative_to(source)
        for name in dirs:
            if (Path(folder) / name).is_symlink():
                raise Refused('the dependency cache holds a link')
            (target / rel / name).mkdir()
        for name in files:
            path = Path(folder) / name
            if name.endswith(CACHE_SKIP_SUFFIXES) or name in CACHE_SKIP_NAMES:
                continue
            if not stat.S_ISREG(path.lstat().st_mode):
                raise Refused('the dependency cache holds a link or special file')
            shutil.copyfile(path, target / rel / name, follow_symlinks=False)
            count += 1
    return count


def tree_digest(root: Path) -> str:
    """SHA-256 of sorted JSON-line [path, mode, content-sha256] records of every directory (content null) and file
    under ``root`` (the root is path ""); links and special files are refused, never followed."""
    root = Path(root)
    if root.is_symlink() or not root.is_dir():
        raise Refused(f'{root} is not a plain directory')
    records = []

    def error(exc):
        raise exc

    for folder, dirs, files in os.walk(root, followlinks=False, onerror=error):
        for path in [Path(folder), *(Path(folder) / n for n in files), *(Path(folder) / n for n in dirs
                                                                         if (Path(folder) / n).is_symlink())]:
            mode = path.lstat().st_mode
            if stat.S_ISREG(mode):
                content = sha256_file(path)
            elif stat.S_ISDIR(mode):
                content = None
            else:
                raise Refused(f'{root} holds a link or special file')
            records.append((path.relative_to(root).as_posix() if path != root else '', stat.S_IMODE(mode), content))
    digest = hashlib.sha256(b'rose-gradle-cache-v1\n')
    for record in sorted(records):
        digest.update((json.dumps(record, ensure_ascii=True, separators=(',', ':')) + '\n').encode('ascii'))
    return digest.hexdigest()


def record(system: str, machine: str) -> dict:
    """stack.json without its cacheSha256: what a stack made now from these pins for this host records."""
    return {'schemaVersion': SCHEMA_VERSION, 'os': system, 'arch': machine, 'gradle': gradle_stack.GRADLE,
            'agp': gradle_stack.AGP, 'kotlin': gradle_stack.KOTLIN, 'composeBom': gradle_stack.COMPOSE_BOM,
            'jdk': gradle_stack.JDKS[(system, machine)].name, 'jdkFeature': gradle_stack.JDK_FEATURE,
            'platform': gradle_stack.PLATFORM, 'buildTools': gradle_stack.BUILD_TOOLS,
            'sdkPackages': [p for p, _t, _a in gradle_stack.SDK_PACKAGES],
            'plugins': dict(gradle_stack.PLUGINS), 'libraries': dict(gradle_stack.LIBRARIES)}


def verify(out: Path, system: str | None = None, machine: str | None = None) -> dict:
    """The stack.json of the stack in ``out`` once it is the stack these pins make for this host and its cache is
    the one it records; Refused otherwise."""
    system, machine = (system, machine) if system else host()
    try:
        data = json.loads((Path(out) / 'stack.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        raise Refused(f'{out} holds no readable stack.json') from None
    if not isinstance(data, dict):
        raise Refused(f'{out}/stack.json is not a stack record')
    expected = record(system, machine)
    if {k: v for k, v in data.items() if k != 'cacheSha256'} != expected:
        raise Refused(f'{out} is a stack of other pins or another host')
    for part in PARTS:
        if Path(out, part).is_symlink() or not Path(out, part).is_dir():
            raise Refused(f'{out} has no {part}')
    if data.get('cacheSha256') != tree_digest(Path(out) / 'cache'):
        raise Refused(f'{out}/cache is not the cache stack.json records')
    return data


def config(out: Path, system: str | None = None) -> dict:
    """The builder's ``gradle`` config object (rose.app_builder.host) for the stack in ``out``."""
    out = Path(os.path.realpath(out))
    isolation = 'seatbelt' if (system or host()[0]) == 'darwin' else 'bubblewrap'
    return {'jdk': str(out / 'jdk'), 'distribution': str(out / 'gradle'), 'sdk': str(out / 'sdk'),
            'dependencyCache': str(out / 'cache'), 'isolation': isolation}


def _read_only(root: Path) -> None:
    """Everyone may read (and enter, and run what was executable); no one may write."""
    for folder, dirs, files in os.walk(root, topdown=False, followlinks=False):
        for name in files:
            path = Path(folder) / name
            if not path.is_symlink():
                os.chmod(path, 0o555 if path.stat().st_mode & 0o111 else 0o444)
        for name in dirs:
            if not (Path(folder) / name).is_symlink():
                os.chmod(Path(folder) / name, 0o555)
    os.chmod(root, 0o555)


def _remove(path: Path) -> None:
    """Remove a tree even where it was made read-only (directories are made writable first)."""
    if not os.path.lexists(path):
        return
    if path.is_symlink() or not path.is_dir():
        path.unlink()
        return
    for folder, dirs, _files in os.walk(path, followlinks=False):
        os.chmod(folder, 0o700)
        for name in dirs:
            if not (Path(folder) / name).is_symlink():
                os.chmod(Path(folder) / name, 0o700)
    shutil.rmtree(path)


# --- the command -------------------------------------------------------------------------------------------------

def stack(out: Path, downloads: Path | None = None, log=sys.stderr) -> dict:
    """Make (or keep) the stack in ``out``; returns {'prepared', 'stack', 'gradle'} (see the module docstring)."""
    system, machine = host()
    archives(system, machine)  # an unpinned host is refused before anything is fetched or written
    out = Path(out)
    if out.is_symlink() or (out.exists() and not out.is_dir()):
        raise Refused(f'{out} is not a directory')
    if out.exists() and any(out.iterdir()):
        try:
            return {'prepared': False, 'stack': verify(out, system, machine), 'gradle': config(out, system)}
        except Refused as exc:
            if not (out / 'stack.json').is_file():
                raise Refused(f'{out} is not empty and is not a Gradle stack: {exc}') from None
            print(f'gradle_prepare: replacing {out} ({exc})', file=log, flush=True)
    out.parent.mkdir(parents=True, exist_ok=True)
    parent = Path(os.path.realpath(out.parent))
    stage, work = parent / f'.{out.name}.partial-{os.getpid()}', parent / f'.{out.name}.seed-{os.getpid()}'
    for leftover in (stage, work):
        if os.path.lexists(leftover):
            raise Refused(f'{leftover} is left from an earlier run; remove it')
    stage.mkdir(mode=0o755)
    work.mkdir(mode=0o700)
    try:
        with contextlib.ExitStack() as scope:
            if downloads is None:
                downloads = Path(scope.enter_context(tempfile.TemporaryDirectory(dir=work)))
            install(stage, Path(downloads), system, machine, log)
        user = run_catalog(stage, work / 'seed', online=True, log=log)
        files = copy_cache(user / 'caches' / CACHE, stage / 'cache' / CACHE)
        _remove(work / 'seed')  # its Gradle home holds gigabytes of transforms; the offline proof needs none
        _read_only(stage / 'cache')
        data = {**record(system, machine), 'cacheSha256': tree_digest(stage / 'cache')}
        (stage / 'stack.json').write_text(json.dumps(data, indent=1, sort_keys=True) + '\n', encoding='utf-8')
        _read_only(stage)
        run_catalog(stage, work / 'offline', online=False, log=log)
        if tree_digest(stage / 'cache') != data['cacheSha256']:
            raise Refused('the offline build changed the read-only dependency cache')
        old = parent / f'.{out.name}.old-{os.getpid()}'
        if out.exists():
            if any(out.iterdir()):
                os.chmod(out, 0o755)
                out.rename(old)
            else:
                out.rmdir()
        os.chmod(stage, 0o755)
        stage.rename(out)
        os.chmod(out, 0o555)
        _remove(old)
    finally:
        _remove(stage)
        _remove(work)
    print(f'gradle_prepare: {files} cache files', file=log, flush=True)
    return {'prepared': True, 'stack': data, 'gradle': config(out, system)}


def measure(downloads: Path | None = None, log=sys.stderr) -> dict:
    """SHA-256 of each macOS archive gradle_stack.py does not pin yet, checked against Google's manifest record."""
    measured = {}
    with contextlib.ExitStack() as scope:
        if downloads is None:
            downloads = Path(scope.enter_context(tempfile.TemporaryDirectory()))
        downloads = Path(downloads)
        downloads.mkdir(parents=True, exist_ok=True)
        for path, _target, per_os in gradle_stack.SDK_PACKAGES:
            if per_os.get('macosx') is not None:
                continue
            if path not in MEASURE:
                raise Refused(f'gradle_prepare has no manifest record of the macOS archive of {path}')
            name, size, sha1 = MEASURE[path]
            file = downloads / name
            if not file.exists():
                print(f'gradle_prepare: downloading {name}', file=log, flush=True)
                partial = downloads / f'.{name}.partial-{os.getpid()}'
                try:
                    _download(gradle_stack._SDK + name, partial)
                    os.replace(partial, file)
                except OSError as exc:
                    raise Refused(f'{name} could not be downloaded ({type(exc).__name__}: {exc})') from None
                finally:
                    with contextlib.suppress(FileNotFoundError):
                        partial.unlink()
            digest = hashlib.sha1(usedforsecurity=False)
            with open(file, 'rb') as stream:
                for block in iter(lambda: stream.read(1 << 20), b''):
                    digest.update(block)
            if file.stat().st_size != size or digest.hexdigest() != sha1:
                raise Refused(f'{file} is not the archive Google\'s manifest records (size or SHA-1 differs)')
            measured[path] = {'name': name, 'url': gradle_stack._SDK + name, 'sha256': sha256_file(file)}
    return {'measured': measured}


def main(argv=None, out=sys.stdout) -> int:
    parser = argparse.ArgumentParser(prog='python -m rose.app_builder.gradle_prepare')
    sub = parser.add_subparsers(dest='command', required=True)
    s = sub.add_parser('stack')
    s.add_argument('--out', type=Path)
    s.add_argument('--downloads', type=Path)
    s.add_argument('--measure', action='store_true')
    m = sub.add_parser('measure')
    m.add_argument('--downloads', type=Path)
    args = parser.parse_args(argv)
    if args.command == 'stack' and not args.measure and args.out is None:
        parser.error('stack needs --out')
    try:
        if args.command == 'measure' or args.measure:
            result = measure(args.downloads, log=sys.stderr)
        else:
            result = stack(args.out, args.downloads, log=sys.stderr)
    except Refused as exc:
        print(json.dumps({'status': 'refused', 'message': str(exc)}), file=out)
        return 1
    except OSError as exc:  # a disk, permission or path error: nothing was moved into place
        print(json.dumps({'status': 'refused', 'message': f'{type(exc).__name__}: {exc}'}), file=out)
        return 1
    print(json.dumps({'status': 'ok', **result}, sort_keys=True), file=out)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
