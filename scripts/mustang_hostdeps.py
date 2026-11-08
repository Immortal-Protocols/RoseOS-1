#!/usr/bin/env python3
"""Bounded generic adevtool dependency build on the dedicated Mustang guest.

Default is a non-executing plan. Only the exact manual main workflow's hostdeps
job can execute. This does not generate vendor, allocate an OTA, sign, or STOP a
VM. The existing hosted ownership cleanup remains responsible for STOP.
"""
import argparse
import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import pwd
import signal
import stat
import subprocess
import sys
import time

import mustang_controller as controller
import mustang_ephemeral as ephemeral
import mustang_hosttools as readers
import mustang_lane
import mustang_route as route
import mustang_source_sync as sync

ROOT = Path(__file__).resolve().parents[1]
JOBS = 24
TIMEOUT = 9000
WORK_BYTES = 128 << 30
RESERVE_BYTES = 300 << 30
LOG_BYTES = 16 << 20
MAX_ENTRIES = 100000
INVENTORY_BYTES = 8 << 20
SAMPLE_SECONDS = 5
KILL_GRACE = 30
HOST_TOOLS = ('aapt2', 'apksigner', 'aprotoc', 'arsclib', 'debugfs', 'dispol',
              'fsck.erofs', 'lpdump', 'lpunpack', 'lz4', 'ota_extractor',
              'simg2img', 'sqlite3', 'toybox')


def recipe(root=ROOT):
    spec = readers.recipe(root)
    tools = spec['adevtool']['depsTools']
    projects = sync.project_identities((Path(root)/'os/pinned-manifest.xml').read_bytes())
    revisions = [row[3] for row in projects if row[1] == 'vendor/adevtool']
    if spec['adevtool']['depsDir'] != 'out_adevtool_deps' or tools != list(HOST_TOOLS) \
            or revisions != [spec['pins']['vendor/adevtool']]:
        raise ValueError('hostdeps recipe path, goals or revision differs')
    return {'schema': 'roseos.mustang-hostdeps-recipe.v1', 'scope': 'adevtool-host-dependencies-only',
            'product': 'sdk_phone64_x86_64', 'outDir': spec['adevtool']['depsDir'],
            'tools': spec['adevtool']['depsTools'], 'adevtoolRevision': spec['pins']['vendor/adevtool'],
            'jobs': JOBS, 'timeoutSeconds': TIMEOUT, 'workBytes': WORK_BYTES,
            'reserveBytes': RESERVE_BYTES, 'logBytes': LOG_BYTES,
            'boundsKind': 'proposal-ceilings-not-measurements',
            'otaAuthorized': False, 'signAuthorized': False, 'publishAuthorized': False}


def require_selection(event):
    if not isinstance(event, dict):
        raise ValueError('hostdeps manual event must be an object')
    inputs = event.get('inputs')
    if not isinstance(inputs, dict):
        raise ValueError('hostdeps_only must be the sole selected manual scope')
    enabled = inputs.get('hostdeps_only')
    disabled = lambda value: value is False or value is None or (type(value) is str and value == 'false')
    if not (enabled is True or (type(enabled) is str and enabled == 'true')) \
            or any(not disabled(inputs.get(k, False)) for k in ('publish', 'source_only', 'dry_run', 'lock_admin_only', 'factory_only')):
        raise ValueError('hostdeps_only must be the sole selected manual scope')


def guard(env, event, root=ROOT):
    lane = mustang_lane.validate(mustang_lane.configuration())
    run = controller.invocation(lane, env)
    if env.get('GITHUB_REPOSITORY_ID') != '1428996648':
        raise ValueError('hostdeps repository ID differs')
    require_selection(event)
    recipe(root)  # Reject a changed goal/path/pin before the hosted controller START.
    ephemeral.require_group5(json.loads((Path(root)/'os/build-only/mustang-runtime.json').read_text()))
    accepted = json.loads((Path(root)/route.ACCEPTED_SOURCE_SYNC['path']).read_text())
    if not route.accepted_source_sync(accepted, route.ACCEPTED_SOURCE_SYNC['path'], root):
        raise ValueError('accepted source-only receipt is unbound')
    return lane, run


