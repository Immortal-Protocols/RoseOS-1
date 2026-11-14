#!/usr/bin/env python3
"""FOLD-FACTORY-PACKAGE workflow wiring (Release-owned) for os-release.yml mode rango-factory-package.

Binds the Signing helper scripts/host-factory-package.py (PR394/PR397) and the existing inspection commands of
scripts/factory-package.py to literal historical expected pins, one manual run per dispatch:

  check                         hosted and on the builder: the literal pins agree with their committed sources
                                (os/retained-rango/release.json, os/flavors/rango.json, os/release-pipeline/frankel.json)
                                and every bound; prints the step and job bounds. Reads no host path.
  package  --run-id --attempt --attempt-record NEW.json
                                creates this run's new task root R (its parent must already exist) and runs exactly
                                host-factory-package.py with the pins, --lock and --out R/package. The helper holds
                                the shared builder lock for its whole run (never taken again here) and keeps R/package
                                (the ZIP, its receipt and its capped raw log); it removes only its own work/. Its
                                receipt exists only once R/package is created, so every earlier refusal (lock, a pinned
                                input, the output or capacity checks, the script derivation) and a refused task root
                                are recorded in the write-once attempt record outside R: the exit code and the helper's
                                own one-line JSON result (its refusal category and detail), never its stderr or log.
  observe  --run-id --attempt   factory-package.py observe of the pinned signed target-files into
                                R/inspection/target-files-observation.json.
  firmware-observe --run-id --attempt
                                scripts/rango-firmware-observe.py (Signing) of the pinned signed target-files into the
                                new R/inspection/firmware-observation.json. Its exit and one-line JSON result are kept
                                write-once in R/inspection/firmware-attempt.json. Exit 0 (with a regular, new output)
                                and 3 (a refusal, with no output) are reported results; anything else fails.
  classify --run-id --attempt   factory-package.py classify of R/package/rango-install-2026103001.zip, its stdout kept
                                write-once as R/inspection/classify.json, with --work-dir R/classify-work, and with
                                --firmware-observation only when firmware-observe exited 0 with its output; otherwise
                                classify keeps reporting the missing coverage truthfully. Exit 0
                                (factoryReady) and 3 (incomplete, still JSON; expected: no archive signature and no
                                firmware observation) are reported results; anything else fails.
  stage-installer --run-id --attempt
                                after inspection reported: the packaged ZIP must be a regular file whose size and
                                sha256 equal the helper receipt's output and whose size is within the proposed 8 GiB
                                cap; writes R/SHA256SUMS (write-once) and prints root=R for the private installer
                                artifact, which holds exactly SHA256SUMS and package/rango-install-2026103001.zip.
  report   --run-id --attempt --upload-dir NEW [--installer-upload OUTCOME] [--attempt-record FILE]
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
import json
import math
import os
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import factory_package_route as route_lib  # noqa: E402 (the device-neutral machinery this route binds)

ROOT = Path(__file__).resolve().parents[1]
HELPER, INSPECTOR, FIRMWARE = route_lib.HELPER, route_lib.INSPECTOR, route_lib.FIRMWARE
DEVICE, BUILD = 'rango', '2026103001'
PRODUCTION = '/home/pixelbuild/grapheneos-2026100600'
PINS = {
    'targetFiles': {'path': f'{PRODUCTION}/releases/{BUILD}/release-{DEVICE}-{BUILD}/{DEVICE}-target_files.zip',
                    'sha256': '73db76e500bcb71757da6dbc43337067af23395a38eaac940b9ae7d5aca0baf0', 'bytes': 4927914252},
    'otatools': {'path': f'{PRODUCTION}/releases/{BUILD}/{DEVICE}-otatools.zip',
                 'sha256': '22d62464ff3fa74fe3fa2b4c118bba116bc3a3aa504feddcdcf38fc3f3cbb664'},
    'releaseScript': {'path': f'{PRODUCTION}/script/generate-release.sh',
                      'sha256': '01f7847ecdc6600bd2e3ffebe45cf79cee4fead1d6b61686c4d3c70e9c548311'},
    # No external avb_pkmd.bin: the helper derives the public AVB key from the pinned signed target-files'
    # IMAGES/vbmeta.img into its own --out and requires this flavor sha256 (run 41503782772: no retained public file).
    'avbPkmd': {'sha256': 'aee947942b80c74dab9956209b7f7836a1824d9aeb1e9dd782dc157c63fd8f27'},
    'fastbootSha256': 'f18fdc8bf467c4bb361ffc62db5f50ca981aa5f37ffb6cb9fd4ac9ff3c4e47a9',
}
TASK_PARENT = route_lib.TASK_PARENT
# Wrapper ceilings (seconds), not measurements. package: the helper's 60-min generation plus its unbounded hashing,
# otatools unzip and target-files copy; observe: names-only target-files walk; firmware: the target-files hash plus
# at most two 1 GiB containers and their RADIO/ images (the helper has no time bound of its own); classify: each tool
# call is bounded at 3600 s by the helper, two expected (simg2img, lpunpack). The firmware step's 600 s came out of
# package (5400 -> 5100) and observe (900 -> 600), so the job total stays at the approved 297 minutes.
BOUNDS = {'packageSeconds': 5100, 'observeSeconds': 600, 'firmwareSeconds': 570, 'classifySeconds': 7200,
          'stageSeconds': 900}
# The private installer artifact (actions/upload-artifact): proposed source bounds, not a measured size or duration.
# Staging (900 s + 30 s kill) and the 45-min upload keep the approved job total at 297 minutes.
INSTALLER = {'capBytes': 8 << 30, 'retentionDays': 3, 'uploadMinutes': 45}
KILL_AFTER_SECONDS = 30
JOB_MARGIN_MINUTES = 10
RESULT_BYTES, CATEGORY, UPLOAD = route_lib.RESULT_BYTES, route_lib.CATEGORY, route_lib.UPLOAD
Invalid = route_lib.Invalid
# The one Rango route the shared machinery runs; its derived names are exactly this wiring's historical ones.
ROUTE = route_lib.Route(DEVICE, BUILD, PINS, installer=INSTALLER, task_parent=TASK_PARENT)
TASK_NAME, INSTALL = ROUTE.task_name, ROUTE.install  # rango-factory-package-{run_id}-{attempt}, rango-install-2026103001.zip
ATTEMPT_SCHEMA, ATTEMPT_NAME = ROUTE.attempt_schema, ROUTE.attempt_name


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
    for name in ('targetFiles', 'otatools', 'releaseScript'):
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


def _route(pins=PINS):
    return ROUTE if pins is PINS else route_lib.Route(DEVICE, BUILD, pins, installer=INSTALLER, task_parent=TASK_PARENT)


def task_root(run_id, attempt, parent=TASK_PARENT):
    return route_lib.task_root(ROUTE, run_id, attempt, parent)


def lock_path(root=ROOT):
    return route_lib.lock_path(root)


def package_argv(root_dir, root=ROOT, pins=PINS):
    return route_lib.package_argv(_route(pins), root_dir, root)


def observe_argv(root_dir, run_id, attempt, pins=PINS):
    return route_lib.observe_argv(_route(pins), root_dir, run_id, attempt)


def firmware_argv(root_dir, run_id, attempt, pins=PINS):
    return route_lib.firmware_argv(_route(pins), root_dir, run_id, attempt)


def classify_argv(root_dir, pins=PINS, firmware=False):
    return route_lib.classify_argv(_route(pins), root_dir, firmware=firmware)


def firmware_observe(root_dir, run_id, attempt, runner=subprocess.run, require_package=True):
    """See factory_package_route.firmware_observe (Rango route)."""
    return route_lib.firmware_observe(ROUTE, root_dir, run_id, attempt, runner, require_package)


def firmware_observed(root_dir):
    return route_lib.firmware_observed(root_dir)


def new_dir(path):
    return route_lib.new_dir(path)


def packaged(root_dir):
    return route_lib.packaged(ROUTE, root_dir)


def classify(root_dir, runner=subprocess.run):
    return route_lib.classify(ROUTE, root_dir, runner)


sha256_file = route_lib.sha256_file


def stage_installer(root_dir, cap=None, hasher=sha256_file):
    """See factory_package_route.stage_installer (Rango route)."""
    return route_lib.stage_installer(ROUTE, root_dir, cap, hasher)


def attempt_record_path(value, run_id, attempt):
    return route_lib.attempt_record_path(ROUTE, value, run_id, attempt)


def sanitized_result(stdout):
    return route_lib.sanitized_result(stdout)


def package(root_dir, record_path, runner=subprocess.run):
    """See factory_package_route.package (Rango route)."""
    return route_lib.package(ROUTE, root_dir, record_path, runner)  # argv built after the record and R, as before


def report(root_dir, upload_dir, installer_upload=None, record_path=None):
    """See factory_package_route.report (Rango route)."""
    return route_lib.report(ROUTE, root_dir, upload_dir, installer_upload, record_path)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('command', choices=('check', 'package', 'observe', 'firmware-observe', 'classify', 'stage-installer',
                                       'report'))
    p.add_argument('--run-id')
    p.add_argument('--attempt')
    p.add_argument('--upload-dir')
    p.add_argument('--installer-upload', choices=('success', 'failure', 'cancelled', 'skipped'))
    p.add_argument('--attempt-record')
    a = p.parse_args(argv)
    try:
        bounds, minutes = check()
        if a.command == 'check':
            if a.run_id or a.attempt or a.upload_dir:
                raise Invalid('check takes no other option')
            print('\n'.join(f'{k}={v}' for k, v in (('package_seconds', bounds['packageSeconds']),
                ('observe_seconds', bounds['observeSeconds']), ('firmware_seconds', bounds['firmwareSeconds']),
                ('classify_seconds', bounds['classifySeconds']),
                ('stage_seconds', bounds['stageSeconds']), ('job_minutes', minutes))))
            return 0
        root_dir = task_root(a.run_id, a.attempt)
        if (a.command == 'report') != bool(a.upload_dir) or (a.installer_upload and a.command != 'report'):
            raise Invalid('--upload-dir and --installer-upload are for report only, and report needs --upload-dir')
        if a.attempt_record and a.command not in ('package', 'report') or (a.command == 'package' and not a.attempt_record):
            raise Invalid('--attempt-record is for package (required) and report only')
        record = attempt_record_path(a.attempt_record, a.run_id, a.attempt) if a.attempt_record else None
        if a.command == 'package':
            return package(root_dir, record)
        if a.command == 'observe':
            packaged(root_dir)
            new_dir(root_dir/'inspection')
            return subprocess.run(observe_argv(root_dir, a.run_id, a.attempt)).returncode
        if a.command == 'firmware-observe':
            return firmware_observe(root_dir, a.run_id, a.attempt)
        if a.command == 'classify':
            return classify(root_dir)
        if a.command == 'stage-installer':
            record = stage_installer(root_dir)
            print(json.dumps(record, sort_keys=True), file=sys.stderr)
            print(f'root={root_dir}')
            return 0
        print(json.dumps(report(root_dir, a.upload_dir, a.installer_upload, record), sort_keys=True))
        return 0
    except (Invalid, FileExistsError, KeyError, ValueError) as error:
        print(json.dumps({'verdict': 'INVALID', 'error': str(error)}), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
