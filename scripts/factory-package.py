#!/usr/bin/env python3
"""FLAVOR001/FLAVOR002: classify an install artifact against an OS flavor contract and package a verified factory archive.

  factory-package.py contract --flavor F
  factory-package.py observe  TARGET_FILES --flavor F --build B --source TEXT --out NEW.json
  factory-package.py classify ARCHIVE --flavor F [evidence...]
  factory-package.py package  ARCHIVE --flavor F --out NEW_DIR [evidence...]
evidence: --target-files TF --target-files-observation OBS.json --firmware-observation FW.json
          --otatools ZIP --otatools-sha256 H [--ssh-keygen PATH] [--work-dir DIR] [--max-work-bytes N]
          [--build B] [--avb-public-key-sha256 H] [--first-use-binding FIRST_USE.json]

F is os/flavors/F.json (frankel or rango), cross-checked against os/devices/pixel10.json and the pinned adevtool
android-info.txt on every load. ARCHIVE is any candidate: an OTA, a target-files, an img zip, or the factory install
archive {device}-install-{build}.zip that generate-release.sh produces with fastboot optimize-factory-image. Only the
last can be factory-ready. Three separate tiers are reported:

structurallyComplete  ZIP members safe, unique, link-free with valid CRCs; android-info.txt and exactly the matching
                      bootloader-/radio- images and the VENDOR/firmware/dauntless/ GSC files the pinned generator copies
                      (device_common 1f5ba267 fixture), byte-equal to the target-files, and no other member; every non-dynamic A/B partition either an IMAGES/ image present
                      byte-equal or listed as carried by the bootloader/radio image in a firmware observation bound to
                      those images' sha256 (never inferred from absence); contiguous sparse super_N.img segments for
                      the dynamic partitions; avb_pkmd.bin equal to the flavor AVB key; flash script declares the wipe.
contentVerified       the super segments rebuilt with simg2img and unpacked with lpunpack, both run from the hash-pinned
                      otatools archive (never from the candidate) in a bounded job-owned directory that is removed on
                      success and failure; the unpacked dynamic partition set and every logical image byte-equal to the
                      signed target-files, which matches the release-delivered names-only observation.
signatureVerified     ARCHIVE.sig verified by ssh-keygen -Y verify against the flavor's pinned factory-signing principal,
                      public key and namespace "factory images" (generate-release.sh). Never an adjacent key; presence
                      alone is nothing. AVB key equality is a separate check.
factoryReady = all three with no missing input or problem. It is still not relock safety, first boot or phone acceptance.
package copies a factory-ready ARCHIVE and ARCHIVE.sig into NEW_DIR with factory-package.json binding the archive,
signed target-files, tool identities and observed inventory. observe writes the names-only target-files observation
(no image bytes) that the release owner delivers through its normal receipt path.
Exit status: 0 ready / packaged / observed / contract valid, 3 refused (the JSON names every missing input and
problem), 2 invalid usage or contract.
"""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat as statmod
import subprocess
import sys
import tempfile
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
from release_receipts import sha256_file  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = 'roseos.factory-package.v2'
OBSERVATION = 'roseos.target-files-observation.v2'
FIRMWARE = 'roseos.factory-firmware-observation.v1'
NAMESPACE = 'factory images'
CHUNK = 1 << 20
MAGIC = {'boot': b'ANDROID!', 'init_boot': b'ANDROID!', 'vendor_boot': b'VNDRBOOT', 'vendor_kernel_boot': b'VNDRBOOT'}
VBMETA_MAGIC = b'AVB0'
SPARSE_MAGIC = b'\x3a\xff\x26\xed'
SUPER = re.compile(r'super_(\d+)\.img')
HEX64 = re.compile(r'[0-9a-f]{64}')
OBSERVED_PREFIXES = ('META/', 'IMAGES/', 'RADIO/', 'OTA/', 'VENDOR/firmware/')
TOOLS = ('bin/simg2img', 'bin/lpunpack')
# Members this script reads from a target-files; a symlink entry there is always refused. Android target-files also carry
# legitimate symlink entries elsewhere (e.g. VENDOR/bin/<tool> -> toybox): those are admitted names-only, never read,
# followed or extracted, and only when their stored link text is small.
TARGET_READ = OBSERVED_PREFIXES + ('SYSTEM/build.prop',)
LINK_TEXT_BYTES = 4095
DEFAULT_MAX_WORK = 64 << 30
TOOL_TIMEOUT = 3600

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
    script_bytes = script.read_bytes()
    checks['releaseScript sha256'] = (hashlib.sha256(script_bytes).hexdigest(),
        flavor['provenance']['releaseScript']['generate-release.sh'])
    signing = flavor['factorySigning']
    checks['factorySigning.namespace'] = (signing['namespace'], NAMESPACE)
    checks['releaseScript namespace'] = (f'ssh-keygen -Y sign -n "{NAMESPACE}"'.encode() in script_bytes, True)
    factory = flavor['artifacts']['factory']
    if pinned_layout(flavor):
        check_generator_layout(name, flavor, script_bytes, root, checks)
    drift = [key for key, (have, want) in checks.items() if have != want]
    if drift:
        raise ContractError(f'{name}: contract disagrees with its sources: {", ".join(drift)}')
    if (signing['principal'] is None) != (signing['publicKey'] is None) or (
            signing['publicKey'] is not None and not (re.fullmatch(r'[\w.@+-]+', signing['principal'])
            and re.fullmatch(r'ssh-ed25519 [A-Za-z0-9+/]+=*', signing['publicKey']))):
        raise ContractError(f'{name}: factorySigning needs both a plain principal and one ssh-ed25519 public key, or neither')
    if not (factory['dataWipe'] and factory['requiresUnlockedBootloader']) or flavor['artifacts']['ota']['dataWipe']:
        raise ContractError(f'{name}: install-mode declarations must be factory wipe+unlock, OTA no wipe')
    check_peer_identities(name, pinned_avb_key(flavor, root), root)
    check_readiness(name, flavor)
    check_historical(name, flavor, root)
    return flavor

