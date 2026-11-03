#!/usr/bin/env python3
"""FOLD-ARCHIVE-SIGN workflow wiring (Release-owned) for os-release.yml mode rango-factory-sign: a detached signature
for the installer ZIP that rango-factory-package run 41515240855 attempt 1 already generated and retained, with no
regeneration and no change to the archive.

  check                         hosted and on the builder: the retained identity (the inspect mode's committed pin)
                                and Signing's own `factory-archive-sign.py check` (its committed binding and the
                                flavor factorySigning pin). Exit 4 while Signing reports anything unbound; prints the
                                bounds as job outputs. Reads no host path.
  sign --run-id --attempt       exactly `factory-archive-sign.py sign --archive R/package/rango-install-2026103001.zip
                                --archive-sha256 <pin> --lock <frankel host.lockPath> --out NEW`, with R the retained
                                package task root and NEW this run's new root <taskParent>/rango-factory-sign-<run>-<attempt>.
                                The signer holds the shared lock, fetches the pinned secret versions itself and writes
                                only NEW/{archive}.sig and NEW/factory-archive-sign-receipt.json.
  report --run-id --attempt --upload-dir NEW_DIR
                                always: copies only those two files (regular, never a link) into a new upload directory.

Bounds (ceilings sized to the work, not measurements): two hashes of the 2.1 GB ZIP within HASH_SECONDS each (the
inspect mode measured about 21 s), Signing's own sign and verify limits (600 s each), the kill grace per step and a
margin. A stop is a recorded failure, never a signature. Signing needs a signer login on this job, an access change
that needs its own approval. Exit status: 0 ok, 2 invalid, 3 refused, 4 unbound.
"""
import argparse
import importlib.util
import json
import math
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
SIGNER = ROOT/'scripts/factory-archive-sign.py'
spec = importlib.util.spec_from_file_location('rango_factory_inspect_wiring', ROOT/'scripts/rango-factory-inspect-wiring.py')
inspect = importlib.util.module_from_spec(spec)
spec.loader.exec_module(inspect)
pkg = inspect.pkg

TASK_NAME = 'rango-factory-sign-{run_id}-{attempt}'
SIGNATURE = f'{pkg.INSTALL}.sig'
RECEIPT = 'factory-archive-sign-receipt.json'
UPLOAD = (SIGNATURE, RECEIPT)
BOUNDS = {'hashSeconds': 300, 'signSeconds': 600, 'verifySeconds': 600}
KILL_GRACE_SECONDS, OUTER_MARGIN_SECONDS, JOB_MARGIN_MINUTES = 30, 120, 10


class Unbound(Exception):
    pass


def signer_check(run=subprocess.run):
    """Signing's own hosted check: exit 0 ready, exit 4 with its missing list (passed through), else invalid."""
    result = run([sys.executable, str(SIGNER), 'check'], capture_output=True, text=True)
    if result.returncode == 4:
        raise Unbound(f'factory-archive-sign.py check: {(result.stdout or result.stderr).strip()[-600:]}')
    if result.returncode != 0:
        raise pkg.Invalid(f'factory-archive-sign.py check exited {result.returncode}')


def check(root=ROOT, run=subprocess.run):
    inspect.check(root)  # the retained identity, exactly as the inspect mode pins it
    signer_check(run)
    outer = 2 * BOUNDS['hashSeconds'] + BOUNDS['signSeconds'] + BOUNDS['verifySeconds'] + 3 * KILL_GRACE_SECONDS \
        + OUTER_MARGIN_SECONDS
    return outer, math.ceil((outer + KILL_GRACE_SECONDS) / 60) + JOB_MARGIN_MINUTES


def sign_root(run_id, attempt, parent=pkg.TASK_PARENT):
    pkg.task_root(run_id, attempt, parent=parent)  # the same numeric checks
    return Path(parent)/TASK_NAME.format(run_id=run_id, attempt=attempt)


def sign_argv(run_id, attempt, root=ROOT):
    archive = inspect.retained_root(inspect.RETAINED)/'package'/pkg.INSTALL
    return [sys.executable, str(SIGNER), 'sign', '--archive', str(archive), '--archive-sha256', inspect.RETAINED['sha256'],
            '--lock', pkg.lock_path(root), '--out', str(sign_root(run_id, attempt))]


def report(root_dir, upload_dir):
    upload_dir = Path(upload_dir)
    upload_dir.mkdir(mode=0o700)
    state = {'schema': 'roseos.rango-factory-sign-upload.v1', 'root': str(root_dir), 'uploaded': [], 'absent': []}
    plain = root_dir.is_dir() and not root_dir.is_symlink()
    for name in UPLOAD:
        source = root_dir/name
        if plain and source.is_file() and not source.is_symlink():
            data = source.read_bytes()
            fd = os.open(upload_dir/name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, 'wb') as f:
                f.write(data)
            state['uploaded'].append(name)
        else:
            state['absent'].append(name)
    return state


def main(argv=None, run=subprocess.run):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('command', choices=('check', 'sign', 'report'))
    p.add_argument('--run-id')
    p.add_argument('--attempt')
    p.add_argument('--upload-dir')
    a = p.parse_args(argv)
    try:
        if a.command == 'report':  # always, after sign: never gated on the signer's readiness
            if not a.upload_dir or not os.path.isabs(a.upload_dir):
                raise pkg.Invalid('report needs an absolute --upload-dir')
            print(json.dumps(report(sign_root(a.run_id, a.attempt), a.upload_dir), sort_keys=True))
            return 0
        outer, minutes = check(run=run)
        if a.upload_dir:
            raise pkg.Invalid('--upload-dir is for report only')
        if a.command == 'check':
            if a.run_id or a.attempt:
                raise pkg.Invalid('check takes no other option')
            print(f'outer_seconds={outer}\njob_minutes={minutes}')
            return 0
        argv = sign_argv(a.run_id, a.attempt)
        print(json.dumps({'signer': argv[1:]}), flush=True)
        return run(argv).returncode
    except Unbound as error:
        print(json.dumps({'verdict': 'SETUP_MISSING', 'error': str(error)}), file=sys.stderr)
        return 4
    except (pkg.Invalid, FileExistsError, KeyError, ValueError) as error:
        print(json.dumps({'verdict': 'INVALID', 'error': str(error)}), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
