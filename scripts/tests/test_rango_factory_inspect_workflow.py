"""FOLD-FACTORY-INSPECT: inspect-only wiring of the retained Fold installer (scripts/rango-factory-inspect-wiring.py).

Stand-ins only: a synthetic retained root and fake tool processes (no host, lock file of the builder, target-files,
classifier or observer run). The retained identity pin, the fail-closed verification, the bounded process-group calls,
the write-once records, the firmware hand-off to classify and the report allowlist are exercised."""
import contextlib
import hashlib
import re
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('rango_factory_inspect_wiring', ROOT/'scripts/rango-factory-inspect-wiring.py')
inspect_wiring = importlib.util.module_from_spec(spec)
spec.loader.exec_module(inspect_wiring)
pkg = inspect_wiring.pkg
ZIP = b'PK-retained-installer'


def no_lock(path):
    return contextlib.nullcontext()


# A process group id that cannot exist (above the kernel's pid_max), so a timeout's killpg never reaches a real one.
NO_SUCH_GROUP = 1 << 23


class Tools:
    def __init__(self, test, **plan):
        self.test, self.plan, self.calls = test, plan, []

    def popen(self, argv, stdout=None, start_new_session=False):
        self.test.assertTrue(start_new_session)  # every tool call gets its own process group
        name = Path(argv[1]).name
        kind = 'classify' if 'classify' in argv[2:3] else 'observe' if 'observe' in argv[2:3] else name
        self.calls.append((kind, argv))
        code, text, hang, write = self.plan.get(kind, (0, '', False, None))
        if kind == 'observe':
            Path(argv[argv.index('--out') + 1]).write_text('{}')
        if write:
            Path(write[0]).write_text(write[1])
        if text and stdout is not None and hasattr(stdout, 'write'):
            stdout.write(text)
            stdout.flush()
        class Proc:
            pid = NO_SUCH_GROUP
            returncode = None

            def wait(self, timeout=None):
                if hang:
                    raise subprocess.TimeoutExpired(argv, timeout)
                self.returncode = code
                return code
        return Proc()


class Base(unittest.TestCase):
    def setUp(self):
        self.parent = Path(tempfile.mkdtemp())
        self.retained = dict(inspect_wiring.RETAINED, attempt='1', bytes=len(ZIP), sha256=hashlib.sha256(ZIP).hexdigest())
        self.source = pkg.task_root(self.retained['runId'], '1', parent=self.parent)
        (self.source/'package').mkdir(parents=True)
        (self.source/'package/rango-install-2026103001.zip').write_bytes(ZIP)
        self.receipt({'status': 'packaged', 'output': {'file': 'rango-install-2026103001.zip', 'size': len(ZIP),
                                                        'sha256': self.retained['sha256']}})
        self.root = inspect_wiring.inspect_root('41600000000', '1', parent=self.parent)

    def receipt(self, document):
        (self.source/'package/factory-package-receipt.json').write_text(json.dumps(document))

    def run_inspect(self, tools, lock=no_lock):
        with open(os.devnull, 'w') as null, contextlib.redirect_stdout(null):
            return inspect_wiring.inspect(self.root, '41600000000', '1', self.retained, source=self.source, lock=lock,
                                          popen=tools.popen)

    def record(self, name):
        return json.loads((self.root/'inspection'/name).read_text())


