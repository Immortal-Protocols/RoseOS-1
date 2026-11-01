#!/usr/bin/env python3
"""One-shot offline Qwen text GPU diagnostic (CLOUD-QWEN-GPU-001).

  python3 -c <this source> <manifest JSON> <limits JSON>

bootstrap's cloud-qwen-gpu-once diagnostic would run it once, when a separate runtime decision sets that mode, in
the exact cached serving image: --gpus all, --network none, all capabilities dropped, a read-only root filesystem,
only the staged Qwen directory mounted (read-only), bounded tmpfs caches, CPU and memory limits, killed at 900 s. It
is NOT gpu-start-once (which starts Muse's production launch script) and shares none of Muse's model, cache, launch
script or container name. It has not run on a VM. In order, stopping at the first refusal:

1. manifest: the qwen group must be exactly REPO at REVISION (anything else, such as a Muse manifest, is
   ``bad_manifest``);
2. isolation, from inside before anything is imported (``not_isolated``): /tmp a tmpfs of at most tmpMaxBytes that
   allows exec (Triton/vLLM kernel caches), /dev/shm present and at most shmMaxBytes, / and the model directory
   read-only, only ``lo``, exactly two /dev/nvidiaN devices;
3. inputs (``bad_inputs``): the 11 non-weight files match their manifest hashes; every weight file and the weight
   index match their manifest sizes (not re-hashed: staging verified them, and hashing 37.5 GB would take much of
   the bound);
4. one child process (its own session, killed as a group at the deadline) that imports vLLM, refuses before loading
   if the image's EngineArgs lacks any field of ENGINE (``engine_arg_unsupported``: never a silent fallback such as
   loading the vision tower), then builds ``vllm.LLM`` (offline API: no HTTP server, no service port) with ENGINE and
   runs ONE generate of the fixed synthetic PROMPT with SAMPLING (greedy, at most 32 tokens).

Vision mode (limits ``{"mode": "vision"}``, bootstrap's cloud-qwen-vision-once, CLOUD-QWEN-VISION-001): the same
steps with VISION_ENGINE (the vision tower loaded instead of ``language_model_only``) and ONE request carrying one
synthetic 256 x 256 solid-red image made in the child (no file, no download) with a fixed question. It is ``done`` only
when the prompt expanded the one image placeholder to between 1 and MAX_IMAGE_TOKENS pad tokens
(``vision_not_applied`` otherwise). ``expectedWordSeen`` says whether the decoded answer contains the colour word; the
answer itself never leaves the child. Text mode (no ``mode``) is exactly the PR345 configuration.

The receipt holds fixed tokens, counts, timings, the vLLM version, the quantization name, exception class names and
the sha256 of the generated token ids. Never the generated text, a prompt, a log line, a path or an environment value.

Test hooks in limits (bootstrap never sets them; its tests assert the exact limits it passes): mountsFile, netDir,
devDir, tmpRoot, shmRoot and extraPath (stand-in modules put first on the child's sys.path).
"""
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import time

MARKER = 'ROSE-CLOUD-QWEN-GPU '
REPO, REVISION = 'Qwen/Qwen3.6-35B-A3B-FP8', '95a723d08a9490559dae23d0cff1d9466213d989'
PROMPT = 'Count from one to ten in English words, separated by commas:'
# vllm.LLM keyword arguments (EngineArgs fields). Text only, TP2 over the two A100s, 2048-token context, one sequence,
# eager mode (no CUDA-graph capture or torch.compile: shorter load, no compile cache), remote code off.
ENGINE = {'tensor_parallel_size': 2, 'max_model_len': 2048, 'max_num_seqs': 1, 'gpu_memory_utilization': 0.90,
          'enforce_eager': True, 'language_model_only': True, 'trust_remote_code': False, 'seed': 0,
          'distributed_executor_backend': 'mp', 'disable_log_stats': True}
