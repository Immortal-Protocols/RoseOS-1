#!/usr/bin/env python3
"""Fresh candidate allocation for 'Build & publish latest Pixel 10' (OCT30-INFRA-S014).

A dispatch captures its source commit once (GITHUB_SHA). From that commit, the committed release records and an
observation of every identity already consumed (pixel-ota releases including drafts, builder attempt directories and
earlier allocation reservations), this derives one fresh candidate:

- build number YYYYMMDDNN (UTC date of the dispatch clock, NN above every known build of that day, and above every
  known build overall) and BUILD_DATETIME above every known timestamp;
- per OS app: the previous release's version when none of the package's source paths changed since that release
  (os/latest-beta/allocation.json), else a fresh versionCode above every version ever consumed;
- the exact manifest delta those versions need, as content hashes, and a deterministic local commit (source + delta,
  fixed identity and dates) so the compiled source really carries the allocated versions. Nothing is pushed.

It never guesses: missing or incomplete state, an unknown previous release source, a version or build sequence that
cannot move forward, or a malformed manifest refuses with a fixed category.

  allocate --source SHA --observation F --now EPOCH --run-id N --run-attempt N --out F
  derive --allocation F            print the deterministic candidate commit (created in this repository's objects)
  notes --allocation F             print truthful release notes for the captured source range
  reserve --dir D --allocation F --candidate SHA
                                   record the allocation (O_EXCL) before any identity is consumed
  latest --out F                   builder, normal button: under the shared lock, observe, allocate from GITHUB_SHA,
                                   derive, reserve, and export the candidate to GITHUB_ENV and GITHUB_OUTPUT
  app --out F                      builder, app-only with apps=auto (AGENT-BETA-AUTO): under the shared lock, observe,
                                   then skip when the newest published app release has these Rose Agent sources, or
                                   allocate one versionCode above every consumed one (drafts included), derive the
                                   version-only candidate, reserve it in rose-actions/app-allocations and export
                                   CANDIDATE_SHA, APPS and APP_SKIP

Exit status: 0 done, 1 refused (category on stderr), 2 invalid input.
"""
import argparse
import contextlib
import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
ALLOCATION_CONFIG = 'os/latest-beta/allocation.json'
PIPELINE_CONFIG = 'os/release-pipeline/frankel.json'
SCHEMA = 'roseos.latest-allocation.v1'
OBSERVATION_SCHEMA = 'roseos.release-observation.v1'
APP_SCHEMA = 'roseos.app-allocation.v1'
APP_RESERVATIONS = 'app-allocations'
APP_PACKAGE = 'dev.pixel.voice'
SHA = re.compile(r'[0-9a-f]{40}')
BUILD = re.compile(r'(\d{8})(\d{2})')
VERSION_CODE = re.compile(r'android:versionCode="(\d+)"')
VERSION_NAME = re.compile(r'android:versionName="([^"]*)"')
IDENTITY = {'GIT_AUTHOR_NAME': 'RoseOS release allocator', 'GIT_AUTHOR_EMAIL': 'release-allocator@roseos.invalid',
    'GIT_COMMITTER_NAME': 'RoseOS release allocator', 'GIT_COMMITTER_EMAIL': 'release-allocator@roseos.invalid'}


class Refused(Exception):
    pass


def git(repo, *args, env=None, data=None):
    result = subprocess.run(['git', '-C', str(repo), *args], capture_output=True, input=data, env=env)
    if result.returncode != 0:
        raise Refused(f'git_failed: git {args[0]} exited {result.returncode}')
    return result.stdout


def show(repo, sha, path):
    try:
        return git(repo, 'show', f'{sha}:{path}').decode()
    except Refused:
        raise Refused(f'source_unreadable: {path} at {sha[:12]}') from None


def canonical(data):
    return json.dumps(data, sort_keys=True, separators=(',', ':')).encode()


def manifest_version(text, path):
    head = text[:text.find('>') + 1] if '<manifest' in text else ''
    codes, names = VERSION_CODE.findall(head), VERSION_NAME.findall(head)
    if len(codes) != 1 or len(names) != 1 or len(VERSION_CODE.findall(text)) != 1:
        raise Refused(f'manifest_malformed: {path} must declare exactly one versionCode and versionName on <manifest>')
    return int(codes[0]), names[0]


def set_manifest_version(text, code, name):
    text = VERSION_CODE.sub(f'android:versionCode="{code}"', text, count=1)
    return VERSION_NAME.sub(lambda m: f'android:versionName="{name}"', text, count=1)


# ---------------------------------------------------------------- observation of consumed identities

