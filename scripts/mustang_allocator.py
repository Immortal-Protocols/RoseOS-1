#!/usr/bin/env python3
"""Mustang allocation over the GCS JSON API.

Objects are created with ifGenerationMatch=0. Generations are the server's
decimal strings, and media reads repeat that generation as a precondition.
The committed provision admits neither the network nor an issued build.
"""
import json
from pathlib import Path
import re
import sys
import socket
import urllib.error
import urllib.request
from urllib.parse import quote

import mustang_epoch as epoch

ROOT = Path(__file__).resolve().parents[1]
PROVISION = ROOT/'os/build-only/mustang-allocation-provision-20261107.json'
NUMBER_PREFIX = 'epoch-202613/numbers/'
ISSUE_PREFIX = 'epoch-202613/issues/'
SCHEMA = 'roseos.mustang-allocation-reservation.v1'
ISSUE_SCHEMA = 'roseos.mustang-allocation-issue.v1'
KEYS = {'schema', 'epoch', 'sequence', 'build', 'previous', 'proposal', 'observed', 'runId',
        'runAttempt', 'issued', 'status'}
ISSUE_KEYS = {'schema', 'build', 'reservation', 'reservationGeneration', 'reservationSha256',
              'runId', 'runAttempt', 'issued', 'status'}
RUN = re.compile(r'[1-9][0-9]{0,11}')
MAX_GENERATION = 2**63 - 1
BUCKET_CONDITION = re.compile(
    r"resource\.type == 'storage\.googleapis\.com/Bucket' && "
    r"resource\.name == 'projects/_/buckets/([a-z0-9][a-z0-9._-]{1,220})'")
OBJECT_CONDITION = re.compile(
    r"resource\.type == 'storage\.googleapis\.com/Object' && "
    r"resource\.name\.startsWith\('projects/_/buckets/([a-z0-9][a-z0-9._-]{1,220})/objects/(epoch-202613/)'\)")


class PreconditionFailed(Exception):
    pass


class TransportLost(Exception):
    pass


def parse_generation(value):
    """GCS returns an opaque positive decimal int64. Callers must use that string, not a stand-in."""
    if type(value) is not str or not re.fullmatch(r'[1-9][0-9]*', value):
        raise ValueError('GCS generation must be a positive decimal int64')
    number = int(value)
    if number > MAX_GENERATION:
        raise ValueError('GCS generation must be a positive decimal int64')
    return number


def strict_int(value, label):
    if isinstance(value, bool) or type(value) is not int:
        raise ValueError(label+' must be an int')
    return value


def _condition_matches(expression, resource):
    bucket = BUCKET_CONDITION.fullmatch(expression)
    if bucket:
        return resource.get('type') == 'storage.googleapis.com/Bucket' \
            and resource.get('name') == 'projects/_/buckets/'+bucket.group(1)
    obj = OBJECT_CONDITION.fullmatch(expression)
    if obj:
        prefix = 'projects/_/buckets/'+obj.group(1)+'/objects/'+obj.group(2)
        return resource.get('type') == 'storage.googleapis.com/Object' \
            and type(resource.get('name')) is str and resource['name'].startswith(prefix)
    raise ValueError('allocation IAM condition is not the bucket-list or object-prefix form')


def _allows(binding, permission, resource):
    if permission not in binding.get('permissions', []):
        return False
    return _condition_matches(binding['condition']['expression'], resource)


