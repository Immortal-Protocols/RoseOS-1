"""The profile a generated project declares (docs/APP-BUILDER.md §7, Level 2): a plain app (`app-v1`, Level 1) or an
app with its own server (`client-server-v1`). Source only and enabled nowhere: nothing calls this yet.

A project is `app-v1` unless it writes rose-app.json at its root, and then everything Level 1 checks stays exactly
as it was. A `client-server-v1` project also has `contract/` (the API both halves share) and `server/` (the server's
Rust source, its data files and its SQL migrations). The builder writes the server's Cargo files, its entry point
and the code generated from the contract itself, so none of those may come from the writer.

Every refusal here is identity, integrity or isolation, never a rule on what an app may do:
- the builder's own files (Cargo, the client's `rose` package) cannot be replaced;
- a server that has been delivered cannot be dropped, which would strand its users' data;
- Rust source may not read files or environment variables from the builder's machine while it compiles, and may
  not bring in native code (inline assembly, linked native libraries), like Level 1's "no native code". The scan
  below is a second line of defence; the boundary is build isolation (§5, blocker 3).
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
SERVER_TOP = frozenset({FILE, 'contract', 'server'})
MAX_FILE = 16 * 1024
MAX_CRATES = 32
ADMISSIONS = ('invite', 'open')  # rose-signin-v1 D7: creator invites by default, open sign-up when declared
CRATE = re.compile(r'([a-z][a-z0-9_-]{0,63})@(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)'
                   r'(?:-[0-9A-Za-z.-]{1,40})?(?:\+[0-9A-Za-z.-]{1,40})?')
MIGRATION = re.compile(r'\d{4}_[a-z0-9_]{1,40}\.sql')
SERVER_DIRS = ('src', 'assets', 'migrations')
# What a writer used to Cargo would put in server/; the builder writes these itself (from rose-app.json).
BUILD_NAMES = ('cargo.toml', 'cargo.lock', 'build.rs', '.cargo')
HANDLERS = Path('server/src/handlers/mod.rs')
INTERNET = 'android.permission.INTERNET'


@dataclass(frozen=True)
class ServerDecl:
    crates: tuple[str, ...] = ()
    shared: bool = False
    admission: str = 'invite'


@dataclass(frozen=True)
class Profile:
    name: str
    server: ServerDecl | None = None


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
    if data.get('profile') != CLIENT_SERVER_V1:
        raise invalid(f'profile must be "{CLIENT_SERVER_V1}" (a plain app has no {FILE})')
    server = data.get('server', {})
    if not isinstance(server, dict) or set(server) - {'crates', 'shared', 'admission'}:
        raise invalid('server allows only crates, shared and admission')
    crates = server.get('crates', [])
    if not isinstance(crates, list) or len(crates) > MAX_CRATES:
        raise invalid(f'server.crates must be a list of at most {MAX_CRATES} entries')
    names = []
    for crate in crates:
        match = CRATE.fullmatch(crate) if isinstance(crate, str) else None
        if not match:
            raise invalid(f'{crate!r} is not an exact crate version (name@1.2.3, no ranges)')
        names.append(match.group(1))
    if len(names) != len(set(names)):
        raise invalid('a crate is listed twice')
    shared = server.get('shared', False)
    if type(shared) is not bool:
        raise invalid('server.shared must be true or false')
    admission = server.get('admission', 'invite')
    if admission not in ADMISSIONS:
        raise invalid('server.admission must be "invite" or "open"')
    return Profile(CLIENT_SERVER_V1, ServerDecl(tuple(crates), shared, admission))


def check(snapshot: Path, package: str, bound: str | None = None) -> Profile:
    """Check the snapshot against its declared profile. `bound` is the profile a delivered version already has."""
    snapshot = Path(snapshot)
    profile = read(snapshot)
    if bound == CLIENT_SERVER_V1 and profile.name == APP_V1:
        raise ProfileRefused(f'This app has a server, and its users\' data lives there; {FILE} and server/ must '
                             'stay.', 'profile_dropped')
    if profile.name == APP_V1:
        template.check(snapshot, package)
        return profile
    template.check(snapshot, package, extra_top=SERVER_TOP)  # links, modes, sizes and suffixes cover server/ too
    _server_tree(snapshot, profile.server)
    _reserved(snapshot, package)
    _internet(snapshot)
    for path in sorted((snapshot / 'server' / 'src').rglob('*.rs')):
        name = path.relative_to(snapshot)
        try:
            text = path.read_bytes().decode('utf-8')
        except UnicodeDecodeError:
            raise ProfileRefused(f'{name} is not UTF-8.', 'source_unreadable') from None
        try:
            hits = rust_refusals(text)
        except SyntaxError as error:
            raise ProfileRefused(f'{name} could not be read: {error.msg}.', 'source_unreadable') from None
        if hits:
            raise ProfileRefused(f'{name}: {hits[0]}.', 'forbidden_construct')
    return profile


def _server_tree(snapshot: Path, decl: ServerDecl):
    server = snapshot / 'server'
    if not server.is_dir() or server.is_symlink():
        raise ProfileRefused('A client-server app needs a server/ folder.', 'layout_invalid')
    for entry in server.iterdir():
        lower = entry.name.lower()
        if lower in BUILD_NAMES or lower.startswith('rust-toolchain'):
            raise ProfileRefused(f'server/{entry.name} is written by the builder; declare crates in {FILE} '
                                 '(server.crates) instead.', 'server_build_file', dependency=True)
        if entry.name not in SERVER_DIRS or not entry.is_dir():
            raise ProfileRefused(f'server/{entry.name} is not part of a server: only src/, assets/ and '
                                 'migrations/ are.', 'layout_invalid')
    if not (snapshot / HANDLERS).is_file():
        raise ProfileRefused(f'{HANDLERS} is missing: it holds the server\'s handlers.', 'layout_invalid')
    for path in (server / 'src').rglob('*'):
        if path.is_file() and path.suffix != '.rs':
            raise ProfileRefused(f'{path.relative_to(snapshot)} is not Rust source; data files go in '
                                 'server/assets/.', 'layout_invalid')
    migrations = server / 'migrations'
    if migrations.is_dir():
        for scope in migrations.iterdir():
            if scope.name not in ('account', 'shared') or not scope.is_dir():
                raise ProfileRefused(f'{scope.relative_to(snapshot)}: migrations are in migrations/account/ '
                                     '(each user\'s data) or migrations/shared/.', 'layout_invalid')
            if scope.name == 'shared' and not decl.shared:
                raise ProfileRefused(f'migrations/shared/ needs "shared": true in {FILE}.', 'layout_invalid')
            for file in scope.iterdir():
                if not file.is_file() or not MIGRATION.fullmatch(file.name):
                    raise ProfileRefused(f'{file.relative_to(snapshot)} is not a migration (NNNN_name.sql).',
                                         'layout_invalid')


def _reserved(snapshot: Path, package: str):
    """The client's `<package>.rose` package is the builder's sign-in and API code; the writer cannot add to it,
    by its folder or by a package clause written anywhere else."""
    reserved = snapshot / 'src' / Path(*package.split('.')) / 'rose'
    if reserved.exists():
        raise ProfileRefused(f'{reserved.relative_to(snapshot)} is written by the builder (sign-in and the API '
                             'client).', 'reserved_path')
    for java in snapshot.glob('src/**/*.java'):
        text = template.unicode_escapes(java.read_text(encoding='utf-8', errors='replace'))
        code = re.sub(r'/\*.*?\*/|//[^\n]*', ' ', text, flags=re.S)
        match = re.match(r'\s*package\s+([\w.]+)\s*;', code)
        if match and (match.group(1) == package + '.rose' or match.group(1).startswith(package + '.rose.')):
            raise ProfileRefused(f'{java.relative_to(snapshot)} is in {package}.rose, which the builder writes.',
                                 'reserved_path')


def _internet(snapshot: Path):
    import xml.etree.ElementTree as ET
    root = ET.parse(snapshot / 'AndroidManifest.xml').getroot()  # template.check has parsed it already
    if not any(p.get(template.ANDROID + 'name') == INTERNET for p in root.iter('uses-permission')):
        raise ProfileRefused(f'A client-server app must declare {INTERNET} to reach its server.',
                             'missing_internet')


# Rust source, read the way rustc reads it: comments, strings and character literals are not code.

HOST_READS = {'include': 'include!', 'include_str': 'include_str!', 'include_bytes': 'include_bytes!',
              'option_env': 'option_env!'}
NATIVE_MACROS = ('asm', 'global_asm', 'naked_asm', 'llvm_asm')


def _literal_prefix(text: str, i: int) -> bool:
    """A literal or raw identifier starts here: r"..", r#"..", r#ident, b"..", b'..', br"..", c"..", cr".."."""
    head = text[i:i + 3]
    return (head[:2] in ('r"', 'r#', 'b"', "b'", 'c"')
            or head[:2] in ('br', 'cr') and len(head) == 3 and head[2] in '"#')