def check_observation(observation):
    if not isinstance(observation, dict) or observation.get('schema') != OBSERVATION_SCHEMA:
        raise Refused('state_unavailable: no release observation')
    missing = [part for part in ('releases', 'attempts', 'reservations')
        if not isinstance(observation.get(part), dict) or observation[part].get('complete') is not True
        or not isinstance(observation[part].get('items'), list)]
    if missing:
        raise Refused('state_unavailable: incomplete ' + ', '.join(missing) + ' (maxima are never guessed)')
    if observation['releases'].get('includesDrafts') is not True:
        raise Refused('state_unavailable: the release listing does not include drafts')
    apps = observation.get('appReservations')
    if apps is not None and (not isinstance(apps, dict) or apps.get('complete') is not True or not isinstance(apps.get('items'), list)):
        raise Refused('state_unavailable: incomplete appReservations (maxima are never guessed)')


def known_identities(observation, pipeline, tag_pattern):
    """Every build number and BUILD_DATETIME already consumed, from committed records and the observation."""
    builds, stamps = set(), set()
    baselines = pipeline.get('versionBaselines') or {}
    if (baselines.get('installedEvidence') or {}).get('build'):
        builds.add(baselines['installedEvidence']['build'])
    for row in baselines.get('retained', []):
        builds.add(str(row['build']))
        stamps.add(int(row['buildTimestamp']))
    for row in (pipeline.get('appRelease') or {}).get('reserved', []):
        if row.get('osBuild'):
            builds.add(str(row['osBuild']))
        if row.get('buildTimestamp'):
            stamps.add(int(row['buildTimestamp']))
    prefix, suffix = tag_pattern.split('{build}')
    for release in observation['releases']['items']:
        tag = str(release.get('tag', ''))
        if tag.startswith(prefix) and tag.endswith(suffix):
            builds.add(tag[len(prefix):len(tag) - len(suffix)])
    for attempt in observation['attempts']['items']:
        # -attemptN-<run> is the earlier hand-dispatched naming (frankel-2026102802-attempt1-40634711221); its build
        # stays consumed like any other attempt.
        match = re.fullmatch(re.escape(prefix) + r'(\d{10})(-resume-\d+|-attempt\d+-\d+)?', str(attempt))
        if not match:
            raise Refused(f'state_unavailable: unrecognised attempt directory {str(attempt)[:60]!r}')
        builds.add(match[1])
    for row in observation['reservations']['items']:
        builds.add(str(row.get('build')))
        stamps.add(int(row.get('buildTimestamp', 0)))
    bad = sorted(b for b in builds if not BUILD.fullmatch(b))
    if bad:
        raise Refused(f'state_unavailable: malformed build identity {bad[0][:20]!r}')
    return builds, stamps


def consumed_versions(package, pipeline, observation):
    versions = set()
    baselines = pipeline.get('versionBaselines') or {}
    for row in baselines.get('retained', []):
        if package in row.get('mustExceed', {}):
            versions.add(int(row['mustExceed'][package]))
    release = pipeline.get('appRelease') or {}
    if release.get('packageName') == package:
        versions.update(int(r['versionCode']) for r in [release.get('published') or {}, *release.get('built', []),
            *release.get('reserved', [])] if r.get('versionCode'))
        if release.get('mustExceed'):
            versions.add(int(release['mustExceed']))
    for row in observation['reservations']['items']:
        versions.update(int(v) for p, v in row.get('apps', []) if p == package)
    if release.get('packageName') == package:
        # AGENT-BETA-AUTO: builder app-only reservations and every app tag listed (drafts included) are consumed too.
        versions.update(int(row['versionCode']) for row in (observation.get('appReservations') or {}).get('items', [])
            if row.get('packageName') == package)
        versions.update(app_tag_versions(observation, pipeline))
    return versions


def app_tag_versions(observation, pipeline, published_only=False):
    """versionCode of every listed app release tag (rose-agent-{versionCode}); published_only keeps published prereleases."""
    prefix, _, suffix = (pipeline.get('appRelease') or {}).get('publication', {}).get('tag', '').partition('{versionCode}')
    found = set()
    for release in observation['releases']['items']:
        tag = str(release.get('tag', ''))
        middle = tag[len(prefix):len(tag) - len(suffix)] if prefix and tag.startswith(prefix) and tag.endswith(suffix) else ''
        if middle.isdigit() and (not published_only or (release.get('draft') is False and release.get('prerelease') is True)):
            found.add(int(middle))
    return found


