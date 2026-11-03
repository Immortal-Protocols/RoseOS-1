"""BLAZER-INTEGRATE wiring (scripts/blazer-integrate-wiring.py): the committed binding carries the identities of the
original Infra binding-observe report; every tool, tool dependency and app input is re-hashed before allocation and the
bundle copies after assemble; a bound binding runs exactly seed, assemble, admit, render, a local candidate worktree and the reconcile command
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
PRODUCTION = '/home/pixelbuild/grapheneos-2026100600'
JAVA = PRODUCTION + '/prebuilts/jdk/jdk21/linux-x86/bin/java'
PACKAGES = ('dev.pixel.backgroundcontrol', 'dev.pixel.calculator.agent', 'dev.pixel.voice')


class Result:
    def __init__(self, code=0, out=''):
        self.returncode, self.stdout, self.stderr = code, out, ''


def render_output(out, command):
    """Exactly what blazer_integration.py render prints on success (pinned against its source below)."""
    return json.dumps({'verdict': 'OK', 'config': out, 'command': command}, indent=2) + '\n'


class Wiring(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.home = self.tmp/'home'
        (self.home/'rose-actions').mkdir(parents=True)
        def pinned(path, data):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            return {'path': str(path), 'sha256': hashlib.sha256(data).hexdigest()}
        self.tool = {name: pinned(self.tmp/'sdk'/name, f'#!/bin/sh\n# {name}\n'.encode()) for name in ('aapt', 'apksigner')}
        # java sits at the fixed production JDK path; the test hasher maps that one path to a local file.
        self.java = pinned(self.tmp/'jdk-java', b'java')
        self.deps = {'apksigner.jar': pinned(self.tmp/'sdk/lib/apksigner.jar', b'jar'),
                     'java': {'path': JAVA, 'sha256': self.java['sha256']}}
        apps = {}
        for package in PACKAGES:
            for name in (f'{package}.apk', f'{package}.receipt.json'):
                apps[name] = pinned(self.tmp/'signed'/name, name.encode())['sha256']
        policy = pinned(self.tmp/'policy.json', b'{}')
        self.bound = {**BINDING, 'sourceRun': '41554911849',
                      'sourceInventory': {'path': str(self.tmp/'inventory.json'), 'sha256': 'b' * 64},
                      'appsDir': str(self.tmp/'signed'), 'policy': policy['path'], **self.tool,
                      'toolDependencies': self.deps, 'inputDigests': {'policy': policy['sha256'], 'apps': apps}}

    def hasher(self, path):
        return wiring.tool_sha256(self.java['path'] if str(path) == JAVA else path)

    def run_wiring(self, runner):
        return wiring.run(self.plan, self.tmp/'run', '41700000000', '1', runner=runner, recipe=RECIPE, hasher=self.hasher)

    def binding(self, **change):
        path = self.tmp/'binding.json'
        path.write_text(json.dumps({**self.bound, **change}))
        return path

    def resolved(self, **change):
        plan, _, _ = wiring.resolve(self.binding(**change))
        return {**plan, 'home': self.home}

    def test_the_committed_binding_carries_the_observed_report_identities(self):
        result = subprocess.run([sys.executable, str(ROOT/'scripts/blazer-integrate-wiring.py'), 'check'],
                                capture_output=True, text=True)
        self.assertEqual((result.returncode, result.stdout), (0, 'outer_seconds=6630\njob_minutes=121\n'))
        self.assertEqual(BINDING['observation'], {
            'run': '41656426913', 'runAttempt': '1', 'source': 'cf3b60e5ab83e2d9b9f5419239b35a9c8bbf3b73',
            'artifact': '13239462248', 'artifactZipSha256': 'cf3dc9838ac756c57d8f6ca44aaef99fe410a95cd4ea0740f645d6b0483b223d',
            'report': 'binding-observation.json',
            'reportSha256': '27cd95ff76f94913c8d5b227312c0218ba937ff65134ca07b4881207e543e577'})
        release = '/home/pixelbuild/rose-actions/frankel-2026110301/release'
        self.assertEqual((BINDING['appsDir'], BINDING['policy']), (release + '/signed-apps', release + '/background-policy.json'))
        self.assertEqual(BINDING['apksigner']['path'], '/home/pixelbuild/rose-actions/tools/android-sdk/build-tools/android-15/apksigner')
        self.assertEqual(sorted(BINDING['inputDigests']['apps']), sorted(f'{p}{s}' for p in PACKAGES for s in ('.apk', '.receipt.json')))
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
        self.assertEqual((outer, minutes), (6630, 121))

    def test_invalid_bindings_refuse(self):
        source = json.loads((ROOT/'os/build-only/blazer-prep-host.json').read_text())['sourceRoot']
        production = '/home/pixelbuild/grapheneos-2026100600'
        for change in ({'sourceRun': '0'}, {'sourceInventory': {'path': '/i', 'sha256': 'X'}},
                       {'appsDir': production + '/out/apps'}, {'policy': production + '/prebuilts/sdk/p.json'},
                       {'aapt': {'path': production + '/out/host/aapt', 'sha256': 'a' * 64}},
                       {'apksigner': {'path': production, 'sha256': 'a' * 64}}, {'aapt': {'path': '/a', 'sha256': 'A' * 64}},
                       {'aapt': '/opt/sdk/aapt'}, {'appsDir': 'relative'}, {'build': '2026110301'},
                       {'frankelReceiptsFrom': 'os/build-only/rango.json host.reconcileReceiptDir'}, {'extra': 1},
                       {'toolDependencies': {**self.deps, 'java': {'path': PRODUCTION + '/prebuilts/jdk/jdk17/bin/java',
                                                                   'sha256': 'a' * 64}}},
                       {'toolDependencies': {**self.deps, 'apksigner.jar': {'path': '/opt/apksigner.jar', 'sha256': 'a' * 64}}},
                       {'toolDependencies': {**self.deps, 'extra': self.deps['java']}},
                       {'inputDigests': {**self.bound['inputDigests'], 'apps': {'dev.pixel.voice.apk': 'a' * 64}}},
                       {'inputDigests': {**self.bound['inputDigests'], 'policy': 'A' * 64}},
                       {'observation': {**BINDING['observation'], 'reportSha256': None}},
                       {'observation': {**BINDING['observation'], 'report': 'other.json'}}):
            with self.subTest(change=change), self.assertRaises(wiring.Invalid):
                wiring.resolve(self.binding(**change))

    def test_missing_dependencies_or_input_digests_are_unbound(self):
        for change in ({'toolDependencies': None}, {'inputDigests': None},
                       {'toolDependencies': {**self.deps, 'java': {'path': JAVA, 'sha256': None}}}):
            with self.subTest(change=change), self.assertRaises(wiring.Unbound):
                wiring.resolve(self.binding(**change))

    def test_read_only_sdk_tools_may_sit_inside_production_only_below_prebuilts_sdk(self):
        production = '/home/pixelbuild/grapheneos-2026100600'
        plan, _, _ = wiring.resolve(self.binding(aapt={'path': production + '/prebuilts/sdk/tools/linux/bin/aapt', 'sha256': 'a' * 64}))
        self.assertEqual(str(plan['tools']['aapt']['path']), production + '/prebuilts/sdk/tools/linux/bin/aapt')
        for bad in ('/prebuilts/other/aapt', '/prebuilts/sdkx/aapt', '/prebuilts/sdk'):
            with self.subTest(bad=bad), self.assertRaises(wiring.Invalid):
                wiring.resolve(self.binding(aapt={'path': production + bad, 'sha256': 'a' * 64}))

    def fake(self, fail=None, printed=None, allocation=None, tamper=None, render_out=None):
        calls = []
        self.tamper = tamper
        self.envs = []

        def runner(argv, **kwargs):
            calls.append(argv)
            self.envs.append(kwargs.get('env'))
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
            if step == 'assemble':
                bundle = self.plan['workDir']/'bundle'
                (bundle/'signed-apps').mkdir(parents=True)
                for name in self.bound['inputDigests']['apps']:
                    (bundle/'signed-apps'/name).write_bytes(name.encode() if name != self.tamper else b'other')
                (bundle/'background-policy.json').write_bytes(b'{}')
            if step == 'admit':
                inputs = self.plan['workDir']/'inputs'
                inputs.mkdir(parents=True)
                (inputs/'local-inputs.json').write_text(json.dumps({'policy': {'path': str(inputs/'background-policy.json')},
                                                                    'signedApps': str(inputs/'signed-apps')}))
            if step == 'render':
                if render_out is not None:
                    return Result(0, render_out())
                command = printed or wiring.expected_reconcile(self.plan, RECIPE)
                return Result(0, render_output(argv[argv.index('--out') + 1], command))
            if argv[:3] == ['git', '-C', str(self.plan['workDir']/f'product-{RECIPE[:12]}')]:
                return Result(0, RECIPE + '\n')
            return Result(0, json.dumps({'verdict': 'OK', 'step': step}))
        return runner, calls

    def test_the_six_steps_run_exactly_in_order(self):
        self.plan = self.resolved()
        runner, calls = self.fake()
        status = self.run_wiring(runner)
        self.assertEqual(status, 0)
        names = [c[2] if c[0] == sys.executable else c[0] for c in calls]
        self.assertEqual(names, ['reserve', 'seed', 'assemble', 'admit', 'render', 'git', 'git', 'flock'])
        reserve, seed, assemble = calls[0], calls[1], calls[2]
        self.assertEqual(reserve[3:], ['--home', str(self.home), '--run-id', '41700000000', '--run-attempt', '1', '--source', RECIPE,
                                       '--out', str(self.tmp/'run/allocation.json')])
        self.assertEqual(seed[3:], ['--frankel-receipts', '/home/pixelbuild/rose-actions/reconcile', '--source-root',
                                    str(self.plan['source']), '--source-run', '41554911849', '--source-inventory',
                                    self.bound['sourceInventory']['path'], '--source-inventory-sha256', 'b' * 64,
                                    '--receipt-dir', str(self.home/'rose-actions/blazer-reconcile-2026110301'),
                                    '--prior-integrations', str(self.home/'rose-actions')])
        self.assertEqual(assemble[assemble.index('--build') + 1], '2026110301')
        self.assertEqual(assemble[assemble.index('--product') + 1], RECIPE)
        self.assertEqual(calls[-1], wiring.expected_reconcile(self.plan, RECIPE))
        self.assertEqual(calls[-1][:5], ['flock', '--nonblock', '--conflict-exit-code', '75', '/home/pixelbuild/frankel-build.lock'])
        record = json.loads((self.tmp/'run/integrate.json').read_text())
        self.assertEqual((record['result'], [s['step'] for s in record['steps']]),
                         ('integrated', ['tools', 'inputs', 'allocate', 'seed', 'assemble', 'admit', 'render', 'candidate', 'reconcile']))
        self.assertEqual(record['build'], '2026110301')

    def test_the_bound_wrapper_execs_the_bound_java_and_jar_by_absolute_path(self):
        # host-sdk-layout.py writes exactly this wrapper; the bound sha256 is these bytes, so PATH and JAVA_HOME never
        # choose the java it runs.
        java, jar = BINDING['toolDependencies']['java']['path'], BINDING['toolDependencies']['apksigner.jar']['path']
        self.assertIn('wrapper.write_text(f\'#!/bin/sh\\nexec "{java}" -jar "{tools}/lib/apksigner.jar" "$@"\\n\')',
                      (ROOT/'scripts/host-sdk-layout.py').read_text())
        wrapper = f'#!/bin/sh\nexec "{java}" -jar "{jar}" "$@"\n'.encode()
        self.assertEqual(hashlib.sha256(wrapper).hexdigest(), BINDING['apksigner']['sha256'])

    def test_every_step_runs_without_jvm_or_loader_injection_variables(self):
        self.plan = self.resolved()
        runner, calls = self.fake()
        saved = {k: os.environ.get(k) for k in wiring.SCRUBBED}
        os.environ.update({k: 'injected' for k in wiring.SCRUBBED})
        try:
            self.assertEqual(self.run_wiring(runner), 0)
        finally:
            for k, v in saved.items():
                os.environ.pop(k) if v is None else os.environ.__setitem__(k, v)
        self.assertEqual(len(self.envs), len(calls))
        for env in self.envs:
            self.assertIsNotNone(env)
            self.assertFalse(set(wiring.SCRUBBED) & set(env))
            self.assertEqual(env.get('PATH'), os.environ.get('PATH'))

    def test_the_first_failure_stops_everything_after_it(self):
        for fail in ('allocate', 'seed', 'assemble', 'admit', 'render', 'candidate', 'reconcile'):
            with self.subTest(fail=fail):
                self.setUp()
                self.plan = self.resolved()
                runner, calls = self.fake(fail=fail)
                self.assertEqual(self.run_wiring(runner), 1)
                record = json.loads((self.tmp/'run/integrate.json').read_text())
                self.assertEqual((record['result'], record['steps'][-1]['step'], record['steps'][-1]['exit']), ('stopped', fail, 1))
                if fail != 'reconcile':
                    self.assertFalse([c for c in calls if c[0] == 'flock'])

    def test_render_may_print_only_the_fixed_reconcile_shape(self):
        self.plan = self.resolved()
        runner, calls = self.fake(printed=['bash', '-c', 'anything'])
        self.assertEqual(self.run_wiring(runner), 1)
        self.assertFalse([c for c in calls if c[0] in ('flock', 'bash')])
        self.assertIn('fixed shape', json.loads((self.tmp/'run/integrate.json').read_text())['steps'][-1]['problem'])

    def test_the_renderers_multi_line_record_is_parsed_whole(self):
        source = (ROOT/'scripts/blazer_integration.py').read_text()
        self.assertIn("print(json.dumps({'verdict': 'OK', 'config': a.out, 'command': command}, indent=2))", source)
        command = ['flock', '--nonblock', '/l', 'python3', 'host-reconcile.py', 'apply', '--label', 'blazer-2026110302']
        text = render_output('/w/reconcile-config.json', command)
        self.assertEqual(wiring.last_json(text), {'verdict': 'OK', 'config': '/w/reconcile-config.json', 'command': command})
        # run 41664975868: the old line-wise parser returned this array element of the pretty record
        self.assertEqual(text.splitlines()[-3].strip(), '"blazer-2026110302"')
        self.assertEqual(wiring.last_json('progress line\n' + json.dumps({'verdict': 'OK'})), {'verdict': 'OK'})
        for bad in (text + '"blazer-2026110302"\n', text[:-5], '"blazer-2026110302"', '42', 'null', '[1, 2]',
                    '{"verdict": "OK"}\n[1]', '', None, 'not json'):
            with self.subTest(bad=bad):
                self.assertIsNone(wiring.last_json(bad))

    def test_render_must_print_its_complete_ok_record_for_this_run(self):
        self.setUp()
        self.plan = self.resolved()
        # Evaluated when render runs, against that run's own allocated work directory.
        out = lambda: str(self.plan['workDir']/'reconcile-config.json')  # noqa: E731
        fixed = lambda: wiring.expected_reconcile(self.plan, RECIPE)  # noqa: E731
        cases = {
            'trailing scalar': lambda: render_output(out(), fixed()) + '"blazer-2026110301"\n',
            'scalar only': lambda: '"blazer-2026110301"\n',
            'truncated': lambda: render_output(out(), fixed())[:-10],
            'no config': lambda: json.dumps({'verdict': 'OK', 'command': fixed()}, indent=2),
            'extra key': lambda: json.dumps({'verdict': 'OK', 'config': out(), 'command': fixed(), 'more': 1}, indent=2),
            'not ok': lambda: json.dumps({'verdict': 'REFUSED', 'config': out(), 'command': fixed()}, indent=2),
            'other config': lambda: render_output('/elsewhere/reconcile-config.json', fixed()),
            'other command': lambda: render_output(out(), ['bash', '-c', 'anything']),
        }
        for name, output in cases.items():
            with self.subTest(case=name):
                self.setUp()
                self.plan = self.resolved()
                runner, calls = self.fake(render_out=output if name != 'other command' else None,
                                          printed=['bash', '-c', 'anything'] if name == 'other command' else None)
                self.assertEqual(self.run_wiring(runner), 1)
                self.assertFalse([c for c in calls if c[0] in ('flock', 'bash')])
                self.assertFalse([c for c in calls if 'worktree' in c])  # stopped at render, before the candidate
                step = json.loads((self.tmp/'run/integrate.json').read_text())['steps'][-1]
                self.assertEqual(step['step'], 'render')
                self.assertEqual(step['problem'], 'render printed another reconcile command than the fixed shape'
                                 if name == 'other command' else
                                 'render printed no complete OK record {verdict, config, command} for this run')

    def test_the_exact_render_record_for_this_run_reaches_the_fixed_reconcile(self):
        self.plan = self.resolved()
        runner, calls = self.fake(render_out=lambda: render_output(str(self.plan['workDir']/'reconcile-config.json'),
                                                                   wiring.expected_reconcile(self.plan, RECIPE)))
        self.assertEqual(self.run_wiring(runner), 0)
        self.assertEqual(calls[-1], wiring.expected_reconcile(self.plan, RECIPE))

    def test_an_existing_derived_directory_or_a_foreign_allocation_stops_before_seed(self):
        self.plan = self.resolved()
        (self.home/'rose-actions/blazer-integrate-2026110301').mkdir()
        runner, calls = self.fake()
        self.assertEqual(self.run_wiring(runner), 1)
        self.assertEqual([c[2] for c in calls], ['reserve'])
        self.setUp()
        self.plan = self.resolved()
        runner, calls = self.fake(allocation={'device': 'blazer', 'build': '2026110301', 'source': RECIPE,
                                              'workDir': '/elsewhere', 'integrationDir': '/other'})
        with self.assertRaises(wiring.Invalid):
            self.run_wiring(runner)
        self.assertEqual([c[2] for c in calls], ['reserve'])

    def test_a_tool_that_is_not_its_pinned_bytes_stops_before_allocation(self):
        Path(self.tool['aapt']['path']).write_bytes(b'changed')
        self.plan = self.resolved()
        runner, calls = self.fake()
        self.assertEqual(self.run_wiring(runner), 1)
        self.assertEqual(calls, [])
        record = json.loads((self.tmp/'run/integrate.json').read_text())
        self.assertEqual(record['steps'], [{'step': 'tools', 'exit': 1, 'result': {
            'aapt': False, 'apksigner': True, 'apksigner.jar': True, 'java': True}}])

    def test_a_changed_tool_dependency_or_app_input_stops_before_allocation(self):
        for changed in ('sdk/lib/apksigner.jar', 'jdk-java', 'signed/dev.pixel.voice.receipt.json', 'policy.json'):
            with self.subTest(changed=changed):
                self.setUp()
                (self.tmp/changed).write_bytes(b'x')
                self.plan = self.resolved()
                runner, calls = self.fake()
                self.assertEqual(self.run_wiring(runner), 1)
                self.assertEqual(calls, [])
                steps = json.loads((self.tmp/'run/integrate.json').read_text())['steps']
                self.assertEqual(steps[-1]['exit'], 1)
                self.assertIn(False, steps[-1]['result'].values())

    def test_bundle_copies_that_are_not_the_bound_bytes_stop_after_assemble(self):
        self.plan = self.resolved()
        runner, calls = self.fake(tamper='dev.pixel.calculator.agent.apk')
        self.assertEqual(self.run_wiring(runner), 1)
        step = json.loads((self.tmp/'run/integrate.json').read_text())['steps'][-1]
        self.assertEqual((step['step'], step['problem']), ('assemble', 'the bundle copies are not the bound input bytes'))
        self.assertEqual([c[2] for c in calls], ['reserve', 'seed', 'assemble'])

    def test_report_uploads_only_the_allowlist(self):
        self.plan = self.resolved()
        runner, _ = self.fake()
        self.run_wiring(runner)
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
