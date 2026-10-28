#!/usr/bin/env python3
"""Public Rose Agent publication for os-release.yml app-only mode (INFRA-APP-096, APR024).

Runs only in the hosted app-publisher job, after the builder's private app-release artifact was transferred with the
workflow's own artifact steps; the pixel-ota installation token is minted in that job and never reaches the builder.
Before any API call it verifies the artifact: exactly the APK, app-release-receipt.json and SHA256SUMS, every digest,
a non-simulated receipt for this candidate and the dispatched [["dev.pixel.voice", N]] (N above appRelease.mustExceed,
and a reserved N only for the app-only route and its candidate), the APK's size and sha256, the receipt's signer, and
the APK's own v2/v3 signing certificate read from its signing block, all equal to the pinned app signer.

The public receipt is the app's direct-GitHub update manifest (APP-GITHUB-002-SCHEMA-001): schemaVersion 1 and a
compatibility object. deviceCodenames, minOsBuild and minControllerVersion are the declared support policy in
appRelease.compatibility. minSdk is the builder's measurement of the signed APK (equal to the source), and abis come from
the APK's own lib/<abi>/*.so entries, re-read here and equal to the builder's: ["universal"] only when there are none,
otherwise exactly those ABIs, and an ABI the app does not know fails.

It then publishes only an allowlist to its own versioned prerelease (appRelease.publication: tag rose-agent-N, asset
dev.pixel.voice-N.apk, a sanitized public receipt and SHA256SUMS), never Latest: OTA Latest must be the configured
release before and after. A published release is never modified: identical assets are an idempotent no-op, anything
else fails. A draft left by an earlier attempt is reconciled. Every asset is downloaded back with authentication and
the APK once more anonymously from the exact public URL the client uses; each must match. Errors name only the
method, endpoint template and status; response bodies and the token are never printed.
Exit status: 0 ok, 1 check or publication failed, 4 setup missing.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import struct
import sys
import tempfile
import zipfile
sys.path.insert(0, str(Path(__file__).resolve().parent))
import github_release_publisher as gp  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT/'os/release-pipeline/frankel.json'
RECEIPT, SUMS = 'app-release-receipt.json', 'SHA256SUMS'
SIGNATURE_SCHEMES = (0x7109871A, 0xF05368C0)  # APK Signature Scheme v2, v3
PUBLIC_FIELDS = ('packageName', 'versionCode', 'versionName', 'size', 'sha256', 'signerSha256', 'candidate', 'minimumTestedOs')
SCHEMA_VERSION = 1  # APP-GITHUB-002-SCHEMA-001, read by the app's DirectUpdate
KNOWN_ABIS = ('arm64-v8a', 'armeabi-v7a', 'x86_64', 'x86')
UNIVERSAL = 'universal'

class CheckFailed(Exception):
    pass

def sha256(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()

def length_prefixed(data, offset):
    if offset + 4 > len(data):
        raise CheckFailed('truncated APK signing block')
    size = struct.unpack_from('<I', data, offset)[0]
    if offset + 4 + size > len(data):
        raise CheckFailed('truncated APK signing block')
    return data[offset + 4:offset + 4 + size], offset + 4 + size

def sequence(data):
    items, offset = [], 0
    while offset < len(data):
        item, offset = length_prefixed(data, offset)
        items.append(item)
    return items

def apk_signer_digests(path):
    """sha256 of the first certificate of each v2 (else v3) signer, read from the APK signing block. This is the
    signer identity apksigner prints; signature validity was established by the builder's apksigner verify."""
    data = Path(path).read_bytes()
    eocd = data.rfind(b'PK\x05\x06')
    if eocd < 0 or eocd + 22 > len(data):
        raise CheckFailed('the APK has no ZIP end of central directory')
    directory = struct.unpack_from('<I', data, eocd + 16)[0]
    if directory < 32 or data[directory - 16:directory] != b'APK Sig Block 42':
        raise CheckFailed('the APK has no APK signing block')
    size = struct.unpack_from('<Q', data, directory - 24)[0]
    start = directory - size - 8
    if start < 0 or struct.unpack_from('<Q', data, start)[0] != size:
        raise CheckFailed('the APK signing block is malformed')
    pairs, offset, blocks = data[start + 8:directory - 24], 0, {}
    while offset < len(pairs):
        if offset + 12 > len(pairs):
            raise CheckFailed('the APK signing block is malformed')
        length, block_id = struct.unpack_from('<QI', pairs, offset)
        if length < 4 or offset + 8 + length > len(pairs):
            raise CheckFailed('the APK signing block is malformed')
        blocks[block_id] = pairs[offset + 12:offset + 8 + length]
        offset += 8 + length
    scheme = next((s for s in SIGNATURE_SCHEMES if s in blocks), None)
    if scheme is None:
        raise CheckFailed('the APK has no v2 or v3 signature')
    digests = []
    signers, _ = length_prefixed(blocks[scheme], 0)
    for signer in sequence(signers):
        signed, _ = length_prefixed(signer, 0)
        _, offset = length_prefixed(signed, 0)  # digests
        certificates, _ = length_prefixed(signed, offset)
        certs = sequence(certificates)
        if not certs:
            raise CheckFailed('an APK signer has no certificate')
        digests.append(hashlib.sha256(certs[0]).hexdigest())
    if not digests:
        raise CheckFailed('the APK signature has no signer')
    return digests

