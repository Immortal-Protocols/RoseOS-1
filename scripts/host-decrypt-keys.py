#!/usr/bin/env python3
"""Decrypt release signing keys in place inside a temporary RAM key directory, reading the passphrase from a file.

Replaces the interactive script/decrypt-keys call in the derived release script. Every *.pk8 is decrypted with
`openssl pkcs8 -inform DER -passin file:` and rewritten as unencrypted DER; avb.pem is rewritten unencrypted. Only
directories under a RAM prefix (default /dev/shm/) are accepted, so persistent originals are never touched. No key
material or openssl output is printed.
Usage: host-decrypt-keys.py KEY_DIR PASSPHRASE_FILE
"""
import os
from pathlib import Path
import subprocess
import sys
import tempfile

def replace(target, data):
    fd, temporary = tempfile.mkstemp(prefix='.decrypt-', dir=target.parent)
    with os.fdopen(fd, 'wb') as f:
        f.write(data)
    os.chmod(temporary, 0o600)
    os.replace(temporary, target)

def decrypt(key_dir, passfile, prefix=os.environ.get('ROSE_KEY_RAM_PREFIX', '/dev/shm/')):
    key_dir = Path(key_dir).resolve()
    if not str(key_dir).startswith(prefix):
        raise SystemExit('refusing to decrypt keys outside a temporary RAM directory')
    passin = 'file:' + str(Path(passfile).resolve())
    keys = sorted(key_dir.glob('*.pk8'))
    if not keys:
        raise SystemExit('no .pk8 keys found to decrypt')
    for key in keys:
        plain = subprocess.run(['openssl', 'pkcs8', '-inform', 'DER', '-in', str(key), '-passin', passin], capture_output=True)
        if plain.returncode:
            raise SystemExit(f'could not decrypt {key.name}')
        der = subprocess.run(['openssl', 'pkcs8', '-topk8', '-outform', 'DER', '-nocrypt'], input=plain.stdout, capture_output=True)
        if der.returncode:
            raise SystemExit(f'could not rewrite {key.name}')
        replace(key, der.stdout)
    avb = key_dir/'avb.pem'
    if avb.is_file():
        result = subprocess.run(['openssl', 'pkcs8', '-topk8', '-in', str(avb), '-passin', passin, '-nocrypt'], capture_output=True)
        if result.returncode:
            raise SystemExit('could not decrypt avb.pem')
        replace(avb, result.stdout)
    return len(keys)

if __name__ == '__main__':
    if len(sys.argv) != 3:
        raise SystemExit('usage: host-decrypt-keys.py KEY_DIR PASSPHRASE_FILE')
    decrypt(sys.argv[1], sys.argv[2])
