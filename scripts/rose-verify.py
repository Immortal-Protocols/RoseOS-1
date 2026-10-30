#!/usr/bin/env python3
"""Repeatable RoseOS build verification: artifact/source inspection, then handback-gated runtime capture.

Artifact and source inspection never touch a device. Runtime capture talks to one already-booted device over adb and
only with an explicit, recorded host handback (docs/VERIFY.md). Every result names its kind: artifact-inspection,
source-inspection, runtime-readonly, runtime-interactive or screenshot-comparison. One kind never stands in for another.

Identity checks compare against one explicit candidate expectation (--expect FILE, schema below), never an implicit
default: a build is checked against exactly the identities Infra recorded for it. Runtime observations are bound to
one recorded tested user (--user, default the current foreground user, resolved and recorded).

  expected  print the repository's pinned identities and the Fold prototype reference; --expect FILE validates and
            prints an expectation
  release   DIR: a downloaded pixel-ota release directory. Checks SHA256SUMS for every file present,
            verification.json, the feed line and the AVB key pin, and reads OTA metadata. --extract-apps DIR (needs
            --expect) also extracts the expected apps' partitions from payload.bin (every operation and partition
            hash checked), copies those APKs out of the ext4/erofs images and inspects them like `apk`
  apk       APK...: sha256, package/version/SDK/ABI, launchable and HOME activities, signer, Fold wallpaper resource
            and Home/conversation classes; with --expect, also bytes/version/signer against the expectation
  source    [REV]: the manifest and Home routing of a source revision (git show; no build)
  runtime   --serial S --handback REF --expect FILE --out DIR [--user ID]: read-only properties, the tested user's
            installed app paths/state/bytes/versions, HOME role holders and unqualified MAIN/HOME resolution.
            --interactive (tested user must be the foreground user) also drives the system HOME key, a labeled
            explicit Rose HOME launch, Rose -> conversation -> Back, the launcher-icon entry and HOME again, with
            screenshots, UI dumps and (where Pillow is installed) a comparison of each Home screenshot
  compare   SCREENSHOT --out DIR: a screenshot against the reference wallpaper (cover crop), black-screen detection
            and a side-by-side image for human review
Exit status: 0 ok, 1 check failed, 2 invalid input, 4 setup missing.

Expectation schema (schemaVersion 1; unknown keys, nulls and malformed values are rejected):
  {"schemaVersion": 1, "kind": "rose-verify-expectation", "label": str, "channel": "alpha"|"beta"|"app",
   "candidate": 40-hex source SHA, "build": digits (optional), "target": {str: str} (device/lunch/...),
   "apps": [{"package": str, "version": int >= 1, "sha256": 64-hex, "imagePath": "partition/path.apk" (needed by --extract-apps)}],
   "appSignerSha256": 64-hex, "avbPublicKeySha256": 64-hex (optional), "basis": str (where each value came from)}
"""
import argparse
import bz2
import hashlib
import json
import lzma
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
import zipfile

ROOT = Path(__file__).resolve().parents[1]
METHOD = ('rose-verify r3 (INFRA-VERIFY-003): explicit candidate expectation; tested-user binding; '
          'labeled explicit Rose HOME step; comparison optional on the capturing host')
CONTRACT = ROOT/'os/alpha/cuttlefish-product-contract.json'
PIPELINE = ROOT/'os/release-pipeline/frankel.json'
WALLPAPER = 'workspace/rose-agent/app/res/drawable-nodpi/rose_home_wallpaper.jpg'
REFERENCE = {
    'prototype': 'Rose-HiFi.html at ce126f56c8967008b0a5db889137f224ac9ad36c/prototypes/rose-hifi '
                 '(built HTML SHA-256 56a18b41e98747079039e50ca6a9fbdebc062cfe19ca47ebd26bce4de72c0b76; '
                 'HIFI-OCT29-001-VISUAL-03, APP-OS-VISUAL-1030-M002)',
    'status': 'Owner-selected implementation reference (Nathan, 2026-10-29 20:12Z) that the product Home implements; '
              'rendered and native visual acceptance remain open',
    'wallpaperPath': WALLPAPER,
    'wallpaperSha256': '67e3975453b7caa1fc344b12f1f82088747c3aa5730621af72b7ecf02d0463c5',
    'wallpaperBasis': 'prototypes/rose-hifi/src/assets/wallpaper.jpg at ce126f56 (Git blob '
                      '1d99a767629749ee23a08487a0352d214ef29720), 1024 x 1536, 286252 bytes, embedded unchanged as '
                      'Rose-HiFi.html --wallpaper. Earlier Agent builds ship the Rose-Fold-v3 image (461 x 1024, '
                      '409c0dbfa309dbd56f97a59b04223532e9931ae5c5c4625d3567b77a9c56d8c5) and fail this check truthfully',
}
# Image paths of the contract apps (os/alpha/cuttlefish-product-contract.json imageExpectations, docs/OS-RELEASE.md).
APP_PATHS = {
    'dev.pixel.voice': ('product', 'app/RosePreinstall_dev_pixel_voice/RosePreinstall_dev_pixel_voice.apk'),
    'dev.pixel.calculator.agent': ('product', 'app/RosePreinstall_dev_pixel_calculator_agent/RosePreinstall_dev_pixel_calculator_agent.apk'),
    'dev.pixel.backgroundcontrol': ('system_ext', 'priv-app/PixelBackgroundControl/PixelBackgroundControl.apk'),
}
DEX_MARKERS = ['MainActivity', 'RoseHomeView', 'RoseConversationActivity', 'LauncherPager', 'PairingActivity']
EMULATOR_DEVICES = ('vsoc_',)
ROSE_HOME_MARKER = 'rose_home_role_selected'  # per-user Settings.Secure marker of the OS default-HOME selection
HOME = 'android.intent.category.HOME'
LAUNCHER = 'android.intent.category.LAUNCHER'


class Fail(Exception):
    def __init__(self, message, code=2):
        super().__init__(message)
        self.code = code


def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


def check(name, ok, detail=None):
    """ok: True pass, False fail, None unavailable (not measured; never counted as a pass)."""
    return {'check': name, 'result': {True: 'pass', False: 'fail', None: 'unavailable'}[ok], 'detail': detail}


EXPECTATION_KEYS = {'schemaVersion', 'kind', 'label', 'channel', 'candidate', 'build', 'target', 'apps',
                    'appSignerSha256', 'avbPublicKeySha256', 'basis'}
HEX64 = re.compile(r'[0-9a-f]{64}')