def _tokens(text: str) -> list[tuple[str, str, int]]:
    """(kind, value, line) for code tokens: ident, punct, literal (strings, chars, numbers) and lifetime. Raises
    SyntaxError for an unterminated comment or literal."""
    out, i, n, line = [], 0, len(text), 1

    def ident_start(c):
        return c == '_' or c.isalpha()

    def ident_end(j):
        while j < n and (text[j] == '_' or text[j].isalnum()):
            j += 1
        return j

    def quoted(j, end_quote):  # j just past the opening quote; returns the index past the closing one
        nonlocal line
        while j < n:
            c = text[j]
            if c == '\\':
                j += 2
                continue
            if c == '\n':
                line += 1
            if c == end_quote:
                return j + 1
            j += 1
        raise SyntaxError('an unterminated string or character literal')

    def raw(j):  # j at the first '#' or '"' after r / br / cr
        nonlocal line
        hashes = 0
        while j < n and text[j] == '#':
            hashes += 1
            j += 1
        if j >= n or text[j] != '"':
            return None
        close = '"' + '#' * hashes
        end = text.find(close, j + 1)
        if end < 0:
            raise SyntaxError('an unterminated raw string')
        line += text.count('\n', j, end)
        return end + len(close)

    while i < n:
        c = text[i]
        if c == '\n':
            line += 1
            i += 1
        elif c.isspace():
            i += 1
        elif text.startswith('//', i):
            end = text.find('\n', i)
            i = n if end < 0 else end
        elif text.startswith('/*', i):
            depth, j = 1, i + 2
            while depth:
                if j >= n:
                    raise SyntaxError('an unterminated comment')
                if text.startswith('/*', j):
                    depth, j = depth + 1, j + 2
                elif text.startswith('*/', j):
                    depth, j = depth - 1, j + 2
                else:
                    line += text[j] == '\n'
                    j += 1
            i = j
        elif c == '"':
            start, i = line, quoted(i + 1, '"')
            out.append(('literal', 'str', start))
        elif c == "'":
            if i + 1 < n and text[i + 1] == '\\':
                start, i = line, quoted(i + 1, "'")
                out.append(('literal', 'char', start))
            elif i + 2 < n and text[i + 2] == "'":
                out.append(('literal', 'char', line))
                i += 3
            elif i + 1 < n and ident_start(text[i + 1]):
                j = ident_end(i + 1)
                out.append(('lifetime', text[i + 1:j], line))
                i = j
            else:
                raise SyntaxError('an unreadable character literal')
        elif _literal_prefix(text, i):
            start = line
            if c == 'r' and text[i + 1] == '#' and i + 2 < n and ident_start(text[i + 2]):
                j = ident_end(i + 2)  # a raw identifier: r#include_str is include_str
                out.append(('ident', text[i + 2:j], line))
                i = j
                continue
            j = i + 1 if c == 'r' else i + 2 if text[i + 1] == 'r' else None
            if j is not None:
                end = raw(j)
                if end is None:
                    raise SyntaxError('an unreadable raw string')
                i = end
            elif text[i + 1] == '"':
                i = quoted(i + 2, '"')
            elif c == 'b' and text[i + 1] == "'":
                i = quoted(i + 2, "'")
            else:
                raise SyntaxError('an unreadable literal')
            out.append(('literal', 'str', start))
        elif ident_start(c):
            j = ident_end(i)
            out.append(('ident', text[i:j], line))
            i = j
        elif c.isdigit():
            j = i + 1
            while j < n and (text[j].isalnum() or text[j] == '_' or text[j] == '.' and j + 1 < n
                             and text[j + 1].isdigit()):
                j += 1
            out.append(('literal', 'number', line))
            i = j
        else:
            if text.startswith('::', i):
                out.append(('punct', '::', line))
                i += 2
            else:
                out.append(('punct', c, line))
                i += 1
    return out


