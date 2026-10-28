#!/usr/bin/env python3
"""Run and record OS release stages as an immutable, candidate-bound receipt chain; offline.

init               bind a state directory to one candidate (source revision, device, build number)
run STAGE -- CMD   run the next local stage under a non-blocking lock; record start and completion
record STAGE       record evidence for an external stage (publication) that this tool does not run
mark-interrupted   explicitly close a stage whose run died, so the same stage may run again
resume             open a new state directory that carries the completed stages of a chain that failed at a
                   builder stage, so that stage runs again on the same, hash-recorded artifacts (no rebuild);
                   the failed chain is only read and stays as it is
fail / abandon     explicit terminal outcomes; never inferred from missing or empty files
status             derive RUNNING / INTERRUPTED / AWAITING / AWAITING_EXTERNAL / TERMINAL from receipts and lock

Liveness needs the lock held, the owner's boot id equal to this boot and the owner process alive; a pid
alone is not proof. Receipts hold stage, times, exit code and file hashes only: never command output,
arguments or environment. Exit status: 0 ok, 1 stage command failed, 2 invalid input or state, 3 locked.
"""
import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
# Import the shared helper beside this file, also under python3 -I (no script directory on sys.path).
sys.path.insert(0, str(Path(__file__).resolve().parent))
import release_receipts  # noqa: E402
from release_receipts import sha256_file

SCHEMA = 1
KIND = 'rose-os-stage-receipt'
# Ordered release lifecycle. 'builder' stages need the build VM; after transfer the artifacts are local,
# so compute may stop while publication (performed outside this tool) is awaited.
STAGES = [('build', 'builder'), ('bytes-verify', 'builder'), ('sign', 'builder'), ('ota-verify', 'builder'),
    ('transfer', 'builder'), ('publish', 'external'), ('publication-verify', 'local')]
LOCATION = dict(STAGES)
ORDER = [name for name, _ in STAGES]
FINAL = ORDER[-1]
RECEIPT = re.compile(r'(\d{3})-([a-z-]+)\.json')
SHA1 = re.compile(r'[0-9a-f]{40}')
DEVICE = re.compile(r'[a-z0-9_]{1,64}')
REASON = re.compile(r'[A-Za-z0-9 ._,:/-]{1,200}')
LOCK = 'stage.lock'

class StateError(Exception):
    pass

class Locked(Exception):
    pass

def now():
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())

def boot_id():
    try:
        return Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    except OSError:
        return None

