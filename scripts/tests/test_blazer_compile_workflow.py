"""BLAZER-COMPILE workflow wiring (scripts/blazer-compile-wiring.py, os-release.yml mode blazer-compile): the hosted
check exits 4 while the compile binding is null, a bound binding resolves exactly Flavors' accepted compile argv with
the recipe's own bounds, and the job is read-only, bounded and uploads only the three receipts. Text and synthetic
bindings only: no host, lock, integration or build."""
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
BOUND = {'jobs': 64, 'integrationDir': '/home/pixelbuild/rose-actions/blazer-reconcile',
         'inputsReceipt': '/home/pixelbuild/rose-actions/blazer-inputs/local-inputs.json'}


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
    def test_the_committed_binding_is_unbound_and_exits_4(self):
        self.assertEqual([BINDING[k] for k in ('jobs', 'integrationDir', 'inputsReceipt')], [None, None, None])
        result = subprocess.run([sys.executable, str(ROOT/'scripts/blazer-compile-wiring.py'), 'check'],
                                capture_output=True, text=True)
        self.assertEqual((result.returncode, result.stdout), (4, ''))
        self.assertIn('jobs, integrationDir, inputsReceipt', result.stderr)

    def test_a_bound_binding_resolves_the_accepted_argv_and_recipe_bounds(self):
        tail, outer, minutes = wiring.resolve(binding())
        self.assertEqual(tail, ['--stage', 'compile', '--host', 'immortal-os-builder', '--lock', '/home/pixelbuild/frankel-build.lock',
                                '--source-root', SOURCE, '--jobs', '64', '--integration-dir', BOUND['integrationDir'],
                                '--inputs-receipt', BOUND['inputsReceipt']])
        recipe = json.loads((ROOT/'os/build-only/blazer.json').read_text())
        self.assertEqual((outer, minutes), (recipe['compile']['timeoutSeconds'] + 300, -(-(recipe['compile']['timeoutSeconds'] + 330) // 60) + 10))
        self.assertEqual((outer, minutes), (29100, 496))
        helper = (ROOT/'scripts/blazer-build-only.py').read_text()
        for flag in ('--stage', '--host', '--lock', '--source-root', '--jobs', '--integration-dir', '--inputs-receipt', '--receipt-dir'):
            self.assertIn(f"parser.add_argument('{flag}'", helper)

    def test_invalid_bindings_refuse(self):
        for change in ({'jobs': 0}, {'jobs': 257}, {'jobs': '64'}, {'jobs': True}, {'integrationDir': 'relative'},
                       {'inputsReceipt': '/home/pixelbuild/rose-actions/blazer-inputs/other.json'},
                       {'integrationDir': SOURCE + '/reconcile'}, {'integrationDir': '/home/pixelbuild/grapheneos-2026100600/x'},
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