SAMPLING = {'max_tokens': 32, 'temperature': 0.0, 'seed': 0}
# Vision mode (limits {"mode": "vision"}, CLOUD-QWEN-VISION-001): the same engine with the vision tower loaded instead of
# language_model_only, at most one image and no video per prompt with a 256 x 256 profiling image (vLLM's
# ImageDummyOptions), and the processor's max_pixels capped at 256 x 256 (Qwen3VLProcessingInfo._get_vision_info's
# override) so an image is at most (256 / 32) ** 2 = 64 tokens, far inside the 2048-token context and batch budget. ONE request: a synthetic solid-colour image made in the child
# (no file, no download) and a fixed question, raw Qwen chat format with thinking off, greedy, at most 32 tokens.
VISION_ENGINE = {**{k: v for k, v in ENGINE.items() if k != 'language_model_only'},
                 'limit_mm_per_prompt': {'image': {'count': 1, 'width': 256, 'height': 256}, 'video': 0},
                 'mm_processor_kwargs': {'max_pixels': 256 * 256}}
VISION = {'width': 256, 'height': 256, 'rgb': [255, 0, 0], 'expect': 'red',
          'prompt': '<|im_start|>user\n<|vision_start|><|image_pad|><|vision_end|>What is the colour of this image? '
                    'Answer with one word.<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n'}
IMAGE_PAD = '<|image_pad|>'
MAX_IMAGE_TOKENS = 256  # (256 / 32) ** 2 = 64 expected; more means the size cap was not applied
STOPS = ('done', 'bad_manifest', 'not_isolated', 'bad_inputs', 'engine_arg_unsupported', 'engine_failed',
         'generate_failed', 'crashed', 'deadline', 'vision_not_applied', 'bad_mode', 'bad_bundle', 'server_failed',
         'session_failed')
MODES = ('text', 'vision', 'adapter')
# Adapter mode (limits {"mode": "adapter"}, CLOUD-QWEN-ADAPTER-001): the real adapter (rose_cloud.vllm_brain) is an
# HTTP client of vLLM's OpenAI server, so this mode starts `vllm serve` on the container's own loopback (the container
# has --network none: nothing is published or reachable from outside) with the vision mode's engine bounds, the
# adapter's RAW_TEMPLATE and metrics on, waits for /health, and runs adapter_session.run_session once. The adapter
# sources come from the payload as a bundle (main's third argument) of exactly ADAPTER_FILES.
ADAPTER_FILES = ('vllm_brain.py', 'coop.py', 'pump.py', 'adapter_session.py')
ADAPTER_PORT, ADAPTER_REQUEST_TIMEOUT_S = 18000, 60
SERVER_ARGS = ['--tensor-parallel-size', '2', '--max-model-len', '2048', '--max-num-seqs', '1',
               '--gpu-memory-utilization', '0.9', '--enforce-eager', '--seed', '0', '--distributed-executor-backend',
               'mp', '--limit-mm-per-prompt', json.dumps(VISION_ENGINE['limit_mm_per_prompt'], sort_keys=True),
               '--mm-processor-kwargs', json.dumps(VISION_ENGINE['mm_processor_kwargs'], sort_keys=True),
               '--chat-template-content-format', 'openai']
ADAPTER_STEPS = ('import', 'engine_args', 'server', 'tokenizer', 'session', 'done')
SESSION_STEPS = ('turn1', 'turn2', 'turn3', 'fork', 'cancel', 'late_cancel', 'done')
SEMANTICS = ('inflight_abort', 'inflight_abort_probe_only', 'client_suppressed_only', 'completed_before_cancel',
             'unknown')
TURN_KEYS = ('turn1', 'turn2', 'turn3', 'rewound', 'staleText')
STEPS = ('import', 'engine_args', 'load', 'generate', 'done')
FINISH = ('length', 'stop', 'abort')
TOKEN = re.compile(r'^[A-Za-z][A-Za-z0-9_]{0,63}$')
MODULE = re.compile(r'^[A-Za-z_][A-Za-z0-9_.]{0,63}$')
VERSION = re.compile(r'^[0-9][0-9A-Za-z.+-]{0,31}$')
HEX64 = re.compile(r'^[0-9a-f]{64}$')
CHILD_OUT = 1 << 16
GPU_DEVICE = re.compile(r'^nvidia[0-9]+$')

