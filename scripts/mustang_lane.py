"""Mustang route retirement and source-only dedicated lifecycle contract.

No cloud client, process execution, credentials, host inspection or deletion.
The plan is for the existing Release owner to integrate with the shared
controller. It never makes a legacy entry point usable again.
"""
import argparse
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT/'os/build-only/mustang-lane.json'
READBACK = 'os/build-only/mustang-infra-readback-20261107.json'
CONFIRMED_INSTANCE = 'roseos-mustang-builder-20261107-a'
CONFIRMED_INSTANCE_ID = '5585425566393384049'
CONFIRMED_UUID = '4183bff8-6be4-46bb-a641-2193d19813ee'
CONFIRMED_MOUNT = '/mnt/disks/mustang-data'
CONFIRMED_DISK_ID = '4322966418460195960'
CONFIRMED_FREE_BYTES = 1584163655680
CONFIRMED_STOP = {'seconds': 14400, 'action': 'STOP', 'hours': 4}
FRANKEL_INSTANCE_ID = '685707771105568194'
RETIRED = 'Mustang legacy route retired: Frankel builder is forbidden; dedicated Mustang lifecycle is not activated'
OTHER_STORAGE = ('/home/pixelbuild', '/srv/rose-yogi')


def reject_legacy():
    # No environment flag or future profile binding can restore the old route.
    raise ValueError(RETIRED)


def configuration():
    return json.loads(CONFIG.read_text())


def attached_readback(lane, root=ROOT):
    """The confirmed VM must match its readback. Synthetic fixtures are not that VM."""
    if lane.get('infraEvidence') != READBACK:
        if (lane.get('vm') or {}).get('instance') == CONFIRMED_INSTANCE:
            raise ValueError
        return None
    path = Path(root)/READBACK
    if path.is_symlink() or not path.is_file():
        raise ValueError
    value = json.loads(path.read_text())
    vm, disk = lane['vm'], lane['diskResource']
    recorded, layout = lane.get('disk'), value.get('layout') or {}
    identity = value.get('disk') or {}
    if value.get('schema') != 'roseos.mustang-infra-readback.v1' or value.get('buildAuthorized') is not False \
            or value.get('publicationAuthorized') is not False or value.get('vm') != vm \
            or value.get('diskResource') != disk or value.get('automaticStop') != CONFIRMED_STOP \
            or value.get('runnerLabel') != lane.get('runnerLabel') or value.get('queue') != lane.get('queue') \
            or value.get('workflow') != lane.get('workflow') \
            or any(lane.get(key) != layout.get(key) for key in ('sourceRoot', 'storageRoot', 'lockPath')) \
            or not isinstance(recorded, dict) or recorded.get('resource') != disk or recorded.get('id') != identity.get('id') \
            or identity.get('id') != CONFIRMED_DISK_ID or identity.get('uuid') != CONFIRMED_UUID \
            or identity.get('mount') != CONFIRMED_MOUNT or vm.get('instanceId') != CONFIRMED_INSTANCE_ID \
            or identity.get('availableBytesObserved') != CONFIRMED_FREE_BYTES \
            or identity.get('availableInodesObserved') is not None \
            or not str(lane.get('sourceRoot', '')).startswith(CONFIRMED_MOUNT + '/'):
        raise ValueError
    return value


