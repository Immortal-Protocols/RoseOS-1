#!/usr/bin/env python3
"""SSH-KEYGEN-OBSERVE: one bounded, read-only observation of the builder's installed ssh-keygen identity and version,
plus the pure contract that turns a complete observation into the profile/binding fields. It establishes nothing by
itself; every consumer re-verifies the sha256 at execution (signing-bootstrap tool_sha256, factory-archive-sign
pinned_file).

  observe --host immortal-os-builder        exit 0: report printed (complete true or false); 2: invalid usage
  bind --observation FILE                   exit 0: the fields to commit; 4: not bindable (problems listed)

Exactly one path, fixed in source: /usr/bin/ssh-keygen (factory_signing's child PATH is /usr/bin:/bin; no argument
chooses a path and no PATH lookup is made).

observe, holding the shared builder lock for its whole duration:
  lock      os/release-pipeline/frankel.json host.lockPath, taken exactly as blazer-binding-observe.take_lock does:
            no-follow walk, opened read-only and never created, exclusive non-blocking flock. Held by another job,
            absent or unusable, the report is complete:false with that reason and nothing else is observed. It never
            waits.
  identity  the file hashed by blazer-binding-observe.observe_file: every directory component opened
            O_DIRECTORY|O_NOFOLLOW relative to the previous descriptor and the leaf O_NOFOLLOW, fstat'ed regular and
            the same inode as its lstat, streamed within the byte cap and deadline, then fstat'ed again. A link
            anywhere is reported, never followed.
  safety    fold_tool_proof.host_file: regular, executable, root-owned, not writable by group or others, ancestors
            likewise (merged-/usr aliases only); its sha256 must equal the no-follow hash.
  version   from the host's installed package database, with fold_tool_proof's allowlisted fixed argv only:
            dpkg-query -S /usr/bin/ssh-keygen, dpkg-query -W -f '...' <owner>, dpkg --verify <owner>  (or rpm -qf /
            rpm -V on an rpm host). ssh-keygen itself is never run: OpenSSH's ssh-keygen.c has no version option
            (getopt "...V:...", where -V parses a certificate validity interval) and never prints SSH_RELEASE, so any
            "version flag" would be a guess. The upstream version is the package version without epoch and distro
            revision ("1:9.6p1-3ubuntu13.5" -> 9.6p1); minimum 8.4, the first release whose readpass.c reads
            SSH_ASKPASS_REQUIRE, which factory_signing's askpass route needs (absent at V_8_3_P1).
  recheck   the file is hashed again after the package queries; any difference reports `changed`.

Each package query is factory_signing.run_bounded: fixed argv, environment PATH=/usr/bin:/bin LC_ALL=C, stdin
/dev/null, its own process group, at most QUERY_SECONDS and QUERY_OUTPUT_BYTES (stderr merged), then killed. The
whole observation stops at LIMITS['seconds']; a step that would start past the deadline is reported, not run. The
report carries paths, sizes, modes, hashes, package name/version/architecture and fixed codes only; no tool output.
Nothing is written, generated, signed, read from a secret or changed.

bindable is true only when complete, the file is present, safe and unchanged, the package integrity check is clean and
the upstream version is >= 8.4. The observation does not replace factory_signing.self_check, which the profile still
requires in the approved bootstrap run.

bind reads the observation file (at most 64 KiB), re-checks every bindable condition and prints:
  {"installed": {"path", "sha256"},                        -> os/signing-bootstrap/profiles.json
                                                              profiles.<factory profile>.tools.ssh-keygen.installed
   "binding": {"sshKeygen": {"path", "sha256"}},           -> os/retained-rango/factory-signing.json sshKeygen
   "observedVersion": {...}, "observationSha256": ...}     (record in the commit/PR as the evidence identity)
It writes nothing and never invents a value: a hash appears only if a complete observation measured it.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
HOST = 'immortal-os-builder'
TOOL = Path('/usr/bin/ssh-keygen')
SCHEMA = 'roseos.ssh-keygen-observation.v1'
MINIMUM = (8, 4)
QUERY_SECONDS = 60
QUERY_OUTPUT_BYTES = 16 << 10
LIMITS = {'seconds': 300, 'querySeconds': QUERY_SECONDS, 'queryOutputBytes': QUERY_OUTPUT_BYTES,
          'observationBytes': 64 << 10, 'outputBytes': 16 << 10}
UPSTREAM = re.compile(r'(?:[0-9]+:)?(([0-9]+)\.([0-9]+)(?:p[0-9]+)?)(?:-[A-Za-z0-9.+~_]+)?')
HEX64 = re.compile(r'[0-9a-f]{64}')
SOURCE_BASIS = ('openssh/openssh-portable V_9_6_P1 (8241b9c0529228b4b86d88b1a6076fb9f97e4a99) ssh-keygen.c blob '
                '5b945a849202c24334b972669d66bcaaf6300ec4: getopt has no version option, -V is parse_cert_times; '
                'V_8_4_P1 (279261e1ea8150c7c64ab5fe7cb4a4ea17acbb29) readpass.c blob '
                '122d2a87c46555ce9c21537ce2f6c28d04358b3c reads SSH_ASKPASS_REQUIRE, V_8_3_P1 '
                '(9ca7e9c861775dd6c6312bc8aaab687403d24676) readpass.c blob 974d67f0bfa64cd0b08359dbfc53d98bd98ecf2e '
                'does not')


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


OBSERVE = _load('blazer_binding_observe_for_ssh_keygen', ROOT/'scripts/blazer-binding-observe.py')
PROOF = _load('fold_tool_proof_for_ssh_keygen', ROOT/'scripts/fold_tool_proof.py')
SIGNING = _load('factory_signing_for_ssh_keygen', ROOT/'scripts/factory_signing.py')


class Budget:
    """The interface observe_file expects (bytes, expired()), on this helper's own deadline."""
    def __init__(self, clock=time.monotonic):
        self.clock, self.start, self.bytes = clock, clock(), 0
        self.deadline = self.start + LIMITS['seconds']

    def expired(self):
        return self.clock() > self.deadline

    def remaining(self):
        return self.deadline - self.clock()


