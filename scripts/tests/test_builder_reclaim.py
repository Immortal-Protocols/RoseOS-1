"""BUILDER-RECLAIM: scripts/builder-reclaim.py and its policy os/release-pipeline/storage/builder-reclaim.json.

Synthetic attempt storage in temporary directories only: no builder, no real lock, no network, nothing on any host."""
import copy
import fcntl
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
POLICY = ROOT/'os/release-pipeline/storage/builder-reclaim.json'
WORKFLOW = (ROOT/'.github/workflows/os-release.yml').read_text()


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT/path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


reclaim = load('builder_reclaim', 'scripts/builder-reclaim.py')
allocate = load('release_allocate', 'scripts/release-allocate.py')
mustang_allocate = load('mustang_allocate', 'scripts/mustang-allocate.py')
launcher3 = load('launcher3_compile', 'scripts/launcher3-compile.py')
PROTECTED = ['frankel-2026110403', 'frankel-2026110403-resume-41861964515', 'frankel-2026102802-attempt1-40634711221',
             'allocations', 'app-allocations', 'mustang-allocations', 'blazer-allocations', 'reconcile',
             'mustang-reconcile-2026110401', 'mustang-integrate-2026110401', 'blazer-integrate-2026110301',
             'blazer-reconcile-2026110301', 'mustang-sign-41738359336-1', 'mustang-collect-41738359336-1',
             'mustang-package-41738359336-1', 'mustang-ota-41761758623-1', 'rango-retained', 'rango-identity',
             'rango-factory-package-41515240855-1', 'rango-factory-inspect-41515240856-1',
             'frankel-factory-package-41600000000-1', 'public-assets', 'tools', 'reclaim', '.reclaim-trash',
             'alpha-source', 'blazer-prep-41473133543-1.part', 'mustang-prep-1-1-old', 'app-reuse-', 'app-x1',
             'app-200-notes', 'device-readiness-5-1.bak', 'launcher3-compile-5-1-keep']


def tree(path):
    """{relative path: kind and bytes} of everything under path, without following links."""
    out = {}
    for base, dirs, files in os.walk(path):
        for name in dirs + files:
            full = Path(base)/name
            rel = str(full.relative_to(path))
            out[rel] = ('link:' + os.readlink(full)) if full.is_symlink() else ('dir' if full.is_dir() else
                                                                                 full.read_bytes().decode())
    return out


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(os.path.realpath(tempfile.mkdtemp(prefix='reclaim-')))
        self.addCleanup(self.remove, self.tmp)
        self.home = self.tmp/'home'
        self.root = self.home/'rose-actions'
        self.root.mkdir(parents=True)
        self.outside = self.tmp/'outside'
        (self.outside/'secret').mkdir(parents=True)
        (self.outside/'secret'/'key.pem').write_text('KEY')
        self.repo = self.tmp/'repo'
        for d in ('.github/workflows', 'os', 'scripts'):
            (self.repo/d).mkdir(parents=True)
        self.policy = reclaim.load_policy(POLICY)
        self.policy.update(root=str(self.root), lock=str(self.home/'frankel-build.lock'))

    @staticmethod
    def remove(path):
        for base, dirs, _ in os.walk(path):
            for d in dirs:
                p = Path(base)/d
                if not p.is_symlink():
                    p.chmod(0o700)
        shutil.rmtree(path)

    def prep(self, name, build='2026110401'):
        run = self.root/name
        (run/'receipts').mkdir(parents=True)
        (run/'receipts'/'compile-completed.json').write_text('{"returncode": 0}')
        (run/'source.selection.tsv').write_text('selection')
        for project in ('build/make', 'frameworks/base', f'out-mustang-build-{build}/soong'):
            (run/'source'/project).mkdir(parents=True)
            for i in range(3):
                (run/'source'/project/f'f{i}').write_text('x' * 64)
        (run/'source'/'releases'/build).mkdir(parents=True)
        (run/'source'/'releases'/build/'signed-target_files.zip').write_text('signed')
        (run/'downloads'/'vendor').mkdir(parents=True)
        (run/'downloads'/'vendor'/'image.zip').write_text('vendor')
        return run

    def apply(self, label='9-1-builder', current=None, repo=None):
        return reclaim.apply(self.policy, label, current, repo or self.repo)

    def kept_prep(self, run):
        return {p: v for p, v in tree(run).items()}


