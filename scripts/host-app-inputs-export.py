#!/usr/bin/env python3
"""os-release app-inputs-export mode: hand the three signed Rose apps and the background policy of one recorded Frankel
build to another builder (Yogi, or Mustang on its own capacity) without a new Frankel compile.

Run on the Frankel builder by .github/workflows/os-release.yml (mode app-inputs-export) with BUILD_NUMBER set. Nothing
is built or signed and no secret is read. The build must have exactly one committed Frankel allocation record
(roseos.latest-allocation.v1, device frankel, tag frankel-<build>) in os/latest-beta or os/ota. Its candidate, build,
run and app versions are the producer. Copies with identical content count once; records that differ refuse.

Under the builder's shared source/output lock (host.lockPath, non-blocking), and without writing anything in the
original attempt $HOME/rose-actions/frankel-<build>, this checks:
- the attempt's stage receipt chain is the original attempt of that candidate and build, completed through transfer
  (host-app-reuse.attempt_chain);
- release/signed-apps holds exactly what the existing app signing writes for the three apps. That is each app's
  APK, its .receipt.json and its v4 .apk.idsig (mustang-candidate-bundle.app_inputs), each a regular file and never
  a link;
- each receipt matches its APK's bytes, names the pinned app signer and records a verified signature;
- aapt and apksigner, run again here on the retained bytes, read each app's package, the allocation's versionCode,
  and exactly the pinned signer (background_apks.identity);
- release/background-policy.json is a valid background policy pinning exactly those bytes, versions and signer, with
  the pinned SDK and control policy and one target.
The pins (apps, roles, SDK, control policy, app signer) are the already-signed candidate-bundle contract
os/build-only/mustang-reconcile.json candidateInputs, and its signer must equal frankel.json's app signer.

It writes only to the new artifact directory, which must be outside the attempt:
- signed-apps/ with the nine files, copied unchanged and re-hashed;
- background-policy.json;
- app-inputs-export.json, the receipt: producer, allocation record and digest, and per-file names, sizes and sha256;
- SHA256SUMS.
A consumer admits the set only after checking SHA256SUMS and the receipt against the same committed allocation record,
then re-checking the apps and policy with its own tools, as mustang-candidate-bundle.py assemble does. Any refusal
leaves no artifact. Missing configuration fails closed (exit 4) and names the key.

  --check                       report missing builder configuration only
  --artifact-dir DIR            export (BUILD_NUMBER in the environment)
Exit status: 0 exported, 1 refused, 4 configuration missing.
"""
import argparse
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from host_secrets import AdapterFailed, AdapterSetupMissing  # noqa: E402
import background_apks  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT/'os/release-pipeline/frankel.json'
PINS = ROOT/'os/build-only/mustang-reconcile.json'
RECORD_DIRS = ('os/latest-beta', 'os/ota')
ALLOCATION_SCHEMA = 'roseos.latest-allocation.v1'
RECEIPT_SCHEMA = 'roseos.app-inputs-export.v1'
DEVICE = 'frankel'
BUILD = re.compile(r'[1-9][0-9]{9}')
SHA = re.compile(r'[0-9a-f]{40}')
SUFFIXES = ('.apk', '.receipt.json', '.apk.idsig')


