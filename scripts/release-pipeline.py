#!/usr/bin/env python3
"""Job steps for .github/workflows/os-release.yml: preflight, builder control, build, verification,
artifact staging and publication, behind fake and production adapters.

The production adapter fails closed: a missing runner, identity, publisher credential, host entry
point or tool stops the job and names the missing input. Verification reads the actual target-files,
OTA and public files, runs the real OTA and AVB signature tools and the APK byte/signer gate; claimed
metadata never counts as success. The fake adapter only swaps external edges (builder control, the
make step and the release store) and marks every receipt simulated; production refuses those.
Exit status: 0 ok, 1 check failed, 2 invalid input, 4 setup missing.
"""
import argparse
import base64
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import zipfile
sys.path.insert(0, str(Path(__file__).resolve().parent))
import release_receipts  # noqa: E402
import github_release_publisher  # noqa: E402
from release_receipts import sha256_file  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT/'os/release-pipeline/frankel.json'
TARGET_FILES_VERIFIER = ROOT/'qa/first-update/runtime/verify-target-files.py'
SUPPORTED_PUBLISHERS = ('github-app',)  # pixel-ota-only App; token minted in the hosted publisher job.
BUILDER = {'project': 'protocols-465723', 'zone': 'us-west2-a', 'instance': 'immortal-os-builder'}
PROPS = ('SYSTEM/build.prop', 'VENDOR/build.prop', 'PRODUCT/etc/build.prop', 'SYSTEM_EXT/etc/build.prop')

class CheckFailed(Exception):
    pass

class InvalidInput(Exception):
    pass

class SetupMissing(Exception):
    def __init__(self, missing):
        super().__init__('setup missing: ' + ', '.join(missing))
        self.missing = missing

def load_config(path):
    try:
        config = json.loads(Path(path).read_text())
    except (OSError, ValueError) as error:
        raise InvalidInput(f'cannot read pipeline config: {error.__class__.__name__}')
    if config.get('schemaVersion') != 1 or not isinstance(config.get('host'), dict):
        raise InvalidInput('unsupported pipeline config')
    return config

def candidate(a):
    if not re.fullmatch(r'[0-9a-f]{40}', a.candidate_sha or ''):
        raise InvalidInput('--candidate-sha must be a full commit SHA')
    if not re.fullmatch(r'\d{10}', a.build_number or ''):
        raise InvalidInput('--build-number must be ten digits')
    if not re.fullmatch(r'\d{10}', a.build_timestamp or ''):
        raise InvalidInput('--build-timestamp must be a ten-digit UTC epoch')
    if a.channel not in ('beta', 'prod'):
        raise InvalidInput('--channel must be beta or prod')
    return {'sha': a.candidate_sha, 'build': a.build_number, 'timestamp': a.build_timestamp, 'channel': a.channel}

def public_assets(device, build):
    return [f'{device}-ota_update-{build}.zip', f'{device}-stable', 'releasekey.x509.pem', 'avb_pkmd.bin',
        'SHA256SUMS', 'UPDATE.md', 'verification.json']

def host_path(config, value):
    return Path(value) if Path(value).is_absolute() else Path(config['host']['sourceRoot'])/value

# ---------------------------------------------------------------- preflight

def preflight(job, config, env, adapter):
    """Names of everything this job needs that is absent. Values are never printed."""
    missing = []
    if adapter == 'production' and config.get('fixture'):
        raise InvalidInput('a fixture config cannot be used for production')
    if adapter == 'fake':
        return missing
    if job in ('controller', 'cleanup'):
        missing += [n for n in ('GCP_WORKLOAD_IDENTITY_PROVIDER', 'GCP_CONTROLLER_SERVICE_ACCOUNT') if not env.get(n)]
    if job == 'guard':
        if env.get('ROSEOS_BUILDER_RUNNER_READY') != 'true':
            missing.append('ROSEOS_BUILDER_RUNNER_READY')
        missing += [n for n in ('GCP_WORKLOAD_IDENTITY_PROVIDER', 'GCP_CONTROLLER_SERVICE_ACCOUNT',
            'GCP_SIGNER_SERVICE_ACCOUNT') if not env.get(n)]
        missing += publisher_inputs(env)
        missing += adapter_inputs(config)
        missing += [f'host.tools.{k}' for k, v in config['host']['tools'].items() if not v]
    if job == 'builder':
        if not env.get('GCP_SIGNER_SERVICE_ACCOUNT'):
            missing.append('GCP_SIGNER_SERVICE_ACCOUNT')
        host = config['host']
        missing += adapter_inputs(config)
        missing += [f'host.tools.{k}' for k, v in host['tools'].items() if not v or not host_path(config, v).exists()]
        if not Path(host['sourceRoot']).is_dir():
            missing.append('host.sourceRoot (not present on this runner)')
        if not Path(host['lockPath']).parent.is_dir():
            missing.append('host.lockPath directory')
    if job == 'publisher':
        missing += publisher_inputs(env)
    return list(dict.fromkeys(missing))