def bounded_runner(budget, run=SIGNING.run_bounded):
    """fold_tool_proof.package_identity's runner(argv) -> (code, text), through factory_signing.run_bounded."""
    def runner(argv):
        seconds = min(QUERY_SECONDS, budget.remaining())
        if seconds <= 0:
            raise PROOF.Refused('deadline')
        result = run(list(argv), dict(PROOF.ENVIRONMENT), '/', seconds, keep_output=True)
        if result['category'] in ('timeout', 'output_overflow', 'spawn_failed'):
            raise PROOF.Refused(f'query_{result["category"]}')
        return result['exit'], (result.get('output') or b'').decode('utf-8', 'replace')
    return runner


def upstream_version(package_version):
    """('9.6p1', (9, 6)) from a dpkg/rpm package version, or None when it is not an OpenSSH-shaped version."""
    match = UPSTREAM.fullmatch(str(package_version))
    return (match.group(1), (int(match.group(2)), int(match.group(3)))) if match else None


def lock_path(root=ROOT):
    return OBSERVE.lock_path(root)


def observe(host, root=ROOT, tool=TOOL, lock=None, clock=time.monotonic, safety=None, manager=None, run=None):
    """tool/lock/safety/manager/run exist for tests only; the CLI always observes TOOL under the committed lock."""
    if host != HOST:
        raise ValueError(f'--host must be {HOST}')
    lock = Path(lock or lock_path(root))
    held, reason = OBSERVE.take_lock(lock)
    if held is None:
        return {'schema': SCHEMA, 'host': host, 'complete': False, 'bindable': False,
                'lock': {'held': False, 'reason': reason}, 'problems': [reason]}
    try:
        return observe_locked(host, Path(tool), lock, clock, safety or PROOF.host_file,
                              manager or PROOF.package_manager, run or SIGNING.run_bounded)
    finally:
        os.close(held)  # releases the flock


