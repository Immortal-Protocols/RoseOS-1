#!/usr/bin/env python3
"""Fixed hosted receipt read -> one allocation-only review branch. No build or publication."""
import argparse
import base64
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('draft_recovery', ROOT/'scripts/frankel-draft-recovery.py')
recovery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(recovery)
REPO = recovery.REPO
RECEIPT_RUN = 42075447865
RECEIPT_SOURCE = 'f61064f74d038ef76f7e4ced2456ce42c7bda4cd'
PIN = (13438221694, 'frankel-draft-recovery-42075447865', 1968,
       '2c9195275b87136880d47f9daeed9d294e8180a2942220917dbdb8e86847cede')
BRANCH = 'codex/frankel-2026110501-allocation'
PATH = 'os/latest-beta/frankel-2026110501-allocation.json'
WORKFLOW = '.github/workflows/frankel-allocation-recovery.yml'
FILES = {'allocation/allocation.json', 'ingest-receipt.json', 'publish-receipt.json'}
Refused = recovery.Refused


def admission(env):
    if (env.get('GITHUB_REPOSITORY') != REPO or env.get('GITHUB_EVENT_NAME') != 'workflow_dispatch'
            or env.get('GITHUB_REF') != 'refs/heads/main' or env.get('GITHUB_RUN_ATTEMPT') != '1'
            or env.get('GITHUB_WORKFLOW_REF') != REPO+'/'+WORKFLOW+'@refs/heads/main'
            or not re.fullmatch(r'[0-9a-f]{40}', env.get('GITHUB_SHA', ''))):
        raise Refused('only a fresh manual main allocation-recovery attempt is admitted')


def api(client, method, path, **kwargs):
    return client._request(method, 'https://api.github.com/repos/'+REPO+path, 'fixed allocation recovery', **kwargs)


def check_receipt_source(run, artifact):
    if (run.get('id'), run.get('head_sha'), run.get('path'), run.get('event'), run.get('head_branch'),
            run.get('run_attempt'), run.get('status'), run.get('conclusion')) != (
            RECEIPT_RUN, RECEIPT_SOURCE, '.github/workflows/frankel-draft-recovery.yml', 'workflow_dispatch',
            'main', 1, 'completed', 'success'):
        raise Refused('receipt producer is not the exact successful recovery')
    if (artifact.get('id'), artifact.get('name'), artifact.get('size_in_bytes'), artifact.get('digest'),
            artifact.get('expired'), (artifact.get('workflow_run') or {}).get('id')) != (
            PIN[0], PIN[1], PIN[2], 'sha256:'+PIN[3], False, RECEIPT_RUN):
        raise Refused('receipt artifact metadata differs from the fixed pin')


def extract(path, home):
    with zipfile.ZipFile(path) as archive:
        entries = archive.infolist()
        if len(entries) > 4 or sum(i.file_size for i in entries) > 65536:
            raise Refused('receipt entry/expanded-byte bound exceeded')
        seen = set()
        for item in entries:
            kind = stat.S_IFMT(item.external_attr >> 16)
            if item.filename in seen or kind not in (0, stat.S_IFREG, stat.S_IFDIR):
                raise Refused('duplicate or nonregular receipt entry')
            seen.add(item.filename)
            if item.is_dir():
                if item.filename != 'allocation/': raise Refused('unexpected receipt directory')
            elif item.filename not in FILES:
                raise Refused('unexpected receipt file')
        if {i.filename for i in entries if not i.is_dir()} != FILES:
            raise Refused('receipt set is incomplete')
        documents = {name: archive.read(name) for name in FILES}  # bounded above; CRC verified
    (home/'allocation').mkdir()
    (home/'allocation/allocation.json').write_bytes(documents['allocation/allocation.json'])
    record = recovery.allocation(home)
    candidate = dict(sha=record['candidateSha'], build=recovery.BUILD,
                     timestamp=str(record['buildTimestamp']), channel='beta', device='frankel')
    ingest = json.loads(documents['ingest-receipt.json'])
    published = json.loads(documents['publish-receipt.json'])
    if (ingest.get('producerRun') != recovery.RUN or ingest.get('sourceSha') != recovery.SOURCE
            or ingest.get('candidate') != candidate or published.get('producerRun') != recovery.RUN
            or str(published.get('recoveryRun')) != str(RECEIPT_RUN) or published.get('candidate') != candidate
            or published.get('draft') is not True or published.get('promoted') is not False
            or published.get('releaseId') != 435513431):
        raise Refused('receipt and allocation producer/candidate/draft identities differ')
    return documents['allocation/allocation.json'], candidate


