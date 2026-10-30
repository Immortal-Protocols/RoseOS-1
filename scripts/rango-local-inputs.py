#!/usr/bin/env python3
"""OS-RANGO-BUILD-003: verify the already-downloaded public frankel 2026102901 release, copy its three signed Rose
apps out unchanged, and write the local SDK 37 background policy for the isolated Rango integration.

  prepare --ota ZIP --verification JSON --out NEW_DIR --fsck-erofs PATH --debugfs PATH --aapt PATH --apksigner PATH
          [--config os/build-only/rango-reconcile.json]

Steps, each fail-closed and in order:
  1. both files are regular (not links), named, sized and hashed exactly as the config's inputs pin (release
     428513880); verification.json is read for provenance only (build, signed target-files hash);
  2. the OTA zip has no absolute, .., backslash or duplicate entry names, one stored payload.bin, and metadata
     pre-device frankel: it is a Frankel OTA, used here as an app source and never installed on or offered to Rango;
  3. only the partitions holding the three apps are rebuilt from payload.bin with every operation and partition hash
     checked (rose-verify.py's reader, pure Python; no ota_extractor, mount, repack or resign);
  4. each APK is read from its pinned image path with the explicit fsck.erofs or debugfs given (no PATH lookup); a
     symlink or non-regular file anywhere on that path is refused; the bytes are written once, read-only, unchanged;
  5. package, versionCode and the app signer (apksigner at SDK 37) must equal the pins;
  6. prepare-background-policy.py writes the policy: controller as --agent-apk, voice as --voice-apk, Calculator as
     --target-apk, prototypeControl as pinned (false, as the delivered product);
  7. NEW_DIR/local-inputs.json (write-once) records every hash, tool and path; rango-build-only.py compile binds the
     reconcile receipt to it.
No network, credential, signer, device, publisher or cloud call. NEW_DIR must not exist; nothing else is written.
Exit status: 0 ok, 1 an input or check failed, 2 invalid usage.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import sys
import time
import zipfile

SCRIPTS = Path(__file__).resolve().parent
ROOT = SCRIPTS.parent
CONFIG = ROOT/'os/build-only/rango-reconcile.json'
sys.path.insert(0, str(SCRIPTS))
import background_apks  # noqa: E402
import release_receipts  # noqa: E402

def _rose_verify():
    spec = importlib.util.spec_from_file_location('rose_verify', SCRIPTS/'rose-verify.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

rv = _rose_verify()
TOOLS = ('fsck_erofs', 'debugfs', 'aapt', 'apksigner')
SIGNED_NAME = '{package}.apk'  # the names rango-reconcile.json applyArgs read under {signed_apps}

class Failed(Exception):
    pass

def sha256_file(path):
    return rv.sha256(path)

def regular(path, label):
    path = Path(path)
    if not path.is_absolute():
        raise Failed(f'{label} {path} must be an absolute path')
    if path.is_symlink() or not path.is_file():
        raise Failed(f'{label} {path} must be a regular file, not a link')
    return path

def pinned_file(path, pin, label):
    path = regular(path, label)
    if path.name != pin['file']:
        raise Failed(f'{label} is named {path.name}, not {pin["file"]}')
    size, digest = path.stat().st_size, sha256_file(path)
    if (size, digest) != (pin['sizeBytes'], pin['sha256']):
        raise Failed(f'{label} {path.name}: {size} bytes sha256 {digest}; pinned {pin["sizeBytes"]} bytes {pin["sha256"]}')
    return {'path': str(path), 'sizeBytes': size, 'sha256': digest}

def safe_member(name):
    pure = PurePosixPath(name)
    return name and '\\' not in name and not pure.is_absolute() and '..' not in pure.parts and '\0' not in name

def check_archive(ota, device):
    with zipfile.ZipFile(ota) as archive:
        names = archive.namelist()
        bad = sorted({n for n in names if not safe_member(n)})
        if bad:
            raise Failed(f'OTA has unsafe entry names: {bad[:5]}')
        duplicates = sorted({n for n in names if names.count(n) > 1})
        if duplicates:
            raise Failed(f'OTA has duplicate entries: {duplicates[:5]}')
        if 'payload.bin' not in names:
            raise Failed('OTA has no payload.bin')
    try:
        rv.payload_entry(ota)
        metadata = rv.ota_metadata(ota)['metadata']
    except rv.Fail as error:
        raise Failed(f'OTA payload: {error}')
    if metadata.get('pre-device') != device:
        raise Failed(f'OTA pre-device is {metadata.get("pre-device")!r}, not the pinned {device!r}')
    return metadata

def provenance(verification, inputs):
    data = json.loads(Path(verification).read_text())
    candidate = data.get('candidate') if isinstance(data.get('candidate'), dict) else {}
    observed = {'candidate': {k: candidate.get(k) for k in ('sha', 'build', 'timestamp') if k in candidate},
        'signedTargetFilesSha256': data.get('signedTargetFilesSha256')}
    if 'build' in candidate and str(candidate['build']) != inputs['release']['build']:
        raise Failed(f'verification.json names build {candidate["build"]}, not {inputs["release"]["build"]}')
    if observed['signedTargetFilesSha256'] not in (None, inputs['signedTargetFilesSha256']):
        raise Failed('verification.json names a different signed target-files hash')
    observed['signedTargetFilesMatchesPin'] = observed['signedTargetFilesSha256'] == inputs['signedTargetFilesSha256']
    return observed

def run_tool(argv, binary=False):
    result = subprocess.run([str(a) for a in argv], capture_output=True, text=not binary)
    if result.returncode:
        raise Failed(f'{Path(str(argv[0])).name} exited {result.returncode}')
    return result.stdout

def erofs_file(image, inner, tool, tree):
    """Bytes of one regular file from an erofs image, extracted once into tree with the given fsck.erofs."""
    if not tree.exists():
        run_tool([tool, f'--extract={tree}', image])
    path = tree
    for part in PurePosixPath(inner).parts:
        path = path/part
        if path.is_symlink():
            raise Failed(f'{inner}: {part} is a symlink in the image')
    if not path.is_file():
        raise Failed(f'{inner} is not a regular file in the image')
    resolved = path.resolve()
    if tree.resolve() not in resolved.parents:
        raise Failed(f'{inner} resolves outside the extracted image')
    return resolved.read_bytes()

def ext4_file(image, inner, tool):
    """Bytes of one regular file from an ext4 image with the given debugfs; every parent must be a directory."""
    parts = PurePosixPath(inner).parts
    for depth in range(1, len(parts) + 1):
        stat = run_tool([tool, '-R', 'stat /' + '/'.join(parts[:depth]), image])
        kind = 'regular' if depth == len(parts) else 'directory'
        if f'Type: {kind}' not in stat:
            raise Failed(f'{inner}: /{"/".join(parts[:depth])} is not a {kind} entry (a symlink or other) in the image')
    return run_tool([tool, '-R', f'cat /{inner}', image], binary=True)

def image_file(image, inner, tools, scratch):
    kind = rv.fs_type(image)
    if kind == 'erofs':
        return kind, erofs_file(image, inner, tools['fsck_erofs'], scratch/(Path(image).stem + '.tree'))
    return kind, ext4_file(image, inner, tools['debugfs'])

def verify_identity(apk, app, inputs, tools):
    found = background_apks.identity(apk, tools['aapt'], tools['apksigner'], inputs['sdk'])
    expected = (app['package'], app['versionCode'], [inputs['appSignerSha256'].upper()])
    actual = (found['packageName'], found['versionCode'], found['certificates'])
    if actual != expected:
        raise Failed(f'{Path(apk).name}: package/version/signer {actual}, pinned {expected}')
    return found

def prepare(ota, verification, out, tools, config_path=CONFIG, run=subprocess.run, now=time.time):
    config = json.loads(Path(config_path).read_text())
    inputs = config['inputs']
    out = Path(out)
    if not out.is_absolute() or '..' in out.parts:
        raise Failed('--out must be an absolute path without ..')
    if os.path.lexists(out):
        raise Failed(f'{out} exists; choose a new inputs directory')
    if not out.parent.is_dir() or out.parent.is_symlink():
        raise Failed(f'{out.parent} must be an existing directory, not a link')
    tool_record = {}
    for name in TOOLS:
        path = regular(tools[name], f'--{name.replace("_", "-")}')
        if not os.access(path, os.X_OK):
            raise Failed(f'{path} is not executable')
        tool_record[name] = {'path': str(path), 'sha256': sha256_file(path)}
    record = {'receiptType': 'rango-local-inputs', 'schemaVersion': 1, 'release': inputs['release'],
        'product': config['product'], 'config': {'path': str(config_path), 'sha256': sha256_file(config_path)},
        'ota': pinned_file(ota, inputs['ota'], '--ota'),
        'verification': pinned_file(verification, inputs['verification'], '--verification'), 'tools': tool_record}
    record['provenance'] = provenance(verification, inputs)
    record['otaMetadata'] = check_archive(ota, inputs['ota']['device'])
    try:
        payload = rv.read_payload(ota)
    except rv.Fail as error:
        raise Failed(f'OTA payload: {error}')
    out.mkdir(mode=0o700)
    scratch, signed = out/'work', out/'signed-apps'
    scratch.mkdir()
    signed.mkdir()
    partitions, apps = {}, []
    for app in inputs['apps']:
        part, inner = app['imagePath'].split('/', 1)
        if part not in partitions:
            try:
                partitions[part] = rv.extract_partition(ota, payload, part, scratch/f'{part}.img')
            except rv.Fail as error:
                raise Failed(f'payload {part}: {error}')
        kind, data = image_file(scratch/f'{part}.img', inner, tool_record_paths(tool_record), scratch)
        target = signed/SIGNED_NAME.format(package=app['package'])
        with open(target, 'xb') as f:
            f.write(data)
        target.chmod(0o444)
        digest = hashlib.sha256(data).hexdigest()
        if sha256_file(target) != digest:
            raise Failed(f'{target.name} changed while being written')
        found = verify_identity(target, app, inputs, tool_record_paths(tool_record))
        apps.append({'role': app['role'], 'package': app['package'], 'versionCode': found['versionCode'],
            'signerSha256': inputs['appSignerSha256'], 'imagePath': app['imagePath'], 'filesystem': kind,
            'sha256': digest, 'sizeBytes': len(data), 'signedPath': str(target)})
    record['partitions'] = partitions
    record['apps'] = apps
    by_role = {a['role']: a['signedPath'] for a in apps}
    policy = out/'background-policy.json'
    argv = [sys.executable, str(SCRIPTS/'prepare-background-policy.py'), '--agent-apk', by_role['agent'],
        '--voice-apk', by_role['voice'], '--target-apk', by_role['target'],
        *(['--prototype-control'] if inputs['prototypeControl'] else []),
        '--aapt', tool_record['aapt']['path'], '--apksigner', tool_record['apksigner']['path'],
        '--sdk', str(inputs['sdk']), '--output', str(policy)]
    result = run(argv, capture_output=True, text=True)
    if result.returncode:
        raise Failed('prepare-background-policy.py failed: ' + (result.stderr or '').strip()[-300:])
    written = json.loads(policy.read_text())
    rows = {'agent': written['agent'], 'voice': written['voice']}
    for app in apps:
        row = rows.get(app['role']) or next((t for t in written['targets'] if t['packageName'] == app['package']), None)
        if row is None or (row['packageName'], row['versionCode'], row['apkSha256']) != (app['package'],
                app['versionCode'], app['sha256']):
            raise Failed(f'the policy does not pin the verified {app["package"]} bytes')
    if written.get('prototypeControl', False) != inputs['prototypeControl'] or written['sdk'] != inputs['sdk']:
        raise Failed('the policy prototypeControl or SDK differs from the pin')
    if len(written['targets']) != 1:
        raise Failed('the policy must name exactly the Calculator target')
    record['policy'] = {'path': str(policy), 'sha256': sha256_file(policy), 'sdk': written['sdk'],
        'prototypeControl': written.get('prototypeControl', False), 'command': argv[1:]}
    record['signedApps'] = str(signed)
    record['network'] = record['signed'] = record['published'] = False
    record['at'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(now()))
    release_receipts.write_once(out/'local-inputs.json', record)
    return record

def tool_record_paths(record):
    return {name: entry['path'] for name, entry in record.items()}

def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('command', choices=['prepare'])
    p.add_argument('--config', type=Path, default=CONFIG)
    p.add_argument('--ota', required=True)
    p.add_argument('--verification', required=True)
    p.add_argument('--out', required=True)
    for name in TOOLS:
        p.add_argument('--' + name.replace('_', '-'), dest=name, required=True)
    a = p.parse_args(argv)
    try:
        record = prepare(a.ota, a.verification, a.out, {name: getattr(a, name) for name in TOOLS}, a.config)
    except (Failed, rv.Fail, ValueError, OSError, subprocess.CalledProcessError, release_receipts.ReceiptExists) as error:
        print(json.dumps({'verdict': 'FAIL', 'error': str(error)}), file=sys.stderr)
        return 1
    print(json.dumps({'verdict': 'OK', 'receipt': str(Path(a.out)/'local-inputs.json'),
        'apps': {x['package']: x['sha256'] for x in record['apps']}, 'policySha256': record['policy']['sha256']}, indent=2))
    return 0

if __name__ == '__main__':
    sys.exit(main())
