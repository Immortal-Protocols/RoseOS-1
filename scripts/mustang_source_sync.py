#!/usr/bin/env python3
"""Mustang source-sync run contract. This process does not start repo."""
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

import mustang_lane

ROOT = Path(__file__).resolve().parents[1]
REVISION = '2ac24c5475de121ebe4e9e6a5c1a404ef44aa9c8'
MANIFEST_SHA = '32d30e70ee59d373a17fe8090bdded898afa80651f27c331caca4578b3008888'
SOURCE = '/mnt/disks/mustang-data/roseos/source'
PIN = '/mnt/disks/mustang-data/roseos/roseos-pin'
USER = 'mustang-runner'
SYNC_TIMEOUT_SECONDS = 10800
STOP_ARGV = ['python3', 'scripts/mustang_controller.py', 'stop', '--ownership-record']
FLAGS = ('compileAuthorized', 'signAuthorized', 'publishAuthorized', 'vendorGenerateAuthorized',
         'allocateAuthorized', 'runtimeBound', 'executed', 'enrollmentClaimed', 'oidcTested', 'jitTested')
APPROVAL_KEYS = {'schema', 'recipeSha256', 'scope', *FLAGS}


def as_runner(argv, workdir):
    if not isinstance(argv, list) or not argv or any(type(part) is not str for part in argv):
        raise ValueError('source sync argv differs')
    if workdir not in (SOURCE, PIN):
        raise ValueError('source sync working directory leaves the Mustang data disk')
    return ['/usr/bin/env', '--chdir', '/tmp', '--', 'sudo', '-H', '-u', USER, '--',
            '/usr/bin/env', '--chdir', workdir, '--', *argv]


def canonical_argv(env=None):
    """Exact commands. A caller environment cannot change the revision."""
    if env is not None and not isinstance(env, dict):
        raise ValueError('source sync environment differs')
    return [
        ['curl', '-fsS', '-H', 'Metadata-Flavor: Google',
         'http://metadata.google.internal/computeMetadata/v1/instance/id'],
        ['findmnt', '--noheadings', '--output', 'UUID', '--target', '/mnt/disks/mustang-data'],
        as_runner(['stat', '-c', '%U %a', SOURCE], SOURCE),
        as_runner(['git', 'rev-parse', 'HEAD'], PIN),
        as_runner(['sha256sum', 'os/pinned-manifest.xml'], PIN),
        as_runner(['repo', 'init', '-u', 'https://github.com/Immortal-Protocols/RoseOS-1.git',
                   '-b', REVISION, '-m', 'os/pinned-manifest.xml'], SOURCE),
        as_runner(['repo', 'sync', '-c', '-j4', '--fail-fast'], SOURCE),
        as_runner(['repo', 'manifest', '-r', '-o', 'resolved-manifest.xml'], SOURCE),
    ]


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


def project_count(root=ROOT):
    return len(list(ET.parse(Path(root)/'os/pinned-manifest.xml').getroot().iter('project')))


def require_resolved(xml_bytes, root=ROOT):
    if not isinstance(xml_bytes, (bytes, bytearray)):
        raise ValueError('resolved manifest bytes are absent')
    expected = project_count(root)
    try:
        document = ET.fromstring(xml_bytes)
    except ET.ParseError:
        raise ValueError('resolved manifest is not XML') from None
    projects = list(document.iter('project'))
    if expected < 1000 or len(projects) != expected:
        raise ValueError('resolved manifest project count differs')
    for project in projects:
        revision = project.get('revision')
        if type(revision) is not str or len(revision) != 40 or any(char not in '0123456789abcdef' for char in revision):
            raise ValueError('resolved manifest revision is not a commit')
    return {'sha256': hashlib.sha256(bytes(xml_bytes)).hexdigest(), 'projectCount': expected,
            'executed': False, 'compileAuthorized': False}


def _stop(stop, reason, **extra):
    stop({'reason': reason, 'stopArgv': list(STOP_ARGV), **extra})


def on_command(name, exit_code, stop):
    if isinstance(exit_code, bool) or type(exit_code) is not int:
        raise ValueError('source sync exit differs')
    if exit_code != 0:
        _stop(stop, 'source-sync-failed', command=name, exit=exit_code)
        raise ValueError('source sync failed and the VM stop was requested')


def watch_sync(state, elapsed, stop, timeout=SYNC_TIMEOUT_SECONDS):
    if isinstance(timeout, bool) or type(timeout) is not int or timeout <= 0 \
            or timeout >= mustang_lane.CONFIRMED_STOP['seconds']:
        raise ValueError('source sync timeout is not tighter than the runner crash stop')
    if isinstance(elapsed, bool) or type(elapsed) is not int or elapsed < 0:
        raise ValueError('source sync clock differs')
    if state is None and elapsed >= timeout:
        _stop(stop, 'source-sync-timeout', timeoutSeconds=timeout)
        raise ValueError('source sync exceeded the bounded timeout and the VM stop was requested')
    if state is None:
        return 'wait'
    if isinstance(state, bool) or type(state) is not int:
        raise ValueError('source sync exit differs')
    if state != 0 or elapsed >= timeout:
        reason = 'source-sync-timeout' if elapsed >= timeout else 'source-sync-failed'
        _stop(stop, reason, timeoutSeconds=timeout, exit=state)
        raise ValueError('source sync failed and the VM stop was requested')
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


def main():
    record = load()
    print(json.dumps({'admitted': False, 'executed': False, 'roseRevision': record['roseRevision'],
                      'sourceRoot': record['sourceRoot'], 'recipeSha256': recipe_sha256(),
                      'syncTimeoutSeconds': record['syncTimeoutSeconds']}, sort_keys=True))
    return 4


if __name__ == '__main__':
    raise SystemExit(main())
