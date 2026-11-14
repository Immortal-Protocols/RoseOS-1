"""The skeleton a generated app is written into, and the checks that keep its identity out of generated code's reach.

The toolchain is the repository's pinned one (aapt v1, javac at the Java 8 source level, d8), so a generated app uses
framework APIs only: no Gradle, AndroidX, Kotlin or third-party libraries. The builder owns the package name,
versionCode, versionName and SDK levels. Generated code may change activities, resources and Java sources, but a
manifest that sets or changes any of those, asks for a shared user id or debuggability, or a source tree with build
scripts or native code is refused before anything is compiled.
"""
from __future__ import annotations

import os
import re
import stat
import xml.etree.ElementTree as ET
from pathlib import Path

ANDROID = '{http://schemas.android.com/apk/res/android}'
MIN_SDK, TARGET_SDK = 26, 35
ALLOWED_TOP = {'AndroidManifest.xml', 'src', 'res', 'assets', 'README.md'}
# git metadata is the builder's, kept outside the generated tree; none of it may appear inside.
FORBIDDEN_NAMES = {'.git', '.gitignore', '.gitattributes', '.gitmodules'}
MAX_FILE = 4 * 1024 * 1024


def git_name(name: str) -> bool:
    """A name git treats as its own: .git and friends in any case, with trailing dots or spaces, or an NTFS short
    name such as git~1 (git refuses all of these as paths)."""
    clean = name.lower().rstrip('. ')
    return clean in FORBIDDEN_NAMES or bool(re.fullmatch(r'git~\d+', clean))
FORBIDDEN_SUFFIXES = ('.gradle', '.kts', '.kt', '.so', '.jar', '.aar', '.sh', '.py', '.mk', '.cmake', '.c', '.cpp',
                      '.h', '.jks', '.keystore', '.p12', '.pk8', '.apk', '.dex')
OWNED_ATTRIBUTES = ('versionCode', 'versionCodeMajor', 'versionName', 'sharedUserId', 'sharedUserLabel',
                    'isSplitRequired', 'isFeatureSplit', 'requiredSplitTypes', 'splitTypes', 'compileSdkVersion',
                    'compileSdkVersionCodename')
ROOT_ATTRIBUTES = {'package'}  # unprefixed attributes allowed on <manifest>; `split` and the like are not
# Manifest elements a generated app may declare. Anything else (key-sets and upgrade-key-set, original-package,
# adopt-permissions, uses-sdk, instrumentation, ...) could change how Android identifies, updates or trusts the app.
MANIFEST_CHILDREN = {'application', 'uses-permission', 'uses-feature', 'queries', 'supports-screens',
                     'uses-configuration', 'compatible-screens', 'permission', 'permission-group'}
DEPENDENCY_SUFFIXES = ('.gradle', '.kts', '.kt', '.jar', '.aar', '.so')


class TemplateRefused(ValueError):
    """Generated source the builder will not compile; the summary is safe to show the owner. ``dependency`` marks a
    refusal caused by a library or language the framework-only toolchain does not have."""

    def __init__(self, message: str, dependency: bool = False):
        super().__init__(message)
        self.dependency = dependency


def android_string(text: str) -> str:
    """Text as an aapt string resource: XML-escaped, with aapt's own escapes for quotes, backslashes and a
    leading @ or ? (which would otherwise be read as a resource reference)."""
    text = ''.join(c for c in text if c in '\t\n\r' or '\x20' <= c <= '\ud7ff' or '\ue000' <= c <= '\ufffd'
                   or c >= '\U00010000')  # only characters XML 1.0 allows
    text = text.replace('\\', '\\\\').replace("'", "\\'").replace('"', '\\"')
    if text[:1] in ('@', '?'):
        text = '\\' + text
    return text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def skeleton(package: str, label: str) -> dict[str, str]:
    """Files of a new project: one launcher activity and one string, framework APIs only."""
    path = package.replace('.', '/')
    label = android_string(label)
    return {
        'AndroidManifest.xml': (
            '<?xml version="1.0" encoding="utf-8"?>\n'
            f'<manifest xmlns:android="http://schemas.android.com/apk/res/android" package="{package}">\n'
            '  <application android:label="@string/app_name" android:allowBackup="true">\n'
            '    <activity android:name=".MainActivity" android:exported="true">\n'
            '      <intent-filter>\n'
            '        <action android:name="android.intent.action.MAIN"/>\n'
            '        <category android:name="android.intent.category.LAUNCHER"/>\n'
            '      </intent-filter>\n'
            '    </activity>\n'
            '  </application>\n'
            '</manifest>\n'),
        'res/values/strings.xml': (
            '<?xml version="1.0" encoding="utf-8"?>\n'
            f'<resources><string name="app_name" formatted="false">{label}</string></resources>\n'),
        f'src/{path}/MainActivity.java': (
            f'package {package};\n\n'
            'import android.app.Activity;\nimport android.os.Bundle;\nimport android.widget.TextView;\n\n'
            'public final class MainActivity extends Activity {\n'
            '    @Override protected void onCreate(Bundle state) {\n'
            '        super.onCreate(state);\n'
            '        TextView text = new TextView(this);\n'
            '        text.setText(R.string.app_name);\n'
            '        setContentView(text);\n'
            '    }\n'
            '}\n'),
    }


