"""Stage receipts, locking and interruption, exercised with real local dummy processes only."""
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest

SCRIPT = Path(__file__).resolve().parents[1]/'release-stage.py'
SOURCE = '34fa48ff16d046e16c7d680731bf01058d794279'
OTHER = '0'*40
PY = sys.executable
SLEEP = [PY, '-c', 'import time; time.sleep(60)']

def cli(*args, source=SOURCE):
    argv = [PY, str(SCRIPT), *[str(a) for a in args]]
    if source and args[0] not in ('status',):
        argv[3:3] = ['--source-revision', source]
    result = subprocess.run(argv, capture_output=True, text=True, timeout=60)
    text = result.stdout or result.stderr
    try:
        return result.returncode, json.loads(text)
    except ValueError:
        return result.returncode, text

class StageTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.state = self.root/'state'
        code, report = cli('init', '--state', self.state, '--device', 'testdev', '--build-number', 2026102801)
        self.assertEqual(code, 0, report)

    def run_stage(self, stage, *command, inputs=(), outputs=(), source=SOURCE):
        args = ['run', '--state', self.state]
        for path in inputs:
            args += ['--input', path]
        for path in outputs:
            args += ['--output', path]
        return cli(*args, stage, '--', *(command or ['true']), source=source)

    def status(self):
        return cli('status', '--state', self.state)

    def spawn(self, stage, *command):
        argv = [PY, str(SCRIPT), 'run', '--state', str(self.state), '--source-revision', SOURCE, stage, '--', *command]
        process = subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        def cleanup():
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
        self.addCleanup(cleanup)
        return process

    def wait_state(self, expected, timeout=15):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            code, report = self.status()
            if code == 0 and report['state'] == expected:
                return report
            time.sleep(0.05)
        self.fail(f'state never became {expected}: {self.status()}')

    def receipts(self):
        return [json.loads(p.read_text()) for p in sorted(self.state.glob('*.json'))]

    def test_compile_success_is_not_release_completion_and_binds_hashes(self):
        source_file, built = self.root/'input.txt', self.root/'out.img'
        source_file.write_text('synthetic input\n')
        code, report = self.run_stage('build', PY, '-c', f'open({str(built)!r}, "w").write("image")',
            inputs=[source_file], outputs=[built])
        self.assertEqual(code, 0, report)
        self.assertEqual(report['state'], 'AWAITING')
        self.assertEqual(report['stage'], 'bytes-verify')
        self.assertTrue(report['builderComputeNeeded'])
        completed = self.receipts()[-1]
        self.assertEqual(completed['event'], 'completed')
        self.assertEqual(completed['outputs'][0]['sha256'], __import__('hashlib').sha256(b'image').hexdigest())
        self.assertEqual(completed['inputs'][0]['name'], 'input.txt')

    def test_lifecycle_awaits_external_publication_without_builder_compute(self):
        for stage in ('build', 'bytes-verify', 'sign', 'ota-verify', 'transfer'):
            self.assertEqual(self.run_stage(stage)[0], 0, stage)
        code, report = self.status()
        self.assertEqual((report['state'], report['stage']), ('AWAITING_EXTERNAL', 'publish'))
        self.assertFalse(report['builderComputeNeeded'])
        self.assertEqual(self.run_stage('publish')[0], 2)
        evidence = self.root/'delivery-receipt.json'
        evidence.write_text('{"verdict": "PASS"}\n')
        code, report = cli('record', '--state', self.state, '--evidence', evidence, 'publish')
        self.assertEqual(code, 0, report)
        self.assertEqual(report['state'], 'AWAITING')
        code, report = self.run_stage('publication-verify')
        self.assertEqual((report['state'], report['stage']), ('TERMINAL', 'published-verified'))
        self.assertEqual(self.run_stage('publication-verify')[0], 2)

    def test_out_of_order_duplicate_candidate_mismatch_and_tampering_rejected(self):
        self.assertEqual(self.run_stage('sign')[0], 2)
        self.assertEqual(self.run_stage('build', source=OTHER)[0], 2)
        self.assertEqual(self.run_stage('build')[0], 0)
        self.assertEqual(self.run_stage('build')[0], 2)
        self.assertEqual(len(self.receipts()), 3)
        shutil.copy(self.state/'001-started.json', self.state/'003-started.json')
        code, report = self.status()
        self.assertEqual(code, 2)
        self.assertIn('chain broken', report['error'])
        (self.state/'003-started.json').unlink()
        self.assertEqual(self.status()[0], 0)
        # Any edit to a receipt that a later receipt chains over is detected. (The newest receipt has
        # no later anchor; it is protected only by write-once creation and file permissions.)
        receipt = json.loads((self.state/'001-started.json').read_text())
        receipt['inputs'] = [{'name': 'forged', 'size': 1, 'sha256': 'f'*64}]
        (self.state/'001-started.json').write_text(json.dumps(receipt))
        code, report = self.status()
        self.assertEqual(code, 2)
        self.assertIn('chain broken at 002-completed.json', report['error'])

    def test_kill_9_cannot_advance_and_recovery_is_explicit(self):
        process = self.spawn('build', *SLEEP)
        report = self.wait_state('RUNNING')
        self.assertTrue(report['lock']['ownerBootMatches'])
        os.killpg(process.pid, signal.SIGKILL)
        process.wait()
        report = self.wait_state('INTERRUPTED')
        self.assertEqual(report['stage'], 'build')
        self.assertTrue(report['builderComputeNeeded'])
        self.assertEqual(self.run_stage('bytes-verify')[0], 2)
        self.assertEqual(self.run_stage('build')[0], 2)
        self.assertEqual(cli('mark-interrupted', '--state', self.state, 'build')[0], 0)
        code, report = self.run_stage('build')
        self.assertEqual((code, report['state'], report['stage']), (0, 'AWAITING', 'bytes-verify'))
        self.assertEqual([r['event'] for r in self.receipts()], ['init', 'started', 'interrupted', 'started', 'completed'])

    def test_orphaned_command_keeps_lock_and_is_never_running(self):
        process = self.spawn('build', *SLEEP)
        self.wait_state('RUNNING')
        os.kill(process.pid, signal.SIGKILL)
        process.wait()
        report = self.wait_state('LOCKED_UNVERIFIED')
        self.assertFalse(report['lock']['ownerAlive'])
        self.assertEqual(cli('mark-interrupted', '--state', self.state, 'build')[0], 3)
        os.killpg(process.pid, signal.SIGKILL)
        self.wait_state('INTERRUPTED')

    def test_crash_after_artifact_before_completion_receipt(self):
        built = self.root/'out.img'
        code, _ = self.run_stage('build', PY, '-c',
            f'import os, signal; open({str(built)!r}, "w").write("x"); os.kill(os.getppid(), signal.SIGKILL)',
            outputs=[built])
        self.assertNotEqual(code, 0)
        self.assertTrue(built.exists())
        self.assertEqual(self.wait_state('INTERRUPTED')['completedStages'], [])
        self.assertNotIn('completed', [r['event'] for r in self.receipts()])

    def test_concurrent_runner_refused(self):
        self.spawn('build', *SLEEP)
        self.wait_state('RUNNING')
        code, report = self.run_stage('build')
        self.assertEqual(code, 3)
        self.assertEqual(report['verdict'], 'LOCKED')
        self.assertEqual(cli('abandon', '--state', self.state, '--reason', 'x')[0], 3)

    def test_foreign_boot_id_is_never_running(self):
        process = self.spawn('build', *SLEEP)
        self.wait_state('RUNNING')
        lock = self.state/'stage.lock'
        owner = json.loads(lock.read_text())
        lock.write_text(json.dumps(dict(owner, bootId='another-boot')))
        report = self.wait_state('LOCKED_UNVERIFIED')
        self.assertFalse(report['lock']['ownerBootMatches'])
        os.killpg(process.pid, signal.SIGKILL)
        process.wait()
        self.wait_state('INTERRUPTED')

    def test_failures_are_explicit_terminal_receipts(self):
        source_file = self.root/'input.txt'
        source_file.write_text('a')
        code, report = self.run_stage('build', PY, '-c', f'open({str(source_file)!r}, "w").write("b")',
            inputs=[source_file])
        self.assertEqual((code, report['state'], report['stage']), (1, 'TERMINAL', 'failed'))
        self.assertEqual(self.receipts()[-1]['reason'], 'inputs changed during the stage')
        for command, outputs, reason in [([PY, '-c', 'raise SystemExit(7)'], [], 'stage command exited 7'),
                                         (['true'], [self.root/'missing.img'], 'not a regular file: missing.img'),
                                         ([str(self.root/'no-such-tool')], [], 'stage command exited 127')]:
            shutil.rmtree(self.state)
            cli('init', '--state', self.state, '--device', 'testdev', '--build-number', 2026102801)
            code, report = self.run_stage('build', *command, outputs=outputs)
            self.assertEqual((code, report['state']), (1, 'TERMINAL'), reason)
            self.assertEqual(self.receipts()[-1]['reason'], reason)

    def test_receipts_and_status_stay_nonsecret(self):
        marker = 'SYNTHETIC-SECRET-7f3a'
        env_probe = f'import os, sys; print(os.environ.get("PROBE")); print(sys.argv[1], file=sys.stderr)'
        os.environ['PROBE'] = marker
        self.addCleanup(os.environ.pop, 'PROBE', None)
        code, report = self.run_stage('build', PY, '-c', env_probe, marker)
        self.assertEqual(code, 0)
        self.assertIsInstance(report, dict, 'stdout must carry only the JSON report')
        text = json.dumps(report) + json.dumps(self.status()[1]) + ''.join(p.read_text() for p in self.state.iterdir())
        self.assertNotIn(marker, text)

    def test_release_tools_run_in_isolated_mode(self):
        for script in (SCRIPT, SCRIPT.parent/'release-manifest.py'):
            result = subprocess.run([PY, '-I', str(script), '--help'], capture_output=True, text=True, cwd=self.root)
            self.assertEqual(result.returncode, 0, result.stderr)

    def failed_at_bytes_verify(self, build_outputs=()):
        self.assertEqual(self.run_stage('build', outputs=build_outputs)[0], 0)
        self.assertEqual(self.run_stage('bytes-verify', 'false')[0], 1)
        self.assertEqual(self.status()[1]['state'], 'TERMINAL')

    def artifacts(self, data=b'unsigned target files'):
        directory = self.root/'resumed-release'
        directory.mkdir(exist_ok=True)
        (directory/'unsigned-target_files.zip').write_bytes(data)
        (directory/'background-policy.json').write_text('{}')
        return [directory/'unsigned-target_files.zip', directory/'background-policy.json']

    def resume(self, *extra, artifacts=None, state=None, stage='bytes-verify', source=SOURCE):
        args = ['resume', stage, '--from', self.state, '--state', state or self.root/'resumed']
        for path in self.artifacts() if artifacts is None else artifacts:
            args += ['--artifact', path]
        return cli(*args, *extra, source=source)

    def test_resume_carries_completed_stages_into_a_new_bound_chain(self):
        self.failed_at_bytes_verify()
        before = {p.name: p.read_bytes() for p in self.state.iterdir()}
        digest = __import__('hashlib').sha256(b'unsigned target files').hexdigest()
        code, report = self.resume('--expect', f'unsigned-target_files.zip={digest}')
        self.assertEqual(code, 0, report)
        self.assertEqual((report['state'], report['stage'], report['completedStages']), ('AWAITING', 'bytes-verify', ['build']))
        self.assertEqual({p.name: p.read_bytes() for p in self.state.iterdir()}, before)  # the failed chain is only read
        init = json.loads((self.root/'resumed/000-init.json').read_text())
        origin = init['resumedFrom']
        self.assertEqual((origin['failedStage'], origin['carriedStages'], origin['receipts']), ('bytes-verify', ['build'], 6))
        self.assertEqual(origin['lastReceiptSha256'], __import__('hashlib').sha256((self.state/'005-failed.json').read_bytes()).hexdigest())
        self.assertEqual(origin['expected'], {'unsigned-target_files.zip': digest})
        self.assertEqual(sorted(a['name'] for a in origin['artifacts']), ['background-policy.json', 'unsigned-target_files.zip'])
        self.state = self.root/'resumed'
        self.assertEqual(self.run_stage('bytes-verify')[0], 0)
        self.assertEqual(self.status()[1]['completedStages'], ['build', 'bytes-verify'])

    def test_resume_refuses_anything_but_an_exact_failed_stage(self):
        code, report = self.resume()
        self.assertEqual(code, 2)
        self.assertIn('did not fail at bytes-verify', report['error'])  # nothing has failed yet
        self.failed_at_bytes_verify()
        for extra, stage, source, message in (
                ([], 'sign', SOURCE, 'did not fail at sign'),
                ([], 'build', SOURCE, 'only a builder stage after build'),
                ([], 'publish', SOURCE, 'only a builder stage after build'),
                ([], 'bytes-verify', OTHER, 'does not match'),
                (['--expect', 'unsigned-target_files.zip=' + 'a' * 64], 'bytes-verify', SOURCE, 'differs from its expected sha256'),
                (['--expect', 'missing.zip=' + 'a' * 64], 'bytes-verify', SOURCE, 'for a carried artifact')):
            code, report = self.resume(*extra, stage=stage, source=source)
            self.assertEqual(code, 2, (stage, extra))
            self.assertIn(message, report['error'])
        self.assertFalse((self.root/'resumed').exists() and any((self.root/'resumed').iterdir()))
        code, report = self.resume(artifacts=[])
        self.assertIn('each carried artifact once', report['error'])
        self.assertEqual(self.resume()[0], 0)
        code, report = self.resume()
        self.assertIn('must be new and empty', report['error'])

    def test_recorded_build_outputs_must_be_carried_unchanged(self):
        output = self.root/'unsigned-target_files.zip'
        output.write_bytes(b'the bytes the build recorded')
        self.failed_at_bytes_verify(build_outputs=[output])
        code, report = self.resume()
        self.assertEqual(code, 2)
        self.assertIn('differs from the source receipt', report['error'])
        directory = self.root/'resumed-release'
        shutil.copy(output, directory)  # the directory holds the other test artifacts already
        self.assertEqual(self.resume(artifacts=[directory/'unsigned-target_files.zip'])[0], 0)

    def test_a_resumed_chain_that_fails_signing_resumes_again_with_the_same_artifacts(self):
        self.failed_at_bytes_verify()
        self.assertEqual(self.resume()[0], 0)
        first = self.state
        self.state = self.root/'resumed'
        self.assertEqual(self.run_stage('bytes-verify')[0], 0)
        self.assertEqual(self.run_stage('sign', 'false')[0], 1)
        before = {p.name: p.read_bytes() for p in self.state.iterdir()}
        artifacts = self.artifacts()
        (artifacts[1]).write_text('{"changed": true}')
        code, report = self.resume(state=self.root/'resumed-2', stage='sign', artifacts=artifacts)
        self.assertEqual(code, 2)
        self.assertIn('background-policy.json differs from the source receipt', report['error'])
        code, report = self.resume(state=self.root/'resumed-2', stage='sign')
        self.assertEqual(code, 0, report)
        self.assertEqual((report['stage'], report['completedStages']), ('sign', ['build', 'bytes-verify']))
        self.assertEqual({p.name: p.read_bytes() for p in self.state.iterdir()}, before)
        self.assertEqual(len(list(first.glob('*.json'))), 6)
        self.state = self.root/'resumed-2'
        self.assertEqual(self.run_stage('sign')[0], 0)

    def test_carried_receipts_only_open_a_resumed_chain(self):
        self.assertEqual(self.run_stage('build')[0], 0)
        receipt = json.loads((self.state/'002-completed.json').read_text())
        receipt.update(event='carried', sequence=3, previousSha256=__import__('hashlib').sha256(
            (self.state/'002-completed.json').read_bytes()).hexdigest(), stage='bytes-verify')
        (self.state/'003-carried.json').write_text(json.dumps(receipt))
        code, report = self.status()
        self.assertEqual(code, 2)
        self.assertIn('illegal transition: carried', report['error'])

    def test_uninitialized_is_not_failed_and_abandon_is_explicit(self):
        empty = self.root/'empty'
        empty.mkdir()
        code, report = cli('status', '--state', empty)
        self.assertEqual(code, 2)
        self.assertIn('not initialized', report['error'])
        code, report = cli('init', '--state', self.state, '--device', 'testdev', '--build-number', 1)
        self.assertEqual(code, 2)
        code, report = cli('abandon', '--state', self.state, '--reason', 'superseded by 2026102802')
        self.assertEqual((report['state'], report['stage']), ('TERMINAL', 'abandoned'))
        self.assertEqual(self.run_stage('build')[0], 2)

if __name__ == '__main__':
    unittest.main()
