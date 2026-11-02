#!/usr/bin/env python3
"""Cloud preflight (MODELS-ROSE-CLOUD-003): what the cached serving image and the data disk already have for the
proposed Rose cloud bring-up, measured without loading, installing or downloading anything.

  python3 -c <this source> <manifest JSON>

bootstrap's cloud-preflight-once diagnostic runs it once in the exact cached image (--pull=never, runc, no network,
no GPU, no credentials, the data disk read-only, at most 120 s). It prints one line, MARKER followed by JSON of
fixed tokens (sanitize() is the allowlist bootstrap re-applies):

- python, glibc and installed package versions, from metadata only (importlib.metadata; no package is imported);
- vLLM capabilities from its installed source text, never by importing it: the Qwen3.5-MoE architecture in the model
  registry, its model file, the completions request fields the adapter sends, FP8 Marlin MoE sources and the
  --language-model-only option. These show the code is present, not that the A100 kernels run;
- ptts: importable or not; if it is, its stream/add_voice API and whether available_quants() offers f32 (importing
  the extension loads no model); if not, the same API facts from the staged wheel's type stub (read as a zip);
- CPU flags for x86-64-v3 (ptts's wheels need AVX2, FMA and F16C);
- free space under the staging root, and per manifest group how many pinned files are present, missing or of the
  wrong size, with small files (<= hashLimitBytes) also hashed. Large files are checked by size only.

Never an exception message, a path outside the manifest's fixed names, file contents or environment values.
"""
import hashlib
import json
import os
import re
import sys

MARKER = 'ROSE-CLOUD-PREFLIGHT '
VERSION = re.compile(r'^[0-9][0-9A-Za-z.+-]{0,31}$')
PACKAGES = ('vllm', 'torch', 'transformers', 'huggingface_hub', 'tokenizers', 'safetensors', 'numpy', 'fastapi',
            'uvicorn', 'cryptography', 'pillow', 'ptts')
CPU_FLAGS = ('avx', 'avx2', 'bmi1', 'bmi2', 'f16c', 'fma', 'abm', 'movbe', 'xsave')  # abm: LZCNT
API_FIELDS = ('return_token_ids', 'return_tokens_as_token_ids', 'stop_token_ids', 'top_k', 'skip_special_tokens')
QWEN_ARCH = 'Qwen3_5MoeForConditionalGeneration'
GROUP_KEYS = ('expected', 'present', 'missing', 'sizeMismatch', 'hashChecked', 'hashMismatch')
READ_LIMIT = 4 << 20  # bytes read from any one source file


def version(dist):
    try:
        from importlib import metadata
        v = metadata.version(dist)
    except Exception:  # noqa: BLE001 - not installed or unreadable
        return None
    return v if isinstance(v, str) and VERSION.match(v) else 'other'


def read(path):
    try:
        with open(path, 'rb') as f:
            return f.read(READ_LIMIT).decode('utf-8', 'replace')
    except OSError:
        return None


def package_dir(name):
    try:
        import importlib.util
        spec = importlib.util.find_spec(name)  # locates a top-level package without running it
    except Exception:  # noqa: BLE001
        return None
    if spec is None or not spec.submodule_search_locations:
        return None
    return list(spec.submodule_search_locations)[0]


def walk(root, limit=2000):
    n = 0
    for base, dirs, files in os.walk(root):
        dirs.sort()
        for name in sorted(files):
            if name.endswith('.py'):
                n += 1
                if n > limit:
                    return
                yield os.path.join(base, name)


