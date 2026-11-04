#!/usr/bin/env python3
"""Factory-archive SSH signing identity: protected generation, signing and pinned verification (FOLD-FACTORY-IDENTITY).

Shared by scripts/signing-bootstrap.py (profile archive.format "openssh-ed25519": generate one passphrase-encrypted
Ed25519 key in the owner-only bootstrap workspace) and scripts/host-sign.py (sign {device}-install-{build}.zip with
ssh-keygen -Y sign -n "factory images" and verify it against the pinned public key before mapping it).

The passphrase never enters argv, the environment, output or a receipt. ssh-keygen has no passphrase-file option; it
reads passphrases through read_passphrase, which with SSH_ASKPASS_REQUIRE=force (documented in ssh(1), OpenSSH 8.4+)
runs SSH_ASKPASS and reads the passphrase from its stdout. The askpass helper is fixed bytes (ASKPASS_TEMPLATE, pinned
by ASKPASS_TEMPLATE_SHA256) naming only the owner-only passphrase file. The child environment is exactly CHILD_ENV
plus the helper path; stdin is closed, the child gets its own session (no controlling terminal) with core dumps off,
a finite timeout ends the whole process group (TERM, then KILL) and reaps it, and combined output is capped and never
returned or logged (only its size and sha256).

self_check() runs before any import: the generated key must decrypt with its passphrase through the helper (ssh-keygen
-y yields the header's public key), sign a synthetic challenge, verify against that public key, and fail to verify
under another namespace. Header structure alone never counts as a working key.

Nothing here proves compatibility with a real OpenSSH build or builder: that needs an approved run with the measured
tool identity. Source tests use mocked process and secret boundaries only.
"""
import base64
import hashlib
import os
from pathlib import Path
import re
import resource
import select
import signal
import stat
import struct
import subprocess
import time

NAMESPACE = 'factory images'
KEY_FILE = 'id_ed25519'
ASKPASS_FILE = 'askpass'
ASKPASS_TEMPLATE = '#!/bin/sh\nexec cat {passfile}\n'
ASKPASS_TEMPLATE_SHA256 = hashlib.sha256(ASKPASS_TEMPLATE.encode()).hexdigest()
CHILD_ENV = {'PATH': '/usr/bin:/bin', 'LANG': 'C', 'SSH_ASKPASS_REQUIRE': 'force'}
PLAIN_PATH = re.compile(r'/[A-Za-z0-9_./-]+')
PRINCIPAL = re.compile(r'[a-z][a-z0-9-]{2,62}')
PUBLIC_KEY = re.compile(r'ssh-ed25519 [A-Za-z0-9+/]{68}')
KDF_ROUNDS = 100
LIMITS = {'seconds': 60, 'signSeconds': 600, 'outputBytes': 16 << 10, 'killGraceSeconds': 2, 'keyBytes': 4096,
          'signatureBytes': 4096}


class Refused(Exception):
    """A fixed category and nonsecret detail, safe to log."""


def _plain(path, label):
    path = str(path)
    if not PLAIN_PATH.fullmatch(path) or '..' in path.split('/'):
        raise Refused(f'path_refused: {label} must be a plain absolute path')
    return path


STOP_SIGNALS = (signal.SIGTERM, signal.SIGHUP)


def stop_on_signals(raise_stop, signals=STOP_SIGNALS):
    """Turn the first SIGTERM or SIGHUP (an outer timeout, a cancelled job) into raise_stop(signum) at the current point,
    so every finally block still runs: the owner-only RAM directories holding a fetched or freshly generated key are
    overwritten and removed instead of being left behind by the default action. Later signals are ignored so cleanup
    is never interrupted. Returns the previous handlers."""
    def handler(signum, frame):
        for each in signals:
            signal.signal(each, signal.SIG_IGN)
        raise_stop(signum)
    return {each: signal.signal(each, handler) for each in signals}


def workspace_problem(path, statvfs=os.statvfs):
    """None when an askpass helper written into PATH can be executed; else a fixed reason. A RAM directory on a noexec
    mount would make ssh-keygen read an empty passphrase, so callers refuse before anything irreversible."""
    try:
        flags = statvfs(path).f_flag
    except OSError as error:
        return f'workspace_unusable: {type(error).__name__}'
    if flags & os.ST_NOEXEC:
        return 'workspace_noexec: the private directory is on a noexec mount, so the askpass helper could not run'
    return None