def load_expectation(path):
    """One candidate's exact identities, validated fail-closed. Nothing is defaulted or inferred."""
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, ValueError) as e:
        raise Fail(f'expectation {path}: {e}')
    errors = []
    if not isinstance(data, dict):
        raise Fail(f'expectation {path}: not a JSON object')
    errors += [f'unknown key {k}' for k in sorted(set(data) - EXPECTATION_KEYS)]
    if data.get('schemaVersion') != 1 or data.get('kind') != 'rose-verify-expectation':
        errors.append('schemaVersion must be 1 and kind rose-verify-expectation')
    for key in ('label', 'basis'):
        if not isinstance(data.get(key), str) or not data[key].strip():
            errors.append(f'{key} must be a non-empty string')
    if data.get('channel') not in ('alpha', 'beta', 'app'):
        errors.append('channel must be alpha, beta or app')
    if not isinstance(data.get('candidate'), str) or not re.fullmatch(r'[0-9a-f]{40}', data['candidate']):
        errors.append('candidate must be a full 40-hex source SHA')
    if 'build' in data and (not isinstance(data['build'], str) or not data['build'].isdigit()):
        errors.append('build must be a digit string')
    target = data.get('target')
    if not isinstance(target, dict) or not target or not all(isinstance(k, str) and isinstance(v, str) and v
                                                             for k, v in target.items()):
        errors.append('target must be a non-empty object of non-empty strings')
    for key in ('appSignerSha256',) + (('avbPublicKeySha256',) if 'avbPublicKeySha256' in data else ()):
        if not isinstance(data.get(key), str) or not HEX64.fullmatch(data[key]):
            errors.append(f'{key} must be 64 lowercase hex')
    apps, seen = data.get('apps'), set()
    if not isinstance(apps, list) or not apps:
        errors.append('apps must be a non-empty list')
        apps = []
    for i, app in enumerate(apps):
        if not isinstance(app, dict) or set(app) - {'package', 'version', 'sha256', 'imagePath'}:
            errors.append(f'apps[{i}] must have only package, version, sha256, imagePath')
            continue
        pkg = app.get('package')
        if not isinstance(pkg, str) or not re.fullmatch(r'[a-zA-Z][\w]*(\.[a-zA-Z][\w]*)+', pkg):
            errors.append(f'apps[{i}].package invalid')
        elif pkg in seen:
            errors.append(f'apps[{i}].package {pkg} repeated')
        seen.add(pkg)
        if not isinstance(app.get('version'), int) or isinstance(app.get('version'), bool) or app['version'] < 1:
            errors.append(f'apps[{i}].version must be an integer >= 1')
        if not isinstance(app.get('sha256'), str) or not HEX64.fullmatch(app['sha256']):
            errors.append(f'apps[{i}].sha256 must be 64 lowercase hex')
        if 'imagePath' in app and (not isinstance(app['imagePath'], str) or
                                   not re.fullmatch(r'(product|system_ext|system|vendor)/\S+\.apk', app['imagePath'])):
            errors.append(f'apps[{i}].imagePath must be partition/path.apk')
    if errors:
        raise Fail(f'expectation {path} rejected: ' + '; '.join(errors))
    return dict(data, path=str(path), fileSha256=sha256(path))


def expectation_summary(e):
    return None if e is None else {k: e.get(k) for k in ('label', 'channel', 'candidate', 'build', 'path', 'fileSha256')}


def image_location(app):
    """(partition, path inside it) from the expectation's imagePath; None when the expectation does not say."""
    if 'imagePath' not in app:
        return None
    part, inner = app['imagePath'].split('/', 1)
    return part, inner


def expected():
    contract = json.loads(CONTRACT.read_text())
    pipeline = json.loads(PIPELINE.read_text())
    return {
        'kind': 'expected-identities',
        'candidate': contract['base']['candidate'],
        'apps': [dict(a, imagePath=f'{APP_PATHS[a["package"]][0]}/{APP_PATHS[a["package"]][1]}') for a in contract['apps']],
        'appSignerSha256': pipeline['identities']['appSignerSha256'],
        'avbPublicKeySha256': pipeline['identities']['avbPublicKeySha256'],
        'otaCertificateSha256': pipeline['identities']['otaCertificateSha256'],
        'targets': {
            'beta': {'device': pipeline['target']['device'], 'lunch': pipeline['target']['lunch'],
                     'runtime': 'physical Pixel 10 (arm64) only; never bootable on x86_64 Cuttlefish'},
            'alpha': {'lunch': contract['target']['lunch'], 'device': contract['target']['device'],
                      'runtime': 'x86_64 Cuttlefish guest; same app bytes, different OS product (no Updater, '
                                 'Pixel overlays not established)'},
        },
        'reference': REFERENCE,
    }


# ---------- tools ----------

def build_tools(override=None):
    roots = [Path(override)] if override else []
    for env in ('ANDROID_SDK_ROOT', 'ANDROID_HOME'):
        if os.environ.get(env):
            roots.append(Path(os.environ[env]))
    roots += [ROOT/'.tools/android-sdk', Path('/opt/roseos/android-sdk')]
    for root in roots:
        for candidate in [root, *sorted((root/'build-tools').glob('*'), reverse=True)]:
            if (candidate/'aapt2').exists() and (candidate/'apksigner').exists():
                return candidate
    raise Fail('aapt2/apksigner not found; run bash scripts/bootstrap.sh or pass --build-tools', 4)


def run(argv, check_rc=True, binary=False):
    p = subprocess.run(argv, capture_output=True, text=not binary)
    if check_rc and p.returncode:
        raise Fail(f'{Path(argv[0]).name} exited {p.returncode}: {(p.stderr or "")[-300:]}', 1)
    return p


# ---------- APK inspection ----------

def parse_badging(text):
    out = {'packageName': None, 'versionCode': None, 'versionName': None, 'minSdk': None, 'targetSdk': None,
           'nativeCode': [], 'launchableActivity': None, 'label': None}
    m = re.search(r"^package: name='([^']*)' versionCode='([^']*)' versionName='([^']*)'", text, re.M)
    if m:
        out['packageName'], out['versionCode'], out['versionName'] = m.group(1), int(m.group(2)), m.group(3)
    for key, pattern in (('minSdk', r"^(?:minSdkVersion|sdkVersion):'(\d+)'"), ('targetSdk', r"^targetSdkVersion:'(\d+)'")):
        m = re.search(pattern, text, re.M)
        out[key] = int(m.group(1)) if m else None
    m = re.search(r"^native-code: (.*)$", text, re.M)
    out['nativeCode'] = re.findall(r"'([^']+)'", m.group(1)) if m else []
    m = re.search(r"^launchable-activity: name='([^']*)'", text, re.M)
    out['launchableActivity'] = m.group(1) if m else None
    m = re.search(r"^application-label:'([^']*)'", text, re.M)
    out['label'] = m.group(1) if m else None
    return out


def parse_xmltree(text):
    """aapt2 dump xmltree output as nested {'tag', 'attrs', 'children'} using its indentation."""
    root = {'tag': '#root', 'attrs': {}, 'children': []}
    stack = [(-1, root)]
    for line in text.splitlines():
        stripped = line.lstrip()
        indent = len(line) - len(stripped)
        while stack[-1][0] >= indent:
            stack.pop()
        if stripped.startswith('E: '):
            node = {'tag': stripped[3:].split(' ')[0], 'attrs': {}, 'children': []}
            stack[-1][1]['children'].append(node)
            stack.append((indent, node))
        elif stripped.startswith('A: '):
            m = re.match(r'A: (?:[^=]*:)?([\w-]+)(?:\(0x[0-9a-f]+\))?="([^"]*)"', stripped) or \
                re.match(r'A: (?:[^=]*:)?([\w-]+)(?:\(0x[0-9a-f]+\))?=(\S+)', stripped)
            if m:
                stack[-1][1]['attrs'][m.group(1)] = m.group(2)
    return root


