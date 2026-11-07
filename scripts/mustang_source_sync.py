#!/usr/bin/env python3
"""Mustang source-sync orchestration. The default command does not start repo.

The guest has no attached service account and no controller credential. This
process does not stop the VM. The dedicated workflow's GitHub-hosted cleanup
job is the owned STOP, and the instance maxRunDuration of 14400 seconds
remains the backstop.
"""
import argparse
import hashlib
import json
import os
import pwd
import re
import signal
import subprocess
import time
from pathlib import Path
import xml.etree.ElementTree as ET

import mustang_lane

ROOT = Path(__file__).resolve().parents[1]
REVISION = '2ac24c5475de121ebe4e9e6a5c1a404ef44aa9c8'
MANIFEST_SHA = '32d30e70ee59d373a17fe8090bdded898afa80651f27c331caca4578b3008888'
SOURCE = '/mnt/disks/mustang-data/roseos/source'
PIN = '/mnt/disks/mustang-data/roseos/roseos-pin'
ORIGIN = 'https://github.com/Immortal-Protocols/RoseOS-1.git'
MANIFEST_CHECKOUT = '.repo/manifests'
PIN_REF = 'mustang-pin'
LOG_ROOT = '/mnt/disks/mustang-data/roseos/sync-logs'
MAX_STDOUT_RECEIPT = 8 * 1024 * 1024
USER = 'mustang-runner'
SYNC_TIMEOUT_SECONDS = 10800
WORKFLOW_REF = 'Immortal-Protocols/RoseOS-1/.github/workflows/mustang-ota-dedicated.yml@refs/heads/main'
FLAGS = ('compileAuthorized', 'signAuthorized', 'publishAuthorized', 'vendorGenerateAuthorized',
         'allocateAuthorized', 'runtimeBound', 'executed', 'enrollmentClaimed', 'oidcTested', 'jitTested')
APPROVAL_KEYS = {'schema', 'recipeSha256', 'scope', *FLAGS}


def as_runner(argv, workdir):
    if not isinstance(argv, list) or not argv or any(type(part) is not str for part in argv):
        raise ValueError('source sync argv differs')
    if workdir not in (SOURCE, PIN, '/tmp'):
        raise ValueError('source sync working directory leaves the Mustang data disk')
    # The runner service sets NoNewPrivileges, so sudo cannot run in the job.
    return ['/usr/bin/env', '--chdir', workdir, '--', *argv]


def install_dir(path):
    return ['install', '-d', '-m', '0750', path]


def pin_commands(pin, origin, revision):
    """Idempotent pin setup. config overwrites a remote left by a failed fetch.

    The fetch is not depth-limited. Git refuses to fetch a branch from a
    shallow repository, so a depth-1 pin cannot be the repo manifest remote.
    """
    return [
        ['git', 'init', pin],
        ['git', '-C', pin, 'config', 'remote.origin.url', origin],
        ['git', '-C', pin, 'config', 'remote.origin.fetch', '+refs/heads/*:refs/remotes/origin/*'],
        ['git', '-C', pin, 'fetch', 'origin', revision],
        ['git', '-C', pin, 'checkout', '--detach', revision],
        ['git', '-C', pin, 'branch', '-f', PIN_REF, revision],
    ]


def repo_init_command(pin, revision, manifest):
    """Checkout the local mustang-pin branch. Ubuntu repo 2.36 has no upstream-branch flag."""
    if not re.fullmatch(r'[0-9a-f]{40}', revision):
        raise ValueError('source sync revision differs')
    return ['repo', 'init', '-u', pin, '-b', PIN_REF, '--no-use-superproject', '-m', manifest]


