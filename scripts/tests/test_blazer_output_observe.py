"""BLAZER-OUTPUT-OBSERVE (scripts/blazer-output-observe.py): one bounded, read-only observation of a finished blazer
compile's outputs under the shared lock. Synthetic source trees, real ZIP files and a temporary lock only: no host,
build, signing or process; links are never followed and only allowlisted values are emitted."""
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('blazer_output_observe', ROOT/'scripts/blazer-output-observe.py')
observer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(observer)

BUILD = '2026110303'
SYSTEM_PROP = (f'ro.build.version.incremental={BUILD}\nro.build.date.utc=1793700000\n'
               f'ro.build.fingerprint=google/blazer/blazer:16/CP3A.261005.005/{BUILD}:user/release-keys\n'
               'ro.build.id=CP3A.261005.005\nro.build.type=user\nro.build.tags=release-keys\n'
               'ro.build.display.id=PRIVATE-DISPLAY\nro.secret.token=PRIVATE-TOKEN\n')
VENDOR_PROP = f'ro.vendor.build.version.incremental={BUILD}\nro.vendor.build.date.utc=1793700000\nro.vendor.extra=PRIVATE-VENDOR\n'
MISC = 'ab_update=true\nvirtual_ab=true\ndynamic_partition_list=system vendor product\navb_key_path=/PRIVATE/key\n'
INFO = 'require board=blazer\nrequire version-bootloader=bl-1|bl-2\nrequire version-baseband=bb-1\nrequire other=PRIVATE\n'
FASTBOOT = b'\x7fELF fastboot'


