#!/usr/bin/env python3
"""FOLD-OBSERVE-001: read-only factory observation of the retained Rango build 2026103001 on the original builder.

  check                                   hosted-safe: validate the committed bindings only; no host path is read
  run --run-id ID --recipe-sha SHA --out NEW_DIRECTORY
                                          builder, under the shared builder lock: hash and observe the fixed retained
                                          inputs and stage fastboot plus its dependencies task-locally (never executed)

Every path and hash is fixed by committed configuration (os/retained-rango/release.json, os/flavors/rango.json) or by
the retained signing marker those paths name; nothing is taken from arguments, the environment or a guess. A missing
binding refuses. The signed target-files is bound by the marker's signedTargetFilesSha256, and the marker itself by the
committed product, build, exact five public outputs, the full committed avb_pkmd.bin digest and the committed OTA
digest prefix and suffix. The otatools archive is bound by the committed provenance size and sha256 and by the
transported input receipt (itself bound by its committed sha256), and is verified before any member is read.

Outputs (only inside the newly created NEW_DIRECTORY, removed again on any refusal):
  target-files-observation.json   the pinned scripts/factory-package.py observe of the signed target-files
  firmware-members.json           sha256 and size of the selected RADIO/, VENDOR/firmware/dauntless/ and OTA members
  fastboot/bin/fastboot, fastboot/lib64/*
                                  otatools bin/fastboot and exactly its DT_NEEDED closure from lib64/, hashed;
                                  never executed here (a bounded --version belongs to the future operator helper)
  rango-factory-observe.json      the receipt binding all of the above

Never read: the first-use record directory (first-use.json), secrets, bootstrap receipts (held artifact 12961047183)
or any other path. No generator, signing, packaging, device or network action.

Exit status: 0 observed/checked, 3 refused (JSON on stdout), 2 usage error.
"""
import argparse
import contextlib
import fcntl
import hashlib
import importlib.util
import json
import os
import re
import shutil
import stat
import struct
import subprocess
import sys
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = 'roseos.rango-factory-observe.v1'
FIRMWARE_SCHEMA = 'roseos.rango-firmware-members.v1'
DEVICE, BUILD = 'rango', '2026103001'
HEX64 = re.compile(r'[0-9a-f]{64}')
HEX40 = re.compile(r'[0-9a-f]{40}')
RUN_ID = re.compile(r'[1-9][0-9]{0,19}')
LIB_NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9._+-]{0,127}')
CHUNK = 8 << 20
PUBLIC_OUTPUTS = (f'{DEVICE}-ota_update-{BUILD}.zip', f'{DEVICE}-stable', 'releasekey.x509.pem', 'avb_pkmd.bin', 'UPDATE.md')
FIRMWARE_MEMBER = re.compile(r'(RADIO/[^/]+|VENDOR/firmware/dauntless/[^/]+|OTA/android-info\.txt)')
# Host C runtime libraries fastboot may name but otatools never ships; recorded as system, never copied or trusted.
SYSTEM_LIBS = frozenset({'libc.so.6', 'libm.so.6', 'libdl.so.2', 'libpthread.so.0', 'librt.so.1', 'libgcc_s.so.1',
                         'ld-linux-x86-64.so.2'})
LIMITS = {'seconds': 25 * 60, 'markerBytes': 1 << 20, 'receiptBytes': 16 << 20, 'firmwareMembers': 128,
          'firmwareMemberBytes': 1 << 30, 'firmwareBytes': 4 << 30, 'fastbootLibs': 32, 'fastbootBytes': 256 << 20}


class Refused(Exception):
    """A fixed category and nonsecret detail, safe to log."""


class Deadline:
    def __init__(self, seconds, clock=time.monotonic):
        self.clock, self.end = clock, clock() + seconds

    def check(self, where):
        if self.clock() > self.end:
            raise Refused(f'time_bound: the observation exceeded its time bound at {where}')


def _load(path, label):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        raise Refused(f'binding_missing: {label} is unreadable') from None