def check_peer_identities(name, pinned, root):
    """Every other os/flavors contract is read as raw JSON (never through load_flavor) and its AVB pin resolved strictly.
    A non-null pin equal to this flavor's is signer_collision; null on either side is unknown, never a match. A peer
    whose identity cannot be read or resolved refuses this load: it is never skipped."""
    for path in sorted((root/'os/flavors').glob('*.json')):
        peer = path.stem
        if peer == name:
            continue
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError) as error:
            raise ContractError(f'{name}: peer {peer} contract is unreadable ({type(error).__name__})') from None
        if not isinstance(data, dict) or data.get('flavor') != peer or 'identity' not in data:
            raise ContractError(f'{name}: peer {peer} contract has no identity for flavor {peer}')
        theirs = strict_avb_pin(data['identity'], root, f'{name}: peer {peer}')
        if pinned is not None and theirs == pinned:
            raise ContractError(f'{name}: signer_collision: the pinned AVB key equals the {peer} key')

READINESS_FLAGS = ('buildReady', 'factoryReady', 'phoneValidated')

HISTORICAL_FORBIDDEN = {'buildReady', 'factoryReady', 'phoneValidated', 'ready', 'readiness', 'safe', 'validated'}

def check_historical(name, flavor, root):
    """Optional inventory.historicalObservations: identity facts from an already-authorized past inspection. Each entry
    must say it is historical, name its source and what it does not establish, carry only lowercase sha256 digests, make
    no readiness or safety claim, and any `sha256From` pointer must resolve to the same digest. It is never a names-only
    observation and never changes classify or readiness."""
    entries = flavor['inventory'].get('historicalObservations')
    if entries is None:
        return
    if type(entries) is not list:
        raise ContractError(f'{name}: inventory.historicalObservations must be a list')
    def walk(node, where):
        if isinstance(node, dict):
            for key, value in node.items():
                if key in HISTORICAL_FORBIDDEN:
                    raise ContractError(f'{name}: {where}.{key}: a historical observation makes no readiness or safety claim')
                if key.lower().endswith('sha256') and (type(value) is not str or not HEX64.fullmatch(value)):
                    raise ContractError(f'{name}: {where}.{key} is not a sha256')
                if key == 'sha256From':
                    if type(value) is not str:
                        raise ContractError(f'{name}: {where}.sha256From is not text')
                    resolved = resolve_sha256_pointer(value, root, f'{name}: {where}.sha256From')
                    if resolved is None or resolved != node.get('sha256'):
                        raise ContractError(f'{name}: {where}.sha256From {value!r} does not equal its sha256')
                walk(value, f'{where}.{key}')
        elif isinstance(node, list):
            for index, value in enumerate(node):
                walk(value, f'{where}[{index}]')
    for index, entry in enumerate(entries):
        where = f'historicalObservations[{index}]'
        if type(entry) is not dict or entry.get('historical') is not True or type(entry.get('source')) is not str \
                or not entry['source'].strip():
            raise ContractError(f'{name}: {where} must be an object with historical: true and a source')
        limits = entry.get('doesNotEstablish')
        if type(limits) is not list or not limits or any(type(x) is not str or not x.strip() for x in limits):
            raise ContractError(f'{name}: {where}.doesNotEstablish must list what the observation does not establish')
        walk(entry, where)

def check_readiness(name, flavor):
    """The optional readiness declaration: strict booleans and a list of receipt references. Source cannot carry a
    positive build, factory or phone claim (artifacts are verified by classify/package, phones by QA), and synthetic
    evidence is never a readiness receipt. Contracts without the block are unchanged."""
    if 'readiness' not in flavor:
        return
    readiness = flavor['readiness']
    if not isinstance(readiness, dict):
        raise ContractError(f'{name}: readiness must be an object')
    unknown = sorted(set(readiness) - {*READINESS_FLAGS, 'receipts', 'basis'})
    absent = [key for key in (*READINESS_FLAGS, 'receipts') if key not in readiness]
    if unknown or absent:
        raise ContractError(f'{name}: readiness keys: unknown {unknown}, missing {absent}')
    wrong = [key for key in READINESS_FLAGS if type(readiness[key]) is not bool]
    if wrong:
        raise ContractError(f'{name}: readiness {wrong} must be true or false')
    receipts = readiness['receipts']
    if type(receipts) is not list or any(type(r) is not str or not r.strip() for r in receipts):
        raise ContractError(f'{name}: readiness.receipts must be a list of non-empty receipt references')
    if 'basis' in readiness and type(readiness['basis']) is not str:
        raise ContractError(f'{name}: readiness.basis must be text')
    claimed = [key for key in READINESS_FLAGS if readiness[key]]
    if claimed:
        raise ContractError(f'{name}: unsupported readiness claim {claimed}: a contract never declares a build, factory or '
                            'phone result; artifacts are verified by classify/package and phones by QA')
    if any('synthetic' in r.lower() for r in receipts):
        raise ContractError(f'{name}: readiness.receipts names synthetic evidence, which is never a readiness receipt')

def pinned_layout(flavor):
    """True when the contract anchors its layout in the pinned device_common generator fixture. A contract without it
    (an older shape) still loads, but no factory archive classified under it can be structurally complete."""
    return 'generatorMembers' in flavor['artifacts']['factory']

