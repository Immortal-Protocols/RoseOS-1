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
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from . import template

FILE = 'rose-app.json'
APP_V1, CLIENT_SERVER_V1 = 'app-v1', 'client-server-v1'
SERVER_TOP = frozenset({FILE, 'contract', 'server'})
MAX_FILE = 16 * 1024
MAX_CRATES = 32
ADMISSIONS = ('invite', 'open')  # rose-signin-v1 D7: creator invites by default, open sign-up when declared
CRATE = re.compile(r'([a-z][a-z0-9_-]{0,63})@(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)'
                   r'(?:-[0-9A-Za-z.-]{1,40})?(?:\+[0-9A-Za-z.-]{1,40})?')
MIGRATION = re.compile(r'[0-9]{4}_[a-z0-9_]{1,40}\.sql')
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
    try:  # links, modes, sizes and forbidden suffixes cover server/ and contract/ too
        template.check(snapshot, package, extra_top=SERVER_TOP)
    except template.TemplateRefused as refused:
        raise ProfileRefused(str(refused), 'template_refused', dependency=refused.dependency) from None
    contract = snapshot / 'contract'
    if not contract.is_dir() or contract.is_symlink():
        raise ProfileRefused('A client-server app needs a contract/ folder (the API both halves share).',
                             'layout_invalid')
    _server_tree(snapshot, profile.server)
    _reserved(snapshot, package)
    _internet(snapshot)
    for path in sorted(p for p in (snapshot / 'server' / 'src').rglob('*.rs') if p.is_file()):
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
        if path.is_dir() and path.suffix == '.rs':
            raise ProfileRefused(f'{path.relative_to(snapshot)} is a folder with a source file name.',
                                 'layout_invalid')
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
    if os.path.lexists(reserved):
        raise ProfileRefused(f'{reserved.relative_to(snapshot)} is written by the builder (sign-in and the API '
                             'client).', 'reserved_path')
    for folder in reversed(reserved.parents):  # the folders the builder writes it in: a folder, or not there yet
        if folder.is_relative_to(snapshot / 'src') and os.path.lexists(folder) \
                and not stat.S_ISDIR(os.lstat(folder).st_mode):
            raise ProfileRefused(f'{folder.relative_to(snapshot)} is not a folder; the builder writes its sign-in and '
                                 f'API client in {reserved.relative_to(snapshot)}.', 'reserved_path')
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

    def applies(p):  # as Android reads it: a direct child of <manifest>, in force from minSdk
        top = p.get(template.ANDROID + 'maxSdkVersion')
        return p.get(template.ANDROID + 'name') == INTERNET and (top is None or top.isdigit()
                                                                 and int(top) >= template.MIN_SDK)
    if not any(applies(p) for p in [*root.findall('uses-permission'), *root.findall('uses-permission-sdk-23')]):
        raise ProfileRefused(f'A client-server app must declare {INTERNET} to reach its server.',
                             'missing_internet')


# Rust source, read the way rustc reads it: comments, strings and character literals are not code.

HOST_READS = {'include': 'include!', 'include_str': 'include_str!', 'include_bytes': 'include_bytes!',
              'option_env': 'option_env!'}
NATIVE_MACROS = ('asm', 'global_asm', 'naked_asm', 'llvm_asm')
FILE_ATTRIBUTES = ('debugger_visualizer',)  # embeds a file from the builder's machine in the binary
RUST_SPACE = '\t\n\x0b\x0c\r \x85\u200e\u200f\u2028\u2029'
PUNCT = set('!#$%&()*+,-./:;<=>?@[]^{|}~')
OTHER_ID_CONTINUE = '\u00b7\u0387\u1369\u136a\u136b\u136c\u136d\u136e\u136f\u1370\u1371\u19da'


def _literal_prefix(text: str, i: int) -> bool:
    """A literal or raw identifier starts here: r"..", r#"..", r#ident, b"..", b'..', br"..", c"..", cr".."."""
    head = text[i:i + 3]
    return (head[:2] in ('r"', 'r#', 'b"', "b'", 'c"')
            or head[:2] in ('br', 'cr') and len(head) == 3 and head[2] in '"#')


def _tokens(text: str) -> list[tuple[str, str, int]]:
    """(kind, value, line) for code tokens: ident, punct, literal (strings, chars, numbers) and lifetime. Raises
    SyntaxError for an unterminated comment or literal."""
    if text.startswith('\ufeff'):  # rustc skips a leading byte order mark
        text = text[1:]
    out, i, n, line = [], 0, len(text), 1

    def ident_start(c):  # XID_Start, roughly; a character rustc would refuse fails to compile anyway
        return c == '_' or c.isalpha() or unicodedata.category(c) == 'Nl'

    def ident_end(j):  # XID_Continue: letters, digits, combining marks, connectors, Other_ID_Continue
        while j < n and (text[j] == '_' or text[j].isalnum()
                         or unicodedata.category(text[j]) in ('Mn', 'Mc', 'Nd', 'Pc', 'Nl')
                         or text[j] in OTHER_ID_CONTINUE):
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
        elif c in RUST_SPACE:  # rustc's Pattern_White_Space: U+200E and U+200F separate tokens too
            line += c in '\x85\u2028\u2029'
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
        elif c in PUNCT:
            if text.startswith('::', i):
                out.append(('punct', '::', line))
                i += 2
            else:
                out.append(('punct', c, line))
                i += 1
        else:  # rustc refuses it too, or it would read as something this scan cannot follow
            raise SyntaxError(f'the character U+{ord(c):04X} on line {line}')
    return out