class Pins(Base):
    def test_committed_pin_is_the_exported_installer_of_run_41515240855_attempt_1(self):
        self.assertEqual(inspect_wiring.RETAINED, {'runId': '41515240855', 'attempt': '1', 'bytes': 2130374982,
            'sha256': 'e37db904490a99857dc1fe106b2eac1db12c71b32ac23372cfda474ab70d3b48'})
        self.assertEqual(inspect_wiring.retained_root(inspect_wiring.RETAINED),
                         Path('/home/pixelbuild/rose-actions/rango-factory-package-41515240855-1'))
        result = subprocess.run([sys.executable, str(ROOT/'scripts/rango-factory-inspect-wiring.py'), 'check'],
                                capture_output=True, text=True)
        self.assertEqual((result.returncode, result.stdout), (0, 'outer_seconds=4410\njob_minutes=84\n'))
        with self.assertRaises(inspect_wiring.Unbound):  # an uncommitted attempt is never guessed
            inspect_wiring.check(retained=dict(inspect_wiring.RETAINED, attempt=None))

    def test_bounds_once_bound(self):
        bounds, outer, minutes = inspect_wiring.check()
        self.assertEqual(bounds, {'verifySeconds': 300, 'observeSeconds': 600, 'firmwareSeconds': 570, 'classifySeconds': 2700})
        self.assertEqual((outer, minutes), (4410, 84))  # 4170 s of steps + 4 x 30 s kill + 120 s margin
        self.assertIn('never a pass', open(ROOT/'scripts/rango-factory-inspect-wiring.py').read())
        for change in ({'attempt': '01'}, {'attempt': 'x'}, {'sha256': 'A' * 64}, {'bytes': 0},
                       {'bytes': pkg.INSTALLER['capBytes'] + 1}, {'runId': 'r1'}):
            with self.subTest(change=change), self.assertRaises(pkg.Invalid):
                inspect_wiring.check(retained={**inspect_wiring.RETAINED, 'attempt': '1', **change})


class Verify(Base):
    def test_mismatches_refuse_before_any_tool_runs(self):
        cases = (lambda: (self.source/'package/rango-install-2026103001.zip').write_bytes(ZIP + b'x'),
                 lambda: (self.source/'package/rango-install-2026103001.zip').write_bytes(ZIP[:-1] + b'Q'),
                 lambda: self.receipt({'status': 'failed'}),
                 lambda: self.receipt({'status': 'packaged', 'output': {'file': 'rango-install-2026103001.zip',
                                       'size': len(ZIP), 'sha256': 'a' * 64}}),
                 lambda: (self.source/'package/rango-install-2026103001.zip').unlink())
        for i, damage in enumerate(cases):
            with self.subTest(case=i):
                self.setUp()
                damage()
                tools = Tools(self)
                self.assertEqual(self.run_inspect(tools), 3)
                self.assertEqual(tools.calls, [])
                self.assertFalse(self.record('retained.json')['verified'])
                self.assertRegex(self.record('retained.json')['refused'], '^retained_(mismatch|missing):')
                self.assertEqual(self.record('inspect.json')['status'], 'refused')

    def test_the_verify_bound_is_enforced_in_process(self):
        ticks = iter(range(0, 10_000, 200))  # each block read advances a fake clock by 200 s
        path = self.parent/'big'
        path.write_bytes(b'x' * (3 * 1024))
        with self.assertRaisesRegex(inspect_wiring.Refused, '^verify_timeout:'):
            inspect_wiring.bounded_sha256(path, 300, clock=lambda: next(ticks), block=1024)
        self.assertEqual(inspect_wiring.bounded_sha256(path, 300, block=1024), hashlib.sha256(b'x' * 3072).hexdigest())
        tools = Tools(self)

        def slow(archive):
            raise inspect_wiring.Refused('verify_timeout: hashing the retained archive exceeded 300 s')
        with open(os.devnull, 'w') as null, contextlib.redirect_stdout(null):
            status = inspect_wiring.inspect(self.root, '41600000000', '1', self.retained, source=self.source, lock=no_lock,
                                            popen=tools.popen, hasher=slow)
        self.assertEqual(status, 3)
        self.assertEqual(tools.calls, [])  # a verify timeout fails the run before any inspection
        self.assertTrue(self.record('retained.json')['refused'].startswith('verify_timeout'))
        self.assertEqual(self.record('inspect.json')['status'], 'refused')

    def test_cleanup_reports_leftovers_and_never_deletes_them(self):
        (self.root/'inspection').mkdir(parents=True)
        (self.root/'classify-work/factory-package-x').mkdir(parents=True)
        (self.root/'classify-work/factory-package-x/partial').write_bytes(b'interrupted')
        state = inspect_wiring.report(self.root, self.parent/'upload-left')
        self.assertEqual((state['classifyWorkLeft'], state['classifyWorkRemoved']), (['factory-package-x'], False))
        self.assertTrue((self.root/'classify-work/factory-package-x/partial').exists())

    def test_a_symlinked_retained_archive_refuses(self):
        real = self.parent/'elsewhere.zip'
        real.write_bytes(ZIP)
        archive = self.source/'package/rango-install-2026103001.zip'
        archive.unlink()
        archive.symlink_to(real)
        tools = Tools(self)
        self.assertEqual(self.run_inspect(tools), 3)
        self.assertEqual(tools.calls, [])

    def test_the_retained_root_is_never_written(self):
        before = sorted((p.relative_to(self.source), p.stat().st_mtime_ns) for p in self.source.rglob('*'))
        self.run_inspect(Tools(self, classify=(3, '{"factoryReady": false}', False, None)))
        self.assertEqual(sorted((p.relative_to(self.source), p.stat().st_mtime_ns) for p in self.source.rglob('*')), before)


