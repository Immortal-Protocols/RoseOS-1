"""BLAZER-PREP: the manual blazer-prep mode of .github/workflows/os-release.yml and scripts/blazer-prep-wiring.py.

The workflow's own shell is executed with stand-ins (no host, network, token, download, vendor script or lunch): the
guard, the hosted binding check, the bounded stage invocation, the receipt allowlist, and the controller/cleanup
ownership commands against the fake builder adapter. The helper scripts/blazer-build-only.py is Flavors-owned and tested
by test_blazer_build_only.py; it is never run here."""
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = (ROOT/'.github/workflows/os-release.yml').read_text()
MODE = 'blazer-prep'
WIRING = ROOT/'scripts/blazer-prep-wiring.py'
BINDING = json.loads((ROOT/'os/build-only/blazer-prep-host.json').read_text())
RECIPE = json.loads((ROOT/'os/build-only/blazer.json').read_text())
PRODUCTION = '/home/pixelbuild/grapheneos-2026100600'
LOCK = '/home/pixelbuild/frankel-build.lock'
RECEIPTS = ('preflight.json', 'started.json', 'completed.json')

spec = importlib.util.spec_from_file_location('blazer_prep_wiring', WIRING)
wiring = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wiring)


def jobs():
    body = WORKFLOW.split('\njobs:\n', 1)[1]
    return dict(re.findall(r'(?ms)^  ([\w-]+):\n(.*?)(?=^  [\w-]+:\n|\Z)', body))


def step(job, name):
    match = re.search(r'(?ms)^      - name: ' + re.escape(name) + r'\n(.*?)(?=^      - |\Z)', job)
    assert match, name
    return match[1]


def script(step_text):
    block = re.search(r'(?ms)^        run: \|\n(.*)', step_text)
    if block:
        lines = []
        for line in block[1].splitlines():
            if line.strip() and not line.startswith(' ' * 10):
                break  # the run block ends where its indentation does (a following step comment is not shell)
            lines.append(line[10:])
        return '\n'.join(lines) + '\n'
    return re.search(r'(?m)^        run: (.+)$', step_text)[1] + '\n'


def code(text):
    return '\n'.join(line for line in text.splitlines() if not line.strip().startswith('#'))


def bound(root, **change):
    """A bound copy of the committed binding (isolated example paths, never the production tree)."""
    record = {**BINDING, 'sourceRoot': '/srv/rose-blazer/src', 'downloadDir': '/srv/rose-blazer-dl', 'node': '/usr/bin/node',
        **change}
    path = Path(root)/'binding.json'
    path.write_text(json.dumps(record))
    return path


def wire(*argv):
    return subprocess.run([sys.executable, str(WIRING), *argv], capture_output=True, text=True, cwd=ROOT)


