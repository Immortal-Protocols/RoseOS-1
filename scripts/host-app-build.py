#!/usr/bin/env python3
"""os-release host.integrate.appBuildCommand: build the unsigned apps from the exact candidate checkout.

stage SOURCE_DIR DEST_DIR   copy only the five public connection inputs (verified with the candidate's
                            check-public-assets.mjs) into DEST_DIR with a nonsecret digest receipt
build CANDIDATE RELEASE_DIR restore those five inputs into the candidate, then with the stable SDK and JDK:
                            npm ci --ignore-scripts, the candidate's scripts/setup-zxing.sh when present,
                            both app build.sh scripts and the controller compile;
                            each new output is captured by before/after directory sets (exactly one each) and
                            copied to RELEASE_DIR/unsigned/{voice.apk,calculator.apk,controller/Agent-unsigned.apk}
build-voice CANDIDATE RELEASE_DIR
                            the same for Rose Agent only (app-only releases): RELEASE_DIR/unsigned/voice.apk
No keys are read and no loopback or default assets are invented.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

# Candidate-relative input paths that workspace/rose-agent/build.sh requires.
PUBLIC_ASSETS = ('workspace/rose-agent/app/assets/connection.json', 'workspace/rose-agent/app/assets/mac-screen-connection.json',
    'workspace/pixel-clipboard/clipboard-connection.json', 'workspace/rose-agent/app/res/raw/mac_screen_cert.pem',
    'workspace/rose-agent/app/res/xml/mac_screen_network.xml')
RECEIPT = 'public-assets-receipt.json'
ZXING_SETUP = 'scripts/setup-zxing.sh'  # candidate-relative; verifies .tools/zxing/core-3.5.4.jar by SHA-256

def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def check_assets(candidate, asset_dir):
    """The candidate's own checker over the three JSON connection files (https endpoint, pins, no extras)."""
    staging = Path(asset_dir)/'.check'
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir()
    try:
        for relative in PUBLIC_ASSETS:
            if relative.endswith('.json'):
                shutil.copyfile(Path(asset_dir)/relative, staging/Path(relative).name)
        subprocess.run(['node', str(Path(candidate)/'scripts/check-public-assets.mjs'), str(staging)], check=True,
            capture_output=True)
    finally:
        shutil.rmtree(staging, ignore_errors=True)

def stage(source, destination, candidate):
    source, destination = Path(source), Path(destination)
    if destination.exists():
        raise SystemExit(f'{destination} exists; stage into a new directory')
    missing = [r for r in PUBLIC_ASSETS if not (source/r).is_file() or (source/r).is_symlink()]
    if missing:
        raise SystemExit('missing public inputs: ' + ', '.join(missing))
    for relative in PUBLIC_ASSETS:
        (destination/relative).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source/relative, destination/relative)
    check_assets(candidate, destination)
    receipt = {'receiptType': 'public-connection-assets', 'files': {r: sha256(destination/r) for r in PUBLIC_ASSETS}}
    (destination/RECEIPT).write_text(json.dumps(receipt, indent=2, sort_keys=True) + '\n')
    return receipt

def restore(candidate, asset_dir, pinned=None):
    asset_dir = Path(asset_dir)
    receipt = json.loads((asset_dir/RECEIPT).read_text())['files']
    if set(receipt) != set(PUBLIC_ASSETS):
        raise SystemExit('public asset receipt does not list exactly the five inputs')
    for relative in PUBLIC_ASSETS:
        digest = sha256(asset_dir/relative)
        if digest != receipt[relative] or (pinned and pinned.get(relative) != digest):
            raise SystemExit(f'public input {relative} does not match its recorded digest')
    check_assets(candidate, asset_dir)
    for relative in PUBLIC_ASSETS:
        target = Path(candidate)/relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(asset_dir/relative, target)

def capture(directory, pattern, before):
    found = sorted(set(Path(directory).glob(pattern)) - before)
    if len(found) != 1:
        raise SystemExit(f'expected exactly one new {pattern}, found {len(found)}')
    return found[0]

