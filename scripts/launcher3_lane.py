"""Launcher3's dedicated capacity contract; source checks only, no cloud operations.

The compile input contract remains Frankel-targeted. Its capacity must be independent
of every release lane and Agent. Binding a different label to a production VM fails.
"""
import hashlib
import json
import os
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT/'os/build-only/launcher3-compile-lane.json'
REPOSITORY = 'Immortal-Protocols/RoseOS-1'
WORKFLOW = '.github/workflows/launcher3-compile.yml'
LANE_KEYS = {'status', 'workflow', 'runnerName', 'runnerLabel', 'vm', 'disk', 'storageRoot', 'lockPath',
             'queue', 'controller', 'infraEvidence'}
VM_PATTERNS = {'project': r'[a-z][a-z0-9-]{4,28}[a-z0-9]', 'zone': r'[a-z][a-z0-9-]{0,62}',
               'instance': r'[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?', 'instanceId': r'[1-9][0-9]{0,19}'}
# Frankel's numeric identity is also pinned by mustang_lane.validate. Keep it refused
# even if a proposed binding supplies an alias for its project/zone/name.
FRANKEL_INSTANCE_ID = '685707771105568194'
FRANKEL_DISK = 'projects/protocols-465723/zones/us-west2-a/disks/immortal-os-builder'


class Invalid(ValueError):
    pass


class Unbound(ValueError):
    pass


def strict_json(data):
    def pairs(rows):
        result = {}
        for key, value in rows:
            if key in result:
                raise Invalid('duplicate JSON key')
            result[key] = value
        return result

    def constant(value):
        raise Invalid('non-finite JSON value')

    return json.loads(data, object_pairs_hook=pairs, parse_constant=constant)


def load(path=CONFIG):
    return strict_json(Path(path).read_bytes())


