#!/usr/bin/env python3
"""INFRA-ALPHA-120: export the verified retained Cuttlefish alpha images for the separate alpha runtime.

The images compiled by run 40765631053 were fully verified in place by run 40777816699 (recipe e07d535). That run's
attempt directory was removed after its private artifact was uploaded, so its receipt exists only as that artifact.

  check-original   hosted (os-release.yml guard job, mode emulator-alpha-export). --receipt-dir holds the artifact the
                   workflow's own actions/download-artifact fetched from run 40777816699 with the job token. Checks the
                   run (repository, workflow path, main, workflow_dispatch, recipe, success) and the artifact (id,
                   name, run, not expired, API digest) through the GitHub API, then the receipt itself: exactly the
                   receipt and SHA256SUMS, matching digests, verdict PASS of the original compile and super.img, no
                   build receipt claimed. Writes retained-verification-check.json beside it. No builder is started.
  export           builder (emulator-alpha-export job), under the shared builder lock. Re-validates the receipt (the
                   checked copy handed over inside this run), re-checks the retained build identity, then re-reads
                   every top-level image and the host package the receipt measured: each must be a regular file of
                   the measured size and sha256, and is hashed again after copying. --export-dir receives only
                   images/<name>.img, host/cvd-host_package.tar.gz, original/emulator-alpha-verification.json,
                   alpha-export.json and SHA256SUMS. A missing, linked or changed file fails the export.

Never runs lunch, make, signing, an installer, a publisher or a guest; never writes in the retained tree. Not a boot,
UI or phone result. Exit status: 0 ok, 1 check failed, 2 invalid input, 4 setup missing.
"""
import argparse
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
import urllib.request
sys.path.insert(0, str(Path(__file__).resolve().parent))
import release_receipts  # noqa: E402
from release_receipts import sha256_file  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT/'os/release-pipeline/alpha-runtime.json'
CHECK = 'retained-verification-check.json'
HEX64 = re.compile(r'[0-9a-f]{64}')

class CheckFailed(Exception):
    pass

class InvalidInput(Exception):
    pass

class SetupMissing(Exception):
    def __init__(self, missing):
        super().__init__(missing)
        self.missing = missing

def load(path=CONFIG):
    return json.loads(Path(path).read_text())

