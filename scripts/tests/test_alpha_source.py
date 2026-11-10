"""alpha_source.py on real filesystem fixtures: an independent copy with exact exclusions, the contained-link policy,
OS's real alpha patch applied to a 2804-shaped build/make, write-once staging and receipts, and every refusal leaving
the base unchanged and no task-owned partial tree behind."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1]
ROOT = SCRIPTS.parent
sys.path.insert(0, str(SCRIPTS))
import alpha_source  # noqa: E402

PATCH = ROOT/'os/patches/cuttlefish-alpha-no-updater.patch'
MEDIA = 'target/product/media_system.mk'
GENERIC = 'target/product/generic/Android.bp'
FIXTURES = ROOT/'os/tests/fixtures/cuttlefish-alpha'
CONTRACT = json.loads((ROOT/'os/alpha/cuttlefish-product-contract.json').read_text())
POLICY = {'rootNames': ['.repo', 'keys', 'releases', 'out'], 'rootPrefixes': ['out-', 'out_'], 'anywhereNames': ['.git', '.repo'],
    'externalLinks': [{'path': 'vendor/dl/unpacked/x/vendor_boot/r__unpacked/d', 'target': '/sys/kernel/debug'},
                      {'path': 'vendor/dl/unpacked/x/system/d', 'target': '/sys/kernel/debug'}]}
BINDING = {'baseCandidate': 'ae9cfd4d9fafd2db4f2bc6aa76b7d025d6473fb9', 'baseResolvedManifestSha256': 'f' * 64,
    'baseReconcileReceipt': '0004-2026102804.json', 'baseReconcileSha256': 'db1cc9a215f92a09d772c01efc15590b37fd6e748f97893908838340c9da9de0'}
DELTA = {'commit': 'f21bf68322b3fa0a8924840554ab3e9398f79125', 'changedFiles': [{'project': 'build/make', 'path': GENERIC}, {'project': 'build/make', 'path': MEDIA}],
    'patches': [{'path': 'os/patches/cuttlefish-alpha-no-updater.patch', 'project': 'build/make',
        'sha256': hashlib.sha256(PATCH.read_bytes()).hexdigest(), 'file': str(PATCH)}]}

def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def snapshot(root, directory_mtimes=True):
    """Every entry under root with its type, mode, inode, mtime and content or link text."""
    result = {}
    for directory, dirs, files in os.walk(root):
        for name in dirs + files:
            path = Path(directory)/name
            info = os.lstat(path)
            value = os.readlink(path) if stat.S_ISLNK(info.st_mode) else \
                hashlib.sha256(path.read_bytes()).hexdigest() if stat.S_ISREG(info.st_mode) else None
            mtime = info.st_mtime_ns if directory_mtimes or not stat.S_ISDIR(info.st_mode) else None
            result[str(path.relative_to(root))] = (stat.S_IFMT(info.st_mode), stat.S_IMODE(info.st_mode), info.st_ino, mtime, value)
    return result

def media_2804(directory):
    """Pinned media_system.mk (OS fixture) plus the real Rose updater patch: the retained 2804 bytes' shape."""
    target = directory/MEDIA
    target.parent.mkdir(parents=True)
    target.write_bytes((ROOT/'os/tests/fixtures/cuttlefish-alpha/build-make'/MEDIA).read_bytes())
    subprocess.run(['git', '-C', str(directory), 'apply', str(ROOT/'os/patches/updater-product-build-make.patch'), '--include', MEDIA],
        check=True, env=dict(os.environ, GIT_CEILING_DIRECTORIES=str(directory.parent)))

class PreparationTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        base = self.base = self.root/'base'
        files = {'build/make/core/main.mk': 'core', 'device/google/cuttlefish/AndroidProducts.mk': 'products',
            'frameworks/base/core/res/res/values/config.xml': '<resources/>', 'tools/run.sh': '#!/bin/sh\n',
            'packages/apps/RosePreinstall/voice.apk': 'voice apk', 'vendor/dl/unpacked/x/system/bin/sh': 'sh',
            'vendor/dl/unpacked/x/vendor_boot/r__unpacked/init': 'init', 'prebuilts/readonly/tool': 'tool',
            # excluded inputs that must never be copied
            'keys/releasekey.pk8': 'PRIVATE', '.repo/manifest.xml': 'manifest', 'releases/2804/bin/x': 'release',
            'out/soong/x': 'out', 'out-rose-first-update-2026102701/target/x': 'pixel out', 'out_adevtool_deps/x': 'deps',
            'build/make/.git/HEAD': 'ref', 'device/google/cuttlefish/.git/HEAD': 'ref', 'frameworks/base/.repo/x': 'x'}
        for path, text in files.items():
            (base/path).parent.mkdir(parents=True, exist_ok=True)
            (base/path).write_text(text)
        media_2804(base/'build/make')
        (base/'build/make'/GENERIC).parent.mkdir(parents=True)  # not integrated by 2804: the pinned upstream bytes
        (base/'build/make'/GENERIC).write_bytes((FIXTURES/'build-make'/GENERIC).read_bytes())
        os.chmod(base/'tools/run.sh', 0o755)
        os.chmod(base/'prebuilts/readonly/tool', 0o444)
        os.chmod(base/'prebuilts/readonly', 0o555)
        self.addCleanup(os.chmod, base/'prebuilts/readonly', 0o755)
        os.link(base/'tools/run.sh', base/'tools/run-again.sh')  # a shared inode inside the base
        os.symlink('../core/main.mk', base/'build/make/target/main.mk')        # relative, internal
        os.symlink(str(base/'build/make/core/main.mk'), base/'tools/main.mk')  # absolute, internal: rebased
        os.symlink('run.sh', base/'tools/chain2')
        os.symlink('chain2', base/'tools/chain1')                              # chain inside
        os.symlink('missing/fixture.txt', base/'tools/dangling')               # contained dangling fixture
        os.symlink('/sys/kernel/debug', base/'vendor/dl/unpacked/x/system/d')
        os.symlink('/sys/kernel/debug', base/'vendor/dl/unpacked/x/vendor_boot/r__unpacked/d')
        os.symlink('../.repo/projects/fw.git', base/'frameworks/base/.git')    # repo-style .git link: excluded by name
        self.reserved = self.root/'alpha-source'
        self.extra = {'target': 'aosp_cf_x86_64_only_phone-cur-userdebug', 'updaterMode': 'excluded-no-compatible-feed', 'updaterEndpoint': None,
            'preservationPaths': ['build/make/' + GENERIC, 'build/make/' + MEDIA, 'frameworks/base/core/res/res/values/config.xml', 'packages/apps/RosePreinstall/voice.apk']}

    def prepare(self, **overrides):
        args = dict(base=self.base, reserved_root=self.reserved, binding=BINDING, delta=DELTA, policy=POLICY, extra=self.extra,
            free_needed_bytes=0, budget_bytes=10**12)
        args.update(overrides)
        return alpha_source.prepare(**args)

    def assert_nothing_prepared(self):
        self.assertEqual(sorted(p.name for p in self.reserved.iterdir()) if self.reserved.exists() else [], [])

    def test_independent_copy_with_exact_exclusions_links_and_delta(self):
        before = snapshot(self.base)
        receipt = self.prepare()
        self.assertEqual(snapshot(self.base), before, 'the base is only read')
        final = self.reserved/receipt['preparationId']
        tree = final/'tree'
        self.assertEqual(sorted(p.name for p in final.iterdir()), ['manifest.tsv', 'preparation.json', 'tree'])
        self.assertEqual(json.loads((final/'preparation.json').read_text()), receipt)
        # Excluded inputs are absent; everything else is present with modes and mtimes, in new inodes.
        for absent in ('keys', '.repo', 'releases', 'out', 'out-rose-first-update-2026102701', 'out_adevtool_deps',
                       'build/make/.git', 'device/google/cuttlefish/.git', 'frameworks/base/.repo', 'frameworks/base/.git',
                       'vendor/dl/unpacked/x/system/d', 'vendor/dl/unpacked/x/vendor_boot/r__unpacked/d'):
            self.assertFalse(os.path.lexists(tree/absent), absent)
        self.assertEqual(sorted((e['path'], e['reason']) for e in receipt['excluded']), sorted([
            ('.repo', 'excluded root'), ('keys', 'excluded root'), ('releases', 'excluded root'), ('out', 'excluded root'),
            ('out-rose-first-update-2026102701', 'excluded root'), ('out_adevtool_deps', 'excluded root'),
            ('build/make/.git', 'version-control metadata'), ('device/google/cuttlefish/.git', 'version-control metadata'),
            ('frameworks/base/.repo', 'version-control metadata'), ('frameworks/base/.git', 'version-control metadata'),
            ('vendor/dl/unpacked/x/system/d', 'listed non-input link to /sys/kernel/debug'),
            ('vendor/dl/unpacked/x/vendor_boot/r__unpacked/d', 'listed non-input link to /sys/kernel/debug')]))
        for rel in ('tools/run.sh', 'tools/run-again.sh', 'prebuilts/readonly/tool', 'packages/apps/RosePreinstall/voice.apk'):
            base_info, copy_info = os.lstat(self.base/rel), os.lstat(tree/rel)
            self.assertNotEqual(copy_info.st_ino, base_info.st_ino, rel)
            self.assertEqual((stat.S_IMODE(copy_info.st_mode), copy_info.st_mtime_ns, (tree/rel).read_bytes()),
                (stat.S_IMODE(base_info.st_mode), base_info.st_mtime_ns, (self.base/rel).read_bytes()), rel)
        self.assertNotEqual(os.lstat(tree/'tools/run.sh').st_ino, os.lstat(tree/'tools/run-again.sh').st_ino, 'no shared inode in the copy')
        self.assertEqual(stat.S_IMODE(os.lstat(tree/'prebuilts/readonly').st_mode), 0o555)
        self.assertEqual(os.readlink(tree/'build/make/target/main.mk'), '../core/main.mk')
        self.assertEqual(os.readlink(tree/'tools/main.mk'), str(tree/'build/make/core/main.mk'), 'absolute internal link rebased')
        self.assertEqual((tree/'tools/chain1').read_text(), '#!/bin/sh\n')
        self.assertEqual(os.readlink(tree/'tools/dangling'), 'missing/fixture.txt')
        self.assertEqual(receipt['danglingLinks'], [{'path': 'tools/dangling', 'target': 'tools/missing/fixture.txt'}])
        self.assertEqual({k: receipt['counts'][k] for k in ('linksInternal', 'linksRebased', 'linksDangling')},
            {'linksInternal': 4, 'linksRebased': 1, 'linksDangling': 1})
        # The delta, in the copy only: the Rose Updater block leaves media_system.mk and the Seedvault closure joins the
        # Soong system image; before/after are the base and copy bytes and equal OS's contract fixture digests.
        digest = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
        self.assertEqual([(c['project'], c['path'], c['beforeSha256'], c['afterSha256']) for c in receipt['changedFiles']],
            [('build/make', path, digest(self.base/'build/make'/path), digest(tree/'build/make'/path)) for path in (GENERIC, MEDIA)])
        self.assertEqual([(c['project'], c['path'], c['beforeSha256'], c['afterSha256']) for c in receipt['changedFiles']],
            [(c['project'], c['path'], c['fixtureBeforeSha256'], c['fixtureAfterSha256']) for c in CONTRACT['osDelta']['changedFiles']])
        self.assertIn(b'"Seedvault",', (tree/'build/make'/GENERIC).read_bytes())
        self.assertEqual((tree/'build/make'/MEDIA).read_bytes(), (ROOT/'os/tests/fixtures/cuttlefish-alpha/build-make'/MEDIA).read_bytes())
        self.assertIn(b'PRODUCT_PACKAGES += Updater\n\nPRODUCT_PACKAGES += Seedvault', (self.base/'build/make'/MEDIA).read_bytes().replace(
            b'# RoseOS ships the existing Updater in every build whose channel apply-os-changes.py sets\n'
            b'# (--updater-url); OFFICIAL_BUILD stays unset. Its Android.bp pulls the required permission XML.\n', b''))
        # Identity, binding and receipt fields.
        self.assertEqual(receipt['preparationId'], alpha_source.preparation_id(BINDING, DELTA, receipt['changedFiles']))
        self.assertEqual(receipt['preparedRoot'], str(tree))
        self.assertEqual({k: receipt[k] for k in BINDING}, BINDING)
        self.assertEqual((receipt['osDeltaCommit'], receipt['updaterMode'], receipt['updaterEndpoint'], receipt['kind']),
            (DELTA['commit'], 'excluded-no-compatible-feed', None, 'rose-cuttlefish-source-preparation'))
        self.assertEqual(receipt['selectionSha256'], hashlib.sha256((final/'manifest.tsv').read_bytes()).hexdigest())
        self.assertTrue(receipt['preservationChecks']['unchangedAfterCopy'])
        self.assertEqual(alpha_source.verify_tree(tree, (final/'manifest.tsv').read_text(), receipt['changedFiles']), [])
        # Writing the copy never reaches the base, and verification sees it.
        (tree/'frameworks/base/core/res/res/values/config.xml').write_text('<resources>changed</resources>')
        (tree/'extra').write_text('x')
        self.assertEqual((self.base/'frameworks/base/core/res/res/values/config.xml').read_text(), '<resources/>')
        self.assertEqual(sorted(alpha_source.verify_tree(tree, (final/'manifest.tsv').read_text(), receipt['changedFiles'])),
            ['extra: not in the preparation manifest', 'frameworks/base/core/res/res/values/config.xml: file differs'])
        (tree/'out-rose-cf-alpha').mkdir()
        (tree/'extra').unlink()
        (tree/'frameworks/base/core/res/res/values/config.xml').write_text('<resources/>')
        self.assertEqual(alpha_source.verify_tree(tree, (final/'manifest.tsv').read_text(), receipt['changedFiles'], skip={'out-rose-cf-alpha'}), [])

    def test_escapes_loops_and_unknown_external_dependencies_stop_before_any_write(self):
        before = snapshot(self.base, directory_mtimes=False)  # the test itself adds and removes links in tools/
        cases = (
            (lambda: os.symlink('/etc/hostname', self.base/'tools/host'), 'tools/host -> /etc/hostname leaves the source tree'),
            (lambda: os.symlink('../../outside', self.base/'tools/up'), 'tools/up -> ../../outside leaves the source tree'),
            (lambda: os.symlink('../../../gone/x', self.base/'tools/up2'), 'leaves the source tree'),  # dangling, escaping
            (lambda: (os.symlink('/tmp', self.base/'tools/hop'), os.symlink('hop/x', self.base/'tools/via')), 'tools/hop -> /tmp leaves'),
            (lambda: os.symlink('../keys/releasekey.pk8', self.base/'tools/key'), 'reaches excluded keys/releasekey.pk8'),
            (lambda: (os.symlink('loop-b', self.base/'tools/loop-a'), os.symlink('loop-a', self.base/'tools/loop-b')), 'chain longer than'),
            (lambda: os.mkfifo(self.base/'tools/fifo'), 'tools/fifo: unsupported file type'),
        )
        for damage, message in cases:
            damage()
            with self.assertRaisesRegex(alpha_source.PreparationFailed, message):
                self.prepare()
            self.assert_nothing_prepared()
            for name in ('host', 'up', 'up2', 'hop', 'via', 'key', 'loop-a', 'loop-b', 'fifo'):
                if os.path.lexists(self.base/'tools'/name):
                    os.unlink(self.base/'tools'/name)
        self.assertEqual(snapshot(self.base, directory_mtimes=False), before)

    def test_external_links_must_be_exactly_the_listed_ones(self):
        os.unlink(self.base/'vendor/dl/unpacked/x/system/d')
        os.symlink('/sys/kernel', self.base/'vendor/dl/unpacked/x/system/d')
        with self.assertRaisesRegex(alpha_source.PreparationFailed, 'system/d -> /sys/kernel is not the listed /sys/kernel/debug'):
            self.prepare()
        os.unlink(self.base/'vendor/dl/unpacked/x/system/d')
        with self.assertRaisesRegex(alpha_source.PreparationFailed, 'listed external link vendor/dl/unpacked/x/system/d not found'):
            self.prepare()
        self.assert_nothing_prepared()

    def test_patched_files_keep_their_recorded_modes_under_a_restrictive_umask(self):
        # INFRA-ALPHA-094: the builder prepares with umask 0077; git apply rewrote both changed files as 0600 while the
        # copy manifest recorded 0644, and the full re-hash failed on exactly those two files.
        for path in (GENERIC, MEDIA):
            os.chmod(self.base/'build/make'/path, 0o644)
        before = snapshot(self.base)
        old = os.umask(0o077)
        try:
            receipt = self.prepare()
        finally:
            os.umask(old)
        final = self.reserved/receipt['preparationId']
        tree, manifest = final/'tree', (final/'manifest.tsv').read_text()
        self.assertEqual(alpha_source.verify_tree(tree, manifest, receipt['changedFiles']), [])
        self.assertEqual(receipt['changedFileModes'], [{'path': f'build/make/{path}', 'recordedMode': '644', 'modeAfterDelta': '600',
            'restored': True} for path in (GENERIC, MEDIA)])
        for path in (GENERIC, MEDIA):
            self.assertEqual(stat.S_IMODE(os.lstat(tree/'build/make'/path).st_mode), 0o644)
            self.assertEqual(digest(tree/'build/make'/path), next(c['afterSha256'] for c in receipt['changedFiles'] if c['path'] == path))
        self.assertEqual(stat.S_IMODE(os.lstat(tree/'tools/run.sh').st_mode), 0o755, 'other files keep their copied modes')
        self.assertEqual(snapshot(self.base), before, 'the base is only read')
        # Mode drift elsewhere, or on a changed file after preparation, is still a difference.
        os.chmod(tree/'tools/run.sh', 0o700)
        os.chmod(tree/'build/make'/MEDIA, 0o600)
        self.assertEqual(sorted(alpha_source.verify_tree(tree, manifest, receipt['changedFiles'])),
            [f'build/make/{MEDIA}: file differs', 'tools/run.sh: file differs'])

    def test_optional_cache_does_not_hide_observation_errors(self):
        boundary = dict(path='vendor/dl/unpacked', pruneMarker='vendor/dl/.find-ignore', referenceMarkers=[])
        (self.base/boundary['pruneMarker']).write_bytes(b'')
        original = os.lstat
        def denied(path, *args, **kwargs):
            if Path(path) == self.base/boundary['path']:
                raise PermissionError(13, 'fixture refusal')
            return original(path, *args, **kwargs)
        with mock.patch.object(alpha_source.os, 'lstat', side_effect=denied):
            problems = alpha_source.boundary_problems(self.base, boundary, POLICY)
        self.assertTrue(any('cannot be observed' in value for value in problems))

    def test_mode_restoration_is_limited_to_the_declared_changed_files(self):
        for path in (GENERIC, MEDIA):
            os.chmod(self.base/'build/make'/path, 0o644)
        old = os.umask(0o077)
        chmods = []
        real_chmod = os.chmod
        def record(path, mode, *args, **kwargs):
            chmods.append(str(path))
            return real_chmod(path, mode, *args, **kwargs)
        real_apply = alpha_source.apply_patch
        def apply_then_watch(directory, patch, ceiling):
            real_apply(directory, patch, ceiling)
            if 'staging' in str(directory):
                # Exercise restoration independently of whether this patch
                # version preserves copied modes under the restrictive umask.
                for path in (GENERIC, MEDIA):
                    real_chmod(Path(directory)/path, 0o600)
                chmods.clear()  # count only restoration after the controlled drift
        try:
            with mock.patch.object(alpha_source, 'apply_patch', apply_then_watch), mock.patch.object(alpha_source.os, 'chmod', record):
                receipt = self.prepare()
        finally:
            os.umask(old)
        self.assertEqual(sorted(p.split('/tree/', 1)[1] for p in chmods), sorted(f'build/make/{path}' for path in (GENERIC, MEDIA)))
        self.assertEqual([m['path'] for m in receipt['changedFileModes']], [f'build/make/{path}' for path in (GENERIC, MEDIA)])

    def test_wrong_base_for_the_delta_fails_before_copying(self):
        (self.base/'build/make'/MEDIA).write_bytes((ROOT/'os/tests/fixtures/cuttlefish-alpha/build-make'/MEDIA).read_bytes())
        with self.assertRaisesRegex(alpha_source.PreparationFailed, 'git apply --check of cuttlefish-alpha-no-updater.patch failed'):
            self.prepare()
        self.assert_nothing_prepared()
        wrong = dict(DELTA, changedFiles=[{'project': 'build/make', 'path': 'target/product/absent.mk'}])
        with self.assertRaisesRegex(alpha_source.PreparationFailed, 'absent.mk is not a regular file in the base'):
            self.prepare(delta=wrong)

    def test_reused_or_interrupted_destinations_are_refused_not_removed(self):
        receipt = self.prepare()
        with self.assertRaisesRegex(alpha_source.PreparationFailed, 'already exists; preparations are immutable'):
            self.prepare()
        final = self.reserved/receipt['preparationId']
        keep = snapshot(final)
        leftover = self.reserved/f'.staging-{receipt["preparationId"]}-deadbeef0000'
        os.rename(final, leftover)  # as if a run was killed after copying
        with self.assertRaisesRegex(alpha_source.PreparationFailed, f'interrupted preparation left .staging-{receipt["preparationId"]}-deadbeef0000'):
            self.prepare()
        self.assertEqual(snapshot(leftover), keep, 'a leftover is never removed automatically')

    def test_a_failed_copy_removes_only_its_own_staging(self):
        real, calls = alpha_source.copy_file, []
        def failing(source, destination, entry):
            calls.append(entry['path'])
            if len(calls) == 3:
                raise OSError('disk error')
            return real(source, destination, entry)
        unrelated = self.reserved/'owner-data'
        unrelated.mkdir(parents=True)
        (unrelated/'keep').write_text('keep')
        with mock.patch.object(alpha_source, 'copy_file', failing), self.assertRaises(OSError):
            self.prepare()
        self.assertEqual(sorted(p.name for p in self.reserved.iterdir()), ['owner-data'])
        self.assertEqual((unrelated/'keep').read_text(), 'keep')

    def test_base_changes_during_preparation_are_detected(self):
        real = alpha_source.copy_file
        def touch_base(source, destination, entry):
            if entry['path'] == 'tools/run.sh':
                with open(source, 'a') as f:
                    f.write('# edited\n')
            return real(source, destination, entry)
        with mock.patch.object(alpha_source, 'copy_file', touch_base), \
                self.assertRaisesRegex(alpha_source.PreparationFailed, 'tools/run.sh changed in the base during the copy'):
            self.prepare()
        self.assert_nothing_prepared()
        real_apply, applied = alpha_source.apply_patch, []
        def edit_preserved(directory, patch, ceiling):  # after the copy, while the delta is applied in staging
            applied.append(directory)
            if len(applied) == 2:
                (self.base/'frameworks/base/core/res/res/values/config.xml').write_text('<resources>owner edit</resources>')
            return real_apply(directory, patch, ceiling)
        with mock.patch.object(alpha_source, 'apply_patch', edit_preserved), \
                self.assertRaisesRegex(alpha_source.PreparationFailed, 'the base changed during preparation: frameworks/base'):
            self.prepare()
        self.assert_nothing_prepared()

    def test_copy_budget_and_post_copy_free_space_are_checked_before_copying(self):
        with self.assertRaisesRegex(alpha_source.PreparationFailed, 'above the copy budget 1'):
            self.prepare(budget_bytes=1)
        with self.assertRaisesRegex(alpha_source.PreparationFailed, 'below the .* needed for the copy budget plus the post-copy build minimum'):
            self.prepare(free_needed_bytes=10**18)
        self.assert_nothing_prepared()

if __name__ == '__main__':
    unittest.main()