def child_env(home, tmp):
    # Allowlist rather than forwarding job tokens, OIDC endpoints, injected GCP
    # material, GIT_*, BASH_ENV, inherited OUT_DIR or workflow output channels.
    return {'PATH': str(Path(ephemeral.NODE['install']).parent)+':/usr/bin:/bin',
            'HOME': str(home), 'TMPDIR': str(tmp), 'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8',
            'GIT_CONFIG_GLOBAL': '/dev/null', 'GIT_CONFIG_SYSTEM': '/dev/null',
            'GIT_NO_REPLACE_OBJECTS': '1', 'GIT_TERMINAL_PROMPT': '0'}


def plain_dir(path):
    fd = readers._walk_dir(readers._body(str(path)) or [])
    try:
        if not readers._body(str(path)):
            raise ValueError('hostdeps directory is not absolute')
        return os.fstat(fd)
    finally:
        os.close(fd)


def argv(spec):
    return ['/bin/bash', 'vendor/adevtool/scripts/run-build.sh', spec['product'], spec['outDir'],
            '-j'+str(spec['jobs']), *spec['tools']]


def checked(command, source, env, deadline):
    remaining = deadline-time.monotonic()
    if remaining <= 0:
        raise ValueError('hostdeps total timeout')
    proc = subprocess.run(command, cwd=source, env=env, capture_output=True, timeout=min(60, remaining))
    if proc.returncode or len(proc.stdout) > 1 << 20:
        raise ValueError('hostdeps source inspection failed')
    return proc.stdout.decode('utf-8', errors='strict').strip()


def source_check(source, env, deadline, root=ROOT):
    """HEAD + git status for every pinned project; no pristine-runtime attestation."""
    projects = sync.project_identities((Path(root)/'os/pinned-manifest.xml').read_bytes())
    for _name, relative, _remote, revision in projects:
        path = Path(relative)
        if path.is_absolute() or '..' in path.parts or not path.parts:
            raise ValueError('pinned project path differs')
        target = source/path
        plain_dir(target)
        git = ['/usr/bin/git', '-c', 'core.fsmonitor=false', '-c', 'core.hooksPath=/dev/null', '-C', str(target)]
        if checked(git+['rev-parse', '--show-toplevel'], source, env, deadline) != str(target) \
                or checked(git+['rev-parse', 'HEAD'], source, env, deadline) != revision \
                or checked(git+['status', '--porcelain', '--untracked-files=all'], source, env, deadline):
            raise ValueError('pinned project HEAD or worktree differs')
    return {'projectCount': len(projects), 'check': 'project-heads-and-git-status',
            'ignoredContentUnchecked': True, 'pristineRuntimeAttestation': False}


def fingerprint(source, path, deadline):
    fd, before = readers._open_regular(str(source), str(path))
    try:
        digest = hashlib.sha256()
        size = 0
        while True:
            if time.monotonic() >= deadline:
                raise ValueError('hostdeps output observation timed out')
            block = os.read(fd, 1 << 20)
            if not block:
                break
            digest.update(block)
            size += len(block)
            if size > WORK_BYTES:
                raise ValueError('hostdeps file exceeds output bound')
        after = os.fstat(fd)
        identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
        if identity(before) != identity(after) or size != before.st_size:
            raise ValueError('hostdeps output changed while hashing')
        return {'sizeBytes': size, 'sha256': digest.hexdigest(), 'mode': stat.S_IMODE(before.st_mode)}
    finally:
        os.close(fd)


def tree_bytes(path, deadline=None):
    """Approximate one live sample. Rename/unlink races are normal during Make.

    Walk from a no-follow directory descriptor. Disappearing entries may be
    missed by this sample; permission/I/O errors and special files still refuse.
    """
    if not path.exists() and not path.is_symlink():
        return 0
    total = 0
    count = 1
    fd = readers._walk_dir(readers._body(str(path)) or [])
    def onerror(error):
        if not isinstance(error, FileNotFoundError):
            raise ValueError('hostdeps output sampling failed') from None
    try:
        device = os.fstat(fd).st_dev
        for _directory, dirs, files, parent in os.fwalk('.', dir_fd=fd, follow_symlinks=False, onerror=onerror):
            if deadline is not None and time.monotonic() >= deadline:
                raise ValueError('hostdeps total timeout')
            for name in dirs+files:
                if deadline is not None and time.monotonic() >= deadline:
                    raise ValueError('hostdeps total timeout')
                count += 1
                if count > MAX_ENTRIES:
                    raise ValueError('hostdeps output entry bound exceeded')
                try:
                    info = os.stat(name, dir_fd=parent, follow_symlinks=False)
                except FileNotFoundError:
                    continue
                if info.st_dev != device:
                    raise ValueError('hostdeps sampling crosses a filesystem')
                if stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
                    total += info.st_size
                elif not stat.S_ISDIR(info.st_mode):
                    raise ValueError('hostdeps special output refused')
                if total > WORK_BYTES:
                    raise ValueError('hostdeps output byte bound exceeded')
    finally:
        os.close(fd)
    return total


