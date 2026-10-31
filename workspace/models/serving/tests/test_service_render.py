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
        for name in ('preflight.py', 'manifest.json'):
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


if __name__ == '__main__':
    unittest.main()