def bindings(root=ROOT):
    """The fixed inputs, only from committed configuration. Every missing or null value refuses."""
    release = _load(Path(root)/'os/retained-rango/release.json', 'os/retained-rango/release.json')
    flavor = _load(Path(root)/'os/flavors/rango.json', 'os/flavors/rango.json')
    target, inputs, signing = release.get('target') or {}, release.get('input') or {}, release.get('signing') or {}
    transport, otatools = inputs.get('transport') or {}, signing.get('otatoolsProvenance') or {}
    first_use = (signing.get('identityPolicy') or {}).get('firstUse') or {}
    retained = [r for r in flavor.get('retained') or [] if r.get('build') == BUILD]
    abbrev = (retained[0].get('otaSha256Abbrev') if len(retained) == 1 else None) or ''
    values = {
        'target.device': target.get('device'), 'target.build': target.get('build'), 'target.product': target.get('product'),
        'signing.sourceRoot': signing.get('sourceRoot'), 'signing.lockPath': signing.get('lockPath'),
        'input.transport.directory': transport.get('directory'), 'input.transport.receiptSha256': transport.get('receiptSha256'),
        'input.receiptFile': inputs.get('receiptFile'), 'input.otatools': inputs.get('otatools'),
        'signing.otatoolsProvenance.sha256': otatools.get('sha256'), 'signing.otatoolsProvenance.bytes': otatools.get('bytes'),
        'signing.identityPolicy.firstUse.recordDir': first_use.get('recordDir'),
        'flavor.identity.avbPublicKeySha256': (flavor.get('identity') or {}).get('avbPublicKeySha256'),
        'flavor.retained.otaSha256Abbrev': abbrev or None,
    }
    missing = sorted(k for k, v in values.items() if v is None)
    if missing:
        raise Refused('binding_missing: ' + ' '.join(missing))
    abbrev_parts = re.fullmatch(r'([0-9a-f]{8})…([0-9a-f]{4})', abbrev)
    checks = [
        (values['target.device'] == DEVICE and values['target.build'] == BUILD, 'target is not rango 2026103001'),
        (HEX40.fullmatch(str(values['target.product'])), 'target.product is not a commit'),
        (HEX64.fullmatch(str(values['input.transport.receiptSha256'])), 'input.transport.receiptSha256 is not a sha256'),
        (HEX64.fullmatch(str(values['signing.otatoolsProvenance.sha256'])), 'otatools sha256 is not a sha256'),
        (isinstance(values['signing.otatoolsProvenance.bytes'], int) and values['signing.otatoolsProvenance.bytes'] > 0,
         'otatools bytes is not a size'),
        (HEX64.fullmatch(str(values['flavor.identity.avbPublicKeySha256'])), 'avbPublicKeySha256 is not a sha256'),
        (abbrev_parts, 'otaSha256Abbrev is not <8 hex>…<4 hex>'),
    ] + [(str(values[k]).startswith('/') and '..' not in str(values[k]).split('/'), f'{k} is not an absolute plain path')
         for k in ('signing.sourceRoot', 'signing.lockPath', 'input.transport.directory',
                   'signing.identityPolicy.firstUse.recordDir')] \
      + [(LIB_NAME.fullmatch(str(values[k])), f'{k} is not a plain file name') for k in ('input.receiptFile', 'input.otatools')]
    for ok, why in checks:
        if not ok:
            raise Refused(f'binding_invalid: {why}')
    releases = Path(values['signing.sourceRoot'])/'releases'/BUILD
    transport_dir = Path(values['input.transport.directory'])
    return {
        'lock': values['signing.lockPath'],
        'marker': str(releases/'.rose-actions-signed.json'),
        'targetFiles': str(releases/f'release-{DEVICE}-{BUILD}'/f'{DEVICE}-target_files.zip'),
        'inputReceipt': {'path': str(transport_dir/values['input.receiptFile']), 'sha256': values['input.transport.receiptSha256']},
        'otatools': {'path': str(transport_dir/values['input.otatools']), 'sha256': values['signing.otatoolsProvenance.sha256'],
                     'bytes': values['signing.otatoolsProvenance.bytes']},
        'product': values['target.product'],
        'avbPublicKeySha256': values['flavor.identity.avbPublicKeySha256'],
        'otaPrefix': abbrev_parts[1], 'otaSuffix': abbrev_parts[2],
        'excluded': [values['signing.identityPolicy.firstUse.recordDir']],
    }


