#!/usr/bin/env python3
"""One-off hosted recovery of run 42063694943; never starts a builder or promotes."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import sys
from types import SimpleNamespace
from urllib.parse import urlsplit
import zipfile

import github_release_publisher as github

ROOT = Path(__file__).resolve().parents[1]
REPO = 'Immortal-Protocols/RoseOS-1'
RUN = 42063694943
SOURCE = '9a98e80f50b654af20f43dda41ad41f86e14b434'
BUILD = '2026110501'
TAG = 'frankel-' + BUILD
ARTIFACTS = {
    'ota': (13436253665, 'os-release-frankel-2026110501', 1679796732,
            '448803ccd7faca70ed8d3a0301271a3b308d59c30b4c0411456e32de9da52b40'),
    'allocation': (13434213406, 'os-release-allocation-42063694943', 1193,
                   '6dd6ea5ed24f2027b0ca58f700011e903bfe0fa9f928e933d089e52114cccca0'),
}


class Refused(Exception):
    pass


class NewDraftOnly:
    """Keep the shared publisher from reconciling a tag that appears during recovery."""
    def __init__(self, client):
        self.client = client

    def find_release(self, tag):
        if tag != TAG or self.client.find_release(tag) is not None:
            raise Refused('tag already exists or differs; no overwrite or reconciliation')
        return None

    def delete_asset(self, *args, **kwargs):
        raise Refused('recovery never deletes an existing asset')

    def promote(self, *args, **kwargs):
        raise Refused('recovery never promotes')

    def finalize_public(self, *args, **kwargs):
        raise Refused('recovery never finalizes publicly')

    def __getattr__(self, name):
        return getattr(self.client, name)


def load(name):
    spec = importlib.util.spec_from_file_location(name.replace('-', '_'), ROOT/'scripts'/f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def digest(path):
    h = hashlib.sha256()
    with open(path, 'rb') as stream:
        for block in iter(lambda: stream.read(8 << 20), b''):
            h.update(block)
    return h.hexdigest()


def admission(env):
    if (env.get('GITHUB_REPOSITORY') != REPO or env.get('GITHUB_EVENT_NAME') != 'workflow_dispatch'
            or env.get('GITHUB_REF') != 'refs/heads/main' or env.get('GITHUB_RUN_ATTEMPT') != '1'
            or env.get('GITHUB_WORKFLOW_REF') != REPO+'/.github/workflows/frankel-draft-recovery.yml@refs/heads/main'):
        raise Refused('only a fresh manual main recovery attempt is admitted')


def check_producer(run, jobs):
    if (run.get('id') != RUN or run.get('head_sha') != SOURCE or run.get('run_attempt') != 1
            or run.get('head_branch') != 'main' or run.get('event') != 'workflow_dispatch'
            or run.get('path') != '.github/workflows/os-release.yml'
            or run.get('status') != 'completed' or run.get('conclusion') != 'failure'):
        raise Refused('producer run identity or terminal result differs')
    for name in ('guard', 'builder', 'cleanup'):
        matches = [j for j in jobs if j.get('name') == name]
        if len(matches) != 1 or matches[0].get('conclusion') != 'success':
            raise Refused('producer required job did not succeed: '+name)
    builder = next(j for j in jobs if j.get('name') == 'builder')
    for name in ('Integrate (apps signed first) and build', 'Sign', 'Verify the signed candidate', 'Transfer'):
        steps = [s for s in builder.get('steps', []) if s.get('name') == name]
        if len(steps) != 1 or steps[0].get('conclusion') != 'success':
            raise Refused('producer required step did not succeed: '+name)
    publishers = [j for j in jobs if j.get('name') == 'publisher']
    if len(publishers) != 1 or publishers[0].get('conclusion') != 'failure':
        raise Refused('producer publisher is not the expected failure')


def check_artifact(meta, pin):
    ident, name, size, sha = pin
    if (meta.get('id'), meta.get('name'), meta.get('size_in_bytes'), meta.get('digest'), meta.get('expired')) != \
            (ident, name, size, 'sha256:'+sha, False):
        raise Refused('artifact metadata differs from exact retained pin')
    if (meta.get('workflow_run') or {}).get('id') != RUN:
        raise Refused('artifact belongs to another run')


def download(client, pin, path):
    ident, _, size, sha = pin
    url = f'https://api.github.com/repos/{REPO}/actions/artifacts/{ident}/zip'
    status, location, _ = client._request('GET', url, 'fixed Actions archive', expect=(200, 302),
                                         stream_to=path, limit=size)
    if status == 302:
        parsed = urlsplit(location or '')
        if (parsed.scheme != 'https' or parsed.username or parsed.password or parsed.port not in (None, 443)
                or not (parsed.hostname or '').endswith(('.blob.core.windows.net', '.actions.githubusercontent.com'))):
            raise Refused('unexpected artifact storage redirect')
        client._request('GET', location, 'fixed Actions archive storage', authenticated=False,
                        stream_to=path, limit=size)
    if path.stat().st_size != size or digest(path) != sha:
        raise Refused('artifact archive bytes differ from exact pin')


def unpack(path, destination, allowed, limit):
    with zipfile.ZipFile(path) as archive:
        entries = archive.infolist()
        if len(entries) > 32 or sum(i.file_size for i in entries) > limit:
            raise Refused('archive entry/expanded size bound exceeded')
        seen = set()
        for info in entries:
            name = info.filename
            parts = PurePosixPath(name).parts
            kind = stat.S_IFMT(info.external_attr >> 16)
            if (name in seen or '\\' in name or name.startswith('/') or '..' in parts
                    or kind not in (0, stat.S_IFREG, stat.S_IFDIR)):
                raise Refused('unsafe or duplicate archive member')
            seen.add(name)
            if info.is_dir():
                if name != 'assets/':
                    raise Refused('unexpected archive directory')
            elif name not in allowed:
                raise Refused('unexpected archive file')
        if {i.filename for i in entries if not i.is_dir()} != allowed:
            raise Refused('archive is missing required files')
        destination.mkdir()
        for info in entries:
            if info.is_dir():
                continue
            target = destination/info.filename
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as src, open(target, 'xb') as out:
                shutil.copyfileobj(src, out, 1 << 20)  # zipfile checks CRC; declared total size bounded above


def allocation(home):
    allocate = load('release-allocate')
    record = allocate.check_allocation(json.loads((home/'allocation/allocation.json').read_text()))
    if (record.get('sourceSha'), record.get('recipeSha'), record.get('build'), record.get('device'),
            record.get('channel'), record.get('tag'), record.get('run')) != \
            (SOURCE, SOURCE, BUILD, 'frankel', 'beta', TAG, {'id': str(RUN), 'attempt': '1'}):
        raise Refused('allocation does not name the exact producer')
    if allocate.derive(record, ROOT) != record.get('candidateSha'):
        raise Refused('allocation deterministic candidate mismatch')
    return record


def ingest(home, client):
    if home.exists():
        raise Refused('recovery scratch already exists; no automatic reuse')
    get = lambda path: client._request('GET', 'https://api.github.com/repos/'+REPO+path, 'producer metadata')[2]
    run = get(f'/actions/runs/{RUN}')
    jobs = get(f'/actions/runs/{RUN}/attempts/1/jobs?per_page=100')
    if jobs.get('total_count', 101) > 100:
        raise Refused('producer jobs exceed bounded page')
    check_producer(run, jobs['jobs'])
    home.mkdir(mode=0o700)
    for name, pin in ARTIFACTS.items():
        check_artifact(get(f'/actions/artifacts/{pin[0]}'), pin)
        download(client, pin, home/(name+'.zip'))
    pipeline = load('release-pipeline')
    allowed = {'manifest.json', 'expected-identities.json', 'pipeline-receipt.json'} | \
        {'assets/'+n for n in pipeline.public_assets('frankel', BUILD)}
    unpack(home/'ota.zip', home/'ota', allowed, 3 << 30)
    unpack(home/'allocation.zip', home/'allocation', {'allocation.json'}, 64 << 10)
    record = allocation(home)
    candidate = dict(sha=record['candidateSha'], build=BUILD, timestamp=str(record['buildTimestamp']),
                     channel='beta', device='frankel')
    receipt = json.loads((home/'ota/pipeline-receipt.json').read_text())
    if receipt.get('candidate') != candidate or receipt.get('simulated') is not False:
        raise Refused('transfer receipt candidate mismatch or simulation')
    return {'producerRun': RUN, 'sourceSha': SOURCE, 'candidate': candidate}


def publish(home):
    record = allocation(home)
    pipeline = load('release-pipeline')
    config = pipeline.device_config('frankel', None)
    adapter = pipeline.Production()
    client = NewDraftOnly(github.client_from_environment(config))
    client.find_release(TAG)
    candidate = dict(sha=record['candidateSha'], build=BUILD, timestamp=str(record['buildTimestamp']),
                     channel='beta', device='frankel')
    args = SimpleNamespace(artifact_dir=str(home/'ota'), allocation=str(home/'allocation/allocation.json'),
                           promote=False, draft_only=True)
    original_factory = pipeline.github_release_publisher.client_from_environment
    try:
        pipeline.github_release_publisher.client_from_environment = lambda config: client
        result = pipeline.publish(args, config, candidate, adapter)
    finally:
        pipeline.github_release_publisher.client_from_environment = original_factory
    readback = adapter.client.get_release(adapter.release['id'])
    if readback.get('tag_name') != TAG or readback.get('draft') is not True or readback.get('published_at') is not None:
        raise Refused('draft readback failed; inspect uncertain outcome, never retry automatically')
    return dict(result, producerRun=RUN, recoveryRun=os.environ.get('GITHUB_RUN_ID'),
                candidate=candidate, releaseId=readback['id'], url=readback['html_url'], draft=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('ingest', 'publish'))
    parser.add_argument('--dir', type=Path, required=True)
    a = parser.parse_args()
    try:
        admission(os.environ)
        if a.command == 'ingest':
            result = ingest(a.dir, github.GitHubReleases(REPO, os.environ.get('GITHUB_TOKEN')))
        else:
            result = publish(a.dir)
        (a.dir/(a.command+'-receipt.json')).write_text(json.dumps(result, indent=2)+'\n')
        print(json.dumps(result, sort_keys=True))
    except Exception as error:
        # Never print signed URLs, tokens or arbitrary remote response bodies.
        detail = str(error) if isinstance(error, (Refused, github.PublishError)) else type(error).__name__
        print('Frankel recovery refused: '+detail, file=sys.stderr)
        if a.dir.is_dir() and not a.dir.is_symlink():
            (a.dir/'failure-receipt.json').write_text(json.dumps(dict(
                producerRun=RUN, stage=a.command, error=detail,
                publicationMayHaveStarted=a.command == 'publish', automaticRetry=False), indent=2)+'\n')
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
