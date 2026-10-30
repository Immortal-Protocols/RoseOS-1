#!/usr/bin/env python3
"""Local identity check of the four Fold key-generation tools (FOLD-TOOL-PROOF-001, narrow correction).

The fold-rango-v1 recipe runs make_key ("generator"), decrypt-keys and avbtool, which run OpenSSL. As in the Frankel
release path, the managed host OS and its interpreters are the trusted computing base; this helper does not bootstrap
trust in the OS, parse packages or verify signatures itself. It checks, before login and again under the lock
immediately before use:

  pinned scripts   the bytes at <spec root>/<profile source.path> equal the profile's pinned git blob and sha256
                   (signing-bootstrap.verify_source), the profile revision equals the pinned-manifest revision when the
                   manifest lists the project, reads are contained and bounded, no ancestor and not the script itself
                   is a symlink, and no sibling avb_openssl overrides OpenSSL for avbtool.
  interpreters     taken from the verified shebang, at its absolute path or through the fixed PATH below (never the
                   caller's PATH); each is a regular, root-owned file not writable by group or others.
  OpenSSL          the `openssl` the scripts reach through the fixed PATH, with the same file checks, and its owning
                   package, version, architecture and package-manager integrity result from the host's own installed
                   official tools: exactly one of dpkg (dpkg-query -S / -W, dpkg --verify) or rpm (rpm -qf, rpm -V).
                   The trust basis is "managed-host-package": existing managed package trust, NOT an independent
                   cryptographic attestation. An independent signed-package proof is not performed; the result names
                   the exact retained inputs it would need.

  validate_spec(spec)                         pure hosted-guard shape check -> [fixed problem codes]
  verify_local(spec, root='/', runner=...)    the closed proof below, or Refused(<fixed code>)
  recheck(proof, root='/')                    re-hash and re-resolve immediately before use; Refused('changed_after_proof')
  CLI: python3 scripts/fold_tool_proof.py --spec <spec.json>   (prints the proof or `refused: <code>`; exit 0/1)

Every package-tool call is fixed argv from an allowlist, minimal environment, no stdin, bounded time and output.
The proof carries tool paths, public hashes, package identity and fixed results only; no file content or tool output.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
SPEC_SCHEMA = 'roseos.fold-tool-proof-spec.v2'
PROOF_SCHEMA = 'roseos.fold-tool-proof.v2'
PROFILE = 'fold-rango-v1'
SCRIPTS = ('generator', 'decrypt-keys', 'avbtool')
ENVIRONMENT = {'PATH': '/usr/bin:/bin', 'LC_ALL': 'C'}  # signing-bootstrap.subprocess_runner's fixed environment
SHEBANGS = {'/bin/bash': '/bin/bash', '/usr/bin/bash': '/usr/bin/bash', '/usr/bin/env python3': 'python3',
            '/usr/bin/python3': '/usr/bin/python3'}
SYSTEM_ALIASES = {'/bin': '/usr/bin', '/sbin': '/usr/sbin', '/lib': '/usr/lib', '/lib64': '/usr/lib64'}
MANAGERS = {'dpkg': ('/usr/bin/dpkg-query', '/usr/bin/dpkg'), 'rpm': ('/usr/bin/rpm',)}
SCRIPT_BYTES = 4 << 20
HOST_FILE_BYTES = 256 << 20
TOOL_SECONDS = 120
TOOL_OUTPUT_BYTES = 1 << 20
PACKAGE = re.compile(r'[A-Za-z0-9][A-Za-z0-9+._-]{0,99}')
VERSION = re.compile(r'[A-Za-z0-9.+~:_-]{1,100}')
ARCH = re.compile(r'[A-Za-z0-9_-]{1,30}')
REL = re.compile(r'[A-Za-z0-9_.+-]{1,128}(/[A-Za-z0-9_.+-]{1,128}){0,15}')
INDEPENDENT_PROOF_REQUIRES = (
    'an approved vendor signing identity (fingerprint) recorded in the profile',
    'that vendor keyring already retained on the protected host',
    'the signed repository metadata for the installed release, already retained on the host',
    'the installed package file (.deb or .rpm) already in the host package cache',
    "the host's existing official verifier for that metadata (gpgv or rpm --checksig)")


class Refused(Exception):
    """A fixed refusal code; no file content, path or tool output is carried."""


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SB = _load('signing_bootstrap_for_fold_tool_proof', ROOT / 'scripts/signing-bootstrap.py')


def _abs(value):
    return isinstance(value, str) and SB.approved_path(value) and value != '/'


def validate_spec(spec):
    """Pure shape check (hosted guard). [] means the spec may be verified locally."""
    if not isinstance(spec, dict) or set(spec) != {'schema', 'profile', 'scripts'}:
        return ['spec_fields']
    problems = []
    if spec['schema'] != SPEC_SCHEMA or spec['profile'] != PROFILE:
        problems.append('spec_schema')
    scripts = spec['scripts']
    if not isinstance(scripts, dict) or set(scripts) != set(SCRIPTS) or not all(
            isinstance(v, dict) and set(v) == {'root'} and _abs(v['root']) for v in scripts.values()):
        problems.append('spec_scripts')
    return problems


# ------------------------------------------------------------------ files

def _under(root, path):
    return Path(path) if str(root) == '/' else Path(str(root).rstrip('/') + path)


def _lstat(path):
    try:
        return os.lstat(path)
    except OSError:
        raise Refused('file_missing') from None


def _walk(path, root, aliases, owner):
    """The logical path of absolute `path` after its ancestors. Ancestors may not be symlinks (merged-/usr aliases only
    when `aliases`); with `owner` set, each ancestor must be owned by it and not writable by group or others."""
    if not _abs(path):
        raise Refused('path_unsafe')
    parts, logical = PurePosixPath(path).parts[1:], ''
    for part in parts[:-1]:
        logical += '/' + part
        on_disk = _under(root, logical)
        info = _lstat(on_disk)
        if stat.S_ISLNK(info.st_mode):
            target = SYSTEM_ALIASES.get(logical)
            if not (aliases and target and os.readlink(on_disk).lstrip('/') == target.lstrip('/')):
                raise Refused('symlink_ancestor')
            logical = target
            info = _lstat(_under(root, logical))
            if stat.S_ISLNK(info.st_mode):
                raise Refused('symlink_ancestor')
        if not stat.S_ISDIR(info.st_mode):
            raise Refused('path_unsafe')
        if owner is not None and (info.st_uid != owner or info.st_mode & 0o022):
            raise Refused('host_file_unsafe')
    return logical + '/' + parts[-1]


def _read(path, root, limit, aliases=False, owner=None):
    """(sha256, bytes) of a regular file that is not a symlink, reached by `_walk`, within `limit` bytes."""
    logical = _walk(path, root, aliases, owner)
    try:
        fd = os.open(_under(root, logical), os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        raise Refused('file_missing') from None
    with os.fdopen(fd, 'rb') as f:
        info = os.fstat(f.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise Refused('not_regular')
        if owner is not None and (info.st_uid != owner or info.st_mode & 0o022):
            raise Refused('host_file_unsafe')
        if info.st_size > limit:
            raise Refused('bound_exceeded')
        data = f.read(limit + 1)
    if len(data) > limit:
        raise Refused('bound_exceeded')
    return hashlib.sha256(data).hexdigest(), data


def _unalias(path):
    for alias, target in SYSTEM_ALIASES.items():
        if path.startswith(alias + '/'):
            return target + path[len(alias):]
    return path


def host_file(path, root='/', owner=0):
    """{path, realPath, sha256} of a host executable: symlinks followed (at most 8, each step's ancestors checked),
    the final file regular, executable, owned by `owner` and not writable by group or others."""
    logical = path
    for _ in range(8):
        on_disk = _under(root, _walk(logical, root, True, owner))
        info = _lstat(on_disk)
        if stat.S_ISLNK(info.st_mode):
            if info.st_uid != owner:
                raise Refused('host_file_unsafe')
            target = os.readlink(on_disk)
            logical = os.path.normpath(target if target.startswith('/') else
                                       str(PurePosixPath(_unalias(logical)).parent / target))
            continue
        if not info.st_mode & 0o111:
            raise Refused('host_file_unsafe')
        digest, _ = _read(logical, root, HOST_FILE_BYTES, aliases=True, owner=owner)
        return {'path': path, 'realPath': _unalias(logical), 'sha256': digest}
    raise Refused('host_file_unsafe')


def resolve(name, root='/', owner=0):
    """`name` through the fixed PATH only (as the scripts will find it); the first existing entry wins."""
    for directory in ENVIRONMENT['PATH'].split(':'):
        candidate = f'{directory}/{name}'
        try:
            exists = os.path.lexists(_under(root, _walk(candidate, root, True, None)))
        except Refused:
            exists = False
        if exists:
            return host_file(candidate, root, owner)
    raise Refused('tool_missing')


# ------------------------------------------------------------------ pinned scripts

def _manifest_revisions(root=ROOT):
    text = (Path(root) / SB.MANIFEST_PATH).read_text()
    return {m[2]: m[3] for m in re.finditer(r'<project ([^>]*?)path="([^"]+)"[^>]*revision="([0-9a-f]{40})"', text)}


def _project_path(identity):
    match = re.search(r'\(path ([A-Za-z0-9_./-]+)\)', str(identity['source'].get('project', '')))
    return match[1] if match else None


def _script(name, identity, spec_root, manifest, root):
    if SB.source_problems({'tools': {name: identity}}):
        raise Refused('profile_identity_missing')
    source = identity['source']
    project = _project_path(identity)
    if project is not None and project in manifest and manifest[project] != source['revision']:
        raise Refused('source_revision_mismatch')
    if project is not None and not spec_root.rstrip('/').endswith('/' + project):
        raise Refused('source_root_mismatch')
    if not REL.fullmatch(source['path']) or '..' in source['path'].split('/'):
        raise Refused('path_unsafe')
    path = spec_root.rstrip('/') + '/' + source['path']
    _, data = _read(path, root, SCRIPT_BYTES)
    if SB.verify_source(identity, data):
        raise Refused('source_mismatch')
    first = data.split(b'\n', 1)[0]
    shebang = ' '.join(first[2:].decode('ascii', 'replace').split()) if first.startswith(b'#!') else ''
    if shebang not in SHEBANGS:
        raise Refused('interpreter_unsupported')
    return path, SHEBANGS[shebang]


# ------------------------------------------------------------------ package manager (existing official tools)

def run_tool(argv):
    """Production boundary for the allowlisted package queries: fixed argv, fixed environment, no stdin, bounded."""
    try:
        result = subprocess.run(argv, env=dict(ENVIRONMENT), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, timeout=TOOL_SECONDS)
    except (OSError, subprocess.TimeoutExpired):
        raise Refused('package_query_failed') from None
    if len(result.stdout) > TOOL_OUTPUT_BYTES:
        raise Refused('bound_exceeded')
    return result.returncode, result.stdout.decode('utf-8', 'replace')


def package_manager(root='/', owner=0):
    """Exactly one installed official package manager (dpkg or rpm), its tools themselves safe host files."""
    present = [name for name, tools in MANAGERS.items() if all(os.path.lexists(_under(root, t)) for t in tools)]
    if not present:
        raise Refused('package_manager_missing')
    if len(present) > 1:
        raise Refused('package_manager_ambiguous')
    for tool in MANAGERS[present[0]]:
        host_file(tool, root, owner)
    return present[0]


def _alternatives(path):
    out = [path]
    for alias, target in SYSTEM_ALIASES.items():
        if path.startswith(target + '/'):
            out.append(alias + path[len(target):])
    return out


def package_identity(manager, real_path, runner):
    """{manager, name, version, architecture} and the integrity result, from the host's own package tools."""
    if manager == 'dpkg':
        owners, queried = None, None
        for candidate in _alternatives(real_path):
            code, out = runner(['/usr/bin/dpkg-query', '-S', candidate])
            lines = [line for line in out.splitlines() if line.strip()]
            if code == 0 and lines:
                owners, queried = [line.split(': ', 1) for line in lines], candidate
                break
        if not owners:
            raise Refused('package_owner_missing')
        if len(owners) != 1 or len(owners[0]) != 2 or owners[0][1] != queried or ', ' in owners[0][0] \
                or owners[0][0].startswith('diversion by'):
            raise Refused('package_owner_ambiguous')
        name = owners[0][0].split(':', 1)[0]
        code, out = runner(['/usr/bin/dpkg-query', '-W', '-f',
                            '${Package}\\t${Version}\\t${Architecture}\\t${Status}\\n', name])
        rows = [line.split('\t') for line in out.splitlines() if line.strip()]
        if code != 0 or len(rows) != 1 or len(rows[0]) != 4 or rows[0][3] != 'install ok installed':
            raise Refused('package_query_failed')
        _, version, arch, _ = rows[0]
        code, out = runner(['/usr/bin/dpkg', '--verify', name])
        integrity = {'tool': 'dpkg --verify', 'result': 'clean' if code == 0 and not out.strip() else 'failed'}
    else:
        code, out = runner(['/usr/bin/rpm', '-qf', '--qf', '%{NAME}\\t%{VERSION}-%{RELEASE}\\t%{ARCH}\\n', real_path])
        rows = [line.split('\t') for line in out.splitlines() if line.strip()]
        if code != 0 or not rows:
            raise Refused('package_owner_missing')
        if len(rows) != 1 or len(rows[0]) != 3:
            raise Refused('package_owner_ambiguous')
        name, version, arch = rows[0]
        code, out = runner(['/usr/bin/rpm', '-V', name])
        integrity = {'tool': 'rpm -V', 'result': 'clean' if code == 0 and not out.strip() else 'failed'}
    if not (PACKAGE.fullmatch(name) and VERSION.fullmatch(version) and ARCH.fullmatch(arch)):
        raise Refused('package_query_failed')
    if integrity['result'] != 'clean':
        raise Refused('package_integrity_failed')
    return {'manager': manager, 'name': name, 'version': version, 'architecture': arch}, integrity


# ------------------------------------------------------------------ verify / recheck

def default_profile(root=ROOT):
    return SB.load_profiles(root)['profiles'][PROFILE]


def verify_local(spec, root='/', profile=None, manifest=None, runner=run_tool, owner=0):
    """The closed proof for `spec`, or Refused. `profile`/`manifest` default to the checked-in fold-rango-v1 profile
    and pinned manifest; `runner` is the allowlisted package-tool boundary (mocked in tests)."""
    if validate_spec(spec):
        raise Refused('spec_invalid')
    profile = profile if profile is not None else default_profile()
    manifest = manifest if manifest is not None else _manifest_revisions()
    if not all(name in profile.get('tools', {}) for name in SCRIPTS):
        raise Refused('profile_identity_missing')
    tools = {}
    for name in SCRIPTS:
        identity = profile['tools'][name]
        path, interpreter = _script(name, identity, spec['scripts'][name]['root'], manifest, root)
        resolved = host_file(interpreter, root, owner) if interpreter.startswith('/') else resolve(interpreter, root, owner)
        tools[name] = {'path': path, 'sha256': identity['sourceSha256'], 'gitBlob': identity['source']['gitBlob'],
                       'revision': identity['source']['revision'], 'trustBasis': 'pinned-source',
                       'interpreter': resolved, 'invocation': [resolved['path'], path]}
    if os.path.lexists(_under(root, spec['scripts']['avbtool']['root'].rstrip('/') + '/avb_openssl')):
        raise Refused('openssl_override')  # avbtool prefers a sibling avb_openssl over PATH
    openssl = resolve('openssl', root, owner)
    manager = package_manager(root, owner)
    package, integrity = package_identity(manager, openssl['realPath'], runner)
    tools['openssl'] = dict(openssl, trustBasis='managed-host-package', package=package, integrity=integrity)
    return {'schema': PROOF_SCHEMA, 'profile': PROFILE, 'environment': dict(ENVIRONMENT), 'tools': tools,
            'independentPackageProof': {'performed': False, 'requires': list(INDEPENDENT_PROOF_REQUIRES)}}


def recheck(proof, root='/', owner=0):
    """Immediately before use: every proven file has the same bytes, `openssl` and each interpreter still resolve to
    the same file, and no avb_openssl appeared. Any difference refuses."""
    try:
        for name in SCRIPTS:
            tool = proof['tools'][name]
            if _read(tool['path'], root, SCRIPT_BYTES)[0] != tool['sha256']:
                raise Refused('changed_after_proof')
            if host_file(tool['interpreter']['path'], root, owner) != tool['interpreter']:
                raise Refused('changed_after_proof')
        avbtool_dir = str(PurePosixPath(proof['tools']['avbtool']['path']).parent)
        if os.path.lexists(_under(root, avbtool_dir + '/avb_openssl')):
            raise Refused('changed_after_proof')
        current = resolve('openssl', root, owner)
        if any(current[k] != proof['tools']['openssl'][k] for k in ('path', 'realPath', 'sha256')):
            raise Refused('changed_after_proof')
    except Refused:
        raise Refused('changed_after_proof') from None
    return True


def main(argv=None):
    parser = argparse.ArgumentParser(description='Check the Fold tool identities (fixed allowlisted queries only).')
    parser.add_argument('--spec', required=True)
    args = parser.parse_args(argv)
    try:
        proof = verify_local(json.loads(Path(args.spec).read_text()))
    except Refused as error:
        print(f'refused: {error}', file=sys.stderr)
        return 1
    except (OSError, ValueError):
        print('refused: spec_invalid', file=sys.stderr)
        return 1
    print(json.dumps(proof, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    sys.exit(main())
