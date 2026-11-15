"""The server half's binary check and manifest (docs/APP-BUILDER.md §7.7). Source only: every server build runs
``check_elf``, and each delivered version whose server is for the host's target records its ``manifest``.

A client-server version's server is one ELF plus a manifest, with nothing host-specific inside the app.

``check_elf`` reads the binary's own headers, every offset bounded by the file, and accepts only what §7.7 agreed
with the Chariot owner: a 64-bit little-endian x86-64 Linux executable, static (no program interpreter and no
shared library needed, as a static or static-pie executable is). It decides nothing about what the server does;
it shows the binary is the self-contained kind the host runs.

``manifest`` is App Core's description of one version's server, and ``check`` is what a host (the fake host in
tests now, the Chariot adapter later) holds a manifest and its binary to. The manifest is not Chariot's own wire
format: further manifest fields are still open (§7.7, item 1). Its keys follow the host's snake_case, as its
readiness keys do. It names no host identity and no secret:
- ``app_id`` is the host's own UUID, which the server learns from the host;
- ``deployment_sha256`` is the host's, which the server echoes;
- the sealing key's delivery (D9) has no agreed contract, so the manifest says nothing about it, and nothing here
  makes a server deployable.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import struct
from dataclasses import dataclass
from pathlib import Path

SCHEMA_VERSION = 1
TARGET = 'x86_64-unknown-linux-musl'  # §7.7: a static x86-64 musl ELF
SIGNIN_REVISION = '23062a04'  # rose-signin, as §7.1 pins it
PORT_DEFAULT = 8080
HEALTH = '/health'
MAX_ELF = 512 * 1024 * 1024
MAX_PHNUM = 512
MAX_DYNAMIC = 4096  # entries in PT_DYNAMIC
MAX_MANIFEST = 16 * 1024
MAX_SCHEMA = 9999
MAX_VERSION_CODE = 2100000000  # Android's largest versionCode
PROJECT_ID = re.compile(r'[0-9a-f]{16}')  # rose.app_builder.ledger's project ids
HEX64 = re.compile(r'[0-9a-f]{64}')

# ELF constants (System V gABI; x86-64 psABI).
ELFCLASS64, ELFDATA2LSB, EV_CURRENT = 2, 1, 1
ELFOSABI_SYSV, ELFOSABI_GNU = 0, 3
ET_EXEC, ET_DYN = 2, 3
EM_X86_64 = 62
PT_LOAD, PT_DYNAMIC, PT_INTERP = 1, 2, 3
PF_X = 1
DT_NULL, DT_NEEDED, DT_SONAME = 0, 1, 14
EHDR = struct.Struct('<16sHHIQQQIHHHHHH')
PHDR = struct.Struct('<IIQQQQQQ')
DYN = struct.Struct('<qQ')


class Refused(ValueError):
    """A binary or manifest the builder will not use; ``reason`` is a stable token (server_binary_invalid,
    server_not_static, manifest_invalid), the message is safe to show the owner."""

    def __init__(self, message: str, reason: str):
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True)
class ElfInfo:
    sha256: str
    size: int
    pie: bool  # ET_DYN: a static-pie executable


def check_elf(path: Path) -> ElfInfo:
    """The binary's digest and size once it is a static x86-64 Linux executable (module docstring), else Refused.
    Read without following a link."""
    def bad(why: str, reason: str = 'server_binary_invalid'):
        raise Refused(f'The server binary {why}.', reason)

    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
    except OSError:
        bad('is missing or is a link')
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            bad('is not a regular file')
        if st.st_size > MAX_ELF:
            bad(f'is larger than {MAX_ELF // (1024 * 1024)} MB')
        h = hashlib.sha256()
        chunks = []
        while chunk := os.read(fd, 1 << 20):
            h.update(chunk)
            chunks.append(chunk)
            if sum(map(len, chunks)) > MAX_ELF:
                bad(f'is larger than {MAX_ELF // (1024 * 1024)} MB')
        data = b''.join(chunks)
    finally:
        os.close(fd)
    size = len(data)
    if size < EHDR.size:
        bad('is too short to be an ELF file')
    (ident, e_type, e_machine, e_version, e_entry, e_phoff, _shoff, _flags, e_ehsize, e_phentsize, e_phnum,
     *_rest) = EHDR.unpack_from(data, 0)
    if ident[:4] != b'\x7fELF':
        bad('is not an ELF file')
    if ident[4] != ELFCLASS64 or ident[5] != ELFDATA2LSB or ident[6] != EV_CURRENT or e_version != EV_CURRENT:
        bad('is not a 64-bit little-endian ELF file')
    if ident[7] not in (ELFOSABI_SYSV, ELFOSABI_GNU):
        bad('is not a Linux executable')
    if e_machine != EM_X86_64:
        bad('is not for x86-64')
    if e_type not in (ET_EXEC, ET_DYN):
        bad('is not an executable')
    if e_ehsize != EHDR.size or e_phentsize != PHDR.size or not 1 <= e_phnum <= MAX_PHNUM:
        bad('has headers this builder cannot read')
    if e_entry == 0:
        bad('has no entry point')
    if e_phoff < EHDR.size or e_phoff + e_phnum * PHDR.size > size:
        bad('has program headers outside the file')
    headers = [PHDR.unpack_from(data, e_phoff + i * PHDR.size) for i in range(e_phnum)]
    executable = False
    dynamic = None
    for p_type, p_flags, p_offset, _vaddr, _paddr, p_filesz, p_memsz, _align in headers:
        if p_offset > size or p_filesz > size - p_offset:
            bad('has a segment outside the file')
        if p_type == PT_INTERP:
            bad('needs a program interpreter (it is dynamically linked)', 'server_not_static')
        if p_type == PT_LOAD:
            if p_filesz > p_memsz:
                bad('has a segment larger in the file than in memory')
            executable = executable or bool(p_flags & PF_X)
        if p_type == PT_DYNAMIC:
            if dynamic is not None:
                bad('has two dynamic sections')
            dynamic = (p_offset, p_filesz)
    if not executable:
        bad('has no executable segment')
    if dynamic is not None:
        offset, length = dynamic
        for i in range(min(length // DYN.size, MAX_DYNAMIC + 1)):
            if i == MAX_DYNAMIC:
                bad('has more dynamic entries than this builder reads')
            tag, _value = DYN.unpack_from(data, offset + i * DYN.size)
            if tag == DT_NULL:
                break
            if tag == DT_NEEDED:
                bad('needs a shared library (it is dynamically linked)', 'server_not_static')
            if tag == DT_SONAME:
                bad('is a shared library, not an executable')
    elif e_type == ET_DYN:
        bad('is position-independent with no dynamic section, which no static-pie executable is')
    return ElfInfo(h.hexdigest(), size, e_type == ET_DYN)


def manifest(project_id: str, version_code: int, server: dict, migrations=(), *, shared: bool = False,
             signin_revision: str = SIGNIN_REVISION) -> dict:
    """The manifest of one version's server. ``server`` is its record ({sha256, size, target}, as
    rose.app_builder.server_build.ServerBuild.record gives it); ``migrations`` its migration lock entries
    ({scope, number, ...}), whose highest number per scope is the schema the server migrates to."""
    migrations = list(migrations)
    if any(not isinstance(m, dict) or m.get('scope') not in ('account', 'shared') or type(m.get('number')) is not int
           for m in migrations):
        raise Refused('A migration lock entry has no known scope or number.', 'manifest_invalid')
    schema = {'account': max((m['number'] for m in migrations if m['scope'] == 'account'), default=0)}
    if shared:
        schema['shared'] = max((m['number'] for m in migrations if m['scope'] == 'shared'), default=0)
    elif any(m['scope'] == 'shared' for m in migrations):
        raise Refused('A server without a shared database has shared migrations.', 'manifest_invalid')
    out = {'schema_version': SCHEMA_VERSION, 'project_id': project_id, 'version_code': version_code,
           'elf': {'sha256': server['sha256'], 'size': server['size'], 'target': server['target']},
           'listen': {'env': 'PORT', 'default': PORT_DEFAULT}, 'health': HEALTH, 'data_dir': {'env': 'DATA_DIR'},
           'signin': {'revision': signin_revision}, 'schema': schema}
    check(out)
    return out


def encode(m: dict) -> bytes:
    """The manifest's canonical bytes (what is stored next to the binary and hashed)."""
    check(m)
    return (json.dumps(m, sort_keys=True, separators=(',', ':'), ensure_ascii=True) + '\n').encode('ascii')


