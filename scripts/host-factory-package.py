#!/usr/bin/env python3
"""Packaging-only factory install archive from an already SIGNED retained target-files (FOLD-FACTORY-PACKAGE).

  python3 scripts/host-factory-package.py --device rango --build 2026103001 \
      --target-files TF --target-files-sha256 H --otatools OTA --otatools-sha256 H \
      --release-script script/generate-release.sh --release-script-sha256 H \
      --fastboot-sha256 H --lock SHARED_BUILDER_LOCK --out NEW_ROOT

No key, secret, decrypt, signing, OTA or archive signature is involved. host-sign.py correctly refuses a build whose
release already holds a successful signature; this entrypoint never touches that release. It only reads the inputs,
each a plain regular file matching its pin:
  - the signed target-files and the otatools package (sha256 given by the operator from existing receipts);
  - the pinned generate-release.sh (sha256 os/retained-rango/release.json releaseScriptProvenance), from which
    host-sign.derive_factory_only_script keeps exactly the factory variables and the factory tail;
  - the PUBLIC AVB key, never an external file: after the target-files hash is verified, exactly its
    IMAGES/vbmeta.img member is read (one regular entry, size-capped, CRC-checked, in an archive with only safe
    names) and the embedded public key extracted with the vbmeta layout signing-job.py / rango-retained.py use, plus
    bounds checks; it must hash to os/flavors/<device>.json identity.avbPublicKeySha256 and only those public bytes
    are written, to NEW_ROOT/avb_pkmd.bin, for the factory script;
  - the otatools' device/common generator scripts, which must equal the flavor's pinned generatorFixture files, and
    its bin/fastboot, checked against --fastboot-sha256 inside the script immediately before optimize-factory-image.
Everything is written below NEW_ROOT (absent beforehand; never inside an input's directory): work/ (the otatools
unzip, a COPY of the target-files, the generator's files), removed on success and failure; factory-package.log
(capped); factory-package-receipt.json; and on success {device}-install-{build}.zip.

The whole run holds the shared builder lock (--lock, non-blocking: lock_held refuses before anything is written).
The receipt records each phase's wall time (verify, stage, generate, recheck) as measured, not as a requirement.

Bounds: the derived script runs in its own process group, killed (TERM, then KILL) after LIMITS['seconds'] (60 min)
or when its combined log exceeds LIMITS['logBytes']. Free space under NEW_ROOT's filesystem must be at least
LIMITS['freeBytes'] (128 GiB) before starting; actual use is measured and recorded, not capped (no quota exists), so
the free-space floor is a precondition, not a guarantee. Nothing here is a runtime approval or a measured capacity.

Exit status: 0 packaged, 3 refused or failed (one JSON line on stdout), 2 usage.
"""
import argparse
import contextlib
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import select
import shutil
import signal
import stat
import subprocess
import sys
import time
import zipfile

ROOT = Path(__file__).resolve().parent.parent
SCHEMA = 'roseos.factory-package-run.v1'
HEX64 = re.compile(r'[0-9a-f]{64}')
LIMITS = {'seconds': 60 * 60, 'killGraceSeconds': 10, 'logBytes': 64 << 20, 'freeBytes': 128 << 30,
          'otatoolsMembers': 50000, 'vbmetaBytes': 1 << 20}
VBMETA = 'IMAGES/vbmeta.img'


class Refused(Exception):
    """A fixed category and nonsecret detail."""


def _module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(Path(path).parent))
    spec.loader.exec_module(module)
    return module


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(8 << 20), b''):
            digest.update(block)
    return digest.hexdigest()


def pinned_input(path, digest, label):
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts:
        raise Refused(f'input_refused: {label} must be an absolute path without ..')
    for parent in list(reversed(path.parents))[1:]:
        if parent.is_symlink():
            raise Refused(f'input_refused: {label} has a symlinked ancestor')
    if path.is_symlink() or not path.is_file():
        raise Refused(f'input_missing: {label} is not a regular file')
    if not HEX64.fullmatch(str(digest)) or sha256_file(path) != digest:
        raise Refused(f'hash_mismatch: {label} differs from its pin')
    return path


