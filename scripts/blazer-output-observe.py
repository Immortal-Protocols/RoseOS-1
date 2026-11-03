#!/usr/bin/env python3
"""BLAZER-OUTPUT-OBSERVE (Release-owned): one bounded, read-only observation of a finished blazer compile's outputs,
for the signing and packaging bindings and for the build-identity gate. It establishes nothing by itself: it reports
measured identities for a reviewed binding commit, which every consuming step re-verifies at execution.

  observe --host immortal-os-builder

Exactly these paths, fixed in source (no argument chooses a path). S is the isolated blazer-prep work tree
(os/build-only/blazer-prep-host.json sourceRoot) and OUT is S/<os/build-only/blazer.json compile.outDir>:
  targetFiles  the one OUT/target/product/blazer/obj/PACKAGING/target_files_intermediates/blazer-target_files*.zip
               (Flavors' compile collector paths; zero or several matches are reported, never chosen between): its
               size, sha256, mode and mtime, then from its central directory only these members, each at most
               memberBytes and CRC-checked while read: every <PARTITION>/build.prop and <PARTITION>/etc/build.prop
               (only the ro.*build.{version.incremental,date.utc,fingerprint,id,type,tags} values), META/misc_info.txt
               (only ab_update, virtual_ab, dynamic_partition_list) and OTA/android-info.txt (only the board,
               version-bootloader and version-baseband requirements)
  otatools     OUT/host/linux-x86/obj/ETC/otatools-packagelinux_glibc_x86_64_intermediates/
               otatools-packagelinux_glibc_x86_64 (an extensionless ZIP): its size and sha256, and the size and sha256
               of its bin/fastboot member (host-sign.py host.sign.factory.fastbootSha256)
  scripts      S/script/generate-release.sh, S/script/common.sh, S/device/common/generate-factory-images-common.sh and
               S/device/common/clear-factory-images-variables.sh: size and sha256 (the release script host-sign derives)

Every path is reached as scripts/blazer-binding-observe.py reaches its paths: a descriptor walk from / with each
directory component opened O_DIRECTORY|O_NOFOLLOW relative to the previous descriptor and the leaf O_NOFOLLOW relative
to its parent's; a component that is or becomes a link is refused at that step and never traversed. Each file is
fstat'ed regular and the same inode as its lstat, hashed streamed within its byte cap and the deadline, its ZIP members
read from that same open file, then fstat'ed again: any inode, size or mtime change reports `changed`. Only allowlisted
values are emitted, each at most VALUE_CHARS printable characters; no other content leaves the host. Nothing is
written, invoked, signed or changed. The whole observation holds the existing shared builder lock
(os/release-pipeline/frankel.json host.lockPath), exclusive and non-blocking: never created, never waited on; held by
another job, absent or unusable, the report is complete:false with that reason and nothing else is read.

complete:true means every path above was measured within the limits; it says nothing about whether the identity is the
expected one (for example whether build.version.incremental equals the allocated build): the receipt reports the values
and the reviewer applies that gate. The CLI exits 0 whenever it printed a report (complete or not; os-release.yml's
blazer-prep verdict decides the stage) and 2 on invalid usage.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import sys
import time
import zipfile

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location('blazer_binding_observe', ROOT/'scripts/blazer-binding-observe.py')
base = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(base)

HOST = base.HOST
SCHEMA = 'roseos.blazer-output-observation.v1'
DEVICE = 'blazer'
PRODUCT = f'target/product/{DEVICE}'
TARGET_FILES_DIR = f'{PRODUCT}/obj/PACKAGING/target_files_intermediates'
TARGET_FILES_NAME = re.compile(re.escape(DEVICE) + r'-target_files[A-Za-z0-9._-]*\.zip')
OTATOOLS = 'host/linux-x86/obj/ETC/otatools-packagelinux_glibc_x86_64_intermediates/otatools-packagelinux_glibc_x86_64'
SCRIPTS = ('script/generate-release.sh', 'script/common.sh', 'device/common/generate-factory-images-common.sh',
           'device/common/clear-factory-images-variables.sh')
PROP_MEMBER = re.compile(r'[A-Z][A-Z0-9_]{0,31}/(?:etc/)?build\.prop')
PROP_KEY = re.compile(r'ro\.(?:[a-z0-9_]{1,32}\.)?build\.(?:version\.incremental|date\.utc|fingerprint|id|type|tags)')
MISC_KEYS = ('ab_update', 'virtual_ab', 'dynamic_partition_list')
INFO_KEYS = ('board', 'version-bootloader', 'version-baseband')
REQUIRED_MEMBERS = ('SYSTEM/build.prop', 'META/misc_info.txt', 'OTA/android-info.txt')
FASTBOOT = 'bin/fastboot'
VALUE_CHARS = 512
PRINTABLE = re.compile(r'[ -~]*')
LIMITS = {'seconds': 900, 'targetFilesBytes': 32 << 30, 'otatoolsBytes': 4 << 30, 'scriptBytes': 1 << 20,
          'memberBytes': 1 << 20, 'fastbootBytes': 128 << 20, 'propMembers': 32, 'zipEntries': 200000,
          'nameEntries': 4096, 'outputBytes': 64 << 10}


class Budget:
    def __init__(self, clock=time.monotonic):
        self.clock, self.start = clock, clock()
        self.deadline = self.start + LIMITS['seconds']

    def expired(self):
        return self.clock() > self.deadline


class Stop(Exception):
    """A measurement that cannot be completed: the row state."""
    def __init__(self, state, **detail):
        super().__init__(state)
        self.state, self.detail = state, detail


def paths(root=ROOT):
    """(S, OUT) from the committed bindings: never an argument."""
    source = Path(json.loads((root/'os/build-only/blazer-prep-host.json').read_text())['sourceRoot'])
    out_dir = json.loads((root/'os/build-only/blazer.json').read_text())['compile']['outDir']
    if not source.is_absolute() or not re.fullmatch(r'[A-Za-z0-9._-]{1,64}', str(out_dir)):
        raise ValueError('the committed source root or compile OUT_DIR is malformed')
    return source, source/out_dir


def open_leaf(parent, leaf, path):
    """(fd, lstat) of LEAF opened O_NOFOLLOW relative to PARENT, checked regular and the same inode; Stop otherwise."""
    try:
        before = os.lstat(leaf, dir_fd=parent)
    except FileNotFoundError:
        raise Stop('absent') from None
    except OSError as error:
        raise Stop('error', error=type(error).__name__) from None
    if stat.S_ISLNK(before.st_mode):
        raise Stop('link', component=str(path), target=os.readlink(leaf, dir_fd=parent)[:256])
    if not stat.S_ISREG(before.st_mode):
        raise Stop('not-regular')
    try:
        fd = os.open(leaf, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=parent)
    except OSError as error:
        raise Stop('link' if error.errno == 40 else 'error', error=type(error).__name__) from None
    opened = os.fstat(fd)
    if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino) or not stat.S_ISREG(opened.st_mode):
        os.close(fd)
        raise Stop('changed')
    return fd, before


def hash_fd(fd, before, cap, budget):
    """sha256 of the whole open file, streamed within CAP and the deadline; Stop on growth, cap or deadline."""
    if before.st_size > cap:
        raise Stop('too-large', size=before.st_size)
    os.lseek(fd, 0, os.SEEK_SET)
    digest, size = hashlib.sha256(), 0
    while True:
        if budget.expired():
            raise Stop('deadline')
        block = os.read(fd, 8 << 20)
        if not block:
            break
        size += len(block)
        if size > before.st_size:
            raise Stop('changed')
        digest.update(block)
    if size != before.st_size:
        raise Stop('changed')
    return digest.hexdigest()


def unchanged(fd, before):
    after = os.fstat(fd)
    if (after.st_ino, after.st_size, after.st_mtime_ns) != (before.st_ino, before.st_size, before.st_mtime_ns):
        raise Stop('changed')


def identity(row, before, sha256):
    row.update(state='present', size=before.st_size, sha256=sha256, mode=f'{stat.S_IMODE(before.st_mode):o}',
               mtimeNs=before.st_mtime_ns)


def clean(value):
    """An emitted value: printable ASCII, at most VALUE_CHARS characters, or a marker."""
    value = value.strip()
    if not PRINTABLE.fullmatch(value):
        return '<non-printable>'
    return value if len(value) <= VALUE_CHARS else value[:VALUE_CHARS] + '<truncated>'


def read_member(archive, info, cap, budget):
    """The member's bytes, at most CAP, CRC-checked by zipfile while read; Stop otherwise."""
    if info.file_size > cap:
        raise Stop('too-large', size=info.file_size)
    chunks, size = [], 0
    try:
        with archive.open(info) as member:
            while True:
                if budget.expired():
                    raise Stop('deadline')
                block = member.read(1 << 16)
                if not block:
                    break
                size += len(block)
                if size > cap:
                    raise Stop('too-large', size=size)
                chunks.append(block)
    except (zipfile.BadZipFile, EOFError, OSError, NotImplementedError, RuntimeError) as error:
        raise Stop('corrupt', error=type(error).__name__) from None
    return b''.join(chunks)