def publisher_inputs(env):
    # Presence only: the workflow passes 'true'/'false' for the secret, never its value.
    if env.get('PIXEL_OTA_PUBLISHER') not in SUPPORTED_PUBLISHERS:
        return ['PIXEL_OTA_PUBLISHER (must be github-app)']
    return [n for n, ok in (('PIXEL_OTA_APP_ID', bool(env.get('PIXEL_OTA_APP_ID'))),
        ('PIXEL_OTA_APP_PRIVATE_KEY', env.get('PIXEL_OTA_APP_KEY_PRESENT') == 'true')) if not ok]

# ---------------------------------------------------------------- adapters

class Production:
    simulated = False

    def control_builder(self, action):
        subprocess.run(['gcloud', 'compute', 'instances', action, BUILDER['instance'], '--zone', BUILDER['zone'],
            '--project', BUILDER['project'], '--quiet'], check=True)

    def make(self, config, cand, env):
        host = config['host']
        steps = ['source build/envsetup.sh', f'lunch {host_token(config["target"]["lunch"])}']
        if host.get('installclean'):
            # Regenerates installed metadata (including AVB fingerprints) after a build-number change.
            steps.append('m installclean')
        steps.append(f'm -j{int(host["makeJobs"])} ' + ' '.join(host_token(g) for g in host['makeGoals']))
        subprocess.run(['bash', '-euo', 'pipefail', '-c', ' && '.join(steps)], cwd=host['sourceRoot'], env=env, check=True)

    def publish(self, config, cand, directory, receipt_dir):
        if not os.environ.get('PIXEL_OTA_TOKEN'):
            raise SetupMissing(['PIXEL_OTA_TOKEN (minted by the publisher job; never on the builder)'])
        self.client = github_release_publisher.client_from_environment(config)
        tag = config['publication']['tag'].format(build=cand['build'])
        run = os.environ.get('GITHUB_SERVER_URL', '') + '/' + os.environ.get('GITHUB_REPOSITORY', '') + \
            '/actions/runs/' + os.environ.get('GITHUB_RUN_ID', '')
        body = (f'RoseOS {config["target"]["device"]} build {cand["build"]} from RoseOS-1 {cand["sha"]}, '
            f'channel {cand["channel"]}. Verified and published by {run}.')
        manifest = json.loads((Path(receipt_dir)/'manifest.json').read_text())
        self.release, download, self.already_published = github_release_publisher.publish_release(
            self.client, tag, tag, body, manifest, directory, Path(receipt_dir).parent/f'published-{cand["build"]}')
        return download

    def promote(self, config, cand, draft):
        if self.already_published:
            return
        promoted = self.client.promote(self.release['id'])
        if promoted.get('draft') is not False:
            raise CheckFailed('release is still a draft after promotion')

class Fake:
    """Swaps only external edges; everything it produces is marked simulated."""
    simulated = True

    def __init__(self, state):
        self.state = Path(state)
        self.state.mkdir(parents=True, exist_ok=True)

    def log(self, event):
        with open(self.state/'events.log', 'a') as f:
            f.write(event + '\n')

    def control_builder(self, action):
        self.log(f'builder {action}')

    def make(self, config, cand, env):
        self.log('make')
        subprocess.run(config['host']['fakeMake'], env=env, check=True)

    def publish(self, config, cand, directory, receipt_dir):
        store = self.state/'releases'/config['publication']['tag'].format(build=cand['build'])
        if store.exists():
            raise CheckFailed('release tag already exists in the fake store')
        draft = store/'draft'
        shutil.copytree(directory, draft)
        self.log('draft uploaded')
        return draft

    def promote(self, config, cand, draft):
        draft.rename(draft.parent/'latest')
        self.log('promoted latest')

