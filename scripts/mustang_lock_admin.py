#!/usr/bin/env python3
"""Bounded, unprivileged handoff for separately authorized lock administration.

This job never logs in, generates/registers credentials, runs sudo or executes
the initializer. It publishes a public host-key pin, observes the canonical
lock for at most ten minutes, and leaves owned STOP to hosted cleanup.
"""
import argparse
import base64
import fcntl
import hashlib
import json
import os
from pathlib import Path
import pwd
import signal
import stat
import struct
import subprocess
import sys
import time

import mustang_controller as controller
import mustang_ephemeral as ephemeral
import mustang_hostdeps as deps
import mustang_lane
import mustang_ota

ROOT = Path(__file__).resolve().parents[1]
TIMEOUT = 600
PUBLIC_KEY = Path('/etc/ssh/ssh_host_ed25519_key.pub')
READY = 'mustang-lock-admin-ready.json'
RESULT = 'mustang-lock-admin-result.json'


def host_command(argv, **kwargs):
    if not argv or argv[0] != 'findmnt':
        raise ValueError('unexpected host identity command')
    return subprocess.run(['/usr/bin/findmnt', *argv[1:]], timeout=10,
                          env={'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8'}, **kwargs)


def guard(env, event):
    lane = mustang_lane.validate(mustang_lane.configuration())
    run = controller.invocation(lane, env)
    inputs = event.get('inputs') if isinstance(event, dict) else None
    known = {'lock_admin_only', 'publish', 'dry_run', 'source_only', 'hostdeps_only'}
    disabled = lambda value: value is None or value is False or (type(value) is str and value == 'false')
    if env.get('GITHUB_REPOSITORY_ID') != '1428996648' or not isinstance(inputs, dict) \
            or set(inputs)-known or not (inputs.get('lock_admin_only') is True or inputs.get('lock_admin_only') == 'true') \
            or any(not disabled(inputs.get(key)) for key in known-{'lock_admin_only'}):
        raise ValueError('lock administration requires its exclusive fresh manual scope')
    ephemeral.require_group5(json.loads((ROOT/'os/build-only/mustang-runtime.json').read_text()))
    deps.readers.read_regular(str(ROOT), str(ROOT/'scripts/mustang-prepare-lock.py'), 1 << 20)
    return lane, run


def guest(env):
    lane, run = guard(env, deps.read_event(env))
    if env.get('GITHUB_JOB') != 'lock-admin' or pwd.getpwuid(os.geteuid()).pw_name != 'mustang-runner':
        raise ValueError('lock administration requires its unprivileged dedicated job')
    cfg = json.loads((ROOT/'os/build-only/mustang-runtime.json').read_text())
    dedicated = mustang_ota.module('mustang_lock_admin_identity', 'mustang-dedicated.py')
    paths = dedicated.host_identity(lane, cfg['dataFilesystemUuid'], execute=host_command, mount=cfg['dataMount'])
    for path in (Path(lane['sourceRoot']), paths['storageRoot']):
        if deps.plain_dir(path).st_dev != deps.plain_dir(paths['home']).st_dev:
            raise ValueError('lock administration leaves the dedicated data filesystem')
    directory = Path(env['RUNNER_TEMP'])
    deps.plain_dir(directory)
    clean = deps.child_env(paths['home']/'runner', directory)
    git = ['/usr/bin/git', '-c', 'core.fsmonitor=false', '-c', 'core.hooksPath=/dev/null', '-C', str(ROOT)]
    deadline = time.monotonic()+60
    if deps.checked(git+['rev-parse', 'HEAD'], ROOT, clean, deadline) != run['source'] \
            or deps.checked(git+['status', '--porcelain'], ROOT, clean, deadline):
        raise ValueError('lock administration checkout differs from captured clean source')
    parent = deps.plain_dir(paths['home'])
    if parent.st_uid != 0 or parent.st_gid != 0 or stat.S_IMODE(parent.st_mode) != 0o755:
        raise ValueError('canonical lock parent differs from the observed root-owned directory')
    identity = dict(run=run, builder=lane['vm'], diskUuid=cfg['dataFilesystemUuid'])
    return lane, identity, directory


