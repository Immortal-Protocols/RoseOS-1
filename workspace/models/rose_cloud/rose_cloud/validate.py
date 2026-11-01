#!/usr/bin/env python3
"""Offline dependency/config validation of the staged Rose cloud inputs (CLOUD-DEPS-VALIDATE-001).

  python3 -c <this source> <manifest JSON> <limits JSON>

bootstrap's cloud-validate-once diagnostic would run it once, when a separate runtime decision sets that mode, in the
exact cached image: --network none, no GPU, all capabilities dropped, a read-only root filesystem, the staging root
mounted read-only, a size-bounded tmpfs at /tmp as the only writable storage, 2 CPUs, 4 GiB RAM, at most 300 s. It
has not run on a VM. In order, stopping at the first refusal:

1. isolation, checked from inside before anything else (refused as ``not_isolated``): /tmp is a tmpfs of at most
   ``tmpMaxBytes`` that allows exec (the ptts extension is loaded from it), the root filesystem and the staging root
   are mounted read-only, the only network interface is ``lo`` and no /dev/nvidia* device exists;
2. inputs: the ptts wheel's sha256 equals the pin (PTTS_WHEEL_SHA256, also the manifest's), and every non-weight
   Qwen file (config, tokenizer, processor, template) matches its manifest size and hash. Weights are never opened;
3. ptts: ``pip install --isolated --no-index --no-deps --no-cache-dir --target /tmp/...`` of the verified wheel
   (nothing else can be fetched or installed; the image's own site-packages is never written). The wheel's own
   Requires-Dist names are checked against the image's installed distributions (numpy is the only pinned one). Then,
   in a child process: ``import ptts``, ``TTS.stream`` and ``TTS.add_voice`` exist, ``available_quants()`` offers
   f32. No model is loaded and nothing is synthesized;
4. Qwen, in a child process with HF_HUB_OFFLINE/TRANSFORMERS_OFFLINE set: ``AutoConfig``, ``AutoTokenizer`` and
   ``AutoProcessor`` ``from_pretrained(<staged dir>, local_files_only=True, trust_remote_code=False)``. No weights,
   no model class, no inference.

Children run with ``python -I -B`` (no user site, no bytecode writes), HOME/TMPDIR/HF_HOME under the tmpfs, and their
own time limits inside ``deadlineS``. A child that crashes (for example SIGILL on a CPU without AVX2) is a fixed
category, not a lost run. Output: one MARKER line of fixed tokens, booleans, bounded counts, versions, exception
CLASS names and a missing module's import name; sanitize() is the allowlist bootstrap re-applies. Never an exception
message, path, file contents or environment value.

Test hooks in limits (bootstrap never sets them; its tests assert the exact limits it passes): mountsFile, netDir,
devDir, tmpRoot and extraPath (a directory of stand-in modules put first on the children's sys.path).
"""
import hashlib
import json
import os
import re
import subprocess
import sys
import time

MARKER = 'ROSE-CLOUD-VALIDATE '
PTTS_WHEEL_SHA256 = '6efb197e73af258e278e90fcc771f36022c5123e9ecca2fca50ae90472caa60c'
QWEN_ARCH, QWEN_TYPE = 'Qwen3_5MoeForConditionalGeneration', 'qwen3_5_moe'
STOPS = ('done', 'not_isolated', 'bad_inputs', 'deadline', 'bad_manifest')
STATUS = ('ok', 'failed', 'crashed', 'timeout', 'skipped', 'pip_unavailable')
KNOWN_REQUIRES = ('numpy',)  # ptts 0.6.0 Requires-Dist (manifest.groups.ptts.requiresDist)
TOKEN = re.compile(r'^[A-Za-z][A-Za-z0-9_]{0,63}$')  # exception class / processor class names
MODULE = re.compile(r'^[A-Za-z_][A-Za-z0-9_.]{0,63}$')
VERSION = re.compile(r'^[0-9][0-9A-Za-z.+-]{0,31}$')
CHILD_OUT = 1 << 16  # bytes of a child's stdout kept
# The only install: the verified local wheel, no index, no dependencies, no cache, into a target under /tmp.
PIP_INSTALL = ('-m', 'pip', 'install', '--isolated', '--no-index', '--no-deps', '--no-cache-dir',
               '--disable-pip-version-check', '--no-compile', '--no-warn-script-location')