def _excluded(path, excluded):
    path = os.path.normpath(path)
    return any(path == e or path.startswith(e.rstrip('/') + '/') for e in excluded)


def open_regular(path, label, excluded):
    """Open a fixed absolute path read-only: no symlink anywhere on the path, a regular file, never an excluded path."""
    path = str(path)
    if not path.startswith('/') or '..' in path.split('/') or _excluded(path, excluded):
        raise Refused(f'path_refused: {label}')
    current = '/'
    for part in [p for p in path.split('/') if p][:-1]:
        current = os.path.join(current, part)
        try:
            info = os.lstat(current)
        except OSError:
            raise Refused(f'input_missing: {label}') from None
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise Refused(f'path_refused: {label} has a symlink or non-directory ancestor')
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        raise Refused(f'input_missing: {label}') from None
    except OSError:
        raise Refused(f'path_refused: {label} is a symlink or unreadable') from None
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        raise Refused(f'path_refused: {label} is not a regular file')
    return os.fdopen(fd, 'rb')


def hash_stream(f, limit, label, deadline):
    digest, size = hashlib.sha256(), 0
    for block in iter(lambda: f.read(CHUNK), b''):
        size += len(block)
        if size > limit:
            raise Refused(f'size_bound: {label} exceeds {limit} bytes')
        digest.update(block)
        deadline.check(label)
    return size, digest.hexdigest()


def read_bounded(path, label, limit, excluded, deadline, sha256=None):
    with open_regular(path, label, excluded) as f:
        data = f.read(limit + 1)
    deadline.check(label)
    if len(data) > limit:
        raise Refused(f'size_bound: {label} exceeds {limit} bytes')
    if sha256 is not None and hashlib.sha256(data).hexdigest() != sha256:
        raise Refused(f'hash_mismatch: {label}')
    return data


def check_marker(data, b):
    """The retained signing marker (host-sign.py) must be exactly this product's build and its committed identities."""
    try:
        marker = json.loads(data)
    except ValueError:
        raise Refused('marker_invalid: unreadable') from None
    outputs = marker.get('outputs') if isinstance(marker, dict) else None
    if marker.get('candidate') != b['product'] or marker.get('build') != BUILD:
        raise Refused('marker_mismatch: not the retained product and build')
    if not isinstance(outputs, dict) or sorted(outputs) != sorted(PUBLIC_OUTPUTS) \
            or not all(HEX64.fullmatch(str(v)) for v in outputs.values()):
        raise Refused('marker_mismatch: not exactly the five signed public outputs')
    if outputs['avb_pkmd.bin'] != b['avbPublicKeySha256']:
        raise Refused('marker_mismatch: avb_pkmd.bin is not the committed Rango AVB key')
    ota = outputs[PUBLIC_OUTPUTS[0]]
    if not (ota.startswith(b['otaPrefix']) and ota.endswith(b['otaSuffix'])):
        raise Refused('marker_mismatch: the OTA digest is not the published rango-2026103001 OTA')
    signed = marker.get('signedTargetFilesSha256')
    if not HEX64.fullmatch(str(signed)):
        raise Refused('marker_mismatch: no signedTargetFilesSha256')
    return signed, ota


def _member(archive, name, label):
    infos = [i for i in archive.infolist() if i.filename == name]
    if len(infos) != 1:
        raise Refused(f'archive_unsafe: {label} {name} is missing or duplicated')
    info = infos[0]
    if info.is_dir() or stat.S_ISLNK(info.external_attr >> 16):
        raise Refused(f'archive_unsafe: {label} {name} is not a regular member')
    return info


def check_names(archive, label):
    names = archive.namelist()
    if len(names) != len(set(names)):
        raise Refused(f'archive_unsafe: {label} has duplicate member names')
    for name in names:
        if name.startswith('/') or '..' in name.split('/') or '\\' in name or '\0' in name:
            raise Refused(f'archive_unsafe: {label} member path {name!r}')
    return names