def build(candidate, release_dir, sdk, java_home, asset_dir, pinned=None, run=subprocess.run, voice_only=False):
    candidate, release_dir, sdk = Path(candidate), Path(release_dir), Path(sdk)
    tools = sdk/'build-tools/android-15'
    needed = [sdk/'platforms/android-35/android.jar', tools/'core-lambda-stubs.jar', tools/'apksigner',
        *(tools/t for t in ('aapt', 'd8', 'zipalign')), Path(java_home)/'bin/java']
    absent = [str(p) for p in needed if not p.exists()]
    if absent:
        raise SystemExit('stable SDK layout incomplete: ' + ', '.join(absent))
    restore(candidate, asset_dir, pinned)
    env = dict(os.environ, ANDROID_SDK_ROOT=str(sdk), JAVA_HOME=str(java_home),
        PATH=f'{java_home}/bin:' + os.environ.get('PATH', ''))
    out = candidate/'out'
    out.mkdir(exist_ok=True)
    run(['npm', 'ci', '--ignore-scripts', '--no-audit', '--no-fund'], cwd=candidate/'workspace/rose-agent/server', env=env, check=True)
    if (candidate/ZXING_SETUP).is_file():
        # A fresh candidate checkout has no .tools: its build.sh needs the ZXing jar its own hash-pinned setup fetches.
        run(['bash', ZXING_SETUP], cwd=candidate, env=env, check=True)
    before = set(out.glob('rose-agent.*/rose-agent-unsigned.apk'))
    run(['bash', 'workspace/rose-agent/build.sh'], cwd=candidate, env=env, check=True)
    voice = capture(out, 'rose-agent.*/rose-agent-unsigned.apk', before)
    if voice_only:
        (release_dir/'unsigned').mkdir(parents=True)
        shutil.copyfile(voice, release_dir/'unsigned/voice.apk')
        return {'voice': sha256(release_dir/'unsigned/voice.apk')}
    before = set(out.glob('calculator.*/calculator-unsigned.apk'))
    run(['bash', 'workspace/pixel-calculator-agent/build.sh'], cwd=candidate, env=env, check=True)
    calculator = capture(out, 'calculator.*/calculator-unsigned.apk', before)
    unsigned = release_dir/'unsigned'
    (unsigned/'controller').mkdir(parents=True)
    run([sys.executable, str(candidate/'scripts/build-background-agent.py'), '--android-jar', str(sdk/'platforms/android-35/android.jar'),
        '--build-tools', str(tools), '--out', str(unsigned/'controller'), '--viewer', 'pip'], cwd=candidate, env=env, check=True)
    if not (unsigned/'controller/Agent-unsigned.apk').is_file():
        raise SystemExit('controller compile did not produce Agent-unsigned.apk')
    shutil.copyfile(voice, unsigned/'voice.apk')
    shutil.copyfile(calculator, unsigned/'calculator.apk')
    return {'voice': sha256(unsigned/'voice.apk'), 'calculator': sha256(unsigned/'calculator.apk'),
        'controller': sha256(unsigned/'controller/Agent-unsigned.apk')}

if __name__ == '__main__':
    config = json.loads((Path(__file__).resolve().parents[1]/'os/release-pipeline/frankel.json').read_text())
    apps = config['host']['integrate']
    if sys.argv[1:2] == ['stage'] and len(sys.argv) == 5:
        print(json.dumps(stage(sys.argv[2], sys.argv[3], sys.argv[4]), indent=2, sort_keys=True))
    elif sys.argv[1:2] in (['build'], ['build-voice']) and len(sys.argv) == 4:
        print(json.dumps(build(sys.argv[2], sys.argv[3], apps['appSdkRoot'], apps['javaHome'], apps['publicAssetsDir'],
            apps.get('publicAssetDigests'), voice_only=sys.argv[1] == 'build-voice'), indent=2, sort_keys=True))
    else:
        raise SystemExit('usage: host-app-build.py stage SOURCE DEST CANDIDATE | build|build-voice CANDIDATE RELEASE_DIR')