# ptts child: argv = [site dir, extra path or '']. Prints one JSON line.
PTTS_CHILD = r'''
import json, sys
site, extra = sys.argv[1], sys.argv[2]
sys.path[:0] = [p for p in (extra, site) if p]
out = {'import': 'ok', 'version': None, 'stream': None, 'addVoice': None, 'f32': None, 'error': None, 'module': None}
try:
    import ptts
    from importlib import metadata
    try:
        out['version'] = metadata.version('ptts')
    except Exception:
        out['version'] = getattr(ptts, '__version__', None)
    tts = getattr(ptts, 'TTS', None)
    out['stream'] = tts is not None and callable(getattr(tts, 'stream', None))
    out['addVoice'] = tts is not None and callable(getattr(tts, 'add_voice', None))
    quants = ptts.available_quants() if callable(getattr(ptts, 'available_quants', None)) else None
    out['f32'] = isinstance(quants, (list, tuple)) and 'f32' in quants
except BaseException as e:
    out['import'] = 'failed'
    out['error'] = type(e).__name__
    out['module'] = getattr(e, 'name', None) if isinstance(e, ImportError) else None
print(json.dumps(out), flush=True)
'''

# Qwen child: argv = [staged model dir, extra path or '']. Config/tokenizer/processor only. Prints one JSON line.
QWEN_CHILD = r'''
import json, sys
model, extra = sys.argv[1], sys.argv[2]
if extra:
    sys.path.insert(0, extra)
out = {'transformers': None, 'config': 'skipped', 'modelType': None, 'architecture': None, 'tokenizer': 'skipped',
       'tokenizerFast': None, 'chatTemplate': None, 'processor': 'skipped', 'processorClass': None, 'errors': {},
       'modules': {}}
def fail(step, e):
    out[step] = 'failed'
    out['errors'][step] = type(e).__name__
    if isinstance(e, ImportError):
        out['modules'][step] = getattr(e, 'name', None)
kw = dict(local_files_only=True, trust_remote_code=False)
try:
    import transformers
    out['transformers'] = getattr(transformers, '__version__', None)
except BaseException as e:
    fail('config', e)
else:
    try:
        cfg = transformers.AutoConfig.from_pretrained(model, **kw)
        out['config'] = 'ok'
        out['modelType'] = getattr(cfg, 'model_type', None) == %(qtype)r
        out['architecture'] = %(qarch)r in (getattr(cfg, 'architectures', None) or [])
    except BaseException as e:
        fail('config', e)
    try:
        tok = transformers.AutoTokenizer.from_pretrained(model, **kw)
        out['tokenizer'] = 'ok'
        out['tokenizerFast'] = bool(getattr(tok, 'is_fast', False))
        out['chatTemplate'] = bool(getattr(tok, 'chat_template', None))
    except BaseException as e:
        fail('tokenizer', e)
    try:
        proc = transformers.AutoProcessor.from_pretrained(model, **kw)
        out['processor'] = 'ok'
        out['processorClass'] = type(proc).__name__
    except BaseException as e:
        fail('processor', e)
print(json.dumps(out), flush=True)
''' % {'qtype': QWEN_TYPE, 'qarch': QWEN_ARCH}


class Env:
    """Where isolation facts are read from. Defaults are the container's own; tests point them at fixtures."""

    def __init__(self, limits):
        self.mounts = limits.get('mountsFile', '/proc/self/mounts')
        self.net = limits.get('netDir', '/sys/class/net')
        self.dev = limits.get('devDir', '/dev')
        self.tmp = limits.get('tmpRoot', '/tmp')
        self.extra = limits.get('extraPath', '')


def mount_table(path):
    """{mount point: (fstype, set of options)} from a /proc/self/mounts-format file; {} if unreadable."""
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


