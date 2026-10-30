#!/usr/bin/env python3
"""FLAVOR001: classify an install artifact against an OS flavor contract and package a complete factory archive.

  factory-package.py contract --flavor F
  factory-package.py classify ARCHIVE --flavor F [--target-files TF] [--build B] [--avb-public-key-sha256 H]
  factory-package.py package  ARCHIVE --flavor F --target-files TF --out NEW_DIR [--build B] [--avb-public-key-sha256 H]

F is os/flavors/F.json (frankel or rango). The contract is cross-checked against os/devices/pixel10.json and the pinned
adevtool android-info.txt on every load. ARCHIVE is any candidate: an OTA ({device}-ota_update-{build}.zip), a
target-files, an img zip, or the factory install archive that generate-release.sh produces with fastboot
optimize-factory-image ({device}-install-{build}.zip). Only the last can be factory-ready, and only when:
- every ZIP member name is safe and unique, every CRC checks, and nothing is a link;
- android-info.txt names the flavor board, bootloader and baseband, and exactly the matching bootloader-/radio-
  images are present (no other firmware versions);
- the inventory derived from the given signed target-files (META/ab_partitions.txt, META/misc_info.txt,
  OTA/android-info.txt, IMAGES/, RADIO/) is covered: each non-dynamic A/B image byte-equal, firmware byte-equal to
  RADIO/bootloader.img and RADIO/radio.img, dynamic partitions by contiguous sparse super_N.img segments, and no
  unexpected image;
- avb_pkmd.bin equals the flavor's AVB public key and the flash script declares the data wipe of a first install.
Nothing is extracted, executed, flashed or published; images are read in memory-bounded streams. Factory-ready means
complete and consistent offline. It is not a signature verification of the archive, relock safety or phone acceptance.

package copies a factory-ready ARCHIVE (and ARCHIVE.sig when present) into NEW_DIR with factory-package.json; the
generator itself is the established generate-release.sh tail, never reimplemented here.
Exit status: 0 factory-ready / packaged / contract valid, 3 refused (the JSON names every missing input and problem),
2 invalid usage or contract.
"""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import stat as statmod
import sys
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
from release_receipts import sha256_file  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = 'roseos.factory-package.v1'
CHUNK = 1 << 20
MAGIC = {'boot': b'ANDROID!', 'init_boot': b'ANDROID!', 'vendor_boot': b'VNDRBOOT', 'vendor_kernel_boot': b'VNDRBOOT'}
VBMETA_MAGIC = b'AVB0'
SPARSE_MAGIC = b'\x3a\xff\x26\xed'
SUPER = re.compile(r'super_(\d+)\.img')

class ContractError(Exception):
    pass

class Refused(Exception):
    def __init__(self, result):
        super().__init__('not factory-ready')
        self.result = result

def android_info(text):
    info = {}
    for line in text.splitlines():
        match = re.fullmatch(r'require ([\w-]+)=(.*)', line.strip())
        if match:
            info.setdefault(match.group(1), []).append(match.group(2))
    return info

