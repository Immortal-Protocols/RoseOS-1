#!/usr/bin/env python3
"""Decision D2: the Frankel builder's Rose Agent versions in the shared version ledger (scripts/app_reservation_store.py).

The builder allocates under its own lock (scripts/release-allocate.py latest, or app with apps=auto), but never holds
the pixel-ota publisher token. So the run's records are written by the hosted os-release.yml job app-ledger, and the
builder waits for them before anything is built or signed:

  fetch-allocation --name ARTIFACT --job JOB --out DIR [--timeout S]
      hosted, with the job's GITHUB_TOKEN (actions: read): wait for this run's allocation artifact (uploaded by the
      builder right after allocating), download it and unpack exactly one allocation.json into DIR. The storage
      redirect is followed without the token. When the builder job JOB has finished without one (it failed before
      allocating), there is nothing to record: found=false, and the job does not hold the queue.
  record --allocation FILE
      hosted, with PIXEL_OTA_TOKEN: write this run's floor, and its reservation when the allocation took a fresh
      dev.pixel.voice versionCode, each created only if absent (app_reservation_store.Ledger).
  wait --allocation FILE [--timeout S]
      builder, anonymous git: wait until the ledger holds exactly this run's records. Another record holding the
      run's fresh version (a lane that reserved it first) refuses at once: the version is never built.
  expected --allocation FILE
      print the records this run must hold.

The records are derived from the allocation alone, so the writer and the waiter agree byte for byte:
- floor (roseos.app-version-floor.v1): consumedThrough of dev.pixel.voice, the highest version the builder's
  observation consumed (its own reservations, attempts and app reservations, every pixel-ota release and the ledger
  itself), owned by this run;
- reservation (roseos.app-version-reservation.v1), only for a fresh version: a latest-beta allocation whose
  dev.pixel.voice changedSinceRelease, or an app-only allocation. A reused version was consumed by an earlier run.
Exit status: 0 done, 1 refused, 2 usage, 4 setup missing. Tokens are never printed or passed on a command line.
"""
import argparse
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
sys.path.insert(0, str(Path(__file__).resolve().parent))
import app_reservation_store as ledger_store  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
HOSTED = ROOT/'os/release-pipeline/agent/hosted.json'
PACKAGE = 'dev.pixel.voice'
POLL = 15  # seconds between checks
ARTIFACT_MAX_BYTES = 1 << 20


class Refused(Exception):
    pass


class SetupMissing(Exception):
    def __init__(self, missing):
        super().__init__(', '.join(missing))
        self.missing = missing


