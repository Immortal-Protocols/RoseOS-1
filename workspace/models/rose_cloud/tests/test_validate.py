"""rose_cloud/validate.py (CLOUD-DEPS-VALIDATE-001) with local fixtures only: a synthetic ptts wheel installed by the
real ``pip --no-index --no-deps --target`` into a temp dir, stand-in ``ptts``/``transformers`` modules, and fixture
mount/net/dev tables. Nothing here downloads, loads a model or touches a VM; the real wheel, image, tmpfs and
transformers are unverified until a separately approved run.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path[:0] = [str(ROOT)]

from rose_cloud import validate as V  # noqa: E402

MANIFEST = json.loads((ROOT / 'rose_cloud' / 'manifest.json').read_text())
SOURCE = (ROOT / 'rose_cloud' / 'validate.py').read_text()
WHEEL = 'ptts-0.6.0-cp39-abi3-manylinux2014_x86_64.manylinux_2_17_x86_64.whl'
PIN = V.PTTS_WHEEL_SHA256  # the source constant, before any test patches it
HAS_PIP = __import__('importlib.util').util.find_spec('pip') is not None

PTTS_OK = '''
class TTS:
    def __init__(self, *a, **k):
        raise RuntimeError('a model load is never part of validation')
    def stream(self, *a, **k):
        raise RuntimeError('synthesis is never part of validation')
    def add_voice(self, *a, **k):
        raise RuntimeError('never called')
def available_quants():
    return ['f32', 'q8_0', 'q4k']
'''

TRANSFORMERS = '''
import json, os, time
__version__ = '4.99.0'
MODE = json.load(open(os.path.join(os.path.dirname(__file__), 'mode.json')))
CALLS = os.path.join(os.path.dirname(__file__), 'calls.log')
def guard(model, kw):
    with open(CALLS, 'a') as f:
        f.write(json.dumps({'kw': kw, 'offline': os.environ.get('HF_HUB_OFFLINE'),
                            'tf': os.environ.get('TRANSFORMERS_OFFLINE'), 'home': os.environ.get('HOME')}) + '\\n')
    if kw != {'local_files_only': True, 'trust_remote_code': False} or os.environ.get('HF_HUB_OFFLINE') != '1':
        raise PermissionError('not offline: SECRET')
    if MODE.get('sleep'):
        time.sleep(MODE['sleep'])
class _Cfg:
    pass
class AutoConfig:
    @staticmethod
    def from_pretrained(model, **kw):
        guard(model, kw)
        if MODE.get('config') == 'unknown':
            raise ValueError('The checkpoint you are trying to load has model type SECRET /mnt/x')
        c = _Cfg()
        raw = json.load(open(os.path.join(model, 'config.json')))
        c.model_type, c.architectures = raw['model_type'], raw['architectures']
        return c
class _Tok:
    is_fast = True
    chat_template = '{{ messages }}'
class AutoTokenizer:
    @staticmethod
    def from_pretrained(model, **kw):
        guard(model, kw)
        json.load(open(os.path.join(model, 'tokenizer.json')))
        return _Tok()
class Qwen3VLProcessor:
    pass
class AutoProcessor:
    @staticmethod
    def from_pretrained(model, **kw):
        guard(model, kw)
        if MODE.get('processor') == 'missing':
            raise ModuleNotFoundError('No module named torchvision (SECRET)', name='torchvision')
        return Qwen3VLProcessor()
'''


def gitsha1(data):
    return hashlib.sha1(b'blob %d\0' % len(data) + data).hexdigest()


def wheel_bytes(init=PTTS_OK, requires=('numpy',)):
    files = {'ptts/__init__.py': init.encode(),
             'ptts-0.6.0.dist-info/METADATA': ('Metadata-Version: 2.1\nName: ptts\nVersion: 0.6.0\n'
                                               + ''.join(f'Requires-Dist: {r}\n' for r in requires)).encode(),
             'ptts-0.6.0.dist-info/WHEEL': b'Wheel-Version: 1.0\nGenerator: fixture\nRoot-Is-Purelib: false\n'
                                           b'Tag: cp39-abi3-manylinux2014_x86_64\n'}
    record = []
    for name, data in files.items():
        h = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b'=').decode()
        record.append(f'{name},sha256={h},{len(data)}')
    record.append('ptts-0.6.0.dist-info/RECORD,,')
    files['ptts-0.6.0.dist-info/RECORD'] = ('\n'.join(record) + '\n').encode()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as z:
        for name, data in files.items():
            z.writestr(name, data)
    return buf.getvalue()


class Fixture(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix='validate-'))
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.staging = self.dir / 'rose-cloud'
        self.tmp = self.dir / 'tmp'
        self.extra = self.dir / 'extra'
        for d in (self.tmp, self.extra / 'transformers', self.dir / 'net' / 'lo', self.dir / 'dev'):
            d.mkdir(parents=True)
        (self.dir / 'dev' / 'null').write_text('')
        (self.extra / 'transformers' / '__init__.py').write_text(TRANSFORMERS)
        self.mode({})
        qdir = self.staging / 'qwen-fixture'
        qdir.mkdir(parents=True)
        files = {'config.json': json.dumps({'model_type': V.QWEN_TYPE, 'architectures': [V.QWEN_ARCH]}).encode(),
                 'tokenizer.json': b'{"model": {"type": "BPE"}}',
                 'tokenizer_config.json': b'{}', 'chat_template.jinja': b'{{ messages }}',
                 'preprocessor_config.json': b'{}'}
        qfiles = []
        for name, data in files.items():
            (qdir / name).write_bytes(data)
            kind = 'sha256' if name == 'tokenizer.json' else 'gitsha1'
            qfiles.append([name, len(data), kind, hashlib.sha256(data).hexdigest() if kind == 'sha256'
                           else gitsha1(data)])
        qfiles.append(['layers-0.safetensors', 10 << 30, 'sha256', '0' * 64])  # never created, never opened
        qfiles.append(['model.safetensors.index.json', 999, 'gitsha1', '0' * 40])
        self.manifest = {'stagingRoot': str(self.staging), 'groups': {
            'qwen': {'repo': 'Qwen/x', 'revision': 'r', 'dir': 'qwen-fixture', 'files': qfiles},
            'ptts': {'package': 'ptts', 'version': '0.6.0', 'dir': 'wheels', 'files': []}}}
        self.put_wheel(wheel_bytes())
        self.write_mounts()
        self.limits = {'deadlineS': 120, 'tmpMaxBytes': 1 << 62, 'mountsFile': str(self.dir / 'mounts'),
                       'netDir': str(self.dir / 'net'), 'devDir': str(self.dir / 'dev'), 'tmpRoot': str(self.tmp),
                       'extraPath': str(self.extra)}

    def put_wheel(self, data, pin=True):
        w = self.staging / 'wheels' / WHEEL
        w.parent.mkdir(parents=True, exist_ok=True)
        w.write_bytes(data)
        digest = hashlib.sha256(data).hexdigest()
        self.manifest['groups']['ptts']['files'] = [[WHEEL, len(data), 'sha256', digest]]
        if pin:
            self.patch_pin(digest)

    def patch_pin(self, digest):
        old = V.PTTS_WHEEL_SHA256
        V.PTTS_WHEEL_SHA256 = digest
        self.addCleanup(setattr, V, 'PTTS_WHEEL_SHA256', old)

    def mode(self, mode):
        (self.extra / 'transformers' / 'mode.json').write_text(json.dumps(mode))

    def write_mounts(self, tmp_opts='rw,nosuid,nodev,size=524288k,mode=1777', root='ro', staging='ro',
                     tmp_type='tmpfs'):
        (self.dir / 'mounts').write_text(
            f'overlay / overlay {root},relatime,lowerdir=/x 0 0\n'
            f'{tmp_type} {self.tmp} {tmp_type} {tmp_opts} 0 0\n'
            f'/dev/sdb {self.staging} ext4 {staging},relatime 0 0\n')

    def calls(self):
        p = self.extra / 'transformers' / 'calls.log'
        return [json.loads(l) for l in p.read_text().splitlines()] if p.exists() else []

    def run_validate(self, **limits):
        return V.validate(self.manifest, dict(self.limits, **limits))


@unittest.skipUnless(HAS_PIP, 'pip is not installed for this python (the production path needs it in the image)')
class Validate(Fixture):
    def test_happy_path_installs_into_tmp_only_and_loads_config_tokenizer_processor(self):
        argvs = []
        real_run = V.run
        V.run = lambda argv, env, timeout: (argvs.append((list(argv), dict(env))), real_run(argv, env, timeout))[1]
        self.addCleanup(setattr, V, 'run', real_run)
        opened = []
        real = V.digest
        V.digest = lambda path, kind, size: (opened.append(path), real(path, kind, size))[1]
        self.addCleanup(setattr, V, 'digest', real)
        r = self.run_validate()
        self.assertEqual(r['stop'], 'done')
        self.assertTrue(r['isolation']['ok'])
        self.assertEqual(r['inputs'], {'wheelSha256': True, 'qwenFiles': {'expected': 5, 'verified': 5, 'missing': 0,
                                                                          'mismatch': 0}})
        self.assertFalse([p for p in opened if 'safetensors' in p])  # weights and their index are never opened
        p = r['ptts']
        self.assertEqual((p['install'], p['import'], p['version'], p['stream'], p['addVoice'], p['f32']),
                         ('ok', 'ok', '0.6.0', True, True, True))
        try:
            numpy = bool(__import__('importlib.metadata').metadata.version('numpy'))
        except Exception:  # noqa: BLE001
            numpy = False
        self.assertEqual((p['requires'], p['otherRequires']), ({'numpy': numpy}, 0))
        self.assertTrue((self.tmp / 'rose-validate' / 'site' / 'ptts' / '__init__.py').exists())  # tmp only
        self.assertGreater(r['tmpUsedBytes'], 0)
        q = r['qwen']
        self.assertEqual((q['run'], q['transformers'], q['config'], q['modelType'], q['architecture'], q['tokenizer'],
                          q['tokenizerFast'], q['chatTemplate'], q['processor'], q['processorClass']),
                         ('ok', '4.99.0', 'ok', True, True, 'ok', True, True, 'ok', 'Qwen3VLProcessor'))
        calls = self.calls()
        self.assertEqual(len(calls), 3)
        for c in calls:  # every load: local files only, remote code off, offline env, HOME on the tmpfs
            self.assertEqual((c['kw'], c['offline'], c['tf']),
                             ({'local_files_only': True, 'trust_remote_code': False}, '1', '1'))
            self.assertTrue(c['home'].startswith(str(self.tmp)))
        self.assertEqual(sorted(os.listdir(self.staging / 'qwen-fixture')),
                         ['chat_template.jinja', 'config.json', 'preprocessor_config.json', 'tokenizer.json',
                          'tokenizer_config.json'])  # nothing written beside the staged inputs
        pip = argvs[0][0]
        self.assertEqual(pip[1:3 + len(V.PIP_INSTALL)], ['-I', '-B', *V.PIP_INSTALL])
        self.assertEqual(pip[-3:], ['--target', str(self.tmp / 'rose-validate' / 'site'),
                                    str(self.staging / 'wheels' / WHEEL)])
        self.assertEqual(V.PIP_INSTALL, ('-m', 'pip', 'install', '--isolated', '--no-index', '--no-deps',
                                         '--no-cache-dir', '--disable-pip-version-check', '--no-compile',
                                         '--no-warn-script-location'))
        self.assertEqual(len(argvs), 3)  # pip, the ptts child, the Qwen child: nothing else is run
        for argv, env in argvs:
            self.assertEqual(argv[1:3], ['-I', '-B'])
            self.assertTrue(all(env[k].startswith(str(self.tmp)) for k in ('HOME', 'TMPDIR', 'HF_HOME',
                                                                            'XDG_CACHE_HOME')))
            self.assertEqual((env['HF_HUB_OFFLINE'], env['TRANSFORMERS_OFFLINE']), ('1', '1'))
            self.assertEqual(set(env), {'PATH', 'HOME', 'TMPDIR', 'HF_HOME', 'XDG_CACHE_HOME', 'HF_HUB_OFFLINE',
                                        'TRANSFORMERS_OFFLINE', 'HF_DATASETS_OFFLINE', 'HF_HUB_DISABLE_TELEMETRY',
                                        'TOKENIZERS_PARALLELISM', 'OMP_NUM_THREADS', 'PIP_NO_INPUT',
                                        'PIP_DISABLE_PIP_VERSION_CHECK', 'LC_ALL'})  # nothing inherited: no
            # credentials, proxies or tokens from the parent's environment
        line = json.dumps(V.sanitize(r))
        for leak in (str(self.dir), 'SECRET', '/tmp', 'site-packages'):
            self.assertNotIn(leak, line)

    def test_dependency_failures_are_fixed_categories(self):
        cases = (
            ('missing_native', 'import ptts_native_ext\n', ('failed', 'ModuleNotFoundError', 'ptts_native_ext')),
            ('crash', 'import os, signal\nos.kill(os.getpid(), signal.SIGABRT)\n', ('crashed', None, None)),
            ('no_f32', PTTS_OK.replace("'f32', ", ""), ('ok', None, None)),
            ('noisy', 'import sys\nsys.stdout.write("x" * (4 << 20) + "\\n")\n' + PTTS_OK, ('ok', None, None)),
        )
        for name, init, (status, error, module) in cases:
            with self.subTest(name):
                shutil.rmtree(self.tmp / 'rose-validate', True)
                self.put_wheel(wheel_bytes(init, requires=('numpy', 'evil-dep>=1; python_version>"3"',
                                                           'docs-only; extra == "docs"')))
                p = self.run_validate()['ptts']
                self.assertEqual((p['install'], p['import'], p['error'], p['module']), ('ok', status, error, module))
                self.assertEqual(p['otherRequires'], 1)  # evil-dep is named nowhere; --no-deps never fetched it
                if name == 'no_f32':
                    self.assertEqual((p['stream'], p['f32']), (True, False))
        self.assertFalse((self.tmp / 'rose-validate' / 'site' / 'evil_dep').exists())

    def test_wheel_that_pip_refuses_and_missing_pip(self):
        self.put_wheel(b'not a zip file')
        r = self.run_validate()
        self.assertEqual((r['stop'], r['ptts']['install'], r['ptts']['import']), ('done', 'failed', 'skipped'))
        real = __import__('importlib.util').util.find_spec
        import importlib.util as iu
        iu.find_spec = lambda name, *a: None if name == 'pip' else real(name, *a)
        self.addCleanup(setattr, iu, 'find_spec', real)
        self.put_wheel(wheel_bytes())
        r = self.run_validate()
        self.assertEqual((r['ptts']['install'], r['ptts']['import']), ('pip_unavailable', 'skipped'))
        self.assertEqual(r['qwen']['run'], 'ok')  # the Qwen check still runs

    def test_qwen_failures_and_privacy(self):
        self.mode({'config': 'unknown', 'processor': 'missing'})
        q = self.run_validate()['qwen']
        self.assertEqual((q['config'], q['errors']['config'], q['tokenizer'], q['processor'], q['errors']['processor'],
                          q['modules']['processor']),
                         ('failed', 'ValueError', 'ok', 'failed', 'ModuleNotFoundError', 'torchvision'))
        self.assertNotIn('SECRET', json.dumps(V.sanitize({'qwen': q})))
        shutil.rmtree(self.extra / 'transformers')  # transformers not installed at all
        q = self.run_validate()['qwen']
        self.assertEqual((q['run'], q['transformers'], q['config'], q['errors']['config'], q['modules']['config']),
                         ('ok', None, 'failed', 'ModuleNotFoundError', 'transformers'))

    def test_deadline_bounds_a_stuck_child(self):
        self.mode({'sleep': 30})
        t = __import__('time').monotonic()
        r = self.run_validate(deadlineS=8)
        self.assertLess(__import__('time').monotonic() - t, 15)
        self.assertEqual(r['qwen']['run'], 'timeout')
        self.assertEqual(r['stop'], 'deadline')

    def test_script_marker_line_end_to_end(self):
        import subprocess
        # The pin is a module constant; a fixture wheel cannot match it, so the script run proves the refusal path.
        out = subprocess.run([sys.executable, '-I', '-c', SOURCE, json.dumps(self.manifest), json.dumps(self.limits)],
                             capture_output=True, text=True, timeout=120).stdout
        line = out.strip().splitlines()[-1]
        self.assertTrue(line.startswith(V.MARKER))
        raw = json.loads(line[len(V.MARKER):])
        self.assertEqual((raw['stop'], raw['inputs']['wheelSha256']), ('bad_inputs', False))
        self.assertIsNone(raw['ptts'])
        self.assertFalse((self.tmp / 'rose-validate').exists())  # refused before any install


class Refusals(Fixture):
    """Isolation and input refusals happen before anything is installed, imported or loaded."""

    def assertNothingRan(self, r):
        self.assertIsNone(r['ptts'])
        self.assertIsNone(r['qwen'])
        self.assertFalse((self.tmp / 'rose-validate').exists())
        self.assertEqual(self.calls(), [])

    def test_isolation_refusals(self):
        cases = {
            'tmp_too_big': (dict(), dict(tmpMaxBytes=1 << 20), 'tmpBounded'),
            'tmp_noexec': (dict(tmp_opts='rw,noexec,nosuid,size=524288k'), {}, 'tmpExec'),
            'tmp_not_tmpfs': (dict(tmp_type='ext4'), {}, 'tmpfs'),
            'root_rw': (dict(root='rw'), {}, 'rootReadOnly'),
            'staging_rw': (dict(staging='rw'), {}, 'stagingReadOnly'),
        }
        for name, (mounts, limits, key) in cases.items():
            with self.subTest(name):
                self.write_mounts(**mounts)
                r = self.run_validate(**limits)
                self.assertEqual(r['stop'], 'not_isolated')
                self.assertFalse(r['isolation'][key])
                self.assertNothingRan(r)
        self.write_mounts()
        (self.dir / 'net' / 'eth0').mkdir()
        r = self.run_validate()
        self.assertEqual((r['stop'], r['isolation']['networkOnlyLo']), ('not_isolated', False))
        (self.dir / 'net' / 'eth0').rmdir()
        (self.dir / 'dev' / 'nvidia0').write_text('')
        r = self.run_validate()
        self.assertEqual((r['stop'], r['isolation']['gpuDevices']), ('not_isolated', True))
        self.assertNothingRan(r)
        r = self.run_validate(mountsFile=str(self.dir / 'absent'))
        self.assertEqual(r['stop'], 'not_isolated')  # unreadable facts never count as isolated

    def test_input_refusals(self):
        self.patch_pin('0' * 64)  # the manifest's wheel no longer equals the pinned digest
        r = self.run_validate()
        self.assertEqual((r['stop'], r['inputs']['wheelSha256']), ('bad_inputs', False))
        self.assertNothingRan(r)
        self.patch_pin(self.manifest['groups']['ptts']['files'][0][3])
        (self.staging / 'qwen-fixture' / 'tokenizer.json').write_bytes(b'{"tampered": true}')
        r = self.run_validate()
        self.assertEqual((r['stop'], r['inputs']['qwenFiles']['mismatch']), ('bad_inputs', 1))
        (self.staging / 'qwen-fixture' / 'config.json').unlink()
        self.assertEqual(self.run_validate()['inputs']['qwenFiles']['missing'], 1)
        self.assertNothingRan(r)
        bad = json.loads(json.dumps(self.manifest))
        bad['groups']['qwen']['files'][0][0] = '../escape'
        self.assertEqual(V.validate(bad, self.limits)['stop'], 'bad_manifest')

    def test_sanitize_is_an_allowlist_and_bounded(self):
        hostile = V.sanitize({'stop': 'done; rm -rf /', 'elapsedS': -1, 'tmpUsedBytes': 'SECRET',
                              'isolation': {'ok': 'yes', 'tmpBytes': 1 << 60},
                              'inputs': {'wheelSha256': 1, 'qwenFiles': {'expected': True}},
                              'ptts': {'install': 'ok; SECRET', 'version': '/home/SECRET', 'error': 'Error: SECRET x',
                                       'module': 'mod/../SECRET', 'requires': {'numpy': 'SECRET', 'evil': True},
                                       'f32': 'true'},
                              'qwen': {'run': 'ok', 'processorClass': 'A B', 'errors': {'config': 'x' * 70},
                                       'modules': {'config': 'torch vision'}, 'transformers': 'SECRET!'},
                              'extra': 'SECRET'})
        text = json.dumps(hostile)
        self.assertNotIn('SECRET', text)
        self.assertNotIn('evil', text)
        self.assertEqual((hostile['stop'], hostile['isolation']['ok'], hostile['isolation']['tmpBytes'],
                          hostile['ptts']['install'], hostile['ptts']['error'], hostile['ptts']['module'],
                          hostile['qwen']['processorClass'], hostile['qwen']['errors']['config'],
                          hostile['qwen']['transformers']),
                         (None, None, None, None, None, None, None, None, 'other'))
        self.assertIsNone(V.sanitize('SECRET'))
        worst = V.sanitize({'stop': 'done', 'elapsedS': 1 << 40, 'tmpUsedBytes': 1 << 40,
                            'isolation': dict.fromkeys(('ok', 'tmpfs', 'tmpBounded', 'tmpExec', 'rootReadOnly',
                                                        'stagingReadOnly', 'networkOnlyLo', 'gpuDevices'), True)
                            | {'tmpBytes': 1 << 40},
                            'inputs': {'wheelSha256': True, 'qwenFiles': dict.fromkeys(
                                ('expected', 'verified', 'missing', 'mismatch'), 1 << 40)},
                            'ptts': {'install': 'pip_unavailable', 'import': 'pip_unavailable', 'version': '9' * 32,
                                     'stream': True, 'addVoice': True, 'f32': True, 'requires': {'numpy': True},
                                     'otherRequires': 1 << 40, 'siteBytes': 1 << 40, 'error': 'E' * 64,
                                     'module': 'm' * 64},
                            'qwen': {'run': 'ok', 'transformers': '9' * 32, 'config': 'ok', 'tokenizer': 'ok',
                                     'processor': 'ok', 'modelType': True, 'architecture': True,
                                     'tokenizerFast': True, 'chatTemplate': True, 'processorClass': 'P' * 64,
                                     'errors': dict.fromkeys(('config', 'tokenizer', 'processor'), 'E' * 64),
                                     'modules': dict.fromkeys(('config', 'tokenizer', 'processor'), 'm' * 64)}})
        self.assertLess(len(json.dumps(worst, separators=(',', ':'))), 3000)  # room in the 4 KiB receipt

    def test_pins_and_real_manifest(self):
        rel, size, kind, digest = MANIFEST['groups']['ptts']['files'][0]
        self.assertEqual((rel, kind, digest), (WHEEL, 'sha256', PIN))
        self.assertEqual(PIN, '6efb197e73af258e278e90fcc771f36022c5123e9ecca2fca50ae90472caa60c')
        names = [f[0] for f in MANIFEST['groups']['qwen']['files']
                 if not f[0].endswith('.safetensors') and f[0] != 'model.safetensors.index.json']
        self.assertEqual(len(names), 11)  # what validate hashes: configs, tokenizer, template, licence
        self.assertLess(sum(f[1] for f in MANIFEST['groups']['qwen']['files'] if f[0] in names), 24 << 20)
        self.assertEqual(MANIFEST['groups']['ptts']['requiresDist'], list(V.KNOWN_REQUIRES))

    def test_stdlib_only_at_module_level(self):
        import ast
        top = [n for n in ast.parse(SOURCE).body if isinstance(n, (ast.Import, ast.ImportFrom))]
        self.assertEqual({a.name for n in top for a in n.names},
                         {'hashlib', 'json', 'os', 're', 'subprocess', 'sys', 'time'})
        for child in (V.PTTS_CHILD, V.QWEN_CHILD):
            compile(child, 'child', 'exec')
        self.assertIn('local_files_only=True, trust_remote_code=False', V.QWEN_CHILD)
        self.assertNotIn('AutoModel', V.QWEN_CHILD)  # no model class, no weights
        self.assertNotIn('TTS(', V.PTTS_CHILD)  # no engine is constructed, so no model loads


if __name__ == '__main__':
    unittest.main()
