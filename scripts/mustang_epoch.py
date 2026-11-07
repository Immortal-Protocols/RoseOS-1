#!/usr/bin/env python3
"""Proposed Mustang numbering epoch. No live allocation and no durable reserve.

The range is a proposal: ten digits, above the evidenced builds, inside a signed
32-bit int, and not a date the legacy allocator can emit. An in-memory chain
check is not an authority. Nothing here writes a journal or issues a build.
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
EVENT_KEYS = {'schema', 'epoch', 'sequence', 'build', 'previous', 'timestamp', 'rollbackIndex',
              'securityPatchLevel', 'issued', 'status'}
EVENT_SCHEMA = 'roseos.mustang-retired-epoch-event.v1'
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


def ten_digit(build, label):
    if type(build) is not str or not BUILD.fullmatch(build):
        raise ValueError(label+' must be a ten-digit string')
    return int(build)


def in_range(build):
    number = ten_digit(build, 'Mustang epoch build')
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


def security_patch_timestamp(level):
    """GMT midnight of PLATFORM_SECURITY_PATCH, the AVB rollback source."""
    if type(level) is not str:
        raise ValueError('security patch level must be a YYYY-MM-DD string')
    try:
        day = datetime.datetime.strptime(level, '%Y-%m-%d')
    except ValueError:
        raise ValueError('security patch level is not a real date') from None
    if day.strftime('%Y-%m-%d') != level:
        raise ValueError('security patch level is not a real date')
    return int(day.replace(tzinfo=datetime.timezone.utc).timestamp())


def observed_ids(observed):
    """Exact ten-digit strings. A non-string is not coerced into an ID."""
    if not isinstance(observed, (list, tuple)):
        raise ValueError('observed builds must be a list of ten-digit strings')
    found = []
    for item in observed:
        found.append(ten_digit(item, 'observed build'))
    for item in EVIDENCED_BUILDS:
        found.append(ten_digit(item, 'evidenced build'))
    return found


def check_candidate(build, timestamp, rollback_index, security_patch, observed=()):
    """Validate one proposal. This does not reserve or issue it."""
    number = in_range(build)
    higher = [item for item in observed_ids(observed) if item >= number]
    if higher:
        raise ValueError('build is not strictly newer than every observed published or installed ID')
    if isinstance(timestamp, bool) or type(timestamp) is not int or not PUBLISHED_TIMESTAMP < timestamp < INT64_MAX:
        raise ValueError('ro.build.date.utc must be after the published 2026110401 timestamp and fit int64')
    if timestamp <= INSTALLED_TIMESTAMP:
        raise ValueError('ro.build.date.utc must be after the installed baseline')
    stamp = security_patch_timestamp(security_patch)
    if stamp < security_patch_timestamp(SECURITY_PATCH_FLOOR):
        raise ValueError('security patch level is older than 2026-10-05')
    if isinstance(rollback_index, bool) or type(rollback_index) is not int or rollback_index != stamp:
        raise ValueError('AVB rollback index must be PLATFORM_SECURITY_PATCH_TIMESTAMP for the declared patch')
    if timestamp < stamp:
        raise ValueError('ro.build.date.utc is earlier than the declared security patch')
    return {'build': build, 'issued': False, 'reserved': False, 'epoch': EPOCH}


def genesis():
    return digest({'schema': 'roseos.mustang-retired-epoch-genesis.v1', 'epoch': EPOCH})


def authenticate(events, observed=()):
    """Recompute the whole proposal chain. Length and the last hash are not authority."""
    if not isinstance(events, list):
        raise ValueError('journal events must be a list')
    previous = genesis()
    prior_time = PUBLISHED_TIMESTAMP
    prior_rollback = security_patch_timestamp(SECURITY_PATCH_FLOOR) - 1
    for index, event in enumerate(events):
        if not isinstance(event, dict) or set(event) != EVENT_KEYS:
            raise ValueError('journal event fields differ')
        if event.get('schema') != EVENT_SCHEMA or event.get('epoch') != EPOCH or event.get('status') != 'proposal' \
                or event.get('issued') is not False:
            raise ValueError('journal event is not an unissued proposal')
        if isinstance(event.get('sequence'), bool) or type(event.get('sequence')) is not int or event['sequence'] != index:
            raise ValueError('journal sequence differs')
        if event.get('build') != str(RANGE_START + index) or event.get('previous') != previous:
            raise ValueError('journal build sequence or predecessor differs')
        check_candidate(event['build'], event.get('timestamp'), event.get('rollbackIndex'),
                        event.get('securityPatchLevel'), observed)
        if event['timestamp'] <= prior_time or event['rollbackIndex'] < prior_rollback:
            raise ValueError('journal timestamp or rollback index moved backwards')
        prior_time = event['timestamp']
        prior_rollback = event['rollbackIndex']
        previous = digest(event)
    return previous


def append(events, proposal):
    """Return a longer in-memory proposal. This does not reserve, issue, or write."""
    if not isinstance(proposal, dict):
        raise ValueError('journal proposal must be an object')
    observed = proposal.get('observed', ())
    previous = authenticate(events, observed)
    expected = str(RANGE_START + len(events))
    if proposal.get('build') != expected or proposal.get('epoch') != EPOCH or proposal.get('previous') != previous:
        raise ValueError('journal build is not the next unissued proposal')
    check_candidate(proposal.get('build'), proposal.get('timestamp'), proposal.get('rollbackIndex'),
                    proposal.get('securityPatchLevel'), observed)
    if events and (proposal['timestamp'] <= events[-1]['timestamp'] or proposal['rollbackIndex'] < events[-1]['rollbackIndex']):
        raise ValueError('journal timestamp or rollback index moved backwards')
    return events + [{'schema': EVENT_SCHEMA, 'epoch': EPOCH, 'sequence': len(events), 'build': expected,
                      'previous': previous, 'timestamp': proposal['timestamp'],
                      'rollbackIndex': proposal['rollbackIndex'], 'securityPatchLevel': proposal['securityPatchLevel'],
                      'issued': False, 'status': 'proposal'}]


def describe():
    return {
        'epoch': EPOCH,
        'selectedMode': 'retired-legacy-epoch',
        'rangeStatus': 'proposal',
        'reserved': False,
        'authority': None,
        'rangeStart': str(RANGE_START),
        'rangeEnd': str(RANGE_END),
        'proposalStart': str(RANGE_START),
        'issued': False,
        'selectsBuildNumber': False,
        'callsNextBuild': False,
        'inMemoryChainIsAuthority': False,
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
