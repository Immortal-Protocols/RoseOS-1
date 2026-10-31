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
import socket, sys, time
mode, port = sys.argv[1], int(sys.argv[sys.argv.index('--port') + 1])
print('INFO starting server', flush=True)
print('WARNING to stderr', file=sys.stderr, flush=True)
if mode == 'oom':
    print('Traceback (most recent call last):', file=sys.stderr)
    print('(VllmWorker TP1 pid=7) torch.OutOfMemoryError: CUDA out of memory. Tried SECRET-TEXT', file=sys.stderr)
    sys.exit(1)
s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1); s.bind(('127.0.0.1', port)); s.listen()
time.sleep(60)
"""
DRIVER = "import importlib.util, sys; spec = importlib.util.spec_from_file_location('g', sys.argv[1]); " \
         "g = importlib.util.module_from_spec(spec); spec.loader.exec_module(g); sys.exit(g.supervise(sys.argv[3:], sys.argv[2]))"


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

    def driver(self, mode):
        cmd = [sys.executable, str(self.dir / 'server.py'), mode, '--host', '127.0.0.1', '--port', str(self.port)]
        return subprocess.Popen([sys.executable, '-c', DRIVER, str(HERE / 'startup_guard.py'), str(self.status), *cmd],
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

    def test_server_failure_exit_and_exception_class_without_text(self):
        proc = self.driver('oom')
        out, err = proc.communicate(timeout=30)
        self.assertEqual(proc.returncode, 1)  # the server's own status, as `docker run` and systemd see it
        self.assertIn(b'INFO starting server', out)  # passed through unchanged
        self.assertIn(b'SECRET-TEXT', err)
        status = self.read()
        self.assertEqual((status['stage'], status['exitCode'], status['signal'], status['listenedAfterS'],
                          status['lastException']), ('exited', 1, None, None, 'torch.OutOfMemoryError'))
        self.assertNotIn('SECRET', self.status.read_text())

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

if __name__ == '__main__':
    unittest.main()