class Policy(unittest.TestCase):
    def test_the_committed_policy_reclaims_attempt_storage_under_the_builder_lock(self):
        policy = reclaim.load_policy(POLICY)
        host = json.loads((ROOT/'os/release-pipeline/frankel.json').read_text())['host']
        self.assertEqual(policy['lock'], host['lockPath'])
        self.assertEqual(policy['root'], str(Path(host['sourceRoot']).parent/'rose-actions'))
        self.assertEqual([c['name'] for c in policy['classes']], ['mustang-prep', 'blazer-prep', 'launcher3-compile',
                                                                  'run-scratch', 'device-readiness-scratch',
                                                                  'alpha-staging'])
        raw = json.loads(POLICY.read_text())
        for spec in raw['classes']:
            self.assertTrue(spec['regenerate'] and spec['readers'], spec['name'])

    def test_no_class_matches_a_store_that_is_never_reclaimed(self):
        policy = reclaim.load_policy(POLICY)
        for name in PROTECTED:
            for cls in policy['classes']:
                if cls['parent'] == '.':
                    self.assertIsNone(cls['entry'].fullmatch(name), (cls['name'], name))
        staging = next(c for c in policy['classes'] if c['name'] == 'alpha-staging')
        for name in ('f21bf683-e000da0d', 'successors', 'staging-1', '.staging', '.staging-a/b'):
            self.assertIsNone(staging['entry'].fullmatch(name), name)

    def test_the_launcher3_class_is_the_harness_finish_rule(self):
        # One rule for the Launcher3 harness: finish removes WORK_DIRS of its run root and keeps receipts; reclaim is
        # the backstop for a run whose finish never ran, with the same paths, under the same root.
        cls = next(c for c in reclaim.load_policy(POLICY)['classes'] if c['name'] == 'launcher3-compile')
        self.assertEqual(tuple(r['path'] for r in cls['reclaim']), launcher3.WORK_DIRS)
        binding = json.loads((ROOT/'os/build-only/launcher3-compile-host.json').read_text())
        root = launcher3.task_root(binding, '41123456789', '2')
        self.assertEqual(str(root.parent), reclaim.load_policy(POLICY)['root'])
        self.assertTrue(cls['entry'].fullmatch(root.name))

    def test_unsafe_policies_are_invalid(self):
        raw = json.loads(POLICY.read_text())

        def broken(change):
            spec = copy.deepcopy(raw)
            change(spec)
            path = Path(tempfile.mkdtemp())/'p.json'
            self.addCleanup(shutil.rmtree, path.parent)
            path.write_text(json.dumps(spec))
            with self.assertRaises(reclaim.Invalid):
                reclaim.load_policy(path)
        broken(lambda s: s['classes'][0]['reclaim'].append({'path': 'source/out'}))
        broken(lambda s: s['classes'][0]['reclaim'].append({'path': '..'}))
        broken(lambda s: s['classes'][3]['reclaim'][0].update(keep=['x']))
        broken(lambda s: s['classes'][3].update(keepNewest=1, entry='app-[0-9]+'))
        broken(lambda s: s['classes'].append(dict(s['classes'][0])))
        broken(lambda s: s.update(pinSources=['/etc']))
        broken(lambda s: s.update(pinSources=['os/../..']))
        broken(lambda s: s.update(trashDir='reclaim'))
        broken(lambda s: s.update(classes=[]))
        broken(lambda s: s.update(root='rose-actions'))
        broken(lambda s: s.update(root='/'))
        broken(lambda s: s.update(root='/home/pixelbuild/../tmp'))
        broken(lambda s: s.update(lockFrom='os/release-pipeline/frankel.json host.nothing'))
        broken(lambda s: s['classes'][5].update(parent='a/b'))
        broken(lambda s: s['classes'][0].update(entry='(unclosed'))
        broken(lambda s: s['classes'][2]['reclaim'].append({'path': '.'}))

    def test_the_real_repository_pins_the_recovery_and_blazer_preps(self):
        pins = reclaim.pinned_names(reclaim.load_policy(POLICY))
        self.assertIn('mustang-prep-41720536968-1', pins)
        self.assertIn('blazer-prep-41473133543-1', pins)