def observe_locked(host, tool, lock, clock, safety, manager, run):
    budget = Budget(clock)
    report = {'schema': SCHEMA, 'host': host, 'limits': dict(LIMITS), 'lock': {'held': True, 'path': str(lock)},
              'minimumVersion': '.'.join(map(str, MINIMUM)), 'sourceBasis': SOURCE_BASIS,
              'evidence': 'installed file identity and package-database version; ssh-keygen is never run; '
                          'not a replacement for factory_signing.self_check in the approved bootstrap run',
              'problems': []}
    problems = report['problems']
    row, _ = OBSERVE.observe_file(tool, budget)
    report['tool'] = row
    if row['state'] != 'present':
        problems.append(f'tool: {row["state"]}')
    else:
        try:
            safe = safety(str(tool), '/', 0)
            report['tool']['safety'] = 'root-owned, not group/other writable, executable'
            if safe['sha256'] != row['sha256'] or safe.get('realPath', str(tool)) != str(tool):
                problems.append('tool: changed')
        except PROOF.Refused as error:
            report['tool']['safety'] = str(error)
            problems.append(f'tool: {error}')
    if not problems:
        runner = bounded_runner(budget, run)
        try:
            name = manager('/', 0)
            identity, integrity = PROOF.package_identity(name, str(tool), runner)
            report['package'] = dict(identity, integrity=integrity['result'], integrityTool=integrity['tool'])
        except PROOF.Refused as error:
            report['package'] = {'refused': str(error)}
            problems.append(f'package: {error}')
    if 'package' in report and 'version' in report['package']:
        parsed = upstream_version(report['package']['version'])
        if parsed is None:
            report['version'] = {'source': 'package-manager', 'upstream': None, 'meetsMinimum': False}
            problems.append('version: not an OpenSSH upstream version')
        else:
            report['version'] = {'source': 'package-manager', 'upstream': parsed[0], 'meetsMinimum': parsed[1] >= MINIMUM}
            if parsed[1] < MINIMUM:
                problems.append(f'version: {parsed[0]} is below {report["minimumVersion"]}')
    if row['state'] == 'present':
        again, _ = OBSERVE.observe_file(tool, budget)
        if again.get('sha256') != row['sha256'] or again.get('mtimeNs') != row['mtimeNs']:
            problems.append(f'recheck: {again["state"] if again["state"] != "present" else "changed"}')
    if budget.expired():
        problems.append('deadline: the observation exceeded its time bound')
    report['complete'] = not problems
    report['bindable'] = not problems
    report['elapsedSeconds'] = round(clock() - budget.start, 3)
    return report


def render(report):
    text = json.dumps(report, sort_keys=True, separators=(',', ':'))
    if len(text.encode()) > LIMITS['outputBytes']:
        text = json.dumps({'schema': SCHEMA, 'host': report.get('host'), 'complete': False, 'bindable': False,
                           'problems': ['output: the report exceeds outputBytes']}, sort_keys=True)
    return text


def binding_fields(data):
    """(fields, []) from a complete bindable observation's bytes, or (None, problems). Pure; writes nothing."""
    if len(data) > LIMITS['observationBytes']:
        return None, ['observation: larger than observationBytes']
    try:
        report = json.loads(data)
    except ValueError:
        return None, ['observation: not JSON']
    if not isinstance(report, dict) or report.get('schema') != SCHEMA:
        return None, [f'observation: not a {SCHEMA} report']
    problems = []
    if report.get('host') != HOST:
        problems.append(f'observation.host is not {HOST}')
    if report.get('complete') is not True or report.get('bindable') is not True or report.get('problems'):
        problems.append('observation: not complete and bindable')
    if not (report.get('lock') or {}).get('held'):
        problems.append('observation: the shared builder lock was not held')
    tool = report.get('tool') or {}
    if tool.get('path') != str(TOOL) or tool.get('state') != 'present' or not HEX64.fullmatch(str(tool.get('sha256'))):
        problems.append(f'observation.tool: no measured {TOOL} sha256')
    package = report.get('package') or {}
    if package.get('integrity') != 'clean':
        problems.append('observation.package: integrity not clean')
    parsed = upstream_version(package.get('version'))
    if parsed is None or parsed[1] < MINIMUM or (report.get('version') or {}).get('upstream') != parsed[0]:
        problems.append(f'observation.version: not a consistent OpenSSH >= {".".join(map(str, MINIMUM))}')
    if problems:
        return None, problems
    installed = {'path': tool['path'], 'sha256': tool['sha256']}
    return {'installed': installed, 'binding': {'sshKeygen': dict(installed)},
            'observedVersion': {'upstream': parsed[0], 'package': package.get('name'),
                                'packageVersion': package['version'], 'architecture': package.get('architecture'),
                                'manager': package.get('manager')},
            'observationSha256': hashlib.sha256(data).hexdigest()}, []


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest='command', required=True)
    o = sub.add_parser('observe')
    o.add_argument('--host', required=True)
    b = sub.add_parser('bind')
    b.add_argument('--observation', required=True)
    a = p.parse_args(argv)
    if a.command == 'observe':
        try:
            print(render(observe(a.host)))
            return 0
        except (ValueError, KeyError, OSError) as error:
            print(json.dumps({'verdict': 'INVALID', 'error': type(error).__name__ + ': ' + str(error)[:200]}),
                  file=sys.stderr)
            return 2
    try:
        with open(a.observation, 'rb') as f:
            data = f.read(LIMITS['observationBytes'] + 1)
    except OSError as error:
        print(json.dumps({'verdict': 'INVALID', 'error': type(error).__name__}), file=sys.stderr)
        return 2
    fields, problems = binding_fields(data)
    if fields is None:
        print(json.dumps({'bindable': False, 'problems': problems}, sort_keys=True))
        return 4
    print(json.dumps(fields, sort_keys=True))
    return 0


if __name__ == '__main__':
    sys.exit(main())