def flavor_pins(device, root=ROOT):
    """The flavor's install-archive name, the AVB public-key pin and the pinned generator script digests."""
    fp = _module('factory_package', root/'scripts/factory-package.py')
    try:
        flavor = fp.load_flavor(device, root)
        return {'install': flavor['artifacts']['factory']['file'], 'avb': flavor['identity']['avbPublicKeySha256'],
                'generator': dict(flavor['inventory']['generatorFixture']['files'])}
    except (fp.ContractError, KeyError, TypeError) as error:
        raise Refused(f'contract_missing: no factory contract for {device} ({error})') from None


def avb_public_key(vbmeta):
    """The AVB public key embedded in a vbmeta image (what avbtool extract_public_key writes as avb_pkmd.bin): the
    same header arithmetic as signing-job.py and rango-retained.py avb_public_key, with every offset bounds-checked
    against the authentication and auxiliary blocks instead of silently truncating."""
    import struct
    if len(vbmeta) < 256 or vbmeta[:4] != b'AVB0':
        raise Refused('vbmeta_invalid: not an AVB vbmeta image')
    auth_size, aux_size = struct.unpack('>QQ', vbmeta[12:28])
    pk_offset, pk_size = struct.unpack('>QQ', vbmeta[64:80])
    if auth_size % 64 or aux_size % 64 or 256 + auth_size + aux_size > len(vbmeta):
        raise Refused('vbmeta_invalid: header block sizes exceed the image')
    if pk_size == 0 or pk_offset + pk_size > aux_size:
        raise Refused('vbmeta_invalid: the public key lies outside the auxiliary block')
    start = 256 + auth_size + pk_offset
    return vbmeta[start:start + pk_size]


def derive_avb_pkmd(target_files, expected):
    """Read exactly IMAGES/vbmeta.img from the (already hash-verified) signed target-files and return the public key
    bytes and their provenance; the key must hash to the flavor pin."""
    try:
        archive = zipfile.ZipFile(target_files)
    except (OSError, zipfile.BadZipFile):
        raise Refused('target_files_invalid: not a readable zip') from None
    with archive:
        names = archive.namelist()
        if any(n.startswith('/') or '\\' in n or '..' in n.split('/') or '\0' in n for n in names):
            raise Refused('target_files_unsafe: a member name is absolute or escapes')
        infos = [i for i in archive.infolist() if i.filename == VBMETA]
        if len(infos) != 1:
            raise Refused(f'vbmeta_missing: the target-files has {len(infos)} {VBMETA} entries, expected exactly one')
        info = infos[0]
        if info.is_dir() or stat.S_ISLNK(info.external_attr >> 16):
            raise Refused(f'vbmeta_invalid: {VBMETA} is not a regular member')
        if info.file_size > LIMITS['vbmetaBytes']:
            raise Refused(f'vbmeta_invalid: {VBMETA} exceeds {LIMITS["vbmetaBytes"]} bytes')
        try:
            with archive.open(info) as member:
                vbmeta = member.read(LIMITS['vbmetaBytes'] + 1)
        except (zipfile.BadZipFile, OSError):
            raise Refused(f'vbmeta_invalid: {VBMETA} fails its CRC or cannot be read') from None
    if len(vbmeta) != info.file_size or len(vbmeta) > LIMITS['vbmetaBytes']:
        raise Refused(f'vbmeta_invalid: {VBMETA} size differs from its header')
    public = avb_public_key(vbmeta)
    if hashlib.sha256(public).hexdigest() != expected:
        raise Refused('avb_mismatch: the public key in IMAGES/vbmeta.img is not the flavor identity.avbPublicKeySha256')
    return public, {'member': VBMETA, 'memberSha256': hashlib.sha256(vbmeta).hexdigest(), 'memberSize': len(vbmeta)}


def check_output(out, inputs):
    out = Path(out)
    if not out.is_absolute() or '..' in out.parts:
        raise Refused('output_refused: --out must be an absolute path without ..')
    if out.parent.is_symlink() or not out.parent.is_dir():
        raise Refused('output_refused: the parent of --out must be an existing plain directory')
    if os.path.lexists(out):
        raise Refused('output_exists: --out must be a new directory; nothing is overwritten')
    for path in inputs:
        if out == path.parent or path.parent in out.parents:
            raise Refused(f'output_refused: --out is inside the directory of {path.name}; retained data is never touched')
    return out


def free_bytes(path):
    stats = os.statvfs(path)
    return stats.f_bavail * stats.f_frsize


