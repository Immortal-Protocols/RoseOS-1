#!/usr/bin/env python3
"""Mustang build allocation: reserve one fresh mustang build number, collision-checked, before anything consumes
it, and derive the integration's own new directories from it. Never a guessed or committed number.

  reserve --home H --run-id N --run-attempt N --source SHA --out F
      under the nonblocking shared builder lock (frankel.json host.lockPath; the same builder), observe every mustang
      identity already consumed:
        - pixel-ota releases (the public listing, as scripts/release-allocate.py lists it) tagged mustang-{build}
          (os/build-only/mustang-updater-route.json producerContract.tag);
        - builder storage H/rose-actions entries named mustang-integrate-<build> or mustang-reconcile-<build>;
        - earlier reservations H/rose-actions/mustang-allocations/<build>.json;
      then take YYYYMMDDNN (UTC day of the run clock, NN above every known build of that day, above every known build
      overall; release-allocate.next_build), write H/rose-actions/mustang-allocations/<build>.json write-once and
      print the record. The derived workDir H/rose-actions/mustang-integrate-<build> and integrationDir
      H/rose-actions/mustang-reconcile-<build> must not exist. Missing or unreadable state, an unknown entry, a malformed
      identity, a clock behind a known build or an exhausted day refuse with a fixed category; nothing is guessed.

Exit status: 0 reserved, 1 refused, 2 invalid input.
"""
import argparse
import contextlib
import importlib.util
import json
import os
from pathlib import Path
import re
import sys
import time
import datetime
import mustang_lane
import mustang_paths

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('release_allocate', ROOT/'scripts/release-allocate.py')
allocator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(allocator)
Refused = allocator.Refused

SCHEMA = 'roseos.mustang-allocation.v1'
DEVICE = 'mustang'
RESERVATIONS = 'mustang-allocations'
DERIVED = ('mustang-integrate-{build}', 'mustang-reconcile-{build}')
BUILD = re.compile(r'[1-9][0-9]{9}')
STORAGE = re.compile(r'mustang-(?:integrate|reconcile)-([0-9]{10})')
DAILY_LIMIT = 99
RECORD_KEYS = {'schema', 'device', 'build', 'source', 'runId', 'runAttempt', 'reservedAt', 'workDir', 'integrationDir',
               'observed'}


def tag_pattern(root=ROOT):
    route = json.loads((root/'os/build-only/mustang-updater-route.json').read_text())
    pattern = route['producerContract']['tag']
    if pattern != 'mustang-{build}':
        raise Refused('invalid: the mustang release tag is not mustang-{build}')
    return pattern


def read_reservation(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd) as f:
        record = json.load(f)
    if not isinstance(record, dict) or set(record) != RECORD_KEYS or record['schema'] != SCHEMA \
            or record['device'] != DEVICE or not BUILD.fullmatch(str(record['build'])) or path.name != f'{record["build"]}.json':
        raise ValueError('not a mustang reservation')
    return record['build']


def observe(home, repository, api, fetch=None, root=ROOT):
    """(consumed builds, observed counts). Every source must be readable and fully recognised."""
    base = Path(home)/'rose-actions'
    if base.is_symlink() or not base.is_dir():
        raise Refused('state_unavailable: the builder attempt storage is missing')
    prefix, suffix = tag_pattern(root).split('{build}')
    builds = set()
    releases = allocator.list_releases(repository, api, fetch)
    for release in releases:
        tag = str(release.get('tag') or '')
        if tag.startswith(prefix) and tag.endswith(suffix):
            builds.add(tag[len(prefix):len(tag) - len(suffix)])
    storage = 0
    for entry in sorted(e.name for e in os.scandir(base)):
        if entry.startswith('mustang-integrate-') or entry.startswith('mustang-reconcile-'):
            match = STORAGE.fullmatch(entry)
            if not match:
                raise Refused(f'state_unavailable: unrecognised mustang storage entry {entry[:60]!r}')
            builds.add(match[1])
            storage += 1
    reservations = 0
    directory = base/RESERVATIONS
    if directory.exists() or directory.is_symlink():
        if directory.is_symlink() or not directory.is_dir():
            raise Refused('state_unavailable: the mustang reservation directory is not a plain directory')
        for path in sorted(directory.iterdir()):
            try:
                builds.add(read_reservation(path))
            except (OSError, ValueError, KeyError, TypeError):
                raise Refused(f'state_unavailable: unreadable mustang reservation {path.name[:40]}') from None
            reservations += 1
    bad = sorted(b for b in builds if not BUILD.fullmatch(b))
    if bad:
        raise Refused(f'state_unavailable: malformed mustang build identity {bad[0][:20]!r}')
    return builds, {'releasesListed': len(releases), 'mustangStorageEntries': storage, 'reservations': reservations,
                    'consumedBuilds': sorted(builds)}


