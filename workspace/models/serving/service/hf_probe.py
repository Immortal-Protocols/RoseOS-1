#!/usr/bin/env python3
"""Offline loader probe for the pinned model (MODEL-HF-PROBE-001). Runs inside the exact pinned vLLM image, started by
bootstrap's hf-probe-once diagnostic with no GPU, no network, the Hugging Face cache read-only and offline variables.

  python3 -c <this source> <repo> <revision>

It tries four loaders independently, each in its own try, with local_files_only=True and trust_remote_code=False:
AutoConfig, AutoTokenizer, AutoProcessor and GenerationConfig. It prints one line, MARKER followed by JSON of fixed
tokens only: a result category per loader, the transformers version (only if it matches VERSION), whether the model
type is registered with transformers and with vLLM, and whether each small config file is cached. Never an exception
message, path, frame or environment value.

Names checked against upstream main (no commit visible), NOT against the pinned image, whose source is not available
outside it: transformers' AutoConfig.from_pretrained (trust_remote_code, revision, local_files_only; ValueError for an
unrecognized model_type), GenerationConfig.from_pretrained (local_files_only, revision), CONFIG_MAPPING_NAMES reachable
from models.auto.configuration_auto, __version__ like "5.23.0.dev0";
huggingface_hub.file_download.try_to_load_from_cache (path; _CACHED_NO_EXIST when the cache recorded the file as
missing upstream; None when it is simply not cached) and _CACHED_NO_EXIST;
vllm.transformers_utils.config._CONFIG_REGISTRY. Unverified even
upstream: the AutoTokenizer and AutoProcessor keyword handling. Anything missing reports `import_error`, another
category, null or `unknown` instead of failing the probe.
"""
import json
import re
import sys

MARKER = 'ROSE-HF-PROBE '
MODEL_TYPE = 'muse_glimmer'
VERSION = re.compile(r'^\d{1,3}\.\d{1,3}\.\d{1,3}(?:\.dev\d{1,4}|rc\d{1,2})?$')
LOADERS = ('autoConfig', 'autoTokenizer', 'autoProcessor', 'generationConfig')
FILES = ('config.json', 'tokenizer_config.json', 'tokenizer.json', 'generation_config.json', 'preprocessor_config.json',
         'processor_config.json')
FILE_STATES = ('present', 'absent', 'not_cached', 'unknown')  # absent: the cache recorded it missing upstream
CATEGORIES = ('ok', 'value_error', 'os_error', 'key_error', 'type_error', 'attribute_error', 'import_error',
              'runtime_error', 'not_implemented', 'other')


def category(exc):
    """A fixed category from the exception's type only (subclasses included); its message is never read."""
    for kind, name in ((NotImplementedError, 'not_implemented'), (ImportError, 'import_error'),
                       (ValueError, 'value_error'), (KeyError, 'key_error'), (TypeError, 'type_error'),
                       (AttributeError, 'attribute_error'), (OSError, 'os_error'), (RuntimeError, 'runtime_error')):
        if isinstance(exc, kind):
            return name
    return 'other'


def attempt(load):
    try:
        load()
        return 'ok'
    except Exception as e:  # noqa: BLE001 - classified by type only, never reported
        return category(e)


def probe(repo, revision):
    out = {'loaders': {}, 'transformersVersion': None, 'registeredTransformers': None, 'registeredVllm': None,
           'files': {}}
    try:
        import transformers
    except Exception as e:  # noqa: BLE001
        out['loaders'] = dict.fromkeys(LOADERS, category(e))
        transformers = None
    if transformers is not None:
        version = getattr(transformers, '__version__', None)
        out['transformersVersion'] = version if isinstance(version, str) and VERSION.match(version) else 'other'
        kw = {'revision': revision, 'local_files_only': True}
        calls = (('autoConfig', lambda: transformers.AutoConfig.from_pretrained(repo, trust_remote_code=False, **kw)),
                 ('autoTokenizer', lambda: transformers.AutoTokenizer.from_pretrained(
                     repo, trust_remote_code=False, **kw)),
                 ('autoProcessor', lambda: transformers.AutoProcessor.from_pretrained(
                     repo, trust_remote_code=False, **kw)),
                 ('generationConfig', lambda: transformers.GenerationConfig.from_pretrained(repo, **kw)))
        for name, load in calls:
            out['loaders'][name] = attempt(load)
        try:
            from transformers.models.auto import configuration_auto
            out['registeredTransformers'] = MODEL_TYPE in configuration_auto.CONFIG_MAPPING_NAMES
        except Exception:  # noqa: BLE001
            pass
    try:
        from vllm.transformers_utils import config as vllm_config
        out['registeredVllm'] = MODEL_TYPE in vllm_config._CONFIG_REGISTRY
    except Exception:  # noqa: BLE001
        pass
    try:
        from huggingface_hub import try_to_load_from_cache
        from huggingface_hub.file_download import _CACHED_NO_EXIST
    except Exception:  # noqa: BLE001
        try_to_load_from_cache = _CACHED_NO_EXIST = None
    for name in FILES:
        state = 'unknown'
        if try_to_load_from_cache is not None:
            try:
                found = try_to_load_from_cache(repo, name, revision=revision)
                state = ('present' if isinstance(found, str) else 'absent' if found is _CACHED_NO_EXIST
                         else 'not_cached' if found is None else 'unknown')
            except Exception:  # noqa: BLE001
                state = 'unknown'
        out['files'][name] = state
    return out


def main(argv):
    if len(argv) != 2:
        raise SystemExit(__doc__)
    print(MARKER + json.dumps(probe(argv[0], argv[1]), sort_keys=True), flush=True)


if __name__ == '__main__':
    main(sys.argv[1:])