def previous_release(observation, pipeline, tag_pattern):
    """The current Beta Latest: the newest published, non-draft, non-prerelease release, with its exact source."""
    prefix, suffix = tag_pattern.split('{build}')
    published = []
    for release in observation['releases']['items']:
        tag = str(release.get('tag', ''))
        if release.get('draft') is False and release.get('prerelease') is False and tag.startswith(prefix) and tag.endswith(suffix):
            published.append(tag[len(prefix):len(tag) - len(suffix)])
    if not published:
        raise Refused('previous_release_unknown: no published Beta release is observed')
    build = max(published)
    for row in observation['reservations']['items']:
        if str(row.get('build')) == build and row.get('schema') == SCHEMA:
            return {'build': build, 'sourceSha': row['sourceSha'], 'candidateSha': row.get('candidateSha'),
                'apps': {p: int(v) for p, v in row['apps']}}
    for row in (pipeline.get('versionBaselines') or {}).get('retained', []):
        if str(row['build']) == build and SHA.fullmatch(str(row.get('source'))):
            return {'build': build, 'sourceSha': row['source'], 'candidateSha': row['source'], 'apps': None}
    raise Refused(f'previous_release_unknown: the exact source of published {build} is not recorded')


# ---------------------------------------------------------------- allocation

def next_build(builds, now, limit):
    today = now.strftime('%Y%m%d')
    newest = max(builds) if builds else None
    sequence = max([int(b[8:]) for b in builds if b[:8] == today] or [0]) + 1
    if newest and newest[:8] > today:
        raise Refused(f'clock_behind: known build {newest} is dated after the dispatch day {today}')
    if sequence > limit:
        raise Refused(f'build_sequence_exhausted: {today} already has build {today}{limit:02d}')
    return f'{today}{sequence:02d}'


def allocate(source, observation, now_epoch, run_id, run_attempt, repo=ROOT, recipe=None):
    if not SHA.fullmatch(source or ''):
        raise Refused('invalid: --source must be a full commit SHA')
    check_observation(observation)
    cfg = json.loads(show(repo, source, ALLOCATION_CONFIG))
    pipeline = json.loads(show(repo, source, PIPELINE_CONFIG))
    if git(repo, 'cat-file', '-t', source).strip() != b'commit':
        raise Refused('source_unreadable: the captured source is not a commit')
    builds, stamps = known_identities(observation, pipeline, cfg['tagPattern'])
    now = datetime.datetime.fromtimestamp(int(now_epoch), datetime.timezone.utc)
    build = next_build(builds, now, int(cfg['buildSequenceMax']))
    timestamp = max([int(now_epoch)] + [s + 1 for s in stamps])
    previous = previous_release(observation, pipeline, cfg['tagPattern'])
    try:
        git(repo, 'cat-file', '-e', previous['sourceSha'] + '^{commit}')
    except Refused:
        raise Refused(f'previous_release_unknown: source {previous["sourceSha"][:12]} of {previous["build"]} is not in this checkout') from None
    apps, packages, delta = [], {}, []
    for package in cfg['packages']:
        name, manifest = package['packageName'], package['manifest']
        if not any(manifest == root or manifest.startswith(root.rstrip('/') + '/') for root in package['sources']):
            raise Refused(f'config_invalid: {manifest} is outside the sources of {name}')
        text = show(repo, source, manifest)
        code, version_name = manifest_version(text, manifest)
        prev_code = (previous['apps'] or {}).get(name)
        if prev_code is None:
            prev_code, _ = manifest_version(show(repo, previous['sourceSha'], manifest), manifest)
        changed = bool(git(repo, 'diff', '--name-only', previous['sourceSha'], source, '--', *package['sources']).strip())
        if changed:
            floor = consumed_versions(name, pipeline, observation) | {code, prev_code}
            new_code = max(floor) + 1
        else:
            new_code = prev_code
            if new_code < code:
                raise Refused(f'version_regression: {name} source declares {code} above the unchanged previous {prev_code}')
        new_name = version_name
        if new_code != code and package.get('versionNamePattern'):
            new_name = package['versionNamePattern'].format(code=new_code, build=build)
        elif new_code != code and version_name.startswith(f'{code}-'):
            new_name = f'{new_code}-' + version_name[len(f'{code}-'):]
        packages[name] = {'changedSinceRelease': changed, 'sourceVersionCode': code, 'previousVersionCode': prev_code,
            'versionCode': new_code, 'versionName': new_name}
        apps.append([name, new_code])
        if (new_code, new_name) != (code, version_name):
            after = set_manifest_version(text, new_code, new_name)
            delta.append({'path': manifest, 'beforeSha256': hashlib.sha256(text.encode()).hexdigest(),
                'afterSha256': hashlib.sha256(after.encode()).hexdigest(), 'versionCode': [code, new_code],
                'versionName': [version_name, new_name]})
    allocation = {'schema': SCHEMA, 'device': cfg['device'], 'channel': cfg['channel'], 'sourceSha': source,
        'recipeSha': recipe or source, 'run': {'id': str(run_id), 'attempt': str(run_attempt)}, 'clock': int(now_epoch),
        'build': build, 'buildTimestamp': timestamp, 'tag': cfg['tagPattern'].format(build=build),
        'previous': {k: previous[k] for k in ('build', 'sourceSha')}, 'apps': apps, 'packages': packages, 'delta': delta,
        'deltaSha256': hashlib.sha256(canonical(delta)).hexdigest(),
        'observationSha256': hashlib.sha256(canonical(observation)).hexdigest()}
    allocation['allocationSha256'] = hashlib.sha256(canonical(allocation)).hexdigest()
    return allocation


