"""Runtime-measured capacity budget for the os-release build and sign stages (OCT29-INFRA-S008).

A Budget is created under the existing source/output lock, before any stage write. It resolves the stage's write
paths to their filesystems (nearest existing ancestor, one entry per device) and requires every one to have strictly
more than floor + stop buffer available to this user (statvfs f_bavail * f_frsize). It then runs each owned child in
its own session/process group, sampling every filesystem at most every interval seconds, and copies parent-managed
files in checked chunks. A threshold crossing, stat error, stale sample or monitor exception stops the owned process
group: SIGTERM, up to term_grace seconds, then SIGKILL, and termination verified within kill_verify seconds. Only the
group this Budget created is ever signalled.

What this is not: the stop buffer is a deliberately conservative abort margin, not an estimate of OS build peak and
not a promise that a build fits. Cooperative sampling cannot enforce a hard filesystem quota, prevent an instantaneous
large allocation between samples, interrupt uninterruptible kernel I/O, or account for unrelated writers; a process
that leaves the group with its own setsid is outside it. A budgeted stage may abort safely; it never continues past a
crossing. Nothing is deleted except this stage's own partial copy.
"""
import contextlib
import ctypes
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

FLOOR_BYTES = 322122547200   # 300 GiB reserve that must stay available
STOP_BUFFER_BYTES = 68719476736  # 64 GiB abort margin; not a peak estimate
MAX_INTERVAL, MAX_TERM_GRACE, MAX_KILL_VERIFY = 1.0, 2.0, 2.0
CHUNK_BYTES = 64 << 20
PR_SET_CHILD_SUBREAPER = 36


class CapacityAbort(Exception):
    """The budget stopped a stage (threshold, unknown space, stale sample or monitor failure)."""


def policy_from(capacity):
    """Validate the budgeted host.capacity keys; any deviation from the reviewed policy refuses the stage."""
    if capacity.get('reserveBytes') != FLOOR_BYTES:
        raise CapacityAbort(f'host.capacity.reserveBytes must be {FLOOR_BYTES} (300 GiB); the reserve is never lowered')
    if capacity.get('stopBufferBytes') != STOP_BUFFER_BYTES:
        raise CapacityAbort(f'host.capacity.stopBufferBytes must be {STOP_BUFFER_BYTES} (64 GiB)')
    limits = {'sampleIntervalSeconds': MAX_INTERVAL, 'termGraceSeconds': MAX_TERM_GRACE, 'killVerifySeconds': MAX_KILL_VERIFY}
    values = {}
    for key, limit in limits.items():
        value = capacity.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 < value <= limit:
            raise CapacityAbort(f'host.capacity.{key} must be > 0 and <= {limit}')
        values[key] = float(value)
    return {'floor': FLOOR_BYTES, 'buffer': STOP_BUFFER_BYTES, 'interval': values['sampleIntervalSeconds'],
        'termGrace': values['termGraceSeconds'], 'killVerify': values['killVerifySeconds']}


def nearest_existing(path):
    path = Path(path)
    while not path.exists():
        if path.parent == path:
            raise FileNotFoundError(str(path))
        path = path.parent
    return path


def become_subreaper():
    """Orphaned descendants of owned children reparent to this process, so their exit can be reaped and verified."""
    with contextlib.suppress(OSError, AttributeError):
        libc = ctypes.CDLL(None, use_errno=True)
        return libc.prctl(PR_SET_CHILD_SUBREAPER, 1, 0, 0, 0) == 0
    return False


def group_alive(pgid):
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def reap(pgid):
    """Collect exited members of an owned group (zombie descendants reparented to this subreaper)."""
    while True:
        try:
            pid, _ = os.waitpid(-pgid, os.WNOHANG)
        except ChildProcessError:
            return
        if pid == 0:
            return