def decode(data: bytes) -> dict:
    """A manifest from bytes, strictly: UTF-8 JSON within bounds, no repeated key, no number JSON allows that the
    manifest's own grammar does not, then ``check``, and the bytes must be exactly ``encode``'s (one manifest has one
    form, so one digest)."""
    if not isinstance(data, (bytes, bytearray)) or len(data) > MAX_MANIFEST:
        raise Refused(f'The server manifest is larger than {MAX_MANIFEST // 1024} KB.', 'manifest_invalid')

    def pairs(items):
        keys = [k for k, _ in items]
        if len(set(keys)) != len(keys):
            raise ValueError('repeated key')
        return dict(items)

    def no_constant(name):
        raise ValueError(name)
    try:
        m = json.loads(bytes(data).decode('utf-8'), object_pairs_hook=pairs, parse_constant=no_constant,
                       parse_float=lambda s: no_constant(s))
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise Refused('The server manifest is not valid JSON.', 'manifest_invalid') from None
    check(m)
    if encode(m) != bytes(data):
        raise Refused('The server manifest is not in its canonical form.', 'manifest_invalid')
    return m


def _exact(value, keys) -> bool:
    return isinstance(value, dict) and set(value) == set(keys)


def _int(value, low: int, high: int) -> bool:
    return type(value) is int and low <= value <= high


