#!/usr/bin/env python3
"""BLAZER-OUTPUT-OBSERVE (Release-owned): one bounded, read-only observation of a finished blazer compile's outputs,
for the signing and packaging bindings and for the build-identity gate. It establishes nothing by itself: it reports
measured identities for a reviewed binding commit, which every consuming step re-verifies at execution.

  observe --host immortal-os-builder

Exactly these paths, fixed in source (no argument chooses a path). S is the isolated blazer-prep work tree
(os/build-only/blazer-prep-host.json sourceRoot) and OUT is S/<outDir> of the committed target
os/build-only/blazer-output-observe.json {build, outDir}. outDir is exactly one of two names: that build's own
os/build-only/blazer.json compile.outDirTemplate directory (out-blazer-build-<build>, every compile since F1/F2), or
the legacy fixed out-blazer-build that every compile before F1/F2 wrote; anything else refuses (exit 2). The bindings
are read only once the lock is held (an unusable target with the lock unavailable is that lock report). Every report
past the lock carries the target, so the build-identity gate compares summary.incremental with target.build:
  targetFiles  the one OUT/target/product/blazer/obj/PACKAGING/target_files_intermediates/blazer-target_files*.zip
               (Flavors' compile collector paths; zero or several matches are reported, never chosen between): its
               size, sha256, mode and mtime, then from its central directory only these members, each at most
               memberBytes and CRC-checked while read: every <PARTITION>/build.prop and <PARTITION>/etc/build.prop
               (only the ro.*build.{version.incremental,date.utc,fingerprint,id,type,tags} and ro.product[.*].device
               values), META/misc_info.txt (only ab_update, virtual_ab, dynamic_partition_list and the
               <name>.fingerprint:<value> tokens release-pipeline.py check_build_identity reads; never a whole
               avb_*_args line), META/ab_partitions.txt (the partition names) and OTA/android-info.txt (only the
               board, version-bootloader, version-baseband and partition-exists requirements, one raw value per
               'require k=v' line as factory-package.py android_info reads them); and, from the central directory only (names,
               sizes and CRC32, never content), the factory inputs scripts/factory-package.py requires: RADIO/bootloader.img
               and RADIO/radio.img, the GSC firmware under os/flavors/blazer.json's gscFirmware.source, and each
               dynamic partition's IMAGES/<part>.img, reported beside the flavor's committed board/firmware pins
  otatools     OUT/host/linux-x86/obj/ETC/otatools-packagelinux_glibc_x86_64_intermediates/
               otatools-packagelinux_glibc_x86_64 (an extensionless ZIP): its size and sha256; the size and sha256 of
               its bin/fastboot member (host-sign.py host.sign.factory.fastbootSha256) and of its
               device/common/generate-factory-images-common.sh and clear-factory-images-variables.sh (the generator the
               release script sources after unzipping otatools, beside os/flavors/blazer.json generatorFixture pins);
               and from the central directory the state, size and CRC32 of bin/simg2img, bin/lpunpack, bin/fsck.erofs
  vendorBoot   OUT/target/product/blazer/vendor_boot.img and vendor_kernel_boot.img: size, sha256 and whether the
               header magic is VNDRBOOT (the compile collector's other two outputs)
  scripts      S/script/generate-release.sh and S/script/common.sh: size and sha256 (what host-sign reads with S as cwd)

Every path is reached as scripts/blazer-binding-observe.py reaches its paths: a descriptor walk from / with each
directory component opened O_DIRECTORY|O_NOFOLLOW relative to the previous descriptor and the leaf O_NOFOLLOW relative
to its parent's; a component that is or becomes a link is refused at that step and never traversed. Each file is
fstat'ed regular and the same inode as its lstat, hashed streamed within its byte cap and the deadline, its ZIP members
read from that same open file, then fstat'ed again: any inode, size or mtime change reports `changed`. Only allowlisted
values are emitted, each at most VALUE_CHARS printable characters; no other content leaves the host. Nothing is
written, invoked, signed or changed. The whole observation holds the existing shared builder lock
(os/release-pipeline/frankel.json host.lockPath), exclusive and non-blocking: never created, never waited on; held by
another job, absent or unusable, the report is complete:false with that reason and nothing else is read.

ZIP members are read only when stored or deflated (any other method, encryption, a duplicate name, a central directory
beyond zipEntries/centralDirectoryBytes, or any decoding error is reported, never followed); the end-of-central-directory
record is checked before the directory is parsed. Each file is checked unchanged by device, inode, size, mtime and
ctime after its reads. Every emitted string is printable ASCII within VALUE_CHARS. An OSError in one section is
reported in that section; the report is always printed.

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
SCRIPTS = ('script/generate-release.sh', 'script/common.sh')
GENERATOR = ('generate-factory-images-common.sh', 'clear-factory-images-variables.sh')  # otatools device/common/<name>
OTATOOLS_BINARIES = ('bin/simg2img', 'bin/lpunpack', 'bin/fsck.erofs')
VENDOR_BOOT = {'vendorBootImage': f'{PRODUCT}/vendor_boot.img', 'vendorKernelBootImage': f'{PRODUCT}/vendor_kernel_boot.img'}
VENDOR_BOOT_MAGIC = b'VNDRBOOT'
PROP_MEMBER = re.compile(r'[A-Z][A-Z0-9_]{0,31}/(?:etc/)?build\.prop')
PROP_KEY = re.compile(r'ro\.(?:[a-z0-9_]{1,32}\.)?build\.(?:version\.incremental|date\.utc|fingerprint|id|type|tags)'
                      r'|ro\.product\.(?:[a-z0-9_]{1,32}\.)?device')
FINGERPRINT_TOKEN = re.compile(r'([A-Za-z0-9_.]{1,96}\.fingerprint):(\S+)')
PARTITION = re.compile(r'[a-z0-9_]{1,32}')
MISC_KEYS = ('ab_update', 'virtual_ab', 'dynamic_partition_list')
INFO_KEYS = ('board', 'version-bootloader', 'version-baseband', 'partition-exists')
INFO_LINE = re.compile(r'require ([\w-]+)=(.*)')
REQUIRED_MEMBERS = ('SYSTEM/build.prop', 'META/misc_info.txt', 'META/ab_partitions.txt', 'OTA/android-info.txt')
FASTBOOT = 'bin/fastboot'
MAX_LISTED = 64
VALUE_CHARS = 512
PRINTABLE = re.compile(r'[ -~]*')
LIMITS = {'seconds': 900, 'targetFilesBytes': 32 << 30, 'otatoolsBytes': 4 << 30, 'scriptBytes': 1 << 20,
          'memberBytes': 1 << 20, 'fastbootBytes': 128 << 20, 'propMembers': 32, 'zipEntries': 200000,
          'centralDirectoryBytes': 64 << 20, 'vendorBootBytes': 512 << 20, 'listed': 128, 'nameEntries': 4096,
          'outputBytes': 64 << 10}
READABLE_METHODS = (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)
OUT_DIR_NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,63}')
TARGET = 'os/build-only/blazer-output-observe.json'
TARGET_KEYS = {'schemaVersion', 'kind', 'basis', 'build', 'outDir'}
TARGET_KIND = 'rose-blazer-output-observe-target'
BUILD = re.compile(r'[1-9][0-9]{9}')  # blazer-allocate's YYYYMMDDNN
LEGACY_OUT_DIR = 'out-blazer-build'  # the fixed OUT_DIR of every compile before F1/F2 (297122a), never one build's own


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


def committed(root, relative, unique=False):
    """A committed JSON binding that must be an object; ValueError (exit 2) otherwise, and with unique for a repeated
    key too (a target whose diff shows one outDir must never observe another)."""
    def pairs(items):
        if len({key for key, _ in items}) != len(items):
            raise ValueError(f'{relative} repeats a key')
        return dict(items)
    try:
        value = json.loads((root/relative).read_text(), **({'object_pairs_hook': pairs} if unique else {}))
    except RecursionError:
        raise ValueError(f'{relative} is nested too deeply') from None
    if not isinstance(value, dict):
        raise ValueError(f'{relative} is not a JSON object')
    return value


def paths(root=ROOT):
    """(S, OUT, target) from the committed bindings, never an argument: OUT is the target's outDir under S, which must
    be exactly the target build's own compile.outDirTemplate directory or the legacy fixed out-blazer-build."""
    source = committed(root, 'os/build-only/blazer-prep-host.json').get('sourceRoot')
    compile_ = committed(root, 'os/build-only/blazer.json').get('compile')
    template = compile_.get('outDirTemplate') if isinstance(compile_, dict) else None
    target = committed(root, TARGET, unique=True)
    if not isinstance(source, str) or not Path(source).is_absolute() or '..' in Path(source).parts:
        raise ValueError('the committed source root is unbound or malformed')
    source = Path(source)
    if (set(target) != TARGET_KEYS or type(target['schemaVersion']) is not int or target['schemaVersion'] != 1
            or target['kind'] != TARGET_KIND):
        raise ValueError(f'{TARGET} is not exactly an observation target')
    build, out_dir = target['build'], target['outDir']
    if not isinstance(build, str) or not BUILD.fullmatch(build):
        raise ValueError('the observation target build must be a ten-digit YYYYMMDDNN string')
    if (not isinstance(template, str) or template.count('{build}') != 1
            or re.search(r'[{}]', template.replace('{build}', ''))):
        raise ValueError('compile.outDirTemplate must hold {build} exactly once and no other field')
    forms = {template.format(build=build): 'per-build', LEGACY_OUT_DIR: 'legacy'}
    if not isinstance(out_dir, str) or out_dir not in forms or not OUT_DIR_NAME.fullmatch(out_dir):
        raise ValueError(f'the observation target outDir must be {template.format(build=build)} or {LEGACY_OUT_DIR}')
    return source, source/out_dir, {'build': build, 'outDir': out_dir, 'form': forms[out_dir]}