def sample(source, output, attempt, log, deadline=None, observations=None):
    usage = tree_bytes(output, deadline)+tree_bytes(attempt, deadline)
    available = os.statvfs(source)
    free = available.f_bavail*(available.f_frsize or available.f_bsize)
    if observations is not None:
        observations.setdefault('firstAvailableBytes', free)
        observations['samples'] = observations.get('samples', 0)+1
        observations['lastAvailableBytes'] = free
        observations['minimumAvailableBytes'] = min(observations.get('minimumAvailableBytes', free), free)
        observations['minimumAvailableInodes'] = min(observations.get('minimumAvailableInodes', available.f_favail), available.f_favail)
        observations['peakWatchedBytes'] = max(observations.get('peakWatchedBytes', 0), usage)
        observations['filesystemAvailableDropBytes'] = observations['firstAvailableBytes']-free
    if usage > WORK_BYTES or free < RESERVE_BYTES or available.f_favail <= 0:
        raise ValueError('hostdeps space or inode bound exceeded')
    if log.exists() and log.stat().st_size > LOG_BYTES:
        raise ValueError('hostdeps log bound exceeded')
    return usage


def stop_group(proc):
    # Also terminate surviving children when the leader has returned successfully.
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(proc.pid, sig)
        except ProcessLookupError:
            break
        if sig == signal.SIGTERM:
            try:
                proc.wait(timeout=KILL_GRACE)
            except subprocess.TimeoutExpired:
                pass
    proc.wait(timeout=KILL_GRACE)


def supervise(command, source, env, log, deadline, monitor):
    monitor()
    with open(log, 'xb') as stream:
        proc = subprocess.Popen(command, cwd=source, env=env, stdout=stream,
                                stderr=subprocess.STDOUT, start_new_session=True)
        try:
            while proc.poll() is None:
                if time.monotonic() >= deadline:
                    raise ValueError('hostdeps total timeout')
                monitor()
                time.sleep(min(SAMPLE_SECONDS, max(0, deadline-time.monotonic())))
            monitor()
            if proc.returncode:
                raise ValueError('hostdeps Make failed')
        finally:
            stop_group(proc)
    return 0


def inventory(source, output, deadline):
    distribution = output/'host/linux-x86'
    plain_dir(distribution)
    rows = {}
    def onerror(_error):
        raise ValueError('hostdeps distribution inventory is incomplete')
    for directory, dirs, files in os.walk(distribution, followlinks=False, onerror=onerror):
        for name in sorted(dirs+files):
            path = Path(directory)/name
            relative = str(path.relative_to(output))
            info = path.lstat()
            if len(rows) >= MAX_ENTRIES or time.monotonic() >= deadline:
                raise ValueError('hostdeps inventory bound exceeded')
            if stat.S_ISLNK(info.st_mode):
                target = os.readlink(path)
                if os.path.isabs(target) or not path.resolve(strict=True).is_relative_to(distribution):
                    raise ValueError('hostdeps distribution link escapes')
                rows[relative] = {'link': target}
            elif stat.S_ISREG(info.st_mode):
                rows[relative] = fingerprint(source, path, deadline)
            elif not stat.S_ISDIR(info.st_mode):
                raise ValueError('hostdeps special output refused')
    if len(json.dumps(rows).encode()) > INVENTORY_BYTES:
        raise ValueError('hostdeps inventory byte bound exceeded')
    return rows


@contextlib.contextmanager
def lock(path):
    plain_dir(path.parent)
    # The root-owned data home is not runner-writable. Reuse its existing lock;
    # never create/chown it or make the parent writable as an incidental repair.
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        raise ValueError('Mustang builder lock is absent; host preparation required') from None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode) or os.fstat(fd).st_nlink != 1:
            raise ValueError('hostdeps lock is not a plain file')
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError('Mustang builder lock is busy') from None
        yield
    finally:
        os.close(fd)