def check_allocation(allocation):
    body = {k: v for k, v in allocation.items() if k not in ('allocationSha256', 'candidateSha')}
    if allocation.get('schema') != SCHEMA or allocation.get('allocationSha256') != hashlib.sha256(canonical(body)).hexdigest():
        raise Refused('allocation_invalid: the allocation record does not match its hash')
    return allocation


# ---------------------------------------------------------------- deterministic candidate commit

def notes_path(allocation, repo=ROOT):
    """Where the signing stage reads this build's update notes: the source pipeline's host.sign.updateNotes template."""
    pipeline = json.loads(show(repo, allocation['sourceSha'], PIPELINE_CONFIG))
    template = pipeline.get('host', {}).get('sign', {}).get('updateNotes')
    if not isinstance(template, str):
        raise Refused('config_invalid: the normal button needs a host.sign.updateNotes path template')
    path = template.format(device=allocation['device'], build=allocation['build'])
    if not re.fullmatch(r'os/release-notes/[\w.-]+\.md', path):
        raise Refused(f'config_invalid: {path} is not an os/release-notes/*.md path')
    return path


def derive(allocation, repo=ROOT):
    """source + exactly the allocation's manifest delta + this build's generated notes (notes(), at notes_path()), as a
    commit with a fixed identity and dates: the same allocation always yields the same SHA. The signing stage reads the
    notes from the candidate, so every candidate, unchanged main included, is a derived commit. Created only in local
    objects; no ref moves and nothing is pushed."""
    check_allocation(allocation)
    source = allocation['sourceSha']
    path = notes_path(allocation, repo)
    if git(repo, 'ls-tree', source, '--', path).strip():
        raise Refused(f'notes_exist: the source already has {path}; a fresh build never reuses committed notes')
    with tempfile.TemporaryDirectory(prefix='rose-alloc-') as tmp:
        env = dict(os.environ, GIT_INDEX_FILE=str(Path(tmp)/'index'), **IDENTITY,
            GIT_AUTHOR_DATE=f'{allocation["buildTimestamp"]} +0000', GIT_COMMITTER_DATE=f'{allocation["buildTimestamp"]} +0000')
        git(repo, 'read-tree', source, env=env)
        for edit in allocation['delta']:
            text = show(repo, source, edit['path'])
            if hashlib.sha256(text.encode()).hexdigest() != edit['beforeSha256']:
                raise Refused(f'delta_mismatch: {edit["path"]} at the source is not the allocated preimage')
            code, name = edit['versionCode'][1], edit['versionName'][1]
            after = set_manifest_version(text, code, name).encode()
            if hashlib.sha256(after).hexdigest() != edit['afterSha256']:
                raise Refused(f'delta_mismatch: {edit["path"]} postimage differs from the allocation')
            blob = git(repo, 'hash-object', '-w', '--stdin', data=after, env=env).decode().strip()
            mode = git(repo, 'ls-tree', source, '--', edit['path']).decode().split()[0]
            git(repo, 'update-index', '--cacheinfo', f'{mode},{blob},{edit["path"]}', env=env)
        text = notes(allocation, repo).encode()
        blob = git(repo, 'hash-object', '-w', '--stdin', data=text, env=env).decode().strip()
        git(repo, 'update-index', '--add', '--cacheinfo', f'100644,{blob},{path}', env=env)
        tree = git(repo, 'write-tree', env=env).decode().strip()
        message = (f'Allocate {allocation["tag"]} (BUILD_DATETIME {allocation["buildTimestamp"]})\n\n'
            f'Source: {source}\nAllocation: {allocation["allocationSha256"]}\nDelta: {allocation["deltaSha256"]}\n'
            f'Notes: {path} {hashlib.sha256(text).hexdigest()}\n'
            'Version-only candidate delta and generated notes derived by scripts/release-allocate.py; not pushed.\n')
        return git(repo, 'commit-tree', tree, '-p', source, data=message.encode(), env=env).decode().strip()