def open_leaf(parent, leaf, path):
    """(fd, lstat) of LEAF opened O_NOFOLLOW relative to PARENT, checked regular and the same inode; Stop otherwise."""
    try:
        before = os.lstat(leaf, dir_fd=parent)
    except FileNotFoundError:
        raise Stop('absent') from None
    except OSError as error:
        raise Stop('error', error=type(error).__name__) from None
    if stat.S_ISLNK(before.st_mode):
        raise Stop('link', component=clean(str(path)), target=clean(os.readlink(leaf, dir_fd=parent)))
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
    """Stop('changed') unless device, inode, size, mtime and ctime are all as first seen: an in-place rewrite that
    restores mtime still changes ctime."""
    after = os.fstat(fd)
    fields = lambda st: (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)
    if fields(after) != fields(before):
        raise Stop('changed')


def identity(row, before, sha256):
    row.update(state='present', size=before.st_size, sha256=sha256, mode=f'{stat.S_IMODE(before.st_mode):o}',
               mtimeNs=before.st_mtime_ns)


def clean(value):
    """An emitted value: printable ASCII, at most VALUE_CHARS characters, or a marker."""
    value = value.strip(' \t\r')
    if not PRINTABLE.fullmatch(value):
        return '<non-printable>'
    return value if len(value) <= VALUE_CHARS else value[:VALUE_CHARS] + '<truncated>'