def firmware_members(target_files, excluded, deadline):
    """sha256 and size of the selected firmware members, each bounded; image bytes never leave the archive."""
    with open_regular(target_files, 'signed target-files', excluded) as f, zipfile.ZipFile(f) as archive:
        names = check_names(archive, 'signed target-files')
        selected = sorted(n for n in names if FIRMWARE_MEMBER.fullmatch(n))
        if not any(n.startswith('RADIO/') for n in selected) or 'OTA/android-info.txt' not in selected:
            raise Refused('firmware_missing: the signed target-files has no RADIO/ members or OTA/android-info.txt')
        if len(selected) > LIMITS['firmwareMembers']:
            raise Refused(f'size_bound: more than {LIMITS["firmwareMembers"]} firmware members')
        members, total = {}, 0
        for name in selected:
            info = _member(archive, name, 'target-files')
            if info.file_size > LIMITS['firmwareMemberBytes']:
                raise Refused(f'size_bound: firmware member {name} exceeds {LIMITS["firmwareMemberBytes"]} bytes')
            total += info.file_size
            if total > LIMITS['firmwareBytes']:
                raise Refused(f'size_bound: firmware members exceed {LIMITS["firmwareBytes"]} bytes')
            with archive.open(info) as m:
                size, digest = hash_stream(m, LIMITS['firmwareMemberBytes'], name, deadline)
            members[name] = {'sha256': digest, 'size': size}
    return members


def elf_needed(data, label):
    """DT_NEEDED names of an x86-64 ELF64 little-endian dynamic executable or library, parsed from bytes only."""
    if len(data) < 64 or data[:4] != b'\x7fELF' or data[4] != 2 or data[5] != 1:
        raise Refused(f'fastboot_invalid: {label} is not an ELF64 little-endian file')
    machine, = struct.unpack_from('<H', data, 18)
    if machine != 62:
        raise Refused(f'fastboot_invalid: {label} is not x86-64')
    phoff, = struct.unpack_from('<Q', data, 32)
    phentsize, phnum = struct.unpack_from('<HH', data, 54)
    loads, dynamic = [], None
    for i in range(phnum):
        off = phoff + i * phentsize
        if off + 56 > len(data):
            raise Refused(f'fastboot_invalid: {label} program headers are truncated')
        p_type, _, p_offset, p_vaddr, _, p_filesz = struct.unpack_from('<IIQQQQ', data, off)
        if p_type == 1:
            loads.append((p_vaddr, p_offset, p_filesz))
        elif p_type == 2:
            dynamic = (p_offset, p_filesz)
    if dynamic is None:
        return []
    entries, strtab, strsz = [], None, None
    for off in range(dynamic[0], min(dynamic[0] + dynamic[1], len(data) - 15), 16):
        tag, value = struct.unpack_from('<qQ', data, off)
        if tag == 0:
            break
        if tag == 1:
            entries.append(value)
        elif tag == 5:
            strtab = value
        elif tag == 10:
            strsz = value
    if entries and strtab is None:
        raise Refused(f'fastboot_invalid: {label} has DT_NEEDED without DT_STRTAB')
    base = next((o + strtab - v for v, o, s in loads if v <= (strtab or 0) < v + s), None) if entries else None
    if entries and base is None:
        raise Refused(f'fastboot_invalid: {label} DT_STRTAB is outside every PT_LOAD segment')
    names = []
    for value in entries:
        start = base + value
        end = data.find(b'\0', start, start + 256 if strsz is None else min(start + 256, base + strsz))
        if start >= len(data) or end < 0:
            raise Refused(f'fastboot_invalid: {label} DT_NEEDED name is out of range')
        names.append(data[start:end].decode('ascii', 'replace'))
    return names


def _write_new(path, data, mode):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    with os.fdopen(fd, 'wb') as f:
        f.write(data)


