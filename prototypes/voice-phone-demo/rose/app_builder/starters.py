"""A new app's first tree from a Rose starter: one template (behaviour and state) under one theme (appearance and
stock controls), from workspace/rose-starters.

The builder writes a new project's tree once, before the first writer turn, through its skeleton hook
(service.Builder(skeleton=...)). With a starter ``{"template": id, "theme": id}`` this writes an ordinary Gradle
project:
  - app/, whose MainActivity is ``RoseTheme(<Theme>) { <Template>.App() }``;
  - the vendored framework modules (:design, :design-testing, :examples), the chosen template and the chosen theme;
  - the starters' version catalog.
Without a starter it is the builder's plain skeleton, unchanged. The ids and their Kotlin objects come from
workspace/rose-starters/starters.json.

Only source is vendored: build output, Gradle and IDE state, local.properties and *.iml are left behind, and a link
or special file in the starters is refused, so the tree meets the builder's own checks (rose.app_builder.gradle.check).
Every version comes from the vendored catalog (gradle/libs.versions.toml), the same file the starters build and test
with, so a generated app builds exactly what was tested.
"""
from __future__ import annotations

import json
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path

from . import template

# workspace/rose-starters in this checkout: prototypes/voice-phone-demo/rose/app_builder/starters.py → repo root.
ROOT = Path(__file__).resolve().parents[4] / 'workspace' / 'rose-starters'
MANIFEST = 'starters.json'
APP_MODULE = 'app'
ID = re.compile(r'[a-z][a-z0-9-]{0,39}')
# Never vendored: build output, Gradle/Kotlin/IDE state, the machine's SDK path.
SKIP_DIRS = frozenset({'build', '.gradle', '.kotlin', '.idea', '.cxx'})
SKIP_FILES = frozenset({'local.properties', '.DS_Store'})
SKIP_SUFFIXES = ('.iml',)
KOTLIN_KEYWORDS = frozenset({'as', 'break', 'class', 'continue', 'do', 'else', 'false', 'for', 'fun', 'if', 'in',
                             'interface', 'is', 'null', 'object', 'package', 'return', 'super', 'this', 'throw', 'true',
                             'try', 'typealias', 'typeof', 'val', 'var', 'when', 'while'})


class UnknownStarter(ValueError):
    """The request named a template or theme the starters do not have (the builder's refusal word: unknown_starter)."""


@dataclass(frozen=True)
class Entry:
    id: str
    dir: str  # relative to the starters root, e.g. templates/feed
    object: str  # fully qualified Kotlin object, e.g. app.rose.template.feed.FeedTemplate

    @property
    def module(self) -> str:
        return ':' + self.dir.replace('/', ':')

    @property
    def simple(self) -> str:
        return self.object.rsplit('.', 1)[1]


@dataclass(frozen=True)
class Starters:
    root: Path
    shared: tuple[str, ...]
    templates: dict
    themes: dict

    @classmethod
    def load(cls, root: Path = ROOT) -> 'Starters':
        root = Path(root)
        data = json.loads((root / MANIFEST).read_text(encoding='utf-8'))
        if data.get('schemaVersion') != 1:
            raise ValueError(f'{MANIFEST}: unsupported schemaVersion')

        def entries(kind: str) -> dict:
            out = {}
            for key, value in data[kind].items():
                if not ID.fullmatch(key):
                    raise ValueError(f'{MANIFEST}: {kind} id {key!r} is not a lowercase id')
                out[key] = Entry(key, _relative(value['dir']), value['object'])
            return out

        return cls(root, tuple(_relative(d) for d in data['shared']), entries('templates'), entries('themes'))

    def resolve(self, starter) -> tuple[Entry, Entry]:
        """The template and theme a create request named; UnknownStarter for anything else."""
        if not isinstance(starter, dict) or set(starter) != {'template', 'theme'}:
            raise UnknownStarter('A starter names exactly one template and one theme.')
        t, h = starter['template'], starter['theme']
        if not isinstance(t, str) or t not in self.templates:
            raise UnknownStarter(f'There is no starter template {t!r}.')
        if not isinstance(h, str) or h not in self.themes:
            raise UnknownStarter(f'There is no starter theme {h!r}.')
        return self.templates[t], self.themes[h]

    def files(self, package: str, label: str, starter) -> dict[str, bytes]:
        """The new project's files (relative path -> bytes) for this starter."""
        tmpl, theme = self.resolve(starter)
        out: dict[str, bytes] = {}
        for folder in (*self.shared, tmpl.dir, theme.dir):
            out.update(_source(self.root, folder))
        out['gradle/libs.versions.toml'] = (self.root / 'gradle/libs.versions.toml').read_bytes()
        modules = [APP_MODULE, *self.shared, tmpl.dir, theme.dir]
        for name, text in _app(package, label, tmpl, theme, modules).items():
            out[name] = text.encode('utf-8')
        return out