def names(config, version):
    publication, package = config['appRelease']['publication'], config['appRelease']['packageName']
    tag = publication['tag'].format(versionCode=version)
    asset = publication['asset'].format(package=package, versionCode=version)
    return tag, asset, publication['apkUrl'].format(tag=tag, asset=asset)

def verify_artifact(directory, config, candidate, apps):
    """The builder's private artifact, checked completely before any API call; returns (receipt, apk path)."""
    directory, app = Path(directory), config['appRelease']
    package, signer = app['packageName'], config['identities']['appSignerSha256'].lower()
    if not re.fullmatch(r'[0-9a-f]{40}', candidate or ''):
        raise CheckFailed('a full candidate SHA is required')
    if len(apps) != 1 or apps[0][0] != package or type(apps[0][1]) is not int or apps[0][1] <= app['mustExceed']:
        raise CheckFailed(f'app publication takes exactly [["{package}", N]] with N above {app["mustExceed"]}')
    version = apps[0][1]
    reserved = app.get('reserved', [])
    row = next((r for r in reserved if r['versionCode'] == version), None)
    if row and (row['route'] != 'app-only' or row['candidate'] not in (None, candidate)):
        raise CheckFailed(f'{package} versionCode {version} is reserved for the {row["route"]} route'
            + (f' and candidate {row["candidate"]}' if row['candidate'] else ''))
    if not row and reserved and version <= max(r['versionCode'] for r in reserved):
        raise CheckFailed(f'{package} versionCode {version} must exceed the reserved {max(r["versionCode"] for r in reserved)}')
    _, asset, _ = names(config, version)
    present = sorted(p.name for p in directory.iterdir())
    if present != sorted([asset, RECEIPT, SUMS]):
        raise CheckFailed(f'the app artifact holds {present}, not exactly {asset}, {RECEIPT} and {SUMS}')
    sums = {}
    for line in (directory/SUMS).read_text().splitlines():
        digest, _, name = line.partition('  ')
        sums[name] = digest
    if sorted(sums) != sorted([asset, RECEIPT]) or any(sha256(directory/n) != d for n, d in sums.items()):
        raise CheckFailed('SHA256SUMS does not cover exactly the APK and the receipt with matching digests')
    receipt = json.loads((directory/RECEIPT).read_text())
    apk = directory/asset
    expected = {'receiptType': 'app-release', 'simulated': False, 'candidate': candidate, 'packageName': package,
        'versionCode': version, 'apk': asset, 'size': apk.stat().st_size, 'sha256': sha256(apk), 'signerSha256': signer,
        'minimumTestedOs': app['minimumTestedOs']}
    wrong = [f'{k}={receipt.get(k)!r}' for k, v in expected.items() if receipt.get(k) != v]
    if wrong:
        raise CheckFailed('the app receipt does not match: ' + ', '.join(wrong))
    if not isinstance(receipt.get('versionName'), str) or not receipt['versionName']:
        raise CheckFailed('the app receipt has no versionName')
    if apk_signer_digests(apk) != [signer]:
        raise CheckFailed('the APK is not signed by exactly the pinned app signer')
    compatibility(receipt, apk, config)
    return receipt, apk

