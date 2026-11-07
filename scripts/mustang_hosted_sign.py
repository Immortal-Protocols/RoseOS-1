#!/usr/bin/env python3
"""Hosted Mustang signing gate. The guest builder never logs in and never signs.

Stock GitHub-hosted ubuntu-24.04 is the only signer job. Official public-repo
specification (docs.github.com/en/actions/reference/runners/github-hosted-runners):
4 vCPU, 16 GB RAM, 14 GB SSD. That 14 GB figure is the whole disk, not free
space. This module treats 14 GiB as the more generous reading of that label.
Actions artifact storage allowances (docs.github.com/en/billing/concepts/product-billing/github-actions)
are included amounts, not a per-artifact cap. Use past the allowance is billed
at $0.25 per GB-month and a budget or a missing payment method can block it.
This account's plan is not recorded here. A larger runner is not purchased or selected.

Peak bytes follow scripts/host-sign.py: the unsigned target-files and otatools
zips are kept and copy_fresh writes a second copy of each, then the release
script's signed target-files and OTA are copyfile'd to a second path. JDK,
otatools expansion and generate-release.sh are extra. They are not treated as
zero. If the measured copies already exceed the active runner disk, or admission
is closed, secret access stays closed. The entry point that may run is this checkout
file. A guest-supplied script is hashed and never executed.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
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
MEASURED_SIGN = ROOT / 'os/mustang-delivery/41738359336/sign.json'
CAPACITY = ROOT / 'os/build-only/mustang-hosted-signer-capacity.json'
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


def load_capacity(path=None):
    data = json.loads(Path(path or CAPACITY).read_text())
    if data.get('schema') != 'roseos.mustang-hosted-signer-capacity.v1' or data.get('trust') != 'held':
        raise ValueError('hosted signer capacity config is missing')
    runner = data.get('activeRunner')
    proposed = data.get('proposedRunner') or {}
    storage = data.get('storage') or {}
    if not isinstance(runner, dict) or not isinstance(runner.get('ssdBytes'), int) or isinstance(runner.get('ssdBytes'), bool) \
            or runner['ssdBytes'] <= 0 or data.get('admission') not in (True, False):
        raise ValueError('hosted signer capacity config is missing')
    if proposed.get('selected') is True or proposed.get('purchased') is True or proposed.get('configured') is True \
            or proposed.get('approved') is True or proposed.get('sizingProof') is True:
        raise ValueError('hosted signer capacity config is missing')
    if storage.get('accountPlan') is not None or storage.get('includedAllowancesAreNotPerArtifactLimits') is not True:
        raise ValueError('hosted signer capacity config is missing')
    return data


def capacity(bundle, free, config, measured=None):
    """Disk and admission only. Included artifact allowances are not a per-file cap."""
    measured = measured or measured_inputs()
    unsigned = bundle['unsigned']['sizeBytes']
    otatools = bundle['otatools']['sizeBytes']
    signed = projected_output(unsigned, measured['unsigned'], measured['signedTargetFiles'])
    ota = projected_output(unsigned, measured['unsigned'], measured['ota'])
    jdk = bundle['jdk']['sizeBytes']
    script = bundle['generateRelease']['sizeBytes']
    required = peak_bytes(unsigned, otatools, signed, ota, 0 if jdk is None else jdk, 0, 0 if script is None else script)
    stock = config['activeRunner']['ssdBytes']
    if not isinstance(free, int) or isinstance(free, bool) or free < 0:
        raise ValueError('free space is not a byte count')
    reasons = []
    if required > stock or required > free:
        reasons.append(
            f'hosted signer capacity exceeded: required {required} bytes, stock SSD {stock} bytes, free {free} bytes')
    if config.get('admission') is not True:
        reasons.append('hosted signer admission is closed')
    missing = [name for name, value in (('jdk', jdk), ('generateRelease', script)) if value is None]
    if bundle['jdk'].get('sha256') is None and 'jdk' not in missing:
        missing.append('jdk')
    if bundle['generateRelease'].get('sha256') is None and 'generateRelease' not in missing:
        missing.append('generateRelease')
    if missing:
        reasons.append('unmeasured signer inputs: ' + ','.join(missing))
    if reasons:
        error = CapacityExceeded('; '.join(reasons)) if reasons[0].startswith('hosted signer capacity') else ValueError('; '.join(reasons))
        raise error
    return {'requiredBytes': required, 'signedTargetFiles': signed, 'ota': ota}


def require_entry(root=ROOT):
    entry = Path(root) / ENTRY
    if entry.is_symlink() or not entry.is_file():
        raise ValueError('sign entry point is not the checkout file')
    resolved = entry.resolve()
    root_resolved = Path(root).resolve()
    if root_resolved not in resolved.parents or resolved.name != 'mustang_hosted_sign.py':
        raise ValueError('sign entry point left the checkout')


EXTRACT_MARKER = '.mustang-extract.json'


def load_pin(root, pin):
    if pin is None:
        pin = json.loads((Path(root) / SCRIPT_PIN).read_text())
    if pin.get('entryPoint') != ENTRY:
        raise ValueError('sign entry point is not the checkout script')
    return pin


def require_trusted_pins(bundle, root, files, pin=None):
    """Checkout pins are the authority. A guest-recorded hash is not."""
    pin = load_pin(root, pin)
    expected = pin.get('generateReleaseSha256')
    jdk = pin.get('jdkSha256')
    tools = pin.get('otatoolsSha256')
    if not isinstance(expected, str) or not HEX64.fullmatch(expected):
        raise ValueError('sign script pin is unbound')
    if not isinstance(jdk, str) or not HEX64.fullmatch(jdk):
        raise ValueError('jdk provenance is unbound')
    if not isinstance(tools, str) or not HEX64.fullmatch(tools):
        raise ValueError('otatools provenance is unbound')
    executable = pin.get('otatoolsExecutable')
    if not isinstance(executable, str) or not executable or executable.startswith(('/', '\\')) or '..' in Path(executable).parts:
        raise ValueError('otatools provenance is unbound')
    script = Path(root) / pin.get('generateReleasePath', 'script/generate-release.sh')
    if script.is_symlink() or not script.is_file() or Path(root).resolve() not in script.resolve().parents \
            or sha256_file(script) != expected or bundle['generateRelease']['sha256'] != expected \
            or sha256_file(files['generateRelease']) != expected:
        raise ValueError('generate-release.sh is not the pinned checkout script')
    if sha256_file(files['jdk']) != jdk or bundle['jdk']['sha256'] != jdk:
        raise ValueError('jdk provenance is not the trusted pin')
    if sha256_file(files['otatools']) != tools or bundle['otatools']['sha256'] != tools:
        raise ValueError('otatools provenance is not the trusted pin')
    return pin


def approved_mode(info):
    mode = info.external_attr >> 16
    if stat.S_ISLNK(mode) or mode & 0o7000:
        raise ValueError('unsafe zip mode')
    return 0o755 if mode & 0o111 else 0o644


def zip_members(path):
    seen = set()
    total = 0
    rows = []
    with zipfile.ZipFile(path) as archive:
        for info in archive.infolist():
            name = info.filename.replace('\\', '/')
            if EXTRACT_MARKER in Path(name).parts:
                raise ValueError('unsafe zip member')
            parts = [part for part in name.split('/') if part not in ('', '.')]
            if name.startswith('/') or '..' in parts or '\x00' in info.filename:
                raise ValueError('unsafe zip member')
            if not parts:
                continue
            key = '/'.join(parts)
            if key in seen:
                raise ValueError('duplicate zip member')
            seen.add(key)
            if name.endswith('/'):
                rows.append((key, None, 0, True, None))
                continue
            if stat.S_ISLNK(info.external_attr >> 16):
                raise ValueError('unsafe zip symlink')
            mode = approved_mode(info)
            total += info.file_size
            rows.append((key, mode, info.file_size, False, info))
    return total, rows


def reject_unsafe_zip(path, max_uncompressed):
    total, _rows = zip_members(path)
    if total > max_uncompressed:
        raise ValueError('zip expansion exceeds capacity')
    return total


OUTPUT_BOUNDS = (
    'signedTargetFilesBytes', 'otaBytes', 'signedTargetFilesCopyBytes', 'otaCopyBytes',
    'scratchBytes', 'reserveBytes')


def bound_values(config):
    bounds = config.get('outputBounds')
    if not isinstance(bounds, dict):
        return {}, list(OUTPUT_BOUNDS)
    missing, values = [], {}
    for name in OUTPUT_BOUNDS:
        value = bounds.get(name)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            missing.append(name)
        else:
            values[name] = value
    return values, missing


def current_peak_terms(unsigned, otatools_zip, otatools_uncompressed, jdk_zip, jdk_uncompressed, script, bounds):
    """Input copies, expansion, and explicit output/scratch/reserve bounds.

    A missing bound is not filled from the historical estimate.
    """
    terms = {
        'unsignedCopies': 2 * unsigned,
        'otatoolsCopies': 2 * otatools_zip,
        'otatoolsExpansion': otatools_uncompressed,
        'jdkArchive': jdk_zip,
        'jdkExpansion': jdk_uncompressed,
        'script': script,
        'signedTargetFiles': bounds['signedTargetFilesBytes'],
        'ota': bounds['otaBytes'],
        'signedTargetFilesCopy': bounds['signedTargetFilesCopyBytes'],
        'otaCopy': bounds['otaCopyBytes'],
        'scratch': bounds['scratchBytes'],
        'reserve': bounds['reserveBytes'],
    }
    if any(not isinstance(value, int) or isinstance(value, bool) or value < 0 for value in terms.values()):
        raise ValueError('size is not a byte count')
    return terms


def current_peak_bytes(unsigned, otatools_zip, otatools_uncompressed, jdk_zip, jdk_uncompressed, script, bounds):
    return sum(current_peak_terms(
        unsigned, otatools_zip, otatools_uncompressed, jdk_zip, jdk_uncompressed, script, bounds).values())


def _member_path(root, key):
    target = root.joinpath(*key.split('/'))
    if target.resolve() != root and root not in target.resolve().parents:
        raise ValueError('unsafe zip member')
    return target


def resume_extract(dest, archive_path, rows):
    """Every resumed file must match the trusted archive bytes, not only its mode."""
    root = Path(dest).resolve()
    digest = sha256_file(archive_path)
    marker_path = Path(dest) / EXTRACT_MARKER
    if marker_path.is_symlink() or not marker_path.is_file() or marker_path.resolve().parent != root:
        raise ValueError('extract marker does not match the trusted archive')
    try:
        marker = json.loads(marker_path.read_text())
    except (json.JSONDecodeError, OSError):
        raise ValueError('extract marker does not match the trusted archive')
    if marker.get('sha256') != digest or not isinstance(marker.get('members'), list):
        raise ValueError('extract marker does not match the trusted archive')
    expected = {}
    with zipfile.ZipFile(archive_path) as archive:
        for key, mode, size, is_dir, info in rows:
            if is_dir:
                continue
            if key in expected:
                raise ValueError('duplicate zip member')
            data = archive.read(info)
            if len(data) != size:
                raise ValueError('resumed file does not match the trusted archive')
            expected[key] = (hashlib.sha256(data).hexdigest(), len(data), mode)
    seen = []
    for file in root.rglob('*'):
        if file.is_symlink():
            raise ValueError('resumed file does not match the trusted archive')
        if file.is_dir():
            continue
        rel = file.relative_to(root).as_posix()
        if rel == EXTRACT_MARKER:
            continue
        if rel in seen:
            raise ValueError('duplicate zip member')
        seen.append(rel)
        if rel not in expected:
            raise ValueError('resumed file does not match the trusted archive')
        want_hash, want_size, want_mode = expected[rel]
        if file.stat().st_size != want_size or sha256_file(file) != want_hash \
                or stat.S_IMODE(file.stat().st_mode) != want_mode:
            raise ValueError('resumed file does not match the trusted archive')
    if set(seen) != set(expected):
        raise ValueError('resumed file does not match the trusted archive')
    return Path(dest)


def safe_extract(archive_path, dest, max_uncompressed):
    dest = Path(dest)
    digest = sha256_file(archive_path)
    total, rows = zip_members(archive_path)
    if total > max_uncompressed:
        raise ValueError('zip expansion exceeds capacity')
    if dest.is_symlink():
        raise ValueError('otatools extraction destination exists')
    if dest.exists():
        return resume_extract(dest, archive_path, rows)
    dest.mkdir(mode=0o700)
    root = dest.resolve()
    with zipfile.ZipFile(archive_path) as archive:
        for info in archive.infolist():
            name = info.filename.replace('\\', '/')
            parts = [part for part in name.split('/') if part not in ('', '.')]
            if not parts or name.endswith('/'):
                if parts and not name.endswith('/'):
                    continue
                if parts:
                    _member_path(root, '/'.join(parts)).mkdir(parents=True, exist_ok=True)
                continue
            target = _member_path(root, '/'.join(parts))
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as source, open(target, 'wb') as copied:
                shutil.copyfileobj(source, copied)
            os.chmod(target, approved_mode(info))
    recorded = [{'name': key, 'mode': mode} for key, mode, _size, is_dir, _info in rows if not is_dir]
    (dest / EXTRACT_MARKER).write_text(json.dumps({'sha256': digest, 'members': recorded}, sort_keys=True) + '\n')
    os.chmod(dest / EXTRACT_MARKER, 0o644)
    return dest


def require_java_home(jdk_root):
    java = Path(jdk_root) / 'bin' / 'java'
    if java.is_symlink() or not java.is_file() or Path(jdk_root).resolve() not in java.resolve().parents \
            or not os.access(java, os.X_OK):
        raise ValueError('jdk home has no runnable bin/java')
    return Path(jdk_root)


def require_otatools_executable(extracted, relative):
    tool = Path(extracted) / relative
    if tool.is_symlink() or not tool.is_file() or Path(extracted).resolve() not in tool.resolve().parents \
            or not os.access(tool, os.X_OK):
        raise ValueError('otatools executable provenance is not the trusted pin')
    return tool


def tree_bytes(path):
    total = 0
    for file in Path(path).rglob('*'):
        if file.is_symlink():
            raise ValueError('unsafe zip symlink')
        if file.is_file():
            total += file.stat().st_size
    return total


def verify_files(bundle, files):
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


def evaluate(bundle, root, free, files=None, secret_access=None, pin=None, capacity_config=None,
             env=None, measured=None):
    env = os.environ if env is None else env
    config = load_capacity() if capacity_config is None else capacity_config
    require_bundle_identity(bundle, env, root)
    require_entry(root)
    stock = config['activeRunner']['ssdBytes']
    historical = config.get('historicalLowerBoundBytes')
    _bound_values, missing_bounds = bound_values(config)
    early = []
    if missing_bounds:
        early.append('output bound is missing: ' + ','.join(missing_bounds))
    if config.get('admission') is not True:
        early.append('hosted signer admission is closed')
    missing = [name for name in ('jdk', 'generateRelease') if bundle[name].get('sha256') is None or bundle[name].get('sizeBytes') is None]
    if bundle['otatools'].get('sha256') is None:
        missing.append('otatools')
    if missing:
        early.append('unmeasured signer inputs: ' + ','.join(missing))
    needed = ('unsigned', 'otatools', 'generateRelease', 'jdk')
    if not isinstance(files, dict) or any(key not in files for key in needed):
        if early:
            raise CapacityExceeded('; '.join(early)) if early[0].startswith('hosted signer capacity') else ValueError('; '.join(early))
        raise ValueError('signing bundle files are absent')
    verify_files(bundle, files)
    for path in (files['otatools'], files['jdk'], files['unsigned']):
        reject_unsafe_zip(path, free)
    if missing_bounds:
        text = 'output bound is missing: ' + ','.join(missing_bounds)
        if config.get('admission') is not True:
            text += '; hosted signer admission is closed'
        raise ValueError(text)
    otatools_uncompressed, _rows = zip_members(files['otatools'])
    jdk_uncompressed, _jdk_rows = zip_members(files['jdk'])
    for label, declared, measured_size in (
            ('otatoolsExtractedBytes', bundle.get('otatoolsExtractedBytes'), otatools_uncompressed),
            ('jdkExtractedBytes', bundle.get('jdkExtractedBytes'), jdk_uncompressed)):
        if isinstance(declared, bool) or not isinstance(declared, int) or declared < measured_size:
            raise ValueError('declared expansion bound does not cover the archive')
    current = current_peak_bytes(
        files['unsigned'].stat().st_size, files['otatools'].stat().st_size, otatools_uncompressed,
        files['jdk'].stat().st_size, jdk_uncompressed, files['generateRelease'].stat().st_size, _bound_values)
    reasons = []
    if config.get('admission') is not True:
        reasons.append('hosted signer admission is closed')
    if current > stock or current > free:
        reasons.insert(0, f'hosted signer capacity exceeded: current peak {current} bytes, stock SSD {stock} bytes, free {free} bytes')
    if reasons:
        raise CapacityExceeded('; '.join(reasons)) if reasons[0].startswith('hosted signer capacity') else ValueError('; '.join(reasons))
    trusted = require_trusted_pins(bundle, root, files, pin)
    parent = Path(files['otatools']).parent
    extracted = safe_extract(files['otatools'], parent / 'otatools-extracted', free)
    jdk_home = safe_extract(files['jdk'], parent / 'jdk-extracted', free)
    require_java_home(jdk_home)
    require_otatools_executable(extracted, trusted['otatoolsExecutable'])
    if secret_access is not None:
        secret_access()
    return {'admit': True, 'requiredBytes': current, 'currentPeakBytes': current,
            'historicalEstimateBytes': historical, 'extracted': extracted, 'javaHome': jdk_home}


def _collected(row, name, dest, filename):
    if not isinstance(row, dict) or row.get('state') != 'collected':
        raise ValueError(f'signing bundle is missing {name}')
    path = Path(row['path'])
    if guest_secret_path(path):
        raise ValueError('signing material is refused on the guest')
    if path.is_symlink() or not path.is_file():
        raise ValueError(f'signing bundle file is missing for {name}')
    if path.stat().st_size != row.get('sizeBytes') or not HEX64.fullmatch(row.get('sha256') or ''):
        raise ValueError(f'signing bundle hash differs for {name}')
    target = dest / filename
    shutil.copyfile(path, target)
    if target.stat().st_size != row['sizeBytes'] or sha256_file(target) != row['sha256']:
        raise ValueError(f'signing bundle hash differs for {name}')
    return {'sha256': row['sha256'], 'sizeBytes': row['sizeBytes'], 'file': 'inputs/' + filename}


def export_bundle(collection_path, out_path, root=ROOT):
    if guest_secret_path(out_path) or guest_secret_path(collection_path):
        raise ValueError('signing material is refused on the guest')
    collection = read_json(collection_path)
    if collection.get('schema') != 'roseos.mustang-compile-collection.v1' or collection.get('status') != 'complete':
        raise ValueError('complete same-run collection required')
    out = Path(out_path)
    if out.exists() or out.is_symlink():
        raise ValueError('signing bundle output already exists')
    out.mkdir(mode=0o700)
    inputs = out / 'inputs'
    inputs.mkdir(mode=0o700)
    artifacts = collection['outputs']['artifacts']
    jdk = {'sha256': None, 'sizeBytes': None, 'file': None}
    script = {'sha256': None, 'sizeBytes': None, 'file': None}
    if isinstance(artifacts.get('jdk'), dict) and artifacts['jdk'].get('state') == 'collected':
        jdk = _collected(artifacts['jdk'], 'jdk', inputs, 'jdk.zip')
    script_path = collection.get('generateReleasePath')
    if isinstance(script_path, str):
        script_file = Path(script_path)
        if not guest_secret_path(script_file) and script_file.is_file() and not script_file.is_symlink():
            copied = _collected({'state': 'collected', 'path': str(script_file), 'sizeBytes': script_file.stat().st_size,
                                 'sha256': sha256_file(script_file)}, 'generateRelease', inputs, 'generate-release.sh')
            script = copied
    bundle = {
        'schema': BUNDLE_SCHEMA,
        'device': 'mustang',
        'runId': collection['runId'],
        'runAttempt': str(collection['runAttempt']),
        'workflow': WORKFLOW,
        'sourceSha': collection['collectorSource'],
        'build': collection.get('build'),
        'unsigned': _collected(artifacts.get('targetFiles'), 'unsigned', inputs, 'unsigned-target_files.zip'),
        'otatools': _collected(artifacts.get('otatools'), 'otatools', inputs, 'otatools.zip'),
        'jdk': jdk,
        'generateRelease': script,
        'otatoolsExtractedBytes': None,
        'jdkExtractedBytes': None,
        'identity': public_identity(root),
    }
    write_json(out / 'mustang-signing-bundle.json', bundle)
    return bundle


def bind_outputs(directory, env=None):
    env = os.environ if env is None else env
    directory = Path(directory)
    receipt_path = directory / 'mustang-hosted-sign-receipt.json'
    if receipt_path.is_symlink() or not receipt_path.is_file():
        raise ValueError('hosted signing receipt is missing')
    receipt = json.loads(receipt_path.read_text())
    if receipt.get('schema') != RECEIPT_SCHEMA or receipt.get('device') != 'mustang' \
            or receipt.get('signer') != SIGNER or receipt.get('secretVersion') != SECRET_VERSION:
        raise ValueError('hosted signing receipt is missing')
    if receipt.get('workflow') != WORKFLOW or receipt.get('runId') != env.get('GITHUB_RUN_ID') \
            or str(receipt.get('runAttempt')) != str(env.get('GITHUB_RUN_ATTEMPT')) \
            or receipt.get('sourceSha') != env.get('GITHUB_SHA') or not SHA40.fullmatch(receipt.get('sourceSha') or ''):
        raise ValueError('hosted signing receipt is not this run')
    outputs = receipt.get('outputs')
    if not isinstance(outputs, dict):
        raise ValueError('hosted signed output is missing')
    root = directory.resolve()
    for name in ('signedTargetFiles', 'ota'):
        row = outputs.get(name)
        filename = row.get('file') if isinstance(row, dict) else None
        if not isinstance(filename, str) or filename != Path(filename).name or filename in ('.', '..') \
                or not HEX64.fullmatch(row.get('sha256') or ''):
            raise ValueError('hosted signed output left the receipt directory')
        path = directory / filename
        if path.is_symlink() or not path.is_file() or path.resolve().parent != root \
                or path.stat().st_size != row.get('sizeBytes') or sha256_file(path) != row['sha256']:
            raise ValueError('hosted signed output hash differs' if path.is_file() and not path.is_symlink()
                             else 'hosted signed output left the receipt directory')
    return receipt


def bundle_document(path):
    path = Path(path)
    if path.is_dir():
        path = path / 'mustang-signing-bundle.json'
    bundle = read_json(path)
    return bundle, path.parent


def bundle_files(directory, bundle):
    files = {}
    for key in ('unsigned', 'otatools', 'jdk', 'generateRelease'):
        relative = (bundle.get(key) or {}).get('file')
        if not isinstance(relative, str):
            continue
        if relative.startswith(('/', '\\')) or '..' in Path(relative).parts:
            raise ValueError('signing bundle file left the bundle directory')
        path = directory / relative
        if path.is_symlink():
            raise ValueError('signing bundle file left the bundle directory')
        if path.is_file() and directory.resolve() in path.resolve().parents:
            files[key] = path
    return files or None


def declared_integer(bundle, name):
    value = bundle.get(name)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError('declared expansion bound is missing: ' + name)
    return value


def preliminary(bundle_path, root=ROOT, env=None, capacity_config=None, pin=None, free=None):
    """Metadata only. Declared sizes, pins, run identity, and admission. No payload bytes."""
    bundle, _directory = bundle_document(bundle_path)
    env = os.environ if env is None else env
    config = load_capacity() if capacity_config is None else capacity_config
    if free is None:
        space = os.statvfs('/')
        free = space.f_bavail * space.f_frsize
    require_bundle_identity(bundle, env, root)
    require_entry(root)
    loaded = load_pin(root, pin)
    reasons = []
    for field, key, missing in (
            ('generateReleaseSha256', 'generateRelease', 'sign script pin is unbound'),
            ('jdkSha256', 'jdk', 'jdk provenance is unbound'),
            ('otatoolsSha256', 'otatools', 'otatools provenance is unbound')):
        expected = loaded.get(field)
        if not isinstance(expected, str) or not HEX64.fullmatch(expected):
            reasons.append(missing)
        elif bundle[key].get('sha256') != expected:
            reasons.append(missing.replace(' is unbound', ' is not the trusted pin'))
    executable = loaded.get('otatoolsExecutable')
    if not isinstance(executable, str) or not executable or executable.startswith(('/', '\\')) or '..' in Path(executable).parts:
        if 'otatools provenance is unbound' not in reasons:
            reasons.append('otatools provenance is unbound')
    _values, missing_bounds = bound_values(config)
    if missing_bounds:
        reasons.append('output bound is missing: ' + ','.join(missing_bounds))
    declared = None
    if not missing_bounds:
        try:
            declared = current_peak_bytes(
                bundle['unsigned']['sizeBytes'], bundle['otatools']['sizeBytes'], declared_integer(bundle, 'otatoolsExtractedBytes'),
                bundle['jdk']['sizeBytes'], declared_integer(bundle, 'jdkExtractedBytes'), bundle['generateRelease']['sizeBytes'],
                _values)
        except ValueError as error:
            reasons.append(str(error))
    if declared is not None:
        stock = config['activeRunner']['ssdBytes']
        if declared > stock or declared > free:
            reasons.append(
                f'hosted signer capacity exceeded: current peak {declared} bytes, stock SSD {stock} bytes, free {free} bytes')
    if config.get('admission') is not True:
        reasons.append('hosted signer admission is closed')
    if reasons:
        raise ValueError('; '.join(reasons))
    return {'allow': True, 'declaredPeakBytes': declared}


def preflight(bundle_path, root=ROOT, env=None, capacity_config=None, pin=None, free=None):
    bundle, directory = bundle_document(bundle_path)
    if free is None:
        space = os.statvfs('/')
        free = space.f_bavail * space.f_frsize
    return evaluate(bundle, root, free, files=bundle_files(directory, bundle),
                    secret_access=None, pin=pin, env=env, capacity_config=capacity_config)


def run_host_sign(source_root, release_dir, extracted, jdk_home, bundle, env, host_sign_main=None):
    if host_sign_main is None:
        import host_sign as signer
        host_sign_main = signer.main
    release_dir = Path(release_dir)
    release_dir.mkdir(mode=0o700, exist_ok=True)
    config = {
        'target': {'device': 'mustang'},
        'host': {'sign': {
            'project': PROJECT,
            'keysSecret': KEYS,
            'passphraseSecret': PASSPHRASE,
            'secretVersion': SECRET_VERSION,
            'passphraseSecretVersion': SECRET_VERSION,
            'otatools': str(extracted),
            'javaHome': str(jdk_home),
            'updateNotes': str(Path(source_root) / 'script' / 'generate-release.sh'),
        }},
        'identities': {'otaCertificateSha256': OTA, 'avbPublicKeySha256': AVB},
        'publication': {'channels': {'beta': {'feedChannel': 'beta'}}},
    }
    config_path = release_dir / 'hosted-sign-config.json'
    config_path.write_text(json.dumps(config, sort_keys=True) + '\n')
    sign_env = dict(env)
    sign_env.update(ROSE_RELEASE_DIR=str(release_dir), ROSE_SIGN_OTATOOLS=str(extracted),
                    BUILD_NUMBER=str(bundle.get('build') or '0'), BUILD_DATETIME=str(bundle.get('timestamp') or '0'),
                    ROSE_CANDIDATE_SHA=bundle['sourceSha'])
    previous = Path.cwd()
    os.chdir(source_root)
    try:
        host_sign_main(config_path, env=sign_env, managed_decrypted_keys=True)
    finally:
        os.chdir(previous)
    return release_dir


def write_hosted_receipt(release_dir, out_dir, bundle):
    signed = Path(release_dir) / 'signed-target_files.zip'
    public = Path(release_dir) / 'public'
    otas = [path for path in public.glob('mustang-ota_update-*.zip') if path.is_file() and not path.is_symlink()] \
        if public.is_dir() else []
    if not signed.is_file() or signed.is_symlink() or len(otas) != 1:
        raise ValueError('hosted signed output is missing')
    out = Path(out_dir)
    out.mkdir(mode=0o700, exist_ok=True)
    copied = {}
    for source, name in ((signed, 'signed-target_files.zip'), (otas[0], otas[0].name)):
        target = out / name
        shutil.copyfile(source, target)
        copied[name] = target
    receipt = {
        'schema': RECEIPT_SCHEMA, 'device': 'mustang', 'signer': SIGNER, 'secretVersion': SECRET_VERSION,
        'runId': bundle['runId'], 'runAttempt': str(bundle['runAttempt']), 'sourceSha': bundle['sourceSha'],
        'workflow': WORKFLOW,
        'outputs': {
            'signedTargetFiles': {'file': 'signed-target_files.zip', 'sizeBytes': copied['signed-target_files.zip'].stat().st_size,
                                  'sha256': sha256_file(copied['signed-target_files.zip'])},
            'ota': {'file': otas[0].name, 'sizeBytes': copied[otas[0].name].stat().st_size,
                    'sha256': sha256_file(copied[otas[0].name])},
        },
    }
    write_json(out / 'mustang-hosted-sign-receipt.json', receipt)
    return receipt


def sign(bundle_path, out_dir, root=ROOT, env=None, capacity_config=None, host_sign_main=None, free=None, pin=None):
    bundle, directory = bundle_document(bundle_path)
    if free is None:
        space = os.statvfs('/')
        free = space.f_bavail * space.f_frsize
    files = bundle_files(directory, bundle)
    result = evaluate(bundle, root, free, files=files, secret_access=None, pin=pin, capacity_config=capacity_config, env=env)
    release = Path(out_dir).parent / 'hosted-sign-work'
    run_host_sign(root, release, result['extracted'], result['javaHome'], bundle, env or os.environ, host_sign_main)
    return write_hosted_receipt(release, out_dir, bundle)


def step_output(name, value):
    if os.environ.get('GITHUB_OUTPUT'):
        with open(os.environ['GITHUB_OUTPUT'], 'a') as handle:
            handle.write(name + '=' + value + '\n')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('export-bundle', 'preliminary', 'gate', 'preflight', 'sign', 'bind-outputs'))
    parser.add_argument('--collection')
    parser.add_argument('--out')
    parser.add_argument('--bundle')
    parser.add_argument('--dir')
    parser.add_argument('--capacity')
    parser.add_argument('--pin')
    parser.add_argument('--checkout')
    args = parser.parse_args(argv)
    root = Path(args.checkout) if args.checkout else ROOT
    capacity_config = load_capacity(args.capacity) if args.capacity else None
    pin = json.loads(Path(args.pin).read_text()) if args.pin else None
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
        if args.action == 'sign':
            if not args.out:
                raise ValueError('hosted signed output is missing')
            sign(args.bundle, args.out, root=root, capacity_config=capacity_config, pin=pin)
            return 0
        if args.action in ('preliminary', 'gate'):
            try:
                preliminary(args.bundle, root=root, capacity_config=capacity_config, pin=pin)
            except (ValueError, OSError, KeyError, TypeError, zipfile.BadZipFile) as error:
                message = str(error) if isinstance(error, ValueError) else type(error).__name__
                step_output('allow', 'false')
                print('Hosted Mustang signer stopped: ' + message, file=sys.stderr)
                return 4
            step_output('allow', 'true')
            return 0
        preflight(args.bundle, root=root, capacity_config=capacity_config, pin=pin)
        step_output('admit', 'true')
        return 0
    except (ValueError, OSError, KeyError, TypeError, zipfile.BadZipFile) as error:
        print('Hosted Mustang signer stopped: ' + (str(error) if isinstance(error, ValueError) else type(error).__name__),
              file=sys.stderr)
        return 4


if __name__ == '__main__':
    sys.exit(main())