def vllm_source():
    root = package_dir('vllm')
    out = {'found': root is not None, 'registryQwen35Moe': None, 'qwen35ModelFile': None, 'fp8MarlinMoe': None,
           'languageModelOnly': None, 'completionsFields': None}
    if root is None:
        return out
    registry = read(os.path.join(root, 'model_executor', 'models', 'registry.py'))
    out['registryQwen35Moe'] = None if registry is None else ('"%s"' % QWEN_ARCH) in registry
    models = os.path.join(root, 'model_executor', 'models')
    out['qwen35ModelFile'] = os.path.isdir(models) and any(n.startswith('qwen3_5') and n.endswith('.py')
                                                          for n in os.listdir(models))
    fp8 = os.path.isfile(os.path.join(root, 'model_executor', 'layers', 'quantization', 'utils',
                                      'marlin_utils_fp8.py'))
    moe = any('fused_marlin_moe' in (read(p) or '')
              for p in walk(os.path.join(root, 'model_executor', 'layers', 'fused_moe'), 200))
    out['fp8MarlinMoe'] = fp8 and moe
    out['languageModelOnly'] = any('language_model_only' in (read(p) or '')
                                   for p in [os.path.join(root, 'engine', 'arg_utils.py')]
                                   + list(walk(os.path.join(root, 'config'), 200)))
    request = None
    for p in walk(os.path.join(root, 'entrypoints'), 400):
        text = read(p) or ''
        start = text.find('class CompletionRequest(')
        if start >= 0:
            end = text.find('\nclass ', start + 1)
            request = text[start:end if end > 0 else len(text)]
            break
    if request is not None:
        fields = {f: bool(re.search(r'^\s+%s\s*:' % f, request, re.M)) for f in API_FIELDS}
        fields['tokenIdPrompt'] = bool(re.search(r'^\s+prompt\s*:[^\n]*(list|List)\[(Annotated\[)?int', request,
                                                 re.M | re.S)) or 'list[Annotated[int' in request
        out['completionsFields'] = fields
    return out


def ptts_facts(wheel_path):
    out = {'importable': False, 'api': None, 'apiFrom': None, 'f32': None}
    if package_dir('ptts') is not None:
        try:
            import ptts
            out['importable'] = True
            tts = getattr(ptts, 'TTS', None)
            out['api'] = bool(tts is not None and hasattr(tts, 'stream') and hasattr(tts, 'add_voice'))
            quants = ptts.available_quants() if hasattr(ptts, 'available_quants') else None
            out['f32'] = isinstance(quants, list) and 'f32' in quants
            out['apiFrom'] = 'installed'
            return out
        except Exception:  # noqa: BLE001
            out['apiFrom'] = 'import_failed'
    if wheel_path and os.path.isfile(wheel_path):
        try:
            import zipfile
            with zipfile.ZipFile(wheel_path) as z:
                stub = next((n for n in z.namelist() if n.endswith('ptts/__init__.pyi')), None)
                text = z.read(stub).decode('utf-8', 'replace') if stub else ''
            out['api'] = all(s in text for s in ('def stream(', 'def add_voice(', 'quant: str | None'))
            out['f32'] = None  # the quant list is compiled code; the stub cannot show it
            out['apiFrom'] = 'wheel_stub'
        except Exception:  # noqa: BLE001
            out['apiFrom'] = 'wheel_unreadable'
    return out


def cpu(path):
    text = read(path) or ''
    line = next((l for l in text.splitlines() if l.startswith('flags')), None)
    if line is None:
        return {f: None for f in CPU_FLAGS} | {'x8664v3': None}
    flags = set(line.split(':', 1)[-1].split())
    out = {f: f in flags for f in CPU_FLAGS}
    out['x8664v3'] = all(out.values())
    return out


def digest(path, kind):
    h = hashlib.sha256() if kind == 'sha256' else hashlib.sha1()
    if kind == 'gitsha1':
        h.update(b'blob %d\0' % os.path.getsize(path))
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


def files(manifest):
    root = manifest['stagingRoot']
    limit = manifest['probe']['hashLimitBytes']
    out = {}
    for name, group in manifest['groups'].items():
        counts = dict.fromkeys(GROUP_KEYS, 0)
        for rel, size, kind, want in group['files']:
            counts['expected'] += 1
            path = os.path.join(root, group['dir'], rel)
            try:
                actual = os.path.getsize(path) if os.path.isfile(path) else None
            except OSError:
                actual = None
            if actual is None:
                counts['missing'] += 1
                continue
            counts['present'] += 1
            if actual != size:
                counts['sizeMismatch'] += 1
            elif size <= limit:
                counts['hashChecked'] += 1
                try:
                    if digest(path, kind) != want:
                        counts['hashMismatch'] += 1
                except OSError:
                    counts['hashMismatch'] += 1
        out[name] = counts
    return out


