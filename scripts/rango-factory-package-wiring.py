#!/usr/bin/env python3
"""FOLD-FACTORY-PACKAGE workflow wiring (Release-owned) for os-release.yml mode rango-factory-package.

Binds the Signing helper scripts/host-factory-package.py (PR394/PR397) and the existing inspection commands of
scripts/factory-package.py to literal historical expected pins, one manual run per dispatch:

  check                         hosted and on the builder: the literal pins agree with their committed sources
                                (os/retained-rango/release.json, os/flavors/rango.json, os/release-pipeline/frankel.json)
                                and every bound; prints the step and job bounds. Reads no host path.
  package  --run-id --attempt   creates this run's new task root R (its parent must already exist) and execs exactly
                                host-factory-package.py with the pins, --lock and --out R/package. The helper holds
                                the shared builder lock for its whole run (never taken again here) and keeps R/package
                                (the ZIP, its receipt and its capped raw log); it removes only its own work/.
  observe  --run-id --attempt   factory-package.py observe of the pinned signed target-files into
                                R/inspection/target-files-observation.json.
  classify --run-id --attempt   factory-package.py classify of R/package/rango-install-2026103001.zip, its stdout kept
                                write-once as R/inspection/classify.json, with --work-dir R/classify-work. Exit 0
                                (factoryReady) and 3 (incomplete, still JSON; expected: no archive signature and no
                                firmware observation) are reported results; anything else fails.
  stage-installer --run-id --attempt
                                after inspection reported: the packaged ZIP must be a regular file whose size and
                                sha256 equal the helper receipt's output and whose size is within the proposed 8 GiB
                                cap; writes R/SHA256SUMS (write-once) and prints root=R for the private installer
                                artifact, which holds exactly SHA256SUMS and package/rango-install-2026103001.zip.
  report   --run-id --attempt --upload-dir NEW [--installer-upload OUTCOME]
                                writes R/cleanup.json with the actually observed scratch state and copies only
                                factory-package-receipt.json, target-files-observation.json, classify.json and
                                cleanup.json (regular files) into NEW, recording the installer staging and the upload
                                step's outcome as reported. Never the raw log or the target-files; the ZIP leaves only
                                through the separate private installer artifact.

The pins are historical expected values, checked by the helpers before use; they are not current byte proof. The
task root is a proposed path under /home/pixelbuild/rose-actions, not known to exist: a missing parent refuses before
anything is written. 128 GiB is the helper's free-space precondition, not a usage cap. Bounds are wrapper ceilings,
not measurements: no total runtime has been measured. Exit status: 0 ok, 2 invalid, 3 refused, others from the helpers.
"""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT/'scripts/host-factory-package.py'
INSPECTOR = ROOT/'scripts/factory-package.py'
DEVICE, BUILD = 'rango', '2026103001'
PRODUCTION = '/home/pixelbuild/grapheneos-2026100600'
PINS = {
    'targetFiles': {'path': f'{PRODUCTION}/releases/{BUILD}/release-{DEVICE}-{BUILD}/{DEVICE}-target_files.zip',
                    'sha256': '73db76e500bcb71757da6dbc43337067af23395a38eaac940b9ae7d5aca0baf0', 'bytes': 4927914252},
    'otatools': {'path': f'{PRODUCTION}/releases/{BUILD}/{DEVICE}-otatools.zip',
                 'sha256': '22d62464ff3fa74fe3fa2b4c118bba116bc3a3aa504feddcdcf38fc3f3cbb664'},
    'releaseScript': {'path': f'{PRODUCTION}/script/generate-release.sh',
                      'sha256': '01f7847ecdc6600bd2e3ffebe45cf79cee4fead1d6b61686c4d3c70e9c548311'},
    'avbPkmd': {'path': f'{PRODUCTION}/releases/{BUILD}/public/avb_pkmd.bin',
                'sha256': 'aee947942b80c74dab9956209b7f7836a1824d9aeb1e9dd782dc157c63fd8f27'},
    'fastbootSha256': 'f18fdc8bf467c4bb361ffc62db5f50ca981aa5f37ffb6cb9fd4ac9ff3c4e47a9',
}
TASK_PARENT = '/home/pixelbuild/rose-actions'
TASK_NAME = 'rango-factory-package-{run_id}-{attempt}'
INSTALL = f'{DEVICE}-install-{BUILD}.zip'
# Wrapper ceilings (seconds), not measurements. package: the helper's 60-min generation plus its unbounded hashing,
# otatools unzip and target-files copy; observe: names-only target-files walk; classify: each tool call is bounded at
# 3600 s by the helper, two expected (simg2img, lpunpack).
BOUNDS = {'packageSeconds': 5400, 'observeSeconds': 900, 'classifySeconds': 7200, 'stageSeconds': 900}
# The private installer artifact (actions/upload-artifact): proposed source bounds, not a measured size or duration.
# Staging (900 s + 30 s kill) and the 45-min upload keep the approved job total at 297 minutes.
INSTALLER = {'capBytes': 8 << 30, 'retentionDays': 3, 'uploadMinutes': 45}
KILL_AFTER_SECONDS = 30
JOB_MARGIN_MINUTES = 10
UPLOAD = {'package/factory-package-receipt.json': 'factory-package-receipt.json',
          'inspection/target-files-observation.json': 'target-files-observation.json',
          'inspection/classify.json': 'classify.json', 'cleanup.json': 'cleanup.json'}