def host_token(value):
    if not re.fullmatch(r'[A-Za-z0-9_.-]+', str(value)):
        raise InvalidInput(f'unsafe build token in config: {value!r}')
    return value

def adapter_for(a):
    return Fake(a.fake_state) if a.adapter == 'fake' else Production()

# ---------------------------------------------------------------- build

def stage_env(config, cand, release_dir):
    env = dict(os.environ, ROSE_CANDIDATE_SHA=cand['sha'], ROSE_CHANNEL=cand['channel'],
        BUILD_NUMBER=cand['build'], BUILD_DATETIME=cand['timestamp'], OUT_DIR=config['host']['outDir'],
        ROSE_RELEASE_DIR=str(release_dir))
    return env

def entry_point(argv):
    """argv lists only, never a shell string; {python} and {checkout} become this trusted checkout's absolute paths."""
    if not isinstance(argv, list) or not argv or not all(isinstance(x, str) for x in argv):
        raise InvalidInput('host entry points must be argv lists')
    return [x.replace('{python}', sys.executable).replace('{checkout}', str(ROOT)) for x in argv]

def run_entry_point(argv, env, cwd):
    subprocess.run(entry_point(argv), env=env, cwd=cwd, check=True)

def adapter_inputs(config):
    """Recipe keys the checked-in host adapters still need (their --check mode)."""
    missing = []
    for key in ('integrateCommand', 'signCommand'):
        argv = config['host'].get(key)
        if not argv:
            missing.append(f'host.{key}')
            continue
        argv = entry_point(argv)
        if len(argv) == 2 and argv[1].startswith(str(ROOT/'scripts')+'/host-'):
            result = subprocess.run([*argv, '--check'], capture_output=True, text=True)
            if result.returncode == 4:
                missing += json.loads(result.stderr)['missing']
            elif result.returncode:
                missing.append(f'host.{key} (adapter check failed)')
    return missing

def drop_signer_access():
    """Remove the per-job gcloud config (actions-gcp-login.py) as soon as an entry point returns."""
    directory = os.environ.get('CLOUDSDK_CONFIG')
    runner_temp = os.environ.get('RUNNER_TEMP')
    if directory and runner_temp and Path(directory) == Path(runner_temp)/'gcloud-job-config' and Path(directory).exists():
        shutil.rmtree(directory)

def build(a, config, cand, adapter):
    host = config['host']
    if not host.get('integrateCommand'):
        raise SetupMissing(['host.integrateCommand'])
    release_dir = Path(a.release_dir)
    release_dir.mkdir(parents=True, exist_ok=True)
    lock = open(host['lockPath'], 'a')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise CheckFailed('the source/output lock is held by another job')
    try:
        env = stage_env(config, cand, release_dir)
        # Lead-managed entry point: reconcile the exact candidate into the retained tree and write the
        # resolved manifest and the background policy receipt into the release directory.
        try:
            run_entry_point(host['integrateCommand'], env, host['sourceRoot'])
        finally:
            drop_signer_access()
        for name in ('resolved-manifest.xml', 'background-policy.json'):
            if not (release_dir/name).is_file():
                raise CheckFailed(f'integration did not produce $ROSE_RELEASE_DIR/{name}')
        started = time.time()
        adapter.make(config, cand, env)
        # The retained OUT_DIR holds older target-files too; take only the one this build wrote.
        packaging = Path(host['sourceRoot'])/host['outDir']/'target/product'/config['target']['device']/'obj/PACKAGING/target_files_intermediates'
        fresh = [z for z in packaging.glob('*-target_files*.zip') if z.stat().st_mtime >= started - 1]
        if len(fresh) != 1:
            raise CheckFailed(f'expected exactly one target-files zip written by this build, found {len(fresh)}')
        shutil.copyfile(fresh[0], release_dir/'unsigned-target_files.zip')
    finally:
        lock.close()
    return {'stage': 'build', 'unsignedTargetFiles': sha256_file(release_dir/'unsigned-target_files.zip')}

