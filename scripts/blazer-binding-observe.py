#!/usr/bin/env python3
"""BLAZER-BINDING-OBSERVE (Release-owned): one bounded, read-only observation of exactly the host facts the blazer
integration and compile bindings still need. It establishes nothing by itself: it reports identities for a reviewed
binding commit, which every consuming step re-verifies at execution.

  observe --host immortal-os-builder

Exactly these paths, fixed in source (no argument chooses a path):
  files     the three signed Rose APKs and their sign-apk receipts and the background policy that Frankel run
            build 2026110301 retained (FRANKEL_RELEASE/signed-apps/<package>.apk|.receipt.json, background-policy.json);
            the copy inventory bound in os/build-only/blazer-integrate-host.json; and the tool files: aapt and the
            apksigner wrapper (os/release-pipeline/frankel.json host.tools), the wrapper's companion lib/apksigner.jar
            and the JDK java it runs (scripts/host-sdk-layout.py's layout)
  names     entry names only (never contents) directly under HOME/rose-actions that match blazer-integrate-<build> or
            blazer-reconcile-<build>, and under HOME/rose-actions/blazer-allocations that match <build>.json
  gitMeta   lstat type only of <isolated source root>/<project>/.git for the seven git-meta projects
  resources CPU count and affinity, MemTotal/MemAvailable, this process's cgroup v2 cpu.max and memory.max, and
            free/total bytes of the filesystem holding HOME/rose-actions

Each path is reached by a descriptor walk from /: every directory component is opened O_DIRECTORY|O_NOFOLLOW relative
to the previous descriptor and the leaf O_NOFOLLOW relative to its parent's, so a component that is or becomes a link is
refused at that step and never traversed (no out-of-scope byte is read). The leaf is fstat'ed regular and the same
inode as its lstat, hashed streamed within its byte cap and the global deadline, then fstat'ed again; a changed size,
mtime or inode reports `changed`. Directory names are enumerated from such a descriptor. The .git types prove presence
and type only, never a revision or a completed git-meta. Receipt and policy JSON are parsed only within
JSON_BYTES and only allowlisted identity fields are emitted (no other content). Nothing is written, invoked, signed,
allocated or changed. Partial results report complete:false with every reason; the output is at most OUTPUT_BYTES.
Serialization: the whole observation holds the existing shared builder lock (os/release-pipeline/frankel.json
host.lockPath), the exclusive non-blocking flock every builder writer takes (the preparation and compile stages, the
Frankel and blazer allocators, the rendered host-reconcile), so nothing it measures changes while it runs. The lock file
is reached by the same no-follow walk and opened read-only, never created; held by another job, absent or unusable, the
report is only complete:false with that reason and nothing is observed. It never waits, so the time bounds are unchanged.
Exit status: 0 report printed (complete true or false), 2 invalid usage.
"""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
HOST = 'immortal-os-builder'
HOME = Path('/home/pixelbuild')
FRANKEL_RELEASE = HOME/'rose-actions/frankel-2026110301/release'
PACKAGES = ('dev.pixel.backgroundcontrol', 'dev.pixel.voice', 'dev.pixel.calculator.agent')
GIT_META_PATHS = ('build/make', 'build/release', 'frameworks/base', 'packages/apps/Launcher3', 'packages/apps/Updater',
                  'packages/modules/Permission', 'vendor/adevtool')
SCHEMA = 'roseos.blazer-binding-observation.v1'
LIMITS = {'seconds': 300, 'fileBytes': 512 << 20, 'totalBytes': 1 << 30, 'jsonBytes': 64 << 10, 'nameEntries': 4096,
          'reportedNames': 256, 'outputBytes': 64 << 10, 'procBytes': 4096}
NAMES = re.compile(r'blazer-(?:integrate|reconcile)-[0-9]{10}')
RESERVATION = re.compile(r'[0-9]{10}\.json')
RECEIPT_FIELDS = ('schemaVersion', 'apk', 'sha256', 'certificateSha256', 'signatureVerified')
POLICY_ROW_FIELDS = ('packageName', 'versionCode', 'apkSha256', 'certificates')


