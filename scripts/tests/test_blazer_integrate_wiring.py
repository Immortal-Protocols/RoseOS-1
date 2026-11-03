"""BLAZER-INTEGRATE wiring (scripts/blazer-integrate-wiring.py): the hosted check exits 4 while the committed binding is
null; a bound binding runs exactly seed, assemble, admit, render, a local candidate worktree and the reconcile command
render printed, in order, stopping at the first failure; render may print only the fixed reconcile shape; only the
allowlisted reports are uploaded. A fake runner stands in for every process: no host, git, reconcile or tree change."""
import importlib.util
import json
import os
from pathlib import Path
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
        self.bound = {**BINDING, 'sourceRun': '41554911849',
                      'sourceInventory': {'path': str(self.tmp/'inventory.json'), 'sha256': 'b' * 64},
                      'appsDir': str(self.tmp/'signed'), 'policy': str(self.tmp/'policy.json'), 'aapt': '/opt/sdk/aapt',
                      'apksigner': '/opt/sdk/apksigner', 'build': '2026110350', 'integrationDir': str(self.tmp/'reconcile'),
                      'workDir': str(self.tmp/'work')}

    def binding(self, **change):
        path = self.tmp/'binding.json'
        path.write_text(json.dumps({**self.bound, **change}))
        return path

    def test_the_committed_binding_is_unbound_and_exits_4(self):
        result = subprocess.run([sys.executable, str(ROOT/'scripts/blazer-integrate-wiring.py'), 'check'],
                                capture_output=True, text=True)
        self.assertEqual((result.returncode, result.stdout), (4, ''))
        self.assertIn('sourceInventory.path, appsDir, policy, aapt, apksigner, build, integrationDir, workDir', result.stderr)
        self.assertEqual((BINDING['sourceRun'], BINDING['sourceInventory']['sha256']),
                         ('41554911849', '3c0f9af1cf2f931a6a6c238dc9297223730ca1477d8473b0853f72e2ad388c7d'))
        self.assertIn('EXPECTED identities', BINDING['basis'])
        self.assertIn('never observed current state', BINDING['basis'])

    def test_bounds(self):
        _, outer, minutes = wiring.resolve(self.binding())
        self.assertEqual((outer, minutes), (6000, 111))

    def test_invalid_bindings_refuse(self):
        source = json.loads((ROOT/'os/build-only/blazer-prep-host.json').read_text())['sourceRoot']
        for change in ({'build': '123'}, {'sourceRun': '0'}, {'sourceInventory': {'path': '/i', 'sha256': 'X'}},
                       {'workDir': source + '/work'}, {'integrationDir': '/home/pixelbuild/grapheneos-2026100600/r'},
                       {'workDir': str(self.tmp/'reconcile/work')}, {'appsDir': 'relative'},
                       {'frankelReceiptsFrom': 'os/build-only/rango.json host.reconcileReceiptDir'}, {'extra': 1}):
            with self.subTest(change=change), self.assertRaises(wiring.Invalid):
                wiring.resolve(self.binding(**change))

    def fake(self, fail=None, printed=None):
        calls = []

        def runner(argv, **kwargs):
            calls.append(argv)
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
        self.plan, _, _ = wiring.resolve(self.binding())
        runner, calls = self.fake()
        status = wiring.run(self.plan, self.tmp/'run', runner=runner, recipe=RECIPE)
        self.assertEqual(status, 0)
        names = [c[2] if c[0] == sys.executable else c[0] for c in calls]
        self.assertEqual(names, ['seed', 'assemble', 'admit', 'render', 'git', 'git', 'flock'])
        seed, assemble = calls[0], calls[1]
        self.assertEqual(seed[3:], ['--frankel-receipts', '/home/pixelbuild/rose-actions/reconcile', '--source-root',
                                    str(self.plan['source']), '--source-run', '41554911849', '--source-inventory',
                                    self.bound['sourceInventory']['path'], '--source-inventory-sha256', 'b' * 64,
                                    '--receipt-dir', self.bound['integrationDir']])
        self.assertEqual(assemble[assemble.index('--product') + 1], RECIPE)
        self.assertEqual(calls[-1], wiring.expected_reconcile(self.plan, RECIPE))
        self.assertEqual(calls[-1][:5], ['flock', '--nonblock', '--conflict-exit-code', '75', '/home/pixelbuild/frankel-build.lock'])
        record = json.loads((self.tmp/'run/integrate.json').read_text())
        self.assertEqual((record['result'], [s['step'] for s in record['steps']]),
                         ('integrated', ['seed', 'assemble', 'admit', 'render', 'candidate', 'reconcile']))

    def test_the_first_failure_stops_everything_after_it(self):
        for fail in ('seed', 'assemble', 'admit', 'render', 'candidate', 'reconcile'):
            with self.subTest(fail=fail):
                self.setUp()
                self.plan, _, _ = wiring.resolve(self.binding())
                runner, calls = self.fake(fail=fail)
                self.assertEqual(wiring.run(self.plan, self.tmp/'run', runner=runner, recipe=RECIPE), 1)
                record = json.loads((self.tmp/'run/integrate.json').read_text())
                self.assertEqual((record['result'], record['steps'][-1]['step'], record['steps'][-1]['exit']), ('stopped', fail, 1))
                if fail != 'reconcile':
                    self.assertFalse([c for c in calls if c[0] == 'flock'])

    def test_render_may_print_only_the_fixed_reconcile_shape(self):
        self.plan, _, _ = wiring.resolve(self.binding())
        runner, calls = self.fake(printed=['bash', '-c', 'anything'])
        self.assertEqual(wiring.run(self.plan, self.tmp/'run', runner=runner, recipe=RECIPE), 1)
        self.assertFalse([c for c in calls if c[0] in ('flock', 'bash')])
        self.assertIn('fixed shape', json.loads((self.tmp/'run/integrate.json').read_text())['steps'][-1]['problem'])

    def test_an_existing_work_directory_refuses_before_any_step(self):
        self.plan, _, _ = wiring.resolve(self.binding())
        self.plan['workDir'].mkdir()
        runner, calls = self.fake()
        with self.assertRaises(wiring.Invalid):
            wiring.run(self.plan, self.tmp/'run', runner=runner, recipe=RECIPE)
        self.assertEqual(calls, [])

    def test_report_uploads_only_the_allowlist(self):
        self.plan, _, _ = wiring.resolve(self.binding())
        runner, _ = self.fake()
        wiring.run(self.plan, self.tmp/'run', runner=runner, recipe=RECIPE)
        integration = self.plan['integrationDir']
        integration.mkdir()
        (integration/'0001-2026103101.json').write_text('{}')
        (integration/'0002-blazer-2026110350.json').write_text('{"label": "blazer-2026110350"}')
        (integration/'seed-provenance.json').write_text('{}')
        (self.plan['workDir']/'reconcile-config.json').write_text('{}')
        (self.plan['workDir']/'secret.txt').write_text('never')
        copied = wiring.report(self.plan, self.tmp/'run', self.tmp/'up')
        self.assertEqual(sorted(copied), ['integrate.json', 'local-inputs.json', 'newest-reconcile-receipt.json',
                                          'reconcile-config.json', 'seed-provenance.json'])
        self.assertEqual(json.loads((self.tmp/'up/newest-reconcile-receipt.json').read_text())['label'], 'blazer-2026110350')


if __name__ == '__main__':
    unittest.main()
