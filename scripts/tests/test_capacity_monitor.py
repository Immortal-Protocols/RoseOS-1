"""OCT29-INFRA-S008: scripts/capacity_monitor.py and the budgeted os-release stages.

Real child process trees (python fixtures that fork a descendant and ignore SIGTERM) run under a Budget whose
statvfs is synthetic, so a threshold crossing can be staged at a chosen moment. Nothing builds, signs or reads a key;
no builder, host, network or cloud access. The 64 GiB stop buffer is an abort margin, not a peak estimate."""
import contextlib
import copy
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import textwrap
import time
import types
import unittest

SCRIPTS = Path(__file__).resolve().parents[1]
ROOT = SCRIPTS.parent
sys.path.insert(0, str(SCRIPTS))
import capacity_monitor as cm  # noqa: E402

spec = importlib.util.spec_from_file_location('release_pipeline_budget', SCRIPTS/'release-pipeline.py')
pipeline = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pipeline)
FRANKEL = json.loads((ROOT/'os/release-pipeline/frankel.json').read_text())
LIMIT = cm.FLOOR_BYTES + cm.STOP_BUFFER_BYTES
GiB = 1 << 30
POLICY = {'floor': cm.FLOOR_BYTES, 'buffer': cm.STOP_BUFFER_BYTES, 'interval': 0.1, 'termGrace': 0.3, 'killVerify': 1.0}

# A TERM-resistant parent that forks a TERM-resistant descendant; both record their pid and sleep.
TREE = textwrap.dedent('''
    import os, signal, sys, time
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    marker = sys.argv[1]
    pid = os.fork()
    if pid == 0:
        open(marker + '.desc', 'w').write(str(os.getpid()))
        time.sleep(60)
        os._exit(0)
    open(marker, 'w').write(str(os.getpid()))
    time.sleep(60)
''')
# Exits at once but leaves a descendant that finishes after a short delay.
LINGER = textwrap.dedent('''
    import os, sys, time
    if os.fork() == 0:
        time.sleep(0.4)
        open(sys.argv[1], 'w').write('descendant done')
        os._exit(0)
''')
# Signing-shaped child: the real host-sign SIGTERM hook plus host_secrets.private_directory around a secret file.
SIGNER = textwrap.dedent('''
    import importlib.util, signal, sys, time
    sys.path.insert(0, {scripts!r})
    spec = importlib.util.spec_from_file_location('host_sign', {scripts!r} + '/host-sign.py')
    host_sign = importlib.util.module_from_spec(spec); spec.loader.exec_module(host_sign)
    import host_secrets
    signal.signal(signal.SIGTERM, host_sign.terminate_cleanly)
    with host_secrets.private_directory('rose-test-signing-') as private:
        (private/'passphrase').write_bytes(b'synthetic-secret')
        open(sys.argv[1], 'w').write(str(private))
        time.sleep(60)
''')


def alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    with contextlib.suppress(FileNotFoundError):
        if Path(f'/proc/{pid}/stat').read_text().split(') ')[1].startswith('Z'):
            return False
    return True


class Space:
    """Synthetic statvfs: plenty until trigger() is true, then low (or an exception)."""

    def __init__(self, high=LIMIT + 100 * GiB, low=LIMIT, trigger=lambda: False, error=None):
        self.high, self.low, self.trigger, self.error, self.calls = high, low, trigger, error, 0

    def __call__(self, path):
        self.calls += 1
        if self.trigger():
            if self.error:
                raise self.error
            available = self.low
        else:
            available = self.high
        return types.SimpleNamespace(f_bavail=available, f_frsize=1, f_blocks=2100 * GiB)


class Fixture:
    def __init__(self, test):
        self.root = Path(tempfile.mkdtemp(prefix='budget-'))
        test.addCleanup(lambda: __import__('shutil').rmtree(self.root, True))
        self.marker = self.root/'pid'
        self.script = self.root/'tree.py'
        self.script.write_text(TREE)

    def budget(self, space, policy=POLICY, paths=None, **kw):
        return cm.Budget(dict(policy), paths or {'work': self.root}, space, **kw)

    def pids(self):
        return int(self.marker.read_text()), int(Path(str(self.marker) + '.desc').read_text())

    def ready(self):
        return self.marker.exists() and Path(str(self.marker) + '.desc').exists()


