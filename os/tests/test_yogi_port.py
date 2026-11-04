"""Experimental Yogi source preparation, with upstream preimages and isolated Git fixtures.

No network, phone, vendor archive, build, signing or cloud access. Snippet fixtures
exercise patch context; they do not establish that a complete Android tree builds.
"""
import copy
import fcntl
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location('yogi_port', ROOT / 'scripts/prepare-yogi-port.py')
PORT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PORT)
FIXTURE = json.loads((ROOT / 'os/tests/fixtures/yogi-port/preimages.json').read_text())


def run_git(directory, *args):
    return subprocess.check_output(['git', '-C', str(directory), *args], text=True).strip()


class YogiPortTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.source, self.bundle = self.base / 'src', self.base / 'bundle'
        (self.source / '.repo').mkdir(parents=True)
        (self.bundle / 'os/fold-port').mkdir(parents=True)
        self.config = copy.deepcopy(json.loads((ROOT / 'os/fold-port/source.json').read_text()))
        # The large real overlay is byte-bound separately; exercise its guard with
        # a small independently committed metadata delta, including a new file.
        metadata = 'vendor-skels/google_devices/yogi/BoardConfig.mk'
        for relative, data in FIXTURE['files'].items():
            path = self.source / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(data)
            self.config['preimages'][relative] = hashlib.sha256(path.read_bytes()).hexdigest()
        manifest = ET.Element('manifest')
        for relative in self.config['projects']:
            directory = self.source / relative
            directory.mkdir(parents=True, exist_ok=True)
            (directory / 'fixture.txt').write_text('Synthetic Git revision for offline tests only.\n')
            if relative == 'vendor/adevtool':
                path = directory / metadata
                path.parent.mkdir(parents=True)
                path.write_text('# synthetic original metadata\n')
            run_git(directory, 'init', '-q')
            run_git(directory, 'add', '.')
            run_git(directory, '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                    'commit', '-qm', 'fixture')
            ET.SubElement(manifest, 'project', path=relative,
                          revision=run_git(directory, 'rev-parse', 'HEAD'))
        ET.ElementTree(manifest).write(self.bundle / 'os/pinned-manifest.xml')
        for name in self.config['patches']:
            shutil.copyfile(ROOT / 'os/fold-port' / name, self.bundle / 'os/fold-port' / name)
        adevtool = self.source / 'vendor/adevtool'
        (adevtool / metadata).write_text('# synthetic updated metadata\n')
        new = 'vendor-skels/google_devices/yogi/config.fs'
        (adevtool / new).write_text('[SYNTHETIC_FIXTURE]\nvalue: 1\n')
        run_git(adevtool, 'add', '-N', new)
        patch = subprocess.check_output(['git', '-C', str(adevtool), 'diff'])
        patch_path = 'os/fold-port/reference-fixture.patch'
        (self.bundle / patch_path).write_bytes(patch)
        self.config['vendorReference'] = {
            'patch': patch_path, 'sha256': hashlib.sha256(patch).hexdigest(),
            'postimages': {p: hashlib.sha256((adevtool / p).read_bytes()).hexdigest()
                           for p in (metadata, new)}}
        (self.bundle / 'os/fold-port/source.json').write_text(json.dumps(self.config))
        self.lock = self.base / 'builder.lock'
        self.lock.touch()

    def bytes(self):
        return {p: (self.source / p).read_bytes() for p in self.config['preimages']}

    def prepare(self, **kwargs):
        return PORT.prepare(self.source, root=self.bundle, **kwargs)

    def test_check_preserves_source_and_describes_stock_kernel_build(self):
        before = self.bytes()
        result = self.prepare()
        self.assertFalse(result['applied'])
        self.assertEqual(self.bytes(), before)
        self.assertEqual(result['build']['environment'], {'USE_STOCK_KERNEL': 'true'})
        self.assertEqual(result['build']['lunch'], 'yogi-cur-user')
        self.assertTrue(all(x['beforeSha256'] != x['afterSha256'] for x in result['files'].values()))

    def test_apply_keeps_default_allocator_and_enables_only_yogi_workarounds(self):
        result = self.prepare(apply=True, lock_file=self.lock)
        self.assertTrue(result['applied'])
        malloc = (self.source / 'external/hardened_malloc/Android.bp').read_text()
        self.assertIn('conditions_default:', malloc)
        self.assertIn('-DCONFIG_CLASS_REGION_SIZE=34359738368', malloc)
        board = (self.source / 'vendor/google_devices/yogi/BoardConfig.mk').read_text()
        self.assertIn('hardened_malloc,class_region_size,2147483648', board)
        product = (self.source / 'vendor/google_devices/yogi/yogi.mk').read_text()
        self.assertIn('persist.wm.debug.pixel_agent_tray=1', product)
        self.assertIn('debug.inc.disable_dataloader=true', product)
        selinux = (self.source / 'external/selinux/libselinux/src/android/android_device.c').read_text()
        self.assertIn('strcmp(device, "yogi") != 0', selinux)
        self.assertIn('errno != ENOENT', selinux)
        self.assertIn('selinux_android_setcon(ctx_str)', selinux)
        for relative, row in result['files'].items():
            self.assertEqual(hashlib.sha256((self.source / relative).read_bytes()).hexdigest(),
                             row['afterSha256'])

    def test_vendor_drift_is_refused_before_any_change(self):
        board = self.source / 'vendor/google_devices/yogi/BoardConfig.mk'
        board.write_text(board.read_text() + '# owner change\n')
        before = self.bytes()
        with self.assertRaisesRegex(PORT.Refused, 'pinned preimage'):
            self.prepare(apply=True, lock_file=self.lock)
        self.assertEqual(self.bytes(), before)

    def test_dirty_project_is_preserved(self):
        path = self.source / 'external/hardened_malloc/Android.bp'
        path.write_text(path.read_text() + '// owner change\n')
        before = self.bytes()
        with self.assertRaisesRegex(PORT.Refused, 'Local changes'):
            self.prepare(apply=True, lock_file=self.lock)
        self.assertEqual(self.bytes(), before)

    def test_wrong_revision_is_refused(self):
        project = self.source / 'external/hardened_malloc'
        run_git(project, '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                'commit', '--allow-empty', '-qm', 'another revision')
        with self.assertRaisesRegex(PORT.Refused, 'Wrong upstream revision'):
            self.prepare()

    def test_unreviewed_adevtool_changes_are_refused(self):
        (self.source / 'vendor/adevtool/owner.txt').write_text('preserve this\n')
        before = self.bytes()
        with self.assertRaisesRegex(PORT.Refused, 'Local adevtool changes differ'):
            self.prepare(apply=True, lock_file=self.lock)
        self.assertEqual(self.bytes(), before)

    def test_vendor_reference_drift_is_refused(self):
        relative = next(iter(self.config['vendorReference']['postimages']))
        (self.source / 'vendor/adevtool' / relative).write_text('different metadata\n')
        with self.assertRaisesRegex(PORT.Refused, 'reference bytes differ'):
            self.prepare()

    def test_vendor_reference_patch_identity_is_checked(self):
        (self.bundle / self.config['vendorReference']['patch']).write_text('wrong patch\n')
        with self.assertRaisesRegex(PORT.Refused, 'patch identity differs'):
            self.prepare()

    def test_real_vendor_reference_patch_is_byte_bound_and_yogi_scoped(self):
        reference = json.loads((ROOT / 'os/fold-port/source.json').read_text())['vendorReference']
        patch = (ROOT / reference['patch']).read_bytes()
        self.assertEqual(hashlib.sha256(patch).hexdigest(), reference['sha256'])
        paths = {line.split(' b/', 1)[1] for line in patch.decode().splitlines()
                 if line.startswith('diff --git ')}
        self.assertEqual(paths, set(reference['postimages']))
        self.assertTrue(all(p.startswith('vendor-skels/google_devices/yogi/') or
                            p == 'vendor-specs/google_devices/yogi.yml' for p in paths))

    def test_another_device_cannot_supply_vendor_files(self):
        (self.source / 'vendor/google_devices/yogi').rename(self.source / 'vendor/google_devices/rango')
        with self.assertRaisesRegex(PORT.Refused, 'Missing source file'):
            self.prepare()

    def test_missing_or_held_lock_prevents_apply(self):
        before = self.bytes()
        with self.assertRaisesRegex(PORT.Refused, 'requires --lock-file'):
            self.prepare(apply=True)
        with self.lock.open('r+') as held:
            fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(PORT.Refused, 'lock is held'):
                self.prepare(apply=True, lock_file=self.lock)
        self.assertEqual(self.bytes(), before)

    def test_symlink_is_refused(self):
        path = self.source / 'vendor/google_devices/yogi/BoardConfig.mk'
        original = self.base / 'board.mk'
        path.rename(original)
        path.symlink_to(original)
        with self.assertRaisesRegex(PORT.Refused, 'Symlink'):
            self.prepare()

    def test_full_upstream_fixtures_match_recorded_preimages(self):
        config = json.loads((ROOT / 'os/fold-port/source.json').read_text())
        for row in FIXTURE['provenance']:
            self.assertEqual(row['sourceSha256'], config['preimages'][row['sourcePath']])
            if row['kind'] == 'full':
                self.assertEqual(hashlib.sha256(FIXTURE['files'][row['sourcePath']].encode()).hexdigest(),
                                 row['sourceSha256'])


if __name__ == '__main__':
    unittest.main()
