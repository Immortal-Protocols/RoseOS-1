"""LAUNCHER3-COMPILE workflow wiring (.github/workflows/os-release.yml mode launcher3-compile): manual only, the hosted
guard admits the bare mode and runs the adapter's check (an unbound input of the bound branch exits 4 before the
controller), the builder job re-checks the bounds, runs one bounded adapter run and always finishes (allowlisted reports,
this run's own cleanup), and the owned-start controller/cleanup covers it. Text and stand-ins only: no host, lock, copy,
lunch or build is run."""
import importlib.util
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import os_release_shape as shape  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = (ROOT/'.github/workflows/os-release.yml').read_text()
MODE = 'launcher3-compile'
spec = importlib.util.spec_from_file_location('launcher3_compile', ROOT/'scripts/launcher3-compile.py')
adapter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adapter)


def jobs():
    body = WORKFLOW.split('\njobs:\n', 1)[1]
    return dict(re.findall(r'(?ms)^  ([\w-]+):\n(.*?)(?=^  [\w-]+:\n|\Z)', body))


def step(job, name):
    match = re.search(r'(?ms)^      - name: ' + re.escape(name) + r'\n(.*?)(?=^      - |\Z)', job)
    assert match, name
    return match[1]


def code(text):
    return '\n'.join(line for line in text.splitlines() if not line.strip().startswith('#'))


class Guard(unittest.TestCase):
    def guard(self, **change):
        body = step(jobs()['guard'], 'Candidate is an exact commit on main')
        lines = []
        for line in re.search(r'(?ms)^        run: \|\n(.*)', body)[1].splitlines():
            if line.strip() and not line.startswith(' ' * 10):
                break
            lines.append(line[10:])
        # The bare dispatch's workflow env (every input at its declared default), plus the guard step's own env.
        env = shape.dispatch_env(**{'PATH': os.environ['PATH'], 'MODE': MODE, 'PROMOTE': 'false', 'APP_PUBLISH': 'false',
                                    'ALPHA_RECOVER': 'false', 'GITHUB_SHA': 'f' * 40, **change})
        return subprocess.run(['bash', '-c', 'set -euo pipefail\n' + '\n'.join(lines) + '\n'], env=env, cwd=ROOT,
                              capture_output=True).returncode

    def test_guard_admits_only_the_bare_mode(self):
        self.assertEqual(self.guard(), 0)
        for change in ({'CANDIDATE_SHA': 'a' * 40}, {'BUILD_NUMBER': '2026103001'}, {'APPS': '[["dev.pixel.voice",1]]'},
                       {'PROMOTE': 'true'}, {'CAMPAIGN': 'start'}, {'APP_PUBLISH': 'true'}, {'ROSE_RANGO_PUBLISH': 'true'},
                       {'BOOTSTRAP_PROFILE': 'fold-rango-factory-v1'}, {'ROSE_DEVICE': 'rango'}, {'BLAZER_PREP_STAGE': 'source'},
                       {'RESUME_STAGE': 'sign'}, {'REUSE_BINDING': '{}'}, {'ALPHA_RECOVER': 'true'}, {'ROSE_RECOVER_RUN': '1'}):
            with self.subTest(change=change):
                self.assertNotEqual(self.guard(**change), 0)

    def test_the_hosted_check_is_the_adapter_and_the_committed_binding_stops_before_any_host(self):
        self.assertIn(MODE, shape.modes())
        self.assertIn("      - name: Launcher3 compile check\n        id: launcher3-compile\n        if: inputs.mode == "
                      "'launcher3-compile'\n        run: python3 scripts/launcher3-compile.py check >> \"$GITHUB_OUTPUT\"\n",
                      jobs()['guard'])
        for name in ('outer_seconds', 'finish_seconds', 'job_minutes'):
            self.assertIn(f'      launcher3_{name}: ${{{{ steps.launcher3-compile.outputs.{name} }}}}\n', jobs()['guard'])
        result = subprocess.run([sys.executable, str(ROOT/'scripts/launcher3-compile.py'), 'check'], capture_output=True, text=True)
        self.assertEqual((result.returncode, result.stdout), (4, ''))  # the controller never starts while unbound
        self.assertIn('    needs: guard\n', jobs()['controller'])


