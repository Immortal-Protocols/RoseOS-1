"""1A lane capacity map (os/release-pipeline/lanes/capacity.json) against the committed route.

The routing actually committed is derived from os-release.yml (each self-hosted job's runner label and the modes its
if: names, the workflow's concurrency group for each mode, the controller's identity variable), release-pipeline.py's
BUILDER (the one VM the controller starts and stops), frankel.json's host lock and the builder-reclaim storage root. The
map must say exactly that. The rules that a lane's move to its own host must satisfy are checked too: its jobs' runner, a
queue keyed by host, a lane-aware controller, owned stops wherever a host's lanes do not share one queue, and the shared
app version ledger. A GitHub-hosted lane (agent-hosted, 1F) has no host: its modes need their own queue, GitHub-hosted
jobs only, a controller and cleanup that never run for them, and the shared ledger store as the only thing it shares
with a builder lane; it counts as capacity only once it is active. Yogi's own route (yogi-ota.yml and os/ota/yogi.json)
is checked against its host and kept apart from the Frankel host. The standalone harness has its own manual workflow
(launcher3-compile.yml). Its compile binding can be source-bound without claiming release or OTA capacity. A host
builds only when it is in service. Synthetic splits show each rule refuses an unsafe move. Source only: no runner, VM
or API is touched.
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
HOSTED = 'github-hosted'  # a lane target with no host: GitHub-hosted runners, its own queue, nothing else to bind
# The commands that allocate dev.pixel.voice versions, and the modes for which each one allocates.
ALLOCATORS = ((re.compile(r'scripts/release-allocate\.py (latest|app) '), {'latest-beta', 'app-only'}),
              (re.compile(r'scripts/agent-hosted\.py reserve '), {'agent-hosted'}))
BUILDER_LEDGER = ALLOCATORS[0][0]
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


def queue_of(workflow, mode=None):
    """The concurrency group a run of MODE joins (with no mode, the workflow's default): one literal group, or
    os-release's mode-keyed one, read by os_release_shape.queues, which refuses any other expression."""
    return shape.queue(mode, workflow)


def runs_on(block):
    runs = RUNS_ON.search(block)
    return re.sub(r'\s+#.*$', '', runs[1]).strip() if runs else ''


def self_hosted_jobs(workflow):
    """{job: (runner label, [modes its if: names])} for every job that runs on a self-hosted runner. A self-hosted
    runs-on in any other shape than [self-hosted, <label>] is refused, so no job's routing is skipped unread."""
    found = {}
    for name, block in shape.jobs(workflow).items():
        value = runs_on(block)
        if 'self-hosted' in value:
            label = SELF_HOSTED.fullmatch(value)
            assert label, f'job {name}: a self-hosted runs-on this map does not read: {value}'
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
    all_jobs = shape.jobs(workflow)
    for mode in shape.modes(workflow):
        if len(owner.get(mode, [])) != 1:
            found.append(f'mode {mode} belongs to {owner.get(mode, [])} lanes, not exactly one')
    for mode in set(owner) - set(shape.modes(workflow)):
        found.append(f'mode {mode} is mapped but os-release has no such mode')
    for job, (label, modes) in sorted(self_hosted_jobs(workflow).items()):
        targets = {lanes[owner[m][0]]['host'] for m in modes if len(owner.get(m, [])) == 1}
        if not modes:
            found.append(f'self-hosted job {job} names no mode, so its lane is unknown')
        elif None in targets:
            found.append(f'self-hosted job {job} serves a GitHub-hosted lane\'s mode; that lane has no self-hosted runner')
        elif len(targets) != 1:
            found.append(f'job {job} serves lanes on {sorted(map(str, targets))}; one job runs on one host')
        elif label != hosts[targets.pop()]['runnerLabel']:
            found.append(f'job {job} runs on {label}, not on its lane\'s host runner')
    os_lanes = {name: entry for name, entry in lanes.items() if entry['workflow'] == OS_RELEASE}
    used = sorted({entry['host'] for entry in os_lanes.values() if entry['host']})
    hosted = {name: entry for name, entry in os_lanes.items() if entry['target'] == HOSTED}
    hosted_modes = {m for entry in hosted.values() for m in entry['modes']}
    default, keyed = shape.queues(workflow)
    if len(used) != 1:
        found.append(f'the controller starts and stops one VM ({builder["instance"]}): make it lane-aware for hosts {used}')
        unowned = sorted(set(shape.modes(workflow)) - set(owned) - hosted_modes)
        if unowned:
            found.append(f'lanes on several hosts need owned stops for every mode; plain start/stop remains for {unowned}')
    for host in used:
        entry = hosts[host]
        served = sorted({keyed.get(m, default) for lane in os_lanes.values() if lane['host'] == host for m in lane['modes']})
        if served != [entry['queue']]:
            found.append(f'host {host} queue {entry["queue"]} is not the workflow queue {", ".join(served)} of its '
                         'lanes\' modes: key the queue by host')
        if entry['vm'] != builder:
            found.append(f'host {host} VM {entry["vm"]} is not the VM the controller starts and stops ({builder})')
        if entry['lockPath'] != lock:
            found.append(f'host {host} lock {entry["lockPath"]} is not frankel.json host.lockPath {lock}')
        if entry['storageRoot'] != storage:
            found.append(f'host {host} storage {entry["storageRoot"]} is not the builder-reclaim root {storage}')
        if {entry['controller']['identityVariable']} != controller_variable(workflow):
            found.append(f'host {host} controller identity is not the one the controller and cleanup jobs use')
    for name, entry in sorted(os_lanes.items()):
        if entry['host'] is None and entry['target'] != HOSTED:
            found.append(f'lane {name} has no host and is not a GitHub-hosted lane')
    for name, entry in sorted(hosted.items()):
        modes = set(entry['modes'])
        if entry['host'] is not None:
            found.append(f'GitHub-hosted lane {name} names host {entry["host"]}')
        served = sorted({keyed.get(m, default) for m in modes})
        if served != [entry['queue']]:
            found.append(f'GitHub-hosted lane {name} queue {entry["queue"]} is not the workflow queue '
                         f'{", ".join(served)} of its modes: key the group by mode')
        if entry['queue'] == default or entry['queue'] in {h['queue'] for h in hosts.values()}:
            found.append(f'GitHub-hosted lane {name} shares its queue {entry["queue"]} with a builder')
        for job, block in sorted(all_jobs.items()):
            named = set(shape.MODE_EQ.findall(job_condition(block)))
            if not named & modes:
                continue
            if runs_on(block) != entry['runner']:
                found.append(f'job {job} of GitHub-hosted lane {name} runs on {runs_on(block)}, not {entry["runner"]}')
            if named - modes:
                found.append(f'job {job} serves GitHub-hosted lane {name} and another lane; one job runs on one route')
            if BUILDER_LEDGER.search(block):
                found.append(f'job {job} of GitHub-hosted lane {name} allocates with the builder-local ledger')
    # A hosted run never starts the builder, so its cleanup never stops one: another queue's run may be using it.
    controller = job_condition(all_jobs['controller'])
    excluded = set(shape.MODE_NE.findall(controller)) if '||' not in controller else set()
    for mode in sorted(hosted_modes - excluded):
        found.append(f'the controller can start the builder for GitHub-hosted mode {mode}, and its cleanup would stop '
                     'a builder that a run in another queue is using')
    if hosted_modes and (job_condition(all_jobs['cleanup']) != "always() && needs.controller.result != 'skipped'"
                         or 'controller' not in shape.cleanup_needs(workflow)):
        found.append('cleanup no longer follows the controller, so a GitHub-hosted run could stop the builder')
    ledger = contract['shared']['appVersionLedger']
    allocating = set()
    for name, block in all_jobs.items():
        for command, allocates in ALLOCATORS:
            if command.search(block):
                allocating |= {owner[m][0] for m in shape.MODE_EQ.findall(job_condition(block))
                               if len(owner.get(m, [])) == 1 and m in allocates}
    if allocating != set(ledger['lanes']):
        found.append(f'the lanes that allocate app versions are {sorted(allocating)}, not the ledger lanes {ledger["lanes"]}')
    shared_store = set(ledger.get('hostedLanes', []))
    for lane in ledger['lanes']:
        if lane in shared_store:
            if lanes[lane]['host'] is not None or lanes[lane]['target'] != HOSTED:
                found.append(f'lane {lane} allocates through the shared store alone but is not a GitHub-hosted lane')
        elif lanes[lane]['host'] != ledger['host']:
            found.append(f'lane {lane} allocates app versions but is not on the ledger host {ledger["host"]}')
    for lane in sorted(shared_store - set(ledger['lanes'])):
        found.append(f'hosted ledger lane {lane} is not a ledger lane')
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


def route(contract, name):
    """What a lane builds on: its host's runner label, VM and queue, or a GitHub-hosted lane's runner label and own queue
    with no VM of its own (GitHub gives each job a fresh machine)."""
    lane = contract['lanes'][name]
    if lane['host'] is None:
        return {'runnerLabel': lane['runner'], 'vm': None, 'queue': lane['queue']}
    return contract['hosts'][lane['host']]


def lane_in_service(contract, name):
    """A host lane builds once its host is in service; a GitHub-hosted lane once its committed runtimeStatus is active."""
    lane = contract['lanes'][name]
    if lane['host'] is None:
        return lane.get('runtimeStatus') == 'active'
    return in_service(lane['host'], contract['hosts'][lane['host']])


def independent_pairs(contract, live=True):
    """Pairs of lanes on known, different routes with a different runner, VM and queue; with live, only lanes in
    service. Storage and lock paths may be the same strings on two machines: each VM has its own disk."""
    known = [k for k, v in contract['lanes'].items()
             if v.get('runtimeStatus') != 'retired-shared-host' and (v['host'] or v['target'] == HOSTED)
             and (not live or lane_in_service(contract, k))]
    pairs = []
    for a in sorted(known):
        for b in sorted(known):
            if a < b:
                x, y = route(contract, a), route(contract, b)
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
                self.assertIn(lane['target'], [*CONTRACT['hosts'], 'own', HOSTED])
                if lane['target'] == 'own':
                    self.assertEqual(lane['unbound'] if lane['host'] else FIELDS, FIELDS)
                elif lane['target'] == HOSTED:
                    # Nothing to bind: no VM, storage, lock or controller; only GitHub-hosted runners and its own queue.
                    self.assertIsNone(lane['host'])
                    self.assertNotIn('unbound', lane)
                    self.assertEqual(lane['runner'], 'ubuntu-24.04')
                    self.assertIn(lane['runtimeStatus'], ('inactive', 'active'))
                else:
                    self.assertEqual(lane['target'], lane['host'])

    def test_the_map_is_the_committed_route(self):
        self.assertEqual(problems(CONTRACT, *real()), [])

    def test_the_shared_host_values_come_from_the_committed_route(self):
        host = CONTRACT['hosts']['frankel-builder']
        self.assertEqual({label for label, _ in self_hosted_jobs(shape.text()).values()}, {host['runnerLabel']})
        self.assertEqual(host['vm'], pipeline.BUILDER)
        self.assertEqual(host['queue'], queue_of(shape.text()))
        for name, lane in CONTRACT['lanes'].items():
            if lane['host'] == 'frankel-builder' and lane['workflow'] == OS_RELEASE:
                for mode in lane['modes']:
                    self.assertEqual(queue_of(shape.text(), mode), host['queue'], (name, mode))
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

    def test_the_version_ledger_is_shared_by_frankel_and_both_agent_routes(self):
        ledger = CONTRACT['shared']['appVersionLedger']
        self.assertEqual((ledger['host'], ledger['lanes'], ledger['hostedLanes']),
                         ('frankel-builder', ['frankel', 'agent', 'agent-hosted'], ['agent-hosted']))
        packages = [p['packageName'] for p in json.loads((ROOT/'os/latest-beta/allocation.json').read_text())['packages']]
        self.assertIn('dev.pixel.voice', packages)  # latest-beta allocates the app app-only also allocates
        allocator = (ROOT/'scripts/release-allocate.py').read_text()
        self.assertIn("APP_PACKAGE = 'dev.pixel.voice'", allocator)
        self.assertIn("APP_RESERVATIONS = 'app-allocations'", allocator)

    def test_retired_mustang_jobs_are_not_executable_capacity(self):
        lane = CONTRACT['lanes']['mustang']
        self.assertEqual(lane['runtimeStatus'], 'retired-shared-host')
        binding = json.loads((ROOT/lane['activeBinding']).read_text())
        runtime = json.loads((ROOT/'os/build-only/mustang-runtime.json').read_text())
        self.assertEqual(binding['status'], 'bound')
        self.assertEqual(binding['vm']['instance'], 'roseos-mustang-builder-20261107-a')
        self.assertNotEqual(binding['vm']['instance'], 'immortal-os-builder')
        self.assertEqual(runtime['status'], 'unbound')
        self.assertFalse(runtime['publication']['enabled'])
        self.assertEqual(runtime['appInputsRecord'], 'os/app-inputs/frankel-2026110704.json')
        self.assertIsNone(runtime['baselineTargetFiles'])
        self.assertTrue(json.loads((ROOT/runtime['allocationState']).read_text())['unknown'])
        self.assertEqual(lane['dedicatedRuntimeStatus'], 'unbound')
        self.assertFalse(in_service('mustang-builder', CONTRACT['hosts']['mustang-builder']))
        for live in (True, False):
            self.assertFalse(any('mustang' in pair for pair in independent_pairs(CONTRACT, live)))
        # A historical runner/VM label must not manufacture a parallel lane.
        mislabeled = copy.deepcopy(CONTRACT)
        mislabeled['lanes']['mustang'].pop('runtimeStatus')
        self.assertIn(['mustang','yogi'], independent_pairs(mislabeled, live=False))

    def test_the_harness_source_binding_claims_no_release_or_ota_capacity(self):
        lane = CONTRACT['lanes']['harness']
        self.assertEqual((lane['workflow'], lane['modes'], lane['runtimeStatus']),
                         ('.github/workflows/launcher3-compile.yml', [], 'retired-shared-host'))
        binding = json.loads((ROOT/lane['activeBinding']).read_text())
        self.assertEqual((binding['lane']['status'], binding['lane']['workflow'], binding['branch']),
                         ('bound', lane['workflow'], 'verifyIntegrated'))
        self.assertNotEqual(binding['lane']['queue'], CONTRACT['hosts']['frankel-builder']['queue'])
        self.assertNotIn('launcher3-compile', shape.modes())  # the os-release.yml mode is removed
        for live in (True, False):
            self.assertFalse(any('harness' in pair for pair in independent_pairs(CONTRACT, live)))
        workflow = (ROOT/lane['workflow']).read_text()
        # Its own queue, which no host of the map uses; no static self-hosted runner, so the worker goes only to the
        # bound runner label the hosted guard derives from the binding; its own controller identity.
        self.assertEqual(queue_of(workflow), 'launcher3-compile')
        self.assertNotIn(queue_of(workflow), [h['queue'] for h in CONTRACT['hosts'].values()])
        self.assertEqual(self_hosted_jobs(workflow), {})
        self.assertIn('    runs-on: ${{ fromJSON(needs.guard.outputs.runs_on) }}\n', shape.jobs(workflow)['worker'])
        self.assertEqual(controller_variable(workflow, 'LAUNCHER3_CONTROLLER_SERVICE_ACCOUNT'),
                         {'LAUNCHER3_CONTROLLER_SERVICE_ACCOUNT'})
        self.assertEqual(controller_variable(workflow), set())
        # Only the retirement status keeps its historical Frankel host from reading as parallel capacity.
        mislabeled = copy.deepcopy(CONTRACT)
        mislabeled['lanes']['harness'].pop('runtimeStatus')
        self.assertIn(['harness', 'yogi'], independent_pairs(mislabeled, live=False))

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


class HostedLaneTest(unittest.TestCase):
    """1F: the hosted Agent modes run in their own queue on GitHub-hosted runners and share only the version ledger."""
    LANE = CONTRACT['lanes']['agent-hosted']

    def test_only_the_hosted_agent_modes_leave_the_builders_queue(self):
        builder = CONTRACT['hosts']['frankel-builder']['queue']
        self.assertEqual(self.LANE['modes'], ['agent-hosted', 'agent-hosted-probe'])
        self.assertEqual(shape.queues(), (builder, {mode: self.LANE['queue'] for mode in self.LANE['modes']}))
        for mode in shape.modes():  # Frankel's, the builder's app-only and Mustang's retired jobs all stay
            with self.subTest(mode=mode):
                self.assertEqual(queue_of(shape.text(), mode), self.LANE['queue'] if mode in self.LANE['modes'] else builder)
        settings = shape.concurrency()
        self.assertEqual((settings['cancel-in-progress'], settings['queue']), ('false', 'max'))  # both queues keep order

    def test_a_hosted_run_never_starts_or_stops_the_builder(self):
        jobs, hosted = shape.jobs(), set(self.LANE['modes'])
        reached = sorted(name for name, block in jobs.items() if hosted & set(shape.MODE_EQ.findall(job_condition(block))))
        self.assertEqual(reached, ['agent-build', 'agent-publisher', 'agent-reserve', 'agent-signer'])
        for name in reached:
            self.assertEqual(runs_on(jobs[name]), self.LANE['runner'], name)
            self.assertNotRegex(jobs[name], r'(?m)^    needs: .*\bcontroller\b', name)
        self.assertFalse(any(hosted & set(modes) for _, modes in self_hosted_jobs(shape.text()).values()))
        # The controller (the only job that starts the builder) never runs for them, and cleanup (the only job that stops
        # it) runs only after a controller that ran: no hosted run can stop a builder another queue's run is using.
        terms = job_condition(jobs['controller']).split(' && ')
        self.assertNotIn('||', job_condition(jobs['controller']))
        self.assertLessEqual({f"inputs.mode != '{mode}'" for mode in hosted}, set(terms))
        self.assertEqual(job_condition(jobs['cleanup']), "always() && needs.controller.result != 'skipped'")
        self.assertIn('controller', shape.cleanup_needs())
        starting = sorted(name for name, block in jobs.items() if shape.START in block or shape.STOP in block)
        self.assertEqual(starting, ['cleanup', 'controller'])

    def test_it_shares_only_the_version_ledger(self):
        queue = self.LANE['queue']
        self.assertNotIn(queue, [host['queue'] for host in CONTRACT['hosts'].values()])
        for path in sorted((ROOT/'.github/workflows').glob('*.yml')):
            if path.name != 'os-release.yml' and '\nconcurrency:\n' in path.read_text():
                with self.subTest(workflow=path.name):
                    self.assertNotIn('agent-hosted', shape.concurrency(path.read_text())['group'])
        store = json.loads((ROOT/'os/release-pipeline/agent/hosted.json').read_text())['reservation']['store']
        self.assertEqual((store['kind'], store['repository']), ('github-annotated-tags', 'Immortal-Protocols/pixel-ota'))
        self.assertEqual(CONTRACT['shared']['appVersionLedger']['hostedLanes'], ['agent-hosted'])
        reserve = shape.jobs()['agent-reserve']
        self.assertIn('python3 scripts/agent-hosted.py reserve ', reserve)
        self.assertNotRegex(reserve, BUILDER_LEDGER)

    def test_it_claims_no_capacity_until_its_committed_status_is_active(self):
        self.assertEqual(self.LANE['runtimeStatus'], 'inactive')
        self.assertFalse(any('agent-hosted' in pair for pair in independent_pairs(CONTRACT)))
        self.assertEqual([p for p in CONTRACT['parallelByRoute'] if 'agent-hosted' in p],
                         [['agent', 'agent-hosted'], ['agent-hosted', 'frankel'], ['agent-hosted', 'frankel-host-other'],
                          ['agent-hosted', 'yogi']])
        active = copy.deepcopy(CONTRACT)
        active['lanes']['agent-hosted']['runtimeStatus'] = 'active'
        self.assertEqual(independent_pairs(active), [['agent', 'agent-hosted'], ['agent-hosted', 'frankel'],
                                                     ['agent-hosted', 'frankel-host-other']])  # Yogi is not in service


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
        self.assertEqual([p for p in pairs if 'mustang' in p], [['agent', 'mustang'], ['agent-hosted', 'mustang'],
            ['frankel', 'mustang'], ['frankel-host-other', 'mustang'], ['mustang', 'yogi']])
        self.assertEqual(independent_pairs(self.moved()), [])  # a host not in service never builds in parallel
        # A second machine still on the shared queue (or runner label) does not build in parallel.
        for field, value in (('queue', 'os-release-frankel'), ('runnerLabel', 'roseos-frankel-builder')):
            with self.subTest(shared=field):
                contract = self.moved()
                contract['hosts']['own-host'][field] = value
                self.assertEqual([p for p in independent_pairs(contract, live=False) if 'mustang' in p],
                                 [['agent-hosted', 'mustang'], ['mustang', 'yogi']])

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
                                 (dict(workflow=workflow.replace("|| 'os-release-frankel' }}\n", "|| 'other' }}\n", 1)),
                                  'host frankel-builder queue os-release-frankel is not the workflow queue other'),
                                 (dict(workflow=workflow.replace('vars.GCP_CONTROLLER_SERVICE_ACCOUNT', 'vars.OTHER_CONTROLLER')),
                                  'controller identity is not the one the controller and cleanup jobs use')):
            with self.subTest(expected=expected):
                args = dict(workflow=workflow, builder=builder, lock=lock, storage=storage, owned=owned, **{})
                args.update(change)
                self.assertNotEqual(args, dict(workflow=workflow, builder=builder, lock=lock, storage=storage, owned=owned))
                found = problems(CONTRACT, **args)
                self.assertTrue(any(expected in p for p in found), found)

    def test_a_hosted_route_that_leans_on_the_builder_is_refused(self):
        workflow, builder, lock, storage, owned = real()
        group = ("  group: ${{ (inputs.mode == 'agent-hosted' || inputs.mode == 'agent-hosted-probe') && "
                 "'os-release-agent-hosted' || 'os-release-frankel' }}\n")
        controller = ("    if: needs.guard.outputs.mustang_skip != 'true' && inputs.mode != 'agent-hosted' && "
                      "inputs.mode != 'agent-hosted-probe'\n")
        cleanup = "    if: always() && needs.controller.result != 'skipped'\n"
        for text in (group, controller, cleanup):
            self.assertEqual(workflow.count(text), 1, text)
        lane = 'GitHub-hosted lane agent-hosted'
        for name, changed, expected in (
                ('one literal queue again', workflow.replace(group, '  group: os-release-frankel\n'),
                 f'{lane} queue os-release-agent-hosted is not the workflow queue os-release-frankel of its modes'),
                ('the probe left in the builder queue', workflow.replace(group, group.replace(" || inputs.mode == 'agent-hosted-probe'", '')),
                 f'{lane} queue os-release-agent-hosted is not the workflow queue os-release-agent-hosted, os-release-frankel'),
                ('a hosted job on the builder', re.sub(r'(?m)^(  agent-build:\n(?:    .*\n)*?    runs-on: )ubuntu-24.04$',
                                                       r'\1[self-hosted, roseos-frankel-builder]', workflow),
                 "self-hosted job agent-build serves a GitHub-hosted lane's mode"),
                ('the controller runs for the probe', workflow.replace(controller, controller.replace(" && inputs.mode != 'agent-hosted-probe'", '')),
                 'the controller can start the builder for GitHub-hosted mode agent-hosted-probe'),
                ('an or in the controller', workflow.replace(controller, controller.replace(' && inputs.mode != ', ' || inputs.mode != ', 1)),
                 'the controller can start the builder for GitHub-hosted mode agent-hosted'),
                ('cleanup without the controller', workflow.replace(cleanup, '    if: always()\n'),
                 'cleanup no longer follows the controller')):
            with self.subTest(name):
                self.assertNotEqual(changed, workflow)
                self.assertIn(expected, ' | '.join(problems(CONTRACT, changed, builder, lock, storage, owned)))

    def test_a_hosted_lane_in_the_map_must_stay_hosted_and_alone(self):
        for name, change, expected in (
                ('on a host', lambda c: c['lanes']['agent-hosted'].update(host='frankel-builder'),
                 'GitHub-hosted lane agent-hosted names host frankel-builder'),
                ('in the builder queue', lambda c: c['lanes']['agent-hosted'].update(queue='os-release-frankel'),
                 'GitHub-hosted lane agent-hosted shares its queue os-release-frankel with a builder'),
                ('another runner', lambda c: c['lanes']['agent-hosted'].update(runner='roseos-frankel-builder'),
                 'job agent-signer of GitHub-hosted lane agent-hosted runs on ubuntu-24.04, not roseos-frankel-builder'),
                ('no target', lambda c: c['lanes']['agent-hosted'].update(target='own'),
                 'lane agent-hosted has no host and is not a GitHub-hosted lane'),
                ('off the ledger', lambda c: c['shared']['appVersionLedger'].update(hostedLanes=[]),
                 'lane agent-hosted allocates app versions but is not on the ledger host frankel-builder'),
                ('the builder route as shared-store only', lambda c: c['shared']['appVersionLedger'].update(
                    hostedLanes=['agent-hosted', 'agent']),
                 'lane agent allocates through the shared store alone but is not a GitHub-hosted lane'),
                ('app-only relabelled hosted', lambda c: (c['lanes']['agent']['modes'].remove('app-only'),
                                                          c['lanes']['agent-hosted']['modes'].append('app-only')),
                 'job app-builder of GitHub-hosted lane agent-hosted allocates with the builder-local ledger')):
            with self.subTest(name):
                contract = copy.deepcopy(CONTRACT)
                change(contract)
                self.assertIn(expected, problems(contract, *real()))


if __name__ == '__main__':
    unittest.main()
