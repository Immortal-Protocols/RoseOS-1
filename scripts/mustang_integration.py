#!/usr/bin/env python3
"""Mustang sprint integration: mustang (Pixel 10 Pro XL) Rose integration inputs for the existing host-reconcile.py.

  admit  --bundle DIR --updater-route ROUTE.json --out NEW_DIR --aapt PATH --apksigner PATH
  render --inputs NEW_DIR/local-inputs.json --source-root DIR --lock PATH --receipt-dir DIR --work-dir DIR --out NEW.json

admit verifies the release owner's already-signed bundle (no key is read or needed): exactly
signed-apps/<package>.apk with its sign-apk.py .receipt.json for the three Rose apps, background-policy.json and
candidate.json (roseos.mustang-candidate.v1: device mustang, build, product C, recipe R, androidManifestBlob, apps as
[[package, versionCode], ...]). It requires this checkout to be R; C and R to carry the pinned Android manifest blob; C
to carry the Launcher3 and Updater release-discovery changes; every receipt to match its bytes and the existing app
signer; aapt/apksigner (background_apks.identity, SDK 37) to confirm package, version and signer; and the policy
(background_apks.validate_policy) to pin exactly those bytes with Frankel's shipped control policy (controller as
sole prototype agent). It also requires the release owner's explicit mustang Updater route: without one it refuses and
names the missing input, never a default, Frankel's Latest or Rango's feed. The bytes are copied unchanged and
NEW_DIR/local-inputs.json (mustang-local-inputs) is written last; on any refusal NEW_DIR is removed and no provenance
exists.

render writes the operator's host-reconcile.py --config (write-once) from os/build-only/mustang-reconcile.json, the
admitted inputs and the operator's host paths, refusing symlinks, relative paths and paths shared with the Frankel or
Rango builders (only --lock may be exactly Frankel's shared host.lockPath, the intended serialization point), and prints the host-reconcile command (label mustang-BUILD) to run under the operator's lock.

compile_problems is the compile-preflight binding scripts/mustang-build-only.py calls: the reconcile receipt must be
labelled for the admitted build, have installed the admitted APK bytes, Launcher3 and the routed Updater config, and
compiled app/signer and controller-policy verification are omitted for this sprint. Exit status: 0 ok, 1 refused, 2 invalid usage.
"""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

SCRIPTS = Path(__file__).resolve().parent
ROOT = SCRIPTS.parent
sys.path.insert(0, str(SCRIPTS))
import background_apks  # noqa: E402
import release_receipts  # noqa: E402

TEMPLATE = ROOT/'os/build-only/mustang-reconcile.json'
DEVICE = 'mustang'
ROUTE_SCHEMA = 'roseos.mustang-updater-route.v1'
RELEASE_REPO = re.compile(r'https://github\.com/([A-Za-z0-9-]+/[A-Za-z0-9._-]+)/releases/download/')
PRODUCER = {'tag': 'mustang-{build}', 'metadataAsset': 'mustang-stable', 'otaAsset': 'mustang-ota_update-{build}.zip',
    'metadataLine': '{build} {buildDateUtc} mustang stable'}
INSTALLED = {'agent': 'packages/apps/PixelBackgroundControl/Agent.apk',
    'preinstall': 'packages/apps/RosePreinstall/RosePreinstall_{module}/app.apk'}
POLICY_CONFIG = 'frameworks/base/core/res/res/values/config.xml'
UPDATER_CONFIG = 'packages/apps/Updater/res/values/config.xml'
OTHER_BUILDERS = ('os/release-pipeline/frankel.json', 'os/build-only/rango.json', 'os/build-only/rango-isolated.json',
    'os/build-only/rango-reconcile.json', 'os/build-only/rango-reconcile-next.json',
    'os/build-only/blazer-prep-host.json')

class Failed(Exception):
    pass

def sha256_file(path):
    return release_receipts.sha256_file(path)

def git(*args):
    """Read-only git in this checkout (the recipe); None when the object or repository is unavailable."""
    result = subprocess.run(['git', '-C', str(ROOT), *args], capture_output=True, text=True)
    return result.stdout.strip() if result.returncode == 0 else None

