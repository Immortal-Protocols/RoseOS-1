#!/usr/bin/env python3
"""os-release host.signCommand: sign the selected Frankel candidate with the existing OS identities.

Invoked by release-pipeline.py on the builder with cwd at the Android source root and the candidate in
ROSE_CANDIDATE_SHA, BUILD_NUMBER, BUILD_DATETIME, OUT_DIR and ROSE_RELEASE_DIR. It reads the pinned
PIXEL_OS_SIGNING_KEYS_FRANKEL_V5 / PIXEL_OS_SIGNING_PASSPHRASE_FRANKEL_V5 versions with the job's
short-lived signer identity into an owner-only RAM directory, validates the archive, checks the public
certificate and AVB key against the established pins, runs the source tree's release command and copies
only the signed target-files and the exact public release set into ROSE_RELEASE_DIR. Secret copies are
overwritten and removed on success and failure. Steps whose source-specific recipe has not been supplied
fail closed (exit 4) and name the missing config key; nothing is guessed.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
import host_secrets  # noqa: E402
from host_secrets import AdapterFailed, AdapterSetupMissing  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
def expand(value, values):
    for key, replacement in values.items():
        value = value.replace('{' + key + '}', replacement)
    return value

def missing_inputs(config):
    sign = config['host'].get('sign') or {}
    return [f'host.sign.{k}' for k in ('project', 'keysSecret', 'passphraseSecret', 'secretVersion', 'keyDirectory',
        'releaseCommand', 'outputs') if not sign.get(k)]

def main(config_path=ROOT/'os/release-pipeline/frankel.json', env=os.environ, gcloud='gcloud'):
    config = json.loads(Path(config_path).read_text())
    sign = config['host'].get('sign') or {}
    missing = missing_inputs(config)
    if missing:
        raise AdapterSetupMissing(missing)
    device, build = config['target']['device'], env['BUILD_NUMBER']
    release_dir = Path(env['ROSE_RELEASE_DIR'])
    with host_secrets.private_directory('rose-os-signing-') as private:
        archive = host_secrets.fetch_secret(sign['project'], sign['keysSecret'], sign['secretVersion'], private/'keys.tar.gz', gcloud)
        passphrase = host_secrets.fetch_secret(sign['project'], sign['passphraseSecret'], sign['secretVersion'],
            private/'passphrase', gcloud)
        keys = host_secrets.extract_key_archive(archive, private/'keys')
        archive.unlink()
        host_secrets.check_public_pins(keys/sign['keyDirectory'], config['identities'])
        values = {'device': device, 'build': build, 'keys': str(keys), 'passphrase_file': str(passphrase),
            'release_dir': str(release_dir)}
        command = [expand(part, values) for part in sign['releaseCommand']]
        # The passphrase stays in its owner-only file; the release command reads it from the path.
        result = subprocess.run(command, cwd=os.getcwd(), env=dict(env))
        if result.returncode:
            raise AdapterFailed(f'release command exited {result.returncode}')
    outputs = sign['outputs']
    source = lambda key: Path(expand(outputs[key], {'device': device, 'build': build}))
    signed = source('signedTargetFiles')
    if not signed.is_file():
        raise AdapterFailed('release command did not produce the signed target-files')
    shutil.copyfile(signed, release_dir/'signed-target_files.zip')
    public = release_dir/'public'
    public.mkdir()
    for name, path in outputs['public'].items():
        name = expand(name, {'device': device, 'build': build})
        file = Path(expand(path, {'device': device, 'build': build}))
        if file.is_symlink() or not file.is_file():
            raise AdapterFailed(f'release command did not produce {name}')
        shutil.copyfile(file, public/name)
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
