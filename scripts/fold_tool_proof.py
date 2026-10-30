#!/usr/bin/env python3
"""Local identity proof of the four Fold key-generation tools (FOLD-TOOL-PROOF-001).

The fold-rango-v1 recipe runs make_key ("generator"), decrypt-keys and avbtool, which in turn run OpenSSL. This module
proves, before login and again under the lock immediately before use, exactly which local files those are. It never
executes a proven or unproven file, reads no network, metadata API or secret, and mutates nothing.

  validate_spec(spec)                pure hosted-guard check of the spec shape; returns a list of fixed problem codes.
  verify_local(spec, root='/')       bounded local verification; returns the closed proof below or raises
                                     Refused(<fixed code>). `root` prefixes every absolute path (tests use a task-owned
                                     fixture root; production passes '/').
  recheck(proof, root='/')           re-hashes every proven file; raises Refused('changed_after_proof') on any change.

Trust bases.
  pinned-source   generator, decrypt-keys and avbtool: the bytes at <spec root>/<profile source.path> (contained, regular,
                  no symlink in any ancestor, bounded) must equal the profile's pinned git blob and sha256
                  (signing-bootstrap.verify_source), and the profile revision must equal the pinned-manifest revision
                  of that project path when the manifest lists it. Each script's interpreter comes from its verified
                  shebang and is mapped to a spec-named host file; nothing is resolved through PATH.
  vendor-package  OpenSSL, the interpreters and every ELF dependency: an approved OpenPGP fingerprint (from the profile,
                  never from the spec or caller) must equal the retained public key's v4 fingerprint; that key's RSA
                  v4 signature over the retained clearsigned InRelease must verify; the retained Packages index must
                  match its SHA256 entry in that Release; each named package's stanza (exactly one per name, version and
                  architecture) gives the SHA256 and size of the retained .deb; the .deb's data member (xz, gzip or
                  uncompressed tar; zstd and others refuse) lists the package files; and every installed file must
                  equal its package member byte for byte (a final symlink only where the member is the same symlink).
  ELF closure     PT_INTERP and DT_NEEDED are parsed from proven bytes (never ldd); each needed library resolves in the
                  spec's library directories to exactly one real file that is itself package-proven, transitively.
                  RPATH/RUNPATH, a missing library and two distinct candidates refuse.

The proof (closed, nonsecret, no file content):
  {schema, profile, tools: {generator|decrypt-keys|avbtool: {path, sha256, gitBlob, revision, trustBasis,
   interpreter}, openssl: {path, sha256, trustBasis, package}}, hostFiles: {path: {sha256, package, role}},
   vendor: {fingerprint, anchorSource, releaseSha256, indexSha256, suite}, environment: {PATH, LC_ALL}, limits}
A proof made from synthetic test metadata proves only the test contract, never production vendor trust.

Not yet covered (continuation): the Python standard-library import closure of avbtool.py and the OpenSSL config and
provider modules the recipe loads.
"""
import base64
import gzip
import hashlib
import importlib.util
import io
import json
import lzma
import os
from pathlib import Path, PurePosixPath
import re
import stat
import struct
import tarfile
import time

ROOT = Path(__file__).resolve().parents[1]
SPEC_SCHEMA = 'roseos.fold-tool-proof-spec.v1'
PROOF_SCHEMA = 'roseos.fold-tool-proof.v1'
PROFILE = 'fold-rango-v1'
SCRIPTS = ('generator', 'decrypt-keys', 'avbtool')
TOOLS = SCRIPTS + ('openssl',)
INTERPRETERS = {'/bin/bash': 'bash', '/usr/bin/bash': 'bash', '/usr/bin/env python3': 'python3',
                '/usr/bin/python3': 'python3'}
# Merged-/usr aliases are the only ancestor symlinks accepted for host files.
SYSTEM_ALIASES = {'/bin': '/usr/bin', '/sbin': '/usr/sbin', '/lib': '/usr/lib', '/lib64': '/usr/lib64'}
LIMITS = {'seconds': 600, 'scriptBytes': 4 << 20, 'metadataBytes': 4 << 20, 'indexBytes': 256 << 20,
          'debBytes': 512 << 20, 'members': 50000, 'memberBytes': 256 << 20, 'elfBytes': 256 << 20,
          'libraries': 200}