def load_template(path=TEMPLATE, root=ROOT):
    """The template, cross-checked against the shipped Frankel identities and control policy; Failed on drift."""
    template = json.loads(Path(path).read_text())
    pins, frankel = template['candidateInputs'], json.loads((root/'os/release-pipeline/frankel.json').read_text())
    args = template['reconcile']['applyArgs']
    problems = []
    if template.get('kind') != 'rose-mustang-reconcile-template' or template.get('device') != DEVICE:
        raise Failed('not the mustang reconcile template')
    if pins['appSignerSha256'] != frankel['identities']['appSignerSha256']:
        problems.append('appSignerSha256 is not the existing app signer')
    if pins['prototypeControl'] is not bool(frankel['framework'].get('prototypeAgents')) \
            or frankel['framework'].get('lockedSessions') is not False:
        problems.append('control policy differs from the shipped Frankel policy')
    if {'--locked-control', '--keep-upstream-updater', '--installed-identities', '--reference-display-address'} & set(args):
        problems.append('applyArgs must not lock sessions, keep the upstream Updater, or borrow another device\'s identities')
    value = lambda flag: args[args.index(flag) + 1] if flag in args[:-1] else None
    if [value(f) for f in ('--updater-url', '--updater-release-index', '--updater-published-channel')] != \
            ['{updater_url}', '{updater_release_index}', '{updater_channel}']:
        problems.append('the Updater route must come from the admitted route, never a fixed URL')
    for project in ('packages/apps/Launcher3', 'packages/apps/Updater', 'frameworks/base'):
        if project not in template['reconcile']['projects']:
            problems.append(f'{project} must be reconciled')
    if template['reconcile']['patcher'] != 'trusted':
        problems.append('patcher must be trusted (this checkout)')
    if problems:
        raise Failed('; '.join(problems))
    return template

def regular(path, label):
    path = Path(path)
    if not path.is_absolute() or path.is_symlink() or not path.is_file():
        raise Failed(f'{label} {path} must be an absolute regular file, not a link')
    return path

def check_route(route_path):
    """The release owner's explicit mustang Updater route; refuses anything else and names the missing input."""
    missing = load_template()['updaterRoute']['missingInput']
    if route_path is None:
        raise Failed(f'no mustang Updater route: supply {missing}')
    path = regular(route_path, '--updater-route')
    try:
        route = json.loads(path.read_text())
    except ValueError:
        raise Failed('--updater-route is not JSON')
    keys = {'schema', 'device', 'updaterUrl', 'releaseIndex', 'publishedChannel', 'producerContract', 'owner', 'approvalRef'}
    if not isinstance(route, dict) or set(route) != keys or route['schema'] != ROUTE_SCHEMA:
        raise Failed(f'--updater-route must be exactly {sorted(keys)} with schema {ROUTE_SCHEMA}')
    if route['device'] != DEVICE:
        raise Failed(f'--updater-route is for {route["device"]!r}, not {DEVICE}')
    repo = RELEASE_REPO.fullmatch(str(route['updaterUrl']))
    if not repo or route['releaseIndex'] != f'https://api.github.com/repos/{repo[1]}/releases':
        raise Failed('--updater-route must be a per-release route: updaterUrl https://github.com/OWNER/REPO/releases/'
            'download/ and releaseIndex https://api.github.com/repos/OWNER/REPO/releases (never releases/latest)')
    if route['publishedChannel'] != 'stable' or route['producerContract'] != PRODUCER:
        raise Failed(f'--updater-route must publish channel stable with the mustang producer contract {PRODUCER}')
    for key in ('owner', 'approvalRef'):
        if not isinstance(route[key], str) or not route[key].strip():
            raise Failed(f'--updater-route {key} must name the release owner\'s decision')
    return route, {'path': str(path), 'sha256': sha256_file(path)}

def bundle_files(bundle, packages):
    bundle = Path(bundle)
    if not bundle.is_absolute() or '..' in bundle.parts or bundle.is_symlink() or not bundle.is_dir():
        raise Failed('--bundle must be an existing absolute directory, not a link')
    expected = {'candidate.json', 'background-policy.json', 'signed-apps'}
    expected |= {f'signed-apps/{p}.apk' for p in packages} | {f'signed-apps/{p}.receipt.json' for p in packages}
    found = set()
    for path in sorted(bundle.rglob('*')):
        relative = path.relative_to(bundle).as_posix()
        if path.is_symlink() or not (path.is_dir() or path.is_file()):
            raise Failed(f'bundle entry {relative} is a link or not a regular file')
        found.add(relative)
    if found != expected:
        raise Failed(f'bundle inventory: missing {sorted(expected - found)}, unexpected {sorted(found - expected)}')
    return bundle

