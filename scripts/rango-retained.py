#!/usr/bin/env python3
"""Retained Rango signing, verification and separate publication (OCT30-INFRA-S015).

One retained, already-compiled Rango (Pixel 10 Pro Fold) build is signed with the Frankel primitives (host-sign.py,
host_secrets.py) and published as its own pixel-ota release that never becomes Latest. Nothing here compiles, rebuilds
apps, generates or imports keys, runs a bootstrap or moves input between hosts. os/retained-rango/release.json names
every real binding; a null one is unbound and the route refuses before any controller, login or secret access.

  preflight [--input-dir D] [--bootstrap-receipt R]
                       names every unbound binding (exit 4) or refuses a wrong one (exit 1); with D the input receipt,
                       target-files and otatools are validated too; with R the bootstrap receipt is checked
  stage --input-dir D --consumer C
                       validate the local input directory and copy it into a fresh task-owned consumer directory, then
                       revalidate the copy (the only input interface; no remote URL, credential or held route)
  recheck --consumer C revalidate a staged consumer directory (run again immediately before secret access)

Exit status: 0 done, 1 refused (category on stderr), 2 invalid input, 4 setup missing.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import stat
import sys

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT/'os/retained-rango/release.json'
PROFILES_PATH = ROOT/'os/signing-bootstrap/profiles.json'
FRANKEL_PATH = ROOT/'os/release-pipeline/frankel.json'
HEX64 = re.compile(r'[0-9a-f]{64}')
SHA1 = re.compile(r'[0-9a-f]{40}')
VERSION = re.compile(r'[1-9][0-9]{0,9}')
TOOLS = ('generator', 'decrypt-keys', 'avbtool', 'openssl')
CONSUMER_RECEIPT = 'consumer-receipt.json'


class Refused(Exception):
    pass


class SetupMissing(Exception):
    def __init__(self, missing):
        super().__init__(', '.join(missing))
        self.missing = missing


def config():
    return json.loads(CONFIG_PATH.read_text())


def profile(cfg=None):
    return json.loads(PROFILES_PATH.read_text())['profiles'][(cfg or config())['signing']['bootstrapProfile']]


def frankel_identities():
    return json.loads(FRANKEL_PATH.read_text())['identities']


def check_config(cfg):
    """Fixed facts a mutated config cannot change: Rango only, a rango- tag and never Latest."""
    target, publication = cfg.get('target') or {}, cfg.get('publication') or {}
    if target.get('device') != 'rango' or not SHA1.fullmatch(str(target.get('product'))) or not re.fullmatch(r'\d{10}', str(target.get('build'))):
        raise Refused('config_invalid: the retained route is Rango with a pinned product commit and build')
    if publication.get('tag') != 'rango-{build}' or publication.get('latest') is not False:
        raise Refused('config_invalid: Rango publishes only as rango-{build} and never as Latest')
    if (cfg.get('signing') or {}).get('bootstrapProfile') != 'fold-rango-v1':
        raise Refused('config_invalid: Rango signs only with the fold-rango-v1 identities')
    return cfg


# ---------------------------------------------------------------- input receipt

def _artifact(value, name, limit, where, unbound):
    if value is None:
        unbound.append(where)
        return
    if not isinstance(value, dict) or value.get('name') != name or not isinstance(value.get('size'), int) \
            or not 0 < value['size'] <= limit or not HEX64.fullmatch(str(value.get('sha256'))):
        raise Refused(f'input_invalid: {where} must name {name} with its size (at most {limit}) and sha256')


def check_receipt(receipt, cfg=None):
    """A strict, versioned description of one retained Rango build. Returns the unbound fields; refuses wrong ones."""
    cfg = cfg or config()
    target, spec = cfg['target'], cfg['input']
    if not isinstance(receipt, dict) or receipt.get('schema') != spec['receiptSchema']:
        raise Refused('input_invalid: not a retained Rango input receipt')
    if receipt.get('device') != target['device'] or receipt.get('product') != target['product']:
        raise Refused('input_foreign: the receipt names another device or product')
    build = receipt.get('build') or {}
    if build.get('number') != target['build']:
        raise Refused(f'input_foreign: the receipt names build {build.get("number")!r}, not {target["build"]}')
    unbound = []
    if build.get('timestamp') is None:
        unbound.append('input.build.timestamp')
    elif not isinstance(build['timestamp'], int) or not 10**9 <= build['timestamp'] < 10**10:
        raise Refused('input_invalid: build.timestamp must be a ten-digit UTC epoch')
    source = receipt.get('source') or {}
    for key in ('manifestSha256', 'configSha256', 'compileReceiptSha256'):
        if source.get(key) is None:
            unbound.append(f'input.source.{key}')
        elif not HEX64.fullmatch(str(source[key])):
            raise Refused(f'input_invalid: source.{key} must be a sha256')
    overlays = source.get('overlays')
    if overlays is None:
        unbound.append('input.source.overlays')
    elif not isinstance(overlays, list) or not overlays or any(not isinstance(o, dict) or not HEX64.fullmatch(str(o.get('sha256')))
            or not re.fullmatch(r'[\w.-]+(/[\w.-]+)*', str(o.get('path'))) or '..' in str(o.get('path')).split('/') for o in overlays) \
            or len({o['path'] for o in overlays}) != len(overlays):
        raise Refused('input_invalid: source.overlays must list distinct relative paths with sha256')
    if source.get('compileExit') != 0:
        raise Refused('input_invalid: the retained build did not record a successful compile (exit 0)')
    artifacts = receipt.get('artifacts') or {}
    _artifact(artifacts.get('targetFiles'), spec['targetFiles'], spec['limits']['targetFilesBytes'], 'input.artifacts.targetFiles', unbound)
    _artifact(artifacts.get('otatools'), spec['otatools'], spec['limits']['otatoolsBytes'], 'input.artifacts.otatools', unbound)
    apps = receipt.get('apps') or {}
    rose = apps.get('roseApk')
    if rose is None:
        unbound.append('input.apps.roseApk')
    elif not isinstance(rose, dict) or rose.get('packageName') != 'dev.pixel.voice' or not isinstance(rose.get('versionCode'), int) \
            or not HEX64.fullmatch(str(rose.get('sha256'))) or not HEX64.fullmatch(str(rose.get('signerSha256'))):
        raise Refused('input_invalid: apps.roseApk must bind dev.pixel.voice versionCode, sha256 and signer')
    if apps.get('policySha256') is None:
        unbound.append('input.apps.policySha256')
    elif not HEX64.fullmatch(str(apps['policySha256'])):
        raise Refused('input_invalid: apps.policySha256 must be a sha256')
    producer = receipt.get('producer')
    if producer is None:
        unbound.append('input.producer')
    elif not isinstance(producer, dict) or producer.get('route') != 'rango-build-only' or not producer.get('operator') \
            or not producer.get('host'):
        raise Refused('input_foreign: the receipt was not produced by the rango-build-only route')
    return unbound


def _regular(path):
    info = os.lstat(path)
    if not stat.S_ISREG(info.st_mode):
        raise Refused(f'input_unsafe: {Path(path).name} is not a regular file')
    return info


def _hash(path, limit):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    digest, total = hashlib.sha256(), 0
    with os.fdopen(fd, 'rb') as f:
        if not stat.S_ISREG(os.fstat(f.fileno()).st_mode):
            raise Refused(f'input_unsafe: {Path(path).name} is not a regular file')
        for block in iter(lambda: f.read(8 << 20), b''):
            total += len(block)
            if total > limit:
                raise Refused(f'input_drift: {Path(path).name} exceeds its recorded size')
            digest.update(block)
    return total, digest.hexdigest()


def _plain_directory(path):
    path = Path(path)
    if not path.is_absolute() or os.path.realpath(path) != str(path) or not path.is_dir():
        raise Refused('input_unsafe: the input directory must be a plain absolute directory')
    return path


def check_directory(directory, cfg=None):
    """Exactly the receipt, target-files and otatools, each a regular file matching the receipt's size and hash."""
    cfg = cfg or config()
    spec = cfg['input']
    directory = _plain_directory(directory)
    names = sorted(os.listdir(directory))
    if names != sorted([spec['receiptFile'], spec['targetFiles'], spec['otatools']]):
        raise Refused('input_inventory: the directory must hold exactly the receipt, target-files and otatools')
    _regular(directory/spec['receiptFile'])
    fd = os.open(directory/spec['receiptFile'], os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd) as f:
        try:
            receipt = json.load(f)
        except ValueError:
            raise Refused('input_invalid: unreadable receipt') from None
    unbound = check_receipt(receipt, cfg)
    if any(name.startswith('input.artifacts.') for name in unbound):
        raise SetupMissing(unbound)
    observed = {}
    for key in ('targetFiles', 'otatools'):
        expected = receipt['artifacts'][key]
        _regular(directory/expected['name'])
        size, digest = _hash(directory/expected['name'], expected['size'])
        if (size, digest) != (expected['size'], expected['sha256']):
            raise Refused(f'input_drift: {expected["name"]} differs from the receipt')
        observed[expected['name']] = digest
    receipt_sha = hashlib.sha256((directory/spec['receiptFile']).read_bytes()).hexdigest()
    return receipt, unbound, {'receiptSha256': receipt_sha, 'files': observed}


