#!/usr/bin/env python3
"""Durable Mustang allocation against an injected object store.

Objects are created once with ifGenerationMatch=0. A replay of the same run and
attempt reads that object back. A conflicting create loses and takes the next
number. Nothing here contacts GCP, and issued stays false.
"""
import json
from pathlib import Path
from urllib.parse import quote
import re

import mustang_epoch as epoch

ROOT = Path(__file__).resolve().parents[1]
PROVISION = ROOT/'os/build-only/mustang-allocation-provision-20261107.json'
PREFIX = 'epoch-202613/numbers/'
SCHEMA = 'roseos.mustang-allocation-reservation.v1'
KEYS = {'schema', 'epoch', 'sequence', 'build', 'previous', 'proposal', 'runId', 'runAttempt',
        'issued', 'status'}
RUN = re.compile(r'[1-9][0-9]{0,11}')


class PreconditionFailed(Exception):
    pass


def provision():
    record = json.loads(PROVISION.read_text())
    if record.get('schema') != 'roseos.mustang-allocation-provision.v1' or record.get('backendEstablished') is not False \
            or record.get('bucketCreated') is not False or record.get('issued') is not False \
            or record.get('projectNumber') != '1072799993443' or record.get('prefix') != PREFIX \
            or record.get('permissions') != ['storage.objects.create', 'storage.objects.get', 'storage.objects.list'] \
            or 'storage.objects.delete' not in record.get('notGranted', []) \
            or 'ifGenerationMatch=0' not in record.get('atomicCreate', ''):
        raise ValueError('allocation provision contract differs')
    return record


def object_name(build):
    return PREFIX + build + '.json'


def digest(value):
    return epoch.digest(value)


def authenticate(events):
    """Re-read every stored reservation. A caller's list is not the authority."""
    if not isinstance(events, list):
        raise ValueError('allocation journal must be a list')
    previous = epoch.genesis()
    inners = []
    seen = set()
    for index, event in enumerate(events):
        if not isinstance(event, dict) or set(event) != KEYS:
            raise ValueError('allocation reservation fields differ')
        if event.get('schema') != SCHEMA or event.get('epoch') != epoch.EPOCH or event.get('issued') is not False \
                or event.get('status') != 'reserved':
            raise ValueError('allocation reservation is not an unissued reserve')
        if isinstance(event.get('sequence'), bool) or event.get('sequence') != index \
                or event.get('build') != str(epoch.RANGE_START + index) or event.get('previous') != previous:
            raise ValueError('allocation sequence or predecessor differs')
        run_id, attempt = event.get('runId'), event.get('runAttempt')
        if type(run_id) is not str or not RUN.fullmatch(run_id) or run_id in seen:
            raise ValueError('allocation run id differs')
        if isinstance(attempt, bool) or type(attempt) is not int or attempt < 1:
            raise ValueError('allocation attempt differs')
        seen.add(run_id)
        proposal = event.get('proposal')
        expected = epoch.append(inners, {'build': event['build'], 'timestamp': proposal.get('timestamp') if isinstance(proposal, dict) else None,
                                         'rollbackIndex': proposal.get('rollbackIndex') if isinstance(proposal, dict) else None,
                                         'securityPatchLevel': proposal.get('securityPatchLevel') if isinstance(proposal, dict) else None,
                                         'epoch': epoch.EPOCH, 'previous': proposal.get('previous') if isinstance(proposal, dict) else None,
                                         'observed': ()})[-1]
        if proposal != expected:
            raise ValueError('allocation proposal is not the vetted epoch event')
        inners.append(proposal)
        previous = digest(event)
    return previous


def read_chain(store):
    names = store.list_prefix(PREFIX)
    if names != [object_name(str(epoch.RANGE_START + index)) for index in range(len(names))]:
        raise ValueError('allocation object names differ from the epoch sequence')
    events = []
    for name in names:
        body, generation = store.get(name)
        if generation != 1:
            raise ValueError('allocation object generation differs')
        events.append(body)
    authenticate(events)
    return events