def steps(env=None):
    """Bootstrap the pinned RoseOS tree on the builder, then sync only after that checkout matches."""
    if env is not None and not isinstance(env, dict):
        raise ValueError('source sync environment differs')
    return [
        {'name': 'instance', 'limit': 60, 'expect': mustang_lane.CONFIRMED_INSTANCE_ID,
         'argv': ['curl', '-fsS', '-H', 'Metadata-Flavor: Google',
                  'http://metadata.google.internal/computeMetadata/v1/instance/id']},
        {'name': 'disk', 'limit': 60, 'expect': mustang_lane.CONFIRMED_UUID,
         'argv': ['findmnt', '--noheadings', '--output', 'UUID', '--target', '/mnt/disks/mustang-data']},
        {'name': 'pin-dir', 'limit': 60, 'argv': install_dir(PIN)},
        {'name': 'source-dir', 'limit': 60, 'argv': install_dir(SOURCE)},
        {'name': 'git-init', 'limit': 60, 'argv': as_runner(pin_commands(PIN, ORIGIN, REVISION)[0], '/tmp')},
        {'name': 'git-url', 'limit': 60, 'argv': as_runner(pin_commands(PIN, ORIGIN, REVISION)[1], '/tmp')},
        {'name': 'git-fetchspec', 'limit': 60, 'argv': as_runner(pin_commands(PIN, ORIGIN, REVISION)[2], '/tmp')},
        {'name': 'git-fetch', 'limit': 600, 'argv': as_runner(pin_commands(PIN, ORIGIN, REVISION)[3], '/tmp')},
        {'name': 'git-checkout', 'limit': 60, 'argv': as_runner(pin_commands(PIN, ORIGIN, REVISION)[4], '/tmp')},
        {'name': 'git-branch', 'limit': 60, 'argv': as_runner(pin_commands(PIN, ORIGIN, REVISION)[5], '/tmp')},
        {'name': 'pin-head', 'limit': 60, 'expect': REVISION,
         'argv': as_runner(['git', '-C', PIN, 'rev-parse', 'HEAD'], '/tmp')},
        {'name': 'pin-hash', 'limit': 60, 'expect': MANIFEST_SHA+'  '+PIN+'/os/pinned-manifest.xml',
         'argv': as_runner(['sha256sum', PIN+'/os/pinned-manifest.xml'], '/tmp')},
        {'name': 'repo-init', 'limit': 600,
         'argv': as_runner(repo_init_command(PIN, REVISION, 'os/pinned-manifest.xml'), SOURCE)},
        {'name': 'manifest-head', 'limit': 60, 'expect': REVISION,
         'argv': as_runner(['git', '-C', MANIFEST_CHECKOUT, 'rev-parse', 'HEAD'], SOURCE)},
        {'name': 'manifest-hash', 'limit': 60,
         'expect': MANIFEST_SHA+'  '+MANIFEST_CHECKOUT+'/os/pinned-manifest.xml',
         'argv': as_runner(['sha256sum', MANIFEST_CHECKOUT+'/os/pinned-manifest.xml'], SOURCE)},
        {'name': 'repo-sync', 'limit': SYNC_TIMEOUT_SECONDS,
         'argv': as_runner(['repo', 'sync', '-c', '-j4', '--fail-fast'], SOURCE)},
        {'name': 'resolved', 'limit': 120,
         'argv': as_runner(['repo', 'manifest', '-r', '-o', 'resolved-manifest.xml'], SOURCE)},
        {'name': 'resolved-bytes', 'limit': 120,
         'argv': as_runner(['cat', 'resolved-manifest.xml'], SOURCE)},
    ]


def canonical_argv(env=None):
    """Exact commands. A caller environment cannot change the revision."""
    return [step['argv'] for step in steps(env)]


def _load(path):
    return json.loads(Path(path).read_text())


def _exact(text, expected, label):
    if type(text) is not str or text not in (expected, expected+'\n'):
        raise ValueError(label)
    return expected


def require_host(instance_text, uuid_text):
    _exact(instance_text, mustang_lane.CONFIRMED_INSTANCE_ID, 'source sync instance differs')
    _exact(uuid_text, mustang_lane.CONFIRMED_UUID, 'source sync disk uuid differs')


