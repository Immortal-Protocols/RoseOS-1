"""Pinned pure-Java JAR libraries for a generated app (docs/APP-BUILDER.md §2, "Libraries").

The writer never ships a binary and never touches the network. It may only *declare* libraries, in the app's
``dependencies.json``:

    {"schemaVersion": 1, "dependencies": ["group:artifact:version", ...]}

The builder resolves each coordinate through an injected, offline ``Resolver`` (for example ``RecordedStore``, a
host-owned directory of recorded files and digests; nothing here downloads anything). Every artifact is then:

- copied into the job's own directory, never followed through a link, within size bounds, and its sha256 compared
  with the digest the resolver recorded and, for a coordinate an earlier delivered version used, with that version's
  lock (a library never changes under an app without its coordinate changing);
- read as a plain JVM JAR: bounded entries and sizes, safe names, class files only (plus inert META-INF metadata),
  each checked under the name its bytes define (this_class), not just its entry path,
  no class in the ``java``, ``android`` or ``dalvik`` packages, none that android.jar defines, none in the app's own
  package, and no class two libraries both define;
- rewritten as a job-local JAR holding exactly those class files, which is what javac and d8 see.

Deliberately not supported yet (interim, not product scope): AAR packages and Android resources from libraries
(they need an aapt2 resource pipeline; aapt2 is in the pinned build-tools but the builder does not use it yet),
Kotlin sources (no pinned kotlinc), Gradle, native code, Java resources inside a JAR,
multi-release JARs, ServiceLoader files, and transitive resolution (each library is declared). Each fails as a
``dependency`` failure with a summary the owner can read.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import zipfile
from dataclasses import dataclass
from pathlib import Path

FILE = 'dependencies.json'
SCHEMA_VERSION = 1
MAX_DECLARED = 16
MAX_COORDINATE = 200
MAX_JAR = 16 * 1024 * 1024  # bytes of one resolved artifact
MAX_TOTAL = 64 * 1024 * 1024  # bytes of all of a job's artifacts
MAX_ENTRIES = 20000  # entries in one JAR
MAX_ENTRY = 8 * 1024 * 1024  # uncompressed bytes of one entry
MAX_UNCOMPRESSED = 64 * 1024 * 1024  # uncompressed bytes of one JAR
MAX_NAME = 512
MAX_CLASS_MAJOR = 65  # class files up to Java 21, which the pinned d8 reads
COORDINATE = re.compile(r'[A-Za-z0-9_.-]{1,80}:[A-Za-z0-9_.-]{1,80}:[A-Za-z0-9_.+-]{1,40}')
SHA256 = re.compile(r'[0-9a-f]{64}')
SEGMENT = re.compile(r'[A-Za-z_$][A-Za-z0-9_$]*')
CLASS_NAME = re.compile(r'(?:[A-Za-z_$][A-Za-z0-9_$]*|package-info)\.class')
# Package prefixes no library may define a class in: the platform's own, whatever android.jar happens to list.
RESERVED = ('java/', 'android/', 'dalvik/')
META_KEPT = re.compile(r'META-INF/(?:MANIFEST\.MF|(?:LICENSE|NOTICE|DEPENDENCIES)[^/]*|maven/.+|[^/]+\.(?:SF|RSA|DSA|EC))',
                       re.I)
EPOCH = (1980, 1, 1, 0, 0, 0)


class Refused(ValueError):
    """A library the builder will not use; the summary is safe to show the owner. ``malformed`` marks a broken
    declaration (a compile failure) rather than a library the builder cannot provide (a dependency failure)."""

    def __init__(self, message: str, malformed: bool = False):
        super().__init__(message)
        self.malformed = malformed


@dataclass(frozen=True)
class Artifact:
    """What a resolver returns for one coordinate: a local file and the sha256 it recorded for it."""
    coordinate: str
    path: Path
    sha256: str


@dataclass(frozen=True)
class Library:
    """A verified library for one job: the job-local JAR javac and d8 read, and what is recorded for the version."""
    coordinate: str
    sha256: str
    size: int
    jar: Path


class Resolver:
    """Offline coordinate lookup. ``resolve`` returns an ``Artifact`` or None when this builder has no such library;
    ``available`` lists what it can provide (shown to the writer). It must never download."""

    def resolve(self, coordinate: str) -> Artifact | None:
        raise NotImplementedError

    def available(self) -> list[str]:
        return []


class RecordedStore(Resolver):
    """A host-owned directory: ``index.json`` ``{"schemaVersion": 1, "artifacts": {"<coordinate>": "<sha256>"}}``
    and one file ``<sha256>.jar`` per artifact. Populating it is the host's job and outside this module."""

    def __init__(self, root: Path):
        self.root = Path(root)

    def _index(self) -> dict:
        try:
            data = json.loads((self.root / 'index.json').read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return {}
        artifacts = data.get('artifacts') if isinstance(data, dict) and data.get('schemaVersion') == 1 else None
        if not isinstance(artifacts, dict):
            return {}
        return {c: h for c, h in artifacts.items()
                if isinstance(c, str) and COORDINATE.fullmatch(c) and isinstance(h, str) and SHA256.fullmatch(h)}

    def resolve(self, coordinate: str) -> Artifact | None:
        digest = self._index().get(coordinate)
        return None if digest is None else Artifact(coordinate, self.root / f'{digest}.jar', digest)

    def available(self) -> list[str]:
        return sorted(self._index())


def declared(source: Path) -> list[str]:
    """The coordinates the app declares, in order; [] when it declares none. Refused when malformed."""
    path = Path(source) / FILE
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise Refused(f'{FILE} is not valid JSON.', malformed=True) from None
    if (not isinstance(data, dict) or set(data) != {'schemaVersion', 'dependencies'}
            or type(data['schemaVersion']) is not int or data['schemaVersion'] != SCHEMA_VERSION
            or not isinstance(data['dependencies'], list)):
        raise Refused(f'{FILE} must be {{"schemaVersion": 1, "dependencies": ["group:artifact:version", ...]}}.',
                      malformed=True)
    coordinates = data['dependencies']
    if len(coordinates) > MAX_DECLARED:
        raise Refused(f'An app may declare at most {MAX_DECLARED} libraries.', malformed=True)
    seen = set()
    for coordinate in coordinates:
        if not isinstance(coordinate, str) or len(coordinate) > MAX_COORDINATE or not COORDINATE.fullmatch(coordinate):
            if isinstance(coordinate, str) and coordinate.count(':') >= 3:  # a packaging or classifier: @aar and such
                raise Refused(f'{coordinate[:MAX_COORDINATE]!r}: only plain JAR libraries are supported.')
            raise Refused(f'{FILE} lists something that is not a group:artifact:version coordinate.', malformed=True)
        module = coordinate.rsplit(':', 1)[0]
        if module in seen:
            raise Refused(f'{module} is declared twice.', malformed=True)
        seen.add(module)
    return list(coordinates)


def resolve(coordinates: list[str], resolver: Resolver | None, libs: Path, package: str, framework: frozenset,
            locked: dict | None = None) -> list[Library]:
    """Verified job-local libraries for these coordinates, or Refused. ``locked`` maps a coordinate to the sha256 the
    last delivered version used; ``framework`` is the set of class entries android.jar defines."""
    if not coordinates:
        return []
    if resolver is None:
        raise Refused('The app declares libraries, and this builder has no library store set up.')
    locked = locked or {}
    libs.mkdir(mode=0o700, parents=True, exist_ok=True)
    out, owners, total = [], {}, 0
    for index, coordinate in enumerate(coordinates):
        try:
            artifact = resolver.resolve(coordinate)
        except Exception:
            raise Refused(f'The library store could not look up {coordinate}.') from None
        if artifact is None or artifact.coordinate != coordinate:
            raise Refused(f'The library {coordinate} is not available on this builder.')
        if not isinstance(artifact.sha256, str) or not SHA256.fullmatch(artifact.sha256):
            raise Refused(f'The library store has no valid digest recorded for {coordinate}.')
        if coordinate in locked and locked[coordinate] != artifact.sha256:
            raise Refused(f'The library {coordinate} is not the one this app was last built with, so it was not used.')
        copy = libs / f'{index}.orig'
        size = _copy(Path(artifact.path), copy, coordinate, MAX_TOTAL - total)
        total += size
        digest = _sha256(copy)
        if digest != artifact.sha256:
            raise Refused(f'The library {coordinate} does not match its recorded digest, so it was not used.')
        classes = _classes(copy, coordinate, package, framework)
        for name in classes:
            if name in owners:
                raise Refused(f'{coordinate} and {owners[name]} both define {name[:-6].replace("/", ".")}.')
            owners[name] = coordinate
        jar = libs / f'{index}.jar'
        _rewrite(copy, jar, classes)
        copy.unlink()
        out.append(Library(coordinate, digest, size, jar))
    return out


def framework_classes(android_jar: Path) -> frozenset:
    """Class entries android.jar defines (a library may not redefine one)."""
    with zipfile.ZipFile(android_jar) as z:
        return frozenset(n for n in z.namelist() if n.endswith('.class'))


def _copy(src: Path, dst: Path, coordinate: str, budget: int) -> int:
    try:
        fd = os.open(src, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
    except OSError:
        raise Refused(f'The library {coordinate} is missing from the library store.') from None
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise Refused(f'The library {coordinate} is not a regular file in the library store.')
        limit = min(MAX_JAR, budget)
        size = 0
        with open(dst, 'xb') as out:
            os.chmod(dst, 0o600)
            while True:
                chunk = os.read(fd, 1 << 20)
                if not chunk:
                    break
                size += len(chunk)
                if size > limit:
                    raise Refused(f'The library {coordinate} is larger than this builder accepts.')
                out.write(chunk)
        return size
    finally:
        os.close(fd)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


# Constant pool entries' sizes after their tag (Utf8, tag 1, is sized by its own length; Long and Double take 2 slots).
POOL_SIZES = {3: 4, 4: 4, 5: 8, 6: 8, 7: 2, 8: 2, 9: 4, 10: 4, 11: 4, 12: 4, 15: 3, 16: 2, 17: 4, 18: 4, 19: 2, 20: 2}
ACC_MODULE = 0x8000


def class_name(data: bytes) -> tuple[str, int]:
    """The internal name a class file defines (this_class) and its access flags. That name, not the JAR entry's,
    is what javac and d8 use. ValueError when the constant pool is malformed or truncated."""
    count, pos, utf8, classes = int.from_bytes(data[8:10], 'big'), 10, {}, {}
    index = 1
    while index < count:
        tag = data[pos] if pos < len(data) else None
        if tag == 1:
            length = int.from_bytes(data[pos + 1:pos + 3], 'big')
            raw = data[pos + 3:pos + 3 + length]
            if len(raw) != length:
                raise ValueError('truncated')
            utf8[index] = raw
            pos += 3 + length
        elif tag in POOL_SIZES:
            if tag == 7:
                classes[index] = int.from_bytes(data[pos + 1:pos + 3], 'big')
            pos += 1 + POOL_SIZES[tag]
        else:
            raise ValueError('bad constant pool tag')
        index += 2 if tag in (5, 6) else 1
    if pos + 4 > len(data):
        raise ValueError('truncated')
    access, this = int.from_bytes(data[pos:pos + 2], 'big'), int.from_bytes(data[pos + 2:pos + 4], 'big')
    raw = utf8.get(classes.get(this))
    if raw is None:
        raise ValueError('this_class is not a class')
    return raw.decode('ascii'), access  # a name outside ASCII is refused (UnicodeDecodeError is a ValueError)


def _classes(jar: Path, coordinate: str, package: str, framework: frozenset) -> dict:
    """The JAR's class entries, name -> bytes, after every check; Refused otherwise. Anything the archive reader
    raises is a library that is not a valid JAR, never a builder failure."""
    try:
        return _check_archive(jar, coordinate, package, framework)
    except Refused:
        raise
    except Exception:
        raise Refused(f'The library {coordinate} is not a valid JAR.') from None


def _check_archive(jar: Path, coordinate: str, package: str, framework: frozenset) -> dict:
    own = package.replace('.', '/') + '/'
    with zipfile.ZipFile(jar) as z:
        infos = z.infolist()
        if len(infos) > MAX_ENTRIES:
            raise Refused(f'The library {coordinate} has more entries than this builder accepts.')
        if sum(i.file_size for i in infos) > MAX_UNCOMPRESSED:
            raise Refused(f'The library {coordinate} is larger than this builder accepts once unpacked.')
        names, classes = set(), {}
        for info in infos:
            name = info.filename
            if (len(name) > MAX_NAME or not name or name.startswith('/') or '\\' in name or '\x00' in name
                    or any(part in ('', '.', '..') for part in name.rstrip('/').split('/'))):
                raise Refused(f'The library {coordinate} has an entry with an unsafe name.')
            if name in names:
                raise Refused(f'The library {coordinate} has the same entry twice.')
            names.add(name)
            if info.flag_bits & 0x1:
                raise Refused(f'The library {coordinate} has an encrypted entry.')
            if name.endswith('/'):
                continue
            if name.startswith('META-INF/'):
                if name.startswith('META-INF/versions/'):
                    raise Refused(f'The library {coordinate} is a multi-release JAR, which is not supported yet.')
                if not META_KEPT.fullmatch(name):
                    raise Refused(f'The library {coordinate} carries {name}, a resource this builder does not '
                                  'package yet.')
                continue
            parts = name.split('/')
            if name != 'module-info.class' and not (CLASS_NAME.fullmatch(parts[-1])
                                                    and all(SEGMENT.fullmatch(p) for p in parts[:-1])):
                raise Refused(f'The library {coordinate} carries {name}; only class files are supported yet '
                              '(no Java resources or native code).')
            if info.file_size > MAX_ENTRY:
                raise Refused(f'The library {coordinate} has an entry larger than this builder accepts.')
            with z.open(info) as f:
                data = f.read(MAX_ENTRY + 1)
            if len(data) > MAX_ENTRY or len(data) != info.file_size:
                raise Refused(f'The library {coordinate} is not a valid JAR.')
            major = int.from_bytes(data[6:8], 'big') if len(data) >= 10 else 0
            if data[:4] != b'\xca\xfe\xba\xbe' or not 45 <= major <= MAX_CLASS_MAJOR:
                raise Refused(f'The library {coordinate} has a class file this builder cannot read ({name}).')
            try:
                defined, access = class_name(data)
            except (ValueError, IndexError):
                raise Refused(f'The library {coordinate} has a class file this builder cannot read ({name}).') from None
            if name == 'module-info.class':
                if defined != 'module-info' or not access & ACC_MODULE:
                    raise Refused(f'The library {coordinate} has a module-info.class that is not module metadata.')
                continue  # module metadata; Android has no modules
            # Every check below is on the class the bytes define, which is what javac and d8 use.
            if defined + '.class' != name:
                raise Refused(f'The library {coordinate} stores a class under another class\'s name ({name}).')
            if name.startswith(RESERVED) or name in framework or name.startswith(own):
                raise Refused(f'The library {coordinate} defines {name[:-6].replace("/", ".")}, which belongs to '
                              f'{"this app" if name.startswith(own) else "the Android platform"}.')
            classes[name] = data
    return classes


def _rewrite(src: Path, dst: Path, classes: dict):
    """A job-local JAR with exactly the checked class files, in a fixed order with fixed times."""
    with zipfile.ZipFile(dst, 'x', zipfile.ZIP_STORED) as z:
        for name in sorted(classes):
            info = zipfile.ZipInfo(name, EPOCH)
            info.external_attr = 0o644 << 16
            z.writestr(info, classes[name])
    os.chmod(dst, 0o600)