def write_askpass(workspace, passfile):
    """The pinned helper: fixed template bytes naming only the owner-only passphrase file, mode 0700."""
    if ASKPASS_TEMPLATE_SHA256 != hashlib.sha256(ASKPASS_TEMPLATE.encode()).hexdigest():
        raise Refused('askpass_changed: the helper template is not the pinned bytes')
    helper = Path(workspace)/ASKPASS_FILE
    data = ASKPASS_TEMPLATE.format(passfile=_plain(passfile, 'passphrase file')).encode()
    fd = os.open(helper, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o700)
    with os.fdopen(fd, 'wb') as f:
        f.write(data)
    return helper


def child_env(helper):
    return dict(CHILD_ENV, SSH_ASKPASS=_plain(helper, 'askpass helper'))


def keygen_argv(workspace, tool):
    return [_plain(tool, 'ssh-keygen'), '-q', '-t', 'ed25519', '-a', str(KDF_ROUNDS), '-C', '', '-f', _plain(Path(workspace)/KEY_FILE, 'key')]


def sign_argv(key, archive, tool):
    return [_plain(tool, 'ssh-keygen'), '-Y', 'sign', '-n', NAMESPACE, '-f', _plain(key, 'key'), _plain(archive, 'archive')]


def verify_argv(signers, principal, signature, tool):
    return [_plain(tool, 'ssh-keygen'), '-Y', 'verify', '-f', _plain(signers, 'allowed signers'), '-I', principal, '-n', NAMESPACE,
            '-s', _plain(signature, 'signature')]


def _no_core():
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


def run_bounded(argv, env, cwd, seconds, stdin_path=None, popen=subprocess.Popen, clock=time.monotonic, keep_output=False):
    """The only process boundary. Returns {'exit', 'category', 'outputBytes', 'outputSha256'}; output is returned (as
    'output', capped) only with keep_output, used solely for ssh-keygen -y, whose output is the public key. stdin is
    /dev/null unless stdin_path names an owner-only file (the archive for -Y verify)."""
    cap, grace = LIMITS['outputBytes'], LIMITS['killGraceSeconds']
    with open(stdin_path or os.devnull, 'rb') as stdin:
        try:
            proc = popen(argv, cwd=str(cwd), env=env, stdin=stdin, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         close_fds=True, start_new_session=True, preexec_fn=_no_core)
        except OSError:
            return {'exit': None, 'category': 'spawn_failed', 'outputBytes': 0, 'outputSha256': None}
        output, category, end = bytearray(), None, clock() + seconds
        fd = proc.stdout.fileno()
        try:
            while True:
                remaining = end - clock()
                if remaining <= 0:
                    category = 'timeout'
                    break
                ready, _, _ = select.select([fd], [], [], min(remaining, 0.1))
                if ready:
                    block = os.read(fd, 4096)
                    if not block:
                        break
                    output += block
                    if len(output) > cap:
                        category = 'output_overflow'
                        break
                elif proc.poll() is not None:
                    break
        finally:
            for sig in (signal.SIGTERM, signal.SIGKILL):
                try:
                    os.killpg(proc.pid, sig)
                except (ProcessLookupError, PermissionError):
                    pass
                if sig == signal.SIGTERM:
                    try:
                        proc.wait(timeout=grace)
                    except subprocess.TimeoutExpired:
                        pass
            proc.wait()
            proc.stdout.close()
    if category is None:
        category = 'ok' if proc.returncode == 0 else 'nonzero_exit'
    result = {'exit': proc.returncode if category in ('ok', 'nonzero_exit') else None, 'category': category,
              'outputBytes': min(len(output), cap + 1), 'outputSha256': hashlib.sha256(bytes(output[:cap + 1])).hexdigest()}
    if keep_output and category == 'ok':
        result['output'] = bytes(output[:cap])
    return result


def _strings(blob, offset, count):
    values = []
    for _ in range(count):
        if offset + 4 > len(blob):
            raise Refused('key_invalid: truncated OpenSSH key')
        n, = struct.unpack('>I', blob[offset:offset + 4])
        if offset + 4 + n > len(blob):
            raise Refused('key_invalid: truncated OpenSSH key')
        values.append(blob[offset + 4:offset + 4 + n])
        offset += 4 + n
    return values, offset


def public_identity(blob):
    key_type, offset = _strings(blob, 0, 1)
    if key_type != [b'ssh-ed25519']:
        raise Refused('key_invalid: not an ssh-ed25519 key')
    (raw,), offset = _strings(blob, offset, 1)
    if len(raw) != 32 or offset != len(blob):
        raise Refused('key_invalid: not a 32-byte Ed25519 public key')
    fingerprint = 'SHA256:' + base64.b64encode(hashlib.sha256(blob).digest()).decode().rstrip('=')
    return {'publicKey': 'ssh-ed25519 ' + base64.b64encode(blob).decode(), 'fingerprint': fingerprint}