def prove_iam(record):
    """List is granted on the dedicated bucket resource. Create and get use the object prefix."""
    bindings = record.get('bindings')
    bucket_name = record.get('bucket')
    if record.get('projectIamPolicy') is not False or record.get('attachedTo') != 'bucket' \
            or not isinstance(bindings, list) or len(bindings) != 2 or type(bucket_name) is not str:
        raise ValueError('allocation IAM is not two bucket bindings')
    lister, writer = bindings
    if lister.get('permissions') != ['storage.objects.list'] \
            or writer.get('permissions') != ['storage.objects.create', 'storage.objects.get']:
        raise ValueError('allocation roles mix list with object writes')
    if not BUCKET_CONDITION.fullmatch(lister['condition']['expression']) \
            or BUCKET_CONDITION.fullmatch(lister['condition']['expression']).group(1) != bucket_name:
        raise ValueError('list condition is not the dedicated bucket resource')
    writer_match = OBJECT_CONDITION.fullmatch(writer['condition']['expression'])
    if not writer_match or writer_match.group(1) != bucket_name or writer_match.group(2) != 'epoch-202613/':
        raise ValueError('object condition is not the epoch prefix')
    bucket = {'type': 'storage.googleapis.com/Bucket', 'name': 'projects/_/buckets/'+bucket_name}
    number = {'type': 'storage.googleapis.com/Object',
              'name': 'projects/_/buckets/'+bucket_name+'/objects/epoch-202613/numbers/2026130001.json'}
    other_bucket = {'type': 'storage.googleapis.com/Bucket', 'name': 'projects/_/buckets/other-bucket'}
    other_object = {'type': 'storage.googleapis.com/Object',
                    'name': 'projects/_/buckets/'+bucket_name+'/objects/other/file'}
    project = {'type': 'cloudresourcemanager.googleapis.com/Project', 'name': 'projects/'+record.get('projectId', '')}
    if not _allows(lister, 'storage.objects.list', bucket) or _allows(lister, 'storage.objects.list', number) \
            or _allows(lister, 'storage.objects.list', other_bucket) or _allows(lister, 'storage.objects.list', project):
        raise ValueError('list is not limited to the dedicated bucket resource')
    if _allows(writer, 'storage.objects.list', bucket) or not _allows(writer, 'storage.objects.create', number) \
            or not _allows(writer, 'storage.objects.get', number) or _allows(writer, 'storage.objects.create', other_object) \
            or _allows(writer, 'storage.objects.create', other_bucket) or _allows(writer, 'storage.objects.get', project):
        raise ValueError('object writes are not limited to the epoch prefix')
    for binding in bindings:
        if any(name in binding['permissions'] for name in ('storage.objects.delete', 'storage.objects.update')):
            raise ValueError('allocation role grants a mutating permission')
    if any(item in record.get('notGranted', []) for item in ('roles/storage.admin', 'a project-wide storage grant')) \
            and 'roles/storage.admin' in record['notGranted'] and 'a project-wide storage grant' in record['notGranted']:
        return True
    raise ValueError('allocation provision does not record the refused project-wide roles')


def provision():
    record = json.loads(PROVISION.read_text())
    if record.get('schema') != 'roseos.mustang-allocation-provision.v1' or record.get('backendEstablished') is not False \
            or record.get('networkAdmitted') is not False or record.get('issueAdmitted') is not False \
            or record.get('bucketCreated') is not False or record.get('issued') is not False \
            or record.get('projectNumber') != '1072799993443' or record.get('numberPrefix') != NUMBER_PREFIX \
            or record.get('issuePrefix') != ISSUE_PREFIX or 'ifGenerationMatch=0' not in record.get('atomicCreate', '') \
            or 'ifGenerationMatch=GENERATION' not in record.get('mediaRead', '') or 'pageToken' not in record.get('list', ''):
        raise ValueError('allocation provision contract differs')
    prove_iam(record)
    return record


def object_name(build):
    return NUMBER_PREFIX+build+'.json'


def digest(value):
    return epoch.digest(value)


def normalize_observed(observed):
    if isinstance(observed, (str, bytes)) or not isinstance(observed, (list, tuple)):
        raise ValueError('observed builds must be a list of ten-digit strings')
    found = []
    for item in observed:
        epoch.ten_digit(item, 'observed build')
        if item in found:
            raise ValueError('observed build is repeated')
        found.append(item)
    return found


def extends(previous, latest):
    if not isinstance(latest, list) or latest[:len(previous)] != list(previous):
        raise ValueError('allocation observed IDs were dropped or reordered')
    return latest


def proposal_for(prior, build, timestamp, rollback_index, security_patch):
    previous = epoch.genesis() if not prior else epoch.digest(prior[-1])
    return {'schema': epoch.EVENT_SCHEMA, 'epoch': epoch.EPOCH, 'sequence': len(prior), 'build': build,
            'previous': previous, 'timestamp': timestamp, 'rollbackIndex': rollback_index,
            'securityPatchLevel': security_patch, 'issued': False, 'status': 'proposal'}