def readable(info):
    """Stop unless the member is a plain stored or deflated, unencrypted entry: other methods (bzip2, lzma) can expand
    without bound inside a single read."""
    if info.compress_type not in READABLE_METHODS:
        raise Stop('unsupported-compression', method=int(info.compress_type))
    if info.flag_bits & 0x1:
        raise Stop('encrypted')
    if stat.S_ISLNK(info.external_attr >> 16):
        raise Stop('link-entry')


def read_member(archive, info, cap, budget):
    """The member's bytes, at most CAP, CRC-checked by zipfile while read; Stop otherwise."""
    readable(info)
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
    except Stop:
        raise
    except Exception as error:  # noqa: BLE001 - any decoding fault (zlib.error, struct.error, ...) is a reported row
        raise Stop('corrupt', error=type(error).__name__) from None
    return b''.join(chunks)


def hash_member(archive, info, cap, budget):
    readable(info)
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
    except Stop:
        raise
    except Exception as error:  # noqa: BLE001 - any decoding fault is a reported row
        raise Stop('corrupt', error=type(error).__name__) from None
    return {'state': 'present', 'size': size, 'sha256': digest.hexdigest()}


class Archive:
    """A ZipFile over a duplicate of FD (the same open file), opened only after its end-of-central-directory record
    shows at most zipEntries entries and centralDirectoryBytes of directory; both handles are closed on exit."""
    def __init__(self, fd, budget):
        self.handle = os.fdopen(os.dup(fd), 'rb')
        try:
            if budget.expired():
                raise Stop('deadline')
            try:
                end = zipfile._EndRecData(self.handle)  # the record zipfile itself reads first; it reads <= 64 KiB
            except Exception as error:  # noqa: BLE001
                raise Stop('not-zip', error=type(error).__name__) from None
            if not end:
                raise Stop('not-zip')
            if end[zipfile._ECD_ENTRIES_TOTAL] > LIMITS['zipEntries']:
                raise Stop('too-many-entries', entries=int(end[zipfile._ECD_ENTRIES_TOTAL]))
            if end[zipfile._ECD_SIZE] > LIMITS['centralDirectoryBytes']:
                raise Stop('central-directory-too-large', size=int(end[zipfile._ECD_SIZE]))
            try:
                self.archive = zipfile.ZipFile(self.handle)
            except Exception as error:  # noqa: BLE001 - a malformed directory or name is a reported row
                raise Stop('not-zip', error=type(error).__name__) from None
            infos = self.archive.infolist()
            if len(infos) > LIMITS['zipEntries']:
                raise Stop('too-many-entries', entries=len(infos))
            names = [info.filename for info in infos]
            if len(set(names)) != len(names):
                raise Stop('duplicate-members')
            self.infos = {info.filename: info for info in infos}
        except BaseException:
            self.handle.close()
            raise

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        try:
            self.archive.close()
        finally:
            self.handle.close()


