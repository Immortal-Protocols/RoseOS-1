#!/usr/bin/env python3
"""os-release app-only mode: build, sign and verify Rose Agent alone from the exact candidate commit.

Run on the builder by .github/workflows/os-release.yml (mode app-only) with ROSE_CANDIDATE_SHA, ROSE_RELEASE_DIR and
APPS (exactly [["dev.pixel.voice", N]]) set, and the job's short-lived signer identity active:
1. check out the candidate as a worktree of this trusted checkout and build ONLY Rose Agent with the stable SDK and
   the staged public connection inputs (host-app-build.py build-voice);
2. sign it with scripts/sign-apk.py and the four pinned PIXEL_APP_SIGNING_* versions, read into owner-only RAM and
   removed on success or failure (the same helpers as the OS integrate adapter);
3. verify the signed APK independently: package dev.pixel.voice, the dispatched versionCode, a versionCode above
   appRelease.mustExceed (the published Rose Agent) and exactly the pinned app signer;
4. write to the artifact directory only the signed APK, a nonsecret receipt and SHA256SUMS;
5. remove the candidate worktree and every temporary input under ROSE_RELEASE_DIR, on success or failure.
It holds the builder's shared source/output lock (host.lockPath) for the whole run, so it never overlaps an OS stage.
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
sys.path.insert(0, str(Path(__file__).resolve().parent))
import host_secrets  # noqa: E402
from host_secrets import AdapterFailed, AdapterSetupMissing  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SECRETS = ('keystore', 'config', 'storePassword', 'keyPassword')
PACKAGE = 'dev.pixel.voice'

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

def requested_version(config, apps):
    if len(apps) != 1 or apps[0][0] != PACKAGE or type(apps[0][1]) is not int:
        raise AdapterFailed(f'app-only mode releases exactly [["{PACKAGE}", versionCode]]')
    version, floor = apps[0][1], config['appRelease']['mustExceed']
    if version <= floor:
        raise AdapterFailed(f'{PACKAGE} versionCode {version} must exceed {floor}, the newest built or published')
    return version

def main(artifact_dir, config_path=ROOT/'os/release-pipeline/frankel.json', env=os.environ, gcloud='gcloud', build=None):
    config = json.loads(Path(config_path).read_text())
    missing = missing_inputs(config)
    if missing:
        raise AdapterSetupMissing(missing)
    tools = config['host']['tools']
    sha, work = env['ROSE_CANDIDATE_SHA'], Path(env['ROSE_RELEASE_DIR'])
    version = requested_version(config, json.loads(env.get('APPS', '[]')))
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
    artifact.mkdir(parents=True)
    shutil.copyfile(signed, artifact/signed.name)
    receipt = {'receiptType': 'app-release', 'simulated': False, 'candidate': sha, 'packageName': PACKAGE, 'versionCode': version,
        'versionName': version_name, 'apk': signed.name, 'size': (artifact/signed.name).stat().st_size,
        'sha256': sha256(artifact/signed.name), 'signerSha256': config['identities']['appSignerSha256'].lower(),
        'minimumTestedOs': config['appRelease']['minimumTestedOs'],
        'checks': ['candidate worktree at the selected commit', 'Rose Agent built alone', 'unsigned package and versionCode',
            f'versionCode above {config["appRelease"]["mustExceed"]} (newest built or published)', 'signed with the pinned app signer (sign-apk.py)',
            'independent apksigner verification: exactly the pinned signer', 'signed package and versionCode']}
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
