#!/usr/bin/env python3
"""FOLD-FACTORY-INSPECT workflow wiring (Release-owned) for os-release.yml mode rango-factory-inspect: inspection of the
installer ZIP that an earlier rango-factory-package run already generated and retained, with no regeneration.

  check                        hosted and on the builder: the package-mode pins (scripts/rango-factory-package-wiring.py
                               check) plus the retained-installer pin below; prints the bounds. Reads no host path.
                               Exit 4 if the retained run's attempt were not committed (it is: attempt 1).
  inspect --run-id --attempt   one bounded command under the nonblocking shared builder lock (lock_held refuses, never
                               waits), into this run's new task root R (/home/pixelbuild/rose-actions/rango-factory-
                               inspect-<run>-<attempt>; the retained root is only read):
                               1. the retained ZIP must be a regular file reached without symlinks, of the pinned size
                                  and sha256 (hashed in-process within verifySeconds; verify_timeout refuses), and the
                                  retained package receipt must be status packaged with that same output; any mismatch
                                  refuses before any inspection (R/inspection/retained.json says why);
                               2. factory-package.py observe of the pinned target-files (its own sha256 is recorded);
                               3. Signing's rango-firmware-observe.py, recorded as in the package mode (exit 0 with a new
                                  output is an observation, 3 a refusal, anything else fails);
                               4. factory-package.py classify of the retained ZIP, with --firmware-observation only after
                                  an observation; exit 0 and 3 are reported results.
                               Each tool call runs in its own process group with its own time bound (TERM, then KILL
                               after 30 s); a stopped call is recorded as a timeout and fails the command.
  report --run-id --attempt --upload-dir NEW
                               copies only the inspection JSON files (retained.json, target-files-observation.json,
                               firmware-observation.json, firmware-attempt.json, classify.json, inspect.json) and an
                               observed cleanup.json into NEW. Never the ZIP, the target-files or any log. Cleanup is a
                               report, not a guarantee: classify removes its own job directory when it finishes; report
                               removes R/classify-work only when it is empty and otherwise lists what an interrupted run
                               left (classifyWorkLeft) without deleting it. The task root itself is kept.

Nothing is generated, signed, downloaded, installed or published. Bounds are wrapper ceilings, not measurements.
Exit status: 0 inspected, 2 invalid, 3 refused or failed, 4 unbound.
"""
import argparse
import contextlib
import fcntl
import importlib.util
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('rango_factory_package_wiring', ROOT/'scripts/rango-factory-package-wiring.py')
pkg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pkg)

# The installer ZIP generated and exported by run 41515240855 attempt 1 (Infra's sanitized receipt, verified by Chief:
# source "os-release rango-factory-package run 41515240855 attempt 1", retained under
# /home/pixelbuild/rose-actions/rango-factory-package-41515240855-1/package/): a historical expected identity checked
# before use, not current byte proof.
RETAINED = {'runId': '41515240855', 'attempt': '1', 'bytes': 2130374982,
            'sha256': 'e37db904490a99857dc1fe106b2eac1db12c71b32ac23372cfda474ab70d3b48'}
TASK_NAME = 'rango-factory-inspect-{run_id}-{attempt}'
# Wrapper ceilings (seconds), sized to the work, not measurements. verify hashes the 2.1 GB retained ZIP (about 21 s at a
# conservative 100 MB/s); observe reads the target-files central directory; firmware hashes the 4.9 GB target-files and
# reads at most two 1 GiB containers (about 70 s); classify reads the ZIP and the target-files images (about 7 GB) and
# writes the simg2img super and the lpunpack partitions (about 9 GB each): about 25 GB, about 250 s at 100 MB/s, so
# 2700 s is roughly ten times that. The classifier's own per-call limit is 3600 s; this wrapper may stop it earlier, and
# a stop is recorded as a timeout and fails the run, never a pass.
BOUNDS = {'verifySeconds': 300, 'observeSeconds': 600, 'firmwareSeconds': 570, 'classifySeconds': 2700}
KILL_GRACE_SECONDS = 30
OUTER_MARGIN_SECONDS = 120
JOB_MARGIN_MINUTES = 10
REPORTS = ('retained.json', 'target-files-observation.json', 'firmware-observation.json', 'firmware-attempt.json',
           'classify.json', 'inspect.json')


class Unbound(Exception):
    pass


class Refused(Exception):
    pass


