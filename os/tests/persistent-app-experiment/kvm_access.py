#!/usr/bin/env python3
"""EXP006-KVM-001: a job-only /dev/kvm read/write ACL entry for the experiment's own non-root user, and its exact
restoration. Manual and default off: the workflow calls it only when its kvm_acl input is true, and only on a
GitHub-hosted Linux job. Executing a grant needs Nathan's approval of this exact delta; this file is the source.

  kvm_access.py grant   --state STATE_DIR --evidence EDIR
  kvm_access.py restore --state STATE_DIR --evidence EDIR

grant, before the experiment runs:
  * the actual job user (os.getuid, never an input) must be non-root, on a GitHub-hosted Linux runner;
  * /dev/kvm must be a root-owned character device, not a link; its identity (dev, ino, rdev, uid, gid, mode) and
    its exact numeric access ACL are saved in an owner-only STATE_DIR before anything changes;
  * getfacl, setfacl and non-interactive sudo must already exist; nothing is installed;
  * if the user can already read and write it, nothing is changed;
  * otherwise only `sudo -n setfacl --no-mask --modify u:<uid>:rw -- /dev/kvm` runs, and only when the existing
    mask (or, without one, the owning group's rights) already includes rw, so no other principal's effective
    rights can widen. An ACL that already names this user, or any entry kind but user/group/other/mask, refuses;
  * afterwards the device identity, mode and every other entry's effective rights must be unchanged, the only new
    entry must be user:<uid>:rw-, and the user must be able to open it read/write and get the KVM API version;
    any mismatch restores at once and refuses.
restore, from a shell trap and again from the always-run cleanup step: rewrites the saved ACL onto the hard-coded
/dev/kvm with `setfacl --set-file` (no path ever comes from a file) and verifies exact ACL, owner, group and mode
equality. It is idempotent; a replaced device is never touched. A hard cancellation or VM loss can prevent it: the
evidence then says the restoration was not observed, and only GitHub's disposable job VM bounds the grant.
No chmod, chown, group membership, udev rule, world write, package, service or persistent setting.
EDIR/kvm-access.json holds the bounded, non-secret before/after/cleanup record. Exit 0 ok, 1 refused or failed.
"""
import argparse
import fcntl
import json
import os
from pathlib import Path
import platform
import pwd
import shutil
import stat
import subprocess
import sys
import time

DEVICE = '/dev/kvm'
KVM_GET_API_VERSION = 0xAE00
STATE = 'kvm-acl-state.json'
EVIDENCE = 'kvm-access.json'
KINDS = ('user', 'group', 'other', 'mask')

class Refused(Exception):
    pass

class System:
    """Every boundary this module touches, so tests can replace it; the real one acts on DEVICE only."""
    def uid(self):
        return os.getuid()
    def username(self, uid):
        return pwd.getpwuid(uid).pw_name
    def environment(self):
        return {'GITHUB_ACTIONS': os.environ.get('GITHUB_ACTIONS'), 'RUNNER_ENVIRONMENT': os.environ.get('RUNNER_ENVIRONMENT'),
            'RUNNER_OS': os.environ.get('RUNNER_OS'), 'platform': platform.system()}
    def lstat(self, path):
        return os.lstat(path)
    def which(self, tool):
        return shutil.which(tool)
    def run(self, argv):
        return subprocess.run(argv, capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL)
    def access(self, path):
        return os.access(path, os.R_OK | os.W_OK)
    def kvm_api(self, path):
        fd = os.open(path, os.O_RDWR | os.O_CLOEXEC)
        try:
            return fcntl.ioctl(fd, KVM_GET_API_VERSION)
        finally:
            os.close(fd)

def identity(st):
    return {'dev': st.st_dev, 'ino': st.st_ino, 'rdev': st.st_rdev, 'uid': st.st_uid, 'gid': st.st_gid,
        'mode': stat.S_IMODE(st.st_mode)}

SAME_DEVICE = ('dev', 'ino', 'rdev', 'uid', 'gid')

def same_device(a, b):
    """The same device node; its mode follows the ACL mask, so it is compared separately."""
    return all(a[k] == b[k] for k in SAME_DEVICE)

