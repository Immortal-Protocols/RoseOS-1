#!/usr/bin/env python3
"""os-release app-reuse mode: hand the exact signed Rose Agent an OS candidate bundled to the app publisher (INFRA-APP-118).

Run on the builder by .github/workflows/os-release.yml (mode app-reuse) with CANDIDATE_SHA, BUILD_NUMBER, BUILD_TIMESTAMP,
APPS (exactly [["dev.pixel.voice", N]]) and REUSE_BINDING set. Nothing is built or signed and no secret is read: the
APK is the one host-integrate.py signed for the OS candidate and kept at
$HOME/rose-actions/frankel-<build>/release/signed-apps/dev.pixel.voice.apk. Before copying it, under the builder's
shared source/output lock (host.lockPath), this checks without writing anything in the original attempt:

- N is the appRelease.reserved row for the os-release route with this exact candidate, osBuild and buildTimestamp,
  and above appRelease.mustExceed; the candidate's own AndroidManifest.xml declares versionCode N;
- the attempt's stage receipt chain (release-stage.py) is the original attempt of this candidate and build number,
  unbroken, with build, bytes-verify, sign, ota-verify and transfer completed with exit code 0;
- unsigned-target_files.zip and signed-target_files.zip have the dispatched digests; the signed one is the one the
  signing receipt and the passing, non-simulated verification receipt name for this candidate, build and timestamp;
- background-policy.json pins dev.pixel.voice to the dispatched APK digest and the pinned signer, and both target-files
  carry exactly those bytes at PRODUCT/app/RosePreinstall_dev_pixel_voice/ marked PRESIGNED;
- the retained APK has the dispatched digest, aapt reads package dev.pixel.voice versionCode N with the source's
  minSdk, and apksigner, run again here on these bytes, prints exactly the pinned app signer.

Only that APK, a nonsecret receipt (the app-release receipt with a `source` block naming the OS build, run and
digests) and SHA256SUMS are written to the new artifact directory. REUSE_BINDING is the lead's record of the original
run: {"sourceRun": id, "apkSha256", "unsignedTargetFilesSha256", "signedTargetFilesSha256"}. The builder keeps no run
id, so the run is bound through those digests, which come from that run's own output. A resumed attempt keeps its
signed apps in the original directory and is refused. Missing configuration fails closed (exit 4) naming the key.
"""
import argparse
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import zipfile
sys.path.insert(0, str(Path(__file__).resolve().parent))
from host_secrets import AdapterFailed, AdapterSetupMissing  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = 'dev.pixel.voice'
SOURCE_MANIFEST = 'workspace/rose-agent/app/AndroidManifest.xml'
TARGET_PREFIX = 'PRODUCT/app/RosePreinstall_dev_pixel_voice/'
COMPLETED = ('build', 'bytes-verify', 'sign', 'ota-verify', 'transfer')
BINDING_KEYS = ('sourceRun', 'apkSha256', 'unsignedTargetFilesSha256', 'signedTargetFilesSha256')
HEX64 = re.compile(r'[0-9a-f]{64}')