def isolation(env, staging, max_tmp):
    table = mount_table(env.mounts)
    tmp_type, tmp_opts = table.get(env.tmp, (None, set()))
    try:
        st = os.statvfs(env.tmp)
        tmp_bytes = st.f_blocks * st.f_frsize
    except OSError:
        tmp_bytes = None
    try:
        nets = sorted(os.listdir(env.net))
    except OSError:
        nets = None
    try:
        gpu = any(n.startswith('nvidia') for n in os.listdir(env.dev))
    except OSError:
        gpu = None
    out = {
        'tmpfs': tmp_type == 'tmpfs',
        'tmpBytes': tmp_bytes,
        'tmpBounded': tmp_bytes is not None and 0 < tmp_bytes <= max_tmp,
        'tmpExec': tmp_type is not None and 'noexec' not in tmp_opts,
        'rootReadOnly': 'ro' in table.get('/', ('', set()))[1],
        'stagingReadOnly': 'ro' in table.get(staging, ('', set()))[1],
        'networkOnlyLo': nets == ['lo'],
        'gpuDevices': gpu,
    }
    out['ok'] = (out['tmpfs'] and out['tmpBounded'] and out['tmpExec'] and out['rootReadOnly']
                 and out['stagingReadOnly'] and out['networkOnlyLo'] and gpu is False)
    return out


def digest(path, kind, size):
    h = hashlib.sha256() if kind == 'sha256' else hashlib.sha1()
    if kind == 'gitsha1':
        h.update(b'blob %d\0' % size)
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


def inputs(manifest):
    """The wheel against the pin, and every non-weight Qwen file against the manifest. Weights are never opened."""
    root = manifest['stagingRoot']
    wheel_group = manifest['groups']['ptts']
    rel, size, kind, want = wheel_group['files'][0]
    wheel = os.path.join(root, wheel_group['dir'], rel)
    try:
        wheel_ok = (want == PTTS_WHEEL_SHA256 and kind == 'sha256' and os.path.getsize(wheel) == size
                    and digest(wheel, kind, size) == PTTS_WHEEL_SHA256)
    except OSError:
        wheel_ok = False
    qwen = manifest['groups']['qwen']
    counts = {'expected': 0, 'verified': 0, 'missing': 0, 'mismatch': 0}
    for rel, size, kind, want in qwen['files']:
        if rel.endswith('.safetensors') or rel == 'model.safetensors.index.json':
            continue  # weights and their index: never read here
        counts['expected'] += 1
        path = os.path.join(root, qwen['dir'], rel)
        try:
            ok = os.path.getsize(path) == size and digest(path, kind, size) == want
        except OSError:
            counts['missing'] += 1
            continue
        counts['verified' if ok else 'mismatch'] += 1
    return wheel, os.path.join(root, qwen['dir']), {'wheelSha256': wheel_ok, 'qwenFiles': counts}


def child_env(work):
    env = {'PATH': os.environ.get('PATH', '/usr/local/bin:/usr/bin:/bin'), 'HOME': os.path.join(work, 'home'),
           'TMPDIR': os.path.join(work, 'tmp'), 'HF_HOME': os.path.join(work, 'hf'),
           'XDG_CACHE_HOME': os.path.join(work, 'cache'), 'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1',
           'HF_DATASETS_OFFLINE': '1', 'HF_HUB_DISABLE_TELEMETRY': '1', 'TOKENIZERS_PARALLELISM': 'false',
           'OMP_NUM_THREADS': '2', 'PIP_NO_INPUT': '1', 'PIP_DISABLE_PIP_VERSION_CHECK': '1', 'LC_ALL': 'C.UTF-8'}
    for key in ('HOME', 'TMPDIR', 'HF_HOME', 'XDG_CACHE_HOME'):
        os.makedirs(env[key], exist_ok=True)
    return env


def run(argv, env, timeout):
    """(status, last stdout line or None). status: ok, failed (exit != 0), crashed (signal), timeout. stdout goes to a
    file on the bounded tmpfs (never into this process's memory); only its last CHILD_OUT bytes are read back."""
    path = os.path.join(env['TMPDIR'], 'child.out')
    try:
        with open(path, 'wb') as out:
            p = subprocess.run(argv, env=env, stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.DEVNULL,
                               timeout=max(1.0, timeout))
    except subprocess.TimeoutExpired:
        return 'timeout', None
    except OSError:
        return 'failed', None
    try:
        with open(path, 'rb') as f:
            f.seek(max(0, os.path.getsize(path) - CHILD_OUT))
            tail = f.read(CHILD_OUT)
        os.unlink(path)
    except OSError:
        tail = b''
    lines = tail.decode('utf-8', 'replace').strip().splitlines()
    status = 'ok' if p.returncode == 0 else 'crashed' if p.returncode < 0 else 'failed'
    return status, lines[-1] if lines else None


