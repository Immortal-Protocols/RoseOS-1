"""Model upload renderer: pinned regional image only, 8K contract, API-only fields, and the rendered guard command run as a real process."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
import capacity  # noqa: E402
import render_upload  # noqa: E402

CONFIG = capacity.load()
DIGEST = CONFIG['options'][0]['runtime']['image_digest']
GOOD = f'us-central1-docker.pkg.dev/example-models/models-serving/vllm-openai@{DIGEST}'
FAKE_TORCH = '''
def _total(i): return 42*10**9
class cuda:
    @staticmethod
    def device_count(): return 2
    @staticmethod
    def mem_get_info(i): return (41*10**9, 42*10**9)
    @staticmethod
    def get_device_name(i): return 'synthetic'
'''


class RenderTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.dir = Path(temp.name)

    def cli(self, *argv):
        return subprocess.run([sys.executable, '-I', str(HERE/'render_upload.py'), *argv],
            capture_output=True, text=True, timeout=60)

    def render_file(self, image=GOOD, config=None):
        out = self.dir/'upload.json'
        argv = ['--image-uri', image, '--out', str(out)]
        if config:
            argv += ['--config', str(config)]
        return self.cli(*argv), out

    def test_cli_writes_body_and_matching_summary(self):
        run, out = self.render_file()
        self.assertEqual(run.returncode, 0, run.stderr)
        summary = json.loads(run.stdout)
        data = out.read_bytes()
        import hashlib
        self.assertEqual(summary['sha256'], hashlib.sha256(data).hexdigest())
        body = json.loads(data)
        self.assertEqual(set(body), {'model'})
        self.assertEqual(set(body['model']), {'displayName', 'description', 'containerSpec'})
        spec = body['model']['containerSpec']
        self.assertLessEqual(set(spec), render_upload.CONTAINER_FIELDS)
        self.assertNotIn('command_rendered_by', json.dumps(body))
        self.assertEqual(spec['imageUri'], GOOD)
        args = spec['args']
        self.assertEqual(args[args.index('--max-model-len')+1], '8192')
        self.assertEqual(args[args.index('--max-num-seqs')+1], '1')
        self.assertEqual(args[args.index('--tensor-parallel-size')+1], '2')
        self.assertEqual(spec['command'][-3:], ['--', 'vllm', 'serve'])
        self.assertEqual(json.loads(spec['command'][3]), summary['guard_limits'])
        # Agent Platform expands $(VAR) in command/args; the inline guard must contain none.
        self.assertNotIn('$(', json.dumps(spec))
        self.assertNotIn('import capacity', spec['command'][2])

    def test_rejects_unpinned_or_foreign_images(self):
        index = CONFIG['options'][0]['runtime']['image_index_digest']
        for image in ('us-central1-docker.pkg.dev/example-models/models-serving/vllm-openai:muse-glimmer',
                      f'us-central1-docker.pkg.dev/example-models/models-serving/vllm-openai@{index}',
                      f'us-west1-docker.pkg.dev/example-models/models-serving/vllm-openai@{DIGEST}',
                      f'docker.io/vllm/vllm-openai@{DIGEST}',
                      f'us-docker.pkg.dev/example-models/models-serving/vllm-openai@{DIGEST}',
                      GOOD + ' '):
            run, out = self.render_file(image)
            self.assertEqual(run.returncode, 2, image)
            self.assertIn('rejected', run.stderr)
            self.assertFalse(out.exists())

    def test_rejects_inconsistent_context(self):
        cases = []
        stale = copy.deepcopy(CONFIG)
        args = stale['options'][0]['container_spec']['args']
        args[args.index('--max-model-len')+1] = '16384'
        cases.append(stale)
        overflow = copy.deepcopy(CONFIG)
        overflow['prompt_envelope']['acceptance_max_tokens'] = 2048
        cases.append(overflow)
        seqs = copy.deepcopy(CONFIG)
        args = seqs['options'][0]['container_spec']['args']
        args[args.index('--max-num-seqs')+1] = '4'
        cases.append(seqs)
        for i, config in enumerate(cases):
            path = self.dir/f'config{i}.json'
            path.write_text(json.dumps(config))
            run, out = self.render_file(config=path)
            self.assertEqual(run.returncode, 2, run.stdout)
            self.assertFalse(out.exists())

    def test_8k_limits_are_below_16k(self):
        limits = render_upload.guard_limits(CONFIG, CONFIG['options'][0])
        wide = copy.deepcopy(CONFIG)
        wide['demo_assumptions']['max_model_len'] = 16384
        self.assertLess(limits['gpu_total_min_bytes'], render_upload.guard_limits(wide, wide['options'][0])['gpu_total_min_bytes'])

    def run_rendered(self, env_extra=None):
        run, out = self.render_file()
        spec = json.loads(out.read_text())['model']['containerSpec']
        command = [sys.executable if spec['command'][0] == 'python3' else spec['command'][0], *spec['command'][1:]]
        env = {'PATH': os.environ['PATH'], 'HOME': str(self.dir)}
        env.update(env_extra or {})
        return subprocess.run(command + spec['args'], capture_output=True, text=True, timeout=60, env=env)

    def test_rendered_command_fails_closed_on_this_host(self):
        run = self.run_rendered()
        self.assertEqual(run.returncode, 3, run.stderr)
        evidence = json.loads(run.stdout.strip().splitlines()[-1])
        self.assertFalse(evidence['proceed'])
        self.assertEqual(evidence['limits']['gpu_count'], 2)
        self.assertTrue(any('GPUs visible' in f for f in evidence['failures']))

    def test_rendered_command_reads_synthetic_gpus_and_still_refuses_without_driver(self):
        fake = self.dir/'fake'
        fake.mkdir()
        (fake/'torch.py').write_text(FAKE_TORCH)
        run = self.run_rendered({'PYTHONPATH': str(fake)})
        self.assertEqual(run.returncode, 3, run.stderr)
        evidence = json.loads(run.stdout.strip().splitlines()[-1])
        self.assertEqual(len(evidence['facts']['gpus']), 2)
        self.assertFalse(any('GPU' in f for f in evidence['failures']), evidence['failures'])
        self.assertTrue(any('driver' in f for f in evidence['failures']))


if __name__ == '__main__':
    unittest.main()