def manifest_components(tree_root, package):
    """Activities (and aliases) with their intent-filter categories/actions; names made absolute."""
    found = []

    def walk(node):
        for child in node['children']:
            if child['tag'] in ('activity', 'activity-alias'):
                name = child['attrs'].get('name', '')
                name = package + name if name.startswith('.') else name
                filters = []
                for f in child['children']:
                    if f['tag'] == 'intent-filter':
                        filters.append({'actions': [c['attrs'].get('name') for c in f['children'] if c['tag'] == 'action'],
                                        'categories': [c['attrs'].get('name') for c in f['children'] if c['tag'] == 'category']})
                found.append({'name': name, 'exported': child['attrs'].get('exported'), 'filters': filters})
            walk(child)
    walk(tree_root)
    return found


def with_category(components, category):
    return [c['name'] for c in components if any(category in f['categories'] for f in c['filters'])]


def signer(tools, apk):
    p = run([str(tools/'apksigner'), 'verify', '--print-certs', str(apk)], check_rc=False)
    certs = re.findall(r'Signer #\d+ certificate SHA-256 digest: ([0-9a-f]{64})', p.stdout)
    return {'verified': p.returncode == 0, 'certSha256': certs,
            'status': 'signed' if p.returncode == 0 and certs else 'unsigned-or-unverified'}


def wallpaper_entry(tools, apk):
    text = run([str(tools/'aapt2'), 'dump', 'resources', str(apk)], check_rc=False).stdout
    m = re.search(r'drawable/rose_home_wallpaper\b.*?\n\s+\([^)]*\) \(file\) (\S+)', text, re.S)
    if not m:
        return None
    with zipfile.ZipFile(apk) as z:
        data = z.read(m.group(1))
    return {'entry': m.group(1), 'sha256': hashlib.sha256(data).hexdigest(), 'size': len(data)}


def dex_markers(apk):
    present = {}
    with zipfile.ZipFile(apk) as z:
        dex = [n for n in z.namelist() if re.fullmatch(r'classes\d*\.dex', n)]
        blobs = [z.read(n) for n in dex]
    for marker in DEX_MARKERS:
        token = f'Ldev/pixel/voice/{marker};'.encode()
        present[marker] = any(token in b for b in blobs)
    return {'dexFiles': dex, 'classes': present}


def inspect_apk(tools, apk, expectation=None):
    apk = Path(apk)
    if not apk.is_file():
        raise Fail(f'not a file: {apk}')
    badging = parse_badging(run([str(tools/'aapt2'), 'dump', 'badging', str(apk)]).stdout)
    tree = parse_xmltree(run([str(tools/'aapt2'), 'dump', 'xmltree', '--file', 'AndroidManifest.xml', str(apk)]).stdout)
    components = manifest_components(tree, badging['packageName'] or '')
    digest = sha256(apk)
    sig = signer(tools, apk)
    result = {'path': str(apk), 'sha256': digest, 'size': apk.stat().st_size, **badging,
              'abi': badging['nativeCode'] or 'none (no native code: ABI-neutral)',
              'homeActivities': with_category(components, HOME),
              'launcherActivities': with_category(components, LAUNCHER), 'signer': sig, 'checks': []}
    if badging['packageName'] == 'dev.pixel.voice':
        result['wallpaper'] = wallpaper_entry(tools, apk)
        result['dex'] = dex_markers(apk)
        result['checks'].append(check('Reference wallpaper bytes', None if result['wallpaper'] is None else
                                      result['wallpaper']['sha256'] == REFERENCE['wallpaperSha256'], result['wallpaper']))
        result['checks'].append(check('RoseHomeView and RoseConversationActivity compiled in',
                                      result['dex']['classes']['RoseHomeView'] and result['dex']['classes']['RoseConversationActivity']))
        result['checks'].append(check('MainActivity declares HOME and LAUNCHER',
                                      'dev.pixel.voice.MainActivity' in result['homeActivities'] and
                                      'dev.pixel.voice.MainActivity' in result['launcherActivities']))
    if expectation is None:
        return result
    entry = next((a for a in expectation['apps'] if a['package'] == badging['packageName']), None)
    result['checks'].append(check(f'{badging["packageName"]} listed in expectation', entry is not None, expectation['label']))
    if entry:
        result['checks'].append(check('expected versionCode', badging['versionCode'] == entry['version'],
                                      {'expected': entry['version'], 'actual': badging['versionCode']}))
        result['checks'].append(check('expected bytes (sha256)', digest == entry['sha256'],
                                      {'expected': entry['sha256'], 'actual': digest}))
        pin = expectation['appSignerSha256']
        result['checks'].append(check('expected app signer', sig['verified'] and sig['certSha256'] == [pin],
                                      {'expected': pin, 'actual': sig['certSha256']}))
    return result


# ---------- OTA payload ----------

def pb_fields(buf):
    i = 0
    while i < len(buf):
        key, i = varint(buf, i)
        field, wire = key >> 3, key & 7
        if wire == 0:
            value, i = varint(buf, i)
        elif wire == 2:
            size, i = varint(buf, i)
            value, i = buf[i:i + size], i + size
        elif wire == 1:
            value, i = buf[i:i + 8], i + 8
        elif wire == 5:
            value, i = buf[i:i + 4], i + 4
        else:
            raise Fail(f'unsupported protobuf wire type {wire}', 1)
        yield field, value


def varint(buf, i):
    shift = value = 0
    while True:
        b = buf[i]
        i += 1
        value |= (b & 0x7f) << shift
        if b < 0x80:
            return value, i
        shift += 7


def parse_manifest(buf):
    """DeltaArchiveManifest (update_engine update_metadata.proto): block size and per-partition full operations."""
    block_size, partitions = 4096, []
    for field, value in pb_fields(buf):
        if field == 3:
            block_size = value
        elif field == 13:
            part = {'name': None, 'size': None, 'hash': None, 'operations': []}
            for f, v in pb_fields(value):
                if f == 1:
                    part['name'] = v.decode()
                elif f == 7:
                    for pf, pv in pb_fields(v):
                        if pf == 1:
                            part['size'] = pv
                        elif pf == 2:
                            part['hash'] = pv.hex()
                elif f == 8:
                    op = {'type': 0, 'offset': 0, 'length': 0, 'dst': [], 'sha256': None}
                    for of, ov in pb_fields(v):
                        if of == 1:
                            op['type'] = ov
                        elif of == 2:
                            op['offset'] = ov
                        elif of == 3:
                            op['length'] = ov
                        elif of == 6:
                            ext = dict(pb_fields(ov))
                            op['dst'].append((ext.get(1, 0), ext.get(2, 0)))
                        elif of == 8:
                            op['sha256'] = ov.hex()
                    part['operations'].append(op)
            partitions.append(part)
    return block_size, partitions


def payload_entry(ota):
    with zipfile.ZipFile(ota) as z:
        info = z.getinfo('payload.bin')
        if info.compress_type != zipfile.ZIP_STORED:
            raise Fail('payload.bin is not stored uncompressed', 1)
    with open(ota, 'rb') as f:
        f.seek(info.header_offset)
        header = f.read(30)
        if header[:4] != b'PK\x03\x04':
            raise Fail('bad local header for payload.bin', 1)
        name_len, extra_len = struct.unpack('<HH', header[26:30])
    return info.header_offset + 30 + name_len + extra_len, info.file_size