def check_generator_layout(name, flavor, script_bytes, root, checks):
    factory, generator = flavor['artifacts']['factory'], flavor['inventory']['generatorFixture']
    texts = {}
    for file, digest in generator['files'].items():
        data = (root/generator['fixture']/file).read_bytes()
        checks[f'generatorFixture {file} sha256'] = (hashlib.sha256(data).hexdigest(), digest)
        texts[file] = data.decode()
    members = factory['generatorMembers']
    for anchor in members['anchors']:
        checks[f'generator anchor {anchor!r}'] = (anchor in texts['generate-factory-images-common.sh'], True)
    checks['requiredMembers'] = (sorted(factory['requiredMembers']),
        sorted(members['members'] + factory['optimizeMembers']['members']))
    # GSCFIRMWARESRC: generate-release.sh overrides the generator default only for its epic device list.
    epic = re.search(r'if \[\[ \$DEVICE == @\(([\w|]+)\) \]\]; then\n\s+GSCFIRMWARESRC="(\w+)"', script_bytes.decode())
    default = re.search(r'^\s+GSCFIRMWARESRC=(\w+)$', texts['generate-factory-images-common.sh'], re.M)
    gsc = epic.group(2) if epic and name in epic.group(1).split('|') else (default.group(1) if default else None)
    checks['gscFirmware.source'] = (members['gscFirmware']['source'], f'VENDOR/firmware/{gsc}/')
    if 'optimizeFixture' in flavor['inventory']:
        check_optimize_layout(name, flavor, script_bytes, root, checks)

IMAGE_ROW = re.compile(r'\{\s*"(\w*)",\s*"(\w+)\.img",\s*"\w+\.sig",\s*"(\w+)",\s*(true|false),\s*ImageType::(\w+)\s*\}')

def check_optimize_layout(name, flavor, script_bytes, root, checks):
    """The install-archive rules come from the pinned fastboot optimize-factory-image excerpt, never from memory."""
    factory, pin = flavor['artifacts']['factory'], flavor['inventory']['optimizeFixture']
    raw = (root/pin['excerpt']).read_bytes()
    checks['optimizeFixture excerpt sha256'] = (hashlib.sha256(raw).hexdigest(), pin['excerptSha256'])
    provenance = json.loads((root/pin['provenance']).read_text())
    checks['optimizeFixture revision'] = (provenance['revision'], pin['revision'])
    checks['optimizeFixture files'] = ({e['path']: e['sha256'] for e in provenance['files'] if 'excerpt' in e}, pin['files'])
    files = json.loads(raw)['files']
    text = '\n'.join(line for lines in files.values() for line in lines.values())
    for anchor in factory['optimizeMembers']['anchors']:
        checks[f'optimize anchor {anchor!r}'] = (anchor in text, True)
    table = ' '.join(line for number, line in files['fastboot/fastboot.cpp'].items() if 117 <= int(number) <= 174)
    flashed = sorted({part for nick, _, part, _, kind in IMAGE_ROW.findall(table) if nick and kind in ('BootCritical', 'Normal')})
    checks['fastbootImages'] = (factory['fastbootImages'], flashed)
    sizes = re.search(r'elif \[\[ \$DEVICE == @\(([\w|]+)\) \]\]; then\n\s+MAX_DOWNLOAD_SIZE=(0x[0-9a-f]+)', script_bytes.decode())
    checks['scriptChecks.maxDownloadSize'] = (factory['scriptChecks']['maxDownloadSize'],
        sizes.group(2) if sizes and name in sizes.group(1).split('|') else None)

def pinned_avb_key(flavor, root=ROOT):
    """A literal pin with its provenance, or a pointer 'FILE dotted.field' read from a checked-in source; None is unknown."""
    return strict_avb_pin(flavor['identity'], root, str(flavor.get('flavor')))

def strict_avb_pin(identity, root, label):
    """null (absent) is unknown; anything else must be, or resolve to, one lowercase sha256, or the contract refuses."""
    if not isinstance(identity, dict):
        raise ContractError(f'{label}: identity is not an object')
    literal = identity.get('avbPublicKeySha256')
    if literal is not None:
        if type(literal) is not str or not HEX64.fullmatch(literal):
            raise ContractError(f'{label}: avbPublicKeySha256 is not a sha256')
        return literal
    source = identity.get('avbPublicKeySha256From')
    if source is not None and type(source) is not str:
        raise ContractError(f'{label}: avbPublicKeySha256From is not text')
    return resolve_sha256_pointer(source, root, f'{label}: avbPublicKeySha256From')

def resolve_sha256_pointer(source, root, label):
    """'FILE dotted.field' in a checked-in JSON source naming one lowercase sha256; anything else that is not such a
    pointer is None, and a pointer that does not resolve to a sha256 refuses."""
    match = re.fullmatch(r'(os/[\w./-]+\.json) ([\w.]+)', source or '')
    if not match:
        return None
    try:
        value = json.loads((root/match.group(1)).read_text())
        for key in match.group(2).split('.'):
            value = value[key]
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise ContractError(f'{label} {source!r} does not resolve ({type(error).__name__})') from None
    if type(value) is not str or not HEX64.fullmatch(value):
        raise ContractError(f'{label} {source!r} is not a sha256')
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

def open_checked(path, label, result, names_only_links=False):
    """Open a ZIP whose names are safe and unique and whose CRCs check; problems go to result. Link entries are refused,
    except, with names_only_links (target-files only), small ones outside TARGET_READ, which are counted, never used."""
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
            if names_only_links and not name.startswith(TARGET_READ) and not name.endswith('/') \
                    and 0 < info.file_size <= LINK_TEXT_BYTES:
                result['linkEntries'] = result.get('linkEntries', 0) + 1
                continue
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
    if len(tops) == 1 and all('/' in n for n in names) and re.fullmatch(r'[a-z0-9]+-factory-\d+', next(iter(tops))):
        return 'factory-intermediate'
    if 'android-info.txt' in names and any(n.endswith('.img') and '/' not in n for n in names):
        return 'img-zip'
    return 'unknown'

def read_json(path, label, result):
    try:
        path = Path(path)
        if path.is_symlink() or not path.is_file():
            raise OSError
        return json.loads(path.read_text())
    except (OSError, ValueError):
        result['missing'].append(f'{label}:{Path(path).name} (unreadable or not JSON)')
        return None

def misc_info(archive):
    return dict(line.split('=', 1) for line in archive.read('META/misc_info.txt').decode().splitlines() if '=' in line)

