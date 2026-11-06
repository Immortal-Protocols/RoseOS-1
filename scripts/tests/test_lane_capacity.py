"""1A lane capacity map (os/release-pipeline/lanes/capacity.json) against the committed route.

The routing actually committed is derived from os-release.yml (each self-hosted job's runner label and the modes its
if: names, the workflow's concurrency group, the controller's identity variable), release-pipeline.py's BUILDER (the
one VM the controller starts and stops), frankel.json's host lock and the builder-reclaim storage root. The map must
say exactly that. The rules that a lane's move to its own host must satisfy are checked too: its jobs' runner, a queue
keyed by host, a lane-aware controller, owned stops wherever a host's lanes do not share one queue, and the shared app
version ledger. Yogi's own route (yogi-ota.yml and os/ota/yogi.json) is checked against its host and kept apart from the
Frankel host. A host builds only when it is in service. Synthetic splits show each rule refuses an unsafe move. Source
only: no runner, VM or API is touched.
"""
import copy
import importlib.util
import json
from pathlib import Path
import re
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import os_release_shape as shape  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
CONTRACT = json.loads((ROOT/'os/release-pipeline/lanes/capacity.json').read_text())
FRANKEL = json.loads((ROOT/'os/release-pipeline/frankel.json').read_text())
RECLAIM = json.loads((ROOT/'os/release-pipeline/storage/builder-reclaim.json').read_text())
OS_RELEASE = '.github/workflows/os-release.yml'
YOGI = json.loads((ROOT/'os/ota/yogi.json').read_text())
spec = importlib.util.spec_from_file_location('release_pipeline_for_lanes', ROOT/'scripts/release-pipeline.py')
pipeline = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pipeline)
spec = importlib.util.spec_from_file_location('yogi_ota_for_lanes', ROOT/'scripts/yogi_ota.py')
yogi_ota = importlib.util.module_from_spec(spec)
spec.loader.exec_module(yogi_ota)
FIELDS = ['runnerLabel', 'vm', 'storageRoot', 'lockPath', 'queue', 'controller']
RUNS_ON = re.compile(r'(?m)^    runs-on: (.+)$')
JOB_IF = re.compile(r'(?m)^    if: (.+)$')
SELF_HOSTED = re.compile(r'\[self-hosted, ([\w-]+)\]')


def job_condition(block):
    """A job's if:, on one line or as a folded block (if: >-) whose indented lines are joined; '' when it has none."""
    found = JOB_IF.search(block)
    if not found:
        return ''
    if found[1].strip() not in ('>-', '>', '|-', '|'):
        return found[1]
    lines = []
    for line in block[found.end():].split('\n')[1:]:
        if not line.startswith('      '):
            break
        lines.append(line.strip())
    return ' '.join(lines)


def queue_of(workflow):
    """The workflow's concurrency group, which must be one literal (no expression) for this map to read it."""
    head = workflow.split('\njobs:\n', 1)[0]
    block = re.search(r'(?ms)^concurrency:\n(.*?)(?=^\S|\Z)', head)
    assert block, 'no workflow-level concurrency'
    group = re.search(r'(?m)^  group: (\S+)$', block[1])
    assert group, 'no concurrency group'
    return group[1]


def self_hosted_jobs(workflow):
    """{job: (runner label, [modes its if: names])} for every job that runs on a self-hosted runner. A self-hosted
    runs-on in any other shape than [self-hosted, <label>] is refused, so no job's routing is skipped unread."""
    found = {}
    for name, block in shape.jobs(workflow).items():
        runs = RUNS_ON.search(block)
        value = re.sub(r'\s+#.*$', '', runs[1]).strip() if runs else ''
        if 'self-hosted' in value:
            label = SELF_HOSTED.fullmatch(value)
            assert label, f'job {name}: a self-hosted runs-on this map does not read: {runs[1]}'
            found[name] = (label[1], shape.MODE_EQ.findall(job_condition(block)))
    return found


def controller_variable(workflow, name='GCP_CONTROLLER_SERVICE_ACCOUNT'):
    """The repository variables the controller and cleanup jobs take their controller identity from."""
    found = set()
    for job in ('controller', 'cleanup'):
        found |= set(re.findall(name + r': \$\{\{ vars\.(\w+) \}\}', shape.jobs(workflow)[job]))
    return found


