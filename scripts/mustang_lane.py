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
RETIRED = 'Mustang legacy route retired: Frankel builder is forbidden; dedicated Mustang lifecycle is not activated'


def reject_legacy():
    # No environment flag or future profile binding can restore the old route.
    raise ValueError(RETIRED)


def configuration():
    return json.loads(CONFIG.read_text())


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
        if vm['instanceId'] == '685707771105568194':
            raise ValueError
    except (KeyError, TypeError, ValueError):
        raise ValueError('dedicated Mustang bindings missing or sharing another flavor builder; no lifecycle action allowed') from None
    return lane


def controller_plan(lane, root=ROOT):
    """Only render exact instance-scoped commands; never execute or log in."""
    validate(lane, root)
    vm = lane['vm']
    prefix = ['gcloud', 'compute', 'instances']
    where = ['--project='+vm['project'], '--zone='+vm['zone']]
    return {
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