def lines(data):
    """Lines as Android's property parsers see them: split on '\\n' only, a trailing '\\r' dropped."""
    return [line[:-1] if line.endswith('\r') else line for line in data.decode('utf-8', 'replace').split('\n')]


def props(data):
    found = {}
    for line in lines(data):
        key, sep, value = line.partition('=')
        key = key.strip(' \t')
        if sep and PROP_KEY.fullmatch(key):
            found[key] = clean(value)
    return found


def misc(data):
    found, fingerprints = {}, []
    for line in lines(data):
        key, sep, value = line.partition('=')
        key = key.strip(' \t')
        if sep and key in MISC_KEYS:
            found[key] = clean(value)
        fingerprints += [{'name': clean(name), 'value': clean(token)} for name, token in FINGERPRINT_TOKEN.findall(line)]
    found['fingerprints'] = fingerprints[:LIMITS['listed']]
    return found


def android_info(data):
    """Exactly what factory-package.py android_info builds for these keys: one raw value per 'require k=v' line."""
    found = {}
    for line in lines(data):
        match = INFO_LINE.fullmatch(line.strip(' \t'))
        if match and match.group(1) in INFO_KEYS:
            values = found.setdefault(match.group(1), [])
            if len(values) < 16:
                values.append(clean(match.group(2)))
    return found


def ab_partitions(data):
    names = data.decode('utf-8', 'replace').split()
    return [n for n in names if PARTITION.fullmatch(n)][:LIMITS['listed']], len(names)


