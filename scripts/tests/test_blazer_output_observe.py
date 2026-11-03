"""BLAZER-OUTPUT-OBSERVE (scripts/blazer-output-observe.py): one bounded, read-only observation of a finished blazer
compile's outputs under the shared lock. Synthetic source trees, real ZIP files and a temporary lock only: no host,
build, signing or process; links are never followed and only allowlisted values are emitted."""
import fcntl
import hashlib
import signal
import struct
import threading
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
NEXT = '2026110401'  # a later build: its compile writes out-blazer-build-<build> (F1/F2)
SYSTEM_PROP = (f'ro.build.version.incremental={BUILD}\nro.build.date.utc=1793700000\n'
               f'ro.build.fingerprint=google/blazer/blazer:16/CP3A.261005.005/{BUILD}:user/release-keys\n'
               'ro.build.id=CP3A.261005.005\nro.build.type=user\nro.build.tags=release-keys\n'
               'ro.build.display.id=PRIVATE-DISPLAY\nro.secret.token=PRIVATE-TOKEN\n')
VENDOR_PROP = (f'ro.vendor.build.version.incremental={BUILD}\nro.vendor.build.date.utc=1793700000\n'
               'ro.product.vendor.device=blazer\nro.vendor.extra=PRIVATE-VENDOR\n')
MISC = ('ab_update=true\nvirtual_ab=true\ndynamic_partition_list=system vendor product\navb_key_path=/PRIVATE/key\n'
        'avb_system_add_hashtree_footer_args=--key /PRIVATE/avb.pem --prop com.android.build.system.fingerprint:'
        f'google/blazer/blazer:16/CP3A.261005.005/{BUILD}:user/test-keys\n')
INFO = ('require board=blazer\nrequire version-bootloader=bl-1|bl-2\nrequire version-baseband=bb-1\n'
        'require partition-exists=vendor_kernel_boot\nrequire other=PRIVATE\n')
AB = 'system\nvendor\nproduct\nabl\nmodem\nboot\n'
FASTBOOT = b'\x7fELF fastboot'
GENERATOR = {'device/common/generate-factory-images-common.sh': b'# generator\n', 'device/common/clear-factory-images-variables.sh': b'# clear\n'}


TEMPS = []


def tearDownModule():
    for temp in TEMPS:
        temp.cleanup()


def bindings(source, build, out_dir, template='out-blazer-build-{build}', **change):
    """A temporary repository root holding only the three committed bindings paths() reads."""
    TEMPS.append(tempfile.TemporaryDirectory())
    root = Path(TEMPS[-1].name)
    (root/'os/build-only').mkdir(parents=True)
    (root/'os/build-only/blazer-prep-host.json').write_text(json.dumps({'sourceRoot': str(source)}))
    (root/'os/build-only/blazer.json').write_text(json.dumps({'compile': {'outDirTemplate': template}}))
    target = {'schemaVersion': 1, 'kind': observer.TARGET_KIND, 'basis': 'test', 'build': build, 'outDir': out_dir, **change}
    (root/observer.TARGET).write_text(json.dumps({k: v for k, v in target.items() if v is not ...}))
    return root