def reserve(home, run_id, run_attempt, source, fetch=None, clock=time.time, lock=None, root=ROOT, lane=None):
    paths = mustang_paths.layout(lane) if lane is not None else None
    if paths is not None and Path(home) != paths['home']:
        raise Refused('invalid: allocation home differs from the dedicated Mustang layout')
    for label, value in (('run id', run_id), ('run attempt', run_attempt)):
        if not (isinstance(value, str) and value.isdigit() and value[0] != '0' and len(value) <= 20):
            raise Refused(f'invalid: a positive numeric {label} is required')
    if not re.fullmatch(r'[0-9a-f]{40}', str(source)):
        raise Refused('invalid: --source must be the dispatch commit')
    pipeline = json.loads((root/'os/release-pipeline/frankel.json').read_text())
    base = Path(home)/'rose-actions'
    with (lock or allocator.shared_lock)(str(paths['lockPath']) if paths is not None else pipeline['host']['lockPath']):
        builds, observed = observe(home, pipeline['publication']['repository'], pipeline['publication']['api'], fetch, root)
        now = datetime.datetime.fromtimestamp(int(clock()), datetime.timezone.utc)
        build = allocator.next_build(builds, now, DAILY_LIMIT)
        work, integration = (base/name.format(build=build) for name in DERIVED)
        for path in (work, integration):
            if os.path.lexists(path):
                raise Refused(f'state_unavailable: {path.name} already exists')
        record = {'schema': SCHEMA, 'device': DEVICE, 'build': build, 'source': source, 'runId': run_id,
                  'runAttempt': run_attempt, 'reservedAt': now.strftime('%Y-%m-%dT%H:%M:%SZ'), 'workDir': str(work),
                  'integrationDir': str(integration), 'observed': observed}
        directory = base/RESERVATIONS
        if not directory.exists():
            directory.mkdir(mode=0o755)
        fd = os.open(directory/f'{build}.json', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
        with os.fdopen(fd, 'w') as f:
            f.write(json.dumps(record, indent=2, sort_keys=True) + '\n')
    return record


def main(argv=None):
    mustang_lane.reject_legacy()
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('command', choices=('reserve',))
    for flag in ('--home', '--run-id', '--run-attempt', '--source', '--out'):
        p.add_argument(flag, required=True)
    a = p.parse_args(argv)
    try:
        if not os.path.isabs(a.home) or not os.path.isabs(a.out):
            raise Refused('invalid: --home and --out must be absolute')
        record = reserve(a.home, a.run_id, a.run_attempt, a.source)
        fd = os.open(a.out, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
        with os.fdopen(fd, 'w') as f:
            f.write(json.dumps(record, indent=2, sort_keys=True) + '\n')
    except Refused as error:
        print(json.dumps({'verdict': 'REFUSED', 'error': str(error)}), file=sys.stderr)
        return 1
    print(json.dumps({'verdict': 'RESERVED', 'build': record['build'], 'workDir': record['workDir'],
                      'integrationDir': record['integrationDir']}, sort_keys=True))
    return 0


if __name__ == '__main__':
    sys.exit(main())