def parse(text):
    """Access ACL entries (kind, qualifier, perms) from `getfacl --numeric --omit-header`; refuses anything else."""
    entries = []
    for line in text.splitlines():
        line = line.split('#', 1)[0].strip()
        if not line:
            continue
        parts = line.split(':')
        if len(parts) != 3 or parts[0] not in KINDS or len(parts[2]) != 3 or any(c not in 'rwx-' for c in parts[2]):
            raise Refused(f'unsupported ACL entry {line[:40]!r}')
        if parts[0] in ('other', 'mask') and parts[1] or parts[1] and not parts[1].isdigit():
            raise Refused(f'unsupported ACL entry {line[:40]!r}')
        entries.append((parts[0], parts[1], parts[2]))
    if len({(k, q) for k, q, _ in entries}) != len(entries) or not {('user', ''), ('group', ''), ('other', '')} <= {(k, q) for k, q, _ in entries}:
        raise Refused('the ACL is incomplete or repeats an entry')
    return entries

def text(entries):
    return ''.join(f'{k}:{q}:{p}\n' for k, q, p in entries)

def effective(entries):
    """{(kind, qualifier): effective perms} for every principal: group-class entries are bounded by the mask."""
    mask = next((p for k, q, p in entries if k == 'mask'), None)
    out = {}
    for k, q, p in entries:
        if k == 'mask':
            continue
        if mask and (k == 'group' or (k == 'user' and q)):
            p = ''.join(c if c != '-' and mask[i] != '-' else '-' for i, c in enumerate(p))
        out[(k, q)] = p
    return out

def has_rw(perms):
    return perms[0] == 'r' and perms[1] == 'w'

def read_acl(system):
    result = system.run(['getfacl', '--numeric', '--omit-header', '--absolute-names', '--', DEVICE])
    if result.returncode:
        result = system.run(['sudo', '-n', 'getfacl', '--numeric', '--omit-header', '--absolute-names', '--', DEVICE])
    if result.returncode:
        raise Refused(f'getfacl {DEVICE} exited {result.returncode}')
    return parse(result.stdout)

def device(system):
    try:
        st = system.lstat(DEVICE)
    except FileNotFoundError:
        raise Refused(f'{DEVICE} does not exist')
    if stat.S_ISLNK(st.st_mode):
        raise Refused(f'{DEVICE} is a link')
    if not stat.S_ISCHR(st.st_mode):
        raise Refused(f'{DEVICE} is not a character device')
    if st.st_uid != 0:
        raise Refused(f'{DEVICE} is not owned by root')
    return identity(st)

def write_private(directory, name, document):
    directory = Path(directory)
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(directory, 0o700)
    path = directory/name
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as f:
        json.dump(document, f, indent=2, sort_keys=True)
        f.write('\n')
    return path

def record(evidence, update):
    path = Path(evidence)/EVIDENCE
    document = json.loads(path.read_text()) if path.is_file() else {}
    document.update(update)
    Path(evidence).mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=2, sort_keys=True) + '\n')
    return document