class Tree:
    """A synthetic isolated source tree S with OUT = S/<out_name> (default the legacy out-blazer-build), and a lock file
    (removed after the module)."""
    def __init__(self, members=None, otatools=None, out_name='out-blazer-build'):
        TEMPS.append(tempfile.TemporaryDirectory())
        self.tmp = Path(TEMPS[-1].name)
        self.source = self.tmp/'source'
        self.out = self.source/out_name
        self.inter = self.out/observer.TARGET_FILES_DIR
        self.inter.mkdir(parents=True)
        (self.inter/'blazer-target_files').mkdir()  # the unzipped tree beside the ZIP is never read
        self.target = self.inter/f'blazer-target_files-{BUILD}.zip'
        self.write_zip(self.target, members if members is not None else {
            'SYSTEM/build.prop': SYSTEM_PROP, 'VENDOR/etc/build.prop': VENDOR_PROP, 'META/misc_info.txt': MISC,
            'META/ab_partitions.txt': AB, 'OTA/android-info.txt': INFO, 'SYSTEM/app/Private.apk': 'PRIVATE-APK',
            'IMAGES/boot.img': 'boot',
            'RADIO/bootloader.img': 'bootloader bytes', 'RADIO/radio.img': 'radio bytes',
            'VENDOR/firmware/dauntless/evt.ec.bin': 'PRIVATE-GSC-BYTES', 'VENDOR/firmware/dauntless/nested/x.bin': 'n',
            'IMAGES/system.img': 'system', 'IMAGES/vendor.img': 'vendor'})
        self.otatools = self.out/observer.OTATOOLS
        self.otatools.parent.mkdir(parents=True)
        self.write_zip(self.otatools, otatools if otatools is not None else {
            'bin/fastboot': FASTBOOT, 'releasetools/x.py': 'x', 'bin/simg2img': 'simg2img', 'bin/lpunpack': 'lpunpack', **GENERATOR})
        for relative in observer.VENDOR_BOOT.values():
            (self.out/relative).parent.mkdir(parents=True, exist_ok=True)
            (self.out/relative).write_bytes(observer.VENDOR_BOOT_MAGIC + b'\0' * 56)
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
                         {'ro.vendor.build.version.incremental': BUILD, 'ro.vendor.build.date.utc': '1793700000',
                          'ro.product.vendor.device': 'blazer'})
        self.assertEqual(target['summary'], {'incremental': [BUILD], 'dateUtc': ['1793700000']})
        self.assertEqual(target['miscInfo'], {'ab_update': 'true', 'virtual_ab': 'true', 'dynamic_partition_list': 'system vendor product',
            'fingerprints': [{'name': 'com.android.build.system.fingerprint',
                              'value': f'google/blazer/blazer:16/CP3A.261005.005/{BUILD}:user/test-keys'}]})
        # factory-package.py android_info semantics: one raw value per line, never split on '|'
        self.assertEqual(target['androidInfo'], {'board': ['blazer'], 'version-bootloader': ['bl-1|bl-2'], 'version-baseband': ['bb-1'],
                                                 'partition-exists': ['vendor_kernel_boot']})
        self.assertEqual(target['abPartitions'], {'names': ['system', 'vendor', 'product', 'abl', 'modem', 'boot'], 'count': 6,
                                                  'truncated': False})
        ota = report['otatools']
        self.assertEqual((ota['state'], ota['sha256']), ('present', sha(tree.otatools)))
        self.assertEqual(ota['fastboot'], {'state': 'present', 'size': len(FASTBOOT), 'sha256': hashlib.sha256(FASTBOOT).hexdigest()})
        for name in observer.GENERATOR:
            self.assertEqual(ota[f'generator:{name}']['sha256'], hashlib.sha256(GENERATOR[f'device/common/{name}']).hexdigest())
        pins = json.loads((ROOT/'os/flavors/blazer.json').read_text())['inventory']['generatorFixture']['files']
        self.assertEqual(ota['generatorPins'], {name: pins[name] for name in observer.GENERATOR})
        self.assertEqual({k: v['state'] for k, v in ota['binaries'].items()},
                         {'bin/simg2img': 'present', 'bin/lpunpack': 'present', 'bin/fsck.erofs': 'absent'})
        for key, relative in observer.VENDOR_BOOT.items():
            row = report['vendorBoot'][key]
            self.assertEqual((row['state'], row['sha256'], row['vndrbootMagic']), ('present', sha(tree.out/relative), True))
        self.assertEqual(sorted(report['scripts']), sorted(observer.SCRIPTS))
        for relative, row in report['scripts'].items():
            self.assertEqual((row['state'], row['sha256']), ('present', sha(tree.source/relative)))
        factory = target['factoryInputs']
        self.assertEqual({k: v['state'] for k, v in factory['firmware'].items()}, {'bootloader': 'present', 'radio': 'present'})
        self.assertEqual(factory['firmware']['radio']['size'], len('radio bytes'))
        self.assertEqual(factory['gscFirmware']['source'], 'VENDOR/firmware/dauntless/')
        self.assertEqual([(m['name'], m['nested']) for m in factory['gscFirmware']['members']],
                         [('evt.ec.bin', False), ('nested/x.bin', True)])
        self.assertEqual(factory['dynamicImagesMissing'], ['product'])  # misc_info lists product; no IMAGES/product.img
        self.assertEqual(factory['withoutImage'], ['abl', 'modem'])  # A/B, not dynamic, no IMAGES/<part>.img: firmware-carried
        flavor = json.loads((ROOT/'os/flavors/blazer.json').read_text())
        self.assertEqual(factory['flavorPins'], {'board': flavor['device']['board'], 'bootloader': flavor['firmware']['bootloader'],
                                                 'baseband': flavor['firmware']['baseband'],
                                                 'partitionExists': flavor['firmware']['partitionExists']})
        self.assertNotIn('PRIVATE', json.dumps(report))  # nothing outside the allowlist leaves the host: no member content

    def test_absent_factory_inputs_are_findings_not_observation_failures(self):
        tree = Tree(members={'SYSTEM/build.prop': SYSTEM_PROP, 'META/misc_info.txt': MISC, 'META/ab_partitions.txt': AB,
                             'OTA/android-info.txt': INFO})
        report = tree.observe()
        self.assertTrue(report['complete'])
        factory = report['targetFiles']['factoryInputs']
        self.assertEqual(factory['firmware'], {'bootloader': {'state': 'absent'}, 'radio': {'state': 'absent'}})
        self.assertEqual((factory['gscFirmware']['count'], factory['dynamicImagesMissing']), (0, ['system', 'vendor', 'product']))
        self.assertEqual(factory['withoutImage'], ['abl', 'modem', 'boot'])

    def test_the_committed_target_is_observed_and_reported_never_another_directory(self):
        for out_name, build, form in (('out-blazer-build', BUILD, 'legacy'), (f'out-blazer-build-{NEXT}', NEXT, 'per-build')):
            with self.subTest(form=form):
                tree = Tree(out_name=out_name)
                report = json.loads(observer.observe('immortal-os-builder', root=bindings(tree.source, build, out_name),
                                                     lock=tree.lock))
                self.assertEqual((report['complete'], report['problems']), (True, []))
                self.assertEqual((report['outDir'], report['target']), (str(tree.out), {'build': build, 'outDir': out_name,
                                                                                         'form': form}))
                self.assertIn('target.build', report['gate'])
                self.assertEqual(report['targetFiles']['sha256'], sha(tree.target))
        # Another build's (or the legacy) output beside the target is never observed in the target's place.
        tree = Tree(out_name=f'out-blazer-build-{NEXT}')
        later = '2026110402'
        report = json.loads(observer.observe('immortal-os-builder', root=bindings(tree.source, later, f'out-blazer-build-{later}'),
                                             lock=tree.lock))
        self.assertFalse(report['complete'])
        self.assertEqual(report['outDir'], str(tree.source/f'out-blazer-build-{later}'))
        self.assertEqual(report['targetFiles']['state'], 'absent')
        self.assertEqual(report['target']['build'], later)
        report = json.loads(observer.observe('immortal-os-builder', root=bindings(tree.source, NEXT, 'out-blazer-build'),
                                             lock=tree.lock))
        self.assertEqual((report['complete'], report['targetFiles']['state']), (False, 'absent'))
        # An unusable target is invalid usage (exit 2), decided under the lock and released.
        root = bindings(tree.source, NEXT, f'out-blazer-build-{BUILD}')
        with self.assertRaises(ValueError):
            observer.observe('immortal-os-builder', root=root, lock=tree.lock)
        fd = os.open(tree.lock, os.O_RDONLY)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)  # the lock was released
        finally:
            os.close(fd)

    def test_the_identity_is_reported_never_judged(self):
        tree = Tree(members={'SYSTEM/build.prop': 'ro.build.version.incremental=eng.pixelbuild.20261103.155410\n',
                             'META/misc_info.txt': MISC, 'META/ab_partitions.txt': AB, 'OTA/android-info.txt': INFO})
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
        tree = Tree(members={'SYSTEM/build.prop': SYSTEM_PROP, 'META/misc_info.txt': MISC, 'OTA/android-info.txt': INFO})
        report = tree.observe()  # factory-package.py observe raises without META/ab_partitions.txt
        self.assertIn('targetFiles: member META/ab_partitions.txt absent', report['problems'])
        self.assertFalse(report['complete'])
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
        self.assertEqual(len(seen), 2 + len(observer.VENDOR_BOOT) + len(observer.SCRIPTS))

    def test_the_output_is_capped(self):
        tree = Tree()
        saved = dict(observer.LIMITS)
        self.addCleanup(observer.LIMITS.update, saved)
        observer.LIMITS['outputBytes'] = 200
        report = tree.observe()
        self.assertEqual((report['complete'], report['problems']), (False, ['output: the report exceeds outputBytes']))


