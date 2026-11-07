#!/usr/bin/env python3
"""Hosted Mustang signing gate. The guest builder never logs in and never signs.

Stock GitHub-hosted ubuntu-24.04 is the only signer job. Official public-repo
specification (docs.github.com/en/actions/reference/runners/github-hosted-runners):
4 vCPU, 16 GB RAM, 14 GB SSD. That 14 GB figure is the whole disk, not free
space. This module treats 14 GiB as the more generous reading of that label.
Actions artifact storage (docs.github.com/en/actions/reference/limits) is a plan
quota GitHub Support cannot raise: Free and Free for organizations 500 MB,
Pro 1 GB, Team 2 GB, Enterprise Cloud 50 GB. The stock check uses the Free
quota. A larger quota or a larger runner is a paid plan and is not purchased here.

Peak bytes follow scripts/host-sign.py: the unsigned target-files and otatools
zips are kept and copy_fresh writes a second copy of each, then the release
script's signed target-files and OTA are copyfile'd to a second path. JDK,
otatools expansion and generate-release.sh are extra. They are not treated as
zero. If the measured copies already exceed the disk or the artifact quota,
secret access stays closed. The entry point that may run is this checkout
file. A guest-supplied script is hashed and never executed.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = '.github/workflows/mustang-ota-dedicated.yml'
BUNDLE_SCHEMA = 'roseos.mustang-hosted-signing-bundle.v1'
RECEIPT_SCHEMA = 'roseos.mustang-hosted-sign-receipt.v1'
SIGNER = 'roseos-build-signer@protocols-465723.iam.gserviceaccount.com'
PROJECT = 'protocols-465723'
KEYS = 'PIXEL_OS_SIGNING_KEYS_MUSTANG_V1'
PASSPHRASE = 'PIXEL_OS_SIGNING_PASSPHRASE_MUSTANG_V1'
SECRET_VERSION = '1'
OTA = '93cc757e7f4e4e5c4ef7726fbeec9acc77b1f97d471c1cd0d62753cf94e471a8'
AVB = '608b39aac976984e45ddbc39fc9ebc916f066656d8d4316395c9c408e06dbf2b'
# Documented "14 GB" SSD. 14 GiB is larger than 14 * 10**9, so it is the ceiling
# that still rejects the measured payload. It is the whole disk, not free space.
STOCK_SSD_BYTES = 14 * 1024 ** 3
# Documented "500 MB" Free / Free-for-organizations artifact storage.
FREE_ARTIFACT_BYTES = 500 * 1000 * 1000
MEASURED_SIGN = ROOT / 'os/mustang-delivery/41738359336/sign.json'
SCRIPT_PIN = 'os/build-only/mustang-hosted-sign-script.json'
ENTRY = 'scripts/mustang_hosted_sign.py'
HEX64 = re.compile(r'^[0-9a-f]{64}$')
SHA40 = re.compile(r'^[0-9a-f]{40}$')


class CapacityExceeded(ValueError):
    pass


def guest_secret_path(path):
    """Refuse guest RAM and its resolved mount. /dev/shm is still the guest."""
    text = str(path)
    if text == '/dev/shm' or text.startswith('/dev/shm/'):
        return True
    resolved = Path(path).resolve()
    return any(resolved == root or root in resolved.parents for root in (Path('/dev/shm'), Path('/run/shm')))


def read_json(path):
    file = Path(path)
    if guest_secret_path(file):
        raise ValueError('signing material is refused on the guest')
    if file.is_symlink() or not file.is_file():
        raise ValueError('signing bundle file is missing')
    return json.loads(file.read_text())


def write_json(path, value):
    file = Path(path)
    if guest_secret_path(file):
        raise ValueError('signing material is refused on the guest')
    if file.exists() or file.is_symlink():
        raise ValueError('signing bundle output already exists')
    file.write_text(json.dumps(value, sort_keys=True) + '\n')
    os.chmod(file, 0o600)


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def public_identity(root=ROOT):
    receipt = json.loads((Path(root) / 'os/signing-bootstrap/mustang-bootstrap-receipt.json').read_text())
    secrets = receipt['secrets']
    public = receipt['publicIdentity']
    if receipt.get('project') != PROJECT or receipt.get('profile') != 'pro-mustang-v1':
        raise ValueError('Mustang v1 signing profile differs')
    if secrets['keys']['name'] != KEYS or secrets['passphrase']['name'] != PASSPHRASE:
        raise ValueError('Mustang v1 secret names differ')
    if secrets['keys']['committedVersion'] != SECRET_VERSION or secrets['passphrase']['committedVersion'] != SECRET_VERSION:
        raise ValueError('Mustang v1 secret versions differ')
    if public['otaCertificateSha256'] != OTA or public['avbPublicKeySha256'] != AVB:
        raise ValueError('Mustang v1 public identity differs')
    return {'otaCertificateSha256': OTA, 'avbPublicKeySha256': AVB, 'signer': SIGNER,
            'keysSecret': KEYS, 'passphraseSecret': PASSPHRASE, 'secretVersion': SECRET_VERSION}


def measured_inputs(path=None):
    """Byte sizes recorded on the historical sign receipt. Not a dedicated-disk measurement."""
    data = json.loads(Path(path or MEASURED_SIGN).read_text())
    rows = {
        'unsigned': data['compileArtifacts']['targetFiles']['sizeBytes'],
        'otatools': data['compileArtifacts']['otatools']['sizeBytes'],
        'signedTargetFiles': data['outputs']['targetFiles']['sizeBytes'],
        'ota': data['outputs']['ota']['sizeBytes'],
    }
    for name, size in rows.items():
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            raise ValueError(f'measured {name} size is not a byte count')
    return rows


def peak_bytes(unsigned, otatools, signed_target, ota, jdk=0, extracted=0, script=0):
    values = (unsigned, otatools, signed_target, ota, jdk, extracted, script)
    if any(not isinstance(value, int) or isinstance(value, bool) or value < 0 for value in values):
        raise ValueError('size is not a byte count')
    return 2 * unsigned + 2 * otatools + 2 * signed_target + 2 * ota + jdk + extracted + script


def projected_output(unsigned, measured_unsigned, measured_output):
    if not isinstance(unsigned, int) or isinstance(unsigned, bool) or unsigned < 0:
        raise ValueError('size is not a byte count')
    if measured_unsigned <= 0:
        raise ValueError('measured unsigned size is empty')
    return (unsigned * measured_output + measured_unsigned - 1) // measured_unsigned


def _size_row(row, name):
    if not isinstance(row, dict) or not isinstance(row.get('sizeBytes'), int) or isinstance(row.get('sizeBytes'), bool):
        raise ValueError(f'signing bundle is missing {name}')
    if row['sizeBytes'] < 0 or not HEX64.fullmatch(row.get('sha256') or ''):
        raise ValueError(f'signing bundle hash differs for {name}')
    return row


def require_bundle_identity(bundle, env, root=ROOT):
    if not isinstance(bundle, dict) or bundle.get('schema') != BUNDLE_SCHEMA or bundle.get('device') != 'mustang':
        raise ValueError('signing bundle is missing')
    if bundle.get('workflow') != WORKFLOW or bundle.get('runId') != env.get('GITHUB_RUN_ID') \
            or str(bundle.get('runAttempt')) != str(env.get('GITHUB_RUN_ATTEMPT')) \
            or bundle.get('sourceSha') != env.get('GITHUB_SHA') or not SHA40.fullmatch(bundle.get('sourceSha') or ''):
        raise ValueError('signing bundle is not this run')
    if bundle.get('identity') != public_identity(root):
        raise ValueError('Mustang v1 public identity differs')
    _size_row(bundle.get('unsigned'), 'unsigned')
    _size_row(bundle.get('otatools'), 'otatools')
    for name in ('jdk', 'generateRelease'):
        row = bundle.get(name)
        if not isinstance(row, dict) or 'sizeBytes' not in row or 'sha256' not in row:
            raise ValueError(f'signing bundle is missing {name}')
        if row['sizeBytes'] is not None:
            _size_row(row, name)
    if 'otatoolsExtractedBytes' not in bundle:
        raise ValueError('signing bundle is missing otatoolsExtractedBytes')
    extracted = bundle['otatoolsExtractedBytes']
    if extracted is not None and (not isinstance(extracted, int) or isinstance(extracted, bool) or extracted < 0):
        raise ValueError('size is not a byte count')


def capacity(bundle, free, stock_bytes=STOCK_SSD_BYTES, artifact_quota=FREE_ARTIFACT_BYTES, measured=None):
    measured = measured or measured_inputs()
    unsigned = bundle['unsigned']['sizeBytes']
    otatools = bundle['otatools']['sizeBytes']
    signed = projected_output(unsigned, measured['unsigned'], measured['signedTargetFiles'])
    ota = projected_output(unsigned, measured['unsigned'], measured['ota'])
    jdk = bundle['jdk']['sizeBytes']
    extracted = bundle['otatoolsExtractedBytes']
    script = bundle['generateRelease']['sizeBytes']
    required = peak_bytes(unsigned, otatools, signed, ota,
                          0 if jdk is None else jdk,
                          0 if extracted is None else extracted,
                          0 if script is None else script)
    offenders = [name for name, size in (('unsigned', unsigned), ('otatools', otatools),
                                         ('signedTargetFiles', signed), ('ota', ota)) if size > artifact_quota]
    if not isinstance(free, int) or isinstance(free, bool) or free < 0:
        raise ValueError('free space is not a byte count')
    if required > stock_bytes or required > free or offenders:
        raise CapacityExceeded(
            f'hosted signer capacity exceeded: required {required} bytes, stock SSD {stock_bytes} bytes, '
            f'free {free} bytes, artifact quota {artifact_quota} bytes, offenders {",".join(offenders) or "none"}')
    missing = [name for name, value in (('jdk', jdk), ('otatoolsExtractedBytes', extracted),
                                       ('generateRelease', script)) if value is None]
    if missing:
        raise ValueError('unmeasured signer inputs: ' + ','.join(missing))
    return {'requiredBytes': required, 'signedTargetFiles': signed, 'ota': ota}


def require_entry(root=ROOT):
    entry = Path(root) / ENTRY
    if entry.is_symlink() or not entry.is_file():
        raise ValueError('sign entry point is not the checkout file')
    resolved = entry.resolve()
    root_resolved = Path(root).resolve()
    if root_resolved not in resolved.parents or resolved.name != 'mustang_hosted_sign.py':
        raise ValueError('sign entry point left the checkout')


def require_script_pin(bundle, root=ROOT, pin=None):
    if pin is None:
        pin = json.loads((Path(root) / SCRIPT_PIN).read_text())
    if pin.get('entryPoint') != ENTRY:
        raise ValueError('sign entry point is not the checkout script')
    expected = pin.get('generateReleaseSha256')
    if not isinstance(expected, str) or not HEX64.fullmatch(expected):
        raise ValueError('sign script pin is unbound')
    if bundle['generateRelease']['sha256'] != expected:
        raise ValueError('generate-release.sh is not the pinned checkout script')


def reject_unsafe_zip(path, max_uncompressed):
    with zipfile.ZipFile(path) as archive:
        total = 0
        for info in archive.infolist():
            name = info.filename.replace('\\', '/')
            parts = [part for part in name.split('/') if part not in ('', '.')]
            if name.startswith('/') or '..' in parts or '\x00' in info.filename:
                raise ValueError('unsafe zip member')
            mode = info.external_attr >> 16
            if stat.S_ISLNK(mode):
                raise ValueError('unsafe zip symlink')
            total += info.file_size
            if total > max_uncompressed:
                raise ValueError('zip expansion exceeds capacity')


def verify_files(bundle, files, max_uncompressed):
    if not isinstance(files, dict):
        raise ValueError('signing bundle files are absent')
    for key in ('unsigned', 'otatools', 'generateRelease', 'jdk'):
        if key not in files:
            raise ValueError('signing bundle files are absent')
        path = Path(files[key])
        if path.is_symlink() or not path.is_file():
            raise ValueError('signing bundle file is missing')
        row = bundle[key]
        if path.stat().st_size != row['sizeBytes'] or sha256_file(path) != row['sha256']:
            raise ValueError('signing bundle hash differs')
        if key in ('unsigned', 'otatools'):
            reject_unsafe_zip(path, max_uncompressed)


def evaluate(bundle, root, free, files=None, secret_access=None, pin=None,
             stock_bytes=STOCK_SSD_BYTES, artifact_quota=FREE_ARTIFACT_BYTES, env=None, measured=None):
    env = os.environ if env is None else env
    require_bundle_identity(bundle, env, root)
    plan = capacity(bundle, free, stock_bytes, artifact_quota, measured)
    require_entry(root)
    require_script_pin(bundle, root, pin)
    if files is None:
        raise ValueError('signing bundle files are absent')
    verify_files(bundle, files, plan['requiredBytes'])
    if secret_access is not None:
        secret_access()
    return {'admit': True, 'requiredBytes': plan['requiredBytes']}


def _collected(row, name):
    if not isinstance(row, dict) or row.get('state') != 'collected':
        raise ValueError(f'signing bundle is missing {name}')
    path = Path(row['path'])
    if guest_secret_path(path):
        raise ValueError('signing material is refused on the guest')
    if path.is_symlink() or not path.is_file():
        raise ValueError(f'signing bundle file is missing for {name}')
    if path.stat().st_size != row.get('sizeBytes') or not HEX64.fullmatch(row.get('sha256') or ''):
        raise ValueError(f'signing bundle hash differs for {name}')
    return {'sha256': row['sha256'], 'sizeBytes': row['sizeBytes']}


def export_bundle(collection_path, out_path, root=ROOT):
    if guest_secret_path(out_path) or guest_secret_path(collection_path):
        raise ValueError('signing material is refused on the guest')
    collection = read_json(collection_path)
    if collection.get('schema') != 'roseos.mustang-compile-collection.v1' or collection.get('status') != 'complete':
        raise ValueError('complete same-run collection required')
    artifacts = collection['outputs']['artifacts']
    bundle = {
        'schema': BUNDLE_SCHEMA,
        'device': 'mustang',
        'runId': collection['runId'],
        'runAttempt': str(collection['runAttempt']),
        'workflow': WORKFLOW,
        'sourceSha': collection['collectorSource'],
        'unsigned': _collected(artifacts.get('targetFiles'), 'unsigned'),
        'otatools': _collected(artifacts.get('otatools'), 'otatools'),
        'jdk': {'sha256': None, 'sizeBytes': None},
        'generateRelease': {'sha256': None, 'sizeBytes': None},
        'otatoolsExtractedBytes': None,
        'identity': public_identity(root),
    }
    write_json(out_path, bundle)
    return bundle


def bind_outputs(directory):
    directory = Path(directory)
    receipt_path = directory / 'mustang-hosted-sign-receipt.json'
    if receipt_path.is_symlink() or not receipt_path.is_file():
        raise ValueError('hosted signing receipt is missing')
    receipt = json.loads(receipt_path.read_text())
    if receipt.get('schema') != RECEIPT_SCHEMA or receipt.get('device') != 'mustang' \
            or receipt.get('signer') != SIGNER or receipt.get('secretVersion') != SECRET_VERSION:
        raise ValueError('hosted signing receipt is missing')
    outputs = receipt.get('outputs')
    if not isinstance(outputs, dict):
        raise ValueError('hosted signed output is missing')
    for name in ('signedTargetFiles', 'ota'):
        row = outputs.get(name)
        if not isinstance(row, dict) or not HEX64.fullmatch(row.get('sha256') or ''):
            raise ValueError('hosted signed output is missing')
        path = directory / row['file']
        if path.is_symlink() or not path.is_file() or path.stat().st_size != row.get('sizeBytes') \
                or sha256_file(path) != row['sha256']:
            raise ValueError('hosted signed output hash differs')
    return receipt


def preflight(bundle_path, root=ROOT, env=None):
    bundle = read_json(bundle_path)
    space = os.statvfs(root if Path(root).is_dir() else '/')
    # Admission writes no output. A failed check must not look like admit=true.
    evaluate(bundle, root, space.f_bavail * space.f_frsize, files=None, secret_access=None, env=env)
    raise ValueError('signing bundle files are absent')


def sign(bundle_path, root=ROOT, env=None):
    bundle = read_json(bundle_path)
    space = os.statvfs(root if Path(root).is_dir() else '/')
    evaluate(bundle, root, space.f_bavail * space.f_frsize, files=None, secret_access=None, env=env)
    raise ValueError('pinned generate-release.sh is not executable from this checkout; no secret fetch')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('export-bundle', 'preflight', 'sign', 'bind-outputs'))
    parser.add_argument('--collection')
    parser.add_argument('--out')
    parser.add_argument('--bundle')
    parser.add_argument('--dir')
    args = parser.parse_args(argv)
    try:
        if args.action == 'export-bundle':
            if not args.collection or not args.out:
                raise ValueError('collection and out are required')
            export_bundle(args.collection, args.out)
            return 0
        if args.action == 'bind-outputs':
            if not args.dir:
                raise ValueError('hosted signing receipt is missing')
            bind_outputs(args.dir)
            return 0
        if not args.bundle:
            raise ValueError('signing bundle is missing')
        if args.action == 'preflight':
            preflight(args.bundle)
        else:
            sign(args.bundle)
        if os.environ.get('GITHUB_OUTPUT'):
            with open(os.environ['GITHUB_OUTPUT'], 'a') as handle:
                handle.write('admit=true\n')
        return 0
    except (ValueError, OSError, KeyError, TypeError, zipfile.BadZipFile) as error:
        print('Hosted Mustang signer stopped: ' + (str(error) if isinstance(error, ValueError) else type(error).__name__),
              file=sys.stderr)
        return 4


if __name__ == '__main__':
    sys.exit(main())