CHILD = r'''
import hashlib, json, os, sys, time
model, cfg, extra = sys.argv[1], json.loads(sys.argv[2]), sys.argv[3]
if extra:
    sys.path.insert(0, extra)
out = {'step': 'import', 'vllm': None, 'unsupported': [], 'loadS': None, 'generateS': None, 'promptTokens': None,
       'generatedTokens': None, 'finishReason': None, 'outputSha256': None, 'inVocab': None, 'quantization': None,
       'error': None, 'module': None}
def emit(code):
    sys.stdout.write(json.dumps(out) + '\n')
    sys.stdout.flush()
    os._exit(code)  # never wait on engine worker shutdown; the parent kills the group, bootstrap removes the container
try:
    import vllm
    from vllm.engine.arg_utils import EngineArgs
    out['vllm'] = getattr(vllm, '__version__', None)
    out['step'] = 'engine_args'
    fields = getattr(EngineArgs, '__dataclass_fields__', None) or {}
    out['unsupported'] = sorted(k for k in cfg['engine'] if k not in fields)
    if out['unsupported']:
        emit(3)
    out['step'] = 'load'
    t = time.monotonic()
    llm = vllm.LLM(model=model, tokenizer=model, **cfg['engine'])
    out['loadS'] = round(time.monotonic() - t, 1)
    try:
        out['quantization'] = llm.llm_engine.model_config.quantization
    except Exception:
        pass
    out['step'] = 'generate'
    tok = llm.get_tokenizer()
    vision = cfg.get('vision')
    if vision:  # one synthetic image made here: nothing read from disk or network
        from PIL import Image
        image = Image.new('RGB', (vision['width'], vision['height']), tuple(vision['rgb']))
        prompt = {'prompt': vision['prompt'], 'multi_modal_data': {'image': image}}
    else:
        ids = list(tok.encode(cfg['prompt']))
        out['promptTokens'] = len(ids)
        prompt = {'prompt_token_ids': ids}
    t = time.monotonic()
    res = llm.generate([prompt], vllm.SamplingParams(**cfg['sampling']), use_tqdm=False)
    out['generateS'] = round(time.monotonic() - t, 1)
    first = res[0].outputs[0]
    if vision:
        pids = [int(x) for x in (getattr(res[0], 'prompt_token_ids', None) or [])]
        out['promptTokens'] = len(pids)
        out['imageTokens'] = pids.count(tok.convert_tokens_to_ids(vision['pad']))
        out['expectedWordSeen'] = vision['expect'] in tok.decode(list(first.token_ids)).lower()  # a flag; no text
    toks = [int(x) for x in first.token_ids]
    out['generatedTokens'] = len(toks)
    out['finishReason'] = first.finish_reason
    out['outputSha256'] = hashlib.sha256(json.dumps(toks).encode()).hexdigest()
    out['inVocab'] = all(0 <= x < len(tok) for x in toks)
    out['step'] = 'done'
except BaseException as e:
    out['error'] = type(e).__name__
    out['module'] = getattr(e, 'name', None) if isinstance(e, ImportError) else None
    emit(1)
emit(0)
'''


