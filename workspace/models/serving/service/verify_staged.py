#!/usr/bin/env python3
"""Verified staged model storage for the selectable Qwen serving profile (MODELS-QWEN-SERVE-001).

The Qwen files were staged as a plain directory by bootstrap's cloud-stage-once and are bound to the pinned rose_cloud
manifest, not to a Hugging Face cache receipt (that is verify_model.py, used by the default Muse profile).

  verify_staged.py --manifest <rose_cloud/manifest.json> --manifest-sha256 <pin> --group qwen
                   --staging-root <dir> [--full] [--result PATH]

The manifest file must hash to the pin; then every file of the group must exist at its pinned size, and every
non-weight file (config, tokenizer, template, licence) must match its pinned hash. --full also rehashes the weights
(the daily timer). There is no download branch: exit 0 = verified, 4 = mismatch or missing (refuse to start).
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import time

CHUNK = 8 << 20
EXIT_MISMATCH = 4


class Mismatch(Exception):
    pass


def digest(path, kind, size):
    h = hashlib.sha256() if kind == 'sha256' else hashlib.sha1()
    if kind == 'gitsha1':
        h.update(b'blob %d\0' % size)
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(CHUNK), b''):
            h.update(block)
    return h.hexdigest()


def is_weight(rel):
    return rel.endswith('.safetensors') or rel == 'model.safetensors.index.json'


def verify(manifest_path, manifest_sha256, group, staging_root, full=False):
    """{'files', 'hashed', 'revision'} for a verified staged group, or Mismatch."""
    data = Path(manifest_path).read_bytes()
    if hashlib.sha256(data).hexdigest() != manifest_sha256:
        raise Mismatch('manifest is not the pinned one')
    manifest = json.loads(data)
    g = manifest['groups'][group]
    root = Path(staging_root) / g['dir']  # from the rendered profile (qwen-fp8.json staged_dir), like the pin
    hashed = 0
    for rel, size, kind, want in g['files']:
        if '..' in rel.split('/') or rel.startswith('/'):
            raise Mismatch('manifest path escapes the staged directory')
        path = root / rel
        if not path.is_file():
            raise Mismatch(f'{rel} missing')
        if path.stat().st_size != size:
            raise Mismatch(f'{rel} size differs')
        if full or not is_weight(rel):
            if digest(path, kind, size) != want:
                raise Mismatch(f'{rel} content differs')
            hashed += 1
    return {'files': len(g['files']), 'hashed': hashed, 'revision': g['revision']}


def write_result(path, out):
    if path is None:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = Path(str(path) + '.tmp')
        tmp.write_text(json.dumps({'result': out['result'], 'full': bool(out.get('full')), 'at': int(time.time())}) + '\n')
        os.replace(tmp, path)
    except OSError:
        pass


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--manifest', type=Path, required=True)
    p.add_argument('--manifest-sha256', required=True)
    p.add_argument('--group', required=True)
    p.add_argument('--staging-root', required=True)
    p.add_argument('--full', action='store_true')
    p.add_argument('--result', type=Path, help='also write the outcome here (for the health receipt)')
    a = p.parse_args(argv)
    try:
        r = verify(a.manifest, a.manifest_sha256, a.group, a.staging_root, a.full)
    except (Mismatch, OSError, ValueError, KeyError, TypeError) as e:
        write_result(a.result, {'result': 'mismatch', 'full': a.full})
        print(json.dumps({'result': 'mismatch', 'reason': str(e) if isinstance(e, Mismatch) else type(e).__name__}))
        return EXIT_MISMATCH
    out = {'result': 'verified', 'staged': True, 'full': a.full, **r}
    write_result(a.result, out)
    print(json.dumps(out))
    return 0


if __name__ == '__main__':
    sys.exit(main())
