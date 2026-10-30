#!/usr/bin/env python3
"""OS-RANGO-COLLECT-006: collect (locate, validate and hash) the completed unsigned Rango build outputs.

  rango-collect.py --out-root SRC --build-number B --receipt NEW.json

Only these paths under O = SRC/out-rango-B are read; nothing else is searched or listed:
  vendorBootImage        target/product/rango/vendor_boot.img          (vendor boot header magic VNDRBOOT)
  vendorKernelBootImage  target/product/rango/vendor_kernel_boot.img   (vendor boot header magic VNDRBOOT)
  otatools               host/linux-x86/obj/ETC/otatools-packagelinux_glibc_x86_64_intermediates/
                         otatools-packagelinux_glibc_x86_64            (an extensionless ZIP; the otatools-package
                         output os/release-pipeline/frankel.json pins and scripts/host-sign.py copies)
  targetFiles            the one rango-*target_files*.zip directly in
                         target/product/rango/obj/PACKAGING/target_files_intermediates/ (the only directory listed;
                         zero is missing, more than one is ambiguous)
Aliases such as host/linux-x86/otatools.zip or dist/otatools.zip are not consulted: no fallback, no tree or disk scan.

Each artifact is opened without following links after every path component from O was checked to be an ordinary
directory (no symlink, no traversal); the bytes are hashed and sized in one streaming read of the same descriptor,
which must still match its size and mtime afterwards. ZIPs are checked by reading their central directory and every
entry's CRC (zipfile.testzip), with safe entry names and required entries; nothing is extracted or executed. Each
artifact ends as collected, missing, unreadable, invalid, ambiguous or uninspected, and the receipt is written once,
outside O, even when incomplete. Sources are never renamed, deleted, copied over or written.
Exit status: 0 all four collected, 1 written but incomplete, 2 invalid usage (nothing written).
"""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat as statmod
import sys
import time
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
import release_receipts  # noqa: E402

DEVICE = 'rango'
OUT_PREFIX = 'out-rango-'
PRODUCT = f'target/product/{DEVICE}'
FILES = {
    'vendorBootImage': (f'{PRODUCT}/vendor_boot.img', 'vendor-boot'),
    'vendorKernelBootImage': (f'{PRODUCT}/vendor_kernel_boot.img', 'vendor-boot'),
    'otatools': ('host/linux-x86/obj/ETC/otatools-packagelinux_glibc_x86_64_intermediates/'
        'otatools-packagelinux_glibc_x86_64', 'otatools'),
}
TARGET_FILES_DIR = f'{PRODUCT}/obj/PACKAGING/target_files_intermediates'
TARGET_FILES_NAME = re.compile(re.escape(DEVICE) + r'-target_files[A-Za-z0-9._-]*\.zip')
REQUIRED_ENTRIES = {'otatools': ('bin/', 'releasetools/'), 'target-files': ('META/misc_info.txt', 'SYSTEM/')}
BOOT_MAGIC = b'VNDRBOOT'

class InvalidInput(Exception):
    pass

class Refused(Exception):
    """The artifact is present but not acceptable: (state, why)."""
    def __init__(self, state, why):
        super().__init__(why)
        self.state = state

def output_dir(out_root, build):
    if not re.fullmatch(r'[1-9][0-9]{9}', str(build or '')):
        raise InvalidInput('--build-number must be a ten-digit build number')
    root = Path(out_root)
    if not root.is_absolute() or '..' in root.parts:
        raise InvalidInput('--out-root must be an absolute path without ..')
    return root, root/(OUT_PREFIX + str(build))

