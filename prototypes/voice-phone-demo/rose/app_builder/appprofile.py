"""Backend declarations. New work uses client-backend-v1 in the existing agent container.
client-server-v1 is parsed only to retain and migrate delivered source. Backend files are opaque to the Android
builder; the author owns their language, dependencies and build files. Client-only apps need no declaration.
"""
from __future__ import annotations

import json
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path

from . import template

FILE = 'rose-app.json'
APP_V1, CLIENT_SERVER_V1 = 'app-v1', 'client-server-v1'
# §7.7a: the building agent sets the backend up in the app's own agent container, in a stack it chooses; backend/
# keeps its source with the project (any language, any files), and the builder neither compiles nor runs it.
CLIENT_BACKEND_V1 = 'client-backend-v1'
BACKEND_TOP = frozenset({FILE, 'contract', 'backend'})
MAX_FILE = 16 * 1024
INTERNET = 'android.permission.INTERNET'


@dataclass(frozen=True)
class Profile:
    name: str


class ProfileRefused(template.TemplateRefused):
    """A refused project; `reason` is a stable token for the job's failure, the message is safe to show."""

    def __init__(self, message: str, reason: str, dependency: bool = False):
        super().__init__(message, dependency=dependency)
        self.reason = reason


def _no_duplicates(pairs):
    keys = [k for k, _ in pairs]
    if len(keys) != len(set(keys)):
        raise ValueError('duplicate key')
    return dict(pairs)


def read(snapshot: Path) -> Profile:
    """The declared profile: app-v1 when there is no rose-app.json."""
    path = Path(snapshot) / FILE
    try:
        mode = os.lstat(path).st_mode
    except FileNotFoundError:
        return Profile(APP_V1)

    def invalid(why: str):
        return ProfileRefused(f'{FILE} is not valid: {why}.', 'profile_invalid')

    if not stat.S_ISREG(mode):
        raise invalid('it must be a plain file')
    if os.lstat(path).st_size > MAX_FILE:
        raise invalid(f'it is larger than {MAX_FILE // 1024} KB')
    try:
        data = json.loads(path.read_bytes().decode('utf-8'), object_pairs_hook=_no_duplicates)
    except (UnicodeDecodeError, ValueError):
        raise invalid('it must be UTF-8 JSON with no repeated keys') from None
    if not isinstance(data, dict) or set(data) - {'schemaVersion', 'profile', 'server'}:
        raise invalid('only schemaVersion, profile and server are allowed')
    if type(data.get('schemaVersion')) is not int or data['schemaVersion'] != 1:
        raise invalid('schemaVersion must be 1')
    if data.get('profile') == CLIENT_BACKEND_V1:
        if set(data) - {'schemaVersion', 'profile'}:
            raise invalid(f'a {CLIENT_BACKEND_V1} app declares only schemaVersion and profile (its backend is its own)')
        return Profile(CLIENT_BACKEND_V1)
    if data.get('profile') != CLIENT_SERVER_V1:
        raise invalid(f'profile must be "{CLIENT_BACKEND_V1}" or "{CLIENT_SERVER_V1}" (a plain app has no {FILE})')
    # Legacy declaration is retained verbatim during migration, never interpreted as a new build recipe.
    return Profile(CLIENT_SERVER_V1)


def check(snapshot: Path, package: str, bound: str | None = None) -> Profile:
    """Check the snapshot against its declared profile. `bound` is the profile a delivered version already has."""
    snapshot = Path(snapshot)
    profile = read(snapshot)
    if bound in (CLIENT_SERVER_V1, CLIENT_BACKEND_V1) and profile.name == APP_V1:
        raise ProfileRefused(f'This app has a server, and its users\' data lives there; {FILE} must stay.',
                             'profile_dropped')
    if bound in (CLIENT_SERVER_V1, CLIENT_BACKEND_V1) and profile.name != bound \
            and not (bound == CLIENT_SERVER_V1 and profile.name == CLIENT_BACKEND_V1):
        raise ProfileRefused(f'This app\'s backend is {bound}; it stays that kind.', 'profile_changed')
    from . import gradle  # an ordinary Gradle project (settings.gradle(.kts) at the top), or the older flat layout
    layout = gradle.is_gradle_project(snapshot)
    if profile.name == APP_V1:
        if layout:
            gradle.check(snapshot)
            _reserved(snapshot, package, gradle=True)
        else:
            template.check(snapshot, package)
        return profile
    if profile.name == CLIENT_BACKEND_V1:
        try:  # links, modes and sizes cover backend/ and contract/ too
            if layout:
                gradle.check(snapshot, extra_top=BACKEND_TOP, opaque_top=frozenset({'backend'}))
            else:
                template.check(snapshot, package, extra_top=BACKEND_TOP, opaque_top=frozenset({'backend'}))
        except template.TemplateRefused as refused:
            raise ProfileRefused(str(refused), 'template_refused', dependency=refused.dependency) from None
        contract = snapshot / 'contract'
        if not contract.is_dir() or contract.is_symlink():
            raise ProfileRefused('An app with a backend needs a contract/ folder (the API its client is built from).',
                                 'layout_invalid')
        backend = snapshot / 'backend'
        if os.path.lexists(backend) and (backend.is_symlink() or not backend.is_dir()):
            raise ProfileRefused('backend/ must be a folder.', 'layout_invalid')
        _reserved(snapshot, package, gradle=layout)
        _internet(snapshot, gradle=layout)
        return profile
    raise ProfileRefused('The builder-managed Rust backend is retired. Revise a delivered app through its '
                         'migration to client-backend-v1; new backends use the existing Chariot agent container.',
                         'legacy_backend')


