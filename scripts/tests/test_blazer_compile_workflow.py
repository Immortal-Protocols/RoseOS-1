"""BLAZER-COMPILE workflow wiring (scripts/blazer-compile-wiring.py, os-release.yml mode blazer-compile): the hosted
check exits 4 while jobs is null, a bound binding resolves exactly Flavors' accepted compile argv with the recipe's own
bounds, run selects the one completed integration reserved for this checkout's HEAD (none or several refuse), and the
job is read-only, bounded and uploads only the three receipts. Text, synthetic bindings and a temporary home only: no
host, lock, integration or build."""
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
MODE = 'blazer-compile'
spec = importlib.util.spec_from_file_location('blazer_compile_wiring', ROOT/'scripts/blazer-compile-wiring.py')
wiring = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wiring)
BINDING = json.loads((ROOT/'os/build-only/blazer-compile-host.json').read_text())
SOURCE = json.loads((ROOT/'os/build-only/blazer-prep-host.json').read_text())['sourceRoot']
BOUND = {'jobs': 64}
RECIPE = 'c' * 40


def jobs():
    body = WORKFLOW.split('\njobs:\n', 1)[1]
    return dict(re.findall(r'(?ms)^  ([\w-]+):\n(.*?)(?=^  [\w-]+:\n|\Z)', body))


def step(job, name):
    match = re.search(r'(?ms)^      - name: ' + re.escape(name) + r'\n(.*?)(?=^      - |\Z)', job)
    assert match, name
    return match[1]


def code(text):
    return '\n'.join(line for line in text.splitlines() if not line.strip().startswith('#'))


def binding(**change):
    path = Path(tempfile.mkdtemp())/'binding.json'
    path.write_text(json.dumps({**BINDING, **BOUND, **change}))
    return path