def targets(root=ROOT):
    """{role: absolute path} of exactly the observed files, from committed sources only."""
    frankel = json.loads((root/'os/release-pipeline/frankel.json').read_text())
    integrate = json.loads((root/'os/build-only/blazer-integrate-host.json').read_text())
    tools = frankel['host']['tools']
    wrapper = Path(tools['apksigner'])
    files = {}
    for package in PACKAGES:
        files[f'apk:{package}'] = FRANKEL_RELEASE/'signed-apps'/f'{package}.apk'
        files[f'receipt:{package}'] = FRANKEL_RELEASE/'signed-apps'/f'{package}.receipt.json'
    files['policy'] = FRANKEL_RELEASE/'background-policy.json'
    inventory = integrate['sourceInventory']['path']
    files['inventory'] = Path(inventory) if inventory else None  # unbound until the binding names it
    files['tool:aapt'] = Path(tools['aapt'])
    files['tool:apksigner'] = wrapper
    files['tool:apksigner.jar'] = wrapper.parent/'lib/apksigner.jar'
    files['tool:java'] = Path(frankel['host']['sourceRoot'])/'prebuilts/jdk/jdk21/linux-x86/bin/java'
    for role, path in files.items():
        if path is not None and (not path.is_absolute() or '..' in path.parts):
            raise ValueError(f'{role}: not an absolute path')
    return files


class Budget:
    def __init__(self, clock=time.monotonic):
        self.clock, self.deadline, self.bytes = clock, clock() + LIMITS['seconds'], 0

    def expired(self):
        return self.clock() > self.deadline


DIR_OPEN = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC


class Walk(Exception):
    """A path component that is not a plain directory reached without links: state and the component."""
    def __init__(self, state, component, target=None):
        super().__init__(state)
        self.state, self.component, self.target = state, component, target


def open_parent(path):
    """(parent directory fd, leaf name): every component from / opened O_DIRECTORY|O_NOFOLLOW relative to the previous
    descriptor, so a component replaced by a link at any moment is refused, never traversed. The caller closes the fd."""
    path = Path(path)
    fd = os.open('/', DIR_OPEN)
    walked = Path('/')
    try:
        for part in path.parts[1:-1]:
            walked = walked/part
            try:
                child = os.open(part, DIR_OPEN, dir_fd=fd)
            except FileNotFoundError:
                raise Walk('absent', walked) from None
            except OSError:
                try:
                    info = os.lstat(part, dir_fd=fd)
                except OSError:
                    raise Walk('error', walked) from None
                if stat.S_ISLNK(info.st_mode):
                    raise Walk('link', walked, os.readlink(part, dir_fd=fd)[:256]) from None
                raise Walk('not-directory', walked) from None
            os.close(fd)
            fd = child
        return fd, path.parts[-1]
    except BaseException:
        os.close(fd)
        raise


def observe_file(path, budget, keep=False):
    """({state, size, sha256, ...}, the bytes when keep and small). The leaf is opened O_NOFOLLOW relative to its
    parent's descriptor from a no-follow walk: no link anywhere is followed and no out-of-scope byte is read."""
    row = {'path': str(path)}
    try:
        parent, leaf = open_parent(path)
    except Walk as walk:
        row.update(state=walk.state, component=str(walk.component), **({'target': walk.target} if walk.target else {}))
        return row, None
    try:
        try:
            before = os.lstat(leaf, dir_fd=parent)
        except FileNotFoundError:
            row['state'] = 'absent'
            return row, None
        except OSError as error:
            row.update(state='error', error=type(error).__name__)
            return row, None
        if stat.S_ISLNK(before.st_mode):
            row.update(state='link', component=str(path), target=os.readlink(leaf, dir_fd=parent)[:256])
            return row, None
        if not stat.S_ISREG(before.st_mode):
            row['state'] = 'not-regular'
            return row, None
        if before.st_size > LIMITS['fileBytes'] or budget.bytes + before.st_size > LIMITS['totalBytes']:
            row.update(state='too-large', size=before.st_size)
            return row, None
        try:
            fd = os.open(leaf, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=parent)
        except OSError as error:
            row.update(state='link' if error.errno == 40 else 'error', error=type(error).__name__)
            return row, None
    finally:
        os.close(parent)
    try:
        opened = os.fstat(fd)
        if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino) or not stat.S_ISREG(opened.st_mode):
            row['state'] = 'changed'
            return row, None
        digest, kept, size = hashlib.sha256(), [], 0
        while True:
            if budget.expired():
                row['state'] = 'deadline'
                return row, None
            block = os.read(fd, 8 << 20)
            if not block:
                break
            size += len(block)
            if size > before.st_size:
                row['state'] = 'changed'
                return row, None
            digest.update(block)
            if keep and size <= LIMITS['jsonBytes']:
                kept.append(block)
        after = os.fstat(fd)
    finally:
        os.close(fd)
    budget.bytes += size
    if (after.st_ino, after.st_size, after.st_mtime_ns) != (before.st_ino, before.st_size, before.st_mtime_ns) or size != before.st_size:
        row['state'] = 'changed'
        return row, None
    row.update(state='present', size=size, sha256=digest.hexdigest(), mode=f'{stat.S_IMODE(before.st_mode):o}',
               mtimeNs=before.st_mtime_ns)
    return row, (b''.join(kept) if keep and size <= LIMITS['jsonBytes'] else None)