def load(name):
    spec = importlib.util.spec_from_file_location(name.replace('-', '_'), ROOT/'scripts'/f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def sha256(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()

def missing_inputs(config):
    missing = [f'host.tools.{k}' for k in ('aapt', 'apksigner') if not config['host']['tools'].get(k)]
    missing += [f'host.{k}' for k in ('lockPath', 'sourceRoot') if not config['host'].get(k)]
    return missing + [f'appRelease.{k}' for k in ('mustExceed', 'minimumTestedOs', 'reserved') if not (config.get('appRelease') or {}).get(k)]

def parse_binding(text):
    """The lead's record of the original run, exactly BINDING_KEYS: a positive run id and three lowercase digests."""
    try:
        binding = json.loads(text)
    except ValueError:
        raise AdapterFailed('REUSE_BINDING is not JSON')
    if not isinstance(binding, dict) or sorted(binding) != sorted(BINDING_KEYS):
        raise AdapterFailed(f'REUSE_BINDING must hold exactly {list(BINDING_KEYS)}')
    if type(binding['sourceRun']) is not int or binding['sourceRun'] < 1:
        raise AdapterFailed('REUSE_BINDING.sourceRun must be a positive run id')
    bad = [k for k in BINDING_KEYS[1:] if not isinstance(binding[k], str) or not HEX64.fullmatch(binding[k])]
    if bad:
        raise AdapterFailed(f'REUSE_BINDING {bad} must be lowercase sha256 digests')
    return binding

def reserved_row(config, sha, build, timestamp, apps):
    """The os-release reservation this exact candidate, build and timestamp bundled; never an app-only or other version."""
    release = config['appRelease']
    if len(apps) != 1 or apps[0][0] != PACKAGE or type(apps[0][1]) is not int:
        raise AdapterFailed(f'app-reuse publishes exactly [["{PACKAGE}", versionCode]]')
    version = apps[0][1]
    if version <= release['mustExceed']:
        raise AdapterFailed(f'{PACKAGE} versionCode {version} must exceed {release["mustExceed"]}, the newest built or published')
    row = next((r for r in release['reserved'] if r['versionCode'] == version), None)
    if not row or row.get('route') != 'os-release':
        raise AdapterFailed(f'{PACKAGE} versionCode {version} is not reserved for an OS candidate; only an OS-bundled version is reused')
    expected = {'candidate': sha, 'osBuild': build, 'buildTimestamp': int(timestamp)}
    wrong = [k for k, v in expected.items() if row.get(k) != v]
    if wrong:
        raise AdapterFailed(f'{PACKAGE} versionCode {version} is reserved for another OS candidate (differs in {wrong})')
    return version, row

def source_manifest(sha):
    result = subprocess.run(['git', '-C', str(ROOT), 'show', f'{sha}:{SOURCE_MANIFEST}'], capture_output=True, text=True)
    if result.returncode:
        raise AdapterFailed('the candidate commit is not in this checkout')
    return result.stdout

def attempt_chain(base, sha, build):
    """The original attempt's validated receipt chain: this candidate and build, every OS stage through transfer done."""
    stage = load('release-stage')
    try:
        chain, _ = stage.load_chain(base/'stages')
        phase, _, done = stage.replay(chain)
    except stage.StateError as error:
        raise AdapterFailed(f'the attempt receipt chain is not valid: {error}')
    candidate = chain[0]['candidate']
    if 'resumedFrom' in chain[0]:
        raise AdapterFailed('the attempt is a resumed attempt; only the original attempt keeps the signed apps')
    if (candidate.get('sourceRevision'), candidate.get('device'), candidate.get('buildNumber')) != (sha, 'frankel', int(build)):
        raise AdapterFailed('the attempt receipt chain belongs to another candidate or build')
    if done[:len(COMPLETED)] != list(COMPLETED) or phase in ('failed', 'abandoned', 'stage-failed', 'started'):
        raise AdapterFailed(f'the attempt has not completed {list(COMPLETED)} (completed {done}, phase {phase})')
    return chain

def preinstalled(target_files, digest):
    """Exactly one Rose Agent APK in the target-files preinstall directory, with these bytes and marked PRESIGNED."""
    with zipfile.ZipFile(target_files) as archive:
        apks = [n for n in archive.namelist() if n.startswith(TARGET_PREFIX) and n.endswith('.apk')]
        if len(apks) != 1:
            raise AdapterFailed(f'{target_files.name} holds {len(apks)} Rose Agent preinstall APKs, not one')
        if hashlib.sha256(archive.read(apks[0])).hexdigest() != digest:
            raise AdapterFailed(f'{target_files.name} preinstalls other Rose Agent bytes than the retained APK')
        certs = archive.read('META/apkcerts.txt').decode(errors='replace') if 'META/apkcerts.txt' in archive.namelist() else ''
        if f'name="{Path(apks[0]).name}" certificate="PRESIGNED"' not in certs:
            raise AdapterFailed(f'{target_files.name} does not mark the Rose Agent preinstall PRESIGNED')
    return apks[0]

def main(artifact_dir, config_path=ROOT/'os/release-pipeline/frankel.json', env=os.environ):
    config = json.loads(Path(config_path).read_text())
    missing = missing_inputs(config)
    if missing:
        raise AdapterSetupMissing(missing)
    sha, build, timestamp = env['CANDIDATE_SHA'], env['BUILD_NUMBER'], env['BUILD_TIMESTAMP']
    if not re.fullmatch(r'[0-9a-f]{40}', sha) or not re.fullmatch(r'\d{10}', build) or not re.fullmatch(r'\d{10}', timestamp):
        raise AdapterFailed('a full candidate SHA, ten-digit build number and ten-digit BUILD_DATETIME are required')
    binding = parse_binding(env.get('REUSE_BINDING', ''))
    version, row = reserved_row(config, sha, build, timestamp, json.loads(env.get('APPS', '[]')))
    base = Path(env.get('ROSE_ATTEMPTS', Path.home()/'rose-actions'))/f'frankel-{build}'
    artifact = Path(artifact_dir)
    if artifact.exists():
        raise AdapterFailed(f'{artifact} already exists; use a new artifact directory')
    if artifact.resolve().is_relative_to(base.resolve()):
        raise AdapterFailed('the artifact directory must be outside the original attempt, which is only read')
    tools = config['host']['tools']
    host = lambda v: str(Path(v) if Path(v).is_absolute() else Path(config['host']['sourceRoot'])/v)
    aapt, apksigner = host(tools['aapt']), host(tools['apksigner'])
    lock = open(config['host']['lockPath'], 'a')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock.close()
        raise AdapterFailed('the source/output lock is held by another job')
    try:
        return reuse(config, sha, build, timestamp, version, row, binding, base, artifact, aapt, apksigner)
    except BaseException:
        shutil.rmtree(artifact, ignore_errors=True)  # never leave a partial artifact to upload
        raise
    finally:
        lock.close()

def reuse(config, sha, build, timestamp, version, row, binding, base, artifact, aapt, apksigner):
    signer = config['identities']['appSignerSha256'].lower()
    release = base/'release'
    attempt_chain(base, sha, build)
    unsigned, signed = release/'unsigned-target_files.zip', release/'signed-target_files.zip'
    if sha256(unsigned) != binding['unsignedTargetFilesSha256']:
        raise AdapterFailed('unsigned-target_files.zip differs from the dispatched unsignedTargetFilesSha256')
    signed_digest = sha256(signed)
    if signed_digest != binding['signedTargetFilesSha256']:
        raise AdapterFailed('signed-target_files.zip differs from the dispatched signedTargetFilesSha256')
    signing = json.loads((release/'sign-receipt.json').read_text())
    if (signing.get('receiptType'), signing.get('candidate'), signing.get('build'), signing.get('signedTargetFilesSha256')) \
            != ('os-signing', sha, build, signed_digest):
        raise AdapterFailed('the signing receipt does not name this candidate, build and signed target-files')
    verification = json.loads((release/'public/verification.json').read_text())
    candidate = verification.get('candidate') or {}
    if (verification.get('receiptType'), verification.get('simulated'), verification.get('signedTargetFilesSha256'),
            candidate.get('sha'), candidate.get('build'), candidate.get('timestamp')) \
            != ('candidate-verification', False, signed_digest, sha, build, timestamp):
        raise AdapterFailed('no passing, non-simulated verification receipt for this candidate, build, timestamp and signed target-files')
    apk = release/'signed-apps'/f'{PACKAGE}.apk'
    digest = sha256(apk)
    if digest != binding['apkSha256']:
        raise AdapterFailed('the retained signed APK differs from the dispatched apkSha256')
    voice = json.loads((release/'background-policy.json').read_text()).get('voice') or {}
    if voice.get('packageName') != PACKAGE or str(voice.get('apkSha256', '')).lower() != digest \
            or [c.lower() for c in voice.get('certificates', [])] != [signer]:
        raise AdapterFailed('the background policy does not pin the retained APK and the app signer for Rose Agent')
    paths = {name: preinstalled(path, digest) for name, path in (('unsigned', unsigned), ('signed', signed))}
    tools = load('host-app-release')
    package, built_version, version_name = tools.badging(aapt, apk)
    if (package, built_version) != (PACKAGE, version) or version_name != row['versionName']:
        raise AdapterFailed(f'the retained APK is {package} {built_version} {version_name!r}, expected {PACKAGE} {version} {row["versionName"]!r}')
    if tools.signers(apksigner, apk) != [signer]:
        raise AdapterFailed('the retained APK is not signed by exactly the pinned app signer')
    manifest = source_manifest(sha)
    declared = re.search(r'android:versionCode="(\d+)"', manifest)
    if not declared or int(declared[1]) != version:
        raise AdapterFailed(f'the candidate source declares versionCode {declared and declared[1]}, not {version}')
    facts = apk_facts(aapt, apk, manifest)
    artifact.mkdir(parents=True)
    name = f'{PACKAGE}-{version}.apk'
    shutil.copyfile(apk, artifact/name)
    if sha256(artifact/name) != digest:
        raise AdapterFailed('the copied APK differs from the retained bytes')
    receipt = {'receiptType': 'app-release', 'simulated': False, 'candidate': sha, 'packageName': PACKAGE, 'versionCode': version,
        'versionName': version_name, 'apk': name, 'size': (artifact/name).stat().st_size, 'sha256': digest, 'signerSha256': signer,
        'minimumTestedOs': config['appRelease']['minimumTestedOs'], 'apkFacts': facts,
        'source': {'route': 'os-release-reuse', 'osBuild': build, 'buildTimestamp': int(timestamp), 'sourceRun': binding['sourceRun'],
            'unsignedTargetFilesSha256': binding['unsignedTargetFilesSha256'], 'signedTargetFilesSha256': signed_digest,
            'targetFilesPath': paths['signed']},
        'checks': ['reserved os-release version for this candidate, build and timestamp', 'candidate source declares the versionCode',
            'original attempt receipt chain complete through transfer', 'unsigned and signed target-files digests as dispatched',
            'signing receipt and passing verification receipt name the signed target-files',
            'background policy pins the retained APK and the app signer',
            'unsigned and signed target-files preinstall these bytes PRESIGNED', 'retained APK package, versionCode and versionName',
            'independent apksigner verification of the retained bytes: exactly the pinned signer',
            'minSdk from the retained APK equal to the source', 'native-library ABIs from the retained APK',
            'not rebuilt or re-signed: the OS candidate\'s signed bytes']}
    (artifact/'app-release-receipt.json').write_text(json.dumps(receipt, indent=2, sort_keys=True) + '\n')
    (artifact/'SHA256SUMS').write_text(''.join(f'{sha256(artifact/n)}  {n}\n' for n in sorted(p.name for p in artifact.iterdir())))
    return receipt

def apk_facts(aapt, apk, manifest):
    """minSdk and native-library ABIs measured from the retained APK, as host-app-release.py records them."""
    out = subprocess.run([aapt, 'dump', 'badging', str(apk)], capture_output=True, text=True).stdout
    built = re.search(r"^sdkVersion:'(\d+)'$", out, re.M)
    source = re.search(r'<uses-sdk\b[^>]*android:minSdkVersion="(\d+)"', manifest)
    if not built or not source or built[1] != source[1]:
        raise AdapterFailed(f'retained APK minSdk {built and built[1]} does not match the source minSdkVersion {source and source[1]}')
    with zipfile.ZipFile(apk) as archive:
        abis = sorted({n.split('/')[1] for n in archive.namelist() if re.fullmatch(r'lib/[^/]+/.+\.so', n)})
    declared = re.search(r"^native-code: (.*)$", out, re.M)
    if sorted(re.findall(r"'([^']+)'", declared[1]) if declared else []) != abis:
        raise AdapterFailed(f'native libraries {abis} do not match aapt native-code {declared and declared[1]}')
    return {'minSdk': int(built[1]), 'nativeAbis': abis}

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--artifact-dir', type=Path)
    parser.add_argument('--check', action='store_true', help='Only report missing configuration')
    a = parser.parse_args()
    try:
        config = json.loads((ROOT/'os/release-pipeline/frankel.json').read_text())
        if a.check:
            missing = missing_inputs(config)
            if missing:
                raise AdapterSetupMissing(missing)
            sys.exit(0)
        if not a.artifact_dir:
            parser.error('--artifact-dir is required')
        print(json.dumps(main(a.artifact_dir), indent=2, sort_keys=True))
    except AdapterSetupMissing as error:
        print(json.dumps({'verdict': 'SETUP_MISSING', 'missing': error.missing}), file=sys.stderr)
        sys.exit(4)
    except (AdapterFailed, OSError, KeyError, ValueError, subprocess.CalledProcessError) as error:
        print(json.dumps({'verdict': 'FAIL', 'error': str(error) if isinstance(error, AdapterFailed) else error.__class__.__name__}),
            file=sys.stderr)
        sys.exit(1)