def write_review_branch(client, base, data):
    """Create only a new ref and one file. Never patch a ref, overwrite a file or merge."""
    if not re.fullmatch(r'[0-9a-f]{40}', base) or len(data) > 65536:
        raise Refused('invalid captured base or allocation size')
    for path in ('/git/ref/heads/'+BRANCH, '/contents/'+PATH+'?ref='+base):
        status, _, _ = api(client, 'GET', path, expect=(200, 404))
        if status != 404: raise Refused('review branch or allocation file already exists')
    commit = api(client, 'GET', '/git/commits/'+base)[2]
    tree = api(client, 'POST', '/git/trees', expect=(201,), body={
        'base_tree': commit['tree']['sha'], 'tree': [dict(path=PATH, mode='100644', type='blob', content=data.decode('utf-8'))]})[2]
    new_commit = api(client, 'POST', '/git/commits', expect=(201,), body={
        'message': 'Record verified Frankel 2026110501 allocation for app export',
        'tree': tree['sha'], 'parents': [base]})[2]
    sha = new_commit['sha']
    if not re.fullmatch(r'[0-9a-f]{40}', sha): raise Refused('invalid created commit identity')
    api(client, 'POST', '/git/refs', expect=(201,), body={'ref':'refs/heads/'+BRANCH, 'sha':sha})
    ref = api(client, 'GET', '/git/ref/heads/'+BRANCH)[2]
    content = api(client, 'GET', '/contents/'+PATH+'?ref='+sha)[2]
    if (ref.get('object', {}).get('sha') != sha or content.get('encoding') != 'base64'
            or content.get('size') != len(data) or base64.b64decode(content['content']) != data):
        raise Refused('created branch readback failed; inspect uncertain outcome, never retry')
    return dict(branch=BRANCH, commit=sha, path=PATH, allocationSha256=hashlib.sha256(data).hexdigest(),
                url='https://github.com/'+REPO+'/tree/'+BRANCH)


def recover(client, home, base):
    if home.exists(): raise Refused('scratch exists; no automatic retry')
    check_receipt_source(api(client, 'GET', f'/actions/runs/{RECEIPT_RUN}')[2],
                         api(client, 'GET', f'/actions/artifacts/{PIN[0]}')[2])
    home.mkdir(mode=0o700)
    recovery.download(client, PIN, home/'receipt.zip')
    data, candidate = extract(home/'receipt.zip', home)
    result = write_review_branch(client, base, data)
    return dict(result, receiptRun=RECEIPT_RUN, artifactId=PIN[0], producerRun=recovery.RUN,
                candidate=candidate, merged=False, exported=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dir', required=True, type=Path)
    a = parser.parse_args()
    try:
        admission(os.environ)
        result = recover(recovery.github.GitHubReleases(REPO, os.environ.get('GITHUB_TOKEN')),
                         a.dir, os.environ['GITHUB_SHA'])
        (a.dir/'result.json').write_text(json.dumps(result, indent=2)+'\n')
        print(json.dumps(result, sort_keys=True))
    except Exception as error:
        detail = str(error) if isinstance(error, (Refused, recovery.github.PublishError)) else type(error).__name__
        print('Allocation recovery refused: '+detail, file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__': sys.exit(main())