def load(name):
    spec = importlib.util.spec_from_file_location(name.replace('-', '_'), ROOT/'scripts'/f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sha256(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()


def missing_inputs(config):
    missing = [f'host.tools.{k}' for k in ('aapt', 'apksigner') if not config['host']['tools'].get(k)]
    return missing + [f'host.{k}' for k in ('lockPath', 'sourceRoot') if not config['host'].get(k)]


def pins(config, path=PINS):
    """The candidate-bundle contract's app pins, whose signer must be the release config's app signer."""
    found = json.loads(Path(path).read_text())['candidateInputs']
    if found['appSignerSha256'].lower() != config['identities']['appSignerSha256'].lower():
        raise AdapterFailed('the candidate-bundle app signer differs from frankel.json identities.appSignerSha256')
    return found


def allocation_record(build, root=ROOT):
    """(repo-relative path, record) of the one committed Frankel allocation for BUILD; identical copies count once."""
    found = []
    for directory in RECORD_DIRS:
        for path in sorted((Path(root)/directory).glob('*allocation*.json')):
            if path.is_symlink() or not path.is_file():
                continue
            try:
                record = json.loads(path.read_text())
            except ValueError:
                continue
            if isinstance(record, dict) and record.get('schema') == ALLOCATION_SCHEMA and record.get('build') == build:
                found.append((str(path.relative_to(root)), record))
    contents = {json.dumps(record, sort_keys=True) for _, record in found}
    if not found:
        raise AdapterFailed(f'no committed {ALLOCATION_SCHEMA} record for build {build} in {list(RECORD_DIRS)}')
    if len(contents) != 1:
        raise AdapterFailed(f'committed allocation records for build {build} differ: {[p for p, _ in found]}')
    path, record = found[0]
    if (record.get('device') != DEVICE or record.get('tag') != f'{DEVICE}-{build}'
            or not SHA.fullmatch(str(record.get('candidateSha', ''))) or not SHA.fullmatch(str(record.get('sourceSha', '')))
            or not isinstance(record.get('apps'), list) or not isinstance(record.get('run'), dict)):
        raise AdapterFailed(f'{path} is not a complete Frankel allocation (device, tag, candidate, source, apps, run)')
    return path, record


def check_set(apps_dir, policy_path, record, pin, tools, identity=background_apks.identity):
    """Every check of the docstring on the retained set; returns the app rows. Nothing is written."""
    bundle = load('mustang-candidate-bundle')
    signer = pin['appSignerSha256']
    packages = [a['package'] for a in pin['apps']]
    versions = {package: version for package, version in record['apps']}
    if sorted(versions) != sorted(packages):
        raise AdapterFailed(f'the allocation pins apps {sorted(versions)}, the contract {sorted(packages)}')
    try:
        bundle.app_inputs(apps_dir, packages)
    except bundle.Refused as error:
        raise AdapterFailed(str(error)) from None
    apps = []
    for package in packages:
        apk, receipt_path = apps_dir/f'{package}.apk', apps_dir/f'{package}.receipt.json'
        digest = sha256(apk)
        try:
            receipt = json.loads(receipt_path.read_text())
        except ValueError:
            raise AdapterFailed(f'{receipt_path.name} is not JSON') from None
        if (not isinstance(receipt, dict) or receipt.get('schemaVersion') != 1 or receipt.get('apk') != apk.name
                or receipt.get('sha256') != digest or receipt.get('certificateSha256') != [signer]
                or receipt.get('signatureVerified') is not True):
            raise AdapterFailed(f'{receipt_path.name} does not match {apk.name} (bytes, signer or verification)')
        try:
            found = identity(apk, tools['aapt'], tools['apksigner'], pin['sdk'])
        except (ValueError, OSError, subprocess.CalledProcessError) as error:
            raise AdapterFailed(f'{apk.name}: identity not verified ({type(error).__name__})') from None
        if (found.get('packageName'), found.get('versionCode'), found.get('certificates')) != \
                (package, versions[package], [signer.upper()]):
            raise AdapterFailed(f'{apk.name} is not {package} {versions[package]} signed by the app signer')
        apps.append({'package': package, 'versionCode': versions[package], 'sha256': digest})
    try:
        written = background_apks.validate_policy(json.loads(Path(policy_path).read_text()),
                                                  apps_dir/'dev.pixel.backgroundcontrol.apk')
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise AdapterFailed(f'background-policy.json is not a valid background policy ({type(error).__name__})') from None
    if written['sdk'] != pin['sdk'] or written.get('prototypeControl', False) is not pin['prototypeControl'] \
            or len(written['targets']) != 1:
        raise AdapterFailed('the policy SDK, control policy or target count differs from the pin')
    rows = {'agent': written['agent'], 'voice': written['voice'], 'target': written['targets'][0]}
    roles = {a['package']: a['role'] for a in pin['apps']}
    for app in apps:
        row = rows[roles[app['package']]]
        if (row['packageName'], row['versionCode'], row['apkSha256'], row['certificates']) != \
                (app['package'], app['versionCode'], app['sha256'], [signer.upper()]):
            raise AdapterFailed(f'the policy does not pin the signed {app["package"]} bytes and version')
    return apps


def export(config, build, base, artifact, tools, root=ROOT, identity=background_apks.identity,
           chain=None, pins_path=PINS):
    """Check, then copy into ARTIFACT and write the receipt. The caller holds the shared lock."""
    pin = pins(config, pins_path)
    path, record = allocation_record(build, root)
    (chain or load('host-app-reuse').attempt_chain)(base, record['candidateSha'], build, DEVICE)
    release = base/'release'
    apps_dir, policy = release/'signed-apps', release/'background-policy.json'
    if policy.is_symlink() or not policy.is_file():
        raise AdapterFailed('release/background-policy.json must be a regular file, not a link')
    apps = check_set(apps_dir, policy, record, pin, tools, identity)
    names = [f'{a["package"]}{suffix}' for a in apps for suffix in SUFFIXES]
    expected = {name: sha256(apps_dir/name) for name in names}
    expected['background-policy.json'] = sha256(policy)
    artifact.mkdir(parents=True, mode=0o755)
    (artifact/'signed-apps').mkdir()
    files = []
    for name, digest in expected.items():
        source = policy if name == 'background-policy.json' else apps_dir/name
        target = artifact/name if name == 'background-policy.json' else artifact/'signed-apps'/name
        shutil.copyfile(source, target)
        if sha256(target) != digest:
            raise AdapterFailed(f'{name} changed while being copied')
        files.append({'path': str(target.relative_to(artifact)), 'sizeBytes': target.stat().st_size, 'sha256': digest})
    receipt = {'schema': RECEIPT_SCHEMA, 'producer': {
                   'device': DEVICE, 'build': build, 'tag': record['tag'], 'candidateSha': record['candidateSha'],
                   'sourceSha': record['sourceSha'], 'run': record['run'],
                   'allocation': {'path': path, 'sha256': sha256(Path(root)/path)}},
               'signerSha256': pin['appSignerSha256'], 'sdk': pin['sdk'],
               'apps': [{'package': a['package'], 'versionCode': a['versionCode'], 'apkSha256': a['sha256']} for a in apps],
               'files': sorted(files, key=lambda f: f['path']),
               'checks': ['the one committed Frankel allocation record of this build names the producer and app versions',
                          'the original attempt receipt chain is complete through transfer for that candidate and build',
                          'exactly the nine files the app signing writes, each a regular file',
                          'each receipt matches its APK bytes, names the app signer and records a verified signature',
                          'aapt and apksigner read each package, the allocation versionCode and exactly the app signer',
                          'the background policy pins exactly these bytes, versions and signer with the pinned SDK',
                          'copied unchanged and re-hashed; not rebuilt or re-signed']}
    (artifact/'app-inputs-export.json').write_text(json.dumps(receipt, indent=2, sort_keys=True) + '\n')
    sums = sorted(files + [{'path': 'app-inputs-export.json', 'sha256': sha256(artifact/'app-inputs-export.json')}],
                  key=lambda f: f['path'])
    (artifact/'SHA256SUMS').write_text(''.join(f'{f["sha256"]}  {f["path"]}\n' for f in sums))
    return receipt


def main(artifact_dir, config_path=CONFIG, env=os.environ):
    config = json.loads(Path(config_path).read_text())
    missing = missing_inputs(config)
    if missing:
        raise AdapterSetupMissing(missing)
    build = env.get('BUILD_NUMBER', '')
    if not BUILD.fullmatch(build):
        raise AdapterFailed('BUILD_NUMBER must be the ten-digit Frankel build to export')
    base = Path(env.get('ROSE_ATTEMPTS', Path.home()/'rose-actions'))/f'{DEVICE}-{build}'
    artifact = Path(artifact_dir)
    if os.path.lexists(artifact):
        raise AdapterFailed(f'{artifact} already exists; use a new artifact directory')
    if artifact.resolve().is_relative_to(base.resolve()):
        raise AdapterFailed('the artifact directory must be outside the original attempt, which is only read')
    host = lambda v: str(Path(v) if Path(v).is_absolute() else Path(config['host']['sourceRoot'])/v)
    tools = {k: host(config['host']['tools'][k]) for k in ('aapt', 'apksigner')}
    lock = open(config['host']['lockPath'], 'a')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock.close()
        raise AdapterFailed('the source/output lock is held by another job')
    try:
        return export(config, build, base, artifact, tools)
    except BaseException:
        shutil.rmtree(artifact, ignore_errors=True)  # never leave a partial artifact to upload
        raise
    finally:
        lock.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--artifact-dir', type=Path)
    parser.add_argument('--check', action='store_true', help='Only report missing configuration')
    a = parser.parse_args()
    try:
        if a.check:
            missing = missing_inputs(json.loads(CONFIG.read_text()))
            if missing:
                raise AdapterSetupMissing(missing)
            sys.exit(0)
        if not a.artifact_dir:
            parser.error('--artifact-dir is required')
        print(json.dumps(main(a.artifact_dir), indent=2, sort_keys=True))
    except AdapterSetupMissing as error:
        print(json.dumps({'verdict': 'SETUP_MISSING', 'missing': error.missing}), file=sys.stderr)
        sys.exit(4)
    except (AdapterFailed, OSError, KeyError, ValueError, subprocess.CalledProcessError) as error:
        print(json.dumps({'verdict': 'FAIL', 'error': str(error) if isinstance(error, AdapterFailed) else error.__class__.__name__}),
              file=sys.stderr)
        sys.exit(1)