def ordinary_dirs(base, relative_parts):
    """Each directory from base along relative_parts must be an ordinary directory, not a link; Refused otherwise."""
    current = base
    for part in relative_parts:
        if part in ('', '.', '..'):
            raise Refused('invalid', f'unsafe path component {part!r}')
        current = current/part
        try:
            mode = os.lstat(current).st_mode
        except FileNotFoundError:
            raise Refused('missing', f'{current} does not exist')
        except OSError as error:
            raise Refused('unreadable', f'{current}: {error.strerror}')
        if statmod.S_ISLNK(mode) or not statmod.S_ISDIR(mode):
            raise Refused('invalid', f'{current} is not an ordinary directory')
    return current

def read_artifact(path):
    """(size, sha256, header) from one no-follow descriptor; Refused when absent, linked, special or changing."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, 'O_NONBLOCK', 0))
    except FileNotFoundError:
        raise Refused('missing', f'{path} does not exist')
    except OSError as error:
        state = 'invalid' if os.path.islink(path) else 'unreadable'
        raise Refused(state, f'{path}: {error.strerror}')
    try:
        before = os.fstat(fd)
        if not statmod.S_ISREG(before.st_mode):
            raise Refused('invalid', f'{path} is not a regular file')
        digest, size, header = hashlib.sha256(), 0, b''
        try:
            while True:
                block = os.read(fd, 1 << 20)
                if not block:
                    break
                if size < 64:
                    header = (header + block)[:64]
                digest.update(block)
                size += len(block)
        except OSError as error:
            raise Refused('unreadable', f'{path}: read failed: {error.strerror}')
        after = os.fstat(fd)
        if size != before.st_size or (after.st_size, after.st_mtime_ns) != (before.st_size, before.st_mtime_ns):
            raise Refused('unreadable', f'{path} changed while it was read')
        if size == 0:
            raise Refused('invalid', f'{path} is empty')
        return {'size': size, 'sha256': digest.hexdigest(), 'mtimeNs': before.st_mtime_ns}, header
    finally:
        os.close(fd)

def check_zip(path, kind):
    """Facts about a ZIP by its central directory and CRCs, never extracting; Refused('invalid') otherwise."""
    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            unsafe = [n for n in names if n.startswith('/') or '\\' in n or '..' in PurePosixPath(n).parts]
            if unsafe:
                raise Refused('invalid', f'{path}: unsafe entry names {unsafe[:3]}')
            if len(set(names)) != len(names):
                raise Refused('invalid', f'{path}: duplicate entry names')
            absent = [r for r in REQUIRED_ENTRIES[kind]
                if not any(n == r or (r.endswith('/') and n.startswith(r)) for n in names)]
            if absent:
                raise Refused('invalid', f'{path}: required {kind} entries absent: {absent}')
            bad = archive.testzip()
            if bad is not None:
                raise Refused('invalid', f'{path}: CRC mismatch in {bad}')
            return {'format': 'zip', 'entries': len(names), 'required': list(REQUIRED_ENTRIES[kind])}
    except Refused:
        raise
    except (zipfile.BadZipFile, zipfile.LargeZipFile, EOFError, ValueError, NotImplementedError) as error:
        raise Refused('invalid', f'{path}: not a readable {kind} ZIP: {error}')
    except OSError as error:
        raise Refused('unreadable', f'{path}: {error.strerror}')

def collect_file(out, relative, kind):
    parts = PurePosixPath(relative).parts
    ordinary_dirs(out, parts[:-1])
    path = out.joinpath(*parts)
    facts, header = read_artifact(path)
    if kind == 'vendor-boot':
        if not header.startswith(BOOT_MAGIC):
            raise Refused('invalid', f'{path}: no vendor boot header (magic {BOOT_MAGIC.decode()})')
        facts['format'] = 'vendor-boot'
    else:
        facts.update(check_zip(path, kind))
        again, _ = read_artifact(path)
        if again['sha256'] != facts['sha256']:
            raise Refused('unreadable', f'{path} changed during validation')
    return {'state': 'collected', 'path': str(path), **facts}

def collect_target_files(out):
    directory = ordinary_dirs(out, PurePosixPath(TARGET_FILES_DIR).parts)
    try:
        names = sorted(os.listdir(directory))
    except OSError as error:
        raise Refused('unreadable', f'{directory}: {error.strerror}')
    eligible = [n for n in names if TARGET_FILES_NAME.fullmatch(n)]
    if not eligible:
        raise Refused('missing', f'no {DEVICE}-target_files*.zip in {directory}')
    if len(eligible) > 1:
        raise Refused('ambiguous', f'{len(eligible)} target-files ZIPs in {directory}: {eligible}')
    result = collect_file(out, f'{TARGET_FILES_DIR}/{eligible[0]}', 'target-files')
    result['directoryListing'] = {'directory': str(directory), 'eligible': eligible}
    return result

def collect(out_root, build):
    """{'artifacts': {...}, 'verdict': ...}: reads only the fixed paths below O; writes nothing."""
    root, out = output_dir(out_root, build)
    artifacts = {}
    try:
        if any(Path(*root.parts[:i]).is_symlink() for i in range(2, len(root.parts) + 1)):
            raise Refused('invalid', f'{root} has a linked component')
        ordinary_dirs(root, [out.name])
    except Refused as refused:
        why = f'output directory not inspected: {refused}'
        artifacts = {name: {'state': 'uninspected', 'why': why} for name in (*FILES, 'targetFiles')}
        return {'outDir': str(out), 'artifacts': artifacts, 'verdict': 'incomplete', 'outDirState': refused.state}
    for name, (relative, kind) in FILES.items():
        try:
            artifacts[name] = collect_file(out, relative, kind)
        except Refused as refused:
            artifacts[name] = {'state': refused.state, 'path': str(out/relative), 'why': str(refused)}
    try:
        artifacts['targetFiles'] = collect_target_files(out)
    except Refused as refused:
        artifacts['targetFiles'] = {'state': refused.state, 'path': str(out/TARGET_FILES_DIR), 'why': str(refused)}
    complete = all(a['state'] == 'collected' for a in artifacts.values())
    return {'outDir': str(out), 'artifacts': artifacts, 'verdict': 'complete' if complete else 'incomplete'}

def main(argv=None, now=time.time):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--out-root', required=True, help='the source root holding out-rango-<build>')
    p.add_argument('--build-number', required=True)
    p.add_argument('--receipt', required=True, help='new receipt path outside the output directory (write-once)')
    a = p.parse_args(argv)
    try:
        root, out = output_dir(a.out_root, a.build_number)
        receipt = Path(a.receipt)
        if not receipt.is_absolute() or '..' in receipt.parts:
            raise InvalidInput('--receipt must be an absolute path without ..')
        if os.path.lexists(receipt):
            raise InvalidInput(f'{receipt} already exists; use a new receipt path')
        resolved = Path(os.path.realpath(receipt.parent))/receipt.name
        if resolved == Path(os.path.realpath(out)) or Path(os.path.realpath(out)) in resolved.parents:
            raise InvalidInput('the receipt must be outside the output directory')
    except InvalidInput as error:
        print(json.dumps({'verdict': 'INVALID', 'error': str(error)}), file=sys.stderr)
        return 2
    started = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(now()))
    result = collect(a.out_root, a.build_number)
    document = {'receiptType': 'rango-collection', 'schemaVersion': 1, 'device': DEVICE,
        'buildNumber': str(a.build_number), 'startedAt': started,
        'finishedAt': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(now())), **result,
        'collector': {'sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()},
        'signed': False, 'releaseReady': False,
        'scope': 'existence, format and hashes of four fixed unsigned outputs; not signing, verification or acceptance'}
    release_receipts.write_once(receipt, document)
    print(json.dumps({'verdict': result['verdict'], 'receipt': str(receipt),
        'states': {k: v['state'] for k, v in result['artifacts'].items()}}, indent=2, sort_keys=True))
    return 0 if result['verdict'] == 'complete' else 1

if __name__ == '__main__':
    sys.exit(main())
