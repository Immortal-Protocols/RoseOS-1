#!/usr/bin/env python3
"""Yogi (Pixel 11 Pro Fold) OTA route on its own dedicated builder: build, sign, stage and publish.

Separate from os-release.yml, release-pipeline.py and every Mustang/Frankel/Rango/Blazer path. It reuses the
existing machinery by import or subprocess, unchanged: host-reconcile.py (Rose integration), host-sign.py (the
pinned GrapheneOS release script with an explicit key set), the device-agnostic OTA checks of mustang_ota.py and
github_release_publisher.py. Every runtime stage is refused until os/ota/yogi.json records the prerequisite it
needs (registered runner, approved Yogi signing window, Yogi bootstrap receipt, bound app inputs, publication).

Hosted:   guard --stage bootstrap|ota|publish [--access] | builder state|start|stop | verify-staged | publish
Builder:  preflight | allocate | prepare | integrate | compile | sign | install-image | stage

bootstrap (build A) compiles and signs the first Yogi-key image with the owned Updater and writes a private fastboot
install image. The proposed supported design installs it once with a WIPE (userdata and metadata), separately
authorized by the phone owner: the lab artifact packages no Updater and uses public development keys, while its
installed OTA trust and any other transition path remain uninspected. ota (build B and later) compiles, signs, checks
and stages a full A/B OTA newer than the bootstrap, signed by the same Yogi identity, for publication as yogi-{build}.
No key generation, IAM, phone action or automatic trigger. Exit status: 0 ok, 1 refused or failed, 2 usage.
"""
import argparse
import datetime
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
import tempfile
import time
import xml.etree.ElementTree as ET
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'scripts'))
import background_apks  # noqa: E402
import github_release_publisher as publisher  # noqa: E402
import host_secrets  # noqa: E402
import mustang_ota as ota_checks  # noqa: E402  (device-agnostic helpers only; Mustang behaviour is not changed)
import release_receipts  # noqa: E402

DEVICE = 'yogi'
CONFIG = ROOT/'os/ota/yogi.json'
ROUTE = ROOT/'os/build-only/yogi-updater-route.json'
TEMPLATE = ROOT/'os/build-only/yogi-reconcile.json'
SOURCE = ROOT/'os/fold-port/source.json'
REPOSITORY = 'Immortal-Protocols/pixel-ota'
INSTANCE = 'roseos-yogi-builder-20261103-a'
RUNNER_LABEL = 'roseos-yogi-builder'
PROFILE = 'yogi-v1'
SECRETS = {'keys': 'PIXEL_OS_SIGNING_KEYS_YOGI_V1', 'passphrase': 'PIXEL_OS_SIGNING_PASSPHRASE_YOGI_V1'}
SIGNER = 'roseos-build-signer@protocols-465723.iam.gserviceaccount.com'
LAB_PINS = {'7728e30f50bfa5cea165f473175a08803f6a8346642b5aa10913e9d9e6defef6',
            'a40da80a59d170caa950cf15c18c454d47a39b26989d8b640ecd745ba71bf5dc'}
STAGES = ('bootstrap', 'ota', 'publish')
SDK = 37
LUNCH = 'yogi-cur-user'
GOALS = ('target-files-package', 'otatools-package')
TARGET_FILES_DIR = 'target/product/yogi/obj/PACKAGING/target_files_intermediates'
OTATOOLS = 'soong/.intermediates/build/make/tools/otatools_package/otatools-package/linux_glibc_x86_64/gen/otatools.zip'
TOOLS = {'aapt': 'prebuilts/sdk/tools/linux/bin/aapt', 'apksigner': 'prebuilts/sdk/tools/linux/bin/apksigner',
         'java': 'prebuilts/jdk/jdk21/linux-x86'}
SCRUBBED = ('JAVA_TOOL_OPTIONS', 'JDK_JAVA_OPTIONS', '_JAVA_OPTIONS', 'CLASSPATH', 'JAVA_HOME', 'LD_PRELOAD',
            'LD_LIBRARY_PATH', 'LD_AUDIT')
LABEL = 'Yogi experimental stock-kernel build; OTA-specific checks only; physical-device acceptance unproven'
BUILD = re.compile(r'[1-9][0-9]{9}')
SHA = re.compile(r'[0-9a-f]{64}')
COMMIT = re.compile(r'[0-9a-f]{40}')
STATES = {'start': ('RUNNING', 'TERMINATED', {'STAGING', 'PROVISIONING'}),
          'stop': ('TERMINATED', 'RUNNING', {'STOPPING'})}


class Refused(Exception):
    pass


def read(path):
    return json.loads(Path(path).read_text())


digest = ota_checks.digest


def module(name, file):
    spec = importlib.util.spec_from_file_location(name, ROOT/'scripts'/file)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def head(root=ROOT):
    return subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD'], text=True).strip()


# ------------------------------------------------------------------------------------------------ contract

def config(path=CONFIG):
    cfg = read(path)
    if cfg.get('schema') != 'roseos.yogi-ota-config.v1' or cfg.get('device') != DEVICE \
            or cfg.get('repository') != REPOSITORY:
        raise Refused('Yogi OTA configuration identity differs')
    b = cfg['builder']
    if (b['instance'], b['runnerLabel'], b['project'], b['zone']) != (INSTANCE, RUNNER_LABEL, 'protocols-465723', 'us-west2-a'):
        raise Refused('Yogi builder binding differs from the dedicated VM')
    for key in ('root', 'retainedSource', 'attempts', 'lock', 'coverDisplayInput'):
        path = Path(b[key])
        if not path.is_absolute() or '..' in path.parts or Path(b['root']) not in (path, *path.parents):
            raise Refused(f'builder.{key} must be an absolute path under {b["root"]}')
    s = cfg['signing']
    if (s['profile'], s['secrets'], s['signer']) != (PROFILE, SECRETS, SIGNER):
        raise Refused('Yogi signing binding differs; never another device\'s key set')
    w = cfg['wire']
    if w != {'tag': 'yogi-{build}', 'otaAsset': 'yogi-ota_update-{build}.zip', 'metadataAsset': 'yogi-stable',
             'metadataLine': '{build} {buildDateUtc} yogi stable', 'channel': 'stable', 'latest': False}:
        raise Refused('Yogi wire contract differs')
    route = check_route()
    if route['producerContract'] != {'tag': w['tag'], 'metadataAsset': w['metadataAsset'], 'otaAsset': w['otaAsset'],
                                     'metadataLine': w['metadataLine']}:
        raise Refused('Updater route and publication wire contract differ')
    floor = cfg['allocationFloor']
    if not BUILD.fullmatch(floor['build']) or not re.fullmatch(r'[1-9][0-9]*', floor['timestamp']):
        raise Refused('allocation floor must be a build number and an epoch')
    return cfg