class Policy(unittest.TestCase):
    def test_policy_values_are_the_reviewed_ones(self):
        good = FRANKEL['host']['capacity']
        self.assertEqual(good['mode'], 'budgeted')
        self.assertIsNone(good['osBuildAllowanceBytes'])
        self.assertEqual(cm.policy_from(good), {'floor': 322122547200, 'buffer': 68719476736, 'interval': 1.0,
            'termGrace': 2.0, 'killVerify': 2.0})
        for change in ({'reserveBytes': cm.FLOOR_BYTES - 1}, {'stopBufferBytes': 32 * GiB}, {'sampleIntervalSeconds': 1.5},
                       {'termGraceSeconds': 3}, {'killVerifySeconds': 0}, {'sampleIntervalSeconds': True}, {'termGraceSeconds': None}):
            with self.assertRaises(cm.CapacityAbort, msg=change):
                cm.policy_from({**good, **change})


class Admission(unittest.TestCase):
    def test_initial_available_must_be_strictly_above_floor_plus_buffer(self):
        f = Fixture(self)
        logs = []
        with self.assertRaisesRegex(cm.CapacityAbort, f'has {LIMIT} bytes available'):
            f.budget(Space(high=LIMIT), log=logs.append).start()
        budget = f.budget(Space(high=LIMIT + 1), log=logs.append).start()
        self.assertEqual(len(budget.filesystems), 1)
        self.assertIn(f'stopAt={LIMIT}', logs[-1])
        self.assertIn('total=', logs[-1])

    def test_distinct_filesystems_are_each_measured(self):
        f = Fixture(self)
        (f.root/'a').mkdir()
        (f.root/'b').mkdir()
        devices = {str(f.root/'a'): 1, str(f.root/'b'): 2}

        def stat(path):
            return types.SimpleNamespace(st_dev=devices.get(str(path), 1))
        budget = cm.Budget(dict(POLICY), {'a': f.root/'a', 'same': f.root/'a/new/dir', 'b': f.root/'b'}, Space(), stat)
        self.assertEqual([fs['label'] for fs in budget.filesystems], ['a', 'b'])

    def test_stat_error_and_invalid_or_stale_samples_refuse(self):
        f = Fixture(self)
        with self.assertRaisesRegex(cm.CapacityAbort, 'could not be read'):
            f.budget(Space(trigger=lambda: True, error=OSError(5, 'io'))).start()
        bad = lambda path: types.SimpleNamespace(f_bavail=10 ** 15, f_frsize=0, f_blocks=0)
        with self.assertRaisesRegex(cm.CapacityAbort, 'invalid'):
            f.budget(bad).start()
        ticks = iter([0.0, 5.0] * 10)
        with self.assertRaisesRegex(cm.CapacityAbort, 'stale'):
            f.budget(Space(), clock=lambda: next(ticks)).start()


class OwnedTree(unittest.TestCase):
    def unrelated(self):
        other = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'], start_new_session=True)
        self.addCleanup(lambda: (other.kill(), other.wait()))
        return other

    def stopped(self, f, budget, code_re):
        other = self.unrelated()
        started = time.monotonic()
        with self.assertRaisesRegex(cm.CapacityAbort, code_re):
            budget.run([sys.executable, str(f.script), str(f.marker)])
        parent, descendant = f.pids()
        self.assertFalse(alive(parent))
        self.assertFalse(alive(descendant))
        self.assertIsNone(other.poll())  # an unrelated process group is never signalled
        self.assertLess(time.monotonic() - started, 10)

    def test_threshold_crossing_mid_child_kills_term_resistant_tree(self):
        f = Fixture(self)
        self.stopped(f, f.budget(Space(trigger=f.ready)), 'budget stops at')

    def test_stat_failure_mid_child_stops_the_tree(self):
        f = Fixture(self)
        self.stopped(f, f.budget(Space(trigger=f.ready, error=PermissionError(13, 'denied'))), 'could not be read')

    def test_monitor_crash_stops_the_tree_and_propagates(self):
        f = Fixture(self)
        other = self.unrelated()
        with self.assertRaisesRegex(RuntimeError, 'monitor bug'):
            f.budget(Space(trigger=f.ready, error=RuntimeError('monitor bug'))).run([sys.executable, str(f.script), str(f.marker)])
        self.assertFalse(any(alive(p) for p in f.pids()))
        self.assertIsNone(other.poll())

    def test_parent_cancellation_stops_the_tree(self):
        f = Fixture(self)

        def interrupt():
            if f.ready():
                raise KeyboardInterrupt
            return False
        with self.assertRaises(KeyboardInterrupt):
            f.budget(Space(trigger=interrupt)).run([sys.executable, str(f.script), str(f.marker)])
        self.assertFalse(any(alive(p) for p in f.pids()))

    def test_child_failure_and_lingering_descendant_are_owned(self):
        f = Fixture(self)
        with self.assertRaises(subprocess.CalledProcessError):
            f.budget(Space()).run([sys.executable, '-c', 'import sys; sys.exit(3)'])
        (f.root/'linger.py').write_text(LINGER)
        done = f.root/'done'
        self.assertEqual(f.budget(Space()).run([sys.executable, str(f.root/'linger.py'), str(done)]), 0)
        self.assertTrue(done.exists())  # run() returned only after the descendant finished

    def test_signing_child_cleans_its_secret_directory_on_a_stop(self):
        f = Fixture(self)
        (f.root/'signer.py').write_text(SIGNER.format(scripts=str(SCRIPTS)))
        marker = f.root/'private'
        policy = dict(POLICY, termGrace=2.0)
        with self.assertRaises(cm.CapacityAbort):
            f.budget(Space(trigger=marker.exists), policy).run([sys.executable, str(f.root/'signer.py'), str(marker)])
        private = Path(marker.read_text())
        self.assertFalse(private.exists())  # host_secrets.private_directory overwrote and removed it

    def test_stop_reports_a_survivor(self):
        f = Fixture(self)
        budget = f.budget(Space())
        proc = types.SimpleNamespace(poll=lambda: None)
        original_killpg, original_alive = cm.os.killpg, cm.group_alive
        cm.os.killpg, cm.group_alive = (lambda pgid, sig: None), (lambda pgid: True)
        try:
            with self.assertRaisesRegex(cm.CapacityAbort, 'did not terminate'):
                budget.stop(999999, proc)
        finally:
            cm.os.killpg, cm.group_alive = original_killpg, original_alive


