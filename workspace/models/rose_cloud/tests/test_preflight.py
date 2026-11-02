"""rose_cloud/preflight.py as bootstrap runs it (`python3 -c <source> <manifest>`), as a real subprocess through
bootstrap's own Host.run, against FIXTURES: a stand-in vLLM source tree and ptts package on PYTHONPATH, a staging
tree with some pinned files present, wrong or missing, and a fixture cpuinfo. Nothing here is the cached serving
image, real vLLM or real ptts; the manifest's real pins are used for names, sizes and hashes.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
import sys
import tempfile
import textwrap
import time
import unittest
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path[:0] = [str(ROOT), str(ROOT.parent / 'serving' / 'service')]

import bootstrap as B  # noqa: E402
from rose_cloud import preflight as P  # noqa: E402

SOURCE = (ROOT / 'rose_cloud' / 'preflight.py').read_text()
MANIFEST = json.loads((ROOT / 'rose_cloud' / 'manifest.json').read_text())
CPU_V3 = 'flags\t\t: fpu sse4_2 avx avx2 bmi1 bmi2 f16c fma abm movbe xsave popcnt\n'
PROTOCOL = '''
class OpenAIBaseModel: pass
class CompletionRequest(OpenAIBaseModel):
    model: str | None = None
    prompt: (
        list[Annotated[int, Field(ge=0)]]
        | str
        | None
    ) = None
    top_k: int | None = None
    stop_token_ids: list[int] | None = []
    skip_special_tokens: bool | None = None
    return_tokens_as_token_ids: bool | None = None
    return_token_ids: bool | None = None

class CompletionResponse(OpenAIBaseModel):
    return_token_ids: bool
'''


def gitsha1(data: bytes) -> str:
    return hashlib.sha1(b'blob %d\0' % len(data) + data).hexdigest()


class Tree:
    """A temp dir with site/ (stand-in packages), staging/ and cpuinfo."""

    def __init__(self, vllm=True, ptts='installed', cpu=CPU_V3):
        self.dir = Path(tempfile.mkdtemp(prefix='preflight-'))
        self.site, self.staging = self.dir / 'site', self.dir / 'staging'
        self.site.mkdir()
        self.staging.mkdir()
        (self.dir / 'cpuinfo').write_text(cpu)
        if vllm:
            self.write('vllm/__init__.py', 'raise SystemExit("vllm must not be imported")\n')
            self.write('vllm/model_executor/models/registry.py',
                       '_MODELS = {\n    "Qwen3_5MoeForConditionalGeneration": ("qwen3_5", "X"),\n}\n')
            self.write('vllm/model_executor/models/qwen3_5.py', '')
            self.write('vllm/model_executor/layers/quantization/utils/marlin_utils_fp8.py', '')
            self.write('vllm/model_executor/layers/fused_moe/fused_marlin_moe.py', 'def fused_marlin_moe(): pass\n')
            self.write('vllm/engine/arg_utils.py', 'language_model_only: bool = False\n')
            self.write('vllm/entrypoints/openai/completion/protocol.py', PROTOCOL)
            self.dist('vllm', '0.19.1')
            self.dist('torch', '2.10.0+cu130')
            self.dist('transformers', '5.15.0')
        if ptts == 'installed':
            self.write('ptts/__init__.py', textwrap.dedent('''
                class TTS:
                    def stream(self): pass
                    def add_voice(self): pass
                def available_quants():
                    return ["f32", "q8_0", "q4k"]
                '''))
            self.dist('ptts', '0.6.0')

    def write(self, rel, text, root=None):
        path = (root or self.site) / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def dist(self, name, version):
        self.write(f'{name}-{version}.dist-info/METADATA', f'Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n')

    def manifest(self, **probe):
        m = copy.deepcopy(MANIFEST)
        m['stagingRoot'] = str(self.staging)
        m['probe'].update({'cpuinfo': str(self.dir / 'cpuinfo'), **probe})
        return m

    def run(self, manifest=None, timeout=60, prefix=''):
        manifest = manifest or self.manifest()
        argv = [sys.executable, '-c', prefix + SOURCE, json.dumps(manifest)]  # as the container runs it
        t = time.monotonic()
        code, out = B.Host().run(argv, timeout, env={'PYTHONPATH': str(self.site)})
        return code, out, time.monotonic() - t, manifest

    def receipt(self, **kw):
        code, out, _, manifest = self.run(**kw)
        self.case.assertEqual(code, 0)
        return P.sanitize(json.loads(out.strip().splitlines()[-1][len(P.MARKER):]), manifest), out


class Preflight(unittest.TestCase):
    def tree(self, **kw):
        t = Tree(**kw)
        t.case = self
        self.addCleanup(shutil.rmtree, t.dir, True)
        return t

    def test_capabilities_from_source_and_metadata_without_importing(self):
        t = self.tree()
        r, out = t.receipt()
        self.assertEqual((r['packages']['vllm'], r['packages']['torch'], r['packages']['transformers'],
                          r['packages']['ptts']), ('0.19.1', '2.10.0+cu130', '5.15.0', '0.6.0'))
        self.assertEqual(r['vllm'], {'found': True, 'registryQwen35Moe': True, 'qwen35ModelFile': True,
                                     'fp8MarlinMoe': True, 'languageModelOnly': True,
                                     'completionsFields': dict.fromkeys(P.API_FIELDS + ('tokenIdPrompt',), True)})
        self.assertEqual(r['ptts'], {'importable': True, 'api': True, 'f32': True, 'apiFrom': 'installed'})
        self.assertEqual(r['cpu']['x8664v3'], True)
        self.assertNotIn('vllm must not be imported', out)

    def test_staged_files_present_wrong_and_missing(self):
        t = self.tree()
        phonon, qwen = MANIFEST['groups']['phonon'], MANIFEST['groups']['qwen']
        cfg = next(f for f in qwen['files'] if f[0] == 'generation_config.json')
        t.write(f"{qwen['dir']}/generation_config.json", 'x' * cfg[1], t.staging)  # right size, wrong content
        big = next(f for f in phonon['files'] if f[0] == 'model.safetensors')
        (t.staging / phonon['dir']).mkdir(parents=True)
        with open(t.staging / phonon['dir'] / 'model.safetensors', 'wb') as f:
            f.truncate(big[1])  # sparse file of the pinned size: checked by size only
        t.write(f"{phonon['dir']}/config.json", 'short', t.staging)
        r, out = t.receipt()
        self.assertEqual(r['files']['qwen'], {'expected': len(qwen['files']), 'present': 1,
                                              'missing': len(qwen['files']) - 1, 'sizeMismatch': 0, 'hashChecked': 1,
                                              'hashMismatch': 1})
        self.assertEqual(r['files']['phonon'], {'expected': 3, 'present': 2, 'missing': 1, 'sizeMismatch': 1,
                                                'hashChecked': 0, 'hashMismatch': 0})
        self.assertEqual(r['filesComplete'], dict.fromkeys(MANIFEST['groups'], False))
        self.assertTrue(r['disk']['stagingRoot'])
        self.assertIsInstance(r['disk']['freeBytes'], int)
        self.assertNotIn(str(t.staging), out)  # counts only, never paths

    def test_complete_group_and_hash_match(self):
        t = self.tree()
        m = t.manifest()
        data = b'{"voice": 1}\n'
        m['groups']['voices']['files'] = [['voices/v.safetensors', len(data), 'gitsha1', gitsha1(data)]]
        t.write(f"{m['groups']['voices']['dir']}/voices/v.safetensors", data.decode(), t.staging)
        r, _ = t.receipt(manifest=m)
        self.assertTrue(r['filesComplete']['voices'])
        self.assertEqual(r['files']['voices']['hashChecked'], 1)

    def test_nothing_installed_and_ptts_from_staged_wheel_stub(self):
        t = self.tree(vllm=False, ptts=None, cpu='flags\t\t: fpu sse4_2 avx\n')
        wheel = MANIFEST['groups']['ptts']
        path = t.staging / wheel['dir'] / wheel['files'][0][0]
        path.parent.mkdir(parents=True)
        with zipfile.ZipFile(path, 'w') as z:
            z.writestr('ptts/__init__.pyi', 'class TTS:\n    def __init__(self, quant: str | None = None): ...\n'
                                            '    def stream(self): ...\n    def add_voice(self): ...\n')
        r, _ = t.receipt()
        self.assertEqual(r['vllm']['found'], False)
        self.assertIsNone(r['vllm']['registryQwen35Moe'])
        self.assertEqual(r['ptts'], {'importable': False, 'api': True, 'f32': None, 'apiFrom': 'wheel_stub'})
        self.assertEqual((r['cpu']['avx'], r['cpu']['avx2'], r['cpu']['x8664v3']), (True, False, False))
        self.assertEqual(r['files']['ptts']['sizeMismatch'], 1)  # the fixture wheel is not the pinned one

    def test_hostile_values_become_fixed_tokens(self):
        raw = {'python': '3.12.3; rm -rf /', 'glibc': 'x' * 100, 'packages': {'vllm': '0.19.1 SECRET'},
               'vllm': {'found': 'yes', 'completionsFields': {'top_k': 1}}, 'ptts': {'apiFrom': '/root/SECRET'},
               'cpu': {'avx2': 'true'}, 'disk': {'freeBytes': -5}, 'files': {'qwen': {'present': True}}}
        r = P.sanitize(raw, MANIFEST)
        self.assertEqual((r['python'], r['glibc'], r['packages']['vllm']), ('other', 'other', 'other'))
        self.assertEqual((r['vllm']['found'], r['vllm']['completionsFields']['top_k'], r['ptts']['apiFrom'],
                          r['cpu']['avx2'], r['disk']['freeBytes'], r['files']['qwen']['present']),
                         (None, None, None, None, None, None))
        self.assertNotIn('SECRET', json.dumps(r))
        self.assertIsNone(P.sanitize([], MANIFEST))

    def test_hung_probe_is_killed_at_the_deadline(self):
        t = self.tree()
        fifo = t.dir / 'cpuinfo-fifo'
        os.mkfifo(fifo)  # opening it blocks forever: a probe stuck inside the container
        code, out, elapsed, _ = t.run(manifest=t.manifest(cpuinfo=str(fifo)), timeout=2)
        self.assertEqual(code, 124)
        self.assertLess(elapsed, 5)
        self.assertNotIn(P.MARKER, out)

    def test_noisy_stdout_is_bounded(self):
        t = self.tree()
        _, quiet, _, manifest = t.run(prefix='print("warning: " * 1000)\n')
        module = type('M', (), {'MARKER': P.MARKER, 'sanitize': staticmethod(P.sanitize)})
        self.assertIsNotNone(B.preflight_result(module, manifest, quiet))  # noise before the line is skipped
        code, noisy, _, _ = t.run(prefix='import sys\nsys.stdout.write("x" * (3 << 20) + "\\n")\n')
        self.assertEqual(code, 0)
        self.assertLessEqual(len(noisy.encode()), B.CAPTURE_BYTES)  # Host.run kept 1 MiB, drained the rest
        self.assertIsNone(B.preflight_result(module, manifest, noisy))  # the line fell past the bound: no result

    def test_receipt_fits_the_bound(self):
        t = self.tree()
        r, _ = t.receipt()
        self.assertLess(len(json.dumps(r, sort_keys=True, separators=(',', ':'))), 2048)

    def test_stdlib_only_at_module_level(self):
        import ast
        top = [n for n in ast.parse(SOURCE).body if isinstance(n, (ast.Import, ast.ImportFrom))]
        self.assertEqual({a.name for n in top for a in n.names}, {'hashlib', 'json', 'os', 're', 'sys'})


class Manifest(unittest.TestCase):
    def test_pins_agree_with_the_contract_and_provider(self):
        from rose_cloud import contract, phonon
        g = MANIFEST['groups']
        self.assertEqual((g['qwen']['repo'], g['qwen']['revision']), contract.CLOUD_BRAIN)
        self.assertEqual((g['phonon']['repo'], g['phonon']['revision']), phonon.REPO)
        self.assertEqual((g['voices']['repo'], g['voices']['revision']), phonon.VOICE_REPO)
        for name, size, kind, digest in g['phonon']['files']:
            self.assertEqual(phonon.MODEL_FILES[name], (kind, digest, size))
        self.assertEqual(g['voices']['files'][0][3], phonon.VOICES['zoey'][1])
        self.assertEqual(g['ptts']['quant'], 'f32')
        self.assertEqual(MANIFEST['image']['id'],
                         'sha256:e90b8320f680a6a7b8daff87ad08cdf063a68d869880e662fd6d2cebfef689dc')
        self.assertEqual(MANIFEST['target']['instanceId'], '2705251739288295377')
        self.assertFalse(MANIFEST['trial']['approved'])
        self.assertEqual(len(g['qwen']['files']), 54)
        self.assertEqual(sum(f[1] for f in g['qwen']['files']), 37492949243)
        for group in g.values():
            for name, size, kind, digest in group['files']:
                self.assertRegex(digest, r'^[0-9a-f]{64}$' if kind == 'sha256' else r'^[0-9a-f]{40}$')


if __name__ == '__main__':
    unittest.main()