def unzip_otatools(archive, work):
    """Plain extraction: regular files and directories only, names relative without .., at most otatoolsMembers."""
    with zipfile.ZipFile(archive) as z:
        infos = z.infolist()
        if len(infos) > LIMITS['otatoolsMembers']:
            raise Refused('otatools_unsafe: too many members')
        for info in infos:
            name = info.filename
            if name.startswith('/') or '\\' in name or '..' in name.split('/') or stat.S_ISLNK(info.external_attr >> 16):
                raise Refused(f'otatools_unsafe: member {name!r} is not a plain relative file')
        z.extractall(work)
        for info in infos:
            mode = (info.external_attr >> 16) & 0o777
            if mode and not info.is_dir():
                os.chmod(Path(work)/info.filename, mode & 0o755)


@contextlib.contextmanager
def shared_lock(path):
    """The existing shared builder lock: non-blocking, never waited on."""
    path = Path(path)
    if not path.is_absolute():
        raise Refused('usage_refused: --lock must be an absolute path')
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


def run_script(script, cwd, log, popen=subprocess.Popen, clock=time.monotonic):
    """bash SCRIPT in its own session with a minimal environment; combined output to the capped log. Returns a
    category: ok, nonzero_exit, timeout, log_overflow or spawn_failed."""
    env = {'PATH': '/usr/bin:/bin', 'LANG': 'C', 'LC_ALL': 'C', 'HOME': str(cwd)}
    try:
        proc = popen(['bash', str(script)], cwd=str(cwd), env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                     stderr=subprocess.STDOUT, close_fds=True, start_new_session=True)
    except OSError:
        return 'spawn_failed', None
    category, written, end = None, 0, clock() + LIMITS['seconds']
    fd = proc.stdout.fileno()
    with open(log, 'ab') as out:
        try:
            while True:
                remaining = end - clock()
                if remaining <= 0:
                    category = 'timeout'
                    break
                ready, _, _ = select.select([fd], [], [], min(remaining, 1.0))
                if ready:
                    block = os.read(fd, 65536)
                    if not block:
                        break
                    written += len(block)
                    if written > LIMITS['logBytes']:
                        category = 'log_overflow'
                        break
                    out.write(block)
                elif proc.poll() is not None:
                    break
        finally:
            for sig in (signal.SIGTERM, signal.SIGKILL):
                try:
                    os.killpg(proc.pid, sig)
                except (ProcessLookupError, PermissionError):
                    pass
                if sig == signal.SIGTERM:
                    try:
                        proc.wait(timeout=LIMITS['killGraceSeconds'])
                    except subprocess.TimeoutExpired:
                        pass
            proc.wait()
            proc.stdout.close()
    if category is None:
        category = 'ok' if proc.returncode == 0 else 'nonzero_exit'
    return category, proc.returncode


def package(args, root=ROOT, runner=run_script, free=free_bytes, clock=time.time, lock=shared_lock):
    with lock(args.lock):
        return _package(args, root, runner, free, clock)


