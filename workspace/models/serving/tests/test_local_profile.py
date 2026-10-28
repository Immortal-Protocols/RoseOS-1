"""H100 local profile: one-GPU guard limits, loopback launch script run as a real process, and blank-only scratch inventory."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
import capacity  # noqa: E402
import render_local  # noqa: E402
import render_upload  # noqa: E402
import scratch_inventory  # noqa: E402
import startup_guard  # noqa: E402

CONFIG = capacity.load()
H100 = render_upload.profile(CONFIG, render_local.PROFILE)
A100 = render_upload.profile(CONFIG, 'a100x2-bf16-vllm')
LIMITS = render_upload.guard_limits(CONFIG, H100)
GIB = 2**30
FAKE_DOCKER = '''#!/usr/bin/env bash
if [ "$1" = image ]; then echo "$FAKE_IMAGE_ID"; exit 0; fi
if [ "$1" = run ]; then
  printf '%s\\n' "$@" > "$FAKE_ARGV"
  while [ "$1" != "$IMAGE" ]; do shift; done; shift
  exec "$PYTHON" "$@"
fi
exit 99
'''


def receipt(**over):
    base = {'driver_version': '580.95.05', 'gpus': [{'name': 'synthetic H100', 'total': 85*10**9, 'free': 84*10**9}],
            'cache_free': 700*10**9, 'shm_total': 8*GIB}
    base.update(over)
    return base


class H100GuardTest(unittest.TestCase):
    def test_profile_limits(self):
        self.assertEqual(LIMITS['gpu_count'], 1)
        self.assertEqual(LIMITS['driver_major_min'], 580)
        self.assertGreater(LIMITS['gpu_total_min_bytes'], CONFIG['model']['bf16_parameters']*2)
        self.assertTrue(capacity.fit(CONFIG, H100)['fits'])

    def test_sufficient_80gb_receipt_proceeds(self):
        self.assertEqual(startup_guard.evaluate(receipt(), LIMITS), [])

    def test_shortfalls_refuse(self):
        cases = {
            'total bytes': receipt(gpus=[{'name': 'A100 40GB', 'total': 42*10**9, 'free': 41*10**9}]),
            'GPUs visible': receipt(gpus=receipt()['gpus']*2),
            'driver 575': receipt(driver_version='575.57.08'),
            'cache free': receipt(cache_free=50*10**9),
            '/dev/shm': receipt(shm_total=64*2**20),
        }
        for expected, node in cases.items():
            failures = startup_guard.evaluate(node, LIMITS)
            self.assertTrue(any(expected in f for f in failures), (expected, failures))

    def test_two_a100_profile_unchanged(self):
        limits = render_upload.guard_limits(CONFIG, A100)
        self.assertEqual(limits['gpu_count'], 2)
        self.assertLess(limits['gpu_total_min_bytes'], LIMITS['gpu_total_min_bytes'])
        args = A100['container_spec']['args']
        self.assertEqual(args[args.index('--tensor-parallel-size')+1], '2')
        self.assertEqual(args[args.index('--host')+1], '0.0.0.0')


class LaunchScriptTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.dir = Path(temp.name)
        config = copy.deepcopy(CONFIG)
        launch = render_upload.profile(config, render_local.PROFILE)['local_launch']
        launch['scratch_mount'] = str(self.dir/'scratch')
        launch['host_cache_dir'] = str(self.dir/'scratch'/'hf')
        self.config = self.dir/'config.json'
        self.config.write_text(json.dumps(config))
        bin_dir = self.dir/'bin'
        bin_dir.mkdir()
        (bin_dir/'docker').write_text(FAKE_DOCKER)
        (bin_dir/'docker').chmod(0o755)
        self.bin = bin_dir

    def render(self, *extra):
        out = self.dir/'launch.sh'
        run = subprocess.run([sys.executable, '-I', str(HERE/'render_local.py'), '--out', str(out), '--config', str(self.config), *extra],
            capture_output=True, text=True, timeout=60)
        self.assertEqual(run.returncode, 0, run.stderr)
        return out

    def launch(self, script, mounted=True, image_id=None):
        (self.bin/'mountpoint').write_text(f'#!/usr/bin/env bash\nexit {0 if mounted else 1}\n')
        (self.bin/'mountpoint').chmod(0o755)
        env = {'PATH': f"{self.bin}:{os.environ['PATH']}", 'HOME': str(self.dir), 'PYTHON': sys.executable,
               'IMAGE': H100['runtime']['image_id'], 'FAKE_IMAGE_ID': image_id or H100['runtime']['image_id'],
               'FAKE_ARGV': str(self.dir/'argv')}
        return subprocess.run(['bash', str(script)], capture_output=True, text=True, timeout=60, env=env)

    def test_launch_reaches_guard_with_loopback_tp1_8k(self):
        run = self.launch(self.render())
        self.assertEqual(run.returncode, 3, run.stderr)  # guard refuses on this GPU-less host
        evidence = json.loads(run.stdout.strip().splitlines()[-1])
        self.assertEqual(evidence['limits'], LIMITS)
        argv = (self.dir/'argv').read_text().splitlines()
        self.assertIn('--network', argv)
        self.assertEqual(argv[argv.index('--network')+1], 'host')
        self.assertFalse({'-p', '--publish', '-P'} & set(argv))
        for flag, value in (('--host', '127.0.0.1'), ('--port', '8000'), ('--tensor-parallel-size', '1'),
                            ('--max-model-len', '8192'), ('--max-num-seqs', '1'), ('--served-model-name', 'muse-glimmer'),
                            ('--revision', CONFIG['model']['revision'])):
            self.assertEqual(argv[argv.index(flag)+1], value, flag)
        self.assertNotIn('HF_HUB_OFFLINE=1', argv)

    def test_offline_mode(self):
        self.launch(self.render('--offline'))
        self.assertIn('HF_HUB_OFFLINE=1', (self.dir/'argv').read_text().splitlines())

    def test_preconditions_refuse_before_docker_run(self):
        script = self.render()
        for kwargs in ({'mounted': False}, {'image_id': 'sha256:' + '0'*64}):
            run = self.launch(script, **kwargs)
            self.assertEqual(run.returncode, render_local.EXIT_PRECONDITION, kwargs)
            self.assertIn('refused', run.stderr)
            self.assertFalse((self.dir/'argv').exists())

    def test_rejects_non_loopback_or_off_scratch_cache(self):
        bad_host = copy.deepcopy(CONFIG)
        args = render_upload.profile(bad_host, render_local.PROFILE)['container_spec']['args']
        args[args.index('--host')+1] = '0.0.0.0'
        bad_cache = copy.deepcopy(CONFIG)
        render_upload.profile(bad_cache, render_local.PROFILE)['local_launch']['host_cache_dir'] = '/var/lib/hf'
        for config in (bad_host, bad_cache):
            with self.assertRaises(render_upload.Rejected):
                render_local.render(config)


def disk(name, children=None, **fields):
    node = {'name': name, 'path': f'/dev/{name}', 'type': 'disk', 'size': 402653184000, 'model': 'nvme_card',
            'fstype': None, 'pttype': None, 'mountpoints': [None]}
    node.update(fields)
    if children:
        node['children'] = children
    return node


LSBLK = {'blockdevices': [
    disk('nvme0n1', pttype='gpt', children=[
        {'name': 'nvme0n1p1', 'path': '/dev/nvme0n1p1', 'type': 'part', 'fstype': 'ext4', 'mountpoints': ['/']},
        {'name': 'nvme0n1p15', 'path': '/dev/nvme0n1p15', 'type': 'part', 'fstype': 'vfat', 'mountpoints': ['/boot/efi']}]),
    disk('nvme1n1'),
    disk('nvme2n1', fstype='ext4'),
    disk('nvme3n1'),
    disk('nvme4n1'),
    disk('nvme5n1', mountpoint='/mnt/old', mountpoints=None),
]}
BY_ID = {'google-persistent-disk-0': '/dev/nvme0n1', 'google-local-nvme-ssd-0': '/dev/nvme1n1',
         'google-local-nvme-ssd-1': '/dev/nvme2n1', 'google-local-nvme-ssd-2': '/dev/nvme4n1',
         'google-local-nvme-ssd-3': '/dev/nvme5n1', 'nvme-other': '/dev/nvme3n1'}
SIGNATURES = {'/dev/nvme4n1': [{'type': 'xfs', 'offset': '0x0'}]}


class ScratchInventoryTest(unittest.TestCase):
    def report(self):
        return {d['path']: d for d in scratch_inventory.classify(LSBLK, BY_ID, SIGNATURES)}

    def test_only_blank_provider_local_ssd_is_eligible(self):
        report = self.report()
        self.assertEqual([p for p, d in report.items() if d['eligible']], ['/dev/nvme1n1'])
        self.assertIn('backs a mounted path (boot disk or ancestor)', report['/dev/nvme0n1']['reasons'])
        self.assertIn('has filesystem ext4', report['/dev/nvme2n1']['reasons'])
        self.assertIn('not identified by the provider as a local SSD', report['/dev/nvme3n1']['reasons'])
        self.assertIn('wipefs reports signatures', report['/dev/nvme4n1']['reasons'])
        self.assertIn('is mounted', report['/dev/nvme5n1']['reasons'])  # older lsblk single mountpoint
        self.assertNotIn('/dev/nvme0n1p1', report)  # partitions are never candidates

    def prepare(self, device, confirm, fail=None):
        calls = []

        def run(cmd):
            calls.append(cmd)
            return subprocess.CompletedProcess(cmd, 1 if cmd[0] == fail else 0, '', '')
        with mock.patch.object(scratch_inventory, 'collect', return_value=(LSBLK, BY_ID, SIGNATURES)):
            ok, message = scratch_inventory.prepare(device, confirm, run)
        return ok, message, calls

    def test_prepare_formats_only_the_confirmed_eligible_device(self):
        ok, _, calls = self.prepare('/dev/nvme1n1', '/dev/nvme1n1')
        self.assertTrue(ok)
        self.assertEqual(calls[0], ['mkfs.ext4', '-L', 'models-scratch', '/dev/nvme1n1'])
        self.assertEqual(calls[-1], ['mount', '/dev/nvme1n1', '/mnt/models-scratch'])

    def test_prepare_refuses_without_touching_disks(self):
        for device, confirm in (('/dev/nvme1n1', '/dev/nvme2n1'), ('/dev/nvme0n1', '/dev/nvme0n1'),
                                ('/dev/nvme2n1', '/dev/nvme2n1'), ('/dev/nvme4n1', '/dev/nvme4n1'), ('/dev/sdz', '/dev/sdz')):
            ok, message, calls = self.prepare(device, confirm)
            self.assertFalse(ok, device)
            self.assertTrue(message.startswith('refused'), message)
            self.assertEqual(calls, [])

    def test_prepare_reports_mkfs_failure(self):
        ok, message, calls = self.prepare('/dev/nvme1n1', '/dev/nvme1n1', fail='mkfs.ext4')
        self.assertFalse(ok)
        self.assertIn('mkfs.ext4', message)
        self.assertEqual(len(calls), 1)


if __name__ == '__main__':
    unittest.main()