class Selection(Base):
    def test_old_preps_lose_only_their_regenerable_paths(self):
        runs = [self.prep(f'mustang-prep-10{i}-1') for i in range(1, 5)]
        before = {r.name: tree(r) for r in runs}
        record = self.apply()
        self.assertEqual(record['verdict'], 'reclaimed')
        self.assertTrue(record['complete'])
        for run in runs[2:]:   # the two newest stay whole
            self.assertEqual(tree(run), before[run.name], run.name)
        for run in runs[:2]:
            self.assertEqual(sorted(tree(run)), sorted(['receipts', 'receipts/compile-completed.json', 'source',
                                                        'source.selection.tsv', 'source/releases',
                                                        'source/releases/2026110401',
                                                        'source/releases/2026110401/signed-target_files.zip']))
        kept = {(k['entry'], k['kept']) for k in record['kept']}
        self.assertIn(('mustang-prep-104-1', 'one of the 2 newest'), kept)

    def test_pinned_and_current_run_entries_stay_whole(self):
        runs = [self.prep(f'mustang-prep-10{i}-1') for i in range(1, 5)]
        (self.repo/'os'/'recovery.json').write_text('{"preserved": "/home/pixelbuild/rose-actions/mustang-prep-101-1/source"}')
        before = tree(runs[0]), tree(runs[1])
        record = self.apply(current=102)
        self.assertEqual((tree(runs[0]), tree(runs[1])), before)
        reasons = {k['entry']: k['kept'] for k in record['kept']}
        self.assertEqual(reasons['mustang-prep-101-1'], 'pinned by a tracked file')
        self.assertEqual(reasons['mustang-prep-102-1'], 'the current run')
        self.assertEqual(record['reclaim'], [])

    def test_scratch_of_killed_runs_goes_whole_but_never_the_current_runs(self):
        for name in ('app-200', 'app-201', 'cf-alpha-export-202', 'app-reuse-203', 'device-readiness-204-1'):
            (self.root/name/'work').mkdir(parents=True)
            (self.root/name/'work'/'f').write_text('x')
        self.apply(current=201)
        self.assertEqual(sorted(os.listdir(self.root)), ['.reclaim-trash', 'app-201', 'reclaim'])

    def test_launcher3_leftovers_lose_their_work_dirs_and_keep_receipts(self):
        run = self.root/'launcher3-compile-300-1'
        for name in ('tree', 'out', 'home', 'tmp', 'receipts'):
            (run/name).mkdir(parents=True)
            (run/name/'f').write_text(name)
        self.apply()
        self.assertEqual(tree(run), {'receipts': 'dir', 'receipts/f': 'receipts'})

    def test_alpha_staging_leftovers_go_and_preparations_stay(self):
        alpha = self.root/'alpha-source'
        for name in ('.staging-f21bf683-0123456789ab', 'f21bf683-e000da0d', 'successors'):
            (alpha/name/'tree').mkdir(parents=True)
            (alpha/name/'tree'/'f').write_text(name)
        self.apply()
        self.assertEqual(sorted(os.listdir(alpha)), ['f21bf683-e000da0d', 'successors'])

    def test_everything_else_is_never_touched(self):
        for name in PROTECTED:
            if name in ('reclaim', '.reclaim-trash', 'alpha-source'):
                continue
            (self.root/name/'release').mkdir(parents=True)
            (self.root/name/'release'/'signed-target_files.zip').write_text(name)
            for sub in ('source', 'downloads', 'tree', 'out', 'home', 'tmp'):   # every class's reclaimable paths
                (self.root/name/sub).mkdir()
                (self.root/name/sub/'f').write_text(sub)
        (self.root/'notes.txt').write_text('operator notes')
        for name in ('mustang-prep-900-1', 'mustang-prep-901-1', 'blazer-prep-41473133544-1'):
            self.prep(name)   # newer than the near-miss names, so only an exact match could ever select those
        before = tree(self.root)
        record = self.apply()
        after = {k: v for k, v in tree(self.root).items() if not k.startswith(('reclaim', '.reclaim-trash'))}
        self.assertEqual(after, before)
        self.assertEqual((record['verdict'], record['reclaim'], record['items']), ('nothing-to-reclaim', [], []))

    def test_a_missing_pin_source_refuses_rather_than_guess(self):
        for i in (1, 2, 3):
            self.prep(f'mustang-prep-10{i}-1')
        before = tree(self.root)
        shutil.rmtree(self.repo/'os')
        with self.assertRaises(reclaim.Invalid):
            self.apply()
        self.assertEqual({k: v for k, v in tree(self.root).items() if not k.startswith(('reclaim', '.reclaim-trash'))},
                         before)
        self.assertEqual(os.listdir(self.root/'reclaim'), [])


