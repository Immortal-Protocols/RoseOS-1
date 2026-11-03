"""BLAZER-COMPILE workflow wiring (scripts/blazer-compile-wiring.py, os-release.yml mode blazer-compile): the hosted
check exits 4 while jobs is null, a bound binding resolves exactly Flavors' accepted compile argv with the recipe's own
bounds, run selects the one completed integration reserved for this checkout's HEAD (none or several refuse), and the
job is read-only, bounded and uploads only the three receipts. Text, synthetic bindings and a temporary home only: no
host, lock, integration or build."""
import calendar
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
import time
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
STAMP = '%Y-%m-%dT%H:%M:%SZ'


def epoch(text):
    """UTC text as epoch seconds by an independent route (calendar.timegm), to cross-check the wiring's datetime parse."""
    return str(calendar.timegm(time.strptime(text, STAMP)))


def helper_module():
    spec = importlib.util.spec_from_file_location('blazer_build_only', ROOT/'scripts/blazer-build-only.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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
        """A blazer-allocate reservation reserved at <NN>:00:00Z of its build's day (NN = its sequence), so every
        reservation has its own time and a datetime taken from the wrong one is visible."""
        base = home/'rose-actions'
        (base/'blazer-allocations').mkdir(parents=True, exist_ok=True)
        work, reconcile = base/f'blazer-integrate-{build}', base/f'blazer-reconcile-{build}'
        reserved = f'{build[:4]}-{build[4:6]}-{build[6:8]}T{int(build[8:]):02d}:00:00Z'
        record = {'schema': 'roseos.blazer-allocation.v1', 'device': 'blazer', 'build': build, 'source': source, 'runId': '1',
                  'runAttempt': '1', 'reservedAt': reserved, 'workDir': str(work),
                  'integrationDir': str(reconcile), 'observed': {}}
        (base/'blazer-allocations'/f'{build}.json').write_text(json.dumps(record))
        if inputs:
            (work/'inputs').mkdir(parents=True)
            (work/'inputs/local-inputs.json').write_text(json.dumps({'candidate': {'build': build}}))
        if applied:
            reconcile.mkdir()
            (reconcile/'0001-2026103101.json').write_text('{}')
            (reconcile/f'0002-blazer-{build}.json').write_text('{}')
        return {'integrationDir': str(reconcile), 'inputsReceipt': str(work/'inputs/local-inputs.json'),
                'buildNumber': build, 'buildDatetime': epoch(reserved)}  # this reservation's own BUILD_DATETIME

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
        reconcile = self.reserve(home, '2026110301')['integrationDir']
        os.rename(reconcile, reconcile + '-real')
        os.symlink(reconcile + '-real', reconcile)
        with self.assertRaises(wiring.Refused):
            wiring.integration(RECIPE, source, home)  # a linked integration directory never counts
        home = Path(tempfile.mkdtemp())
        self.reserve(home, '2026110301')
        (home/'rose-actions/blazer-allocations/2026110301.json').write_text('{"build": "2026110301"}')
        with self.assertRaises(wiring.Refused):
            wiring.integration(RECIPE, source, home)  # an unrecognised reservation refuses

    def test_the_selected_reservation_gives_the_build_identity(self):
        self.assertEqual(wiring.build_datetime('2026-11-03T15:00:00Z'), epoch('2026-11-03T15:00:00Z'))
        self.assertEqual(wiring.build_datetime('2026-11-03T15:00:00Z'), '1793718000')
        # UTC whatever the runner's zone: a naive (local-time) parse would shift by the zone offset.
        saved = os.environ.get('TZ')
        self.addCleanup(time.tzset)
        self.addCleanup(lambda: os.environ.__setitem__('TZ', saved) if saved is not None else os.environ.pop('TZ', None))
        for zone in ('America/Los_Angeles', 'Asia/Kolkata', 'UTC'):
            with self.subTest(zone=zone):
                os.environ['TZ'] = zone
                time.tzset()
                self.assertEqual(wiring.build_datetime('2026-11-03T15:00:00Z'), '1793718000')
        for bad in ('2026-11-03 15:00:00', '', None, '2026-11-03T15:00:00+01:00',
                    '1970-01-01T00:00:00Z', '1969-12-31T23:59:59Z', '1000-01-01T00:00:00Z'):  # the epoch or before
            with self.subTest(bad=bad), self.assertRaises(wiring.Refused):
                wiring.build_datetime(bad)
        self.assertEqual(wiring.build_datetime('1970-01-01T00:00:01Z'), '1')
        self.assertEqual(wiring.BUILD_DATETIME.pattern, helper_module().BUILD_DATETIME.pattern)
        home = Path(tempfile.mkdtemp())
        self.reserve(home, '2026110302', source='d' * 40)  # another commit's reservation, at another time
        chosen = self.reserve(home, '2026110303')
        self.assertEqual(wiring.integration(RECIPE, Path(SOURCE), home), chosen)
        self.assertEqual((chosen['buildNumber'], chosen['buildDatetime']), ('2026110303', epoch('2026-11-03T03:00:00Z')))

    def test_run_execs_exactly_the_argv_the_helper_parses_and_admits(self):
        """main('run') end to end with only HOME, HEAD and execv replaced: the exec'd argv is exactly the documented one,
        all strings, and Flavors' real parser and operator turn it into the selected identity, which F1 admits."""
        home = Path(tempfile.mkdtemp())
        self.reserve(home, '2026110302', source='d' * 40)  # another commit's reservation, at another time
        chosen = self.reserve(home, '2026110303')
        real, captured = wiring.integration, []
        for owner, name, value in ((wiring, 'integration', lambda recipe, source: real(recipe, source, home)),
                                   (wiring, 'head', lambda root=wiring.ROOT: RECIPE),
                                   (wiring.os, 'execv', lambda path, argv: captured.append((path, list(argv))))):
            self.addCleanup(setattr, owner, name, getattr(owner, name))
            setattr(owner, name, value)
        with contextlib.redirect_stdout(io.StringIO()) as out:
            wiring.main(['run', '--receipt-dir', '/new/receipts'])
        tail = wiring.resolve(wiring.BINDING)[0]
        expected = [sys.executable, str(wiring.HELPER), 'run', *tail, '--integration-dir', chosen['integrationDir'],
                    '--inputs-receipt', chosen['inputsReceipt'], '--build-number', '2026110303',
                    '--build-datetime', epoch('2026-11-03T03:00:00Z'), '--receipt-dir', '/new/receipts']
        self.assertEqual(captured, [(sys.executable, expected)])
        self.assertTrue(all(isinstance(arg, str) for arg in expected))
        self.assertEqual(json.loads(out.getvalue()), {'helper': expected[1:]})
        helper, ran = helper_module(), []
        lexical = helper.load
        helper.load = lambda path=helper.RECIPE: lexical(path, probe=False)  # no host probe in a test
        helper.run = lambda recipe, stage, op, receipt_dir: ran.append((stage, op, receipt_dir)) or 0
        self.assertEqual(helper.main(expected[2:]), 0)
        (stage, op, receipt_dir), = ran
        self.assertEqual((stage, receipt_dir, op['buildNumber'], op['buildDatetime'], str(op['integrationDir']),
                          str(op['inputsReceipt']), op['jobs']),
                         ('compile', '/new/receipts', '2026110303', epoch('2026-11-03T03:00:00Z'), chosen['integrationDir'],
                          chosen['inputsReceipt'], BINDING['jobs']))
        problems, facts = helper.identity_problems(op, lambda: int(epoch('2026-11-03T03:00:00Z')) + 3600)
        self.assertEqual(problems, [])
        self.assertEqual(facts['reservation'], str(home/'rose-actions/blazer-allocations/2026110303.json'))

    def test_the_helper_admits_exactly_the_identity_the_wiring_selects(self):
        """Both sides' real code on the same reservation bytes: the --build-number/--build-datetime the wiring derives are
        the ones Flavors' F1 admission (identity_problems, reservation_datetime) requires, and name its OUT_DIR."""
        helper = helper_module()
        recipe = json.loads((ROOT/'os/build-only/blazer.json').read_text())
        lock = '/home/pixelbuild/frankel-build.lock'

        def admit(chosen, wall=1793718000 + 3600):  # 16:00Z, after every fixture reservation of 2026-11-03
            op = helper.operator(BINDING['host'], SOURCE, 48, lock, integration_dir=chosen['integrationDir'],
                                 inputs_receipt=chosen['inputsReceipt'], build_number=chosen['buildNumber'],
                                 build_datetime=chosen['buildDatetime'])
            return op, helper.identity_problems(op, lambda: wall)

        home = Path(tempfile.mkdtemp())
        self.reserve(home, '2026110303')
        chosen = wiring.integration(RECIPE, Path(SOURCE), home)
        op, (problems, facts) = admit(chosen)
        self.assertEqual(problems, [])
        self.assertEqual((facts['reservedAtEpoch'], facts['inputsReceiptBuild'], facts['buildDatetimeUtc']),
                         (int(epoch('2026-11-03T03:00:00Z')), '2026110303', '2026-11-03T03:00:00Z'))
        self.assertEqual(facts['reservation'], str(home/'rose-actions/blazer-allocations/2026110303.json'))
        self.assertEqual(helper.compile_out_dir(recipe, op), 'out-blazer-build-2026110303')
        # The helper refuses any other second or build, so the wiring's derivation is load-bearing, not advisory.
        for change in ({'buildDatetime': str(int(chosen['buildDatetime']) + 1)}, {'buildNumber': '2026110304'}):
            with self.subTest(change=change):
                self.assertTrue(admit({**chosen, **change})[1][0])
        # A reservation time either side cannot read is refused by both (a leap second included), never re-derived.
        path = home/'rose-actions/blazer-allocations/2026110303.json'
        record = json.loads(path.read_text())
        for value in ('2026-11-03T03:00:60Z', '2026-11-03T03:00:00+00:00'):
            with self.subTest(reservedAt=value):
                path.write_text(json.dumps({**record, 'reservedAt': value}))
                with self.assertRaises(wiring.Refused):
                    wiring.integration(RECIPE, Path(SOURCE), home)
                self.assertIn('has no UTC reservedAt', ' '.join(admit(chosen)[1][0]))
        path.write_text(json.dumps({**record, 'build': 2026110303}))
        with self.assertRaises(wiring.Refused):
            wiring.integration(RECIPE, Path(SOURCE), home)  # a numeric build is never passed as the identity

    def test_a_bound_binding_resolves_the_accepted_argv_and_recipe_bounds(self):
        tail, outer, minutes = wiring.resolve(binding())
        self.assertEqual(tail, ['--stage', 'compile', '--host', 'immortal-os-builder', '--lock', '/home/pixelbuild/frankel-build.lock',
                                '--source-root', SOURCE, '--jobs', '64'])
        recipe = json.loads((ROOT/'os/build-only/blazer.json').read_text())
        self.assertEqual((outer, minutes), (recipe['compile']['timeoutSeconds'] + 300, -(-(recipe['compile']['timeoutSeconds'] + 330) // 60) + 10))
        self.assertEqual((outer, minutes), (29100, 496))
        helper = (ROOT/'scripts/blazer-build-only.py').read_text()
        for flag in ('--stage', '--host', '--lock', '--source-root', '--jobs', '--integration-dir', '--inputs-receipt',
                     '--build-number', '--build-datetime', '--receipt-dir'):
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