def in_service(name, host):
    """The Frankel builder is the operating builder. Another host builds only once its own committed config says its
    runner and controller are ready, as scripts/yogi_ota.py requires (os/ota/yogi.json builder.status == 'ready')."""
    if host.get('config') == 'os/ota/yogi.json':
        return json.loads((ROOT/host['config']).read_text())['builder'].get('status') == 'ready'
    return name == 'frankel-builder'


def lane_of(contract):
    owner = {}
    for lane, entry in contract['lanes'].items():
        for mode in entry['modes']:
            owner.setdefault(mode, []).append(lane)
    return owner


def problems(contract, workflow, builder, lock, storage, owned):
    """Every way the map and the committed os-release route disagree, or a split is unsafe; [] when they agree."""
    found, hosts, lanes, owner = [], contract['hosts'], contract['lanes'], lane_of(contract)
    for mode in shape.modes(workflow):
        if len(owner.get(mode, [])) != 1:
            found.append(f'mode {mode} belongs to {owner.get(mode, [])} lanes, not exactly one')
    for mode in set(owner) - set(shape.modes(workflow)):
        found.append(f'mode {mode} is mapped but os-release has no such mode')
    for job, (label, modes) in sorted(self_hosted_jobs(workflow).items()):
        targets = {lanes[owner[m][0]]['host'] for m in modes if len(owner.get(m, [])) == 1}
        if not modes:
            found.append(f'self-hosted job {job} names no mode, so its lane is unknown')
        elif len(targets) != 1:
            found.append(f'job {job} serves lanes on {sorted(map(str, targets))}; one job runs on one host')
        elif label != hosts[targets.pop()]['runnerLabel']:
            found.append(f'job {job} runs on {label}, not on its lane\'s host runner')
    used = sorted({entry['host'] for entry in lanes.values() if entry['workflow'] == OS_RELEASE})
    queue = queue_of(workflow)
    if len(used) != 1:
        found.append(f'os-release has one literal queue ({queue}) but its lanes are on hosts {used}: key the queue by host')
        found.append(f'the controller starts and stops one VM ({builder["instance"]}): make it lane-aware for hosts {used}')
        unowned = sorted(set(shape.modes(workflow)) - set(owned))
        if unowned:
            found.append(f'lanes on several hosts need owned stops for every mode; plain start/stop remains for {unowned}')
    for host in used:
        entry = hosts[host]
        if entry['queue'] != queue:
            found.append(f'host {host} queue {entry["queue"]} is not the workflow queue {queue}')
        if entry['vm'] != builder:
            found.append(f'host {host} VM {entry["vm"]} is not the VM the controller starts and stops ({builder})')
        if entry['lockPath'] != lock:
            found.append(f'host {host} lock {entry["lockPath"]} is not frankel.json host.lockPath {lock}')
        if entry['storageRoot'] != storage:
            found.append(f'host {host} storage {entry["storageRoot"]} is not the builder-reclaim root {storage}')
        if {entry['controller']['identityVariable']} != controller_variable(workflow):
            found.append(f'host {host} controller identity is not the one the controller and cleanup jobs use')
    ledger = contract['shared']['appVersionLedger']
    allocating = set()
    for name, block in shape.jobs(workflow).items():
        if re.search(r'scripts/release-allocate\.py (latest|app) ', block):
            allocating |= {owner[m][0] for m in shape.MODE_EQ.findall(job_condition(block)) if len(owner.get(m, [])) == 1
                           and m in {'latest-beta', 'app-only'}}
    if allocating != set(ledger['lanes']):
        found.append(f'the lanes that allocate app versions are {sorted(allocating)}, not the ledger lanes {ledger["lanes"]}')
    for lane in ledger['lanes']:
        if lanes[lane]['host'] != ledger['host']:
            found.append(f'lane {lane} allocates app versions but is not on the ledger host {ledger["host"]}')
    return found


