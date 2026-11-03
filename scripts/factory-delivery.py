#!/usr/bin/env python3
"""FACTORY-DELIVERY: offline, independent verification of the delivered Rango factory installer and its detached
signature, and the final delivery receipt. Pure: no builder, lock, secret, key, network or login. It reads only the two
extracted artifacts and the committed pins, and runs the verifier's own ssh-keygen (recorded by path and sha256).

  python3 scripts/factory-delivery.py verify --installer DIR --signature DIR --out RECEIPT.json
      [--ssh-keygen PATH] [--installer-artifact TEXT] [--signature-artifact TEXT]

  --installer   the extracted private installer artifact (rango-install-2026103001-<run>-<attempt>): exactly SHA256SUMS
                and package/rango-install-2026103001.zip
  --signature   the extracted sign-run artifact: exactly rango-install-2026103001.zip.sig and
                factory-archive-sign-receipt.json
  --*-artifact  free text naming where each came from (artifact name, id, digest); recorded, not trusted
  --ssh-keygen  the verifier's ssh-keygen (default: the one on PATH); its path and sha256 are recorded

Checks, all of which must hold:
  archive    a regular file reached without links, whose size and sha256 equal the committed retained pin
             (scripts/rango-factory-inspect-wiring.py RETAINED); SHA256SUMS names exactly that file with that sha256
  signature  one small regular file; when the committed SIGNATURE pin is bound, its sha256 equals it
  receipt    the sign receipt: roseos.factory-archive-signature.v1, status signed, the same archive sha256 and size, the
             same signature sha256 and file, and the committed flavor identity (principal, namespace, public key)
  verify     ssh-keygen -Y verify against allowed_signers built only from the committed os/flavors/<device>.json
             factorySigning pin, reading exactly the archive bytes checked above; and a negative control under another
             namespace must be refused, so a verifier that accepts anything is caught
The receipt (roseos.rango-factory-delivery.v1) binds archive, signature, identity, pins, sources, verifier and the
repository commit, and states its non-claims. Exit: 0 verified, 3 refused (the receipt says why), 2 invalid usage.
"""
import argparse
import base64
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import factory_signing  # noqa: E402

SCHEMA = 'roseos.rango-factory-delivery.v1'
SIGN_SCHEMA = 'roseos.factory-archive-signature.v1'
DEVICE, BUILD = 'rango', '2026103001'
INSTALL = f'{DEVICE}-install-{BUILD}.zip'
SIGN_RECEIPT = 'factory-archive-sign-receipt.json'
RECEIPT_BYTES = 64 << 10
NON_CLAIMS = ('not installed, flashed, booted or phone-validated', 'not a reproducible-build attestation',
              'not publication', 'the archive content checks are the inspect run\'s, not repeated here')


class Refused(Exception):
    pass


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def committed_pins(root=ROOT):
    """(retained, signature pin, flavor pin) from the committed sources, never from the delivered files."""
    wiring = _load('rango_factory_inspect_wiring_for_delivery', Path(root)/'scripts/rango-factory-inspect-wiring.py')
    flavor = json.loads((Path(root)/f'os/flavors/{DEVICE}.json').read_text())['factorySigning']
    try:
        pin = factory_signing.bound_pin(flavor)
    except factory_signing.Refused as error:
        raise Refused(f'pin_invalid: {error}') from None
    if pin is None:
        raise Refused(f'identity_unbound: os/flavors/{DEVICE}.json factorySigning is not bound; nothing can be verified')
    return dict(wiring.RETAINED), dict(wiring.SIGNATURE), pin


def regular(path, limit=None):
    """lstat of a regular file reached without a link in its last component; Refused otherwise."""
    path = Path(path)
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        raise Refused(f'missing: {path.name}') from None
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise Refused(f'not_regular: {path.name} is a link or not a regular file')
    if limit is not None and info.st_size > limit:
        raise Refused(f'too_large: {path.name} is larger than {limit} bytes')
    return info


