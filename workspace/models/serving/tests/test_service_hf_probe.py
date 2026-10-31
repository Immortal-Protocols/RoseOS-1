"""The offline loader probe (MODEL-HF-PROBE-001), run exactly as bootstrap runs it: `python3 -c <source> <repo> <revision>`.

transformers, huggingface_hub and vLLM are stand-ins with the upstream names the probe uses; the pinned image's own
versions are not available here, so these tests show the probe's handling, not the image's behaviour."""
import ast
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest

HERE = Path(__file__).resolve().parent
PROBE = HERE.parent / 'service' / 'hf_probe.py'
sys.path.insert(0, str(PROBE.parent))
import hf_probe  # noqa: E402

REPO, REVISION = 'meta-models/Muse-Glimmer-30B', 'c' * 40

TRANSFORMERS = '''
import json, os
MODE = json.loads(os.environ['FAKE_MODE'])
__version__ = MODE.get('version', '4.57.1')
EXPECTED = {'revision': os.environ['FAKE_REVISION'], 'local_files_only': True}

def _load(name, repo, kw, remote):
    want = dict(EXPECTED, **({'trust_remote_code': False} if remote else {}))
    assert repo == os.environ['FAKE_REPO'] and kw == want, (repo, kw)  # pinned, offline, no remote code
    error = MODE.get('raise', {}).get(name)
    if error:
        import builtins
        cls = getattr(builtins, error, None) or type(error, (Exception,), {})
        raise cls('SECRET-TEXT /root/.cache/huggingface/token hf_abc123')

class AutoConfig:
    @staticmethod
    def from_pretrained(repo, **kw): _load('autoConfig', repo, kw, True)
class AutoTokenizer:
    @staticmethod
    def from_pretrained(repo, **kw): _load('autoTokenizer', repo, kw, True)
class AutoProcessor:
    @staticmethod
    def from_pretrained(repo, **kw): _load('autoProcessor', repo, kw, True)
class GenerationConfig:
    @staticmethod
    def from_pretrained(repo, **kw): _load('generationConfig', repo, kw, False)
'''
CONFIG_AUTO = '''
import json, os
CONFIG_MAPPING_NAMES = dict.fromkeys(json.loads(os.environ['FAKE_MODE']).get('transformers_types', []), 'X')
'''
HUB = '''
import json, os
from huggingface_hub.file_download import _CACHED_NO_EXIST
def try_to_load_from_cache(repo, filename, revision=None):
    state = json.loads(os.environ['FAKE_MODE']).get('files', {}).get(filename)
    if state == 'raise':
        raise OSError('SECRET /root/.cache')
    return '/root/.cache/huggingface/hub/x/' + filename if state == 'present' else _CACHED_NO_EXIST if state == 'absent' else None
'''
VLLM_CONFIG = '''
import json, os
_CONFIG_REGISTRY = dict.fromkeys(json.loads(os.environ['FAKE_MODE']).get('vllm_types', []), object)
'''