def check_candidate(allocation, candidate, repo=ROOT):
    """The candidate is exactly source + delta + notes: its only parent is the source and only those paths differ."""
    parents = git(repo, 'rev-list', '--parents', '-n', '1', candidate).decode().split()[1:]
    expected = derive(allocation, repo)
    if candidate != expected or parents != [allocation['sourceSha']]:
        raise Refused('candidate_mismatch: the candidate is not the allocation\'s deterministic commit')
    changed = git(repo, 'diff', '--name-only', allocation['sourceSha'], candidate).decode().split()
    if sorted(changed) != sorted([e['path'] for e in allocation['delta']] + [notes_path(allocation, repo)]):
        raise Refused('candidate_mismatch: the candidate changes more than the version delta and its notes')
    return expected


# ---------------------------------------------------------------- reservation and notes

def reserve(directory, allocation, candidate):
    """Record the allocation before any identity is used. O_EXCL: a concurrent or repeated allocation of the same build
    refuses instead of sharing it; every reservation becomes consumed state for later allocations."""
    check_allocation(allocation)
    directory = Path(directory)
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    record = dict(allocation, candidateSha=candidate)
    try:
        fd = os.open(directory/f'{allocation["build"]}.json', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    except FileExistsError:
        raise Refused(f'reservation_exists: build {allocation["build"]} is already reserved') from None
    with os.fdopen(fd, 'w') as f:
        json.dump(record, f, indent=2, sort_keys=True)
    return record


def notes(allocation, repo=ROOT, limit=200):
    check_allocation(allocation)
    previous, source = allocation['previous'], allocation['sourceSha']
    # Full hashes cut to 12 and the allocation's own app order: the text (committed into the candidate by derive) must
    # not depend on the checkout's object count or on JSON key order, so builder and publisher derive the same bytes.
    log = [line[:12] + line[40:] for line in git(repo, 'log', '--no-merges', f'--max-count={limit + 1}', '--format=%H %s',
        f'{previous["sourceSha"]}..{source}').decode().splitlines()]
    lines = [f'RoseOS Pixel 10 (frankel) Beta build {allocation["build"]}', '',
        f'Source: RoseOS-1 {source} (captured at dispatch; later main changes are not included).',
        f'Changes since the previous Beta {previous["build"]} ({previous["sourceSha"][:12]}):', '']
    lines += [f'- {line}' for line in log[:limit]] or ['- no source commits']
    if len(log) > limit:
        lines.append(f'- ... more commits; see the source range {previous["sourceSha"][:12]}..{source[:12]}')
    lines += ['', 'Bundled apps:']
    for name, _ in allocation['apps']:
        info = allocation['packages'][name]
        state = 'changed, new version' if info['changedSinceRelease'] else 'unchanged since the previous Beta'
        lines.append(f'- {name} {info["versionCode"]} ({info["versionName"]}): {state}')
    lines += ['', f'Version-only candidate delta {allocation["deltaSha256"][:16]} over the source (allocation '
        f'{allocation["allocationSha256"][:16]}, BUILD_DATETIME {allocation["buildTimestamp"]}).',
        'Evidence limits: built, signed and verified by the release workflow (image, signature, version and download '
        'checks). No phone runtime acceptance is claimed by this release.']
    return '\n'.join(lines) + '\n'


# ---------------------------------------------------------------- builder observation and the normal button

DRAFTS_BASIS = ('pixel-ota drafts of this device are only created by this workflow\'s publisher from a builder attempt '
    'directory of the same build, and earlier manual drafts are committed retained baselines; the publisher re-lists '
    'every release, drafts included, with its own token and refuses an existing tag before any upload')


def list_releases(repository, api, fetch=None, pages=10):
    """Every release of the public repository, anonymously, as tag/draft/prerelease; a list longer than scanned refuses."""
    fetch = fetch or _fetch_json
    items = []
    for page in range(1, pages + 1):
        batch = fetch(f'{api.rstrip("/")}/repos/{repository}/releases?per_page=100&page={page}')
        if not isinstance(batch, list):
            raise Refused('state_unavailable: the release listing is unreadable')
        items += [{'tag': r.get('tag_name'), 'draft': r.get('draft'), 'prerelease': r.get('prerelease')} for r in batch]
        if len(batch) < 100:
            return items
    raise Refused('state_unavailable: the release listing is longer than scanned')


def _fetch_json(url):
    request = urllib.request.Request(url, headers={'Accept': 'application/vnd.github+json', 'User-Agent': 'roseos-release-allocate'})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.loads(response.read(32 << 20))
    except Exception as error:  # noqa: BLE001 - any failure is unavailable state, never a guess
        raise Refused(f'state_unavailable: release listing failed ({type(error).__name__})') from None


def observe(home, device, repository, api, fetch=None):
    """Consumed identities on this builder (attempt directories, earlier reservations) plus the public release list."""
    base = Path(home)/'rose-actions'
    if not base.is_dir():
        raise Refused('state_unavailable: the builder attempt storage is missing')
    attempts = sorted(entry.name for entry in base.iterdir() if entry.name.startswith(f'{device}-'))
    reservations = []
    directory = base/'allocations'
    if directory.exists():
        if directory.is_symlink() or not directory.is_dir():
            raise Refused('state_unavailable: the reservation directory is not a plain directory')
        for path in sorted(directory.iterdir()):
            try:
                fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
                with os.fdopen(fd) as f:
                    reservations.append(check_allocation(json.load(f)) if path.suffix == '.json' else None)
            except (OSError, ValueError, Refused):
                raise Refused(f'state_unavailable: unreadable reservation {path.name[:40]}') from None
        if None in reservations:
            raise Refused('state_unavailable: unexpected file in the reservation directory')
    return {'schema': OBSERVATION_SCHEMA,
        'releases': {'complete': True, 'includesDrafts': True, 'draftsBasis': DRAFTS_BASIS,
                     'items': list_releases(repository, api, fetch)},
        'attempts': {'complete': True, 'items': attempts},
        'reservations': {'complete': True, 'items': reservations},
        'appReservations': {'complete': True, 'items': read_app_reservations(base/APP_RESERVATIONS)}}


def read_app_reservations(directory):
    directory, records = Path(directory), []
    if not directory.exists():
        return records
    if directory.is_symlink() or not directory.is_dir():
        raise Refused('state_unavailable: the app reservation directory is not a plain directory')
    for path in sorted(directory.iterdir()):
        try:
            if path.suffix != '.json':
                raise ValueError
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(fd) as f:
                records.append(check_app_allocation(json.load(f)))
        except (OSError, ValueError, Refused):
            raise Refused(f'state_unavailable: unreadable app reservation {path.name[:40]}') from None
    return records


@contextlib.contextmanager
def shared_lock(path):
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o644)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Refused('lock_held: the shared builder lock is held by another job') from None
        yield
    finally:
        os.close(fd)