def inspect_private(path):
    """Header-only check of the generated private key file (nothing is decrypted): openssh-key-v1, exactly one key,
    aes256-ctr with bcrypt at >= KDF_ROUNDS, an ssh-ed25519 public key. Returns the public identity."""
    path = Path(path)
    info = os.lstat(path)
    if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) & 0o077 or not 0 < info.st_size <= LIMITS['keyBytes']:
        raise Refused('key_invalid: the private key is not a small owner-only regular file')
    lines = path.read_text(errors='replace').splitlines()
    if len(lines) < 3 or lines[0] != '-----BEGIN OPENSSH PRIVATE KEY-----' or lines[-1] != '-----END OPENSSH PRIVATE KEY-----':
        raise Refused('key_invalid: not an OpenSSH private key file')
    try:
        blob = base64.b64decode(''.join(lines[1:-1]), validate=True)
    except ValueError:
        raise Refused('key_invalid: undecodable OpenSSH private key') from None
    magic = b'openssh-key-v1\0'
    if not blob.startswith(magic):
        raise Refused('key_invalid: not openssh-key-v1')
    (cipher, kdf, options), offset = _strings(blob, len(magic), 3)
    if cipher != b'aes256-ctr' or kdf != b'bcrypt':
        raise Refused('key_unencrypted: the private key is not aes256-ctr with bcrypt')
    (_salt,), after = _strings(options, 0, 1)
    if after + 4 != len(options) or struct.unpack('>I', options[after:])[0] < KDF_ROUNDS:
        raise Refused(f'key_invalid: bcrypt rounds below {KDF_ROUNDS}')
    if offset + 4 > len(blob) or struct.unpack('>I', blob[offset:offset + 4])[0] != 1:
        raise Refused('key_invalid: exactly one key is required')
    (public,), _ = _strings(blob, offset + 4, 1)
    return public_identity(public)


