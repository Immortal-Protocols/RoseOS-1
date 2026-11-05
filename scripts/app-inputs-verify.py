#!/usr/bin/env python3
"""Consumer side of APP-INPUTS-EXPORT: the one committed record of an exported app-input set, and its admission.

The exporter (scripts/host-app-inputs-export.py, os-release.yml mode app-inputs-export) writes a private artifact.
Release then commits that export as os/app-inputs/<producer tag>.json (schema roseos.app-inputs-record.v1). The
record is built only from the artifact's own receipt and the run's actual metadata (run, attempt, artifact id,
digest). A consuming builder (Yogi, or Mustang on its own capacity) downloads that exact artifact by run and
artifact id, then admits it with `verify` before any use.

  record --receipt R --run ID --attempt N --artifact-name NAME --artifact-id ID --artifact-digest sha256:HEX
          prints the record for R's export (the receipt file as downloaded); Release commits it
  verify --record PATH --dir DIR [--aapt PATH --apksigner PATH]
          admits DIR against the committed record; nothing is written

verify checks:
- the record is roseos.app-inputs-record.v1, and its producer's allocation record is committed in this checkout
  with the recorded sha256;
- DIR holds exactly the recorded files plus app-inputs-export.json and SHA256SUMS, each a regular file and never a
  link;
- every file has the recorded size and sha256;
- app-inputs-export.json has the recorded receiptSha256 and the same producer, signer, SDK, apps and files;
- SHA256SUMS lists exactly those files with those digests;
- with --aapt and --apksigner (the consuming builder's own tools), the exporter's checks run again on DIR's bytes:
  the nine-file inventory, receipts, package, versionCode and signer, and the policy pins
  (host-app-inputs-export.check_set).
Exit status: 0 admitted or printed; 1 refused; 2 invalid usage.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
RECORD_SCHEMA = 'roseos.app-inputs-record.v1'
RECEIPT_SCHEMA = 'roseos.app-inputs-export.v1'
RECEIPT, SUMS = 'app-inputs-export.json', 'SHA256SUMS'
HEX64 = re.compile(r'[0-9a-f]{64}')
RUN = re.compile(r'[1-9][0-9]{0,19}')


class Refused(Exception):
    pass


def sha256(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()


def exporter():
    spec = importlib.util.spec_from_file_location('host_app_inputs_export', ROOT/'scripts/host-app-inputs-export.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def record(receipt_path, run, attempt, artifact_name, artifact_id, artifact_digest):
    """The committed record of one export, from its receipt as downloaded and the run's actual metadata."""
    receipt = json.loads(Path(receipt_path).read_text())
    if receipt.get('schema') != RECEIPT_SCHEMA:
        raise Refused('not an app-inputs export receipt')
    if not RUN.fullmatch(str(run)) or not RUN.fullmatch(str(attempt)) or not RUN.fullmatch(str(artifact_id)):
        raise Refused('run, attempt and artifact id must be positive numbers')
    build = receipt['producer']['build']
    if artifact_name != f'app-inputs-frankel-{build}-{run}-{attempt}':
        raise Refused(f'artifact {artifact_name} is not the export of build {build} by run {run} attempt {attempt}')
    if not re.fullmatch(r'sha256:[0-9a-f]{64}', str(artifact_digest)):
        raise Refused('the artifact digest must be sha256:<64 hex> as the API reports it')
    return {'schema': RECORD_SCHEMA, 'producer': receipt['producer'], 'signerSha256': receipt['signerSha256'],
            'sdk': receipt['sdk'], 'apps': receipt['apps'], 'files': receipt['files'],
            'export': {'run': str(run), 'attempt': str(attempt), 'receiptSha256': sha256(receipt_path),
                       'artifact': {'name': artifact_name, 'id': int(artifact_id), 'digest': artifact_digest}}}


def regular(path, label):
    if os.path.islink(path) or not Path(path).is_file():
        raise Refused(f'{label} must be a regular file, not a link')
    return Path(path)