def load(name):
    spec = importlib.util.spec_from_file_location(name.replace('-', '_'), ROOT/'scripts'/f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ALLOC = load('release-allocate')


def store(config=HOSTED):
    bound = (json.loads(Path(config).read_text()).get('reservation') or {}).get('store')
    if not bound:
        raise SetupMissing(['reservation.store (os/release-pipeline/agent/hosted.json, decision D1)'])
    return ledger_store.check_store(bound)


def unchanged(allocation):
    """An app-only allocation that found these sources already published: nothing was allocated or consumed."""
    return allocation.get('verdict') == 'unchanged' and 'schema' not in allocation


def expected(allocation, env, bound):
    """(floor, reservation or None) this run must hold, from its own allocation record."""
    run_owner = ledger_store.owner(env)
    schema = allocation.get('schema')
    if schema == ALLOC.SCHEMA:
        ALLOC.check_allocation(allocation)
        voice = (allocation.get('packages') or {}).get(PACKAGE)
        if not isinstance(voice, dict) or type(voice.get('consumedThrough')) is not int:
            raise Refused(f'the allocation records no {PACKAGE} consumedThrough (allocated before decision D2)')
        through, fresh, version = voice['consumedThrough'], voice['changedSinceRelease'] is True, voice['versionCode']
    elif schema == ALLOC.APP_SCHEMA:
        ALLOC.check_app_allocation({k: v for k, v in allocation.items() if k != 'verdict'})
        if allocation.get('packageName') != PACKAGE or type(allocation.get('consumedThrough')) is not int:
            raise Refused(f'the app allocation records no {PACKAGE} consumedThrough (allocated before decision D2)')
        through, fresh, version = allocation['consumedThrough'], True, allocation['versionCode']
    else:
        raise Refused('not a latest-beta or app-only allocation record')
    if allocation.get('run') != {'id': run_owner['runId'], 'attempt': run_owner['runAttempt']}:
        raise Refused('the allocation is not this run\'s')
    if through < version:
        raise Refused('the allocation consumed less than the version it allocated')
    floor = ledger_store.floor(PACKAGE, through, allocation['allocationSha256'], run_owner, bound)
    reservation = None
    if fresh:
        candidate = allocation.get('candidateSha')
        reservation = ledger_store.reservation(PACKAGE, version, allocation['sourceSha'], candidate,
                                               allocation['allocationSha256'], run_owner, bound)
    return floor, reservation


def held(found, floor, reservation):
    """'held' when the ledger holds exactly this run's records, 'missing' while one is absent, or Refused when another
    record holds what this run needs."""
    name = floor_name(floor)
    mine = next((f for f in found['floors'] if floor_name(f) == name), None)
    if mine is not None and mine != floor:
        raise Refused('the ledger holds another floor for this run')
    if reservation is not None:
        other = found['reservations'].get(reservation['versionCode'])
        if other is not None and other != reservation:
            raise Refused(f'{PACKAGE} {reservation["versionCode"]} is held by another run '
                          f'({other["owner"]["runId"]}); it is never built here')
        if other is None:
            return 'missing'
    return 'held' if mine is not None else 'missing'


def floor_name(record):
    return f'{record["owner"]["runId"]}-{record["owner"]["runAttempt"]}'


def record(allocation, env, ledger):
    if unchanged(allocation):
        return {'unchanged': True}
    floor, reservation = expected(allocation, env, ledger.store)
    written = []
    if reservation is not None:  # the version first: a lane that took it first stops this run before its floor
        written.append(ledger.reserve(reservation))
    written.append(ledger.record_floor(floor))
    return {'floor': floor['consumedThrough'], 'reservation': reservation and reservation['versionCode'],
            'refs': [w['ref'] for w in written], 'created': [w['created'] for w in written]}


def wait(allocation, env, ledger, timeout, clock=time.monotonic, sleep=time.sleep):
    if unchanged(allocation):
        return {'unchanged': True}
    floor, reservation = expected(allocation, env, ledger.store)
    deadline = clock() + timeout
    while True:
        try:
            state = held(ledger.read(PACKAGE), floor, reservation)
        except ledger_store.Refused:
            state = 'unreadable'  # a transient read failure is retried until the deadline
        if state == 'held':
            return {'floor': floor['consumedThrough'], 'reservation': reservation and reservation['versionCode']}
        if clock() >= deadline:
            raise Refused(f'the ledger did not hold this run\'s records within {timeout} s ({state})')
        sleep(POLL)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def get_json(opener, url, headers):
    try:
        with opener.open(urllib.request.Request(url, headers=headers), timeout=60) as response:
            return json.loads(response.read(1 << 20))
    except (urllib.error.URLError, OSError, ValueError):
        return None


def fetch_allocation(name, out, env, timeout, job='builder', opener=None, plain=None, clock=time.monotonic,
                     sleep=time.sleep):
    """This run's allocation artifact, once it exists: listed and fetched with the job token, its storage redirect
    followed without it, and exactly one allocation.json unpacked into out. None when the builder job finished without
    uploading one."""
    token, api = env.get('GITHUB_TOKEN', ''), env.get('GITHUB_API_URL', 'https://api.github.com').rstrip('/')
    repo, run = env.get('GITHUB_REPOSITORY', ''), env.get('GITHUB_RUN_ID', '')
    if not token or not repo or not run.isdigit():
        raise SetupMissing(['GITHUB_TOKEN, GITHUB_REPOSITORY and GITHUB_RUN_ID (the app-ledger job)'])
    opener = opener or urllib.request.build_opener(NoRedirect)
    plain = plain or urllib.request.build_opener()
    headers = {'Authorization': 'Bearer ' + token, 'Accept': 'application/vnd.github+json',
               'X-GitHub-Api-Version': '2022-11-28', 'User-Agent': 'roseos-app-ledger'}
    out = Path(out)
    if out.exists():
        raise Refused(f'{out} already exists; use a new directory')
    listing = f'{api}/repos/{repo}/actions/runs/{run}/artifacts?name={urllib.parse.quote(name)}'
    jobs = f'{api}/repos/{repo}/actions/runs/{run}/jobs?per_page=100'
    deadline = clock() + timeout
    while True:
        body = get_json(opener, listing, headers) or {}
        found = [a for a in body.get('artifacts', []) if a.get('name') == name and not a.get('expired')]
        if len(found) == 1:
            break
        if len(found) > 1:
            raise Refused(f'this run has {len(found)} artifacts named {name}')
        builders = [j for j in (get_json(opener, jobs, headers) or {}).get('jobs', []) if j.get('name') == job]
        if builders and all(j.get('status') == 'completed' for j in builders):
            # It may have uploaded just before finishing: look once more before concluding there is none.
            body = get_json(opener, listing, headers) or {}
            found = [a for a in body.get('artifacts', []) if a.get('name') == name and not a.get('expired')]
            if len(found) == 1:
                break
            return None
        if clock() >= deadline:
            raise Refused(f'no {name} artifact within {timeout} s')
        sleep(POLL)
    request = urllib.request.Request(found[0]['archive_download_url'], headers=headers)
    try:
        opener.open(request, timeout=60)
        raise Refused('the artifact download did not redirect to storage')
    except urllib.error.HTTPError as error:
        if error.code not in (301, 302, 303, 307, 308) or not error.headers.get('Location'):
            raise Refused(f'GET artifact archive returned HTTP {error.code}') from None
        location = error.headers['Location']
    if urllib.parse.urlsplit(location).scheme != 'https':
        raise Refused('the artifact storage URL is not https')
    with plain.open(urllib.request.Request(location, headers={'User-Agent': 'roseos-app-ledger'}), timeout=120) as response:
        data = response.read(ARTIFACT_MAX_BYTES + 1)
    if len(data) > ARTIFACT_MAX_BYTES:
        raise Refused('the allocation artifact is larger than expected')
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        if archive.namelist() != ['allocation.json']:
            raise Refused('the allocation artifact does not hold exactly allocation.json')
        body = archive.read('allocation.json')
    out.mkdir(parents=True)
    (out/'allocation.json').write_bytes(body)
    return {'artifact': found[0].get('id'), 'allocation': str(out/'allocation.json')}


def main(argv=None, env=os.environ):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--config', type=Path, default=HOSTED)
    sub = p.add_subparsers(dest='command', required=True)
    f = sub.add_parser('fetch-allocation')
    f.add_argument('--name', required=True)
    f.add_argument('--job', required=True, help='the builder job that uploads the artifact')
    f.add_argument('--out', type=Path, required=True)
    f.add_argument('--timeout', type=int, default=1500)
    for name in ('record', 'wait', 'expected'):
        s = sub.add_parser(name)
        s.add_argument('--allocation', type=Path, required=True)
        if name == 'wait':
            s.add_argument('--timeout', type=int, default=900)
    a = p.parse_args(argv)
    try:
        if a.command == 'fetch-allocation':
            result = fetch_allocation(a.name, a.out, env, a.timeout, job=a.job) or {'allocation': None}
            if env.get('GITHUB_OUTPUT'):
                with open(env['GITHUB_OUTPUT'], 'a') as f:
                    f.write(f'found={"true" if result["allocation"] else "false"}\n')
        else:
            bound = store(a.config)
            allocation = json.loads(a.allocation.read_text())
            if a.command == 'expected':
                floor, reservation = expected(allocation, env, bound)
                result = {'floor': floor, 'reservation': reservation}
            elif a.command == 'record':
                token = env.get('PIXEL_OTA_TOKEN', '')
                if not token:
                    raise SetupMissing(['PIXEL_OTA_TOKEN (minted by the app-ledger job)'])
                result = record(allocation, env, ledger_store.ledger_from(bound, token=token))
            else:
                result = wait(allocation, env, ledger_store.ledger_from(bound), a.timeout)
    except SetupMissing as error:
        print(json.dumps({'verdict': 'SETUP_MISSING', 'missing': error.missing}), file=sys.stderr)
        return 4
    except (Refused, ledger_store.Refused, ALLOC.Refused) as error:
        print(json.dumps({'verdict': 'FAIL', 'error': str(error)}), file=sys.stderr)
        return 1
    except (OSError, ValueError, KeyError, zipfile.BadZipFile) as error:
        print(json.dumps({'verdict': 'FAIL', 'error': error.__class__.__name__}), file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    sys.exit(main())