def sha256_nofollow(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    digest, size = hashlib.sha256(), 0
    with os.fdopen(fd, 'rb') as f:
        for block in iter(lambda: f.read(8 << 20), b''):
            digest.update(block)
            size += len(block)
    return digest.hexdigest(), size


def exact_entries(directory, expected):
    names = sorted(p.relative_to(directory).as_posix() for p in Path(directory).rglob('*') if not p.is_dir())
    if names != sorted(expected):
        raise Refused(f'unexpected_contents: {Path(directory).name} holds {names[:8]}, not exactly {sorted(expected)}')


def verify(installer, signature, out, ssh_keygen=None, sources=None, root=ROOT, runner=factory_signing.run_bounded, pins=None):
    """Write the delivery receipt to `out` and return it; Refused (after writing a refused receipt) on any failure."""
    receipt = {'schema': SCHEMA, 'device': DEVICE, 'build': BUILD, 'verified': False, 'sources': dict(sources or {}),
               'claims': 'the delivered archive is the retained installer and its detached signature verifies against the '
                         'committed factory-signing pin', 'nonClaims': list(NON_CLAIMS), 'problems': []}
    try:
        receipt['repo'] = {'commit': subprocess.run(['git', '-C', str(root), 'rev-parse', 'HEAD'], capture_output=True,
                                                    text=True, timeout=30).stdout.strip() or None}
    except (OSError, subprocess.SubprocessError):
        receipt['repo'] = {'commit': None}
    try:
        _verify(Path(installer), Path(signature), ssh_keygen, root, runner, receipt, pins)
        receipt['verified'] = True
        return receipt
    except Refused as error:
        receipt['problems'].append(str(error))
        raise
    finally:
        fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
        with os.fdopen(fd, 'w') as f:
            f.write(json.dumps(receipt, indent=2, sort_keys=True) + '\n')


def _verify(installer, signature, ssh_keygen, root, runner, receipt, pins=None):
    retained, signature_pin, pin = pins or committed_pins(root)  # pins: tests only
    exact_entries(installer, ['SHA256SUMS', f'package/{INSTALL}'])
    exact_entries(signature, [f'{INSTALL}.sig', SIGN_RECEIPT])
    archive = installer/'package'/INSTALL
    regular(archive)
    archive_sha, archive_size = sha256_nofollow(archive)
    receipt['archive'] = {'file': INSTALL, 'size': archive_size, 'sha256': archive_sha}
    receipt['pins'] = {'retained': {'runId': retained['runId'], 'attempt': retained['attempt']},
                       'signature': {k: signature_pin.get(k) for k in ('runId', 'attempt')} if signature_pin.get('sha256') else None}
    if (archive_size, archive_sha) != (retained['bytes'], retained['sha256']):
        raise Refused('archive_mismatch: the delivered archive is not the committed retained installer')
    regular(installer/'SHA256SUMS', 4096)
    if (installer/'SHA256SUMS').read_text().split('\n') != [f'{archive_sha}  package/{INSTALL}', '']:
        raise Refused('sums_mismatch: SHA256SUMS does not name exactly the archive with its sha256')
    sig = signature/f'{INSTALL}.sig'
    regular(sig, factory_signing.LIMITS['signatureBytes'])
    sig_bytes = sig.read_bytes()
    sig_sha = hashlib.sha256(sig_bytes).hexdigest()
    receipt['signature'] = {'file': sig.name, 'size': len(sig_bytes), 'sha256': sig_sha}
    if not sig_bytes.startswith(b'-----BEGIN SSH SIGNATURE-----\n'):
        raise Refused('signature_invalid: not an SSH signature')
    if signature_pin.get('sha256') and sig_sha != signature_pin['sha256']:
        raise Refused('signature_mismatch: the signature is not the committed SIGNATURE pin')
    regular(signature/SIGN_RECEIPT, RECEIPT_BYTES)
    try:
        sign = json.loads((signature/SIGN_RECEIPT).read_text())
    except ValueError:
        raise Refused('sign_receipt_invalid: not JSON') from None
    identity = sign.get('identity') or {}
    if sign.get('schema') != SIGN_SCHEMA or sign.get('status') != 'signed' \
            or (sign.get('device'), sign.get('build')) != (DEVICE, BUILD) \
            or (sign.get('archive') or {}).get('sha256') != archive_sha or (sign.get('archive') or {}).get('size') != archive_size \
            or (sign.get('signature') or {}).get('sha256') != sig_sha or (sign.get('signature') or {}).get('file') != sig.name:
        raise Refused('sign_receipt_mismatch: the sign receipt is not a signed receipt of this archive and signature')
    if (identity.get('principal'), identity.get('publicKey'), identity.get('namespace')) != \
            (pin['principal'], pin['publicKey'], factory_signing.NAMESPACE):
        raise Refused('identity_mismatch: the sign receipt identity is not the committed flavor pin')
    fingerprint = factory_signing.public_identity(base64.b64decode(pin['publicKey'].split()[1]))['fingerprint']
    receipt['identity'] = {'principal': pin['principal'], 'publicKey': pin['publicKey'], 'fingerprint': fingerprint,
                           'namespace': factory_signing.NAMESPACE}
    tool = ssh_keygen or shutil.which('ssh-keygen')
    if not tool:
        raise Refused('verifier_missing: no ssh-keygen to verify with')
    tool = os.path.realpath(tool)
    regular(tool)
    receipt['verifier'] = {'sshKeygen': tool, 'sha256': sha256_nofollow(tool)[0]}
    with tempfile.TemporaryDirectory(prefix='rango-delivery-') as work:
        signers = Path(work)/'allowed_signers'
        signers.write_text(f'{pin["principal"]} namespaces="{factory_signing.NAMESPACE}" {pin["publicKey"]}\n')
        copy = Path(work)/sig.name
        copy.write_bytes(sig_bytes)  # exactly the bytes hashed above
        argv = factory_signing.verify_argv(signers, pin['principal'], copy, tool)
        good = runner(argv, dict(factory_signing.CHILD_ENV), work, factory_signing.LIMITS['signSeconds'], stdin_path=str(archive))
        negative = list(argv)
        negative[negative.index('-n') + 1] = 'file'
        bad = runner(negative, dict(factory_signing.CHILD_ENV), work, factory_signing.LIMITS['signSeconds'], stdin_path=str(archive))
    receipt['verifier'].update(verify=good['category'], negativeControl=bad['category'])
    if good['category'] != 'ok':
        raise Refused(f'verify_failed: ssh-keygen -Y verify refused the signature ({good["category"]})')
    if bad['category'] != 'nonzero_exit':
        raise Refused('verifier_untrusted: a verify under another namespace was not refused')
    if sha256_nofollow(archive) != (archive_sha, archive_size):
        raise Refused('input_changed: the archive changed while it was verified')


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split('\n', 1)[0])
    sub = p.add_subparsers(dest='command', required=True)
    v = sub.add_parser('verify')
    for flag in ('--installer', '--signature', '--out'):
        v.add_argument(flag, required=True)
    v.add_argument('--ssh-keygen')
    v.add_argument('--installer-artifact', default='')
    v.add_argument('--signature-artifact', default='')
    a = p.parse_args(argv)
    if os.path.lexists(a.out):
        print(json.dumps({'refused': 'output_exists: --out must be a new file'}))
        return 2
    sources = {'installerArtifact': a.installer_artifact[:300], 'signatureArtifact': a.signature_artifact[:300]}
    try:
        receipt = verify(a.installer, a.signature, a.out, a.ssh_keygen, sources)
    except Refused as error:
        print(json.dumps({'verified': False, 'refused': str(error)}))
        return 3
    print(json.dumps({'verified': True, 'archive': receipt['archive']['sha256'], 'signature': receipt['signature']['sha256'],
                      'fingerprint': receipt['identity']['fingerprint']}))
    return 0


if __name__ == '__main__':
    sys.exit(main())