class Locking(Base):
    def hold(self):
        fd = os.open(self.policy['lock'], os.O_RDWR | os.O_CREAT, 0o644)
        fcntl.flock(fd, fcntl.LOCK_EX)
        return fd

    def test_a_busy_lock_reclaims_nothing_and_writes_nothing(self):
        run = self.prep('mustang-prep-101-1')
        for i in (2, 3):
            self.prep(f'mustang-prep-10{i}-1')
        before = tree(self.root)
        fd = self.hold()
        try:
            record = self.apply()
        finally:
            os.close(fd)
        self.assertEqual(record['verdict'], 'busy')
        self.assertEqual(tree(self.root), before)
        self.assertTrue((run/'source'/'build').exists())

    def test_the_lock_is_held_for_the_whole_apply_and_released_after(self):
        for i in (1, 2, 3):
            self.prep(f'mustang-prep-10{i}-1')
        seen = []
        original = reclaim.empty_trash

        def probe(fd):
            other = os.open(self.policy['lock'], os.O_RDWR)
            try:
                with self.assertRaises(BlockingIOError):
                    fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
                seen.append('held')
            finally:
                os.close(other)
            return original(fd)
        with mock.patch.object(reclaim, 'empty_trash', probe):
            self.apply()
        self.assertEqual(seen, ['held', 'held'])
        fcntl.flock(self.hold(), fcntl.LOCK_UN)

    def test_a_symlinked_lock_refuses(self):
        os.symlink(self.outside/'lock', self.policy['lock'])
        with self.assertRaises(reclaim.Refused):
            self.apply()
        self.assertFalse((self.outside/'lock').exists())


class Interruption(Base):
    def setUp(self):
        super().setUp()
        self.runs = [self.prep(f'mustang-prep-10{i}-1') for i in range(1, 5)]
        for name in ('app-200', 'app-201'):
            (self.root/name/'w').mkdir(parents=True)

    def finished_state(self):
        return {r.name: tree(r) for r in self.runs}, sorted(os.listdir(self.root))

    def reference(self):
        other = Base('setUp')
        other.setUp()
        try:
            for i in range(1, 5):
                other.prep(f'mustang-prep-10{i}-1')
            for name in ('app-200', 'app-201'):
                (other.root/name/'w').mkdir(parents=True)
            other.apply()
            return {f'mustang-prep-10{i}-1': tree(other.root/f'mustang-prep-10{i}-1') for i in range(1, 5)}
        finally:
            other.doCleanups()

    def test_a_kill_after_moving_leaves_only_trash_which_the_next_apply_empties(self):
        with mock.patch.object(reclaim, 'empty_trash', side_effect=[([], []), KeyboardInterrupt]):
            with self.assertRaises(KeyboardInterrupt):
                self.apply('1-1-builder')
        self.assertTrue(os.listdir(self.root/'.reclaim-trash'))
        for run in self.runs[:2]:   # nothing half removed where a build reads: each path is gone whole or untouched
            self.assertEqual(sorted(os.listdir(run/'source')), ['releases'])
        record = self.apply('2-1-builder')
        self.assertTrue(record['earlierTrashRemoved'])
        self.assertTrue(record['complete'])
        self.assertEqual(os.listdir(self.root/'.reclaim-trash'), [])
        self.assertEqual({r.name: tree(r) for r in self.runs}, self.reference())
        self.assertTrue((self.root/'reclaim'/'0001-1-1-builder.plan.json').exists())
        self.assertFalse((self.root/'reclaim'/'0001-1-1-builder.json').exists())
        self.assertTrue((self.root/'reclaim'/'0002-2-1-builder.json').exists())

    def test_a_kill_part_way_through_removal_is_finished_later(self):
        calls = {'n': 0}
        unlink = os.unlink

        def flaky(*args, **kwargs):
            calls['n'] += 1
            if calls['n'] == 5:
                raise KeyboardInterrupt
            return unlink(*args, **kwargs)
        with mock.patch.object(reclaim.os, 'unlink', flaky):
            with self.assertRaises(KeyboardInterrupt):
                self.apply('1-1-builder')
        record = self.apply('2-1-builder')
        self.assertTrue(record['complete'])
        self.assertEqual(os.listdir(self.root/'.reclaim-trash'), [])
        self.assertEqual({r.name: tree(r) for r in self.runs}, self.reference())

    def test_a_second_apply_reclaims_nothing_more(self):
        self.apply('1-1-builder')
        state = self.finished_state()
        record = self.apply('2-1-builder')
        self.assertEqual((record['verdict'], record['items'], record['complete']), ('nothing-to-reclaim', [], True))
        self.assertEqual(self.finished_state(), state)