class Job(unittest.TestCase):
    def test_job_header_permissions_and_commands(self):
        job = jobs()[MODE]
        self.assertEqual(code(job.split('    steps:\n', 1)[0]), "    needs: [guard, controller]\n    if: inputs.mode == "
            "'launcher3-compile'\n    runs-on: [self-hosted, roseos-frankel-builder]\n"
            "    timeout-minutes: ${{ fromJSON(needs.guard.outputs.launcher3_job_minutes || '1') }}\n"
            "    permissions:\n      contents: read\n    env:\n"
            "      OUTER_SECONDS: ${{ needs.guard.outputs.launcher3_outer_seconds }}\n"
            "      FINISH_SECONDS: ${{ needs.guard.outputs.launcher3_finish_seconds }}")
        body = code(job)
        for absent in ('id-token', 'secrets.', 'actions-gcp-login', 'GCP_', 'PIXEL_', 'publish', 'host-sign', 'rm -rf',
                       'download-artifact', 'apply-os-changes', 'launcher3-compile-contract', 'reconstruct'):
            self.assertNotIn(absent, body)
        self.assertEqual(sorted(set(re.findall(r'python3 (\S+)', body))), ['scripts/launcher3-compile.py'])
        self.assertIn('timeout --kill-after=30s "${OUTER_SECONDS}s" python3 scripts/launcher3-compile.py run '
                      '--run-id "$GITHUB_RUN_ID" --attempt "$GITHUB_RUN_ATTEMPT"\n', job)
        self.assertIn('timeout --kill-after=30s "${FINISH_SECONDS}s" python3 scripts/launcher3-compile.py finish '
                      '--run-id "$GITHUB_RUN_ID" --attempt "$GITHUB_RUN_ATTEMPT" --upload-dir '
                      '"$RUNNER_TEMP/launcher3-compile-upload-$GITHUB_RUN_ID-$GITHUB_RUN_ATTEMPT"\n', job)
        for name in ('Allowlisted reports and this run\'s cleanup', 'Reports artifact'):
            self.assertIn('        if: always()\n', step(job, name))
        self.assertNotIn('if: always()', step(job, 'Compile (bounded, under the shared lock)'))
        self.assertEqual(job.count('actions/upload-artifact@'), 1)
        self.assertIn('          path: ${{ runner.temp }}/launcher3-compile-upload-${{ github.run_id }}-${{ github.run_attempt }}\n'
                      '          retention-days: 7\n', job)

    def test_the_recheck_step_refuses_drifted_bounds(self):
        body = re.search(r'(?ms)^        run: \|\n(.*)', step(jobs()[MODE], 'Recheck the contract, binding and bounds on the builder'))[1]
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

    def test_guard_outputs_match_the_adapter_bounds(self):
        # every lunch is the contract's (definitions.lunch): its inputs and the admitted fetch come before lunch and m;
        # the candidate branch checks its tree pins twice (base-exact, final-exact)
        for pins, expected in ((1, (21720, 1800, 403)), (2, (23520, 1800, 433))):
            outer = (adapter.PREPARE_SECONDS + adapter.KILL_GRACE_SECONDS + pins * adapter.PIN_SECONDS
                     + adapter.LUNCH_INPUT_SECONDS + adapter.ADEVTOOL_FETCH_SECONDS + adapter.KILL_GRACE_SECONDS
                     + adapter.LUNCH_SECONDS + adapter.KILL_GRACE_SECONDS + adapter.COMPILE_SECONDS + adapter.KILL_GRACE_SECONDS + 600)
            minutes = -(-(outer + adapter.FINISH_SECONDS + adapter.KILL_GRACE_SECONDS) // 60) + 10
            self.assertEqual((outer, adapter.FINISH_SECONDS, minutes), expected)


class Ownership(unittest.TestCase):
    def test_owned_start_cleanup_covers_this_mode_and_joins_cleanup(self):
        # owned_modes() also requires the controller's plain/owned start and ownership record and cleanup's plain/owned
        # stop to name the same modes, so this mode is excluded from both plain steps and covered by all owned ones.
        self.assertIn(MODE, shape.owned_modes())
        self.assertIn(MODE, shape.cleanup_needs())
        for name, job in jobs().items():
            if name != MODE:
                self.assertNotIn('launcher3-compile.py', job.replace("python3 scripts/launcher3-compile.py check", '') if name == 'guard' else job)


if __name__ == '__main__':
    unittest.main()