def open_directory(path):
    """A descriptor of PATH itself, every component (the last included) opened no-follow; Walk otherwise."""
    parent, leaf = open_parent(path)
    try:
        try:
            return os.open(leaf, DIR_OPEN, dir_fd=parent)
        except FileNotFoundError:
            raise Walk('absent', Path(path)) from None
        except OSError:
            try:
                info = os.lstat(leaf, dir_fd=parent)
            except OSError:
                raise Walk('error', Path(path)) from None
            raise Walk('link' if stat.S_ISLNK(info.st_mode) else 'not-directory', Path(path)) from None
    finally:
        os.close(parent)


def receipt_identity(data):
    try:
        receipt = json.loads(data)
    except (ValueError, TypeError):
        return {'parsed': False}
    if not isinstance(receipt, dict):
        return {'parsed': False}
    return {'parsed': True, **{k: receipt.get(k) for k in RECEIPT_FIELDS if k in receipt}}


def policy_identity(data):
    try:
        policy = json.loads(data)
    except (ValueError, TypeError):
        return {'parsed': False}
    if not isinstance(policy, dict):
        return {'parsed': False}
    row = lambda r: {k: r.get(k) for k in POLICY_ROW_FIELDS if k in r} if isinstance(r, dict) else None
    targets_ = policy.get('targets')
    return {'parsed': True, 'schemaVersion': policy.get('schemaVersion'), 'sdk': policy.get('sdk'),
            'prototypeControl': policy.get('prototypeControl'), 'agent': row(policy.get('agent')),
            'voice': row(policy.get('voice')),
            'targets': [row(t) for t in targets_[:8]] if isinstance(targets_, list) else None}


def matching_names(directory, pattern, budget):
    """(matching names, other count, state) of one directory level, enumerated from a descriptor reached by a no-follow
    walk; names only, never contents, never a deeper level."""
    try:
        fd = open_directory(directory)
    except Walk as walk:
        return [], 0, walk.state
    names, others, scanned = [], 0, 0
    try:
        with os.scandir(fd) as it:
            for entry in it:
                scanned += 1
                if scanned > LIMITS['nameEntries'] or budget.expired():
                    return sorted(names)[:LIMITS['reportedNames']], others, 'truncated'
                if pattern.fullmatch(entry.name):
                    names.append(entry.name)
                else:
                    others += 1
    finally:
        os.close(fd)
    if len(names) > LIMITS['reportedNames']:
        return sorted(names)[:LIMITS['reportedNames']], others, 'truncated'
    return sorted(names), others, 'listed'


def small_read(path):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        return None
    try:
        return os.read(fd, LIMITS['procBytes']).decode(errors='replace')
    finally:
        os.close(fd)


def resources(proc=Path('/proc'), cgroup_root=Path('/sys/fs/cgroup'), statvfs=os.statvfs, storage=HOME/'rose-actions'):
    out = {'cpuCount': os.cpu_count()}
    try:
        out['cpuAffinity'] = len(os.sched_getaffinity(0))
    except (AttributeError, OSError):
        out['cpuAffinity'] = None
    meminfo = small_read(proc/'meminfo') or ''
    for key in ('MemTotal', 'MemAvailable'):
        match = re.search(rf'^{key}:\s+(\d+) kB$', meminfo, re.M)
        out[f'{key[0].lower()}{key[1:]}Bytes'] = int(match[1]) * 1024 if match else None
    group = small_read(proc/'self/cgroup') or ''
    match = re.search(r'^0::(/[^\n]*)$', group, re.M)
    out['cgroup'] = None
    if match and '..' not in match[1]:
        base = cgroup_root/match[1].lstrip('/')
        out['cgroup'] = {'path': match[1][:256], **{name.replace('.', '_'): (small_read(base/name) or '').strip()[:64] or None
                                                    for name in ('cpu.max', 'memory.max')}}
    try:
        fs = statvfs(storage)
        out['storage'] = {'path': str(storage), 'availableBytes': fs.f_bavail * fs.f_frsize, 'totalBytes': fs.f_blocks * fs.f_frsize}
    except OSError as error:
        out['storage'] = {'path': str(storage), 'error': type(error).__name__}
    return out


def lock_path(root=ROOT):
    return Path(json.loads((root/'os/release-pipeline/frankel.json').read_text())['host']['lockPath'])


