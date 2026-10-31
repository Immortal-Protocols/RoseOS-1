"""Startup guard: synthetic node facts pass or refuse before any weights download, and the rendered command fails closed."""
import importlib.util
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
spec = importlib.util.spec_from_file_location('startup_guard', HERE/'startup_guard.py')
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)
import capacity  # noqa: E402
import render_upload  # noqa: E402

CONFIG = capacity.load()
A100 = CONFIG['options'][0]
LIMITS = render_upload.guard_limits(CONFIG, A100)
GIB = 2**30


def facts(**over):
    gpu = {'name': 'synthetic', 'total': 42*10**9, 'free': 41*10**9}
    base = {'driver_version': '580.65.06', 'gpus': [dict(gpu), dict(gpu)], 'cache_free': 200*10**9, 'shm_total': 16*GIB}
    base.update(over)
    return base


class GuardTest(unittest.TestCase):
    def test_limits_come_from_pinned_config(self):
        self.assertEqual(LIMITS['gpu_count'], 2)
        self.assertEqual(LIMITS['driver_major_min'], 580)
        self.assertGreater(LIMITS['cache_free_min_bytes'], CONFIG['model']['weights_fetch']['total_bytes_including_small_files'])
        _, weights = capacity.weight_bytes_per_gpu(CONFIG['model'], A100)
        self.assertGreater(LIMITS['gpu_total_min_bytes'], weights)

    def test_healthy_node_proceeds(self):
        self.assertEqual(guard.evaluate(facts(), LIMITS), [])

    def test_each_shortfall_refuses(self):
        cases = {
            'driver 575': facts(driver_version='575.57.08'),
            'not reported': facts(driver_version=None),
            'GPUs visible': facts(gpus=facts()['gpus'][:1]),
            'total bytes': facts(gpus=[{'name': 'x', 'total': 38*10**9, 'free': 38*10**9}]*2),
            'free bytes': facts(gpus=[{'name': 'x', 'total': 42*10**9, 'free': 30*10**9}]*2),
            'cache free': facts(cache_free=60*10**9),
            '/dev/shm': facts(shm_total=64*2**20),
            'CUDA unavailable': facts(cuda_error='RuntimeError: driver too old', gpus=[]),
        }
        for expected, node in cases.items():
            failures = guard.evaluate(node, LIMITS)
            self.assertTrue(any(expected in f for f in failures), (expected, failures))

    def test_missing_disk_or_shm_facts_refuse(self):
        self.assertTrue(guard.evaluate(facts(cache_free=None, shm_total=None), LIMITS))

    def test_driver_version_parsing(self):
        samples = {
            'NVRM version: NVIDIA UNIX x86_64 Kernel Module  580.65.06  Sun Jul 27 06:54:38 UTC 2025\n': '580.65.06',
            'NVRM version: NVIDIA UNIX Open Kernel Module for x86_64  580.126.20  Release Build\n': '580.126.20',
            'unrelated\n': None,
        }
        with tempfile.TemporaryDirectory() as d:
            for text, expected in samples.items():
                path = Path(d)/'version'
                path.write_text(text)
                self.assertEqual(guard._driver_version(str(path)), expected)
            self.assertIsNone(guard._driver_version(str(Path(d)/'absent')))

    def test_main_refuses_without_exec(self):
        with mock.patch.object(guard, 'collect', return_value=facts(driver_version='550.1')), \
                mock.patch.object(guard.os, 'execvp') as execvp, mock.patch('builtins.print') as out:
            with self.assertRaises(SystemExit) as stop:
                guard.main([json.dumps(LIMITS), '--', 'vllm', 'serve'])
        self.assertEqual(stop.exception.code, guard.EXIT_REFUSED)
        execvp.assert_not_called()
        evidence = json.loads(out.call_args.args[0])
        self.assertFalse(evidence['proceed'])

    def test_main_execs_server_when_node_fits(self):
        with mock.patch.object(guard, 'collect', return_value=facts()), \
                mock.patch.object(guard.os, 'execvp') as execvp, mock.patch('builtins.print'):
            guard.main([json.dumps(LIMITS), '--', 'vllm', 'serve', 'meta-models/Muse-Glimmer-30B'])
        execvp.assert_called_once_with('vllm', ['vllm', 'serve', 'meta-models/Muse-Glimmer-30B'])