def validate(lane, root=ROOT):
    """Require actual, separate bindings. This is source validation, not runtime approval."""
    try:
        vm = lane['vm']
        if lane['schema'] != 'roseos.mustang-dedicated-lane.v1' or lane['device'] != 'mustang' \
                or lane['status'] != 'bound':
            raise ValueError
        for field in ('project', 'zone', 'instance', 'instanceId'):
            if not isinstance(vm[field], str) or not vm[field]:
                raise ValueError
        if not re.fullmatch(r'[1-9][0-9]*', vm['instanceId']):
            raise ValueError
        disk = lane['diskResource']
        prefix = f'projects/{vm["project"]}/zones/{vm["zone"]}/disks/'
        if not isinstance(disk, str) or not disk.startswith(prefix) \
                or not re.fullmatch(r'[a-z][a-z0-9-]*', disk[len(prefix):]) \
                or disk == 'projects/protocols-465723/zones/us-west2-a/disks/immortal-os-builder':
            raise ValueError
        for field in ('workflow', 'runnerLabel', 'queue', 'infraEvidence'):
            if not isinstance(lane[field], str) or not lane[field]:
                raise ValueError
        if not re.fullmatch(r'\.github/workflows/[A-Za-z0-9_-]+\.yml', lane['workflow']) \
                or lane['workflow'] in ('.github/workflows/os-release.yml', '.github/workflows/yogi-ota.yml'):
            raise ValueError
        for field in ('storageRoot', 'sourceRoot', 'lockPath'):
            value = lane[field]
            if not isinstance(value, str) or not Path(value).is_absolute() or '..' in Path(value).parts \
                    or value == '/':
                raise ValueError
        for field in ('identityVariable', 'providerVariable'):
            if not re.fullmatch(r'MUSTANG_[A-Z0-9_]+', str(lane['controller'][field])):
                raise ValueError
        capacity = json.loads((Path(root)/'os/release-pipeline/lanes/capacity.json').read_text())
        hosts = capacity['hosts']
        protected = {row['host'] for name, row in capacity['lanes'].items() if name != 'mustang'}
        for name in protected:
            host = hosts[name]
            other = host['vm']
            # A different runner label, disk or queue on that VM is still the same builder.
            if (vm['project'], vm['zone'], vm['instance']) == (
                    other['project'], other['zone'], other['instance']) \
                    or lane['runnerLabel'] == host['runnerLabel'] or lane['queue'] == host['queue'] \
                    or host.get('workflow') == lane['workflow']:
                raise ValueError
        if vm['instanceId'] == FRANKEL_INSTANCE_ID:
            raise ValueError
        yogi = json.loads((Path(root)/'os/ota/yogi.json').read_text())['builder']
        retired = yogi.get('retired') or {}
        disk_id = (lane.get('disk') or {}).get('id')
        if vm['instanceId'] in {yogi.get('instanceId'), retired.get('instanceId')} \
                or (disk_id and disk_id == yogi.get('restoredDiskId')):
            raise ValueError
        if any(str(lane[key]).startswith(prefix) for key in ('sourceRoot', 'storageRoot', 'lockPath')
               for prefix in OTHER_STORAGE):
            raise ValueError
        attached_readback(lane, root)
    except (KeyError, TypeError, ValueError):
        raise ValueError('dedicated Mustang bindings missing or sharing another flavor builder; no lifecycle action allowed') from None
    return lane


def controller_plan(lane, root=ROOT):
    """Only render exact instance-scoped commands; never execute or log in."""
    validate(lane, root)
    vm = lane['vm']
    prefix = ['gcloud', 'compute', 'instances']
    where = ['--project='+vm['project'], '--zone='+vm['zone']]
    plan = {
        'device': 'mustang', 'workflow': lane['workflow'], 'runnerLabel': lane['runnerLabel'],
        'queue': lane['queue'], 'storageRoot': lane['storageRoot'], 'diskResource': lane['diskResource'],
        'lockPath': lane['lockPath'],
        'controller': lane['controller'],
        'get': prefix+['describe', vm['instance']]+where+['--format=json(id,name,status,disks)'],
        'start': prefix+['start', vm['instance']]+where+['--async', '--quiet'],
        'stop': prefix+['stop', vm['instance']]+where+['--async', '--quiet'],
        'ownership': dict(vm),
        'stopPolicy': 'Stop only a verified matching instance started by this operation; unknown ownership refuses.',
        'runtimeAuthorized': False}
    evidence = attached_readback(lane, root)
    if evidence is not None:
        plan['automaticStop'] = dict(evidence['automaticStop'])
        plan['stopPolicy'] += ' The VM maxRunDuration is 14400 seconds and STOPs the instance if cleanup does not.'
    return plan


def owned_start(record, lane, root=ROOT):
    """A STOP decision is valid only for this exact dedicated VM and known start ownership."""
    validate(lane, root)
    if not isinstance(record, dict) or set(record) != {'vm', 'startedByThisOperation'} \
            or record['vm'] != lane['vm'] or type(record['startedByThisOperation']) is not bool:
        raise ValueError('dedicated Mustang ownership unknown or for another VM; STOP refused')
    return record['startedByThisOperation']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('check', 'plan'))
    args = parser.parse_args()
    try:
        lane = validate(configuration())
        print(json.dumps(controller_plan(lane) if args.action == 'plan' else
                         {'status': 'source-bound', 'runtimeAuthorized': False}))
    except (ValueError, OSError) as error:
        print(str(error), file=sys.stderr)
        return 4
    return 0


if __name__ == '__main__':
    sys.exit(main())