def patched(test, owner, name, value):
    original = getattr(owner, name)
    setattr(owner, name, value)
    test.addCleanup(setattr, owner, name, original)
    return original


def limits(test, **change):
    saved = dict(observer.LIMITS)
    test.addCleanup(observer.LIMITS.update, saved)
    observer.LIMITS.update(change)


class Guards(unittest.TestCase):
    """One test per guard: deleting that guard alone fails its test."""
    def test_a_directory_where_the_target_files_should_be_is_not_regular(self):
        tree = Tree()
        tree.target.unlink()
        tree.target.mkdir()
        self.assertEqual(tree.observe()['targetFiles']['state'], 'not-regular')

    def test_an_inode_swapped_between_lstat_and_open_is_changed(self):
        tree = Tree()
        real = os.fstat
        def other_inode(fd):
            st = real(fd)
            if stat_is_target(st, tree):
                return os.stat_result((st.st_mode, st.st_ino + 1) + tuple(st)[2:])
            return st
        patched(self, observer.os, 'fstat', other_inode)
        self.assertEqual(tree.observe()['targetFiles']['state'], 'changed')

    def test_a_same_size_rewrite_with_mtime_restored_is_changed(self):
        tree = Tree()
        real = observer.hash_fd
        def rewrite_then_hash(fd, before, cap, budget):
            digest = real(fd, before, cap, budget)
            if before.st_size == tree.target.stat().st_size and before.st_ino == tree.target.stat().st_ino:
                data = bytearray(tree.target.read_bytes())
                data[-1] ^= 0
                with open(tree.target, 'r+b') as f:
                    f.write(bytes(data))  # same bytes, same size: only ctime moves
                os.utime(tree.target, ns=(before.st_atime_ns, before.st_mtime_ns))
            return digest
        patched(self, observer, 'hash_fd', rewrite_then_hash)
        self.assertEqual(tree.observe()['targetFiles']['state'], 'changed')

    def test_bzip2_lzma_and_encrypted_members_are_never_read(self):
        tree = Tree()
        Tree.write_zip(tree.target, {'SYSTEM/build.prop': SYSTEM_PROP, 'META/misc_info.txt': MISC,
                                     'OTA/android-info.txt': INFO}, method=zipfile.ZIP_BZIP2)
        report = tree.observe()
        self.assertEqual(report['targetFiles']['props']['SYSTEM/build.prop'], {'state': 'unsupported-compression', 'method': 12})
        self.assertFalse(report['complete'])
        tree = Tree()
        Tree.write_zip(tree.target, {'SYSTEM/build.prop': SYSTEM_PROP, 'META/misc_info.txt': MISC,
                                     'OTA/android-info.txt': INFO}, method=zipfile.ZIP_STORED)
        data = bytearray(tree.target.read_bytes())
        for signature, offset in ((b'PK\x03\x04', 6), (b'PK\x01\x02', 8)):  # set the encryption flag in both headers
            at = data.index(signature)
            data[at + offset] |= 0x1
        tree.target.write_bytes(bytes(data))
        self.assertEqual(tree.observe()['targetFiles']['props']['SYSTEM/build.prop']['state'], 'encrypted')

    def test_an_oversized_member_is_never_opened(self):
        tree = Tree()
        limits(self, memberBytes=16)
        opened = []
        real = zipfile.ZipFile.open
        def spy(archive, name, *args, **kwargs):
            opened.append(getattr(name, 'filename', name))
            return real(archive, name, *args, **kwargs)
        patched(self, zipfile.ZipFile, 'open', spy)
        report = tree.observe()
        self.assertEqual(report['targetFiles']['props']['SYSTEM/build.prop']['state'], 'too-large')
        self.assertNotIn('SYSTEM/build.prop', opened)

    def test_the_central_directory_is_bounded_before_it_is_parsed(self):
        tree = Tree()
        with zipfile.ZipFile(tree.otatools) as o, zipfile.ZipFile(tree.target) as t:
            between = len(o.infolist())  # otatools fits; the larger target-files does not
            self.assertLess(between, len(t.infolist()))
        limits(self, zipEntries=between)
        parsed = []
        real = zipfile.ZipFile.__init__
        def spy(self_, *args, **kwargs):
            parsed.append(1)
            return real(self_, *args, **kwargs)
        patched(self, zipfile.ZipFile, '__init__', spy)
        report = tree.observe()
        self.assertEqual(report['targetFiles']['state'], 'too-many-entries')
        self.assertEqual((report['otatools']['state'], len(parsed)), ('present', 1))  # only the otatools ZIP was parsed
        tree = Tree()
        limits(self, zipEntries=200000, centralDirectoryBytes=64)
        self.assertEqual(tree.observe()['targetFiles']['state'], 'central-directory-too-large')

    def test_an_end_record_that_under_reports_entries_is_still_caught_after_parsing(self):
        tree = Tree()
        data = bytearray(tree.target.read_bytes())
        at = data.rindex(b'PK\x05\x06')
        struct.pack_into('<HH', data, at + 8, 1, 1)  # entries on this disk / total: claim 1
        tree.target.write_bytes(bytes(data))
        with zipfile.ZipFile(tree.otatools) as o:
            limits(self, zipEntries=len(o.infolist()))
        self.assertEqual(tree.observe()['targetFiles']['state'], 'too-many-entries')

    def test_duplicate_member_names_are_refused(self):
        tree = Tree()
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter('ignore')
            with zipfile.ZipFile(tree.target, 'w') as archive:
                for name, data in (('SYSTEM/build.prop', SYSTEM_PROP), ('SYSTEM/build.prop', 'ro.build.version.incremental=EVIL\n'),
                                   ('META/misc_info.txt', MISC), ('OTA/android-info.txt', INFO)):
                    archive.writestr(name, data)
        self.assertEqual(tree.observe()['targetFiles']['state'], 'duplicate-members')

    def test_a_corrupt_deflate_stream_is_a_reported_row_not_a_crash(self):
        tree = Tree()
        data = bytearray(tree.target.read_bytes())
        at = data.index(b'PK\x03\x04')
        name_len, extra_len = struct.unpack_from('<HH', data, at + 26)
        start = at + 30 + name_len + extra_len
        for i in range(start, start + 8):
            data[i] ^= 0xFF  # garble the first deflate bytes of the first member
        tree.target.write_bytes(bytes(data))
        report = tree.observe()
        first = report['targetFiles']['props'].get('SYSTEM/build.prop') or {}
        self.assertEqual(first.get('state'), 'corrupt')
        self.assertIsInstance(report, dict)

    def test_the_target_is_exactly_the_legacy_dir_or_the_builds_own_dir(self):
        source = Path('/srv/source')
        for build, out_dir, form in ((NEXT, f'out-blazer-build-{NEXT}', 'per-build'), (BUILD, 'out-blazer-build', 'legacy'),
                                     (NEXT, 'out-blazer-build', 'legacy')):
            with self.subTest(out_dir=out_dir, build=build):
                self.assertEqual(observer.paths(bindings(source, build, out_dir)),
                                 (source, source/out_dir, {'build': build, 'outDir': out_dir, 'form': form}))
        refused = [  # (build, outDir, bindings() changes: target keys, or template=)
            (NEXT, f'out-blazer-build-{BUILD}', {}),  # another build's own directory, never this target's
            (NEXT, f'out-blazer-build-{NEXT}/', {}), (NEXT, f'/srv/source/out-blazer-build-{NEXT}', {}),
            *((NEXT, bad, {}) for bad in ('..', '.', '.hidden', 'a/b', '', 'out-blazer-build/..', None, 5)),
            *((bad, 'out-blazer-build', {}) for bad in (int(NEXT), '0026110401', '202611040', '2026110401x', '', None)),
            (NEXT, 'out-blazer-build', {'extra': 1}), (NEXT, 'out-blazer-build', {'basis': ...}),
            (NEXT, 'out-blazer-build', {'schemaVersion': 2}), (NEXT, 'out-blazer-build', {'kind': 'other'}),
            # a template that is not exactly one {build} field naming one plain directory
            *((NEXT, f'out-blazer-build-{NEXT}', {'template': bad}) for bad in (
                'out-blazer-build', '{build}-{build}', 'out-{build}-{other}', 'out-{build:>12}', 'out-{build!r}',
                'out/{build}', '../{build}', 'out-{build}}', None, 5)),
            # ...including when outDir is exactly what that malformed template renders
            (NEXT, f'{NEXT}-{NEXT}', {'template': '{build}-{build}'}), (NEXT, 'out-blazer-build', {'template': 'out-blazer-build'}),
            (NEXT, f'out/{NEXT}', {'template': 'out/{build}'}), (NEXT, f'../{NEXT}', {'template': '../{build}'}),
            (NEXT, f'.{NEXT}', {'template': '.{build}'})]
        for build, out_dir, change in refused:
            with self.subTest(build=build, out_dir=out_dir, change=change):
                with self.assertRaises(ValueError):
                    observer.paths(bindings(source, build, out_dir, **change))
        root = bindings(source, NEXT, 'out-blazer-build')
        (root/'os/build-only/blazer.json').write_text(json.dumps({'compile': {'outDir': 'out-blazer-build'}}))
        with self.assertRaises(KeyError):  # the pre-F1/F2 recipe form names no template: never guessed
            observer.paths(root)
        for bad in ('srv/source', '/srv/../source'):
            with self.subTest(source=bad), self.assertRaises(ValueError):
                observer.paths(bindings(Path(bad), NEXT, 'out-blazer-build'))
        root = bindings(source, NEXT, 'out-blazer-build')
        for text in ('[]', json.dumps(sorted(observer.TARGET_KEYS)), 'null', '"out-blazer-build"'):
            with self.subTest(target=text), self.assertRaises(ValueError):  # a list of the key names is no target either
                (root/observer.TARGET).write_text(text)
                observer.paths(root)

    def test_values_are_printable_ascii_within_value_chars(self):
        tree = Tree(members={'SYSTEM/build.prop': 'ro.build.version.incremental=2026110303\x0bro.build.id=EVIL\n'
                                                  'ro.build.fingerprint=' + 'x' * 600 + '\n'
                                                  'ro.build.type=us\x01er\n',
                             'META/misc_info.txt': MISC, 'OTA/android-info.txt': INFO})
        props = tree.observe()['targetFiles']['props']['SYSTEM/build.prop']
        self.assertEqual(props['ro.build.version.incremental'], '<non-printable>')  # one line: \x0b never splits it
        self.assertNotIn('ro.build.id', props)
        self.assertTrue(props['ro.build.fingerprint'].endswith('<truncated>'))
        self.assertEqual(len(props['ro.build.fingerprint']), observer.VALUE_CHARS + len('<truncated>'))
        self.assertEqual(props['ro.build.type'], '<non-printable>')

    def test_the_printed_report_including_its_newline_fits_the_receipt_cap(self):
        tree = Tree()
        fixed = lambda: 100.0  # noqa: E731 - a constant elapsedSeconds keeps the report length stable
        text = lambda: observer.observe('immortal-os-builder', source=tree.source, out=tree.out, lock=tree.lock, clock=fixed)
        limits(self, outputBytes=9999)  # the same digit count as the limits below, so the report length does not move
        size = len(text().encode())
        self.assertLess(size, 9999)
        limits(self, outputBytes=size)  # print() adds a newline: size + 1 bytes would exceed the receipt cap
        self.assertEqual(json.loads(text())['problems'], ['output: the report exceeds outputBytes'])
        limits(self, outputBytes=size + 1)
        self.assertTrue(json.loads(text())['complete'])

    def test_a_fifo_at_the_lock_path_refuses_without_blocking(self):
        tree = Tree()
        tree.lock.unlink()
        os.mkfifo(tree.lock)
        result = []
        worker = threading.Thread(target=lambda: result.append(tree.observe()), daemon=True)
        worker.start()
        worker.join(10)
        self.assertFalse(worker.is_alive(), 'the observer blocked on a FIFO lock')
        self.assertEqual(result[0]['lock'], {'held': False, 'reason': 'lock is not a regular file'})

    def test_an_os_error_in_one_section_is_that_sections_row(self):
        tree = Tree()
        def broken(out, budget):
            raise PermissionError('denied')
        patched(self, observer, 'observe_otatools', broken)
        report = tree.observe()
        self.assertEqual(report['otatools'], {'state': 'error', 'error': 'PermissionError'})
        self.assertEqual(report['targetFiles']['state'], 'present')
        self.assertIn('otatools: error PermissionError', report['problems'])

    def test_an_unreadable_real_directory_is_an_error_not_a_type(self):
        tree = Tree()
        real = os.open
        def denied(path, flags, *args, dir_fd=None, **kwargs):
            if path == 'target_files_intermediates' and dir_fd is not None:
                raise PermissionError(13, 'denied')
            return real(path, flags, *args, dir_fd=dir_fd, **kwargs)
        patched(self, observer.os, 'open', denied)
        row = tree.observe()['targetFiles']
        self.assertEqual((row['state'], row.get('target')), ('error', 'EACCES'))

    def test_name_and_prop_member_caps(self):
        tree = Tree()
        limits(self, nameEntries=1)  # the ZIP and the unzipped tree: two names
        self.assertEqual(tree.observe()['targetFiles']['state'], 'too-many-names')
        tree = Tree()
        limits(self, nameEntries=4096, propMembers=1)
        self.assertIn('targetFiles: 2 build.prop members exceed propMembers', tree.observe()['problems'])

    def test_a_swapped_inode_is_never_read(self):
        tree = Tree()
        real = os.fstat
        def other_inode(fd):
            st = real(fd)
            return os.stat_result((st.st_mode, st.st_ino + 1) + tuple(st)[2:]) if stat_is_target(st, tree) else st
        hashed = []
        real_hash = observer.hash_fd
        def spy(fd, before, cap, budget):
            hashed.append(before.st_ino)
            return real_hash(fd, before, cap, budget)
        patched(self, observer.os, 'fstat', other_inode)
        patched(self, observer, 'hash_fd', spy)
        self.assertEqual(tree.observe()['targetFiles']['state'], 'changed')
        self.assertNotIn(tree.target.stat().st_ino, hashed)  # refused at open: not one byte of it was hashed

    def test_nothing_is_read_once_the_deadline_has_passed(self):
        tree = Tree()
        reads = []
        real_read = os.read
        def counting(fd, n):
            reads.append(fd)
            return real_read(fd, n)
        patched(self, observer.os, 'read', counting)
        ticks = iter([0.0] + [10000.0] * 1000)
        report = tree.observe(clock=lambda: next(ticks))
        self.assertEqual((report['targetFiles']['state'], 'sha256' in report['targetFiles']), ('deadline', False))
        self.assertEqual(reads, [])

    def test_every_zip_handle_is_closed_explicitly(self):
        import gc, warnings
        tree = Tree()
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter('always')
            tree.observe()
            gc.collect()
        self.assertFalse([w for w in caught if issubclass(w.category, ResourceWarning)])

    def test_a_special_file_at_the_lock_path_is_never_opened(self):
        tree = Tree()
        tree.lock.unlink()
        os.mkfifo(tree.lock)
        opened = []
        real = os.open
        def spy(path, flags, *args, dir_fd=None, **kwargs):
            opened.append(path)
            return real(path, flags, *args, dir_fd=dir_fd, **kwargs)
        patched(self, observer.base.os, 'open', spy)
        self.assertEqual(tree.observe()['lock']['reason'], 'lock is not a regular file')
        self.assertNotIn(tree.lock.name, opened)

    def test_a_symlink_zip_entry_is_never_hashed_as_the_file(self):
        tree = Tree()
        with zipfile.ZipFile(tree.otatools, 'w') as archive:
            link = zipfile.ZipInfo('bin/fastboot')
            link.external_attr = (0o120777 << 16)  # S_IFLNK: its "content" is a link target
            archive.writestr(link, '/usr/bin/fastboot')
            for name, data in GENERATOR.items():
                archive.writestr(name, data)
        self.assertEqual(tree.observe()['otatools']['fastboot'], {'state': 'link-entry'})

    def test_a_vendor_boot_image_without_its_magic_is_reported(self):
        tree = Tree()
        (tree.out/observer.VENDOR_BOOT['vendorBootImage']).write_bytes(b'ANDROID!' + b'\0' * 56)
        self.assertIs(tree.observe()['vendorBoot']['vendorBootImage']['vndrbootMagic'], False)

    def test_no_descriptor_is_left_open(self):
        tree = Tree()
        before = len(os.listdir('/proc/self/fd'))
        for _ in range(3):
            tree.observe()
        self.assertEqual(len(os.listdir('/proc/self/fd')), before)