def skeleton(source: Path, package: str, label: str, starter=None, *, root: Path = ROOT) -> None:
    """The builder's skeleton hook: the starter's tree when one is named, else the builder's plain skeleton."""
    if starter is None:
        from . import gradle  # the builder's own Gradle skeleton (rose.app_builder.gradle)
        gradle.write_skeleton(source, package, label)
        return
    for name, data in Starters.load(root).files(package, label, starter).items():
        target = Path(source) / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)


def outside_template(source: Path, starter, *, root: Path = ROOT) -> list[str]:
    """What a writer changed that a starter app must keep as vendored: the shared framework modules, the theme, the
    version catalog and app/'s wiring (MainActivity, build files, manifest). Paths (relative to ``source``) that were
    changed, added or removed there, sorted; empty when only the template and app/src/main/res changed. The builder
    may refuse or repair a turn that returns any, so the theme stays swappable and the template stays the only
    variable."""
    starters = Starters.load(root)
    tmpl, theme = starters.resolve(starter)
    expected = starters.files(_package_of(source), _label_of(source), starter)
    guarded = (*(f + '/' for f in starters.shared), theme.dir + '/')
    fixed = {'gradle/libs.versions.toml', 'settings.gradle.kts', 'build.gradle.kts', f'{APP_MODULE}/build.gradle.kts',
             f'{APP_MODULE}/src/main/AndroidManifest.xml'}
    source = Path(source)

    def watched(name: str) -> bool:
        return name in fixed or name.startswith(guarded) or (
            name.startswith(f'{APP_MODULE}/src/main/java/') and name.endswith('/MainActivity.kt'))

    actual = {}
    for path in source.rglob('*'):
        name = path.relative_to(source).as_posix()
        if path.is_file() and not path.is_symlink() and watched(name) and not (set(Path(name).parts) & SKIP_DIRS):
            actual[name] = path.read_bytes()
    want = {n: d for n, d in expected.items() if watched(n)}
    return sorted(n for n in set(actual) | set(want) if actual.get(n) != want.get(n))


def _package_of(source: Path) -> str:
    """The package the tree was made for: app/build.gradle.kts's namespace."""
    text = (Path(source) / APP_MODULE / 'build.gradle.kts').read_text(encoding='utf-8')
    found = re.search(r'namespace = "([^"]+)"', text)
    if not found:
        raise ValueError('app/build.gradle.kts has no namespace; it is not a starter tree.')
    return found.group(1)


def _label_of(source: Path) -> str:
    return ''  # strings.xml is the writer's to change; the label only reaches files outside_template ignores


def available(root: Path = ROOT) -> bool:
    """Whether this checkout has the starters (the host wires the hook only then)."""
    return (Path(root) / MANIFEST).is_file()


def _relative(path: str) -> str:
    p = Path(path)
    if p.is_absolute() or '..' in p.parts or not p.parts:
        raise ValueError(f'{MANIFEST}: {path!r} must be a relative folder inside the starters')
    return p.as_posix()


def _source(root: Path, folder: str) -> dict[str, bytes]:
    """Every source file under root/folder, keyed by its path relative to root. Refuses links and special files."""
    out = {}
    base = root / folder
    if not stat.S_ISDIR(os.lstat(base).st_mode):
        raise ValueError(f'The starters have no folder {folder}.')
    for current, dirs, files in os.walk(base):
        here = Path(current)
        kept = []
        for d in sorted(dirs):
            mode = os.lstat(here / d).st_mode
            if stat.S_ISLNK(mode):
                raise ValueError(f'{(here / d).relative_to(root)} is a link; starters are vendored as plain files.')
            if d not in SKIP_DIRS:
                kept.append(d)
        dirs[:] = kept
        for f in sorted(files):
            path = here / f
            mode = os.lstat(path).st_mode
            if not stat.S_ISREG(mode):
                raise ValueError(f'{path.relative_to(root)} is not a plain file; starters are vendored as plain files.')
            if f in SKIP_FILES or f.endswith(SKIP_SUFFIXES):
                continue
            out[path.relative_to(root).as_posix()] = path.read_bytes()
    return out