def receipt(path, data):
    # Parent is the fresh, verified attempt directory. No resolve()/overwrite.
    with open(path, 'x', encoding='utf-8') as out:
        json.dump(data, out, sort_keys=True)
        out.write('\n')
        out.flush()
        os.fsync(out.fileno())


def mark_revision(source, output, attempt, revision, deadline):
    if time.monotonic() >= deadline:
        raise ValueError('hostdeps total timeout')
    plain_dir(output)
    temporary = attempt/'.adevtool_revision'
    with open(temporary, 'x') as marker:
        marker.write(revision+'\n')
        marker.flush()
        os.fsync(marker.fileno())
    try:
        os.link(temporary, output/'adevtool_revision', follow_symlinks=False)
        return fingerprint(source, output/'adevtool_revision', deadline)
    finally:
        temporary.unlink()


def build(source, attempt, spec, env, root=ROOT, identity=None):
    started = time.monotonic()
    deadline = started+spec['timeoutSeconds']
    output = source/spec['outDir']
    if os.path.lexists(output):
        raise ValueError('hostdeps output already exists; preserve and reconcile it')
    result = {'schema': 'roseos.mustang-hostdeps-run.v1', 'identity': identity, 'scope': spec['scope'],
              'recipe': spec, 'sourceRoot': str(source), 'outDir': str(output), 'argv': argv(spec),
              'manifestSha256': hashlib.sha256((Path(root)/'os/pinned-manifest.xml').read_bytes()).hexdigest(),
              'success': False, 'otaAuthorized': False, 'signAuthorized': False, 'publishAuthorized': False,
              'authenticatedStop': False, 'boundEnforcement': 'sampled-process-group-stop',
              'detachedDescendantsContained': False, 'spaceObservations': {}}
    log = attempt/'build.log'
    try:
        monitor = lambda: sample(source, output, attempt, log, deadline, result['spaceObservations'])
        monitor()
        result['source'] = source_check(source, env, deadline, root)
        if checked(['/usr/bin/git', '-C', str(source/'vendor/adevtool'), 'rev-parse', 'HEAD'], source, env, deadline) != spec['adevtoolRevision']:
            raise ValueError('adevtool revision differs')
        # Assert the pinned wrapper is a contained regular executable, not a link.
        fd, info = readers._open_regular(str(source), str(source/'vendor/adevtool/scripts/run-build.sh'))
        os.close(fd)
        if not info.st_mode & 0o111:
            raise ValueError('adevtool build wrapper is not executable')
        result['wrapper'] = fingerprint(source, source/'vendor/adevtool/scripts/run-build.sh', deadline)
        result['makeExitCode'] = supervise(argv(spec), source, env, log, deadline, monitor)
        result['distribution'] = inventory(source, output, deadline)
        # All entrypoints must be real, executable files. Full distribution above
        # includes supporting JARs, libraries, scripts and contained links.
        for name in spec['tools']:
            item = result['distribution'].get('host/linux-x86/bin/'+name, {})
            if not item.get('sizeBytes') or not item.get('mode', 0) & 0o111:
                raise ValueError('adevtool entrypoint missing or not executable')
        if checked(['/usr/bin/git', '-C', str(source/'vendor/adevtool'), 'rev-parse', 'HEAD'], source, env, deadline) != spec['adevtoolRevision']:
            raise ValueError('adevtool revision changed during build')
        result['outputBytes'] = monitor()
        # This marker is never a substitute for this attempt's Make/output proof.
        result['revisionMarker'] = mark_revision(source, output, attempt, spec['adevtoolRevision'], deadline)
        result['success'] = True
    except BaseException as error:
        result['failure'] = str(error) if isinstance(error, ValueError) else type(error).__name__
        raise
    finally:
        result['elapsedSeconds'] = round(time.monotonic()-started, 3)
        receipt(attempt/'hostdeps-receipt.json', result)
    return result


def read_event(env):
    path = env.get('GITHUB_EVENT_PATH')
    if not path:
        raise ValueError('manual dispatch event is absent')
    return json.loads(Path(path).read_text())


