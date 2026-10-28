#!/usr/bin/env python3
"""os-release host.signCommand: sign the selected Frankel candidate with the existing OS identities (OTA only).

Invoked by release-pipeline.py under the existing source/output lock, cwd at the Android source root, with
ROSE_CANDIDATE_SHA, BUILD_NUMBER, BUILD_DATETIME, OUT_DIR and ROSE_RELEASE_DIR set. Steps:
1. create releases/{build} (or resume only an exact task-owned state) and copy the selected unsigned target-files
   and the built otatools package into it with fresh mtimes;
2. derive the release script from the source tree's script/generate-release.sh: truncate before
   img_from_target_files (OTA only), source the absolute script/common.sh, point PERSISTENT_KEY_DIR at the
   discovered key directory and replace script/decrypt-keys with host-decrypt-keys.py (file passphrase). Each
   anchor must occur exactly once; original and derived hashes are recorded;
3. read the pinned PIXEL_OS_SIGNING_* versions with the job's short-lived signer identity into owner-only RAM,
   validate the archive and discover the unique pinned key directory;
4. run the derived script and map its outputs: signed target-files, the OTA, the public certificate and AVB key
   from the verified key directory, the feed line from the validated build/timestamp and UPDATE.md from the
   immutable candidate commit. verification.json and SHA256SUMS are written later, after independent checks.
Secret copies are overwritten and removed on success and failure. Nothing is guessed: missing recipe pieces fail
closed (exit 4) naming the config key.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
sys.path.insert(0, str(Path(__file__).resolve().parent))
import host_secrets  # noqa: E402
from host_secrets import AdapterFailed, AdapterSetupMissing  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
REQUIRED = ('project', 'keysSecret', 'passphraseSecret', 'secretVersion', 'otatools', 'javaHome', 'updateNotes')

def missing_inputs(config):
    sign = config['host'].get('sign') or {}
    return [f'host.sign.{k}' for k in REQUIRED if not sign.get(k)]

def sha256(data):
    return hashlib.sha256(data).hexdigest()

def derive_release_script(original, common_sh, key_dir, passfile, release_keys, python=sys.executable,
                          helper=str(ROOT/'scripts/host-decrypt-keys.py')):
    """Exact-once edits of the upstream script; any drift in its shape stops the run instead of signing.

    release_keys is the absolute path of the `keys` link the script creates in its release directory (to the RAM key
    directory). The script then sets KEY_DIR=keys so that otacerts.zip and the recorded certificate and AVB key paths
    stay relative and reproducible; those are left alone. Only the APEX payload keys become that absolute path: the
    APEX re-pack opens them from another working directory, where keys/avb.pem does not resolve (run 40641163775)."""
    def once(pattern, text, flags=0):
        found = list(re.finditer(pattern, text, flags))
        if len(found) != 1:
            raise AdapterFailed(f'release script anchor {pattern!r} found {len(found)} times, expected once')
        return found[0]
    cut = once(r'\nimg_from_target_files', original)
    text = original[:cut.start() + 1]
    source = once(r'^source [^\n]*common\.sh[^\n]*$', text, re.M)
    text = text[:source.start()] + 'source ' + shlex.quote(str(common_sh)) + text[source.end():]
    keys = once(r'^PERSISTENT_KEY_DIR=keys/\$DEVICE$', text, re.M)
    text = text[:keys.start()] + 'PERSISTENT_KEY_DIR=' + shlex.quote(str(key_dir)) + text[keys.end():]
    decrypt = once(r'script/decrypt-keys\s+(\S+)', text)
    text = text[:decrypt.start()] + ' '.join(map(shlex.quote, (python, helper))) + ' ' + decrypt.group(1) + ' ' + \
        shlex.quote(str(passfile)) + text[decrypt.end():]
    once(r'^KEY_DIR=keys$', text, re.M)
    if not re.fullmatch(r'/[\w./-]+', str(release_keys)):
        raise AdapterFailed('release keys path must be a plain absolute path')
    lines = text.splitlines(keepends=True)
    apex = [i for i, line in enumerate(lines) if '--extra_apex_payload_key' in line]
    if not apex:
        raise AdapterFailed('release script anchor --extra_apex_payload_key found 0 times, expected at least once')
    for i in apex:
        if lines[i].count('--extra_apex_payload_key') != lines[i].count('$KEY_DIR/avb.pem'):
            raise AdapterFailed('an --extra_apex_payload_key argument does not use $KEY_DIR/avb.pem; not rewriting it')
        lines[i] = lines[i].replace('$KEY_DIR/avb.pem', f'{release_keys}/avb.pem')
    return ''.join(lines)

def candidate_file(sha, path):
    result = subprocess.run(['git', '-C', str(ROOT), 'show', f'{sha}:{path}'], capture_output=True)
    if result.returncode:
        raise AdapterFailed(f'candidate {sha[:12]} has no {path}')
    return result.stdout

def copy_fresh(source, target):
    if not Path(source).is_file():
        raise AdapterFailed(f'missing input {source}')
    fd, temporary = tempfile.mkstemp(prefix='.' + Path(target).name + '.', dir=Path(target).parent)
    os.close(fd)
    shutil.copyfile(source, temporary)
    os.replace(temporary, target)
    os.utime(target)

SIGNED = '.rose-actions-signed.json'

def prepare_release_dir(source_root, device, build, sha, archive=None):
    """releases/{build} for this run. A partial release output from a failed signing attempt is moved aside whole, to
    releases/{build}.{archive}, only when a sign resume names that archive, the owner marker is exactly this
    candidate's and no successful-signing marker exists; the generate-release script would otherwise rm -rf it."""
    releases = Path(source_root)/'releases'/build
    marker = releases/'.rose-actions-owner.json'
    owner = {'candidate': sha, 'build': build, 'device': device}
    if archive is not None and not re.fullmatch(r'failed-sign-\d+', archive):
        raise AdapterFailed('sign resume archive name must be failed-sign-<run id>')
    if releases.exists():
        if not marker.is_file() or json.loads(marker.read_text()) != owner:
            raise AdapterFailed(f'{releases} exists and is not this task\'s resumable state; it is not touched')
        if (releases/SIGNED).exists():
            raise AdapterFailed(f'{releases} holds a successfully signed release; it is not touched')
        if (releases/f'release-{device}-{build}').exists():
            if archive is None:
                raise AdapterFailed(f'{releases}/release-{device}-{build} already exists; refusing to overwrite a signed release')
            target = releases.parent/f'{build}.{archive}'
            if target.exists():
                raise AdapterFailed(f'{target} already exists; nothing moved')
            os.rename(releases, target)
    if not releases.exists():
        releases.mkdir(parents=True)
        marker.write_text(json.dumps(owner, sort_keys=True) + '\n')
    return releases

