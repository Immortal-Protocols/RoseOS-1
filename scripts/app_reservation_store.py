#!/usr/bin/env python3
"""The shared Rose Agent version ledger (1F decision D1): annotated tags in one GitHub repository.

Binding (os/release-pipeline/agent/hosted.json reservation.store), exactly these keys:
  kind "github-annotated-tags", repository "Immortal-Protocols/pixel-ota",
  reservationPrefix "refs/tags/app-reservations/dev.pixel.voice/", floorPrefix "refs/tags/app-floors/dev.pixel.voice/".

Reservation: versionCode N is held by the annotated tag <reservationPrefix>N whose message is exactly the canonical
JSON (sorted keys, no spaces) of its roseos.app-version-reservation.v1 record (scripts/app_reservation.py) naming this
store. Creating that ref is the one atomic step: GitHub creates a ref only when none exists (HTTP 422 otherwise), so
exactly one run ever holds N, whichever route asks first. Nothing here moves, replaces, deletes or reuses a
reservation; a failed or other run's reservation stays consumed.

Floor: the annotated tag <floorPrefix><runId>-<runAttempt> holds a roseos.app-version-floor.v1 record: the highest
versionCode a builder route's own ledger (its allocation records, attempt directories and app reservations) had
consumed when that run allocated. It is how a builder-local history reaches a lane that cannot read the builder.
Every versionCode at or below the highest floor counts as consumed, exactly like a reserved one. The Frankel builder
writes its floor through its ledger step (decision D2); until one exists, a hosted allocation refuses.

Reads use git itself (ls-remote and a fetch of the ledger's tags into a private bare repository): complete, with no
API page limit, and anonymous for a public store. Writes use the GitHub API with the pixel-ota publisher App's
installation token (contents: write), minted in the hosted job: POST git/tags (a tag object on the store's HEAD
commit) and then POST git/refs. The token is never printed or passed on a command line; errors name only the method,
endpoint template and status.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
sys.path.insert(0, str(Path(__file__).resolve().parent))
import app_reservation  # noqa: E402

KIND = 'github-annotated-tags'
STORE_KEYS = frozenset({'kind', 'repository', 'reservationPrefix', 'floorPrefix'})
FLOOR_SCHEMA = 'roseos.app-version-floor.v1'
FLOOR_KEYS = frozenset({'schema', 'packageName', 'consumedThrough', 'allocationSha256', 'owner', 'store', 'floorSha256'})
OWNER_KEYS = ('workflowRef', 'runId', 'runAttempt')
TAGGER = {'name': 'RoseOS release ledger', 'email': 'release-ledger@roseos.invalid'}
SHA1 = re.compile(r'[0-9a-f]{40}')
SHA256 = re.compile(r'[0-9a-f]{64}')
GIT_TIMEOUT = 300
READBACK_TRIES, READBACK_DELAY = 5, 2.0  # a just-created ref is normally visible at once; allow brief replication lag


class Refused(Exception):
    """The ledger is unreadable, malformed or holds the requested version already; nothing was changed."""


def canonical(data):
    return app_reservation.canonical(data)


def check_store(store):
    """The exact store binding, or Refused. Both prefixes live under refs/tags/, end with '/', and are disjoint."""
    if not isinstance(store, dict) or set(store) != STORE_KEYS or store['kind'] != KIND:
        raise Refused(f'the reservation store must be exactly a {KIND} binding of {", ".join(sorted(STORE_KEYS))}')
    if not isinstance(store['repository'], str) or not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', store['repository']):
        raise Refused('the reservation store repository must be owner/name')
    prefixes = [store['reservationPrefix'], store['floorPrefix']]
    part = r'[A-Za-z0-9_][A-Za-z0-9_.-]*'  # a git ref component: no leading dot, so never '.' or '..'
    for prefix in prefixes:
        if not isinstance(prefix, str) or not re.fullmatch(rf'refs/tags/{part}(/{part})*/', prefix) or '..' in prefix \
                or '.lock/' in prefix:
            raise Refused('each store prefix must be a refs/tags/ path ending with /')
    if prefixes[0].startswith(prefixes[1]) or prefixes[1].startswith(prefixes[0]):
        raise Refused('the reservation and floor prefixes must be disjoint')
    return store


def owner(env):
    """This workflow run as a ledger owner, or Refused when the run identity is incomplete."""
    found = {key: env.get(name) for key, name in zip(OWNER_KEYS, ('GITHUB_WORKFLOW_REF', 'GITHUB_RUN_ID', 'GITHUB_RUN_ATTEMPT'))}
    if not all(isinstance(v, str) and v for v in found.values()) or not found['runId'].isdigit() \
            or not found['runAttempt'].isdigit():
        raise Refused('this run has no complete workflow ref, run id and attempt')
    return found


def check_owner(value):
    return isinstance(value, dict) and tuple(sorted(value)) == tuple(sorted(OWNER_KEYS)) \
        and all(isinstance(value[k], str) and value[k] for k in OWNER_KEYS) \
        and value['runId'].isdigit() and value['runAttempt'].isdigit()


def check_reservation(record, store):
    """An exact roseos.app-version-reservation.v1 record of this store with its digest, or Refused."""
    if not isinstance(record, dict) or set(record) != app_reservation.KEYS or record.get('schema') != app_reservation.SCHEMA \
            or record.get('reservationSha256') != app_reservation.digest(record):
        raise Refused(f'not an exact {app_reservation.SCHEMA} record with its digest')
    if record['store'] != store:
        raise Refused('the reservation names another store')
    if not isinstance(record['packageName'], str) or type(record['versionCode']) is not int or record['versionCode'] < 1:
        raise Refused('the reservation has no package and positive versionCode')
    if not all(isinstance(record[k], str) and SHA1.fullmatch(record[k]) for k in ('sourceSha', 'candidateSha')) \
            or not isinstance(record['allocationSha256'], str) or not SHA256.fullmatch(record['allocationSha256']) \
            or not check_owner(record['owner']):
        raise Refused('the reservation does not name its source, candidate, allocation digest and owner run')
    return record


def reservation(package, version, source, candidate, allocation_sha256, run_owner, store):
    record = {'schema': app_reservation.SCHEMA, 'packageName': package, 'versionCode': version, 'sourceSha': source,
              'candidateSha': candidate, 'allocationSha256': allocation_sha256, 'owner': dict(run_owner), 'store': store}
    record['reservationSha256'] = app_reservation.digest(record)
    return check_reservation(record, store)


def floor_digest(record):
    return hashlib.sha256(canonical({k: v for k, v in record.items() if k != 'floorSha256'})).hexdigest()


def floor(package, consumed_through, allocation_sha256, run_owner, store):
    record = {'schema': FLOOR_SCHEMA, 'packageName': package, 'consumedThrough': consumed_through,
              'allocationSha256': allocation_sha256, 'owner': dict(run_owner), 'store': store}
    record['floorSha256'] = floor_digest(record)
    return check_floor(record, store)


def check_floor(record, store):
    """An exact roseos.app-version-floor.v1 record of this store with its digest, or Refused."""
    if not isinstance(record, dict) or set(record) != FLOOR_KEYS or record.get('schema') != FLOOR_SCHEMA \
            or record.get('floorSha256') != floor_digest(record):
        raise Refused(f'not an exact {FLOOR_SCHEMA} record with its digest')
    if record['store'] != store:
        raise Refused('the floor names another store')
    if not isinstance(record['packageName'], str) or type(record['consumedThrough']) is not int \
            or record['consumedThrough'] < 1 or not isinstance(record['allocationSha256'], str) \
            or not SHA256.fullmatch(record['allocationSha256']) or not check_owner(record['owner']):
        raise Refused('the floor has no package, positive versionCode, allocation digest and owner run')
    return record


def reservation_ref(store, version):
    return f'{store["reservationPrefix"]}{version}'


def floor_ref(store, run_owner):
    return f'{store["floorPrefix"]}{run_owner["runId"]}-{run_owner["runAttempt"]}'


def message(record):
    """The exact tag message of a ledger record: its canonical JSON on one line."""
    return canonical(record).decode() + '\n'


def parse_message(text, what):
    try:
        return json.loads(text.strip())
    except ValueError:
        raise Refused(f'{what} does not hold a JSON ledger record') from None


def parse(store, tags, package):
    """{'reservations': {versionCode: record}, 'floors': [record]} from {ref: (objecttype, message)}: every ref under
    the two prefixes must be an annotated tag holding an exact record of this store for this package, at its own name.
    Anything else refuses: maxima are never guessed past an unreadable entry."""
    reservations, floors = {}, []
    for ref, (kind, text) in sorted(tags.items()):
        if ref.startswith(store['reservationPrefix']):
            name = ref[len(store['reservationPrefix']):]
            if not re.fullmatch(r'[1-9][0-9]*', name) or kind != 'tag':
                raise Refused(f'unexpected reservation ref {ref[:120]}')
            record = check_reservation(parse_message(text, ref), store)
            if record['versionCode'] != int(name) or record['packageName'] != package:
                raise Refused(f'{ref[:120]} does not hold its own {package} reservation')
            reservations[record['versionCode']] = record
        elif ref.startswith(store['floorPrefix']):
            name = ref[len(store['floorPrefix']):]
            if not re.fullmatch(r'[1-9][0-9]*-[1-9][0-9]*', name) or kind != 'tag':
                raise Refused(f'unexpected floor ref {ref[:120]}')
            record = check_floor(parse_message(text, ref), store)
            if f'{record["owner"]["runId"]}-{record["owner"]["runAttempt"]}' != name or record['packageName'] != package:
                raise Refused(f'{ref[:120]} does not hold its own {package} floor')
            floors.append(record)
        else:
            raise Refused(f'{ref[:120]} is outside the ledger prefixes')
    return {'reservations': reservations, 'floors': floors}


def consumed(ledger):
    """Every versionCode the ledger consumes: each reservation and everything at or below the highest floor."""
    top = max([f['consumedThrough'] for f in ledger['floors']], default=0)
    return set(ledger['reservations']) | set(range(1, top + 1))


def _git(*args, cwd=None, env=None):
    result = subprocess.run(['git', *args], cwd=cwd, env=env, capture_output=True, text=True, timeout=GIT_TIMEOUT)
    if result.returncode:
        raise Refused(f'git {args[0]} of the ledger failed (exit {result.returncode})')
    return result.stdout


class GitReader:
    """Reads the ledger with git: ls-remote of both prefixes, then a fetch of exactly those tags into a private bare
    repository whose tag objects are read locally. url defaults to https://github.com/<repository>."""

    def __init__(self, store, url=None, git=_git):
        self.store, self.git = check_store(store), git
        self.url = url or f'https://github.com/{store["repository"]}'

    def head(self):
        rows = [line.split('\t') for line in self.git('ls-remote', self.url, 'HEAD').splitlines() if line.strip()]
        if len(rows) != 1 or rows[0][1] != 'HEAD' or not SHA1.fullmatch(rows[0][0]):
            raise Refused('the store HEAD commit is unreadable')
        return rows[0][0]

    def tags(self):
        """{ref: (objecttype, message)} for every ref under the two prefixes."""
        prefixes = (self.store['reservationPrefix'], self.store['floorPrefix'])
        listed = {}
        for line in self.git('ls-remote', self.url, *(p + '*' for p in prefixes)).splitlines():
            sha, _, ref = line.partition('\t')
            if not SHA1.fullmatch(sha) or not ref.startswith(prefixes):
                raise Refused('the ledger listing is unreadable')
            if not ref.endswith('^{}'):
                listed[ref] = sha
        if not listed:
            return {}
        with tempfile.TemporaryDirectory(prefix='rose-ledger-') as tmp:
            self.git('init', '--quiet', '--bare', tmp)
            self.git('-C', tmp, 'fetch', '--quiet', '--no-tags', '--depth=1', self.url,
                     *(f'{ref}:{ref}' for ref in sorted(listed)))
            found = {}
            for ref, sha in listed.items():
                kind = self.git('-C', tmp, 'cat-file', '-t', sha).strip()
                body = self.git('-C', tmp, 'cat-file', kind, sha) if kind == 'tag' else ''
                found[ref] = (kind, body.split('\n\n', 1)[1] if '\n\n' in body else '')
            return found

    def read(self, package):
        return parse(self.store, self.tags(), package)