def observe(target_files, flavor_name, build, source, root=ROOT):
    """Names-only observation of a signed target-files: its digest, member names under META/ IMAGES/ RADIO/ OTA/ and
    the partition metadata the classifier reads. No image bytes leave the archive."""
    flavor = load_flavor(flavor_name, root)
    result = {'problems': [], 'missing': []}
    archive = open_checked(target_files, 'target-files', result, names_only_links=True)
    if archive is None:
        raise ContractError('target-files refused: ' + ', '.join(result['problems']))
    with archive:
        names = archive.namelist()
        if 'META/misc_info.txt' not in names or 'META/ab_partitions.txt' not in names:
            raise ContractError('not a target-files: META/misc_info.txt and META/ab_partitions.txt are required')
        misc = misc_info(archive)
        ab = archive.read('META/ab_partitions.txt').decode().split()
    return {'schema': OBSERVATION, 'device': flavor['device']['codename'], 'build': build, 'source': source,
            'synthetic': False, 'targetFiles': {'file': Path(target_files).name, 'sha256': sha256_file(target_files),
            'size': Path(target_files).stat().st_size},
            'members': sorted(n for n in names if n.startswith(OBSERVED_PREFIXES) and not n.endswith('/')),
            'abPartitions': ab, 'misc': {k: misc.get(k) for k in ('ab_update', 'use_dynamic_partitions',
            'dynamic_partition_list', 'virtual_ab', 'virtual_ab_compression')}}

def check_observation(obs, tf_path, names, flavor, build, result, allow_synthetic):
    if obs is None:
        result['missing'].append(f'target-files-observation (release: `factory-package.py observe` of the signed '
                                 f'{flavor["device"]["codename"]} target-files through its normal receipt path)')
        return False
    ok = True
    def bad(code):
        nonlocal ok
        result['problems'].append(code)
        ok = False
    if obs.get('schema') != OBSERVATION or obs.get('device') != flavor['device']['codename']:
        bad('observation-mismatch:schema or device')
    if build and obs.get('build') != build:
        bad(f'observation-mismatch:build {obs.get("build")}')
    if obs.get('synthetic') and not allow_synthetic:
        bad('target-files-observation-synthetic:a synthetic observation is never release evidence')
    if (obs.get('targetFiles') or {}).get('sha256') != result['inventory'].get('targetFilesSha256'):
        bad('observation-mismatch:target-files sha256')
    if obs.get('members') != sorted(n for n in names if n.startswith(OBSERVED_PREFIXES) and not n.endswith('/')):
        bad('observation-mismatch:member names')
    result['evidence']['targetFilesObservation'] = {'source': obs.get('source'), 'synthetic': bool(obs.get('synthetic'))}
    return ok

def target_inventory(path, flavor, build, result, observation, allow_synthetic):
    archive = open_checked(path, 'target-files', result, names_only_links=True)
    if archive is None:
        return None
    result['inventory']['targetFilesSha256'] = sha256_file(path)
    with archive:
        names = set(archive.namelist())
        need = [n for n in ('META/ab_partitions.txt', 'META/misc_info.txt', 'OTA/android-info.txt') if n not in names]
        result['missing'] += [f'target-files:{n}' for n in need]
        if need:
            return None
        check_observation(observation, path, archive.namelist(), flavor, build, result, allow_synthetic)
        misc = misc_info(archive)
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
                     'images': {}, 'withoutImage': [], 'firmware': {}, 'gsc': {}}
        for part in sorted(set(ab) - set(dynamic)):
            if f'IMAGES/{part}.img' in names:
                inventory['images'][part] = member_digest(archive, f'IMAGES/{part}.img')[0]
            else:
                inventory['withoutImage'].append(part)
        for part in inventory['dynamic']:
            if f'IMAGES/{part}.img' not in names:
                result['missing'].append(f'target-files:IMAGES/{part}.img (dynamic)')
        for kind in ('bootloader', 'radio'):
            if f'RADIO/{kind}.img' in names:
                inventory['firmware'][kind] = member_digest(archive, f'RADIO/{kind}.img')[0]
            else:
                result['missing'].append(f'target-files:RADIO/{kind}.img')
        if not pinned_layout(flavor):
            return inventory
        # The generator copies VENDOR/firmware/$GSCFIRMWARESRC/* (cp -r) into the archive top directory.
        source = flavor['artifacts']['factory']['generatorMembers']['gscFirmware']['source']
        gsc = sorted(n for n in names if n.startswith(source) and not n.endswith('/'))
        if not gsc:
            result['missing'].append(f'target-files:{source}* (GSC firmware the generator copies)')
        for name in gsc:
            rel = name[len(source):]
            if '/' in rel:
                result['problems'].append(f'gsc-firmware-nested:{name} (nested GSC firmware is not modelled)')
            else:
                inventory['gsc'][rel] = member_digest(archive, name)[0]
        return inventory

def check_firmware_carried(fw, needed, factory_firmware, flavor, build, result, allow_synthetic):
    """A/B partitions without an IMAGES/ entry, or with one that fastboot flashall never flashes, count as carried only
    when a firmware observation, bound to the exact bootloader/radio image digests in this archive, lists them. Absence
    alone never qualifies."""
    if not needed:
        return
    if fw is None:
        result['missing'].append('firmware-observation for A/B partitions without an install image: '
                                 + ' '.join(needed) + ' (signing: factory generator firmware inventory)')
        return
    if fw.get('schema') != FIRMWARE or fw.get('device') != flavor['device']['codename'] or (build and fw.get('build') != build):
        result['problems'].append('firmware-observation-mismatch:schema, device or build')
        return
    if fw.get('synthetic') and not allow_synthetic:
        result['problems'].append('firmware-observation-synthetic:a synthetic observation is never release evidence')
        return
    carried = {}
    for kind in ('bootloader', 'radio'):
        entry = fw.get(kind) or {}
        if entry.get('sha256') != factory_firmware.get(kind):
            result['problems'].append(f'firmware-observation-mismatch:{kind} sha256')
            return
        for part in entry.get('partitions') or []:
            carried.setdefault(part, []).append(kind)
    uncovered = [p for p in needed if p not in carried]
    twice = [p for p in needed if len(carried.get(p, [])) > 1]
    if uncovered:
        result['missing'].append('firmware-carried evidence for ' + ' '.join(uncovered))
    if twice:
        result['problems'].append('firmware-observation-mismatch:partition in both images ' + ' '.join(twice))
    result['inventory']['firmwareCarried'] = {p: carried[p][0] for p in needed if p in carried}
    result['evidence']['firmwareObservation'] = {'source': fw.get('source'), 'synthetic': bool(fw.get('synthetic'))}