def yogi_problems(contract, workflow, config):
    """Every way Yogi's committed route (yogi-ota.yml and os/ota/yogi.json) disagrees with its host in the map, or
    shares anything with the Frankel host; [] when it is its own host."""
    lane = contract['lanes']['yogi']
    own, shared, builder, found = contract['hosts'][lane['host']], contract['hosts']['frankel-builder'], config['builder'], []
    jobs = self_hosted_jobs(workflow)
    if not jobs:
        found.append('yogi-ota has no self-hosted job')
    for job, (label, _) in sorted(jobs.items()):
        if label != own['runnerLabel']:
            found.append(f'yogi job {job} runs on {label}, not on the Yogi runner {own["runnerLabel"]}')
    if queue_of(workflow) != own['queue']:
        found.append(f'yogi-ota queue {queue_of(workflow)} is not the Yogi host queue {own["queue"]}')
    if controller_variable(workflow, 'YOGI_CONTROLLER_SERVICE_ACCOUNT') != {own['controller']['identityVariable']}:
        found.append('yogi-ota controller identity is not the Yogi host controller')
    if own['vm'] != {k: builder[k] for k in ('project', 'zone', 'instance')}:
        found.append('the Yogi host VM is not os/ota/yogi.json builder')
    if (own['runnerLabel'], own['storageRoot'], own['lockPath']) != (builder['runnerLabel'], builder['root'], builder['lock']):
        found.append('the Yogi host runner, disk or lock is not os/ota/yogi.json builder')
    for field in FIELDS:
        if own[field] == shared[field]:
            found.append(f'the Yogi host shares its {field} with the Frankel host')
    return found


def independent_pairs(contract, live=True):
    """Pairs of lanes on known, different hosts with a different runner, VM and queue; with live, only hosts in
    service. Storage and lock paths may be the same strings on two machines: each VM has its own disk."""
    hosts = contract['hosts']
    known = {k: v['host'] for k, v in contract['lanes'].items()
             if v.get('runtimeStatus') != 'retired-shared-host' and v['host']
             and (not live or in_service(v['host'], hosts[v['host']]))}
    pairs = []
    for a in sorted(known):
        for b in sorted(known):
            if a < b:
                x, y = hosts[known[a]], hosts[known[b]]
                if all(x[f] != y[f] for f in ('runnerLabel', 'vm', 'queue')):
                    pairs.append([a, b])
    return pairs


def real():
    return (shape.text(), pipeline.BUILDER, FRANKEL['host']['lockPath'], RECLAIM['root'], shape.owned_modes())