def main(config_path=ROOT/'os/release-pipeline/frankel.json', env=os.environ, gcloud='gcloud'):
    config = json.loads(Path(config_path).read_text())
    missing = missing_inputs(config)
    if missing:
        raise AdapterSetupMissing(missing)
    sign, source_root = config['host']['sign'], Path(os.getcwd())
    device, build, stamp, sha = config['target']['device'], env['BUILD_NUMBER'], env['BUILD_DATETIME'], env['ROSE_CANDIDATE_SHA']
    release_dir = Path(env['ROSE_RELEASE_DIR'])
    notes = candidate_file(sha, sign['updateNotes'].format(device=device, build=build))
    if build.encode() not in notes:
        raise AdapterFailed(f'release notes in candidate {sha[:12]} do not name build {build}')
    releases = prepare_release_dir(source_root, device, build, sha, env.get('ROSE_SIGN_RESUME_ARCHIVE') or None)
    copy_fresh(release_dir/'unsigned-target_files.zip', releases/f'{device}-target_files.zip')
    copy_fresh(source_root/env['OUT_DIR']/sign['otatools'], releases/f'{device}-otatools.zip')
    original = (source_root/'script/generate-release.sh').read_bytes()
    public = release_dir/'public'
    public.mkdir()
    with host_secrets.private_directory('rose-os-signing-') as private:
        archive = host_secrets.fetch_secret(sign['project'], sign['keysSecret'], sign['secretVersion'], private/'keys.tar.gz', gcloud)
        passphrase = host_secrets.fetch_secret(sign['project'], sign['passphraseSecret'], sign['secretVersion'],
            private/'passphrase', gcloud)
        extracted = host_secrets.extract_key_archive(archive, private/'keys')
        archive.unlink()
        key_dir = host_secrets.discover_key_directory(extracted, config['identities'])
        derived = derive_release_script(original.decode(), source_root/'script/common.sh', key_dir, passphrase,
            releases/f'release-{device}-{build}'/'keys')
        script = private/'generate-release-derived.sh'
        script.write_text(derived)
        java = Path(sign['javaHome'])
        result = subprocess.run(['bash', str(script), device, build], cwd=str(source_root),
            env=dict(env, JAVA_HOME=str(java), PATH=f'{java}/bin:' + env.get('PATH', '')))
        if result.returncode:
            raise AdapterFailed(f'derived release script exited {result.returncode}')
        shutil.copyfile(key_dir/'releasekey.x509.pem', public/'releasekey.x509.pem')
        shutil.copyfile(key_dir/'avb_pkmd.bin', public/'avb_pkmd.bin')
        key_location = str(key_dir.relative_to(extracted))
    output = releases/f'release-{device}-{build}'
    for produced, target in ((output/f'{device}-target_files.zip', release_dir/'signed-target_files.zip'),
                             (output/f'{device}-ota_update-{build}.zip', public/f'{device}-ota_update-{build}.zip')):
        if not produced.is_file():
            raise AdapterFailed(f'release script did not produce {produced.name}')
        shutil.copyfile(produced, target)
    feed = config['publication']['channels']['beta']['feedChannel']
    (public/f'{device}-stable').write_text(f'{build} {stamp} {device} {feed}\n')
    (public/'UPDATE.md').write_bytes(notes)
    receipt = {'receiptType': 'os-signing', 'candidate': sha, 'build': build, 'secretVersion': sign['secretVersion'],
        'keyDirectoryInArchive': key_location, 'releaseScriptSha256': sha256(original), 'derivedScriptSha256': sha256(derived.encode()),
        'outputs': {p.name: sha256(p.read_bytes()) for p in sorted(public.iterdir())},
        'signedTargetFilesSha256': sha256((release_dir/'signed-target_files.zip').read_bytes())}
    (release_dir/'sign-receipt.json').write_text(json.dumps(receipt, indent=2, sort_keys=True) + '\n')
    (releases/SIGNED).write_text(json.dumps({'candidate': sha, 'build': build, 'outputs': receipt['outputs'],
        'signedTargetFilesSha256': receipt['signedTargetFilesSha256']}, sort_keys=True) + '\n')
    return 0

if __name__ == '__main__':
    try:
        if sys.argv[1:] == ['--check']:
            missing = missing_inputs(json.loads((ROOT/'os/release-pipeline/frankel.json').read_text()))
            if missing:
                raise AdapterSetupMissing(missing)
            sys.exit(0)
        sys.exit(main())
    except AdapterSetupMissing as error:
        print(json.dumps({'verdict': 'SETUP_MISSING', 'missing': error.missing}), file=sys.stderr)
        sys.exit(4)
    except (AdapterFailed, OSError, KeyError, ValueError) as error:
        print(json.dumps({'verdict': 'FAIL', 'error': str(error) if isinstance(error, AdapterFailed) else error.__class__.__name__}),
            file=sys.stderr)
        sys.exit(1)