def require_before_sync(instance_text, uuid_text, stat_text, head_text, manifest_sum_text):
    require_host(instance_text, uuid_text)
    _exact(stat_text, USER+' 750', 'source directory is not mustang-runner mode 0750')
    _exact(head_text, REVISION, 'RoseOS git HEAD is not the pinned revision')
    _exact(manifest_sum_text, MANIFEST_SHA+'  os/pinned-manifest.xml', 'pinned manifest hash differs')


def project_identities(xml_bytes):
    try:
        document = ET.fromstring(xml_bytes)
    except ET.ParseError:
        raise ValueError('resolved manifest is not XML') from None
    default = document.find('default')
    default_remote = default.get('remote') if default is not None else None
    found = []
    for project in document.iter('project'):
        name = project.get('name')
        path = project.get('path') or name
        remote = project.get('remote') or default_remote
        revision = project.get('revision')
        if type(name) is not str or type(path) is not str or type(remote) is not str or type(revision) is not str:
            raise ValueError('resolved manifest project is incomplete')
        found.append((name, path, remote, revision))
    if len(found) != len(set(found)):
        raise ValueError('resolved manifest project is repeated')
    return found


def require_resolved(xml_bytes, root=ROOT):
    if not isinstance(xml_bytes, (bytes, bytearray)):
        raise ValueError('resolved manifest bytes are absent')
    pinned = project_identities((Path(root)/'os/pinned-manifest.xml').read_bytes())
    resolved = project_identities(xml_bytes)
    if sorted(resolved) != sorted(pinned):
        raise ValueError('resolved manifest does not match the pinned project set')
    return {'sha256': hashlib.sha256(bytes(xml_bytes)).hexdigest(), 'projectCount': len(pinned),
            'executed': False, 'compileAuthorized': False}


def guest_failure(reason, **extra):
    """A local failure record. It is not a STOP and it carries no command to run."""
    if 'argv' in extra or 'stopArgv' in extra:
        raise ValueError('source sync failure cannot carry a stop command')
    return {'reason': reason, 'authenticatedStop': False, 'localControllerStop': False, **extra}


def _stop(stop, reason, **extra):
    stop(guest_failure(reason, **extra))


def on_command(name, exit_code, stop):
    if isinstance(exit_code, bool) or type(exit_code) is not int:
        raise ValueError('source sync exit differs')
    if exit_code != 0:
        _stop(stop, 'source-sync-failed', command=name, exit=exit_code)
        raise ValueError('source sync failed; the guest did not stop the VM')


def watch_sync(state, elapsed, stop, timeout=SYNC_TIMEOUT_SECONDS):
    if isinstance(timeout, bool) or type(timeout) is not int or timeout <= 0 \
            or timeout >= mustang_lane.CONFIRMED_STOP['seconds']:
        raise ValueError('source sync timeout is not tighter than the runner crash stop')
    if isinstance(elapsed, bool) or type(elapsed) is not int or elapsed < 0:
        raise ValueError('source sync clock differs')
    if state is None and elapsed >= timeout:
        _stop(stop, 'source-sync-timeout', timeoutSeconds=timeout)
        raise ValueError('source sync exceeded the bounded timeout; the guest did not stop the VM')
    if state is None:
        return 'wait'
    if isinstance(state, bool) or type(state) is not int:
        raise ValueError('source sync exit differs')
    if state != 0 or elapsed >= timeout:
        reason = 'source-sync-timeout' if elapsed >= timeout else 'source-sync-failed'
        _stop(stop, reason, timeoutSeconds=timeout, exit=state)
        raise ValueError('source sync failed; the guest did not stop the VM')
    return 'exited'