def guest_context(env):
    lane, run = guard(env, read_event(env))
    if env.get('GITHUB_JOB') != 'hostdeps' or pwd.getpwuid(os.geteuid()).pw_name != 'mustang-runner':
        raise ValueError('hostdeps requires its dedicated guest job and runner user')
    dedicated = __import__('mustang_ota').module('mustang_hostdeps_dedicated', 'mustang-dedicated.py')
    cfg = json.loads((ROOT/'os/build-only/mustang-runtime.json').read_text())
    paths = dedicated.host_identity(lane, cfg['dataFilesystemUuid'], mount=cfg['dataMount'])
    source = Path(lane['sourceRoot'])
    for path in (source, paths['storageRoot']):
        if plain_dir(path).st_dev != plain_dir(paths['home']).st_dev:
            raise ValueError('hostdeps path leaves the dedicated data filesystem')
    return lane, run, cfg, paths


def execute(env):
    lane, run, cfg, paths = guest_context(env)
    source = Path(lane['sourceRoot'])
    checkout_env = child_env(paths['home']/'runner', paths['home'])
    if checked(['/usr/bin/git', '-C', str(ROOT), 'rev-parse', 'HEAD'], ROOT, checkout_env, time.monotonic()+60) != run['source'] \
            or checked(['/usr/bin/git', '-C', str(ROOT), 'status', '--porcelain'], ROOT, checkout_env, time.monotonic()+60):
        raise ValueError('hostdeps checkout differs from captured clean source')
    with lock(Path(lane['lockPath'])):
        attempt = paths['storageRoot']/('mustang-hostdeps-'+run['runId']+'-'+run['runAttempt'])
        attempt.mkdir(mode=0o750)
        plain_dir(attempt)
        (attempt/'tmp').mkdir(mode=0o700)
        identity = dict(run=run, builder=lane['vm'], diskUuid=cfg['dataFilesystemUuid'])
        result = build(source, attempt, recipe(), child_env(paths['home']/'runner', attempt/'tmp'), identity=identity)
        return dict(run=run, builder=lane['vm'], diskUuid=cfg['dataFilesystemUuid'],
                    receipt=str(attempt/'hostdeps-receipt.json'), success=result['success'])


def report(env, destination):
    lane, run, cfg, paths = guest_context(env)
    attempt = paths['storageRoot']/('mustang-hostdeps-'+run['runId']+'-'+run['runAttempt'])
    data = readers.read_regular(str(paths['storageRoot']), str(attempt/'hostdeps-receipt.json'), INVENTORY_BYTES+(1 << 20))
    parsed = json.loads(data)
    if parsed.get('schema') != 'roseos.mustang-hostdeps-run.v1' or parsed.get('identity') != dict(
            run=run, builder=lane['vm'], diskUuid=cfg['dataFilesystemUuid']):
        raise ValueError('hostdeps report belongs to another run or builder')
    # Copy only the structured receipt; raw build.log and child tmp files stay
    # on the dedicated disk. Never let a raw-log path become an upload glob.
    receipt(destination, parsed)