def requires(site):
    """Requires-Dist names of the installed ptts dist-info (markers ignored): (known -> installed in the image,
    count of other names)."""
    from importlib import metadata
    names = []
    for entry in sorted(os.listdir(site)) if os.path.isdir(site) else []:
        if entry.startswith('ptts-') and entry.endswith('.dist-info'):
            try:
                with open(os.path.join(site, entry, 'METADATA'), encoding='utf-8', errors='replace') as f:
                    for line in f.read(1 << 20).splitlines():
                        if line.startswith('Requires-Dist:'):
                            spec = line.split(':', 1)[1].strip()
                            if 'extra ==' not in spec:
                                names.append(re.split(r'[\s;<>=!~\[(]', spec, maxsplit=1)[0].lower())
            except OSError:
                pass
    known = {}
    for name in KNOWN_REQUIRES:
        if name in names:
            try:
                known[name] = bool(metadata.version(name))
            except Exception:  # noqa: BLE001 - not installed in the image
                known[name] = False
    return known, len([n for n in names if n not in KNOWN_REQUIRES])


def used_bytes(root):
    total = 0
    for base, _, files in os.walk(root):
        for name in files:
            try:
                total += os.lstat(os.path.join(base, name)).st_size
            except OSError:
                pass
    return total


def ptts_step(wheel, work, env, extra, left):
    out = {'install': 'skipped', 'requires': {}, 'otherRequires': None, 'import': 'skipped', 'version': None,
           'stream': None, 'addVoice': None, 'f32': None, 'error': None, 'module': None, 'siteBytes': None}
    import importlib.util
    if importlib.util.find_spec('pip') is None:
        out['install'] = 'pip_unavailable'
        return out
    site = os.path.join(work, 'site')
    status, _ = run([sys.executable, '-I', '-B', *PIP_INSTALL, '--target', site, wheel], env, min(120.0, left()))
    out['install'] = status
    out['siteBytes'] = used_bytes(site)
    if status != 'ok':
        return out
    out['requires'], out['otherRequires'] = requires(site)
    status, line = run([sys.executable, '-I', '-B', '-c', PTTS_CHILD, site, extra], env, min(60.0, left()))
    out['import'] = status
    if status == 'ok':
        try:
            facts = json.loads(line)
            for k in ('version', 'stream', 'addVoice', 'f32', 'error', 'module'):
                out[k] = facts.get(k)
            out['import'] = facts.get('import', 'failed')
        except (TypeError, ValueError, AttributeError):
            out['import'] = 'failed'
    return out


def qwen_step(model_dir, env, extra, left):
    out = {'run': 'skipped'}
    status, line = run([sys.executable, '-I', '-B', '-c', QWEN_CHILD, model_dir, extra], env, min(150.0, left()))
    out['run'] = status
    if status == 'ok':
        try:
            out.update(json.loads(line))
        except (TypeError, ValueError):
            out['run'] = 'failed'
    return out


def validate(manifest, limits, clock=time.monotonic):
    t0 = clock()
    deadline = t0 + float(limits['deadlineS'])
    left = lambda: deadline - clock()  # noqa: E731
    env = Env(limits)
    result = {'stop': None, 'elapsedS': 0, 'isolation': None, 'inputs': None, 'ptts': None, 'qwen': None}
    groups = manifest.get('groups', {})
    if not all(g in groups for g in ('ptts', 'qwen')) or any(
            '..' in f[0].split('/') or f[0].startswith('/') for g in groups.values() for f in g['files']):
        result['stop'] = 'bad_manifest'
        return result
    result['isolation'] = iso = isolation(env, manifest['stagingRoot'], int(limits['tmpMaxBytes']))
    if not iso['ok']:
        result['stop'] = 'not_isolated'  # nothing is installed, imported or loaded
        return result
    wheel, model_dir, result['inputs'] = inputs(manifest)
    if not result['inputs']['wheelSha256'] or result['inputs']['qwenFiles']['verified'] != \
            result['inputs']['qwenFiles']['expected']:
        result['stop'] = 'bad_inputs'
        return result
    work = os.path.join(env.tmp, 'rose-validate')
    os.makedirs(work, exist_ok=True)
    cenv = child_env(work)
    result['ptts'] = ptts_step(wheel, work, cenv, env.extra, left)
    result['qwen'] = qwen_step(model_dir, cenv, env.extra, left) if left() > 1 else {'run': 'timeout'}
    result['tmpUsedBytes'] = used_bytes(work)
    result['stop'] = 'deadline' if left() <= 0 else 'done'
    result['elapsedS'] = round(clock() - t0)
    return result