def check(m, elf: Path | None = None, *, project_id: str | None = None, version_code: int | None = None) -> dict:
    """Refuse a manifest that is not exactly this schema, or (given them) that names another project or version,
    or whose binary at ``elf`` is not the one it records or not a static x86-64 executable."""
    def bad(why: str, reason: str = 'manifest_invalid'):
        raise Refused(f'The server manifest {why}.', reason)

    if not _exact(m, ('schema_version', 'project_id', 'version_code', 'elf', 'listen', 'health', 'data_dir',
                      'signin', 'schema')):
        bad('does not have exactly the fields of schema version 1')
    if not _int(m['schema_version'], SCHEMA_VERSION, SCHEMA_VERSION):
        bad('is not schema version 1')
    if not isinstance(m['project_id'], str) or not PROJECT_ID.fullmatch(m['project_id']):
        bad('has no valid project id')
    if not _int(m['version_code'], 1, MAX_VERSION_CODE):
        bad('has no valid version code')
    e = m['elf']
    if not _exact(e, ('sha256', 'size', 'target')) or not isinstance(e['sha256'], str) \
            or not HEX64.fullmatch(e['sha256']) or not _int(e['size'], 1, MAX_ELF) or e['target'] != TARGET:
        bad(f'does not describe one {TARGET} binary by sha256 and size')
    if m['listen'] != {'env': 'PORT', 'default': PORT_DEFAULT} or type(m['listen']['default']) is not int:
        bad(f'does not listen where the host says (PORT, default {PORT_DEFAULT})')
    if m['health'] != HEALTH:
        bad(f'does not serve readiness at {HEALTH}')
    if m['data_dir'] != {'env': 'DATA_DIR'}:
        bad('does not keep its data where the host says (DATA_DIR)')
    if m['signin'] != {'revision': SIGNIN_REVISION}:
        bad(f'is not built on rose-signin {SIGNIN_REVISION}')
    s = m['schema']
    if not isinstance(s, dict) or set(s) not in ({'account'}, {'account', 'shared'}) \
            or not all(_int(v, 0, MAX_SCHEMA) for v in s.values()):
        bad('does not state its schema versions')
    if project_id is not None and m['project_id'] != project_id:
        bad('is for another project')
    if version_code is not None and m['version_code'] != version_code:
        bad('is for another version')
    if elf is not None:
        info = check_elf(elf)
        if (info.sha256, info.size) != (e['sha256'], e['size']):
            bad('does not match its binary', 'server_binary_invalid')
    return m