class MapTest(unittest.TestCase):
    def test_the_map_names_every_host_field_and_lane(self):
        self.assertEqual(CONTRACT['schema'], 'roseos.lane-capacity.v1')
        self.assertEqual(CONTRACT['unboundFields'], FIELDS)
        for name, host in CONTRACT['hosts'].items():
            with self.subTest(host=name):
                self.assertEqual(sorted(set(host) - {'evidence', 'config'}), sorted(FIELDS))
                self.assertTrue((ROOT/host['config']).is_file())
                self.assertTrue(all(host[f] for f in FIELDS) and host['evidence'], 'a host is listed only with real values')
                self.assertEqual(sorted(host['vm']), ['instance', 'project', 'zone'])
        for name, lane in CONTRACT['lanes'].items():
            with self.subTest(lane=name):
                self.assertTrue((ROOT/lane['workflow']).is_file())
                self.assertIn(lane['host'], [*CONTRACT['hosts'], None])
                self.assertIn(lane['target'], [*CONTRACT['hosts'], 'own'])
                if lane['target'] == 'own':
                    self.assertEqual(lane['unbound'] if lane['host'] else FIELDS, FIELDS)
                else:
                    self.assertEqual(lane['target'], lane['host'])

    def test_the_map_is_the_committed_route(self):
        self.assertEqual(problems(CONTRACT, *real()), [])

    def test_the_shared_host_values_come_from_the_committed_route(self):
        host = CONTRACT['hosts']['frankel-builder']
        self.assertEqual({label for label, _ in self_hosted_jobs(shape.text()).values()}, {host['runnerLabel']})
        self.assertEqual(host['vm'], pipeline.BUILDER)
        self.assertEqual(host['queue'], queue_of(shape.text()))
        self.assertEqual((host['lockPath'], host['storageRoot']), (FRANKEL['host']['lockPath'], RECLAIM['root']))
        self.assertEqual(controller_variable(shape.text()), {host['controller']['identityVariable']})

    def test_no_two_lanes_can_build_at_once_today(self):
        # Every os-release lane shares one runner, VM, disk, lock, queue and controller. Yogi's route is separate, but
        # its host is not in service until os/ota/yogi.json says builder.status ready.
        self.assertEqual(YOGI['builder']['status'], 'runner-unregistered')
        self.assertFalse(in_service('yogi-builder', CONTRACT['hosts']['yogi-builder']))
        # The same readiness rule Yogi's own guard applies: its blockers name the builder until builder.status is ready.
        def builder_blocked(status):
            cfg = copy.deepcopy(YOGI)
            cfg['builder']['status'] = status
            return any(b.startswith('builder runner/controller not ready') for b in yogi_ota.blockers(cfg, 'ota'))
        self.assertTrue(builder_blocked('runner-unregistered'))
        self.assertFalse(builder_blocked('ready'))
        self.assertEqual(independent_pairs(CONTRACT), CONTRACT['parallelToday'])
        self.assertEqual(CONTRACT['parallelToday'], [])
        self.assertEqual(independent_pairs(CONTRACT, live=False), CONTRACT['parallelByRoute'])
        self.assertEqual(sorted(k for k, v in CONTRACT['lanes'].items() if v['target'] == 'own'),
                         ['agent', 'harness', 'mustang'])

    def test_the_version_ledger_is_shared_by_frankel_and_agent(self):
        ledger = CONTRACT['shared']['appVersionLedger']
        self.assertEqual((ledger['host'], ledger['lanes']), ('frankel-builder', ['frankel', 'agent']))
        packages = [p['packageName'] for p in json.loads((ROOT/'os/latest-beta/allocation.json').read_text())['packages']]
        self.assertIn('dev.pixel.voice', packages)  # latest-beta allocates the app app-only also allocates
        allocator = (ROOT/'scripts/release-allocate.py').read_text()
        self.assertIn("APP_PACKAGE = 'dev.pixel.voice'", allocator)
        self.assertIn("APP_RESERVATIONS = 'app-allocations'", allocator)

    def test_retired_mustang_jobs_are_not_executable_capacity(self):
        lane = CONTRACT['lanes']['mustang']
        self.assertEqual(lane['runtimeStatus'], 'retired-shared-host')
        binding = json.loads((ROOT/lane['activeBinding']).read_text())
        self.assertEqual(binding['status'], 'unbound')
        for live in (True, False):
            self.assertFalse(any('mustang' in pair for pair in independent_pairs(CONTRACT, live)))
        # A historical runner/VM label must not manufacture a parallel lane.
        mislabeled = copy.deepcopy(CONTRACT)
        mislabeled['lanes']['mustang'].pop('runtimeStatus')
        self.assertIn(['mustang','yogi'], independent_pairs(mislabeled, live=False))

    def test_folded_job_conditions_are_read_whole(self):
        jobs = self_hosted_jobs(shape.text())
        self.assertEqual(jobs['mustang-signer'], ('roseos-frankel-builder', ['mustang-delivery', 'mustang-ota']))
        self.assertEqual(jobs['mustang-packager'], ('roseos-frankel-builder', ['mustang-delivery']))
        self.assertEqual(job_condition('    if: a == b\n    runs-on: x\n'), 'a == b')
        self.assertEqual(job_condition('    if: >-\n      x &&\n      y\n    runs-on: z\n'), 'x && y')
        self.assertEqual(job_condition('    runs-on: z\n'), '')

    def test_yogi_route_is_its_own_host(self):
        # Every Yogi stage on its runner, its own queue, controller, VM, disk and lock; nothing of the Frankel host.
        workflow = (ROOT/CONTRACT['lanes']['yogi']['workflow']).read_text()
        self.assertEqual(yogi_problems(CONTRACT, workflow, YOGI), [])
        self.assertTrue({'build', 'sign'} <= set(self_hosted_jobs(workflow)))  # more stages may add jobs on the runner

    def test_a_yogi_route_that_leans_on_the_frankel_host_is_refused(self):
        workflow = (ROOT/CONTRACT['lanes']['yogi']['workflow']).read_text()
        # Whichever Yogi job comes first moves to the Frankel runner; exactly that one is refused.
        label = CONTRACT['hosts']['yogi-builder']['runnerLabel']
        moved = workflow.replace(f'runs-on: [self-hosted, {label}]', 'runs-on: [self-hosted, roseos-frankel-builder]', 1)
        refused = [p for p in yogi_problems(CONTRACT, moved, YOGI)
                   if p.endswith(f'runs on roseos-frankel-builder, not on the Yogi runner {label}')]
        self.assertEqual(len(refused), 1, refused)
        queued = workflow.replace('  group: yogi-ota\n', '  group: os-release-frankel\n', 1)
        self.assertIn('yogi-ota queue os-release-frankel is not the Yogi host queue yogi-ota', yogi_problems(CONTRACT, queued, YOGI))
        controlled = workflow.replace('vars.YOGI_CONTROLLER_SERVICE_ACCOUNT', 'vars.GCP_CONTROLLER_SERVICE_ACCOUNT')
        self.assertIn('yogi-ota controller identity is not the Yogi host controller', yogi_problems(CONTRACT, controlled, YOGI))
        config = copy.deepcopy(YOGI)
        config['builder']['instance'] = 'immortal-os-builder'
        self.assertIn('the Yogi host VM is not os/ota/yogi.json builder', yogi_problems(CONTRACT, workflow, config))
        config = copy.deepcopy(YOGI)
        config['builder']['lock'] = '/home/pixelbuild/frankel-build.lock'
        self.assertIn('the Yogi host runner, disk or lock is not os/ota/yogi.json builder', yogi_problems(CONTRACT, workflow, config))
        shared = copy.deepcopy(CONTRACT)
        shared['hosts']['yogi-builder']['queue'] = 'os-release-frankel'
        self.assertIn('the Yogi host shares its queue with the Frankel host', yogi_problems(shared, queued, YOGI))
        self.assertEqual(yogi_problems(CONTRACT, workflow.replace(f'[self-hosted, {label}]', 'ubuntu-24.04'), YOGI),
                         ['yogi-ota has no self-hosted job'])