def check_factory(archive, names, flavor, build, inventory, avb_key, fw, result, allow_synthetic):
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
    if not pinned_layout(flavor):
        result['missing'].append(f'contract:{flavor["flavor"]} pinned generator layout (artifacts.factory.generatorMembers '
                                 'anchored in the device_common 1f5ba267 fixture)')
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
    gsc = inventory['gsc'] if inventory else {}
    images = sorted(rel for rel in files if rel.endswith('.img') and rel not in firmware.values() and rel not in gsc)
    for rel in images:
        if head(archive, files[rel], 1) == b'':
            result['problems'].append(f'corrupt-image:{rel} empty')
    supers = sorted((int(SUPER.fullmatch(rel).group(1)), files[rel]) for rel in images if SUPER.fullmatch(rel))
    result['superMembers'] = [name for _, name in supers]
    if inventory is None:
        result['missing'].append('target-files inventory (--target-files: the signed target-files this archive came from)')
        return
    factory_firmware = {kind: member_digest(archive, files[rel])[0] for kind, rel in firmware.items() if rel in files}
    evidenced = 'fastbootImages' in factory
    # fastboot flashall writes only its images[] table partitions; other non-dynamic images need firmware evidence.
    install = {p: d for p, d in inventory['images'].items() if not evidenced or p in factory['fastbootImages']}
    not_flashed = sorted(set(inventory['images']) - set(install))
    expected = {f'{part}.img' for part in install}
    for rel in images:
        if rel not in expected and not SUPER.fullmatch(rel):
            result['problems'].append(f'unexpected-image:{rel}')
    sparse_members = {}
    for part, digest in install.items():
        rel = f'{part}.img'
        if rel not in files:
            result['missing'].append(f'factory:{rel}')
            continue
        magic = MAGIC.get(part, VBMETA_MAGIC if part.startswith('vbmeta') else None)
        if evidenced and head(archive, files[rel], 4) == SPARSE_MAGIC:
            sparse_members[part] = files[rel]  # resparsed by fastboot above the limit: compared flattened in contentVerified
        elif magic and head(archive, files[rel], len(magic)) != magic:
            result['problems'].append(f'corrupt-image:{rel} header')
        elif member_digest(archive, files[rel])[0] != digest:
            result['problems'].append(f'image-mismatch:{rel} differs from target-files IMAGES/{rel}')
    for kind, rel in firmware.items():
        if kind in factory_firmware and kind in inventory['firmware'] and factory_firmware[kind] != inventory['firmware'][kind]:
            result['problems'].append(f'mixed-firmware:{rel} differs from target-files RADIO/{kind}.img')
    for rel, digest in gsc.items():
        if rel not in files:
            result['missing'].append(f'factory:{rel} (GSC firmware from target-files)')
        elif member_digest(archive, files[rel])[0] != digest:
            source = factory['generatorMembers']['gscFirmware']['source']
            result['problems'].append(f'mixed-firmware:{rel} differs from target-files {source}{rel}')
    if pinned_layout(flavor):
        allowed = set(required) | set(factory['generatorMembers']['optional']) | set(images) | set(gsc)
        for rel in sorted(files):
            if '/' not in rel and rel not in allowed:
                result['problems'].append(f'unexpected-member:{rel}')
    check_firmware_carried(fw, inventory['withoutImage'] + not_flashed, factory_firmware, flavor, build, result, allow_synthetic)
    result['sparseMembers'] = sparse_members
    if evidenced:
        check_install_scripts(archive, files, flavor, build, supers, result)
    if inventory['dynamic']:
        if not supers:
            result['missing'].append(f'factory:super_1.img.. (dynamic partitions {" ".join(inventory["dynamic"])})')
        elif [n for n, _ in supers] != list(range(1, len(supers) + 1)):
            result['problems'].append(f'corrupt-image:super segments not contiguous {[n for n, _ in supers]}')
        for _, name in supers:
            if head(archive, name, 4) != SPARSE_MAGIC:
                result['problems'].append(f'corrupt-image:{PurePosixPath(name).name} not an Android sparse image')
    elif supers:
        result['problems'].append('unexpected-image:super segments without dynamic partitions')
    result['inventory'].update({'abPartitions': inventory['abPartitions'], 'dynamic': inventory['dynamic'],
        'virtualAb': inventory['virtualAb'], 'withoutImage': inventory['withoutImage'], 'notFlashedByFastboot': not_flashed,
        'images': sorted(expected), 'sparseImages': sorted(sparse_members), 'gscFirmware': sorted(gsc), 'superSegments': len(supers)})

