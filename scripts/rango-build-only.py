#!/usr/bin/env python3
"""OS-RANGO-BUILD-001: unsigned build-only route for rango (Pixel 10 Pro Fold) on the retained GrapheneOS tree.

It reuses the existing device support; it is not a port. Inputs are bound by os/build-only/rango.json, which is
checked against the FAM-03a device contract (os/devices/pixel10.json). Two stages, each behind the same fail-closed
preflight and the shared builder lock:

  generate   cd SRC; ADEVTOOL_IMG_DOWNLOAD_DIR=DL vendor/adevtool/bin/run generate-all -d rango
             Never the all-device default, which would regenerate (rm -rf) Frankel's vendor tree.
  compile    cd SRC; source build/envsetup.sh; lunch rango-cur-user; m <goals> with OUT_DIR=out-rango-<build>

  plan       prints both stages' exact commands and the preflight report; runs nothing
  preflight  the report alone (exit 0 ready, 1 not ready)

Preflight refuses, before any write or command, when:
  * the target, pin or factory hash differs from the contract;
  * a path is unsafe;
  * the adevtool host tools are not already built at the pinned revision (generate-all would otherwise start a Soong
    host build);
  * the stage's generated inputs are present or missing as that stage requires;
  * the shared lock is held;
  * the Rose integration receipt is absent;
  * the stage's measured allowance is unknown (null), or the filesystem's available bytes minus it would fall below
    the 300 GiB reserve.
--receipt-dir receives write-once preflight.json, then started.json just before the command runs (the start event),
then completed.json with its exit status. Unsigned test-keys output only: this module reaches no signer, secret or
publisher, and the unsigned build is not release-ready.
Exit status: 0 ready/success, 1 not ready or command failed, 2 invalid input.
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

sys.path.insert(0, str(Path(__file__).resolve().parent))
import release_receipts  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT/'os/build-only/rango.json'
DEVICE = 'rango'
STAGES = ('generate', 'compile')
FORBIDDEN_TREE_PATHS = ('os/fold-port',)

class InvalidInput(Exception):
    pass

def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()

def load(path=CONFIG, root=ROOT):
    """The config, checked against the FAM-03a device contract; InvalidInput on any mismatch."""
    config = json.loads(Path(path).read_text())
    problems = []
    if config.get('device') != DEVICE:
        problems.append(f'config device is {config.get("device")!r}; this route builds {DEVICE} only')
    contract = json.loads((root/config['deviceContract']).read_text())
    device = contract['devices'].get(DEVICE, {})
    if config.get('lunch') != device.get('lunch', {}).get('expected'):
        problems.append(f'lunch {config.get("lunch")!r} is not the contract\'s {device.get("lunch", {}).get("expected")!r}')
    image = device.get('vendorInputs', {}).get('factoryImage', {})
    if (config['factory']['file'], config['factory']['sha256']) != (image.get('file'), image.get('sha256')):
        problems.append('factory image name or sha256 differs from the device contract')
    if not config['factory']['url'].endswith('/' + config['factory']['file']) \
            or not config['factory']['url'].startswith('https://dl.google.com/dl/android/aosp/'):
        problems.append('factory URL is not the adevtool index URL for that file')
    if config['adevtool']['revision'] != contract['source']['projects']['vendor/adevtool']:
        problems.append('adevtool revision differs from the device contract pin')
    if any(p in json.dumps(config) for p in FORBIDDEN_TREE_PATHS):
        problems.append('config references os/fold-port, which never applies to rango')
    if problems:
        raise InvalidInput('; '.join(problems))
    return config

def out_dir_name(config, build):
    if not re.fullmatch(r'[1-9][0-9]{9}', str(build or '')):
        raise InvalidInput('--build-number must be a ten-digit build number')
    return config['host']['outDirPrefix'] + str(build)

def commands(config, build, download_dir):
    """The exact argv, cwd and extra environment of each stage."""
    src, host = config['host']['sourceRoot'], config['host']
    make = 'm -j{} {}'.format(host['makeJobs'], ' '.join(host['makeGoals']))
    return {
        'generate': {'cwd': src, 'env': {'ADEVTOOL_IMG_DOWNLOAD_DIR': str(download_dir)},
            'argv': ['vendor/adevtool/bin/run', 'generate-all', '-d', DEVICE]},
        'compile': {'cwd': src, 'env': {'OUT_DIR': out_dir_name(config, build), 'BUILD_NUMBER': str(build)},
            'argv': ['bash', '-c', f'source build/envsetup.sh && lunch {config["lunch"]} && {make}']},
    }

def lock_free(path):
    """True when nobody holds the shared builder lock (probed without keeping it)."""
    try:
        with open(path, 'a') as handle:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(handle, fcntl.LOCK_UN)
        return True
    except BlockingIOError:
        return False

def latest_reconcile(directory):
    receipts = sorted(Path(directory).glob('[0-9][0-9][0-9][0-9]-*.json')) if Path(directory).is_dir() else []
    return receipts[-1] if receipts else None

def preflight(config, stage, build, download_dir, statvfs=os.statvfs, lock_probe=lock_free):
    """{'ready': bool, 'problems': [...], 'facts': {...}} for one stage. Reads only; writes and runs nothing."""
    if stage not in STAGES:
        raise InvalidInput(f'stage must be one of {STAGES}')
    out_name = out_dir_name(config, build)
    host, problems, facts = config['host'], [], {'stage': stage, 'device': DEVICE, 'outDir': out_name}
    src = Path(host['sourceRoot'])
    download = Path(download_dir) if download_dir else None
    if not src.is_absolute() or not src.is_dir():
        problems.append(f'source root {src} is not an existing absolute directory')
        return {'ready': False, 'problems': problems, 'facts': facts}
    if download is None or not download.is_absolute() or '..' in download.parts:
        raise InvalidInput('--download-dir must be an absolute path without ..')
    if download == src or src in download.parents:
        problems.append('the download dir must be outside the source tree (keeps the archive out of Soong and alpha copies)')
    if download.exists() and (not download.is_dir() or (stage == 'generate' and any(download.iterdir()))):
        problems.append(f'download dir {download} must be absent or an empty directory before generation')
    probe = download if download.exists() else download.parent
    if not probe.is_dir():
        problems.append(f'download dir parent {probe} does not exist')
    elif os.stat(probe).st_dev != os.stat(src).st_dev:
        problems.append('the download dir is not on the source tree\'s filesystem; capacity would be checked on the wrong one')
    for needed in ('vendor/adevtool/bin/run', 'prebuilts/build-tools/path/linux-x86', 'prebuilts/jdk/jdk21/linux-x86/bin'):
        if not (src/needed).exists():
            problems.append(f'{needed} is missing from the source tree')
    deps = src/config['adevtool']['depsDir']
    revision = (deps/'adevtool_revision').read_text().strip() if (deps/'adevtool_revision').is_file() else None
    if revision != config['adevtool']['revision']:
        problems.append(f'{config["adevtool"]["depsDir"]}/adevtool_revision is {revision!r}, not the pinned revision; '
            'generate-all would start a Soong host-tool build')
    missing_tools = [t for t in config['adevtool']['depsTools'] if not (deps/'host/linux-x86/bin'/t).is_file()]
    if missing_tools:
        problems.append(f'adevtool host tools missing: {missing_tools}')
    vendor = src/'vendor/google_devices'/DEVICE
    if stage == 'generate' and os.path.lexists(vendor):
        problems.append(f'{vendor} already exists; generate-all would rm -rf it')
    if stage == 'compile':
        product = vendor/f'{DEVICE}.mk'
        check = vendor/'adevtool-version-check.mk'
        if not product.is_file():
            problems.append(f'{product} is missing: run the generate stage first')
        if not check.is_file() or config['adevtool']['revision'] not in check.read_text():
            problems.append('the generated vendor module is not from the pinned adevtool revision')
    facts['frankelVendorPresent'] = (src/'vendor/google_devices/frankel').is_dir()
    if os.path.lexists(src/out_name):
        problems.append(f'{out_name} already exists; each build needs a new OUT_DIR')
    reconcile = latest_reconcile(host['reconcileReceiptDir'])
    if reconcile is None:
        problems.append('no Rose integration (reconcile) receipt: the tree has no recorded Rose app/controller inputs')
    else:
        facts['roseIntegration'] = {'receipt': reconcile.name, 'sha256': sha256_file(reconcile)}
    if not lock_probe(host['lockPath']):
        problems.append('the shared builder lock is held by another job')
    allowance = config['capacity'][f'{stage}AllowanceBytes']
    fs = statvfs(src)
    available = fs.f_bavail * fs.f_frsize
    facts['capacity'] = {'availableBytes': available, 'reserveBytes': config['capacity']['reserveBytes'],
        'allowanceBytes': allowance}
    if allowance is None:
        problems.append(f'capacity.{stage}AllowanceBytes is unmeasured; a measured allowance is required, never an estimate')
    elif available - allowance < config['capacity']['reserveBytes']:
        problems.append(f'available {available} minus allowance {allowance} is below the reserve {config["capacity"]["reserveBytes"]}')
    return {'ready': not problems, 'problems': problems, 'facts': facts}

def run(config, stage, build, download_dir, receipt_dir, runner=subprocess.run, now=time.time, **probes):
    """Preflight, then the stage's command under the shared lock, with write-once receipts."""
    receipt_dir = Path(receipt_dir)
    if receipt_dir.exists():
        raise InvalidInput(f'{receipt_dir} already exists; use a new receipt directory')
    report = preflight(config, stage, build, download_dir, **probes)
    receipt_dir.mkdir(parents=True)
    release_receipts.write_once(receipt_dir/'preflight.json', {**report, 'at': stamp(now)})
    if not report['ready']:
        return 1
    command = commands(config, build, download_dir)[stage]
    with open(config['host']['lockPath'], 'a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            release_receipts.write_once(receipt_dir/'completed.json', {'started': False, 'error': 'lock taken after preflight'})
            return 1
        release_receipts.write_once(receipt_dir/'started.json', {'stage': stage, 'device': DEVICE, 'command': command,
            'signed': False, 'at': stamp(now)})
        result = runner(command['argv'], cwd=command['cwd'], env={**os.environ, **command['env']})
        release_receipts.write_once(receipt_dir/'completed.json', {'stage': stage, 'returncode': result.returncode,
            'signed': False, 'releaseReady': False, 'at': stamp(now)})
    return 0 if result.returncode == 0 else 1

def stamp(now):
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(now()))

