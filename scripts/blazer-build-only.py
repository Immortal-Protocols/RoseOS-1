#!/usr/bin/env python3
"""FAM-BLAZER-BUILD-004: unsigned build-only recipe for blazer (Pixel 10 Pro), admission and command rendering.

The recipe (os/build-only/blazer.json) binds only checked-in pinned sources: lunch blazer-cur-user, the indexed factory
image name and sha256, the vendor/adevtool, vendor/state and laguna kernel pins of os/pinned-manifest.xml, generation
with an explicit -d blazer and an unsigned compile into OUT_DIR out-blazer-build. It binds no host. The operator
supplies, and this tool admits, every host fact:

  --host LABEL        the operator's name for the builder (recorded, never resolved or created)
  --source-root DIR   absolute, existing, symlink-free checkout of the pinned manifest
  --jobs N            make -j value, 1..compile.maxJobs
  --lock PATH         the builder's lock file (probed, never kept by preflight)
  --download-dir DIR  generate: absent or empty, outside the tree, on the tree's filesystem
  --integration-dir DIR   compile: the Rose reconcile receipts for this tree
  --inputs-receipt FILE   compile: the local-inputs.json of scripts/blazer_integration.py admit

  blazer-build-only.py plan      --stage S ...   the rendered command and the preflight report; runs nothing
  blazer-build-only.py preflight --stage S ...   the report alone (exit 0 ready, 1 not ready)
  blazer-build-only.py run       --stage S ... --receipt-dir NEW   preflight, then the command under the lock

Preflight reads only and refuses on: a recipe that drifts from the device contract, manifest, build index or flavor;
a symlink anywhere in an operator path; a missing source tree entry, adevtool host tool or pinned tool revision; an
existing generated vendor tree (generate) or a missing or foreign-revision one (compile); an existing OUT_DIR; a held
lock; missing or foreign Rose integration (compile); an unmeasured allowance, or available minus allowance below the
reserve. There is no isolated-host allowance exception. Exit status: 0 ready/success, 1 not ready or command failed,
2 invalid input. It reaches no network, signer, secret, publisher, host or phone itself; the output would be unsigned
test-keys and never release-ready. Source tests use synthetic trees and a fake runner only.
"""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from xml.etree import ElementTree

sys.path.insert(0, str(Path(__file__).resolve().parent))
import release_receipts  # noqa: E402
import blazer_integration  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
RECIPE = ROOT/'os/build-only/blazer.json'
DEVICE = 'blazer'
STAGES = ('generate', 'compile')
BUILD_INDEX = 'os/tests/fixtures/adevtool-17df3b79/adevtool/config/build-index/build-index-main.yml.excerpt.json'
FACTORY_URL = 'https://dl.google.com/dl/android/aosp/'
TREE_NEEDS = ('vendor/adevtool/bin/run', 'build/envsetup.sh', 'prebuilts/build-tools/path/linux-x86',
    'prebuilts/jdk/jdk21/linux-x86/bin')
HOST_LABEL = re.compile(r'[a-z0-9][a-z0-9.-]{0,62}')

class InvalidInput(Exception):
    pass

def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()

def index_block(root, build_id):
    """The indented entries under 'blazer BUILD_ID:' in the pinned adevtool build index excerpt."""
    lines = json.loads((root/BUILD_INDEX).read_text())['lines']
    block, inside = [], False
    for _, text in sorted(lines.items(), key=lambda item: int(item[0])):
        if not text.startswith(' '):
            inside = text.strip() == f'{DEVICE} {build_id}:'
        elif inside:
            block.append(text.strip())
    return block