def check_install_scripts(archive, files, flavor, build, supers, result):
    """The FlashCapturer output (pinned optimize-factory-image excerpt): android-info.zip holds exactly the
    android-info.txt, and script.txt carries the product/slot/max-download-size checks, the two other-slot bootloader
    flashes, check-requirements and one `flash super super_N.img` per segment, and names only archive members."""
    factory, device = flavor['artifacts']['factory'], flavor['device']['codename']
    checks = factory['scriptChecks']
    fields = dict(device=device, build=build, maxDownloadSize=checks['maxDownloadSize'],
                  **{k: flavor['firmware'][k] for k in ('bootloader', 'baseband')})
    if 'android-info.zip' in files and 'android-info.txt' in files:
        try:
            with zipfile.ZipFile(io.BytesIO(archive.read(files['android-info.zip']))) as inner:
                same = inner.namelist() == ['android-info.txt'] and \
                    inner.read('android-info.txt') == archive.read(files['android-info.txt'])
        except (zipfile.BadZipFile, OSError, KeyError):
            same = False
        if not same:
            result['problems'].append('install-script:android-info.zip is not exactly the archive android-info.txt')
    if 'script.txt' not in files:
        return
    lines = [line.strip() for line in archive.read(files['script.txt']).decode(errors='replace').splitlines()]
    commands = [line for line in lines if line and not line.startswith('#')]
    for template in checks['required']:
        if template.format(**fields) not in commands:
            result['problems'].append(f'install-script:script.txt lacks `{template.format(**fields)}`')
    bootloader = checks['bootloaderOtherSlot'].format(**fields)
    if commands.count(bootloader) != checks['bootloaderOtherSlotCount']:
        result['problems'].append(f'install-script:script.txt has {commands.count(bootloader)} `{bootloader}`, expected '
                                  f'{checks["bootloaderOtherSlotCount"]}')
    flashed_super = [line for line in commands if line.startswith('flash super ')]
    wanted = [f'flash super super_{index}.img' for index in range(1, len(supers) + 1)]
    if flashed_super != wanted:
        result['problems'].append(f'install-script:script.txt super flashes {flashed_super} do not match segments {wanted}')
    for line in commands:
        words = line.split()
        if words[0] == 'flash' and len(words) in (3, 4) and words[2] not in files:
            result['problems'].append(f'install-script:script.txt flashes absent member {words[2]}')

def copy_member(archive, name, target, budget):
    with archive.open(name) as source, open(target, 'xb') as out:
        for block in iter(lambda: source.read(CHUNK), b''):
            budget.spend(len(block))
            out.write(block)

class Budget:
    def __init__(self, limit):
        self.limit, self.used = limit, 0
    def spend(self, count):
        self.used += count
        if self.used > self.limit:
            raise OverflowError(f'job directory would exceed --max-work-bytes {self.limit}')

def run_tool(argv, env, label, result):
    try:
        done = subprocess.run(argv, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                              stderr=subprocess.PIPE, timeout=TOOL_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired) as error:
        result['problems'].append(f'content-tool-failed:{label} {type(error).__name__}')
        return False
    if done.returncode:
        result['problems'].append(f'content-tool-failed:{label} exit {done.returncode}')
        return False
    return True

def flat_copy(archive, member, base, tool_dir, env, budget, result, label):
    """Copy one member into the job directory, flattened with the pinned simg2img when it is Android sparse."""
    copied = base.with_suffix('.img')
    copy_member(archive, member, copied, budget)
    with open(copied, 'rb') as f:
        if f.read(4) != SPARSE_MAGIC:
            return copied
    flat = base.with_suffix('.raw')
    if not run_tool([str(tool_dir/'bin/simg2img'), str(copied), str(flat)], env, label, result):
        return None
    budget.spend(flat.stat().st_size)
    copied.unlink()
    return flat