def _package(args, root, runner, free, clock):
    hs = _module('host_sign', root/'scripts/host-sign.py')
    begun = time.monotonic()
    pins = flavor_pins(args.device, root)
    target_files = pinned_input(args.target_files, args.target_files_sha256, 'target-files')
    otatools = pinned_input(args.otatools, args.otatools_sha256, 'otatools')
    release_script = pinned_input(args.release_script, args.release_script_sha256, 'release script')
    if not HEX64.fullmatch(str(args.fastboot_sha256)):
        raise Refused('usage_refused: --fastboot-sha256 must be a sha256')
    out = check_output(args.out, [target_files, otatools, release_script])
    if free(out.parent) < LIMITS['freeBytes']:
        raise Refused(f'capacity_unavailable: less than {LIMITS["freeBytes"]} bytes free under {out.parent}')
    try:
        public, provenance = derive_avb_pkmd(target_files, pins['avb'])
        avb_pkmd = out/'avb_pkmd.bin'
        derived = hs.derive_factory_only_script(release_script.read_text(), args.device, args.build, avb_pkmd,
                                                args.fastboot_sha256)
    except hs.AdapterFailed as error:
        raise Refused(f'script_refused: {error}') from None
    install_name = pins['install'].format(device=args.device, build=args.build)
    timings = {'verifySeconds': round(time.monotonic() - begun, 3)}
    out.mkdir(mode=0o700)
    fd = os.open(avb_pkmd, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
    with os.fdopen(fd, 'wb') as f:
        f.write(public)  # public key bytes only, derived above from the hash-pinned target-files
    work = out/'work'
    receipt = {'schema': SCHEMA, 'device': args.device, 'build': args.build, 'status': 'failed', 'started': int(clock()),
               'inputs': {'targetFiles': {'path': str(target_files), 'sha256': args.target_files_sha256},
                          'otatools': {'path': str(otatools), 'sha256': args.otatools_sha256},
                          'releaseScript': {'path': str(release_script), 'sha256': args.release_script_sha256},
                          'avbPkmd': {'path': str(avb_pkmd), 'sha256': pins['avb'],
                                      'derivedFrom': dict(provenance, targetFiles=args.target_files_sha256)},
                          'fastbootSha256': args.fastboot_sha256},
               'derivedScriptSha256': hashlib.sha256(derived.encode()).hexdigest(), 'limits': LIMITS, 'timings': timings,
               'signature': None, 'signatureBasis': 'packaging only: no archive signature (identity not a generation gate)',
               'claims': 'generated install archive bytes only; not installed, booted or validated on a phone'}
    try:
        work.mkdir(mode=0o700)
        unzip_otatools(otatools, work)
        for name, digest in sorted(pins['generator'].items()):
            generator = work/'device/common'/name
            if generator.is_symlink() or not generator.is_file() or sha256_file(generator) != digest:
                raise Refused(f'generator_mismatch: otatools device/common/{name} is not the pinned generator fixture')
        receipt['generatorSha256'] = dict(sorted(pins['generator'].items()))
        shutil.copyfile(target_files, work/f'{args.device}-target_files.zip')  # a copy; the retained file is only read
        script = work/'factory-only.sh'
        script.write_text(derived)
        timings['stageSeconds'] = round(time.monotonic() - begun - timings['verifySeconds'], 3)
        mark = time.monotonic()
        category, code = runner(script, work, out/'factory-package.log')
        timings['generateSeconds'] = round(time.monotonic() - mark, 3)
        receipt['script'] = {'category': category, 'exit': code}
        if category != 'ok':
            raise Refused(f'generation_failed: the factory-only script ended {category} (exit {code})')
        produced = work/install_name
        if produced.is_symlink() or not produced.is_file():
            raise Refused(f'output_missing: the script did not produce {install_name}')
        if os.path.lexists(work/f'{install_name}.sig'):
            raise Refused(f'output_unexpected: {install_name}.sig exists although nothing signs')
        used = sum(p.stat().st_size for p in work.rglob('*') if p.is_file() and not p.is_symlink())
        shutil.copyfile(produced, out/install_name)
        receipt.update(status='packaged', output={'file': install_name, 'sha256': sha256_file(out/install_name),
                                                  'size': (out/install_name).stat().st_size},
                       measuredWorkBytes=used)
        mark = time.monotonic()
        if sha256_file(target_files) != args.target_files_sha256:
            raise Refused('input_changed: the retained target-files changed during packaging')
        timings['recheckSeconds'] = round(time.monotonic() - mark, 3)
        return receipt
    except Refused as error:
        receipt['refused'] = str(error)
        if os.path.lexists(out/install_name):
            (out/install_name).unlink()
        receipt['status'] = 'failed'
        raise
    finally:
        shutil.rmtree(work, ignore_errors=True)
        receipt['finished'], receipt['workRemoved'] = int(clock()), not os.path.lexists(work)
        fd = os.open(out/'factory-package-receipt.json', os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o644)
        with os.fdopen(fd, 'w') as f:
            f.write(json.dumps(receipt, indent=2, sort_keys=True) + '\n')


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split('\n', 1)[0])
    for flag in ('--device', '--build', '--target-files', '--target-files-sha256', '--otatools', '--otatools-sha256',
                 '--release-script', '--release-script-sha256', '--fastboot-sha256', '--lock', '--out'):
        p.add_argument(flag, required=True)
    args = p.parse_args(argv)
    try:
        receipt = package(args)
    except Refused as error:
        print(json.dumps({'refused': str(error)}))
        return 3
    print(json.dumps({'packaged': receipt['output']}))
    return 0


if __name__ == '__main__':
    sys.exit(main())
