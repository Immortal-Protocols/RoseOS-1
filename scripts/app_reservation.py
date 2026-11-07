#!/usr/bin/env python3
"""Rose Agent version reservations (roseos.app-version-reservation.v1): the receipt check shared by the hosted build
(scripts/host-app-release.py --hosted) and its publisher (scripts/app_release_publisher.py --hosted).

A reservation receipt is exactly {schema, packageName, versionCode, sourceSha, candidateSha, allocationSha256,
owner {workflowRef, runId, runAttempt}, store, reservationSha256}. reservationSha256 is the sha256 of the canonical JSON
(sorted keys, no spaces) of every other field. owned() admits only the reservation this run owns:
  - an exact record whose digest holds;
  - owned by this workflow run and attempt (GITHUB_WORKFLOW_REF, GITHUB_RUN_ID, GITHUB_RUN_ATTEMPT);
  - for exactly the package and versionCode APPS names, and this candidate, with its source and allocation digest;
  - recorded in the store the hosted config binds.
A failed or other run's reservation stays consumed; nothing here reuses or reclaims one. This module only checks
receipts: writing reservations to the shared store is pending decision D1.
"""
import hashlib
import json
import os
from pathlib import Path
import re

SCHEMA = 'roseos.app-version-reservation.v1'
KEYS = frozenset({'schema', 'packageName', 'versionCode', 'sourceSha', 'candidateSha', 'allocationSha256', 'owner',
                  'store', 'reservationSha256'})
RECEIPT_FIELDS = ('reservationSha256', 'allocationSha256', 'sourceSha', 'store')  # what an app receipt records


class Refused(Exception):
    pass


def canonical(data):
    return json.dumps(data, sort_keys=True, separators=(',', ':')).encode()


def digest(record):
    return hashlib.sha256(canonical({k: v for k, v in record.items() if k != 'reservationSha256'})).hexdigest()


def owned(path, env, package, apps, candidate, store, label='ROSE_APP_RESERVATION'):
    """The one reservation this run owns (its reservation step's receipt, named by label), or Refused."""
    if not path or os.path.islink(path) or not Path(path).is_file():
        raise Refused(f'{label} must name this run\'s reservation receipt, a regular file')
    record = json.loads(Path(path).read_text())
    if not isinstance(record, dict) or set(record) != KEYS or record['schema'] != SCHEMA \
            or record['reservationSha256'] != digest(record):
        raise Refused(f'the reservation receipt is not an exact {SCHEMA} record with its digest')
    owner = {'workflowRef': env.get('GITHUB_WORKFLOW_REF'), 'runId': env.get('GITHUB_RUN_ID'),
             'runAttempt': env.get('GITHUB_RUN_ATTEMPT')}
    if not all(owner.values()) or record['owner'] != owner:
        raise Refused('the reservation is not owned by this run')
    if record['packageName'] != package or type(record['versionCode']) is not int \
            or apps != [[package, record['versionCode']]]:
        raise Refused(f'APPS {apps} is not the reserved {package} {record["versionCode"]}')
    if record['candidateSha'] != candidate or not all(isinstance(record[k], str) and re.fullmatch(r'[0-9a-f]{40}', record[k])
                                                      for k in ('sourceSha', 'candidateSha')) \
            or not re.fullmatch(r'[0-9a-f]{64}', str(record['allocationSha256'])):
        raise Refused('the reservation does not name this candidate, its source and its allocation digest')
    if record['store'] != store:
        raise Refused('the reservation is not recorded in the store the hosted config binds')
    return record


def receipt_fields(record):
    """The reservation block an app receipt of this reservation carries."""
    return {k: record[k] for k in RECEIPT_FIELDS}