class Inspect(Base):
    def firmware_writes(self):
        return (str(self.root/'inspection/firmware-observation.json'), '{"schema": "fw"}')

    def test_observation_flows_to_classify_of_the_retained_archive(self):
        tools = Tools(self, firmware=None)
        tools.plan['rango-firmware-observe.py'] = (0, '{"observed": "x"}', False, self.firmware_writes())
        tools.plan['classify'] = (3, '{"factoryReady": false}', False, None)
        self.assertEqual(self.run_inspect(tools), 0)
        kinds = [k for k, _ in tools.calls]
        self.assertEqual(kinds, ['observe', 'rango-firmware-observe.py', 'classify'])
        classify = tools.calls[2][1]
        self.assertEqual(classify[3], str(self.source/'package/rango-install-2026103001.zip'))
        self.assertEqual(classify[classify.index('--firmware-observation') + 1],
                         str(self.root/'inspection/firmware-observation.json'))
        self.assertEqual(classify[classify.index('--work-dir') + 1], str(self.root/'classify-work'))
        summary = self.record('inspect.json')
        self.assertEqual((summary['status'], summary['steps']['firmware'], summary['steps']['classify']['exit']),
                         ('inspected', 'observed', 3))
        self.assertTrue(self.record('retained.json')['verified'])
        self.assertEqual(json.loads((self.root/'inspection/classify.json').read_text()), {'factoryReady': False})

    def test_firmware_refusal_keeps_classify_without_the_observation(self):
        tools = Tools(self)
        tools.plan['rango-firmware-observe.py'] = (3, '{"refused": "format_invalid: not FBPK"}', False, None)
        tools.plan['classify'] = (3, '{}', False, None)
        self.assertEqual(self.run_inspect(tools), 0)
        self.assertNotIn('--firmware-observation', tools.calls[-1][1])
        self.assertEqual(self.record('firmware-attempt.json')['category'], 'format_invalid')

    def test_timeouts_and_unexpected_exits_fail_and_are_recorded(self):
        for plan, refused in (({'observe': (0, '', True, None)}, 'observe_failed'),
                              ({'observe': (2, '', False, None)}, 'observe_failed'),
                              ({'rango-firmware-observe.py': (0, '', True, None)}, 'firmware_failed'),
                              ({'rango-firmware-observe.py': (2, '', False, None)}, 'firmware_failed'),
                              ({'classify': (2, '', False, None)}, 'classify_failed'),
                              ({'classify': (0, '', True, None)}, 'classify_failed')):
            with self.subTest(plan=plan):
                self.setUp()
                tools = Tools(self, **plan)
                tools.plan.setdefault('rango-firmware-observe.py', (3, '{"refused": "x_y: z"}', False, None))
                self.assertEqual(self.run_inspect(tools), 3)
                self.assertTrue(self.record('inspect.json')['refused'].startswith(refused))

    def test_a_held_lock_refuses_before_verification(self):
        @contextlib.contextmanager
        def held(path):
            raise inspect_wiring.Refused('lock_held: the shared builder lock is held by another job')
            yield
        tools = Tools(self)
        self.assertEqual(self.run_inspect(tools, lock=held), 3)
        self.assertEqual(tools.calls, [])
        self.assertFalse((self.root/'inspection/retained.json').exists())
        self.assertEqual(self.record('inspect.json')['refused'][:9], 'lock_held')

    def test_the_task_root_is_new_and_reports_are_write_once(self):
        self.run_inspect(Tools(self, classify=(3, '{}', False, None)))
        with self.assertRaises(FileExistsError):
            self.run_inspect(Tools(self))

    def test_report_uploads_only_the_inspection_json(self):
        tools = Tools(self)
        tools.plan['rango-firmware-observe.py'] = (0, '{}', False, self.firmware_writes())
        tools.plan['classify'] = (3, '{}', False, None)
        self.run_inspect(tools)
        upload = self.parent/'upload'
        state = inspect_wiring.report(self.root, upload)
        self.assertEqual(sorted(p.name for p in upload.iterdir()), ['classify.json', 'cleanup.json', 'firmware-attempt.json',
            'firmware-observation.json', 'inspect.json', 'retained.json', 'target-files-observation.json'])
        self.assertTrue(state['classifyWorkRemoved'])
        absent = inspect_wiring.report(inspect_wiring.inspect_root('9', '1', parent=self.parent), self.parent/'upload-2')
        self.assertFalse(absent['taskRootExists'])

    def test_bounded_kills_the_process_group_on_timeout(self):
        script = 'import time; time.sleep(30)'
        start = __import__('time').monotonic()
        code, late = inspect_wiring.bounded([sys.executable, '-c', script], 1, subprocess.DEVNULL, grace=5)
        self.assertTrue(late)
        self.assertLess(__import__('time').monotonic() - start, 10)
        self.assertNotEqual(code, 0)



