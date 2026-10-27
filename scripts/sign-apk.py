#!/usr/bin/env python3
"""Sign one APK using explicitly supplied private files; never fetch secrets or install."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('apk', type=Path)
p.add_argument('--keystore', type=Path, required=True)
p.add_argument('--config', type=Path, required=True)
p.add_argument('--store-password-file', type=Path)
p.add_argument('--key-password-file', type=Path)
p.add_argument('--output', type=Path, required=True)
p.add_argument('--expected-cert-sha256', help='Optional full public certificate SHA-256 pin')
a = p.parse_args()
root = Path(__file__).resolve().parents[1]
os.umask(0o077)
def private_file(value):
    f = Path(value).expanduser().resolve(strict=True)
    if f.is_relative_to(root) or not f.is_file() or f.stat().st_mode & 0o077:
        raise ValueError('Signing input must be an owner-only file outside the repository')
    return f
try:
    keystore = private_file(a.keystore)
    config = json.loads(private_file(a.config).read_text())
    alias = config['keyAlias']
    if not isinstance(alias, str) or not alias:
        raise ValueError('Missing signing alias')
    store_pass = private_file(a.store_password_file or config['storePasswordFile'])
    key_pass = private_file(a.key_password_file or config['keyPasswordFile'])
    apk = a.apk.resolve(strict=True)
    output = a.output.resolve()
    if output.exists() or output.with_suffix('.apk.idsig').exists():
        raise ValueError('Output exists; choose a fresh output path')
    if a.expected_cert_sha256 and not re.fullmatch(r'[0-9a-fA-F]{64}', a.expected_cert_sha256):
        raise ValueError('Expected certificate pin must be full SHA-256')
    sdk = Path(os.environ.get('ANDROID_SDK_ROOT', root/'.tools/android-sdk'))
    bt = sdk/'build-tools/android-15'
    output.parent.mkdir(parents=True, exist_ok=True)
    commands = [
        [str(bt/'zipalign'), '-c', '4', str(apk)],
        [str(bt/'apksigner'), 'sign', '--ks', str(keystore), '--ks-key-alias', alias,
         '--ks-pass', 'file:'+str(store_pass), '--key-pass', 'file:'+str(key_pass),
         '--v4-signing-enabled', 'true', '--out', str(output), str(apk)],
        [str(bt/'apksigner'), 'verify', '--verbose', '--print-certs', str(output)],
    ]
    for i, command in enumerate(commands):
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode:
            raise RuntimeError(['alignment_failed', 'signing_failed', 'verification_failed'][i])
    certs = re.findall(r'^Signer #\d+ certificate SHA-256 digest: ([0-9a-fA-F]{64})$', result.stdout, re.M)
    if not certs or (a.expected_cert_sha256 and certs != [a.expected_cert_sha256.lower()]):
        raise RuntimeError('certificate_pin_mismatch')
    receipt = {'schemaVersion': 1, 'apk': output.name,
               'sha256': hashlib.sha256(output.read_bytes()).hexdigest(),
               'certificateSha256': certs, 'signatureVerified': True,
               'inputSha256': hashlib.sha256(apk.read_bytes()).hexdigest(),
               'installed': False, 'published': False}
    output.with_suffix('.receipt.json').write_text(json.dumps(receipt, indent=2)+'\n')
    print(json.dumps(receipt))
except Exception as error:
    # Tool stderr and exception input may contain private paths or provider data.
    category = str(error) if isinstance(error, RuntimeError) else 'invalid_or_missing_private_input'
    print('APK signing stopped: '+category, file=sys.stderr)
    sys.exit(1)
