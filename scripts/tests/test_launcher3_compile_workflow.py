"""LAUNCHER3-COMPILE workflow wiring: the dedicated manual workflow .github/workflows/launcher3-compile.yml.

The harness has its own queue, a hosted guard that runs the adapter's and the controller's checks (an unbound binding
exits 4 before any login, VM start or self-hosted routing), an owned controller start whose ownership record is always
preserved, a worker routed only to the bound runner label that re-checks the bounds, runs one bounded adapter run and
always finishes (allowlisted reports, this run's own cleanup), and a hosted cleanup that stops only a VM this run
started, from this run's own record. The os-release.yml launcher3-compile mode is retired. Text, the real unbound
binding and stand-ins only: no host, VM, lock, copy, lunch, build or cloud call is made."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import os_release_shape as shape  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
PATH = '.github/workflows/launcher3-compile.yml'
WORKFLOW = (ROOT/PATH).read_text()
spec = importlib.util.spec_from_file_location('launcher3_compile', ROOT/'scripts/launcher3-compile.py')
adapter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adapter)
spec = importlib.util.spec_from_file_location('launcher3_routing', ROOT/'scripts/launcher3-routing.py')
routing = importlib.util.module_from_spec(spec)
spec.loader.exec_module(routing)
CHECKOUT = ('      - uses: actions/checkout@11d5960a326750d5838078e36cf38b85af677262 # v4\n        with:\n'
            '          ref: ${{ github.sha }}\n          persist-credentials: false\n')
ENV = ('      GCP_WORKLOAD_IDENTITY_PROVIDER: ${{ vars.LAUNCHER3_CONTROLLER_WORKLOAD_IDENTITY_PROVIDER }}\n'
       '      LAUNCHER3_CONTROLLER_SERVICE_ACCOUNT: ${{ vars.LAUNCHER3_CONTROLLER_SERVICE_ACCOUNT }}\n')


def jobs(text=WORKFLOW):
    body = text.split('\njobs:\n', 1)[1]
    return dict(re.findall(r'(?ms)^  ([\w-]+):\n(.*?)(?=^  [\w-]+:\n|\Z)', body))


def step(job, name):
    match = re.search(r'(?ms)^      - name: ' + re.escape(name) + r'\n(.*?)(?=^      - |\Z)', job)
    assert match, name
    return match[1]


def code(text):
    return '\n'.join(line for line in text.splitlines() if not line.strip().startswith('#'))


def header(job):
    return code(job.split('    steps:\n', 1)[0])


def runs(job):
    """Every run: command of a job, in order."""
    return [a or b for a, b in re.findall(r'(?m)^        run: (.+)$|^      - run: (.+)$', job)]


class Trigger(unittest.TestCase):
    def test_manual_only_with_its_own_queue_and_least_privilege(self):
        head = WORKFLOW.split('\njobs:\n', 1)[0]
        self.assertIn('\non:\n  workflow_dispatch: {}\n\npermissions:\n  contents: read\n', head)
        for absent in ('schedule', 'push:', 'pull_request', 'workflow_run', 'repository_dispatch', 'inputs:'):
            self.assertNotIn(absent, code(head))
        self.assertIn('\nconcurrency:\n', head)
        group = re.search(r'(?m)^  group: (\S+)$', head)[1]
        self.assertEqual(group, routing.QUEUE)  # the queue the routing guard requires the binding to name
        self.assertIn('  cancel-in-progress: false\n  queue: max\n', head)
        for other in ('os-release-frankel', 'yogi-ota'):  # never a release, Yogi or Agent queue
            self.assertNotEqual(group, other)
        self.assertEqual(list(jobs()), ['guard', 'controller', 'worker', 'cleanup'])

    def test_every_job_checks_out_this_runs_own_commit(self):
        for name, job in jobs().items():
            with self.subTest(job=name):
                self.assertEqual(job.count('actions/checkout@'), 1)
                self.assertIn(CHECKOUT, job)


class Guard(unittest.TestCase):
    def test_hosted_guard_checks_main_attempt_contract_and_lane_before_any_login(self):
        guard = jobs()['guard']
        self.assertIn('    runs-on: ubuntu-24.04\n', guard)
        self.assertNotIn('permissions:', guard)  # the workflow's contents: read; no OIDC token
        for absent in ('id-token', 'actions-gcp-login', 'vars.', 'secrets.', 'start', 'stop'):
            self.assertNotIn(absent, code(guard.split('    steps:\n', 1)[1]))
        first = step(guard, 'Main only, attempt 1 only')
        self.assertIn('[[ "$GITHUB_EVENT_NAME" == workflow_dispatch && "$GITHUB_REF" == refs/heads/main && '
                      '"$GITHUB_RUN_ATTEMPT" == 1 ]]', first)
        self.assertLess(guard.index('Main only, attempt 1 only'), guard.index('actions/checkout@'))
        self.assertIn('        id: bounds\n        run: python3 scripts/launcher3-compile.py check >> "$GITHUB_OUTPUT"\n',
                      step(guard, 'Contract, binding and bounds'))
        self.assertIn('        id: lane\n        run: python3 scripts/launcher3-routing.py guard >> "$GITHUB_OUTPUT"\n',
                      step(guard, 'Dedicated lane routing'))
        self.assertLess(guard.index('Contract, binding and bounds'), guard.index('Dedicated lane routing'))
        for name in ('outer_seconds', 'finish_seconds', 'job_minutes'):
            self.assertIn(f'      {name}: ${{{{ steps.bounds.outputs.{name} }}}}\n', guard)
        self.assertIn('      runs_on: ${{ steps.lane.outputs.runs_on }}\n', guard)

    def test_the_attempt_check_refuses_another_ref_event_or_attempt(self):
        body = step(jobs()['guard'], 'Main only, attempt 1 only')
        script = '\n'.join(line[10:] for line in re.search(r'(?ms)^        run: \|\n(.*)', body)[1].splitlines())
        run = lambda **e: subprocess.run(['bash', '-c', script], env={'PATH': os.environ['PATH'], **e},
                                         capture_output=True).returncode
        ok = dict(GITHUB_EVENT_NAME='workflow_dispatch', GITHUB_REF='refs/heads/main', GITHUB_RUN_ATTEMPT='1')
        self.assertEqual(run(**ok), 0)
        for change in ({'GITHUB_EVENT_NAME': 'push'}, {'GITHUB_REF': 'refs/heads/other'}, {'GITHUB_RUN_ATTEMPT': '2'}):
            with self.subTest(change=change):
                self.assertNotEqual(run(**{**ok, **change}), 0)

    def test_the_committed_binding_is_unbound_so_nothing_is_routed(self):
        env = {k: v for k, v in os.environ.items() if not k.startswith('GITHUB_')}
        env.update(GITHUB_ACTIONS='true', GITHUB_WORKFLOW_REF=f'Immortal-Protocols/RoseOS-1/{PATH}@refs/heads/main')
        for script in ('scripts/launcher3-compile.py', 'scripts/launcher3_controller.py'):
            with self.subTest(script=script):
                result = subprocess.run([sys.executable, str(ROOT/script), 'check'], capture_output=True, text=True,
                                        env=env, cwd=ROOT)
                self.assertEqual((result.returncode, result.stdout), (4, ''))
        result = subprocess.run([sys.executable, str(ROOT/'scripts/launcher3-routing.py'), 'guard'], capture_output=True,
                                text=True, env=env, cwd=ROOT)
        self.assertEqual((result.returncode, result.stdout), (4, ''))  # no runs_on output while unbound
        self.assertIn('unbound', result.stderr)
        # From the retired os-release.yml route, or any other workflow, the lane refuses outright.
        env['GITHUB_WORKFLOW_REF'] = 'Immortal-Protocols/RoseOS-1/.github/workflows/os-release.yml@refs/heads/main'
        result = subprocess.run([sys.executable, str(ROOT/'scripts/launcher3_controller.py'), 'check'],
                                capture_output=True, text=True, env=env, cwd=ROOT)
        self.assertEqual(result.returncode, 4)
        self.assertIn('legacy or foreign workflow refused', result.stderr)


class Routing(unittest.TestCase):
    """scripts/launcher3-routing.py on a stand-in controller check; the real check runs above."""
    LANE = {'status': 'source-bound', 'runtimeAuthorized': False, 'bindingSha256': 'b' * 64,
            'workflow': PATH, 'runnerName': 'launcher3-runner-synthetic', 'runnerLabel': 'launcher3-label-synthetic',
            'queue': 'launcher3-compile', 'vm': {'project': 'p', 'zone': 'z', 'instance': 'i', 'instanceId': '1'},
            'controller': {'identityVariable': 'LAUNCHER3_CONTROLLER_SERVICE_ACCOUNT',
                           'providerVariable': 'LAUNCHER3_CONTROLLER_WORKLOAD_IDENTITY_PROVIDER'}}

    def guard(self, lane=None, code=0, stderr=''):
        calls = []

        def run(argv, **kwargs):
            calls.append(argv)
            return subprocess.CompletedProcess(argv, code, json.dumps(lane or self.LANE) if not code else '', stderr)
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            result = routing.guard(run=run)
        self.assertEqual(calls, [[sys.executable, str(ROOT/'scripts/launcher3_controller.py'), 'check']])
        return result, out.getvalue(), err.getvalue()

    def test_a_bound_lane_routes_the_worker_to_its_own_label_only(self):
        code, out, _ = self.guard()
        self.assertEqual(code, 0)
        self.assertEqual(out, 'runs_on=["self-hosted","launcher3-label-synthetic"]\n'
                              'runner_name=launcher3-runner-synthetic\nbinding_sha256=' + 'b' * 64 + '\n')

    def test_an_unbound_or_invalid_binding_passes_its_refusal_through_and_routes_nothing(self):
        code, out, err = self.guard(code=4, stderr='independent harness capacity is unbound: status')
        self.assertEqual((code, out), (4, ''))
        self.assertIn('unbound', err)

    def test_a_binding_naming_other_routing_is_refused(self):
        cases = {
            'queue': dict(queue='os-release-frankel'),
            'workflow': dict(workflow='.github/workflows/os-release.yml'),
            'identity': dict(controller={'identityVariable': 'GCP_CONTROLLER_SERVICE_ACCOUNT',
                                         'providerVariable': 'LAUNCHER3_CONTROLLER_WORKLOAD_IDENTITY_PROVIDER'}),
            'provider': dict(controller={'identityVariable': 'LAUNCHER3_CONTROLLER_SERVICE_ACCOUNT',
                                         'providerVariable': 'GCP_WORKLOAD_IDENTITY_PROVIDER'}),
            'label with a space': dict(runnerLabel='roseos frankel'),
            'label self-hosted': dict(runnerLabel='Self-Hosted'),
            'no runner name': dict(runnerName=None),
            'no digest': dict(bindingSha256='x'),
            'not source-bound': dict(status='unbound'),
            'runtime authorized': dict(runtimeAuthorized=True),
        }
        for label, change in cases.items():
            with self.subTest(label):
                code, out, err = self.guard(lane={**self.LANE, **change})
                self.assertEqual((code, out), (2, ''))
                self.assertEqual(json.loads(err)['verdict'], 'MISMATCH')

    def test_the_routing_constants_are_this_workflows(self):
        self.assertEqual(routing.WORKFLOW, PATH)
        for name in ('controller', 'cleanup'):
            self.assertIn(ENV, jobs()[name])
        names = set(re.findall(r'vars\.(\w+)', WORKFLOW))
        self.assertEqual(names, set(routing.CONTROLLER.values()))
        self.assertTrue(all(n.startswith('LAUNCHER3_') for n in names))


class Controller(unittest.TestCase):
    def test_hosted_controller_logs_in_with_the_harness_identity_and_keeps_its_ownership_record(self):
        job = jobs()['controller']
        self.assertEqual(header(job), '    needs: guard\n    runs-on: ubuntu-24.04\n    timeout-minutes: 15\n'
                         '    permissions:\n      contents: read\n      id-token: write\n    env:\n' + ENV.rstrip('\n'))
        self.assertEqual(runs(job), ['python3 scripts/actions-gcp-login.py login "$LAUNCHER3_CONTROLLER_SERVICE_ACCOUNT"',
            'python3 scripts/launcher3_controller.py start --ownership-record "$RUNNER_TEMP/launcher3-ownership.json"',
            'python3 scripts/actions-gcp-login.py logout'])
        record = step(job, 'Ownership record')
        self.assertIn('        if: always()\n', record)
        self.assertIn('          name: launcher3-ownership-${{ github.run_id }}-${{ github.run_attempt }}\n'
                      '          path: ${{ runner.temp }}/launcher3-ownership.json\n', record)
        self.assertLess(job.index('launcher3_controller.py start'), job.index('- name: Ownership record'))
        self.assertLess(job.index('- name: Ownership record'), job.index('actions-gcp-login.py logout'))
        self.assertIn('      - if: always()\n        run: python3 scripts/actions-gcp-login.py logout\n', job)
        for absent in ('release-pipeline.py', 'start-builder', 'stop-builder', 'GCP_CONTROLLER_SERVICE_ACCOUNT',
                       'GCP_SIGNER', 'YOGI_', 'MUSTANG_', 'secrets.', '--only-if-started'):
            self.assertNotIn(absent, code(job))


class Worker(unittest.TestCase):
    def test_worker_header_runner_permissions_and_commands(self):
        job = jobs()['worker']
        self.assertEqual(header(job), "    needs: [guard, controller]\n"
            "    runs-on: ${{ fromJSON(needs.guard.outputs.runs_on) }}\n"
            "    timeout-minutes: ${{ fromJSON(needs.guard.outputs.job_minutes || '1') }}\n"
            "    permissions:\n      contents: read\n    env:\n"
            "      OUTER_SECONDS: ${{ needs.guard.outputs.outer_seconds }}\n"
            "      FINISH_SECONDS: ${{ needs.guard.outputs.finish_seconds }}")
        body = code(job)
        for absent in ('id-token', 'secrets.', 'vars.', 'actions-gcp-login', 'GCP_', 'PIXEL_', 'publish', 'host-sign',
                       'rm -rf', 'download-artifact', 'apply-os-changes', 'launcher3-compile-contract', 'reconstruct',
                       'roseos-frankel-builder', 'self-hosted'):
            self.assertNotIn(absent, body)
        self.assertEqual(sorted(set(re.findall(r'python3 (\S+)', body))), ['scripts/launcher3-compile.py'])
        self.assertIn('exec timeout --kill-after=30s "${OUTER_SECONDS}s" python3 scripts/launcher3-compile.py run '
                      '--run-id "$GITHUB_RUN_ID" --attempt "$GITHUB_RUN_ATTEMPT"\n', job)
        self.assertIn('timeout --kill-after=30s "${FINISH_SECONDS}s" python3 scripts/launcher3-compile.py finish '
                      '--run-id "$GITHUB_RUN_ID" --attempt "$GITHUB_RUN_ATTEMPT" --upload-dir '
                      '"$RUNNER_TEMP/launcher3-compile-upload-$GITHUB_RUN_ID-$GITHUB_RUN_ATTEMPT"\n', job)
        for name in ("Allowlisted reports and this run's cleanup", 'Reports artifact'):
            self.assertIn('        if: always()\n', step(job, name))
        self.assertNotIn('if: always()', step(job, 'Compile (bounded, under the lane lock)'))
        self.assertEqual(job.count('actions/upload-artifact@'), 1)
        self.assertIn('          name: launcher3-compile-${{ github.run_id }}-${{ github.run_attempt }}\n'
                      '          path: ${{ runner.temp }}/launcher3-compile-upload-${{ github.run_id }}-${{ github.run_attempt }}\n'
                      '          retention-days: 7\n', job)

    def test_the_recheck_step_refuses_drifted_bounds(self):
        body = re.search(r'(?ms)^        run: \|\n(.*)', step(jobs()['worker'], 'Recheck the contract, binding and bounds on the worker'))[1]
        script = '\n'.join(line[10:] for line in body.splitlines())
        tmp = Path(tempfile.mkdtemp())
        fake = tmp/'python3'
        fake.write_text('#!/bin/sh\nprintf "outer_seconds=18090\\nfinish_seconds=1800\\njob_minutes=342\\n"\n')
        fake.chmod(0o755)
        env = {'PATH': f'{tmp}:{os.environ["PATH"]}', 'RUNNER_TEMP': str(tmp)}
        run = lambda **e: subprocess.run(['bash', '-c', script], env={**env, **e}, cwd=ROOT, capture_output=True).returncode
        self.assertEqual(run(OUTER_SECONDS='18090', FINISH_SECONDS='1800'), 0)
        for change in ({'OUTER_SECONDS': '18091', 'FINISH_SECONDS': '1800'}, {'OUTER_SECONDS': '18090', 'FINISH_SECONDS': '1801'},
                       {'OUTER_SECONDS': '', 'FINISH_SECONDS': '1800'}, {'OUTER_SECONDS': '1;x', 'FINISH_SECONDS': '1800'}):
            with self.subTest(change=change):
                self.assertNotEqual(run(**change), 0)

    def test_an_interrupted_stage_ends_inside_the_outer_kill_after_and_a_cancels_sigint_window(self):
        """An interrupt is noticed within POLL_SECONDS; then TERM, then KILL, each phase INTERRUPT_GRACE_SECONDS and one
        more poll at most. That must fit, with margin, inside the outer timeout's kill-after and inside the 7.5 s a
        cancel's SIGINT leaves before the runner's SIGTERM."""
        line = re.search(r'run: exec timeout --kill-after=(\d+)s "\$\{OUTER_SECONDS\}s" python3 scripts/launcher3-compile.py run ',
                         jobs()['worker'])
        self.assertIsNotNone(line)
        worst = adapter.POLL_SECONDS + 2 * (adapter.INTERRUPT_GRACE_SECONDS + adapter.POLL_SECONDS) + adapter.POLL_SECONDS
        self.assertLessEqual(worst + 5, int(line[1]))
        self.assertEqual(adapter.CANCEL_SIGTERM_SECONDS, 7.5)  # GitHub's documented cancel: SIGINT, 7500 ms, SIGTERM
        self.assertLessEqual(worst + 1, adapter.CANCEL_SIGTERM_SECONDS)

    def test_guard_outputs_match_the_adapter_bounds(self):
        # every lunch is the contract's (definitions.lunch): its inputs and the admitted fetch come before lunch and m;
        # the candidate branch checks its tree pins twice (base-exact, final-exact)
        for pins, expected in ((1, (21720, 1800, 403)), (2, (23520, 1800, 433))):
            outer = (adapter.PREPARE_SECONDS + adapter.KILL_GRACE_SECONDS + pins * adapter.PIN_SECONDS
                     + adapter.LUNCH_INPUT_SECONDS + adapter.ADEVTOOL_FETCH_SECONDS + adapter.KILL_GRACE_SECONDS
                     + adapter.LUNCH_SECONDS + adapter.KILL_GRACE_SECONDS + adapter.COMPILE_SECONDS + adapter.KILL_GRACE_SECONDS + 600)
            minutes = -(-(outer + adapter.FINISH_SECONDS + adapter.KILL_GRACE_SECONDS) // 60) + 10
            self.assertEqual((outer, adapter.FINISH_SECONDS, minutes), expected)


class Cleanup(unittest.TestCase):
    def test_cleanup_stops_only_from_this_runs_own_ownership_record(self):
        job = jobs()['cleanup']
        self.assertEqual(header(job), "    needs: [guard, controller, worker]\n"
            "    if: always() && needs.controller.result != 'skipped'\n    runs-on: ubuntu-24.04\n    timeout-minutes: 15\n"
            "    permissions:\n      contents: read\n      id-token: write\n    env:\n" + ENV.rstrip('\n'))
        fetched = step(job, "This run's ownership record only")
        self.assertIn('        uses: actions/download-artifact@d3f86a106a0bac45b974a628896c90dbdf5c8093 # v4.3.0\n', fetched)
        self.assertIn('          name: launcher3-ownership-${{ github.run_id }}-${{ github.run_attempt }}\n'
                      '          path: ${{ runner.temp }}/launcher3-ownership\n', fetched)
        for absent in ('run-id:', 'github-token', 'pattern:', 'repository:'):  # this run's artifact, never another's
            self.assertNotIn(absent, fetched)
        self.assertEqual(runs(job), ['python3 scripts/actions-gcp-login.py login "$LAUNCHER3_CONTROLLER_SERVICE_ACCOUNT"',
            'python3 scripts/launcher3_controller.py stop --ownership-record "$RUNNER_TEMP/launcher3-ownership/launcher3-ownership.json"',
            'python3 scripts/actions-gcp-login.py logout'])
        self.assertLess(job.index("This run's ownership record only"), job.index('actions-gcp-login.py login'))
        self.assertNotIn('if:', step(job, 'Stop only a VM this run started'))
        for absent in ('release-pipeline.py', 'stop-builder', '--only-if-started', 'GCP_CONTROLLER_SERVICE_ACCOUNT',
                       'secrets.', 'continue-on-error'):
            self.assertNotIn(absent, code(job))

    def test_the_downloaded_record_is_the_one_the_controller_uploaded(self):
        uploaded = re.search(r'path: \$\{\{ runner\.temp \}\}/(launcher3-ownership\.json)\n', jobs()['controller'])[1]
        self.assertIn(f'"$RUNNER_TEMP/launcher3-ownership/{uploaded}"', jobs()['cleanup'])
        self.assertIn(f'"$RUNNER_TEMP/{uploaded}"', jobs()['controller'])


class Retired(unittest.TestCase):
    """The os-release.yml launcher3-compile mode is removed; Frankel keeps reclaiming its earlier runs."""
    def test_os_release_no_longer_routes_the_harness(self):
        text = shape.text()
        self.assertNotIn('launcher3-compile', shape.modes())
        self.assertNotIn('launcher3-compile', shape.owned_modes())
        self.assertNotIn('launcher3-compile', shape.cleanup_needs())
        self.assertNotIn('launcher3', text.replace('Launcher3', ''))  # no job, guard output, check or condition remains

    def test_frankel_still_reclaims_the_retired_routes_earlier_runs(self):
        policy = json.loads((ROOT/'os/release-pipeline/storage/builder-reclaim.json').read_text())
        launcher3 = next(c for c in policy['classes'] if c['name'] == 'launcher3-compile')
        self.assertEqual(tuple(r['path'] for r in launcher3['reclaim']), adapter.WORK_DIRS)
        legacy = json.loads((ROOT/'os/build-only/launcher3-compile-host.json').read_text())
        self.assertTrue(launcher3['entry'] and legacy)  # the old Frankel root stays known to the reclaim policy


if __name__ == '__main__':
    unittest.main()