def load(path=RECIPE, root=ROOT):
    """The recipe, checked against its checked-in sources; InvalidInput on any drift."""
    recipe = json.loads(Path(path).read_text())
    problems = []
    if recipe.get('kind') != 'rose-build-only-recipe' or recipe.get('device') != DEVICE:
        raise InvalidInput(f'not a {DEVICE} build-only recipe; this route builds {DEVICE} only')
    contract = json.loads((root/recipe['deviceContract']).read_text())
    entry = contract['devices'].get(DEVICE) or {}
    if recipe['lunch'] != entry.get('lunch', {}).get('expected'):
        problems.append(f'lunch {recipe["lunch"]!r} is not the contract\'s')
    image = entry.get('vendorInputs', {}).get('factoryImage', {})
    factory = recipe['factory']
    if (factory['file'], factory['sha256']) != (image.get('file'), image.get('sha256')):
        problems.append('factory image name or sha256 differs from the device contract')
    if f'factory: {factory["sha256"]} {factory["file"]}' not in index_block(root, contract['shared']['vendorBuildId']):
        problems.append('factory image is not the pinned adevtool build index entry')
    if factory['url'] != FACTORY_URL + factory['file']:
        problems.append('factory URL is not the adevtool index URL for that file')
    manifest = {p.get('path'): p.get('revision') for p in ElementTree.parse(root/recipe['manifest']).getroot().iter('project')}
    for path, revision in recipe['pins'].items():
        if revision != manifest.get(path) or revision != contract['source']['projects'].get(path):
            problems.append(f'pin {path} differs from the pinned manifest or device contract')
    if set(recipe['pins']) != {'vendor/adevtool', 'vendor/state', 'device/google/laguna-kernels/6.6'}:
        problems.append('pins must be exactly vendor/adevtool, vendor/state and the laguna kernel')
    if recipe['kernelDir'] != entry.get('kernelDir') or not recipe['kernelDir'].startswith('device/google/laguna-kernels/6.6/'):
        problems.append('kernelDir differs from the device contract')
    if recipe['generate']['argv'] != ['vendor/adevtool/bin/run', 'generate-all', '-d', DEVICE]:
        problems.append('generation must name -d blazer explicitly (never the all-device default)')
    if recipe['compile']['outDir'] != 'out-blazer-build':
        problems.append('compile OUT_DIR must be out-blazer-build')
    flavor = json.loads((root/recipe['flavor']).read_text())
    if flavor['device']['lunch'] != recipe['lunch'] or flavor['provenance']['adevtool'] != recipe['pins']['vendor/adevtool']:
        problems.append('recipe differs from the blazer flavor contract')
    for forbidden in entry.get('forbiddenPatchSets', []):
        if forbidden in json.dumps(recipe):
            problems.append(f'recipe references {forbidden}, which never applies to {DEVICE}')
    capacity = recipe['capacity']
    if capacity.get('allowanceRequired') is not True:
        problems.append('capacity.allowanceRequired must be true: there is no isolated-host allowance exception')
    if not isinstance(capacity.get('reserveBytes'), int) or capacity['reserveBytes'] < 322122547200:
        problems.append('capacity.reserveBytes must be at least the 300 GiB reserve')
    if problems:
        raise InvalidInput('; '.join(problems))
    return recipe

def operator(host, source_root, jobs, lock, download_dir=None, integration_dir=None, max_jobs=256, inputs_receipt=None):
    """The operator's host inputs, validated for shape only. Nothing is invented: every value is required."""
    if not HOST_LABEL.fullmatch(str(host or '')):
        raise InvalidInput('--host must be the operator\'s builder label (lowercase letters, digits, . and -)')
    if not isinstance(jobs, int) or isinstance(jobs, bool) or not 1 <= jobs <= max_jobs:
        raise InvalidInput(f'--jobs must be an integer from 1 to {max_jobs}')
    paths = {'sourceRoot': source_root, 'lock': lock, 'downloadDir': download_dir, 'integrationDir': integration_dir,
        'inputsReceipt': inputs_receipt}
    for name, value in paths.items():
        if value is None:
            continue
        path = Path(value)
        if not path.is_absolute() or '..' in path.parts:
            raise InvalidInput(f'{name} must be an absolute path without ..')
    return {'host': host, 'jobs': jobs, **{k: (Path(v) if v is not None else None) for k, v in paths.items()}}

def symlink_in(path):
    """The first existing component of path that is a symlink, or None."""
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current = current/part
        if current.is_symlink():
            return current
        if not os.path.lexists(current):
            return None
    return None

def commands(recipe, op):
    """The exact argv, cwd and extra environment of each stage, rendered from the recipe and the operator's inputs."""
    src = str(op['sourceRoot'])
    make = 'm -j{} {}'.format(op['jobs'], ' '.join(recipe['compile']['makeGoals']))
    rendered = {'compile': {'cwd': src, 'env': {'OUT_DIR': recipe['compile']['outDir']},
        'argv': ['bash', '-c', f'source build/envsetup.sh && lunch {recipe["lunch"]} && {make}']}}
    if op['downloadDir'] is not None:
        rendered['generate'] = {'cwd': src, 'env': {recipe['generate']['downloadEnv']: str(op['downloadDir'])},
            'argv': list(recipe['generate']['argv'])}
    return rendered

def lock_free(path):
    """True when nobody holds the builder lock (probed without keeping it)."""
    try:
        with open(path, 'a') as handle:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(handle, fcntl.LOCK_UN)
        return True
    except BlockingIOError:
        return False

