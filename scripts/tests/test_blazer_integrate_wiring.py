"""BLAZER-INTEGRATE wiring (scripts/blazer-integrate-wiring.py): the hosted check exits 4 while the committed binding is
null; a bound binding runs exactly seed, assemble, admit, render, a local candidate worktree and the reconcile command
render printed, in order, stopping at the first failure; render may print only the fixed reconcile shape; only the
allowlisted reports are uploaded. A fake runner stands in for every process: no host, git, reconcile or tree change."""
import hashlib
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
spec = importlib.util.spec_from_file_location('blazer_integrate_wiring', ROOT/'scripts/blazer-integrate-wiring.py')
wiring = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wiring)
BINDING = json.loads((ROOT/'os/build-only/blazer-integrate-host.json').read_text())
RECIPE = 'a' * 40


class Result:
    def __init__(self, code=0, out=''):
        self.returncode, self.stdout, self.stderr = code, out, ''


class Wiring(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.home = self.tmp/'home'
        (self.home/'rose-actions').mkdir(parents=True)
        self.tool = {}
        for name in ('aapt', 'apksigner'):
            path = self.tmp/name
            path.write_bytes(f'#!/bin/sh\n# {name}\n'.encode())
            self.tool[name] = {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
        self.bound = {**BINDING, 'sourceRun': '41554911849',
                      'sourceInventory': {'path': str(self.tmp/'inventory.json'), 'sha256': 'b' * 64},
                      'appsDir': str(self.tmp/'signed'), 'policy': str(self.tmp/'policy.json'), **self.tool}

    def binding(self, **change):
        path = self.tmp/'binding.json'
        path.write_text(json.dumps({**self.bound, **change}))
        return path

    def resolved(self, **change):
        plan, _, _ = wiring.resolve(self.binding(**change))
        return {**plan, 'home': self.home}

    def test_the_committed_binding_is_unbound_and_exits_4(self):
        result = subprocess.run([sys.executable, str(ROOT/'scripts/blazer-integrate-wiring.py'), 'check'],
                                capture_output=True, text=True)
        self.assertEqual((result.returncode, result.stdout), (4, ''))
        self.assertIn('appsDir, policy, aapt.sha256, apksigner.path, apksigner.sha256', result.stderr)
        self.assertEqual((BINDING['sourceRun'], BINDING['sourceInventory']),
                         ('41554911849', {'path': '/home/pixelbuild/rose-actions/blazer-prep-41473133543-1/source.selection.tsv',
                                          'sha256': '3c0f9af1cf2f931a6a6c238dc9297223730ca1477d8473b0853f72e2ad388c7d'}))
        self.assertEqual(BINDING['aapt']['path'], '/home/pixelbuild/grapheneos-2026100600/prebuilts/sdk/tools/linux/bin/aapt')
        for gone in ('build', 'integrationDir', 'workDir'):
            self.assertNotIn(gone, BINDING)  # derived from the run's own reservation, never committed
        self.assertIn('EXPECTED identities', BINDING['basis'])
        self.assertIn('never observed current state', BINDING['basis'])

    def test_bounds(self):
        _, outer, minutes = wiring.resolve(self.binding())
        self.assertEqual((outer, minutes), (6480, 119))

    def test_invalid_bindings_refuse(self):
        source = json.loads((ROOT/'os/build-only/blazer-prep-host.json').read_text())['sourceRoot']
        production = '/home/pixelbuild/grapheneos-2026100600'
        for change in ({'sourceRun': '0'}, {'sourceInventory': {'path': '/i', 'sha256': 'X'}},
                       {'appsDir': production + '/out/apps'}, {'policy': production + '/prebuilts/sdk/p.json'},
                       {'aapt': {'path': production + '/out/host/aapt', 'sha256': 'a' * 64}},
                       {'apksigner': {'path': production, 'sha256': 'a' * 64}}, {'aapt': {'path': '/a', 'sha256': 'A' * 64}},
                       {'aapt': '/opt/sdk/aapt'}, {'appsDir': 'relative'}, {'build': '2026110301'},
                       {'frankelReceiptsFrom': 'os/build-only/rango.json host.reconcileReceiptDir'}, {'extra': 1}):
            with self.subTest(change=change), self.assertRaises(wiring.Invalid):
                wiring.resolve(self.binding(**change))

    def test_read_only_sdk_tools_may_sit_inside_production_only_below_prebuilts_sdk(self):
        production = '/home/pixelbuild/grapheneos-2026100600'
        plan, _, _ = wiring.resolve(self.binding(aapt={'path': production + '/prebuilts/sdk/tools/linux/bin/aapt', 'sha256': 'a' * 64}))
        self.assertEqual(str(plan['tools']['aapt']['path']), production + '/prebuilts/sdk/tools/linux/bin/aapt')
        for bad in ('/prebuilts/other/aapt', '/prebuilts/sdkx/aapt', '/prebuilts/sdk'):
            with self.subTest(bad=bad), self.assertRaises(wiring.Invalid):
                wiring.resolve(self.binding(aapt={'path': production + bad, 'sha256': 'a' * 64}))

    def fake(self, fail=None, printed=None, allocation=None):
        calls = []

        def runner(argv, **kwargs):
            calls.append(argv)
            if len(argv) > 2 and argv[2] == 'reserve':
                if fail == 'allocate':
                    return Result(1, json.dumps({'verdict': 'REFUSED'}))
                build = '2026110301'
                record = allocation or {'device': 'blazer', 'build': build, 'source': RECIPE,
                                        'workDir': str(self.home/'rose-actions'/f'blazer-integrate-{build}'),
                                        'integrationDir': str(self.home/'rose-actions'/f'blazer-reconcile-{build}')}
                Path(argv[argv.index('--out') + 1]).write_text(json.dumps(record))
                self.plan = {**self.plan, 'build': build, 'workDir': Path(record['workDir']),
                             'integrationDir': Path(record['integrationDir'])}
                return Result(0, json.dumps({'verdict': 'RESERVED', 'build': build}))
            name = argv[2] if argv[0] == sys.executable and len(argv) > 2 else argv[0]
            step = {'seed': 'seed', 'assemble': 'assemble', 'admit': 'admit', 'render': 'render'}.get(name)
            if argv[:2] == ['git', '-C'] and 'worktree' in argv:
                step = 'candidate'
            if argv[0] == 'flock':
                step = 'reconcile'
            if fail and step == fail:
                return Result(1, json.dumps({'verdict': 'REFUSED'}))
            if step == 'admit':
                inputs = self.plan['workDir']/'inputs'
                inputs.mkdir(parents=True)
                (inputs/'local-inputs.json').write_text(json.dumps({'policy': {'path': str(inputs/'background-policy.json')},
                                                                    'signedApps': str(inputs/'signed-apps')}))
            if step == 'render':
                command = printed or wiring.expected_reconcile(self.plan, RECIPE)
                return Result(0, json.dumps({'verdict': 'OK', 'command': command}))
            if argv[:3] == ['git', '-C', str(self.plan['workDir']/f'product-{RECIPE[:12]}')]:
                return Result(0, RECIPE + '\n')
            return Result(0, json.dumps({'verdict': 'OK', 'step': step}))
        return runner, calls

    def test_the_six_steps_run_exactly_in_order(self):
        self.plan = self.resolved()
        runner, calls = self.fake()
        status = wiring.run(self.plan, self.tmp/'run', '41700000000', '1', runner=runner, recipe=RECIPE)
        self.assertEqual(status, 0)
        names = [c[2] if c[0] == sys.executable else c[0] for c in calls]
        self.assertEqual(names, ['reserve', 'seed', 'assemble', 'admit', 'render', 'git', 'git', 'flock'])
        reserve, seed, assemble = calls[0], calls[1], calls[2]
        self.assertEqual(reserve[3:], ['--home', str(self.home), '--run-id', '41700000000', '--run-attempt', '1', '--source', RECIPE,
                                       '--out', str(self.tmp/'run/allocation.json')])
        self.assertEqual(seed[3:], ['--frankel-receipts', '/home/pixelbuild/rose-actions/reconcile', '--source-root',
                                    str(self.plan['source']), '--source-run', '41554911849', '--source-inventory',
                                    self.bound['sourceInventory']['path'], '--source-inventory-sha256', 'b' * 64,
                                    '--receipt-dir', str(self.home/'rose-actions/blazer-reconcile-2026110301')])
        self.assertEqual(assemble[assemble.index('--build') + 1], '2026110301')
        self.assertEqual(assemble[assemble.index('--product') + 1], RECIPE)
        self.assertEqual(calls[-1], wiring.expected_reconcile(self.plan, RECIPE))
        self.assertEqual(calls[-1][:5], ['flock', '--nonblock', '--conflict-exit-code', '75', '/home/pixelbuild/frankel-build.lock'])
        record = json.loads((self.tmp/'run/integrate.json').read_text())
        self.assertEqual((record['result'], [s['step'] for s in record['steps']]),
                         ('integrated', ['tools', 'allocate', 'seed', 'assemble', 'admit', 'render', 'candidate', 'reconcile']))
        self.assertEqual(record['build'], '2026110301')

    def test_the_first_failure_stops_everything_after_it(self):
        for fail in ('allocate', 'seed', 'assemble', 'admit', 'render', 'candidate', 'reconcile'):
            with self.subTest(fail=fail):
                self.setUp()
                self.plan = self.resolved()
                runner, calls = self.fake(fail=fail)
                self.assertEqual(wiring.run(self.plan, self.tmp/'run', '41700000000', '1', runner=runner, recipe=RECIPE), 1)
                record = json.loads((self.tmp/'run/integrate.json').read_text())
                self.assertEqual((record['result'], record['steps'][-1]['step'], record['steps'][-1]['exit']), ('stopped', fail, 1))
                if fail != 'reconcile':
                    self.assertFalse([c for c in calls if c[0] == 'flock'])

    def test_render_may_print_only_the_fixed_reconcile_shape(self):
        self.plan = self.resolved()
        runner, calls = self.fake(printed=['bash', '-c', 'anything'])
        self.assertEqual(wiring.run(self.plan, self.tmp/'run', '41700000000', '1', runner=runner, recipe=RECIPE), 1)
        self.assertFalse([c for c in calls if c[0] in ('flock', 'bash')])
        self.assertIn('fixed shape', json.loads((self.tmp/'run/integrate.json').read_text())['steps'][-1]['problem'])

    def test_an_existing_derived_directory_or_a_foreign_allocation_stops_before_seed(self):
        self.plan = self.resolved()
        (self.home/'rose-actions/blazer-integrate-2026110301').mkdir()
        runner, calls = self.fake()
        self.assertEqual(wiring.run(self.plan, self.tmp/'run', '41700000000', '1', runner=runner, recipe=RECIPE), 1)
        self.assertEqual([c[2] for c in calls], ['reserve'])
        self.setUp()
        self.plan = self.resolved()
        runner, calls = self.fake(allocation={'device': 'blazer', 'build': '2026110301', 'source': RECIPE,
                                              'workDir': '/elsewhere', 'integrationDir': '/other'})
        with self.assertRaises(wiring.Invalid):
            wiring.run(self.plan, self.tmp/'run', '41700000000', '1', runner=runner, recipe=RECIPE)
        self.assertEqual([c[2] for c in calls], ['reserve'])

    def test_a_tool_that_is_not_its_pinned_bytes_stops_before_allocation(self):
        Path(self.tool['aapt']['path']).write_bytes(b'changed')
        self.plan = self.resolved()
        runner, calls = self.fake()
        self.assertEqual(wiring.run(self.plan, self.tmp/'run', '41700000000', '1', runner=runner, recipe=RECIPE), 1)
        self.assertEqual(calls, [])
        record = json.loads((self.tmp/'run/integrate.json').read_text())
        self.assertEqual(record['steps'], [{'step': 'tools', 'exit': 1, 'result': {'aapt': False, 'apksigner': True}}])

    def test_report_uploads_only_the_allowlist(self):
        self.plan = self.resolved()
        runner, _ = self.fake()
        wiring.run(self.plan, self.tmp/'run', '41700000000', '1', runner=runner, recipe=RECIPE)
        integration = self.plan['integrationDir']
        integration.mkdir()
        (integration/'0001-2026103101.json').write_text('{}')
        (integration/'0002-blazer-2026110350.json').write_text('{"label": "blazer-2026110350"}')
        (integration/'seed-provenance.json').write_text('{}')
        (self.plan['workDir']/'reconcile-config.json').write_text('{}')
        (self.plan['workDir']/'secret.txt').write_text('never')
        copied = wiring.report(self.plan, self.tmp/'run', self.tmp/'up')
        self.assertEqual(sorted(copied), ['allocation.json', 'integrate.json', 'local-inputs.json', 'newest-reconcile-receipt.json',
                                          'reconcile-config.json', 'seed-provenance.json'])
        self.assertEqual(json.loads((self.tmp/'up/newest-reconcile-receipt.json').read_text())['label'], 'blazer-2026110350')


WORKFLOW = (ROOT/'.github/workflows/os-release.yml').read_text()


def jobs():
    body = WORKFLOW.split('\njobs:\n', 1)[1]
    return dict(re.findall(r'(?ms)^  ([\w-]+):\n(.*?)(?=^  [\w-]+:\n|\Z)', body))


class Workflow(unittest.TestCase):
    def test_mode_check_job_and_ownership(self):
        self.assertIn('blazer-compile, blazer-integrate]\n', WORKFLOW)
        self.assertIn("      - name: Blazer integrate check\n        id: blazer-integrate\n        if: inputs.mode == "
                      "'blazer-integrate'\n        run: python3 scripts/blazer-integrate-wiring.py check >> \"$GITHUB_OUTPUT\"\n",
                      jobs()['guard'])
        job = jobs()['blazer-integrate']
        code = '\n'.join(l for l in job.splitlines() if not l.strip().startswith('#'))
        self.assertEqual(code.split('    steps:\n', 1)[0], "    needs: [guard, controller]\n    if: inputs.mode == "
            "'blazer-integrate'\n    runs-on: [self-hosted, roseos-frankel-builder]\n"
            "    timeout-minutes: ${{ fromJSON(needs.guard.outputs.blazer_integrate_job_minutes || '1') }}\n"
            "    permissions:\n      contents: read\n    env:\n"
            "      OUTER_SECONDS: ${{ needs.guard.outputs.blazer_integrate_outer_seconds }}\n")
        for absent in ('id-token', 'secrets.', 'actions-gcp-login', 'GCP_', 'publish', 'host-sign', 'rm -rf', 'download-artifact'):
            self.assertNotIn(absent, code)
        self.assertEqual(sorted(set(re.findall(r'python3 (\S+)', code))), ['scripts/blazer-integrate-wiring.py'])
        self.assertIn('timeout --kill-after=30s "${OUTER_SECONDS}s" python3 scripts/blazer-integrate-wiring.py run --run-dir "$run_dir" '
                      '--run-id "$GITHUB_RUN_ID" --run-attempt "$GITHUB_RUN_ATTEMPT"', job)
        self.assertEqual(job.count('actions/upload-artifact@'), 1)
        for name in ('Allowlisted reports', 'Reports artifact'):
            self.assertIn(f'      - name: {name}\n        if: always()\n', job)
        own = "inputs.mode == 'blazer-compile' || inputs.mode == 'blazer-integrate'"
        self.assertEqual(jobs()['controller'].count(own), 2)
        self.assertEqual(jobs()['cleanup'].count(own), 1)
        self.assertIn('blazer-compile, blazer-integrate, app-builder', re.search(r'(?m)^    needs: (.+)$', jobs()['cleanup'])[1])


if __name__ == '__main__':
    unittest.main()