ADAPTER_CHILD = r'''
import json, os, shutil, subprocess, sys, time, urllib.request
model, cfg, extra, pkg = sys.argv[1], json.loads(sys.argv[2]), sys.argv[3], sys.argv[4]
sys.path[:0] = [p for p in (extra, pkg) if p]
out = {'step': 'import', 'vllm': None, 'unsupported': [], 'serverReadyS': None, 'serverExit': None, 'session': None,
       'error': None, 'module': None}
server = None
def emit(code):
    if server is not None and server.poll() is None:
        try:
            server.kill()
        except OSError:
            pass
    sys.stdout.write(json.dumps(out) + '\n')
    sys.stdout.flush()
    os._exit(code)  # the parent kills the whole group (server and engine workers) either way
try:
    import vllm
    from vllm.engine.arg_utils import EngineArgs
    out['vllm'] = getattr(vllm, '__version__', None)
    out['step'] = 'engine_args'
    fields = getattr(EngineArgs, '__dataclass_fields__', None) or {}
    out['unsupported'] = sorted(k for k in cfg['engine'] if k not in fields)
    if out['unsupported']:
        emit(3)
    out['step'] = 'server'
    from rose_cloud.vllm_brain import RAW_TEMPLATE, VllmBrain, load_tokenizer
    from rose_cloud import adapter_session
    template_path = os.path.join(os.environ['TMPDIR'], 'raw_template.jinja')
    with open(template_path, 'w') as f:
        f.write(RAW_TEMPLATE)
    base = 'http://127.0.0.1:%d' % cfg['port']
    command = cfg.get('serverCommand') or [shutil.which('vllm') or 'vllm']
    t = time.monotonic()
    server = subprocess.Popen(command + ['serve', model, '--served-model-name', 'rose-brain', '--host', '127.0.0.1',
                                         '--port', str(cfg['port']), '--chat-template', template_path] + cfg['server'],
                              stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    while True:
        if server.poll() is not None:
            out['serverExit'] = server.returncode
            emit(1)
        try:
            with urllib.request.urlopen(base + '/health', timeout=5) as r:
                if r.status == 200:
                    break
        except Exception:
            pass
        time.sleep(1)
    out['serverReadyS'] = round(time.monotonic() - t, 1)
    out['step'] = 'tokenizer'
    tok, chat_template = load_tokenizer(model)
    out['step'] = 'session'
    from PIL import Image
    image = Image.new('RGB', (cfg['image']['width'], cfg['image']['height']), tuple(cfg['image']['rgb']))
    brain = VllmBrain(tok, base, 'rose-brain', chat_template, timeout_s=cfg['requestTimeoutS'])
    out['session'] = {}
    adapter_session.run_session(brain, image, adapter_session.Server(base), into=out['session'])
    out['step'] = 'done'
except BaseException as e:
    out['error'] = type(e).__name__
    out['module'] = getattr(e, 'name', None) if isinstance(e, ImportError) else None
    emit(1)
emit(0)
'''


class Env:
    def __init__(self, limits):
        self.mounts = limits.get('mountsFile', '/proc/self/mounts')
        self.net = limits.get('netDir', '/sys/class/net')
        self.dev = limits.get('devDir', '/dev')
        self.tmp = limits.get('tmpRoot', '/tmp')
        self.shm = limits.get('shmRoot', '/dev/shm')
        self.extra = limits.get('extraPath', '')


def mount_table(path):
    table = {}
    try:
        with open(path) as f:
            for line in f.read(1 << 20).splitlines():
                parts = line.split()
                if len(parts) >= 4:
                    table[parts[1].replace('\\040', ' ')] = (parts[2], set(parts[3].split(',')))
    except OSError:
        pass
    return table


def size_of(path):
    try:
        st = os.statvfs(path)
        return st.f_blocks * st.f_frsize
    except OSError:
        return None