HEX40 = re.compile(r'[0-9A-F]{40}')
HEX64 = re.compile(r'[0-9a-f]{64}')
NAME = re.compile(r'[a-z0-9][a-z0-9+.-]{0,99}')
VERSION = re.compile(r'[A-Za-z0-9.+~:-]{1,100}')
ARCH = re.compile(r'[a-z0-9-]{1,30}')
REL = re.compile(r'[A-Za-z0-9_.+-]{1,128}(/[A-Za-z0-9_.+-]{1,128}){0,15}')


class Refused(Exception):
    """A fixed refusal code; no file content, path or tool output is carried."""


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SB = _load('signing_bootstrap_for_fold_tool_proof', ROOT / 'scripts/signing-bootstrap.py')


# ------------------------------------------------------------------ spec

def _abs(value):
    return isinstance(value, str) and SB.approved_path(value) and value != '/'


def validate_spec(spec):
    """Pure shape check (hosted guard). Returns fixed problem codes; [] means the spec may be verified locally."""
    problems = []
    if not isinstance(spec, dict) or set(spec) != {'schema', 'profile', 'scripts', 'hostFiles', 'libraryDirectories',
                                                   'vendor'}:
        return ['spec_fields']
    if spec['schema'] != SPEC_SCHEMA or spec['profile'] != PROFILE:
        problems.append('spec_schema')
    scripts = spec['scripts']
    if not isinstance(scripts, dict) or set(scripts) != set(SCRIPTS) or not all(
            isinstance(v, dict) and set(v) == {'root'} and _abs(v['root']) for v in scripts.values()):
        problems.append('spec_scripts')
    host = spec['hostFiles']
    if not isinstance(host, dict) or not {'openssl', 'bash', 'python3'} <= set(host) or not all(
            NAME.fullmatch(k) and isinstance(v, dict) and set(v) == {'path', 'package'} and _abs(v['path'])
            and NAME.fullmatch(str(v['package'])) for k, v in host.items()):
        problems.append('spec_host_files')
    dirs = spec['libraryDirectories']
    if not isinstance(dirs, list) or not 1 <= len(dirs) <= 8 or not all(_abs(d) for d in dirs) or len(set(dirs)) != len(dirs):
        problems.append('spec_library_directories')
    vendor = spec['vendor']
    if not isinstance(vendor, dict) or set(vendor) != {'publicKey', 'release', 'suite', 'index', 'packages'}:
        problems.append('spec_vendor')
    else:
        index = vendor['index']
        if not (_abs(vendor['publicKey']) and _abs(vendor['release']) and isinstance(vendor['suite'], str)
                and NAME.fullmatch(vendor['suite']) and isinstance(index, dict) and set(index) == {'path', 'releasePath'}
                and _abs(index['path']) and isinstance(index['releasePath'], str) and REL.fullmatch(index['releasePath'])):
            problems.append('spec_vendor')
        packages = vendor['packages']
        if not isinstance(packages, dict) or not 1 <= len(packages) <= 64 or not all(
                NAME.fullmatch(k) and isinstance(v, dict) and set(v) == {'version', 'architecture', 'deb'}
                and VERSION.fullmatch(str(v['version'])) and ARCH.fullmatch(str(v['architecture'])) and _abs(v['deb'])
                for k, v in packages.items()):
            problems.append('spec_packages')
        elif isinstance(host, dict) and not all(isinstance(v, dict) and v.get('package') in packages
                                                for v in host.values()):
            problems.append('spec_host_package_unlisted')
    return problems


# ------------------------------------------------------------------ bounded local reads

class _Budget:
    def __init__(self, clock=time.monotonic):
        self.clock, self.end = clock, clock() + LIMITS['seconds']

    def check(self):
        if self.clock() > self.end:
            raise Refused('bound_exceeded')


def _under(root, path):
    return Path(str(root).rstrip('/') + path) if str(root) != '/' else Path(path)