def stage_fastboot(otatools, b, out, excluded, deadline):
    """Verify the whole otatools archive against its committed size and sha256 first; then copy only bin/fastboot and
    its DT_NEEDED closure from lib64/ into out/fastboot. Nothing is executed."""
    with open_regular(otatools, 'otatools', excluded) as f:
        size, digest = hash_stream(f, b['otatools']['bytes'], 'otatools', deadline)
        if (size, digest) != (b['otatools']['bytes'], b['otatools']['sha256']):
            raise Refused('hash_mismatch: otatools is not the committed otatools archive')
        f.seek(0)
        with zipfile.ZipFile(f) as archive:
            check_names(archive, 'otatools')
            budget = [LIMITS['fastbootBytes']]

            def take(name, label):
                info = _member(archive, name, 'otatools')
                if info.file_size > budget[0]:
                    raise Refused(f'size_bound: fastboot and its dependencies exceed {LIMITS["fastbootBytes"]} bytes')
                with archive.open(info) as m:
                    data = m.read(info.file_size + 1)
                if len(data) != info.file_size:
                    raise Refused(f'archive_unsafe: {label} size differs from its header')
                budget[0] -= len(data)
                deadline.check(label)
                return data

            (out/'fastboot/bin').mkdir(parents=True)
            (out/'fastboot/lib64').mkdir()
            fastboot = take('bin/fastboot', 'bin/fastboot')
            _write_new(out/'fastboot/bin/fastboot', fastboot, 0o500)
            libs, system, queue = {}, set(), [('bin/fastboot', fastboot)]
            while queue:
                label, data = queue.pop(0)
                for name in elf_needed(data, label):
                    if name in libs or name in system:
                        continue
                    if not LIB_NAME.fullmatch(name):
                        raise Refused(f'fastboot_invalid: {label} names an unsafe library {name!r}')
                    member = f'lib64/{name}'
                    if member in archive.namelist():
                        if len(libs) >= LIMITS['fastbootLibs']:
                            raise Refused(f'size_bound: more than {LIMITS["fastbootLibs"]} fastboot libraries')
                        lib = take(member, member)
                        _write_new(out/'fastboot/lib64'/name, lib, 0o400)
                        libs[name] = hashlib.sha256(lib).hexdigest()
                        queue.append((member, lib))
                    elif name in SYSTEM_LIBS:
                        system.add(name)
                    else:
                        raise Refused(f'fastboot_dependency_missing: {name} (needed by {label}) is not in otatools lib64/')
    return {'otatools': {'sha256': digest, 'size': size},
            'fastboot': {'member': 'bin/fastboot', 'sha256': hashlib.sha256(fastboot).hexdigest(), 'size': len(fastboot)},
            'libraries': dict(sorted(libs.items())), 'systemLibraries': sorted(system), 'executed': False,
            'versionCheck': 'not run here: a bounded fastboot --version is reserved for the future operator helper'}


def factory_package(root=ROOT):
    path = Path(root)/'scripts/factory-package.py'
    spec = importlib.util.spec_from_file_location('factory_package', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, hashlib.sha256(path.read_bytes()).hexdigest()


def recipe_head(root=ROOT):
    try:
        return subprocess.run(['git', '-C', str(root), 'rev-parse', 'HEAD'], capture_output=True, text=True, timeout=30,
                              check=True).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None


@contextlib.contextmanager
def shared_lock(path):
    """The existing shared builder lock (os/retained-rango/release.json signing.lockPath): nonblocking, never waited on."""
    try:
        fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o644)
    except OSError:
        raise Refused('lock_unavailable: the shared builder lock cannot be opened') from None
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Refused('lock_held: the shared builder lock is held by another job') from None
        yield
    finally:
        os.close(fd)


def check_output(out, excluded):
    out = Path(out)
    if not out.is_absolute() or '..' in out.parts or _excluded(str(out), excluded):
        raise Refused('output_refused: --out must be an absolute path without .. outside excluded directories')
    parent = out.parent
    if parent.is_symlink() or not parent.is_dir():
        raise Refused('output_refused: the parent of --out must be an existing plain directory')
    if os.path.lexists(out):
        raise Refused('output_exists: --out must be a new directory')
    return out


def new_directory(out):
    try:
        out.mkdir(mode=0o700)
    except FileExistsError:
        raise Refused('output_exists: --out must be a new directory') from None
    return out


