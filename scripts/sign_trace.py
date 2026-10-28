"""Nonsecret lifecycle trace of the OS signing key family, written while the derived release script runs.

Runs 40641163775, 40643229025 and 40646237776 failed when a signing tool could not open a key file that existed
earlier in the same run; the last one named the absolute path, so a working-directory explanation is not enough.
This records what disappears and when, without key bytes, argv, environment or raw key paths:
  * a watcher samples role-based booleans every interval (private directory, discovered source key files, the
    release-local `keys` link, its RAM target and the key files through it), whether the release script process is
    still alive, and the logind state and session count of this uid; only changes are written;
  * the key-path wrappers append one event per tool start: tool name, pid, ppid and, per rewritten key argument, its
    basename and whether it was readable at that moment;
  * static host facts once: uid, effective logind RemoveIPC setting, linger flag and /dev/shm mount options.
On failure, `summary()` names the first sample in which a key role went from present to missing, whether the script
was still running and whether its EXIT trap had started (that trap is the script's only remover of the RAM keys).
"""
import glob
import json
import os
from pathlib import Path
import pwd
import threading
import time

KEY_FILES = ('releasekey.x509.pem', 'releasekey.pk8', 'avb.pem')

def host_facts():
    uid = os.getuid()
    try:
        user = pwd.getpwuid(uid).pw_name
    except KeyError:
        user = None
    remove_ipc = None
    for path in ['/usr/lib/systemd/logind.conf', '/etc/systemd/logind.conf', *sorted(glob.glob('/usr/lib/systemd/logind.conf.d/*.conf')),
                 *sorted(glob.glob('/etc/systemd/logind.conf.d/*.conf')), *sorted(glob.glob('/run/systemd/logind.conf.d/*.conf'))]:
        try:
            for line in Path(path).read_text().splitlines():
                if line.strip().startswith('RemoveIPC='):
                    remove_ipc = line.strip().split('=', 1)[1].strip()
        except OSError:
            continue
    shm = None
    try:
        for line in Path('/proc/mounts').read_text().splitlines():
            fields = line.split()
            if len(fields) > 3 and fields[1] == '/dev/shm':
                shm = {'type': fields[2], 'options': fields[3]}
    except OSError:
        pass
    return {'uid': uid, 'removeIPC': remove_ipc or 'unset (systemd default yes)',
        'linger': bool(user) and os.path.exists(f'/var/lib/systemd/linger/{user}'), 'devShm': shm}

def logind_user(uid):
    try:
        values = dict(line.split('=', 1) for line in Path(f'/run/systemd/users/{uid}').read_text().splitlines() if '=' in line)
    except OSError:
        return None
    return {'state': values.get('STATE'), 'sessions': len(values.get('SESSIONS', '').split())}

class SignTrace:
    def __init__(self, path, private, source_keys, release_keys, interval=0.1):
        self.path, self.private, self.source, self.link = Path(path), Path(private), Path(source_keys), Path(release_keys)
        self.interval, self.process, self.start = interval, None, time.monotonic()
        self.stop_event, self.thread, self.last = threading.Event(), None, None
        self.write({'event': 'start', 'facts': host_facts()})

    def write(self, record):
        record = {'t': round(time.monotonic() - self.start, 3), 'wall': time.time(), **record}
        with open(self.path, 'a') as f:
            f.write(json.dumps(record, sort_keys=True) + '\n')

    def sample(self):
        through = lambda name: os.access(self.link/name, os.R_OK)
        state = {'privateDir': self.private.is_dir(),
            'sourceKeys': all((self.source/name).is_file() for name in KEY_FILES),
            'releaseLink': os.path.islink(self.link), 'ramTarget': self.link.is_dir(),
            **{f'ram:{name}': through(name) for name in KEY_FILES},
            'scriptAlive': self.process is not None and self.process.poll() is None,
            'logind': logind_user(os.getuid())}
        return state

    def run(self):
        while not self.stop_event.is_set():
            try:
                self.observe()
            except OSError:
                return  # the trace is diagnostic; losing it must never affect signing
            self.stop_event.wait(self.interval)

    def observe(self):
        state = self.sample()
        if state != self.last:
            self.write({'event': 'state', 'state': state})
            self.last = state

    def attach(self, process):
        self.process = process
        self.write({'event': 'script-started', 'pid': process.pid})
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def mark(self, event, **fields):
        self.observe()
        self.write({'event': event, **fields})

    def stop(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join()
        self.observe()
        self.write({'event': 'stop'})

def summary(path):
    """The first loss of a RAM key role after it was present, with the script's liveness at that sample."""
    present, records = set(), [json.loads(line) for line in Path(path).read_text().splitlines()]
    facts = next((r['facts'] for r in records if r['event'] == 'start'), None)
    unreadable = next(({'tool': r['tool'], 'wall': r['wall'], 'keys': r['keys']} for r in records
        if r['event'] == 'tool' and not all(r['keys'].values())), None)
    exiting = False
    for record in records:
        exiting = exiting or record['event'] in ('exit-trap', 'script-exited')
        if record['event'] != 'state':
            continue
        state = record['state']
        roles = ['releaseLink', 'ramTarget', *(f'ram:{name}' for name in KEY_FILES)]
        lost = [role for role in roles if role in present and not state[role]]
        if lost:
            # Before the script's EXIT trap started (or it exited) nothing in the script removes them.
            return {'keyFamilyLost': {'t': record['t'], 'roles': lost, 'scriptAlive': state['scriptAlive'],
                'scriptExiting': exiting,
                'privateDir': state['privateDir'], 'logind': state['logind']},
                'firstUnreadableToolStart': unreadable, 'facts': facts}
        present.update(role for role in roles if state[role])
    return {'keyFamilyLost': None, 'firstUnreadableToolStart': unreadable, 'facts': facts}