def read_payload(ota):
    start, size = payload_entry(ota)
    with open(ota, 'rb') as f:
        f.seek(start)
        magic, version, manifest_size, sig_size = struct.unpack('>4sQQI', f.read(24))
        if magic != b'CrAU' or version != 2:
            raise Fail(f'unsupported payload header {magic!r} v{version}', 1)
        manifest = f.read(manifest_size)
    block_size, partitions = parse_manifest(manifest)
    return {'start': start, 'size': size, 'dataStart': start + 24 + manifest_size + sig_size,
            'blockSize': block_size, 'partitions': partitions}


def decompress(kind, data):
    if kind == 0:
        return data
    if kind == 1:
        return bz2.decompress(data)
    if kind == 8:
        return lzma.decompress(data)
    if kind == 14:
        try:
            import zstandard
        except ImportError:
            raise Fail('payload uses zstd; install the zstandard module', 4)
        return zstandard.ZstdDecompressor().decompressobj().decompress(data)
    raise Fail(f'operation type {kind} is not a full-image operation', 1)


def extract_partition(ota, payload, name, dest):
    part = next((p for p in payload['partitions'] if p['name'] == name), None)
    if part is None:
        raise Fail(f'partition {name} not in payload', 1)
    bs = payload['blockSize']
    with open(ota, 'rb') as src, open(dest, 'wb') as out:
        out.truncate(part['size'])
        for op in part['operations']:
            if op['type'] in (6, 7):  # ZERO, DISCARD: the truncated file already reads as zeros
                continue
            src.seek(payload['dataStart'] + op['offset'])
            blob = src.read(op['length'])
            if op['sha256'] and hashlib.sha256(blob).hexdigest() != op['sha256']:
                raise Fail(f'{name}: operation data hash mismatch at offset {op["offset"]}', 1)
            data = decompress(op['type'], blob)
            for start, count in op['dst']:
                chunk, data = data[:count * bs], data[count * bs:]
                out.seek(start * bs)
                out.write(chunk)
    actual = sha256(dest)
    if part['hash'] and actual != part['hash']:
        raise Fail(f'{name}: partition hash mismatch (expected {part["hash"]}, got {actual})', 1)
    return {'partition': name, 'size': part['size'], 'sha256': actual, 'manifestHash': part['hash']}


BUILD_PROPS = {'system': 'system/build.prop', 'vendor': 'build.prop', 'product': 'etc/build.prop',
               'system_ext': 'etc/build.prop'}
PROP_KEYS = re.compile(r'cpu\.abi|build\.fingerprint|build\.version\.sdk$|build\.type$|product\.device$|pixel_agent_tray')


def fs_type(image):
    with open(image, 'rb') as f:
        f.seek(1024)
        superblock = f.read(64)
    if superblock[:4] == b'\xe2\xe1\xf5\xe0':
        return 'erofs'
    if superblock[56:58] == b'\x53\xef':
        return 'ext4'
    raise Fail(f'unknown filesystem in {Path(image).name}', 1)


def erofs_tree(image):
    """erofs images are extracted once with fsck.erofs (nothing mounted) and read from the tree."""
    tree = Path(image).with_suffix('.tree')
    if not tree.exists():
        tool = shutil.which('fsck.erofs')
        if not tool:
            raise Fail('erofs image needs fsck.erofs (erofs-utils)', 4)
        run([tool, f'--extract={tree}', str(image)])
    return tree


def debugfs():
    tool = shutil.which('debugfs') or '/usr/sbin/debugfs'
    if not Path(tool).exists():
        raise Fail('ext4 image needs debugfs (e2fsprogs)', 4)
    return tool


def image_read(image, inner):
    """(filesystem, bytes of one regular file or None if absent) from an ext4 or erofs image."""
    fs = fs_type(image)
    if fs == 'erofs':
        path = erofs_tree(image)/inner
        return fs, path.read_bytes() if path.is_file() and not path.is_symlink() else None
    if 'Type: regular' not in run([debugfs(), '-R', f'stat /{inner}', str(image)], check_rc=False).stdout:
        return fs, None
    return fs, run([debugfs(), '-R', f'cat /{inner}', str(image)], binary=True).stdout


def build_props(image, name):
    fs, data = image_read(image, BUILD_PROPS[name]) if name in BUILD_PROPS else (None, None)
    if data is None:
        return None
    props = dict(line.split('=', 1) for line in data.decode(errors='replace').splitlines()
                 if '=' in line and not line.startswith('#'))
    return {k: v for k, v in sorted(props.items()) if PROP_KEYS.search(k)}


APP_DIRS = {'system': ('system/app', 'system/priv-app'), 'vendor': ('app', 'priv-app'), 'product': ('app', 'priv-app'),
            'system_ext': ('app', 'priv-app')}


def image_list(image, inner):
    """Names directly under a directory of an ext4/erofs image (directories and files)."""
    if fs_type(image) == 'erofs':
        d = erofs_tree(image)/inner
        return sorted(x.name for x in d.iterdir()) if d.is_dir() else []
    out = run([debugfs(), '-R', f'ls -p /{inner}', str(image)], check_rc=False).stdout
    names = [line.split('/')[5] for line in out.splitlines() if line.count('/') >= 6]
    return sorted(n for n in names if n not in ('.', '..', ''))


def home_candidates(tools, image, name, scratch):
    """Every APK under the partition's app/priv-app directories whose manifest has a HOME activity."""
    found = []
    for root in APP_DIRS.get(name, ()):
        for app in image_list(image, root):
            for apk in [f for f in image_list(image, f'{root}/{app}') if f.endswith('.apk')]:
                _, data = image_read(image, f'{root}/{app}/{apk}')
                if data is None:
                    continue
                tmp = Path(scratch)/'home-scan.apk'
                tmp.write_bytes(data)
                xml = run([str(tools/'aapt2'), 'dump', 'xmltree', '--file', 'AndroidManifest.xml', str(tmp)], check_rc=False).stdout
                pkg = re.search(r'A: package="([^"]+)"', xml)
                pkg = pkg.group(1) if pkg else None
                homes = with_category(manifest_components(parse_xmltree(xml), pkg or ''), HOME)
                if homes:
                    found.append({'path': f'{name}/{root}/{app}/{apk}', 'package': pkg, 'homeActivities': homes,
                                  'sha256': hashlib.sha256(data).hexdigest()})
                tmp.unlink()
    return found


def ota_metadata(ota):
    with zipfile.ZipFile(ota) as z:
        names = set(z.namelist())
        meta = z.read('META-INF/com/android/metadata').decode() if 'META-INF/com/android/metadata' in names else ''
        props = z.read('payload_properties.txt').decode() if 'payload_properties.txt' in names else ''
    fields = dict(line.split('=', 1) for line in meta.splitlines() if '=' in line)
    keep = ('ota-type', 'pre-device', 'post-build', 'post-build-incremental', 'post-sdk-level',
            'post-security-patch-level', 'post-timestamp', 'ota-required-cache')
    return {'metadata': {k: fields[k] for k in keep if k in fields},
            'payloadProperties': sorted(line.split('=', 1)[0] for line in props.splitlines() if '=' in line)}