def validate(record, root=ROOT):
    root = Path(root)
    if not isinstance(record, dict) or record.get('schema') != 'roseos.mustang-source-sync-recipe.v1':
        raise ValueError('source sync recipe schema differs')
    requirement = _load(root/'os/build-only/mustang-source-requirement-20261107.json')
    factory = _load(root/'os/build-only/mustang-factory-box-20261107.json')
    manifest = hashlib.sha256((root/'os/pinned-manifest.xml').read_bytes()).hexdigest()
    if record.get('roseRevision') != REVISION or requirement.get('roseRevision') != REVISION \
            or requirement.get('upstream', {}).get('roseRevision') != REVISION or manifest != MANIFEST_SHA \
            or record.get('manifestSha256') != manifest:
        raise ValueError('source sync revision or manifest hash differs')
    if record.get('adevtool') != requirement.get('upstream', {}).get('adevtool'):
        raise ValueError('source sync adevtool revision differs')
    boxed = record.get('factory') or {}
    if factory.get('match') is not True or boxed.get('sha256') != factory.get('sha256') \
            or boxed.get('sizeBytes') != factory.get('sizeBytes') or boxed.get('extracted') is not False \
            or boxed.get('vendorGenerated') is not False or boxed.get('copiedToDedicatedDisk') is not False \
            or boxed.get('location') != 'box-only':
        raise ValueError('source sync factory input differs from the verified box object')
    host = record.get('checkoutHost') or {}
    if record.get('sourceRoot') != SOURCE or record.get('roseosPin') != PIN \
            or host.get('instance') != mustang_lane.CONFIRMED_INSTANCE \
            or host.get('instanceId') != mustang_lane.CONFIRMED_INSTANCE_ID \
            or host.get('diskUuid') != mustang_lane.CONFIRMED_UUID:
        raise ValueError('source checkout host differs from the dedicated Mustang VM')
    if record.get('argv') != canonical_argv() or record.get('syncTimeoutSeconds') != SYNC_TIMEOUT_SECONDS \
            or record.get('runnerCrashStopSeconds') != mustang_lane.CONFIRMED_STOP['seconds'] \
            or record.get('approval') is not None:
        raise ValueError('source sync argv differs from the canonical contract')
    for flag in FLAGS:
        if record.get(flag) is not False:
            raise ValueError('source sync cannot authorize compile, signing, enrollment, or publication')
    if SYNC_TIMEOUT_SECONDS >= mustang_lane.CONFIRMED_STOP['seconds']:
        raise ValueError('source sync timeout is not tighter than the runner crash stop')
    return record


def recipe_path(root=ROOT):
    return Path(root)/'os/build-only/mustang-source-sync-recipe-20261107.json'


def load(root=ROOT):
    return validate(json.loads(recipe_path(root).read_bytes()), root)


def recipe_sha256(root=ROOT):
    return hashlib.sha256(recipe_path(root).read_bytes()).hexdigest()


def admit(approval, root=ROOT):
    """Admit source sync only when the approval hash is the recipe bytes. Nothing is executed."""
    raw = recipe_path(root).read_bytes()
    record = validate(json.loads(raw), root)
    if not isinstance(approval, dict) or set(approval) != APPROVAL_KEYS:
        raise ValueError('source sync approval fields differ')
    if approval.get('schema') != 'roseos.mustang-source-sync-approval.v1' or approval.get('scope') != 'source-sync-only':
        raise ValueError('source sync approval is not source-only')
    if approval.get('recipeSha256') != hashlib.sha256(raw).hexdigest():
        raise ValueError('source sync approval hash differs')
    for flag in FLAGS:
        if approval.get(flag) is not False:
            raise ValueError('source sync approval authorizes more than source sync')
    return {'admitted': 'source-sync-only', 'executed': False, 'argv': record['argv'],
            'syncTimeoutSeconds': SYNC_TIMEOUT_SECONDS, 'recipeSha256': approval['recipeSha256']}


