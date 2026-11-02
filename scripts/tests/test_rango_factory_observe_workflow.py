"""FOLD-OBSERVE-WIRE-001: the manual rango-factory-observe mode of .github/workflows/os-release.yml.

The workflow's own shell is executed with stand-ins (no host, network, token or helper run): the guard branch, the exact
timeout invocation, the receipt allowlist and the task cleanup. The helper itself is scripts/rango-factory-observe.py
(tested by test_rango_factory_observe.py) and is not changed here."""
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = (ROOT/'.github/workflows/os-release.yml').read_text()
MODE = 'rango-factory-observe'
RECEIPTS = ('rango-factory-observe.json', 'target-files-observation.json', 'firmware-members.json')
COMMAND = ('timeout --kill-after=5s 20m python3 scripts/rango-factory-observe.py run --run-id "$GITHUB_RUN_ID" '
           '--recipe-sha "$GITHUB_SHA" --out "$TASK_OUTPUT"')


def jobs():
    body = WORKFLOW.split('\njobs:\n', 1)[1]
    return dict(re.findall(r'(?ms)^  ([\w-]+):\n(.*?)(?=^  [\w-]+:\n|\Z)', body))


def step(job, name):
    match = re.search(r'(?ms)^      - name: ' + re.escape(name) + r'\n(.*?)(?=^      - |\Z)', job)
    assert match, name
    return match[1]


def script(step_text):
    """The run block of a step, dedented."""
    block = re.search(r'(?ms)^        run: \|\n(.*)', step_text)
    if block:
        return '\n'.join(line[10:] for line in block[1].splitlines()) + '\n'
    return re.search(r'(?m)^        run: (.+)$', step_text)[1] + '\n'


class Guard(unittest.TestCase):
    BASE = {'MODE': MODE, 'CANDIDATE_SHA': '', 'BUILD_NUMBER': '', 'BUILD_TIMESTAMP': '', 'APPS': '', 'PROMOTE': 'false',
            'RESUME_STAGE': 'none', 'RESUME_SOURCE_RUN': '', 'RESUME_UNSIGNED_SHA256': '', 'RESUME_SIGNED_SHA256': '',
            'CAMPAIGN': '', 'APP_PUBLISH': 'false', 'ROSE_RANGO_PUBLISH': 'false', 'ROSE_FIRST_USE_ENROLL': 'false',
            'ROSE_RECOVER_RUN': '', 'BOOTSTRAP_PROFILE': 'none', 'MODELS_BOOTSTRAP_RUN': '', 'REUSE_BINDING': '',
            'ALPHA_RECOVER': 'false', 'ROSE_DEVICE': 'frankel'}

    def branch(self):
        guard = jobs()['guard']
        match = re.search(r'(?ms)^          elif \[\[ "\$MODE" == rango-factory-observe \]\]; then\n(.*?)(?=^          (?:elif|else|fi))', guard)
        self.assertIsNotNone(match, 'the guard has an explicit observation branch')
        return 'set -euo pipefail\n' + '\n'.join(line[12:] for line in match[1].splitlines() if not line.strip().startswith('#')) + '\n'

    def run_branch(self, **change):
        return subprocess.run(['bash', '-c', self.branch()], env={'PATH': os.environ['PATH'], **self.BASE, **change},
            capture_output=True).returncode

    def test_only_the_bare_mode_is_admitted(self):
        self.assertEqual(self.run_branch(), 0)
        for change in ({'CANDIDATE_SHA': 'a' * 40}, {'BUILD_NUMBER': '2026103001'}, {'BUILD_TIMESTAMP': '1793500000'},
                       {'APPS': '[["dev.pixel.voice",1]]'}, {'PROMOTE': 'true'}, {'RESUME_STAGE': 'sign'},
                       {'RESUME_SOURCE_RUN': '1'}, {'RESUME_UNSIGNED_SHA256': 'a' * 64}, {'RESUME_SIGNED_SHA256': 'a' * 64},
                       {'CAMPAIGN': 'start'}, {'APP_PUBLISH': 'true'}, {'ROSE_RANGO_PUBLISH': 'true'},
                       {'ROSE_FIRST_USE_ENROLL': 'true'}, {'ROSE_RECOVER_RUN': '41000000000'},
                       {'BOOTSTRAP_PROFILE': 'models-v0'}, {'MODELS_BOOTSTRAP_RUN': '1'}, {'REUSE_BINDING': '{}'},
                       {'ALPHA_RECOVER': 'true'}, {'ROSE_DEVICE': 'blazer'}, {'ROSE_DEVICE': 'rango'}):
            with self.subTest(change=change):
                self.assertNotEqual(self.run_branch(**change), 0)

    def test_mode_is_manual_selectable_and_checked_before_the_controller(self):
        self.assertIn(f', emulator-alpha-export, {MODE}]\n', WORKFLOW)
        self.assertEqual(re.findall(r'(?m)^  (\w+):\n', WORKFLOW.split('\npermissions:', 1)[0].split('\non:\n', 1)[1]), ['workflow_dispatch'])
        guard = jobs()['guard']
        check = step(guard, 'Rango factory observation check')
        self.assertIn(f"        if: inputs.mode == '{MODE}'\n        run: python3 scripts/rango-factory-observe.py check\n", check)
        self.assertIn(f'"$MODE" == rango-factory-observe ]] || git merge-base --is-ancestor', guard)
        self.assertEqual(re.search(r'(?m)^    needs: (.+)$', jobs()['controller'])[1], 'guard')