def release(directory, extract_dir=None, partitions=None, expect_source=None, tools_override=None, expectation=None):
    d = Path(directory)
    sums = d/'SHA256SUMS'
    if not sums.is_file():
        raise Fail(f'{sums} missing')
    if extract_dir and expectation is None:
        raise Fail('--extract-apps needs --expect: the exact identities recorded for this candidate')
    if expectation and expect_source and expect_source != expectation['candidate']:
        raise Fail(f'--expect-source {expect_source} contradicts the expectation candidate {expectation["candidate"]}')
    expect_source = expectation['candidate'] if expectation else expect_source
    pipeline = json.loads(PIPELINE.read_text())
    listed = {}
    for line in sums.read_text().splitlines():
        m = re.fullmatch(r'([0-9a-f]{64})  (\S+)', line.strip())
        if m:
            listed[m.group(2)] = m.group(1)
    files, checks = {}, []
    for name, digest in sorted(listed.items()):
        path = d/name
        if path.is_file():
            actual = sha256(path)
            files[name] = {'sha256': actual, 'size': path.stat().st_size, 'matchesSums': actual == digest}
            checks.append(check(f'SHA256SUMS {name}', actual == digest))
        else:
            files[name] = {'sha256Listed': digest, 'present': False}
    result = {'kind': 'artifact-inspection', 'directory': str(d), 'expectation': expectation_summary(expectation),
              'files': files, 'checks': checks}
    avb = listed.get('avb_pkmd.bin')
    avb_pin = (expectation or {}).get('avbPublicKeySha256') or pipeline['identities']['avbPublicKeySha256']
    checks.append(check('AVB public key pin (SHA256SUMS listing)', None if avb is None else avb == avb_pin,
                        {'listed': avb, 'expected': avb_pin,
                         'pinSource': 'expectation' if (expectation or {}).get('avbPublicKeySha256') else str(PIPELINE.relative_to(ROOT))}))
    if (d/'verification.json').is_file():
        v = json.loads((d/'verification.json').read_text())
        result['candidate'] = v.get('candidate')
        result['receipt'] = {k: v.get(k) for k in ('receiptType', 'published', 'simulated', 'signedTargetFilesSha256')}
        if expect_source:
            checks.append(check('candidate source', v.get('candidate', {}).get('sha') == expect_source,
                                {'expected': expect_source, 'actual': v.get('candidate', {}).get('sha')}))
        if expectation and 'build' in expectation:
            checks.append(check('candidate build', v.get('candidate', {}).get('build') == expectation['build'],
                                {'expected': expectation['build'], 'actual': v.get('candidate', {}).get('build')}))
    feeds = sorted(p.name for p in d.glob('*-stable'))
    for feed in feeds:
        parts = (d/feed).read_text().split()
        result['feedLine'] = parts
        if result.get('candidate'):
            checks.append(check(f'{feed} line matches candidate', parts[:2] == [result['candidate'].get('build'),
                                                                                  result['candidate'].get('timestamp')], parts))
    otas = sorted(d.glob('*-ota_update-*.zip'))
    result['runtime'] = {'performed': False, 'reason': 'artifact inspection only'}
    for ota in otas:
        meta = ota_metadata(ota)
        result['ota'] = {'file': ota.name, **meta}
        device = meta['metadata'].get('pre-device', '')
        if expectation and 'device' in expectation['target']:
            checks.append(check('OTA device equals expectation target', device == expectation['target']['device'],
                                {'expected': expectation['target']['device'], 'actual': device}))
        if device == pipeline['target']['device']:
            result['runtime']['cuttlefishBootable'] = False
            result['runtime']['reason'] = (f'{device} is a physical Pixel 10 (arm64) OTA; it cannot boot on x86_64 '
                                           'Cuttlefish. Runtime evidence for it comes only from a physical device.')
        payload = read_payload(ota)
        result['ota']['payload'] = {'blockSize': payload['blockSize'],
                                    'partitions': [{'name': p['name'], 'size': p['size'], 'operations': len(p['operations'])}
                                                   for p in payload['partitions']]}
        if extract_dir:
            out = Path(extract_dir)
            out.mkdir(parents=True, exist_ok=True)
            tools = build_tools(tools_override)
            images, apps = {}, []
            located = {a['package']: image_location(a) for a in expectation['apps']}
            wanted = partitions or sorted({loc[0] for loc in located.values() if loc})
            for name in wanted:
                images[name] = extract_partition(ota, payload, name, out/f'{name}.img')
                checks.append(check(f'payload {name} operation and partition hashes', True, images[name]['sha256']))
                images[name]['buildProps'] = build_props(out/f'{name}.img', name)
            for package, loc in located.items():
                if loc is None:
                    checks.append(check(f'{package} image path known', False, 'the expectation has no imagePath for it'))
                    continue
                part, inner = loc
                if part not in images:
                    checks.append(check(f'{package} partition {part} extracted', False, 'add --partition ' + part))
                    continue
                dest = out/f'{package}.apk'
                fs, data = image_read(out/f'{part}.img', inner)
                if data is None:
                    checks.append(check(f'{package} present at {part}/{inner}', False))
                    continue
                dest.write_bytes(data)
                info = inspect_apk(tools, dest, expectation)
                info.update(imagePath=f'{part}/{inner}', filesystem=fs)
                apps.append(info)
                checks.extend(info['checks'])
            homes = [h for name in images for h in home_candidates(tools, out/f'{name}.img', name, out)]
            result['ota']['extracted'] = {'partitions': images, 'apps': apps, 'homeCandidates': homes}
            result['ota']['homeNote'] = ('Image content only: every packaged HOME activity. Which one holds the HOME role '
                                         'is decided on the device (runtime: cmd role get-role-holders).')
    return result


# ---------- source inspection ----------

def git_show(rev, path):
    p = subprocess.run(['git', '-C', str(ROOT), 'show', f'{rev}:{path}'], capture_output=True)
    if p.returncode:
        raise Fail(f'git show {rev}:{path} failed: {p.stderr.decode()[-200:]}', 2)
    return p.stdout


def source(rev):
    commit = subprocess.run(['git', '-C', str(ROOT), 'rev-parse', '--verify', f'{rev}^{{commit}}'],
                            capture_output=True, text=True)
    if commit.returncode:
        raise Fail(f'unknown revision {rev}')
    commit = commit.stdout.strip()
    ns = '{http://schemas.android.com/apk/res/android}'
    manifest = ET.fromstring(git_show(commit, 'workspace/rose-agent/app/AndroidManifest.xml'))
    package = manifest.get('package')
    sdk = manifest.find('uses-sdk')
    activities = []
    for a in manifest.iter():
        if a.tag in ('activity', 'activity-alias'):
            name = a.get(ns + 'name')
            name = package + name if name.startswith('.') else name
            filters = [{'actions': [x.get(ns + 'name') for x in f.findall('action')],
                        'categories': [x.get(ns + 'name') for x in f.findall('category')]} for f in a.findall('intent-filter')]
            activities.append({'name': name, 'exported': a.get(ns + 'exported'), 'filters': filters})
    main = git_show(commit, 'workspace/rose-agent/app/src/dev/pixel/voice/MainActivity.java').decode()
    pages = re.findall(r'titles\.add\("([^"]+)"\)', main)
    indexes = dict(re.findall(r'\b(homePage|voicePage|widgetsPage|ourAppsPage)=(\d+)', main))
    home_rule = next((line.strip() for line in main.splitlines() if 'void showHomeFor(' in line), None)
    try:
        entry = git_show(commit, 'workspace/rose-agent/app/src/dev/pixel/voice/HomeEntry.java').decode()
        entry_rules = re.findall(r'^\s*(if \(.*?;|return new HomeEntry\(.*?;)\s*$', entry, re.M)
    except Fail:
        entry_rules = None
    wallpaper = hashlib.sha256(git_show(commit, WALLPAPER)).hexdigest()
    result = {
        'kind': 'source-inspection', 'commit': commit,
        'package': package, 'versionCode': int(manifest.get(ns + 'versionCode')),
        'versionName': manifest.get(ns + 'versionName'),
        'minSdk': int(sdk.get(ns + 'minSdkVersion')) if sdk is not None else None,
        'targetSdk': int(sdk.get(ns + 'targetSdkVersion')) if sdk is not None else None,
        'homeActivities': with_category(activities, HOME), 'launcherActivities': with_category(activities, LAUNCHER),
        'routing': {
            'launcherPages': pages, 'pageIndexes': {k: int(v) for k, v in indexes.items()},
            'entryMethod': home_rule,
            'homeEntryRules': entry_rules,
            'homePresenceOpens': 'RoseConversationActivity' if 'void openRoseConversation(){startActivity(new Intent(this,RoseConversationActivity.class));}' in main else None,
            'pairingOnCreate': 'PairingActivity started from MainActivity.onCreate when the device is unpaired'
            if 'if(!DeviceIdentity.paired(this))startActivity(new Intent(this,PairingActivity.class))' in main else None,
        },
        'wallpaperSha256': wallpaper,
        'checks': [check('Reference wallpaper bytes', wallpaper == REFERENCE['wallpaperSha256']),
                   check('MainActivity declares HOME', f'{package}.MainActivity' in with_category(activities, HOME))],
        'runtime': {'performed': False, 'reason': 'source inspection only; no build or device'},
    }
    return result