def file_record(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise StateError(f'not a regular file: {path.name}')
    return {'name': path.name, 'size': path.stat().st_size, 'sha256': sha256_file(path)}

def load_chain(state):
    """Read and validate every receipt; any gap, edit, reorder or illegal transition is an error."""
    state = Path(state)
    if not state.is_dir():
        raise StateError('state directory does not exist')
    names = sorted(p.name for p in state.iterdir() if p.name.endswith('.json') and not p.name.startswith('.'))
    chain, previous = [], None
    for index, name in enumerate(names):
        match = RECEIPT.fullmatch(name)
        if not match or int(match[1]) != index:
            raise StateError(f'unexpected or out-of-sequence receipt file: {name}')
        path = state/name
        try:
            receipt = json.loads(path.read_text())
        except ValueError:
            raise StateError(f'unreadable receipt: {name}')
        if (not isinstance(receipt, dict) or receipt.get('kind') != KIND or receipt.get('sequence') != index
                or receipt.get('previousSha256') != previous or match[2] != receipt.get('event')):
            raise StateError(f'receipt chain broken at {name}')
        if chain and receipt.get('candidate') != chain[0]['candidate']:
            raise StateError(f'candidate changed at {name}')
        chain.append(receipt)
        previous = sha256_file(path)
    if not chain:
        raise StateError('state directory is not initialized')
    replay(chain)
    return chain, previous

def replay(chain):
    """Return (phase, stage): phase is open, started, failed, abandoned or complete."""
    if chain[0]['event'] != 'init':
        raise StateError('first receipt must be init')
    phase, stage, done = 'open', None, []
    carrying = 'resumedFrom' in chain[0]
    for receipt in chain[1:]:
        event, name = receipt['event'], receipt.get('stage')
        expected = ORDER[len(done)] if len(done) < len(ORDER) else None
        if phase in ('failed', 'abandoned', 'complete'):
            raise StateError(f'receipt after terminal state: {event}')
        # Carried stages come only first in a resumed chain, exactly as its init receipt lists them.
        if event == 'carried' and carrying and phase == 'open' and name == expected \
                and name in chain[0]['resumedFrom']['carriedStages']:
            done.append(name)
            continue
        carrying = False
        if event == 'started' and phase == 'open' and name == expected:
            phase, stage = 'started', name
        elif event == 'interrupted' and phase == 'started' and name == stage:
            phase = 'open'
        elif event == 'completed' and phase == 'started' and name == stage:
            if receipt.get('exitCode') == 0:
                done.append(name)
                phase = 'complete' if name == FINAL else 'open'
            else:
                phase = 'stage-failed'
        elif event == 'failed' and phase in ('open', 'started', 'stage-failed'):
            phase = 'failed'
        elif event == 'abandoned' and phase in ('open', 'started', 'stage-failed'):
            phase = 'abandoned'
        else:
            raise StateError(f'illegal transition: {event} {name or ""} after {phase} {stage or ""}'.strip())
    if phase == 'stage-failed':
        raise StateError('a failed stage completion must be followed by its failed receipt')
    return phase, stage, done

def append(state, chain, previous, event, **fields):
    receipt = {'kind': KIND, 'schemaVersion': SCHEMA, 'sequence': len(chain), 'previousSha256': previous,
        'candidate': chain[0]['candidate'], 'event': event, 'at': now(), **fields}
    path = Path(state)/f'{len(chain):03d}-{event}.json'
    release_receipts.write_once(path, receipt)
    chain.append(receipt)
    return chain, sha256_file(path)

def check_candidate(chain, source):
    if chain[0]['candidate']['sourceRevision'] != source:
        raise StateError('candidate source revision does not match this state directory')

def acquire(state):
    """Non-blocking exclusive lock; the kernel releases it if the owner dies."""
    handle = open(Path(state)/LOCK, 'a+')
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        raise Locked('another runner holds the stage lock')
    handle.seek(0)
    handle.truncate()
    handle.write(json.dumps({'pid': os.getpid(), 'bootId': boot_id(), 'since': now()}))
    handle.flush()
    return handle

def lock_view(state):
    path = Path(state)/LOCK
    if not path.exists():
        return {'held': False}
    with open(path, 'a+') as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            held = True
        else:
            fcntl.flock(handle, fcntl.LOCK_UN)
            held = False
        handle.seek(0)
        try:
            owner = json.loads(handle.read() or '{}')
        except ValueError:
            owner = {}
    pid = owner.get('pid') if isinstance(owner.get('pid'), int) else None
    alive = False
    if pid:
        try:
            os.kill(pid, 0)
            alive = True
        except ProcessLookupError:
            pass
        except PermissionError:
            alive = True
    current = boot_id()
    return {'held': held, 'ownerPid': pid, 'ownerAlive': alive,
        'ownerBootMatches': current is not None and owner.get('bootId') == current}

def describe(state):
    chain, _ = load_chain(state)
    phase, stage, done = replay(chain)
    lock = lock_view(state)
    verified_running = lock['held'] and lock['ownerAlive'] and lock['ownerBootMatches']
    upcoming = ORDER[len(done)] if len(done) < len(ORDER) else None
    if phase == 'complete':
        result, current = 'TERMINAL', 'published-verified'
    elif phase in ('failed', 'abandoned'):
        result, current = 'TERMINAL', phase
    elif phase == 'started':
        current = stage
        # Held by an unverified owner (dead runner with a surviving command, or a foreign boot id):
        # never reported as running, and not yet interruptible.
        result = 'RUNNING' if verified_running else 'LOCKED_UNVERIFIED' if lock['held'] else 'INTERRUPTED'
    elif LOCATION[upcoming] == 'external':
        result, current = 'AWAITING_EXTERNAL', upcoming
    else:
        result, current = 'AWAITING', upcoming
    compute = result in ('RUNNING', 'LOCKED_UNVERIFIED', 'INTERRUPTED', 'AWAITING') and LOCATION.get(current) == 'builder'
    return {'state': result, 'stage': current, 'completedStages': done, 'candidate': chain[0]['candidate'],
        'receipts': len(chain), 'lock': lock, 'builderComputeNeeded': compute,
        'lastReceiptAt': chain[-1]['at']}

def init(a):
    if not SHA1.fullmatch(a.source_revision) or not DEVICE.fullmatch(a.device) or a.build_number < 1:
        raise StateError('init needs a full source revision, lowercase device and positive build number')
    state = Path(a.state)
    state.mkdir(parents=True, exist_ok=True)
    if any(p.name.endswith('.json') for p in state.iterdir()):
        raise StateError('state directory already holds receipts')
    handle = acquire(state)
    try:
        candidate = {'sourceRevision': a.source_revision, 'device': a.device, 'buildNumber': a.build_number}
        receipt = {'kind': KIND, 'schemaVersion': SCHEMA, 'sequence': 0, 'previousSha256': None,
            'candidate': candidate, 'event': 'init', 'at': now(), 'stages': ORDER}
        release_receipts.write_once(state/'000-init.json', receipt)
    finally:
        handle.close()
    return describe(state)

def locked_chain(a):
    handle = acquire(a.state)
    try:
        chain, previous = load_chain(a.state)
        check_candidate(chain, a.source_revision)
    except Exception:
        handle.close()
        raise
    return handle, chain, previous

def run(a):
    if LOCATION.get(a.stage) in (None, 'external'):
        raise StateError(f'{a.stage} is not a stage this tool runs')
    if not a.command:
        raise StateError('give the stage command after --')
    handle, chain, previous = locked_chain(a)
    try:
        phase, stage, done = replay(chain)
        if phase != 'open' or ORDER[len(done)] != a.stage:
            raise StateError(f'{a.stage} is not the next legal stage (phase {phase}, completed {done})')
        inputs = [file_record(p) for p in a.input]
        chain, previous = append(a.state, chain, previous, 'started', stage=a.stage, pid=os.getpid(),
            bootId=boot_id(), inputs=inputs, executable=Path(a.command[0]).name)
        # Command output goes to stderr (the caller's log), never into receipts or the stdout report.
        # The command inherits the lock, so an orphan left by a killed runner still holds it and the
        # stage cannot be marked interrupted.
        try:
            code = subprocess.run(a.command, stdout=sys.stderr, pass_fds=(handle.fileno(),)).returncode
        except OSError:
            code = 127
        problem = None
        if code == 0:
            if [file_record(p) for p in a.input] != inputs:
                problem, code = 'inputs changed during the stage', 1
            else:
                try:
                    outputs = [file_record(p) for p in a.output]
                except StateError as error:
                    problem, code = str(error), 1
        fields = {'stage': a.stage, 'exitCode': code, 'inputs': inputs}
        if code == 0:
            fields['outputs'] = outputs
        chain, previous = append(a.state, chain, previous, 'completed', **fields)
        if code:
            append(a.state, chain, previous, 'failed', stage=a.stage,
                reason=problem or f'stage command exited {code}')
    finally:
        handle.close()
    report = describe(a.state)
    report['exitCode'] = code
    return report

def record(a):
    if LOCATION.get(a.stage) != 'external':
        raise StateError(f'{a.stage} is not an external stage')
    if not a.evidence:
        raise StateError('record needs at least one --evidence file')
    handle, chain, previous = locked_chain(a)
    try:
        phase, _, done = replay(chain)
        if phase != 'open' or ORDER[len(done)] != a.stage:
            raise StateError(f'{a.stage} is not the next legal stage')
        evidence = [file_record(p) for p in a.evidence]
        chain, previous = append(a.state, chain, previous, 'started', stage=a.stage, external=True, inputs=[])
        append(a.state, chain, previous, 'completed', stage=a.stage, exitCode=0, external=True,
            inputs=[], outputs=evidence)
    finally:
        handle.close()
    return describe(a.state)

def mark_interrupted(a):
    handle, chain, previous = locked_chain(a)
    try:
        phase, stage, _ = replay(chain)
        if phase != 'started' or stage != a.stage:
            raise StateError(f'{a.stage} has no unfinished run to mark')
        # Holding the lock proves no live runner owns this stage.
        append(a.state, chain, previous, 'interrupted', stage=stage)
    finally:
        handle.close()
    return describe(a.state)

def resume(a):
    if not SHA1.fullmatch(a.source_revision):
        raise StateError('resume needs a full source revision')
    if LOCATION.get(a.stage) != 'builder' or a.stage == ORDER[0]:
        raise StateError(f'{a.stage} cannot be resumed; only a builder stage after build')
    source_chain, source_last = load_chain(a.source)
    check_candidate(source_chain, a.source_revision)
    if lock_view(a.source)['held']:
        raise StateError('the source state is locked by a runner')
    phase, _, done = replay(source_chain)
    if phase != 'failed' or source_chain[-1].get('stage') != a.stage or ORDER[len(done)] != a.stage:
        raise StateError(f'the source chain did not fail at {a.stage} after completing every earlier stage')
    artifacts = [file_record(p) for p in a.artifact]
    names = [r['name'] for r in artifacts]
    if not artifacts or len(set(names)) != len(names):
        raise StateError('resume needs each carried artifact once')
    by_name = {r['name']: r for r in artifacts}
    expected = {}
    for item in a.expect:
        name, _, digest = item.partition('=')
        if not re.fullmatch(r'[0-9a-f]{64}', digest) or name not in by_name:
            raise StateError(f'--expect must be NAME=SHA256 for a carried artifact: {name}')
        if by_name[name]['sha256'] != digest:
            raise StateError(f'{name} differs from its expected sha256; nothing resumed')
        expected[name] = digest
    # Outputs that the completed stages recorded, and the artifacts a resumed source chain carried, must be carried
    # unchanged (a resume of a resume keeps the original binding).
    recorded = list(source_chain[0].get('resumedFrom', {}).get('artifacts', []))
    for receipt in source_chain:
        recorded += receipt.get('outputs', []) if receipt.get('event') == 'completed' else []
    for output in recorded:
        if by_name.get(output['name'], {}).get('sha256') != output['sha256']:
            raise StateError(f'{output["name"]} differs from the source receipt; nothing resumed')
    state = Path(a.state)
    if state.exists() and any(state.iterdir()):
        raise StateError('the new state directory must be new and empty')
    state.mkdir(parents=True, exist_ok=True)
    handle = acquire(state)
    try:
        receipt = {'kind': KIND, 'schemaVersion': SCHEMA, 'sequence': 0, 'previousSha256': None,
            'candidate': source_chain[0]['candidate'], 'event': 'init', 'at': now(), 'stages': ORDER,
            'resumedFrom': {'state': str(Path(a.source).resolve()), 'lastReceiptSha256': source_last,
                'receipts': len(source_chain), 'failedStage': a.stage, 'carriedStages': done,
                'artifacts': artifacts, 'expected': expected}}
        release_receipts.write_once(state/'000-init.json', receipt)
        chain, previous = [receipt], sha256_file(state/'000-init.json')
        for name in done:
            chain, previous = append(state, chain, previous, 'carried', stage=name, sourceReceiptSha256=source_last)
    finally:
        handle.close()
    return describe(state)

def terminal(a, event):
    if not REASON.fullmatch(a.reason):
        raise StateError('reason must be short plain text')
    handle, chain, previous = locked_chain(a)
    try:
        phase, stage, _ = replay(chain)
        if phase not in ('open', 'started'):
            raise StateError(f'cannot record {event} after {phase}')
        append(a.state, chain, previous, event, stage=stage, reason=a.reason)
    finally:
        handle.close()
    return describe(a.state)

def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest='command_name', required=True)
    def command(name, help, bound=True):
        c = sub.add_parser(name, help=help)
        c.add_argument('--state', type=Path, required=True, help='Candidate state directory')
        if bound:
            c.add_argument('--source-revision', required=True, help='Must match the initialized candidate')
        return c
    c = command('init', 'Bind a new state directory to one candidate')
    c.add_argument('--device', required=True)
    c.add_argument('--build-number', type=int, required=True)
    c = command('run', 'Run the next local stage')
    c.add_argument('stage', choices=ORDER)
    c.add_argument('--input', type=Path, action='append', default=[], help='File the stage consumes (hashed)')
    c.add_argument('--output', type=Path, action='append', default=[], help='File the stage must produce (hashed)')
    c.add_argument('command', nargs=argparse.REMAINDER)
    c = command('record', 'Record evidence for an external stage')
    c.add_argument('stage', choices=ORDER)
    c.add_argument('--evidence', type=Path, action='append', default=[])
    c = command('resume', 'Carry the completed stages of a failed chain into a new state directory')
    c.add_argument('stage', choices=ORDER, help='The failed builder stage to run again')
    c.add_argument('--from', dest='source', type=Path, required=True, help='State directory of the failed chain')
    c.add_argument('--artifact', type=Path, action='append', default=[], help='Carried file (hashed into the receipt)')
    c.add_argument('--expect', action='append', default=[], help='NAME=SHA256 an artifact must match (e.g. from the run log)')
    c = command('mark-interrupted', 'Close a stage whose runner died')
    c.add_argument('stage', choices=ORDER)
    for name in ('fail', 'abandon'):
        command(name, f'Record an explicit {name} outcome').add_argument('--reason', required=True)
    command('status', 'Report the derived state', bound=False)
    a = p.parse_args(argv)
    if getattr(a, 'command', None) and a.command[:1] == ['--']:
        a.command = a.command[1:]
    try:
        report = {'init': init, 'run': run, 'record': record, 'resume': resume, 'mark-interrupted': mark_interrupted,
            'fail': lambda a: terminal(a, 'failed'), 'abandon': lambda a: terminal(a, 'abandoned'),
            'status': lambda a: describe(a.state)}[a.command_name](a)
    except Locked as error:
        print(json.dumps({'verdict': 'LOCKED', 'error': str(error)}), file=sys.stderr)
        return 3
    except (StateError, release_receipts.ReceiptExists) as error:
        print(json.dumps({'verdict': 'INVALID', 'error': str(error)}), file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2, sort_keys=True))
    return 1 if report.get('exitCode') else 0

if __name__ == '__main__':
    sys.exit(main())