def isolation(env, model_dir, limits):
    table = mount_table(env.mounts)
    tmp_type, tmp_opts = table.get(env.tmp, (None, set()))
    shm_type = table.get(env.shm, (None, set()))[0]
    tmp_bytes, shm_bytes = size_of(env.tmp), size_of(env.shm)
    try:
        nets = sorted(os.listdir(env.net))
    except OSError:
        nets = None
    try:
        gpus = len([n for n in os.listdir(env.dev) if GPU_DEVICE.match(n)])
    except OSError:
        gpus = None
    out = {
        'tmpfs': tmp_type == 'tmpfs', 'tmpBytes': tmp_bytes,
        'tmpBounded': tmp_bytes is not None and 0 < tmp_bytes <= int(limits['tmpMaxBytes']),
        'tmpExec': tmp_type is not None and 'noexec' not in tmp_opts,
        'shmBytes': shm_bytes,
        'shmBounded': shm_type == 'tmpfs' and shm_bytes is not None and 0 < shm_bytes <= int(limits['shmMaxBytes']),
        'rootReadOnly': 'ro' in table.get('/', ('', set()))[1],
        'modelReadOnly': 'ro' in table.get(model_dir, ('', set()))[1],
        'networkOnlyLo': nets == ['lo'],
        'gpuDevices': gpus,
    }
    out['ok'] = (out['tmpfs'] and out['tmpBounded'] and out['tmpExec'] and out['shmBounded'] and out['rootReadOnly']
                 and out['modelReadOnly'] and out['networkOnlyLo'] and gpus == ENGINE['tensor_parallel_size'])
    return out


def digest(path, kind, size):
    h = hashlib.sha256() if kind == 'sha256' else hashlib.sha1()
    if kind == 'gitsha1':
        h.update(b'blob %d\0' % size)
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


def is_weight(rel):
    return rel.endswith('.safetensors') or rel == 'model.safetensors.index.json'


def inputs(model_dir, files):
    small = {'expected': 0, 'verified': 0, 'missing': 0, 'mismatch': 0}
    weights = {'expected': 0, 'sizeOk': 0, 'missing': 0, 'sizeMismatch': 0}
    for rel, size, kind, want in files:
        path = os.path.join(model_dir, rel)
        if is_weight(rel):
            weights['expected'] += 1
            try:
                weights['sizeOk' if os.path.getsize(path) == size else 'sizeMismatch'] += 1
            except OSError:
                weights['missing'] += 1
            continue
        small['expected'] += 1
        try:
            ok = os.path.getsize(path) == size and digest(path, kind, size) == want
        except OSError:
            small['missing'] += 1
            continue
        small['verified' if ok else 'mismatch'] += 1
    return {'qwenFiles': small, 'weights': weights}


def child_env(work):
    """The image's own environment (docker run passes no -e, so nothing from the host), with caches on the tmpfs and
    every network and telemetry path disabled."""
    env = dict(os.environ)
    for key in [k for k in env if 'PROXY' in k.upper()]:
        del env[key]
    paths = {'HOME': 'home', 'TMPDIR': 'tmp', 'XDG_CACHE_HOME': 'cache', 'HF_HOME': 'hf', 'VLLM_CACHE_ROOT': 'vllm',
             'TRITON_CACHE_DIR': 'triton', 'TORCHINDUCTOR_CACHE_DIR': 'inductor', 'CUDA_CACHE_PATH': 'nv'}
    for key, name in paths.items():
        env[key] = os.path.join(work, name)
        os.makedirs(env[key], exist_ok=True)
    env.update({'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1', 'HF_DATASETS_OFFLINE': '1',
                'HF_HUB_DISABLE_TELEMETRY': '1', 'VLLM_NO_USAGE_STATS': '1', 'DO_NOT_TRACK': '1',
                'VLLM_HOST_IP': '127.0.0.1', 'VLLM_WORKER_MULTIPROC_METHOD': 'spawn',
                'TOKENIZERS_PARALLELISM': 'false', 'PYTHONDONTWRITEBYTECODE': '1'})
    return env


def child_config(mode, limits=None):
    """What the child runs. Text mode is exactly the PR345 configuration."""
    if mode == 'adapter':
        return {'engine': VISION_ENGINE, 'server': SERVER_ARGS, 'port': ADAPTER_PORT,
                'requestTimeoutS': ADAPTER_REQUEST_TIMEOUT_S, 'image': {k: VISION[k] for k in ('width', 'height', 'rgb')},
                'serverCommand': (limits or {}).get('serverCommand')}  # a test hook; bootstrap never sets it
    if mode == 'vision':
        return {'engine': VISION_ENGINE, 'sampling': SAMPLING, 'prompt': None,
                'vision': {**VISION, 'pad': IMAGE_PAD}}
    return {'engine': ENGINE, 'sampling': SAMPLING, 'prompt': PROMPT}