class Invalid(Exception):
    pass


def pointer(root, file, *keys):
    value = json.loads((root/file).read_text())
    for key in keys:
        value = value[key]
    return value


def check(root=ROOT, pins=PINS):
    """The pins agree with their committed sources; returns (bounds, job_minutes)."""
    hexes = [pins['targetFiles']['sha256'], pins['otatools']['sha256'], pins['releaseScript']['sha256'],
             pins['avbPkmd']['sha256'], pins['fastbootSha256']]
    if not all(isinstance(h, str) and len(h) == 64 and all(c in '0123456789abcdef' for c in h) for h in hexes):
        raise Invalid('every pin must be a lowercase sha256')
    if pins['otatools']['sha256'] != pointer(root, 'os/retained-rango/release.json', 'signing', 'otatoolsProvenance', 'sha256'):
        raise Invalid('otatools sha256 differs from os/retained-rango/release.json signing.otatoolsProvenance')
    if pins['releaseScript']['sha256'] != pointer(root, 'os/retained-rango/release.json', 'signing',
                                                  'releaseScriptProvenance', 'files', 'generate-release.sh'):
        raise Invalid('release script sha256 differs from os/retained-rango/release.json releaseScriptProvenance')
    if pins['avbPkmd']['sha256'] != pointer(root, 'os/flavors/rango.json', 'identity', 'avbPublicKeySha256'):
        raise Invalid('avb_pkmd.bin sha256 differs from os/flavors/rango.json identity.avbPublicKeySha256')
    if pointer(root, 'os/release-pipeline/frankel.json', 'host', 'sourceRoot') != PRODUCTION:
        raise Invalid('the production source root differs from os/release-pipeline/frankel.json host.sourceRoot')
    release = pointer(root, 'os/retained-rango/release.json', 'target')
    if (release['device'], release['build']) != (DEVICE, BUILD):
        raise Invalid('the pinned build is not os/retained-rango/release.json target')
    for name in ('targetFiles', 'otatools', 'releaseScript', 'avbPkmd'):
        path = pins[name]['path']
        if not path.startswith(PRODUCTION + '/') or os.path.normpath(path) != path:
            raise Invalid(f'{name} must be a normalized path inside the production tree (read only)')
    if not isinstance(pins['targetFiles']['bytes'], int) or pins['targetFiles']['bytes'] <= 0:
        raise Invalid('targetFiles bytes must be a positive size')
    if Path(TASK_PARENT) in Path(PRODUCTION).parents or Path(PRODUCTION) in Path(TASK_PARENT).parents \
            or Path(TASK_PARENT) == Path(PRODUCTION):
        raise Invalid('the task parent must not overlap the production tree')
    total = sum(BOUNDS.values()) + KILL_AFTER_SECONDS * len(BOUNDS) + INSTALLER['uploadMinutes'] * 60
    return dict(BOUNDS), math.ceil(total / 60) + JOB_MARGIN_MINUTES


def task_root(run_id, attempt, parent=TASK_PARENT):
    for label, value in (('run id', run_id), ('attempt', attempt)):
        if not (isinstance(value, str) and value.isdigit() and value[0] != '0' and len(value) <= 20):
            raise Invalid(f'a positive numeric {label} is required')
    return Path(parent)/TASK_NAME.format(run_id=run_id, attempt=attempt)