def latest(out, env, repo=ROOT, fetch=None, clock=time.time, lock=shared_lock):
    """The normal button on the builder: one fresh candidate from the dispatch commit, reserved before it is used."""
    source = env.get('GITHUB_SHA', '')
    pipeline = json.loads(show(repo, source, PIPELINE_CONFIG)) if SHA.fullmatch(source) else None
    if pipeline is None:
        raise Refused('invalid: GITHUB_SHA is not a commit')
    cfg = json.loads(show(repo, source, ALLOCATION_CONFIG))
    if env.get('ROSE_DEVICE') != cfg['device']:
        raise Refused(f'invalid: the normal button builds {cfg["device"]} only')
    with lock(pipeline['host']['lockPath']):
        observation = observe(env['HOME'], cfg['device'], pipeline['publication']['repository'],
            pipeline['publication']['api'], fetch)
        allocation = allocate(source, observation, int(clock()), env.get('GITHUB_RUN_ID', ''), env.get('GITHUB_RUN_ATTEMPT', ''),
            repo=repo)
        candidate = derive(allocation, repo)
        record = reserve(Path(env['HOME'])/'rose-actions/allocations', allocation, candidate)
    fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    with os.fdopen(fd, 'w') as f:
        json.dump(record, f, indent=2, sort_keys=True)
    exported = {'CANDIDATE_SHA': candidate, 'BUILD_NUMBER': allocation['build'], 'BUILD_TIMESTAMP': str(allocation['buildTimestamp']),
        'APPS': json.dumps(allocation['apps'], separators=(',', ':')), 'CHANNEL': allocation['channel']}
    for name, target in (('GITHUB_ENV', exported), ('GITHUB_OUTPUT', {k.lower(): v for k, v in exported.items()})):
        if env.get(name):
            with open(env[name], 'a') as f:
                f.writelines(f'{k}={v}\n' for k, v in target.items())
    return record, exported


# ---------------------------------------------------------------- AGENT-BETA-AUTO: app-only allocation

def check_app_allocation(allocation):
    body = {k: v for k, v in allocation.items() if k not in ('allocationSha256', 'candidateSha')}
    if allocation.get('schema') != APP_SCHEMA or allocation.get('allocationSha256') != hashlib.sha256(canonical(body)).hexdigest():
        raise Refused('allocation_invalid: the app allocation record does not match its hash')
    return allocation