class GuardedCopy(unittest.TestCase):
    def test_copy_admission_progress_and_partial_cleanup(self):
        f = Fixture(self)
        source = f.root/'target_files.zip'
        source.write_bytes(os.urandom(3 * cm.CHUNK_BYTES // 2))
        out = f.root/'release'
        out.mkdir()
        f.budget(Space()).copy(source, out/'unsigned-target_files.zip')
        self.assertEqual((out/'unsigned-target_files.zip').read_bytes(), source.read_bytes())
        # Refused before writing when the whole copy would cross the line.
        size = source.stat().st_size
        with self.assertRaisesRegex(cm.CapacityAbort, f'before a {size}-byte copy'):
            f.budget(Space(high=LIMIT + size)).copy(source, out/'second.zip')
        self.assertEqual(sorted(os.listdir(out)), ['unsigned-target_files.zip'])
        # A crossing during the copy stops it and removes only its own partial file.
        state = {'n': 0}

        def later():
            state['n'] += 1
            return state['n'] > 1  # admission sample passes; the first in-copy sample crosses
        ticks = iter(range(1000))
        with self.assertRaises(cm.CapacityAbort):
            f.budget(Space(trigger=later), clock=lambda: next(ticks) * 0.05).copy(source, out/'third.zip')
        self.assertEqual(sorted(os.listdir(out)), ['unsigned-target_files.zip'])

    def test_unmeasured_destination_is_refused(self):
        f = Fixture(self)
        source = f.root/'a.bin'
        source.write_bytes(b'x')
        devices = {str(f.root): 1}
        stat = lambda p: types.SimpleNamespace(st_dev=devices.get(str(p), 7))
        budget = cm.Budget(dict(POLICY), {'work': f.root}, Space(), stat)
        other = Path(tempfile.mkdtemp(prefix='other-'))
        self.addCleanup(lambda: __import__('shutil').rmtree(other, True))
        with self.assertRaisesRegex(cm.CapacityAbort, 'uncovered write'):
            budget.copy(source, other/'a.bin', stat)


class Stages(unittest.TestCase):
    """release-pipeline build/sign in budgeted mode with a real child crossing the line."""

    def setUp(self):
        self.f = Fixture(self)
        root = self.f.root
        (root/'src/out').mkdir(parents=True)
        self.cfg = copy.deepcopy(FRANKEL)
        self.cfg['host'].update(sourceRoot=str(root/'src'), outDir='out', lockPath=str(root/'build.lock'),
            integrateCommand=[sys.executable, str(self.f.script), str(self.f.marker)],
            signCommand=[sys.executable, str(self.f.script), str(self.f.marker)])
        self.cfg['host']['capacity'].update(sampleIntervalSeconds=0.1, termGraceSeconds=0.3, killVerifySeconds=1.0)
        self.args = types.SimpleNamespace(apps='[]', release_dir=str(root/'actions/r1/release'))
        self.dropped = []
        for name, value in {'check_version_baselines': lambda *a: None, 'stage_env': lambda *a: dict(os.environ),
                            'drop_signer_access': lambda: self.dropped.append(True)}.items():
            original = getattr(pipeline, name)
            setattr(pipeline, name, value)
            self.addCleanup(setattr, pipeline, name, original)

    def adapter(self, space):
        class Adapter:
            statvfs = staticmethod(space)
            stat = staticmethod(os.stat)
            made = []

            def make(self, config, cand, env, runner=None):
                self.made.append(runner)
        return Adapter()

    def test_build_stops_the_integration_tree_and_releases_the_lock(self):
        adapter = self.adapter(Space(trigger=self.f.ready))
        with self.assertRaises(cm.CapacityAbort):
            pipeline.build(self.args, self.cfg, {}, adapter)
        self.assertFalse(any(alive(p) for p in self.f.pids()))
        self.assertEqual(adapter.made, [])
        self.assertEqual(self.dropped, [True])
        import fcntl
        with open(self.cfg['host']['lockPath'], 'a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)  # released

    def test_build_refuses_before_writes_when_the_start_is_at_the_line(self):
        adapter = self.adapter(Space(high=LIMIT))
        with self.assertRaises(cm.CapacityAbort):
            pipeline.build(self.args, self.cfg, {}, adapter)
        self.assertFalse((self.f.root/'actions').exists())
        self.assertFalse(self.f.marker.exists())

    def test_sign_stops_the_signing_tree_and_drops_signer_access(self):
        adapter = self.adapter(Space(trigger=self.f.ready))
        with self.assertRaises(cm.CapacityAbort):
            pipeline.sign(self.args, self.cfg, {}, adapter)
        self.assertFalse(any(alive(p) for p in self.f.pids()))
        self.assertEqual(self.dropped, [True])
        # Refused at admission: signer access is still dropped and nothing starts.
        self.f.marker.unlink()
        self.dropped.clear()
        with self.assertRaises(cm.CapacityAbort):
            pipeline.sign(self.args, self.cfg, {}, self.adapter(Space(high=LIMIT)))
        self.assertEqual(self.dropped, [True])
        self.assertFalse(self.f.marker.exists())

    def test_static_mode_and_unknown_mode(self):
        cfg = copy.deepcopy(self.cfg)
        cfg['host']['capacity']['mode'] = 'static'
        self.assertIsNone(pipeline.budgeted_gate(cfg, {'w': self.f.root}, self.adapter(Space())))
        with self.assertRaisesRegex(pipeline.CheckFailed, 'OS build allowance is unknown'):
            pipeline.capacity_gate(cfg, {'w': self.f.root}, self.adapter(Space()))
        cfg['host']['capacity']['mode'] = 'quota'
        with self.assertRaisesRegex(pipeline.CheckFailed, 'not static or budgeted'):
            pipeline.capacity_gate(cfg, {'w': self.f.root}, self.adapter(Space()))

    def test_stage_write_paths_cover_temp_and_ram_staging(self):
        paths = pipeline.stage_write_paths(self.cfg, Path('/x/release'))
        self.assertTrue({'source tree', 'OUT_DIR', 'release workspace', 'temporary directory'} <= set(paths))


class MutationControls(unittest.TestCase):
    def mutated(self, needle, replacement):
        source = (SCRIPTS/'capacity_monitor.py').read_text()
        self.assertEqual(source.count(needle), 1, needle)
        module = types.ModuleType('mutated_capacity_monitor')
        exec(compile(source.replace(needle, replacement), str(SCRIPTS/'capacity_monitor.py'), 'exec'), module.__dict__)
        return module

    def test_strict_line_and_stop_are_load_bearing(self):
        f = Fixture(self)
        m = self.mutated('            if available - need <= self.limit:', '            if available - need < self.limit:')
        m.Budget(dict(POLICY), {'w': f.root}, Space(high=LIMIT), log=lambda line: None).start()
        m = self.mutated('            self.stop(pgid, proc)\n', '            pass\n')
        with self.assertRaises(m.CapacityAbort):
            m.Budget(dict(POLICY), {'w': f.root}, Space(trigger=f.ready)).run([sys.executable, str(f.script), str(f.marker)])
        survivors = [p for p in f.pids() if alive(p)]
        self.assertTrue(survivors)  # without the stop the tree would keep running
        for pid in survivors:
            os.kill(pid, signal.SIGKILL)
        with contextlib.suppress(ChildProcessError):
            for pid in survivors:
                os.waitpid(pid, 0)


class Wiring(unittest.TestCase):
    def test_actual_ci_runs_this_suite(self):
        ci = (ROOT/'.github/workflows/release-verification.yml').read_text()
        self.assertIn('          python3 scripts/tests/test_capacity_monitor.py\n', ci)
        self.assertIn('python3 scripts/tests/test_capacity_monitor.py\n', (ROOT/'scripts/test-local.sh').read_text())
        for path in ('scripts/capacity_monitor.py', 'scripts/tests/test_capacity_monitor.py'):
            self.assertEqual(ci.count(f"      - '{path}'\n"), 2, path)


if __name__ == '__main__':
    unittest.main()