def lock_path(root=ROOT):
    return pointer(root, 'os/release-pipeline/frankel.json', 'host', 'lockPath')


def package_argv(root_dir, root=ROOT, pins=PINS):
    return [sys.executable, str(HELPER), '--device', DEVICE, '--build', BUILD,
            '--target-files', pins['targetFiles']['path'], '--target-files-sha256', pins['targetFiles']['sha256'],
            '--otatools', pins['otatools']['path'], '--otatools-sha256', pins['otatools']['sha256'],
            '--release-script', pins['releaseScript']['path'], '--release-script-sha256', pins['releaseScript']['sha256'],
            '--avb-pkmd', pins['avbPkmd']['path'], '--fastboot-sha256', pins['fastbootSha256'],
            '--lock', lock_path(root), '--out', str(root_dir/'package')]


def observe_argv(root_dir, run_id, attempt, pins=PINS):
    return [sys.executable, str(INSPECTOR), 'observe', pins['targetFiles']['path'], '--flavor', DEVICE, '--build', BUILD,
            '--source', f'os-release rango-factory-package run {run_id} attempt {attempt}',
            '--out', str(root_dir/'inspection/target-files-observation.json')]


def classify_argv(root_dir, pins=PINS):
    return [sys.executable, str(INSPECTOR), 'classify', str(root_dir/'package'/INSTALL), '--flavor', DEVICE,
            '--target-files', pins['targetFiles']['path'],
            '--target-files-observation', str(root_dir/'inspection/target-files-observation.json'),
            '--otatools', pins['otatools']['path'], '--otatools-sha256', pins['otatools']['sha256'],
            '--work-dir', str(root_dir/'classify-work'), '--build', BUILD]


def new_dir(path):
    path = Path(path)
    if path.parent.is_symlink() or not path.parent.is_dir():
        raise Invalid(f'{path.parent} must be an existing plain directory (not created here)')
    path.mkdir(mode=0o700)  # FileExistsError: never reused


def packaged(root_dir):
    receipt = root_dir/'package/factory-package-receipt.json'
    if receipt.is_symlink() or not receipt.is_file():
        raise Invalid('no package receipt: nothing to inspect')
    record = json.loads(receipt.read_text())
    archive = root_dir/'package'/INSTALL
    if record.get('status') != 'packaged' or archive.is_symlink() or not archive.is_file():
        raise Invalid('the package step did not produce the install archive: nothing to inspect')


def classify(root_dir, runner=subprocess.run):
    packaged(root_dir)
    new_dir(root_dir/'classify-work')
    out = os.open(root_dir/'inspection/classify.json', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(out, 'w') as f:
        code = runner(classify_argv(root_dir), stdout=f).returncode
    return 0 if code in (0, 3) else (code or 1)


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(8 << 20), b''):
            digest.update(block)
    return digest.hexdigest()