class Guard(unittest.TestCase):
    BASE = {'MODE': MODE, 'CANDIDATE_SHA': '', 'BUILD_NUMBER': '', 'BUILD_TIMESTAMP': '', 'APPS': '', 'PROMOTE': 'false',
            'CHANNEL': 'beta', 'RESUME_STAGE': 'none', 'RESUME_SOURCE_RUN': '', 'RESUME_UNSIGNED_SHA256': '',
            'RESUME_SIGNED_SHA256': '', 'CAMPAIGN': '', 'APP_PUBLISH': 'false', 'ROSE_RANGO_PUBLISH': 'false',
            'ROSE_FIRST_USE_ENROLL': 'false', 'ROSE_RECOVER_RUN': '', 'BOOTSTRAP_PROFILE': 'none', 'MODELS_BOOTSTRAP_RUN': '',
            'REUSE_BINDING': '', 'ALPHA_RECOVER': 'false', 'ROSE_DEVICE': 'frankel', 'BLAZER_PREP_STAGE': 'download',
            'GITHUB_SHA': 'f' * 40}

    def guard_script(self):
        return 'set -euo pipefail\n' + script(step(jobs()['guard'], 'Candidate is an exact commit on main'))

    def run_guard(self, **change):
        env = {'PATH': os.environ['PATH'], **self.BASE, **change}
        return subprocess.run(['bash', '-c', self.guard_script()], env={k: v for k, v in env.items() if v is not None},
            capture_output=True, cwd=ROOT).returncode

    def test_whole_guard_admits_exactly_the_bare_mode_with_one_preparation_stage(self):
        for stage in ('download', 'vendor', 'lunch'):
            with self.subTest(stage=stage):
                self.assertEqual(self.run_guard(BLAZER_PREP_STAGE=stage), 0)
        for change in ({'BLAZER_PREP_STAGE': 'none'}, {'BLAZER_PREP_STAGE': None}, {'BLAZER_PREP_STAGE': 'compile'},
                       {'BLAZER_PREP_STAGE': 'generate'}, {'BLAZER_PREP_STAGE': 'download vendor'},
                       {'CANDIDATE_SHA': 'a' * 40}, {'BUILD_NUMBER': '2026103001'}, {'BUILD_TIMESTAMP': '1793500000'},
                       {'APPS': '[["dev.pixel.voice",1]]'}, {'PROMOTE': 'true'}, {'RESUME_STAGE': 'sign'},
                       {'RESUME_SOURCE_RUN': '1'}, {'RESUME_UNSIGNED_SHA256': 'a' * 64}, {'RESUME_SIGNED_SHA256': 'a' * 64},
                       {'CAMPAIGN': 'start'}, {'APP_PUBLISH': 'true'}, {'ROSE_RANGO_PUBLISH': 'true'},
                       {'ROSE_FIRST_USE_ENROLL': 'true'}, {'ROSE_RECOVER_RUN': '41000000000'},
                       {'BOOTSTRAP_PROFILE': 'models-v0'}, {'MODELS_BOOTSTRAP_RUN': '1'}, {'REUSE_BINDING': '{}'},
                       {'ALPHA_RECOVER': 'true'}, {'ROSE_DEVICE': 'blazer'}, {'ROSE_DEVICE': 'rango'}):
            with self.subTest(change=change):
                self.assertNotEqual(self.run_guard(**change), 0)

    def test_a_preparation_stage_is_refused_in_every_other_mode(self):
        observe = dict(MODE='rango-factory-observe')
        self.assertEqual(self.run_guard(**observe, BLAZER_PREP_STAGE='none'), 0)
        self.assertEqual(self.run_guard(**observe, BLAZER_PREP_STAGE=None), 0)  # older callers never set it
        for stage in ('download', 'vendor', 'lunch'):
            self.assertNotEqual(self.run_guard(**observe, BLAZER_PREP_STAGE=stage), 0)
        line = '[[ "$MODE" == blazer-prep || "${BLAZER_PREP_STAGE:-none}" == none ]]'
        self.assertIn('          ' + line + '\n', jobs()['guard'])
        for mode in ('latest-beta', 'device-readiness', 'app-only', 'rango-retained', 'signing-bootstrap', 'models-sign'):
            result = subprocess.run(['bash', '-c', line], env={'MODE': mode, 'BLAZER_PREP_STAGE': 'vendor'})
            self.assertNotEqual(result.returncode, 0, mode)

    def test_mode_input_and_hosted_check_before_the_controller(self):
        self.assertIn('models-sign, rango-retained, blazer-prep, emulator-alpha-successor,', WORKFLOW)
        self.assertIn('      blazer_prep_stage:\n        description: blazer-prep only - the one preparation stage this run '
                      'executes (none for every other mode)\n        type: choice\n        options: [none, download, vendor, '
                      'lunch]\n        default: none\n', WORKFLOW)
        self.assertIn("  BLAZER_PREP_STAGE: ${{ inputs.blazer_prep_stage || 'none' }}\n", WORKFLOW)
        inputs = re.findall(r'(?m)^      (\w+):$', WORKFLOW.split('    inputs:\n', 1)[1].split('\npermissions:', 1)[0])
        self.assertIn('blazer_prep_stage', inputs)
        self.assertLessEqual(len(inputs), 25)  # the GitHub workflow_dispatch input limit
        guard = jobs()['guard']
        self.assertEqual(step(guard, 'Blazer preparation check'), "        id: blazer-prep\n        if: inputs.mode == "
            "'blazer-prep'\n        run: python3 scripts/blazer-prep-wiring.py check --stage \"$BLAZER_PREP_STAGE\" >> "
            "\"$GITHUB_OUTPUT\"\n      # Fails closed before the controller starts unless the profile is resource-bound "
            "with an approved inventory, tool\n      # identities and unexpired access, and every other option is at its "
            "non-publishing default.\n")
        self.assertIn('    outputs:\n      blazer_prep_outer_seconds: ${{ steps.blazer-prep.outputs.outer_seconds }}\n'
                      '      blazer_prep_job_minutes: ${{ steps.blazer-prep.outputs.job_minutes }}\n', guard)
        self.assertEqual(re.search(r'(?m)^    needs: (.+)$', jobs()['controller'])[1], 'guard')


