#!/usr/bin/env python3
"""EXP006-ISOLATED-TRIAL-001: the checks a job on the isolated self-hosted trial runner must pass before any build,
SDK or emulator step. It changes nothing on the host: no chmod, ACL, group, sudo or service.

  trial_preflight.py --evidence EDIR

  * the job is this repository's (id REPOSITORY_ID) systemui-persistent-experiment.yml on refs/heads/main, started by
    workflow_dispatch, on a self-hosted runner, with a 40-hex GITHUB_SHA;
  * the kvm_acl input is false: the trial never grants or restores a KVM ACL;
  * the job user is not root, `sudo -n true` does not succeed, and it is in no sudo/admin/wheel/docker/root group;
  * /dev/kvm is a character device (not a link) the job user can already open read/write;
  * DEADLINE_FILE, written as root at setup, is a regular non-link file owned by root and writable by nobody else, in
    a root-owned directory writable by nobody else, holding one integer: the trial's end in Unix seconds. At least
    MIN_REMAINING seconds (one 30-minute job plus cleanup) must remain, and it may not lie beyond MAX_AHEAD;
  * the checked-out workspace keeps no git credential (no http extraheader).
The paths are constants, never taken from the environment or the command line. EDIR/trial-preflight.json records the
fixed-word facts (numbers, modes, ids; no paths or text from the host). Exit 0 when every check passes, else 1.
"""
import argparse
import grp
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time

REPOSITORY_ID = '1428996648'
WORKFLOW_REF = 'Immortal-Protocols/RoseOS-1/.github/workflows/systemui-persistent-experiment.yml@refs/heads/main'
DEADLINE_FILE = Path('/etc/roseos-systemui-test/deadline')
KVM = Path('/dev/kvm')
MIN_REMAINING = 35 * 60
MAX_AHEAD = 2 * 60 * 60 + 5 * 60
PRIVILEGED_GROUPS = {'sudo', 'admin', 'wheel', 'docker', 'root', 'lxd', 'libvirt'}

def job_problems(env):
    problems = []
    expected = {'GITHUB_EVENT_NAME': 'workflow_dispatch', 'GITHUB_REF': 'refs/heads/main',
        'GITHUB_REPOSITORY_ID': REPOSITORY_ID, 'GITHUB_WORKFLOW_REF': WORKFLOW_REF, 'RUNNER_ENVIRONMENT': 'self-hosted',
        'KVM_ACL': 'false'}
    for key, value in expected.items():
        if env.get(key) != value:
            problems.append(f'{key} is not the trial value')
    if not re.fullmatch(r'[0-9a-f]{40}', env.get('GITHUB_SHA', '')):
        problems.append('GITHUB_SHA is not a 40-hex commit')
    return problems

def user_problems(uid, euid, group_names, sudo_succeeds):
    problems = []
    if uid == 0 or euid == 0:
        problems.append('the job user is root')
    if sudo_succeeds:
        problems.append('non-interactive sudo succeeds for the job user')
    privileged = sorted(set(group_names) & PRIVILEGED_GROUPS)
    if privileged:
        problems.append(f'the job user is in privileged groups {privileged}')
    return problems

def kvm_facts(path=KVM):
    """(problems, facts) for an already-usable /dev/kvm; opened read/write once and closed, never changed."""
    try:
        info = os.lstat(path)
    except OSError:
        return ['/dev/kvm is absent'], {'kvm': 'absent'}
    facts = {'kvm': 'present', 'kvmMode': oct(stat.S_IMODE(info.st_mode)), 'kvmUid': info.st_uid,
        'kvmGid': info.st_gid}
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISCHR(info.st_mode):
        return ['/dev/kvm is not a character device'], {**facts, 'kvm': 'not-character-device'}
    if info.st_mode & stat.S_IWOTH:
        return ['/dev/kvm is world-writable; the trial expects root:kvm 0660'], facts
    try:
        fd = os.open(path, os.O_RDWR | os.O_CLOEXEC)
    except OSError:
        return ['the job user cannot open /dev/kvm read/write'], {**facts, 'kvmUsable': False}
    os.close(fd)
    return [], {**facts, 'kvmUsable': True}