def check_route(path=ROUTE):
    route = read(path)
    if route.get('schema') != 'roseos.yogi-updater-route.v1' or route.get('device') != DEVICE:
        raise Refused('Yogi updater route identity differs')
    url = re.fullmatch(r'https://github\.com/([A-Za-z0-9-]+/[A-Za-z0-9._-]+)/releases/download/', route['updaterUrl'])
    if not url or url[1] != REPOSITORY or route['releaseIndex'] != f'https://api.github.com/repos/{REPOSITORY}/releases':
        raise Refused('Yogi updater route must be per-release discovery in pixel-ota, never releases/latest')
    if route['publishedChannel'] != 'stable' or not route.get('owner') or not route.get('approvalRef'):
        raise Refused('Yogi updater route must publish stable and name its owner decision')
    return route


def identities(cfg):
    """Public pins from the committed Yogi bootstrap receipt; absent until the separately authorized bootstrap ran."""
    path = ROOT/cfg['signing']['bootstrapReceipt']
    if not path.is_file():
        raise Refused('Yogi bootstrap receipt absent: no Yogi signing identity exists yet')
    receipt = read(path)
    if receipt.get('schema') != 'roseos.signing-bootstrap-receipt.v1' or receipt.get('profile') != PROFILE \
            or receipt.get('device') != DEVICE or receipt.get('status') != 'complete' \
            or receipt.get('recoveryNeeded') is not False:
        raise Refused('complete Yogi bootstrap receipt required; no foreign identity')
    for role, name in SECRETS.items():
        row = receipt['secrets'][role]
        if row.get('name') != name or row.get('state') != 'committed' or not re.fullmatch(r'[1-9][0-9]*', str(row.get('committedVersion'))):
            raise Refused('Yogi bootstrap secret binding differs or is uncommitted')
    pins = receipt['publicIdentity']
    if not all(SHA.fullmatch(str(pins.get(k, ''))) for k in ('otaCertificateSha256', 'avbPublicKeySha256')) \
            or {pins['otaCertificateSha256'], pins['avbPublicKeySha256']} & LAB_PINS:
        raise Refused('Yogi public pins missing or equal to the public lab development identity')
    return pins, {role: str(receipt['secrets'][role]['committedVersion']) for role in SECRETS}


def access_problem(cfg, now=None):
    access = cfg['signing'].get('access') or {}
    if cfg['signing'].get('status') != 'approved' or not access.get('approvalRef') or not access.get('expiresAt'):
        return 'Yogi signing read access is not approved (signing.status/access)'
    expiry = datetime.datetime.fromisoformat(access['expiresAt'].replace('Z', '+00:00'))
    now = now or datetime.datetime.now(datetime.timezone.utc)
    if expiry.tzinfo is None or (expiry-now).total_seconds() < 1800:
        return 'approved Yogi signing window expired or has less than 30 minutes left'
    return None


def blockers(cfg, stage, access=False, now=None):
    """Every unmet prerequisite for a stage, by name (no secret, no runtime read)."""
    if stage not in STAGES:
        raise Refused(f'stage must be one of {STAGES}')
    found = []
    if stage in ('bootstrap', 'ota'):
        if cfg['builder'].get('status') != 'ready':
            found.append(f'builder runner/controller not ready (builder.status={cfg["builder"].get("status")})')
        inputs = cfg['appInputs']
        export = inputs.get('export') or {}
        if inputs.get('status') != 'bound' or not inputs.get('allocation') \
                or not re.fullmatch(r'[1-9][0-9]*', str(export.get('run') or '')) \
                or not re.fullmatch(r'[1-9][0-9]*', str(export.get('artifactId') or '')) \
                or not re.fullmatch(r'sha256:[0-9a-f]{64}', str(export.get('artifactDigest') or '')) \
                or not SHA.fullmatch(str(export.get('receiptSha256') or '')):
            found.append('signed app inputs are not bound (appInputs export run/artifact/digest/receipt)')
        try:
            identities(cfg)
        except Refused as error:
            found.append(str(error))
        if access:
            problem = access_problem(cfg, now)
            if problem:
                found.append(problem)
    if stage == 'ota' and (cfg['bootstrap'].get('status') not in ('signed', 'installed')
                           or not BUILD.fullmatch(str(cfg['bootstrap'].get('build') or ''))):
        found.append('Yogi bootstrap build A is not signed yet (bootstrap.status)')
    if stage == 'publish' and cfg.get('publicationEnabled') is not True:
        found.append('Yogi publication is not enabled (publicationEnabled)')
    return found


def guard(cfg, stage, access=False, now=None):
    found = blockers(cfg, stage, access, now)
    if found:
        raise Refused('Yogi ' + stage + ' blocked: ' + '; '.join(found))


# ------------------------------------------------------------------------------------------------ builder control

def control_builder(cfg, action, ownership=None, gcloud='gcloud', timeout=420, interval=10, sleep=time.sleep,
                    clock=time.monotonic):
    """Start/stop only the dedicated Yogi VM (async + describe polling, never resubmitted). A stop happens only when
    this run's own start record says it started the VM."""
    b = cfg['builder']
    where = [f'--project={b["project"]}', f'--zone={b["zone"]}']

    def describe():
        result = subprocess.run([gcloud, 'compute', 'instances', 'describe', INSTANCE, *where, '--format=value(status)'],
                                capture_output=True, text=True)
        if result.returncode:
            raise Refused(f'Yogi builder describe failed (exit {result.returncode})')
        return result.stdout.strip()
    if action == 'state':
        return describe()
    target, source, passing = STATES[action]
    state = describe()
    if action == 'start':
        if ownership is None:
            raise Refused('start needs an ownership record path')
        with open(ownership, 'x') as f:  # one operation, one record, written before any request
            json.dump({'schema': 'roseos.yogi-builder-ownership.v1', 'instance': INSTANCE,
                       'startedByThisOperation': state == source}, f)
    else:
        record = read(ownership) if ownership and Path(ownership).exists() else None
        if not record or record.get('instance') != INSTANCE or record.get('startedByThisOperation') is not True:
            return 'not-started-by-this-run'
    if state == source:
        result = subprocess.run([gcloud, 'compute', 'instances', action, INSTANCE, *where, '--async', '--quiet'],
                                capture_output=True, text=True)
        if result.returncode:
            state = describe()
            if state not in passing | {target}:
                raise Refused(f'Yogi builder {action} request failed (exit {result.returncode}); observed {state}')
    elif state not in passing | {target}:
        raise Refused(f'Yogi builder is {state}; refusing to {action} it')
    deadline = clock() + timeout
    while state != target:
        if state not in passing | {source}:
            raise Refused(f'Yogi builder moved to {state} while waiting for {target}')
        if clock() >= deadline:
            raise Refused(f'Yogi builder did not reach {target} within {timeout}s; last observed {state}')
        sleep(interval)
        state = describe()
    return state


# ------------------------------------------------------------------------------------------------ builder stages

def regular_dir(path, label):
    path = Path(path)
    for part in (path, *path.parents):
        if part.is_symlink():
            raise Refused(f'{label} passes through a symlink: {part}')
    if not path.is_dir():
        raise Refused(f'{label} {path} is not an existing directory')
    return path