def rust_refusals(text: str) -> list[str]:
    """What in this Rust source would read the builder's machine while compiling, or bring in native code; each
    as owner-safe text naming the line. Empty when there is nothing."""
    toks = _tokens(text)
    pairs = _pairs(toks)
    hits = []

    def at(k, kind, value):
        return k < len(toks) and toks[k][0] == kind and toks[k][1] == value

    def invoked(k):  # toks[k] is called as a macro: `name!` but not `name != x`
        return at(k + 1, 'punct', '!') and not at(k + 2, 'punct', '=')

    # Metavariables a macro captured inside an attribute (`#[$m:meta]`): an invocation writes those attributes
    # out as `#[..]` itself, where this scan reads them, so passing them on as `#[$m]` hides nothing.
    captured = {toks[k + 2][1] for k in range(len(toks) - 4) if at(k, 'punct', '[') and at(k + 1, 'punct', '$')
                and toks[k + 2][0] == 'ident' and at(k + 3, 'punct', ':') and at(k + 4, 'ident', 'meta')
                and k > 0 and at(k - 1, 'punct', '#')}
    depth, use_depth = 0, None  # brace depth; the depth of the `use` item being read, if any
    for k, (kind, value, line) in enumerate(toks):
        if kind == 'punct' and value in '{}':
            depth += 1 if value == '{' else -1
            if use_depth is not None and depth < use_depth:
                use_depth = None
        elif kind == 'punct' and value == ';' and use_depth is not None and depth == use_depth:
            use_depth = None
        elif kind == 'ident' and value == 'use' and use_depth is None:
            use_depth = depth
        elif use_depth is not None and kind == 'ident' and value == 'env' and at(k + 1, 'ident', 'as'):
            # Any `env as ..` in an import: std's env (directly, through `self::`, or a crate alias) or the app's
            # own module; the scan cannot tell which, and a module needs no alias (`env::{self as x}` is fine).
            hits.append(f'renaming env in an import would hide the env! macro (line {line})')
        if kind == 'ident':
            if value in HOST_READS:
                hits.append(f'{HOST_READS[value]} reads a file or setting on the builder\'s machine while '
                            f'compiling (line {line}); put data files in server/assets/ (an item named '
                            f'{value!r} is refused too)')
            elif value in NATIVE_MACROS:  # by name, so a renaming import or a macro argument cannot hide it
                hits.append(f'{value}! is native code (line {line}); generated servers are Rust only (an item '
                            f'named {value!r} is refused too)')
            elif value == 'env' and invoked(k):
                hits.append(f'env! reads the builder\'s environment while compiling (line {line}); '
                            'std::env::var reads the server\'s own environment when it runs')
        elif kind == 'punct' and value == '$' and k + 1 < len(toks) and toks[k + 1][0] == 'ident' \
                and toks[k + 1][1] != 'crate' and invoked(k + 1):
            hits.append(f'a macro that calls the macro its argument names (line {line}) could call env! or '
                        'include_str! without naming them; call the macro by name')
        if kind == 'punct' and value == '#':
            j = k + 1 + at(k + 1, 'punct', '!')
            if at(j, 'punct', '['):
                _attribute(toks, j + 1, pairs[j], hits, pairs, captured)
    return hits


def _pairs(toks) -> dict[int, int]:
    """Each opening bracket's index -> its closing bracket's index, in one pass (unclosed ones close at the end)."""
    pairs, stack = {}, []
    for k, (kind, value, _) in enumerate(toks):
        if kind == 'punct' and value in '([{':
            stack.append(k)
        elif kind == 'punct' and value in ')]}' and stack:
            pairs[stack.pop()] = k
    for k in stack:
        pairs[k] = len(toks)
    return pairs


def _attribute(toks, start: int, end: int, hits: list, pairs: dict, captured=frozenset()):
    """One attribute's tokens in [start, end): `path = ..`, `link(..)`, file-embedding attributes and attributes
    built from a macro argument are refused, also inside cfg_attr (walked with a stack, so depth cannot crash it)."""
    work = [(start, end)]
    while work:
        start, end = work.pop()
        if start >= end:
            continue
        if toks[start][0] == 'punct' and toks[start][1] == '$':
            name = toks[start + 1][1] if start + 1 < end and toks[start + 1][0] == 'ident' else None
            if name not in captured:  # a `#[$m:meta]` matcher puts its own name in `captured`
                hits.append(f'an attribute built by a macro (line {toks[start][2]}) could be #[path] or #[link]; '
                            'write the attribute itself')
            continue
        if toks[start][0] != 'ident':
            continue
        name, line = toks[start][1], toks[start][2]
        following = toks[start + 1][1] if start + 1 < end and toks[start + 1][0] == 'punct' else None
        if name in FILE_ATTRIBUTES:
            hits.append(f'#[{name}] embeds a file from the builder\'s machine in the server (line {line})')
        elif name == 'path' and following == '=':
            hits.append(f'#[path] can compile a file from anywhere on the builder\'s machine (line {line}); keep '
                        'modules under server/src/')
        elif name == 'link' and following == '(':
            hits.append(f'#[link] links native code (line {line}); generated servers are Rust only')
        elif name == 'cfg_attr' and following == '(':
            close = min(pairs.get(start + 1, end), end)
            parts, part, k = [], start + 2, start + 2  # cfg_attr(predicate, attr, attr, ...)
            while k < close:  # top-level commas only: a nested group is jumped over, so each token is seen once
                if toks[k][0] == 'punct' and toks[k][1] in '([{':
                    k = pairs.get(k, close)
                elif toks[k][0] == 'punct' and toks[k][1] == ',':
                    parts.append((part, k))
                    part = k + 1
                k += 1
            parts.append((part, close))
            work.extend(parts[1:])