def parse_host_key(data):
    parts = data.split()
    if len(parts) < 2 or parts[0] != b'ssh-ed25519':
        raise ValueError('public SSH host key is not the expected ed25519 format')
    try:
        blob = base64.b64decode(parts[1], validate=True)
    except ValueError:
        raise ValueError('public SSH host key encoding differs') from None
    if len(blob) != 51 or blob[:15] != struct.pack('>I', 11)+b'ssh-ed25519' \
            or blob[15:19] != struct.pack('>I', 32):
        raise ValueError('public SSH host key structure differs')
    return {'publicKey': 'ssh-ed25519 '+base64.b64encode(blob).decode(),
            'sha256Fingerprint': 'SHA256:'+base64.b64encode(hashlib.sha256(blob).digest()).decode().rstrip('=')}


def public_host_key(path=PUBLIC_KEY):
    fd, before = deps.readers._open_regular(str(path.parent), str(path))
    try:
        if before.st_uid != 0 or before.st_mode & 0o022 or not 0 < before.st_size <= 4096:
            raise ValueError('public SSH host key ownership, mode or size differs')
        data = os.read(fd, 4097)
        after = os.fstat(fd)
        identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
        if identity(before) != identity(after) or len(data) != before.st_size:
            raise ValueError('public SSH host key changed during observation')
        return parse_host_key(data)
    finally:
        os.close(fd)


def validate_lock_info(info):
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_gid != 0 \
            or stat.S_IMODE(info.st_mode) != 0o644 or info.st_nlink != 1 or info.st_size != 0:
        raise ValueError('canonical lock entry differs; preserve and reconcile it')


def observe_lock(path):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    except FileNotFoundError:
        return None
    try:
        info = os.fstat(fd)
        validate_lock_info(info)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError('canonical lock is busy; no adoption') from None
        current = os.lstat(path)
        if (current.st_dev, current.st_ino) != (info.st_dev, info.st_ino):
            raise ValueError('canonical lock changed during observation')
        return {'uid': info.st_uid, 'gid': info.st_gid, 'mode': stat.S_IMODE(info.st_mode),
                'sizeBytes': info.st_size, 'device': info.st_dev, 'inode': info.st_ino, 'available': True}
    finally:
        os.close(fd)


def ready(env):
    lane, identity, directory = guest(env)
    observed = observe_lock(Path(lane['lockPath']))
    helper = deps.readers.read_regular(str(ROOT), str(ROOT/'scripts/mustang-prepare-lock.py'), 1 << 20)
    result = {'schema': 'roseos.mustang-lock-admin-ready.v1', 'identity': identity,
              'scope': 'canonical-lock-administration-only', 'guestIdentityVerified': True,
              'lockPath': lane['lockPath'], 'lockInitiallyAbsent': observed is None,
              'externalActionRequired': observed is None, 'existingLock': observed,
              'initializerSha256': hashlib.sha256(helper).hexdigest(),
              'publicHostKey': public_host_key() if observed is None else None,
              'waitTimeoutSeconds': TIMEOUT, 'externalActionExecutedByJob': False,
              'credentialsCreatedByJob': False, 'buildStarted': False, 'authenticatedStop': False}
    deps.receipt(directory/READY, result)
    state = 'external-action-required' if observed is None else 'valid-lock-already-present'
    print('::notice title=Mustang lock administration::'+state)
    return result