# ---------- screenshot comparison ----------

def cover_crop(image, width, height):
    """RoseHomeView.onSizeChanged: CSS background-size: cover, centered."""
    bw, bh = image.size
    if bw * height > bh * width:
        crop = round(bh * width / height)
        box = ((bw - crop) // 2, 0, (bw + crop) // 2, bh)
    else:
        crop = round(bw * height / width)
        box = (0, (bh - crop) // 2, bw, (bh + crop) // 2)
    return image.crop(box).resize((width, height))


def compare(screenshot, out, reference=None, label='screenshot'):
    try:
        from PIL import Image, ImageChops
    except ImportError:
        raise Fail('screenshot comparison needs Pillow', 4)
    ref_path = Path(reference) if reference else ROOT/WALLPAPER
    shot = Image.open(screenshot).convert('RGB')
    ref = cover_crop(Image.open(ref_path).convert('RGB'), *shot.size)
    grid = (27, 48)
    small_shot = shot.resize(grid, Image.BOX)
    small_ref = ref.resize(grid, Image.BOX)
    shot_px, ref_px = (list(zip(*[iter(img.tobytes())] * 3)) for img in (small_shot, small_ref))
    lum = [sum(p) / 3 for p in shot_px]
    lum_sorted = sorted(lum)
    mean_lum = sum(lum) / len(lum)
    p95 = lum_sorted[int(len(lum_sorted) * .95)]
    cells = [sum(abs(a - b) for a, b in zip(p, q)) / 3 for p, q in zip(shot_px, ref_px)]
    coverage = sum(c <= 40 for c in cells) / len(cells)
    if mean_lum < 20 and p95 < 40:
        verdict = 'black-screen'
    elif coverage >= .5:
        verdict = 'wallpaper-consistent'
    else:
        verdict = 'differs-from-reference'
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    diff = ImageChops.difference(shot, ref)
    side = Image.new('RGB', (shot.width * 3, shot.height), 'white')
    for i, img in enumerate((ref, shot, diff)):
        side.paste(img, (i * shot.width, 0))
    side_path = out/f'{label}-vs-reference.png'
    side.save(side_path)
    return {'kind': 'screenshot-comparison', 'screenshot': str(screenshot), 'screenshotSha256': sha256(screenshot),
            'size': list(shot.size), 'reference': str(ref_path), 'referenceSha256': sha256(ref_path),
            'meanLuminance': round(mean_lum, 1), 'p95Luminance': round(p95, 1),
            'wallpaperCellCoverage': round(coverage, 3), 'verdict': verdict, 'sideBySide': str(side_path),
            'note': 'Heuristic over a 27x48 grid (cells within 40 levels of the cover-cropped wallpaper); '
                    'overlays lower coverage. A human reviews the side-by-side image before any visual claim.'}


# ---------- runtime ----------

class Adb:
    def __init__(self, serial, adb=None):
        self.argv = [adb or shutil.which('adb') or str(ROOT/'.tools/android-sdk/platform-tools/adb'), '-s', serial]
        if not Path(self.argv[0]).exists() and not shutil.which(self.argv[0]):
            raise Fail('adb not found', 4)

    def shell(self, command, binary=False):
        """Command output; a command's own non-zero status (e.g. pm path of a missing package) is not an error,
        an adb transport error is."""
        mode = 'exec-out' if binary else 'shell'
        p = subprocess.run(self.argv + [mode, command], capture_output=True, text=not binary, timeout=120)
        err = p.stderr if isinstance(p.stderr, str) else p.stderr.decode(errors='replace')
        if p.returncode and re.match(r'(error|adb):', err.strip()):
            raise Fail(f'adb {mode} {command.split()[0]}: {err.strip()[:200]}', 1)
        return p.stdout


def outside_repo(path):
    resolved = Path(path).resolve()
    if resolved == ROOT or ROOT in resolved.parents:
        raise Fail('runtime output must be outside the repository (screenshots and dumps are never committed)')
    return resolved


def focus(adb):
    """The resumed activity (ActivityManager), else the focused window (WindowManager)."""
    m = re.search(r'(?:topResumedActivity|mResumedActivity|ResumedActivity)[:=] ?ActivityRecord\{\S+ \S+ (\S+/\S+)',
                  adb.shell('dumpsys activity activities'))
    if not m:
        m = re.search(r'mCurrentFocus=Window\{\S+ \S+ ([^}\s]+)\}', adb.shell('dumpsys window'))
    return short_component(m.group(1)) if m else None


def short_component(component):
    """pkg/pkg.Cls -> pkg/.Cls, the form am and resolve-activity print, so focus compares exactly."""
    pkg, _, cls = component.partition('/')
    return f'{pkg}/{cls[len(pkg):]}' if cls.startswith(pkg + '.') else component


def ui_dump(adb, out, label):
    remote = '/data/local/tmp/rose-verify-ui.xml'
    adb.shell(f'uiautomator dump {remote}')
    xml = adb.shell(f'cat {remote}')
    adb.shell(f'rm -f {remote}')
    (out/f'{label}.xml').write_text(xml)
    nodes = []
    for node in ET.fromstring(xml).iter('node'):
        nodes.append({'text': node.get('text'), 'desc': node.get('content-desc'), 'bounds': node.get('bounds'),
                      'package': node.get('package')})
    return nodes


def screenshot(adb, out, label):
    path = out/f'{label}.png'
    path.write_bytes(adb.shell('screencap -p', binary=True))
    return path


def tap_desc(adb, nodes, prefix):
    for n in nodes:
        if (n['desc'] or '').startswith(prefix) and n['bounds']:
            x1, y1, x2, y2 = map(int, re.findall(r'\d+', n['bounds']))
            adb.shell(f'input tap {(x1 + x2) // 2} {(y1 + y2) // 2}')
            return n['desc']
    return None


def parse_users(text):
    """`pm list users` as ids, flags and running state only; user names are personal and never recorded."""
    return [{'id': int(m.group(1)), 'flags': m.group(2), 'running': bool(m.group(3))}
            for m in re.finditer(r'UserInfo\{(\d+):[^\n]*:([0-9a-fA-F]+)\}( running)?', text)]


def user_package_state(dump, user):
    """The `User N:` line of `dumpsys package PKG`: per-user installed/hidden/suspended/stopped/enabled."""
    m = re.search(rf'^\s*User {user}:(.*)$', dump, re.M)
    if not m:
        return None
    fields = dict(re.findall(r'(\w+)=(\S+)', m.group(1)))
    return {k: fields[k] for k in ('installed', 'hidden', 'suspended', 'stopped', 'enabled') if k in fields}


def runtime(serial, handback, out, expectation, user='current', interactive=False, physical=False, adb_path=None,
            settle=3.0):
    if not handback or not handback.strip():
        raise Fail('runtime needs --handback: the explicit serialized host handback reference from Chief/Infra')
    if expectation is None:
        raise Fail('runtime needs --expect: the exact identities recorded for the build on this guest')
    if user != 'current' and not str(user).isdigit():
        raise Fail('--user must be current or a numeric user id')
    out = outside_repo(out)
    out.mkdir(parents=True, exist_ok=True)
    adb = Adb(serial, adb_path)
    keys = ['ro.product.device', 'ro.product.name', 'ro.hardware', 'ro.build.fingerprint', 'ro.build.type',
            'ro.build.version.sdk', 'ro.build.version.release', 'ro.product.cpu.abi', 'ro.product.cpu.abilist',
            'sys.boot_completed', 'persist.wm.debug.pixel_agent_tray']
    props = {k: adb.shell(f'getprop {k}').strip() for k in keys}
    emulator = props['ro.product.device'].startswith(EMULATOR_DEVICES)
    if not emulator and not physical:
        raise Fail(f'{props["ro.product.device"] or "device"} is not a Cuttlefish guest; pass --physical-device only '
                   'for an owner-approved phone (its evidence stays private)')
    foreground = adb.shell('am get-current-user').strip()
    if not foreground.isdigit():
        raise Fail('the current user is unavailable (am get-current-user); nothing is attributed to an unknown user', 1)
    foreground = int(foreground)
    users = parse_users(adb.shell('pm list users'))
    tested = foreground if user == 'current' else int(user)
    if tested not in [u['id'] for u in users]:
        raise Fail(f'user {tested} is not on the device (pm list users: {[u["id"] for u in users]})')
    binding = {'tested': tested, 'selection': 'current' if user == 'current' else 'explicit --user',
               'mechanism': 'am get-current-user' if user == 'current' else 'explicit id checked against pm list users',
               'foreground': foreground, 'users': users,
               'notTested': [u['id'] for u in users if u['id'] != tested],
               'boundCommands': [f'pm path --user {tested}', 'dumpsys package (User line for the tested user)',
                                 f'cmd role get-role-holders --user {tested}',
                                 f'settings --user {tested} get secure {ROSE_HOME_MARKER}',
                                 f'cmd package resolve-activity --brief --user {tested} -a MAIN -c HOME'],
               'note': 'Every user-scoped observation names the tested user. Other users and profiles are not measured '
                       'and stay unavailable; package versionCode and APK bytes are device-wide.'}
    checks = [check('boot completed', props['sys.boot_completed'] == '1', props['sys.boot_completed']),
              check('agent tray property', props['persist.wm.debug.pixel_agent_tray'] == '1',
                    props['persist.wm.debug.pixel_agent_tray'])]
    if 'device' in expectation['target']:
        checks.append(check('guest device equals expectation target', props['ro.product.device'] == expectation['target']['device'],
                            {'expected': expectation['target']['device'], 'actual': props['ro.product.device']}))
    for other in binding['notTested']:
        checks.append(check(f'user {other} observations', None, f'not tested; only user {tested} was measured'))
    apps = []
    for entry in expectation['apps']:
        pkg = entry['package']
        paths = [line[8:] for line in adb.shell(f'pm path --user {tested} {pkg}').splitlines() if line.startswith('package:')]
        dump = adb.shell(f'dumpsys package {pkg}')
        state = user_package_state(dump, tested)
        versions = sorted({int(v) for v in re.findall(r'versionCode=(\d+)', dump)})
        names = sorted(set(re.findall(r'versionName=(\S+)', dump)))
        flags = re.search(r'pkgFlags=\[([^\]]*)\]', dump)
        digests = {p: adb.shell(f'sha256sum {p}').split()[0] if p else None for p in paths}
        apps.append({'package': pkg, 'user': tested, 'paths': paths, 'userState': state, 'versionCodes': versions,
                     'versionNames': names, 'systemApp': None if flags is None else 'SYSTEM' in flags.group(1).split(),
                     'sha256': digests})
        checks.append(check(f'{pkg} installed for user {tested}', bool(paths) and (state or {}).get('installed') == 'true',
                            {'paths': paths, 'userState': state}))
        checks.append(check(f'{pkg} active versionCode equals expectation (device-wide)', entry['version'] in versions[-1:],
                            {'expected': entry['version'], 'dumpsys': versions}))
        checks.append(check(f'{pkg} active bytes equal expectation (path for the tested user)', bool(paths) and digests[paths[0]] == entry['sha256'],
                            {'expected': entry['sha256'], 'active': digests.get(paths[0]) if paths else None}))
    home = adb.shell(f'cmd package resolve-activity --brief --user {tested} -a android.intent.action.MAIN '
                     '-c android.intent.category.HOME')
    home_component = home.strip().splitlines()[-1] if home.strip() else None
    roles = adb.shell(f'cmd role get-role-holders --user {tested} android.app.role.HOME').strip()
    marker = adb.shell(f'settings --user {tested} get secure {ROSE_HOME_MARKER}').strip()
    result = {'kind': 'runtime-interactive' if interactive else 'runtime-readonly', 'handback': handback.strip(),
              'expectation': expectation_summary(expectation), 'user': binding,
              'device': {'emulator': emulator, 'privateEvidence': not emulator, 'serial': 'not recorded', **props},
              'apps': apps, 'home': {'user': tested, 'unqualifiedResolveActivity': home_component,
                                     'roleHolders': roles.split(';') if roles else [],
                                     ROSE_HOME_MARKER: None if marker in ('', 'null') else marker,
                                     'markerNote': 'Set by builds with os/patches/rose-home-role-permission.patch on '
                                                   'the first start of each full user (once per user; see '
                                                   'docs/OS-INTEGRATION.md). Absent on builds without it.'},
              'checks': checks, 'artifacts': {}}
    checks.append(check(f'HOME role held by Rose Agent for user {tested}', 'dev.pixel.voice' in result['home']['roleHolders'],
                        result['home']['roleHolders']))
    checks.append(check(f'unqualified MAIN/HOME resolves to Rose Agent for user {tested}',
                        home_component == 'dev.pixel.voice/.MainActivity', home_component))
    if interactive and tested != foreground:
        checks.append(check(f'interactive observations for user {tested}', None,
                            f'user {tested} is not the foreground user ({foreground}); switching users is a guest '
                            'mutation this method does not perform'))
    elif interactive:
        steps = []

        def capture(label):
            time.sleep(settle)
            shot = screenshot(adb, out, label)
            nodes = ui_dump(adb, out, label)
            step = {'step': label, 'user': tested, 'focus': focus(adb), 'screenshot': shot.name,
                    'pageDescriptions': sorted({n['desc'] for n in nodes if n['desc'] in ('Home', 'Voice home', 'Widgets')}),
                    'roseEntry': next((n['desc'] for n in nodes if (n['desc'] or '').startswith('Open Rose')), None),
                    'visibleText': sorted({n['text'] for n in nodes if n['text']})[:40]}
            steps.append(step)
            return step, nodes, shot

        def compared(step, shot):
            """The heuristic comparison, or unavailable when this host cannot run it (the PNG is kept for a local
            `compare`). Never a visual verdict on its own."""
            try:
                step['comparison'] = compare(shot, out, label=step['step'])
            except Fail as err:
                if err.code != 4:
                    raise
                step['comparison'] = {'kind': 'screenshot-comparison', 'verdict': 'unavailable', 'reason': str(err),
                                      'screenshot': shot.name, 'note': 'run `compare` on the saved PNG elsewhere'}
            verdict = step['comparison']['verdict']
            checks.append(check(f'{step["step"]} screenshot not black', None if verdict == 'unavailable' else
                                verdict != 'black-screen', verdict))

        rose_main = 'dev.pixel.voice/.MainActivity'
        # 1. The system HOME key: whatever holds HOME for this user (unqualified).
        adb.shell('input keyevent KEYCODE_HOME')
        home_step, _, shot = capture('home-key')
        home_step['entry'] = 'system HOME key (unqualified)'
        compared(home_step, shot)
        checks.append(check('system HOME key shows Rose Home page', home_step['roseEntry'] is not None,
                            {'focus': home_step['focus'], 'pages': home_step['pageDescriptions']}))
        # 2. Rose Home through an explicit component: renders Rose Home whoever holds HOME; never proof of the default.
        adb.shell(f'am start --user {tested} -W -a android.intent.action.MAIN -c android.intent.category.HOME '
                  f'-n {rose_main}')
        explicit, nodes, shot = capture('explicit-rose-home')
        explicit['entry'] = 'explicit component MAIN/HOME (does not prove the HOME default)'
        compared(explicit, shot)
        checks.append(check('explicit Rose HOME shows Rose Home page (explicit component)',
                            explicit['roseEntry'] is not None and rose_main in (explicit['focus'] or ''),
                            {'focus': explicit['focus'], 'pages': explicit['pageDescriptions']}))
        # 3. Rose presence -> conversation, then Back -> Rose Home.
        opened = tap_desc(adb, nodes, 'Open Rose')
        conv, _, _ = capture('home-to-conversation')
        conv['tapped'] = opened
        checks.append(check('Rose entry opens RoseConversationActivity', opened is not None and
                            'RoseConversationActivity' in (conv['focus'] or ''), conv['focus']))
        adb.shell('input keyevent KEYCODE_BACK')
        back, _, _ = capture('conversation-back')
        checks.append(check('Back from the conversation returns to Rose Home', opened is not None and
                            back['roseEntry'] is not None and rose_main in (back['focus'] or ''), back['focus']))
        # 4. The app icon entry (behavior differs by version: run `source` for the rule); recorded, not judged.
        adb.shell(f'am start --user {tested} -W -a android.intent.action.MAIN -c android.intent.category.LAUNCHER '
                  f'-n {rose_main}')
        icon, _, shot = capture('launcher-icon-entry')
        icon['entry'] = 'explicit component MAIN/LAUNCHER (app icon)'
        compared(icon, shot)
        adb.shell('input keyevent KEYCODE_HOME')
        again, _, _ = capture('home-key-again')
        again['entry'] = 'system HOME key (unqualified)'
        after = adb.shell('am get-current-user').strip()
        checks.append(check('foreground user unchanged during interactive capture', after == str(tested),
                            {'before': tested, 'after': after}))
        result['steps'] = steps
    result['ok'] = not any(c['result'] == 'fail' for c in checks)
    result['unmeasured'] = [c['check'] for c in checks if c['result'] == 'unavailable']
    result['method'] = METHOD
    (out/'runtime-receipt.json').write_text(json.dumps(result, indent=1) + '\n')
    return result


# ---------- CLI ----------

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='command', required=True)
    e = sub.add_parser('expected')
    r = sub.add_parser('release')
    r.add_argument('directory')
    r.add_argument('--extract-apps', metavar='DIR')
    r.add_argument('--partition', action='append', help='payload partition to extract (default: the contract app partitions)')
    r.add_argument('--expect-source')
    r.add_argument('--build-tools')
    a = sub.add_parser('apk')
    a.add_argument('apks', nargs='+')
    a.add_argument('--build-tools')
    s = sub.add_parser('source')
    s.add_argument('rev', nargs='?', default='HEAD')
    rt = sub.add_parser('runtime')
    rt.add_argument('--serial', required=True)
    rt.add_argument('--handback', required=True)
    rt.add_argument('--out', required=True)
    rt.add_argument('--user', default='current', help='tested user id, or current (resolved and recorded)')
    rt.add_argument('--interactive', action='store_true')
    rt.add_argument('--physical-device', action='store_true')
    rt.add_argument('--adb')
    c = sub.add_parser('compare')
    c.add_argument('screenshot')
    c.add_argument('--out', required=True)
    c.add_argument('--reference')
    for p in (e, r, a, rt):
        p.add_argument('--expect', metavar='FILE', help='candidate expectation (schema in the module docstring)')
    for p in (r, a, s, rt, c):
        p.add_argument('--receipt', help='also write the JSON result to this new file')
    args = parser.parse_args(argv)
    try:
        expectation = load_expectation(args.expect) if getattr(args, 'expect', None) else None
        if args.command == 'expected':
            result = {'kind': 'expectation', **expectation} if expectation else expected()
        elif args.command == 'release':
            result = release(args.directory, args.extract_apps, args.partition, args.expect_source, args.build_tools,
                             expectation)
        elif args.command == 'apk':
            tools = build_tools(args.build_tools)
            apps = [inspect_apk(tools, p, expectation) for p in args.apks]
            result = {'kind': 'artifact-inspection', 'expectation': expectation_summary(expectation), 'apps': apps,
                      'checks': [c for x in apps for c in x['checks']],
                      'runtime': {'performed': False, 'reason': 'artifact inspection only'}}
            if expectation is None:
                result['note'] = 'No --expect: identity only; nothing was compared with an expected candidate.'
        elif args.command == 'source':
            result = source(args.rev)
        elif args.command == 'runtime':
            result = runtime(args.serial, args.handback, args.out, expectation, args.user, args.interactive,
                             args.physical_device, args.adb)
        else:
            result = compare(args.screenshot, args.out, args.reference, label=Path(args.screenshot).stem)
    except Fail as err:
        print(f'rose-verify: {err}', file=sys.stderr)
        return err.code
    result['method'] = METHOD
    text = json.dumps(result, indent=1) + '\n'
    receipt = getattr(args, 'receipt', None)
    if receipt:
        with open(receipt, 'x') as f:
            f.write(text)
    sys.stdout.write(text)
    failed = [c for c in result.get('checks', []) if c['result'] == 'fail']
    if result.get('verdict') == 'black-screen':
        return 1
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