SERVER = r"""
import os, socket, sys, time
mode, port = sys.argv[1], int(sys.argv[sys.argv.index('--port') + 1])
print('INFO starting server', flush=True)
print('WARNING to stderr', file=sys.stderr, flush=True)
if mode == 'flood':  # 3 MiB without a newline, then an error after it
    for _ in range(48):
        os.write(1, b'x' * 65536)
    print('torch.OutOfMemoryError: CUDA out of memory', file=sys.stderr, flush=True)
    sys.exit(1)
if mode == 'late':  # quiet past the inspection window, then fails
    time.sleep(3)
    print('torch.OutOfMemoryError: CUDA out of memory', file=sys.stderr, flush=True)
    sys.exit(1)
if mode == 'oom':
    print('Traceback (most recent call last):', file=sys.stderr)
    print('(VllmWorker TP1 pid=7) torch.OutOfMemoryError: CUDA out of memory. Tried SECRET-TEXT', file=sys.stderr)
    sys.exit(1)
s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1); s.bind(('127.0.0.1', port)); s.listen()
time.sleep(60)
"""
DRIVER = "import importlib.util, sys; spec = importlib.util.spec_from_file_location('g', sys.argv[1]); " \
         "g = importlib.util.module_from_spec(spec); spec.loader.exec_module(g); " \
         "sys.exit(g.supervise(sys.argv[5:], sys.argv[2], int(sys.argv[3]), int(sys.argv[4])))"