def report_failure(error, env):
    """A minimal job outcome, even when guest/lock admission produced no receipt.

    This records expected identities, not guest observations. Never serialize
    exception text: OS/process errors can contain credentials or private paths.
    The build receipt remains the only distribution/Make evidence.
    """
    if env.get('GITHUB_JOB') != 'hostdeps' or env.get('GITHUB_REPOSITORY_ID') != '1428996648':
        raise ValueError('failure summary requires the dedicated hostdeps job')
    lane = mustang_lane.validate(mustang_lane.configuration())
    run = controller.invocation(lane, env)
    require_selection(read_event(env))
    directory = Path(env['RUNNER_TEMP'])
    plain_dir(directory)
    reasons = {
        'Mustang builder lock is absent; host preparation required': 'lock-absent',
        'Mustang builder lock is busy': 'lock-busy',
        'hostdeps lock is not a plain file': 'lock-not-regular',
        'hostdeps output already exists; preserve and reconcile it': 'output-exists',
        'hostdeps checkout differs from captured clean source': 'checkout-differs',
        'hostdeps requires its dedicated guest job and runner user': 'wrong-job-or-user',
        'runner is not the admitted dedicated VM': 'instance-differs',
        'dedicated data filesystem is not the admitted mounted disk': 'filesystem-differs',
        'dedicated data mount differs': 'mount-differs',
        'dedicated path traverses a link': 'linked-guest-path',
        'hostdeps path leaves the dedicated data filesystem': 'foreign-filesystem',
        'pinned project HEAD or worktree differs': 'source-differs',
        'hostdeps source inspection failed': 'source-inspection-failed',
        'hostdeps Make failed': 'make-failed',
        'hostdeps total timeout': 'timeout',
        'hostdeps space or inode bound exceeded': 'space-bound',
        'hostdeps output byte bound exceeded': 'output-bound',
        'hostdeps output entry bound exceeded': 'entry-bound',
        'hostdeps log bound exceeded': 'log-bound',
        'hostdeps cancelled': 'cancelled',
    }
    if type(error) is ValueError:
        code = reasons.get(str(error), 'admission-or-build-refused')
    elif isinstance(error, PermissionError):
        code = 'permission-denied'
    elif isinstance(error, FileNotFoundError):
        code = 'path-not-found'
    elif isinstance(error, subprocess.TimeoutExpired):
        code = 'process-timeout'
    elif isinstance(error, subprocess.SubprocessError):
        code = 'process-error'
    elif isinstance(error, OSError):
        code = 'filesystem-or-io-error'
    else:
        code = 'invalid-input-or-unreadable-path'
    receipt(directory/'hostdeps-failure.json', {
        'schema': 'roseos.mustang-hostdeps-failure.v1', 'run': run,
        'expectedBuilder': lane['vm'], 'success': False, 'failureCode': code,
        'guestIdentityVerified': False, 'makeSuccessAttested': False,
        'distributionAttested': False, 'authenticatedStop': False,
        'otaAuthorized': False, 'signAuthorized': False, 'publishAuthorized': False})


def admin_policy():
    """List existing permission for the exact initializer; never execute it.

    A positive policy read does not prove passwordless execution. Actual use
    would still require its own explicit guarded action and sudo -n admission.
    """
    command = ['/usr/bin/sudo', '-n', '-l', '--', '/usr/bin/python3',
               str(ROOT/'scripts/mustang-prepare-lock.py'), '--execute']
    result = {'probe': 'sudo-policy-list-command-only', 'listedCommandExecuted': False,
              'initializerExecuted': False, 'passwordlessExecutionAttested': False}
    if not Path('/usr/bin/sudo').is_file():
        return dict(result, state='sudo-unavailable')
    try:
        proc = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL, timeout=10,
                              env={'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8'})
    except subprocess.TimeoutExpired:
        return dict(result, state='probe-timeout')
    except OSError:
        return dict(result, state='probe-unavailable')
    return dict(result, state='policy-allows-command' if proc.returncode == 0 else 'policy-not-confirmed',
                policyExitCode=proc.returncode)