class ApiWriter:
    """Creates ledger tags with the GitHub API through a github_release_publisher.GitHubReleases client (its HTTP
    handling: the token only in the Authorization header, errors without bodies)."""

    def __init__(self, store, client):
        self.store, self.client = check_store(store), client
        self.base = f'{client.api}/repos/{store["repository"]}'

    def _tag(self, ref, record, head):
        status, _, body = self.client._request('POST', f'{self.base}/git/tags', 'create ledger tag object',
            body={'tag': ref[len('refs/tags/'):], 'message': message(record), 'object': head, 'type': 'commit',
                  'tagger': TAGGER}, expect=(201,))
        if not isinstance(body, dict) or not SHA1.fullmatch(str(body.get('sha', ''))):
            raise Refused('the ledger tag object was not created')
        return body['sha']

    def create(self, ref, record, head):
        """Create ref -> a new annotated tag holding record. Returns True when created, False when the ref already
        exists (nothing is changed then: the caller reads what holds it)."""
        sha = self._tag(ref, record, head)
        status, _, _ = self.client._request('POST', f'{self.base}/git/refs', 'create ledger ref',
                                            body={'ref': ref, 'sha': sha}, expect=(201, 422))
        return status == 201


class Ledger:
    """One bound store: read (reader) and atomically extended (writer, when given)."""

    def __init__(self, store, reader, writer=None, sleep=time.sleep):
        self.store, self.reader, self.writer, self.sleep = check_store(store), reader, writer, sleep

    def read(self, package):
        return self.reader.read(package)

    def _write(self, ref, record, holds):
        if self.writer is None:
            raise Refused('this ledger is read-only here')
        created = self.writer.create(ref, record, self.reader.head())
        for attempt in range(READBACK_TRIES):
            held = holds(self.read(record['packageName']))
            if held is not None or attempt == READBACK_TRIES - 1:
                break
            self.sleep(READBACK_DELAY)
        if held != record:
            raise Refused(f'{ref[:120]} is already held by another record' if not created
                          else f'{ref[:120]} does not read back as the record just written')
        return {'ref': ref, 'created': created}

    def reserve(self, record):
        """Hold record's versionCode for its run, atomically: refused when any record already holds it (the same
        record again, as after a lost response, reads back as held and is accepted)."""
        check_reservation(record, self.store)
        version = record['versionCode']
        return self._write(reservation_ref(self.store, version), record, lambda l: l['reservations'].get(version))

    def record_floor(self, record):
        check_floor(record, self.store)
        name = f'{record["owner"]["runId"]}-{record["owner"]["runAttempt"]}'
        return self._write(floor_ref(self.store, record['owner']), record,
                           lambda l: next((f for f in l['floors'] if
                                           f'{f["owner"]["runId"]}-{f["owner"]["runAttempt"]}' == name), None))

    def confirm(self, record):
        """record is exactly the reservation the store holds for its versionCode, or Refused."""
        check_reservation(record, self.store)
        if self.read(record['packageName'])['reservations'].get(record['versionCode']) != record:
            raise Refused(f'the store does not hold this reservation of {record["packageName"]} {record["versionCode"]}')
        return record