def verify_content(archive_path, target_files, inventory_dynamic, super_members, otatools, otatools_sha256, work_dir,
                   max_work_bytes, result, sparse_members=None):
    """Rebuild super from its sparse segments with the pinned otatools simg2img, unpack it with lpunpack and compare
    each logical partition with the signed target-files image. Every byte lives in one job-owned directory that is
    removed on success and failure."""
    sparse_members = sparse_members or {}
    if not inventory_dynamic and not sparse_members:
        result['content'] = {'dynamic': {}, 'note': 'no dynamic partitions or sparse images'}
        return True
    if not otatools or not otatools_sha256:
        result['missing'].append('otatools (--otatools ZIP --otatools-sha256 H: the pinned otatools-package of this build)')
        return False
    if sha256_file(otatools) != otatools_sha256:
        result['problems'].append('tool-identity-mismatch:otatools sha256')
        return False
    tools = {'otatools': {'file': Path(otatools).name, 'sha256': otatools_sha256}}
    budget = Budget(max_work_bytes)
    job = Path(tempfile.mkdtemp(prefix='factory-package-', dir=work_dir))
    try:
        with zipfile.ZipFile(otatools) as zt:
            names = set(zt.namelist())
            absent = [t for t in TOOLS if t not in names]
            if absent:
                result['missing'] += [f'otatools:{t}' for t in absent]
                return False
            tool_dir = job/'otatools'
            for name in list(TOOLS) + sorted(n for n in names if n.startswith('lib64/') and not n.endswith('/')):
                if '..' in PurePosixPath(name).parts or name.startswith('/'):
                    result['problems'].append(f'unsafe-path:otatools:{name}')
                    return False
                (tool_dir/name).parent.mkdir(parents=True, exist_ok=True)
                copy_member(zt, name, tool_dir/name, budget)
            for name in TOOLS:
                os.chmod(tool_dir/name, 0o700)
                tools[name.split('/')[1]] = {'member': name, 'sha256': sha256_file(tool_dir/name)}
        env = {'PATH': '/usr/bin:/bin', 'LD_LIBRARY_PATH': str(tool_dir/'lib64'), 'LC_ALL': 'C'}
        observed, ok = {}, True
        if inventory_dynamic:
            segments = []
            with zipfile.ZipFile(archive_path) as za:
                for index, name in enumerate(super_members, 1):
                    segments.append(job/f'super_{index}.img')
                    copy_member(za, name, segments[-1], budget)
            raw = job/'super.raw'
            if not run_tool([str(tool_dir/'bin/simg2img'), *map(str, segments), str(raw)], env, 'simg2img super', result):
                return False
            for segment in segments:
                segment.unlink()
            budget.spend(raw.stat().st_size)
            parts = job/'partitions'
            parts.mkdir()
            if not run_tool([str(tool_dir/'bin/lpunpack'), str(raw), str(parts)], env, 'lpunpack super', result):
                return False
            raw.unlink()
            unpacked = {}
            for item in sorted(parts.iterdir()):
                match = re.fullmatch(r'([a-z0-9_]+?)(_[ab])?\.img', item.name)
                if not match or item.is_symlink() or not item.is_file():
                    result['problems'].append(f'dynamic-unexpected:{item.name}')
                    ok = False
                    continue
                budget.spend(item.stat().st_size)
                if match.group(2) == '_b':
                    if item.stat().st_size:
                        result['problems'].append(f'mixed-partition:{item.name} is not empty in a factory super')
                        ok = False
                    continue
                if match.group(1) in unpacked:
                    result['problems'].append(f'duplicate-partition:super {match.group(1)}')
                    ok = False
                unpacked[match.group(1)] = item
            for name in sorted(set(inventory_dynamic) - set(unpacked)):
                result['problems'].append(f'dynamic-missing:{name}')
                ok = False
            for name in sorted(set(unpacked) - set(inventory_dynamic)):
                result['problems'].append(f'dynamic-extra:{name}')
                ok = False
            with zipfile.ZipFile(target_files) as zf:
                for name in sorted(set(inventory_dynamic) & set(unpacked)):
                    expected = job/f'expected-{name}.img'
                    copy_member(zf, f'IMAGES/{name}.img', expected, budget)
                    with open(expected, 'rb') as f:
                        sparse = f.read(4) == SPARSE_MAGIC
                    if sparse:
                        flat = job/f'expected-{name}.raw'
                        if not run_tool([str(tool_dir/'bin/simg2img'), str(expected), str(flat)], env, f'simg2img IMAGES/{name}.img', result):
                            return False
                        budget.spend(flat.stat().st_size)
                        expected.unlink()
                        expected = flat
                    actual = {'sha256': sha256_file(unpacked[name]), 'size': unpacked[name].stat().st_size}
                    want = {'sha256': sha256_file(expected), 'size': expected.stat().st_size}
                    expected.unlink()
                    observed[name] = actual
                    if actual != want:
                        result['problems'].append(f'image-mismatch:dynamic {name} differs from target-files IMAGES/{name}.img')
                        ok = False
        sparse = {}
        with zipfile.ZipFile(archive_path) as za, zipfile.ZipFile(target_files) as zf:
            # A non-logical image fastboot wrote as Android sparse (larger than the limit) is compared flattened.
            for part, member in sorted(sparse_members.items()):
                got = flat_copy(za, member, job/f'install-{part}', tool_dir, env, budget, result, f'simg2img {PurePosixPath(member).name}')
                want = got and flat_copy(zf, f'IMAGES/{part}.img', job/f'expected-{part}', tool_dir, env, budget, result,
                                         f'simg2img IMAGES/{part}.img')
                if not (got and want):
                    return False
                sparse[part] = {'sha256': sha256_file(got), 'size': got.stat().st_size}
                if sparse[part] != {'sha256': sha256_file(want), 'size': want.stat().st_size}:
                    result['problems'].append(f'image-mismatch:{part}.img (sparse) differs from target-files IMAGES/{part}.img')
                    ok = False
                got.unlink()
                want.unlink()
        result['content'] = {'dynamic': observed, 'sparseImages': sparse, 'tools': tools, 'workBytes': budget.used}
        return ok
    except OverflowError as error:
        result['problems'].append(f'work-space:{error}')
        return False
    except (OSError, zipfile.BadZipFile, KeyError) as error:
        result['problems'].append(f'content-check-failed:{type(error).__name__}')
        return False
    finally:
        shutil.rmtree(job, ignore_errors=True)

def verify_signature(path, flavor, ssh_keygen, work_dir, result):
    """ssh-keygen -Y verify against the contract's pinned principal and key, namespace "factory images"."""
    signing, device = flavor['factorySigning'], flavor['device']['codename']
    sig = Path(str(path) + '.sig')
    result['signature'] = {'file': sig.name, 'namespace': NAMESPACE, 'principal': signing['principal']}
    if signing['publicKey'] is None:
        result['missing'].append(f'factory-signing identity: pinned principal and ssh-ed25519 public key for {device}, '
                                 f'namespace "{NAMESPACE}" ({signing["owner"]}; never generated here)')
        return False
    if sig.is_symlink() or not sig.is_file():
        result['missing'].append(f'factory:{sig.name} (detached signature)')
        return False
    tool = ssh_keygen or shutil.which('ssh-keygen')
    if not tool:
        result['missing'].append('ssh-keygen (OpenSSH verifier)')
        return False
    result['signature']['verifier'] = {'path': str(tool), 'sha256': sha256_file(tool)}
    with tempfile.TemporaryDirectory(prefix='factory-signers-', dir=work_dir) as job:
        signers = Path(job)/'allowed_signers'
        signers.write_text(f'{signing["principal"]} namespaces="{NAMESPACE}" {signing["publicKey"]}\n')
        with open(path, 'rb') as stream:
            try:
                done = subprocess.run([str(tool), '-Y', 'verify', '-f', str(signers), '-I', signing['principal'],
                                       '-n', NAMESPACE, '-s', str(sig)], stdin=stream, capture_output=True, timeout=600)
            except (OSError, subprocess.TimeoutExpired) as error:
                result['problems'].append(f'signature-invalid:verifier {type(error).__name__}')
                return False
    if done.returncode:
        result['problems'].append('signature-invalid:ssh-keygen -Y verify refused the detached signature')
        return False
    result['signature']['sha256'] = sha256_file(sig)
    return True

def resolve_avb_key(flavor, avb_key, first_use_binding, root, result):
    pinned = pinned_avb_key(flavor, root)
    if pinned and avb_key and avb_key != pinned:
        raise ContractError(f'--avb-public-key-sha256 differs from the pinned {flavor["identity"]["avbPublicKeySha256From"]}')
    if first_use_binding:
        binding = read_json(first_use_binding, 'first-use-binding', result) or {}
        value = binding.get('avbPublicKeySha256')
        if not HEX64.fullmatch(str(value)):
            raise ContractError('first-use binding has no avbPublicKeySha256')
        if (pinned or avb_key) and value != (pinned or avb_key):
            raise ContractError('identity_conflict: the signer first-use binding differs from the pinned published AVB key')
        avb_key = avb_key or value
    return pinned or avb_key