def client_root(gradle: bool = False) -> str:
    """Where the builder writes a client's generated code: the source root of the older layout, or the Gradle
    application module's Java source set."""
    from .gradle_stack import APP_MODULE
    return f'{APP_MODULE}/src/main/java/' if gradle else 'src/'


def _reserved(snapshot: Path, package: str, gradle: bool = False):
    """The client's `<package>.rose` package is the builder's sign-in and API code; the writer cannot add to it,
    by its folder or by a package clause written anywhere else (in a Gradle project: any Java or Kotlin file)."""
    from .gradle_stack import APP_MODULE
    roots = ([f'{APP_MODULE}/src/main/java/', f'{APP_MODULE}/src/main/kotlin/'] if gradle else ['src/'])
    for root in roots:
        reserved = snapshot / root / Path(*package.split('.')) / 'rose'
        if os.path.lexists(reserved):
            raise ProfileRefused(f'{reserved.relative_to(snapshot)} is written by the builder (sign-in and the API '
                                 'client).', 'reserved_path')
        top = snapshot / root.rstrip('/')
        for folder in reversed(reserved.parents):  # the folders the builder writes it in: a folder, or not there yet
            if folder.is_relative_to(top) and os.path.lexists(folder) \
                    and not stat.S_ISDIR(os.lstat(folder).st_mode):
                raise ProfileRefused(f'{folder.relative_to(snapshot)} is not a folder; the builder writes its sign-in '
                                     f'and API client in {reserved.relative_to(snapshot)}.', 'reserved_path')
    files = (sorted(p for p in snapshot.rglob('*') if p.suffix in ('.java', '.kt') and p.is_file()) if gradle
             else snapshot.glob('src/**/*.java'))
    for source in files:
        text = template.unicode_escapes(source.read_text(encoding='utf-8', errors='replace'))
        code = re.sub(r'/\*.*?\*/|//[^\n]*', ' ', text, flags=re.S)
        # Java ends the clause with ';'; Kotlin with a line end (or ';'), and may quote a name part in backticks.
        match = re.match(r'\s*(?:@[\w.:]+(?:\([^)]*\))?\s*)*package\s+([\w.`]+)\s*(?:;|\n|$)', code)
        declared = match.group(1).replace('`', '') if match else None
        if declared and (declared == package + '.rose' or declared.startswith(package + '.rose.')):
            raise ProfileRefused(f'{source.relative_to(snapshot)} is in {package}.rose, which the builder writes.',
                                 'reserved_path')


def _internet(snapshot: Path, gradle: bool = False):
    import xml.etree.ElementTree as ET
    from .gradle_stack import APP_MODULE
    manifest = snapshot / (f'{APP_MODULE}/src/main/AndroidManifest.xml' if gradle else 'AndroidManifest.xml')
    try:  # template.check has parsed the older layout's already; a Gradle project's is read here first
        root = ET.parse(manifest).getroot()
    except (OSError, ET.ParseError):
        raise ProfileRefused(f'{manifest.relative_to(snapshot)} is missing or is not XML.', 'layout_invalid') from None

    def applies(p):  # as Android reads it: a direct child of <manifest>, in force from minSdk
        top = p.get(template.ANDROID + 'maxSdkVersion')
        return p.get(template.ANDROID + 'name') == INTERNET and (top is None or top.isdigit()
                                                                 and int(top) >= template.MIN_SDK)
    if not any(applies(p) for p in [*root.findall('uses-permission'), *root.findall('uses-permission-sdk-23')]):
        raise ProfileRefused(f'A client-server app must declare {INTERNET} to reach its server.',
                             'missing_internet')