def _root_only(info):
    return info.st_uid == 0 and not info.st_mode & (stat.S_IWGRP | stat.S_IWOTH)

def deadline_facts(now, path=DEADLINE_FILE):
    """(problems, facts) for the root-owned trial deadline."""
    try:
        parent, info = os.lstat(path.parent), os.lstat(path)
    except OSError:
        return ['the trial deadline file is absent'], {'deadline': 'absent'}
    if stat.S_ISLNK(parent.st_mode) or not stat.S_ISDIR(parent.st_mode) or not _root_only(parent):
        return ['the trial deadline directory is not a root-only directory'], {'deadline': 'unsafe-directory'}
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode) or not _root_only(info):
        return ['the trial deadline file is not a root-owned regular file writable only by root'], \
            {'deadline': 'unsafe-file'}
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            text = os.read(fd, 64).decode('ascii')
        finally:
            os.close(fd)
    except (OSError, UnicodeDecodeError):
        return ['the trial deadline file is unreadable'], {'deadline': 'unreadable'}
    if not re.fullmatch(r'[0-9]{10}\n?', text):
        return ['the trial deadline file does not hold one Unix time'], {'deadline': 'malformed'}
    remaining = int(text) - int(now)
    facts = {'deadline': 'present', 'remainingSeconds': remaining}
    if remaining < MIN_REMAINING:
        return [f'{remaining} s remain before the trial deadline; at least {MIN_REMAINING} are needed'], facts
    if remaining > MAX_AHEAD:
        return ['the trial deadline lies beyond the approved two-hour trial'], facts
    return [], facts

def credential_problems(workspace, run=subprocess.run):
    if not workspace:
        return ['GITHUB_WORKSPACE is not set']
    found = run(['git', '-C', workspace, 'config', '--get-regexp', r'^http\..*\.extraheader$'], capture_output=True,
        text=True, timeout=30)
    return ['the checkout persisted a git credential'] if found.stdout.strip() else []

def check(env, uid, euid, group_names, sudo_succeeds, now, run=subprocess.run, kvm=KVM, deadline=DEADLINE_FILE):
    problems = job_problems(env) + user_problems(uid, euid, group_names, sudo_succeeds)
    kvm_problems, kvm = kvm_facts(kvm)
    time_problems, time_facts = deadline_facts(now, deadline)
    problems += kvm_problems + time_problems + credential_problems(env.get('GITHUB_WORKSPACE'), run)
    return problems, {'result': 'refused' if problems else 'pass', 'problems': problems, **kvm, **time_facts,
        'uidIsRoot': uid == 0, 'sudo': sudo_succeeds}

def _sudo_succeeds(run=subprocess.run):
    try:
        return run(['sudo', '-n', 'true'], capture_output=True, timeout=10, stdin=subprocess.DEVNULL).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False

def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--evidence', required=True)
    a = p.parse_args(argv)
    groups = []
    for gid in os.getgroups():
        try:
            groups.append(grp.getgrgid(gid).gr_name)
        except KeyError:
            groups.append(str(gid))
    problems, record = check(dict(os.environ), os.getuid(), os.geteuid(), groups, _sudo_succeeds(), time.time())
    evidence = Path(a.evidence)
    evidence.mkdir(parents=True, exist_ok=True)
    (evidence/'trial-preflight.json').write_text(json.dumps(record, indent=2, sort_keys=True) + '\n')
    for problem in problems:
        print(f'::error title=isolated trial preflight::{problem}')
    print(json.dumps({'result': record['result']}))
    return 1 if problems else 0

if __name__ == '__main__':
    sys.exit(main())