def hash_member(archive, info, cap, budget):
    if info.file_size > cap:
        raise Stop('too-large', size=info.file_size)
    digest, size = hashlib.sha256(), 0
    try:
        with archive.open(info) as member:
            while True:
                if budget.expired():
                    raise Stop('deadline')
                block = member.read(1 << 20)
                if not block:
                    break
                size += len(block)
                if size > cap:
                    raise Stop('too-large', size=size)
                digest.update(block)
    except (zipfile.BadZipFile, EOFError, OSError, NotImplementedError, RuntimeError) as error:
        raise Stop('corrupt', error=type(error).__name__) from None
    return {'state': 'present', 'size': size, 'sha256': digest.hexdigest()}


def open_archive(fd):
    """A ZipFile over a duplicate of FD (the same open file), its entry count bounded; Stop otherwise."""
    try:
        archive = zipfile.ZipFile(os.fdopen(os.dup(fd), 'rb'))
    except (zipfile.BadZipFile, OSError, EOFError) as error:
        raise Stop('not-zip', error=type(error).__name__) from None
    if len(archive.infolist()) > LIMITS['zipEntries']:
        archive.close()
        raise Stop('too-many-entries')
    return archive


def props(data):
    found = {}
    for line in data.decode('utf-8', 'replace').splitlines():
        key, sep, value = line.partition('=')
        if sep and PROP_KEY.fullmatch(key.strip()):
            found[key.strip()] = clean(value)
    return found


