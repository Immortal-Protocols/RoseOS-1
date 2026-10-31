"""MODEL-START-DIAG-001: the real model start chain, from the built payload to a served model, with synthetic fixtures.

The bootstrap payload is built and installed by stage_service exactly as on the VM (modes, paths, units). Then the
installed muse-glimmer.service is run the way systemd runs it: ExecStartPre, ExecStart with a clean environment, the
exit status, ExecStopPost. start-model, verify_model.py and the launch scripts run unmodified except that these roots
are relocated into a temporary directory: /etc/rose-models, /mnt/models-data, /run/rose-models, and the vLLM port.
`docker` and `mountpoint` are stand-ins on PATH. The stand-in container runs a stand-in vLLM with the exact argv the
launcher passed, so the loopback address and served-model name are the ones health.py probes. No GPU, image, model,
network or systemd is used."""
import base64
import gzip
import io
import json
import os
from pathlib import Path
import shlex
import shutil
import socket
import stat
import subprocess
import sys
import tarfile
import tempfile
import textwrap
import time
import types
import unittest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / 'service'))
sys.path.insert(0, str(HERE))
import bootstrap as B  # noqa: E402
import build_bootstrap as BB  # noqa: E402
import health as H  # noqa: E402
import render_service  # noqa: E402
import test_service_ops  # noqa: E402
import verify_model as V  # noqa: E402

CONFIG = json.loads((HERE.parent / 'muse-glimmer-30b.json').read_text())
MODEL = CONFIG['model']
IMAGE_ID = render_service.render_local.render_upload.profile(CONFIG, render_service.PROFILE)['runtime']['image_id']
SYSTEMD_PATH = '/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin'

FAKE_DOCKER = r'''#!/usr/bin/env python3
"""docker stand-in: records argv; `run` checks the launcher's container argv, then plays the container."""
import json, os, sys
argv = sys.argv[1:]
with open(os.environ['FAKE_LOG'], 'a') as log:
    log.write(json.dumps(argv) + '\n')
if argv[:2] == ['rm', '-f']:
    sys.exit(0)
if argv[:2] == ['image', 'inspect']:
    if os.environ.get('FAKE_IMAGE') == 'present':
        print(argv[-1]); sys.exit(0)
    print('Error: No such image', file=sys.stderr); sys.exit(1)
if argv[0] == 'run':
    sep = argv.index('--')
    head, command = argv[:sep], argv[sep + 1:]
    mode = os.environ.get('FAKE_CONTAINER', 'serve')
    if mode == 'guard_refused':
        sys.exit(3)
    if mode == 'crash':
        sys.exit(1)
    os.execv(sys.executable, [sys.executable, os.environ['FAKE_VLLM'], *command])
sys.exit(2)
'''

FAKE_VLLM = r'''#!/usr/bin/env python3
"""vllm stand-in: `vllm serve <repo> ... --served-model-name N --host H --port P` serves N on H:P."""
import json, sys
from http.server import BaseHTTPRequestHandler, HTTPServer
args = sys.argv[1:]
assert args[:2] == ['vllm', 'serve'], args
opt = lambda name: args[args.index(name) + 1]
name, host, port = opt('--served-model-name'), opt('--host'), int(opt('--port'))
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def send(self, body):
        data = json.dumps(body).encode()
        self.send_response(200); self.send_header('Content-Length', str(len(data))); self.end_headers(); self.wfile.write(data)
    def do_GET(self):
        self.send({'data': [{'id': name}]})
    def do_POST(self):
        self.rfile.read(int(self.headers['Content-Length']))
        self.send({'model': name, 'choices': [{'message': {'content': 'ok'}}], 'usage': {'completion_tokens': 2}})
HTTPServer((host, port), Handler).serve_forever()
'''

FAKE_MOUNTPOINT = '#!/bin/sh\n[ "$FAKE_MOUNTED" = 1 ]\n'


def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


class Installed:
    """The payload built and installed by stage_service under a temporary prefix."""

    def __init__(self, test):
        self.dir = Path(tempfile.mkdtemp(prefix='rose-start-chain-'))
        test.addCleanup(shutil.rmtree, self.dir, True)
        build = self.dir / 'build'
        BB.build(build, source='test')
        raw = gzip.decompress(base64.b64decode((build / 'rose-models-payload.b64').read_bytes()))
        self.payload = self.dir / 'payload'
        with tarfile.open(fileobj=io.BytesIO(raw)) as tar:
            tar.extractall(self.payload, filter='data')
        self.prefix = self.dir / 'vm'
        host = types.SimpleNamespace(path=lambda p: self.prefix / p.lstrip('/'), run=lambda argv, timeout=0, env=None: (0, ''))
        B.stage_service(host, {'payload': self.payload, 'results': {}})
        self.root = self.prefix / 'etc/rose-models'

    def unit(self, name):
        text = (self.prefix / 'etc/systemd/system' / name).read_text()
        return {k: v for k, v in (line.split('=', 1) for line in text.splitlines() if '=' in line and not line.startswith('#'))}