def run(run_id, recipe_sha, out, root=ROOT, b=None, lock=shared_lock, head=recipe_head, clock=time.monotonic):
    if not RUN_ID.fullmatch(str(run_id)):
        raise Refused('usage_refused: --run-id must be a workflow run id')
    if not HEX40.fullmatch(str(recipe_sha)) or head(root) != recipe_sha:
        raise Refused('recipe_mismatch: --recipe-sha is not this checkout')
    b = b or bindings(root)
    deadline = Deadline(LIMITS['seconds'], clock)
    excluded = b['excluded']
    out = check_output(out, excluded)
    with lock(b['lock']):
        out = new_directory(out)
        try:
            marker = read_bounded(b['marker'], 'signed marker', LIMITS['markerBytes'], excluded, deadline)
            signed_sha, ota_sha = check_marker(marker, b)
            with open_regular(b['targetFiles'], 'signed target-files', excluded) as f:
                tf_size, tf_sha = hash_stream(f, 1 << 40, 'signed target-files', deadline)
            if tf_sha != signed_sha:
                raise Refused('hash_mismatch: the signed target-files is not the marker signedTargetFilesSha256')
            receipt_bytes = read_bounded(b['inputReceipt']['path'], 'input receipt', LIMITS['receiptBytes'], excluded,
                                         deadline, b['inputReceipt']['sha256'])
            try:
                ota_entry = json.loads(receipt_bytes)['artifacts']['otatools']
            except (ValueError, KeyError, TypeError):
                raise Refused('binding_missing: the input receipt has no artifacts.otatools') from None
            if (ota_entry.get('sha256'), ota_entry.get('size')) != (b['otatools']['sha256'], b['otatools']['bytes']):
                raise Refused('binding_conflict: the input receipt otatools differs from the committed provenance')
            fp, fp_sha = factory_package(root)
            try:
                observation = fp.observe(b['targetFiles'], DEVICE, BUILD, f'rango-factory-observe run {run_id}', root)
            except fp.ContractError as error:
                raise Refused(f'observe_refused: {error}') from None
            if observation['targetFiles']['sha256'] != signed_sha:
                raise Refused('hash_mismatch: the target-files changed during the observation')
            deadline.check('observe')
            firmware = firmware_members(b['targetFiles'], excluded, deadline)
            staged = stage_fastboot(b['otatools']['path'], b, out, excluded, deadline)
            files = {
                'target-files-observation.json': observation,
                'firmware-members.json': {'schema': FIRMWARE_SCHEMA, 'device': DEVICE, 'build': BUILD,
                                          'targetFilesSha256': signed_sha, 'members': firmware},
            }
            for name, data in files.items():
                _write_new(out/name, (json.dumps(data, indent=2, sort_keys=True) + '\n').encode(), 0o644)
            receipt = {
                'schema': SCHEMA, 'device': DEVICE, 'build': BUILD, 'runId': str(run_id), 'recipeSha': recipe_sha,
                'factoryPackageSha256': fp_sha,
                'inputs': {
                    'signedMarker': {'path': b['marker'], 'sha256': hashlib.sha256(marker).hexdigest(), 'otaSha256': ota_sha},
                    'signedTargetFiles': {'path': b['targetFiles'], 'sha256': tf_sha, 'size': tf_size},
                    'inputReceipt': {'path': b['inputReceipt']['path'], 'sha256': b['inputReceipt']['sha256']},
                    'otatools': dict(staged['otatools'], path=b['otatools']['path']),
                },
                'outputs': {name: hashlib.sha256((out/name).read_bytes()).hexdigest() for name in sorted(files)},
                'fastboot': {k: staged[k] for k in ('fastboot', 'libraries', 'systemLibraries', 'executed', 'versionCheck')},
                'excluded': excluded + ['secrets', 'bootstrap receipts'],
                'limits': LIMITS, 'claims': 'observation only: no generator, signing, packaging, device or host execution',
            }
            _write_new(out/'rango-factory-observe.json', (json.dumps(receipt, indent=2, sort_keys=True) + '\n').encode(), 0o644)
            return receipt
        except BaseException:
            shutil.rmtree(out, ignore_errors=True)  # only the directory this run created
            raise


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split('\n', 1)[0])
    sub = p.add_subparsers(dest='command', required=True)
    sub.add_parser('check')
    r = sub.add_parser('run')
    r.add_argument('--run-id', required=True)
    r.add_argument('--recipe-sha', required=True)
    r.add_argument('--out', required=True)
    a = p.parse_args(argv)
    try:
        if a.command == 'check':
            result = {'checked': True, 'bindings': bindings()}
        else:
            receipt = run(a.run_id, a.recipe_sha, a.out)
            result = {'observed': True, 'out': a.out, 'receipt': receipt}
    except Refused as error:
        print(json.dumps({'refused': str(error)}))
        return 3
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    sys.exit(main())
