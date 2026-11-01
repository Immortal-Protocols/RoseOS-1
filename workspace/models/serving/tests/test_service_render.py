"""Rendered persistent-service tree (MODELS-ALWAYSON-SERVICE-001): units, launch scripts and the start-model decision,
executed against a fake cache and stub launchers. No systemd, Docker, GPU or network."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / 'service'))
sys.path.insert(0, str(HERE))
import render_service as R  # noqa: E402
import test_service_ops as ops  # noqa: E402

CONFIG = R.capacity.load(R.capacity.CONFIG)


class Rendered(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='svc-render-'))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_tree_manifest_and_supervision_policy(self):
        m = R.render(self.tmp / 'out')
        for rel, digest in m['files'].items():
            data = (self.tmp / 'out' / rel).read_bytes()
            self.assertEqual(R.hashlib.sha256(data).hexdigest(), digest, rel)
            self.assertEqual(bool((self.tmp / 'out' / rel).stat().st_mode & 0o111), rel.startswith('bin/'), rel)
        self.assertEqual(m['model'], 'meta-models/Muse-Glimmer-30B@cec94fcfd6512f2cfe708138c32533b346ea4f69')
        self.assertEqual(m['image'], 'sha256:dbb04f5c0c0a6356779cf3175381a0406584df01b0ad2f88eaadb46f7f2da08c')
        unit = (self.tmp / 'out/systemd/muse-glimmer.service').read_text()
        for line in ('Restart=on-failure', 'RestartSec=30', 'StartLimitBurst=5', 'StartLimitIntervalSec=3600',
                     'RestartPreventExitStatus=4', 'RequiresMountsFor=/mnt/models-data'):
            self.assertIn(line + '\n', unit)
        for name in ('models-auth.service', 'models-edge.service'):
            self.assertIn('StartLimitBurst=10\n', (self.tmp / 'out/systemd' / name).read_text())
        self.assertIn('OnUnitInactiveSec=60s', (self.tmp / 'out/systemd/models-health.timer').read_text())
        self.assertIn('check --full', (self.tmp / 'out/systemd/models-verify-full.service').read_text())
        conf = (self.tmp / 'out/edge/nginx.conf').read_text()
        self.assertIn('auth_request /_validate;', conf)
        self.assertIn('/etc/rose-models/tls/current/fullchain.pem', conf)
        self.assertNotIn('token.map', conf)

    def test_cloud_preflight_helper_is_packaged_but_inert(self):
        m = R.render(self.tmp / 'out')
        src = HERE.parents[1] / 'rose_cloud' / 'rose_cloud'
        for name in ('preflight.py', 'manifest.json', 'stage.py', 'validate.py', 'qwen_gpu.py', 'vllm_brain.py',
                     'coop.py', 'pump.py', 'adapter_session.py'):
            rel = f'lib/rose_cloud/{name}'
            self.assertIn(rel, m['files'])  # in the tree manifest, so in the roots-inclusive payload digest
            self.assertEqual((self.tmp / 'out' / rel).read_bytes(), (src / name).read_bytes())
            self.assertFalse((self.tmp / 'out' / rel).stat().st_mode & 0o111)
        units = ''.join(p.read_text() for p in (self.tmp / 'out/systemd').iterdir())
        self.assertNotIn('rose_cloud', units)  # nothing runs it except bootstrap's explicit diagnostic mode

    def test_launch_scripts_are_the_pinned_tp2_loopback_profile(self):
        R.render(self.tmp / 'out')
        for mode in ('online', 'offline'):
            script = (self.tmp / f'out/bin/launch-{mode}').read_text()
            for needle in ('--tensor-parallel-size 2', '--host 127.0.0.1 --port 8000', '--dtype bfloat16',
                           '--max-model-len 8192', '--max-num-seqs 1', '--revision cec94fcfd6512f2cfe708138c32533b346ea4f69',
                           'sha256:e90b8320f680a6a7b8daff87ad08cdf063a68d869880e662fd6d2cebfef689dc',
                           '-v /mnt/models-data/hf:/root/.cache/huggingface'):
                self.assertIn(needle, script, mode)
            self.assertEqual('HF_HUB_OFFLINE=1' in script, mode == 'offline')
            self.assertNotIn('docker pull', script)
            self.assertNotIn('quant', script)

    def test_start_model_picks_offline_online_or_refuses(self):
        root, data = self.tmp / 'root', self.tmp / 'data'
        for rel, text in R.files(CONFIG, str(root), str(data)).items():
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / rel).write_text(text)
        model = dict(CONFIG['model'], weights_fetch={'files': {'model-00001-of-00002.safetensors': 11, 'tokenizer.json': 7}})
        (root / 'lib/muse-glimmer-30b.json').write_text(json.dumps(dict(CONFIG, model=model)))
        for mode in ('online', 'offline'):
            (root / f'bin/launch-{mode}').write_text(f'#!/bin/sh\necho launched-{mode}\n')
        for p in (root / 'bin').iterdir():
            os.chmod(p, 0o755)

        def start():
            r = subprocess.run([str(root / 'bin/start-model')], capture_output=True, text=True, timeout=60)
            return r.returncode, r.stdout.strip().splitlines()[-1] if r.stdout.strip() else r.stderr.strip()
        self.assertEqual(start(), (0, 'launched-online'))  # first start: no receipt yet
        snap = ops.fake_cache(data / 'hf', model)
        subprocess.run([sys.executable, str(root / 'lib/service/verify_model.py'), 'record', '--cache', str(data / 'hf'),
                        '--receipt', str(data / 'receipt.json')], check=True, capture_output=True)
        self.assertEqual(start(), (0, 'launched-offline'))
        (snap / 'tokenizer.json').unlink()
        code, _ = start()
        self.assertEqual(code, 4)  # refused, and RestartPreventExitStatus=4 stops the restart loop


class QwenSelection(unittest.TestCase):
    """MODELS-QWEN-SERVE-001: Muse stays the default; Qwen only on an explicit selection, as an overlay of the same tree."""

    QWEN = json.loads((HERE.parent / 'qwen-fp8.json').read_text())

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='svc-qwen-'))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def tree(self, name, **kw):
        R.render(self.tmp / name, **kw)
        return {p.relative_to(self.tmp / name).as_posix(): p.read_bytes()
                for p in sorted((self.tmp / name).rglob('*')) if p.is_file()}

    def test_default_is_muse_byte_for_byte(self):
        default, muse = self.tree('a'), self.tree('b', model='muse')
        self.assertEqual(default, muse)
        for absent in ('bin/launch-qwen', 'lib/qwen-fp8.json', 'lib/service/verify_staged.py'):
            self.assertNotIn(absent, default)  # (lib/rose_cloud/qwen_gpu.py is the existing diagnostic helper)
        self.assertEqual(json.loads(default['manifest.json'])['model'],
                         'meta-models/Muse-Glimmer-30B@cec94fcfd6512f2cfe708138c32533b346ea4f69')
        with self.assertRaises(R.render_local.render_upload.Rejected):
            R.render(self.tmp / 'c', model='llama')

    def test_qwen_overlay_changes_only_the_model_slot(self):
        muse, qwen = self.tree('m'), self.tree('q', model='qwen')
        changed = sorted(k for k in set(muse) | set(qwen) if muse.get(k) != qwen.get(k))
        self.assertEqual(changed, ['bin/launch-qwen', 'bin/start-model', 'lib/qwen-fp8.json',
                                   'lib/service/verify_staged.py', 'manifest.json', 'systemd/models-health.service',
                                   'systemd/models-verify-full.service', 'systemd/muse-glimmer.service'])
        for kept in ('bin/launch-offline', 'bin/launch-online', 'lib/muse-glimmer-30b.json', 'edge/nginx.conf',
                     'systemd/models-edge.service', 'systemd/models-auth.service'):
            self.assertEqual(muse[kept], qwen[kept], kept)  # diagnostics, edge, auth and TLS unchanged
        unit = qwen['systemd/muse-glimmer.service'].decode()
        self.assertIn('Description=Qwen Qwen/Qwen3.6-35B-A3B-FP8 as rose-qwen', unit)
        self.assertEqual(unit.replace(unit.split('\n')[2], ''), muse['systemd/muse-glimmer.service'].decode().replace(
            muse['systemd/muse-glimmer.service'].decode().split('\n')[2], ''))  # only the description differs
        self.assertIn('health.py run --model rose-qwen\n', qwen['systemd/models-health.service'].decode())
        self.assertNotIn('--record', qwen['systemd/models-health.service'].decode())
        start = qwen['bin/start-model'].decode()
        self.assertIn('verify_staged.py --manifest /etc/rose-models/lib/rose_cloud/manifest.json --manifest-sha256 '
                      + self.QWEN['model']['manifest_sha256'] + ' --group qwen --staging-root /mnt/models-data/rose-cloud',
                      start)
        self.assertNotIn('launch-online', start)  # no download branch
        self.assertIn('--full', qwen['systemd/models-verify-full.service'].decode())
        m = json.loads(qwen['manifest.json'])
        self.assertEqual((m['profile'], m['servedModel'], m['qwenSettings'], m['notRuntimeProven'], m['model']),
                         (self.QWEN['id'], 'rose-qwen', 'proven', [],
                          'Qwen/Qwen3.6-35B-A3B-FP8@95a723d08a9490559dae23d0cff1d9466213d989'))

    def test_pins_match_the_staged_manifest_and_the_runtime_proven_flags(self):
        import hashlib
        rose = HERE.parents[1] / 'rose_cloud' / 'rose_cloud'
        manifest_bytes = (rose / 'manifest.json').read_bytes()
        manifest = json.loads(manifest_bytes)
        model = self.QWEN['model']
        self.assertEqual(hashlib.sha256(manifest_bytes).hexdigest(), model['manifest_sha256'])
        sys.path.insert(0, str(HERE.parent / 'service'))
        import bootstrap
        self.assertEqual(model['manifest_sha256'], bootstrap.STAGE_MANIFEST_SHA256)
        q = manifest['groups'][model['manifest_group']]
        self.assertEqual((q['repo'], q['revision']), (model['repo'], model['revision']))
        self.assertEqual(model['staged_dir'], f"{manifest['stagingRoot']}/{q['dir']}")
        sys.path.insert(0, str(rose.parent))
        from rose_cloud import qwen_gpu
        self.assertEqual(self.QWEN['settings']['proven']['args'], qwen_gpu.SERVER_ARGS)  # PR349's exact serve flags
        runtime = R.render_local.render_upload.profile(CONFIG, R.PROFILE)['runtime']
        script, limits = R.render_local.render_qwen(self.QWEN, runtime, 'proven')
        self.assertIn(runtime['image_id'], script)  # the same cached image: nothing new is pulled
        self.assertNotIn('docker pull', script)
        with self.assertRaises(R.render_local.render_upload.Rejected):
            R.render_local.qwen_args(self.QWEN, 'unproven')
        bad = json.loads(json.dumps(self.QWEN))
        bad['launch']['host'] = '0.0.0.0'
        with self.assertRaises(R.render_local.render_upload.Rejected):
            R.render_local.render_qwen(bad, runtime, 'proven')


class VerifyStaged(unittest.TestCase):
    """verify_staged.py: the staged files against a pinned manifest; quick (sizes, small-file hashes) and --full."""

    def setUp(self):
        import hashlib
        sys.path.insert(0, str(HERE.parent / 'service'))
        import verify_staged
        self.V = verify_staged
        self.tmp = Path(tempfile.mkdtemp(prefix='staged-'))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        d = self.tmp / 'root' / 'qwen'
        d.mkdir(parents=True)
        files = {'config.json': b'{}', 'layers-0.safetensors': b'w' * 1000}
        entries = []
        for name, data in files.items():
            (d / name).write_bytes(data)
            entries.append([name, len(data), 'sha256', hashlib.sha256(data).hexdigest()])
        self.manifest = self.tmp / 'manifest.json'
        self.manifest.write_text(json.dumps({'stagingRoot': '/x', 'groups': {'qwen': {'dir': 'qwen', 'revision': 'r',
                                                                                    'files': entries}}}))
        self.pin = hashlib.sha256(self.manifest.read_bytes()).hexdigest()
        self.dir = d

    def run_cli(self, *extra):
        result = self.tmp / 'result.json'
        code = self.V.main(['--manifest', str(self.manifest), '--manifest-sha256', self.pin, '--group', 'qwen',
                            '--staging-root', str(self.tmp / 'root'), '--result', str(result), *extra])
        return code, json.loads(result.read_text())['result']

    def test_quick_full_and_refusals(self):
        self.assertEqual(self.run_cli(), (0, 'verified'))
        (self.dir / 'layers-0.safetensors').write_bytes(b'x' * 1000)  # same size, different content
        self.assertEqual(self.run_cli(), (0, 'verified'))  # quick: weights by size only
        self.assertEqual(self.run_cli('--full'), (4, 'mismatch'))  # the daily rehash catches it
        (self.dir / 'layers-0.safetensors').write_bytes(b'w' * 1000)
        (self.dir / 'config.json').write_bytes(b'[]')
        self.assertEqual(self.run_cli(), (4, 'mismatch'))  # small files are always hashed
        (self.dir / 'config.json').write_bytes(b'{}')
        self.pin = '0' * 64
        self.assertEqual(self.run_cli(), (4, 'mismatch'))  # not the pinned manifest


if __name__ == '__main__':
    unittest.main()
