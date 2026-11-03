#!/usr/bin/env python3
"""BLAZER-CANDIDATE-BUNDLE (Release-owned): assemble the already-signed candidate bundle that Flavors'
scripts/blazer_integration.py admit consumes, from actual signed bytes only.

  assemble --apps-dir DIR --policy FILE --build B --product C --aapt PATH --apksigner PATH --out NEW_DIR

DIR holds, for each of the three Rose apps pinned in os/build-only/blazer-reconcile.json candidateInputs.apps, exactly
<package>.apk and the sign-apk.py <package>.receipt.json that an existing signing run produced with the existing app
signer. Every value written comes from those bytes, this checkout or git objects, never from expected values:
  - each receipt must match its APK bytes, name the pinned app signer and record a verified signature;
  - package, versionCode and signer are read from the APK itself with the given aapt and apksigner
    (background_apks.identity, the pinned SDK); the versionCode in candidate.json is the one read here;
  - the policy must be a valid background policy (background_apks.validate_policy) that pins exactly these bytes and
    versions, with the pinned SDK and control policy and one target;
  - the recipe R is this checkout's HEAD, and both R and product C must carry the pinned Android manifest blob; C must
    carry the Launcher3 and Updater integration files;
  - the build is the ten-digit build number allocated for this candidate (the caller's, never derived here).
NEW_DIR must not exist; the files are copied unchanged and re-hashed, and candidate.json is written last. On any
refusal NEW_DIR is removed. Nothing is signed, published or fetched; no key or secret is read. Admission
(blazer_integration.py admit) re-checks everything independently. Exit status: 0 assembled, 1 refused, 2 invalid usage.
"""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
import background_apks  # noqa: E402
import blazer_integration  # noqa: E402

BUILD = re.compile(r'[1-9][0-9]{9}')
COMMIT = re.compile(r'[0-9a-f]{40}')


class Refused(Exception):
    pass


def regular(path, label):
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts or path.is_symlink() or not path.is_file():
        raise Refused(f'{label} {path} must be an absolute regular file, not a link')
    return path


def app_inputs(apps_dir, packages):
    apps_dir = Path(apps_dir)
    if not apps_dir.is_absolute() or '..' in apps_dir.parts or apps_dir.is_symlink() or not apps_dir.is_dir():
        raise Refused('--apps-dir must be an existing absolute directory, not a link')
    expected = {f'{p}.apk' for p in packages} | {f'{p}.receipt.json' for p in packages}
    found = {entry.name for entry in apps_dir.iterdir()}
    if found != expected:
        raise Refused(f'--apps-dir inventory: missing {sorted(expected - found)}, unexpected {sorted(found - expected)}')
    for name in sorted(found):
        regular(apps_dir/name, 'app input')
    return apps_dir