class ProbeScript(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix='hf-probe-'))
        self.addCleanup(shutil.rmtree, self.dir, True)

    def modules(self, transformers=True, hub=True, vllm=True):
        files = {}
        if transformers:
            files.update({'transformers/__init__.py': TRANSFORMERS, 'transformers/models/__init__.py': '',
                          'transformers/models/auto/__init__.py': '',
                          'transformers/models/auto/configuration_auto.py': CONFIG_AUTO})
        if hub:
            files.update({'huggingface_hub/__init__.py': HUB,
                          'huggingface_hub/file_download.py': '_CACHED_NO_EXIST = object()\n'})
        if vllm:
            files.update({'vllm/__init__.py': '', 'vllm/transformers_utils/__init__.py': '',
                          'vllm/transformers_utils/config.py': VLLM_CONFIG})
        for rel, text in files.items():
            (self.dir / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.dir / rel).write_text(textwrap.dedent(text))

    def run_probe(self, mode, timeout=60):
        env = {'PATH': os.environ.get('PATH', ''), 'PYTHONPATH': str(self.dir), 'FAKE_MODE': json.dumps(mode),
               'FAKE_REPO': REPO, 'FAKE_REVISION': REVISION, 'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1'}
        r = subprocess.run([sys.executable, '-c', PROBE.read_text(), REPO, REVISION], capture_output=True, text=True,
                           timeout=timeout, env=env, cwd=self.dir)
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        line, = [l for l in r.stdout.splitlines() if l.startswith(hf_probe.MARKER)]
        self.assertNotIn('SECRET', r.stdout)
        self.assertNotIn('/root', r.stdout)
        self.assertNotIn('hf_abc', r.stdout)
        return json.loads(line[len(hf_probe.MARKER):])

    def test_all_loaders_pass_with_pinned_offline_arguments(self):
        self.modules()
        out = self.run_probe({'transformers_types': ['muse_glimmer'], 'vllm_types': [],
                              'files': {'config.json': 'present', 'tokenizer_config.json': 'present',
                                        'tokenizer.json': 'present', 'generation_config.json': 'present',
                                        'preprocessor_config.json': 'absent', 'processor_config.json': 'raise'}})
        self.assertEqual(out['loaders'], dict.fromkeys(hf_probe.LOADERS, 'ok'))
        self.assertEqual((out['transformersVersion'], out['registeredTransformers'], out['registeredVllm']),
                         ('4.57.1', True, False))
        self.assertEqual(out['files'], {'config.json': 'present', 'tokenizer_config.json': 'present',
                                        'tokenizer.json': 'present', 'generation_config.json': 'present',
                                        'preprocessor_config.json': 'absent', 'processor_config.json': 'unknown'})
        self.assertEqual(self.run_probe({})['files'], dict.fromkeys(hf_probe.FILES, 'not_cached'))

    def test_each_loader_fails_independently_by_type_only(self):
        self.modules()
        out = self.run_probe({'raise': {'autoConfig': 'ValueError', 'autoTokenizer': 'OSError',
                                        'autoProcessor': 'SecretCustomError'}, 'version': '99.0.0.dev1'})
        self.assertEqual(out['loaders'], {'autoConfig': 'value_error', 'autoTokenizer': 'os_error',
                                          'autoProcessor': 'other', 'generationConfig': 'ok'})
        self.assertEqual(out['transformersVersion'], '99.0.0.dev1')
        self.assertEqual(self.run_probe({'version': '5.23.0.dev0'})['transformersVersion'], '5.23.0.dev0')  # upstream
        out = self.run_probe({'raise': {'generationConfig': 'KeyError'}, 'version': '4.x SECRET'})
        self.assertEqual((out['loaders']['generationConfig'], out['transformersVersion']), ('key_error', 'other'))

    def test_missing_cache_reports_os_errors_and_absent_files(self):
        self.modules()
        out = self.run_probe({'raise': dict.fromkeys(hf_probe.LOADERS, 'FileNotFoundError'),
                              'files': dict.fromkeys(hf_probe.FILES, 'absent')})
        self.assertEqual(out['loaders'], dict.fromkeys(hf_probe.LOADERS, 'os_error'))
        self.assertEqual(out['files'], dict.fromkeys(hf_probe.FILES, 'absent'))

    def test_missing_libraries_are_reported_not_fatal(self):
        self.modules(transformers=False, hub=False, vllm=False)
        out = self.run_probe({})
        self.assertEqual(out['loaders'], dict.fromkeys(hf_probe.LOADERS, 'import_error'))
        self.assertEqual((out['transformersVersion'], out['registeredTransformers'], out['registeredVllm']),
                         (None, None, None))
        self.assertEqual(set(out['files'].values()), {'unknown'})

    def test_probe_imports_only_stdlib_at_module_level(self):
        tree = ast.parse(PROBE.read_text())
        top = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))]
        names = {a.name.split('.')[0] for n in top for a in n.names} | {n.module for n in top
                                                                         if isinstance(n, ast.ImportFrom)}
        self.assertEqual(names - {None}, {'json', 're', 'sys'})

    def test_category_vocabulary(self):
        self.assertEqual(hf_probe.category(type('HFValidationError', (ValueError,), {})()), 'value_error')
        self.assertEqual(hf_probe.category(ModuleNotFoundError()), 'import_error')
        self.assertEqual(hf_probe.category(FileNotFoundError()), 'os_error')
        self.assertEqual(hf_probe.category(type('X', (Exception,), {})()), 'other')
        self.assertTrue(set(hf_probe.CATEGORIES) >= {'ok', 'other'})


if __name__ == '__main__':
    unittest.main()