def load_flavor(name, root=ROOT):
    if not re.fullmatch(r'[a-z0-9]+', name or ''):
        raise ContractError(f'invalid flavor name {name!r}')
    path = root/'os/flavors'/f'{name}.json'
    if not path.is_file():
        raise ContractError(f'no flavor contract {path.relative_to(root)}')
    flavor = json.loads(path.read_text())
    if flavor.get('kind') != 'roseos.os-flavor' or flavor.get('schemaVersion') != 1 or flavor.get('flavor') != name:
        raise ContractError(f'{name}: not a schemaVersion 1 roseos.os-flavor for {name}')
    contract = json.loads((root/flavor['provenance']['deviceContract']).read_text())
    device, entry, shared = flavor['device'], contract['devices'].get(name), contract['shared']
    if entry is None:
        raise ContractError(f'{name}: not in {flavor["provenance"]["deviceContract"]}')
    checks = {
        'device.codename': (device['codename'], name), 'device.board': (device['board'], name),
        'device.model': (device['model'], entry['model']), 'device.product': (device['product'], entry['product']),
        'device.lunch': (device['lunch'], entry['lunch']['expected']),
        'provenance.vendorBuildId': (flavor['provenance']['vendorBuildId'], shared['vendorBuildId']),
        'provenance.release': (flavor['provenance']['release'], contract['source']['release']),
        'provenance.adevtool': (flavor['provenance']['adevtool'], contract['source']['projects']['vendor/adevtool']),
        'firmware.bootloader': (flavor['firmware']['bootloader'], shared['firmware']['bootloader']),
        'firmware.baseband': (flavor['firmware']['baseband'], shared['firmware']['baseband']),
        'firmware.partitionExists': (flavor['firmware']['partitionExists'], shared['firmware']['partitionExists']),
    }
    info = android_info((root/flavor['provenance']['androidInfoFixture']).read_text())
    checks['android-info board'] = (info.get('board'), [name])
    checks['android-info bootloader'] = (info.get('version-bootloader'), [flavor['firmware']['bootloader']])
    checks['android-info baseband'] = (info.get('version-baseband'), [flavor['firmware']['baseband']])
    checks['android-info partition-exists'] = (info.get('partition-exists'), flavor['firmware']['partitionExists'])
    script = root/flavor['provenance']['releaseScript']['fixture']
    checks['releaseScript sha256'] = (hashlib.sha256(script.read_bytes()).hexdigest(),
        flavor['provenance']['releaseScript']['generate-release.sh'])
    drift = [key for key, (have, want) in checks.items() if have != want]
    if drift:
        raise ContractError(f'{name}: contract disagrees with its sources: {", ".join(drift)}')
    factory = flavor['artifacts']['factory']
    if not (factory['dataWipe'] and factory['requiresUnlockedBootloader']) or flavor['artifacts']['ota']['dataWipe']:
        raise ContractError(f'{name}: install-mode declarations must be factory wipe+unlock, OTA no wipe')
    return flavor

def pinned_avb_key(flavor, root=ROOT):
    source = flavor['identity']['avbPublicKeySha256From']
    match = re.fullmatch(r'(os/[\w./-]+\.json) ([\w.]+)', source)
    if not match:
        return None  # runtime-only identity (rango first-use): the caller must pass it
    value = json.loads((root/match.group(1)).read_text())
    for key in match.group(2).split('.'):
        value = value[key]
    return value

def member_digest(archive, name):
    digest, size = hashlib.sha256(), 0
    with archive.open(name) as stream:
        for block in iter(lambda: stream.read(CHUNK), b''):
            digest.update(block)
            size += len(block)
    return digest.hexdigest(), size

def head(archive, name, count=8):
    with archive.open(name) as stream:
        return stream.read(count)

def open_checked(path, label, result):
    """Open a ZIP whose names are safe, unique and link-free and whose CRCs check; problems go to result."""
    try:
        archive = zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile) as error:
        result['problems'].append(f'corrupt-archive:{label}:{type(error).__name__}')
        return None
    names = [info.filename for info in archive.infolist()]
    bad = False
    for info in archive.infolist():
        name = info.filename
        parts = PurePosixPath(name).parts
        if name.startswith('/') or '\\' in name or '..' in parts or re.match(r'[A-Za-z]:', name) or '\0' in name:
            result['problems'].append(f'unsafe-path:{label}:{name}')
            bad = True
        if statmod.S_ISLNK(info.external_attr >> 16):
            result['problems'].append(f'link-entry:{label}:{name}')
            bad = True
    for name in sorted({n for n in names if names.count(n) > 1}):
        result['problems'].append(f'duplicate-entry:{label}:{name}')
        bad = True
    if not bad:
        try:
            broken = archive.testzip()
        except (OSError, zipfile.BadZipFile, EOFError, NotImplementedError) as error:
            broken = f'?{type(error).__name__}'
        if broken:
            result['problems'].append(f'corrupt-entry:{label}:{broken}')
            bad = True
    if bad:
        archive.close()
        return None
    return archive

def role_of(names):
    if 'payload.bin' in names and 'payload_properties.txt' in names:
        return 'ota-update'
    if 'META/misc_info.txt' in names:
        return 'target-files'
    tops = {n.split('/', 1)[0] for n in names}
    if len(tops) == 1 and all('/' in n for n in names) and re.fullmatch(r'[a-z0-9]+-install-\d+', next(iter(tops))):
        return 'factory-install'
    if 'android-info.txt' in names and any(n.endswith('.img') and '/' not in n for n in names):
        return 'img-zip'
    return 'unknown'

