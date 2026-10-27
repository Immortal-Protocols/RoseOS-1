#!/usr/bin/env python3
"""Read-only full archive verification, emitting counts rather than filenames."""
import hashlib
import json
from pathlib import Path, PurePosixPath
import sys
import zipfile
import tempfile
import shutil

p = Path(sys.argv[1]).expanduser().resolve()
receipt = json.loads(Path(str(p) + '.json').read_text())
h = hashlib.sha256()
with p.open('rb') as stream:
    for block in iter(lambda: stream.read(1024 * 1024), b''):
        h.update(block)
if h.hexdigest() != receipt['sha256'] or p.stat().st_size != receipt['bytes']:
    raise SystemExit('Receiver receipt does not match archive bytes')
with zipfile.ZipFile(p) as archive:
    names = archive.namelist()
    if len(names) != len(set(names)):
        raise SystemExit('Duplicate archive paths')
    for name in names:
        rel = PurePosixPath(name)
        if rel.is_absolute() or '..' in rel.parts:
            raise SystemExit('Unsafe archive path')
    manifest = json.loads(archive.read('manifest.json'))
    expected = {entry['path'] for entry in manifest['files']}
    if set(names) != expected | {'manifest.json'}:
        raise SystemExit('Manifest does not match archive contents')
    for entry in manifest['files']:
        size = 0
        digest = hashlib.sha256()
        with archive.open(entry['path']) as source:
            for block in iter(lambda: source.read(1024 * 1024), b''):
                size += len(block)
                digest.update(block)
        if size != entry['size'] or digest.hexdigest() != entry['sha256']:
            raise SystemExit('File checksum mismatch')
    nested = []
    for name in names:
        if not name.startswith('app-private/') or not name.endswith('.zip'):
            continue
        with tempfile.TemporaryFile() as spool:
            with archive.open(name) as source:
                shutil.copyfileobj(source, spool)
            spool.seek(0)
            with zipfile.ZipFile(spool) as app:
                app_manifest = json.loads(app.read('manifest.json'))
                if set(app.namelist()) != {v['path'] for v in app_manifest['files']} | {'manifest.json'}:
                    raise SystemExit('App export manifest does not match contents')
                changed = 0
                for entry in app_manifest['files']:
                    rel = PurePosixPath(entry['path'])
                    if rel.is_absolute() or '..' in rel.parts:
                        raise SystemExit('Unsafe app export path')
                    digest = hashlib.sha256()
                    size = 0
                    with app.open(entry['path']) as source:
                        for block in iter(lambda: source.read(1024 * 1024), b''):
                            size += len(block)
                            digest.update(block)
                    if size != entry['size'] or digest.hexdigest() != entry['sha256']:
                        raise SystemExit('App export checksum mismatch')
                    changed += bool(entry.get('changedDuringCopy'))
                nested.append(dict(package=app_manifest['package'],filesVerified=len(app_manifest['files']),changedDuringCopy=changed))
    result = dict(archive=str(p), archiveSha256=h.hexdigest(),
                  filesVerified=len(expected), skippedOrChanged=len(manifest['errors']),
                  sharedFiles=sum(n.startswith('shared/') for n in expected),
                  pixelVoiceFiles=sum(n.startswith('pixel-voice-private/') for n in expected),
                  installedPackages=len(manifest['apps']),appExports=nested,
                  bytes=receipt['bytes'], scope=manifest['exclusions'],
                  consistency='Live copy; changed/skipped entries recorded in private manifest')
    Path(str(p) + '.verified.json').write_text(json.dumps(result, indent=2) + '\n')
    Path(str(p) + '.verified.json').chmod(0o600)
    print(json.dumps(result))