def integration(recipe, src, directory, inputs_receipt=None):
    """(problems, facts): the newest reconcile receipt in the operator's integration dir must be a blazer-labelled
    receipt from this checkout's trusted patcher whose recorded entries still match the tree, and (FAM-BLAZER-
    INTEGRATE-005) bind to the admitted inputs: build label, recipe, installed APK bytes, Launcher3, the routed
    Updater and the policy pins (blazer_integration.compile_problems)."""
    spec = recipe['integration']
    if directory is None:
        return ['compile needs --integration-dir: the Rose reconcile receipts for this tree'], {}
    if not directory.is_dir() or directory.is_symlink():
        return [f'integration dir {directory} is not an existing, non-link directory'], {}
    receipts = sorted(directory.glob('[0-9][0-9][0-9][0-9]-*.json'))
    if not receipts:
        return ['no Rose integration (reconcile) receipt for this tree'], {}
    latest = receipts[-1]
    facts = {'receipt': latest.name}
    if latest.is_symlink() or latest.resolve().parent != directory.resolve():
        return [f'{latest.name} is a link out of {directory}, not a receipt of this builder'], facts
    facts['sha256'] = sha256_file(latest)
    record = json.loads(latest.read_text())
    problems = []
    if record.get('receiptType') != spec['receiptType']:
        problems.append(f'{latest.name} is a {record.get("receiptType")!r} receipt, not a completed reconcile')
    if not str(record.get('label', '')).startswith(spec['labelPrefix']):
        problems.append(f'{latest.name} is labelled {record.get("label")!r}: foreign provenance, not a {DEVICE} integration')
    ours = {name: sha256_file(ROOT/'scripts'/name) for name in spec['patcher']}
    patcher = record.get('patcher') or {}
    if patcher.get('patcher') != 'trusted' or patcher.get('sha256') != ours:
        problems.append('the reconcile patcher is not this checkout\'s trusted patcher (foreign recipe)')
    entries = record.get('entries') or {}
    if not entries:
        problems.append(f'{latest.name} records no installed entries')
    drifted = []
    for relative, entry in sorted(entries.items()):
        path = src/relative
        have = sha256_file(path) if path.is_file() and not path.is_symlink() else None
        if have != entry.get('after'):
            drifted.append(relative)
    if drifted:
        problems.append(f'the source tree no longer matches {latest.name}: {drifted[:5]}')
    problems += blazer_integration.compile_problems(record, src, inputs_receipt)
    return problems, facts

def preflight(recipe, stage, op, statvfs=os.statvfs, lock_probe=lock_free):
    """{'ready': bool, 'problems': [...], 'facts': {...}} for one stage. Reads only; writes and runs nothing."""
    if stage not in STAGES:
        raise InvalidInput(f'stage must be one of {STAGES}')
    src = op['sourceRoot']
    problems, facts = [], {'stage': stage, 'device': DEVICE, 'host': op['host'], 'jobs': op['jobs'],
        'outDir': recipe['compile']['outDir'], 'signed': False}
    for name in ('sourceRoot', 'lock', 'downloadDir', 'integrationDir', 'inputsReceipt'):
        if op[name] is not None and symlink_in(op[name]):
            problems.append(f'{name} passes through a symlink ({symlink_in(op[name])})')
    if not src.is_dir():
        problems.append(f'source root {src} is not an existing directory')
        return {'ready': False, 'problems': problems, 'facts': facts}
    for needed in TREE_NEEDS:
        if not (src/needed).exists():
            problems.append(f'{needed} is missing from the source tree')
    deps = src/recipe['adevtool']['depsDir']
    revision = (deps/'adevtool_revision').read_text().strip() if (deps/'adevtool_revision').is_file() else None
    if revision != recipe['pins']['vendor/adevtool']:
        problems.append(f'{recipe["adevtool"]["depsDir"]}/adevtool_revision is {revision!r}, not the pinned revision')
    tools = deps/'host/linux-x86/bin'
    missing = [t for t in recipe['adevtool']['depsTools']
        if not (tools/t).is_file() or (tools/t).is_symlink() or not os.access(tools/t, os.X_OK)]
    if missing:
        problems.append(f'adevtool host tools missing or not executable: {missing}')
    if not (src/recipe['kernelDir']).is_dir():
        problems.append(f'{recipe["kernelDir"]} (pinned laguna kernel) is missing from the source tree')
    vendor = src/'vendor/google_devices'/DEVICE
    if stage == 'generate':
        download = op['downloadDir']
        if download is None:
            problems.append('generate needs --download-dir')
        else:
            if download == src or src in download.parents:
                problems.append('the download dir must be outside the source tree')
            if download.exists() and (not download.is_dir() or any(download.iterdir())):
                problems.append(f'download dir {download} must be absent or empty before generation')
            probe = download if download.exists() else download.parent
            if not probe.is_dir():
                problems.append(f'download dir parent {probe} does not exist')
            elif os.stat(probe).st_dev != os.stat(src).st_dev:
                problems.append('the download dir is not on the source tree\'s filesystem')
        if os.path.lexists(vendor):
            problems.append(f'{vendor} already exists; generate-all would replace it')
    else:
        check = vendor/'adevtool-version-check.mk'
        if vendor.is_symlink() or not (vendor/f'{DEVICE}.mk').is_file():
            problems.append(f'{vendor}/{DEVICE}.mk is missing: run the generate stage first')
        if not check.is_file() or recipe['pins']['vendor/adevtool'] not in check.read_text():
            problems.append('the generated vendor tree is not from the pinned adevtool revision (foreign provenance)')
        integration_problems, facts['roseIntegration'] = integration(recipe, src, op['integrationDir'],
            op['inputsReceipt'])
        problems += integration_problems
    if os.path.lexists(src/recipe['compile']['outDir']):
        problems.append(f'{recipe["compile"]["outDir"]} already exists; refusing to reuse or overwrite an OUT_DIR')
    if not lock_probe(op['lock']):
        problems.append('the builder lock is held by another job')
    capacity = recipe['capacity']
    allowance = capacity[f'{stage}AllowanceBytes']
    fs = statvfs(src)
    available = fs.f_bavail * fs.f_frsize
    facts['capacity'] = {'availableBytes': available, 'reserveBytes': capacity['reserveBytes'], 'allowanceBytes': allowance}
    if allowance is None:
        problems.append(f'capacity.{stage}AllowanceBytes is unmeasured; a measured allowance is required, never an estimate')
    elif available - allowance < capacity['reserveBytes']:
        problems.append(f'available {available} minus allowance {allowance} is below the reserve {capacity["reserveBytes"]}')
    return {'ready': not problems, 'problems': problems, 'facts': facts}

