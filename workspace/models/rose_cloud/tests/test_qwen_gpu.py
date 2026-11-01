"""rose_cloud/qwen_gpu.py (CLOUD-QWEN-GPU-001) with local fixtures only: a stand-in ``vllm`` package that records how
it is called, fixture mount/net/dev tables and small fixture files. Nothing here imports the real vLLM, touches a GPU,
loads weights or contacts a VM; the real image, vLLM version, kernels and timings are unverified until a separately
approved run.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path[:0] = [str(ROOT)]

from rose_cloud import qwen_gpu as G  # noqa: E402

MANIFEST = json.loads((ROOT / 'rose_cloud' / 'manifest.json').read_text())
SOURCE = (ROOT / 'rose_cloud' / 'qwen_gpu.py').read_text()

VLLM = '''
import json, os, signal, subprocess, sys, time
from dataclasses import make_dataclass
__version__ = '0.99.1'
HERE = os.path.dirname(__file__)
MODE = json.load(open(os.path.join(HERE, 'mode.json')))
def log(kind, **kw):
    with open(os.path.join(HERE, 'calls.log'), 'a') as f:
        f.write(json.dumps({'kind': kind, **kw}) + '\\n')
class SamplingParams:
    def __init__(self, **kw):
        self.kw = kw
        log('sampling', kw=kw)
class _Tok:
    def encode(self, text):
        return [len(w) for w in text.split()]
    def __len__(self):
        return 1000
class _Out:
    def __init__(self, ids, reason):
        self.token_ids, self.finish_reason = ids, reason
class _Res:
    def __init__(self, outs):
        self.outputs = outs
class _Cfg:
    quantization = 'fp8'
class _Engine:
    model_config = _Cfg()
class LLM:
    def __init__(self, model, tokenizer, **kw):
        log('llm', model=model, tokenizer=tokenizer, kw=kw, env={k: os.environ.get(k) for k in (
            'HF_HUB_OFFLINE', 'TRANSFORMERS_OFFLINE', 'VLLM_NO_USAGE_STATS', 'DO_NOT_TRACK', 'VLLM_HOST_IP', 'HOME',
            'VLLM_CACHE_ROOT', 'TRITON_CACHE_DIR', 'HTTPS_PROXY', 'https_proxy')})
        if MODE.get('worker'):  # an engine worker in the same session, like vLLM's multiprocessing executor
            w = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])
            open(os.path.join(HERE, 'worker.pid'), 'w').write(str(w.pid))
        if MODE.get('load') == 'fail':
            raise RuntimeError('CUDA out of memory at /mnt/SECRET')
        if MODE.get('load') == 'hang':
            time.sleep(120)
        if MODE.get('load') == 'crash':
            os.kill(os.getpid(), signal.SIGSEGV)
        if MODE.get('noise'):
            sys.stdout.write('INFO ' + 'x' * (1 << 20) + '\\n')
        self.llm_engine = _Engine()
    def get_tokenizer(self):
        return _Tok()
    def generate(self, prompts, params, use_tqdm=True):
        log('generate', prompts=prompts, use_tqdm=use_tqdm)
        if MODE.get('generate') == 'fail':
            raise ValueError('generated SECRET text')
        n = params.kw['max_tokens']
        return [_Res([_Out(list(range(100, 100 + n)), 'length')])]
'''

ARG_UTILS = '''
import json, os
from dataclasses import make_dataclass
MODE = json.load(open(os.path.join(os.path.dirname(os.path.dirname(__file__)), 'mode.json')))
FIELDS = ['model', 'tokenizer', 'tensor_parallel_size', 'max_model_len', 'max_num_seqs', 'gpu_memory_utilization',
          'enforce_eager', 'language_model_only', 'trust_remote_code', 'seed', 'distributed_executor_backend',
          'disable_log_stats']
EngineArgs = make_dataclass('EngineArgs', [(f, object, None) for f in FIELDS if f not in MODE.get('drop', [])])
'''


def gitsha1(data):
    return hashlib.sha1(b'blob %d\0' % len(data) + data).hexdigest()


class Fixture(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix='qwen-gpu-'))
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.staging = self.dir / 'rose-cloud'
        self.model = self.staging / 'qwen-fixture'
        self.tmp, self.shm, self.extra = self.dir / 'tmp', self.dir / 'shm', self.dir / 'extra'
        for d in (self.model, self.tmp, self.shm, self.extra / 'vllm' / 'engine', self.dir / 'net' / 'lo',
                  self.dir / 'dev'):
            d.mkdir(parents=True)
        for n in ('nvidia0', 'nvidia1', 'nvidiactl', 'nvidia-uvm', 'null'):
            (self.dir / 'dev' / n).write_text('')
        (self.extra / 'vllm' / '__init__.py').write_text(VLLM)
        (self.extra / 'vllm' / 'engine' / '__init__.py').write_text('')
        (self.extra / 'vllm' / 'engine' / 'arg_utils.py').write_text(ARG_UTILS)
        self.mode({})
        files = []
        for name, data in (('config.json', b'{"model_type": "qwen3_5_moe"}'), ('tokenizer.json', b'{}')):
            (self.model / name).write_bytes(data)
            files.append([name, len(data), 'gitsha1', gitsha1(data)])
        for name in ('layers-0.safetensors', 'model.safetensors.index.json'):
            (self.model / name).write_bytes(b'w' * 64)
            files.append([name, 64, 'sha256', 'f' * 64])  # weights: size only, never hashed
        self.manifest = {'stagingRoot': str(self.staging), 'groups': {
            'qwen': {'repo': G.REPO, 'revision': G.REVISION, 'dir': 'qwen-fixture', 'files': files}}}
        self.write_mounts()
        self.limits = {'deadlineS': 60, 'tmpMaxBytes': 1 << 62, 'shmMaxBytes': 1 << 62,
                       'mountsFile': str(self.dir / 'mounts'), 'netDir': str(self.dir / 'net'),
                       'devDir': str(self.dir / 'dev'), 'tmpRoot': str(self.tmp), 'shmRoot': str(self.shm),
                       'extraPath': str(self.extra)}

    def mode(self, mode):
        (self.extra / 'vllm' / 'mode.json').write_text(json.dumps(mode))

    def write_mounts(self, tmp='rw,nosuid,nodev,size=8388608k', root='ro', model='ro', shm_type='tmpfs'):
        (self.dir / 'mounts').write_text(
            f'overlay / overlay {root},relatime 0 0\n'
            f'tmpfs {self.tmp} tmpfs {tmp} 0 0\n'
            f'shm {self.shm} {shm_type} rw,nosuid,nodev,size=4194304k 0 0\n'
            f'/dev/sdb {self.model} ext4 {model},relatime 0 0\n')

    def calls(self, kind=None):
        p = self.extra / 'vllm' / 'calls.log'
        rows = [json.loads(l) for l in p.read_text().splitlines()] if p.exists() else []
        return [r for r in rows if kind is None or r['kind'] == kind]

    def diagnose(self, **limits):
        return G.diagnose(self.manifest, dict(self.limits, **limits))


class QwenGpu(Fixture):
    def test_one_fixed_request_with_exact_engine_and_sampling(self):
        opened = []
        real = G.digest
        G.digest = lambda path, kind, size: (opened.append(path), real(path, kind, size))[1]
        self.addCleanup(setattr, G, 'digest', real)
        r = self.diagnose()
        self.assertEqual(r['stop'], 'done')
        self.assertEqual(r['inputs'], {'qwenFiles': {'expected': 2, 'verified': 2, 'missing': 0, 'mismatch': 0},
                                       'weights': {'expected': 2, 'sizeOk': 2, 'missing': 0, 'sizeMismatch': 0}})
        self.assertFalse([p for p in opened if 'safetensors' in p])  # weights checked by size, never read here
        e = r['engine']
        self.assertEqual((e['run'], e['step'], e['vllm'], e['unsupported'], e['generatedTokens'], e['finishReason'],
                          e['inVocab'], e['quantization']),
                         ('ok', 'done', '0.99.1', [], 32, 'length', True, 'fp8'))
        self.assertEqual(e['outputSha256'], hashlib.sha256(json.dumps(list(range(100, 132))).encode()).hexdigest())
        (llm,) = self.calls('llm')
        self.assertEqual((llm['model'], llm['tokenizer'], llm['kw']), (str(self.model), str(self.model), G.ENGINE))
        self.assertEqual(G.ENGINE, {'tensor_parallel_size': 2, 'max_model_len': 2048, 'max_num_seqs': 1,
                                    'gpu_memory_utilization': 0.9, 'enforce_eager': True, 'language_model_only': True,
                                    'trust_remote_code': False, 'seed': 0, 'distributed_executor_backend': 'mp',
                                    'disable_log_stats': True})
        self.assertEqual([c['kw'] for c in self.calls('sampling')], [{'max_tokens': 32, 'temperature': 0.0,
                                                                      'seed': 0}])
        (gen,) = self.calls('generate')  # exactly one request, token ids of the fixed prompt, no progress bar
        self.assertEqual((gen['prompts'], gen['use_tqdm']),
                         ([{'prompt_token_ids': [len(w) for w in G.PROMPT.split()]}], False))
        env = llm['env']
        self.assertEqual((env['HF_HUB_OFFLINE'], env['TRANSFORMERS_OFFLINE'], env['VLLM_NO_USAGE_STATS'],
                          env['DO_NOT_TRACK'], env['VLLM_HOST_IP'], env['HTTPS_PROXY'], env['https_proxy']),
                         ('1', '1', '1', '1', '127.0.0.1', None, None))
        for k in ('HOME', 'VLLM_CACHE_ROOT', 'TRITON_CACHE_DIR'):
            self.assertTrue(env[k].startswith(str(self.tmp)))  # every cache on the bounded tmpfs
        self.assertEqual(sorted(os.listdir(self.model)), ['config.json', 'layers-0.safetensors',
                                                          'model.safetensors.index.json', 'tokenizer.json'])
        line = json.dumps(G.sanitize(r))
        for leak in (str(self.dir), 'Count', 'SECRET', 'prompt_token_ids'):
            self.assertNotIn(leak, line)

    def test_first_failures_are_fixed_categories(self):
        cases = ((dict(drop=['language_model_only']), 'engine_arg_unsupported', 'engine_args', None, 0),
                 (dict(load='fail'), 'engine_failed', 'load', 'RuntimeError', 0),
                 (dict(generate='fail'), 'generate_failed', 'generate', 'ValueError', 1),
                 (dict(noise=True, generate='fail'), 'generate_failed', 'generate', 'ValueError', 1))
        for mode, stop, step, error, generates in cases:
            with self.subTest(mode=mode):
                (self.extra / 'vllm' / 'calls.log').unlink(missing_ok=True)
                self.mode(mode)
                r = self.diagnose()
                e = r['engine']
                self.assertEqual((r['stop'], e['step'], e['error']), (stop, step, error))
                self.assertEqual(len(self.calls('generate')), generates)  # nothing retried
                if stop == 'engine_arg_unsupported':
                    self.assertEqual(e['unsupported'], ['language_model_only'])
                    self.assertEqual(self.calls('llm'), [])  # refused before any load: no silent vision-tower load
                self.assertNotIn('SECRET', json.dumps(G.sanitize(r)))
        shutil.rmtree(self.extra / 'vllm')  # vLLM not importable at all
        r = self.diagnose()
        self.assertEqual((r['stop'], r['engine']['step'], r['engine']['error'], r['engine']['module']),
                         ('engine_failed', 'import', 'ModuleNotFoundError', 'vllm'))

    def test_crash_and_deadline_kill_the_whole_group(self):
        self.mode({'load': 'crash'})
        r = self.diagnose()
        self.assertEqual((r['stop'], r['engine']['run']), ('crashed', 'crashed'))
        self.mode({'load': 'hang', 'worker': True})
        t = time.monotonic()
        r = self.diagnose(deadlineS=4)
        self.assertLess(time.monotonic() - t, 12)
        self.assertEqual((r['stop'], r['engine']['run'], r['engine'].get('step')), ('deadline', 'timeout', None))
        pid = int((self.extra / 'vllm' / 'worker.pid').read_text())
        time.sleep(0.2)
        try:
            with open(f'/proc/{pid}/stat') as f:
                alive = f.read().split()[2] != 'Z'
        except OSError:
            alive = False
        self.assertFalse(alive)  # the engine worker died with the group, not left running
        self.assertEqual(self.calls('generate'), [])

    def test_script_marker_line_end_to_end(self):
        import subprocess
        out = subprocess.run([sys.executable, '-I', '-c', SOURCE, json.dumps(self.manifest), json.dumps(self.limits)],
                             capture_output=True, text=True, timeout=120).stdout
        line = out.strip().splitlines()[-1]
        self.assertTrue(line.startswith(G.MARKER))
        raw = json.loads(line[len(G.MARKER):])
        self.assertEqual(raw['stop'], 'done')
        self.assertEqual(G.sanitize(raw)['engine']['generatedTokens'], 32)
        self.assertNotIn('Count', out)


class Refusals(Fixture):
    def assertNothingRan(self, r):
        self.assertIsNone(r['engine'])
        self.assertEqual(self.calls(), [])
        self.assertFalse((self.tmp / 'rose-qwen-gpu').exists())

    def test_muse_or_other_manifests_are_refused(self):
        for repo, revision in (('meta-models/Muse-Glimmer-30B', 'cec94fcfd6512f2cfe708138c32533b346ea4f69'),
                               (G.REPO, 'main'), ('Qwen/Qwen3.6-35B-A3B', G.REVISION)):
            with self.subTest(repo=repo, revision=revision):
                m = json.loads(json.dumps(self.manifest))
                m['groups']['qwen'].update(repo=repo, revision=revision)
                r = G.diagnose(m, self.limits)
                self.assertEqual(r['stop'], 'bad_manifest')
                self.assertNothingRan(r)
        m = json.loads(json.dumps(self.manifest))
        m['groups']['qwen']['files'][0][0] = '../escape'
        self.assertEqual(G.diagnose(m, self.limits)['stop'], 'bad_manifest')
        self.assertEqual(G.diagnose({'groups': {}}, self.limits)['stop'], 'bad_manifest')

    def test_isolation_refusals(self):
        cases = {'tmp_too_big': (dict(), dict(tmpMaxBytes=1 << 20), 'tmpBounded'),
                 'shm_too_big': (dict(), dict(shmMaxBytes=1 << 20), 'shmBounded'),
                 'tmp_noexec': (dict(tmp='rw,noexec,size=1k'), {}, 'tmpExec'),
                 'shm_not_tmpfs': (dict(shm_type='ext4'), {}, 'shmBounded'),
                 'root_rw': (dict(root='rw'), {}, 'rootReadOnly'),
                 'model_rw': (dict(model='rw'), {}, 'modelReadOnly')}
        for name, (mounts, limits, key) in cases.items():
            with self.subTest(name):
                self.write_mounts(**mounts)
                r = self.diagnose(**limits)
                self.assertEqual((r['stop'], r['isolation'][key]), ('not_isolated', False))
                self.assertNothingRan(r)
        self.write_mounts()
        (self.dir / 'net' / 'eth0').mkdir()
        self.assertEqual(self.diagnose()['isolation']['networkOnlyLo'], False)
        (self.dir / 'net' / 'eth0').rmdir()
        for gpus in (('nvidia0',), ('nvidia0', 'nvidia1', 'nvidia2')):
            with self.subTest(gpus=gpus):
                for n in ('nvidia0', 'nvidia1', 'nvidia2'):
                    (self.dir / 'dev' / n).unlink(missing_ok=True)
                for n in gpus:
                    (self.dir / 'dev' / n).write_text('')
                r = self.diagnose()
                self.assertEqual((r['stop'], r['isolation']['gpuDevices']), ('not_isolated', len(gpus)))
                self.assertNothingRan(r)

    def test_input_refusals(self):
        (self.model / 'tokenizer.json').write_bytes(b'{"x": 1}')
        r = self.diagnose()
        self.assertEqual((r['stop'], r['inputs']['qwenFiles']['mismatch']), ('bad_inputs', 1))
        self.assertNothingRan(r)
        (self.model / 'tokenizer.json').write_bytes(b'{}')
        (self.model / 'layers-0.safetensors').write_bytes(b'w' * 63)
        r = self.diagnose()
        self.assertEqual((r['stop'], r['inputs']['weights']['sizeMismatch']), ('bad_inputs', 1))
        (self.model / 'layers-0.safetensors').unlink()
        self.assertEqual(self.diagnose()['inputs']['weights']['missing'], 1)
        self.assertNothingRan(r)

    def test_sanitize_is_an_allowlist_and_bounded(self):
        hostile = G.sanitize({'stop': 'done; rm', 'elapsedS': -3, 'extra': 'SECRET',
                              'isolation': {'ok': 'yes', 'gpuDevices': True},
                              'inputs': {'weights': {'sizeOk': 'SECRET'}},
                              'engine': {'run': 'ok', 'step': 'SECRET', 'vllm': '/x/SECRET',
                                         'unsupported': ['language_model_only', 'SECRET'], 'loadS': 'SECRET',
                                         'finishReason': 'SECRET', 'outputSha256': 'SECRET', 'quantization': 'fp8 x',
                                         'error': 'Error: SECRET', 'module': 'a b', 'text': 'SECRET output'}})
        self.assertNotIn('SECRET', json.dumps(hostile))
        self.assertEqual((hostile['stop'], hostile['isolation']['ok'], hostile['isolation']['gpuDevices'],
                          hostile['engine']['vllm'], hostile['engine']['unsupported'], hostile['engine']['step']),
                         (None, None, None, 'other', ['language_model_only'], None))
        self.assertNotIn('text', hostile['engine'])
        self.assertIsNone(G.sanitize([]))
        worst = G.sanitize({'stop': 'generate_failed', 'elapsedS': 1 << 40,
                            'isolation': {**dict.fromkeys(('ok', 'tmpfs', 'tmpBounded', 'tmpExec', 'shmBounded',
                                                           'rootReadOnly', 'modelReadOnly', 'networkOnlyLo'), True),
                                          'tmpBytes': 1 << 40, 'shmBytes': 1 << 40, 'gpuDevices': 1 << 40},
                            'inputs': {'qwenFiles': dict.fromkeys(('expected', 'verified', 'missing', 'mismatch'),
                                                                  1 << 40),
                                       'weights': dict.fromkeys(('expected', 'sizeOk', 'missing', 'sizeMismatch'),
                                                                1 << 40)},
                            'engine': {'run': 'timeout', 'step': 'generate', 'vllm': '9' * 32,
                                       'unsupported': list(G.ENGINE), 'loadS': 999999.9, 'generateS': 999999.9,
                                       'promptTokens': 1 << 40, 'generatedTokens': 1 << 40, 'finishReason': 'length',
                                       'outputSha256': 'f' * 64, 'inVocab': True, 'quantization': 'q' * 64,
                                       'error': 'E' * 64, 'module': 'm' * 64}})
        self.assertLess(len(json.dumps(worst, separators=(',', ':'))), 3000)  # room in the 4 KiB receipt

    def test_pins_against_the_real_manifest(self):
        q = MANIFEST['groups']['qwen']
        self.assertEqual((q['repo'], q['revision']), (G.REPO, G.REVISION))
        self.assertEqual(G.REVISION, '95a723d08a9490559dae23d0cff1d9466213d989')
        self.assertEqual(len([f for f in q['files'] if not G.is_weight(f[0])]), 11)
        self.assertEqual(len([f for f in q['files'] if G.is_weight(f[0])]), 43)
        self.assertEqual(G.SAMPLING['max_tokens'], 32)
        self.assertEqual(G.ENGINE['max_model_len'], 2048)

    def test_stdlib_only_and_no_server(self):
        import ast
        top = [n for n in ast.parse(SOURCE).body if isinstance(n, (ast.Import, ast.ImportFrom))]
        self.assertEqual({a.name for n in top for a in n.names},
                         {'hashlib', 'json', 'os', 're', 'signal', 'subprocess', 'sys', 'time'})
        compile(G.CHILD, 'child', 'exec')
        for forbidden in ('serve', 'api_server', '--port', 'port=', 'AsyncLLM', 'uvicorn', 'http'):
            self.assertNotIn(forbidden, G.CHILD)  # the offline LLM API only: no HTTP server, no service port
        self.assertNotIn('outputs[0].text', G.CHILD)  # generated text is never read
        self.assertNotIn('.text', G.CHILD)


if __name__ == '__main__':
    unittest.main()
