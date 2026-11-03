#!/usr/bin/env python3
"""Detached factory-archive signature for an already generated, hash-pinned install ZIP (FOLD-ARCHIVE-SIGN).

  python3 scripts/factory-archive-sign.py check [--binding os/retained-rango/factory-signing.json]
  python3 scripts/factory-archive-sign.py sign [--binding ...] --archive ZIP --archive-sha256 H \
      --lock SHARED_BUILDER_LOCK --out NEW_ROOT

check (hosted-safe, reads only committed files): exit 0 when the binding (device, build, project, both secret names
and pinned numeric versions, the measured ssh-keygen path and sha256) and the flavor factorySigning pin are complete,
exit 4 listing every missing or invalid field otherwise.

No regeneration and no change to the archive: the retained ZIP is only read. The identity is the flavor's
os/flavors/<device>.json factorySigning pin; while its principal/publicKey are null the run refuses before reading
anything (identity_unbound). Then, before any secret: the archive must be a plain regular file named exactly the
flavor's install-archive name and matching --archive-sha256; ssh-keygen must be the pinned regular file; NEW_ROOT must
be new and outside the archive's directory; the shared builder lock is held (non-blocking).

The key and passphrase come only from the two pinned numeric Secret Manager versions, written by gcloud straight to an
owner-only RAM directory (host_secrets), never argv, output or receipt. The fetched key's public half (read from the
OpenSSH header, nothing decrypted) must equal the pin before anything is signed. scripts/factory_signing.py then signs
with ssh-keygen -Y sign -n "factory images" (passphrase through the pinned askpass helper only), verifies against an
allowed_signers built from the pin alone, and removes the signature on any failure. ssh-keygen signs through a symlink
inside NEW_ROOT, so the signature is written there and nothing is added beside the retained archive; the archive is
rehashed afterwards.

Outputs: NEW_ROOT/{archive}.sig and NEW_ROOT/factory-archive-sign-receipt.json (archive and signature sha256, principal,
namespace, public key and fingerprint, ssh-keygen sha256, secret names and versions, timings); NEW_ROOT/work is
removed. Bounds (BOUNDS, for the caller's outer timeout): each archive hash at most hashSeconds (refused hash_timeout),
each secret fetch at most fetchSeconds (gcloud killed, refused secret_unavailable), ssh-keygen sign and verify each at
most signSeconds by factory_signing (16 KiB output, process group ended, killGraceSeconds). A SIGTERM or SIGHUP is
turned into a refusal (interrupted: signal N) at the point it arrives, so the RAM secrets directory and NEW_ROOT/work
are still removed, a partial signature is deleted and the receipt is written; a caller's stop should allow that
cleanup a grace period before any kill.

Exit status: check 0 ready / 4 unbound (JSON {"ready": false, "missing": [...]}); sign 0 signed and verified,
3 refused (one JSON line {"refused": "<category>: <detail>"}); 2 usage.
"""
import argparse
import contextlib
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
import factory_signing  # noqa: E402
import host_secrets  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = 'roseos.factory-archive-signature.v1'
BINDING_SCHEMA = 'roseos.factory-archive-signing-binding.v1'
DEFAULT_BINDING = ROOT/'os/retained-rango/factory-signing.json'
HEX64 = re.compile(r'[0-9a-f]{64}')
SECRET = re.compile(r'[A-Za-z0-9_-]{1,255}')
BOUNDS = {'hashSeconds': 300, 'fetchSeconds': 120, 'signSeconds': factory_signing.LIMITS['signSeconds'],
          'verifySeconds': factory_signing.LIMITS['signSeconds'], 'killGraceSeconds': factory_signing.LIMITS['killGraceSeconds']}


class Refused(Exception):
    """A fixed category and nonsecret detail."""


def flavor_contract(device, root=ROOT):
    """The flavor's install-archive name template and factorySigning pin."""
    spec = importlib.util.spec_from_file_location('factory_package', root/'scripts/factory-package.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    try:
        flavor = module.load_flavor(device, root)
        return flavor['artifacts']['factory']['file'], flavor.get('factorySigning')
    except (module.ContractError, KeyError, TypeError) as error:
        raise Refused(f'contract_missing: no factory contract for {device} ({error})') from None


def sha256_file(path, seconds=None, clock=time.monotonic):
    digest, end = hashlib.sha256(), None if seconds is None else clock() + seconds
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(8 << 20), b''):
            if end is not None and clock() > end:
                raise Refused(f'hash_timeout: {Path(path).name} was not hashed within {seconds} s')
            digest.update(block)
    return digest.hexdigest()


def bounded_fetch(project, name, version, destination):
    """host_secrets.fetch_secret limited to BOUNDS['fetchSeconds']."""
    return host_secrets.fetch_secret(project, name, version, destination, timeout=BOUNDS['fetchSeconds'])


def stop(signum):
    raise Refused(f'interrupted: signal {signum}')