def stamp(now):
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(now()))

def run(recipe, stage, op, receipt_dir, runner=subprocess.run, now=time.time, **probes):
    """Preflight, then the stage's command under the operator's lock, with write-once receipts."""
    receipt_dir = Path(receipt_dir)
    if os.path.lexists(receipt_dir) or symlink_in(receipt_dir):
        raise InvalidInput(f'{receipt_dir} already exists or passes through a symlink; use a new receipt directory')
    report = preflight(recipe, stage, op, **probes)
    receipt_dir.mkdir(parents=True)
    release_receipts.write_once(receipt_dir/'preflight.json', {**report, 'at': stamp(now)})
    if not report['ready']:
        return 1
    command = commands(recipe, op)[stage]
    with open(op['lock'], 'a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            release_receipts.write_once(receipt_dir/'completed.json', {'started': False, 'error': 'lock taken after preflight'})
            return 1
        release_receipts.write_once(receipt_dir/'started.json', {'stage': stage, 'device': DEVICE, 'host': op['host'],
            'command': command, 'signed': False, 'at': stamp(now)})
        result = runner(command['argv'], cwd=command['cwd'], env={**os.environ, **command['env']})
        release_receipts.write_once(receipt_dir/'completed.json', {'stage': stage, 'returncode': result.returncode,
            'signed': False, 'releaseReady': False, 'at': stamp(now)})
    return 0 if result.returncode == 0 else 1

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('action', choices=('plan', 'preflight', 'run'))
    parser.add_argument('--stage', required=True, choices=STAGES)
    parser.add_argument('--recipe', default=str(RECIPE))
    parser.add_argument('--host', required=True)
    parser.add_argument('--source-root', required=True)
    parser.add_argument('--jobs', required=True, type=int)
    parser.add_argument('--lock', required=True)
    parser.add_argument('--download-dir')
    parser.add_argument('--integration-dir')
    parser.add_argument('--inputs-receipt')
    parser.add_argument('--receipt-dir')
    a = parser.parse_args(argv)
    try:
        recipe = load(a.recipe)
        op = operator(a.host, a.source_root, a.jobs, a.lock, a.download_dir, a.integration_dir,
            recipe['compile']['maxJobs'], a.inputs_receipt)
        if a.action == 'run':
            if not a.receipt_dir:
                raise InvalidInput('run needs --receipt-dir')
            return run(recipe, a.stage, op, a.receipt_dir)
        report = preflight(recipe, a.stage, op)
        if a.action == 'plan':
            report = {**report, 'command': commands(recipe, op).get(a.stage)}
        print(json.dumps(report, indent=2, default=str))
        return 0 if report['ready'] else 1
    except InvalidInput as error:
        print(f'invalid input: {error}', file=sys.stderr)
        return 2

if __name__ == '__main__':
    sys.exit(main())