def run_child(model_dir, env, extra, timeout, config, pkg=None):
    """(status, last stdout line). The child runs in its own session; at the deadline the whole group (engine workers
    included) is killed. stdout goes to the tmpfs and only its tail is read; stderr is discarded unread."""
    path = os.path.join(env['TMPDIR'], 'child.out')
    cfg = json.dumps(config)
    with open(path, 'wb') as out:
        try:
            argv = ([sys.executable, '-I', '-B', '-c', ADAPTER_CHILD, model_dir, cfg, extra, pkg] if pkg else
                    [sys.executable, '-I', '-B', '-c', CHILD, model_dir, cfg, extra])
            child = subprocess.Popen(argv, env=env,
                                     stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.DEVNULL,
                                     start_new_session=True)
        except OSError:
            return 'failed', None
        try:
            code = child.wait(max(1.0, timeout))
        except subprocess.TimeoutExpired:
            code = None
        finally:
            try:
                os.killpg(child.pid, signal.SIGKILL)  # engine workers too, whether or not the child exited
            except OSError:
                pass
            if child.poll() is None:
                child.wait()
    try:
        with open(path, 'rb') as f:
            f.seek(max(0, os.path.getsize(path) - CHILD_OUT))
            tail = f.read(CHILD_OUT)
    except OSError:
        tail = b''
    lines = tail.decode('utf-8', 'replace').strip().splitlines()
    status = 'timeout' if code is None else 'crashed' if code < 0 else 'ok' if code == 0 else 'failed'
    return status, lines[-1] if lines else None


def write_bundle(bundle, work):
    """The adapter sources into work/pkg/rose_cloud (the tmpfs); None unless the bundle is exactly ADAPTER_FILES."""
    if not isinstance(bundle, dict) or sorted(bundle) != sorted(ADAPTER_FILES) or not all(
            isinstance(v, str) and len(v) < 1 << 20 for v in bundle.values()):
        return None
    pkg = os.path.join(work, 'pkg')
    os.makedirs(os.path.join(pkg, 'rose_cloud'), exist_ok=True)
    with open(os.path.join(pkg, 'rose_cloud', '__init__.py'), 'w') as f:
        f.write('')
    for name, source in bundle.items():
        with open(os.path.join(pkg, 'rose_cloud', name), 'w') as f:
            f.write(source)
    return pkg