def inspect_guest(env):
    """Current read-only prerequisites via the existing infrastructure dry run.

    Does not read old attempt logs, create a lock, adopt an output or run Make.
    The only write is the structured observation in this job's temp directory.
    """
    event = read_event(env)
    inputs = event.get('inputs') if isinstance(event, dict) else None
    disabled = lambda value: value is False or value is None or (type(value) is str and value == 'false')
    if not isinstance(inputs, dict) or not (inputs.get('dry_run') is True or inputs.get('dry_run') == 'true') \
            or any(not disabled(inputs.get(k, False)) for k in ('publish', 'source_only', 'hostdeps_only', 'lock_admin_only', 'factory_only')):
        raise ValueError('inspection requires the exclusive infrastructure dry run')
    lane = mustang_lane.validate(mustang_lane.configuration())
    run = controller.invocation(lane, env)
    if env.get('GITHUB_REPOSITORY_ID') != '1428996648' or env.get('GITHUB_JOB') != 'builder' \
            or env.get('MUSTANG_DRY_RUN') != 'true' or pwd.getpwuid(os.geteuid()).pw_name != 'mustang-runner':
        raise ValueError('inspection requires the dedicated infrastructure builder job')
    dedicated = __import__('mustang_ota').module('mustang_inspect_dedicated', 'mustang-dedicated.py')
    cfg = json.loads((ROOT/'os/build-only/mustang-runtime.json').read_text())
    paths = dedicated.host_identity(lane, cfg['dataFilesystemUuid'], mount=cfg['dataMount'])
    source = Path(lane['sourceRoot'])
    for path in (source, paths['storageRoot']):
        if plain_dir(path).st_dev != plain_dir(paths['home']).st_dev:
            raise ValueError('hostdeps path leaves the dedicated data filesystem')
    directory = Path(env['RUNNER_TEMP'])
    plain_dir(directory)
    def entry(path):
        try:
            info = path.lstat()
        except FileNotFoundError:
            return {'state': 'absent'}
        except PermissionError:
            return {'state': 'permission-denied'}
        kind = 'regular' if stat.S_ISREG(info.st_mode) else 'directory' if stat.S_ISDIR(info.st_mode) else 'other'
        return {'state': kind, 'mode': stat.S_IMODE(info.st_mode), 'uid': info.st_uid,
                'gid': info.st_gid, 'linkCount': info.st_nlink}
    lock_state = entry(Path(lane['lockPath']))
    if lock_state['state'] == 'regular':
        try:
            with lock(Path(lane['lockPath'])):
                lock_state['availability'] = 'available'
        except PermissionError:
            lock_state['availability'] = 'permission-denied'
        except ValueError as error:
            lock_state['availability'] = 'busy' if str(error) == 'Mustang builder lock is busy' else 'refused'
        except OSError:
            lock_state['availability'] = 'unreadable'
    output = entry(source/recipe()['outDir'])
    space = os.statvfs(source)
    observation = {'schema': 'roseos.mustang-hostdeps-preflight.v1',
        'identity': dict(run=run, builder=lane['vm'], diskUuid=cfg['dataFilesystemUuid']),
        'guestIdentityVerified': True, 'readOnlyGuestObservation': True,
        'lock': lock_state, 'lockParent': entry(Path(lane['lockPath']).parent),
        'runnerUid': os.geteuid(), 'runnerCanCreateLock': os.access(Path(lane['lockPath']).parent, os.W_OK),
        'dependencyOutput': output,
        'availableBytes': space.f_bavail*(space.f_frsize or space.f_bsize),
        'availableInodes': space.f_favail, 'makeStarted': False,
        'administratorPolicy': admin_policy(),
        'pristineRuntimeAttestation': False, 'authenticatedStop': False,
        'otaAuthorized': False, 'signAuthorized': False, 'publishAuthorized': False}
    receipt(directory/'hostdeps-preflight.json', observation)
    # Only fixed state vocabulary, never paths, exception text or old logs.
    print('::notice title=Mustang host-dependency preflight::lock='+lock_state['state']+
          '; availability='+lock_state.get('availability', 'not-tested')+'; output='+output['state']+
          '; admin-policy='+observation['administratorPolicy']['state'])
    return observation


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('plan', 'run', 'report', 'inspect'), default='plan', nargs='?')
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--out')
    args = parser.parse_args(argv)
    if args.action == 'plan' or (args.action == 'run' and not args.execute):
        print(json.dumps(dict(recipe=recipe(), execute=False, argv=None), sort_keys=True))
        return 4
    try:
        if args.action == 'inspect':
            if not args.execute:
                raise ValueError('guest inspection requires explicit execution')
            inspect_guest(os.environ)
        elif args.action == 'report':
            if not args.out:
                raise ValueError('hostdeps report destination is required')
            report(os.environ, args.out)
        else:
            def cancelled(_signum, _frame):
                raise ValueError('hostdeps cancelled')
            previous = signal.signal(signal.SIGTERM, cancelled)
            try:
                print(json.dumps(execute(os.environ), sort_keys=True))
            finally:
                signal.signal(signal.SIGTERM, previous)
        return 0
    except (ValueError, OSError, KeyError, TypeError, subprocess.SubprocessError, readers._Refused) as error:
        if args.action == 'run':
            try:
                report_failure(error, os.environ)
            except (ValueError, OSError, KeyError, TypeError, subprocess.SubprocessError, readers._Refused):
                # Preserve the original failed exit. A summary is best effort;
                # identity/temp-path failure must never become a success.
                print('Hostdeps failure summary unavailable', file=sys.stderr)
        print(json.dumps({'success': False, 'failure': str(error) if isinstance(error, ValueError) else type(error).__name__,
                          'authenticatedStop': False}), file=sys.stderr)
        return 4


if __name__ == '__main__':
    sys.exit(main())