def sign(a, config, cand, adapter):
    host = config['host']
    if not host.get('signCommand'):
        raise SetupMissing(['host.signCommand'])
    release_dir = Path(a.release_dir)
    lock = open(host['lockPath'], 'a')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise CheckFailed('the source/output lock is held by another job')
    try:
        # Lead-managed entry point: signs with the existing identities; secrets stay on the builder.
        try:
            run_entry_point(host['signCommand'], stage_env(config, cand, release_dir), host['sourceRoot'])
        finally:
            drop_signer_access()
    finally:
        lock.close()
    for name in ('signed-target_files.zip', 'public'):
        if not (release_dir/name).exists():
            raise CheckFailed(f'signing did not produce $ROSE_RELEASE_DIR/{name}')
    return {'stage': 'sign', 'signedTargetFiles': sha256_file(release_dir/'signed-target_files.zip')}

# ---------------------------------------------------------------- verification

def read_props(archive):
    props = {}
    for name in PROPS:
        if name in archive.namelist():
            for line in archive.read(name).decode(errors='replace').splitlines():
                if '=' in line and not line.startswith('#'):
                    key, value = line.split('=', 1)
                    props.setdefault(name, {})[key.strip()] = value.strip()
    if 'SYSTEM/build.prop' not in props:
        raise CheckFailed('target-files has no SYSTEM/build.prop')
    return props

def check_build_identity(path, config, cand, keys):
    """Every partition's build number, timestamp, device and fingerprint, and every fingerprint in
    META/misc_info.txt (stale AVB fingerprints there survived a build-number change once)."""
    failures = []
    device, build = config['target']['device'], cand['build']
    marker = f'/{build}:{config["target"]["variant"]}/{keys}'
    with zipfile.ZipFile(path) as archive:
        props = read_props(archive)
        for name, values in props.items():
            for key, value in values.items():
                if key.endswith('build.version.incremental') and value != build:
                    failures.append(f'{name}: {key}={value}')
                if key.endswith('build.date.utc') and value != cand['timestamp']:
                    failures.append(f'{name}: {key}={value}')
                if key.endswith('build.fingerprint') and marker not in value:
                    failures.append(f'{name}: {key} does not name build {build} with {keys}')
                if key.endswith('product.device') and value != device:
                    failures.append(f'{name}: {key}={value}')
        system = props['SYSTEM/build.prop']
        if system.get('ro.build.type') != config['target']['variant']:
            failures.append('ro.build.type is not ' + config['target']['variant'])
        if 'META/misc_info.txt' not in archive.namelist():
            failures.append('META/misc_info.txt missing')
        else:
            fingerprints = re.findall(r'\.fingerprint:(\S+)', archive.read('META/misc_info.txt').decode(errors='replace'))
            if not fingerprints:
                failures.append('META/misc_info.txt has no AVB fingerprint properties')
            failures += [f'META/misc_info.txt fingerprint {f} does not name build {build}'
                for f in fingerprints if marker not in f]
    return failures

def check_framework(path, config, aapt):
    """Compiled framework allowlist and switches, read from framework-res.apk in the image."""
    expected = config['framework']
    with zipfile.ZipFile(path) as archive:
        data = archive.read('SYSTEM/framework/framework-res.apk')
    extracted = Path(os.environ.get('RUNNER_TEMP', '/tmp'))/f'framework-res-{os.getpid()}.apk'
    extracted.write_bytes(data)
    try:
        dump = subprocess.run([aapt, 'dump', '--values', 'resources', str(extracted)], capture_output=True,
            text=True, check=True).stdout
    finally:
        extracted.unlink()
    failures = []
    def array(name):
        block = re.search(rf'array/{name}:.*?(?=\n\s*spec resource|\n\s*resource 0x|\Z)', dump, re.S)
        return re.findall(r'"([^"]*)"', block.group(0)) if block else None
    targets = array('config_pixelComputerControlTargets')
    if targets is None or sorted(t.split(':')[0] for t in targets) != sorted(expected['computerControlTargets']):
        failures.append(f'compiled computer-control targets are {targets}')
    prototype = array('config_pixelComputerControlPrototypeAgents')
    if expected['prototypeAgentsEmpty'] and prototype:
        failures.append('compiled prototype agents are not empty')
    locked = re.search(r'bool/config_pixelComputerControlLockedSessions:.*?\(bool\)\s*(\w+)', dump, re.S)
    if not locked or (locked.group(1) == 'true') != expected['lockedSessions']:
        failures.append('compiled locked-session switch differs from config')
    return failures

