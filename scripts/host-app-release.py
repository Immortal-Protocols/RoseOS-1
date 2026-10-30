#!/usr/bin/env python3
"""os-release app-only mode: build, sign and verify Rose Agent alone from the exact candidate commit.

Run on the builder by .github/workflows/os-release.yml (mode app-only) with ROSE_CANDIDATE_SHA, ROSE_RELEASE_DIR and
APPS (exactly [["dev.pixel.voice", N]]) set, and the job's short-lived signer identity active:
1. check out the candidate as a worktree of this trusted checkout and build ONLY Rose Agent with the stable SDK and
   the staged public connection inputs (host-app-build.py build-voice);
2. sign it with scripts/sign-apk.py and the four pinned PIXEL_APP_SIGNING_* versions, read into owner-only RAM and
   removed on success or failure (the same helpers as the OS integrate adapter);
3. verify the signed APK independently: package dev.pixel.voice, the dispatched versionCode, a versionCode above
   appRelease.mustExceed (the newest built or published Rose Agent) and exactly the pinned app signer. A version in
   appRelease.reserved is accepted only for the app-only route and its candidate (any candidate when none is recorded);
   any other must exceed every reservation. The receipt also records the signed APK's minSdk (equal to the source's)
   and native-library ABIs, from which the public receipt's compatibility is derived;
4. write to the artifact directory only the signed APK, a nonsecret receipt and SHA256SUMS;
5. remove the candidate worktree and every temporary input under ROSE_RELEASE_DIR, on success or failure.
It holds the builder's shared source/output lock (host.lockPath) for the whole run, so it never overlaps an OS stage.
Before any worktree, build, signing or temporary input, it checks the space available to this user (statvfs f_bavail, not
free or total) on each filesystem it writes to (the trusted checkout, ROSE_RELEASE_DIR and the artifact directory): each
must keep RESERVE_BYTES (300 GiB) after APP_WORKSPACE_ALLOWANCE, or the run stops and changes nothing. A path whose
filesystem cannot be established fails closed. Provider disk size is never taken as filesystem headroom.
It never reconciles or builds the OS, never reads the OS signing secrets, and never builds or signs the controller or
the Calculator. Missing configuration fails closed (exit 4) naming the key.
"""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import importlib.util
import zipfile
sys.path.insert(0, str(Path(__file__).resolve().parent))
import host_secrets  # noqa: E402
from host_secrets import AdapterFailed, AdapterSetupMissing  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SECRETS = ('keystore', 'config', 'storePassword', 'keyPassword')
PACKAGE = 'dev.pixel.voice'
SOURCE_MANIFEST = 'workspace/rose-agent/app/AndroidManifest.xml'
RESERVE_BYTES = 322122547200  # 300 GiB that must stay available on the builder's filesystems
APP_WORKSPACE_ALLOWANCE = 16 * 1024**3  # bound on one attempt's growth: candidate worktree, build, APKs and inputs