def target_inventory(path, flavor, build, result):
    archive = open_checked(path, 'target-files', result)
    if archive is None:
        return None
    with archive:
        names = set(archive.namelist())
        need = [n for n in ('META/ab_partitions.txt', 'META/misc_info.txt', 'OTA/android-info.txt') if n not in names]
        result['missing'] += [f'target-files:{n}' for n in need]
        if need:
            return None
        misc = dict(line.split('=', 1) for line in archive.read('META/misc_info.txt').decode().splitlines() if '=' in line)
        ab = archive.read('META/ab_partitions.txt').decode().split()
        info = android_info(archive.read('OTA/android-info.txt').decode())
        if info.get('board') != [flavor['device']['board']]:
            result['problems'].append(f'wrong-device:target-files board {info.get("board")}')
        for key, want in (('version-bootloader', 'bootloader'), ('version-baseband', 'baseband')):
            if info.get(key) != [flavor['firmware'][want]]:
                result['problems'].append(f'mixed-firmware:target-files {key} {info.get(key)}')
        if 'SYSTEM/build.prop' in names and build:
            props = dict(l.split('=', 1) for l in archive.read('SYSTEM/build.prop').decode().splitlines() if '=' in l)
            if props.get('ro.build.version.incremental') != build:
                result['problems'].append(f'identity-mismatch:target-files build {props.get("ro.build.version.incremental")}')
        if misc.get('ab_update') != 'true' and flavor['installSemantics']['abUpdate']:
            result['problems'].append('install-semantics:target-files ab_update is not true')
        dynamic = misc.get('dynamic_partition_list', '').split()
        if len(set(ab)) != len(ab):
            result['problems'].append('duplicate-partition:META/ab_partitions.txt')
        stray = sorted(set(dynamic) - set(ab))
        if stray:
            result['problems'].append(f'install-semantics:dynamic partitions outside ab_partitions {stray}')
        inventory = {'abPartitions': sorted(set(ab)), 'dynamic': sorted(set(dynamic)), 'virtualAb': misc.get('virtual_ab') == 'true',
                     'images': {}, 'firmwareCarried': [], 'firmware': {}}
        for part in sorted(set(ab) - set(dynamic)):
            if f'IMAGES/{part}.img' in names:
                inventory['images'][part] = member_digest(archive, f'IMAGES/{part}.img')[0]
            else:
                inventory['firmwareCarried'].append(part)
        for kind in ('bootloader', 'radio'):
            if f'RADIO/{kind}.img' in names:
                inventory['firmware'][kind] = member_digest(archive, f'RADIO/{kind}.img')[0]
            elif inventory['firmwareCarried'] or kind == 'bootloader':
                result['missing'].append(f'target-files:RADIO/{kind}.img')
        return inventory