def builder_lock(cfg):
    lock = Path(cfg['builder']['lock'])
    if lock.is_symlink() or not lock.is_file():
        raise Refused('the Yogi builder lock must be the existing regular file')
    handle = lock.open('r+')
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        raise Refused('the Yogi builder lock is held: another Yogi attempt is active; nothing changed')
    return handle


def preflight(cfg, free=shutil.disk_usage):
    b = cfg['builder']
    regular_dir(b['retainedSource'], 'retained Yogi source')
    regular_dir(b['attempts'], 'Yogi attempts directory')
    if not Path(b['coverDisplayInput']).is_file():
        raise Refused('private cover display input is absent')
    with builder_lock(cfg):
        pass
    available = free(b['attempts']).free
    if available < int(b['minimumFreeBytes']):
        raise Refused(f'only {available} bytes free under {b["attempts"]}; {b["minimumFreeBytes"]} required')
    return {'retainedSource': b['retainedSource'], 'freeBytes': available}


def attempt_dir(cfg, build):
    if not BUILD.fullmatch(str(build)):
        raise Refused('build must be a ten-digit build number')
    return Path(cfg['builder']['attempts'])/f'yogi-{build}'


def allocate(cfg, run_id, attempt, source, now=None):
    """A strictly newer build number and BUILD_DATETIME than the lab, the bootstrap and every earlier Yogi attempt."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    attempts = regular_dir(cfg['builder']['attempts'], 'Yogi attempts directory')
    used = [int(cfg['allocationFloor']['build'])]
    stamps = [int(cfg['allocationFloor']['timestamp'])]
    if cfg['bootstrap'].get('build'):
        used.append(int(cfg['bootstrap']['build']))
        stamps.append(int(cfg['bootstrap']['timestamp']))
    for entry in attempts.iterdir():
        match = re.fullmatch(r'yogi-([0-9]{10})', entry.name)
        if match:
            used.append(int(match[1]))
            record = entry/'allocation.json'
            if record.is_file():
                stamps.append(int(read(record)['timestamp']))
    build = max(max(used) + 1, int(now.strftime('%Y%m%d01')))
    if str(build)[:8] != now.strftime('%Y%m%d') or str(build)[8:] == '00':
        raise Refused('no build number left for today (or an earlier build is dated in the future)')
    stamp = max(int(now.timestamp()), max(stamps) + 1)
    directory = attempt_dir(cfg, build)
    directory.mkdir(mode=0o700)  # exclusive: a concurrent or repeated allocation fails here
    record = {'schema': 'roseos.yogi-allocation.v1', 'device': DEVICE, 'build': str(build), 'timestamp': str(stamp),
              'source': source, 'runId': run_id, 'runAttempt': attempt, 'reservedAt': now.strftime('%Y-%m-%dT%H:%M:%SZ')}
    release_receipts.write_once(directory/'allocation.json', record)
    return record


def copy_tree(base, target, run=subprocess.run):
    """Byte copy of the base tree without its top-level build outputs or signed release directory (an earlier
    attempt's host-sign output); the base is only read."""
    target.mkdir(mode=0o700)
    for entry in sorted(Path(base).iterdir()):
        if re.fullmatch(r'out([-_.].*)?|releases', entry.name):
            continue
        result = run(['cp', '-a', '--reflink=auto', str(entry), str(target)+'/'], capture_output=True, text=True)
        if result.returncode:
            raise Refused(f'copy of {entry.name} failed (exit {result.returncode})')


def port_state(source, root=ROOT, run=subprocess.run):
    """The copy carries exactly the pinned Yogi port: project pins, the reviewed vendor reference and all port patches
    already applied (reverse-apply check). Nothing is changed."""
    port = module('yogi_port', 'prepare-yogi-port.py')
    recipe = read(root/'os/fold-port/source.json')
    pins = {p.get('path', p.get('name')): p.get('revision') for p in ET.parse(root/recipe['manifest']).findall('project')}
    for project in recipe['projects']:
        directory = Path(source)/project
        if port.git(directory, 'rev-parse', 'HEAD') != pins[project]:
            raise Refused('wrong upstream revision in the copy: ' + project)
        if project == 'vendor/adevtool':
            port.check_vendor_reference(directory, recipe['vendorReference'], root)
    for name in reversed(recipe['patches']):
        result = run(['git', 'apply', '--reverse', '--check', str(root/'os/fold-port'/name)], cwd=str(source),
                     capture_output=True, text=True)
        if result.returncode:
            raise Refused('Yogi port patch is not applied in the copy: ' + name)
    return {'projects': {p: pins[p] for p in recipe['projects']}, 'patchesApplied': list(recipe['patches'])}


def prepare(cfg, build, base_attempt=None, run=subprocess.run):
    directory = attempt_dir(cfg, build)
    allocation = read(directory/'allocation.json')
    if base_attempt:
        base = attempt_dir(cfg, base_attempt)/'src'
        receipts = attempt_dir(cfg, base_attempt)/'reconcile-receipts'
    else:
        base, receipts = Path(cfg['builder']['retainedSource']), None
    regular_dir(base, 'base tree')
    with builder_lock(cfg):
        copy_tree(base, directory/'src', run)
        if receipts is not None:
            shutil.copytree(receipts, directory/'reconcile-receipts', symlinks=True)
        state = port_state(directory/'src', run=run)
    record = {'schema': 'roseos.yogi-prepare.v1', 'build': allocation['build'], 'base': str(base),
              'baseAttempt': base_attempt, 'baseIsRetainedLabTree': base_attempt is None, 'port': state}
    release_receipts.write_once(directory/'prepare.json', record)
    return record


EXPORT_SCHEMA = 'roseos.app-inputs-export.v1'
SUFFIXES = ('.apk', '.receipt.json', '.apk.idsig')


def verify_export(cfg, directory):
    """Release's app-inputs-export artifact (PR #565 contract), byte-checked against this repository's one record:
    the committed receipt digest, its producer allocation path and digest, signer and SDK, and SHA256SUMS over exactly
    the nine signed files, the policy and the receipt. Admission of the APKs themselves follows in admit_apps."""
    pins, directory = cfg['appInputs'], regular_dir(directory, 'app-inputs export')
    receipt_path = directory/'app-inputs-export.json'
    if digest(receipt_path) != pins['export']['receiptSha256']:
        raise Refused('app-inputs export receipt differs from the committed record')
    receipt = read(receipt_path)
    allocation = ROOT/pins['allocation']
    producer = receipt.get('producer') or {}
    record = read(allocation)
    if receipt.get('schema') != EXPORT_SCHEMA or producer.get('device') != 'frankel' \
            or producer.get('build') != record.get('build') \
            or (producer.get('allocation') or {}) != {'path': pins['allocation'], 'sha256': digest(allocation)} \
            or receipt.get('signerSha256') != pins['appSignerSha256'] or receipt.get('sdk') != SDK:
        raise Refused('app-inputs export producer, allocation, signer or SDK differs from the committed record')
    names = sorted([f'signed-apps/{p}{x}' for p in pins['packages'] for x in SUFFIXES] + ['background-policy.json'])
    if sorted(f['path'] for f in receipt.get('files', [])) != names:
        raise Refused('app-inputs export file inventory differs from the nine signed files and the policy')
    present = sorted(str(p.relative_to(directory)) for p in directory.rglob('*') if p.is_file() or p.is_symlink())
    if present != sorted(names + ['app-inputs-export.json', 'SHA256SUMS']) or any(p.is_symlink() for p in directory.rglob('*')):
        raise Refused('app-inputs export holds missing, extra or linked files')
    expected = {f['path']: f['sha256'] for f in receipt['files']}
    expected['app-inputs-export.json'] = pins['export']['receiptSha256']
    seen = {}
    for line in (directory/'SHA256SUMS').read_text().splitlines():
        parts = line.split('  ')
        if len(parts) != 2 or parts[1] in seen or not SHA.fullmatch(parts[0]):
            raise Refused('app-inputs export SHA256SUMS is malformed or ambiguous')
        seen[parts[1]] = parts[0]
    if seen != expected or any(digest(directory/name) != value for name, value in expected.items()):
        raise Refused('app-inputs export bytes differ from SHA256SUMS or the receipt')
    return {'receiptSha256': pins['export']['receiptSha256'], 'producer': producer, 'apps': receipt.get('apps')}


def admit_apps(cfg, apps_dir, policy_path, tools, identity=background_apks.identity):
    """The already-signed fleet apps: receipt bytes/signer, actual APK identity and a policy pinning exactly them."""
    pins = cfg['appInputs']
    signer, packages = pins['appSignerSha256'], pins['packages']
    apps_dir = regular_dir(apps_dir, 'signed app inputs')
    allocation = read(ROOT/pins['allocation'])
    expected = {package: version for package, version in allocation['apps']}
    if sorted(expected) != sorted(packages):
        raise Refused('bound app allocation names other packages')
    apps = []
    for package in packages:
        apk, receipt_path = apps_dir/f'{package}.apk', apps_dir/f'{package}.receipt.json'
        if apk.is_symlink() or receipt_path.is_symlink() or not apk.is_file() or not receipt_path.is_file():
            raise Refused(f'{package}: signed APK and receipt must be regular files')
        receipt = read(receipt_path)
        if receipt.get('schemaVersion') != 1 or receipt.get('apk') != apk.name or receipt.get('sha256') != digest(apk) \
                or receipt.get('certificateSha256') != [signer] or receipt.get('signatureVerified') is not True:
            raise Refused(f'{receipt_path.name} does not match {apk.name} (bytes, signer or verification)')
        found = identity(apk, tools['aapt'], tools['apksigner'], SDK)
        if found.get('packageName') != package or found.get('certificates') != [signer.upper()] \
                or found.get('versionCode') != expected[package]:
            raise Refused(f'{apk.name} is not {package} {expected[package]} signed by the fleet app signer')
        apps.append({'package': package, 'versionCode': found['versionCode'], 'sha256': digest(apk),
                     'receiptSha256': digest(receipt_path)})
    policy = background_apks.validate_policy(read(policy_path), apps_dir/'dev.pixel.backgroundcontrol.apk')
    if policy['sdk'] != SDK or len(policy['targets']) != 1:
        raise Refused('policy SDK or target count differs')
    rows = {r['packageName']: r for r in [policy['agent'], policy['voice'], *policy['targets']]}
    for app in apps:
        row = rows.get(app['package'])
        if not row or (row['versionCode'], row['apkSha256'], row['certificates']) != \
                (app['versionCode'], app['sha256'], [signer.upper()]):
            raise Refused(f'the policy does not pin the signed {app["package"]} bytes and version')
    return {'apps': apps, 'policySha256': digest(policy_path), 'allocation': pins['allocation'],
            'appSignerSha256': signer}


def cover_display(cfg):
    value = read(cfg['builder']['coverDisplayInput']).get('referenceDisplayAddress')
    if not isinstance(value, str) or not value.isdecimal() or not 0 < int(value) < 2**63:
        raise Refused('private cover display input lacks a decimal referenceDisplayAddress')
    return value


def render_reconcile(cfg, source, receipt_dir, display, route=None, template=TEMPLATE):
    """host-reconcile's --config: placeholders except {policy}/{signed_apps} are filled here."""
    route = route or check_route()
    spec = read(template)
    if spec.get('kind') != 'rose-yogi-reconcile-template' or spec.get('device') != DEVICE:
        raise Refused('Yogi reconcile template identity differs')
    args = spec['reconcile']['applyArgs']
    if '--agent-tray' in args or '--keep-upstream-updater' in args or '--updater-url' not in args:
        raise Refused('Yogi integration must carry the owned Updater and no --agent-tray')
    values = {'updater_url': route['updaterUrl'], 'updater_release_index': route['releaseIndex'],
              'updater_channel': route['publishedChannel'], 'cover_display': display,
              'policy': '{policy}', 'signed_apps': '{signed_apps}'}
    reconcile = dict(spec['reconcile'], receiptDir=str(receipt_dir), applyArgs=[a.format(**values) for a in args])
    return {'schemaVersion': 1, 'kind': 'rose-yogi-reconcile', 'device': DEVICE,
            'host': {'sourceRoot': str(source), 'integrate': {'reconcile': reconcile}}}


def conflicts_of(stderr):
    """The conflicting paths host-reconcile names (nothing was written)."""
    for line in reversed(stderr.strip().splitlines()):
        try:
            error = json.loads(line).get('error', '')
        except ValueError:
            continue
        marker = 'owner changes conflict with the candidate (nothing written): '
        if error.startswith(marker):
            return sorted(error[len(marker):].split(', '))
    return None


def integrate(cfg, build, app_inputs, run=subprocess.run):
    directory = attempt_dir(cfg, build)
    allocation, prepared = read(directory/'allocation.json'), read(directory/'prepare.json')
    source = directory/'src'
    tools = {k: str(source/v) for k, v in TOOLS.items() if k != 'java'}
    # The downloaded export is copied into the attempt first, so every later check reads the same retained bytes.
    apps_dir, downloaded = directory/'app-inputs', regular_dir(app_inputs, 'downloaded app inputs')
    entries = list(downloaded.iterdir())
    if len(entries) == 1 and entries[0].is_dir() and not entries[0].is_symlink():
        downloaded = entries[0]  # an artifact downloaded by id may land in its own named directory
    shutil.copytree(downloaded, apps_dir, symlinks=True)
    exported = verify_export(cfg, apps_dir)
    admitted = admit_apps(cfg, apps_dir/'signed-apps', apps_dir/'background-policy.json', tools)
    release_receipts.write_once(directory/'app-input-admission.json', dict(admitted, export=exported))
    product = directory/f'product-{allocation["source"][:12]}'
    result = run(['git', '-C', str(ROOT), 'worktree', 'add', '--detach', str(product), allocation['source']],
                 capture_output=True, text=True)
    if result.returncode:
        raise Refused('candidate checkout failed')
    rendered = directory/'reconcile-config.json'
    release_receipts.write_once(rendered, render_reconcile(cfg, source, directory/'reconcile-receipts', cover_display(cfg)))
    command = [sys.executable, str(ROOT/'scripts/host-reconcile.py'), 'apply', str(product), str(directory/'reconcile-work'),
               str(apps_dir/'background-policy.json'), str(apps_dir/'signed-apps'), '--config', str(rendered),
               '--label', f'yogi-{build}']
    (directory/'reconcile-work').mkdir(exist_ok=True)
    record = {'schema': 'roseos.yogi-integrate.v1', 'build': build, 'source': allocation['source'], 'adopted': []}
    with builder_lock(cfg):
        result = run(command, capture_output=True, text=True, cwd=str(ROOT))
        conflicts = conflicts_of(result.stderr) if result.returncode else None
        allowed = read(TEMPLATE)['labAdoption']
        if conflicts and prepared['baseIsRetainedLabTree'] and set(conflicts) <= set(allowed['paths']):
            adopt = [sys.executable, str(ROOT/'scripts/host-reconcile.py'), 'adopt', *command[3:],
                     '--reason', allowed['reason'], *[x for p in conflicts for x in ('--path', p)]]
            if run(adopt, capture_output=True, text=True, cwd=str(ROOT)).returncode:
                raise Refused('lab-integration adoption failed in the attempt copy')
            record['adopted'] = conflicts
            result = run(command, capture_output=True, text=True, cwd=str(ROOT))
        if result.returncode:
            raise Refused('host-reconcile refused the Yogi integration: ' + result.stderr.strip()[-600:])
    record['reconcile'] = json.loads(result.stdout)
    release_receipts.write_once(directory/'integrate.json', record)
    return record


def target_identity(path):
    with zipfile.ZipFile(path) as archive:
        props = ota_checks.vendor_identity(ota_checks.member(archive, 'VENDOR/build.prop', 4 << 20))
    return {'device': props['ro.product.vendor.device'], 'build': props['ro.vendor.build.version.incremental'],
            'timestamp': props['ro.vendor.build.date.utc']}


def collect_outputs(source, out_dir, build, epoch):
    out = Path(source)/out_dir
    found = sorted((out/TARGET_FILES_DIR).glob('yogi-target_files*.zip'))
    tools = out/OTATOOLS
    if len(found) != 1 or found[0].is_symlink() or not tools.is_file() or tools.is_symlink():
        raise Refused('exactly one Yogi target-files ZIP and the Soong otatools.zip are required')
    if target_identity(found[0]) != {'device': DEVICE, 'build': build, 'timestamp': epoch}:
        raise Refused('compiled target-files device/build/time differs from the allocation')
    return {name: {'path': str(p), 'sizeBytes': p.stat().st_size, 'sha256': digest(p)}
            for name, p in (('targetFiles', found[0]), ('otatools', tools))}


def compile_build(cfg, build, run=subprocess.run):
    directory = attempt_dir(cfg, build)
    allocation = read(directory/'allocation.json')
    read(directory/'integrate.json')
    source, out_dir = directory/'src', f'out/yogi-{build}'
    if (source/out_dir).exists():
        raise Refused('output directory exists; no attempt is resumed')
    env = {k: v for k, v in os.environ.items() if k not in SCRUBBED and not k.startswith('GIT_')}
    env.update(USE_STOCK_KERNEL='true', SOONG_NINJA='ninja', OUT_DIR=out_dir, BUILD_NUMBER=build,
               BUILD_DATETIME=allocation['timestamp'])
    steps = f'source build/envsetup.sh && lunch {LUNCH} && m -j{int(cfg["builder"]["jobs"])} ' + ' '.join(GOALS)
    started = time.time()
    with builder_lock(cfg), open(directory/'compile.log', 'x') as log:
        result = run(['bash', '-euo', 'pipefail', '-c', steps], cwd=str(source), env=env, stdout=log,
                     stderr=subprocess.STDOUT, timeout=6*3600)
    record = {'schema': 'roseos.yogi-compile.v1', 'build': build, 'timestamp': allocation['timestamp'],
              'source': allocation['source'], 'lunch': LUNCH, 'goals': list(GOALS), 'outDir': out_dir,
              'environment': {k: env[k] for k in ('USE_STOCK_KERNEL', 'SOONG_NINJA', 'OUT_DIR', 'BUILD_NUMBER', 'BUILD_DATETIME')},
              'returncode': result.returncode, 'seconds': round(time.time()-started)}
    if result.returncode == 0:
        record['outputs'] = collect_outputs(source, out_dir, build, allocation['timestamp'])
    release_receipts.write_once(directory/'compile.json', record)
    if result.returncode:
        raise Refused(f'make exited {result.returncode}; see the retained compile log; no retry')
    return record


def sign_config(cfg, build, compiled, notes, versions, pins):
    out_dir = compiled['outDir']
    return {'target': {'device': DEVICE}, 'identities': pins,
            'publication': {'channels': {'beta': {'feedChannel': 'stable'}}},
            'host': {'sign': {'project': cfg['signing']['project'], 'keysSecret': SECRETS['keys'],
                              'passphraseSecret': SECRETS['passphrase'], 'secretVersion': versions['keys'],
                              'passphraseSecretVersion': versions['passphrase'],
                              'otatools': str(Path(compiled['outputs']['otatools']['path']).relative_to(
                                  attempt_dir(cfg, build)/'src'/out_dir)),
                              'javaHome': str(attempt_dir(cfg, build)/'src'/TOOLS['java']),
                              'updateNotes': {'file': str(notes), 'sha256': digest(notes), 'device': DEVICE, 'build': build}}}}


def sign(cfg, build, stage, run=subprocess.run, env=os.environ):
    """The pinned release script through host-sign.main with the Yogi key set; OTA output only."""
    guard(cfg, stage, access=True)
    if env.get('GCP_SIGNER_SERVICE_ACCOUNT') != SIGNER:
        raise Refused('the signer identity differs')
    pins, versions = identities(cfg)
    directory = attempt_dir(cfg, build)
    allocation, compiled = read(directory/'allocation.json'), read(directory/'compile.json')
    for row in compiled['outputs'].values():
        ota_checks.checked_file(row)
    out = directory/'sign'
    out.mkdir(mode=0o700)
    notes = out/'UPDATE.md'
    notes.write_text(f'# RoseOS Yogi {build}\n\n{LABEL}\n\nSource: {allocation["source"]}\n')
    release = out/'release'
    release.mkdir(mode=0o700)
    shutil.copyfile(compiled['outputs']['targetFiles']['path'], release/'unsigned-target_files.zip')
    path = out/'sign-config.json'
    release_receipts.write_once(path, sign_config(cfg, build, compiled, notes, versions, pins))
    child = {k: v for k, v in env.items() if k not in SCRUBBED and not k.startswith('GIT_')}
    child.update(BUILD_NUMBER=build, BUILD_DATETIME=allocation['timestamp'], ROSE_CANDIDATE_SHA=allocation['source'],
                 ROSE_RELEASE_DIR=str(release), ROSE_SIGN_OTATOOLS=compiled['outputs']['otatools']['path'],
                 OUT_DIR=compiled['outDir'])
    record = {'schema': 'roseos.yogi-sign.v1', 'device': DEVICE, 'stage': stage, 'build': build,
              'timestamp': allocation['timestamp'], 'candidateSource': allocation['source'], 'recipeSource': head(),
              'secrets': {k: {'name': SECRETS[k], 'version': v} for k, v in versions.items()}, 'status': 'failed'}
    try:
        with builder_lock(cfg):
            # host-sign reads the release script relative to its working directory: the attempt's own tree.
            result = run([sys.executable, str(Path(__file__).resolve()), '_execute-sign', '--dir', str(path)],
                         cwd=str(directory/'src'), env=child, timeout=3*3600)
        record['returncode'] = result.returncode
        if result.returncode:
            raise Refused(f'host-sign exited {result.returncode}; no retry')
        tf, ota = release/'signed-target_files.zip', release/'public'/f'yogi-ota_update-{build}.zip'
        record['outputs'] = {name: {'path': str(p), 'sizeBytes': p.stat().st_size, 'sha256': digest(p)}
                             for name, p in (('targetFiles', tf), ('ota', ota))}
        record.update(status='signed', otatools=compiled['outputs']['otatools'], sourceRoot=str(directory/'src'))
        return record
    finally:
        release_receipts.write_once(out/'yogi-sign-receipt.json', record)


def install_image(cfg, build, run=subprocess.run):
    """Bootstrap only: the private fastboot image ZIP of the signed target-files and its public AVB key metadata."""
    directory = attempt_dir(cfg, build)
    signed = read(directory/'sign/yogi-sign-receipt.json')
    if signed.get('status') != 'signed' or signed.get('stage') != 'bootstrap':
        raise Refused('a completed bootstrap signing receipt is required')
    pins, _ = identities(cfg)
    tf = ota_checks.checked_file(signed['outputs']['targetFiles'])
    avb = directory/'sign/release/public/avb_pkmd.bin'
    if digest(avb) != pins['avbPublicKeySha256']:
        raise Refused('produced AVB public key differs from the Yogi bootstrap pin')
    out = directory/'install'
    out.mkdir(mode=0o700)
    hp = module('yogi_factory_host', 'host-factory-package.py')
    with tempfile.TemporaryDirectory(prefix='yogi-otatools-', dir=directory) as temp:
        hp.unzip_otatools(ota_checks.checked_file(signed['otatools']), Path(temp))
        image = out/f'yogi-img-{build}.zip'
        result = run([str(Path(temp)/'bin/img_from_target_files'), str(tf), str(image)], capture_output=True, text=True,
                     env=dict(os.environ, PATH=str(Path(temp)/'bin')+':'+os.environ.get('PATH', '')), timeout=3600)
        if result.returncode:
            raise Refused('img_from_target_files failed')
    shutil.copyfile(avb, out/'avb_pkmd.bin')
    record = {'schema': 'roseos.yogi-install-image.v1', 'build': build, 'dataWipe': True,
              'image': {'file': image.name, 'sizeBytes': image.stat().st_size, 'sha256': digest(image)},
              'avbPublicKeySha256': pins['avbPublicKeySha256'],
              'instructions': ['fastboot erase avb_custom_key', 'fastboot flash avb_custom_key avb_pkmd.bin',
                               f'fastboot -w update {image.name}'],
              'basis': 'Proposed supported design: one-time wipe installation of build A; erases userdata and metadata. '
                       'Requires the owner\'s separate explicit authorization; the bootloader stays unlocked; no relock.'}
    release_receipts.write_once(out/'install-receipt.json', record)
    return record


def observe_target(path, build, epoch, tools, pins, execute=subprocess.run):
    """Packaged Updater (route resources, discovery classes, signer) and system OTA trust in signed target-files."""
    pipeline = module('yogi_ota_resources', 'release-pipeline.py')
    route = check_route()
    member = ota_checks.member
    import io
    with zipfile.ZipFile(path) as archive, tempfile.TemporaryDirectory(prefix='yogi-ota-observe-') as directory:
        if target_identity(path) != {'device': DEVICE, 'build': build, 'timestamp': epoch}:
            raise Refused('actual target-files device/build/time differs')
        names = archive.namelist()
        for required in ('SYSTEM/etc/permissions/app.seamlessupdate.client.xml',
                         'SYSTEM/etc/default-permissions/app.seamlessupdate.client.xml',
                         'SYSTEM/etc/sysconfig/app.seamlessupdate.client.xml'):
            if required not in names:
                raise Refused('packaged Updater support file absent: ' + required)
        apk = Path(directory)/'Updater.apk'
        apk.write_bytes(member(archive, 'SYSTEM/priv-app/Updater/Updater.apk', 128 << 20))
        with zipfile.ZipFile(apk) as package:
            dexes = [row.filename for row in package.infolist() if re.fullmatch(r'classes[0-9]*\.dex', row.filename)]
            compiled = [member(package, name, 128 << 20) for name in dexes]
            for name in (b'RoseChannels', b'RoseReleases'):
                if not any(b'Lapp/seamlessupdate/client/'+name+b';' in dex for dex in compiled):
                    raise Refused('packaged Updater lacks the Rose discovery class ' + name.decode())
        result = execute([tools['aapt'], 'dump', '--values', 'resources', str(apk)], capture_output=True, text=True, timeout=120)
        if result.returncode:
            raise Refused('packaged Updater resources could not be read')
        expected = {('string', 'url'): route['updaterUrl'], ('string', 'release_index'): route['releaseIndex'],
                    ('string', 'channel_default'): 'prod', ('array', 'channel_published'): ['stable'],
                    ('array', 'channel_values'): ['prod', 'stable', 'alpha']}
        failures = pipeline.updater_failures(result.stdout, expected)
        if failures:
            raise Refused('; '.join(failures))
        result = execute([tools['aapt'], 'dump', 'badging', str(apk)], capture_output=True, text=True, timeout=120)
        if result.returncode or not re.search(r"package: name='app\.seamlessupdate\.client'", result.stdout):
            raise Refused('packaged Updater package identity differs')
        result = execute([tools['apksigner'], 'verify', '--print-certs', str(apk)], capture_output=True, text=True, timeout=120)
        certs = [x.lower() for x in re.findall(r'Signer #[0-9]+ certificate SHA-256 digest: ([0-9a-fA-F]+)', result.stdout)]
        if result.returncode or certs != [pins['otaCertificateSha256']]:
            raise Refused('packaged Updater signer differs from the Yogi release identity')
        trust = []
        with zipfile.ZipFile(io.BytesIO(member(archive, 'SYSTEM/etc/security/otacerts.zip', 4 << 20))) as certificates:
            for row in certificates.infolist():
                cert = Path(directory)/'trusted.pem'
                cert.write_bytes(member(certificates, row.filename, 1 << 20))
                trust.append(host_secrets.certificate_sha256(cert))
        if trust != [pins['otaCertificateSha256']]:
            raise Refused('system OTA trust is not exactly the Yogi release identity')
        return {'device': DEVICE, 'build': build, 'timestamp': epoch, 'updaterApkSha256': digest(apk),
                'updaterResources': {k[1]: v for k, v in expected.items()}, 'updaterSignerSha256': certs[0],
                'trustedOtaCertificateSha256': trust, 'packagedUpdaterVerified': True}


def ota_metadata(path, build, epoch, bootstrap):
    with zipfile.ZipFile(path) as archive:
        values = ota_checks.properties(ota_checks.member(archive, 'META-INF/com/android/metadata', 1 << 20))
    if values.get('pre-device') != DEVICE or values.get('post-build-incremental') != build \
            or values.get('post-timestamp') != epoch or f'/{build}:' not in values.get('post-build', '') \
            or values.get('ota-type') != 'AB' or values.get('ota-downgrade') == 'yes' \
            or values.get('ota-wipe') == 'yes' or 'pre-build' in values or 'pre-build-incremental' in values:
        raise Refused('OTA target/version/full-update metadata differs or requests downgrade/wipe')
    if int(build) <= int(bootstrap['build']) or int(epoch) <= int(bootstrap['timestamp']):
        raise Refused('the OTA must be newer than the Yogi bootstrap build')
    return values


def stage(cfg, build, out, run=subprocess.run):
    """OTA checks on the builder, then the exact public asset set: OTA, yogi-stable, receipt, SHA256SUMS."""
    guard(cfg, 'ota')
    pins, _ = identities(cfg)
    directory = attempt_dir(cfg, build)
    signed = read(directory/'sign/yogi-sign-receipt.json')
    if signed.get('status') != 'signed' or signed.get('stage') != 'ota' or signed.get('build') != build:
        raise Refused('a completed OTA-stage Yogi signing receipt is required')
    tf, ota = (ota_checks.checked_file(signed['outputs'][k]) for k in ('targetFiles', 'ota'))
    epoch, source = signed['timestamp'], Path(signed['sourceRoot'])
    tools = {k: str(source/v) for k, v in TOOLS.items() if k != 'java'}
    observed = observe_target(tf, build, epoch, tools, pins, run)
    metadata = ota_metadata(ota, build, epoch, cfg['bootstrap'])
    public = ota.parent
    if host_secrets.certificate_sha256(public/'releasekey.x509.pem') != pins['otaCertificateSha256'] \
            or digest(public/'avb_pkmd.bin') != pins['avbPublicKeySha256']:
        raise Refused('produced public signing identity differs from the Yogi bootstrap pins')
    checker = source/'build/make/tools/releasetools/check_ota_package_signature.py'
    out = Path(out)
    out.mkdir(mode=0o700)
    hp = module('yogi_ota_factory', 'host-factory-package.py')
    with tempfile.TemporaryDirectory(prefix='yogi-ota-tools-', dir=directory) as temp:
        hp.unzip_otatools(ota_checks.checked_file(signed['otatools']), Path(temp))
        binary = Path(temp)/'bin'
        if not (binary/'delta_generator').is_file() or not checker.is_file():
            raise Refused('OTA signature verifier dependency unavailable; verification not performed')
        result = run([sys.executable, str(checker), str(public/'releasekey.x509.pem'), str(ota)], capture_output=True,
                     text=True, timeout=1800, env=dict(os.environ, PATH=str(binary)+':'+os.environ.get('PATH', '')))
        if result.returncode:
            raise Refused('OTA whole-package or payload signature verification failed')
        tools_used = {'checkOtaSignatureSha256': digest(checker), 'deltaGeneratorSha256': digest(binary/'delta_generator')}
    shutil.copyfile(ota, out/ota.name)
    (out/'yogi-stable').write_text(f'{build} {epoch} yogi stable\n')
    receipt = {'schema': 'roseos.yogi-ota-stage.v1', 'device': DEVICE, 'build': build, 'timestamp': epoch,
               'candidateSource': signed['candidateSource'], 'recipeSource': signed['recipeSource'],
               'runId': os.environ.get('GITHUB_RUN_ID', ''), 'label': LABEL,
               'bootstrapBuild': cfg['bootstrap']['build'], 'packagedUpdater': observed, 'otaMetadata': metadata,
               'publicIdentity': pins, 'verificationTools': tools_used,
               'checks': {'targetVersion': True, 'bootstrapNewer': True, 'otaSignatures': True, 'packagedUpdater': True},
               'phoneUpdatePassed': False, 'automationDemonstrated': False,
               'ota': {'file': ota.name, 'sizeBytes': ota.stat().st_size, 'sha256': digest(ota)}}
    release_receipts.write_once(out/'yogi-ota-receipt.json', receipt)
    (out/'SHA256SUMS').write_text(''.join(f'{digest(p)}  {p.name}\n' for p in sorted(out.iterdir())))
    return receipt


# ------------------------------------------------------------------------------------------------ hosted publication

CHECKS = {'targetVersion': True, 'bootstrapNewer': True, 'otaSignatures': True, 'packagedUpdater': True}


def verify_staged(directory, cfg):
    guard(cfg, 'publish')
    pins, _ = identities(cfg)
    directory = Path(directory)
    r = read(directory/'yogi-ota-receipt.json')
    if r.get('schema') != 'roseos.yogi-ota-stage.v1' or r.get('device') != DEVICE or r.get('checks') != CHECKS \
            or r.get('publicIdentity') != pins or r.get('bootstrapBuild') != cfg['bootstrap']['build'] \
            or r.get('label') != LABEL:
        raise Refused('complete Yogi OTA-specific verification required')
    if not all(COMMIT.fullmatch(str(r.get(k, ''))) for k in ('candidateSource', 'recipeSource')):
        raise Refused('candidate or recipe source identity differs')
    build, epoch = r['build'], r['timestamp']
    if not BUILD.fullmatch(build) or not re.fullmatch(r'[0-9]+', epoch) or r['ota']['file'] != f'yogi-ota_update-{build}.zip':
        raise Refused('Yogi OTA asset identity differs')
    if int(build) <= int(cfg['bootstrap']['build']) or int(epoch) <= int(cfg['bootstrap']['timestamp']):
        raise Refused('publication cannot advertise a build that is not newer than the bootstrap')
    expected = {r['ota']['file'], 'yogi-stable', 'yogi-ota-receipt.json'}
    if {p.name for p in directory.iterdir()} != expected | {'SHA256SUMS'}:
        raise Refused('unexpected public asset set')
    if (directory/'yogi-stable').read_text() != f'{build} {epoch} yogi stable\n':
        raise Refused('channel metadata does not describe the exact OTA')
    names = []
    for line in (directory/'SHA256SUMS').read_text().splitlines():
        parts = line.split('  ')
        if len(parts) != 2 or parts[1] not in expected or not SHA.fullmatch(parts[0]) or digest(directory/parts[1]) != parts[0]:
            raise Refused('staged checksum manifest differs')
        names.append(parts[1])
    if sorted(names) != sorted(expected):
        raise Refused('staged checksum manifest is incomplete or ambiguous')
    ota_checks.checked_file(dict(path=str(directory/r['ota']['file']), sha256=r['ota']['sha256'], sizeBytes=r['ota']['sizeBytes']))
    return r


def publish(client, directory, cfg, out):
    """A fresh yogi-{build} release: OTA first, metadata last, public only after readback, never Latest."""
    directory, out = Path(directory), Path(out)
    r = verify_staged(directory, cfg)
    out.mkdir(mode=0o700)
    tag = 'yogi-' + r['build']
    before = client.latest_tag()
    releases = ota_checks.release_rows(client)
    if len(releases) >= 299:
        raise Refused('adding a release would exceed the updater discovery completeness bound')
    for row in releases:
        match = re.fullmatch(r'yogi-([0-9]{10})', row.get('tag_name', ''))
        if match and int(match[1]) >= int(r['build']):
            raise Refused('a Yogi release or draft already consumes this or a newer build; nothing changed')
    names = [r['ota']['file'], 'yogi-ota-receipt.json', 'SHA256SUMS', 'yogi-stable']
    state = {'schema': 'roseos.yogi-ota-publication.v1', 'device': DEVICE, 'build': r['build'], 'tag': tag,
             'source': r['candidateSource'], 'recipeSource': r['recipeSource'],
             'publicationRun': os.environ.get('GITHUB_RUN_ID'), 'stageReceiptSha256': digest(directory/'yogi-ota-receipt.json'),
             'status': 'intent', 'public': False, 'latestBefore': before, 'phoneUpdatePassed': False}

    def record():
        with (out/'journal.jsonl').open('a') as f:
            f.write(json.dumps(state)+'\n')
    record()
    try:
        release = client.create_draft(tag, tag, LABEL+'\n\nSource: '+r['candidateSource']+
                                      '\nOTA-specific checks only; see yogi-ota-receipt.json.')
        state.update(releaseId=release['id'], status='draft-created')
        record()
        for name in names:
            client.upload(release['id'], directory/name)
            current = client.get_release(release['id'])
            matches = [x for x in current.get('assets', []) if x['name'] == name and x.get('state') == 'uploaded']
            if len(matches) != 1 or matches[0]['size'] != (directory/name).stat().st_size:
                raise Refused('uploaded asset is missing, ambiguous or incomplete')
            client.download(matches[0]['id'], out/name, matches[0]['size'])
            if digest(out/name) != digest(directory/name):
                raise Refused('uploaded asset readback differs; the draft stays unadvertised')
            state.update(status='uploaded-and-readback', lastAsset=name)
            record()
        if {a['name'] for a in current['assets']} != set(names) or client.latest_tag() != before:
            raise Refused('draft asset set or Latest changed; no finalization')
        state.update(status='finalize-requested', public=None)
        record()
        finalized = client.finalize_public(release['id'])
        if finalized.get('draft') is not False or finalized.get('prerelease') is not False:
            raise Refused('public finalization result unclear; inspect before any retry')
        state.update(public=True, status='public-download-pending')
        record()
        for name in names:
            url = f'https://github.com/{REPOSITORY}/releases/download/{tag}/{name}'
            client.public_download(url, out/('public-'+name), (directory/name).stat().st_size)
            if digest(out/('public-'+name)) != digest(directory/name):
                raise Refused('public asset readback differs; inspect the publication, do not retry')
        if client.latest_tag() != before:
            raise Refused('repository Latest changed during Yogi publication')
        state.update(status='ota-published', releaseUrl=f'https://github.com/{REPOSITORY}/releases/tag/{tag}',
                     otaUrl=f'https://github.com/{REPOSITORY}/releases/download/{tag}/{r["ota"]["file"]}',
                     channel='stable', ota=r['ota'], metadata=(directory/'yogi-stable').read_text().strip(),
                     publicDownloadVerified=True, latestAfter=before)
        return state
    except BaseException:
        state['status'] = 'failed-or-uncertain'
        raise
    finally:
        record()
        release_receipts.write_once(out/'publication.json', state)


# ------------------------------------------------------------------------------------------------ CLI

def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('action', choices=('describe', 'guard', 'builder', 'preflight', 'allocate', 'prepare', 'integrate',
                                      'compile', 'sign', 'install-image', 'stage', 'verify-staged', 'publish',
                                      '_execute-sign'))
    p.add_argument('operation', nargs='?', choices=('state', 'start', 'stop'))
    p.add_argument('--stage', choices=STAGES)
    p.add_argument('--access', action='store_true')
    p.add_argument('--build')
    p.add_argument('--base-attempt')
    p.add_argument('--ownership')
    p.add_argument('--dir')
    p.add_argument('--out')
    a = p.parse_args(argv)
    if a.action == '_execute-sign':
        # The child of sign(): the existing adapter with its default public-pin key discovery, in RAM-backed key cleanup.
        return module('yogi_host_sign', 'host-sign.py').main(config_path=a.dir, env=os.environ,
                                                              managed_decrypted_keys=True)
    try:
        cfg = config()
        if a.action == 'describe':
            print(json.dumps({stage: blockers(cfg, stage) for stage in STAGES}, indent=2))
        elif a.action == 'guard':
            guard(cfg, a.stage or p.error('guard needs --stage'), a.access)
        elif a.action == 'builder':
            print(control_builder(cfg, a.operation or p.error('builder needs state, start or stop'), a.ownership))
        elif a.action == 'preflight':
            print(json.dumps(preflight(cfg)))
        elif a.action == 'allocate':
            record = allocate(cfg, os.environ['GITHUB_RUN_ID'], os.environ['GITHUB_RUN_ATTEMPT'], head())
            print(json.dumps(record))
            if os.environ.get('GITHUB_OUTPUT'):
                with open(os.environ['GITHUB_OUTPUT'], 'a') as f:
                    f.write(f'build={record["build"]}\n')
        elif a.action == 'prepare':
            print(json.dumps(prepare(cfg, a.build, a.base_attempt)))
        elif a.action == 'integrate':
            print(json.dumps(integrate(cfg, a.build, a.dir or p.error('integrate needs --dir (downloaded app inputs)'))))
        elif a.action == 'compile':
            print(json.dumps(compile_build(cfg, a.build)))
        elif a.action == 'sign':
            print(json.dumps(sign(cfg, a.build, a.stage or p.error('sign needs --stage'))))
        elif a.action == 'install-image':
            print(json.dumps(install_image(cfg, a.build)))
        elif a.action == 'stage':
            print(json.dumps(stage(cfg, a.build, a.out)))
        else:
            r = verify_staged(a.dir, cfg)
            if r['recipeSource'] != os.environ.get('GITHUB_SHA') or r['runId'] != os.environ.get('GITHUB_RUN_ID'):
                raise Refused('staged OTA recipe/run differs from this same-run publisher')
            if a.action == 'publish':
                client = publisher.GitHubReleases(REPOSITORY, os.environ.get('PIXEL_OTA_TOKEN', ''))
                print(json.dumps(publish(client, a.dir, cfg, a.out)))
    except (Refused, ValueError, OSError, KeyError, publisher.PublishError, subprocess.SubprocessError,
            release_receipts.ReceiptExists) as error:
        shown = error if isinstance(error, (Refused, ValueError, publisher.PublishError)) else 'operation failed; inspect the retained receipt'
        print(f'Yogi OTA stopped: {type(error).__name__}: {shown}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