def native_abis(apk):
    with zipfile.ZipFile(apk) as archive:
        return sorted({n.split('/')[1] for n in archive.namelist() if re.fullmatch(r'lib/[^/]+/.+\.so', n)})

def compatibility(receipt, apk, config):
    """The public compatibility object: declared policy plus the APK's measured minSdk and native-library ABIs."""
    policy = config['appRelease'].get('compatibility') or {}
    if policy.get('schemaVersion') != SCHEMA_VERSION:
        raise CheckFailed(f'appRelease.compatibility is not schemaVersion {SCHEMA_VERSION}')
    codenames, os_build, controller = policy.get('deviceCodenames'), policy.get('minOsBuild'), policy.get('minControllerVersion', 'absent')
    if not isinstance(codenames, list) or not codenames or len(set(codenames)) != len(codenames) \
            or not all(isinstance(c, str) and re.fullmatch(r'[a-z0-9_]{1,32}', c) for c in codenames):
        raise CheckFailed('appRelease.compatibility.deviceCodenames must be a non-empty list of distinct device codenames')
    if not isinstance(os_build, str) or not re.fullmatch(r'[0-9]{10}', os_build):
        raise CheckFailed('appRelease.compatibility.minOsBuild must be a ten-digit build number')
    if not (controller is None or (type(controller) is int and controller >= 1)):
        raise CheckFailed('appRelease.compatibility.minControllerVersion must be a positive integer or null')
    facts = receipt.get('apkFacts') or {}
    if type(facts.get('minSdk')) is not int or not 1 <= facts['minSdk'] <= 1000:
        raise CheckFailed('the app receipt has no measured minSdk')
    abis = native_abis(apk)
    if facts.get('nativeAbis') != abis:
        raise CheckFailed(f'the APK holds native libraries for {abis}, not the receipt\'s {facts.get("nativeAbis")}')
    unknown = [a for a in abis if a not in KNOWN_ABIS]
    if unknown:
        raise CheckFailed(f'the APK has native libraries for ABIs the app cannot match: {unknown}')
    return {'deviceCodenames': codenames, 'abis': abis or [UNIVERSAL], 'minSdk': facts['minSdk'], 'minOsBuild': os_build,
        'minControllerVersion': controller}

def public_files(receipt, apk, config, out):
    """The allowlisted public assets: the APK, a receipt holding only PUBLIC_FIELDS, where it is published, the schema
    version and compatibility, and SHA256SUMS over both. Nothing else (no paths, checks text, configuration or identifiers) leaves the run."""
    tag, asset, url = names(config, receipt['versionCode'])
    publication = config['appRelease']['publication']
    out = Path(out)
    out.mkdir(parents=True)
    (out/asset).write_bytes(apk.read_bytes())
    document = {'receiptType': 'app-publication', 'schemaVersion': SCHEMA_VERSION, **{k: receipt[k] for k in PUBLIC_FIELDS},
        'repository': publication['repository'], 'tag': tag, 'asset': asset, 'apkUrl': url, 'prerelease': True,
        'compatibility': compatibility(receipt, apk, config)}
    public_receipt = asset[:-len('.apk')] + '.receipt.json'
    (out/public_receipt).write_text(json.dumps(document, indent=2, sort_keys=True) + '\n')
    (out/SUMS).write_text(''.join(f'{sha256(out/n)}  {n}\n' for n in sorted([asset, public_receipt])))
    return document, {n: (sha256(out/n), (out/n).stat().st_size) for n in (asset, public_receipt, SUMS)}

def matches(asset, expected):
    digest, size = expected
    return asset.get('state', 'uploaded') == 'uploaded' and asset.get('size') == size and asset.get('digest') in (None, 'sha256:' + digest)