def stage_installer(root_dir, cap=None, hasher=sha256_file):
    """The ZIP the helper recorded, verified again before it may leave: regular file, recorded size and sha256, within
    the cap, and only after inspection wrote its report. Writes R/SHA256SUMS; returns the export record."""
    cap = INSTALLER['capBytes'] if cap is None else cap
    packaged(root_dir)
    report_file = root_dir/'inspection/classify.json'
    if report_file.is_symlink() or not report_file.is_file():
        raise Invalid('the inspection report is missing: the installer is not exported uninspected')
    output = json.loads((root_dir/'package/factory-package-receipt.json').read_text()).get('output') or {}
    if output.get('file') != INSTALL or not isinstance(output.get('size'), int) or not (
            isinstance(output.get('sha256'), str) and len(output['sha256']) == 64):
        raise Invalid('the package receipt does not record the install archive name, size and sha256')
    archive = root_dir/'package'/INSTALL
    size = os.lstat(archive).st_size
    if size != output['size']:
        raise Invalid(f'the archive is {size} bytes, not the recorded {output["size"]}: not exported')
    if size > cap:
        raise Invalid(f'the archive is {size} bytes, above the proposed {cap}-byte cap: not exported')
    digest = hasher(archive)
    if digest != output['sha256']:
        raise Invalid('the archive sha256 differs from the package receipt: not exported')
    fd = os.open(root_dir/'SHA256SUMS', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
    with os.fdopen(fd, 'w') as f:
        f.write(f'{digest}  package/{INSTALL}\n')
    return {'file': f'package/{INSTALL}', 'bytes': size, 'sha256': digest, 'capBytes': cap,
            'retentionDays': INSTALLER['retentionDays']}


def report(root_dir, upload_dir, installer_upload=None):
    """The observed scratch state, then the allowlisted copies. Works when the task root was never created."""
    upload_dir = Path(upload_dir)
    upload_dir.mkdir(mode=0o700)
    state = {'schema': 'roseos.rango-factory-package-cleanup.v1', 'taskRoot': str(root_dir),
             'taskRootExists': root_dir.is_dir() and not root_dir.is_symlink()}
    if state['taskRootExists']:
        work, scratch = root_dir/'package/work', root_dir/'classify-work'
        state['packageWorkRemoved'] = not os.path.lexists(work)
        left = sorted(p.name for p in scratch.iterdir()) if scratch.is_dir() and not scratch.is_symlink() else []
        state['classifyWorkLeft'] = left
        if scratch.is_dir() and not scratch.is_symlink() and not left:
            scratch.rmdir()
        state['classifyWorkRemoved'] = not os.path.lexists(scratch)
        state['retained'] = {name: (root_dir/'package'/name).is_file()
                             for name in (INSTALL, 'factory-package-receipt.json', 'factory-package.log')}
        state['uploaded'] = sorted(UPLOAD.values())
        sums = root_dir/'SHA256SUMS'
        state['installer'] = {'staged': sums.is_file() and not sums.is_symlink(),
                              'sha256sums': sums.read_text().strip() if sums.is_file() and not sums.is_symlink() else None,
                              'uploadOutcome': installer_upload or 'not-run', 'retentionDays': INSTALLER['retentionDays'],
                              'capBytes': INSTALLER['capBytes']}
        fd = os.open(root_dir/'cleanup.json', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
        with os.fdopen(fd, 'w') as f:
            f.write(json.dumps(state, indent=2, sort_keys=True) + '\n')
        for relative, name in UPLOAD.items():
            source = root_dir/relative
            if source.is_file() and not source.is_symlink():
                (upload_dir/name).write_bytes(source.read_bytes())
    else:
        (upload_dir/'cleanup.json').write_text(json.dumps(state, indent=2, sort_keys=True) + '\n')
    return state


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('command', choices=('check', 'package', 'observe', 'classify', 'stage-installer', 'report'))
    p.add_argument('--run-id')
    p.add_argument('--attempt')
    p.add_argument('--upload-dir')
    p.add_argument('--installer-upload', choices=('success', 'failure', 'cancelled', 'skipped'))
    a = p.parse_args(argv)
    try:
        bounds, minutes = check()
        if a.command == 'check':
            if a.run_id or a.attempt or a.upload_dir:
                raise Invalid('check takes no other option')
            print('\n'.join(f'{k}={v}' for k, v in (('package_seconds', bounds['packageSeconds']),
                ('observe_seconds', bounds['observeSeconds']), ('classify_seconds', bounds['classifySeconds']),
                ('stage_seconds', bounds['stageSeconds']), ('job_minutes', minutes))))
            return 0
        root_dir = task_root(a.run_id, a.attempt)
        if (a.command == 'report') != bool(a.upload_dir) or (a.installer_upload and a.command != 'report'):
            raise Invalid('--upload-dir and --installer-upload are for report only, and report needs --upload-dir')
        if a.command == 'package':
            new_dir(root_dir)
            os.execv(sys.executable, package_argv(root_dir))
        if a.command == 'observe':
            packaged(root_dir)
            new_dir(root_dir/'inspection')
            return subprocess.run(observe_argv(root_dir, a.run_id, a.attempt)).returncode
        if a.command == 'classify':
            return classify(root_dir)
        if a.command == 'stage-installer':
            record = stage_installer(root_dir)
            print(json.dumps(record, sort_keys=True), file=sys.stderr)
            print(f'root={root_dir}')
            return 0
        print(json.dumps(report(root_dir, a.upload_dir, a.installer_upload), sort_keys=True))
        return 0
    except (Invalid, FileExistsError, KeyError, ValueError) as error:
        print(json.dumps({'verdict': 'INVALID', 'error': str(error)}), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