def digest(binding):
    return hashlib.sha256(json.dumps(binding, sort_keys=True, separators=(',', ':'),
                                    ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def absolute(value):
    return isinstance(value, str) and value.startswith('/') and value != '/' \
        and os.path.normpath(value) == value and not value.startswith('//')


def within(path, parent):
    return Path(parent) in Path(path).parents


def same_token(value, other):
    # Conservatively reserve case variants too; a spelling change is not isolation.
    return isinstance(value, str) and isinstance(other, str) and value.casefold() == other.casefold()


def other_capacity(root=ROOT):
    """Read existing owners' bindings without editing them or inferring readiness.

    Unbound profiles supply no identity; Infra's evidence must still establish that
    a new runner/disk is exclusive. Known identities are refused even when retired.
    """
    capacity = strict_json((Path(root)/'os/release-pipeline/lanes/capacity.json').read_bytes())
    hosts = capacity['hosts']
    names = {row['host'] for name, row in capacity['lanes'].items() if name != 'harness'}
    rows = [hosts[name] for name in sorted(names)]
    mustang = strict_json((Path(root)/'os/build-only/mustang-lane.json').read_bytes())
    rows.append(mustang)
    yogi = strict_json((Path(root)/'os/ota/yogi.json').read_bytes())['builder']
    runner = (yogi.get('evidence') or {}).get('runner') or {}
    rows.append({'vm': {k: yogi.get(k) for k in VM_PATTERNS}, 'runnerLabel': yogi.get('runnerLabel'),
                 'runnerName': runner.get('name'), 'runnerLabels': runner.get('labels', []),
                 'disk': {'id': yogi.get('restoredDiskId')}})
    retired = yogi.get('retired') or {}
    rows.append({'vm': {'project': yogi.get('project'), **{k: retired.get(k) for k in ('zone', 'instance', 'instanceId')}}})
    return rows


def validate(binding, root=ROOT):
    """Validate capacity only. Compile provenance remains the compile adapter's job."""
    if not isinstance(binding, dict):
        raise Invalid('dedicated binding must be an object')
    lane = binding.get('lane')
    if not isinstance(lane, dict) or set(lane) != LANE_KEYS:
        raise Invalid('dedicated lane must have exactly the reviewed capacity fields')
    if lane['workflow'] != WORKFLOW or lane['status'] not in ('unbound', 'bound'):
        raise Invalid('dedicated lane workflow or status is invalid; legacy routing is retired')
    for name, keys in (('vm', set(VM_PATTERNS)), ('disk', {'resource', 'id'}),
                       ('controller', {'identityVariable', 'providerVariable'})):
        if not isinstance(lane[name], dict) or set(lane[name]) != keys:
            raise Invalid(f'dedicated lane {name} has the wrong fields')
    missing = [name for name, value in lane.items() if value is None]
    missing += [f'{name}.{key}' for name in ('vm', 'disk', 'controller')
                for key, value in lane[name].items() if value is None]
    if binding.get('taskParent') is None:
        missing.append('taskParent')
    if lane['status'] != 'bound' or missing:
        raise Unbound('independent harness capacity is unbound: ' + ', '.join(missing or ['status']))
    vm = lane['vm']
    if any(not isinstance(vm[k], str) or not re.fullmatch(pattern, vm[k]) for k, pattern in VM_PATTERNS.items()):
        raise Invalid('dedicated VM requires exact project, zone, instance and numeric instanceId')
    for field in ('runnerName', 'runnerLabel', 'queue'):
        if not isinstance(lane[field], str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,99}', lane[field]):
            raise Invalid(f'dedicated {field} is invalid')
    if not isinstance(lane['infraEvidence'], str) or not lane['infraEvidence'].strip():
        raise Invalid('dedicated capacity needs reviewed Infra evidence')
    for key, suffix in (('identityVariable', 'SERVICE_ACCOUNT'), ('providerVariable', 'PROVIDER')):
        if not isinstance(lane['controller'][key], str) or not re.fullmatch(
                r'LAUNCHER3_[A-Z0-9_]*' + suffix, lane['controller'][key]):
            raise Invalid('dedicated controller must name Launcher3-specific identity/provider variables')
    disk = lane['disk']
    prefix = f'projects/{vm["project"]}/zones/{vm["zone"]}/disks/'
    if not isinstance(disk['resource'], str) or not disk['resource'].startswith(prefix) \
            or not re.fullmatch(r'[a-z][a-z0-9-]{0,62}', disk['resource'][len(prefix):]) \
            or not isinstance(disk['id'], str) or not re.fullmatch(r'[1-9][0-9]{0,19}', disk['id']):
        raise Invalid('dedicated disk needs its zonal resource and numeric identity')
    storage, parent, lock = lane['storageRoot'], binding['taskParent'], lane['lockPath']
    if not all(absolute(p) for p in (storage, parent, lock)) or not within(parent, storage) \
            or not within(lock, storage) or lock == parent or within(lock, parent) or within(parent, lock):
        raise Invalid('taskParent and lockPath must be separate paths below dedicated storageRoot')
    if binding.get('sourceTree') is not None and (not absolute(binding['sourceTree'])
                                                 or not within(binding['sourceTree'], storage)):
        raise Invalid('sourceTree must be staged below dedicated storageRoot')
    for name in ('policyReceipt', 'bundleReport'):
        value = binding.get(name)
        path = value.get('path') if isinstance(value, dict) else None
        if path is not None and (not absolute(path) or not within(path, storage)):
            raise Invalid(f'{name}.path must be staged below dedicated storageRoot')
    if vm['instanceId'] == FRANKEL_INSTANCE_ID or disk['resource'] == FRANKEL_DISK:
        raise Invalid('dedicated capacity aliases the Frankel builder')
    for other in other_capacity(root):
        other_vm = other.get('vm') or {}
        same_name = all(vm[k] == other_vm.get(k) for k in ('project', 'zone', 'instance'))
        same_id = vm['instanceId'] == other_vm.get('instanceId')
        other_disk = other.get('disk') or {}
        if same_name or same_id or same_token(lane['runnerLabel'], other.get('runnerLabel')) \
                or any(same_token(lane['runnerLabel'], label) for label in (other.get('runnerLabels') or [])) \
                or any(same_token(lane['runnerName'], name) for name in (other.get('runnerName'), other_vm.get('instance'))) \
                or same_token(lane['queue'], other.get('queue')) \
                or disk['resource'] in (other_disk.get('resource'), other.get('diskResource')) \
                or disk['id'] == other_disk.get('id') \
                or same_token(lane['controller']['identityVariable'], (other.get('controller') or {}).get('identityVariable')):
            raise Invalid('dedicated capacity shares another lane VM, runner, disk, queue or controller')
    return lane


def check_context(env):
    """Hosted guard must never make the old os-release controller reachable again."""
    ref = env.get('GITHUB_WORKFLOW_REF')
    expected = f'{REPOSITORY}/{WORKFLOW}@refs/heads/main'
    if (ref or env.get('GITHUB_ACTIONS') == 'true') and ref != expected:
        raise Invalid('legacy or foreign workflow refused; use the dedicated Launcher3 workflow')


def invocation(env):
    check_context(env)
    expected = f'{REPOSITORY}/{WORKFLOW}@refs/heads/main'
    sha, run_id, attempt = (env.get(k, '') for k in ('GITHUB_SHA', 'GITHUB_RUN_ID', 'GITHUB_RUN_ATTEMPT'))
    if env.get('GITHUB_WORKFLOW_REF') != expected or env.get('GITHUB_REPOSITORY') != REPOSITORY \
            or env.get('GITHUB_EVENT_NAME') != 'workflow_dispatch' or env.get('GITHUB_REF') != 'refs/heads/main' \
            or env.get('GITHUB_WORKFLOW_SHA') != sha or not re.fullmatch(r'[0-9a-f]{40}', sha) \
            or not re.fullmatch(r'[1-9][0-9]{0,19}', run_id) or attempt != '1':
        raise Invalid('dedicated lifecycle requires its exact main workflow, source and fresh run attempt')
    return {'workflow': expected, 'source': sha, 'runId': run_id, 'runAttempt': attempt}


def worker(binding, env, run_id, attempt):
    lane = validate(binding)
    run = invocation(env)
    if (run_id, attempt) != (run['runId'], run['runAttempt']) or env.get('RUNNER_NAME') != lane['runnerName']:
        raise Invalid('worker identity or run does not match the dedicated binding')
    paths = [lane['storageRoot'], binding['taskParent'], lane['lockPath'], binding['sourceTree']]
    paths += [binding[name]['path'] for name in ('policyReceipt', 'bundleReport') if binding[name]['path'] is not None]
    for path in paths:
        if path is None or os.path.realpath(path) != path:
            raise Invalid('dedicated worker paths must not resolve through links')
    return run