def publish(client, config, document, files, directory, download_dir):
    """Own versioned prerelease holding exactly `files`; Latest is checked before and after. Returns the release."""
    publication = config['appRelease']['publication']
    tag, name = document['tag'], f'Rose Agent {document["versionCode"]} ({document["versionName"]})'
    if client.latest_tag() != publication['preserveLatest']:
        raise CheckFailed(f'Latest is not {publication["preserveLatest"]}; nothing is published')
    body = (f'Rose Agent {document["versionName"]} for {document["packageName"]}, versionCode {document["versionCode"]}, '
        f'sha256 {document["sha256"]}, signer {document["signerSha256"]}, from RoseOS-1 {document["candidate"]}. '
        'Prerelease: an app update channel, not an OS release. Rose Agent reads the receipt asset to decide eligibility and '
        'verifies the APK against it before installing.')
    release = client.find_release(tag)
    if release and not release.get('draft'):
        assets = {a['name']: a for a in release.get('assets', [])}
        if not release.get('prerelease') or set(assets) != set(files) or not all(matches(assets[n], files[n]) for n in files):
            raise CheckFailed(f'published release {tag} differs from this app release; it is not modified')
    else:
        if release is None:
            release = client.create_prerelease_draft(tag, name, body)
        for asset in release.get('assets', []):
            if asset['name'] not in files or not matches(asset, files[asset['name']]):
                client.delete_asset(asset['id'])
        present = {a['name'] for a in client.get_release(release['id']).get('assets', [])}
        for asset_name in sorted(set(files) - present):
            client.upload(release['id'], Path(directory)/asset_name)
        release = client.get_release(release['id'])
        assets = {a['name']: a for a in release.get('assets', [])}
        if set(assets) != set(files) or not all(matches(assets[n], files[n]) for n in files):
            raise CheckFailed('draft assets differ from this app release after upload')
        release = client.publish_prerelease(release['id'])
    release = client.get_release(release['id'])
    if release.get('draft') or not release.get('prerelease') or release.get('tag_name') != tag:
        raise CheckFailed(f'{tag} is not a published prerelease')
    if client.latest_tag() != publication['preserveLatest']:
        raise CheckFailed(f'Latest moved away from {publication["preserveLatest"]}')
    download_dir = Path(download_dir)
    download_dir.mkdir(parents=True)
    assets = {a['name']: a for a in release.get('assets', [])}
    for asset_name, (digest, size) in sorted(files.items()):
        client.download(assets[asset_name]['id'], download_dir/asset_name, size)
        if sha256(download_dir/asset_name) != digest:
            raise CheckFailed(f'downloaded {asset_name} does not match this app release')
    public = download_dir/('public-' + document['asset'])
    client.public_download(document['apkUrl'], public, document['size'])
    if sha256(public) != document['sha256']:
        raise CheckFailed('the APK at the public URL does not match this app release')
    return release

def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--artifact-dir', type=Path, required=True)
    p.add_argument('--candidate-sha', required=True)
    p.add_argument('--apps', required=True)
    p.add_argument('--public-dir', type=Path, required=True, help='new directory for the public files and receipt')
    p.add_argument('--config', type=Path, default=CONFIG)
    a = p.parse_args(argv)
    try:
        config = json.loads(a.config.read_text())
        receipt, apk = verify_artifact(a.artifact_dir, config, a.candidate_sha, json.loads(a.apps))
        document, files = public_files(receipt, apk, config, a.public_dir/'assets')
        token = os.environ.get('PIXEL_OTA_TOKEN', '')
        if not token:
            print(json.dumps({'verdict': 'SETUP_MISSING', 'missing': ['PIXEL_OTA_TOKEN (minted by the app-publisher job)']}), file=sys.stderr)
            return 4
        publication = config['publication']
        client = gp.GitHubReleases(config['appRelease']['publication']['repository'], token, publication.get('api', 'https://api.github.com'),
            publication.get('uploads', 'https://uploads.github.com'), web=publication.get('web', 'https://github.com'), allow_http=bool(config.get('fixture')))
        with tempfile.TemporaryDirectory(dir=os.environ.get('RUNNER_TEMP')) as work:
            release = publish(client, config, document, files, a.public_dir/'assets', Path(work)/'downloaded')
        (a.public_dir/'publication.json').write_text(json.dumps({**document, 'releaseId': release['id']}, indent=2, sort_keys=True) + '\n')
    except (CheckFailed, gp.PublishError) as error:
        print(json.dumps({'verdict': 'FAIL', 'error': str(error)}), file=sys.stderr)
        return 1
    except (OSError, ValueError, KeyError) as error:
        print(json.dumps({'verdict': 'FAIL', 'error': error.__class__.__name__}), file=sys.stderr)
        return 1
    print(json.dumps(document, indent=2, sort_keys=True))
    return 0

if __name__ == '__main__':
    sys.exit(main())