def _body(events, run_id, attempt, timestamp, rollback_index, security_patch):
    build = str(epoch.RANGE_START + len(events))
    inners = [item['proposal'] for item in events]
    proposal = epoch.append(inners, {'build': build, 'timestamp': timestamp, 'rollbackIndex': rollback_index,
                                     'securityPatchLevel': security_patch, 'epoch': epoch.EPOCH,
                                     'previous': epoch.authenticate(inners), 'observed': ()})[-1]
    return {
        'schema': SCHEMA, 'epoch': epoch.EPOCH, 'sequence': len(events), 'build': build,
        'previous': epoch.genesis() if not events else digest(events[-1]), 'proposal': proposal,
        'runId': run_id, 'runAttempt': attempt, 'issued': False, 'status': 'reserved',
    }


def _same_request(event, timestamp, rollback_index, security_patch):
    proposal = event['proposal']
    return proposal['timestamp'] == timestamp and proposal['rollbackIndex'] == rollback_index \
        and proposal['securityPatchLevel'] == security_patch


def reserve(store, run_id, attempt, timestamp, rollback_index, security_patch, *, backend_established):
    """Create one immutable reservation, or return the identical replay. Issued stays false."""
    if backend_established is not True or store is None:
        raise ValueError('allocation backend is not established')
    if type(run_id) is not str or not RUN.fullmatch(run_id) or isinstance(attempt, bool) \
            or type(attempt) is not int or attempt < 1:
        raise ValueError('allocation run identity differs')
    for _ in range(8):
        events = read_chain(store)
        owned = [item for item in events if item['runId'] == run_id]
        if owned:
            event = owned[0]
            if event['runAttempt'] == attempt and _same_request(event, timestamp, rollback_index, security_patch):
                return event
            raise ValueError('allocation replay differs from the stored reservation')
        body = _body(events, run_id, attempt, timestamp, rollback_index, security_patch)
        name = object_name(body['build'])
        try:
            store.create(name, body)
        except PreconditionFailed:
            continue
        except Exception:
            stored, generation = store.get(name)
            if generation == 1 and stored == body:
                return stored
            raise
        stored, generation = store.get(name)
        if generation != 1 or stored != body:
            raise ValueError('allocation readback differs')
        read_chain(store)
        return stored
    raise ValueError('allocation conflict did not resolve')


def create_request(bucket, name, body):
    if bucket != provision()['bucket'] or not name.startswith(PREFIX) or not isinstance(body, dict):
        raise ValueError('allocation create request leaves the Mustang prefix')
    return {
        'method': 'POST',
        'url': 'https://storage.googleapis.com/upload/storage/v1/b/' + bucket
               + '/o?uploadType=media&name=' + quote(name, safe='') + '&ifGenerationMatch=0',
        'body': body,
        'headers': {'Content-Type': 'application/json'},
    }


class TransportStore:
    """Object store over the JSON API shape. The transport is injected; this opens no socket."""

    def __init__(self, transport, bucket):
        self.transport = transport
        self.bucket = bucket

    def create(self, name, body):
        status, _payload = self.transport(create_request(self.bucket, name, body))
        if status == 412:
            raise PreconditionFailed(name)
        if status != 200:
            raise ValueError('allocation create failed')
        return 1

    def get(self, name):
        status, payload = self.transport({'method': 'GET', 'url': 'https://storage.googleapis.com/storage/v1/b/'
                                         + self.bucket + '/o/' + quote(name, safe='') + '?alt=media'})
        if status == 404:
            raise KeyError(name)
        if status != 200 or not isinstance(payload, dict):
            raise ValueError('allocation read failed')
        return payload, 1

    def list_prefix(self, prefix):
        status, payload = self.transport({'method': 'GET', 'url': 'https://storage.googleapis.com/storage/v1/b/'
                                         + self.bucket + '/o?prefix=' + quote(prefix, safe='')})
        if status != 200 or not isinstance(payload, dict):
            raise ValueError('allocation list failed')
        names = payload.get('names')
        if not isinstance(names, list):
            raise ValueError('allocation list failed')
        return sorted(names)


def main():
    record = provision()
    print(json.dumps({'backendEstablished': record['backendEstablished'], 'issued': False,
                      'bucket': record['bucket'], 'serviceAccount': record['serviceAccount']}, sort_keys=True))
    return 4


if __name__ == '__main__':
    raise SystemExit(main())