def probe(manifest):
    root = manifest['stagingRoot']
    wheel = manifest['groups']['ptts']
    try:
        stat = os.statvfs(root if os.path.isdir(root) else os.path.dirname(root))
        free = stat.f_bavail * stat.f_frsize
    except OSError:
        free = None
    try:
        import platform
        libc = platform.libc_ver()[1] or None
    except Exception:  # noqa: BLE001
        libc = None
    return {
        'python': '%d.%d.%d' % sys.version_info[:3],
        'glibc': libc if libc is None or VERSION.match(libc) else 'other',
        'packages': {p: version(p) for p in PACKAGES},
        'vllm': vllm_source(),
        'ptts': ptts_facts(os.path.join(root, wheel['dir'], wheel['files'][0][0])),
        'cpu': cpu(manifest['probe'].get('cpuinfo', '/proc/cpuinfo')),
        'disk': {'stagingRoot': os.path.isdir(root), 'freeBytes': free},
        'files': files(manifest),
    }


# ----------------------------------------------------------------- the allowlist bootstrap re-applies
def _flag(v):
    return v if isinstance(v, bool) else None


def _ver(v):
    return v if v is None or (isinstance(v, str) and VERSION.match(v)) else 'other'


def _count(v):
    return v if isinstance(v, int) and not isinstance(v, bool) and 0 <= v < 1 << 50 else None


def sanitize(raw, manifest):
    """The probe line's fixed tokens, re-checked (the line comes from inside a container), or None."""
    if not isinstance(raw, dict):
        return None
    get = lambda d, k: d.get(k) if isinstance(d, dict) else None  # noqa: E731
    v, p, c, d, f = (raw.get(k) for k in ('vllm', 'ptts', 'cpu', 'disk', 'files'))
    fields = get(v, 'completionsFields')
    api_from = get(p, 'apiFrom')
    out = {
        'python': _ver(raw.get('python')), 'glibc': _ver(raw.get('glibc')),
        'packages': {k: _ver(get(raw.get('packages'), k)) for k in PACKAGES},
        'vllm': {k: _flag(get(v, k)) for k in ('found', 'registryQwen35Moe', 'qwen35ModelFile', 'fp8MarlinMoe',
                                                'languageModelOnly')},
        'ptts': {'importable': _flag(get(p, 'importable')), 'api': _flag(get(p, 'api')), 'f32': _flag(get(p, 'f32')),
                 'apiFrom': api_from if api_from in ('installed', 'import_failed', 'wheel_stub',
                                                     'wheel_unreadable') else None},
        'cpu': {k: _flag(get(c, k)) for k in CPU_FLAGS + ('x8664v3',)},
        'disk': {'stagingRoot': _flag(get(d, 'stagingRoot')), 'freeBytes': _count(get(d, 'freeBytes'))},
        'files': {g: {k: _count(get(get(f, g), k)) for k in GROUP_KEYS} for g in manifest['groups']},
    }
    out['vllm']['completionsFields'] = (None if not isinstance(fields, dict) else
                                        {k: _flag(fields.get(k)) for k in API_FIELDS + ('tokenIdPrompt',)})
    out['filesComplete'] = {g: c['expected'] is not None and c['present'] == c['expected'] and not c['sizeMismatch']
                            and not c['hashMismatch'] for g, c in out['files'].items()}
    return out


def main(argv):
    if len(argv) != 1:
        raise SystemExit(__doc__)
    manifest = json.loads(argv[0])
    print(MARKER + json.dumps(probe(manifest), sort_keys=True, separators=(',', ':')), flush=True)


if __name__ == '__main__':
    main(sys.argv[1:])