def hosted_source_job(env):
    """Execute only inside the dedicated workflow's source job. That job's cleanup stops the VM."""
    if env.get('GITHUB_EVENT_NAME') != 'workflow_dispatch' or env.get('GITHUB_REF') != 'refs/heads/main' \
            or env.get('GITHUB_WORKFLOW_REF') != WORKFLOW_REF or env.get('GITHUB_RUN_ATTEMPT') != '1' \
            or env.get('GITHUB_JOB') != 'source' or env.get('MUSTANG_SOURCE_ONLY') != 'true' \
            or not re.fullmatch(r'[1-9][0-9]{0,19}', env.get('GITHUB_RUN_ID', '')) \
            or not re.fullmatch(r'[0-9a-f]{40}', env.get('GITHUB_SHA', '')):
        raise ValueError('source sync execute is only the dedicated workflow source job')


def require_runner_user():
    try:
        record = pwd.getpwnam(USER)
    except KeyError:
        raise ValueError('source sync runner user is absent') from None
    if os.geteuid() != record.pw_uid:
        raise ValueError('source sync must run as mustang-runner without sudo')


def workflow_approval(root=ROOT):
    raw = recipe_path(root).read_bytes()
    recipe = json.loads(raw)
    return {'schema': 'roseos.mustang-source-sync-approval.v1', 'scope': 'source-sync-only',
            'recipeSha256': hashlib.sha256(raw).hexdigest(), **{flag: recipe[flag] for flag in FLAGS}}


def elapsed_seconds(start, now):
    if isinstance(now, bool) or isinstance(start, bool) or type(now) not in (int, float) or type(start) not in (int, float):
        raise ValueError('source sync clock differs')
    return int(now - start)


def run_bounded(argv, timeout, spawn, stop, clock, sleep):
    if isinstance(timeout, bool) or type(timeout) is not int or timeout <= 0 \
            or timeout >= mustang_lane.CONFIRMED_STOP['seconds']:
        raise ValueError('source sync timeout is not tighter than the runner crash stop')
    started = clock()
    proc = spawn(argv)
    while True:
        code = proc.poll()
        elapsed = elapsed_seconds(started, clock())
        if code == 0 and elapsed < timeout:
            return proc.output()
        if code is None and elapsed < timeout:
            sleep(min(5, timeout-elapsed))
            continue
        if code is None:
            proc.kill()
            stop(guest_failure('source-sync-timeout'))
            raise ValueError('source sync exceeded the bounded timeout; the guest did not stop the VM')
        stop(guest_failure('source-sync-failed', exit=code))
        raise ValueError('source sync failed; the guest did not stop the VM')