def recipe():
    result = subprocess.run(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'], capture_output=True, text=True)
    return result.stdout.strip() if result.returncode == 0 else None

def github_api(env=os.environ):
    """GET a GitHub REST path with the job token; errors name only the path template and status, never a body."""
    token, base = env.get('GITHUB_TOKEN'), env.get('GITHUB_API_URL', 'https://api.github.com')
    if not token:
        raise SetupMissing(['GITHUB_TOKEN (the job token with actions: read)'])

    def get(path):
        request = urllib.request.Request(base + path, headers={'Authorization': f'Bearer {token}',
            'Accept': 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28'})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as error:
            raise CheckFailed(f'GitHub API GET {re.sub(r"[0-9]+", "N", path)} returned HTTP {error.code}')
    return get

def regular_files(directory):
    """Relative paths of every file under directory; any link, device or other non-regular entry fails."""
    directory, found = Path(directory), []
    for path in sorted(directory.rglob('*')):
        if path.is_symlink() or not (path.is_file() or path.is_dir()):
            raise CheckFailed(f'{path.relative_to(directory)} is not a regular file or directory')
        if path.is_file():
            found.append(path.relative_to(directory).as_posix())
    return found

def read_sums(directory):
    sums = {}
    for line in (Path(directory)/'SHA256SUMS').read_text().splitlines():
        digest, sep, name = line.partition('  ')
        if not sep or not HEX64.fullmatch(digest) or name in sums or name.startswith('/') or '..' in name.split('/'):
            raise CheckFailed('SHA256SUMS has a malformed, repeated or unsafe line')
        sums[name] = digest
    return sums

def receipt_failures(config, receipt):
    """The original verification receipt's own claims, exactly as the export and runtime rely on them."""
    verification, original, image = config['retainedVerification'], config['originalCompile'], config['superImage']
    alpha = load(ROOT/config['alphaConfig'])
    signer = load(ROOT/'os/release-pipeline/frankel.json')['identities']['appSignerSha256'].lower()
    failures = []
    expect = {'receiptType': 'emulator-alpha-retained-verification', 'verdict': 'PASS', 'imageVerified': True,
        'verificationRun': verification['runId'], 'verifierRecipe': verification['recipe'], 'candidate': original['product'],
        'executedMake': False, 'signed': False, 'published': False, 'testOnly': True, 'bootAttempted': False, 'bootVerified': False}
    failures += [f'{k} is {receipt.get(k)!r}, expected {v!r}' for k, v in expect.items() if receipt.get(k) != v]
    compile_ = receipt.get('originalCompile') or {}
    for key, want in (('runId', original['runId']), ('buildDatetime', original['buildDatetime']),
                      ('emulatorBuildId', original['emulatorBuildId'])):
        if compile_.get(key) != want:
            failures.append(f'originalCompile.{key} is {compile_.get(key)!r}, expected {want!r}')
    if 'buildReceipt' not in compile_ or compile_['buildReceipt'] is not None:
        failures.append('originalCompile.buildReceipt must be recorded as null: the original compile wrote none')
    if (receipt.get('preimage') or {}) != {'size': image['size'], 'sha256': image['sha256'], 'matched': True}:
        failures.append('the receipt does not record the measured super.img preimage as matched')
    images = receipt.get('images')
    if not isinstance(images, dict) or not images:
        failures.append('the receipt measured no images')
        images = {}
    for name, row in images.items():
        if not re.fullmatch(config['export']['imagePattern'], name) or not isinstance(row, dict) \
                or type(row.get('size')) is not int or row['size'] < 0 or not HEX64.fullmatch(str(row.get('sha256'))):
            failures.append(f'images entry {name!r} is not a measured top-level image')
    if images.get('super.img') != {'size': image['size'], 'sha256': image['sha256']}:
        failures.append('images super.img is not the measured super.img')
    missing = [n for n in config['export']['requiredImages'] if n not in images]
    if missing:
        failures.append(f'the receipt did not measure required images {missing}')
    host = (receipt.get('hostOutputs') or {}).get(config['export']['hostPackage'])
    if not isinstance(host, dict) or type(host.get('size')) is not int or not HEX64.fullmatch(str(host.get('sha256'))) \
            or sorted(receipt.get('hostOutputs')) != [config['export']['hostPackage']]:
        failures.append(f'the receipt did not measure exactly the host package {config["export"]["hostPackage"]}')
    content = receipt.get('imageContent') or {}
    apps = {a.get('package'): a for a in content.get('apps') or [] if isinstance(a, dict)}
    if content.get('verified') is not True or sorted(apps) != sorted(p for p, _ in original['apps']):
        failures.append('imageContent does not verify exactly the original apps')
    for package, version in original['apps']:
        app = apps.get(package) or {}
        if app.get('present') is not True or app.get('identity') != [package, version] or app.get('signers') != [signer] \
                or not HEX64.fullmatch(str(app.get('sha256'))) or app.get('partition') not in alpha['imageVerification']['partitions']:
            failures.append(f'imageContent app {package} is not {package} {version} signed by the approved app signer')
    return failures

def check_received(config, directory, receipt_sha256=None):
    """The received receipt directory: exactly the receipt and SHA256SUMS, matching, and the receipt's claims."""
    verification = config['retainedVerification']
    files = regular_files(directory)
    if files != sorted([verification['receipt'], 'SHA256SUMS']):
        raise CheckFailed(f'the original receipt artifact holds {files}, not exactly {verification["receipt"]} and SHA256SUMS')
    sums = read_sums(directory)
    digest = sha256_file(Path(directory)/verification['receipt'])
    if sums != {verification['receipt']: digest}:
        raise CheckFailed('the original SHA256SUMS does not cover exactly the receipt with its digest')
    for pin, name in ((verification.get('receiptSha256'), 'the configured'), (receipt_sha256, 'the checked')):
        if pin is not None and digest != pin:
            raise CheckFailed(f'the original receipt sha256 {digest} is not {name} {pin}')
    receipt = json.loads((Path(directory)/verification['receipt']).read_text())
    failures = receipt_failures(config, receipt)
    if failures:
        raise CheckFailed('the original verification receipt is refused: ' + '; '.join(failures))
    return receipt, digest

def check_original(config, receipt_dir, api, env=os.environ):
    verification, repo = config['retainedVerification'], config['repository']
    if env.get('GITHUB_REPOSITORY') != repo:
        raise CheckFailed(f'this run is in {env.get("GITHUB_REPOSITORY")!r}, not {repo}')
    run = api(f'/repos/{repo}/actions/runs/{verification["runId"]}')
    observed = {'repository': (run.get('repository') or {}).get('full_name'), 'path': run.get('path'), 'head_branch': run.get('head_branch'),
        'event': run.get('event'), 'head_sha': run.get('head_sha'), 'status': run.get('status'), 'conclusion': run.get('conclusion')}
    expected = {'repository': repo, 'path': verification['workflowPath'], 'head_branch': verification['branch'],
        'event': verification['event'], 'head_sha': verification['recipe'], 'status': 'completed', 'conclusion': 'success'}
    wrong = [f'{k}={observed[k]!r}' for k in expected if observed[k] != expected[k]]
    if wrong or str(run.get('id')) != verification['runId']:
        raise CheckFailed(f'run {verification["runId"]} is not the recorded verification run: ' + ', '.join(wrong or ['id']))
    artifact = api(f'/repos/{repo}/actions/artifacts/{verification["artifactId"]}')
    digest = artifact.get('digest')
    if (artifact.get('id'), artifact.get('name'), str((artifact.get('workflow_run') or {}).get('id')), artifact.get('expired')) \
            != (verification['artifactId'], verification['artifactName'], verification['runId'], False) \
            or not isinstance(digest, str) or not re.fullmatch(r'sha256:[0-9a-f]{64}', digest):
        raise CheckFailed(f'artifact {verification["artifactId"]} is not the recorded, unexpired verification artifact with a digest')
    receipt, receipt_digest = check_received(config, receipt_dir)
    result = {'checkType': 'alpha-retained-verification-check', 'run': {k: observed[k] for k in expected} | {'id': verification['runId']},
        'artifact': {'id': artifact['id'], 'name': artifact['name'], 'digest': digest, 'size': artifact.get('size_in_bytes')},
        'receiptSha256': receipt_digest, 'checkedBy': {'run': env.get('GITHUB_RUN_ID'), 'recipe': recipe()},
        'receiptSha256Pinned': verification.get('receiptSha256') is not None}
    release_receipts.write_once(Path(receipt_dir)/CHECK, result)
    return result

def copy_measured(source, target, row, what):
    if source.is_symlink() or not source.is_file():
        raise CheckFailed(f'{what} is missing or not a regular file in the retained output')
    if source.stat().st_size != row['size'] or sha256_file(source) != row['sha256']:
        raise CheckFailed(f'{what} differs from the original verification receipt')
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    if target.stat().st_size != row['size'] or sha256_file(target) != row['sha256'] or sha256_file(source) != row['sha256']:
        raise CheckFailed(f'{what} changed while it was exported')

def export(config, ea, alpha, receipt_dir, export_dir, run_id, sha, apps, timestamp, now=time.time):
    original = config['originalCompile']
    if not re.fullmatch(r'[1-9][0-9]*', str(run_id or '')):
        raise InvalidInput('--run-id must be the numeric Actions run id')
    if timestamp != original['buildDatetime']:
        raise InvalidInput(f'--build-timestamp must be run {original["runId"]} BUILD_DATETIME {original["buildDatetime"]}')
    export_dir = Path(export_dir)
    if export_dir.exists():
        raise CheckFailed(f'{export_dir} already exists; use a new export directory')
    check = json.loads((Path(receipt_dir)/CHECK).read_text())
    files = regular_files(receipt_dir)
    if sorted(files) != sorted([config['retainedVerification']['receipt'], 'SHA256SUMS', CHECK]):
        raise CheckFailed(f'the handed-over receipt directory holds {files}')
    with tempfile.TemporaryDirectory() as scratch:
        shutil.copytree(receipt_dir, Path(scratch)/'receipt', ignore=shutil.ignore_patterns(CHECK))
        receipt, receipt_digest = check_received(config, Path(scratch)/'receipt', check.get('receiptSha256'))
    ea.guard(alpha)
    ea.check_dispatch(alpha, sha, apps)
    if sha != original['product'] or apps != original['apps']:
        raise CheckFailed('export takes exactly the original compile\'s product and apps')
    lock = ea.locked(alpha)
    try:
        _, tree, failures = ea.pinned_failures(alpha)
        if failures:
            raise CheckFailed('; '.join(failures))
        lunch = alpha['hostEvidence']['lunch']
        product_out, host_out = tree/lunch['PRODUCT_OUT'], tree/lunch['HOST_OUT']
        failures = ea.identity_failures(ea.read_props(product_out), alpha, original['emulatorBuildId'], original['buildDatetime'],
            lunch['PRODUCT_DEVICE'])
        if failures:
            raise CheckFailed(f'the retained output is not run {original["runId"]}\'s build: ' + '; '.join(failures))
        for name, row in sorted(receipt['images'].items()):
            copy_measured(product_out/name, export_dir/'images'/name, row, f'image {name}')
        host = config['export']['hostPackage']
        copy_measured(host_out/host, export_dir/'host'/host, receipt['hostOutputs'][host], f'host package {host}')
        (export_dir/'original').mkdir()
        shutil.copyfile(Path(receipt_dir)/config['retainedVerification']['receipt'], export_dir/'original'/config['retainedVerification']['receipt'])
    except BaseException:
        shutil.rmtree(export_dir, ignore_errors=True)
        raise
    finally:
        lock.close()
    document = {'receiptType': 'emulator-alpha-export', 'exportRun': str(run_id), 'exportRecipe': recipe(),
        'originalCompile': {k: original[k] for k in ('runId', 'buildDatetime', 'emulatorBuildId', 'product', 'apps', 'buildReceipt')},
        'originalVerification': {'runId': config['retainedVerification']['runId'], 'recipe': config['retainedVerification']['recipe'],
            'artifactId': check['artifact']['id'], 'artifactName': check['artifact']['name'], 'artifactDigest': check['artifact']['digest'],
            'receiptSha256': receipt_digest},
        'images': receipt['images'], 'hostPackage': {host: receipt['hostOutputs'][host]},
        'executedMake': False, 'signed': False, 'published': False, 'bootAttempted': False, 'testOnly': True,
        'notProven': ['guest boot (sys.boot_completed)', 'Rose UI, viewer, input or Stop behaviour',
            'files the original receipt did not measure (none are exported)', 'Pixel hardware, firmware, radio or flashing'],
        'at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(now()))}
    release_receipts.write_once(export_dir/config['export']['receipt'], document)
    names = regular_files(export_dir)
    (export_dir/'SHA256SUMS').write_text(''.join(f'{sha256_file(export_dir/n)}  {n}\n' for n in names))
    return document

def emulator_alpha():
    spec = importlib.util.spec_from_file_location('emulator_alpha', ROOT/'scripts/emulator-alpha.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def main(argv=None, ea=None, api=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('command', choices=['check-original', 'export'])
    p.add_argument('--config', default=str(CONFIG))
    p.add_argument('--receipt-dir', required=True)
    p.add_argument('--export-dir')
    p.add_argument('--candidate-sha')
    p.add_argument('--apps', default='[]')
    p.add_argument('--run-id')
    p.add_argument('--build-timestamp')
    a = p.parse_args(argv)
    try:
        config = load(a.config)
        if a.command == 'check-original':
            result = check_original(config, a.receipt_dir, api or github_api())
        else:
            if not a.export_dir:
                raise InvalidInput('--export-dir is required')
            ea = ea or emulator_alpha()
            alpha = ea.load(ROOT/config['alphaConfig'])
            try:
                result = export(config, ea, alpha, a.receipt_dir, a.export_dir, a.run_id, a.candidate_sha, json.loads(a.apps), a.build_timestamp)
            except ea.SetupMissing as error:
                raise SetupMissing(error.missing)
            except ea.CheckFailed as error:
                raise CheckFailed(str(error))
            except ea.InvalidInput as error:
                raise InvalidInput(str(error))
    except SetupMissing as error:
        print(json.dumps({'verdict': 'SETUP_MISSING', 'missing': error.missing}), file=sys.stderr)
        return 4
    except InvalidInput as error:
        print(json.dumps({'verdict': 'INVALID', 'error': str(error)}), file=sys.stderr)
        return 2
    except (CheckFailed, release_receipts.ReceiptExists) as error:
        print(json.dumps({'verdict': 'FAIL', 'error': str(error)[:4096]}), file=sys.stderr)
        return 1
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(json.dumps({'verdict': 'FAIL', 'error': error.__class__.__name__}), file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0

if __name__ == '__main__':
    sys.exit(main())