def classify(path, flavor_name, target_files=None, build=None, avb_key=None, root=ROOT, *, target_files_observation=None,
             firmware_observation=None, otatools=None, otatools_sha256=None, ssh_keygen=None, first_use_binding=None,
             work_dir=None, max_work_bytes=DEFAULT_MAX_WORK, allow_synthetic=False):
    flavor = load_flavor(flavor_name, root)
    device = flavor['device']['codename']
    path = Path(path)
    result = {'schema': SCHEMA, 'flavor': flavor_name, 'device': device, 'artifact': path.name, 'build': build,
              'role': 'unknown', 'structurallyComplete': False, 'contentVerified': False, 'signatureVerified': False,
              'factoryReady': False, 'otaApplicable': False, 'missing': [], 'problems': [], 'inventory': {}, 'evidence': {},
              'installMode': None, 'limits': {'phoneValidated': False, 'relockSafe': 'not claimed', 'firstBoot': 'not claimed'}}
    avb_key = resolve_avb_key(flavor, avb_key, first_use_binding, root, result)
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
            return result
        if role == 'factory-intermediate':
            result['problems'].append('not-factory:factory-intermediate is the generator output {device}-factory-{build}.zip; '
                                      'only the optimize-factory-image install archive is signed and shipped')
            return result
        if role != 'factory-install':
            result['problems'].append(f'not-factory:{role} is not a {flavor["artifacts"]["factory"]["file"]} install archive')
            return result
        obs = read_json(target_files_observation, 'target-files-observation', result) if target_files_observation else None
        fw = read_json(firmware_observation, 'firmware-observation', result) if firmware_observation else None
        inventory = target_inventory(Path(target_files), flavor, build, result, obs, allow_synthetic) if target_files else None
        result['installMode'] = flavor['artifacts']['factory']
        check_factory(archive, names, flavor, build, inventory, avb_key, fw, result, allow_synthetic)
    result['structurallyComplete'] = not result['missing'] and not result['problems']
    if result['structurallyComplete']:
        result['contentVerified'] = verify_content(path, target_files, inventory['dynamic'], result['superMembers'], otatools,
            otatools_sha256, work_dir, max_work_bytes, result, result.pop('sparseMembers', {}))
    result['signatureVerified'] = verify_signature(path, flavor, ssh_keygen, work_dir, result)
    result['factoryReady'] = all(result[k] for k in ('structurallyComplete', 'contentVerified', 'signatureVerified')) \
        and not result['missing'] and not result['problems']
    return result

def package(path, flavor_name, target_files, out, build=None, avb_key=None, root=ROOT, **evidence):
    result = classify(path, flavor_name, target_files, build, avb_key, root, **evidence)
    if not result['factoryReady']:
        raise Refused(result)
    path, out = Path(path), Path(out)
    out.mkdir(parents=True, exist_ok=False)
    copied = {}
    for source in (path, path.with_name(path.name + '.sig')):
        shutil.copyfile(source, out/source.name)
        copied[source.name] = sha256_file(out/source.name)
    if copied[path.name] != result['sha256'] or copied[path.name + '.sig'] != result['signature']['sha256']:
        shutil.rmtree(out)
        raise Refused(dict(result, factoryReady=False, problems=['identity-mismatch:archive or signature changed while packaging']))
    receipt = dict(result, files=copied)
    (out/'factory-package.json').write_text(json.dumps(receipt, indent=2, sort_keys=True) + '\n')
    return receipt

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('contract').add_argument('--flavor', required=True)
    o = sub.add_parser('observe')
    o.add_argument('target_files')
    for flag in ('--flavor', '--build', '--source', '--out'):
        o.add_argument(flag, required=True)
    for name in ('classify', 'package'):
        p = sub.add_parser(name)
        p.add_argument('archive')
        p.add_argument('--flavor', required=True)
        p.add_argument('--target-files', required=name == 'package')
        for flag in ('--build', '--avb-public-key-sha256', '--target-files-observation', '--firmware-observation',
                     '--otatools', '--otatools-sha256', '--ssh-keygen', '--first-use-binding', '--work-dir'):
            p.add_argument(flag)
        p.add_argument('--max-work-bytes', type=int, default=DEFAULT_MAX_WORK)
        if name == 'package':
            p.add_argument('--out', required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == 'contract':
            print(json.dumps(load_flavor(args.flavor), indent=2, ensure_ascii=False))
            return 0
        if args.build and not re.fullmatch(r'\d{10}', args.build):
            raise ContractError('--build must be a ten-digit build number')
        if args.command == 'observe':
            if Path(args.out).exists():
                raise ContractError(f'--out {args.out} already exists')
            document = observe(args.target_files, args.flavor, args.build, args.source)
            with open(args.out, 'x') as f:
                f.write(json.dumps(document, indent=2, sort_keys=True) + '\n')
            print(json.dumps({'observed': args.out, 'members': len(document['members'])}))
            return 0
        for flag in ('avb_public_key_sha256', 'otatools_sha256'):
            if getattr(args, flag) and not HEX64.fullmatch(getattr(args, flag)):
                raise ContractError(f'--{flag.replace("_", "-")} must be 64 lowercase hex digits')
        evidence = dict(target_files_observation=args.target_files_observation, firmware_observation=args.firmware_observation,
            otatools=args.otatools, otatools_sha256=args.otatools_sha256, ssh_keygen=args.ssh_keygen,
            first_use_binding=args.first_use_binding, work_dir=args.work_dir, max_work_bytes=args.max_work_bytes)
        if args.command == 'classify':
            result = classify(args.archive, args.flavor, args.target_files, args.build, args.avb_public_key_sha256, **evidence)
        else:
            if Path(args.out).exists():
                raise ContractError(f'--out {args.out} already exists')
            result = package(args.archive, args.flavor, args.target_files, args.out, args.build, args.avb_public_key_sha256,
                             **evidence)
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