class PathSafety(Base):
    def test_symlinks_are_never_followed_moved_or_removed(self):
        self.prep('mustang-prep-104-1')
        self.prep('mustang-prep-103-1')
        os.symlink(self.outside, self.root/'mustang-prep-101-1')            # the entry itself
        run = self.prep('mustang-prep-102-1')
        shutil.rmtree(run/'downloads')
        os.symlink(self.outside, run/'downloads')                          # a listed path
        os.symlink(self.outside/'secret', run/'source'/'keys')             # a child of a listed directory
        os.symlink(self.outside/'secret', run/'source'/'build'/'link')     # inside a path that goes
        for name in ('app-200', 'launcher3-compile-300-1'):
            os.symlink(self.outside, self.root/name)
        record = self.apply()
        self.assertEqual(tree(self.outside), {'secret': 'dir', 'secret/key.pem': 'KEY'})
        self.assertTrue((self.root/'mustang-prep-101-1').is_symlink())
        self.assertTrue((run/'downloads').is_symlink())
        self.assertEqual(sorted(os.listdir(run/'source')), ['keys', 'releases'])
        left = {(i['path'], i['outcome']) for i in record['items'] if i['outcome'].startswith('left')}
        self.assertIn(('mustang-prep-102-1/downloads', 'left: a symlink'), left)
        self.assertIn(('mustang-prep-102-1/source/keys', 'left: a symlink'), left)
        self.assertIn(('mustang-prep-101-1', 'left: a symlink'), {(i['path'], i['outcome']) for i in record['items']})
        self.assertFalse(record['complete'])
        planned = {(i['path'], i['reason']) for i in record['left']}   # the plan already leaves them, never lists them
        self.assertLessEqual({('mustang-prep-102-1/downloads', 'a symlink'), ('mustang-prep-102-1/source/keys',
                                                                             'a symlink')}, planned)
        self.assertFalse({'mustang-prep-102-1/downloads', 'mustang-prep-102-1/source/keys'}
                         & {i['path'] for i in record['reclaim']})

    def test_an_unsafe_root_trash_or_receipt_directory_refuses_and_moves_nothing(self):
        self.prep('mustang-prep-101-1')
        for i in (2, 3):
            self.prep(f'mustang-prep-10{i}-1')
        before = tree(self.root)
        link = self.tmp/'link-to-root'
        os.symlink(self.root, link)
        policy = dict(self.policy, root=str(link))
        with self.assertRaises(reclaim.Refused):
            reclaim.apply(policy, 'x', None, self.repo)
        os.symlink(self.home, self.tmp/'home-alias')       # a real final component reached through a symlink
        policy = dict(self.policy, root=str(self.tmp/'home-alias'/'rose-actions'))
        with self.assertRaises(reclaim.Refused):
            reclaim.apply(policy, 'x', None, self.repo)
        with self.assertRaises(reclaim.Refused):
            reclaim.plan(policy, None, self.repo)
        with mock.patch.object(reclaim.os.path, 'realpath', lambda path: path):   # swapped for a link after the check
            with self.assertRaises(reclaim.Refused):
                reclaim.apply(dict(self.policy, root=str(link)), 'x', None, self.repo)
        (self.root/'.reclaim-trash').symlink_to(self.outside)
        with self.assertRaises(reclaim.Refused):
            self.apply()
        (self.root/'.reclaim-trash').unlink()
        (self.root/'reclaim').write_text('not a directory')
        with self.assertRaises(reclaim.Refused):
            self.apply()
        (self.root/'reclaim').unlink()
        self.assertEqual({k: v for k, v in tree(self.root).items() if k != '.reclaim-trash'}, before)

    @staticmethod
    def stat_with(field, names):
        """os.stat reporting field + 1 for the plain names a predicate accepts: a mount (st_dev) or a directory swapped
        between two looks (st_ino), which a test cannot make for real."""
        real = os.stat

        def fake(path, *args, **kwargs):
            st = real(path, *args, **kwargs)
            if isinstance(path, str) and names(path):
                fields = list(st)
                fields[field] += 1
                return os.stat_result(fields)
            return st
        return mock.patch.object(reclaim.os, 'stat', fake)

    def test_another_filesystem_is_never_entered(self):
        for i in (1, 2, 3):
            self.prep(f'mustang-prep-10{i}-1')
        before = tree(self.root/'mustang-prep-101-1')
        with self.stat_with(stat.ST_DEV, lambda name: name in ('downloads', 'source')):
            record = self.apply()
        self.assertEqual(tree(self.root/'mustang-prep-101-1'), before)
        items = {(i['path'], i['outcome']) for i in record['items']}
        self.assertIn(('mustang-prep-101-1/downloads', 'left: on another filesystem'), items)
        self.assertIn(('mustang-prep-101-1/source', 'left: on another filesystem'), items)

    def test_a_mount_below_a_moved_path_is_left_in_the_trash(self):
        for i in (1, 2, 3):
            self.prep(f'mustang-prep-10{i}-1')
        mount = self.root/'mustang-prep-101-1'/'source'/'build'/'make'/'mnt'
        mount.mkdir()
        (mount/'data').write_text('elsewhere')
        with self.stat_with(stat.ST_DEV, lambda name: name == 'mnt'):
            record = self.apply()
        self.assertEqual([p.read_text() for p in (self.root/'.reclaim-trash').rglob('data')], ['elsewhere'])
        self.assertEqual({i['reason'] for i in record['trashLeft']}, {'on another filesystem'})
        self.assertIn(('mustang-prep-101-1/source/build', 'in trash, not yet removed'),
                      {(i['path'], i['outcome']) for i in record['items']})
        self.assertFalse(record['complete'])

    def test_a_directory_swapped_between_two_looks_is_left(self):
        for i in (1, 2, 3):
            self.prep(f'mustang-prep-10{i}-1')
        before = tree(self.root/'mustang-prep-101-1'/'source')
        with self.stat_with(stat.ST_INO, lambda name: name == 'source'):
            record = self.apply('1-1-builder')
        self.assertEqual(tree(self.root/'mustang-prep-101-1'/'source'), before)
        self.assertIn(('mustang-prep-101-1/source', 'left: replaced while it was opened'),
                      {(i['path'], i['outcome']) for i in record['items']})
        with self.stat_with(stat.ST_INO, lambda name: name.startswith('0002-')):   # this apply's trash items
            record = self.apply('2-1-builder')
        self.assertEqual({i['reason'] for i in record['trashLeft']}, {'replaced while it was removed'})
        self.assertTrue(list((self.root/'.reclaim-trash').rglob('f0')))
        self.assertFalse(record['complete'])

    def test_read_only_directories_are_removed(self):
        for i in (1, 2, 3):
            self.prep(f'mustang-prep-10{i}-1')
        cache = self.root/'mustang-prep-101-1'/'source'/'out-mustang-build-2026110401'/'soong'/'gomod'
        (cache/'pkg').mkdir(parents=True)
        (cache/'pkg'/'mod.go').write_text('package x')
        (cache/'pkg').chmod(0o555)
        cache.chmod(0o555)
        modes, fchmod = [], os.fchmod

        def recording(fd, mode):
            modes.append(mode)
            fchmod(fd, mode)
        with mock.patch.object(reclaim.os, 'fchmod', recording):
            record = self.apply()
        self.assertTrue(record['complete'])
        self.assertFalse((self.root/'mustang-prep-101-1'/'source'/'out-mustang-build-2026110401').exists())
        self.assertEqual(modes, [0o755, 0o755])   # made writable by its owner first, even where root needs no write bit