def take_lock(path):
    """An fd holding the exclusive non-blocking flock on PATH, or the reason it is not held. Never creates or waits."""
    try:
        parent, leaf = open_parent(path)
    except Walk as walk:
        return None, f'lock {walk.state} at {walk.component}'
    try:
        try:
            if stat.S_ISLNK(os.lstat(leaf, dir_fd=parent).st_mode):
                return None, f'lock link at {path}'
        except FileNotFoundError:
            return None, 'lock absent'
        fd = os.open(leaf, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent)
    except FileNotFoundError:
        return None, 'lock absent'
    except OSError as error:
        return None, f'lock unusable ({type(error).__name__})'
    finally:
        os.close(parent)
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        return None, 'lock is not a regular file'
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        return None, 'lock_held: the shared builder lock is held by another job'
    except OSError as error:
        os.close(fd)
        return None, f'lock unusable ({type(error).__name__})'
    return fd, None


def observe(host, root=ROOT, clock=time.monotonic, proc=Path('/proc'), cgroup_root=Path('/sys/fs/cgroup'), statvfs=os.statvfs,
            home=HOME, files=None, source=None, lock=None):
    """files/source/home exist for tests only; the CLI always observes targets() and the committed source root."""
    if host != HOST:
        raise ValueError(f'--host must be {HOST}')
    held, reason = take_lock(lock or lock_path(root))
    if held is None:
        return json.dumps({'schema': SCHEMA, 'host': host, 'complete': False, 'lock': {'held': False, 'reason': reason},
                           'problems': [reason]}, sort_keys=True)
    try:
        return observe_locked(host, root, clock, proc, cgroup_root, statvfs, home, files, source, lock or lock_path(root))
    finally:
        os.close(held)  # releases the flock


def observe_locked(host, root, clock, proc, cgroup_root, statvfs, home, files, source, lock):
    budget = Budget(clock)
    report = {'schema': SCHEMA, 'host': host, 'limits': dict(LIMITS), 'lock': {'held': True, 'path': str(lock)},
              'files': {}, 'identities': {}, 'problems': []}
    for role, path in (files or targets(root)).items():
        keep = role.startswith('receipt:') or role == 'policy'
        if path is None:
            report['files'][role] = {'path': None, 'state': 'unbound'}
            report['problems'].append(f'{role}: unbound')
            continue
        row, data = observe_file(path, budget, keep)
        report['files'][role] = row
        if row['state'] != 'present':
            report['problems'].append(f'{role}: {row["state"]}')
        elif keep:
            report['identities'][role] = (policy_identity if role == 'policy' else receipt_identity)(data) \
                if data is not None else {'parsed': False, 'why': 'larger than jsonBytes'}
    storage = home/'rose-actions'
    names, others, state = matching_names(storage, NAMES, budget)
    reservations, reservation_others, reservation_state = matching_names(storage/'blazer-allocations', RESERVATION, budget)
    report['names'] = {'storage': {'state': state, 'blazer': names, 'otherEntries': others},
                       'reservations': {'state': reservation_state, 'names': reservations, 'otherEntries': reservation_others}}
    if state not in ('listed',):
        report['problems'].append(f'storage names: {state}')
    if reservation_state not in ('listed', 'absent'):
        report['problems'].append(f'reservation names: {reservation_state}')
    source = Path(source or json.loads((root/'os/build-only/blazer-prep-host.json').read_text())['sourceRoot'])
    git = {}
    for project in GIT_META_PATHS:
        try:
            fd = open_directory(source/project)
        except Walk as walk:
            git[project] = f'project {walk.state}'
            continue
        try:
            mode = os.lstat('.git', dir_fd=fd).st_mode
            git[project] = 'directory' if stat.S_ISDIR(mode) else 'link' if stat.S_ISLNK(mode) else 'file' \
                if stat.S_ISREG(mode) else 'other'
        except FileNotFoundError:
            git[project] = 'absent'
        except OSError as error:
            git[project] = type(error).__name__
        finally:
            os.close(fd)
    report['gitMeta'] = {'sourceRoot': str(source), 'dotGit': git,
                         'evidence': 'lstat type of each .git only: presence and type, never a revision or a completed '
                                     'git-meta; the original Infra git-meta receipts are the revision and completion evidence'}
    report['resources'] = resources(proc, cgroup_root, statvfs, storage)
    report['complete'] = not report['problems'] and not budget.expired()
    report['elapsedSeconds'] = round(clock() - (budget.deadline - LIMITS['seconds']), 3)
    text = json.dumps(report, sort_keys=True, separators=(',', ':'))
    if len(text.encode()) > LIMITS['outputBytes']:
        text = json.dumps({'schema': SCHEMA, 'host': host, 'complete': False,
                           'problems': ['output: the report exceeds outputBytes']}, sort_keys=True)
    return text


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('command', choices=('observe',))
    p.add_argument('--host', required=True)
    a = p.parse_args(argv)
    try:
        print(observe(a.host))
        return 0
    except (ValueError, KeyError, OSError) as error:
        print(json.dumps({'verdict': 'INVALID', 'error': str(error)[:200]}), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
