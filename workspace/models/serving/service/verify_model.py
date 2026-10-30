#!/usr/bin/env python3
"""Verified model storage for the persistent service (MODELS-ALWAYSON-SERVICE-001).

The Hugging Face cache stores each file of a revision as snapshots/<revision>/<path>, which links to blobs/<id>. The
hub's id is the sha256 of the content for LFS files, and the git blob sha1 otherwise.

  record   the first time the model is loaded: hash every snapshot file, require each content hash to equal its hub id
           and each pinned weight size to match the config, then write the receipt atomically.
  check    at every start: every recorded file links to the same blob id with the same size. Add --full to rehash
           the content (the daily timer does this).

Exit 0 = verified; 3 = no receipt yet (first start: allowed to download); 4 = mismatch or missing (refuse to start).
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import time

SCHEMA = 'rose-models-model-receipt/1'
CHUNK = 8 << 20
EXIT_NO_RECEIPT, EXIT_MISMATCH = 3, 4


class Mismatch(Exception):
    pass


def snapshot(cache, repo, revision):
    return Path(cache) / 'hub' / ('models--' + repo.replace('/', '--')) / 'snapshots' / revision


def content_ids(path):
    """(sha256 hex, git blob sha1 hex) of a file's content."""
    size = path.stat().st_size
    sha256, sha1 = hashlib.sha256(), hashlib.sha1(b'blob %d\0' % size)
    with open(path, 'rb') as f:
        while chunk := f.read(CHUNK):
            sha256.update(chunk)
            sha1.update(chunk)
    return sha256.hexdigest(), sha1.hexdigest()


def files(snap):
    if not snap.is_dir():
        raise Mismatch('snapshot_missing')
    out = {}
    for p in sorted(snap.rglob('*')):
        if p.is_dir():
            continue
        target = p.resolve(strict=False)
        if not p.is_symlink() or target.parent.name != 'blobs' or not target.is_file():
            raise Mismatch(f'not_a_cache_blob: {p.relative_to(snap)}')
        out[p.relative_to(snap).as_posix()] = target
    if not out:
        raise Mismatch('snapshot_empty')
    return out


def record(cache, model, receipt_path):
    snap = snapshot(cache, model['repo'], model['revision'])
    entries = {}
    for rel, blob in files(snap).items():
        sha256, sha1 = content_ids(blob)
        if blob.name not in (sha256, sha1):
            raise Mismatch(f'content_differs_from_hub_id: {rel}')
        entries[rel] = {'blob': blob.name, 'size': blob.stat().st_size, 'sha256': sha256}
    for name, size in model['weights_fetch']['files'].items():
        if entries.get(name, {}).get('size') != size:
            raise Mismatch(f'pinned_file_missing_or_wrong_size: {name}')
    receipt = {'schema': SCHEMA, 'repo': model['repo'], 'revision': model['revision'], 'files': entries,
               'recordedAt': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}
    tmp = Path(str(receipt_path) + '.tmp')
    tmp.write_text(json.dumps(receipt, indent=1, sort_keys=True) + '\n')
    os.replace(tmp, receipt_path)
    return receipt


def check(cache, model, receipt_path, full=False):
    receipt = json.loads(Path(receipt_path).read_text())
    if (receipt.get('schema'), receipt.get('repo'), receipt.get('revision')) != (SCHEMA, model['repo'], model['revision']):
        raise Mismatch('receipt_is_for_another_model')
    present = files(snapshot(cache, model['repo'], model['revision']))
    if set(present) != set(receipt['files']):
        raise Mismatch('file_set_changed')
    for rel, want in receipt['files'].items():
        blob = present[rel]
        if blob.name != want['blob'] or blob.stat().st_size != want['size']:
            raise Mismatch(f'blob_changed: {rel}')
        if full and content_ids(blob)[0] != want['sha256']:
            raise Mismatch(f'content_changed: {rel}')
    return receipt


def write_result(path, out):
    """The outcome as a small nonsecret file the health receipt publishes: result, full, at (no paths or reasons)."""
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(str(path) + '.tmp')
    tmp.write_text(json.dumps({'result': out['result'], 'full': bool(out.get('full')), 'at': int(time.time())}) + '\n')
    os.replace(tmp, path)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('action', choices=('record', 'check'))
    p.add_argument('--config', type=Path, default=Path(__file__).resolve().parents[1] / 'muse-glimmer-30b.json')
    p.add_argument('--cache', type=Path, required=True)
    p.add_argument('--receipt', type=Path, required=True)
    p.add_argument('--full', action='store_true')
    p.add_argument('--result', type=Path, help='also write the outcome here (for the health receipt)')
    a = p.parse_args(argv)
    model = json.loads(a.config.read_text())['model']
    try:
        if a.action == 'record':
            r = record(a.cache, model, a.receipt)
        elif not a.receipt.exists():
            out = {'result': 'no_receipt', 'full': a.full}
            write_result(a.result, out)
            print(json.dumps(out))
            return EXIT_NO_RECEIPT
        else:
            r = check(a.cache, model, a.receipt, a.full)
    except (Mismatch, OSError, ValueError, KeyError) as e:
        write_result(a.result, {'result': 'mismatch', 'full': a.full})
        print(json.dumps({'result': 'mismatch', 'reason': str(e) if isinstance(e, Mismatch) else type(e).__name__}))
        return EXIT_MISMATCH
    out = {'result': 'verified', 'files': len(r['files']), 'revision': r['revision'], 'full': a.full or a.action == 'record'}
    write_result(a.result, out)
    print(json.dumps(out))
    return 0

if __name__ == '__main__':
    sys.exit(main())
