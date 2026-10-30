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


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest='command', required=True)
    pk = sub.add_parser('package')
    for name in ('release-dir', 'otatools', 'device', 'build', 'timestamp', 'candidate', 'notes', 'config', 'job-id', 'out'):
        pk.add_argument(f'--{name}', required=True)
    pk.add_argument('--signing-root', default=SIGNING_ROOT)
    ac = sub.add_parser('accept')
    ac.add_argument('--job', required=True)
    ac.add_argument('--signed', required=True)
    args = p.parse_args(argv)
    try:
        if args.command == 'package':
            result = package(args.release_dir, args.otatools, args.device, args.build, args.timestamp, args.candidate,
                args.notes, args.config, args.job_id, args.out, args.signing_root)
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