def authenticate(events, latest=None):
    """Each stored proposal is checked against the observations recorded with it."""
    if not isinstance(events, list):
        raise ValueError('allocation journal must be a list')
    outer = epoch.genesis()
    inners = []
    seen = set()
    recorded = []
    prior_time = epoch.PUBLISHED_TIMESTAMP
    prior_rollback = epoch.security_patch_timestamp(epoch.SECURITY_PATCH_FLOOR) - 1
    for index, event in enumerate(events):
        if not isinstance(event, dict) or set(event) != KEYS:
            raise ValueError('allocation reservation fields differ')
        sequence = event.get('sequence')
        if isinstance(sequence, bool) or type(sequence) is not int or sequence != index:
            raise ValueError('allocation sequence must be an int')
        if event.get('schema') != SCHEMA or event.get('epoch') != epoch.EPOCH or event.get('issued') is not False \
                or event.get('status') != 'reserved' or event.get('build') != str(epoch.RANGE_START+index) \
                or event.get('previous') != outer:
            raise ValueError('allocation reservation is not the next unissued reserve')
        run_id, attempt = event.get('runId'), event.get('runAttempt')
        if type(run_id) is not str or not RUN.fullmatch(run_id) or run_id in seen:
            raise ValueError('allocation run id differs')
        if isinstance(attempt, bool) or type(attempt) is not int or attempt < 1:
            raise ValueError('allocation attempt must be an int')
        seen.add(run_id)
        stored = normalize_observed(event.get('observed'))
        if event.get('observed') != stored or stored[:len(recorded)] != recorded:
            raise ValueError('allocation observed IDs were dropped or reordered')
        proposal = event.get('proposal')
        if not isinstance(proposal, dict) or isinstance(proposal.get('sequence'), bool) \
                or type(proposal.get('sequence')) is not int:
            raise ValueError('allocation sequence must be an int')
        epoch.check_candidate(event['build'], proposal.get('timestamp'), proposal.get('rollbackIndex'),
                              proposal.get('securityPatchLevel'), stored)
        if proposal.get('timestamp') <= prior_time or proposal.get('rollbackIndex') < prior_rollback:
            raise ValueError('allocation timestamp or rollback moved backwards')
        if proposal != proposal_for(inners, event['build'], proposal.get('timestamp'), proposal.get('rollbackIndex'),
                                    proposal.get('securityPatchLevel')):
            raise ValueError('allocation proposal is not the vetted epoch event')
        inners.append(proposal)
        prior_time = proposal['timestamp']
        prior_rollback = proposal['rollbackIndex']
        recorded = stored
        outer = digest(event)
    if latest is not None:
        extends(recorded, normalize_observed(latest))
    return outer


def _object_url(bucket, name):
    return 'https://storage.googleapis.com/storage/v1/b/'+bucket+'/o/'+quote(name, safe='')


def list_objects(transport, bucket, prefix):
    token = None
    found = []
    seen = set()
    while True:
        query = 'prefix='+quote(prefix, safe='')
        if token is not None:
            query += '&pageToken='+quote(token, safe='')
        status, payload = transport({'method': 'GET',
                                     'url': 'https://storage.googleapis.com/storage/v1/b/'+bucket+'/o?'+query})
        if status != 200 or not isinstance(payload, dict) or 'names' in payload:
            raise ValueError('allocation list is not the GCS items schema')
        items = payload.get('items', [])
        if items is None:
            items = []
        if not isinstance(items, list):
            raise ValueError('allocation list items differ')
        for item in items:
            if not isinstance(item, dict) or type(item.get('name')) is not str:
                raise ValueError('allocation list item differs')
            found.append((item['name'], parse_generation(item.get('generation'))))
        page = payload.get('nextPageToken', None)
        if page is None:
            return found
        if type(page) is not str or not page or page in seen:
            raise ValueError('allocation page token differs')
        seen.add(page)
        token = page


def get_metadata(transport, bucket, name):
    status, payload = transport({'method': 'GET', 'url': _object_url(bucket, name)})
    if status == 404:
        raise KeyError(name)
    if status != 200 or not isinstance(payload, dict):
        raise ValueError('allocation metadata read failed')
    return payload, parse_generation(payload.get('generation'))


def get_media(transport, bucket, name, generation):
    number = parse_generation(generation)
    status, payload = transport({'method': 'GET', 'url': _object_url(bucket, name)
                                 + '?alt=media&ifGenerationMatch='+str(number)})
    if status == 412:
        raise PreconditionFailed(name)
    if status != 200 or not isinstance(payload, dict):
        raise ValueError('allocation media read failed')
    return payload


def create_request(bucket, name, body):
    if bucket != provision()['bucket'] or not name.startswith('epoch-202613/') or not isinstance(body, dict):
        raise ValueError('allocation create request leaves the Mustang prefix')
    return {'method': 'POST',
            'url': 'https://storage.googleapis.com/upload/storage/v1/b/'+bucket
                   + '/o?uploadType=media&name='+quote(name, safe='')+'&ifGenerationMatch=0',
            'body': body, 'headers': {'Content-Type': 'application/json'}}