class Wiring(unittest.TestCase):
    def test_the_committed_binding_binds_jobs_and_derives_the_integration_at_run_time(self):
        self.assertEqual((BINDING['jobs'], BINDING['integrationFrom']), (48, wiring.INTEGRATION_FROM))
        self.assertNotIn('integrationDir', BINDING)
        self.assertIn('unknown, not unlimited', BINDING['basis'])
        result = subprocess.run([sys.executable, str(ROOT/'scripts/blazer-compile-wiring.py'), 'check'],
                                capture_output=True, text=True)
        self.assertEqual((result.returncode, result.stdout), (0, 'outer_seconds=29100\njob_minutes=496\n'))
        result = subprocess.run([sys.executable, str(ROOT/'scripts/blazer-compile-wiring.py'), 'check', '--binding',
                                 str(binding(jobs=None))], capture_output=True, text=True)
        self.assertEqual(result.returncode, 4)

    def reserve(self, home, build, source=RECIPE, inputs=True, applied=True):
        base = home/'rose-actions'
        (base/'blazer-allocations').mkdir(parents=True, exist_ok=True)
        work, reconcile = base/f'blazer-integrate-{build}', base/f'blazer-reconcile-{build}'
        record = {'schema': 'roseos.blazer-allocation.v1', 'device': 'blazer', 'build': build, 'source': source, 'runId': '1',
                  'runAttempt': '1', 'reservedAt': '2026-11-03T15:00:00Z', 'workDir': str(work),
                  'integrationDir': str(reconcile), 'observed': {}}
        (base/'blazer-allocations'/f'{build}.json').write_text(json.dumps(record))
        if inputs:
            (work/'inputs').mkdir(parents=True)
            (work/'inputs/local-inputs.json').write_text('{}')
        if applied:
            reconcile.mkdir()
            (reconcile/'0001-2026103101.json').write_text('{}')
            (reconcile/f'0002-blazer-{build}.json').write_text('{}')
        return str(reconcile), str(work/'inputs/local-inputs.json')

    def test_run_selects_exactly_one_completed_integration_of_this_commit(self):
        home = Path(tempfile.mkdtemp())
        source = Path(SOURCE)
        with self.assertRaises(wiring.Refused):
            wiring.integration(RECIPE, source, home)  # no reservation directory
        expected = self.reserve(home, '2026110301')
        self.reserve(home, '2026110302', source='d' * 40)  # another commit's integration
        self.reserve(home, '2026110303', applied=False)  # stopped before reconcile
        self.reserve(home, '2026110304', inputs=False, applied=False)  # stopped before admit
        self.assertEqual(wiring.integration(RECIPE, source, home), expected)
        self.reserve(home, '2026110305')
        with self.assertRaises(wiring.Refused):
            wiring.integration(RECIPE, source, home)  # two completed: ambiguous
        home = Path(tempfile.mkdtemp())
        reconcile, _ = self.reserve(home, '2026110301')
        os.rename(reconcile, reconcile + '-real')
        os.symlink(reconcile + '-real', reconcile)
        with self.assertRaises(wiring.Refused):
            wiring.integration(RECIPE, source, home)  # a linked integration directory never counts
        home = Path(tempfile.mkdtemp())
        self.reserve(home, '2026110301')
        (home/'rose-actions/blazer-allocations/2026110301.json').write_text('{"build": "2026110301"}')
        with self.assertRaises(wiring.Refused):
            wiring.integration(RECIPE, source, home)  # an unrecognised reservation refuses

    def test_a_bound_binding_resolves_the_accepted_argv_and_recipe_bounds(self):
        tail, outer, minutes = wiring.resolve(binding())
        self.assertEqual(tail, ['--stage', 'compile', '--host', 'immortal-os-builder', '--lock', '/home/pixelbuild/frankel-build.lock',
                                '--source-root', SOURCE, '--jobs', '64'])
        recipe = json.loads((ROOT/'os/build-only/blazer.json').read_text())
        self.assertEqual((outer, minutes), (recipe['compile']['timeoutSeconds'] + 300, -(-(recipe['compile']['timeoutSeconds'] + 330) // 60) + 10))
        self.assertEqual((outer, minutes), (29100, 496))
        helper = (ROOT/'scripts/blazer-build-only.py').read_text()
        for flag in ('--stage', '--host', '--lock', '--source-root', '--jobs', '--integration-dir', '--inputs-receipt', '--receipt-dir'):
            self.assertIn(f"parser.add_argument('{flag}'", helper)

    def test_invalid_bindings_refuse(self):
        for change in ({'jobs': 0}, {'jobs': 257}, {'jobs': '64'}, {'jobs': True}, {'integrationFrom': 'any'},
                       {'integrationDir': '/home/pixelbuild/rose-actions/blazer-reconcile'},
                       {'sourceRootFrom': 'os/release-pipeline/frankel.json host.sourceRoot'}, {'host': 'other'}, {'extra': 1}):
            with self.subTest(change=change), self.assertRaises(wiring.Invalid):
                wiring.resolve(binding(**change))

    def test_run_needs_an_absolute_receipt_dir_and_check_takes_none(self):
        for argv in (['run', '--binding', str(binding())], ['run', '--receipt-dir', 'rel', '--binding', str(binding())],
                     ['check', '--receipt-dir', '/r', '--binding', str(binding())]):
            with self.subTest(argv=argv):
                self.assertEqual(wiring.main(argv), 2)


class Workflow(unittest.TestCase):
    def test_mode_check_job_receipts_and_ownership(self):
        self.assertIn('launcher3-compile, blazer-compile, blazer-integrate]\n', WORKFLOW)
        self.assertIn("      - name: Blazer compile check\n        id: blazer-compile\n        if: inputs.mode == "
                      "'blazer-compile'\n        run: python3 scripts/blazer-compile-wiring.py check >> \"$GITHUB_OUTPUT\"\n",
                      jobs()['guard'])
        job = jobs()[MODE]
        self.assertEqual(code(job.split('    steps:\n', 1)[0]), "    needs: [guard, controller]\n    if: inputs.mode == "
            "'blazer-compile'\n    runs-on: [self-hosted, roseos-frankel-builder]\n"
            "    timeout-minutes: ${{ fromJSON(needs.guard.outputs.blazer_compile_job_minutes || '1') }}\n"
            "    permissions:\n      contents: read\n    env:\n"
            "      OUTER_SECONDS: ${{ needs.guard.outputs.blazer_compile_outer_seconds }}")
        body = code(job)
        for absent in ('id-token', 'secrets.', 'actions-gcp-login', 'GCP_', 'publish', 'host-sign', 'download-artifact'):
            self.assertNotIn(absent, body)
        self.assertEqual(sorted(set(re.findall(r'python3 (\S+)', body))), ['scripts/blazer-compile-wiring.py'])
        self.assertIn('timeout --kill-after=30s "${OUTER_SECONDS}s" python3 scripts/blazer-compile-wiring.py run '
                      '--receipt-dir "$TASK_RUN"\n', job)
        self.assertIn('for name in preflight.json started.json completed.json; do', job)
        for name in ('Allowlisted JSON receipts only', 'Receipts artifact', "Remove this attempt's task directory"):
            self.assertIn('        if: always()\n', step(job, name))
        self.assertEqual(job.count('actions/upload-artifact@'), 1)
        own = "inputs.mode == 'launcher3-compile' || inputs.mode == 'blazer-compile' || inputs.mode == 'blazer-integrate'"
        self.assertEqual(jobs()['controller'].count(own), 2)
        self.assertEqual(jobs()['cleanup'].count(own), 1)
        self.assertIn('launcher3-compile, blazer-compile, blazer-integrate, app-builder', re.search(r'(?m)^    needs: (.+)$', jobs()['cleanup'])[1])
        self.assertNotIn('--stage compile', code(jobs()['blazer-prep']))  # blazer-prep never compiles


if __name__ == '__main__':
    unittest.main()