class Regeneration(Base):
    def observe_allocators(self):
        rows = []
        frankel = allocate.observe(self.home, 'frankel', 'Immortal-Protocols/pixel-ota', 'https://api.invalid',
                                   fetch=lambda url: rows)
        mustang = mustang_allocate.observe(self.home, 'Immortal-Protocols/pixel-ota', 'https://api.invalid',
                                           fetch=lambda url: rows)
        return frankel['attempts'], mustang

    def test_names_stay_for_the_allocators_and_the_next_run_regenerates(self):
        for name in ('frankel-2026110403', 'frankel-2026110404-resume-41861964515', 'mustang-integrate-2026110401',
                     'mustang-reconcile-2026110401'):
            (self.root/name).mkdir()
        runs = [self.prep(f'mustang-prep-10{i}-1') for i in range(1, 4)]
        before = self.observe_allocators()
        self.apply('1-1-builder')
        self.assertEqual(self.observe_allocators(), before)
        self.assertFalse((runs[0]/'source'/'build').exists())
        regenerated = self.prep('mustang-prep-104-1')   # the next mustang-build prepares afresh
        fresh = tree(regenerated)
        self.apply('2-1-builder', current=104)
        self.assertEqual(tree(regenerated), fresh)
        self.assertEqual(sorted(os.listdir(runs[1]/'source')), ['releases'])
        self.assertEqual(self.observe_allocators(), before)