def misc(data):
    found = {}
    for line in data.decode('utf-8', 'replace').splitlines():
        key, sep, value = line.partition('=')
        if sep and key.strip() in MISC_KEYS:
            found[key.strip()] = clean(value)
    return found


def android_info(data):
    found = {}
    for line in data.decode('utf-8', 'replace').splitlines():
        words = line.strip().split(None, 1)
        if len(words) == 2 and words[0] == 'require':
            key, sep, value = words[1].partition('=')
            if sep and key in INFO_KEYS:
                found[key] = [clean(v) for v in value.split('|')][:16]
    return found


def target_files_members(archive, budget):
    """The allowlisted identity values of the target-files, and the problems."""
    infos = {info.filename: info for info in archive.infolist()}
    problems, out = [], {'props': {}, 'miscInfo': None, 'androidInfo': None}
    prop_names = sorted(name for name in infos if PROP_MEMBER.fullmatch(name))
    if len(prop_names) > LIMITS['propMembers']:
        problems.append(f'targetFiles: {len(prop_names)} build.prop members exceed propMembers')
        prop_names = prop_names[:LIMITS['propMembers']]
    for name in REQUIRED_MEMBERS:
        if name not in infos:
            problems.append(f'targetFiles: member {name} absent')
    for name in prop_names:
        try:
            out['props'][name] = props(read_member(archive, infos[name], LIMITS['memberBytes'], budget))
        except Stop as stop:
            out['props'][name] = {'state': stop.state, **stop.detail}
            problems.append(f'targetFiles: member {name} {stop.state}')
    for key, name, parse in (('miscInfo', 'META/misc_info.txt', misc), ('androidInfo', 'OTA/android-info.txt', android_info)):
        if name in infos:
            try:
                out[key] = parse(read_member(archive, infos[name], LIMITS['memberBytes'], budget))
            except Stop as stop:
                out[key] = {'state': stop.state, **stop.detail}
                problems.append(f'targetFiles: member {name} {stop.state}')
    values = lambda suffix: sorted({v for p in out['props'].values() for k, v in p.items()
                                    if isinstance(v, str) and k.endswith(suffix)})
    out['summary'] = {'incremental': values('build.version.incremental'), 'dateUtc': values('build.date.utc')}
    return out, problems


def observe_target_files(out, budget):
    row = {'directory': str(out/TARGET_FILES_DIR)}
    try:
        directory = base.open_directory(out/TARGET_FILES_DIR)
    except base.Walk as walk:
        row.update(state=walk.state, component=str(walk.component), **({'target': walk.target} if walk.target else {}))
        return row, [f'targetFiles: {walk.state} at {walk.component}']
    try:
        names, scanned = [], 0
        with os.scandir(directory) as entries:
            for entry in entries:
                scanned += 1
                if scanned > LIMITS['nameEntries']:
                    row['state'] = 'too-many-names'
                    return row, ['targetFiles: the directory exceeds nameEntries']
                if TARGET_FILES_NAME.fullmatch(entry.name):
                    names.append(entry.name)
        if len(names) != 1:
            row.update(state='absent' if not names else 'ambiguous', names=sorted(names)[:8])
            return row, [f'targetFiles: {len(names)} blazer-target_files*.zip, exactly one is required']
        name = names[0]
        row['path'] = str(out/TARGET_FILES_DIR/name)
        try:
            fd, before = open_leaf(directory, name, row['path'])
        except Stop as stop:
            row.update(state=stop.state, **stop.detail)
            return row, [f'targetFiles: {stop.state}']
    finally:
        os.close(directory)
    try:
        sha256 = hash_fd(fd, before, LIMITS['targetFilesBytes'], budget)
        archive = open_archive(fd)
        try:
            members, problems = target_files_members(archive, budget)
        finally:
            archive.close()
        unchanged(fd, before)
    except Stop as stop:
        row.update(state=stop.state, **stop.detail)
        return row, [f'targetFiles: {stop.state}']
    finally:
        os.close(fd)
    identity(row, before, sha256)
    row.update(members)
    return row, problems