class InstalledTree(unittest.TestCase):
    def test_modes_paths_and_units_as_installed(self):
        vm = Installed(self)
        for name in ('start-model', 'launch-offline', 'launch-online', 'tls-deploy'):
            mode = stat.S_IMODE((vm.root / 'bin' / name).stat().st_mode)
            self.assertEqual(mode, 0o755, name)
        for lib in ('lib/service/verify_model.py', 'lib/service/health.py', 'lib/service/unit_facts.py',
                    'lib/muse-glimmer-30b.json'):
            self.assertEqual(stat.S_IMODE((vm.root / lib).stat().st_mode), 0o644, lib)
        unit = vm.unit('muse-glimmer.service')
        self.assertEqual(unit['ExecStart'], '/etc/rose-models/bin/start-model')
        self.assertEqual((unit['ExecStartPre'], unit['ExecStopPost']), ('-/usr/bin/docker rm -f muse-glimmer',) * 2)
        self.assertEqual((unit['Type'], unit['Restart'], unit['RestartPreventExitStatus']), ('exec', 'on-failure', '4'))
        self.assertEqual((unit['StartLimitIntervalSec'], unit['StartLimitBurst'], unit['RequiresMountsFor']),
                         ('3600', '5', '/mnt/models-data'))
        start = (vm.root / 'bin/start-model').read_text()
        for path in ('/etc/rose-models/lib/service/verify_model.py', '/etc/rose-models/bin/launch-offline',
                     '/etc/rose-models/bin/launch-online'):
            self.assertIn(path, start)
            self.assertTrue((vm.prefix / path.lstrip('/')).exists(), path)
        # verify_model finds its pinned config next to it, as installed
        self.assertEqual(Path(V.__file__).resolve().parents[1].name, 'serving')
        self.assertTrue((vm.root / 'lib/muse-glimmer-30b.json').exists())

    def test_health_probes_the_address_and_name_the_launcher_serves(self):
        vm = Installed(self)
        health = vm.unit('models-health.service')['ExecStart']
        self.assertNotIn('--base', health)
        self.assertNotIn('--model', health)
        src = (HERE.parent / 'service/health.py').read_text()  # the CLI defaults health.py runs with
        for k, v in {'base': 'http://127.0.0.1:8000', 'model': 'muse-glimmer'}.items():
            self.assertIn(f"p.add_argument('--{k}', default='{v}')", src)
        for script in ('launch-offline', 'launch-online'):
            argv = shlex.split((vm.root / 'bin' / script).read_text().rsplit('exec docker run ', 1)[1])
            opt = lambda name: argv[argv.index(name) + 1]  # noqa: E731
            self.assertEqual((opt('--host'), opt('--port'), opt('--served-model-name'), opt('--network')),
                             ('127.0.0.1', '8000', 'muse-glimmer', 'host'), script)
            self.assertIn(IMAGE_ID, argv)


