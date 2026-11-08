#!/usr/bin/env python3
"""One-time lock preparation from an existing authorized root session.

Default is a plan. No login, credential creation, START/STOP, parent permission
change or existing-file repair is provided. Do not rerun the full bootstrap to
prepare this one file. Any existing entry refuses without replacing it.
"""
import argparse
import json
import os
from pathlib import Path
import stat
import subprocess
import sys

import mustang_hosttools as readers
import mustang_lane
import mustang_ota

ROOT = Path(__file__).resolve().parents[1]
NAME = 'mustang.lock'


def validate_parent(info):
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_gid != 0 or info.st_mode & 0o022:
        raise ValueError('lock parent must be a root-owned directory without group/world write')


def parent_fd(path):
    parts = readers._body(str(path))
    if not parts:
        raise ValueError('lock parent must be a non-root absolute directory')
    fd = readers._walk_dir(parts)
    try:
        validate_parent(os.fstat(fd))
    except BaseException:
        os.close(fd)
        raise
    return fd


def create_at(directory):
    # O_EXCL refuses every existing entry, including links, without touching it.
    fd = os.open(NAME, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                 0o644, dir_fd=directory)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_nlink != 1:
            raise ValueError('created lock identity differs; preserve and inspect it')
        os.fchmod(fd, 0o644)
        os.fsync(fd)
        result = {'uid': info.st_uid, 'gid': info.st_gid, 'mode': stat.S_IMODE(os.fstat(fd).st_mode),
                  'sizeBytes': os.fstat(fd).st_size}
        os.fsync(directory)
        return result
    finally:
        os.close(fd)


def host_command(argv, **kwargs):
    if not argv or argv[0] != 'findmnt':
        raise ValueError('unexpected host identity command')
    return subprocess.run(['/usr/bin/findmnt', *argv[1:]],
                          env={'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8'}, **kwargs)


def prepare():
    if os.geteuid() != 0:
        raise ValueError('lock preparation requires an existing authorized root session')
    lane = mustang_lane.validate(mustang_lane.configuration())
    cfg = json.loads((ROOT/'os/build-only/mustang-runtime.json').read_text())
    dedicated = mustang_ota.module('mustang_lock_identity', 'mustang-dedicated.py')
    paths = dedicated.host_identity(lane, cfg['dataFilesystemUuid'], execute=host_command, mount=cfg['dataMount'])
    target = Path(lane['lockPath'])
    if target.parent != paths['home'] or target.name != NAME:
        raise ValueError('canonical lock path differs')
    directory = parent_fd(paths['home'])
    try:
        created = create_at(directory)
    finally:
        os.close(directory)
    return {'schema': 'roseos.mustang-lock-preparation.v1', 'builder': lane['vm'],
            'diskUuid': cfg['dataFilesystemUuid'], 'lockPath': str(target), 'created': created,
            'createdExclusively': True, 'fileAndDirectorySynced': True,
            'authenticatedStop': False, 'buildStarted': False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args(argv)
    if not args.execute:
        lane = mustang_lane.validate(mustang_lane.configuration())
        print(json.dumps({'execute': False, 'builder': lane['vm'], 'lockPath': lane['lockPath'],
                          'requiresExistingRootSession': True, 'overwrite': False}, sort_keys=True))
        return 4
    try:
        print(json.dumps(prepare(), sort_keys=True))
        return 0
    except FileExistsError:
        print('Canonical lock entry already exists; preserved, not repaired', file=sys.stderr)
    except ValueError:
        print('Lock preparation admission refused; no existing entry was replaced', file=sys.stderr)
    except (OSError, readers._Refused):
        print('Lock preparation failed; inspect current state before any retry', file=sys.stderr)
    return 4


if __name__ == '__main__':
    sys.exit(main())