def pinned_file(path, digest, label):
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts or any(p.is_symlink() for p in path.parents):
        raise Refused(f'input_refused: {label} must be an absolute path without .. or symlinked ancestors')
    if path.is_symlink() or not path.is_file():
        raise Refused(f'input_missing: {label} is not a regular file')
    if not HEX64.fullmatch(str(digest)) or sha256_file(path, BOUNDS['hashSeconds']) != digest:
        raise Refused(f'hash_mismatch: {label} differs from its pin')
    return path


@contextlib.contextmanager
def shared_lock(path):
    path = Path(path)
    if not path.is_absolute():
        raise Refused('usage_refused: --lock must be an absolute path')
    try:
        fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o644)
    except OSError:
        raise Refused('lock_unavailable: the shared builder lock cannot be opened') from None
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Refused('lock_held: the shared builder lock is held by another job') from None
        yield
    finally:
        os.close(fd)


def binding_problems(binding, contract=flavor_contract):
    """Every missing or invalid committed value, without touching any host, secret or credential."""
    problems = []
    if not isinstance(binding, dict) or binding.get('schema') != BINDING_SCHEMA:
        return ['binding: not a roseos.factory-archive-signing-binding.v1 document']
    if not re.fullmatch(r'[a-z][a-z0-9]{1,31}', str(binding.get('device'))) or not re.fullmatch(r'[0-9]{10}', str(binding.get('build'))):
        problems.append('binding.device/build')
    if not re.fullmatch(r'[a-z][a-z0-9-]{4,28}[a-z0-9]', str(binding.get('project'))):
        problems.append('binding.project')
    for name, version in (('keySecret', 'keyVersion'), ('passphraseSecret', 'passphraseVersion')):
        if not SECRET.fullmatch(str(binding.get(name))):
            problems.append(f'binding.{name}')
        if not re.fullmatch(r'[1-9][0-9]*', str(binding.get(version))):
            problems.append(f'binding.{version} (from the bootstrap receipt)')
    tool = binding.get('sshKeygen') or {}
    if not re.fullmatch(r'/[\w./-]+', str(tool.get('path'))) or not HEX64.fullmatch(str(tool.get('sha256'))):
        problems.append('binding.sshKeygen.path/sha256 (measured installed identity)')
    if 'binding.device/build' not in problems:
        try:
            _, signing = contract(binding['device'])
            if factory_signing.bound_pin(signing) is None:
                problems.append(f'os/flavors/{binding["device"]}.json factorySigning principal/publicKey (null)')
        except (Refused, factory_signing.Refused) as error:
            problems.append(f'os/flavors/{binding["device"]}.json factorySigning: {error}')
    return problems