def rust_refusals(text: str) -> list[str]:
    """What in this Rust source would read the builder's machine while compiling, or bring in native code; each
    as owner-safe text naming the line. Empty when there is nothing."""
    toks = _tokens(text)
    hits = []

    def at(k, kind, value):
        return k < len(toks) and toks[k][0] == kind and toks[k][1] == value

    for k, (kind, value, line) in enumerate(toks):
        if kind == 'ident':
            if value in HOST_READS:
                hits.append(f'{HOST_READS[value]} reads a file or setting on the builder\'s machine while '
                            f'compiling (line {line}); put data files in server/assets/ (an item named '
                            f'{value!r} is refused too)')
            elif value == 'env' and at(k + 1, 'punct', '!'):
                hits.append(f'env! reads the builder\'s environment while compiling (line {line}); '
                            'std::env::var reads the server\'s own environment when it runs')
            elif value == 'env' and at(k + 1, 'ident', 'as'):
                hits.append(f'renaming env would hide the env! macro (line {line})')
            elif value == 'env' and at(k + 1, 'punct', '::') and at(k + 2, 'punct', '{'):
                depth, j = 0, k + 2
                while j < len(toks):
                    depth += at(j, 'punct', '{') - at(j, 'punct', '}')
                    if at(j, 'ident', 'self') and at(j + 1, 'ident', 'as'):
                        hits.append(f'renaming env would hide the env! macro (line {line})')
                        break
                    if depth == 0:
                        break
                    j += 1
            elif value in NATIVE_MACROS and at(k + 1, 'punct', '!'):
                hits.append(f'{value}! is native code (line {line}); generated servers are Rust only')
        elif kind == 'punct' and value == '#':
            j = k + 1 + at(k + 1, 'punct', '!')
            if at(j, 'punct', '['):
                _attribute(toks, j + 1, _closing(toks, j), hits)
    return hits