class Receipts(Base):
    def test_plan_and_result_receipts_are_sequenced_and_write_once(self):
        for i in (1, 2, 3):
            self.prep(f'mustang-prep-10{i}-1')
        first = self.apply('41-1-builder')
        second = self.apply('42-1-builder')
        names = sorted(os.listdir(self.root/'reclaim'))
        self.assertEqual(names, ['0001-41-1-builder.json', '0001-41-1-builder.plan.json', '0002-42-1-builder.json',
                                 '0002-42-1-builder.plan.json'])
        stored = json.loads((self.root/'reclaim'/'0001-41-1-builder.json').read_text())
        self.assertEqual(stored['items'], first['items'])
        self.assertEqual({i['outcome'] for i in first['items']}, {'removed'})
        self.assertIsInstance(first['availableBytesBefore'], int)
        self.assertIsInstance(first['availableBytesAfter'], int)
        self.assertEqual(second['verdict'], 'nothing-to-reclaim')
        plan = json.loads((self.root/'reclaim'/'0001-41-1-builder.plan.json').read_text())
        self.assertEqual(sorted(i['path'] for i in plan['reclaim']), sorted(i['path'] for i in first['items']))
        self.assertNotIn('items', plan)

    def test_plan_changes_nothing_and_takes_no_lock(self):
        for i in (1, 2, 3):
            self.prep(f'mustang-prep-10{i}-1')
        before = tree(self.root)
        fd = os.open(self.policy['lock'], os.O_RDWR | os.O_CREAT)
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            result = reclaim.plan(self.policy, None, self.repo, measure=True)
        finally:
            os.close(fd)
        self.assertEqual(tree(self.root), before)
        paths = {i['path']: i['bytes'] for i in result['reclaim']}
        self.assertIn('mustang-prep-101-1/downloads', paths)
        self.assertGreater(paths['mustang-prep-101-1/source/build'], 0)
        self.assertNotIn('mustang-prep-101-1/source/releases', paths)