def diagnose(manifest, limits, clock=time.monotonic, bundle=None):
    t0 = clock()
    deadline = t0 + float(limits['deadlineS'])
    env = Env(limits)
    mode = limits.get('mode', 'text')
    result = {'mode': mode, 'stop': None, 'elapsedS': 0, 'isolation': None, 'inputs': None, 'engine': None}
    if mode not in MODES:
        result['stop'] = 'bad_mode'
        return result
    q = manifest.get('groups', {}).get('qwen')
    if not isinstance(q, dict) or q.get('repo') != REPO or q.get('revision') != REVISION or any(
            '..' in f[0].split('/') or f[0].startswith('/') for f in q.get('files', [])):
        result['stop'] = 'bad_manifest'
        return result
    model_dir = os.path.join(manifest['stagingRoot'], q['dir'])
    result['isolation'] = iso = isolation(env, model_dir, limits)
    if not iso['ok']:
        result['stop'] = 'not_isolated'
        return result
    result['inputs'] = n = inputs(model_dir, q['files'])
    if n['qwenFiles']['verified'] != n['qwenFiles']['expected'] or n['weights']['sizeOk'] != n['weights']['expected']:
        result['stop'] = 'bad_inputs'
        return result
    work = os.path.join(env.tmp, 'rose-qwen-gpu')
    os.makedirs(work, exist_ok=True)
    pkg = None
    if mode == 'adapter':
        pkg = write_bundle(bundle, work)
        if pkg is None:
            result['stop'] = 'bad_bundle'
            return result
    status, line = run_child(model_dir, child_env(work), env.extra, deadline - clock(), child_config(mode, limits),
                             pkg)
    engine = {'run': status}
    try:
        engine.update(json.loads(line) if line else {})
    except (TypeError, ValueError):
        pass
    result['engine'] = engine
    step = engine.get('step')
    if status == 'timeout':
        result['stop'] = 'deadline'
    elif status == 'crashed':
        result['stop'] = 'crashed'
    elif step == 'done' and status == 'ok' and mode == 'vision' and not (
            isinstance(engine.get('imageTokens'), int) and 0 < engine['imageTokens'] <= MAX_IMAGE_TOKENS):
        result['stop'] = 'vision_not_applied'  # no image placeholder expanded, or the size cap did not hold
    elif step == 'done' and status == 'ok':
        result['stop'] = 'done'
    elif mode == 'adapter' and step == 'server':
        result['stop'] = 'server_failed'
    elif mode == 'adapter' and step in ('tokenizer', 'session'):
        result['stop'] = 'session_failed'
    elif step == 'engine_args' and engine.get('unsupported'):
        result['stop'] = 'engine_arg_unsupported'
    elif step == 'generate':
        result['stop'] = 'generate_failed'
    else:
        result['stop'] = 'engine_failed'
    result['elapsedS'] = round(clock() - t0)
    return result


# ----------------------------------------------------------------- the allowlist bootstrap re-applies
def _flag(v):
    return v if isinstance(v, bool) else None


def _count(v):
    return v if isinstance(v, int) and not isinstance(v, bool) and 0 <= v < 1 << 50 else None


def _secs(v):
    return round(float(v), 1) if isinstance(v, (int, float)) and not isinstance(v, bool) and 0 <= v < 1e6 else None


def _match(v, pattern):
    return v if isinstance(v, str) and pattern.match(v) else None


def sanitize(raw):
    """Fixed tokens, booleans, bounded numbers, versions, class names and one hex digest, or None."""
    if not isinstance(raw, dict):
        return None
    get = lambda d, k: d.get(k) if isinstance(d, dict) else None  # noqa: E731
    i, n, e = (raw.get(k) for k in ('isolation', 'inputs', 'engine'))
    unsupported = get(e, 'unsupported')
    version = get(e, 'vllm')
    out = {
        'mode': raw.get('mode') if raw.get('mode') in MODES else None,
        'stop': raw.get('stop') if raw.get('stop') in STOPS else None,
        'elapsedS': _count(raw.get('elapsedS')),
        'isolation': None if not isinstance(i, dict) else {
            **{k: _flag(i.get(k)) for k in ('ok', 'tmpfs', 'tmpBounded', 'tmpExec', 'shmBounded', 'rootReadOnly',
                                            'modelReadOnly', 'networkOnlyLo')},
            **{k: _count(i.get(k)) for k in ('tmpBytes', 'shmBytes', 'gpuDevices')}},
        'inputs': None if not isinstance(n, dict) else {
            'qwenFiles': {k: _count(get(get(n, 'qwenFiles'), k)) for k in ('expected', 'verified', 'missing',
                                                                           'mismatch')},
            'weights': {k: _count(get(get(n, 'weights'), k)) for k in ('expected', 'sizeOk', 'missing',
                                                                       'sizeMismatch')}},
        'engine': None if not isinstance(e, dict) else {
            'run': e.get('run') if e.get('run') in ('ok', 'failed', 'crashed', 'timeout') else None,
            'step': e.get('step') if e.get('step') in STEPS or e.get('step') in ADAPTER_STEPS else None,
            'vllm': version if version is None or (isinstance(version, str) and VERSION.match(version)) else 'other',
            'unsupported': (sorted({u for u in unsupported if u in ENGINE or u in VISION_ENGINE})
                            if isinstance(unsupported, list) else None),
            'imageTokens': _count(e.get('imageTokens')), 'expectedWordSeen': _flag(e.get('expectedWordSeen')),
            'loadS': _secs(e.get('loadS')), 'generateS': _secs(e.get('generateS')),
            'promptTokens': _count(e.get('promptTokens')), 'generatedTokens': _count(e.get('generatedTokens')),
            'finishReason': e.get('finishReason') if e.get('finishReason') in FINISH else None,
            'outputSha256': _match(e.get('outputSha256'), HEX64), 'inVocab': _flag(e.get('inVocab')),
            'quantization': _match(e.get('quantization'), TOKEN), 'error': _match(e.get('error'), TOKEN),
            'module': _match(e.get('module'), MODULE)},
    }
    if out['mode'] == 'adapter' and isinstance(out['engine'], dict):  # text and vision receipts are unchanged
        out['engine'].update(serverReadyS=_secs(e.get('serverReadyS')), serverExit=_exit(e.get('serverExit')),
                             session=_session(e.get('session')))
    return out


