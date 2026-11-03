#!/usr/bin/env python3
"""SSH-KEYGEN-OBSERVE: one bounded, read-only observation of the builder's installed ssh-keygen identity and its
package-database version, plus the pure contract that turns a complete observation into the profile/binding fields.
It establishes nothing by itself; every consumer re-verifies the sha256 at execution (signing-bootstrap tool_sha256,
factory-archive-sign pinned_file).

  observe --host immortal-os-builder   exit 0: complete report; 3: report printed, incomplete (problems listed);
                                       2: invalid usage (stderr only)
  bind --observation FILE              exit 0: the fields to commit; 4: not bindable (problems listed); 2: unreadable

Nothing is executed: no ssh-keygen, no dpkg, dpkg-query or rpm, no other process. Exactly these paths, fixed in source
(no argument chooses a path, no PATH lookup):
  lock        /home/pixelbuild/frankel-build.lock (os/release-pipeline/frankel.json host.lockPath)
  tool        /usr/bin/ssh-keygen (factory_signing's child PATH is /usr/bin:/bin)
  status      /var/lib/dpkg/status
  list        /var/lib/dpkg/info/openssh-client.list
  md5sums     /var/lib/dpkg/info/openssh-client.md5sums
  diversions  /var/lib/dpkg/diversions (absent means no diversions)
Every path is reached by blazer-binding-observe's descriptor walk: each directory component opened
O_DIRECTORY|O_NOFOLLOW relative to the previous descriptor and the leaf O_NOFOLLOW relative to its parent's, so a link
anywhere is reported, never followed. The leaf is fstat'ed regular and the same inode as its lstat, read within its own
byte cap, the aggregate cap and the deadline, then fstat'ed again; a changed inode, size or mtime reports `changed`.

observe, holding the shared builder lock for its whole duration:
  lock        taken exactly as blazer-binding-observe.take_lock: opened read-only and never created, exclusive
              non-blocking flock. Held by another job, absent or unusable: the report is incomplete with that reason and
              nothing else is read. It never waits.
  identity    sha256 (and md5, for the dpkg record) of the tool; the tool is root-owned, executable and not writable by
              group or others, and so is every ancestor directory (fstat of the walked descriptors).
  version     from dpkg's own database, parsed in-process: the single status stanza "Package: openssh-client" with
              Status "install ok installed", its Version and Architecture; the package's .list names /usr/bin/ssh-keygen;
              its .md5sums entry for usr/bin/ssh-keygen equals the tool's md5; no diversion names the tool. This is the
              PACKAGE-REPORTED version (1:9.6p1-3ubuntu13.5 -> upstream 9.6p1), not a version printed by the binary:
              OpenSSH's ssh-keygen.c has no version option (getopt "...V:...", where -V parses a certificate validity
              interval) and never prints SSH_RELEASE, so running it for a version would be a guess. The md5 comparison
              covers this one file only (dpkg's recorded digest); no other package file is read. Minimum 8.4, the first
              release whose readpass.c reads SSH_ASKPASS_REQUIRE, which factory_signing's askpass route needs.
              A host without /var/lib/dpkg/status (an rpm host) is reported, never queried: rpm's database is not parsed.
  recheck     after the reads, every file read is checked again (tool and database files re-hashed); any difference
              reports `changed`.
The report carries paths, sizes, modes, hashes, the package name/version/architecture and fixed codes only; no file
content beyond those parsed fields. Nothing is written, generated, signed, read from a secret or changed.

bindable is true only when complete. The observation does not replace factory_signing.self_check, which the profile
still requires in the approved bootstrap run.

bind reads the observation file (at most 64 KiB), re-checks every bindable condition and prints:
  {"installed": {"path", "sha256"},                        -> os/signing-bootstrap/profiles.json
                                                              profiles.<factory profile>.tools.ssh-keygen.installed
   "binding": {"sshKeygen": {"path", "sha256"}},           -> os/retained-rango/factory-signing.json sshKeygen
   "observedVersion": {...}, "observationSha256": ...}     (record in the commit/PR as the evidence identity)
It writes nothing and never invents a value: a hash appears only if a complete observation measured it.

Committed evidence (os/signing-bootstrap/ssh-keygen-evidence.json, schema roseos.ssh-keygen-evidence.v1) ties every
committed copy of the identity to one observation. committed_problems(root) is the pure check the tests run:
  unmeasured  observation and run are null; every copy (both factory profiles' tools.ssh-keygen.installed and
              factory-signing.json sshKeygen) is null; each factory profile still lists the measurement as missing.
  measured    run names the os-release.yml blazer-prep ssh-keygen-observe run (id, attempt, workflow commit, its artifact
              blazer-prep-ssh-keygen-observe-<run>-<attempt>); observation names the committed report bytes under
              os/signing-bootstrap/ by path and sha256; bind accepts those bytes; the Rango profile and factory-signing.json
              equal bind's fields; the Blazer profile is null or equal; a bound profile no longer lists the measurement.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
HOST = 'immortal-os-builder'
TOOL = Path('/usr/bin/ssh-keygen')
PACKAGE = 'openssh-client'
DPKG = Path('/var/lib/dpkg')
SCHEMA = 'roseos.ssh-keygen-observation.v1'
MINIMUM = (8, 4)
LIMITS = {'seconds': 120, 'toolBytes': 64 << 20, 'statusBytes': 32 << 20, 'recordBytes': 1 << 20,
          'totalBytes': 200 << 20, 'observationBytes': 64 << 10, 'outputBytes': 16 << 10}
UPSTREAM = re.compile(r'(?:[0-9]+:)?(([0-9]+)\.([0-9]+)(?:p[0-9]+)?)(?:-[A-Za-z0-9.+~_]+)?')
VERSION = re.compile(r'[A-Za-z0-9.+~:_-]{1,100}')
ARCH = re.compile(r'[A-Za-z0-9_-]{1,30}')
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


def files(tool=TOOL, dpkg=DPKG):
    return {'tool': (Path(tool), 'toolBytes'), 'status': (Path(dpkg)/'status', 'statusBytes'),
            'list': (Path(dpkg)/f'info/{PACKAGE}.list', 'recordBytes'),
            'md5sums': (Path(dpkg)/f'info/{PACKAGE}.md5sums', 'recordBytes'),
            'diversions': (Path(dpkg)/'diversions', 'recordBytes')}


class Budget:
    def __init__(self, clock=time.monotonic):
        self.clock, self.start, self.bytes = clock, clock(), 0
        self.deadline = self.start + LIMITS['seconds']

    def expired(self):
        return self.clock() > self.deadline


def unsafe_mode(info):
    return info.st_uid != 0 or info.st_mode & 0o022


def read_file(path, cap, budget, keep=False, owner=False):
    """({path, state, size, sha256, md5, mode, mtimeNs[, owner]}, bytes when keep) by the no-follow descriptor walk.
    With owner, the walked ancestors and the file must be root-owned and not group/other writable, the file executable."""
    row = {'path': str(path)}
    try:
        parent, leaf = OBSERVE.open_parent(path)
    except OBSERVE.Walk as walk:
        row.update(state=walk.state, component=str(walk.component))
        return row, None
    try:
        if owner and unsafe_mode(os.fstat(parent)):
            row['state'] = 'unsafe-ancestor'
            return row, None
        try:
            before = os.lstat(leaf, dir_fd=parent)
        except FileNotFoundError:
            row['state'] = 'absent'
            return row, None
        except OSError as error:
            row.update(state='error', error=type(error).__name__)
            return row, None
        if stat.S_ISLNK(before.st_mode):
            row['state'] = 'link'
            return row, None
        if not stat.S_ISREG(before.st_mode):
            row['state'] = 'not-regular'
            return row, None
        if before.st_size > cap or budget.bytes + before.st_size > LIMITS['totalBytes']:
            row.update(state='too-large', size=before.st_size)
            return row, None
        if owner and (unsafe_mode(before) or not before.st_mode & 0o111):
            row['state'] = 'unsafe-file'
            return row, None
        try:
            fd = os.open(leaf, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=parent)
        except OSError as error:
            row.update(state='error', error=type(error).__name__)
            return row, None
    finally:
        os.close(parent)
    try:
        opened = os.fstat(fd)
        if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino) or not stat.S_ISREG(opened.st_mode):
            row['state'] = 'changed'
            return row, None
        sha, md5, kept, size = hashlib.sha256(), hashlib.md5(), [], 0
        while True:
            if budget.expired():
                row['state'] = 'deadline'
                return row, None
            block = os.read(fd, 1 << 20)
            if not block:
                break
            size += len(block)
            if size > before.st_size:
                row['state'] = 'changed'
                return row, None
            sha.update(block)
            md5.update(block)
            if keep:
                kept.append(block)
        after = os.fstat(fd)
    finally:
        os.close(fd)
    budget.bytes += size
    if (after.st_ino, after.st_size, after.st_mtime_ns) != (before.st_ino, before.st_size, before.st_mtime_ns):
        row['state'] = 'changed'
        return row, None
    row.update(state='present', size=size, sha256=sha.hexdigest(), md5=md5.hexdigest(),
               mode=f'{stat.S_IMODE(before.st_mode):o}', mtimeNs=before.st_mtime_ns)
    if owner:
        row['owner'] = 'root, not group/other writable, executable; ancestors root-owned, not group/other writable'
    return row, (b''.join(kept) if keep else None)


def ancestors_safe(path):
    """True when every directory from / to the parent of PATH, opened no-follow, is root-owned and not group/other
    writable (read_file checks the last one again on the descriptor it reads through)."""
    fd = os.open('/', OBSERVE.DIR_OPEN)
    try:
        for part in Path(path).parts[1:-1]:
            if unsafe_mode(os.fstat(fd)):
                return False
            child = os.open(part, OBSERVE.DIR_OPEN, dir_fd=fd)
            os.close(fd)
            fd = child
        return not unsafe_mode(os.fstat(fd))
    except OSError:
        return False
    finally:
        os.close(fd)


def status_stanza(data):
    """{'Status', 'Version', 'Architecture'} of the single Package: openssh-client stanza, or a fixed problem code."""
    found = []
    for stanza in data.decode('utf-8', 'replace').split('\n\n'):
        fields = {}
        for line in stanza.split('\n'):
            if line[:1] not in (' ', '\t', '') and ': ' in line:
                key, value = line.split(': ', 1)
                fields.setdefault(key, value.strip())
        if fields.get('Package') == PACKAGE:
            found.append(fields)
    if not found:
        return 'status_package_missing'
    if len(found) > 1:
        return 'status_package_ambiguous'
    fields = found[0]
    if fields.get('Status') != 'install ok installed':
        return 'status_not_installed'
    if not VERSION.fullmatch(fields.get('Version', '')) or not ARCH.fullmatch(fields.get('Architecture', '')):
        return 'status_fields_invalid'
    return fields


def upstream_version(package_version):
    """('9.6p1', (9, 6)) from a dpkg package version, or None when it is not an OpenSSH-shaped version."""
    match = UPSTREAM.fullmatch(str(package_version))
    return (match.group(1), (int(match.group(2)), int(match.group(3)))) if match else None


def lock_path(root=ROOT):
    return OBSERVE.lock_path(root)


def observe(host, root=ROOT, tool=TOOL, dpkg=DPKG, lock=None, clock=time.monotonic, root_owned=True):
    """tool/dpkg/lock/root_owned exist for tests only; the CLI always reads the fixed paths under the committed lock."""
    if host != HOST:
        raise ValueError(f'--host must be {HOST}')
    lock = Path(lock or lock_path(root))
    held, reason = OBSERVE.take_lock(lock)
    if held is None:
        return {'schema': SCHEMA, 'host': host, 'complete': False, 'bindable': False,
                'lock': {'held': False, 'reason': reason}, 'problems': [reason]}
    try:
        return observe_locked(host, files(tool, dpkg), lock, clock, root_owned)
    finally:
        os.close(held)  # releases the flock


def observe_locked(host, targets, lock, clock, root_owned):
    budget = Budget(clock)
    report = {'schema': SCHEMA, 'host': host, 'limits': dict(LIMITS), 'lock': {'held': True, 'path': str(lock)},
              'minimumVersion': '.'.join(map(str, MINIMUM)), 'sourceBasis': SOURCE_BASIS,
              'evidence': 'installed file identity and the package-reported version from the dpkg database; nothing is '
                          'executed; not a replacement for factory_signing.self_check in the approved bootstrap run',
              'files': {}, 'problems': []}
    problems, data = report['problems'], {}
    for role, (path, cap) in targets.items():
        tool = role == 'tool'
        row, content = read_file(path, LIMITS[cap], budget, keep=not tool, owner=tool and root_owned)
        if tool and row['state'] == 'present' and root_owned and not ancestors_safe(path):
            row = {'path': str(path), 'state': 'unsafe-ancestor'}
        report['files'][role] = row
        if row['state'] == 'present':
            data[role] = content
        elif not (role == 'diversions' and row['state'] == 'absent'):
            problems.append(f'{role}: {row["state"]}')
    tool_row = report['files']['tool']
    package = {'manager': 'dpkg', 'source': 'database', 'name': PACKAGE}
    if 'status' in data:
        fields = status_stanza(data['status'])
        if isinstance(fields, str):
            problems.append(f'package: {fields}')
        else:
            package.update(version=fields['Version'], architecture=fields['Architecture'], status=fields['Status'])
    if 'list' in data:
        package['listsTool'] = str(targets['tool'][0]) in data['list'].decode('utf-8', 'replace').split('\n')
        if not package['listsTool']:
            problems.append(f'package: {PACKAGE}.list does not name the tool')
    if 'md5sums' in data and tool_row['state'] == 'present':
        relative = str(targets['tool'][0]).lstrip('/')
        recorded = [line.split(None, 1)[0] for line in data['md5sums'].decode('utf-8', 'replace').split('\n')
                    if len(line.split(None, 1)) == 2 and line.split(None, 1)[1] == relative]
        package['md5sumMatches'] = recorded == [tool_row['md5']]
        if not package['md5sumMatches']:
            problems.append('package: md5sums_mismatch' if recorded else 'package: md5sums_missing_tool')
    diversions = data.get('diversions', b'').decode('utf-8', 'replace').split('\n')
    package['diverted'] = any(line in (str(targets['tool'][0]), '/bin/ssh-keygen') for line in diversions)
    if package['diverted']:
        problems.append('package: tool_diverted')
    report['package'] = package
    if 'version' in package:
        parsed = upstream_version(package['version'])
        if parsed is None:
            report['version'] = {'source': 'package-database', 'upstream': None, 'meetsMinimum': False}
            problems.append('version: not an OpenSSH upstream version')
        else:
            report['version'] = {'source': 'package-database', 'upstream': parsed[0], 'meetsMinimum': parsed[1] >= MINIMUM}
            if parsed[1] < MINIMUM:
                problems.append(f'version: {parsed[0]} is below {report["minimumVersion"]}')
    for role, (path, cap) in targets.items():
        first = report['files'][role]
        if first['state'] != 'present':
            continue
        again, _ = read_file(path, LIMITS[cap], budget)
        if (again.get('sha256'), again.get('mtimeNs')) != (first['sha256'], first['mtimeNs']):
            problems.append(f'recheck: {role} {again["state"] if again["state"] != "present" else "changed"}')
    if budget.expired() and not any(p.startswith('deadline') for p in problems):
        problems.append('deadline: the observation exceeded its time bound')
    report['complete'] = not problems
    report['bindable'] = not problems
    report['bytesRead'] = budget.bytes
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
    tool = (report.get('files') or {}).get('tool') or {}
    if tool.get('path') != str(TOOL) or tool.get('state') != 'present' or not HEX64.fullmatch(str(tool.get('sha256'))):
        problems.append(f'observation.files.tool: no measured {TOOL} sha256')
    package = report.get('package') or {}
    if package.get('name') != PACKAGE or package.get('status') != 'install ok installed' or \
            package.get('listsTool') is not True or package.get('md5sumMatches') is not True or package.get('diverted') is not False:
        problems.append('observation.package: not an installed, undiverted owner whose md5sums match the tool')
    parsed = upstream_version(package.get('version'))
    if parsed is None or parsed[1] < MINIMUM or (report.get('version') or {}).get('upstream') != parsed[0]:
        problems.append(f'observation.version: not a consistent OpenSSH >= {".".join(map(str, MINIMUM))}')
    if problems:
        return None, problems
    installed = {'path': tool['path'], 'sha256': tool['sha256']}
    return {'installed': installed, 'binding': {'sshKeygen': dict(installed)},
            'observedVersion': {'source': 'package-database', 'upstream': parsed[0], 'package': PACKAGE,
                                'packageVersion': package['version'], 'architecture': package.get('architecture')},
            'observationSha256': hashlib.sha256(data).hexdigest()}, []


EVIDENCE_SCHEMA = 'roseos.ssh-keygen-evidence.v1'
EVIDENCE = 'os/signing-bootstrap/ssh-keygen-evidence.json'
EVIDENCE_DIR = 'os/signing-bootstrap'
PROFILES = 'os/signing-bootstrap/profiles.json'
BINDING = 'os/retained-rango/factory-signing.json'
BOUND_PROFILE, SHARED_PROFILES = 'fold-rango-factory-v1', ('pro-blazer-factory-v1',)
MEASURED_ENTRY = 'measured ssh-keygen installed identity and version on the builder'
RUN = {'workflow': 'os-release.yml', 'mode': 'blazer-prep', 'stage': 'ssh-keygen-observe'}
NULL_IDENTITY = {'path': None, 'sha256': None}


def committed_problems(root=ROOT):
    """[] when every committed copy of the builder's ssh-keygen identity agrees with the committed evidence; otherwise
    one line per disagreement. Pure: reads only the four committed files under root, never a host."""
    root = Path(root)
    try:
        evidence = json.loads((root/EVIDENCE).read_text())
        profiles = json.loads((root/PROFILES).read_text())['profiles']
        binding = json.loads((root/BINDING).read_text())
    except (OSError, ValueError, KeyError) as error:
        return [f'evidence: unreadable committed file ({type(error).__name__})']
    if not isinstance(evidence, dict) or evidence.get('schema') != EVIDENCE_SCHEMA:
        return [f'evidence: not a {EVIDENCE_SCHEMA} document']
    copies = {name: ((profiles.get(name) or {}).get('tools') or {}).get('ssh-keygen', {}).get('installed')
              for name in (BOUND_PROFILE, *SHARED_PROFILES)}
    copies[BINDING] = binding.get('sshKeygen') if isinstance(binding, dict) else None
    problems = []
    status = evidence.get('status')
    if status == 'unmeasured':
        if evidence.get('observation') is not None or evidence.get('run') is not None:
            problems.append('evidence: unmeasured evidence names an observation or run')
        problems += [f'{name}: bound without measured evidence' for name, value in copies.items() if value != NULL_IDENTITY]
        expected = None
    elif status == 'measured':
        expected, more = measured_fields(root, evidence)
        problems += more
        if expected is not None:
            for name in (BOUND_PROFILE, BINDING):
                if copies[name] != expected:
                    problems.append(f'{name}: not the measured identity')
            problems += [f'{name}: neither null nor the measured identity' for name in SHARED_PROFILES
                         if copies[name] not in (NULL_IDENTITY, expected)]
    else:
        return ['evidence: status must be unmeasured or measured']
    for name in (BOUND_PROFILE, *SHARED_PROFILES):
        listed = MEASURED_ENTRY in ((profiles.get(name) or {}).get('missing') or [])
        if listed != (copies[name] == NULL_IDENTITY):
            problems.append(f'{name}: the missing list must name the measurement exactly while the identity is null')
    return problems


def measured_fields(root, evidence):
    """(bind's installed fields, problems) for measured evidence: the named run, the exact committed report bytes."""
    problems, run, observation = [], evidence.get('run'), evidence.get('observation')
    if not isinstance(run, dict) or set(run) != {*RUN, 'runId', 'attempt', 'workflowSha', 'artifact'} \
            or any(run.get(k) != v for k, v in RUN.items()) or not re.fullmatch(r'[1-9][0-9]{0,19}', str(run.get('runId'))) \
            or not re.fullmatch(r'[1-9][0-9]{0,3}', str(run.get('attempt'))) \
            or not re.fullmatch(r'[0-9a-f]{40}', str(run.get('workflowSha'))) \
            or run.get('artifact') != f'blazer-prep-ssh-keygen-observe-{run.get("runId")}-{run.get("attempt")}':
        problems.append('evidence.run: not the os-release.yml blazer-prep ssh-keygen-observe run, attempt, commit and artifact')
    if not isinstance(observation, dict) or set(observation) != {'file', 'sha256'} \
            or not re.fullmatch(r'[A-Za-z0-9_.-]+\.json', Path(str(observation.get('file'))).name) \
            or str(Path(str(observation.get('file'))).parent) != EVIDENCE_DIR or not HEX64.fullmatch(str(observation.get('sha256'))):
        return None, problems + [f'evidence.observation: not a file under {EVIDENCE_DIR} with its sha256']
    try:
        with open(root/observation['file'], 'rb') as f:
            data = f.read(LIMITS['observationBytes'] + 1)
    except OSError:
        return None, problems + ['evidence.observation: the committed report is missing']
    if hashlib.sha256(data).hexdigest() != observation['sha256']:
        return None, problems + ['evidence.observation: sha256 is not the committed report bytes']
    fields, refused = binding_fields(data)
    if fields is None:
        return None, problems + [f'evidence.observation: bind refuses ({"; ".join(refused)})']
    return fields['installed'], problems


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
            report = observe(a.host)
        except (ValueError, KeyError, OSError) as error:
            print(json.dumps({'verdict': 'INVALID', 'error': type(error).__name__ + ': ' + str(error)[:200]}),
                  file=sys.stderr)
            return 2
        print(render(report))
        return 0 if report.get('complete') else 3
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