def load(name):
    spec = importlib.util.spec_from_file_location(name.replace('-', '_'), ROOT/'scripts'/f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def host_path(config, value):
    return Path(value) if Path(value).is_absolute() else Path(config['host']['sourceRoot'])/value

def missing_inputs(config):
    integrate, release = config['host'].get('integrate') or {}, config.get('appRelease') or {}
    missing = [f'host.integrate.{k}' for k in ('project', 'secretVersion', 'appSdkRoot', 'javaHome', 'publicAssetsDir')
        if not integrate.get(k)]
    missing += [f'host.integrate.appSecrets.{k}' for k in SECRETS if not (integrate.get('appSecrets') or {}).get(k)]
    missing += [f'host.tools.{k}' for k in ('aapt', 'apksigner') if not config['host']['tools'].get(k)]
    return missing + [f'appRelease.{k}' for k in ('mustExceed', 'minimumTestedOs') if not release.get(k)]

def badging(aapt, apk):
    out = subprocess.run([aapt, 'dump', 'badging', str(apk)], capture_output=True, text=True).stdout
    match = re.search(r"^package: name='([^']+)' versionCode='(\d+)' versionName='([^']*)'", out, re.M)
    if not match:
        raise AdapterFailed(f'no package identity in {Path(apk).name}')
    return match[1], int(match[2]), match[3]

def signers(apksigner, apk):
    result = subprocess.run([apksigner, 'verify', '--print-certs', str(apk)], capture_output=True, text=True)
    if result.returncode:
        raise AdapterFailed(f'{Path(apk).name} does not verify')
    return re.findall(r'certificate SHA-256 digest: ([0-9a-f]{64})', result.stdout)

def requested_version(config, apps, sha=None):
    if len(apps) != 1 or apps[0][0] != PACKAGE or type(apps[0][1]) is not int:
        raise AdapterFailed(f'app-only mode releases exactly [["{PACKAGE}", versionCode]]')
    version, floor = apps[0][1], config['appRelease']['mustExceed']
    if version <= floor:
        raise AdapterFailed(f'{PACKAGE} versionCode {version} must exceed {floor}, the newest built or published')
    # A reserved version belongs to one route and candidate; anything newer must pass every reservation.
    reserved = config['appRelease'].get('reserved', [])
    row = next((r for r in reserved if r['versionCode'] == version), None)
    if row and (row['route'] != 'app-only' or row['candidate'] not in (None, sha)):
        raise AdapterFailed(f'{PACKAGE} versionCode {version} is reserved for the {row["route"]} route'
            + (f' and candidate {row["candidate"]}' if row['candidate'] else ''))
    if not row and reserved and version <= max(r['versionCode'] for r in reserved):
        raise AdapterFailed(f'{PACKAGE} versionCode {version} must exceed the reserved {max(r["versionCode"] for r in reserved)}')
    return version

def allocated_floor(home):
    """OCT30-INFRA-S014: the newest Rose Agent the latest-beta button reserved on this builder (never committed to main).
    An app-only version must exceed it, so one version never names two different APKs."""
    directory = Path(home)/'rose-actions/allocations'
    if not directory.exists():
        return 0
    newest = 0
    for path in sorted(directory.iterdir()):
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(fd) as f:
                record = json.load(f)
            newest = max([newest] + [int(v) for p, v in record['apps'] if p == PACKAGE])
        except (OSError, ValueError, KeyError, TypeError):
            raise AdapterFailed(f'unreadable latest-beta reservation {path.name[:40]}; app versions cannot be checked') from None
    return newest

def apk_facts(aapt, apk, candidate):
    """What the public receipt's compatibility derives from the signed APK itself: its minSdk (aapt sdkVersion, which
    must equal the candidate source's uses-sdk minSdkVersion) and the ABIs of its native libraries (lib/<abi>/*.so,
    which must agree with aapt's native-code line; none means the APK runs on any ABI)."""
    out = subprocess.run([aapt, 'dump', 'badging', str(apk)], capture_output=True, text=True).stdout
    built = re.search(r"^sdkVersion:'(\d+)'$", out, re.M)
    source = re.search(r'<uses-sdk\b[^>]*android:minSdkVersion="(\d+)"', (Path(candidate)/SOURCE_MANIFEST).read_text())
    if not built or not source or built[1] != source[1]:
        raise AdapterFailed(f'signed APK minSdk {built and built[1]} does not match the source minSdkVersion {source and source[1]}')
    with zipfile.ZipFile(apk) as archive:
        abis = sorted({n.split('/')[1] for n in archive.namelist() if re.fullmatch(r'lib/[^/]+/.+\.so', n)})
    declared = re.search(r"^native-code: (.*)$", out, re.M)
    if sorted(re.findall(r"'([^']+)'", declared[1]) if declared else []) != abis:
        raise AdapterFailed(f'native libraries {abis} do not match aapt native-code {declared and declared[1]}')
    return {'minSdk': int(built[1]), 'nativeAbis': abis}

def nearest_existing(path):
    path = Path(path).absolute()
    while not path.exists():
        if path.parent == path:
            raise FileNotFoundError(str(path))
        path = path.parent
    return path

def disk_preflight(paths, statvfs=os.statvfs, stat=os.stat):
    """Fail unless every filesystem behind paths ({label: path}) has RESERVE_BYTES + APP_WORKSPACE_ALLOWANCE available
    to this user. A path not created yet is measured on its nearest existing ancestor; each filesystem is counted once."""
    needed, seen = RESERVE_BYTES + APP_WORKSPACE_ALLOWANCE, set()
    for label, path in paths.items():
        try:
            base = nearest_existing(path)
            device = stat(base).st_dev
            if device in seen:
                continue
            fs = statvfs(base)
        except OSError as error:
            raise AdapterFailed(f'available space for the {label} ({path}) could not be established: {error.__class__.__name__}')
        seen.add(device)
        if fs.f_frsize <= 0:
            raise AdapterFailed(f'available space for the {label} ({path}) could not be established: no fragment size')
        available = fs.f_bavail * fs.f_frsize
        if available < needed:
            raise AdapterFailed(f'the {label} filesystem has {available} bytes available; {needed} are required '
                f'({RESERVE_BYTES} reserve plus {APP_WORKSPACE_ALLOWANCE} app workspace)')

def main(artifact_dir, config_path=ROOT/'os/release-pipeline/frankel.json', env=os.environ, gcloud='gcloud', build=None,
        statvfs=os.statvfs, stat=os.stat):
    config = json.loads(Path(config_path).read_text())
    missing = missing_inputs(config)
    if missing:
        raise AdapterSetupMissing(missing)
    tools = config['host']['tools']
    sha, work = env['ROSE_CANDIDATE_SHA'], Path(env['ROSE_RELEASE_DIR'])
    version = requested_version(config, json.loads(env.get('APPS', '[]')), sha)
    floor = allocated_floor(env.get('HOME', ''))
    if version <= floor:
        raise AdapterFailed(f'{PACKAGE} versionCode {version} must exceed {floor}, reserved by a latest-beta candidate on this builder')
    aapt, apksigner = (str(host_path(config, tools[k])) for k in ('aapt', 'apksigner'))
    artifact = Path(artifact_dir)
    if artifact.exists():
        raise AdapterFailed(f'{artifact} already exists; use a new artifact directory')
    if artifact.resolve().is_relative_to(work.resolve()):
        raise AdapterFailed('the artifact directory must be outside ROSE_RELEASE_DIR, which is removed afterwards')
    lock = open(config['host']['lockPath'], 'a')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock.close()
        raise AdapterFailed('the source/output lock is held by another job')
    try:  # before any worktree, build, secret or temporary input; nothing to clean up yet
        disk_preflight({'source checkout': ROOT, 'release workspace': work, 'artifact directory': artifact}, statvfs, stat)
    except BaseException:
        lock.close()
        raise
    try:
        return release(config, sha, work, version, aapt, apksigner, artifact, env, gcloud, build)
    except BaseException:
        shutil.rmtree(artifact, ignore_errors=True)  # never leave a partial artifact to upload
        raise
    finally:
        subprocess.run(['git', '-C', str(ROOT), 'worktree', 'remove', '--force', str(work/'candidate')], capture_output=True)
        subprocess.run(['git', '-C', str(ROOT), 'worktree', 'prune'], capture_output=True)
        shutil.rmtree(work, ignore_errors=True)
        lock.close()

def release(config, sha, work, version, aapt, apksigner, artifact, env, gcloud, build):
    integrate = config['host']['integrate']
    candidate = work/'candidate'
    subprocess.run(['git', '-C', str(ROOT), 'worktree', 'add', '--detach', str(candidate), sha], check=True, stdout=subprocess.DEVNULL)
    if subprocess.run(['git', '-C', str(candidate), 'rev-parse', 'HEAD'], capture_output=True, text=True).stdout.strip() != sha:
        raise AdapterFailed('candidate worktree is not at the selected commit')
    (build or load('host-app-build').build)(candidate, work, integrate['appSdkRoot'], integrate['javaHome'],
        integrate['publicAssetsDir'], integrate.get('publicAssetDigests'), voice_only=True)
    unsigned = work/'unsigned/voice.apk'
    if badging(aapt, unsigned)[:2] != (PACKAGE, version):
        raise AdapterFailed(f'the candidate built {badging(aapt, unsigned)[:2]}, expected {PACKAGE} {version}')
    signed = work/f'{PACKAGE}-{version}.apk'
    with host_secrets.private_directory('rose-app-signing-') as private:
        files = {k: host_secrets.fetch_secret(integrate['project'], integrate['appSecrets'][k], integrate['secretVersion'],
            private/k, gcloud) for k in SECRETS}
        result = subprocess.run([sys.executable, str(ROOT/'scripts/sign-apk.py'), str(unsigned), '--keystore', str(files['keystore']),
            '--config', str(files['config']), '--store-password-file', str(files['storePassword']),
            '--key-password-file', str(files['keyPassword']), '--output', str(signed),
            '--expected-cert-sha256', config['identities']['appSignerSha256']],
            env=dict(env, ANDROID_SDK_ROOT=integrate['appSdkRoot']), stdout=subprocess.DEVNULL)
        if result.returncode:
            raise AdapterFailed(f'sign-apk.py exited {result.returncode}')
    # Independent checks of the signed bytes, not of the signer's own report.
    package, signed_version, version_name = badging(aapt, signed)
    if (package, signed_version) != (PACKAGE, version):
        raise AdapterFailed(f'signed APK is {package} {signed_version}, expected {PACKAGE} {version}')
    if signers(apksigner, signed) != [config['identities']['appSignerSha256'].lower()]:
        raise AdapterFailed('signed APK is not signed by exactly the pinned app signer')
    facts = apk_facts(aapt, signed, candidate)
    artifact.mkdir(parents=True)
    shutil.copyfile(signed, artifact/signed.name)
    receipt = {'receiptType': 'app-release', 'simulated': False, 'candidate': sha, 'packageName': PACKAGE, 'versionCode': version,
        'versionName': version_name, 'apk': signed.name, 'size': (artifact/signed.name).stat().st_size,
        'sha256': sha256(artifact/signed.name), 'signerSha256': config['identities']['appSignerSha256'].lower(),
        'minimumTestedOs': config['appRelease']['minimumTestedOs'], 'apkFacts': facts,
        'checks': ['candidate worktree at the selected commit', 'Rose Agent built alone', 'unsigned package and versionCode',
            f'versionCode above {config["appRelease"]["mustExceed"]} (newest built or published)', 'signed with the pinned app signer (sign-apk.py)',
            'independent apksigner verification: exactly the pinned signer', 'signed package and versionCode',
            'minSdk from the signed APK equal to the source', 'native-library ABIs from the signed APK']}
    (artifact/'app-release-receipt.json').write_text(json.dumps(receipt, indent=2, sort_keys=True) + '\n')
    (artifact/'SHA256SUMS').write_text(''.join(f'{sha256(artifact/n)}  {n}\n' for n in sorted(p.name for p in artifact.iterdir())))
    return receipt

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--artifact-dir', type=Path)
    parser.add_argument('--check', action='store_true', help='Only report missing configuration')
    a = parser.parse_args()
    try:
        if a.check:
            missing = missing_inputs(json.loads((ROOT/'os/release-pipeline/frankel.json').read_text()))
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