def observe_regular(path, cap, budget):
    """(fd, row with identity) for a regular file reached without links; the caller closes fd. Stop otherwise."""
    try:
        parent, leaf = base.open_parent(path)
    except base.Walk as walk:
        raise Stop(walk.state, component=str(walk.component), **({'target': walk.target} if walk.target else {})) from None
    try:
        fd, before = open_leaf(parent, leaf, path)
    finally:
        os.close(parent)
    try:
        sha256 = hash_fd(fd, before, cap, budget)
    except BaseException:
        os.close(fd)
        raise
    row = {'path': str(path)}
    identity(row, before, sha256)
    return fd, before, row


def observe_otatools(out, budget):
    path = out/OTATOOLS
    try:
        fd, before, row = observe_regular(path, LIMITS['otatoolsBytes'], budget)
    except Stop as stop:
        return {'path': str(path), 'state': stop.state, **stop.detail}, [f'otatools: {stop.state}']
    try:
        archive = open_archive(fd)
        try:
            infos = {info.filename: info for info in archive.infolist()}
            if FASTBOOT not in infos:
                row['fastboot'] = {'state': 'absent'}
                problems = [f'otatools: member {FASTBOOT} absent']
            else:
                try:
                    row['fastboot'] = hash_member(archive, infos[FASTBOOT], LIMITS['fastbootBytes'], budget)
                    problems = []
                except Stop as stop:
                    row['fastboot'] = {'state': stop.state, **stop.detail}
                    problems = [f'otatools: member {FASTBOOT} {stop.state}']
        finally:
            archive.close()
        unchanged(fd, before)
    except Stop as stop:
        return {'path': str(path), 'state': stop.state, **stop.detail}, [f'otatools: {stop.state}']
    finally:
        os.close(fd)
    return row, problems


def observe_scripts(source, budget):
    rows, problems = {}, []
    for relative in SCRIPTS:
        path = source/relative
        try:
            fd, before, row = observe_regular(path, LIMITS['scriptBytes'], budget)
            try:
                unchanged(fd, before)
            finally:
                os.close(fd)
        except Stop as stop:
            row = {'path': str(path), 'state': stop.state, **stop.detail}
            problems.append(f'scripts: {relative} {stop.state}')
        rows[relative] = row
    return rows, problems


def observe(host, root=ROOT, clock=time.monotonic, source=None, out=None, lock=None):
    """source/out/lock exist for tests only; the CLI always observes the committed source root and compile OUT_DIR."""
    if host != HOST:
        raise ValueError(f'--host must be {HOST}')
    lock = lock or base.lock_path(root)
    held, reason = base.take_lock(lock)
    if held is None:
        return json.dumps({'schema': SCHEMA, 'host': host, 'complete': False, 'lock': {'held': False, 'reason': reason},
                           'problems': [reason]}, sort_keys=True)
    try:
        if source is None:
            source, out = paths(root)
        budget = Budget(clock)
        report = {'schema': SCHEMA, 'host': host, 'limits': dict(LIMITS), 'lock': {'held': True, 'path': str(lock)},
                  'sourceRoot': str(source), 'outDir': str(out), 'problems': [],
                  'gate': 'complete:true measures; the reviewer compares summary.incremental with the allocated build'}
        report['targetFiles'], problems = observe_target_files(out, budget)
        report['problems'] += problems
        report['otatools'], problems = observe_otatools(out, budget)
        report['problems'] += problems
        report['scripts'], problems = observe_scripts(source, budget)
        report['problems'] += problems
        if budget.expired():
            report['problems'].append('deadline: the observation exceeded its time bound')
        report['complete'] = not report['problems']
        report['elapsedSeconds'] = round(clock() - budget.start, 3)
    finally:
        os.close(held)  # releases the flock
    text = json.dumps(report, sort_keys=True, separators=(',', ':'))
    if len(text.encode()) > LIMITS['outputBytes']:
        text = json.dumps({'schema': SCHEMA, 'host': host, 'complete': False,
                           'problems': ['output: the report exceeds outputBytes']}, sort_keys=True)
    return text


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('command', choices=('observe',))
    p.add_argument('--host', required=True)
    a = p.parse_args(argv)
    try:
        print(observe(a.host))
        return 0
    except (ValueError, KeyError, OSError) as error:
        print(json.dumps({'verdict': 'INVALID', 'error': str(error)[:200]}), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
