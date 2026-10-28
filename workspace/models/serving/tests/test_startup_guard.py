"""Startup guard: synthetic node facts pass or refuse before any weights download, and the rendered command fails closed."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
spec = importlib.util.spec_from_file_location('startup_guard', HERE/'startup_guard.py')
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)
import capacity  # noqa: E402

CONFIG = capacity.load()
A100 = CONFIG['options'][0]
LIMITS = guard.limits_for(CONFIG, A100)
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

    def test_rendered_command_fails_closed_without_gpus(self):
        command = guard.container_command(CONFIG, A100)
        self.assertEqual(command[:2], ['python3', '-c'])
        self.assertEqual(command[-3:], ['--', 'vllm', 'serve'])
        run = subprocess.run([sys.executable, '-I', '-c', command[2], command[3], '--', 'true'],
            capture_output=True, text=True, timeout=60)
        self.assertEqual(run.returncode, guard.EXIT_REFUSED, run.stderr)
        evidence = json.loads(run.stdout.strip().splitlines()[-1])
        self.assertFalse(evidence['proceed'])
        self.assertTrue(any('GPUs visible' in f for f in evidence['failures']))


if __name__ == '__main__':
    unittest.main()