class StatusTest(unittest.TestCase):
    """The persistent service's guard: status file, supervision, pass-through and exit propagation."""

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix='guard-status-'))
        self.status = self.dir / 'status.json'
        (self.dir / 'server.py').write_text(SERVER)
        with socket.socket() as s:
            s.bind(('127.0.0.1', 0))
            self.port = s.getsockname()[1]

    def tearDown(self):
        import shutil
        shutil.rmtree(self.dir, ignore_errors=True)

    def read(self):
        return json.loads(self.status.read_text())

    def driver(self, mode, max_bytes=2 << 20, max_seconds=600):
        cmd = [sys.executable, str(self.dir / 'server.py'), mode, '--host', '127.0.0.1', '--port', str(self.port)]
        return subprocess.Popen([sys.executable, '-c', DRIVER, str(HERE / 'startup_guard.py'), str(self.status),
                                 str(max_bytes), str(max_seconds), *cmd],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def test_refusal_writes_codes_only(self):
        limits = {**LIMITS, 'status_path': str(self.status)}
        node = facts(gpus=[{'name': 'x', 'total': 42 * 10**9, 'free': 30 * 10**9}] * 2, shm_total=64 * 2**20)
        with mock.patch.object(guard, 'collect', return_value=node), mock.patch('builtins.print'), \
                self.assertRaises(SystemExit) as stop:
            guard.main([json.dumps(limits), '--', 'vllm', 'serve'])
        self.assertEqual(stop.exception.code, guard.EXIT_REFUSED)
        status = self.read()
        self.assertEqual((status['stage'], status['proceed'], status['failures']),
                         ('guard_refused', False, ['gpu_free', 'gpu_free', 'shm']))
        self.assertNotIn('bytes', self.status.read_text())

    def test_server_failure_exit_and_fixed_categories_without_text(self):
        proc = self.driver('oom')
        out, err = proc.communicate(timeout=30)
        self.assertEqual(proc.returncode, 1)  # the server's own status, as `docker run` and systemd see it
        self.assertIn(b'INFO starting server', out)  # passed through unchanged
        self.assertIn(b'SECRET-TEXT', err)
        status = self.read()
        self.assertEqual((status['stage'], status['exitCode'], status['signal'], status['listenedAfterS'],
                          status['firstCategory'], status['categories'], status['limitReached']),
                         ('exited', 1, None, None, 'cuda_out_of_memory', ['cuda_out_of_memory', 'traceback'], None))
        self.assertNotIn('SECRET', self.status.read_text())
        self.assertEqual((status['exceptionType'], status['exceptionProcess'], status['tracebacks']),
                         ('OutOfMemoryError', 'worker', 1))  # allowlisted tokens; the message never leaves

    def test_oversized_output_without_newlines_is_inspected_only_to_the_byte_bound(self):
        proc = self.driver('flood')
        out, err = proc.communicate(timeout=60)
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(out.count(b'x'), 48 * 65536)  # every byte still passed through
        status = self.read()
        self.assertEqual((status['bytesInspected'], status['limitReached']), (2 << 20, 'bytes'))
        self.assertIsNone(status['firstCategory'])  # the error came after the bound

    def test_inspection_expires_while_the_server_keeps_running(self):
        proc = self.driver('late', max_seconds=1)
        out, err = proc.communicate(timeout=60)
        self.assertEqual(proc.returncode, 1)
        self.assertIn(b'OutOfMemoryError', err)  # passed through
        status = self.read()
        self.assertEqual((status['limitReached'], status['firstCategory']), ('time', None))

    def test_capture_bounds_and_window(self):
        clock = [0.0]
        cap = guard.Capture(max_bytes=100, max_seconds=10, clock=lambda: clock[0])
        self.assertTrue(cap.feed(b'error: unrecognized ar'))
        self.assertTrue(cap.feed(b'guments: --bogus'))  # matched across chunks
        self.assertFalse(cap.feed(b'y' * 200))
        self.assertEqual(cap.summary(), {'firstCategory': 'argument_rejected', 'categories': ['argument_rejected'],
                                         'bytesInspected': 100, 'limitReached': 'bytes', 'guard': None,
                                         'tracebacks': 0, 'exceptionType': None, 'exceptionLocation': None,
                                         'exceptionProcess': None})
        cap = guard.Capture(max_bytes=100, max_seconds=10, clock=lambda: clock[0])
        clock[0] = 11
        self.assertFalse(cap.feed(b'Traceback (most recent call last)'))
        self.assertEqual(cap.summary()['limitReached'], 'time')
        self.assertLessEqual(len(guard.Capture().tail), guard.WINDOW)

    def test_supervision_is_default_off_and_one_attempt_only(self):
        launched = []
        def run(limits, token=False):
            if token:
                (self.dir / 'supervise-once').write_text('')
            with mock.patch.object(guard, 'collect', return_value=facts()), mock.patch('builtins.print'), \
                    mock.patch.object(guard.os, 'execvp', side_effect=lambda c, a: launched.append('exec')), \
                    mock.patch.object(guard, 'supervise', side_effect=lambda *a: launched.append('supervise') or 0):
                try:
                    guard.main([json.dumps(limits), '--', 'vllm', 'serve'])
                except SystemExit:
                    pass
        bounds = {'max_bytes': 2 << 20, 'max_seconds': 600}
        run({**LIMITS, 'status_path': str(self.status)}, token=True)  # token without bounds: off
        self.assertTrue((self.dir / 'supervise-once').exists())  # and left unconsumed
        (self.dir / 'supervise-once').unlink()
        run({**LIMITS, 'status_path': str(self.status), 'supervise': bounds})  # bounds without token: off
        run({**LIMITS, 'status_path': str(self.status), 'supervise': bounds}, token=True)  # explicit: once
        run({**LIMITS, 'status_path': str(self.status), 'supervise': bounds})  # the token was consumed
        self.assertEqual(launched, ['exec', 'exec', 'supervise', 'exec'])
        self.assertTrue((self.dir / 'supervise-once.used').exists())
        self.assertEqual(self.read()['stage'], 'guard_passed')

    def test_listening_then_signal_is_forwarded_and_propagated(self):
        proc = self.driver('serve')
        deadline = time.time() + 30
        while time.time() < deadline:
            if self.status.exists() and self.read()['stage'] == 'listening':
                break
            time.sleep(0.2)
        self.assertEqual(self.read()['stage'], 'listening')
        proc.send_signal(signal.SIGTERM)  # docker stop
        proc.communicate(timeout=30)
        self.assertEqual(proc.returncode, 128 + signal.SIGTERM)
        status = self.read()
        self.assertEqual((status['stage'], status['signal'], status['exitCode']), ('exited', signal.SIGTERM, None))
        self.assertIsInstance(status['listenedAfterS'], int)

    def test_unwritable_status_never_changes_the_outcome(self):
        self.status = self.dir / 'missing-dir' / 'status.json'
        proc = self.driver('oom')
        proc.communicate(timeout=30)
        self.assertEqual(proc.returncode, 1)


ENGINE_TRACEBACK = """INFO 10-31 14:31:02 [__init__.py:216] Automatically detected platform cuda.
(EngineCore_DP0 pid=212) ERROR 10-31 14:35:40 [core.py:708] EngineCore failed to start.
(EngineCore_DP0 pid=212) Traceback (most recent call last):
(EngineCore_DP0 pid=212)   File "/usr/local/lib/python3.12/dist-packages/vllm/v1/engine/core.py", line 699, in run_engine_core
(EngineCore_DP0 pid=212)     engine_core = EngineCoreProc(*args, **kwargs)
(EngineCore_DP0 pid=212)   File "/usr/local/lib/python3.12/dist-packages/vllm/v1/executor/multiproc_executor.py", line 90, in _init_executor
(EngineCore_DP0 pid=212)     self.workers = WorkerProc.wait_for_ready(unready_workers)
(EngineCore_DP0 pid=212)   File "/usr/local/lib/python3.12/dist-packages/vllm/model_executor/model_loader/weight_utils.py", line 412, in safetensors_weights_iterator
(EngineCore_DP0 pid=212)     with safe_open(st_file, framework="pt") as f:
(EngineCore_DP0 pid=212) safetensors_rust.SafetensorError: Error while deserializing header: SECRET-TEXT /root/.cache/hf_TOKEN_abc
Traceback (most recent call last):
  File "/usr/local/bin/vllm", line 10, in <module>
    sys.exit(main())
  File "/usr/local/lib/python3.12/dist-packages/vllm/entrypoints/cli/main.py", line 54, in main
    args.dispatch_function(args)
RuntimeError: Engine core initialization failed. See root cause above. Failed core proc(s): {'EngineCore_DP0': 1}
"""
GUARD_CRASH = """Traceback (most recent call last):
  File "<string>", line 118, in <module>
  File "<string>", line 98, in main
KeyError: 'cache_dir'
"""


def guard_line(proceed, pad=0):
    """The guard's own evidence line, as main() prints it, long enough to exceed the window when padded."""
    node = facts(gpus=[{'name': 'NVIDIA A100-SXM4-40GB' + 'x' * pad, 'total': 42 * 10**9, 'free': 41 * 10**9}] * 2)
    return json.dumps({'guard': 'muse-glimmer-startup', 'utc': '2026-10-31T14:31:00Z', 'facts': node, 'limits': LIMITS,
                       'failures': [] if proceed else ['GPU0 has 3 free bytes'], 'proceed': proceed}) + '\n'


class ClassificationTest(unittest.TestCase):
    """Fixed-vocabulary classification of the last traceback: type, innermost component, process; guard result."""

    def feed(self, text, chunk=None, **bounds):
        cap = guard.Capture(**bounds)
        data = text.encode()
        step = chunk or len(data) or 1
        for i in range(0, len(data), step):
            cap.feed(data[i:i + step])
        return cap

    def assertFixed(self, summary):
        vocab = {'guard': {None, 'passed', 'refused'},
                 'exceptionType': set(getattr(guard, 'EXCEPTION_TYPES', ())) | {None, 'other'},
                 'exceptionLocation': set(getattr(guard, 'LOCATION_NAMES', ())) | {None},
                 'exceptionProcess': set(getattr(guard, 'PROCESS_NAMES', ())) | {None}}
        for key, allowed in vocab.items():
            self.assertIn(summary.get(key), allowed, key)
        dumped = json.dumps(summary)
        for leak in ('SECRET', 'hf_TOKEN', '/usr/local', '.py', 'line 4', 'deserializing', 'EngineCore_DP0', 'pid=',
                     'cache_dir', 'Failed core'):
            self.assertNotIn(leak, dumped)

    def test_engine_core_traceback_last_exception_in_any_chunking(self):
        text = guard_line(True) + ENGINE_TRACEBACK
        for chunk in (None, 1, 7, 64, 1000):
            with self.subTest(chunk=chunk):
                s = self.feed(text, chunk).summary()
                self.assertFixed(s)
                self.assertEqual((s['guard'], s['tracebacks'], s['exceptionType'], s['exceptionLocation'],
                                  s['exceptionProcess']), ('passed', 2, 'RuntimeError', 'entrypoint', 'main'))
        first = self.feed(guard_line(True) + ENGINE_TRACEBACK.split('Traceback (most recent call last):\n  File "/usr/local/bin')[0]).summary()
        self.assertEqual((first['exceptionType'], first['exceptionLocation'], first['exceptionProcess']),
                         ('SafetensorError', 'model_loader', 'engine_core'))

    def test_guard_pass_refusal_and_crash_are_distinguished(self):
        self.assertEqual(self.feed(guard_line(True, pad=2000), chunk=333).summary()['guard'], 'passed')  # > window
        self.assertEqual(self.feed(guard_line(False, pad=2000), chunk=333).summary()['guard'], 'refused')
        crash = self.feed(GUARD_CRASH, chunk=5).summary()
        self.assertFixed(crash)
        self.assertEqual((crash['guard'], crash['exceptionType'], crash['exceptionLocation'], crash['exceptionProcess']),
                         (None, 'KeyError', 'guard', 'main'))

    def test_unknown_classes_paths_and_prefixes_become_other(self):
        text = ("(SomeHelper pid=9) Traceback (most recent call last):\n"
                "(SomeHelper pid=9)   File \"/opt/private/secret_project/run.py\", line 3, in go\n"
                "(SomeHelper pid=9) acme.internal.SecretTokenLeakError: token=hf_TOKEN_abc SECRET\n")
        s = self.feed(text, chunk=3).summary()
        self.assertFixed(s)
        self.assertEqual((s['exceptionType'], s['exceptionLocation'], s['exceptionProcess']), ('other', 'other', 'other'))
        self.assertNotIn('SecretToken', json.dumps(s))

    def test_log_lines_outside_a_traceback_are_not_exceptions(self):
        s = self.feed('ValueError: logged but not raised SECRET\nINFO RuntimeError mentioned\n').summary()
        self.assertEqual((s['exceptionType'], s['tracebacks']), (None, 0))

    def test_oversized_lines_progress_bars_and_bounds(self):
        bar = ''.join(f'\rDownloading shards: {i}% SECRET' for i in range(2000))  # \r-separated, no newline
        long_line = 'x' * (5 << 20)  # 5 MiB without a newline
        cap = self.feed(bar + long_line + '\n' + ENGINE_TRACEBACK, chunk=65536)
        s = cap.summary()
        self.assertEqual((s['bytesInspected'], s['limitReached'], s['exceptionType']), (2 << 20, 'bytes', None))
        self.assertLessEqual(len(getattr(cap, 'head', '')), guard.WINDOW)
        self.assertLessEqual(len(getattr(cap, 'end', '')), 64)
        self.assertFixed(s)
        small = self.feed(ENGINE_TRACEBACK, max_bytes=200).summary()  # the exception lies past the byte bound
        self.assertEqual((small['exceptionType'], small['limitReached']), (None, 'bytes'))
        clock = [0.0]
        cap = guard.Capture(max_seconds=5, clock=lambda: clock[0])
        cap.feed(b'Traceback (most recent call last):\n')
        clock[0] = 6
        cap.feed(b'  File "/x/vllm/config/model.py", line 1, in f\nValueError: late\n')  # after expiry
        self.assertEqual((cap.summary()['limitReached'], cap.summary()['exceptionType'], cap.summary()['tracebacks']),
                         ('time', None, 1))

    def test_traceback_count_is_bounded(self):
        cap = self.feed('Traceback (most recent call last):\n' * 1050)
        self.assertEqual(cap.summary().get('tracebacks'), 1000)

if __name__ == '__main__':
    unittest.main()