class SplitTest(unittest.TestCase):
    """An unsafe move of a lane to its own host is refused until every coupled place moves with it."""

    def moved(self, lane='mustang'):
        contract = copy.deepcopy(CONTRACT)
        contract['hosts']['own-host'] = {'runnerLabel': 'roseos-own-builder', 'storageRoot': '/srv/own/rose-actions',
            'vm': {'project': 'p', 'zone': 'z', 'instance': 'own-builder'}, 'lockPath': '/srv/own/build.lock',
            'queue': 'os-release-own', 'controller': {'identityVariable': 'GCP_OWN_CONTROLLER'}, 'evidence': 'synthetic'}
        contract['lanes'][lane].update(host='own-host', target='own-host')
        # A hypothetical completed migration replaces retirement; merely adding
        # a label or deleting this status cannot reactivate the legacy CLI.
        contract['lanes'][lane].pop('runtimeStatus', None)
        return contract

    def test_moving_a_lane_in_the_map_alone_is_refused(self):
        found = problems(self.moved(), *real())
        for expected in ('job mustang-build runs on roseos-frankel-builder, not on its lane\'s host runner',
                         'key the queue by host', 'make it lane-aware', 'need owned stops for every mode'):
            self.assertTrue(any(expected in p for p in found), (expected, found))

    def test_moving_the_jobs_still_needs_the_queue_and_controller(self):
        workflow, builder, lock, storage, owned = real()
        moved = re.sub(r'(?m)^(  mustang-[\w-]+:\n(?:    .*\n)*?    runs-on: )\[self-hosted, roseos-frankel-builder\]',
                       r'\1[self-hosted, roseos-own-builder]', workflow)
        self.assertNotEqual(moved, workflow)
        found = problems(self.moved(), moved, builder, lock, storage, owned)
        self.assertFalse(any('not on its lane\'s host runner' in p for p in found), found)
        self.assertTrue(any('key the queue by host' in p for p in found))
        self.assertTrue(any('make it lane-aware' in p for p in found))

    def test_agent_cannot_leave_the_version_ledger_host(self):
        found = problems(self.moved('agent'), *real())
        self.assertIn('lane agent allocates app versions but is not on the ledger host frankel-builder', found)

    def test_a_split_host_pair_counts_as_parallel(self):
        pairs = independent_pairs(self.moved(), live=False)
        self.assertEqual([p for p in pairs if 'mustang' in p], [['agent', 'mustang'], ['frankel', 'mustang'],
            ['frankel-host-other', 'mustang'], ['harness', 'mustang'], ['mustang', 'yogi']])
        self.assertEqual(independent_pairs(self.moved()), [])  # a host not in service never builds in parallel
        # A second machine still on the shared queue (or runner label) does not build in parallel.
        for field, value in (('queue', 'os-release-frankel'), ('runnerLabel', 'roseos-frankel-builder')):
            with self.subTest(shared=field):
                contract = self.moved()
                contract['hosts']['own-host'][field] = value
                self.assertEqual([p for p in independent_pairs(contract, live=False) if 'mustang' in p], [['mustang', 'yogi']])

    def test_a_self_hosted_job_must_name_its_modes_in_a_shape_this_map_reads(self):
        workflow, builder, lock, storage, owned = real()
        extra = workflow.replace('\n  cleanup:\n', '\n  stray:\n    runs-on: [self-hosted, roseos-frankel-builder]\n    steps: []\n\n  cleanup:\n', 1)
        self.assertNotEqual(extra, workflow)
        self.assertIn('self-hosted job stray names no mode, so its lane is unknown', problems(CONTRACT, extra, builder, lock, storage, owned))
        odd = workflow.replace('runs-on: [self-hosted, roseos-frankel-builder]', 'runs-on: [self-hosted, roseos-frankel-builder, gpu]', 1)
        with self.assertRaisesRegex(AssertionError, 'a self-hosted runs-on this map does not read'):
            problems(CONTRACT, odd, builder, lock, storage, owned)
        commented = workflow.replace('runs-on: [self-hosted, roseos-frankel-builder]\n', 'runs-on: [self-hosted, roseos-frankel-builder]  # the shared host\n', 1)
        self.assertNotEqual(commented, workflow)
        self.assertEqual(problems(CONTRACT, commented, builder, lock, storage, owned), [])

    def test_every_mode_needs_exactly_one_lane(self):
        contract = copy.deepcopy(CONTRACT)
        contract['lanes']['frankel']['modes'].remove('app-reuse')
        contract['lanes']['agent']['modes'].append('latest-beta')
        contract['lanes']['harness']['modes'].append('no-such-mode')
        found = problems(contract, *real())
        self.assertIn('mode no-such-mode is mapped but os-release has no such mode', found)
        self.assertIn('mode app-reuse belongs to [] lanes, not exactly one', found)
        self.assertIn("mode latest-beta belongs to ['frankel', 'agent'] lanes, not exactly one", found)
        self.assertTrue(any('the lanes that allocate app versions are' in p for p in found))

    def test_a_changed_shared_host_value_is_refused(self):
        workflow, builder, lock, storage, owned = real()
        for change, expected in ((dict(builder={**builder, 'instance': 'other'}), 'is not the VM the controller starts'),
                                 (dict(lock='/tmp/other.lock'), 'is not frankel.json host.lockPath'),
                                 (dict(storage='/tmp/other'), 'is not the builder-reclaim root'),
                                 (dict(workflow=workflow.replace('group: os-release-frankel\n', 'group: other\n', 1)),
                                  'is not the workflow queue'),
                                 (dict(workflow=workflow.replace('vars.GCP_CONTROLLER_SERVICE_ACCOUNT', 'vars.OTHER_CONTROLLER')),
                                  'controller identity is not the one the controller and cleanup jobs use')):
            with self.subTest(expected=expected):
                args = dict(workflow=workflow, builder=builder, lock=lock, storage=storage, owned=owned, **{})
                args.update(change)
                found = problems(CONTRACT, **args)
                self.assertTrue(any(expected in p for p in found), found)


if __name__ == '__main__':
    unittest.main()