def load_binding(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        raise Refused(f'binding_missing: {path} is unreadable') from None


def sign_from_binding(binding, archive, archive_sha256, lock_path, out, **kw):
    problems = binding_problems(binding, kw.get('contract', flavor_contract))
    if problems:
        raise Refused('binding_unbound: ' + '; '.join(problems))
    tool = binding['sshKeygen']
    args = type('Args', (), {'device': binding['device'], 'build': binding['build'], 'archive': archive,
        'archive_sha256': archive_sha256, 'project': binding['project'], 'key_secret': binding['keySecret'],
        'key_version': binding['keyVersion'], 'passphrase_secret': binding['passphraseSecret'],
        'passphrase_version': binding['passphraseVersion'], 'ssh_keygen': tool['path'], 'ssh_keygen_sha256': tool['sha256'],
        'lock': lock_path, 'out': out})
    return sign(args, **kw)


def sign(args, contract=flavor_contract, fetch=bounded_fetch, runner=factory_signing.run_bounded,
         lock=shared_lock, private=host_secrets.private_directory, workspace_check=factory_signing.workspace_problem):
    begun = time.monotonic()
    if not re.fullmatch(r'[a-z][a-z0-9]{1,31}', args.device) or not re.fullmatch(r'[0-9]{10}', args.build):
        raise Refused('usage_refused: device must be a plain codename and build ten digits')
    template, signing = contract(args.device)
    try:
        pin = factory_signing.bound_pin(signing)
    except factory_signing.Refused as error:
        raise Refused(f'identity_invalid: os/flavors/{args.device}.json {error}') from None
    if pin is None:
        raise Refused(f'identity_unbound: os/flavors/{args.device}.json factorySigning principal and publicKey are null')
    for flag in ('key_version', 'passphrase_version'):
        if not re.fullmatch(r'[1-9][0-9]*', str(getattr(args, flag))):
            raise Refused(f'usage_refused: --{flag.replace("_", "-")} must be a pinned numeric version')
    archive = pinned_file(args.archive, args.archive_sha256, 'archive')
    if archive.name != template.format(device=args.device, build=args.build):
        raise Refused(f'archive_refused: {archive.name} is not the flavor install archive for {args.device} {args.build}')
    tool = pinned_file(args.ssh_keygen, args.ssh_keygen_sha256, 'ssh-keygen')
    out = Path(args.out)
    if not out.is_absolute() or '..' in out.parts or out.parent.is_symlink() or not out.parent.is_dir():
        raise Refused('output_refused: --out must be a new absolute path in an existing plain directory')
    if os.path.lexists(out):
        raise Refused('output_exists: --out must be a new directory; nothing is overwritten')
    if out == archive.parent or archive.parent in out.parents:
        raise Refused('output_refused: --out is inside the archive directory; the retained archive is never touched')
    with lock(args.lock):
        out.mkdir(mode=0o700)
        work = out/'work'
        work.mkdir(mode=0o700)
        signature = out/f'{archive.name}.sig'
        receipt = {'schema': SCHEMA, 'device': args.device, 'build': args.build, 'status': 'failed',
                   'archive': {'path': str(archive), 'sha256': args.archive_sha256, 'size': archive.stat().st_size},
                   'identity': {'principal': pin['principal'], 'namespace': factory_signing.NAMESPACE,
                                'publicKey': pin['publicKey']},
                   'sshKeygenSha256': args.ssh_keygen_sha256,
                   'secrets': {'project': args.project, 'key': {'name': args.key_secret, 'version': str(args.key_version)},
                               'passphrase': {'name': args.passphrase_secret, 'version': str(args.passphrase_version)}},
                   'timings': {'verifySeconds': round(time.monotonic() - begun, 3)},
                   'claims': 'detached signature of the existing archive bytes only; not installed, booted or phone-validated'}
        try:
            link = work/archive.name
            link.symlink_to(archive)  # ssh-keygen writes link.sig here, never beside the retained archive
            mark = time.monotonic()
            with private('rose-factory-archive-sign-') as secrets:
                problem = workspace_check(secrets)
                if problem:  # before any secret is fetched
                    raise Refused(problem)
                try:
                    key = fetch(args.project, args.key_secret, args.key_version, secrets/factory_signing.KEY_FILE)
                    passfile = fetch(args.project, args.passphrase_secret, args.passphrase_version, secrets/'passphrase')
                except host_secrets.AdapterFailed as error:
                    raise Refused(f'secret_unavailable: {error}') from None
                os.chmod(key, 0o600)
                try:
                    public = factory_signing.inspect_private(key)
                except factory_signing.Refused as error:
                    raise Refused(f'key_invalid: {error}') from None
                if public['publicKey'] != pin['publicKey']:
                    raise Refused('key_mismatch: the fetched key is not the flavor factorySigning public key; nothing signed')
                receipt['identity']['fingerprint'] = public['fingerprint']
                try:
                    signed = factory_signing.sign_and_verify(link, key, passfile, pin, secrets, str(tool), runner)
                except factory_signing.Refused as error:
                    raise Refused(f'signature_failed: {error}') from None
            receipt['timings']['signVerifySeconds'] = round(time.monotonic() - mark, 3)
            os.replace(work/f'{archive.name}.sig', signature)
            if sha256_file(archive, BOUNDS['hashSeconds']) != args.archive_sha256:
                raise Refused('input_changed: the retained archive changed while it was signed')
            receipt.update(status='signed', signature={'file': signature.name, 'sha256': signed['sha256'],
                                                       'size': signature.stat().st_size})
            return receipt
        except BaseException as error:
            receipt['refused'] = str(error) if isinstance(error, Refused) else type(error).__name__
            if os.path.lexists(signature):
                signature.unlink()
            raise
        finally:
            shutil.rmtree(work, ignore_errors=True)
            receipt['workRemoved'] = not os.path.lexists(work)
            fd = os.open(out/'factory-archive-sign-receipt.json', os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o644)
            with os.fdopen(fd, 'w') as f:
                f.write(json.dumps(receipt, indent=2, sort_keys=True) + '\n')


def main(argv=None):
    factory_signing.stop_on_signals(stop)  # a stop still runs every cleanup (RAM secrets, work, partial signature)
    p = argparse.ArgumentParser(description=__doc__.split('\n', 1)[0])
    sub = p.add_subparsers(dest='command', required=True)
    for name in ('check', 'sign'):
        c = sub.add_parser(name)
        c.add_argument('--binding', default=str(DEFAULT_BINDING))
        if name == 'sign':
            for flag in ('--archive', '--archive-sha256', '--lock', '--out'):
                c.add_argument(flag, required=True)
    args = p.parse_args(argv)
    try:
        binding = load_binding(args.binding)
    except Refused as error:
        print(json.dumps({'refused': str(error)}))
        return 4 if args.command == 'check' else 3
    if args.command == 'check':
        problems = binding_problems(binding)
        print(json.dumps({'ready': not problems, 'missing': problems}))
        return 0 if not problems else 4
    try:
        receipt = sign_from_binding(binding, args.archive, args.archive_sha256, args.lock, args.out)
    except Refused as error:
        print(json.dumps({'refused': str(error)}))
        return 3
    print(json.dumps({'signed': receipt['signature'], 'principal': receipt['identity']['principal']}))
    return 0


if __name__ == '__main__':
    sys.exit(main())