def target_files_members(archive, infos, budget):
    """The allowlisted identity values of the target-files, and the problems."""
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
    for key, name, parse in (('miscInfo', 'META/misc_info.txt', misc), ('androidInfo', 'OTA/android-info.txt', android_info),
                             ('abPartitions', 'META/ab_partitions.txt', ab_partitions)):
        if name in infos:
            try:
                out[key] = parse(read_member(archive, infos[name], LIMITS['memberBytes'], budget))
            except Stop as stop:
                out[key] = {'state': stop.state, **stop.detail}
                problems.append(f'targetFiles: member {name} {stop.state}')
    if isinstance(out.get('abPartitions'), tuple):
        names, total = out['abPartitions']
        out['abPartitions'] = {'names': names, 'count': total, 'truncated': total > len(names)}
    values = lambda suffix: sorted({v for p in out['props'].values() for k, v in p.items()
                                    if isinstance(v, str) and k.endswith(suffix)})
    out['summary'] = {'incremental': values('build.version.incremental'), 'dateUtc': values('build.date.utc')}
    out['factoryInputs'] = factory_inputs(infos, out['miscInfo'], out.get('abPartitions'))
    return out, problems


def factory_inputs(infos, misc_info, ab=None, root=ROOT):
    """Report-only, from the central directory (no member content): what factory-package.py's target inventory needs,
    beside the flavor's committed pins. An absence here is a finding for the reviewer, not an observation failure."""
    flavor = json.loads((root/'os/flavors/blazer.json').read_text())
    source = flavor['artifacts']['factory']['generatorMembers']['gscFirmware']['source']
    meta = lambda info: {'state': 'present', 'size': info.file_size, 'crc32': f'{info.CRC:08x}'}
    firmware = {kind: (meta(infos[f'RADIO/{kind}.img']) if f'RADIO/{kind}.img' in infos else {'state': 'absent'})
                for kind in ('bootloader', 'radio')}
    gsc = sorted(n for n in infos if n.startswith(source) and not n.endswith('/'))
    dynamic = (misc_info or {}).get('dynamic_partition_list', '') if isinstance(misc_info, dict) else ''
    parts = [p for p in dynamic.split() if PARTITION.fullmatch(p)]
    ab_names = ab.get('names', []) if isinstance(ab, dict) else []
    # factory-package.py target_inventory: an A/B partition outside the dynamic list with no IMAGES/<part>.img must be
    # firmware-carried (a roseos.factory-firmware-observation.v1 has to cover it)
    without_image = [p for p in ab_names if p not in parts and f'IMAGES/{p}.img' not in infos]
    return {'firmware': firmware,
            'gscFirmware': {'source': source, 'count': len(gsc), 'truncated': len(gsc) > MAX_LISTED,
                            'members': [{'name': clean(n[len(source):]), 'nested': '/' in n[len(source):], **meta(infos[n])}
                                        for n in gsc[:MAX_LISTED]]},
            'dynamicImagesMissing': [p for p in parts if f'IMAGES/{p}.img' not in infos],
            'withoutImage': without_image,
            'flavorPins': {'board': flavor['device']['board'], 'bootloader': flavor['firmware']['bootloader'],
                           'baseband': flavor['firmware']['baseband'],
                           'partitionExists': flavor['firmware'].get('partitionExists')}}