def verify(record_path, directory, tools=None, root=ROOT, identity=None):
    rec = json.loads(Path(record_path).read_text())
    if not isinstance(rec, dict) or rec.get('schema') != RECORD_SCHEMA:
        raise Refused('not an app-inputs record')
    producer, files = rec['producer'], rec['files']
    allocation = Path(root)/producer['allocation']['path']
    if '..' in Path(producer['allocation']['path']).parts or not allocation.is_file() or allocation.is_symlink() \
            or sha256(allocation) != producer['allocation']['sha256']:
        raise Refused('the producer allocation record is not committed here with the recorded sha256')
    directory = Path(directory)
    if directory.is_symlink() or not directory.is_dir():
        raise Refused('the downloaded directory must be a real directory')
    expected = {f['path'] for f in files} | {RECEIPT, SUMS}
    found = {str(p.relative_to(directory)) for p in directory.rglob('*') if not p.is_dir() or p.is_symlink()}
    if found != expected:
        raise Refused(f'inventory differs: missing {sorted(expected - found)}, unexpected {sorted(found - expected)}')
    for f in files:
        path = regular(directory/f['path'], f['path'])
        if path.stat().st_size != f['sizeBytes'] or sha256(path) != f['sha256']:
            raise Refused(f'{f["path"]} differs from the record')
    receipt_path = regular(directory/RECEIPT, RECEIPT)
    if sha256(receipt_path) != rec['export']['receiptSha256']:
        raise Refused('the export receipt differs from the recorded receiptSha256')
    receipt = json.loads(receipt_path.read_text())
    if any(receipt.get(k) != rec.get(k) for k in ('producer', 'signerSha256', 'sdk', 'apps', 'files')) \
            or receipt.get('schema') != RECEIPT_SCHEMA:
        raise Refused('the export receipt does not name the recorded producer, signer, SDK, apps and files')
    sums = {}
    for line in regular(directory/SUMS, SUMS).read_text().splitlines():
        digest, _, name = line.partition('  ')
        if not HEX64.fullmatch(digest) or name in sums:
            raise Refused('SHA256SUMS is malformed')
        sums[name] = digest
    if sums != {**{f['path']: f['sha256'] for f in files}, RECEIPT: rec['export']['receiptSha256']}:
        raise Refused('SHA256SUMS does not list exactly the recorded files')
    checked = False
    if tools:
        module = exporter()
        try:
            pin = module.pins(signer_config(rec))  # the same candidate-bundle contract; its signer must be the record's
            if pin['sdk'] != rec['sdk']:
                raise module.AdapterFailed('the recorded SDK differs from the candidate-bundle contract')
            module.check_set(directory/'signed-apps', directory/'background-policy.json', json.loads(allocation.read_text()),
                             pin, tools, **({'identity': identity} if identity else {}))
        except module.AdapterFailed as error:
            raise Refused(str(error)) from None
        checked = True
    return {'verdict': 'ADMITTED', 'record': str(record_path), 'producer': producer['tag'],
            'export': rec['export'], 'apps': rec['apps'], 'toolsRechecked': checked}


def signer_config(rec):
    return {'identities': {'appSignerSha256': rec['signerSha256']}}



def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest='command', required=True)
    r = sub.add_parser('record')
    for flag in ('--receipt', '--run', '--attempt', '--artifact-name', '--artifact-id', '--artifact-digest'):
        r.add_argument(flag, required=True)
    v = sub.add_parser('verify')
    v.add_argument('--record', required=True)
    v.add_argument('--dir', required=True)
    v.add_argument('--aapt')
    v.add_argument('--apksigner')
    a = p.parse_args(argv)
    try:
        if a.command == 'record':
            out = record(a.receipt, a.run, a.attempt, a.artifact_name, a.artifact_id, a.artifact_digest)
        else:
            if bool(a.aapt) != bool(a.apksigner):
                p.error('--aapt and --apksigner go together')
            tools = {'aapt': a.aapt, 'apksigner': a.apksigner} if a.aapt else None
            out = verify(a.record, a.dir, tools)
    except (Refused, OSError, ValueError, KeyError, TypeError) as error:
        print(json.dumps({'verdict': 'REFUSED', 'error': str(error) if isinstance(error, Refused) else type(error).__name__}),
              file=sys.stderr)
        return 1
    print(json.dumps(out, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    sys.exit(main())