class Tree:
    """A synthetic isolated source tree S with OUT = S/out-blazer-build, and a lock file."""
    def __init__(self, members=None, otatools=None):
        self.tmp = Path(tempfile.mkdtemp())
        self.source = self.tmp/'source'
        self.out = self.source/'out-blazer-build'
        self.inter = self.out/observer.TARGET_FILES_DIR
        self.inter.mkdir(parents=True)
        (self.inter/'blazer-target_files').mkdir()  # the unzipped tree beside the ZIP is never read
        self.target = self.inter/f'blazer-target_files-{BUILD}.zip'
        self.write_zip(self.target, members if members is not None else {
            'SYSTEM/build.prop': SYSTEM_PROP, 'VENDOR/etc/build.prop': VENDOR_PROP, 'META/misc_info.txt': MISC,
            'OTA/android-info.txt': INFO, 'SYSTEM/app/Private.apk': 'PRIVATE-APK'})
        self.otatools = self.out/observer.OTATOOLS
        self.otatools.parent.mkdir(parents=True)
        self.write_zip(self.otatools, otatools if otatools is not None else {'bin/fastboot': FASTBOOT, 'releasetools/x.py': 'x'})
        for relative in observer.SCRIPTS:
            path = self.source/relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f'# {relative}\n')
        self.lock = self.tmp/'frankel-build.lock'
        self.lock.write_text('')

    @staticmethod
    def write_zip(path, members, method=zipfile.ZIP_DEFLATED):
        with zipfile.ZipFile(path, 'w', method) as archive:
            for name, data in members.items():
                archive.writestr(name, data)

    def observe(self, **kwargs):
        return json.loads(observer.observe('immortal-os-builder', source=self.source, out=self.out, lock=self.lock, **kwargs))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class Observe(unittest.TestCase):
    def test_a_complete_observation_reports_only_allowlisted_identity_values(self):
        tree = Tree()
        report = tree.observe()
        self.assertEqual((report['schema'], report['complete'], report['problems']), (observer.SCHEMA, True, []))
        self.assertEqual(report['lock'], {'held': True, 'path': str(tree.lock)})
        target = report['targetFiles']
        self.assertEqual((target['state'], target['path'], target['sha256'], target['size']),
                         ('present', str(tree.target), sha(tree.target), tree.target.stat().st_size))
        self.assertEqual(target['props']['SYSTEM/build.prop'], {
            'ro.build.version.incremental': BUILD, 'ro.build.date.utc': '1793700000', 'ro.build.id': 'CP3A.261005.005',
            'ro.build.fingerprint': f'google/blazer/blazer:16/CP3A.261005.005/{BUILD}:user/release-keys',
            'ro.build.type': 'user', 'ro.build.tags': 'release-keys'})
        self.assertEqual(target['props']['VENDOR/etc/build.prop'],
                         {'ro.vendor.build.version.incremental': BUILD, 'ro.vendor.build.date.utc': '1793700000'})
        self.assertEqual(target['summary'], {'incremental': [BUILD], 'dateUtc': ['1793700000']})
        self.assertEqual(target['miscInfo'], {'ab_update': 'true', 'virtual_ab': 'true', 'dynamic_partition_list': 'system vendor product'})
        self.assertEqual(target['androidInfo'], {'board': ['blazer'], 'version-bootloader': ['bl-1', 'bl-2'], 'version-baseband': ['bb-1']})
        ota = report['otatools']
        self.assertEqual((ota['state'], ota['sha256']), ('present', sha(tree.otatools)))
        self.assertEqual(ota['fastboot'], {'state': 'present', 'size': len(FASTBOOT), 'sha256': hashlib.sha256(FASTBOOT).hexdigest()})
        self.assertEqual(sorted(report['scripts']), sorted(observer.SCRIPTS))
        for relative, row in report['scripts'].items():
            self.assertEqual((row['state'], row['sha256']), ('present', sha(tree.source/relative)))
        self.assertNotIn('PRIVATE', json.dumps(report))  # nothing outside the allowlist leaves the host

    def test_the_identity_is_reported_never_judged(self):
        tree = Tree(members={'SYSTEM/build.prop': 'ro.build.version.incremental=eng.pixelbuild.20261103.155410\n',
                             'META/misc_info.txt': MISC, 'OTA/android-info.txt': INFO})
        report = tree.observe()
        self.assertTrue(report['complete'])
        self.assertEqual(report['targetFiles']['summary']['incremental'], ['eng.pixelbuild.20261103.155410'])
        self.assertIn('reviewer compares', report['gate'])

    def test_links_are_refused_at_the_step_and_never_traversed(self):
        tree = Tree()
        real = tree.out.parent/'real-out'
        tree.out.rename(real)
        tree.out.symlink_to(real)
        report = tree.observe()
        self.assertFalse(report['complete'])
        self.assertEqual((report['targetFiles']['state'], report['targetFiles']['component']), ('link', str(tree.out)))
        self.assertEqual(report['otatools']['state'], 'link')
        tree = Tree()
        moved = tree.tmp/'elsewhere.zip'
        tree.target.rename(moved)
        tree.target.symlink_to(moved)
        report = tree.observe()
        self.assertEqual(report['targetFiles']['state'], 'link')
        self.assertNotIn('sha256', report['targetFiles'])
        tree = Tree()
        script = tree.source/observer.SCRIPTS[0]
        script.unlink()
        script.symlink_to(tree.lock)
        self.assertEqual(tree.observe()['scripts'][observer.SCRIPTS[0]]['state'], 'link')

    def test_zero_or_several_target_files_are_reported_never_chosen_between(self):
        tree = Tree()
        tree.target.unlink()
        report = tree.observe()
        self.assertEqual((report['targetFiles']['state'], report['complete']), ('absent', False))
        tree = Tree()
        Tree.write_zip(tree.inter/'blazer-target_files-other.zip', {'SYSTEM/build.prop': SYSTEM_PROP})
        report = tree.observe()
        self.assertEqual(report['targetFiles']['state'], 'ambiguous')
        self.assertEqual(len(report['targetFiles']['names']), 2)
        self.assertNotIn('sha256', report['targetFiles'])

    def test_missing_corrupt_oversized_and_non_zip_inputs_are_incomplete(self):
        tree = Tree(members={'SYSTEM/build.prop': SYSTEM_PROP, 'META/misc_info.txt': MISC})
        report = tree.observe()
        self.assertIn('targetFiles: member OTA/android-info.txt absent', report['problems'])
        tree = Tree(otatools={'releasetools/x.py': 'x'})
        self.assertIn('otatools: member bin/fastboot absent', tree.observe()['problems'])
        tree = Tree()
        tree.otatools.write_bytes(b'not a zip')
        self.assertEqual(tree.observe()['otatools']['state'], 'not-zip')
        tree = Tree()
        Tree.write_zip(tree.target, {'SYSTEM/build.prop': SYSTEM_PROP, 'META/misc_info.txt': MISC,
                                     'OTA/android-info.txt': INFO}, method=zipfile.ZIP_STORED)
        data = bytearray(tree.target.read_bytes())
        at = data.index(b'ro.build.date.utc')
        data[at] ^= 0x20  # flip a byte of a stored member: its CRC no longer matches
        tree.target.write_bytes(bytes(data))
        report = tree.observe()
        self.assertEqual(report['targetFiles']['props']['SYSTEM/build.prop']['state'], 'corrupt')
        self.assertFalse(report['complete'])
        tree = Tree()
        saved = dict(observer.LIMITS)
        self.addCleanup(observer.LIMITS.update, saved)
        observer.LIMITS['memberBytes'] = 16
        report = tree.observe()
        self.assertEqual(report['targetFiles']['props']['SYSTEM/build.prop']['state'], 'too-large')
        observer.LIMITS.update(saved)
        observer.LIMITS['targetFilesBytes'] = 16
        report = tree.observe()
        self.assertEqual(report['targetFiles']['state'], 'too-large')
        self.assertNotIn('sha256', report['targetFiles'])

    def test_a_file_that_changes_or_a_deadline_is_never_reported_present(self):
        tree = Tree()
        real_read = os.read
        grown = []

        def growing(fd, n):
            block = real_read(fd, n)
            if block and not grown and os.fstat(fd).st_size == tree.target.stat().st_size:
                grown.append(1)
                with open(tree.target, 'ab') as f:
                    f.write(b'appended')
            return block
        observer.os.read = growing
        self.addCleanup(setattr, observer.os, 'read', real_read)
        report = tree.observe()
        observer.os.read = real_read
        self.assertEqual(report['targetFiles']['state'], 'changed')
        self.assertFalse(report['complete'])
        tree = Tree()
        ticks = iter([0.0] + [10000.0] * 1000)
        report = tree.observe(clock=lambda: next(ticks))
        self.assertFalse(report['complete'])
        self.assertIn('deadline: the observation exceeded its time bound', report['problems'])

    def test_a_file_that_never_ends_is_stopped_at_its_size_not_read_without_bound(self):
        tree = Tree()
        real_read = os.read
        calls = []

        def endless(fd, n):
            calls.append(1)
            if len(calls) > 10000:
                raise RuntimeError('read without bound')
            return real_read(fd, n) or b'x' * n  # past EOF the "file" keeps growing
        observer.os.read = endless
        self.addCleanup(setattr, observer.os, 'read', real_read)
        report = tree.observe()
        observer.os.read = real_read
        self.assertEqual(report['targetFiles']['state'], 'changed')
        self.assertLess(len(calls), 100)

    def test_a_held_absent_or_linked_lock_refuses_before_any_read(self):
        tree = Tree()
        with open(tree.lock) as held:
            fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
            report = tree.observe()
        self.assertEqual((report['complete'], report['lock']['held']), (False, False))
        self.assertNotIn('targetFiles', report)
        tree.lock.unlink()
        self.assertEqual(tree.observe()['lock']['reason'], 'lock absent')
        tree.lock.symlink_to(tree.target)
        self.assertIn('link', tree.observe()['lock']['reason'])

    def test_the_lock_is_held_for_the_whole_observation(self):
        tree = Tree()
        real = observer.hash_fd
        seen = []

        def probing(*args, **kwargs):
            with open(tree.lock) as other:
                try:
                    fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    seen.append('free')
                except BlockingIOError:
                    seen.append('held')
            return real(*args, **kwargs)
        observer.hash_fd = probing
        self.addCleanup(setattr, observer, 'hash_fd', real)
        self.assertTrue(tree.observe()['complete'])
        self.assertEqual(set(seen), {'held'})
        self.assertEqual(len(seen), 2 + len(observer.SCRIPTS))

    def test_the_output_is_capped(self):
        tree = Tree()
        saved = dict(observer.LIMITS)
        self.addCleanup(observer.LIMITS.update, saved)
        observer.LIMITS['outputBytes'] = 200
        report = tree.observe()
        self.assertEqual((report['complete'], report['problems']), (False, ['output: the report exceeds outputBytes']))


class Fixed(unittest.TestCase):
    def test_paths_come_only_from_the_committed_bindings(self):
        source, out = observer.paths()
        prep = json.loads((ROOT/'os/build-only/blazer-prep-host.json').read_text())
        self.assertEqual(source, Path(prep['sourceRoot']))
        self.assertEqual(out, source/json.loads((ROOT/'os/build-only/blazer.json').read_text())['compile']['outDir'])
        self.assertEqual(observer.main(['observe', '--host', 'other']), 2)
        text = (ROOT/'scripts/blazer-output-observe.py').read_text()
        body = text.split('"""', 2)[2]
        for absent in ('subprocess', 'urlopen', 'requests', 'shutil', "open('w", 'O_WRONLY', 'O_CREAT', 'unlink', 'rmtree'):
            self.assertNotIn(absent, body, absent)
        opens = re.findall(r'os\.open\([^\n]*', body)
        self.assertTrue(opens)
        self.assertTrue(all('dir_fd=' in line for line in opens), opens)  # every leaf open is relative to a walked fd


if __name__ == '__main__':
    unittest.main()