WORKFLOW = (ROOT/'.github/workflows/os-release.yml').read_text()
MODE = 'rango-factory-inspect'


def jobs():
    body = WORKFLOW.split('\njobs:\n', 1)[1]
    return dict(re.findall(r'(?ms)^  ([\w-]+):\n(.*?)(?=^  [\w-]+:\n|\Z)', body))


def step(job, name):
    match = re.search(r'(?ms)^      - name: ' + re.escape(name) + r'\n(.*?)(?=^      - |\Z)', job)
    assert match, name
    return match[1]


def code(text):
    return '\n'.join(line for line in text.splitlines() if not line.strip().startswith('#'))


class Workflow(unittest.TestCase):
    def guard(self, **change):
        body = step(jobs()['guard'], 'Candidate is an exact commit on main')
        lines = []
        for line in re.search(r'(?ms)^        run: \|\n(.*)', body)[1].splitlines():
            if line.strip() and not line.startswith(' ' * 10):
                break
            lines.append(line[10:])
        env = {'PATH': os.environ['PATH'], 'MODE': MODE, 'CANDIDATE_SHA': '', 'BUILD_NUMBER': '', 'BUILD_TIMESTAMP': '',
               'APPS': '', 'PROMOTE': 'false', 'CHANNEL': 'beta', 'RESUME_STAGE': 'none', 'RESUME_SOURCE_RUN': '',
               'RESUME_UNSIGNED_SHA256': '', 'RESUME_SIGNED_SHA256': '', 'CAMPAIGN': '', 'APP_PUBLISH': 'false',
               'ROSE_RANGO_PUBLISH': 'false', 'ROSE_FIRST_USE_ENROLL': 'false', 'ROSE_RECOVER_RUN': '',
               'BOOTSTRAP_PROFILE': 'none', 'MODELS_BOOTSTRAP_RUN': '', 'REUSE_BINDING': '', 'ALPHA_RECOVER': 'false',
               'ROSE_DEVICE': 'frankel', 'BLAZER_PREP_STAGE': 'none', 'GITHUB_SHA': 'f' * 40, **change}
        return subprocess.run(['bash', '-c', 'set -euo pipefail\n' + '\n'.join(lines) + '\n'], env=env, cwd=ROOT,
                              capture_output=True).returncode

    def test_guard_admits_only_the_bare_mode(self):
        self.assertEqual(self.guard(), 0)
        for change in ({'CANDIDATE_SHA': 'a' * 40}, {'BUILD_NUMBER': '2026103001'}, {'APPS': '[["dev.pixel.voice",1]]'},
                       {'PROMOTE': 'true'}, {'CAMPAIGN': 'start'}, {'APP_PUBLISH': 'true'}, {'ROSE_RANGO_PUBLISH': 'true'},
                       {'BOOTSTRAP_PROFILE': 'fold-rango-factory-v1'}, {'ROSE_DEVICE': 'rango'}, {'BLAZER_PREP_STAGE': 'source'}):
            with self.subTest(change=change):
                self.assertNotEqual(self.guard(**change), 0)

    def test_mode_check_job_and_ownership(self):
        self.assertIn('signing-bootstrap, rango-factory-package, rango-factory-inspect, models-sign,', WORKFLOW)
        self.assertIn("      - name: Rango factory inspect check\n        id: factory-inspect\n        if: inputs.mode == "
                      "'rango-factory-inspect'\n        run: python3 scripts/rango-factory-inspect-wiring.py check >> "
                      "\"$GITHUB_OUTPUT\"\n", jobs()['guard'])
        job = jobs()[MODE]
        self.assertEqual(code(job.split('    steps:\n', 1)[0]), "    needs: [guard, controller]\n    if: inputs.mode == "
            "'rango-factory-inspect'\n    runs-on: [self-hosted, roseos-frankel-builder]\n"
            "    timeout-minutes: ${{ fromJSON(needs.guard.outputs.inspect_job_minutes || '1') }}\n"
            "    permissions:\n      contents: read\n    env:\n      OUTER_SECONDS: ${{ needs.guard.outputs.inspect_outer_seconds }}")
        body = code(job)
        for absent in ('id-token', 'secrets.', 'actions-gcp-login', 'GCP_', 'PIXEL_', 'publish', 'host-factory-package',
                       'host-sign', '.zip', 'target_files', 'rm -rf', 'download-artifact'):
            self.assertNotIn(absent, body)
        self.assertEqual(sorted(set(re.findall(r'python3 (\S+)', body))), ['scripts/rango-factory-inspect-wiring.py'])
        self.assertIn('timeout --kill-after=30s "${OUTER_SECONDS}s" python3 scripts/rango-factory-inspect-wiring.py inspect '
                      '--run-id "$GITHUB_RUN_ID" --attempt "$GITHUB_RUN_ATTEMPT"\n', job)
        for name in ('Cleanup report and allowlisted reports', 'Reports artifact'):
            self.assertIn('        if: always()\n', step(job, name))
        self.assertEqual(job.count('actions/upload-artifact@'), 1)  # reports only: nothing is exported again
        own = "inputs.mode == 'blazer-prep' || inputs.mode == 'rango-factory-package' || inputs.mode == 'rango-factory-inspect' || inputs.mode == 'launcher3-compile'"
        self.assertEqual(jobs()['controller'].count(own), 2)
        self.assertEqual(jobs()['cleanup'].count(own), 1)
        self.assertIn('rango-factory-inspect, launcher3-compile, app-builder', re.search(r'(?m)^    needs: (.+)$', jobs()['cleanup'])[1])
        self.assertNotIn('rango-factory-inspect-wiring.py', jobs()['rango-factory-package'])


if __name__ == '__main__':
    unittest.main()