def spawn_subprocess(argv, log_dir=None):
    """Write step output to private files. A full pipe is not polled, so it would deadlock."""
    directory = Path(log_dir) if log_dir is not None else Path(LOG_ROOT)
    directory.mkdir(parents=True, exist_ok=True)
    os.chmod(directory, 0o700)
    stamp = str(time.time_ns())
    stdout_path = directory/(stamp+'.stdout')
    stderr_path = directory/(stamp+'.stderr')
    stdout_handle = os.fdopen(os.open(stdout_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'wb')
    stderr_handle = os.fdopen(os.open(stderr_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'wb')
    try:
        proc = subprocess.Popen(argv, stdout=stdout_handle, stderr=stderr_handle, start_new_session=True)
    finally:
        stdout_handle.close()
        stderr_handle.close()

    class Bound:
        def poll(self):
            return proc.poll()

        def kill(self):
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except ProcessLookupError:
                return
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                proc.wait(timeout=5)

        def output(self):
            size = stdout_path.stat().st_size
            with stdout_path.open('rb') as handle:
                data = handle.read(MAX_STDOUT_RECEIPT)
            if size > MAX_STDOUT_RECEIPT:
                return data.decode(errors='replace')
            return data.decode()

        @property
        def stdout_path(self):
            return stdout_path

        @property
        def stderr_path(self):
            return stderr_path
    return Bound()


def cancellation_handler(holder):
    """SIGTERM kills the current step only. It does not authenticate or request a VM stop."""
    def handler(signum, _frame):
        proc = holder.get('proc')
        if proc is not None:
            proc.kill()
        raise SystemExit(4)
    return handler


def normalized_resolved(root=ROOT, mutate=None):
    projects = project_identities((Path(root)/'os/pinned-manifest.xml').read_bytes())
    if mutate is not None:
        projects = mutate(list(projects))
    body = '<manifest>\n'+''.join(
        '<project name="%s" path="%s" remote="%s" revision="%s"/>\n' % item for item in projects)+'</manifest>'
    return body.encode()


def orchestrate(approval, _ownership, spawn, stop, clock, sleep, root=ROOT):
    """Run the admitted source sync. A failure is recorded locally and does not stop the VM."""
    halted = {'done': False}

    def halt(reason):
        if not halted['done']:
            halted['done'] = True
            stop(guest_failure(reason))

    try:
        admitted = admit(approval, root)
    except ValueError:
        halt('source-sync-not-admitted')
        raise
    observed = {}
    started = clock()
    try:
        for step in steps():
            remaining = SYNC_TIMEOUT_SECONDS - elapsed_seconds(started, clock())
            if remaining <= 0:
                halt('source-sync-timeout')
                raise ValueError('source sync exceeded the bounded timeout; the guest did not stop the VM')
            limit = step['limit'] if step['limit'] < remaining else remaining
            try:
                text = run_bounded(step['argv'], limit, spawn, lambda item: halt(item['reason']), clock, sleep)
            except ValueError:
                halt('source-sync-failed')
                raise
            if 'expect' in step:
                _exact(text, step['expect'], 'source sync '+step['name']+' differs')
                observed[step['name']] = text.strip().split('  ', 1)[0]
            if step['name'] == 'resolved-bytes':
                compared = require_resolved(text.encode(), root)
                observed['resolvedSha256'] = compared['sha256']
                observed['projectCount'] = compared['projectCount']
    except ValueError:
        halt('source-sync-failed')
        raise
    return {'schema': 'roseos.mustang-source-sync-run.v1', 'admitted': 'source-sync-only', 'executed': True,
            'observed': observed, 'historical': {'oidcExercised': False},
            'compileAuthorized': False, 'signAuthorized': False, 'runtimeBound': False,
            'authenticatedStop': False,
            'runnerCrashStopSeconds': mustang_lane.CONFIRMED_STOP['seconds'],
            'cancellation': 'SIGTERM kills the step process group only. The guest does not stop the VM. '
                            'Workflow cleanup performs the owned STOP. The instance maxRunDuration remains 14400 seconds.',
            'recipeSha256': admitted['recipeSha256']}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', nargs='?', default='show', choices=('show', 'run'))
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--approval')
    parser.add_argument('--ownership-record')
    args = parser.parse_args(argv)
    record = load()
    if args.action == 'show' or not args.execute:
        print(json.dumps({'admitted': False, 'executed': False, 'roseRevision': record['roseRevision'],
                          'sourceRoot': record['sourceRoot'], 'recipeSha256': recipe_sha256(),
                          'syncTimeoutSeconds': record['syncTimeoutSeconds']}, sort_keys=True))
        return 4
    if args.ownership_record:
        print('the guest does not stop the VM')
        return 4
    holder = {}

    def spawn(argv):
        proc = spawn_subprocess(argv)
        holder['proc'] = proc
        return proc

    previous = signal.signal(signal.SIGTERM, cancellation_handler(holder))
    try:
        try:
            hosted_source_job(os.environ)
            require_runner_user()
            approval = json.loads(Path(args.approval).read_text()) if args.approval else workflow_approval()
            receipt = orchestrate(approval, None, spawn, lambda _detail: None, time.monotonic, time.sleep)
        except (OSError, ValueError, json.JSONDecodeError):
            print('source sync failed; the guest did not stop the VM')
            return 4
    finally:
        signal.signal(signal.SIGTERM, previous)
    print(json.dumps(receipt, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