def stage(input_dir, consumer, cfg=None):
    """Validate, copy into a fresh owner-only consumer directory, and revalidate the copy (never the source again)."""
    cfg = cfg or config()
    receipt, unbound, before = check_directory(input_dir, cfg)
    consumer = Path(consumer)
    if consumer.exists() or consumer.is_symlink():
        raise Refused('consumer_exists: the consumer directory must be fresh')
    consumer.mkdir(mode=0o700)
    data = consumer/'input'
    data.mkdir(mode=0o700)
    for name in os.listdir(input_dir):
        src = os.open(Path(input_dir)/name, os.O_RDONLY | os.O_NOFOLLOW)
        dst = os.open(data/name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(src, 'rb') as s, os.fdopen(dst, 'wb') as d:
            shutil.copyfileobj(s, d, 8 << 20)
    _, _, after = check_directory(data, cfg)
    if after != before:
        raise Refused('input_drift: the staged copy differs from the validated input')
    record = dict(after, unbound=unbound, build=receipt['build'], device=receipt['device'], product=receipt['product'])
    fd = os.open(consumer/CONSUMER_RECEIPT, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as f:
        json.dump(record, f, indent=2, sort_keys=True)
    return record


def recheck(consumer, cfg=None):
    consumer = Path(consumer)
    try:
        fd = os.open(consumer/CONSUMER_RECEIPT, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd) as f:
            recorded = json.load(f)
    except (OSError, ValueError):
        raise Refused('consumer_unavailable: no staged consumer receipt') from None
    receipt, unbound, observed = check_directory(consumer/'input', cfg)
    if {k: recorded.get(k) for k in observed} != observed:
        raise Refused('input_drift: the consumer directory changed after staging')
    return receipt, unbound, observed


# ---------------------------------------------------------------- signer and tool bindings

def signer_binding(bootstrap_receipt, prof=None, frankel=None):
    """Exact secret versions and public identities from a genuine completed fold-rango-v1 bootstrap receipt; never a
    placeholder and never the Frankel identity."""
    prof, frankel = prof or profile(), frankel or frankel_identities()
    receipt = bootstrap_receipt
    problems = []
    if not isinstance(receipt, dict) or receipt.get('profile') != 'fold-rango-v1' or receipt.get('device') != 'rango' \
            or receipt.get('status') != 'complete' or receipt.get('recoveryNeeded'):
        raise Refused('signer_unbound: not a complete fold-rango-v1 bootstrap receipt')
    versions = {}
    for role, name in prof['secrets'].items():
        entry = (receipt.get('secrets') or {}).get(role) or {}
        if entry.get('name') != name or entry.get('state') != 'committed' or not VERSION.fullmatch(str(entry.get('committedVersion'))):
            problems.append(f'{role} secret {name} has no committed exact version')
        else:
            versions[role] = str(entry['committedVersion'])
    identity = receipt.get('publicIdentity') or {}
    for key in ('otaCertificateSha256', 'avbPublicKeySha256'):
        if not HEX64.fullmatch(str(identity.get(key))):
            problems.append(f'publicIdentity.{key} missing')
    if (receipt.get('cleanup') or {}).get('workspaceRemoved') is not True:
        problems.append('bootstrap cleanup not confirmed')
    if problems:
        raise Refused('signer_unbound: ' + '; '.join(problems))
    if identity['otaCertificateSha256'] == frankel.get('otaCertificateSha256') or identity['avbPublicKeySha256'] == frankel.get('avbPublicKeySha256'):
        raise Refused('signer_collision: the Rango public identity equals the Frankel identity')
    return {'secrets': dict(prof['secrets']), 'versions': versions, 'otaCertificateSha256': identity['otaCertificateSha256'],
        'avbPublicKeySha256': identity['avbPublicKeySha256'], 'receiptSha256': hashlib.sha256(json.dumps(receipt, sort_keys=True).encode()).hexdigest()}


def tool_bindings(prof=None):
    """The four installed signing tools stay unbound until genuinely observed (source hashes are not installed identities)."""
    prof = prof or profile()
    return [f'signing.tools.{name}.installed' for name in TOOLS
        if not ((prof['tools'].get(name) or {}).get('installed') or {}).get('path')
        or not HEX64.fullmatch(str(((prof['tools'].get(name) or {}).get('installed') or {}).get('sha256')))]


def preflight(input_dir=None, bootstrap_receipt=None, cfg=None):
    """Every unbound binding of the route, before any controller, login or secret. Wrong values refuse instead."""
    cfg = check_config(cfg or config())
    missing = []
    if cfg['input'].get('transport') is None:
        missing.append('input.transport')
    if input_dir:
        try:
            _, unbound, _ = check_directory(input_dir, cfg)
        except SetupMissing as error:
            unbound = error.missing
        missing += unbound
    else:
        missing.append('input.directory')
    receipt = bootstrap_receipt if bootstrap_receipt is not None else cfg['signing'].get('bootstrapReceipt')
    if receipt is None:
        missing.append('signing.bootstrapReceipt')
    else:
        signer_binding(receipt)
    missing += tool_bindings()
    for key in ('otatoolsProvenance', 'releaseScriptProvenance'):
        if cfg['signing'].get(key) is None:
            missing.append(f'signing.{key}')
    for key in ('feedChannel', 'notes'):
        if cfg['publication'].get(key) is None:
            missing.append(f'publication.{key}')
    if cfg['verification'].get('expected') is None:
        missing.append('verification.expected')
    return list(dict.fromkeys(missing))


def jdk_home():
    trust = json.loads(PROFILES_PATH.read_text())['profiles']['models-v0']['tools']['keytool']['trust']
    return str(Path(trust['root'])/trust['treePath'])


def sign_inputs(env):
    """host-sign.py's retained route: the bound config and the revalidated consumer inputs, or a refusal."""
    cfg = check_config(config())
    consumer = env.get('ROSE_RETAINED_INPUT', '')
    try:
        bootstrap = json.loads(Path(env.get('ROSE_RETAINED_BOOTSTRAP_RECEIPT', '')).read_text())
    except (OSError, ValueError):
        bootstrap = None
    missing = preflight(Path(consumer)/'input' if consumer else None, bootstrap, cfg)
    if missing:
        raise SetupMissing(missing)
    receipt, _, _ = recheck(consumer, cfg)
    binding = signer_binding(bootstrap)
    if env.get('BUILD_NUMBER') != cfg['target']['build'] or env.get('BUILD_DATETIME') != str(receipt['build']['timestamp']):
        raise Refused('input_foreign: the signing build number and BUILD_DATETIME are not the retained build')
    bound = {'target': {'device': 'rango'}, 'identities': {'otaCertificateSha256': binding['otaCertificateSha256'],
            'avbPublicKeySha256': binding['avbPublicKeySha256']},
        'host': {'sign': {'project': profile(cfg)['project'], 'keysSecret': binding['secrets']['keys'],
            'passphraseSecret': binding['secrets']['passphrase'], 'secretVersion': binding['versions']['keys'],
            'passphraseSecretVersion': binding['versions']['passphrase'], 'otatools': cfg['input']['otatools'],
            'javaHome': jdk_home(), 'updateNotes': cfg['publication']['notes']}},
        'publication': {'channels': {'beta': {'feedChannel': cfg['publication']['feedChannel']}}}}
    data = Path(consumer)/'input'
    return bound, data/cfg['input']['targetFiles'], data/cfg['input']['otatools']


# ---------------------------------------------------------------- separate publication (never Latest)

def publish_separate(client, cfg, build, body, manifest, assets_dir, download_dir, promote=False):
    """A verified, download-checked draft becomes a public release that never changes Latest: make_latest is always
    false, an explicit promote refuses, and Latest must be the same release before and after."""
    cfg = check_config(cfg)
    spec = importlib.util.spec_from_file_location('github_release_publisher_for_rango', ROOT/'scripts/github_release_publisher.py')
    publisher = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(publisher)
    if promote:
        raise Refused('latest_refused: a Rango release is never promoted to Latest')
    if build != cfg['target']['build']:
        raise Refused('input_foreign: only the retained build is published')
    tag = cfg['publication']['tag'].format(build=build)
    if any(not row['name'].startswith('rango-') for row in manifest['assets']
           if row['name'] not in ('releasekey.x509.pem', 'avb_pkmd.bin', 'SHA256SUMS', 'UPDATE.md', 'verification.json')):
        raise Refused('asset_foreign: device assets must be rango-prefixed')
    if client.find_release(tag) is not None:
        raise Refused(f'release_exists: {tag} already exists; it is not reconciled or overwritten')
    before = client.latest_tag()
    release, download, already = publisher.publish_release(client, tag, tag, body, manifest, assets_dir, download_dir)
    if not already:
        client.finalize_public(release['id'])
    after = client.latest_tag()
    if after != before:
        raise Refused(f'latest_changed: Latest moved from {before} to {after}')
    return {'tag': tag, 'releaseId': release['id'], 'latestBefore': before, 'latestAfter': after, 'downloadChecked': str(download)}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split('\n', 1)[0])
    sub = p.add_subparsers(dest='command', required=True)
    f = sub.add_parser('preflight')
    f.add_argument('--job', choices=['guard', 'builder'], default='guard')
    f.add_argument('--input-dir')
    f.add_argument('--bootstrap-receipt')
    s = sub.add_parser('stage')
    s.add_argument('--input-dir', required=True)
    s.add_argument('--consumer', required=True)
    r = sub.add_parser('recheck')
    r.add_argument('--consumer', required=True)
    args = p.parse_args(argv)
    try:
        if args.command == 'preflight':
            receipt = json.loads(Path(args.bootstrap_receipt).read_text()) if args.bootstrap_receipt else None
            missing = preflight(args.input_dir, receipt)
            if missing:
                raise SetupMissing(missing)
            result = {'preflight': 'ready'}
        elif args.command == 'stage':
            result = stage(args.input_dir, args.consumer)
        else:
            _, unbound, observed = recheck(args.consumer)
            result = dict(observed, unbound=unbound)
    except SetupMissing as error:
        print(json.dumps({'verdict': 'SETUP_MISSING', 'missing': error.missing}), file=sys.stderr)
        return 4
    except Refused as error:
        print(json.dumps({'verdict': 'FAIL', 'error': str(error)}), file=sys.stderr)
        return 1
    except (OSError, ValueError) as error:
        print(json.dumps({'verdict': 'INVALID', 'error': type(error).__name__}), file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    sys.exit(main())