def observe_target_files(out, budget):
    row = {'directory': str(out/TARGET_FILES_DIR)}
    try:
        directory = base.open_directory(out/TARGET_FILES_DIR)
    except base.Walk as walk:
        row.update(state=walk.state, component=clean(str(walk.component)), **({'target': clean(walk.target)} if walk.target else {}))
        return row, [f'targetFiles: {walk.state} at {clean(str(walk.component))}']
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
            row.update(state='absent' if not names else 'ambiguous', names=[clean(n) for n in sorted(names)[:8]])
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
        with Archive(fd, budget) as opened:
            members, problems = target_files_members(opened.archive, opened.infos, budget)
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
        raise Stop(walk.state, component=clean(str(walk.component)), **({'target': clean(walk.target)} if walk.target else {})) from None
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
        with Archive(fd, budget) as opened:
            problems = []
            members = [('fastboot', FASTBOOT, LIMITS['fastbootBytes'])] + \
                [(f'generator:{name}', f'device/common/{name}', LIMITS['scriptBytes']) for name in GENERATOR]
            for key, member, cap in members:
                if member not in opened.infos:
                    row[key] = {'state': 'absent'}
                    problems.append(f'otatools: member {member} absent')
                    continue
                try:
                    row[key] = hash_member(opened.archive, opened.infos[member], cap, budget)
                except Stop as stop:
                    row[key] = {'state': stop.state, **stop.detail}
                    problems.append(f'otatools: member {member} {stop.state}')
            row['binaries'] = {name: ({'state': 'present', 'size': opened.infos[name].file_size,
                                       'crc32': f'{opened.infos[name].CRC:08x}'} if name in opened.infos else {'state': 'absent'})
                               for name in OTATOOLS_BINARIES}
            pins = json.loads((ROOT/'os/flavors/blazer.json').read_text())['inventory']['generatorFixture']['files']
            row['generatorPins'] = {name: pins.get(name) for name in GENERATOR}
        unchanged(fd, before)
    except Stop as stop:
        return {'path': str(path), 'state': stop.state, **stop.detail}, [f'otatools: {stop.state}']
    finally:
        os.close(fd)
    return row, problems


def observe_vendor_boot(out, budget):
    rows, problems = {}, []
    for key, relative in VENDOR_BOOT.items():
        path = out/relative
        try:
            fd, before, row = observe_regular(path, LIMITS['vendorBootBytes'], budget)
            try:
                row['vndrbootMagic'] = os.pread(fd, len(VENDOR_BOOT_MAGIC), 0) == VENDOR_BOOT_MAGIC
                unchanged(fd, before)
            finally:
                os.close(fd)
        except Stop as stop:
            row = {'path': str(path), 'state': stop.state, **stop.detail}
            problems.append(f'vendorBoot: {key} {stop.state}')
        rows[key] = row
    return rows, problems


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


def observe(host, root=ROOT, clock=time.monotonic, source=None, out=None, lock=None, target=None):
    """source/out/lock/target exist for tests only; the CLI always observes the committed source root and target."""
    if host != HOST:
        raise ValueError(f'--host must be {HOST}')
    lock = lock or base.lock_path(root)
    held, reason = base.take_lock(lock)
    if held is None:
        return json.dumps({'schema': SCHEMA, 'host': host, 'complete': False, 'lock': {'held': False, 'reason': reason},
                           'problems': [reason]}, sort_keys=True)
    try:
        if source is None:
            source, out, target = paths(root)
        budget = Budget(clock)
        report = {'schema': SCHEMA, 'host': host, 'limits': dict(LIMITS), 'lock': {'held': True, 'path': str(lock)},
                  'sourceRoot': str(source), 'outDir': str(out), 'target': target, 'problems': [],
                  'gate': 'complete:true measures; the reviewer compares summary.incremental with target.build'}
        for key, section in (('targetFiles', lambda: observe_target_files(out, budget)),
                             ('otatools', lambda: observe_otatools(out, budget)),
                             ('vendorBoot', lambda: observe_vendor_boot(out, budget)),
                             ('scripts', lambda: observe_scripts(source, budget))):
            try:
                report[key], problems = section()
            except OSError as error:
                report[key], problems = {'state': 'error', 'error': type(error).__name__}, [f'{key}: error {type(error).__name__}']
            report['problems'] += problems
        if budget.expired():
            report['problems'].append('deadline: the observation exceeded its time bound')
        report['complete'] = not report['problems']
        report['elapsedSeconds'] = round(clock() - budget.start, 3)
    finally:
        os.close(held)  # releases the flock
    text = json.dumps(report, sort_keys=True, separators=(',', ':'))
    if len(text.encode()) + 1 > LIMITS['outputBytes']:  # print() adds the newline the receipt reader counts
        text = json.dumps({'schema': SCHEMA, 'host': host, 'complete': False, 'target': target, 'outDir': str(out),
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