def check_factory(archive, names, flavor, build, inventory, avb_key, result):
    device = flavor['device']['codename']
    top = names[0].split('/', 1)[0]
    match = re.fullmatch(r'([a-z0-9]+)-install-(\d+)', top)
    if match.group(1) != device:
        result['problems'].append(f'wrong-device:archive directory {top}')
    if build and match.group(2) != build:
        result['problems'].append(f'identity-mismatch:archive build {match.group(2)} expected {build}')
    build = build or match.group(2)
    result['build'] = build
    files = {n[len(top) + 1:]: n for n in names if not n.endswith('/')}
    if any('/' in rel for rel in files):
        result['problems'] += [f'unexpected-member:{n}' for rel, n in sorted(files.items()) if '/' in rel]
    factory = flavor['artifacts']['factory']
    expand = lambda s: s.format(device=device, build=build, **{k: flavor['firmware'][k] for k in ('bootloader', 'baseband')})
    required = [expand(m) for m in factory['requiredMembers']]
    result['missing'] += [f'factory:{m}' for m in required if m not in files]
    if 'android-info.txt' in files:
        info = android_info(archive.read(files['android-info.txt']).decode(errors='replace'))
        if info.get('board') != [flavor['device']['board']]:
            result['problems'].append(f'wrong-device:android-info board {info.get("board")}')
        for key, want in (('version-bootloader', 'bootloader'), ('version-baseband', 'baseband')):
            if info.get(key) != [flavor['firmware'][want]]:
                result['problems'].append(f'mixed-firmware:android-info {key} {info.get(key)}')
        if info.get('partition-exists') != flavor['firmware']['partitionExists']:
            result['problems'].append(f'mixed-firmware:android-info partition-exists {info.get("partition-exists")}')
    firmware = {'bootloader': expand('bootloader-{device}-{bootloader}.img'), 'radio': expand('radio-{device}-{baseband}.img')}
    for rel in files:
        if re.match(r'(bootloader|radio)-', rel) and rel not in firmware.values():
            result['problems'].append(f'mixed-firmware:unexpected {rel}')
    if 'avb_pkmd.bin' in files:
        actual = member_digest(archive, files['avb_pkmd.bin'])[0]
        result['inventory']['avbPublicKeySha256'] = actual
        if avb_key is None:
            result['missing'].append(f'identity:{device} avbPublicKeySha256 ({flavor["identity"]["avbPublicKeySha256From"]})')
        elif actual != avb_key:
            result['problems'].append('identity-mismatch:avb_pkmd.bin')
    script = b''.join(archive.read(files[s]) for s in ('flash-all.sh', 'script.txt') if s in files)
    if script and factory['dataWipeMarker'].encode() not in script:
        result['problems'].append('install-mode:flash script declares no data wipe for a first install')
    images = sorted(rel for rel in files if rel.endswith('.img') and rel not in firmware.values())
    for rel in images:
        if head(archive, files[rel], 1) == b'':
            result['problems'].append(f'corrupt-image:{rel} empty')
    if inventory is None:
        result['missing'].append('target-files inventory (--target-files: the signed target-files this archive came from)')
        return
    expected = {f'{part}.img' for part in inventory['images']}
    supers = sorted((int(SUPER.fullmatch(rel).group(1)), rel) for rel in images if SUPER.fullmatch(rel))
    for rel in images:
        if rel not in expected and not SUPER.fullmatch(rel):
            result['problems'].append(f'unexpected-image:{rel}')
    for part, digest in inventory['images'].items():
        rel = f'{part}.img'
        if rel not in files:
            result['missing'].append(f'factory:{rel}')
            continue
        magic = MAGIC.get(part, VBMETA_MAGIC if part.startswith('vbmeta') else None)
        if magic and head(archive, files[rel], len(magic)) != magic:
            result['problems'].append(f'corrupt-image:{rel} header')
        elif member_digest(archive, files[rel])[0] != digest:
            result['problems'].append(f'image-mismatch:{rel} differs from target-files IMAGES/{rel}')
    for kind, rel in firmware.items():
        if rel in files and kind in inventory['firmware'] and member_digest(archive, files[rel])[0] != inventory['firmware'][kind]:
            result['problems'].append(f'mixed-firmware:{rel} differs from target-files RADIO/{kind}.img')
    if inventory['dynamic']:
        if not supers:
            result['missing'].append(f'factory:super_1.img.. (dynamic partitions {" ".join(inventory["dynamic"])})')
        elif [n for n, _ in supers] != list(range(1, len(supers) + 1)):
            result['problems'].append(f'corrupt-image:super segments not contiguous {[n for n, _ in supers]}')
        for _, rel in supers:
            if head(archive, files[rel], 4) != SPARSE_MAGIC:
                result['problems'].append(f'corrupt-image:{rel} not an Android sparse image')
    elif supers:
        result['problems'].append('unexpected-image:super segments without dynamic partitions')
    result['inventory'].update({'abPartitions': inventory['abPartitions'], 'dynamic': inventory['dynamic'],
        'virtualAb': inventory['virtualAb'], 'firmwareCarried': inventory['firmwareCarried'],
        'images': sorted(expected), 'superSegments': len(supers), 'dynamicContents': 'not decomposed offline (needs otatools lpunpack)'})