class StartChain(unittest.TestCase):
    """muse-glimmer.service as systemd runs it, through each boundary."""

    def setUp(self):
        self.vm = Installed(self)
        self.port = free_port()
        self.data = self.vm.prefix / 'mnt/models-data'
        self.run_dir = self.vm.prefix / 'run/rose-models'
        swaps = (('/etc/rose-models', str(self.vm.root)), ('/mnt/models-data', str(self.data)),
                 ('/run/rose-models', str(self.run_dir)), ('--port 8000', f'--port {self.port}'))
        for name in ('start-model', 'launch-offline', 'launch-online'):
            path = self.vm.root / 'bin' / name
            text = path.read_text()
            for old, new in swaps:
                text = text.replace(old, new)
            path.write_text(text)  # the installed mode is kept
        self.fake = self.vm.dir / 'fakebin'
        self.fake.mkdir()
        for name, text in (('docker', FAKE_DOCKER), ('mountpoint', FAKE_MOUNTPOINT)):
            (self.fake / name).write_text(text)
            os.chmod(self.fake / name, 0o755)
        (self.vm.dir / 'vllm.py').write_text(FAKE_VLLM)
        self.log = self.vm.dir / 'docker.log'
        self.cache = self.data / 'hf'
        self.receipt = self.data / 'receipt.json'
        test_service_ops.fake_cache(self.cache, MODEL)
        V.record(self.cache, {**MODEL, 'weights_fetch': {'files': {}}}, self.receipt)
        self.env = {'PATH': f'{self.fake}:{SYSTEMD_PATH}', 'LANG': 'C.UTF-8', 'FAKE_LOG': str(self.log),
                    'FAKE_VLLM': str(self.vm.dir / 'vllm.py'), 'FAKE_IMAGE': 'present', 'FAKE_MOUNTED': '1'}

    def systemd(self, **env):
        """ExecStartPre (failure ignored), ExecStart; returns (process, unit)."""
        unit = self.vm.unit('muse-glimmer.service')
        pre = shlex.split(unit['ExecStartPre'].lstrip('-'))
        self.assertEqual(pre[0], '/usr/bin/docker')
        subprocess.run([str(self.fake / 'docker'), *pre[1:]], env={**self.env, **env}, timeout=30)
        start = shlex.split(unit['ExecStart'].replace('/etc/rose-models', str(self.vm.root)))
        proc = subprocess.Popen(start, env={**self.env, **env}, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(lambda: proc.poll() is None and (proc.kill(), proc.wait()))
        return proc

    def docker_runs(self):
        lines = self.log.read_text().splitlines() if self.log.exists() else []
        return [json.loads(line) for line in lines if json.loads(line)[0] == 'run']

    def phase(self):
        return json.loads((self.run_dir / 'start-phase.json').read_text())

    def wait_ready(self, proc):
        deadline, result = time.time() + 20, None
        while time.time() < deadline:
            result = H.probe(f'http://127.0.0.1:{self.port}', 'muse-glimmer')
            if result[0] or proc.poll() is not None:
                break
            time.sleep(0.1)
        return result

    def test_verified_storage_launches_offline_and_serves_the_probed_name(self):
        proc = self.systemd()
        self.assertEqual(self.wait_ready(proc), (True, 'ready'))
        run, = self.docker_runs()
        self.assertIn('HF_HUB_OFFLINE=1', run)
        self.assertEqual({k: v for k, v in self.phase().items() if k != 'at'},
                         {'phase': 'launching_offline', 'verifyExit': 0, 'dataMounted': True, 'imagePresent': True})
        self.assertEqual(json.loads((self.run_dir / 'verify-start.json').read_text())['result'], 'verified')
        proc.terminate()
        proc.wait(10)

    def test_no_receipt_launches_online(self):
        self.receipt.unlink()
        proc = self.systemd()
        self.assertEqual(self.wait_ready(proc), (True, 'ready'))
        run, = self.docker_runs()
        self.assertNotIn('HF_HUB_OFFLINE=1', run)
        self.assertEqual((self.phase()['phase'], self.phase()['verifyExit']), ('launching_online', 3))

    def test_refusals_and_failures_reach_systemd_with_their_exit_status(self):
        cases = (
            ('storage mismatch', lambda: (self.cache / 'hub').rename(self.vm.dir / 'moved'), {}, 4,
             ('refused_storage', 4, None, None), 0),
            ('data disk not mounted', None, {'FAKE_MOUNTED': '0'}, 4, ('launching_offline', 0, False, True), 0),
            ('image missing', None, {'FAKE_IMAGE': 'absent'}, 4, ('launching_offline', 0, True, False), 0),
            ('startup guard refused', None, {'FAKE_CONTAINER': 'guard_refused'}, 3, ('launching_offline', 0, True, True), 1),
            ('container crashed', None, {'FAKE_CONTAINER': 'crash'}, 1, ('launching_offline', 0, True, True), 1))
        for label, setup, env, code, phase, runs in cases:
            with self.subTest(label):
                self.log.unlink(missing_ok=True)
                if setup:
                    setup()
                proc = self.systemd(**env)
                self.assertEqual(proc.wait(30), code)
                p = self.phase()
                self.assertEqual((p['phase'], p['verifyExit'], p['dataMounted'], p['imagePresent']), phase)
                self.assertEqual(len(self.docker_runs()), runs)
                if label == 'storage mismatch':
                    (self.vm.dir / 'moved').rename(self.cache / 'hub')
        unit = self.vm.unit('muse-glimmer.service')  # 4 is not restarted; 3 and 1 are, within the start limit
        self.assertEqual(unit['RestartPreventExitStatus'], '4')

    def test_phase_recording_failure_never_changes_the_outcome(self):
        self.run_dir.parent.mkdir(parents=True, exist_ok=True)
        self.run_dir.write_text('not a directory')
        proc = self.systemd()
        self.assertEqual(self.wait_ready(proc), (True, 'ready'))
        self.assertEqual(len(self.docker_runs()), 1)


if __name__ == '__main__':
    unittest.main()
