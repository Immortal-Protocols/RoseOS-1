"""OCT29-INFRA-S007: the os-release build stage's available-space guard (release-pipeline.py capacity_preflight).

Synthetic statvfs/stat values only: no builder, host, network or cloud access. The checked-in frankel.json keeps the OS
build allowance null, so the real configuration fails closed; the passing cases use a synthetic allowance that is not
a claimed OS footprint."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest

SCRIPTS = Path(__file__).resolve().parents[1]
ROOT = SCRIPTS.parent
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location('release_pipeline_capacity', SCRIPTS/'release-pipeline.py')
pipeline = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pipeline)
FRANKEL = json.loads((ROOT/'os/release-pipeline/frankel.json').read_text())
RESERVE = 322122547200
GiB = 1 << 30


def config(allowance=40 * GiB, reserve=RESERVE):
    return {'host': {'capacity': {'reserveBytes': reserve, 'osBuildAllowanceBytes': allowance}}}


class Fs:
    """Synthetic statvfs/stat: {path prefix: (device, available bytes)}; longest prefix wins."""

    def __init__(self, table, fail_stat=(), fail_statvfs=(), frsize=4096):
        self.table, self.fail_stat, self.fail_statvfs, self.frsize = table, fail_stat, fail_statvfs, frsize
        self.statvfs_calls = []

    def entry(self, path):
        key = max((k for k in self.table if str(path).startswith(k)), key=len)
        return self.table[key]

    def stat(self, path):
        if str(path) in self.fail_stat:
            raise PermissionError(path)
        return types.SimpleNamespace(st_dev=self.entry(path)[0])

    def statvfs(self, path):
        self.statvfs_calls.append(str(path))
        if str(path) in self.fail_statvfs:
            raise OSError(5, 'io')
        available = self.entry(path)[1]
        return types.SimpleNamespace(f_bavail=available // self.frsize if self.frsize else 0, f_bfree=10 ** 15,
            f_frsize=self.frsize)


class Paths:
    def __init__(self, test):
        self.root = Path(tempfile.mkdtemp(prefix='capacity-'))
        test.addCleanup(lambda: __import__('shutil').rmtree(self.root, True))
        (self.root/'src/out').mkdir(parents=True)
        (self.root/'actions').mkdir()

    def paths(self):
        return {'source tree': self.root/'src', 'OUT_DIR': self.root/'src/out', 'release workspace': self.root/'actions/r1/release'}


class Preflight(unittest.TestCase):
    def test_exact_threshold_passes_and_one_byte_less_fails(self):
        p = Paths(self)
        needed = RESERVE + 40 * GiB
        fs = Fs({str(p.root): (1, needed)}, frsize=1)
        self.assertEqual(pipeline.capacity_preflight(config(), p.paths(), fs.statvfs, fs.stat), [('source tree', needed)])
        fs = Fs({str(p.root): (1, needed - 1)}, frsize=1)
        with self.assertRaisesRegex(pipeline.CheckFailed, f'has {needed - 1} bytes available; {needed} are required'):
            pipeline.capacity_preflight(config(), p.paths(), fs.statvfs, fs.stat)

    def test_f_bavail_not_f_bfree(self):
        p = Paths(self)
        fs = Fs({str(p.root): (1, 10 * GiB)})  # f_bfree is huge, f_bavail small
        with self.assertRaises(pipeline.CheckFailed):
            pipeline.capacity_preflight(config(), p.paths(), fs.statvfs, fs.stat)

    def test_same_device_is_measured_once_and_different_devices_each(self):
        p = Paths(self)
        fs = Fs({str(p.root): (1, 1 << 50)})
        pipeline.capacity_preflight(config(), p.paths(), fs.statvfs, fs.stat)
        self.assertEqual(fs.statvfs_calls, [str(p.root/'src')])
        fs = Fs({str(p.root): (1, 1 << 50), str(p.root/'src/out'): (2, 1 << 50), str(p.root/'actions'): (3, RESERVE)})
        with self.assertRaisesRegex(pipeline.CheckFailed, 'release workspace filesystem has'):
            pipeline.capacity_preflight(config(), p.paths(), fs.statvfs, fs.stat)
        self.assertEqual(fs.statvfs_calls, [str(p.root/'src'), str(p.root/'src/out'), str(p.root/'actions')])

    def test_missing_release_workspace_is_measured_on_its_nearest_ancestor(self):
        p = Paths(self)
        fs = Fs({str(p.root): (1, 1 << 50), str(p.root/'actions'): (9, 1 << 50)})
        pipeline.capacity_preflight(config(), p.paths(), fs.statvfs, fs.stat)
        self.assertIn(str(p.root/'actions'), fs.statvfs_calls)
        self.assertFalse((p.root/'actions/r1').exists())

    def test_unknown_space_fails(self):
        p = Paths(self)
        for fs in (Fs({str(p.root): (1, 1 << 50)}, fail_stat={str(p.root/'src/out')}),
                   Fs({str(p.root): (1, 1 << 50)}, fail_statvfs={str(p.root/'src')}),
                   Fs({str(p.root): (1, 1 << 50)}, frsize=0)):
            with self.assertRaisesRegex(pipeline.CheckFailed, 'could not be established'):
                pipeline.capacity_preflight(config(), p.paths(), fs.statvfs, fs.stat)

    def test_unknown_os_allowance_fails_closed_after_measuring(self):
        p = Paths(self)
        fs = Fs({str(p.root): (1, 2000 * GiB)}, frsize=1)
        for allowance in (None, 0, -1, True, '40', 40.0):
            with self.assertRaisesRegex(pipeline.CheckFailed, 'OS build allowance is unknown') as error:
                pipeline.capacity_preflight(config(allowance), p.paths(), fs.statvfs, fs.stat)
            self.assertIn(f'source tree {2000 * GiB} bytes available', str(error.exception))
        # The checked-in Frankel contract: reserve kept, allowance null, so the real build fails closed.
        self.assertEqual(FRANKEL['host']['capacity']['reserveBytes'], RESERVE)
        self.assertIsNone(FRANKEL['host']['capacity']['osBuildAllowanceBytes'])
        self.assertEqual(FRANKEL['host']['capacity']['mode'], 'budgeted')  # S008; static mode would still refuse
        with self.assertRaisesRegex(pipeline.CheckFailed, 'OS build allowance is unknown'):
            pipeline.capacity_preflight(FRANKEL, p.paths(), fs.statvfs, fs.stat)

    def test_reserve_is_never_lowered_or_omitted(self):
        p = Paths(self)
        fs = Fs({str(p.root): (1, 1 << 50)})
        for cfg in (config(reserve=RESERVE - 1), config(reserve=None), {'host': {}}):
            with self.assertRaisesRegex(pipeline.CheckFailed, 'never lowered'):
                pipeline.capacity_preflight(cfg, p.paths(), fs.statvfs, fs.stat)
        self.assertEqual(pipeline.OS_RESERVE_BYTES, RESERVE)


class BuildStage(unittest.TestCase):
    """build() takes the lock, then the capacity check, before integration, make or any workspace write."""

    def setUp(self):
        self.p = Paths(self)
        self.events = []
        self.cfg = copy.deepcopy(FRANKEL)
        self.cfg['host'].update(sourceRoot=str(self.p.root/'src'), outDir='out', lockPath=str(self.p.root/'build.lock'),
            integrateCommand=['true'])
        self.cfg['host']['capacity']['mode'] = 'static'  # S007's static bound; budgeted mode: test_capacity_monitor.py
        self.cfg['host']['capacity']['osBuildAllowanceBytes'] = 40 * GiB  # synthetic
        self.args = types.SimpleNamespace(apps='[]', release_dir=str(self.p.root/'actions/r1/release'))
        patches = {'check_version_baselines': lambda *a: None, 'stage_env': lambda *a: {},
                   'run_entry_point': lambda *a: self.events.append('integrate'), 'drop_signer_access': lambda: None}
        for name, value in patches.items():
            original = getattr(pipeline, name)
            setattr(pipeline, name, value)
            self.addCleanup(setattr, pipeline, name, original)

    def adapter(self, available):
        fs = Fs({'': (1, available)}, frsize=1)  # every stage write path, temp and RAM staging included
        events = self.events

        class Adapter:
            statvfs, stat = staticmethod(fs.statvfs), staticmethod(fs.stat)

            def make(self, *a):
                events.append('make')
        return Adapter(), fs

    def test_insufficient_or_unknown_space_mutates_nothing(self):
        for available, allowance in ((RESERVE, 40 * GiB), (1 << 50, None)):
            self.cfg['host']['capacity']['osBuildAllowanceBytes'] = allowance
            adapter, _ = self.adapter(available)
            with self.assertRaises(pipeline.CheckFailed):
                pipeline.build(self.args, self.cfg, {}, adapter)
            self.assertEqual(self.events, [])
            self.assertFalse((self.p.root/'actions/r1').exists())
            self.assertEqual(sorted(os.listdir(self.p.root/'src')), ['out'])

    def test_enough_space_proceeds_to_integration(self):
        adapter, _ = self.adapter(1 << 50)
        with self.assertRaisesRegex(pipeline.CheckFailed, 'integration did not produce'):
            pipeline.build(self.args, self.cfg, {}, adapter)
        self.assertEqual(self.events, ['integrate'])
        self.assertTrue((self.p.root/'actions/r1/release').is_dir())

    def test_lock_is_enforced_before_capacity(self):
        import fcntl
        lock = open(self.cfg['host']['lockPath'], 'a')
        self.addCleanup(lock.close)
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        adapter, fs = self.adapter(1 << 50)
        with self.assertRaisesRegex(pipeline.CheckFailed, 'lock is held'):
            pipeline.build(self.args, self.cfg, {}, adapter)
        self.assertEqual((fs.statvfs_calls, self.events), ([], []))
        self.assertFalse((self.p.root/'actions/r1').exists())

    def test_fake_adapter_reports_recorded_space(self):
        fake = pipeline.Fake(self.p.root/'fake')
        self.assertGreater(fake.statvfs(self.p.root).f_bavail, RESERVE)
        (self.p.root/'fake/available-bytes').write_text(str(RESERVE))
        self.assertEqual(fake.statvfs(self.p.root).f_bavail * fake.statvfs(self.p.root).f_frsize, RESERVE)
        self.assertIs(pipeline.Production.statvfs, os.statvfs)


class MutationControls(unittest.TestCase):
    def mutated(self, needle, replacement):
        source = (SCRIPTS/'release-pipeline.py').read_text()
        self.assertEqual(source.count(needle), 1, needle)
        module = types.ModuleType('mutated_release_pipeline')
        module.__file__ = str(SCRIPTS/'release-pipeline.py')
        exec(compile(source.replace(needle, replacement), module.__file__, 'exec'), module.__dict__)
        return module

    def test_guards_are_load_bearing(self):
        p = Paths(self)
        low = Fs({str(p.root): (1, RESERVE)}, frsize=1)
        m = self.mutated('        if available < needed:', '        if False:')
        self.assertEqual(len(m.capacity_preflight(config(), p.paths(), low.statvfs, low.stat)), 1)
        m = self.mutated('    if type(allowance) is not int or allowance <= 0:', '    if False:\n        pass\n    if allowance is None:\n        allowance = 0\n    if False:')
        big = Fs({str(p.root): (1, 1 << 50)}, frsize=1)
        self.assertTrue(m.capacity_preflight(config(None), p.paths(), big.statvfs, big.stat))
        m = self.mutated("    if capacity.get('reserveBytes') != OS_RESERVE_BYTES:", '    if False:')
        self.assertTrue(m.capacity_preflight(config(reserve=1), p.paths(), big.statvfs, big.stat))


class Wiring(unittest.TestCase):
    def test_actual_ci_runs_this_suite(self):
        ci = (ROOT/'.github/workflows/release-verification.yml').read_text()
        self.assertIn('          python3 scripts/tests/test_release_capacity.py\n', ci)
        self.assertIn('python3 scripts/tests/test_release_capacity.py\n', (ROOT/'scripts/test-local.sh').read_text())
        self.assertEqual(ci.count("      - 'scripts/tests/test_release_capacity.py'\n"), 2)


if __name__ == '__main__':
    unittest.main()