def assemble(apps_dir, policy_path, build, product, tools, out, template_path=blazer_integration.TEMPLATE,
             git_read=blazer_integration.git, identity=background_apks.identity):
    template = blazer_integration.load_template(template_path)
    pins = template['candidateInputs']
    signer = pins['appSignerSha256']
    out = Path(out)
    if not out.is_absolute() or '..' in out.parts:
        raise Refused('--out must be an absolute path without ..')
    if os.path.lexists(out):
        raise Refused(f'{out} exists; choose a new bundle directory')
    if not out.parent.is_dir() or out.parent.is_symlink():
        raise Refused(f'{out.parent} must be an existing directory, not a link')
    if not BUILD.fullmatch(str(build)):
        raise Refused('--build must be the ten-digit allocated build number')
    if not COMMIT.fullmatch(str(product)):
        raise Refused('--product must be a full commit SHA')
    recipe = git_read('rev-parse', 'HEAD')
    if not recipe or not COMMIT.fullmatch(recipe):
        raise Refused('this checkout has no readable HEAD to be the recipe')
    for label, commit in (('product', product), ('recipe', recipe)):
        if git_read('rev-parse', f'{commit}:os/pinned-manifest.xml') != pins['androidManifestBlob']:
            raise Refused(f'{label} {commit[:12]} does not carry the pinned Android manifest')
    for name in pins['candidateFiles']:
        if git_read('rev-parse', f'{product}:{name}') is None:
            raise Refused(f'product {product[:12]} lacks {name} (Launcher3/Updater integration)')
    for name in ('aapt', 'apksigner'):
        path = regular(tools.get(name) or '', f'--{name}')
        if not os.access(path, os.X_OK):
            raise Refused(f'{path} is not executable')
    packages = [a['package'] for a in pins['apps']]
    apps_dir = app_inputs(apps_dir, packages)
    apps = []
    for package in packages:
        apk, receipt_path = apps_dir/f'{package}.apk', apps_dir/f'{package}.receipt.json'
        digest = blazer_integration.sha256_file(apk)
        try:
            receipt = json.loads(receipt_path.read_text())
        except ValueError:
            raise Refused(f'{receipt_path.name} is not JSON')
        if (not isinstance(receipt, dict) or receipt.get('schemaVersion') != 1 or receipt.get('apk') != apk.name
                or receipt.get('sha256') != digest or receipt.get('certificateSha256') != [signer]
                or receipt.get('signatureVerified') is not True):
            raise Refused(f'{receipt_path.name} does not match {apk.name} (bytes, signer or verification)')
        try:
            found = identity(apk, tools['aapt'], tools['apksigner'], pins['sdk'])
        except (ValueError, OSError, subprocess.CalledProcessError) as error:
            raise Refused(f'{apk.name}: identity not verified ({error})')
        if found.get('packageName') != package or found.get('certificates') != [signer.upper()] \
                or type(found.get('versionCode')) is not int or found['versionCode'] < 1:
            raise Refused(f'{apk.name}: the APK is not {package} signed by the existing app signer')
        apps.append({'package': package, 'versionCode': found['versionCode'], 'sha256': digest,
                     'receiptSha256': blazer_integration.sha256_file(receipt_path)})
    policy_path = regular(policy_path, '--policy')
    try:
        written = background_apks.validate_policy(json.loads(policy_path.read_text()),
                                                  apps_dir/'dev.pixel.backgroundcontrol.apk')
    except (ValueError, KeyError, TypeError) as error:
        raise Refused(f'--policy is not a valid background policy: {error}')
    if written['sdk'] != pins['sdk'] or written.get('prototypeControl', False) is not pins['prototypeControl'] \
            or len(written['targets']) != 1:
        raise Refused('the policy SDK, control policy (prototypeControl) or target count differs from the pin')
    rows = {'agent': written['agent'], 'voice': written['voice'], 'target': written['targets'][0]}
    roles = {a['package']: a['role'] for a in pins['apps']}
    for app in apps:
        row = rows[roles[app['package']]]
        if (row['packageName'], row['versionCode'], row['apkSha256'], row['certificates']) != \
                (app['package'], app['versionCode'], app['sha256'], [signer.upper()]):
            raise Refused(f'the policy does not pin the signed {app["package"]} bytes and version')
    candidate = {'schema': pins['schema'], 'device': blazer_integration.DEVICE, 'build': str(build), 'product': product,
                 'recipe': recipe, 'androidManifestBlob': pins['androidManifestBlob'],
                 'apps': [[a['package'], a['versionCode']] for a in apps]}
    try:
        out.mkdir(mode=0o700)
        (out/'signed-apps').mkdir()
        copies = [(apps_dir/f'{a["package"]}.apk', a['sha256']) for a in apps]
        copies += [(apps_dir/f'{a["package"]}.receipt.json', a['receiptSha256']) for a in apps]
        for source, digest in copies:
            target = out/'signed-apps'/source.name
            shutil.copyfile(source, target)
            target.chmod(0o444)
            if blazer_integration.sha256_file(target) != digest:
                raise Refused(f'{source.name} changed while being copied')
        policy = out/'background-policy.json'
        expected = blazer_integration.sha256_file(policy_path)
        shutil.copyfile(policy_path, policy)
        policy.chmod(0o444)
        if blazer_integration.sha256_file(policy) != expected:
            raise Refused('the policy changed while being copied')
        fd = os.open(out/'candidate.json', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o444)
        with os.fdopen(fd, 'w') as f:
            f.write(json.dumps(candidate, indent=2, sort_keys=True) + '\n')
    except BaseException:
        shutil.rmtree(out, ignore_errors=True)
        raise
    return candidate


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('command', choices=('assemble',))
    for flag in ('--apps-dir', '--policy', '--build', '--product', '--aapt', '--apksigner', '--out'):
        p.add_argument(flag, required=True)
    a = p.parse_args(argv)
    try:
        candidate = assemble(a.apps_dir, a.policy, a.build, a.product, {'aapt': a.aapt, 'apksigner': a.apksigner}, a.out)
    except (Refused, blazer_integration.Failed) as error:
        print(json.dumps({'verdict': 'REFUSED', 'error': str(error)}), file=sys.stderr)
        return 1
    print(json.dumps({'verdict': 'ASSEMBLED', 'candidate': candidate}, sort_keys=True))
    return 0


if __name__ == '__main__':
    sys.exit(main())