def classify(path, flavor_name, target_files=None, build=None, avb_key=None, root=ROOT):
    flavor = load_flavor(flavor_name, root)
    pinned = pinned_avb_key(flavor, root)
    if pinned and avb_key and avb_key != pinned:
        raise ContractError(f'--avb-public-key-sha256 differs from the pinned {flavor["identity"]["avbPublicKeySha256From"]}')
    avb_key = pinned or avb_key
    device = flavor['device']['codename']
    path = Path(path)
    result = {'schema': SCHEMA, 'flavor': flavor_name, 'device': device, 'artifact': path.name, 'build': build,
              'role': 'unknown', 'factoryReady': False, 'otaApplicable': False, 'missing': [], 'problems': [], 'inventory': {},
              'installMode': None, 'limits': {'phoneValidated': False, 'archiveSignatureVerified': False,
              'relockSafe': 'not claimed', 'firstBoot': 'not claimed'}}
    if not path.is_file() or path.is_symlink():
        result['missing'].append(f'artifact:{path.name}')
        return result
    result['sha256'], result['size'] = sha256_file(path), path.stat().st_size
    archive = open_checked(path, 'artifact', result)
    if archive is None:
        return result
    with archive:
        names = archive.namelist()
        result['role'] = role = role_of(names)
        if role == 'ota-update':
            meta = {}
            if 'META-INF/com/android/metadata' in names:
                meta = dict(l.split('=', 1) for l in archive.read('META-INF/com/android/metadata').decode().splitlines() if '=' in l)
            result['inventory'] = {k: meta.get(k) for k in ('pre-device', 'post-build-incremental', 'ota-type')}
            if meta.get('pre-device') != device:
                result['problems'].append(f'wrong-device:ota pre-device {meta.get("pre-device")}')
            result['otaApplicable'] = not result['problems']
            result['installMode'] = flavor['artifacts']['ota']
            result['problems'].append('not-factory:ota-update is an in-place update, never a first-install package')
        elif role == 'factory-install':
            inventory = target_inventory(Path(target_files), flavor, build, result) if target_files else None
            result['installMode'] = flavor['artifacts']['factory']
            check_factory(archive, names, flavor, build, inventory, avb_key, result)
        else:
            result['problems'].append(f'not-factory:{role} is not a {flavor["artifacts"]["factory"]["file"]} install archive')
    result['factoryReady'] = role == 'factory-install' and not result['missing'] and not result['problems']
    return result

def package(path, flavor_name, target_files, out, build=None, avb_key=None, root=ROOT):
    result = classify(path, flavor_name, target_files, build, avb_key, root)
    if not result['factoryReady']:
        raise Refused(result)
    path, out = Path(path), Path(out)
    out.mkdir(parents=True, exist_ok=False)
    copied = {}
    for source in (path, path.with_name(path.name + '.sig')):
        if source.is_file() and not source.is_symlink():
            shutil.copyfile(source, out/source.name)
            copied[source.name] = sha256_file(out/source.name)
    if copied[path.name] != result['sha256']:
        raise Refused(dict(result, factoryReady=False, problems=['identity-mismatch:archive changed while packaging']))
    receipt = dict(result, files=copied, signaturePresent=len(copied) == 2,
                   targetFilesSha256=sha256_file(target_files))
    (out/'factory-package.json').write_text(json.dumps(receipt, indent=2, sort_keys=True) + '\n')
    return receipt

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('contract').add_argument('--flavor', required=True)
    for name in ('classify', 'package'):
        p = sub.add_parser(name)
        p.add_argument('archive')
        p.add_argument('--flavor', required=True)
        p.add_argument('--target-files', required=name == 'package')
        p.add_argument('--build')
        p.add_argument('--avb-public-key-sha256')
        if name == 'package':
            p.add_argument('--out', required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == 'contract':
            print(json.dumps(load_flavor(args.flavor), indent=2, ensure_ascii=False))
            return 0
        if args.build and not re.fullmatch(r'\d{10}', args.build):
            raise ContractError('--build must be a ten-digit build number')
        if args.avb_public_key_sha256 and not re.fullmatch(r'[0-9a-f]{64}', args.avb_public_key_sha256):
            raise ContractError('--avb-public-key-sha256 must be 64 lowercase hex digits')
        if args.command == 'classify':
            result = classify(args.archive, args.flavor, args.target_files, args.build, args.avb_public_key_sha256)
        else:
            if Path(args.out).exists():
                raise ContractError(f'--out {args.out} already exists')
            result = package(args.archive, args.flavor, args.target_files, args.out, args.build, args.avb_public_key_sha256)
    except ContractError as error:
        print(f'factory-package: {error}', file=sys.stderr)
        return 2
    except Refused as refused:
        print(json.dumps(refused.result, indent=2, sort_keys=True, ensure_ascii=False))
        return 3
    print(json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False))
    return 0 if result['factoryReady'] else 3

if __name__ == '__main__':
    sys.exit(main())