def check_apps(path, policy, apksigner):
    result = subprocess.run([sys.executable, str(TARGET_FILES_VERIFIER), str(path), str(policy), '--apksigner', apksigner],
        capture_output=True, text=True)
    report = json.loads(result.stdout or '{}')
    if result.returncode or report.get('verdict') != 'PASS':
        return [f'app gate: {r["packageName"]} failed' for r in report.get('results', []) if not r.get('pass')] or ['app gate failed']
    return []

def tool(config, name):
    path = config['host']['tools'].get(name)
    if not path or not host_path(config, path).exists():
        raise SetupMissing([f'host.tools.{name}'])
    return str(host_path(config, path))

def verify_build(a, config, cand, adapter):
    release_dir = Path(a.release_dir)
    target_files = release_dir/'unsigned-target_files.zip'
    failures = check_build_identity(target_files, config, cand, 'test-keys')
    failures += check_apps(target_files, release_dir/'background-policy.json', tool(config, 'apksigner'))
    failures += check_framework(target_files, config, tool(config, 'aapt'))
    if failures:
        raise CheckFailed('; '.join(failures))
    return {'stage': 'bytes-verify', 'checks': ['build identity', 'misc_info fingerprints', 'app bytes/PRESIGNED/signers', 'framework policy']}

def cert_sha256(pem):
    body = pem.split('-----BEGIN CERTIFICATE-----')[1].split('-----END CERTIFICATE-----')[0]
    return hashlib.sha256(base64.b64decode(''.join(body.split()))).hexdigest()

def check_ota(config, cand, ota, public):
    failures = []
    device, build, identities = config['target']['device'], cand['build'], config['identities']
    with zipfile.ZipFile(ota) as archive:
        try:
            metadata = dict(line.split('=', 1) for line in archive.read('META-INF/com/android/metadata').decode().splitlines() if '=' in line)
        except KeyError:
            return ['OTA has no META-INF/com/android/metadata']
    if metadata.get('pre-device') != device:
        failures.append(f'OTA pre-device is {metadata.get("pre-device")}')
    if metadata.get('post-timestamp') != cand['timestamp']:
        failures.append(f'OTA post-timestamp is {metadata.get("post-timestamp")}')
    if f'/{build}:' not in metadata.get('post-build', ''):
        failures.append('OTA post-build does not name the selected build')
    if cert_sha256((public/'releasekey.x509.pem').read_text()) != identities['otaCertificateSha256']:
        failures.append('published OTA certificate does not match the pin')
    if sha256_file(public/'avb_pkmd.bin') != identities['avbPublicKeySha256']:
        failures.append('published AVB key does not match the pin')
    # Whole-package and payload signatures, checked by the source tree's own release tool.
    result = subprocess.run([sys.executable, tool(config, 'checkOtaSignature'), str(public/'releasekey.x509.pem'), str(ota)],
        capture_output=True, text=True)
    if result.returncode:
        failures.append('OTA package or payload signature verification failed')
    return failures

def check_vbmeta(config, signed, public):
    with zipfile.ZipFile(signed) as archive:
        if 'IMAGES/vbmeta.img' not in archive.namelist():
            return ['signed target-files has no IMAGES/vbmeta.img']
        image = Path(os.environ.get('RUNNER_TEMP', '/tmp'))/f'vbmeta-{os.getpid()}.img'
        image.write_bytes(archive.read('IMAGES/vbmeta.img'))
    try:
        avbtool = tool(config, 'avbtool')
        verified = subprocess.run([sys.executable, avbtool, 'verify_image', '--image', str(image)], capture_output=True, text=True)
        info = subprocess.run([sys.executable, avbtool, 'info_image', '--image', str(image)], capture_output=True, text=True)
    finally:
        image.unlink()
    failures = [] if verified.returncode == 0 else ['vbmeta signature verification failed']
    embedded = re.search(r'Public key \(sha1\):\s*([0-9a-f]{40})', info.stdout)
    if not embedded or embedded.group(1) != hashlib.sha1((public/'avb_pkmd.bin').read_bytes()).hexdigest():
        failures.append('vbmeta is not signed by the published AVB key')
    return failures