def _read_back(transport, bucket, name, body, generation):
    _meta, meta_gen = get_metadata(transport, bucket, name)
    if meta_gen != generation:
        raise ValueError('allocation generation readback differs')
    media = get_media(transport, bucket, name, str(generation))
    if media != body:
        raise ValueError('allocation readback differs')
    return generation


def create_object(transport, bucket, name, body):
    request = create_request(bucket, name, body)
    try:
        status, payload = transport(request)
    except TransportLost:
        found = dict(list_objects(transport, bucket, name.rsplit('/', 1)[0]+'/'))
        if name not in found:
            raise ValueError('allocation create response was lost and the object is absent') from None
        return _read_back(transport, bucket, name, body, found[name])
    if status == 412:
        raise PreconditionFailed(name)
    if status != 200 or not isinstance(payload, dict):
        raise ValueError('allocation create failed')
    return _read_back(transport, bucket, name, body, parse_generation(payload.get('generation')))


class AuthorizedTransport:
    def __init__(self, http, token_source, admission):
        self.http = http
        self.token_source = token_source
        self.admission = admission

    def __call__(self, request):
        if not isinstance(self.admission, dict) or self.admission.get('backendEstablished') is not True \
                or self.admission.get('networkAdmitted') is not True:
            raise ValueError('allocation backend is not established')
        token = self.token_source()
        if type(token) is not str or len(token) < 20:
            raise ValueError('allocation token is absent')
        headers = dict(request.get('headers') or {})
        headers['Authorization'] = 'Bearer '+token
        signed = dict(request)
        signed['headers'] = headers
        return self.http(signed)


def urlopen_http(request, opener=urllib.request.urlopen):
    data = None
    headers = dict(request.get('headers') or {})
    if request['method'] == 'POST':
        data = json.dumps(request['body'], sort_keys=True, separators=(',', ':')).encode()
        headers.setdefault('Content-Type', 'application/json')
    outgoing = urllib.request.Request(request['url'], data=data, method=request['method'], headers=headers)
    try:
        with opener(outgoing, timeout=60) as response:
            status, raw = response.status, response.read()
    except urllib.error.HTTPError as error:
        status, raw = error.code, error.read()
    except (urllib.error.URLError, TimeoutError, socket.timeout):
        raise TransportLost('allocation response was lost') from None
    if not raw:
        return status, {}
    payload = json.loads(raw.decode())
    if not isinstance(payload, dict):
        raise ValueError('allocation response is not a JSON object')
    return status, payload


def open_admitted_transport(admission, token_source, opener=urllib.request.urlopen):
    if not isinstance(admission, dict) or admission.get('backendEstablished') is not True \
            or admission.get('networkAdmitted') is not True:
        raise ValueError('allocation backend is not established')
    return AuthorizedTransport(lambda request: urlopen_http(request, opener), token_source, admission)


class TransportStore:
    def __init__(self, transport, bucket):
        self.transport = transport
        self.bucket = bucket

    def list_entries(self, prefix):
        entries = list_objects(self.transport, self.bucket, prefix)
        if len({name for name, _gen in entries}) != len(entries):
            raise ValueError('allocation list repeated an object')
        return entries

    def create(self, name, body):
        return create_object(self.transport, self.bucket, name, body)

    def get(self, name, generation):
        _meta, meta_gen = get_metadata(self.transport, self.bucket, name)
        if meta_gen != generation:
            raise ValueError('allocation generation readback differs')
        return get_media(self.transport, self.bucket, name, str(meta_gen)), meta_gen


def read_chain(store, observed):
    entries = store.list_entries(NUMBER_PREFIX)
    by_name = {name: generation for name, generation in entries}
    expected = [object_name(str(epoch.RANGE_START+index)) for index in range(len(entries))]
    if sorted(by_name) != expected:
        raise ValueError('allocation object names differ from the epoch sequence')
    events, generations = [], []
    for name in expected:
        body, generation = store.get(name, by_name[name])
        events.append(body)
        generations.append(generation)
    authenticate(events, observed)
    return events, generations


