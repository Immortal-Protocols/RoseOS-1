#!/usr/bin/env python3
"""Release transport to the dedicated signer (RELEASE020 M2, producer and acceptance sides).

The frankel builder packages one verified unsigned build as a hash-bound roseos.signing-job.v1 job for
.github/workflows/os-sign.yml; after signing, the release side accepts the signer's public outputs only when their
receipt is bound to exactly that job. No credential, host access or transport route is created here: `package` writes a
local directory and `accept` reads a downloaded one.

  package --release-dir R --otatools F --device D --build N --timestamp T --candidate SHA --notes F
          --config os/release-pipeline/frankel.json --job-id J --out DIR [--signing-root /srv/rose-signing]
      DIR/J/{manifest.json, target_files.zip, otatools.zip, UPDATE.md}; the manifest's absolute input paths are the
      signer's SIGNING_ROOT/inbox/J/ paths os-sign.yml requires. Prints {jobId, manifestSha256, manifest}.
  request --source-run R --artifact-id A --artifact-digest D --manifest-sha256 M --request-id I --job-id J
      os-release's mid-run job (actions:write, contents:read, GH_TOKEN = its short-lived GITHUB_TOKEN): dispatches
      os-sign.yml@main once with those inputs, waits for exactly one successful main workflow_dispatch run named
      'OS signing <request id>' (an existing one is awaited, never repeated; failed, duplicate, untrusted or timed-out
      refuses; an uncertain dispatch is never retried) and prints its single signed-J-<run> artifact.
  accept --job DIR/J --signed S
      S is the os-sign artifact (signing-receipt.json and the public files). The receipt must name this job's device,
      build, timestamp, candidate, config and every input hash, and every public file must match its receipt hash.

Exit status: 0 done, 1 refused, 2 invalid input.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = 'roseos.signing-job.v1'
RECEIPT_SCHEMA = 'roseos.signing-receipt.v1'
SIGNING_ROOT = '/srv/rose-signing'
JOB = re.compile(r'[A-Za-z0-9._-]{1,128}')
INPUTS = (('targetFiles', 'target_files.zip'), ('otatools', 'otatools.zip'), ('notes', 'UPDATE.md'))


class Invalid(Exception):
    pass


class Refused(Exception):
    pass


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(8 << 20), b''):
            digest.update(block)
    return digest.hexdigest()


def public_assets(device, build):
    return {f'{device}-ota_update-{build}.zip', f'{device}-stable', 'releasekey.x509.pem', 'avb_pkmd.bin', 'UPDATE.md'}


def package(release_dir, otatools, device, build, timestamp, candidate, notes, config, job_id, out,
            signing_root=SIGNING_ROOT, repo=ROOT):
    if not re.fullmatch(r'[a-z][a-z0-9]{1,31}', device) or not re.fullmatch(r'[0-9]{10}', build) \
            or not re.fullmatch(r'[0-9]{10}', timestamp) or not re.fullmatch(r'[0-9a-f]{40}', candidate):
        raise Invalid('device, build, timestamp and candidate must be exact')
    if not JOB.fullmatch(job_id) or job_id in ('.', '..'):
        raise Invalid('job id must be a plain [A-Za-z0-9._-] name')
    if not Path(signing_root).is_absolute():
        raise Invalid('the signing root must be absolute')
    config_path = Path(config)
    if config_path.is_absolute() or '..' in config_path.parts:
        raise Invalid('config must be a repository-relative path')
    cfg_bytes = (Path(repo)/config_path).read_bytes()
    if json.loads(cfg_bytes).get('target', {}).get('device') != device:
        raise Refused(f'config_mismatch: {config} is not the {device} config')
    sources = {'targetFiles': Path(release_dir)/'unsigned-target_files.zip', 'otatools': Path(otatools), 'notes': Path(notes)}
    for key, path in sources.items():
        if path.is_symlink() or not path.is_file() or path.stat().st_size == 0:
            raise Refused(f'input_missing: {key} {path}')
    if build.encode() not in sources['notes'].read_bytes():
        raise Refused(f'notes_mismatch: the notes do not name build {build}')
    job = Path(out)/job_id
    if job.exists():
        raise Refused(f'job_exists: {job} is never overwritten')
    job.mkdir(parents=True, mode=0o700)
    inputs = {}
    for key, name in INPUTS:
        shutil.copyfile(sources[key], job/name)
        inputs[key] = {'path': f'{signing_root.rstrip("/")}/inbox/{job_id}/{name}', 'size': (job/name).stat().st_size,
            'sha256': sha256_file(job/name)}
        if inputs[key]['sha256'] != sha256_file(sources[key]):
            raise Refused(f'input_changed: {key} changed while it was copied')
    manifest = {'schema': SCHEMA, 'device': device, 'build': build, 'buildTimestamp': timestamp, 'candidateSha': candidate,
        'config': {'path': str(config_path), 'sha256': hashlib.sha256(cfg_bytes).hexdigest()}, 'inputs': inputs}
    data = (json.dumps(manifest, indent=2, sort_keys=True) + '\n').encode()
    fd = os.open(job/'manifest.json', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'wb') as f:
        f.write(data)
    return {'jobId': job_id, 'manifestSha256': hashlib.sha256(data).hexdigest(), 'manifest': str(job/'manifest.json')}


def accept(job, signed):
    job, signed = Path(job), Path(signed)
    try:
        manifest = json.loads((job/'manifest.json').read_text())
        receipt = json.loads((signed/'signing-receipt.json').read_text())
    except (OSError, ValueError):
        raise Refused('unreadable: the job manifest or the signing receipt is missing or not JSON') from None
    if manifest.get('schema') != SCHEMA or receipt.get('schema') != RECEIPT_SCHEMA:
        raise Refused('schema: not a signing job and its signing receipt')
    if receipt.get('verdict') not in ('signed', 'reused', 'recovered'):
        raise Refused(f'verdict: the signer did not report a signed job ({receipt.get("verdict")})')
    for key in ('device', 'build', 'buildTimestamp', 'candidateSha', 'config', 'inputs'):
        if receipt.get(key) != manifest.get(key):
            raise Refused(f'binding: the receipt {key} differs from this job')
    expected = public_assets(manifest['device'], manifest['build'])
    outputs = {Path(name).name: digest for name, digest in (receipt.get('outputs') or {}).items() if name.startswith('public/')}
    present = {p.name for p in signed.iterdir() if p.is_file() and p.name != 'signing-receipt.json'}
    if set(outputs) != expected or present != expected:
        raise Refused('outputs: the signed public files are not exactly the expected release assets')
    for name in sorted(expected):
        path = signed/name
        if path.is_symlink() or sha256_file(path) != outputs[name]:
            raise Refused(f'output_changed: {name} differs from the signing receipt')
    if hashlib.sha256((signed/'UPDATE.md').read_bytes()).hexdigest() != manifest['inputs']['notes']['sha256']:
        raise Refused('notes: the published UPDATE.md is not this job\'s notes')
    return {'jobId': receipt.get('jobId'), 'build': manifest['build'], 'identities': receipt.get('identities')}



# ---------------------------------------------------------------- request and wait (os-release mid-run job, M2b)

SIGN_WORKFLOW = 'os-sign.yml'
SIGN_TITLE = 'OS signing'
REQUEST = re.compile(r'[1-9][0-9]{0,19}\.[1-9][0-9]{0,3}\.[a-z][a-z0-9]{1,31}-[0-9]{10}')


def sign_run_name(request_id):
    return f'{SIGN_TITLE} {request_id}'


def api(env, method, path, body=None, opener=None):
    import urllib.request
    request = urllib.request.Request(f'{env["GITHUB_API_URL"]}/repos/{env["GITHUB_REPOSITORY"]}{path}', method=method,
        data=None if body is None else json.dumps(body).encode(),
        headers={'Accept': 'application/vnd.github+json', 'Authorization': f'Bearer {env["GH_TOKEN"]}',
                 'X-GitHub-Api-Version': '2022-11-28', 'User-Agent': 'roseos-release-transport'})
    with (opener or urllib.request.urlopen)(request, timeout=60) as response:
        status, data = response.status, response.read()
    return status, data


def sign_runs(env, request_id, opener):
    status, data = api(env, 'GET', f'/actions/workflows/{SIGN_WORKFLOW}/runs?event=workflow_dispatch&branch=main&per_page=100',
        opener=opener)
    runs = json.loads(data).get('workflow_runs') if status == 200 else None
    if not isinstance(runs, list):
        raise Refused('runs_unreadable: cannot list os-sign runs')
    return [r for r in runs if r.get('display_title') == sign_run_name(request_id)]


def request(env, inputs, opener=None, clock=None, pause=None, timeout=5400, poll=30):
    """Dispatch os-sign.yml@main once for this request, then wait for exactly one matching run to succeed.
    Returns the os-sign run id. Never dispatches twice: an existing run for the request id is waited on, not repeated."""
    import time
    import urllib.error
    clock, pause = clock or time.time, pause or time.sleep
    request_id = inputs.get('request_id', '')
    if not REQUEST.fullmatch(request_id) or not request_id.startswith(f'{env.get("GITHUB_RUN_ID")}.'):
        raise Invalid('request_id must be <this run id>.<attempt>.<device>-<build>')
    for key in ('artifact_digest', 'manifest_sha256'):
        if not re.fullmatch(r'(sha256:)?[0-9a-f]{64}', inputs.get(key, '')):
            raise Invalid(f'{key} must be a sha256')
    if inputs.get('source_run') != env.get('GITHUB_RUN_ID') or not re.fullmatch(r'[1-9][0-9]{0,19}', inputs.get('artifact_id', '')):
        raise Invalid('source_run must be this run and artifact_id a number')
    existing = sign_runs(env, request_id, opener)
    if len(existing) > 1:
        raise Refused(f'duplicate: {len(existing)} os-sign runs carry request {request_id}')
    if not existing:
        try:
            status, _ = api(env, 'POST', f'/actions/workflows/{SIGN_WORKFLOW}/dispatches', {'ref': 'main', 'inputs': inputs},
                opener=opener)
        except urllib.error.HTTPError as error:
            if error.code in (401, 403, 404, 422):
                raise Refused(f'dispatch_refused: HTTP {error.code}; no signing run was started') from None
            status = None
        except (urllib.error.URLError, OSError):
            status = None
        if status not in (200, 204, None):
            raise Refused(f'dispatch_refused: HTTP {status}')
    deadline = clock() + timeout
    while True:
        runs = sign_runs(env, request_id, opener)
        if len(runs) > 1:
            raise Refused(f'duplicate: {len(runs)} os-sign runs carry request {request_id}')
        if runs and runs[0].get('status') == 'completed':
            if runs[0].get('conclusion') != 'success':
                raise Refused(f'signing_failed: os-sign run {runs[0].get("id")} concluded {runs[0].get("conclusion")}')
            if runs[0].get('head_branch') != 'main' or runs[0].get('event') != 'workflow_dispatch':
                raise Refused('signing_untrusted: the matching os-sign run is not a main workflow_dispatch')
            return str(runs[0]['id'])
        if clock() >= deadline:
            raise Refused(f'signing_timeout: no completed os-sign run for {request_id}; nothing retried')
        pause(poll)


def signed_artifact(env, sign_run, job_id, opener=None):
    """The one signed-<job>-<run> artifact of that os-sign run: (id, name, digest)."""
    status, data = api(env, 'GET', f'/actions/runs/{sign_run}/artifacts?per_page=100', opener=opener)
    items = json.loads(data).get('artifacts') if status == 200 else None
    if not isinstance(items, list):
        raise Refused('artifacts_unreadable: cannot list the os-sign run artifacts')
    name = f'signed-{job_id}-{sign_run}'
    found = [a for a in items if a.get('name') == name and not a.get('expired')]
    if len(found) != 1:
        raise Refused(f'artifact_missing: os-sign run {sign_run} has no single {name}')
    return str(found[0]['id']), name, found[0].get('digest')


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest='command', required=True)
    pk = sub.add_parser('package')
    for name in ('release-dir', 'otatools', 'device', 'build', 'timestamp', 'candidate', 'notes', 'config', 'job-id', 'out'):
        pk.add_argument(f'--{name}', required=True)
    pk.add_argument('--signing-root', default=SIGNING_ROOT)
    rq = sub.add_parser('request')
    for name in ('source-run', 'artifact-id', 'artifact-digest', 'manifest-sha256', 'request-id', 'job-id'):
        rq.add_argument(f'--{name}', required=True)
    rq.add_argument('--timeout', type=int, default=5400)
    ac = sub.add_parser('accept')
    ac.add_argument('--job', required=True)
    ac.add_argument('--signed', required=True)
    args = p.parse_args(argv)
    try:
        if args.command == 'package':
            result = package(args.release_dir, args.otatools, args.device, args.build, args.timestamp, args.candidate,
                args.notes, args.config, args.job_id, args.out, args.signing_root)
        elif args.command == 'request':
            inputs = {'source_run': args.source_run, 'artifact_id': args.artifact_id, 'artifact_digest': args.artifact_digest,
                      'manifest_sha256': args.manifest_sha256, 'request_id': args.request_id}
            sign_run = request(dict(os.environ), inputs, timeout=args.timeout)
            artifact_id, name, digest = signed_artifact(dict(os.environ), sign_run, args.job_id)
            result = {'signRun': sign_run, 'signedArtifactId': artifact_id, 'signedArtifact': name, 'signedDigest': digest}
        else:
            result = accept(args.job, args.signed)
        print(json.dumps(result, sort_keys=True))
        return 0
    except Invalid as error:
        print(json.dumps({'verdict': 'INVALID', 'error': str(error)}), file=sys.stderr)
        return 2
    except (Refused, OSError) as error:
        print(json.dumps({'verdict': 'REFUSED', 'error': str(error)}), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