class Job(unittest.TestCase):
    def setUp(self):
        self.job = jobs()[MODE]

    def test_isolated_read_only_bounded_job(self):
        head = self.job.split('    steps:\n', 1)[0]
        self.assertEqual(head, f"    needs: controller\n    if: inputs.mode == '{MODE}'\n"
            '    runs-on: [self-hosted, roseos-frankel-builder]\n    timeout-minutes: 22\n    permissions:\n      contents: read\n')
        for absent in ('id-token', 'secrets.', 'actions-gcp-login', 'GCP_', 'PIXEL_', 'sign', 'publish', 'blazer', 'Blazer',
                       'first-use', 'download-artifact', 'release-pipeline.py'):
            self.assertNotIn(absent, self.job.replace('signed marker', ''))
        self.assertEqual(re.findall(r'python3 (\S+)', self.job), ['scripts/rango-factory-observe.py'])
        self.assertIn('          persist-credentials: false\n', self.job)
        # Exactly one job runs the helper, only in this mode.
        self.assertEqual([n for n, body in jobs().items() if 'rango-factory-observe.py run' in body], [MODE])

    def test_exact_timeout_invocation(self):
        command = script(step(self.job, 'Observe the retained Rango build (bounded, under the shared lock)'))
        self.assertEqual(command, COMMAND + '\n')
        with tempfile.TemporaryDirectory() as tmp:
            stub = Path(tmp)/'timeout'
            stub.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$ARGV_OUT"\n')
            stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
            env = {'PATH': f'{tmp}:/usr/bin:/bin', 'ARGV_OUT': f'{tmp}/argv', 'GITHUB_RUN_ID': '41300000000',
                   'GITHUB_SHA': 'b' * 40, 'TASK_OUTPUT': f'{tmp}/task/output'}
            subprocess.run(['bash', '-ec', command], env=env, check=True)
            self.assertEqual(Path(f'{tmp}/argv').read_text().splitlines(), ['--kill-after=5s', '20m', 'python3',
                'scripts/rango-factory-observe.py', 'run', '--run-id', '41300000000', '--recipe-sha', 'b' * 40,
                '--out', f'{tmp}/task/output'])

    def test_new_task_directory_receipt_allowlist_and_cleanup(self):
        prepare = script(step(self.job, 'New task directory for this attempt'))
        collect = script(step(self.job, 'Allowlisted JSON receipts only'))
        clean = script(step(self.job, "Remove this attempt's task directory"))
        for name in ('Allowlisted JSON receipts only', 'Receipts artifact', "Remove this attempt's task directory"):
            self.assertIn('        if: always()\n', step(self.job, name))
        upload = step(self.job, 'Receipts artifact')
        self.assertIn('          path: ${{ env.TASK_RECEIPTS }}\n', upload)
        self.assertIn('          name: rango-factory-observe-${{ github.run_id }}-${{ github.run_attempt }}\n', upload)
        with tempfile.TemporaryDirectory() as tmp:
            env = {'PATH': os.environ['PATH'], 'RUNNER_TEMP': tmp, 'GITHUB_RUN_ID': '7', 'GITHUB_RUN_ATTEMPT': '1',
                   'GITHUB_ENV': f'{tmp}/github_env'}
            subprocess.run(['bash', '-c', prepare], env=env, check=True)
            exported = dict(line.split('=', 1) for line in Path(env['GITHUB_ENV']).read_text().splitlines())
            task = Path(exported['TASK_ROOT'])
            self.assertEqual((task, exported['TASK_OUTPUT'], exported['TASK_RECEIPTS']),
                (Path(tmp)/'rango-factory-observe-7-1', f'{task}/output', f'{task}/receipts'))
            self.assertNotEqual(subprocess.run(['bash', '-c', prepare], env=env).returncode, 0, 'never reuses a task directory')
            out = Path(exported['TASK_OUTPUT'])
            (out/'fastboot/bin').mkdir(parents=True)
            (out/'fastboot/bin/fastboot').write_bytes(b'\x7fELF')
            (out/'fastboot/lib64').mkdir()
            (out/'fastboot/lib64/libc++.so').write_bytes(b'\x7fELF')
            for name in RECEIPTS[:2]:
                (out/name).write_text('{}\n')
            (out/'other.json').write_text('{}\n')
            os.symlink(out/'other.json', out/RECEIPTS[2])  # a link is never followed or uploaded
            run_env = {**env, **exported}
            subprocess.run(['bash', '-c', collect], env=run_env, check=True)
            self.assertEqual(sorted(p.name for p in Path(exported['TASK_RECEIPTS']).iterdir()), sorted(RECEIPTS[:2]))
            self.assertTrue(all(p.is_file() and not p.is_symlink() for p in Path(exported['TASK_RECEIPTS']).iterdir()))
            subprocess.run(['bash', '-c', clean], env=run_env, check=True)
            self.assertFalse(task.exists())
        # Before the directory exists (an earlier step failed), collection and cleanup do nothing and succeed.
        for body in (collect, clean):
            self.assertEqual(subprocess.run(['bash', '-c', body], env={'PATH': os.environ['PATH']}).returncode, 0)

    def test_always_run_host_cleanup_waits_for_the_observation(self):
        cleanup = jobs()['cleanup']
        needs = re.search(r'(?m)^    needs: \[(.+)\]$', cleanup)[1].split(', ')
        self.assertIn(MODE, needs)
        self.assertIn("    if: always() && needs.controller.result != 'skipped'\n", cleanup)
        self.assertIn('run: python3 scripts/release-pipeline.py stop-builder', cleanup)

    def test_limitation_is_stated_in_the_workflow(self):
        self.assertIn('do NOT\n  # guarantee the builder VM stops within 30 minutes', WORKFLOW)
        self.assertIn('Runtime is not approved.', WORKFLOW)


class Helper(unittest.TestCase):
    def test_hosted_check_binds_the_fixed_read_set_and_excludes_first_use(self):
        import json
        result = subprocess.run(['python3', str(ROOT/'scripts/rango-factory-observe.py'), 'check'], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        bindings = json.loads(result.stdout)['bindings']
        self.assertEqual(sorted(bindings), ['avbPublicKeySha256', 'excluded', 'inputReceipt', 'lock', 'marker', 'otaPrefix',
            'otaSuffix', 'otatools', 'product', 'targetFiles'])
        self.assertIn('2026103001-transfer007/input/rango-retained-input.json', bindings['inputReceipt']['path'])
        self.assertIn('2026103001-transfer007/input/rango-otatools.zip', bindings['otatools']['path'])
        self.assertEqual(bindings['lock'], '/home/pixelbuild/frankel-build.lock')
        self.assertTrue(any(p.endswith('rango-identity') for p in bindings['excluded']))
        self.assertNotIn('blazer', result.stdout.lower())


if __name__ == '__main__':
    unittest.main()