def verify_signed(a, config, cand, adapter):
    release_dir = Path(a.release_dir)
    public, signed = release_dir/'public', release_dir/'signed-target_files.zip'
    device, build = config['target']['device'], cand['build']
    expected = set(public_assets(device, build)) - {'verification.json'}
    present = {p.name for p in public.iterdir()}
    failures = [f'public directory has {sorted(present ^ expected)} outside the allowlist'] if present != expected else []
    if not failures:
        failures += check_build_identity(signed, config, cand, 'release-keys')
        failures += check_apps(signed, release_dir/'background-policy.json', tool(config, 'apksigner'))
        failures += check_ota(config, cand, public/f'{device}-ota_update-{build}.zip', public)
        failures += check_vbmeta(config, signed, public)
        feed = (public/f'{device}-stable').read_text().splitlines()[:1]
        if feed != [f'{build} {cand["timestamp"]} {device} {config["publication"]["channels"]["beta"]["feedChannel"]}']:
            failures.append('feed file does not describe the selected build')
    if failures:
        raise CheckFailed('; '.join(failures))
    receipt = {'receiptType': 'candidate-verification', 'candidate': cand, 'simulated': adapter.simulated,
        'signedTargetFilesSha256': sha256_file(signed), 'published': False,
        'checks': ['allowlist', 'build identity (release-keys)', 'misc_info fingerprints', 'app bytes/PRESIGNED/signers',
            'OTA metadata', 'OTA package and payload signatures', 'vbmeta signature and AVB key', 'public pins', 'feed line']}
    release_receipts.write_once(public/'verification.json', receipt)
    return {'stage': 'ota-verify', 'verdict': 'PASS', 'simulated': adapter.simulated}

# ---------------------------------------------------------------- transfer and publication

def write_identities(config, path, apps):
    signer = config['identities']['appSignerSha256']
    release_receipts.write_once(path, {'apps': [{'packageName': p, 'versionCode': v, 'signerSha256': signer} for p, v in apps],
        'otaCertificateSha256': config['identities']['otaCertificateSha256'],
        'avbPublicKeySha256': config['identities']['avbPublicKeySha256']})

def manifest_tool(*args):
    result = subprocess.run([sys.executable, str(ROOT/'scripts/release-manifest.py'), *map(str, args)], capture_output=True, text=True)
    if result.returncode:
        raise CheckFailed(f'release-manifest {args[0]} failed: {(result.stdout or result.stderr).strip()[-400:]}')
    return json.loads(result.stdout)

def stage_artifact(a, config, cand, adapter):
    """Copy only the allowlisted public files and nonsecret receipts into the artifact directory."""
    release_dir, artifact = Path(a.release_dir), Path(a.artifact_dir)
    device, build = config['target']['device'], cand['build']
    verification = json.loads((release_dir/'public/verification.json').read_text())
    if verification.get('candidate') != cand or verification.get('simulated') != adapter.simulated:
        raise CheckFailed('verification receipt does not belong to this candidate and adapter')
    assets = artifact/'assets'
    assets.mkdir(parents=True)
    for name in public_assets(device, build):
        source = release_dir/'public'/name
        if source.is_symlink() or not source.is_file():
            raise CheckFailed(f'public file missing or not regular: {name}')
        shutil.copyfile(source, assets/name)
    apps = [tuple(x) for x in json.loads(a.apps)]
    write_identities(config, artifact/'expected-identities.json', apps)
    resolved = release_dir/'resolved-manifest.xml'
    manifest_tool('create', '--source-revision', cand['sha'], '--resolved-manifest', resolved, '--device', device,
        '--variant', config['target']['variant'], '--channel', cand['channel'], '--build-number', build,
        '--build-timestamp', cand['timestamp'], '--identities', artifact/'expected-identities.json',
        '--output', artifact/'manifest.json', *[x for n in sorted(os.listdir(assets)) for x in ('--asset', assets/n)])
    release_receipts.write_once(artifact/'pipeline-receipt.json', {'receiptType': 'builder-transfer', 'candidate': cand,
        'simulated': adapter.simulated, 'manifestSha256': sha256_file(artifact/'manifest.json')})
    return {'stage': 'transfer', 'assets': len(os.listdir(assets)), 'simulated': adapter.simulated}