def _kotlin_package(package: str) -> str:
    return '.'.join(f'`{p}`' if p in KOTLIN_KEYWORDS else p for p in package.split('.'))


def _app(package: str, label: str, tmpl: Entry, theme: Entry, modules: list[str]) -> dict[str, str]:
    includes = ', '.join(f'":{m.replace("/", ":")}"' for m in modules)
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
            '// The app, the Rose starters framework, and the one template and one theme it starts from.\n'
            f'include({includes})\n'),
        'build.gradle.kts': (
            'plugins {\n'
            '    alias(libs.plugins.android.application) apply false\n'
            '    alias(libs.plugins.android.library) apply false\n'
            '    alias(libs.plugins.kotlin.compose) apply false\n'
            '}\n'),
        'gradle.properties': (
            'android.useAndroidX=true\n'
            'android.nonTransitiveRClass=true\n'
            'kotlin.code.style=official\n'
            'org.gradle.jvmargs=-Xmx2g -Dfile.encoding=UTF-8\n'),
        f'{APP_MODULE}/build.gradle.kts': (
            'plugins {\n'
            '    alias(libs.plugins.android.application)\n'
            '    alias(libs.plugins.kotlin.compose)\n'
            '}\n\n'
            'android {\n'
            f'    namespace = "{package}"\n'
            '    compileSdk = libs.versions.compileSdk.get().toInt()\n'
            '    defaultConfig {\n'
            f'        applicationId = "{package}"\n'
            '        minSdk = libs.versions.minSdk.get().toInt()\n'
            '        targetSdk = libs.versions.targetSdk.get().toInt()\n'
            '        versionCode = 1  // the builder sets the real one\n'
            '        versionName = "1"\n'
            '    }\n'
            '    compileOptions {\n'
            '        sourceCompatibility = JavaVersion.VERSION_17\n'
            '        targetCompatibility = JavaVersion.VERSION_17\n'
            '    }\n'
            '    buildFeatures { compose = true }\n'
            '}\n\n'
            'dependencies {\n'
            '    implementation(project(":design"))\n'
            f'    implementation(project("{tmpl.module}"))\n'
            f'    implementation(project("{theme.module}"))\n'
            '    implementation(libs.activity.compose)\n'
            '}\n'),
        f'{APP_MODULE}/src/main/AndroidManifest.xml': (
            '<?xml version="1.0" encoding="utf-8"?>\n'
            '<manifest xmlns:android="http://schemas.android.com/apk/res/android">\n'
            '  <application android:label="@string/app_name" android:allowBackup="true" android:supportsRtl="true"\n'
            '      android:theme="@android:style/Theme.Material.Light.NoActionBar">\n'
            '    <activity android:name=".MainActivity" android:exported="true" android:configChanges="uiMode">\n'
            '      <intent-filter>\n'
            '        <action android:name="android.intent.action.MAIN"/>\n'
            '        <category android:name="android.intent.category.LAUNCHER"/>\n'
            '      </intent-filter>\n'
            '    </activity>\n'
            '  </application>\n'
            '</manifest>\n'),
        f'{APP_MODULE}/src/main/res/values/strings.xml': (
            '<?xml version="1.0" encoding="utf-8"?>\n'
            '<resources><string name="app_name" formatted="false">'
            f'{template.android_string(label)}</string></resources>\n'),
        f'{APP_MODULE}/src/main/java/{package.replace(".", "/")}/MainActivity.kt': (
            f'package {_kotlin_package(package)}\n\n'
            'import android.os.Bundle\n'
            'import androidx.activity.ComponentActivity\n'
            'import androidx.activity.compose.setContent\n'
            'import androidx.activity.enableEdgeToEdge\n'
            'import app.rose.design.RoseTheme\n'
            f'import {tmpl.object}\n'
            f'import {theme.object}\n\n'
            '/** Started from the Rose starters: the template owns behaviour and state, the theme its look. */\n'
            'class MainActivity : ComponentActivity() {\n'
            '    override fun onCreate(savedInstanceState: Bundle?) {\n'
            '        super.onCreate(savedInstanceState)\n'
            '        enableEdgeToEdge()\n'
            '        setContent {\n'
            f'            RoseTheme({theme.simple}) {{\n'
            f'                {tmpl.simple}.App()\n'
            '            }\n'
            '        }\n'
            '    }\n'
            '}\n'),
    }