def generate_key(workspace, tool, runner=run_bounded, token=None):
    """In the owner-only workspace: passphrase (random, file only), pinned askpass helper, one ssh-keygen run with the
    passphrase supplied only through the helper; leaves exactly id_ed25519 and passphrase and returns the public
    identity. The helper and id_ed25519.pub are overwritten and removed; the .pub must equal the private file header."""
    workspace = Path(workspace)
    passfile = workspace/'passphrase'
    fd = os.open(passfile, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as f:
        f.write((token or base64.urlsafe_b64encode(os.urandom(36)).decode()) + '\n')
    helper = write_askpass(workspace, passfile)
    try:
        result = runner(keygen_argv(workspace, tool), child_env(helper), workspace, LIMITS['seconds'])
    finally:
        helper.write_bytes(b'\0' * helper.stat().st_size)
        helper.unlink()
    if result['category'] != 'ok':
        raise Refused(f'generator_failed: ssh-keygen {result["category"]} (exit {result["exit"]})')
    identity = inspect_private(workspace/KEY_FILE)
    pub = workspace/f'{KEY_FILE}.pub'
    if pub.is_symlink() or not pub.is_file() or pub.read_text().split()[:2] != identity['publicKey'].split():
        raise Refused('key_invalid: id_ed25519.pub does not equal the private key header')
    pub.unlink()
    names = sorted(os.listdir(workspace))
    if names != sorted([KEY_FILE, 'passphrase']):
        raise Refused(f'unexpected_output: generation left {names[:8]}')
    return identity


SELF_CHECK_NEGATIVE_NAMESPACE = 'roseos-self-check-negative'


def self_check(workspace, tool, identity, principal, runner=run_bounded):
    """Before import, in the owner-only workspace: decrypt (ssh-keygen -y through the helper must print exactly the
    header's public key), sign a synthetic challenge, verify it against that key, and require a verify under another
    namespace to fail. Leaves the workspace exactly as found (id_ed25519 and passphrase); returns public booleans."""
    workspace = Path(workspace)
    key, passfile = workspace/KEY_FILE, workspace/'passphrase'
    check = workspace/'self-check'
    check.mkdir(mode=0o700)
    try:
        helper = write_askpass(check, passfile)
        try:
            derived = runner([_plain(tool, 'ssh-keygen'), '-y', '-f', _plain(key, 'key')], child_env(helper), check,
                             LIMITS['seconds'], keep_output=True)
        finally:
            helper.write_bytes(b'\0' * helper.stat().st_size)
            helper.unlink()
        public = (derived.get('output') or b'').decode('ascii', 'replace').split()[:2]
        if derived['category'] != 'ok' or public != identity['publicKey'].split():
            raise Refused(f'selfcheck_failed: the key does not decrypt to its public key ({derived["category"]})')
        challenge = check/'challenge'
        challenge.write_bytes(b'roseos factory-images self-check ' + os.urandom(16).hex().encode() + b'\n')
        pin = {'principal': principal, 'publicKey': identity['publicKey']}
        try:
            sign_and_verify(challenge, key, passfile, pin, check, tool, runner)
        except Refused as error:
            raise Refused(f'selfcheck_failed: {error}') from None
        signers = check/'allowed_signers'
        signers.write_text(f'{principal} namespaces="{NAMESPACE}" {identity["publicKey"]}\n')
        negative = runner([_plain(tool, 'ssh-keygen'), '-Y', 'verify', '-f', str(signers), '-I', principal, '-n',
                           SELF_CHECK_NEGATIVE_NAMESPACE, '-s', f'{challenge}.sig'], dict(CHILD_ENV), check,
                          LIMITS['seconds'], stdin_path=str(challenge))
        if negative['category'] != 'nonzero_exit':
            raise Refused(f'selfcheck_failed: a verify under another namespace did not fail ({negative["category"]})')
    finally:
        for path in sorted(check.iterdir(), reverse=True) if check.exists() else []:
            path.unlink()
        if check.exists():
            check.rmdir()
    return {'decrypt': True, 'signVerify': True, 'negativeRefused': True}


def bound_pin(signing):
    """The flavor factorySigning pin, only when genuinely bound; None while principal/publicKey are null."""
    if not isinstance(signing, dict) or signing.get('publicKey') is None and signing.get('principal') is None:
        return None
    if signing.get('status') != 'bound' or signing.get('namespace') != NAMESPACE \
            or not PRINCIPAL.fullmatch(str(signing.get('principal'))) or not PUBLIC_KEY.fullmatch(str(signing.get('publicKey'))):
        raise Refused('pin_invalid: factorySigning must be status bound, namespace "factory images", a principal and an '
                      'ssh-ed25519 public key')
    public_identity(base64.b64decode(signing['publicKey'].split()[1]))
    return {'principal': signing['principal'], 'publicKey': signing['publicKey']}


def sign_and_verify(archive, key, passfile, pin, workdir, tool, runner=run_bounded):
    """ssh-keygen -Y sign with the encrypted key (passphrase through the pinned helper only), then -Y verify against an
    allowed_signers built from the pin alone. Returns the signature path and digest; any failure removes the signature."""
    workdir = Path(workdir)
    signature = Path(f'{archive}.sig')
    if os.path.lexists(signature):
        raise Refused('signature_exists: a signature is already beside the archive')
    helper = write_askpass(workdir, passfile)
    try:
        signed = runner(sign_argv(key, archive, tool), child_env(helper), workdir, LIMITS['signSeconds'])
    finally:
        helper.write_bytes(b'\0' * helper.stat().st_size)
        helper.unlink()
    try:
        if signed['category'] != 'ok':
            raise Refused(f'sign_failed: ssh-keygen -Y sign {signed["category"]} (exit {signed["exit"]})')
        info = os.lstat(signature) if os.path.lexists(signature) else None
        if info is None or not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= LIMITS['signatureBytes'] \
                or not signature.read_text(errors='replace').startswith('-----BEGIN SSH SIGNATURE-----\n'):
            raise Refused('sign_failed: no single small SSH signature was written')
        signers = workdir/'allowed_signers'
        fd = os.open(signers, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'w') as f:
            f.write(f'{pin["principal"]} namespaces="{NAMESPACE}" {pin["publicKey"]}\n')
        try:
            verified = runner(verify_argv(signers, pin['principal'], signature, tool), dict(CHILD_ENV), workdir,
                              LIMITS['signSeconds'], stdin_path=str(archive))
        finally:
            signers.unlink()
        if verified['category'] != 'ok':
            raise Refused(f'verify_failed: the signature does not verify against the pinned key ({verified["category"]})')
    except BaseException:
        if os.path.lexists(signature):
            signature.unlink()
        raise
    return {'file': signature.name, 'sha256': hashlib.sha256(signature.read_bytes()).hexdigest(), 'principal': pin['principal'],
            'namespace': NAMESPACE, 'publicKey': pin['publicKey']}