def grant(state_dir, evidence, system=None, now=time.time):
    system = system or System()
    uid = system.uid()
    facts = {'device': DEVICE, 'uid': uid}
    try:
        env = system.environment()
        if env.get('platform') != 'Linux' or env.get('GITHUB_ACTIONS') != 'true' or env.get('RUNNER_ENVIRONMENT') != 'github-hosted':
            raise Refused('not a GitHub-hosted Linux job')
        if uid == 0:
            raise Refused('the job user is root; the experiment never runs as root')
        facts['user'] = system.username(uid)
        before = device(system)
        missing = [t for t in ('getfacl', 'setfacl', 'sudo') if not system.which(t)]
        if missing:
            raise Refused(f'tools unavailable: {missing}')
        if system.access(DEVICE):
            record(evidence, {'grant': {**facts, 'result': 'not-needed', 'reason': 'already readable and writable'},
                'restore': {'result': 'not-needed'}})
            write_private(state_dir, STATE, {'mutated': False, 'identity': before, 'uid': uid})
            return 0
        if system.run(['sudo', '-n', 'true']).returncode:
            raise Refused('non-interactive sudo is unavailable')
        original = read_acl(system)
        if any(k == 'user' and q == str(uid) for k, q, _ in original):
            raise Refused('the ACL already names this user')
        mask = next((p for k, q, p in original if k == 'mask'), None)
        bound = mask or next(p for k, q, p in original if k == 'group' and not q)
        if not has_rw(bound):
            raise Refused(f'the {"mask" if mask else "group"} rights {bound} would not let a rw entry take effect '
                'without widening other principals')
        write_private(state_dir, STATE, {'mutated': True, 'identity': before, 'uid': uid, 'acl': text(original),
            'at': now()})
        result = system.run(['sudo', '-n', 'setfacl', '--no-mask', '--modify', f'u:{uid}:rw', '--', DEVICE])
        if result.returncode:
            restore(state_dir, evidence, system, reason='setfacl failed')
            raise Refused(f'setfacl exited {result.returncode}')
        after, problems = device(system), []
        if after != before:
            problems.append('device identity or mode changed')
        acl = read_acl(system)
        added = [e for e in acl if e not in original]
        expected = [('user', str(uid), 'rw-')] + ([] if mask else [('mask', '', bound)])
        if sorted(added) != sorted(expected) or [e for e in original if e not in acl]:
            problems.append(f'unexpected ACL change {added}')
        old, new = effective(original), effective(acl)
        widened = [k for k in old if new.get(k) != old[k]]
        if widened:
            problems.append(f'effective rights changed for {widened}')
        if not problems and not system.access(DEVICE):
            problems.append('still not readable and writable by the job user')
        if not problems:
            try:
                api = system.kvm_api(DEVICE)
                facts['kvmApiVersion'] = api
                if api != 12:
                    problems.append(f'KVM API version {api}, expected 12')
            except OSError as error:
                problems.append(f'KVM did not answer: {type(error).__name__}')
        if problems:
            restore(state_dir, evidence, system, reason='grant verification failed')
            raise Refused('; '.join(problems))
        record(evidence, {'grant': {**facts, 'result': 'granted', 'before': text(original), 'after': text(acl)}})
        return 0
    except Refused as error:
        record(evidence, {'grant': {**facts, 'result': 'refused', 'reason': str(error)[:300]}})
        return 1

def restore(state_dir, evidence, system=None, reason='cleanup'):
    system = system or System()
    path = Path(state_dir)/STATE
    if not path.is_file():
        record(evidence, {'restore': {'result': 'not-needed', 'reason': 'no grant was recorded'}})
        return 0
    state = json.loads(path.read_text())
    if not state.get('mutated'):
        record(evidence, {'restore': {'result': 'not-needed', 'reason': 'nothing was changed'}})
        return 0
    try:
        if not same_device(device(system), state['identity']):
            raise Refused(f'{DEVICE} was replaced; it is not touched')
        original = parse(state['acl'])
        if read_acl(system) != original:
            snapshot = write_private(state_dir, 'kvm-acl-restore.txt', {})
            snapshot.write_text(text(original))
            result = system.run(['sudo', '-n', 'setfacl', f'--set-file={snapshot}', '--', DEVICE])
            snapshot.unlink()
            if result.returncode:
                raise Refused(f'setfacl --set-file exited {result.returncode}')
        if read_acl(system) != original or device(system) != state['identity']:
            raise Refused('the ACL, owner, group or mode differs from the original after restoring')
        record(evidence, {'restore': {'result': 'restored', 'reason': reason, 'acl': text(original)}})
        return 0
    except Refused as error:
        record(evidence, {'restore': {'result': 'restore-failed', 'reason': str(error)[:300]}})
        return 1

def main(argv=None, system=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('command', choices=['grant', 'restore'])
    p.add_argument('--state', required=True)
    p.add_argument('--evidence', required=True)
    a = p.parse_args(argv)
    code = (grant if a.command == 'grant' else restore)(a.state, a.evidence, system)
    print(json.dumps({a.command: 'ok' if code == 0 else 'refused'}))
    return code

if __name__ == '__main__':
    sys.exit(main())