def candidate_binding(bundle, pins, git_read):
    try:
        candidate = json.loads((bundle/'candidate.json').read_text())
    except ValueError:
        raise Failed('candidate.json is not JSON')
    keys = {'schema', 'device', 'build', 'product', 'recipe', 'androidManifestBlob', 'apps'}
    if not isinstance(candidate, dict) or set(candidate) != keys or candidate['schema'] != pins['schema']:
        raise Failed(f'candidate.json must be exactly {sorted(keys)} with schema {pins["schema"]}')
    if candidate['device'] != DEVICE or not re.fullmatch(r'[1-9][0-9]{9}', str(candidate['build'])):
        raise Failed('candidate.json must name device mustang and a ten-digit build')
    for key in ('product', 'recipe'):
        if not isinstance(candidate[key], str) or not re.fullmatch(r'[0-9a-f]{40}', candidate[key]):
            raise Failed(f'candidate.json {key} must be a full commit SHA')
    if candidate['androidManifestBlob'] != pins['androidManifestBlob']:
        raise Failed('candidate.json names another Android manifest than the pinned mustang base')
    apps, roles = candidate['apps'], {a['package'] for a in pins['apps']}
    if (not isinstance(apps, list) or any(not isinstance(a, list) or len(a) != 2 or type(a[1]) is not int or a[1] < 1
            for a in apps) or len({a[0] for a in apps}) != len(apps) or {a[0] for a in apps} != roles):
        raise Failed(f'candidate.json apps must name each of {sorted(roles)} once with a positive versionCode')
    if git_read('rev-parse', 'HEAD') != candidate['recipe']:
        raise Failed('this checkout is not the candidate\'s recipe R')
    for key in ('product', 'recipe'):
        if git_read('rev-parse', f'{candidate[key]}:os/pinned-manifest.xml') != pins['androidManifestBlob']:
            raise Failed(f'{key} {candidate[key][:12]} does not carry the pinned Android manifest')
    for name in pins['candidateFiles']:
        if git_read('rev-parse', f'{candidate["product"]}:{name}') is None:
            raise Failed(f'product {candidate["product"][:12]} lacks {name} (Launcher3/Updater integration)')
    return candidate