def allocate_app(source, observation, now_epoch, run_id, run_attempt, repo=ROOT, floor=0, name_suffix='auto-beta'):
    """One fresh Rose Agent version for the app-only route, or 'unchanged' when the newest published app release was
    built from these same Rose Agent sources (already published: nothing to build). A fresh versionCode exceeds every
    consumed one: retained OS baselines, appRelease published/built/reserved, latest-beta and app-only reservations on
    this builder and every listed rose-agent tag, drafts included. Nothing is assumed about the next number.
    The hosted route (scripts/agent-hosted.py) passes the shared ledger's floor, the highest version a builder's own
    ledger had consumed, and its own versionName suffix; the builder route keeps 0 and auto-beta."""
    if not SHA.fullmatch(source or ''):
        raise Refused('invalid: --source must be a full commit SHA')
    check_observation(observation)
    if observation.get('appReservations') is None:
        raise Refused('state_unavailable: no app reservations observed (maxima are never guessed)')
    cfg = json.loads(show(repo, source, ALLOCATION_CONFIG))
    pipeline = json.loads(show(repo, source, PIPELINE_CONFIG))
    package = next((p for p in cfg['packages'] if p['packageName'] == APP_PACKAGE), None)
    if package is None or (pipeline.get('appRelease') or {}).get('packageName') != APP_PACKAGE:
        raise Refused(f'config_invalid: {APP_PACKAGE} is not an allocated app')
    text = show(repo, source, package['manifest'])
    code, version_name = manifest_version(text, package['manifest'])
    published = app_tag_versions(observation, pipeline, published_only=True)
    newest = max(published) if published else None
    record = next((r for r in observation['appReservations']['items'] if r['versionCode'] == newest), None)
    previous = {'versionCode': newest, 'sourceSha': record['sourceSha'] if record else None}
    if record and not git(repo, 'diff', '--name-only', record['sourceSha'], source, '--', *package['sources']).strip():
        return {'verdict': 'unchanged', 'sourceSha': source, 'previous': previous,
            'reason': f'rose-agent-{newest} is already published from the same Rose Agent sources ({record["sourceSha"][:12]})'}
    if type(floor) is not int or floor < 0 or not re.fullmatch(r'[a-z][a-z0-9-]{0,30}', name_suffix):
        raise Refused('invalid: the ledger floor and versionName suffix')
    new_code = max(consumed_versions(APP_PACKAGE, pipeline, observation) | {code, floor}) + 1
    new_name = f'{new_code}-{name_suffix}'
    after = set_manifest_version(text, new_code, new_name)
    delta = [{'path': package['manifest'], 'beforeSha256': hashlib.sha256(text.encode()).hexdigest(),
        'afterSha256': hashlib.sha256(after.encode()).hexdigest(), 'versionCode': [code, new_code],
        'versionName': [version_name, new_name]}]
    allocation = {'schema': APP_SCHEMA, 'packageName': APP_PACKAGE, 'sourceSha': source,
        'run': {'id': str(run_id), 'attempt': str(run_attempt)}, 'clock': int(now_epoch), 'versionCode': new_code,
        'versionName': new_name, 'previous': previous, 'delta': delta,
        'observationSha256': hashlib.sha256(canonical(observation)).hexdigest()}
    allocation['allocationSha256'] = hashlib.sha256(canonical(allocation)).hexdigest()
    return dict(allocation, verdict='allocated')


def derive_app(allocation, repo=ROOT):
    """source + exactly the manifest version delta, as a fixed-identity commit (no notes: an app release is not an OS
    build). Deterministic; created only in local objects and never pushed."""
    allocation = check_app_allocation({k: v for k, v in allocation.items() if k != 'verdict'})
    source, (edit,) = allocation['sourceSha'], allocation['delta']
    text = show(repo, source, edit['path'])
    after = set_manifest_version(text, edit['versionCode'][1], edit['versionName'][1]).encode()
    if hashlib.sha256(text.encode()).hexdigest() != edit['beforeSha256'] or hashlib.sha256(after).hexdigest() != edit['afterSha256']:
        raise Refused(f'delta_mismatch: {edit["path"]} differs from the app allocation')
    with tempfile.TemporaryDirectory(prefix='rose-app-alloc-') as tmp:
        env = dict(os.environ, GIT_INDEX_FILE=str(Path(tmp)/'index'), **IDENTITY,
            GIT_AUTHOR_DATE=f'{allocation["clock"]} +0000', GIT_COMMITTER_DATE=f'{allocation["clock"]} +0000')
        git(repo, 'read-tree', source, env=env)
        blob = git(repo, 'hash-object', '-w', '--stdin', data=after, env=env).decode().strip()
        mode = git(repo, 'ls-tree', source, '--', edit['path']).decode().split()[0]
        git(repo, 'update-index', '--cacheinfo', f'{mode},{blob},{edit["path"]}', env=env)
        tree = git(repo, 'write-tree', env=env).decode().strip()
        message = (f'Allocate {APP_PACKAGE} {allocation["versionCode"]} ({allocation["versionName"]})\n\n'
            f'Source: {source}\nAllocation: {allocation["allocationSha256"]}\n'
            'Version-only app candidate derived by scripts/release-allocate.py app; not pushed.\n')
        return git(repo, 'commit-tree', tree, '-p', source, data=message.encode(), env=env).decode().strip()