def ledger_from(store, token=None, api='https://api.github.com', url=None, allow_http=False):
    """The bound ledger: git reads, and API writes only when a token is given."""
    writer = None
    if token:
        import github_release_publisher as gp  # noqa: PLC0415 - only writers need the HTTP client
        writer = ApiWriter(store, gp.GitHubReleases(store['repository'], token, api, api, allow_http=allow_http, web=api))
    return Ledger(store, GitReader(store, url), writer)


if __name__ == '__main__':
    import argparse
    p = argparse.ArgumentParser(description=__doc__.split('\n', 1)[0])
    p.add_argument('command', choices=['list'])
    p.add_argument('--config', type=Path, default=Path(__file__).resolve().parents[1]/'os/release-pipeline/agent/hosted.json')
    a = p.parse_args()
    try:
        config = json.loads(a.config.read_text())
        bound = (config.get('reservation') or {}).get('store')
        if not bound:
            print(json.dumps({'verdict': 'SETUP_MISSING', 'missing': ['reservation.store']}), file=sys.stderr)
            sys.exit(4)
        found = ledger_from(bound, url=os.environ.get('ROSE_LEDGER_URL')).read(config['appRelease']['packageName'])
        print(json.dumps({'reservations': sorted(found['reservations']),
                          'floor': max([f['consumedThrough'] for f in found['floors']], default=None)}))
    except Refused as error:
        print(json.dumps({'verdict': 'FAIL', 'error': str(error)}), file=sys.stderr)
        sys.exit(1)