def unicode_escapes(text: str) -> str:
    """Java source after javac's first step: \\uXXXX escapes (with any number of u's) become their characters."""
    return re.sub(r'(?<!\\)((?:\\\\)*)\\u+([0-9a-fA-F]{4})',
                  lambda m: m.group(1) + chr(int(m.group(2), 16)), text)


def write_skeleton(source: Path, package: str, label: str):
    for name, text in skeleton(package, label).items():
        target = source / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding='utf-8')


def check(source: Path, package: str) -> None:
    """Refuse generated source that could change the app's identity or escape the framework-only toolchain."""
    source = Path(source)
    if os.lstat(source).st_mode & 0o700 != 0o700:
        raise TemplateRefused('The app\'s source folder is not readable and writable by the builder.')
    for entry in source.iterdir():
        if entry.name not in ALLOWED_TOP:
            raise TemplateRefused(f'Unexpected top-level entry {entry.name!r}; only the app skeleton is built.')
    for path in source.rglob('*'):
        mode = os.lstat(path).st_mode  # never follow or open anything that is not a plain file or directory
        try:
            str(path.relative_to(source)).encode('utf-8')
        except UnicodeEncodeError:
            raise TemplateRefused(f'A file name in the app is not valid UTF-8 ({str(path.name)!r}).') from None
        name = path.relative_to(source)
        if git_name(path.name):
            raise TemplateRefused(f'{name} is not allowed; the builder keeps the project history itself.')
        if stat.S_ISDIR(mode) and mode & 0o700 != 0o700 or stat.S_ISREG(mode) and mode & 0o600 != 0o600:
            raise TemplateRefused(f'{name} is not readable and writable by the builder; it is not built.')
        if stat.S_ISLNK(mode):
            raise TemplateRefused('Source contains a symbolic link; it is not built.')
        if not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
            raise TemplateRefused(f'{name} is not a regular file; it is not built.')
        if stat.S_ISREG(mode) and os.lstat(path).st_nlink > 1:
            raise TemplateRefused(f'{name} is a hard link; it is not built.')
        if stat.S_ISREG(mode) and os.lstat(path).st_size > MAX_FILE:
            raise TemplateRefused(f'{name} is larger than {MAX_FILE // (1024 * 1024)} MB.')
        if path.name.lower().endswith(('.java',) + FORBIDDEN_SUFFIXES) and not stat.S_ISREG(mode):
            raise TemplateRefused(f'{name} is a directory with a source file name; it is not built.')
        if stat.S_ISREG(mode) and path.name.lower().endswith(FORBIDDEN_SUFFIXES):
            raise TemplateRefused(f'{name} is not supported: generated apps use framework '
                                  'Java only, with no build scripts, libraries or native code.',
                                  dependency=path.name.lower().endswith(DEPENDENCY_SUFFIXES))
    manifest = source / 'AndroidManifest.xml'
    if not manifest.is_file():
        raise TemplateRefused('AndroidManifest.xml is missing.')
    try:
        root = ET.parse(manifest).getroot()
    except ET.ParseError:
        raise TemplateRefused('AndroidManifest.xml is not valid XML.') from None
    for element in root.iter():
        for key in element.attrib:
            if key.startswith('{') and not key.startswith(ANDROID):
                raise TemplateRefused('The manifest may use only the android namespace.')
    for key in root.attrib:
        if key not in ROOT_ATTRIBUTES:  # android:* too: targetSandboxVersion, installLocation, ... are not the app's
            raise TemplateRefused(f'The manifest attribute {key.replace(ANDROID, "android:")!r} is not allowed.')
    if root.tag != 'manifest' or root.get('package') != package:
        raise TemplateRefused('The manifest package must stay ' + package + '.')
    for name in OWNED_ATTRIBUTES:
        if root.get(ANDROID + name) is not None:
            raise TemplateRefused(f'android:{name} is set by the builder, not the app.')
    if root.find('uses-sdk') is not None:
        raise TemplateRefused('SDK levels are set by the builder, not the app.')
    for child in root:
        if child.tag not in MANIFEST_CHILDREN:
            raise TemplateRefused(f'<{child.tag}> is not allowed in a generated app\'s manifest.')
    application = root.find('application')
    if application is not None and application.get(ANDROID + 'debuggable') is not None:
        raise TemplateRefused('android:debuggable is not allowed.')
    for java in source.glob('src/**/*.java'):
        text = unicode_escapes(java.read_text(encoding='utf-8', errors='replace'))  # as javac reads it
        code = re.sub(r'/\*.*?\*/|//[^\n]*', ' ', text, flags=re.S)  # comments cannot hide the declaration
        match = re.match(r'\s*package\s+([\w.]+)\s*;', code)
        if not match or not (match.group(1) == package or match.group(1).startswith(package + '.')):
            raise TemplateRefused(f'{java.relative_to(source)} must be in package {package}.')