def reserve_app(directory, allocation, candidate):
    allocation = check_app_allocation({k: v for k, v in allocation.items() if k != 'verdict'})
    directory = Path(directory)
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    record = dict(allocation, candidateSha=candidate)
    try:
        fd = os.open(directory/f'{allocation["versionCode"]}.json', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    except FileExistsError:
        raise Refused(f'reservation_exists: {APP_PACKAGE} {allocation["versionCode"]} is already reserved') from None
    with os.fdopen(fd, 'w') as f:
        json.dump(record, f, indent=2, sort_keys=True)
    return record


def app_latest(out, env, repo=ROOT, fetch=None, clock=time.time, lock=shared_lock):
    """Builder, app-only with apps=auto: under the shared lock, observe, then skip (already published) or allocate,
    derive and reserve one fresh Rose Agent candidate from the dispatched main commit (CANDIDATE_SHA)."""
    source = env.get('CANDIDATE_SHA', '')
    pipeline = json.loads(show(repo, source, PIPELINE_CONFIG)) if SHA.fullmatch(source) else None
    if pipeline is None:
        raise Refused('invalid: CANDIDATE_SHA is not a commit')
    cfg = json.loads(show(repo, source, ALLOCATION_CONFIG))
    with lock(pipeline['host']['lockPath']):
        observation = observe(env['HOME'], cfg['device'], pipeline['publication']['repository'], pipeline['publication']['api'], fetch)
        allocation = allocate_app(source, observation, int(clock()), env.get('GITHUB_RUN_ID', ''), env.get('GITHUB_RUN_ATTEMPT', ''), repo)
        if allocation['verdict'] == 'unchanged':
            record, exported = allocation, {'APP_SKIP': 'true', 'APP_SOURCE_SHA': source}
        else:
            candidate = derive_app(allocation, repo)
            record = dict(reserve_app(Path(env['HOME'])/'rose-actions'/APP_RESERVATIONS, allocation, candidate), verdict='allocated')
            exported = {'APP_SKIP': 'false', 'APP_SOURCE_SHA': source, 'CANDIDATE_SHA': candidate,
                'APPS': json.dumps([[APP_PACKAGE, allocation['versionCode']]], separators=(',', ':'))}
    fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    with os.fdopen(fd, 'w') as f:
        json.dump(record, f, indent=2, sort_keys=True)
    for name, target in (('GITHUB_ENV', exported), ('GITHUB_OUTPUT', {k.lower(): v for k, v in exported.items()})):
        if env.get(name):
            with open(env[name], 'a') as f:
                f.writelines(f'{k}={v}\n' for k, v in target.items())
    return record, exported


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split('\n', 1)[0])
    sub = p.add_subparsers(dest='command', required=True)
    a = sub.add_parser('allocate')
    for flag in ('--source', '--observation', '--now', '--run-id', '--run-attempt', '--out'):
        a.add_argument(flag, required=True)
    a.add_argument('--recipe')
    sub.add_parser('latest').add_argument('--out', required=True)
    sub.add_parser('app').add_argument('--out', required=True)
    for name in ('derive', 'notes', 'reserve'):
        s = sub.add_parser(name)
        s.add_argument('--allocation', required=True)
        if name == 'reserve':
            s.add_argument('--dir', required=True)
            s.add_argument('--candidate', required=True)
    args = p.parse_args(argv)
    try:
        if args.command == 'allocate':
            if not re.fullmatch(r'\d{10}', args.now) or not args.run_id.isdigit() or not args.run_attempt.isdigit():
                print('invalid: --now, --run-id and --run-attempt must be numeric', file=sys.stderr)
                return 2
            allocation = allocate(args.source, json.loads(Path(args.observation).read_text()), int(args.now), args.run_id,
                args.run_attempt, recipe=args.recipe)
            fd = os.open(args.out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
            with os.fdopen(fd, 'w') as f:
                json.dump(allocation, f, indent=2, sort_keys=True)
            print(json.dumps({k: allocation[k] for k in ('build', 'buildTimestamp', 'apps', 'deltaSha256', 'allocationSha256')}))
        elif args.command == 'latest':
            record, exported = latest(args.out, os.environ)
            print(json.dumps(dict(exported, source=record['sourceSha'], allocation=record['allocationSha256'])))
        elif args.command == 'app':
            record, exported = app_latest(args.out, os.environ)
            print(json.dumps(dict(exported, verdict=record['verdict'])))
        else:
            allocation = json.loads(Path(args.allocation).read_text())
            if args.command == 'derive':
                print(derive(allocation))
            elif args.command == 'notes':
                sys.stdout.write(notes(allocation))
            else:
                print(json.dumps(reserve(args.dir, allocation, check_candidate(allocation, args.candidate))['build']))
    except Refused as error:
        print(f'refused: {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
