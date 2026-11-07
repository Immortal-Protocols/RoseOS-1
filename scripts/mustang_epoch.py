#!/usr/bin/env python3
"""Retired Mustang numbering epoch. No live allocation and no call to next_build.

The reserved IDs are ten digits, greater than every evidenced Mustang and
Frankel build, and impossible for the legacy UTC date allocator to emit.
Nothing here writes a journal or selects a build for a run.
"""
import datetime
import hashlib
import json
import re
import sys

RANGE_START = 2026130001
RANGE_END = 2026139999
INT32_MAX = 2147483647
INT64_MAX = 2**63 - 1
PUBLISHED_TIMESTAMP = 1793755347
INSTALLED_TIMESTAMP = 1793742435
SECURITY_PATCH_FLOOR = '2026-10-05'
QUARANTINE_LOW = 2026110402
QUARANTINE_HIGH = 2026110499
EVIDENCED_BUILDS = ('2026110301', '2026110302', '2026110401', '2026110403', '2026110501')
BUILD = re.compile(r'[1-9][0-9]{9}')
EPOCH_BODY = {
    'schema': 'roseos.mustang-retired-epoch.v1',
    'rangeStart': str(RANGE_START),
    'rangeEnd': str(RANGE_END),
    'reason': 'First eight digits are year 2026 month 13, which the legacy UTC date allocator cannot format.',
}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


EPOCH = digest(EPOCH_BODY)


def legacy_date(build):
    if not BUILD.fullmatch(build):
        return False
    try:
        datetime.datetime.strptime(build[:8], '%Y%m%d')
    except ValueError:
        return False
    return 1 <= int(build[8:]) <= 99


def in_range(build):
    if not BUILD.fullmatch(str(build)):
        raise ValueError('Mustang epoch build must be exactly ten digits')
    number = int(build)
    if QUARANTINE_LOW <= number <= QUARANTINE_HIGH:
        raise ValueError('failed-run day is quarantined and is not a consumed build')
    if build in EVIDENCED_BUILDS:
        raise ValueError('build collides with an evidenced Mustang or Frankel release')
    if legacy_date(build):
        raise ValueError('Mustang epoch build collides with the legacy date allocator')
    if not RANGE_START <= number <= RANGE_END:
        raise ValueError('Mustang epoch build is outside 2026130001-2026139999')
    if number > INT32_MAX:
        raise ValueError('Mustang epoch build must fit a positive signed 32-bit int')
    return number


def check_candidate(build, timestamp, rollback_index, security_patch, observed=()):
    """Validate one ID. This does not reserve it."""
    number = in_range(build)
    if isinstance(timestamp, bool) or type(timestamp) is not int or not PUBLISHED_TIMESTAMP < timestamp < INT64_MAX:
        raise ValueError('ro.build.date.utc must be after the published 2026110401 timestamp and fit int64')
    if timestamp <= INSTALLED_TIMESTAMP:
        raise ValueError('ro.build.date.utc must be after the installed baseline')
    if type(rollback_index) is not int or isinstance(rollback_index, bool) or not 0 < rollback_index < INT64_MAX:
        raise ValueError('AVB rollback index must be a separate positive int64')
    if rollback_index == number:
        raise ValueError('AVB rollback index must not be the build number')
    if not re.fullmatch(r'20[0-9]{2}-[0-9]{2}-[0-9]{2}', str(security_patch)):
        raise ValueError('security patch level must be YYYY-MM-DD')
    if tuple(int(part) for part in security_patch.split('-')) < tuple(int(part) for part in SECURITY_PATCH_FLOOR.split('-')):
        raise ValueError('security patch level is older than 2026-10-05')
    seen = {str(item) for item in observed} | set(EVIDENCED_BUILDS)
    if build in seen:
        raise ValueError('build collides with an evidenced or observed release')
    return {'build': build, 'issued': False, 'epoch': EPOCH}


def genesis():
    return digest({'schema': 'roseos.mustang-retired-epoch-genesis.v1', 'epoch': EPOCH})


def append(events, proposal):
    """Return a new in-memory journal. The caller must not write the allocation state."""
    if not isinstance(events, list):
        raise ValueError('journal events must be a list')
    expected = str(RANGE_START + len(events))
    if proposal.get('build') != expected or proposal.get('epoch') != EPOCH:
        raise ValueError('journal build is not the next unissued epoch id')
    previous = genesis() if not events else digest(events[-1])
    if proposal.get('previous') != previous:
        raise ValueError('journal predecessor differs')
    check_candidate(proposal.get('build'), proposal.get('timestamp'), proposal.get('rollbackIndex'),
                    proposal.get('securityPatchLevel'), proposal.get('observed') or ())
    if any(item.get('build') == proposal.get('build') for item in events):
        raise ValueError('journal build was already recorded')
    event = {'schema': 'roseos.mustang-retired-epoch-event.v1', 'epoch': EPOCH, 'build': expected,
             'previous': previous, 'timestamp': proposal['timestamp'], 'rollbackIndex': proposal['rollbackIndex'],
             'securityPatchLevel': proposal['securityPatchLevel'], 'issued': False}
    return events + [event]


def describe():
    return {
        'epoch': EPOCH,
        'selectedMode': 'retired-legacy-epoch',
        'rangeStart': str(RANGE_START),
        'rangeEnd': str(RANGE_END),
        'recommendedNext': str(RANGE_START),
        'issued': False,
        'selectsBuildNumber': False,
        'callsNextBuild': False,
        'consumedBuilds': None,
        'quarantine': {'low': str(QUARANTINE_LOW), 'high': str(QUARANTINE_HIGH), 'consumed': False,
                       'clockProven': False, 'run': '41884368820'},
        'evidencedBuilds': list(EVIDENCED_BUILDS),
        'timestampFloor': str(PUBLISHED_TIMESTAMP),
        'securityPatchFloor': SECURITY_PATCH_FLOOR,
        'rollbackSource': 'PLATFORM_SECURITY_PATCH_TIMESTAMP',
    }


def main():
    if sys.argv[1:] == ['describe']:
        print(json.dumps(describe(), sort_keys=True))
        return 0
    print('Mustang epoch allocation is not enabled', file=sys.stderr)
    return 4


if __name__ == '__main__':
    sys.exit(main())