def _read_regular(path, limit, budget, allow_aliases=False, root='/'):
    """Bytes of a regular file reached without any symlink in its ancestors (merged-/usr aliases only when allowed),
    not itself a symlink, within `limit` bytes."""
    real = _resolve_ancestors(path, allow_aliases, root)
    try:
        fd = os.open(real, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        raise Refused('file_missing') from None
    with os.fdopen(fd, 'rb') as f:
        info = os.fstat(f.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise Refused('not_regular')
        if info.st_size > limit:
            raise Refused('bound_exceeded')
        chunks, size = [], 0
        while True:
            budget.check()
            block = f.read(1 << 20)
            if not block:
                break
            size += len(block)
            if size > limit:
                raise Refused('bound_exceeded')
            chunks.append(block)
    return b''.join(chunks), info


def _resolve_ancestors(path, allow_aliases, root):
    """The on-disk path of absolute `path` under `root`, refusing a symlink in any ancestor directory except the fixed
    merged-/usr aliases (when allowed)."""
    if not _abs(path):
        raise Refused('path_unsafe')
    parts = PurePosixPath(path).parts[1:]
    logical = ''
    for part in parts[:-1]:
        logical += '/' + part
        on_disk = _under(root, logical)
        try:
            info = os.lstat(on_disk)
        except OSError:
            raise Refused('file_missing') from None
        if stat.S_ISLNK(info.st_mode):
            target = SYSTEM_ALIASES.get(logical)
            if not (allow_aliases and target and os.readlink(on_disk).lstrip('/') == target.lstrip('/')):
                raise Refused('symlink_ancestor')
            logical = target
        elif not stat.S_ISDIR(info.st_mode):
            raise Refused('path_unsafe')
    return _under(root, logical + '/' + parts[-1])


# ------------------------------------------------------------------ pinned scripts

def _manifest_revisions(root=ROOT):
    text = (Path(root) / SB.MANIFEST_PATH).read_text()
    return {m[2]: m[3] for m in re.finditer(r'<project ([^>]*?)path="([^"]+)"[^>]*revision="([0-9a-f]{40})"', text)}


def _project_path(identity):
    match = re.search(r'\(path ([A-Za-z0-9_./-]+)\)', str(identity['source'].get('project', '')))
    return match[1] if match else None


def _shebang(data):
    """(interpreter name, absolute shebang path or None for `env`) from verified script bytes; never executed."""
    first = data.split(b'\n', 1)[0]
    if not first.startswith(b'#!') or len(first) > 128:
        raise Refused('interpreter_unsupported')
    line = ' '.join(first[2:].decode('ascii', 'replace').split())
    if line not in INTERPRETERS:
        raise Refused('interpreter_unsupported')
    return INTERPRETERS[line], None if line.startswith('/usr/bin/env ') else line


def _unalias(path):
    for alias, target in SYSTEM_ALIASES.items():
        if path.startswith(alias + '/'):
            return target + path[len(alias):]
    return path


def _prove_script(name, identity, spec_root, manifest, budget):
    if SB.source_problems({'tools': {name: identity}}):
        raise Refused('profile_identity_missing')
    source = identity['source']
    project = _project_path(identity)
    if project is not None and project in manifest and manifest[project] != source['revision']:
        raise Refused('source_revision_mismatch')
    if project is not None and not spec_root.rstrip('/').endswith('/' + project):
        raise Refused('source_root_mismatch')
    relative = source['path']
    if not REL.fullmatch(relative) or '..' in relative.split('/'):
        raise Refused('path_unsafe')
    data, _ = _read_regular(spec_root.rstrip('/') + '/' + relative, LIMITS['scriptBytes'], budget, root='/')
    if SB.verify_source(identity, data):
        raise Refused('source_mismatch')
    return data


# ------------------------------------------------------------------ OpenPGP (RSA v4, clearsigned)

DIGEST_INFO = {8: ('sha256', bytes.fromhex('3031300d060960864801650304020105000420')),
               10: ('sha512', bytes.fromhex('3051300d060960864801650304020305000440'))}


def _dearmor(text, kind):
    m = re.search(rf'-----BEGIN PGP {kind}-----\r?\n((?:[A-Za-z]+: [^\n]*\r?\n)*)\r?\n(.*?)-----END PGP {kind}-----',
                  text, re.S)
    if not m:
        raise Refused('signature_invalid' if kind == 'SIGNATURE' else 'vendor_key_unsupported')
    body = ''.join(line.strip() for line in m[2].splitlines() if line.strip() and not line.startswith('='))
    try:
        return base64.b64decode(body, validate=True)
    except ValueError:
        raise Refused('signature_invalid') from None


def _packets(data):
    out, i = [], 0
    while i < len(data):
        tag_byte = data[i]
        if not tag_byte & 0x80:
            raise Refused('signature_invalid')
        if tag_byte & 0x40:  # new format
            tag, i = tag_byte & 0x3F, i + 1
            first = data[i]
            if first < 192:
                length, i = first, i + 1
            elif first < 224:
                length, i = ((first - 192) << 8) + data[i + 1] + 192, i + 2
            elif first == 255:
                length, i = struct.unpack('>I', data[i + 1:i + 5])[0], i + 5
            else:
                raise Refused('signature_invalid')  # partial lengths are not used by keys or detached signatures
        else:
            tag, kind, i = (tag_byte >> 2) & 0x0F, tag_byte & 3, i + 1
            if kind == 3:
                raise Refused('signature_invalid')
            size = (1, 2, 4)[kind]
            length, i = int.from_bytes(data[i:i + size], 'big'), i + size
        if i + length > len(data):
            raise Refused('signature_invalid')
        out.append((tag, data[i:i + length]))
        i += length
    return out


def _mpi(data, i):
    bits = int.from_bytes(data[i:i + 2], 'big')
    n = (bits + 7) // 8
    return int.from_bytes(data[i + 2:i + 2 + n], 'big'), i + 2 + n


def public_key(armored):
    """(v4 fingerprint, n, e) of the primary RSA key in an armored public key block."""
    for tag, body in _packets(_dearmor(armored, 'PUBLIC KEY BLOCK')):
        if tag == 6:
            if body[0] != 4 or body[5] not in (1, 3):
                raise Refused('vendor_key_unsupported')
            n, i = _mpi(body, 6)
            e, _ = _mpi(body, i)
            if n.bit_length() < 2048:
                raise Refused('vendor_key_unsupported')
            fingerprint = hashlib.sha1(b'\x99' + len(body).to_bytes(2, 'big') + body).hexdigest().upper()
            return fingerprint, n, e
    raise Refused('vendor_key_unsupported')


def verify_clearsigned(text, key):
    """The signed text of a clearsigned message if an RSA v4 signature by `key` (its primary key) verifies."""
    fingerprint, n, e = key
    m = re.match(r'-----BEGIN PGP SIGNED MESSAGE-----\r?\n((?:[A-Za-z]+: [^\n]*\r?\n)*)\r?\n(.*?)\r?\n'
                 r'(-----BEGIN PGP SIGNATURE-----.*-----END PGP SIGNATURE-----)\s*\Z', text, re.S)
    if not m:
        raise Refused('signature_invalid')
    lines = [line[2:] if line.startswith('- ') else line for line in m[2].split('\n')]
    signed = '\n'.join(lines)
    canonical = '\r\n'.join(line.rstrip(' \t\r') for line in lines).encode()
    for tag, body in _packets(_dearmor(m[3], 'SIGNATURE')):
        if tag != 2 or len(body) < 6 or body[0] != 4 or body[1] != 0x01 or body[2] not in (1, 3) or body[3] not in DIGEST_INFO:
            continue
        hashed_len = int.from_bytes(body[4:6], 'big')
        hashed = body[:6 + hashed_len]
        if not _issuer_matches(body[6:6 + hashed_len], body[6 + hashed_len:], fingerprint):
            continue
        unhashed_len = int.from_bytes(body[6 + hashed_len:8 + hashed_len], 'big')
        i = 8 + hashed_len + unhashed_len + 2
        s, _ = _mpi(body, i)
        name, prefix = DIGEST_INFO[body[3]]
        digest = hashlib.new(name, canonical + hashed + b'\x04\xff' + len(hashed).to_bytes(4, 'big')).digest()
        k = (n.bit_length() + 7) // 8
        encoded = b'\x00\x01' + b'\xff' * (k - 3 - len(prefix) - len(digest)) + b'\x00' + prefix + digest
        if 0 < s < n and pow(s, e, n).to_bytes(k, 'big') == encoded:
            return signed
    raise Refused('signature_invalid')


def _issuer_matches(hashed, unhashed_with_len, fingerprint):
    issuers = set()
    areas = [hashed]
    if len(unhashed_with_len) >= 2:
        areas.append(unhashed_with_len[2:2 + int.from_bytes(unhashed_with_len[:2], 'big')])
    for area in areas:
        i = 0
        while i < len(area):
            first = area[i]
            if first < 192:
                length, i = first, i + 1
            elif first < 255:
                length, i = ((first - 192) << 8) + area[i + 1] + 192, i + 2
            else:
                length, i = int.from_bytes(area[i + 1:i + 5], 'big'), i + 5
            kind, value = area[i] & 0x7F, area[i + 1:i + length]
            if kind == 16:
                issuers.add(value.hex().upper())
            elif kind == 33 and value[:1] == b'\x04':
                issuers.add(value[1:].hex().upper())
            i += length
    return fingerprint in issuers or fingerprint[-16:] in issuers


# ------------------------------------------------------------------ Release, Packages, .deb

def _stanzas(text):
    for block in re.split(r'\n\s*\n', text.strip()):
        fields, last = {}, None
        for line in block.split('\n'):
            if line[:1] in (' ', '\t') and last:
                fields[last] += '\n' + line.strip()
            elif ':' in line:
                last, value = line.split(':', 1)
                fields[last] = value.strip()
        yield fields


def _release_entry(release_text, suite, release_path):
    fields = next(_stanzas(release_text), {})
    if suite not in (fields.get('Suite'), fields.get('Codename')):
        raise Refused('release_invalid')
    matches = [line.split() for line in fields.get('SHA256', '').split('\n')
               if len(line.split()) == 3 and line.split()[2] == release_path]
    if len(matches) != 1 or not HEX64.fullmatch(matches[0][0]) or not matches[0][1].isdigit():
        raise Refused('release_invalid')
    return matches[0][0], int(matches[0][1])


def _decompress(data, name, limit):
    if name.endswith('.xz'):
        stream = lzma.LZMADecompressor()
        out = stream.decompress(data, max_length=limit + 1)
    elif name.endswith('.gz'):
        stream = gzip.GzipFile(fileobj=io.BytesIO(data))
        out = stream.read(limit + 1)
    elif '.' not in name.rsplit('/', 1)[-1] or name.endswith('Packages'):
        out = data
    else:
        raise Refused('format_unsupported')
    if len(out) > limit:
        raise Refused('bound_exceeded')
    return out


def _package_stanza(index_text, name, version, arch):
    hits = [s for s in _stanzas(index_text) if s.get('Package') == name and s.get('Version') == version
            and s.get('Architecture') in (arch, 'all')]
    if len(hits) != 1:
        raise Refused('package_missing' if not hits else 'package_ambiguous')
    sha, size = hits[0].get('SHA256', ''), hits[0].get('Size', '')
    if not HEX64.fullmatch(sha) or not size.isdigit():
        raise Refused('index_invalid')
    return sha, int(size)


def _deb_members(data, budget):
    """{path: ('file', sha256, mode) | ('symlink', target) | ('dir',)} of a .deb's data member."""
    if not data.startswith(b'!<arch>\n'):
        raise Refused('format_unsupported')
    i, members, names = 8, {}, []
    while i < len(data):
        header = data[i:i + 60]
        if len(header) < 60 or header[58:60] != b'`\n':
            raise Refused('format_unsupported')
        name = header[:16].decode('ascii', 'replace').strip().rstrip('/')
        size = int(header[48:58].decode('ascii').strip() or 0)
        body = data[i + 60:i + 60 + size]
        names.append(name)
        if name.startswith('data.tar'):
            if name not in ('data.tar', 'data.tar.xz', 'data.tar.gz'):
                raise Refused('format_unsupported')  # zstd, bzip2 and others are not read here
            members = _tar_members(body, name, budget)
        i += 60 + size + (size & 1)
    if not names or names[0] != 'debian-binary' or not any(n.startswith('data.tar') for n in names):
        raise Refused('format_unsupported')
    return members


def _tar_members(body, name, budget):
    if name.endswith('.xz'):
        fileobj = lzma.LZMAFile(io.BytesIO(body))
    elif name.endswith('.gz'):
        fileobj = gzip.GzipFile(fileobj=io.BytesIO(body))
    else:
        fileobj = io.BytesIO(body)
    members = {}
    try:
        with tarfile.open(fileobj=fileobj, mode='r|') as tar:
            for count, info in enumerate(tar):
                budget.check()
                if count >= LIMITS['members']:
                    raise Refused('bound_exceeded')
                path = '/' + info.name.lstrip('./').rstrip('/')
                if '..' in PurePosixPath(path).parts:
                    raise Refused('format_unsupported')
                if info.isreg():
                    if info.size > LIMITS['memberBytes']:
                        raise Refused('bound_exceeded')
                    digest, f = hashlib.sha256(), tar.extractfile(info)
                    for block in iter(lambda: f.read(1 << 20), b''):
                        digest.update(block)
                    members[path] = ('file', digest.hexdigest(), info.mode)
                elif info.issym():
                    members[path] = ('symlink', info.linkname)
                elif info.isdir():
                    members[path] = ('dir',)
                elif info.islnk():
                    target = members.get('/' + info.linkname.lstrip('./'))
                    if not target:
                        raise Refused('format_unsupported')
                    members[path] = target
    except (tarfile.TarError, lzma.LZMAError, OSError, EOFError):
        raise Refused('format_unsupported') from None
    return members


# ------------------------------------------------------------------ ELF

def elf_dependencies(data):
    """(PT_INTERP path or None, [DT_NEEDED], has RPATH/RUNPATH) of a 64-bit little-endian ELF, parsed only."""
    if data[:4] != b'\x7fELF' or data[4] != 2 or data[5] != 1 or len(data) < 64:
        raise Refused('elf_invalid')
    phoff, = struct.unpack_from('<Q', data, 32)
    phentsize, phnum = struct.unpack_from('<HH', data, 54)
    if phentsize != 56 or phnum > 128 or phoff + phnum * 56 > len(data):
        raise Refused('elf_invalid')
    interp, dynamic, loads = None, None, []
    for k in range(phnum):
        p_type, _, p_offset, p_vaddr, _, p_filesz, _, _ = struct.unpack_from('<IIQQQQQQ', data, phoff + k * 56)
        if p_offset + p_filesz > len(data):
            raise Refused('elf_invalid')
        if p_type == 3:
            interp = data[p_offset:p_offset + p_filesz].split(b'\0', 1)[0].decode('ascii', 'replace')
        elif p_type == 2:
            dynamic = (p_offset, p_filesz)
        elif p_type == 1:
            loads.append((p_vaddr, p_offset, p_filesz))
    needed, strtab, runpath = [], None, False
    if dynamic:
        for j in range(dynamic[1] // 16):
            tag, value = struct.unpack_from('<qQ', data, dynamic[0] + j * 16)
            if tag == 0:
                break
            if tag == 1:
                needed.append(value)
            elif tag == 5:
                strtab = value
            elif tag in (15, 29):
                runpath = True
        if needed and strtab is None:
            raise Refused('elf_invalid')

    def offset(vaddr):
        for start, off, size in loads:
            if start <= vaddr < start + size:
                return off + vaddr - start
        raise Refused('elf_invalid')
    names = []
    for value in needed:
        at = offset(strtab) + value
        end = data.find(b'\0', at)
        if end < 0:
            raise Refused('elf_invalid')
        names.append(data[at:end].decode('ascii', 'replace'))
    return interp, names, runpath


# ------------------------------------------------------------------ verify

def default_profile(root=ROOT):
    return SB.load_profiles(root)['profiles'][PROFILE]


def verify_local(spec, root='/', profile=None, manifest=None, clock=time.monotonic):
    """The closed proof for `spec`, or Refused. `profile` and `manifest` default to the checked-in fold-rango-v1
    profile and pinned manifest; tests inject synthetic ones."""
    if validate_spec(spec):
        raise Refused('spec_invalid')
    anchor_source = 'os/signing-bootstrap/profiles.json' if profile is None else 'caller-supplied profile (test only)'
    profile = profile if profile is not None else default_profile()
    manifest = manifest if manifest is not None else _manifest_revisions()
    budget = _Budget(clock)
    tools = profile.get('tools', {})
    if set(tools) != set(TOOLS):
        raise Refused('profile_identity_missing')
    # The only trust anchor is the profile's approved vendor signing identity; a spec or caller key never is.
    trust = (tools['openssl'].get('trust') or {})
    anchor = str(trust.get('fingerprint') or '')
    if trust.get('basis') != 'vendor-package' or not HEX40.fullmatch(anchor):
        raise Refused('vendor_trust_unbound')

    # 1. pinned scripts and their interpreters
    proof_tools, interpreters = {}, {}
    for name in SCRIPTS:
        identity = tools[name]
        data = _prove_script(name, identity, _under(root, spec['scripts'][name]['root']).as_posix(), manifest, budget)
        interpreter, shebang_path = _shebang(data)
        interpreter_path = spec['hostFiles'][interpreter]['path']
        if shebang_path and _unalias(shebang_path) != _unalias(interpreter_path):
            raise Refused('interpreter_mismatch')  # the kernel would start a different interpreter
        interpreters[name] = interpreter
        script_path = spec['scripts'][name]['root'].rstrip('/') + '/' + identity['source']['path']
        proof_tools[name] = {'path': script_path, 'sha256': identity['sourceSha256'],
                             'gitBlob': identity['source']['gitBlob'], 'revision': identity['source']['revision'],
                             'trustBasis': 'pinned-source', 'interpreter': interpreter_path,
                             'invocation': [interpreter_path, script_path]}  # never through the shebang or PATH
    beside = spec['scripts']['avbtool']['root'].rstrip('/') + '/avb_openssl'
    if os.path.lexists(_under(root, beside)):
        raise Refused('dependency_ambiguous')  # avbtool would prefer it over the proven openssl

    # 2. vendor trust: signed Release, index, packages
    vendor = spec['vendor']
    key_text, _ = _read_regular(vendor['publicKey'], LIMITS['metadataBytes'], budget, root=root)
    key = public_key(key_text.decode('ascii', 'replace'))
    if key[0] != anchor:
        raise Refused('vendor_key_mismatch')
    release_bytes, _ = _read_regular(vendor['release'], LIMITS['metadataBytes'], budget, root=root)
    release_text = verify_clearsigned(release_bytes.decode('utf-8', 'replace'), key)
    index_sha, index_size = _release_entry(release_text, vendor['suite'], vendor['index']['releasePath'])
    index_bytes, _ = _read_regular(vendor['index']['path'], LIMITS['indexBytes'], budget, root=root)
    if hashlib.sha256(index_bytes).hexdigest() != index_sha or len(index_bytes) != index_size:
        raise Refused('index_mismatch')
    index_text = _decompress(index_bytes, vendor['index']['releasePath'], LIMITS['indexBytes']).decode('utf-8', 'replace')
    members, deb_digests = {}, {}
    for name, pkg in sorted(vendor['packages'].items()):
        sha, size = _package_stanza(index_text, name, pkg['version'], pkg['architecture'])
        deb, _ = _read_regular(pkg['deb'], LIMITS['debBytes'], budget, root=root)
        if hashlib.sha256(deb).hexdigest() != sha or len(deb) != size:
            raise Refused('package_digest_mismatch')
        deb_digests[name] = sha
        members[name] = _deb_members(deb, budget)

    # 3. host files (openssl, interpreters) and their ELF closure
    host_files = {}

    def prove_host(path, package_names, role):
        """Prove `path` equals a member of one of `package_names`; returns its bytes."""
        real_logical = path
        for _ in range(8):
            on_disk = _resolve_ancestors(real_logical, True, root)
            info = _lstat(on_disk)
            member = _member(members, package_names, real_logical)
            if stat.S_ISLNK(info.st_mode):
                if member is None or member[1][0] != 'symlink' or os.readlink(on_disk) != member[1][1]:
                    raise Refused('installed_mismatch')
                target = member[1][1]
                real_logical = target if target.startswith('/') else str(PurePosixPath(real_logical).parent / target)
                real_logical = os.path.normpath(real_logical)
                continue
            data, _ = _read_regular(real_logical, LIMITS['elfBytes'], budget, allow_aliases=True, root=root)
            if member is None or member[1][0] != 'file' or hashlib.sha256(data).hexdigest() != member[1][1]:
                raise Refused('installed_mismatch')
            real_logical = _unalias(real_logical)
            host_files[real_logical] = {'sha256': member[1][1], 'package': member[0], 'role': role}
            return real_logical, data
        raise Refused('installed_mismatch')

    queue = []
    for name in ('openssl', 'bash', 'python3'):
        entry = spec['hostFiles'][name]
        real, data = prove_host(entry['path'], [entry['package']], 'tool' if name == 'openssl' else 'interpreter')
        queue.append(data)
        if name == 'openssl':
            proof_tools['openssl'] = {'path': real, 'sha256': host_files[real]['sha256'],
                                      'trustBasis': 'vendor-package',
                                      'package': {'name': entry['package'], 'version': vendor['packages'][entry['package']]['version'],
                                                  'architecture': vendor['packages'][entry['package']]['architecture'],
                                                  'debSha256': deb_digests[entry['package']]}}
    seen = set()
    all_packages = sorted(vendor['packages'])
    while queue:
        budget.check()
        interp, needed, runpath = elf_dependencies(queue.pop())
        if runpath:
            raise Refused('dependency_ambiguous')
        for dep in ([interp] if interp else []):
            if dep not in seen:
                seen.add(dep)
                queue.append(prove_host(dep, all_packages, 'loader')[1])
        for soname in needed:
            if soname in seen:
                continue
            seen.add(soname)
            if len(seen) > LIMITS['libraries']:
                raise Refused('bound_exceeded')
            candidates = {}
            for directory in spec['libraryDirectories']:
                logical = directory.rstrip('/') + '/' + soname
                if os.path.lexists(_resolve_ancestors(logical, True, root)):
                    candidates.setdefault(_canonical(logical, root), logical)
            if not candidates:
                raise Refused('dependency_unresolved')
            if len(candidates) > 1:
                raise Refused('dependency_ambiguous')
            queue.append(prove_host(next(iter(candidates.values())), all_packages, 'library')[1])

    openssl_dir = str(PurePosixPath(spec['hostFiles']['openssl']['path']).parent)
    return {'schema': PROOF_SCHEMA, 'profile': PROFILE, 'tools': proof_tools, 'hostFiles': host_files,
            'vendor': {'fingerprint': anchor, 'anchorSource': anchor_source,
                       'releaseSha256': hashlib.sha256(release_bytes).hexdigest(), 'indexSha256': index_sha,
                       'suite': vendor['suite']},
            'environment': {'PATH': ':'.join(dict.fromkeys((openssl_dir, '/usr/bin', '/bin'))), 'LC_ALL': 'C'},
            'limits': dict(LIMITS)}


def _lstat(path):
    try:
        return os.lstat(path)
    except OSError:
        raise Refused('file_missing') from None


def _canonical(logical, root):
    real = os.path.realpath(_resolve_ancestors(logical, True, root))
    return real


def _member(members, package_names, path):
    """(package, member) for `path` in exactly one of the packages (merged-/usr aliases considered)."""
    alternatives = {path}
    for alias, target in SYSTEM_ALIASES.items():
        for a, b in ((alias, target), (target, alias)):
            if path.startswith(a + '/'):
                alternatives.add(b + path[len(a):])
    found = [(name, members[name][p]) for name in package_names for p in sorted(alternatives) if p in members[name]]
    distinct = {(n, m) for n, m in found}
    if len(distinct) > 1:
        raise Refused('dependency_ambiguous')
    return found[0] if found else None


def recheck(proof, root='/'):
    """Re-hash every proven file immediately before use; any change refuses."""
    budget = _Budget()
    paths = {t['path']: (t['sha256'], t['trustBasis'] != 'pinned-source') for t in proof['tools'].values()}
    paths.update({p: (f['sha256'], True) for p, f in proof['hostFiles'].items()})
    for path, (digest, aliases) in sorted(paths.items()):
        try:
            data, _ = _read_regular(path, max(LIMITS['elfBytes'], LIMITS['scriptBytes']), budget, allow_aliases=aliases,
                                    root=root)
        except Refused:
            raise Refused('changed_after_proof') from None
        if hashlib.sha256(data).hexdigest() != digest:
            raise Refused('changed_after_proof')
    return True


if __name__ == '__main__':
    import argparse
    import sys
    parser = argparse.ArgumentParser(description='Verify the Fold tool identities locally (no execution, no network).')
    parser.add_argument('--spec', required=True)
    parser.add_argument('--root', default='/')
    args = parser.parse_args()
    try:
        print(json.dumps(verify_local(json.loads(Path(args.spec).read_text()), args.root), indent=2, sort_keys=True))
    except Refused as error:
        print(f'refused: {error}', file=sys.stderr)
        sys.exit(1)