class Cli(Base):
    def run_cli(self, *args, policy=None):
        path = policy or POLICY
        with mock.patch.object(reclaim, 'ROOT', self.repo), \
                mock.patch.object(reclaim, 'load_policy', lambda p: dict(self.policy)), \
                mock.patch('sys.stdout', new_callable=io.StringIO) as out, \
                mock.patch('sys.stderr', new_callable=io.StringIO) as err:
            code = reclaim.main([*args, '--policy', str(path)])
        return code, out.getvalue(), err.getvalue()

    def test_exit_status_by_outcome(self):
        for i in (1, 2, 3):
            self.prep(f'mustang-prep-10{i}-1')
        with mock.patch.object(reclaim, 'pinned_names', lambda policy, repo=None: set()):
            code, out, _ = self.run_cli('plan', '--root', str(self.root))
            self.assertEqual((code, json.loads(out)['verdict']), (0, 'planned'))
            code, out, _ = self.run_cli('apply', '--root', str(self.root), '--label', '7-1-builder',
                                        '--current-run', '7')
            self.assertEqual((code, json.loads(out)['verdict']), (0, 'reclaimed'))
            code, _, err = self.run_cli('apply', '--root', str(self.root), '--label', 'bad label')
            self.assertEqual((code, json.loads(err)['verdict']), (2, 'INVALID'))
            code, _, _ = self.run_cli('apply', '--root', 'relative', '--label', 'x')
            self.assertEqual(code, 2)
            code, _, _ = self.run_cli('apply', '--root', str(self.root), '--label', 'x', '--current-run', '0')
            self.assertEqual(code, 2)
            os.symlink(self.root, self.tmp/'alias')
            code, _, err = self.run_cli('apply', '--root', str(self.tmp/'alias'), '--label', 'x')
            self.assertEqual((code, json.loads(err)['verdict']), (3, 'REFUSED'))
            fd = os.open(self.policy['lock'], os.O_RDWR | os.O_CREAT)
            fcntl.flock(fd, fcntl.LOCK_EX)
            try:
                code, out, _ = self.run_cli('apply', '--root', str(self.root), '--label', 'x')
            finally:
                os.close(fd)
            self.assertEqual((code, json.loads(out)['verdict']), (0, 'busy'))

    def test_invalid_policy_file_exits_2(self):
        bad = self.tmp/'bad.json'
        bad.write_text('{"schema": "other"}')
        result = subprocess.run([sys.executable, str(ROOT/'scripts/builder-reclaim.py'), 'plan', '--policy', str(bad)],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stderr)['verdict'], 'INVALID')


class Wiring(unittest.TestCase):
    STEP = ('      - name: Reclaim regenerable build data\n'
            '        run: python3 scripts/builder-reclaim.py apply --current-run "$GITHUB_RUN_ID" '
            '--label "$GITHUB_RUN_ID-$GITHUB_RUN_ATTEMPT-{job}"\n')

    def job(self, name):
        return re.search(rf'(?ms)^  {re.escape(name)}:\n(.*?)(?=^  [\w-]+:\n|\Z)', WORKFLOW).group(1)

    def test_each_space_hungry_builder_job_reclaims_before_it_writes(self):
        for job, before in (('builder', '      - name: Candidate identity'),
                            ('app-builder', '      - run: python3 scripts/host-app-release.py --check'),
                            ('mustang-build', '      - name: Prepare, allocate, integrate and compile Mustang once')):
            text = self.job(job)
            step = self.STEP.format(job=job)
            if job == 'builder':
                step = step.replace('        run:', "        if: inputs.mode != 'latest-draft'\n        run:")
            self.assertEqual(text.count(step), 1, job)
            self.assertIn('roseos-frankel-builder', text, job)
            self.assertLess(text.index(step), text.index(before), job)
        self.assertEqual(WORKFLOW.count('scripts/builder-reclaim.py'), 3)
        self.assertNotIn('builder-reclaim.py apply --root', WORKFLOW)

    def test_the_reclaim_step_cannot_be_skipped_silently(self):
        for job in ('builder', 'app-builder', 'mustang-build'):
            text = self.job(job)
            step = text[text.index('      - name: Reclaim regenerable build data\n'):]
            step = step[:step.index('\n      - ', 1)]
            if job == 'builder':
                self.assertIn("        if: inputs.mode != 'latest-draft'\n", step)
                step = step.replace("        if: inputs.mode != 'latest-draft'\n", '')
            for unsafe in ('continue-on-error', '|| true', 'if:'):
                self.assertNotIn(unsafe, step, job)


if __name__ == '__main__':
    unittest.main()