def admit(bundle, route_path, out, tools, template_path=TEMPLATE, git_read=git, now=time.time):
    template = load_template(template_path)
    pins = template['candidateInputs']
    out = Path(out)
    if not out.is_absolute() or '..' in out.parts:
        raise Failed('--out must be an absolute path without ..')
    if os.path.lexists(out):
        raise Failed(f'{out} exists; choose a new inputs directory')
    if not out.parent.is_dir() or out.parent.is_symlink():
        raise Failed(f'{out.parent} must be an existing directory, not a link')
    route, route_record = check_route(route_path)
    tool_record = {}
    for name in ('aapt', 'apksigner'):
        path = regular(tools.get(name) or '', f'--{name}')
        if not os.access(path, os.X_OK):
            raise Failed(f'{path} is not executable')
        tool_record[name] = {'path': str(path), 'sha256': sha256_file(path)}
    bundle = bundle_files(bundle, sorted(a['package'] for a in pins['apps']))
    candidate = candidate_binding(bundle, pins, git_read)
    versions, signer = dict(map(tuple, candidate['apps'])), pins['appSignerSha256']
    apps = []
    for pin in pins['apps']:
        package = pin['package']
        apk, receipt_path = bundle/'signed-apps'/f'{package}.apk', bundle/'signed-apps'/f'{package}.receipt.json'
        digest = sha256_file(apk)
        try:
            receipt = json.loads(receipt_path.read_text())
        except ValueError:
            raise Failed(f'{receipt_path.name} is not JSON')
        if (receipt.get('schemaVersion') != 1 or receipt.get('apk') != apk.name or receipt.get('sha256') != digest
                or receipt.get('certificateSha256') != [signer] or receipt.get('signatureVerified') is not True):
            raise Failed(f'{receipt_path.name} does not match {apk.name} (bytes, signer or verification)')
        try:
            found = background_apks.identity(apk, tool_record['aapt']['path'], tool_record['apksigner']['path'], pins['sdk'])
        except (ValueError, subprocess.CalledProcessError) as error:
            raise Failed(f'{apk.name}: identity not verified ({error})')
        if (found['packageName'], found['versionCode'], found['certificates']) != (package, versions[package], [signer.upper()]):
            raise Failed(f'{apk.name}: package/version/signer {(found["packageName"], found["versionCode"], found["certificates"])}')
        apps.append({'role': pin['role'], 'package': package, 'versionCode': versions[package], 'signerSha256': signer,
            'sha256': digest, 'sizeBytes': apk.stat().st_size, 'receiptSha256': sha256_file(receipt_path)})
    policy_path = bundle/'background-policy.json'
    try:
        written = background_apks.validate_policy(json.loads(policy_path.read_text()),
            bundle/'signed-apps'/'dev.pixel.backgroundcontrol.apk')
    except (ValueError, KeyError, TypeError) as error:
        raise Failed(f'background-policy.json is not a valid policy: {error}')
    if written['sdk'] != pins['sdk'] or written.get('prototypeControl', False) is not pins['prototypeControl'] \
            or len(written['targets']) != 1:
        raise Failed('the policy SDK, control policy (prototypeControl) or target count differs from the pin')
    rows = {'agent': written['agent'], 'voice': written['voice'], 'target': written['targets'][0]}
    for app in apps:
        row = rows[app['role']]
        if (row['packageName'], row['versionCode'], row['apkSha256'], row['certificates']) != \
                (app['package'], app['versionCode'], app['sha256'], [signer.upper()]):
            raise Failed(f'the policy does not pin the admitted {app["package"]} bytes')
    try:
        out.mkdir(mode=0o700)
        (out/'signed-apps').mkdir()
        for app in apps:
            target = out/'signed-apps'/f'{app["package"]}.apk'
            shutil.copyfile(bundle/'signed-apps'/target.name, target)
            target.chmod(0o444)
            if sha256_file(target) != app['sha256']:
                raise Failed(f'{target.name} changed while being copied')
            app['signedPath'] = str(target)
        policy = out/'background-policy.json'
        shutil.copyfile(policy_path, policy)
        policy.chmod(0o444)
        if sha256_file(policy) != sha256_file(policy_path):
            raise Failed('background-policy.json changed while being copied')
        record = {'receiptType': 'mustang-local-inputs', 'schemaVersion': 1,
            'candidate': {k: candidate[k] for k in ('device', 'build', 'product', 'recipe', 'androidManifestBlob')},
            'template': {'path': str(template_path), 'sha256': sha256_file(template_path)},
            'bundle': {'path': str(bundle), 'candidateSha256': sha256_file(bundle/'candidate.json')},
            'updaterRoute': {**route_record, 'updaterUrl': route['updaterUrl'], 'releaseIndex': route['releaseIndex'],
                'publishedChannel': route['publishedChannel']},
            'tools': tool_record, 'apps': apps,
            'policy': {'path': str(policy), 'sha256': sha256_file(policy), 'sdk': written['sdk'],
                'prototypeControl': written.get('prototypeControl', False)},
            'signedApps': str(out/'signed-apps'), 'network': False, 'signed': False, 'published': False,
            'at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(now()))}
        release_receipts.write_once(out/'local-inputs.json', record)
    except BaseException:
        shutil.rmtree(out, ignore_errors=True)
        raise
    return record

def read_inputs(path):
    if path is None:
        raise Failed('missing')
    path = regular(path, '--inputs')
    inputs = json.loads(path.read_text())
    if inputs.get('receiptType') != 'mustang-local-inputs' or (inputs.get('candidate') or {}).get('device') != DEVICE:
        raise Failed('--inputs is not a mustang-local-inputs receipt')
    return inputs

def other_builder_paths(root=ROOT):
    paths = set()
    for relative in OTHER_BUILDERS:
        host = json.loads((root/relative).read_text()).get('host', {})
        reconcile = host.get('integrate', {}).get('reconcile', {})
        paths |= {Path(p) for p in (host.get('sourceRoot'), host.get('lockPath'), host.get('reconcileReceiptDir'),
            host.get('workDir'), host.get('inputsDir'), reconcile.get('receiptDir')) if p}
    return paths

def shared_lock(root=ROOT):
    """The builder lock mustang shares with Frankel by design (mustang.json lockFrom): frankel.json host.lockPath. It is
    the one other-builder path an operator path may equal, and only as lockPath, so host-reconcile serializes on it."""
    value = json.loads((root/OTHER_BUILDERS[0]).read_text()).get('host', {}).get('lockPath')
    return Path(value) if value else None

def render(inputs_path, source_root, lock, receipt_dir, work_dir, out, template_path=TEMPLATE, root=ROOT):
    """(rendered config, host-reconcile command). The config is written once, only after every check passes."""
    template, inputs = load_template(template_path, root), read_inputs(inputs_path)
    if inputs['template']['sha256'] != sha256_file(template_path):
        raise Failed('the inputs were admitted against another reconcile template')
    host = {'sourceRoot': source_root, 'lockPath': lock, 'receiptDir': receipt_dir, 'workDir': work_dir}
    taken, shared = other_builder_paths(root), shared_lock(root)
    for name, value in host.items():
        path = Path(value or '')
        if not path.is_absolute() or '..' in path.parts:
            raise Failed(f'{name} must be an absolute path without .. (operator input)')
        current = Path(path.anchor)
        for part in path.parts[1:]:
            current = current/part
            if current.is_symlink():
                raise Failed(f'{name} passes through a symlink ({current})')
        # Exactly the shared Frankel lock, as lockPath only, is the intended serialization point, not a collision; it is
        # still checked against every other builder path. Every data path keeps the full check, the shared lock included.
        others = taken - {shared} if name == 'lockPath' and shared is not None and path == shared else taken
        if path in others or any(path == t or t in path.parents or path in t.parents for t in others):
            raise Failed(f'{name} {path} collides with another device\'s builder paths')
    if not Path(source_root).is_dir():
        raise Failed(f'sourceRoot {source_root} is not an existing directory')
    out = Path(out)
    if not out.is_absolute() or os.path.lexists(out) or not out.parent.is_dir():
        raise Failed(f'--out {out} must be a new absolute file in an existing directory')
    route = inputs['updaterRoute']
    values = {'updater_url': route['updaterUrl'], 'updater_release_index': route['releaseIndex'],
        'updater_channel': route['publishedChannel']}
    reconcile = dict(template['reconcile'], receiptDir=str(receipt_dir),
        applyArgs=[a.format(policy='{policy}', signed_apps='{signed_apps}', **values) for a in template['reconcile']['applyArgs']])
    config = {'schemaVersion': 1, 'kind': 'rose-mustang-reconcile', 'device': DEVICE,
        'host': {'sourceRoot': str(source_root), 'lockPath': str(lock), 'workDir': str(work_dir),
            'integrate': {'reconcile': reconcile}},
        'provenance': {'inputs': {'path': str(inputs_path), 'sha256': sha256_file(inputs_path)},
            'template': inputs['template'], 'candidate': inputs['candidate'], 'updaterRoute': route}}
    release_receipts.write_once(out, config)
    build, product = inputs['candidate']['build'], inputs['candidate']['product']
    command = ['flock', '--nonblock', '--conflict-exit-code', '75', str(lock), sys.executable,
        str(SCRIPTS/'host-reconcile.py'), 'apply', f'{work_dir}/product-{product[:12]}', f'{work_dir}/reconcile-{build}',
        inputs['policy']['path'], inputs['signedApps'], '--config', str(out), '--label', f'{DEVICE}-{build}']
    return config, command

def compile_problems(record, src, inputs_path, git_read=git):
    """Compile-preflight binding: the newest reconcile receipt (record) against the admitted inputs and the tree."""
    try:
        inputs = read_inputs(inputs_path)
    except (Failed, ValueError) as error:
        return [f'compile needs --inputs-receipt, the mustang local-inputs.json: {error}']
    problems, entries, src = [], record.get('entries') or {}, Path(src)
    candidate = inputs['candidate']
    if record.get('label') != f'{DEVICE}-{candidate["build"]}':
        problems.append(f'the reconcile receipt is labelled {record.get("label")!r}, not {DEVICE}-{candidate["build"]}')
    if git_read('rev-parse', 'HEAD') != candidate['recipe']:
        problems.append('this checkout is not the recipe R the inputs were admitted with')
    # Nathan removed packaged app/signer and compiled-controller-policy verification
    # for this sprint. Input admission still identifies the existing signed apps;
    # successful reconciliation is not evidence of their presence in compiled images.
    return problems

def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest='action', required=True)
    a_admit = sub.add_parser('admit')
    for flag in ('--bundle', '--out', '--aapt', '--apksigner'):
        a_admit.add_argument(flag, required=True)
    a_admit.add_argument('--updater-route')
    a_render = sub.add_parser('render')
    for flag in ('--inputs', '--source-root', '--lock', '--receipt-dir', '--work-dir', '--out'):
        a_render.add_argument(flag, required=True)
    a = p.parse_args(argv)
    try:
        if a.action == 'admit':
            record = admit(a.bundle, a.updater_route, a.out, {'aapt': a.aapt, 'apksigner': a.apksigner})
            print(json.dumps({'verdict': 'OK', 'inputs': str(Path(a.out)/'local-inputs.json'),
                'build': record['candidate']['build']}, indent=2))
        else:
            _, command = render(a.inputs, a.source_root, a.lock, a.receipt_dir, a.work_dir, a.out)
            print(json.dumps({'verdict': 'OK', 'config': a.out, 'command': command}, indent=2))
        return 0
    except (Failed, release_receipts.ReceiptExists) as error:
        print(json.dumps({'verdict': 'REFUSED', 'error': str(error)}), file=sys.stderr)
        return 1

if __name__ == '__main__':
    sys.exit(main())