def _body(events, run_id, attempt, timestamp, rollback_index, security_patch, observed):
    build = str(epoch.RANGE_START+len(events))
    inners = [item['proposal'] for item in events]
    epoch.check_candidate(build, timestamp, rollback_index, security_patch, observed)
    proposal = proposal_for(inners, build, timestamp, rollback_index, security_patch)
    strict_int(proposal['sequence'], 'allocation sequence')
    return {'schema': SCHEMA, 'epoch': epoch.EPOCH, 'sequence': proposal['sequence'], 'build': build,
            'previous': epoch.genesis() if not events else digest(events[-1]), 'proposal': proposal,
            'observed': list(observed), 'runId': run_id, 'runAttempt': attempt,
            'issued': False, 'status': 'reserved'}


def _same(event, timestamp, rollback_index, security_patch, observed):
    proposal = event['proposal']
    return event['observed'] == observed and proposal['timestamp'] == timestamp \
        and proposal['rollbackIndex'] == rollback_index and proposal['securityPatchLevel'] == security_patch


def reserve(store, run_id, attempt, timestamp, rollback_index, security_patch, observed, *, backend_established):
    if backend_established is not True or store is None:
        raise ValueError('allocation backend is not established')
    if type(run_id) is not str or not RUN.fullmatch(run_id) or isinstance(attempt, bool) \
            or type(attempt) is not int or attempt < 1:
        raise ValueError('allocation run identity differs')
    ids = normalize_observed(observed)
    for _ in range(8):
        events, _generations = read_chain(store, ids)
        owned = [item for item in events if item['runId'] == run_id]
        if owned:
            if owned[0]['runAttempt'] == attempt and _same(owned[0], timestamp, rollback_index, security_patch, ids):
                return owned[0]
            raise ValueError('allocation replay differs from the stored reservation')
        build = str(epoch.RANGE_START+len(events))
        epoch.check_candidate(build, timestamp, rollback_index, security_patch, ids)
        body = _body(events, run_id, attempt, timestamp, rollback_index, security_patch, ids)
        try:
            generation = store.create(object_name(build), body)
        except PreconditionFailed:
            continue
        stored, got = store.get(object_name(build), generation)
        if stored != body or got != generation:
            raise ValueError('allocation readback differs')
        read_chain(store, ids)
        return stored
    raise ValueError('allocation conflict did not resolve')


def issue(store, run_id, attempt, observed, *, admission):
    if not isinstance(admission, dict) or admission.get('issueAdmitted') is not True \
            or admission.get('backendEstablished') is not True:
        raise ValueError('allocation issue is not admitted')
    if type(run_id) is not str or not RUN.fullmatch(run_id) or isinstance(attempt, bool) \
            or type(attempt) is not int or attempt < 1:
        raise ValueError('allocation run identity differs')
    ids = normalize_observed(observed)
    events, generations = read_chain(store, ids)
    owned = [(event, generation) for event, generation in zip(events, generations)
             if event['runId'] == run_id and event['runAttempt'] == attempt]
    if len(owned) != 1:
        raise ValueError('allocation issue has no matching reservation')
    event, generation = owned[0]
    strict_int(generation, 'reservation generation')
    epoch.check_candidate(event['build'], event['proposal']['timestamp'], event['proposal']['rollbackIndex'],
                          event['proposal']['securityPatchLevel'], [item for item in ids if item != event['build']])
    body = {'schema': ISSUE_SCHEMA, 'build': event['build'], 'reservation': object_name(event['build']),
            'reservationGeneration': generation, 'reservationSha256': digest(event),
            'runId': run_id, 'runAttempt': attempt, 'issued': True, 'status': 'issued'}
    if set(body) != ISSUE_KEYS or event['issued'] is not False:
        raise ValueError('allocation issue fields differ')
    name = ISSUE_PREFIX+event['build']+'.json'
    try:
        created = store.create(name, body)
    except PreconditionFailed:
        created = None
    listed = dict(store.list_entries(ISSUE_PREFIX))
    stored, stored_gen = store.get(name, listed[name])
    if stored != body or (created is not None and stored_gen != created):
        raise ValueError('allocation issue readback differs')
    return stored


def main(argv=None):
    record = provision()
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in ('reserve', 'issue'):
        print('allocation backend is not established', file=sys.stderr)
        return 4
    print(json.dumps({'backendEstablished': False, 'networkAdmitted': False, 'issueAdmitted': False,
                      'issued': False, 'bucket': record['bucket'], 'serviceAccount': record['serviceAccount']},
                     sort_keys=True))
    return 4


if __name__ == '__main__':
    raise SystemExit(main())
