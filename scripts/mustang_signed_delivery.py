#!/usr/bin/env python3
"""Publish the immutable, already signed and verified run49 OTA; no signer/VM."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import sys
import urllib.error
import urllib.parse
import urllib.request
import zipfile

import mustang_artifact_delivery as recovery

ROOT = recovery.ROOT
RUN = '43150538877'
SOURCE = '519ccac3dec495924b7196386408bb319735986d'
ARTIFACT = dict(id=13915411398, name='mustang-dedicated-ota-43150538877-1',
    sizeBytes=5897358832, sha256='e8c5fdc790ce0883b7f97f183386c003f7d7a9cb7853c4669ebdd96ad6e34c27')
OTA = 'mustang-ota_update-2611111800.zip'
MEMBERS = {'signed-target_files.zip', 'mustang-hosted-sign-receipt.json',
           'staged/'+OTA, 'staged/mustang-ota-receipt.json', 'staged/mustang-stable', 'staged/SHA256SUMS'}


def authenticate(env, event, *, api=None, root=ROOT, git=None):
    import mustang_bootstrap_collector as collector
    run, source = recovery.scope(env, event, selected=RUN)
    recovery.compatible(source, root, git)
    if git is None:
        import subprocess
        git = lambda args: subprocess.run(['git','-C',str(root),*args],capture_output=True,text=True)
    if git(['merge-base','--is-ancestor',SOURCE,source]).returncode:
        raise ValueError('signed producer is not an ancestor of publication source')
    phase = env.get('GITHUB_JOB')
    if phase not in ('guard', 'publisher'):
        raise ValueError('signed delivery job differs')
    base = '/repos/Immortal-Protocols/RoseOS-1/actions/runs/'
    def reader(owner):
        return api or collector.github_reader(dict(runId=owner), env.get('GH_TOKEN', ''), include_artifacts=True)
    read = reader(RUN)
    producer = read(base+RUN)
    common = dict(run_attempt=1, path=recovery.WORKFLOW, workflow_id=408460629,
                  head_branch='main', event='workflow_dispatch')
    def require_run(row, expected):
        if any(row.get(k) != v for k, v in {**common, **expected}.items()) or any(
                (row.get(k) or {}).get('id') != 1428996648 for k in ('repository', 'head_repository')):
            raise ValueError('signed delivery Actions identity differs')
    require_run(producer, dict(id=int(RUN), run_number=49, head_sha=SOURCE,
        created_at='2026-11-12T03:47:34Z', status='completed', conclusion='success'))
    jobs = collector.listing(read(base+RUN+'/attempts/1/jobs?per_page=100'), 'jobs')
    expected = {'guard':'success', 'artifact-admission':'success', 'signer':'success', 'publisher':'skipped'}
    for name, result in expected.items():
        rows = [j for j in jobs if j.get('name') == name]
        if len(rows) != 1 or any(rows[0].get(k) != v for k, v in dict(
                run_id=int(RUN), head_sha=SOURCE, status='completed', conclusion=result).items()) \
                or rows[0].get('run_attempt', 1) != 1:
            raise ValueError('signed producer job differs: '+name)
        if name == 'signer':
            job = rows[0]
            if job.get('id') != 130958593634 or job.get('runner_group_id') != 6 \
                    or 'mustang-signer-4core' not in job.get('labels', []):
                raise ValueError('signed producer signer route differs')
            required = ('Full byte, hash, archive, and peak preflight before any signer secret',
                'Sign with the checkout entry point, never a guest-supplied executable',
                'Run python3 scripts/actions-gcp-login.py logout', 'Upload the hosted signed outputs')
            if any(len([s for s in job.get('steps', []) if s.get('name') == name and
                    s.get('conclusion') == 'success' and s.get('status') == 'completed']) != 1 for name in required):
                raise ValueError('signed producer preflight, staging, logout or upload did not pass')
    artifacts = collector.listing(read(base+RUN+'/artifacts?per_page=100'), 'artifacts')
    rows = [a for a in artifacts if a.get('id') == ARTIFACT['id'] or a.get('name') == ARTIFACT['name']]
    if len(rows) != 1:
        raise ValueError('signed artifact missing or ambiguous')
    artifact = rows[0]
    if any(artifact.get(k) != v for k, v in dict(id=ARTIFACT['id'], name=ARTIFACT['name'],
            size_in_bytes=ARTIFACT['sizeBytes'], digest='sha256:'+ARTIFACT['sha256'], expired=False).items()) \
            or any((artifact.get('workflow_run') or {}).get(k) != v for k, v in dict(
                id=int(RUN), head_sha=SOURCE, repository_id=1428996648, head_repository_id=1428996648).items()):
        raise ValueError('signed artifact immutable identity differs')
    read = reader(run)
    current = read(base+run)
    require_run(current, dict(id=int(run), head_sha=source, status='in_progress', conclusion=None))
    if type(current.get('run_number')) is not int or current['run_number'] <= 49 \
            or current.get('created_at', '') <= producer['created_at']:
        raise ValueError('distinct current publication run required')
    jobs = collector.listing(read(base+run+'/attempts/1/jobs?per_page=100'), 'jobs')
    outcomes = {'guard':('in_progress', None)} if phase == 'guard' else {
        'guard':('completed', 'success'), 'publisher':('in_progress', None)}
    for name, result in outcomes.items():
        rows = [j for j in jobs if j.get('name') == name]
        if len(rows) != 1 or (rows[0].get('status'), rows[0].get('conclusion')) != result \
                or rows[0].get('run_id') != int(run) or rows[0].get('head_sha') != source \
                or rows[0].get('run_attempt', 1) != 1:
            raise ValueError('current publication job differs')
    native = {'controller', 'enroll', 'builder', 'queue-watch', 'source', 'hostdeps', 'lock-admin',
              'factory', 'vendor', 'otatools', 'artifact-admission', 'signer', 'cleanup'}
    if any(j.get('name') in native and j.get('conclusion') != 'skipped' for j in jobs):
        raise ValueError('signed delivery cannot start native work, cleanup or signing')
    return dict(signedProducerRun=RUN, signedProducerSource=SOURCE, artifact=ARTIFACT,
                publicationRun=run, publicationSource=source)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def download(path, token, *, open_http=None, pin=ARTIFACT):
    if not token:
        raise ValueError('signed artifact read token unavailable')
    open_http = open_http or urllib.request.build_opener(NoRedirect()).open
    request = urllib.request.Request('https://api.github.com/repos/Immortal-Protocols/RoseOS-1/actions/artifacts/'+
        str(pin['id'])+'/zip', headers={'Authorization':'Bearer '+token, 'Accept':'application/vnd.github+json',
            'X-GitHub-Api-Version':'2026-03-10'})
    phase = 'api'
    try:
        try:
            response = open_http(request, timeout=60)
        except urllib.error.HTTPError as error:
            if error.code != 302:
                raise ValueError('signed artifact API HTTP '+str(error.code)+'; no retry') from None
            location = error.headers.get('Location', '');error.close()
            try:
                target = urllib.parse.urlsplit(location);port = target.port
            except ValueError:
                raise ValueError('signed artifact storage redirect differs') from None
            if target.scheme != 'https' or target.username or target.password or target.fragment or port not in (None,443) \
                    or not any((target.hostname or '').endswith('.'+domain) for domain in
                        ('blob.core.windows.net','actions.githubusercontent.com','githubusercontent.com')):
                raise ValueError('signed artifact storage redirect differs')
            phase = 'storage'
            response = open_http(urllib.request.Request(location), timeout=60)
        digest = hashlib.sha256();size = 0
        with response, Path(path).open('xb') as out:
            if response.status != 200:
                raise ValueError('signed artifact response differs')
            while True:
                block = response.read(1 << 20)
                if not block:break
                size += len(block)
                if size > pin['sizeBytes']:
                    raise ValueError('signed artifact exceeds immutable size')
                digest.update(block);out.write(block)
        if size != pin['sizeBytes'] or digest.hexdigest() != pin['sha256']:
            raise ValueError('signed artifact size/hash differs')
    except urllib.error.HTTPError as error:
        raise ValueError('signed artifact '+phase+' HTTP '+str(error.code)+'; no retry') from None
    except (urllib.error.URLError, TimeoutError):
        raise ValueError('signed artifact '+phase+' transport failed; no retry') from None


def receipt_identity(receipt):
    import mustang_hosted_sign as hosted
    if any(receipt.get(k) != v for k,v in dict(schema=hosted.RECEIPT_SCHEMA, device='mustang',
            signer=hosted.SIGNER, secretVersion=hosted.SECRET_VERSION, workflow=recovery.WORKFLOW,
            runId=RUN, runAttempt='1', sourceSha=SOURCE).items()):
        raise ValueError('original signed receipt identity differs')
    if receipt.get('publicStage', {}).get('path') != 'staged':
        raise ValueError('signed public stage missing')
    outputs = receipt.get('outputs', {})
    if set(outputs) != {'signedTargetFiles', 'ota'}:
        raise ValueError('signed output inventory differs')
    for key, filename in (('signedTargetFiles','signed-target_files.zip'), ('ota','staged/'+OTA)):
        row = outputs[key]
        if row.get('file') != filename or type(row.get('sizeBytes')) is not int or not 0 < row['sizeBytes'] <= ((2 if key == 'ota' else 6) << 30) \
                or not hosted.HEX64.fullmatch(row.get('sha256') or ''):
            raise ValueError('signed output descriptor differs')
    return receipt


def extract(archive, out, *, pin=ARTIFACT):
    """Hash the immutable ZIP, stream all signed bytes, retain only public files."""
    import mustang_hosted_sign as hosted
    archive, out = Path(archive), Path(out)
    if archive.is_symlink() or archive.stat().st_size != pin['sizeBytes'] or hosted.sha256_file(archive) != pin['sha256']:
        raise ValueError('signed artifact archive hash differs')
    out.mkdir(mode=0o700)  # Refuse an existing or linked output directory.
    (out/'staged').mkdir(mode=0o700)
    with zipfile.ZipFile(archive) as z:
        rows = z.infolist();files = [m for m in rows if not m.is_dir()]
        if len(files) != len(MEMBERS) or {m.filename for m in files} != MEMBERS \
                or len(rows) != len({m.filename for m in rows}) \
                or any(m.is_dir() and m.filename != 'staged/' for m in rows) \
                or any(stat.S_IFMT(m.external_attr >> 16) not in (0, stat.S_IFREG) for m in files) \
                or any(m.flag_bits & 1 for m in rows):
            raise ValueError('signed artifact inventory, paths or member types differ')
        for name in ('mustang-hosted-sign-receipt.json', 'staged/mustang-ota-receipt.json',
                     'staged/mustang-stable', 'staged/SHA256SUMS'):
            if z.getinfo(name).file_size > 2 << 20:
                raise ValueError('signed receipt/metadata size exceeds bound')
        raw = z.read('mustang-hosted-sign-receipt.json')
        receipt = receipt_identity(json.loads(raw))
        for name in sorted(MEMBERS):
            member = z.getinfo(name)
            descriptor = next((r for r in receipt['outputs'].values() if r['file'] == name), None)
            if descriptor and member.file_size != descriptor['sizeBytes']:
                raise ValueError('signed ZIP member size differs')
            digest = hashlib.sha256();size = 0
            # Signed target-files is checked, never extracted or executed by publisher.
            target = None if name == 'signed-target_files.zip' else (out/name).open('xb')
            try:
                with z.open(member) as source:
                    while True:
                        block = source.read(1 << 20)
                        if not block:break
                        size += len(block);digest.update(block)
                        if target:target.write(block)
                if size != member.file_size or descriptor and digest.hexdigest() != descriptor['sha256']:
                    raise ValueError('signed ZIP member hash differs')
            finally:
                if target:target.close()
    return hashlib.sha256(raw).hexdigest()


def bind(directory, env):
    import mustang_hosted_sign as hosted
    path = Path(directory)/'mustang-hosted-sign-receipt.json'
    expected = env.get('EXPECTED_SIGNED_RECEIPT_SHA256', '')
    if not hosted.HEX64.fullmatch(expected) or path.is_symlink() or not path.is_file() \
            or hosted.sha256_file(path) != expected:
        raise ValueError('authenticated signed archive receipt binding differs')
    receipt = receipt_identity(json.loads(path.read_text()))
    stage = Path(directory)/'staged/mustang-ota-receipt.json'
    if stage.is_symlink() or not stage.is_file() or hosted.sha256_file(stage) != receipt['publicStage'].get('receiptSha256'):
        raise ValueError('original public stage receipt hash differs')
    value = json.loads(stage.read_text())
    if (value.get('runId'), value.get('runAttempt'), value.get('recipeSource')) != (RUN,'1',SOURCE) \
            or value.get('ota') != {**receipt['outputs']['ota'], 'file':OTA}:
        raise ValueError('signed hosted/public stage provenance differs')
    recovery.proof_identity(value.get('artifactProducer'))
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('guard','fetch'))
    parser.add_argument('--out')
    args = parser.parse_args()
    env = os.environ
    authenticate(env, json.loads(Path(env['GITHUB_EVENT_PATH']).read_text()))
    if args.action == 'fetch':
        if env.get('GITHUB_JOB') != 'publisher':raise ValueError('signed fetch requires publisher')
        out = Path(args.out);archive = out.with_suffix('.download.zip')
        # Archive + extracted public OTA + fixed workspace reserve. No larger runner.
        if shutil.disk_usage(out.parent).free < ARTIFACT['sizeBytes'] + (2 << 30) + (1 << 30):
            raise ValueError('publisher lacks signed artifact extraction capacity')
        download(archive, env.get('GH_TOKEN', ''))
        receipt_hash = extract(archive, out)
        bind(out, dict(EXPECTED_SIGNED_RECEIPT_SHA256=receipt_hash))
        archive.unlink()  # Only our own hash-verified temporary download.
        with open(env['GITHUB_OUTPUT'], 'a') as outputs:
            outputs.write('receipt_sha256='+receipt_hash+'\n')
        print(json.dumps(dict(signedProducerRun=RUN, artifactId=ARTIFACT['id'], receiptSha256=receipt_hash)))


if __name__ == '__main__':
    try:main()
    except Exception as error:
        print(json.dumps(dict(success=False, failure=str(error))), file=sys.stderr)
        sys.exit(1)