# ----------------------------------------------------------------- the allowlist bootstrap re-applies
def _flag(v):
    return v if isinstance(v, bool) else None


def _count(v):
    return v if isinstance(v, int) and not isinstance(v, bool) and 0 <= v < 1 << 50 else None


def _status(v):
    return v if v in STATUS else None


def _token(v, pattern=TOKEN):
    return v if isinstance(v, str) and pattern.match(v) else None


def _ver(v):
    return v if v is None or (isinstance(v, str) and VERSION.match(v)) else 'other'


def sanitize(raw):
    """The helper line with only fixed tokens, booleans, bounded counts, versions and class/module names, or None."""
    if not isinstance(raw, dict):
        return None
    get = lambda d, k: d.get(k) if isinstance(d, dict) else None  # noqa: E731
    i, n, p, q = (raw.get(k) for k in ('isolation', 'inputs', 'ptts', 'qwen'))
    files = get(n, 'qwenFiles')
    steps = ('config', 'tokenizer', 'processor')
    return {
        'stop': raw.get('stop') if raw.get('stop') in STOPS else None,
        'elapsedS': _count(raw.get('elapsedS')),
        'tmpUsedBytes': _count(raw.get('tmpUsedBytes')),
        'isolation': None if not isinstance(i, dict) else {
            **{k: _flag(i.get(k)) for k in ('ok', 'tmpfs', 'tmpBounded', 'tmpExec', 'rootReadOnly', 'stagingReadOnly',
                                            'networkOnlyLo', 'gpuDevices')},
            'tmpBytes': _count(i.get('tmpBytes'))},
        'inputs': None if not isinstance(n, dict) else {
            'wheelSha256': _flag(n.get('wheelSha256')),
            'qwenFiles': {k: _count(get(files, k)) for k in ('expected', 'verified', 'missing', 'mismatch')}},
        'ptts': None if not isinstance(p, dict) else {
            'install': _status(p.get('install')), 'import': _status(p.get('import')), 'version': _ver(p.get('version')),
            **{k: _flag(p.get(k)) for k in ('stream', 'addVoice', 'f32')},
            'requires': {k: _flag(get(p.get('requires'), k)) for k in KNOWN_REQUIRES},
            'otherRequires': _count(p.get('otherRequires')), 'siteBytes': _count(p.get('siteBytes')),
            'error': _token(p.get('error')), 'module': _token(p.get('module'), MODULE)},
        'qwen': None if not isinstance(q, dict) else {
            'run': _status(q.get('run')), 'transformers': _ver(q.get('transformers')),
            **{k: _status(q.get(k)) for k in steps},
            **{k: _flag(q.get(k)) for k in ('modelType', 'architecture', 'tokenizerFast', 'chatTemplate')},
            'processorClass': _token(q.get('processorClass')),
            'errors': {k: _token(get(q.get('errors'), k)) for k in steps},
            'modules': {k: _token(get(q.get('modules'), k), MODULE) for k in steps}},
    }


def main(argv):
    if len(argv) != 2:
        raise SystemExit(__doc__)
    manifest, limits = json.loads(argv[0]), json.loads(argv[1])
    try:
        result = validate(manifest, limits)
    except OSError:
        result = {'stop': None}
    print(MARKER + json.dumps(result, sort_keys=True, separators=(',', ':')), flush=True)


if __name__ == '__main__':
    main(sys.argv[1:])
