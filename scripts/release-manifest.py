#!/usr/bin/env python3
"""Create and check an OS release candidate manifest; offline, never signs, uploads or downloads.

create          record exact source, target, channel, build and explicitly listed asset bytes
verify          compare a local candidate directory with the manifest and supplied expected pins
verify-published compare a separately downloaded directory with the manifest (no network action)

Identity fields (app signers, OTA certificate, AVB key) are claimed metadata. Matching them against
expected pins is not a cryptographic signature check, and no output says otherwise.
Exit status: 0 pass, 1 verification failed, 2 invalid input.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile

SCHEMA = 1
KIND = 'rose-os-candidate-manifest'
CHANNELS = ('alpha', 'beta', 'prod')
VARIANTS = ('user', 'userdebug', 'eng')
# GitHub release assets are limited to 2 GiB each.
MAX_ASSET_BYTES = 2 * 1024**3
ASSET_NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9._+-]{0,199}')
DEVICE = re.compile(r'[a-z0-9_]{1,64}')
SHA1 = re.compile(r'[0-9a-f]{40}')
SHA256 = re.compile(r'[0-9a-f]{64}')
PACKAGE = re.compile(r'[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)+')
CLAIMED = 'claimed metadata; matched against expected pins, not cryptographically verified'

class InputError(Exception):
    pass

def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()

def positive_int(value, name):
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise InputError(f'{name} must be a positive integer')
    return value

def digest(value, name):
    if not isinstance(value, str) or not SHA256.fullmatch(value.lower()):
        raise InputError(f'{name} must be a full SHA-256 hex digest')
    return value.lower()

def parse_identities(data):
    """Normalise expected or claimed public identities; private material is never accepted here."""
    if not isinstance(data, dict) or set(data) - {'apps', 'otaCertificateSha256', 'avbPublicKeySha256'}:
        raise InputError('identities must contain only apps, otaCertificateSha256, avbPublicKeySha256')
    apps = data.get('apps')
    if not isinstance(apps, list) or not apps:
        raise InputError('identities.apps must list at least one app')
    rows, names = [], set()
    for app in apps:
        if not isinstance(app, dict) or set(app) != {'packageName', 'versionCode', 'signerSha256'}:
            raise InputError('each app needs exactly packageName, versionCode, signerSha256')
        name = app['packageName']
        if not isinstance(name, str) or not PACKAGE.fullmatch(name) or name in names:
            raise InputError('invalid or duplicate app packageName')
        names.add(name)
        rows.append({'packageName': name, 'versionCode': positive_int(app['versionCode'], name+' versionCode'),
            'signerSha256': digest(app['signerSha256'], name+' signerSha256')})
    return {'apps': sorted(rows, key=lambda r: r['packageName']),
        'otaCertificateSha256': digest(data.get('otaCertificateSha256'), 'otaCertificateSha256'),
        'avbPublicKeySha256': digest(data.get('avbPublicKeySha256'), 'avbPublicKeySha256')}

def read_json(path, name):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError) as error:
        raise InputError(f'cannot read {name}: {error.__class__.__name__}')

def metadata_line(build, device, feed_channel):
    # Updater feed line, e.g. "2026102701 1793129847 frankel stable". The feed keeps its legacy
    # "stable" word and <device>-stable filename whatever the semantic channel is.
    return f'{build["number"]} {build["timestamp"]} {device} {feed_channel}'

def write_once(path, document):
    """Atomically create path with the JSON document; refuse to replace any existing file."""
    path = Path(path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (json.dumps(document, indent=2, sort_keys=True) + '\n').encode()
    fd, temporary = tempfile.mkstemp(prefix='.'+path.name+'.', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(temporary, 0o644)
        try:
            os.link(temporary, path)
        except FileExistsError:
            raise InputError(f'{path.name} already exists; receipts are immutable, choose a new path')
    finally:
        os.unlink(temporary)
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)

def asset_record(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise InputError(f'asset is not a regular file: {path.name}')
    if not ASSET_NAME.fullmatch(path.name):
        raise InputError(f'unsafe asset name: {path.name!r}')
    size = path.stat().st_size
    if size > MAX_ASSET_BYTES:
        raise InputError(f'{path.name} exceeds the 2 GiB release asset limit')
    return {'name': path.name, 'size': size, 'sha256': sha256_file(path)}

def create(a):
    if not SHA1.fullmatch(a.source_revision):
        raise InputError('--source-revision must be a full 40-character lowercase commit SHA')
    if not DEVICE.fullmatch(a.device):
        raise InputError('--device must be a lowercase device codename')
    build = {'number': positive_int(a.build_number, '--build-number'),
        'timestamp': positive_int(a.build_timestamp, '--build-timestamp')}
    if a.resolved_manifest.is_symlink() or not a.resolved_manifest.is_file():
        raise InputError('--resolved-manifest must be a regular file')
    resolved = sha256_file(a.resolved_manifest)
    assets = [asset_record(p) for p in a.asset]
    names = [row['name'] for row in assets]
    if len(set(names)) != len(names):
        raise InputError('asset names must be unique')
    metadata_name = f'{a.device}-stable'
    paths = dict(zip(names, a.asset))
    if metadata_name in paths:
        first = Path(paths[metadata_name]).read_text(errors='replace').splitlines()[:1]
        if first != [metadata_line(build, a.device, a.feed_channel)]:
            raise InputError(f'{metadata_name} does not describe this build, device and feed channel')
    manifest = {'schemaVersion': SCHEMA, 'kind': KIND, 'receiptType': 'pre-publication',
        'source': {'revision': a.source_revision, 'resolvedManifestSha256': resolved},
        'target': {'device': a.device, 'variant': a.variant},
        'channel': a.channel,
        'feed': {'metadataAsset': metadata_name if metadata_name in paths else None,
            'feedChannel': a.feed_channel},
        'build': build,
        'assets': sorted(assets, key=lambda r: r['name']),
        'identities': parse_identities(read_json(a.identities, '--identities')),
        'identityStatus': CLAIMED,
        'cryptographicVerification': 'not-performed'}
    if a.created_at:
        manifest['createdAt'] = a.created_at
    write_once(a.output, manifest)
    return {'verdict': 'CREATED', 'manifest': Path(a.output).name,
        'manifestSha256': sha256_file(a.output), 'assets': len(assets)}

def load_manifest(path):
    manifest = read_json(path, 'manifest')
    if not isinstance(manifest, dict) or manifest.get('schemaVersion') != SCHEMA or manifest.get('kind') != KIND:
        raise InputError('unsupported manifest schema')
    try:
        names = [row['name'] for row in manifest['assets']]
        if any(not ASSET_NAME.fullmatch(n) for n in names) or len(set(names)) != len(names):
            raise InputError('manifest asset names are unsafe or duplicated')
        for row in manifest['assets']:
            if isinstance(row['size'], bool) or not isinstance(row['size'], int) or row['size'] < 0:
                raise InputError('manifest asset size must be a non-negative integer')
            digest(row['sha256'], 'manifest asset sha256')
        if not DEVICE.fullmatch(manifest['target']['device']):
            raise InputError('manifest target device is invalid')
        positive_int(manifest['build']['number'], 'manifest build number')
        positive_int(manifest['build']['timestamp'], 'manifest build timestamp')
    except (KeyError, TypeError) as error:
        raise InputError(f'manifest is missing a required field: {error}')
    return manifest

def compare_bytes(manifest, directory):
    """Exact allowlist: every manifest asset present as a regular file with matching size and hash."""
    failures = []
    directory = Path(directory)
    if not directory.is_dir() or directory.is_symlink():
        raise InputError('candidate directory must be a real directory')
    expected = {row['name']: row for row in manifest['assets']}
    present, rejected = {}, set()
    for entry in os.scandir(directory):
        if entry.name not in expected:
            failures.append(f'unexpected entry: {entry.name}')
        elif entry.is_symlink() or not entry.is_file(follow_symlinks=False):
            failures.append(f'not a regular file: {entry.name}')
            rejected.add(entry.name)
        else:
            present[entry.name] = Path(entry.path)
    for name, row in sorted(expected.items()):
        path = present.get(name)
        if path is None:
            if name not in rejected:
                failures.append(f'missing asset: {name}')
        elif path.stat().st_size != row['size']:
            failures.append(f'size mismatch: {name}')
            rejected.add(name)
        elif sha256_file(path) != row['sha256']:
            failures.append(f'sha256 mismatch: {name}')
            rejected.add(name)
    metadata = manifest.get('feed', {}).get('metadataAsset')
    if metadata and metadata in present and metadata not in rejected:
        first = present[metadata].read_text(errors='replace').splitlines()[:1]
        if first != [metadata_line(manifest['build'], manifest['target']['device'], manifest['feed']['feedChannel'])]:
            failures.append(f'{metadata} does not describe the manifest build')
    return sorted(failures)

def compare_identities(claimed, expected):
    failures = []
    claimed_apps = {r['packageName']: r for r in claimed['apps']}
    for app in expected['apps']:
        found = claimed_apps.pop(app['packageName'], None)
        if found is None:
            failures.append(f'missing app identity: {app["packageName"]}')
            continue
        for field in ('versionCode', 'signerSha256'):
            if found[field] != app[field]:
                failures.append(f'{field} mismatch: {app["packageName"]}')
    failures += [f'unexpected app identity: {name}' for name in sorted(claimed_apps)]
    for field in ('otaCertificateSha256', 'avbPublicKeySha256'):
        if claimed[field] != expected[field]:
            failures.append(f'{field} mismatch')
    return failures

def compare_baseline(manifest, baseline):
    """Monotonic update against the currently published feed line (e.g. frankel-stable)."""
    fields = baseline.split()
    if len(fields) != 4 or not fields[0].isdecimal() or not fields[1].isdecimal():
        raise InputError('baseline must be a feed line: BUILD TIMESTAMP DEVICE CHANNEL')
    failures = []
    if fields[2] != manifest['target']['device']:
        failures.append('baseline device differs from manifest target')
    if manifest['build']['number'] <= int(fields[0]):
        failures.append('build number is not newer than the baseline')
    if manifest['build']['timestamp'] <= int(fields[1]):
        failures.append('build timestamp is not newer than the baseline')
    return failures

def verify(a):
    manifest = load_manifest(a.manifest)
    failures = compare_bytes(manifest, a.dir)
    target = manifest['target']
    if target['device'] != a.expected_device:
        failures.append('wrong target device')
    if a.expected_variant and target.get('variant') != a.expected_variant:
        failures.append('wrong target variant')
    if a.expected_channel and manifest.get('channel') != a.expected_channel:
        failures.append('wrong channel')
    try:
        claimed = parse_identities(manifest.get('identities'))
    except InputError as error:
        failures.append(f'claimed identities invalid or missing: {error}')
    else:
        failures += compare_identities(claimed, parse_identities(read_json(a.expected_identities, '--expected-identities')))
    if a.baseline_file:
        try:
            lines = Path(a.baseline_file).read_text().splitlines()
        except (OSError, ValueError) as error:
            raise InputError(f'cannot read --baseline-file: {error.__class__.__name__}')
        failures += compare_baseline(manifest, lines[0] if lines else '')
    report = {'verdict': 'FAIL' if failures else 'PASS', 'failures': failures,
        'manifestSha256': sha256_file(a.manifest), 'receiptType': 'local-verification',
        'monotonicCheck': 'performed' if a.baseline_file else 'not performed: --no-baseline',
        'identityStatus': CLAIMED, 'cryptographicVerification': 'not-performed'}
    if a.receipt:
        write_once(a.receipt, report)
    return report

def verify_published(a):
    manifest = load_manifest(a.manifest)
    if a.source and not a.source.startswith('https://'):
        raise InputError('--source must be an https URL; it is recorded only, never fetched')
    failures = compare_bytes(manifest, a.dir)
    # The manifest is an immutable pre-publication receipt. Delivery is proven by byte identity,
    # so pre-publication flags inside assets (e.g. published=false) are not interpreted here.
    report = {'verdict': 'FAIL' if failures else 'PASS', 'failures': failures,
        'manifestSha256': sha256_file(a.manifest), 'receiptType': 'delivery-verification',
        'source': a.source, 'assets': len(manifest['assets']),
        'note': 'compares a separately downloaded directory; no network action was taken'}
    if a.receipt:
        write_once(a.receipt, report)
    return report

def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest='command', required=True)
    c = sub.add_parser('create', help='Write a new immutable candidate manifest')
    c.add_argument('--source-revision', required=True, help='RoseOS-1 commit the candidate was built from')
    c.add_argument('--resolved-manifest', type=Path, required=True, help='repo manifest -r output used for the build')
    c.add_argument('--device', required=True, help='Target device codename, e.g. frankel')
    c.add_argument('--variant', choices=VARIANTS, required=True)
    c.add_argument('--channel', choices=CHANNELS, required=True, help='Semantic channel; does not rename feed files')
    c.add_argument('--feed-channel', default='stable', help='Channel word in the Updater feed line (default stable)')
    c.add_argument('--build-number', type=int, required=True)
    c.add_argument('--build-timestamp', type=int, required=True, help='ro.build.date.utc of the build')
    c.add_argument('--asset', type=Path, action='append', required=True, help='Explicit asset path (repeatable)')
    c.add_argument('--identities', type=Path, required=True, help='JSON of claimed public app/OTA/AVB identities')
    c.add_argument('--created-at', help='Optional UTC timestamp to record; omitted keeps output deterministic')
    c.add_argument('--output', type=Path, required=True)
    v = sub.add_parser('verify', help='Check a local candidate directory against the manifest and expected pins')
    v.add_argument('manifest', type=Path)
    v.add_argument('--dir', type=Path, required=True)
    v.add_argument('--expected-device', required=True)
    v.add_argument('--expected-variant', choices=VARIANTS)
    v.add_argument('--expected-channel', choices=CHANNELS)
    v.add_argument('--expected-identities', type=Path, required=True, help='JSON of expected public identities')
    baseline = v.add_mutually_exclusive_group(required=True)
    baseline.add_argument('--baseline-file', type=Path, help='Currently published feed file, e.g. frankel-stable')
    baseline.add_argument('--no-baseline', action='store_true', help='Channel has no published build yet')
    v.add_argument('--receipt', type=Path, help='Write the report as a new immutable receipt')
    d = sub.add_parser('verify-published', help='Check a separately downloaded directory against the manifest')
    d.add_argument('manifest', type=Path)
    d.add_argument('--dir', type=Path, required=True)
    d.add_argument('--source', help='Where the bytes were downloaded from (recorded, never fetched)')
    d.add_argument('--receipt', type=Path)
    a = p.parse_args(argv)
    try:
        report = {'create': create, 'verify': verify, 'verify-published': verify_published}[a.command](a)
    except InputError as error:
        print(json.dumps({'verdict': 'INVALID_INPUT', 'error': str(error)}), file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2, sort_keys=True))
    return 1 if report['verdict'] == 'FAIL' else 0

if __name__ == '__main__':
    sys.exit(main())