def check(root=ROOT, retained=RETAINED):
    """(bounds, outer_seconds, job_minutes). The package-mode pins must hold too: both modes read the same inputs."""
    pkg.check(root)
    if not (isinstance(retained['sha256'], str) and len(retained['sha256']) == 64
            and all(c in '0123456789abcdef' for c in retained['sha256'])):
        raise pkg.Invalid('the retained installer sha256 must be a lowercase sha256')
    if not isinstance(retained['bytes'], int) or isinstance(retained['bytes'], bool) or not 0 < retained['bytes'] <= pkg.INSTALLER['capBytes']:
        raise pkg.Invalid('the retained installer size must be positive and within the installer cap')
    if not (isinstance(retained['runId'], str) and retained['runId'].isdigit()):
        raise pkg.Invalid('the retained run id must be numeric')
    if retained['attempt'] is None:
        raise Unbound(f'the retained run {retained["runId"]} attempt is not committed; its task root is not guessed')
    if not (isinstance(retained['attempt'], str) and retained['attempt'].isdigit() and retained['attempt'][0] != '0'):
        raise pkg.Invalid('the retained attempt must be a positive number')
    steps = sum(BOUNDS.values()) + KILL_GRACE_SECONDS * len(BOUNDS)
    outer = steps + OUTER_MARGIN_SECONDS
    return dict(BOUNDS), outer, math.ceil((outer + KILL_GRACE_SECONDS) / 60) + JOB_MARGIN_MINUTES


def retained_root(retained, parent=pkg.TASK_PARENT):
    return pkg.task_root(retained['runId'], retained['attempt'], parent=parent)


def inspect_root(run_id, attempt, parent=pkg.TASK_PARENT):
    pkg.task_root(run_id, attempt, parent=parent)  # the same numeric checks
    return Path(parent)/TASK_NAME.format(run_id=run_id, attempt=attempt)


def no_symlink(path):
    for parent in [*reversed(Path(path).parents), Path(path)]:
        if parent.is_symlink():
            return False
    return True


def bounded_sha256(path, seconds, clock=time.monotonic, block=8 << 20):
    """sha256 of PATH within SECONDS: the deadline is checked before every block read, so the verify bound is enforced
    in-process (Refused verify_timeout) rather than left to the outer timeout."""
    import hashlib
    digest, deadline = hashlib.sha256(), clock() + seconds
    with open(path, 'rb') as f:
        while True:
            if clock() > deadline:
                raise Refused(f'verify_timeout: hashing the retained archive exceeded {seconds} s')
            chunk = f.read(block)
            if not chunk:
                return digest.hexdigest()
            digest.update(chunk)


def verify_retained(source, retained, hasher=None):
    """The retained ZIP and its package receipt, exactly as pinned; returns the verification facts or raises Refused."""
    archive, receipt = source/'package'/pkg.INSTALL, source/'package/factory-package-receipt.json'
    facts = {'archive': str(archive), 'expected': {'bytes': retained['bytes'], 'sha256': retained['sha256']}}
    for label, path in (('archive', archive), ('receipt', receipt)):
        if not no_symlink(path) or not path.is_file():
            raise Refused(f'retained_missing: the retained {label} {path} is not a regular file reached without symlinks')
    size = os.lstat(archive).st_size
    facts['bytes'] = size
    if size != retained['bytes']:
        raise Refused(f'retained_mismatch: the retained archive is {size} bytes, not the pinned {retained["bytes"]}')
    record = json.loads(receipt.read_text())
    output = record.get('output') or {}
    if record.get('status') != 'packaged' or output.get('file') != pkg.INSTALL or output.get('sha256') != retained['sha256'] \
            or output.get('size') != retained['bytes']:
        raise Refused('retained_mismatch: the retained package receipt does not record the pinned archive')
    facts['sha256'] = digest = (hasher or (lambda path: bounded_sha256(path, BOUNDS['verifySeconds'])))(archive)
    if digest != retained['sha256']:
        raise Refused('retained_mismatch: the retained archive sha256 is not the pinned sha256')
    return facts


def bounded(argv, seconds, stdout, popen=subprocess.Popen, grace=KILL_GRACE_SECONDS):
    """Run one tool call in its own process group; (exit, timed_out). On timeout TERM the group, then KILL."""
    proc = popen(argv, stdout=stdout, start_new_session=True)
    try:
        return proc.wait(timeout=seconds), False
    except subprocess.TimeoutExpired:
        for sig in (signal.SIGTERM, signal.SIGKILL):
            with contextlib.suppress(ProcessLookupError):
                os.killpg(proc.pid, sig)
            try:
                proc.wait(timeout=grace)
                break
            except subprocess.TimeoutExpired:
                continue
        return proc.returncode, True


@contextlib.contextmanager
def shared_lock(path):
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o644)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Refused('lock_held: the shared builder lock is held by another job') from None
        yield
    finally:
        os.close(fd)


def write_once(path, document):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
    with os.fdopen(fd, 'w') as f:
        f.write(json.dumps(document, indent=2, sort_keys=True) + '\n')


def classify_argv(root_dir, archive, firmware):
    argv = pkg.classify_argv(root_dir, firmware=firmware)
    argv[3] = str(archive)  # the retained ZIP, read only; every other argument is the package mode's
    return argv


