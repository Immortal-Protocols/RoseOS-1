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
PAD = 151655
class _Tok:
    def encode(self, text):
        return [len(w) for w in text.split()]
    def __len__(self):
        return 1000
    def convert_tokens_to_ids(self, token):
        return PAD if token == '<|image_pad|>' else 0
    def decode(self, ids):
        return MODE.get('answer', 'Red.')
class _Out:
    def __init__(self, ids, reason):
        self.token_ids, self.finish_reason = ids, reason
class _Res:
    def __init__(self, outs, prompt_ids=None):
        self.outputs, self.prompt_token_ids = outs, prompt_ids
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
        shown = []
        for p in prompts:
            p = dict(p)
            mm = p.pop('multi_modal_data', None)
            if mm is not None:
                img = mm['image']
                p['image'] = [type(img).__module__, img.mode, list(img.size), list(img.color), sorted(mm)]
            shown.append(p)
        log('generate', prompts=shown, use_tqdm=use_tqdm)
        if MODE.get('generate') == 'fail':
            raise ValueError('generated SECRET text')
        n = params.kw['max_tokens']
        pids = None
        if any('image' in p for p in shown):  # the processor expands the one placeholder to (w/32)*(h/32) pads
            w, h = shown[0]['image'][2]
            pids = [1, 2] + [PAD] * MODE.get('pads', (w // 32) * (h // 32)) + [3, 4]
        return [_Res([_Out(list(range(100, 100 + n)), 'length')], pids)]
'''

ARG_UTILS = '''
import json, os
from dataclasses import make_dataclass
MODE = json.load(open(os.path.join(os.path.dirname(os.path.dirname(__file__)), 'mode.json')))
FIELDS = ['model', 'tokenizer', 'tensor_parallel_size', 'max_model_len', 'max_num_seqs', 'gpu_memory_utilization',
          'enforce_eager', 'language_model_only', 'trust_remote_code', 'seed', 'distributed_executor_backend',
          'disable_log_stats', 'limit_mm_per_prompt', 'mm_processor_kwargs', 'reasoning_parser']
EngineArgs = make_dataclass('EngineArgs', [(f, object, None) for f in FIELDS if f not in MODE.get('drop', [])])
'''


def gitsha1(data):
    return hashlib.sha1(b'blob %d\0' % len(data) + data).hexdigest()


PIL_IMAGE = '''
class _Image:
    def __init__(self, mode, size, color):
        self.mode, self.size, self.color = mode, size, color
def new(mode, size, color=0):
    return _Image(mode, tuple(size), tuple(color))
'''


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
        (self.extra / 'PIL').mkdir()
        (self.extra / 'PIL' / '__init__.py').write_text('')
        (self.extra / 'PIL' / 'Image.py').write_text(PIL_IMAGE)
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


class Vision(Fixture):
    """Vision mode (CLOUD-QWEN-VISION-001): one synthetic image, the vision tower loaded, a bounded request."""

    def test_one_synthetic_image_request_with_vision_engine(self):
        r = self.diagnose(mode='vision')
        self.assertEqual((r['mode'], r['stop']), ('vision', 'done'))
        e = r['engine']
        self.assertEqual((e['imageTokens'], e['promptTokens'], e['expectedWordSeen'], e['generatedTokens'],
                          e['finishReason']), (64, 68, True, 32, 'length'))
        (llm,) = self.calls('llm')
        self.assertEqual(llm['kw'], G.VISION_ENGINE)
        self.assertNotIn('language_model_only', llm['kw'])  # the vision tower is loaded in this mode only
        self.assertEqual(llm['kw']['limit_mm_per_prompt'], {'image': {'count': 1, 'width': 256, 'height': 256},
                                                           'video': 0})
        self.assertEqual(llm['kw']['mm_processor_kwargs'], {'max_pixels': 256 * 256})
        self.assertEqual((G.VISION['width'], G.VISION['height']), (256, 256))  # the request matches the profile
        self.assertEqual({k: v for k, v in llm['kw'].items() if k not in ('limit_mm_per_prompt',
                                                                          'mm_processor_kwargs')},
                         {k: v for k, v in G.ENGINE.items() if k != 'language_model_only'})  # same bounds as text
        (gen,) = self.calls('generate')  # exactly one request with exactly one image
        (prompt,) = gen['prompts']
        self.assertEqual(prompt['image'], ['PIL.Image', 'RGB', [256, 256], [255, 0, 0], ['image']])
        self.assertEqual(prompt['prompt'], G.VISION['prompt'])
        self.assertEqual(prompt['prompt'].count('<|image_pad|>'), 1)
        self.assertNotIn('prompt_token_ids', prompt)
        self.assertEqual([c['kw'] for c in self.calls('sampling')], [G.SAMPLING])
        line = json.dumps(G.sanitize(r))
        for leak in ('Red', 'colour', 'vision_start', str(self.dir)):
            self.assertNotIn(leak, line)  # the answer and the prompt never leave the child

    def test_vision_not_applied_or_unbounded_and_weak_answer_signal(self):
        for pads in (0, G.MAX_IMAGE_TOKENS + 1):
            with self.subTest(pads=pads):
                self.mode({'pads': pads})
                r = self.diagnose(mode='vision')
                self.assertEqual((r['stop'], r['engine']['imageTokens']), ('vision_not_applied', pads))
        self.mode({'answer': 'Blue'})
        r = self.diagnose(mode='vision')
        self.assertEqual((r['stop'], r['engine']['expectedWordSeen']), ('done', False))  # a signal, not a gate

    def test_text_engine_cannot_stand_in_for_vision(self):
        self.mode({'drop': ['limit_mm_per_prompt']})  # an image without the multimodal options: refused, not loaded
        r = self.diagnose(mode='vision')
        self.assertEqual((r['stop'], r['engine']['unsupported']), ('engine_arg_unsupported', ['limit_mm_per_prompt']))
        self.assertEqual(self.calls('llm'), [])
        self.mode({})
        r = self.diagnose()  # the default (text) mode is the PR345 configuration, untouched
        self.assertEqual((r['mode'], r['stop'], r['engine'].get('imageTokens')), ('text', 'done', None))
        (llm,) = [c for c in self.calls('llm')]
        self.assertEqual(llm['kw'], G.ENGINE)
        (gen,) = self.calls('generate')
        self.assertNotIn('image', gen['prompts'][0])
        self.assertEqual(G.child_config('text'), {'engine': G.ENGINE, 'sampling': G.SAMPLING, 'prompt': G.PROMPT})
        self.assertEqual(G.diagnose(self.manifest, dict(self.limits, mode='audio'))['stop'], 'bad_mode')

    def test_vision_failures_and_deadline(self):
        self.mode({'generate': 'fail'})
        r = self.diagnose(mode='vision')
        self.assertEqual((r['stop'], r['engine']['error']), ('generate_failed', 'ValueError'))
        self.mode({'load': 'hang', 'worker': True})
        t = time.monotonic()
        r = self.diagnose(mode='vision', deadlineS=4)
        self.assertLess(time.monotonic() - t, 12)
        self.assertEqual(r['stop'], 'deadline')
        (self.extra / 'PIL' / 'Image.py').write_text("raise ModuleNotFoundError('No module named PIL', name='PIL')\n")
        # no Pillow in the image: a fixed category, reported after the load
        self.mode({})
        r = self.diagnose(mode='vision')
        self.assertEqual((r['stop'], r['engine']['step'], r['engine']['error']),
                         ('generate_failed', 'generate', 'ModuleNotFoundError'))
        self.assertEqual(r['engine']['module'], 'PIL')


FAKE_SERVE = '''
import json, os, sys, time
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from fakes import CharTokenizer, FakeVllm, chat_prompt_ids
args = sys.argv[1:]
with open(os.path.join(HERE, 'serve.argv'), 'w') as f:
    json.dump(args, f)
with open(os.path.join(HERE, 'serve.pid'), 'w') as f:
    f.write(str(os.getpid()))
mode = json.load(open(os.path.join(HERE, 'serve.mode')))
if mode.get('die'):
    sys.exit(7)
if mode.get('never_ready'):
    time.sleep(300)
tok = CharTokenizer()
ids, im_end = tok.encode, tok.encode('<|im_end|>')
script = [ids('Hi there friend') + im_end, ids('Red') + im_end, ids('Square') + im_end, ids('Sunny') + im_end,
          ids('Fine') + im_end, ids('1, 2, 3, ' * 40), ids('y'), ids('ab')]
FakeVllm(script, prompt_ids=chat_prompt_ids(tok), serial=True, metrics=True, delay_s=0.02,
         port=int(args[args.index('--port') + 1]))
time.sleep(300)
'''

TRANSFORMERS_CHAR = '''
from fakes import CharTokenizer
class AutoTokenizer:
    @staticmethod
    def from_pretrained(model, local_files_only=False, trust_remote_code=True):
        assert local_files_only and not trust_remote_code
        return CharTokenizer()
'''


def free_port():
    import socket
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


class Adapter(Fixture):
    """Adapter mode (CLOUD-QWEN-ADAPTER-001): the real vllm_brain/adapter_session sources from the bundle, a stand-in
    `vllm serve` (tests/fakes.FakeVllm over real HTTP on loopback), a stand-in tokenizer."""

    def setUp(self):
        super().setUp()
        try:
            import PIL  # noqa: F401
        except ImportError:  # pragma: no cover
            self.skipTest('Pillow is needed for the synthetic image')
        shutil.rmtree(self.extra / 'PIL')  # the real Pillow: the adapter encodes a real PNG
        shutil.copy(HERE / 'fakes.py', self.extra / 'fakes.py')
        (self.extra / 'transformers').mkdir()
        (self.extra / 'transformers' / '__init__.py').write_text(TRANSFORMERS_CHAR)
        (self.extra / 'serve.py').write_text(FAKE_SERVE)
        self.serve_mode({})
        self.bundle = {n: (ROOT / 'rose_cloud' / n).read_text() for n in G.ADAPTER_FILES}
        port = free_port()
        old = G.ADAPTER_PORT
        G.ADAPTER_PORT = port
        self.addCleanup(setattr, G, 'ADAPTER_PORT', old)
        self.port = port

    def serve_mode(self, mode):
        (self.extra / 'serve.mode').write_text(json.dumps(mode))

    def run_adapter(self, **limits):
        return G.diagnose(self.manifest, dict(self.limits, mode='adapter',
                                              serverCommand=[sys.executable, str(self.extra / 'serve.py')], **limits),
                          bundle=self.bundle)

    def test_one_session_through_the_real_adapter(self):
        r = self.run_adapter()
        self.assertEqual((r['mode'], r['stop']), ('adapter', 'done'))
        e = G.sanitize(r)['engine']
        self.assertEqual((e['step'], e['unsupported']), ('done', []))
        sess = e['session']
        self.assertEqual((sess['step'], sess['imageTokens'], sess['imagesInHistory']), ('done', 64, 1))
        self.assertEqual((sess['turn2']['promptExact'], sess['turn3']['promptExact']), (True, True))
        self.assertEqual(sess['history'], {'rewindExact': True, 'forkUntouched': True, 'staleImageDropped': True,
                                           'staleRequestSent': True, 'forkImages': 1})
        self.assertEqual((sess['cancel']['semantics'], sess['cancel']['noLateAppend'], sess['cancel']['runningAfter'],
                          sess['lateCancel']['lateCancelNoop']), ('inflight_abort', True, 0.0, True))
        argv = json.loads((self.extra / 'serve.argv').read_text())
        tmpl = argv[argv.index('--chat-template') + 1]
        self.assertTrue(tmpl.startswith(str(self.tmp)))  # the adapter's RAW_TEMPLATE, written on the tmpfs
        self.assertEqual(argv, ['serve', str(self.model), '--served-model-name', 'rose-brain', '--host', '127.0.0.1',
                                '--port', str(self.port), '--chat-template', tmpl] + G.SERVER_ARGS)
        self.assertEqual(G.SERVER_ARGS, ['--tensor-parallel-size', '2', '--max-model-len', '2048', '--max-num-seqs', '1',
                                         '--gpu-memory-utilization', '0.9', '--enforce-eager', '--seed', '0',
                                         '--distributed-executor-backend', 'mp', '--limit-mm-per-prompt',
                                         '{"image": {"count": 1, "height": 256, "width": 256}, "video": 0}',
                                         '--mm-processor-kwargs', '{"max_pixels": 65536}',
                                         '--chat-template-content-format', 'openai'])
        self.assertNotIn('--language-model-only', argv)
        self.assertNotIn('--disable-log-stats', argv)  # the running gauge stays on for the cancel evidence
        self.assertNotIn('0.0.0.0', argv)
        line = json.dumps(G.sanitize(r))
        for leak in ('Hi there', 'Square', 'colour', 'Rose', 'base64', str(self.dir)):
            self.assertNotIn(leak, line)
        self.assertLess(len(line), 3500)
        pid = int((self.extra / 'serve.pid').read_text())
        time.sleep(0.2)
        self.assertFalse(alive(pid))  # the server died with the group

    def test_server_failures_and_deadline(self):
        self.serve_mode({'die': True})
        r = self.run_adapter()
        self.assertEqual((r['stop'], r['engine']['serverExit']), ('server_failed', 7))
        self.serve_mode({'never_ready': True})
        t = time.monotonic()
        r = self.run_adapter(deadlineS=5)
        self.assertLess(time.monotonic() - t, 12)
        self.assertEqual(r['stop'], 'deadline')
        time.sleep(0.2)
        self.assertFalse(alive(int((self.extra / 'serve.pid').read_text())))  # killed with the group

    def test_refusals_before_any_server(self):
        self.mode({'drop': ['mm_processor_kwargs']})
        r = self.run_adapter()
        self.assertEqual((r['stop'], r['engine']['unsupported']), ('engine_arg_unsupported', ['mm_processor_kwargs']))
        self.assertFalse((self.extra / 'serve.argv').exists())
        self.mode({})
        for bundle in (None, {}, dict(self.bundle, extra='x'), {k: v for k, v in self.bundle.items()
                                                               if k != 'pump.py'}):
            r = G.diagnose(self.manifest, dict(self.limits, mode='adapter'), bundle=bundle)
            self.assertEqual((r['stop'], r['engine']), ('bad_bundle', None))
        self.assertFalse((self.extra / 'serve.argv').exists())

    def test_text_and_vision_receipts_unchanged(self):
        r = self.diagnose()
        self.assertEqual(set(G.sanitize(r)['engine']), {'run', 'step', 'vllm', 'unsupported', 'imageTokens',
                                                        'expectedWordSeen', 'loadS', 'generateS', 'promptTokens',
                                                        'generatedTokens', 'finishReason', 'outputSha256', 'inVocab',
                                                        'quantization', 'error', 'module'})
        self.assertEqual(G.child_config('text'), {'engine': G.ENGINE, 'sampling': G.SAMPLING, 'prompt': G.PROMPT})


class Warm(Adapter):
    """Warm mode (CLOUD-QWEN-WARM-001): the server stays loaded until the helper's deadline, and RUNNER (what bootstrap
    runs with `docker exec`) runs payload-defined cases against it, each a new adapter session, without a reload."""

    def start_warm(self, deadline):
        import threading
        box = {}
        t = threading.Thread(target=lambda: box.update(r=G.diagnose(
            self.manifest, dict(self.limits, mode='warm', deadlineS=deadline,
                                serverCommand=[sys.executable, str(self.extra / 'serve.py')]), bundle=self.bundle)))
        t.start()
        state = self.tmp / 'rose-qwen-gpu' / G.WARM_STATE
        end = time.monotonic() + 20
        while not state.exists() and time.monotonic() < end:
            time.sleep(0.1)
        return t, box, state

    def runner(self, case):
        import subprocess
        out = subprocess.run([sys.executable, '-I', '-B', '-c', G.RUNNER, str(self.tmp / 'rose-qwen-gpu'),
                              str(self.model), case, str(self.extra)], capture_output=True, text=True, timeout=120).stdout
        line = out.strip().splitlines()[-1]
        self.assertTrue(line.startswith(G.RUNNER_MARKER))
        return json.loads(line[len(G.RUNNER_MARKER):])

    def test_one_load_serves_several_cases_until_the_window_ends(self):
        t, box, state = self.start_warm(deadline=40)
        self.assertTrue(state.exists())
        s = json.loads(state.read_text())
        self.assertEqual((s['ready'], s['base']), (True, f'http://127.0.0.1:{self.port}'))
        pid = int((self.extra / 'serve.pid').read_text())
        starts = (self.extra / 'serve.argv').stat().st_mtime
        # the fake replays its script in order; text uses the first three replies, image the next two
        text = G.sanitize_run(self.runner('text'))
        self.assertEqual((text['case'], text['step'], text['session']['step']), ('text', 'done', 'done'))
        self.assertEqual([text['session'][k]['tokens'] for k in ('turn1', 'turn2', 'turn3')], [16, 4, 7])
        image = G.sanitize_run(self.runner('image'))
        self.assertEqual((image['step'], image['session']['imageTokens'], image['session']['turn2']['promptExact']),
                         ('done', 64, True))
        self.assertEqual(int((self.extra / 'serve.pid').read_text()), pid)  # the same server: no reload
        self.assertEqual((self.extra / 'serve.argv').stat().st_mtime, starts)
        bad = self.runner('rm -rf /')
        self.assertEqual((bad['step'], bad['error']), ('tokenizer', 'KeyError'))  # only CASES; nothing else runs
        self.assertIsNone(G.sanitize_run(bad)['case'])
        t.join(60)
        r = box['r']
        self.assertEqual((r['mode'], r['stop']), ('warm', 'deadline'))  # the fixed window ended the container
        time.sleep(0.2)
        self.assertFalse(alive(pid))  # and the server with it
        line = json.dumps([text, image, G.sanitize(r)])
        for leak in ('Hi there', 'Red', 'colour', str(self.dir)):
            self.assertNotIn(leak, line)

    def test_runner_before_ready_and_cases_match_the_payload(self):
        work = self.tmp / 'rose-qwen-gpu'
        work.mkdir(parents=True, exist_ok=True)
        (work / 'pkg' / 'rose_cloud').mkdir(parents=True)
        for n, src in self.bundle.items():
            (work / 'pkg' / 'rose_cloud' / n).write_text(src)
        (work / 'pkg' / 'rose_cloud' / '__init__.py').write_text('')
        r = self.runner('full')
        self.assertEqual((r['step'], r['error']), ('state', 'FileNotFoundError'))  # no server yet: nothing runs
        from rose_cloud import adapter_session
        self.assertEqual(sorted(adapter_session.CASES), sorted(G.WARM_CASES))


FAKE_CONTRACT_SERVE = '''
import json, os, sys, time
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from fakes import FakeOpenAI
args = sys.argv[1:]
with open(os.path.join(HERE, 'serve.argv'), 'w') as f:
    json.dump(args, f)
with open(os.path.join(HERE, 'serve.pid'), 'w') as f:
    f.write(str(os.getpid()))
mode = json.load(open(os.path.join(HERE, 'serve.mode')))
if mode.pop('die', False):
    sys.exit(2)  # as `vllm serve` exits on an argument it does not accept, before loading anything
if '--default-chat-template-kwargs' in args:  # the server-wide template default, as vllm serve applies it
    if json.loads(args[args.index('--default-chat-template-kwargs') + 1]).get('enable_thinking') is False:
        mode.setdefault('nothink', True)
FakeOpenAI(served=args[args.index('--served-model-name') + 1], mode=mode, port=int(args[args.index('--port') + 1]))
time.sleep(300)
'''


class Contract(Adapter):
    """Contract mode (MODELS-QWEN-CONTRACT-001): the warm machinery with the proposed profile, a stand-in `vllm serve`
    (tests/fakes.FakeOpenAI) and CONTRACT_RUNNER, what bootstrap runs with `docker exec`."""

    def setUp(self):
        super().setUp()
        (self.extra / 'serve.py').write_text(FAKE_CONTRACT_SERVE)
        self.bundle = {n: (ROOT / 'rose_cloud' / n).read_text() for n in G.CONTRACT_FILES}

    # the adapter's own session tests do not apply to this server (they run under Adapter and Warm)
    test_one_session_through_the_real_adapter = test_server_failures_and_deadline = None

    def start(self, deadline, **limits):
        import threading
        box = {}
        t = threading.Thread(target=lambda: box.update(r=G.diagnose(
            self.manifest, dict(self.limits, mode='contract', deadlineS=deadline,
                                serverCommand=[sys.executable, str(self.extra / 'serve.py')], **limits),
            bundle=self.bundle)))
        t.start()
        state = self.tmp / 'rose-qwen-gpu' / G.WARM_STATE
        end = time.monotonic() + 20
        while not state.exists() and t.is_alive() and time.monotonic() < end:
            time.sleep(0.1)
        return t, box, state

    def runner(self, case):
        import subprocess
        out = subprocess.run([sys.executable, '-I', '-B', '-c', G.CONTRACT_RUNNER, str(self.tmp / 'rose-qwen-gpu'),
                              case], capture_output=True, text=True, timeout=120).stdout
        line = out.strip().splitlines()[-1]
        self.assertTrue(line.startswith(G.CONTRACT_MARKER))
        self.assertLess(len(line), 3000)  # inside bootstrap's 4 KiB receipt with its envelope
        return json.loads(line[len(G.CONTRACT_MARKER):])

    def test_one_load_runs_the_contract_cases_with_the_proposed_profile(self):
        t, box, state = self.start(deadline=12)
        self.assertTrue(state.exists())
        argv = json.loads((self.extra / 'serve.argv').read_text())
        self.assertEqual(argv, ['serve', str(self.model), '--served-model-name', 'rose-qwen', '--host', '127.0.0.1',
                                '--port', str(self.port)] + G.CONTRACT_SERVER_ARGS)
        self.assertNotIn('--chat-template', argv)  # the model's own template
        self.assertEqual(argv[argv.index('--max-model-len') + 1], '8192')
        self.assertEqual(argv[argv.index('--reasoning-parser') + 1], 'qwen3')
        self.assertNotIn('0.0.0.0', argv)
        pid = int((self.extra / 'serve.pid').read_text())
        full = G.sanitize_contract(self.runner('contract'))
        self.assertEqual((full['case'], full['step'], full['error']), ('contract', 'done', None))
        c = full['contract']
        self.assertEqual((c['step'], c['allExpected'], c['reasoningSeparated']), ('done', True, True))
        self.assertEqual(c['models'], {'status': 200, 'ids': 1, 'idsExact': True, 'maxModelLen': 8192,
                                       'timedOut': False})
        self.assertEqual(c['notRun'], {})
        self.assertEqual([c[k]['verdict'] for k in G.CONTRACT_REPLIES],
                         ['final', 'open_app:calculator', 'final', 'final'])
        self.assertEqual((c['long']['recalled'], c['long']['beyondProven']), (True, True))
        long = G.sanitize_contract(self.runner('long'))
        self.assertEqual((long['case'], long['contract']['allExpected']), ('long', True))
        self.assertEqual(int((self.extra / 'serve.pid').read_text()), pid)  # the same loaded server: no reload
        bad = self.runner('rm -rf /')
        self.assertEqual((bad['step'], bad['error']), ('contract', 'KeyError'))  # only the payload's cases run
        self.assertIsNone(G.sanitize_contract(bad)['case'])
        t.join(60)
        r = box['r']
        self.assertEqual((r['mode'], r['stop']), ('contract', 'deadline'))  # the fixed window ended the container
        s = G.sanitize(r)
        self.assertEqual((s['mode'], s['stop'], s['engine']['run']), ('contract', 'deadline', 'timeout'))
        self.assertNotIn('session', s['engine'])
        time.sleep(0.2)
        self.assertFalse(alive(pid))
        line = json.dumps([full, long, s])
        for leak in ('SECRET', 'AMBERFALCON', 'amberfalcon', 'Hello, I am Rose', 'Journal', str(self.dir)):
            self.assertNotIn(leak, line)

    def test_a_contract_break_is_a_receipt_not_a_pass(self):
        self.serve_mode({'inline_think': True})
        t, box, state = self.start(deadline=8)
        run = G.sanitize_contract(self.runner('chat'))
        self.assertEqual((run['step'], run['contract']['allExpected'], run['contract']['chat']['verdict'],
                          run['contract']['chat']['thinkInContent']), ('done', False, 'failed:malformed_action', True))
        t.join(60)

    def test_client_timeout_through_the_real_runner(self):
        """MODELS-QWEN-CONTRACT-002 end to end: the payload's runner and driver against a stand-in whose reply after the
        tool result is late. The bundle's fixture carries a scaled client timeout (120 s -> 1 s), as the generated
        fixture carries the real one; the old driver's TimeoutError left the receipt without allExpected."""
        requests = json.loads(self.bundle['qwen_contract_requests.json'])
        requests['client']['inference_timeout_ms'] = 1000
        self.bundle['qwen_contract_requests.json'] = json.dumps(requests)
        self.serve_mode({'delay_s': {'after': 1.5}})
        t, box, state = self.start(deadline=8)
        raw = self.runner('tool')
        self.assertEqual((raw['step'], raw['error']), ('done', None))
        run = G.sanitize_contract(raw)
        c = run['contract']
        self.assertEqual((c['step'], c['allExpected'], c['notRun'], c['tool']['verdict']),
                         ('done', False, {}, 'open_app:calculator'))
        self.assertEqual((c['toolAfter']['verdict'], c['toolAfter']['timedOut'], c['toolAfter']['expectMet']),
                         ('failed:timeout_response', True, False))
        self.assertGreaterEqual(c['toolAfter']['seconds'], 1.0)
        self.assertNotIn('SECRET', json.dumps(run))
        t.join(60)

    def test_refusals_before_any_server(self):
        self.mode({'drop': ['reasoning_parser']})
        r = G.diagnose(self.manifest, dict(self.limits, mode='contract',
                                           serverCommand=[sys.executable, str(self.extra / 'serve.py')]),
                       bundle=self.bundle)
        self.assertEqual((r['stop'], r['engine']['unsupported']), ('engine_arg_unsupported', ['reasoning_parser']))
        self.assertEqual(G.sanitize(r)['engine']['unsupported'], ['reasoning_parser'])
        self.assertFalse((self.extra / 'serve.argv').exists())
        self.mode({})
        adapter = {n: (ROOT / 'rose_cloud' / n).read_text() for n in G.ADAPTER_FILES}
        for bundle in (None, {}, adapter, dict(self.bundle, extra='x'), {'contract_session.py': 'x'}):
            r = G.diagnose(self.manifest, dict(self.limits, mode='contract'), bundle=bundle)
            self.assertEqual((r['stop'], r['engine']), ('bad_bundle', None))
        r = G.diagnose(self.manifest, dict(self.limits, mode='adapter'), bundle=self.bundle)
        self.assertEqual(r['stop'], 'bad_bundle')  # and the adapter modes never take the contract files
        self.assertFalse((self.extra / 'serve.argv').exists())
        self.serve_mode({'die': True})
        t, box, _ = self.start(deadline=15)
        t.join(30)
        self.assertEqual((box['r']['stop'], box['r']['engine']['serverExit']), ('server_failed', 2))
        self.assertFalse((self.tmp / 'rose-qwen-gpu' / G.WARM_STATE).exists())

    def test_profile_is_the_proposed_serving_profile_and_proven_modes_are_unchanged(self):
        sys.path.insert(0, str(ROOT.parent / 'serving'))
        import render_local
        qwen = json.loads((ROOT.parent / 'serving' / 'qwen-fp8.json').read_text())
        args, proposed = render_local.qwen_args(qwen, 'proposed')
        self.assertEqual(G.CONTRACT_SERVER_ARGS, args)  # exactly what --qwen-settings proposed would serve
        self.assertEqual(sorted(proposed), ['--max-model-len', '--reasoning-parser'])
        self.assertEqual(render_local.qwen_args(qwen, 'proven')[0], G.SERVER_ARGS)
        self.assertEqual(G.CONTRACT_SERVED, qwen['served_model_name'])
        from rose_cloud import contract_session
        self.assertEqual((contract_session.SERVED, contract_session.MAX_MODEL_LEN), (G.CONTRACT_SERVED,
                                                                                     G.CONTRACT_MAX_MODEL_LEN))
        self.assertEqual(sorted(contract_session.CASES), sorted(G.CONTRACT_CASES))
        self.assertEqual(set(contract_session.VERDICTS), set(G.CONTRACT_VERDICTS))
        self.assertEqual(G.CONTRACT_ENGINE, {**G.VISION_ENGINE, 'max_model_len': 8192, 'reasoning_parser': 'qwen3'})
        # MODELS-QWEN-CONTRACT-002: the client's own wait per request, and a case budget inside bootstrap's 300 s
        self.assertEqual(contract_session.REQUEST_TIMEOUT_S, 120)
        self.assertLess(contract_session.CASE_BUDGET_S, 300)
        worst_reply = {'status': 599, 'verdict': 'failed:model_identity_mismatch', 'expectMet': False,
                       'timedOut': False, 'identityExact': False, 'thinkInContent': False, 'recalled': False, 'beyondProven': False,
                       'finish': 'length', 'reasoningField': 'reasoning_content', 'reasoningChars': (1 << 50) - 1,
                       'contentChars': (1 << 50) - 1, 'promptTokens': (1 << 50) - 1,
                       'completionTokens': (1 << 50) - 1, 'seconds': 999999.9}
        worst = G.sanitize_contract({'case': 'contract', 'step': 'done', 'error': 'E' * 64, 'module': 'm' * 64,
                                     'contract': {'step': 'toolAfter', 'models': {'status': 599, 'ids': (1 << 50) - 1,
                                                                                  'idsExact': False,
                                                                                  'maxModelLen': (1 << 50) - 1,
                                                                                  'timedOut': True},
                                                  'notRun': {k: 'no_proposal' for k in G.CONTRACT_REPLIES},
                                                  **{k: dict(worst_reply) for k in G.CONTRACT_REPLIES},
                                                  'reasoningSeparated': False, 'allExpected': False}})
        self.assertEqual(worst['contract']['long'], worst_reply)  # every field kept when valid
        self.assertLess(len(json.dumps(worst, separators=(',', ':'))), 3000)  # room in the 4 KiB session receipt
        image = {k: G.VISION[k] for k in ('width', 'height', 'rgb')}
        for mode in ('adapter', 'warm'):  # the proven modes: the PR349/PR350 configuration, unchanged
            self.assertEqual(G.child_config(mode), {
                'engine': G.VISION_ENGINE, 'server': G.SERVER_ARGS, 'port': G.ADAPTER_PORT, 'warm': mode == 'warm',
                'state': G.WARM_STATE, 'requestTimeoutS': G.ADAPTER_REQUEST_TIMEOUT_S, 'image': image,
                'serverCommand': None})


CLI_ARGS = '''
import json, os
from dataclasses import make_dataclass
MODE = json.load(open(os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'mode.json')))
FrontendArgs = make_dataclass('FrontendArgs', [(f, object, None) for f in ('host', 'port', 'default_chat_template_kwargs')
                                               if f not in MODE.get('drop', [])])
'''


class ContractNothink(Contract):
    """Contract-nothink mode (MODELS-QWEN-CONTRACT-NOTHINK-001): the contract machinery with the no-thinking profile,
    a stand-in `vllm serve` that honours the template default, a stand-in FrontendArgs, and the real runner."""

    def setUp(self):
        super().setUp()
        cli = self.extra / 'vllm' / 'entrypoints' / 'openai'
        cli.mkdir(parents=True)
        (self.extra / 'vllm' / 'entrypoints' / '__init__.py').write_text('')
        (cli / '__init__.py').write_text('')
        (cli / 'cli_args.py').write_text(CLI_ARGS)

    def start(self, deadline, **limits):
        import threading
        box = {}
        t = threading.Thread(target=lambda: box.update(r=G.diagnose(
            self.manifest, dict(self.limits, mode='contract-nothink', deadlineS=deadline,
                                serverCommand=[sys.executable, str(self.extra / 'serve.py')], **limits),
            bundle=self.bundle)))
        t.start()
        state = self.tmp / 'rose-qwen-gpu' / G.WARM_STATE
        end = time.monotonic() + 20
        while not state.exists() and t.is_alive() and time.monotonic() < end:
            time.sleep(0.1)
        return t, box, state

    # the thinking profile's own tests run under Contract
    test_one_load_runs_the_contract_cases_with_the_proposed_profile = None
    test_a_contract_break_is_a_receipt_not_a_pass = test_client_timeout_through_the_real_runner = None
    test_refusals_before_any_server = test_profile_is_the_proposed_serving_profile_and_proven_modes_are_unchanged = None

    def test_one_load_runs_the_cases_with_thinking_off(self):
        t, box, state = self.start(deadline=12)
        self.assertEqual(json.loads(state.read_text())['thinking'], False)
        argv = json.loads((self.extra / 'serve.argv').read_text())
        self.assertEqual(argv, ['serve', str(self.model), '--served-model-name', 'rose-qwen', '--host', '127.0.0.1',
                                '--port', str(self.port)] + G.CONTRACT_NOTHINK_SERVER_ARGS)
        self.assertEqual(argv[-2:], ['--default-chat-template-kwargs', '{"enable_thinking": false}'])
        self.assertEqual(argv[argv.index('--reasoning-parser') + 1], 'qwen3')
        self.assertNotIn('--chat-template', argv)
        run = G.sanitize_contract(self.runner('contract'))
        c = run['contract']
        self.assertEqual((run['step'], c['allExpected'], c['thinking'], c['noThinking'], c['reasoningSeparated']),
                         ('done', True, False, True, False))
        self.assertEqual([c[k]['verdict'] for k in G.CONTRACT_REPLIES],
                         ['final', 'open_app:calculator', 'final', 'final'])
        self.assertTrue(all(c[k]['reasoningChars'] == 0 for k in G.CONTRACT_REPLIES))
        t.join(60)
        self.assertEqual((box['r']['mode'], box['r']['stop']), ('contract-nothink', 'deadline'))
        self.assertNotIn('SECRET', json.dumps(run))

    def test_a_misrouting_parser_fails_through_the_real_runner(self):
        self.serve_mode({'misroute': True})
        t, box, state = self.start(deadline=8)
        c = G.sanitize_contract(self.runner('chat'))['contract']
        self.assertEqual((c['chat']['verdict'], c['noThinking'], c['allExpected']),
                         ('failed:empty_completion', False, False))
        t.join(60)

    def test_unsupported_flag_refuses_before_any_server(self):
        self.mode({'drop': ['default_chat_template_kwargs']})
        r = G.diagnose(self.manifest, dict(self.limits, mode='contract-nothink',
                                           serverCommand=[sys.executable, str(self.extra / 'serve.py')]),
                       bundle=self.bundle)
        self.assertEqual((r['stop'], r['engine']['unsupported']),
                         ('engine_arg_unsupported', ['default_chat_template_kwargs']))
        self.assertEqual(G.sanitize(r)['engine']['unsupported'], ['default_chat_template_kwargs'])
        self.assertFalse((self.extra / 'serve.argv').exists())
        shutil.rmtree(self.extra / 'vllm' / 'entrypoints')  # an image without the module at all: refused too
        self.mode({})
        r = G.diagnose(self.manifest, dict(self.limits, mode='contract-nothink',
                                           serverCommand=[sys.executable, str(self.extra / 'serve.py')]),
                       bundle=self.bundle)
        self.assertEqual((r['stop'], r['engine']['unsupported']),
                         ('engine_arg_unsupported', ['default_chat_template_kwargs']))
        self.assertFalse((self.extra / 'serve.argv').exists())
        self.serve_mode({'die': True})  # a flag the image's serve rejects: no fallback, the server fails
        self.mode({})
        r = G.diagnose(self.manifest, dict(self.limits, mode='contract-nothink',
                                           serverCommand=[sys.executable, str(self.extra / 'serve.py')]),
                       bundle=self.bundle)
        self.assertEqual(r['stop'], 'engine_arg_unsupported')  # still no cli_args module: refused before serve

    def test_profile_is_the_contract_profile_plus_thinking_off_and_others_unchanged(self):
        self.assertEqual(G.CONTRACT_NOTHINK_SERVER_ARGS,
                         G.CONTRACT_SERVER_ARGS + ['--default-chat-template-kwargs', '{"enable_thinking": false}'])
        sys.path.insert(0, str(ROOT.parent / 'serving'))
        import render_local
        qwen = json.loads((ROOT.parent / 'serving' / 'qwen-fp8.json').read_text())
        self.assertEqual(G.CONTRACT_SERVER_ARGS, render_local.qwen_args(qwen, 'proposed')[0])  # unchanged
        cfg = G.child_config('contract-nothink')
        base = G.child_config('contract')
        self.assertEqual({k: v for k, v in cfg.items() if k not in ('server', 'frontend', 'thinking')},
                         {k: v for k, v in base.items() if k != 'server'})
        self.assertEqual((base['server'], cfg['server']), (G.CONTRACT_SERVER_ARGS, G.CONTRACT_NOTHINK_SERVER_ARGS))
        self.assertEqual((cfg['frontend'], cfg['thinking']), (['default_chat_template_kwargs'], False))
        self.assertNotIn('frontend', base)
        self.assertNotIn('thinking', base)  # the thinking profile writes no thinking key: its runner default is True
        self.assertIn("state.get('thinking', True)", G.CONTRACT_RUNNER)


def alive(pid):
    try:
        with open(f'/proc/{pid}/stat') as f:
            return f.read().split()[2] != 'Z'
    except OSError:
        return False


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