def _closing(toks, j: int) -> int:
    """The index of the bracket that closes the one at toks[j] (or the end)."""
    depth = 0
    for k in range(j, len(toks)):
        if toks[k][0] == 'punct' and toks[k][1] in '([{':
            depth += 1
        elif toks[k][0] == 'punct' and toks[k][1] in ')]}':
            depth -= 1
            if depth == 0:
                return k
    return len(toks)


def _attribute(toks, start: int, end: int, hits: list):
    """One attribute's tokens in [start, end): `path = ..` and `link(..)` are refused, also inside cfg_attr."""
    if start >= end or toks[start][0] != 'ident':
        return
    name, line = toks[start][1], toks[start][2]
    following = toks[start + 1][1] if start + 1 < end and toks[start + 1][0] == 'punct' else None
    if name == 'path' and following == '=':
        hits.append(f'#[path] can compile a file from anywhere on the builder\'s machine (line {line}); keep '
                    'modules under server/src/')
    elif name == 'link' and following == '(':
        hits.append(f'#[link] links native code (line {line}); generated servers are Rust only')
    elif name == 'cfg_attr' and following == '(':
        close = _closing(toks, start + 1)
        parts, depth, part = [], 0, start + 2  # cfg_attr(predicate, attr, attr, ...)
        for k in range(start + 2, close):
            if toks[k][0] == 'punct' and toks[k][1] in '([{':
                depth += 1
            elif toks[k][0] == 'punct' and toks[k][1] in ')]}':
                depth -= 1
            elif depth == 0 and toks[k][0] == 'punct' and toks[k][1] == ',':
                parts.append((part, k))
                part = k + 1
        parts.append((part, close))
        for a, b in parts[1:]:
            _attribute(toks, a, b, hits)