class Budget:
    def __init__(self, policy, paths, statvfs=os.statvfs, stat=os.stat, clock=time.monotonic, sleep=time.sleep,
                 log=lambda line: print(line, file=sys.stderr, flush=True)):
        self.policy, self.statvfs, self.clock, self.sleep, self.log = policy, statvfs, clock, sleep, log
        self.filesystems, seen = [], {}
        for label, path in paths.items():
            try:
                base = nearest_existing(path)
                device = stat(base).st_dev
            except OSError as error:
                raise CapacityAbort(f'filesystem of the {label} ({path}) could not be established: {error.__class__.__name__}')
            if device not in seen:
                seen[device] = label
                self.filesystems.append({'label': label, 'path': str(base), 'device': device})
        self.limit = policy['floor'] + policy['buffer']
        self.groups = []

    def sample(self):
        """{label: (available, total)} for every distinct filesystem; unknown or stale is a stop."""
        started, result = self.clock(), {}
        for fs in self.filesystems:
            try:
                st = self.statvfs(fs['path'])
            except OSError as error:
                raise CapacityAbort(f'available space for the {fs["label"]} could not be read: {error.__class__.__name__}')
            if st.f_frsize <= 0 or st.f_bavail < 0:
                raise CapacityAbort(f'available space for the {fs["label"]} is invalid')
            result[fs['label']] = (st.f_bavail * st.f_frsize, st.f_blocks * st.f_frsize)
        if self.clock() - started > self.policy['interval']:
            raise CapacityAbort(f'capacity sample took longer than {self.policy["interval"]} s (stale)')
        return result

    def check(self, extra=None):
        """Stop unless every filesystem keeps strictly more than floor + buffer (after extra[label] bytes, if given)."""
        for label, (available, _) in self.sample().items():
            need = (extra or {}).get(label, 0)
            if available - need <= self.limit:
                raise CapacityAbort(f'the {label} filesystem has {available} bytes available' +
                    (f' before a {need}-byte copy' if need else '') +
                    f'; the budget stops at {self.limit} ({self.policy["floor"]} reserve plus {self.policy["buffer"]} stop buffer)')

    def start(self):
        """Initial admission and the before-writes report (policy, total and available bytes per filesystem)."""
        sample = self.sample()
        # One plain line (no JSON object, so stage receipts parsed from stderr stay unambiguous).
        self.log('capacity budgeted: floor=%d buffer=%d stopAt=%d interval=%gs termGrace=%gs killVerify=%gs; %s' % (
            self.policy['floor'], self.policy['buffer'], self.limit, self.policy['interval'], self.policy['termGrace'],
            self.policy['killVerify'], '; '.join(f'{fs["label"]} dev={fs["device"]} path={fs["path"]} '
            f'available={sample[fs["label"]][0]} total={sample[fs["label"]][1]}' for fs in self.filesystems)))
        self.check()
        return self

    def run(self, argv, env=None, cwd=None):
        """Run one owned child tree under the budget. Raises CalledProcessError on failure, CapacityAbort on a stop."""
        self.check()
        become_subreaper()
        proc = subprocess.Popen(argv, env=env, cwd=cwd, start_new_session=True)
        pgid = proc.pid
        self.groups.append(pgid)
        try:
            returncode = None
            while True:
                if returncode is None:
                    try:
                        returncode = proc.wait(timeout=self.policy['interval'])
                    except subprocess.TimeoutExpired:
                        pass
                else:
                    reap(pgid)
                    self.sleep(min(0.05, self.policy['interval']))
                self.check()
                if returncode is not None and not group_alive(pgid):
                    break
        except BaseException:
            self.stop(pgid, proc)
            raise
        if returncode:
            raise subprocess.CalledProcessError(returncode, argv)
        return returncode

    def stop(self, pgid, proc):
        """SIGTERM the owned group, SIGKILL after termGrace, verify within killVerify. Never touches other groups."""
        def wait_empty(seconds):
            end = self.clock() + seconds
            while True:
                proc.poll()
                reap(pgid)
                if not group_alive(pgid):
                    return True
                if self.clock() >= end:
                    return False
                time.sleep(0.02)
        for sig, seconds in ((signal.SIGTERM, self.policy['termGrace']), (signal.SIGKILL, self.policy['killVerify'])):
            try:
                os.killpg(pgid, sig)
            except ProcessLookupError:
                proc.poll()
                return
            if wait_empty(seconds):
                return
        raise CapacityAbort(f'owned process group {pgid} did not terminate within '
            f'{self.policy["termGrace"] + self.policy["killVerify"]} s of SIGTERM/SIGKILL')

    def label_of(self, path, stat=os.stat):
        device = stat(nearest_existing(path)).st_dev
        for fs in self.filesystems:
            if fs['device'] == device:
                return fs['label']
        raise CapacityAbort(f'{path} is not on a filesystem this stage measured; refusing an uncovered write')

    def copy(self, source, destination, stat=os.stat):
        """Parent-managed copy in checked chunks: admitted only if the destination keeps the budget after the whole
        size, rechecked at least every interval; a stop removes only this copy's own partial file."""
        source, destination = Path(source), Path(destination)
        size = source.stat().st_size
        label = self.label_of(destination.parent, stat)
        self.check({label: size})
        partial = destination.with_name(destination.name + '.partial')
        fd = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        try:
            with open(source, 'rb') as reader, os.fdopen(fd, 'wb') as writer:
                last, copied = self.clock(), 0
                while chunk := reader.read(CHUNK_BYTES):
                    writer.write(chunk)
                    copied += len(chunk)
                    if self.clock() - last >= self.policy['interval'] or copied % (16 * CHUNK_BYTES) == 0:
                        writer.flush()
                        self.check({label: size - copied})
                        last = self.clock()
            if copied != size:
                raise CapacityAbort(f'{source} changed size during the copy')
            os.replace(partial, destination)
        except BaseException:
            with contextlib.suppress(FileNotFoundError):
                partial.unlink()
            raise
        return destination