class Wiring(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_committed_binding_is_unbound_so_every_stage_refuses_before_the_controller(self):
        self.assertEqual(BINDING['host'], 'immortal-os-builder')
        self.assertEqual([BINDING[k] for k in ('sourceRoot', 'downloadDir', 'node')], [None, None, None])
        for stage, missing in (('download', 'downloadDir'), ('vendor', 'downloadDir, sourceRoot, node'), ('lunch', 'sourceRoot')):
            result = wire('check', '--stage', stage)
            self.assertEqual(result.returncode, 4, stage)
            self.assertEqual(result.stdout, '')
            self.assertIn(f'SETUP_MISSING', result.stderr)
            self.assertIn(missing, result.stderr)

    def test_bounds_are_derived_from_the_recipe_seconds(self):
        binding = bound(self.tmp)
        expected = {'download': (3600, 3900, 76), 'vendor': (10800, 11100, 196), 'lunch': (1800, 2100, 46)}
        for stage, (helper_seconds, outer, minutes) in expected.items():
            self.assertEqual(RECIPE['preparation'][stage]['timeoutSeconds'], helper_seconds)
            result = wire('check', '--stage', stage, '--binding', str(binding))
            self.assertEqual((result.returncode, result.stdout), (0, f'outer_seconds={outer}\njob_minutes={minutes}\n'), stage)

    def test_exact_helper_invocation_per_stage_and_never_compile(self):
        binding = bound(self.tmp)
        seen = []
        original = os.execv
        os.execv = lambda exe, argv: seen.append(argv) or (_ for _ in ()).throw(SystemExit(0))
        try:
            for stage in ('download', 'vendor', 'lunch'):
                with self.assertRaises(SystemExit), open(os.devnull, 'w') as null:
                    stdout, sys.stdout = sys.stdout, null
                    try:
                        wiring.main(['run', '--stage', stage, '--receipt-dir', '/tmp/r', '--binding', str(binding)])
                    finally:
                        sys.stdout = stdout
        finally:
            os.execv = original
        head = [sys.executable, str(ROOT/'scripts/blazer-build-only.py'), 'run']
        common = ['--host', 'immortal-os-builder', '--lock', LOCK]
        self.assertEqual(seen, [
            head + ['--stage', 'download', *common, '--download-dir', '/srv/rose-blazer-dl', '--receipt-dir', '/tmp/r'],
            head + ['--stage', 'vendor', *common, '--download-dir', '/srv/rose-blazer-dl', '--source-root', '/srv/rose-blazer/src',
                    '--node', '/usr/bin/node', '--receipt-dir', '/tmp/r'],
            head + ['--stage', 'lunch', *common, '--source-root', '/srv/rose-blazer/src', '--receipt-dir', '/tmp/r']])
        for argv in seen:
            self.assertFalse({'compile', '--jobs', '--integration-dir', '--inputs-receipt', 'plan'} & set(argv))

    def test_invalid_stage_binding_or_receipt_dir_refuses(self):
        good = bound(self.tmp)
        for argv in (('check', '--stage', 'compile'), ('check', '--stage', 'generate'), ('run', '--stage', 'compile'),
                     ('check', '--stage', 'download', '--receipt-dir', '/tmp/r'), ('run', '--stage', 'lunch'),
                     ('run', '--stage', 'lunch', '--receipt-dir', 'relative')):
            self.assertEqual(wire(*argv, '--binding', str(good)).returncode, 2, argv)
        for change in ({'sourceRoot': PRODUCTION}, {'sourceRoot': PRODUCTION + '/blazer'}, {'sourceRoot': '/home/pixelbuild'},
                       {'downloadDir': PRODUCTION + '/dl'}, {'downloadDir': '/'}, {'sourceRoot': 'srv/relative'},
                       {'sourceRoot': '/srv/a/../b'}, {'sourceRoot': '/srv/a/'}, {'node': 'node'}, {'node': 24},
                       {'downloadDir': '/srv/rose-blazer'}, {'host': 'other-builder'}, {'host': 'Immortal-OS-Builder'},
                       {'lockFrom': 'os/release-pipeline/frankel.json host.sourceRoot'}, {'kind': 'other'},
                       {'schemaVersion': 2}, {'jobs': 8}):
            with self.subTest(change=change):
                self.assertEqual(wire('check', '--stage', 'vendor', '--binding', str(bound(self.tmp, **change))).returncode, 2)


class Job(unittest.TestCase):
    def setUp(self):
        self.job = jobs()[MODE]
        self.tmp = Path(tempfile.mkdtemp())

    def test_isolated_read_only_bounded_job(self):
        head = code(self.job.split('    steps:\n', 1)[0])
        self.assertEqual(head, "    needs: [guard, controller]\n    if: inputs.mode == 'blazer-prep'\n"
            '    runs-on: [self-hosted, roseos-frankel-builder]\n'
            "    timeout-minutes: ${{ fromJSON(needs.guard.outputs.blazer_prep_job_minutes || '1') }}\n"
            '    permissions:\n      contents: read\n    env:\n      OUTER_SECONDS: ${{ needs.guard.outputs.blazer_prep_outer_seconds }}')
        body = code(self.job)
        for absent in ('id-token', 'secrets.', 'actions-gcp-login', 'GCP_', 'PIXEL_', 'sign', 'publish', 'compile', 'make',
                       'download-artifact', 'release-pipeline.py', 'device-readiness', PRODUCTION, 'grapheneos'):
            self.assertNotIn(absent, body)
        self.assertEqual(sorted(set(re.findall(r'python3 (\S+)', body))), ['scripts/blazer-prep-wiring.py'])
        self.assertIn('          persist-credentials: false\n', self.job)
        self.assertEqual([n for n, b in jobs().items() if 'blazer-prep-wiring.py run' in b or 'blazer-build-only.py' in code(b)], [MODE])
        self.assertNotIn('blazer', jobs()['device-readiness'])

    def stage(self, outer, check_output):
        bin_dir = self.tmp/'bin'
        bin_dir.mkdir(exist_ok=True)
        log = self.tmp/'calls.log'
        (bin_dir/'python3').write_text('#!/bin/sh\necho "python3 $*" >> "$LOG"\n'
            'case "$*" in *" check "*) printf "%s\\n" "$CHECK_OUTPUT";; esac\n')
        (bin_dir/'timeout').write_text('#!/bin/sh\necho "timeout $*" >> "$LOG"\n')
        for name in ('python3', 'timeout'):
            (bin_dir/name).chmod(0o755)
        env = {'PATH': f'{bin_dir}:/usr/bin:/bin', 'LOG': str(log), 'OUTER_SECONDS': outer, 'CHECK_OUTPUT': check_output,
            'BLAZER_PREP_STAGE': 'vendor', 'TASK_RUN': '/runner/tmp/blazer-prep-1-1/run'}
        result = subprocess.run(['bash', '-c', script(step(self.job, 'Preparation stage (bounded, under the shared lock)'))],
            env=env, capture_output=True)
        calls = log.read_text().splitlines() if log.exists() else []
        log.unlink(missing_ok=True)
        return result.returncode, calls

    def test_stage_runs_once_under_the_outer_timeout_matching_the_rechecked_bound(self):
        code_, calls = self.stage('11100', 'outer_seconds=11100\njob_minutes=196')
        self.assertEqual(code_, 0)
        self.assertEqual(calls, ['python3 scripts/blazer-prep-wiring.py check --stage vendor',
            'timeout --kill-after=30s 11100s python3 scripts/blazer-prep-wiring.py run --stage vendor --receipt-dir '
            '/runner/tmp/blazer-prep-1-1/run'])
        for outer, check in (('1080', 'outer_seconds=11100\njob_minutes=196'), ('', 'outer_seconds=11100'),
                             ('11100s', 'outer_seconds=11100'), ('0', 'outer_seconds=0')):
            code_, calls = self.stage(outer, check)
            self.assertNotEqual(code_, 0, outer)
            self.assertFalse([c for c in calls if c.startswith('timeout')], outer)

    def test_only_the_three_regular_receipts_leave_the_host_and_the_task_is_removed(self):
        task = self.tmp/'task'
        run = task/'run'
        run.mkdir(parents=True)
        for name in RECEIPTS[:2]:
            (run/name).write_text('{}')
        (run/'completed.json').symlink_to(run/'preflight.json')
        (run/'extra.json').write_text('{}')
        env = {'PATH': os.environ['PATH'], 'TASK_ROOT': str(task), 'TASK_RUN': str(run), 'TASK_RECEIPTS': str(task/'receipts')}
        subprocess.run(['bash', '-c', script(step(self.job, 'Allowlisted JSON receipts only'))], env=env, check=True)
        self.assertEqual(sorted(p.name for p in (task/'receipts').iterdir()), ['preflight.json', 'started.json'])
        artifact = step(self.job, 'Receipts artifact')
        self.assertIn('          name: blazer-prep-${{ inputs.blazer_prep_stage }}-${{ github.run_id }}-${{ github.run_attempt }}\n'
                      '          path: ${{ env.TASK_RECEIPTS }}\n          retention-days: 7\n', artifact)
        subprocess.run(['bash', '-c', script(step(self.job, "Remove this attempt's task directory"))], env=env, check=True)
        self.assertFalse(task.exists())
        self.assertEqual(subprocess.run(['bash', '-c', script(step(self.job, 'Allowlisted JSON receipts only'))],
            env={'PATH': os.environ['PATH']}).returncode, 0)  # no task directory: nothing to collect


class Ownership(unittest.TestCase):
    """The controller and cleanup commands as written in the workflow, run against the fake builder adapter."""
    def commands(self):
        controller, cleanup = jobs()['controller'], jobs()['cleanup']
        start = re.search(r"(?m)^      - if: inputs.mode == 'blazer-prep'\n        run: (.+)$", controller)[1]
        record = re.search(r"(?m)^        if: always\(\) && inputs.mode == 'blazer-prep'\n        run: (.+)$", controller)[1]
        stop = re.search(r"(?m)^      - if: inputs.mode == 'blazer-prep'\n        env:\n          STARTED: \$\{\{ needs.controller"
                         r".outputs.started \}\}\n        run: (.+)$", cleanup)[1]
        return start, record, stop

    def test_workflow_routes_only_blazer_prep_through_ownership(self):
        controller, cleanup = jobs()['controller'], jobs()['cleanup']
        self.assertIn("    outputs:\n      started: ${{ steps.ownership.outputs.started }}\n", controller)
        self.assertIn("      - if: inputs.mode != 'blazer-prep'\n        run: python3 scripts/release-pipeline.py start-builder\n", controller)
        self.assertIn("      - if: inputs.mode != 'blazer-prep'\n        run: python3 scripts/release-pipeline.py stop-builder\n", cleanup)
        self.assertEqual(len(re.findall(r'release-pipeline.py (?:start|stop)-builder', controller + cleanup)), 4)
        self.assertIn('rango-factory-observe, blazer-prep, app-builder', re.search(r'(?m)^    needs: (.+)$', cleanup)[1])
        self.assertIn("    if: always() && needs.controller.result != 'skipped'\n", cleanup)
        start, record, stop = self.commands()
        self.assertEqual(start, 'python3 scripts/release-pipeline.py start-builder --ownership-record "$RUNNER_TEMP/builder-ownership.json"')
        self.assertEqual(stop, 'python3 scripts/release-pipeline.py stop-builder --only-if-started "$STARTED"')
        for job in (j for n, j in jobs().items() if n not in ('controller', 'cleanup')):
            self.assertNotRegex(job, r'(start|stop)-builder|ownership-record|only-if-started')

    def simulate(self, observed, record_step=True):
        tmp = Path(tempfile.mkdtemp())
        fake = tmp/'fake'
        fake.mkdir()
        if observed:
            (fake/'builder-observed').write_text(observed)
        start, record, stop = self.commands()
        suffix = f' --adapter fake --fake-state {fake}'
        env = {'PATH': os.environ['PATH'], 'RUNNER_TEMP': str(tmp), 'GITHUB_OUTPUT': str(tmp/'out')}
        run = lambda cmd, **extra: subprocess.run(['bash', '-c', cmd + suffix], env={**env, **extra}, cwd=ROOT, capture_output=True)
        self.assertEqual(run(start).returncode, 0)
        started = ''
        if record_step:
            self.assertEqual(run(record).returncode, 0)
            started = dict(line.split('=', 1) for line in (tmp/'out').read_text().split())['started']
        result = run(stop, STARTED=started)
        events = (fake/'events.log').read_text().splitlines()
        return started, result.returncode, events

    def test_stops_only_a_builder_this_run_started(self):
        self.assertEqual(self.simulate('TERMINATED'), ('true', 0, ['builder start', 'builder stop']))
        self.assertEqual(self.simulate('RUNNING'), ('false', 0, ['builder start']))
        self.assertEqual(self.simulate('STAGING'), ('false', 0, ['builder start']))
        self.assertEqual(self.simulate('TERMINATED', record_step=False), ('', 1, ['builder start']))  # unknown: no stop


if __name__ == '__main__':
    unittest.main()