def inspect(root_dir, run_id, attempt, retained, source=None, lock=None, popen=subprocess.Popen, hasher=None):
    """The inspect-only run of the retained installer identified by `retained` (RETAINED in production); returns its
    exit status and leaves R/inspection/inspect.json naming each step's outcome."""
    source = retained_root(retained) if source is None else source
    pkg.new_dir(root_dir)
    inspection = root_dir/'inspection'
    pkg.new_dir(inspection)
    summary = {'schema': 'roseos.rango-factory-inspect.v1', 'retained': str(source), 'steps': {}}
    status = 3
    try:
        with (lock or shared_lock)(pkg.lock_path()):
            try:
                write_once(inspection/'retained.json', dict(verify_retained(source, retained, hasher), verified=True))
            except Refused as error:
                write_once(inspection/'retained.json', {'verified': False, 'refused': str(error)})
                raise
            summary['steps']['verify'] = 'verified'
            with open(os.devnull, 'w') as null:
                code, late = bounded(pkg.observe_argv(root_dir, run_id, attempt), BOUNDS['observeSeconds'], null, popen)
            summary['steps']['observe'] = {'exit': code, 'timedOut': late}
            if late or code != 0:
                raise Refused('observe_failed: the target-files observation did not complete')

            def firmware_runner(argv, stdout=None, text=True):
                with open(inspection/'.firmware-stdout', 'w+') as out:
                    code, late = bounded(argv, BOUNDS['firmwareSeconds'], out, popen)
                    out.seek(0)
                    text_out = out.read(pkg.RESULT_BYTES + 1)
                (inspection/'.firmware-stdout').unlink()
                summary['steps']['firmwareTimedOut'] = late
                return subprocess.CompletedProcess(argv, 124 if late else code, stdout=text_out)
            firmware_status = pkg.firmware_observe(root_dir, run_id, attempt, firmware_runner, require_package=False)
            summary['steps']['firmware'] = json.loads((inspection/'firmware-attempt.json').read_text())['category']
            if firmware_status != 0:
                raise Refused('firmware_failed: the firmware observation neither observed nor refused cleanly')
            firmware = pkg.firmware_observed(root_dir)
            pkg.new_dir(root_dir/'classify-work')
            fd = os.open(inspection/'classify.json', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, 'w') as out:
                code, late = bounded(classify_argv(root_dir, source/'package'/pkg.INSTALL, firmware),
                                     BOUNDS['classifySeconds'], out, popen)
            summary['steps']['classify'] = {'exit': code, 'timedOut': late, 'firmwareObservation': firmware}
            if late or code not in (0, 3):
                raise Refused('classify_failed: classify neither reported ready nor incomplete')
            status = 0
    except Refused as error:
        summary['refused'] = str(error)
    finally:
        summary['status'] = 'inspected' if status == 0 else 'refused'
        write_once(inspection/'inspect.json', summary)
    return status


def report(root_dir, upload_dir):
    upload_dir = Path(upload_dir)
    upload_dir.mkdir(mode=0o700)
    state = {'schema': 'roseos.rango-factory-inspect-cleanup.v1', 'taskRoot': str(root_dir),
             'taskRootExists': root_dir.is_dir() and not root_dir.is_symlink()}
    if state['taskRootExists']:
        scratch = root_dir/'classify-work'
        left = sorted(p.name for p in scratch.iterdir()) if scratch.is_dir() and not scratch.is_symlink() else []
        if scratch.is_dir() and not scratch.is_symlink() and not left:
            scratch.rmdir()
        state.update(classifyWorkLeft=left, classifyWorkRemoved=not os.path.lexists(scratch),
                     uploaded=sorted([*REPORTS, 'cleanup.json']))
        write_once(root_dir/'cleanup.json', state)
        for name in REPORTS:
            source = root_dir/'inspection'/name
            if source.is_file() and not source.is_symlink():
                (upload_dir/name).write_bytes(source.read_bytes())
        (upload_dir/'cleanup.json').write_bytes((root_dir/'cleanup.json').read_bytes())
    else:
        (upload_dir/'cleanup.json').write_text(json.dumps(state, indent=2, sort_keys=True) + '\n')
    return state


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('command', choices=('check', 'inspect', 'report'))
    p.add_argument('--run-id')
    p.add_argument('--attempt')
    p.add_argument('--upload-dir')
    a = p.parse_args(argv)
    try:
        bounds, outer, minutes = check()
        if a.command == 'check':
            if a.run_id or a.attempt or a.upload_dir:
                raise pkg.Invalid('check takes no other option')
            print(f'outer_seconds={outer}\njob_minutes={minutes}')
            return 0
        root_dir = inspect_root(a.run_id, a.attempt)
        if (a.command == 'report') != bool(a.upload_dir):
            raise pkg.Invalid('--upload-dir is for report only, and report needs it')
        if a.command == 'inspect':
            return inspect(root_dir, a.run_id, a.attempt, RETAINED)
        print(json.dumps(report(root_dir, a.upload_dir), sort_keys=True))
        return 0
    except Unbound as error:
        print(json.dumps({'verdict': 'SETUP_MISSING', 'error': str(error)}), file=sys.stderr)
        return 4
    except (pkg.Invalid, FileExistsError, KeyError, ValueError) as error:
        print(json.dumps({'verdict': 'INVALID', 'error': str(error)}), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