def publish(a, config, cand, adapter):
    artifact = Path(a.artifact_dir)
    receipt = json.loads((artifact/'pipeline-receipt.json').read_text())
    if receipt.get('simulated') and not adapter.simulated:
        raise CheckFailed('refusing to publish a simulated artifact')
    if receipt.get('candidate') != cand or receipt.get('manifestSha256') != sha256_file(artifact/'manifest.json'):
        raise CheckFailed('artifact receipt does not match this candidate or manifest')
    channel = config['publication']['channels'].get(cand['channel'])
    if channel is None:
        raise SetupMissing([f'publication.channels.{cand["channel"]} (no verified publication target)'])
    # Revalidate the transferred bytes before anything leaves this job.
    manifest_tool('verify-published', artifact/'manifest.json', '--dir', artifact/'assets')
    manifest_tool('verify', artifact/'manifest.json', '--dir', artifact/'assets', '--expected-device', config['target']['device'],
        '--expected-channel', cand['channel'], '--expected-identities', artifact/'expected-identities.json', '--no-baseline')
    public = artifact/'assets'
    if cert_sha256((public/'releasekey.x509.pem').read_text()) != config['identities']['otaCertificateSha256'] or \
            sha256_file(public/'avb_pkmd.bin') != config['identities']['avbPublicKeySha256']:
        raise CheckFailed('transferred public keys do not match the pins')
    draft = adapter.publish(config, cand, public, artifact)
    # Download check of the draft before any promotion; promotion only when explicitly requested.
    manifest_tool('verify-published', artifact/'manifest.json', '--dir', draft)
    promoted = bool(channel['promoteLatest'] and a.promote)
    if promoted:
        adapter.promote(config, cand, draft)
    return {'stage': 'publish', 'draft': 'verified', 'promoted': promoted, 'simulated': adapter.simulated}

# ---------------------------------------------------------------- command line

def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('command', choices=['preflight', 'start-builder', 'stop-builder', 'build', 'verify-build', 'sign',
        'verify-signed', 'stage-artifact', 'publish'])
    p.add_argument('--job', choices=['guard', 'controller', 'builder', 'publisher', 'cleanup'])
    p.add_argument('--adapter', choices=['production', 'fake'], default='production')
    p.add_argument('--fake-state', help='Fake adapter state directory (tests only)')
    p.add_argument('--config', default=str(CONFIG))
    p.add_argument('--candidate-sha')
    p.add_argument('--build-number')
    p.add_argument('--build-timestamp')
    p.add_argument('--channel', default='beta')
    p.add_argument('--release-dir')
    p.add_argument('--artifact-dir')
    p.add_argument('--apps', default='[]', help='JSON list of [package, versionCode] expected in this candidate')
    p.add_argument('--promote', action='store_true')
    a = p.parse_args(argv)
    try:
        if a.adapter == 'fake' and not a.fake_state:
            raise InvalidInput('--adapter fake needs --fake-state')
        config = load_config(a.config)
        if a.adapter == 'production' and config.get('fixture'):
            raise InvalidInput('a fixture config cannot be used for production')
        adapter = adapter_for(a)
        if a.command == 'preflight':
            missing = preflight(a.job, config, os.environ, a.adapter)
            if missing:
                raise SetupMissing(missing)
            report = {'job': a.job, 'preflight': 'ready'}
        elif a.command in ('start-builder', 'stop-builder'):
            adapter.control_builder(a.command.split('-')[0])
            report = {'builder': a.command}
        else:
            cand = candidate(a)
            report = {'build': build, 'verify-build': verify_build, 'sign': sign, 'verify-signed': verify_signed,
                'stage-artifact': stage_artifact, 'publish': publish}[a.command](a, config, cand, adapter)
    except SetupMissing as error:
        print(json.dumps({'verdict': 'SETUP_MISSING', 'missing': error.missing}), file=sys.stderr)
        return 4
    except InvalidInput as error:
        print(json.dumps({'verdict': 'INVALID', 'error': str(error)}), file=sys.stderr)
        return 2
    except (CheckFailed, subprocess.CalledProcessError, release_receipts.ReceiptExists,
            github_release_publisher.PublishError) as error:
        print(json.dumps({'verdict': 'FAIL', 'error': str(error)}), file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0

if __name__ == '__main__':
    sys.exit(main())