def stat_is_target(st, tree):
    try:
        return st.st_ino == tree.target.stat().st_ino and st.st_size == tree.target.stat().st_size
    except OSError:
        return False


class Fixed(unittest.TestCase):
    def test_the_observed_paths_are_flavors_compile_collector_paths(self):
        spec = importlib.util.spec_from_file_location('blazer_build_only', ROOT/'scripts/blazer-build-only.py')
        helper = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(helper)
        collector = helper.collector()
        self.assertEqual(observer.TARGET_FILES_DIR, collector.TARGET_FILES_DIR)
        self.assertEqual(observer.TARGET_FILES_NAME.pattern, collector.TARGET_FILES_NAME.pattern)
        self.assertEqual(observer.OTATOOLS, collector.FILES['otatools'][0])
        self.assertEqual(observer.VENDOR_BOOT, {k: collector.FILES[k][0] for k in ('vendorBootImage', 'vendorKernelBootImage')})

    def test_paths_come_only_from_the_committed_bindings(self):
        source, out, target = observer.paths()
        prep = json.loads((ROOT/'os/build-only/blazer-prep-host.json').read_text())
        committed = json.loads((ROOT/observer.TARGET).read_text())
        self.assertEqual(source, Path(prep['sourceRoot']))
        self.assertEqual(set(committed), observer.TARGET_KEYS)
        self.assertEqual(out, source/committed['outDir'])
        self.assertEqual(target, {'build': committed['build'], 'outDir': committed['outDir'],
                                  'form': 'legacy' if committed['outDir'] == observer.LEGACY_OUT_DIR else 'per-build'})
        # The legacy name is the fixed OUT_DIR compile used before F1/F2: the root of Flavors' per-build template family.
        template = json.loads((ROOT/'os/build-only/blazer.json').read_text())['compile']['outDirTemplate']
        self.assertEqual(template, observer.LEGACY_OUT_DIR + '-{build}')
        spec = importlib.util.spec_from_file_location('blazer_build_only', ROOT/'scripts/blazer-build-only.py')
        helper = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(helper)
        self.assertEqual(helper.COMPILE_OUT_DIR, template)
        self.assertEqual(helper.compile_out_dir({'compile': {'outDirTemplate': template}}, {'buildNumber': NEXT}),
                         observer.paths(bindings(source, NEXT, f'out-blazer-build-{NEXT}'))[2]['outDir'])
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