def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('command', choices=['plan', 'preflight', 'run'])
    p.add_argument('--stage', choices=STAGES)
    p.add_argument('--config', default=str(CONFIG))
    p.add_argument('--build-number', required=True)
    p.add_argument('--download-dir', required=True)
    p.add_argument('--receipt-dir')
    a = p.parse_args(argv)
    try:
        config = load(a.config)
        if a.command == 'plan':
            plan = {'commands': commands(config, a.build_number, a.download_dir),
                'preflight': {s: preflight(config, s, a.build_number, a.download_dir) for s in STAGES}, 'executed': False}
            print(json.dumps(plan, indent=2, sort_keys=True))
            return 0
        if not a.stage:
            raise InvalidInput('--stage is required')
        if a.command == 'preflight':
            report = preflight(config, a.stage, a.build_number, a.download_dir)
            print(json.dumps(report, indent=2, sort_keys=True))
            return 0 if report['ready'] else 1
        if not a.receipt_dir:
            raise InvalidInput('run needs --receipt-dir')
        return run(config, a.stage, a.build_number, a.download_dir, a.receipt_dir)
    except InvalidInput as error:
        print(json.dumps({'verdict': 'INVALID', 'error': str(error)}), file=sys.stderr)
        return 2

if __name__ == '__main__':
    sys.exit(main())