def wait(env, clock=time.monotonic, sleep=time.sleep):
    lane, identity, directory = guest(env)
    raw = deps.readers.read_regular(str(directory), str(directory/READY), 65536)
    admitted = json.loads(raw)
    if admitted.get('schema') != 'roseos.mustang-lock-admin-ready.v1' or admitted.get('identity') != identity \
            or admitted.get('lockPath') != lane['lockPath'] or admitted.get('waitTimeoutSeconds') != TIMEOUT:
        raise ValueError('administrator readiness receipt belongs to another run or route')
    started = clock()
    result = {'schema': 'roseos.mustang-lock-admin-observation.v1', 'identity': identity,
              'scope': 'canonical-lock-administration-only', 'initialGuestIdentityVerified': True,
              'finalGuestIdentityVerified': False,
              'readySha256': hashlib.sha256(raw).hexdigest(), 'success': False,
              'externalActionExecutedByJob': False, 'credentialsCreatedByJob': False,
              'creatorAttested': False, 'buildStarted': False, 'authenticatedStop': False}
    try:
        while True:
            observed = observe_lock(Path(lane['lockPath']))
            if observed is not None:
                _lane, verified, _directory = guest(env)
                if verified != identity:
                    raise ValueError('guest identity changed during administrator window')
                result.update(success=True, finalGuestIdentityVerified=True, observedLock=observed)
                return result
            remaining = TIMEOUT-(clock()-started)
            if remaining <= 0:
                raise ValueError('administrator window expired without a valid canonical lock')
            sleep(min(5, remaining))
    except BaseException as error:
        result['failureType'] = type(error).__name__
        raise
    finally:
        result['elapsedSeconds'] = round(clock()-started, 3)
        deps.receipt(directory/RESULT, result)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('plan', 'ready', 'wait'), nargs='?', default='plan')
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args(argv)
    if args.action == 'plan' or not args.execute:
        print(json.dumps({'execute': False, 'waitTimeoutSeconds': TIMEOUT, 'argv': None,
                          'credentialsCreated': False, 'privilegedAction': None}, sort_keys=True))
        return 4
    try:
        if args.action == 'ready':
            ready(os.environ)
        else:
            def cancelled(_signum, _frame):
                raise ValueError('administrator window cancelled')
            previous = signal.signal(signal.SIGTERM, cancelled)
            try:
                wait(os.environ)
            finally:
                signal.signal(signal.SIGTERM, previous)
        return 0
    except (ValueError, OSError, KeyError, TypeError, subprocess.SubprocessError, deps.readers._Refused) as error:
        reasons = {
            'lock administration requires its exclusive fresh manual scope': 'scope-refused',
            'lock administration requires its unprivileged dedicated job': 'job-or-user-refused',
            'lock administration checkout differs from captured clean source': 'checkout-differs',
            'canonical lock parent differs from the observed root-owned directory': 'parent-differs',
            'public SSH host key ownership, mode or size differs': 'host-key-metadata-refused',
            'public SSH host key changed during observation': 'host-key-changed',
            'public SSH host key is not the expected ed25519 format': 'host-key-format-refused',
            'public SSH host key encoding differs': 'host-key-format-refused',
            'public SSH host key structure differs': 'host-key-format-refused',
            'canonical lock entry differs; preserve and reconcile it': 'lock-entry-refused',
            'canonical lock is busy; no adoption': 'lock-busy',
            'canonical lock changed during observation': 'lock-changed',
            'administrator readiness receipt belongs to another run or route': 'foreign-readiness',
            'administrator window expired without a valid canonical lock': 'window-expired',
            'administrator window cancelled': 'cancelled',
            'guest identity changed during administrator window': 'guest-identity-changed',
        }
        code = reasons.get(str(error), 'admission-refused') if type(error) is ValueError else (
            'permission-denied' if isinstance(error, PermissionError) else 'required-path-unreadable')
        print('::error title=Mustang lock administration::'+args.action+': '+code)
        print(json.dumps({'success': False, 'failureType': type(error).__name__, 'failureCode': code,
                          'externalActionExecutedByJob': False, 'authenticatedStop': False}), file=sys.stderr)
        return 4


if __name__ == '__main__':
    sys.exit(main())