def _exit(v):
    return v if isinstance(v, int) and not isinstance(v, bool) and -255 <= v <= 255 else None


def _session(s):
    """adapter_session.run_session's result: counts, flags, seconds, token hashes and fixed tokens only."""
    if not isinstance(s, dict):
        return None
    get = lambda d, k: d.get(k) if isinstance(d, dict) else None  # noqa: E731
    turn = lambda t: None if not isinstance(t, dict) else {  # noqa: E731
        'tokens': _count(t.get('tokens')), 'endedWithImEnd': _flag(t.get('endedWithImEnd')),
        'firstTokenS': _secs(t.get('firstTokenS')), 'totalS': _secs(t.get('totalS')),
        'promptExact': _flag(t.get('promptExact')), 'tokenSha256': _match(t.get('tokenSha256'), HEX64)}
    h, c, late = s.get('history'), s.get('cancel'), s.get('lateCancel')
    return {
        'step': s.get('step') if s.get('step') in SESSION_STEPS else None,
        'imageTokens': _count(s.get('imageTokens')), 'imagesInHistory': _count(s.get('imagesInHistory')),
        **{k: turn(s.get(k)) for k in TURN_KEYS},
        'history': None if not isinstance(h, dict) else {
            **{k: _flag(h.get(k)) for k in ('rewindExact', 'forkUntouched', 'staleImageDropped', 'staleRequestSent')},
            'forkImages': _count(h.get('forkImages'))},
        'cancel': None if not isinstance(c, dict) else {
            **{k: _flag(c.get(k)) for k in ('fired', 'noLateAppend', 'workerJoined')},
            **{k: _count(c.get(k)) for k in ('tokensAtCancel', 'appended', 'probeTokens')},
            **{k: _secs(c.get(k)) for k in ('clientStopS', 'requestS', 'runningAfter', 'idleAfterS', 'probeS')},
            'semantics': c.get('semantics') if c.get('semantics') in SEMANTICS else None},
        'lateCancel': None if not isinstance(late, dict) else {
            'tokens': _count(get(late, 'tokens')), 'lateCancelNoop': _flag(get(late, 'lateCancelNoop')),
            'workerJoined': _flag(get(late, 'workerJoined'))},
    }


def main(argv):
    if len(argv) not in (2, 3):
        raise SystemExit(__doc__)
    manifest, limits = json.loads(argv[0]), json.loads(argv[1])
    try:
        bundle = json.loads(argv[2]) if len(argv) == 3 else None
    except ValueError:
        bundle = None
    try:
        result = diagnose(manifest, limits, bundle=bundle)
    except OSError:
        result = {'stop': None}
    print(MARKER + json.dumps(result, sort_keys=True, separators=(',', ':')), flush=True)


if __name__ == '__main__':
    main(sys.argv[1:])
