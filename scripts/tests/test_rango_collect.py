"""rango-collect.py (OS-RANGO-COLLECT-006) against synthetic output trees: synthetic vendor boot headers, an
extensionless otatools ZIP and target-files ZIPs. No real build output, host, network or tool execution."""
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock
import zipfile

SCRIPTS = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('rango_collect', SCRIPTS/'rango-collect.py')
rc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rc)

BUILD = '2026103001'
OTATOOLS = 'host/linux-x86/obj/ETC/otatools-packagelinux_glibc_x86_64_intermediates/otatools-packagelinux_glibc_x86_64'
TF_DIR = 'target/product/rango/obj/PACKAGING/target_files_intermediates'

def zip_bytes(entries, compression=zipfile.ZIP_DEFLATED):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', compression) as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    return buffer.getvalue()

OTATOOLS_ZIP = zip_bytes({'bin/simg2img': b'\x7fELF synthetic', 'releasetools/sign_target_files_apks': b'#!py'})
TARGET_FILES_ZIP = zip_bytes({'META/misc_info.txt': b'recovery_api_version=3\n', 'SYSTEM/build.prop': b'x=1\n'})

class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='rango-collect-'))
        self.addCleanup(__import__('shutil').rmtree, self.tmp, True)
        self.root = self.tmp/'src'
        self.out = self.root/('out-rango-' + BUILD)
        self.write('target/product/rango/vendor_boot.img', b'VNDRBOOT' + b'\x04' * 100)
        self.write('target/product/rango/vendor_kernel_boot.img', b'VNDRBOOT' + b'\x05' * 100)
        self.write(OTATOOLS, OTATOOLS_ZIP)
        self.write(f'{TF_DIR}/rango-target_files.zip', TARGET_FILES_ZIP)
        (self.out/TF_DIR/'rango-target_files').mkdir()  # the unzipped intermediates directory is not eligible
        (self.out/TF_DIR/'rango-target_files.zip.list').write_text('x')

    def write(self, relative, data):
        path = self.out/relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def collect(self):
        return rc.collect(str(self.root), BUILD)

    def state(self, name):
        return self.collect()['artifacts'][name]

    def tree(self):
        return {str(p): (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns)
            for p in sorted(self.out.rglob('*')) if p.is_file() and not p.is_symlink()}

class CollectTest(Fixture):
    def test_collects_the_four_fixed_outputs_including_the_extensionless_otatools_zip(self):
        before = self.tree()
        result = self.collect()
        self.assertEqual(result['verdict'], 'complete')
        ota = result['artifacts']['otatools']
        self.assertEqual((ota['state'], ota['format'], ota['entries'], ota['size'], ota['sha256']),
            ('collected', 'zip', 2, len(OTATOOLS_ZIP), hashlib.sha256(OTATOOLS_ZIP).hexdigest()))
        self.assertEqual(ota['path'], str(self.out/OTATOOLS))
        tf = result['artifacts']['targetFiles']
        self.assertEqual((tf['state'], Path(tf['path']).name, tf['directoryListing']['eligible']),
            ('collected', 'rango-target_files.zip', ['rango-target_files.zip']))
        self.assertEqual(result['artifacts']['vendorBootImage']['format'], 'vendor-boot')
        self.assertEqual(self.tree(), before, 'the original bytes and mtimes are unchanged')

    def test_missed_zip_aliases_are_never_consulted(self):
        (self.out/OTATOOLS).unlink()
        for alias in ('host/linux-x86/otatools.zip', 'dist/otatools.zip',
                'host/linux-x86/obj/PACKAGING/otatools_intermediates/otatools.zip'):
            self.write(alias, OTATOOLS_ZIP)
        result = self.collect()
        self.assertEqual(result['artifacts']['otatools']['state'], 'missing')
        self.assertEqual(result['verdict'], 'incomplete')
        self.assertTrue(all(a['state'] == 'collected' for k, a in result['artifacts'].items() if k != 'otatools'),
            'partial results are kept')

    def test_absent_or_ambiguous_target_files(self):
        self.write(f'{TF_DIR}/rango-target_files-2026103001.zip', TARGET_FILES_ZIP)
        tf = self.state('targetFiles')
        self.assertEqual(tf['state'], 'ambiguous')
        self.assertIn('2 target-files ZIPs', tf['why'])
        for name in ('rango-target_files.zip', 'rango-target_files-2026103001.zip'):
            (self.out/TF_DIR/name).unlink()
        self.write(f'{TF_DIR}/frankel-target_files.zip', TARGET_FILES_ZIP)  # another device's name is not eligible
        self.assertEqual(self.state('targetFiles')['state'], 'missing')
        __import__('shutil').rmtree(self.out/TF_DIR)
        self.assertEqual(self.state('targetFiles')['state'], 'missing')

    def test_links_and_special_files_are_invalid_never_followed(self):
        real = self.tmp/'elsewhere.zip'
        real.write_bytes(OTATOOLS_ZIP)
        (self.out/OTATOOLS).unlink()
        (self.out/OTATOOLS).symlink_to(real)
        self.assertEqual(self.state('otatools')['state'], 'invalid')
        etc = self.out/'host/linux-x86/obj/ETC'
        etc.rename(self.tmp/'ETC')
        etc.symlink_to(self.tmp/'ETC')
        ota = self.state('otatools')
        self.assertEqual(ota['state'], 'invalid')
        self.assertIn('not an ordinary directory', ota['why'])
        boot = self.out/'target/product/rango/vendor_boot.img'
        boot.unlink()
        os.mkfifo(boot)
        self.assertEqual(self.state('vendorBootImage')['state'], 'invalid')
        tfdir = self.out/TF_DIR
        tfdir.rename(self.tmp/'tf')
        tfdir.symlink_to(self.tmp/'tf')
        self.assertEqual(self.state('targetFiles')['state'], 'invalid')

    def test_a_linked_or_absent_output_directory_leaves_everything_uninspected(self):
        self.out.rename(self.tmp/'moved')
        self.out.symlink_to(self.tmp/'moved')
        result = self.collect()
        self.assertEqual({a['state'] for a in result['artifacts'].values()}, {'uninspected'})
        self.assertEqual(result['outDirState'], 'invalid')
        self.out.unlink()
        self.assertEqual(self.collect()['outDirState'], 'missing')

    def test_malformed_or_incomplete_archives_and_images_are_invalid(self):
        self.write(OTATOOLS, b'PK\x03\x04 truncated')
        self.assertIn('not a readable otatools ZIP', self.state('otatools')['why'])
        self.write(OTATOOLS, zip_bytes({'bin/x': b'1'}))
        self.assertIn("absent: ['releasetools/']", self.state('otatools')['why'])
        self.write(OTATOOLS, zip_bytes({'bin/x': b'1', 'releasetools/y': b'2', '../escape': b'3'}))
        self.assertIn('unsafe entry names', self.state('otatools')['why'])
        corrupt = bytearray(zip_bytes({'bin/x': b'1', 'releasetools/y': b'#!py'}, zipfile.ZIP_STORED))
        offset = corrupt.index(b'#!py')
        corrupt[offset:offset + 4] = b'XXXX'
        self.write(OTATOOLS, bytes(corrupt))
        self.assertIn('CRC mismatch in releasetools/y', self.state('otatools')['why'])
        self.write(f'{TF_DIR}/rango-target_files.zip', zip_bytes({'SYSTEM/build.prop': b'x'}))
        self.assertIn('META/misc_info.txt', self.state('targetFiles')['why'])
        self.write('target/product/rango/vendor_kernel_boot.img', b'ANDROID!' + b'\0' * 64)
        self.assertIn('no vendor boot header', self.state('vendorKernelBootImage')['why'])
        self.write('target/product/rango/vendor_boot.img', b'')
        self.assertIn('is empty', self.state('vendorBootImage')['why'])

    def test_read_failure_and_change_during_read_are_unreadable(self):
        real_read = os.read
        def failing(fd, n):
            raise OSError(5, 'Input/output error')
        with mock.patch.object(rc.os, 'read', failing):
            self.assertEqual(self.state('vendorBootImage')['state'], 'unreadable')
        target = self.out/'target/product/rango/vendor_boot.img'
        appended = []
        def growing(fd, n):
            block = real_read(fd, n)
            if block and not appended:
                appended.append(True)
                with open(target, 'ab') as f:
                    f.write(b'more')
            return block
        with mock.patch.object(rc.os, 'read', growing):
            self.assertIn('changed while it was read', self.state('vendorBootImage')['why'])
        with mock.patch.object(rc.os, 'open', side_effect=PermissionError(13, 'Permission denied')):
            self.assertEqual(self.state('otatools')['state'], 'unreadable')

    def test_only_the_target_files_directory_is_ever_listed(self):
        listed, real = [], os.listdir
        def spy(path='.'):
            listed.append(str(path))
            return real(path)
        def forbidden(*a, **kw):
            raise AssertionError('no tree walk')
        with mock.patch.object(rc.os, 'listdir', spy), mock.patch.object(rc.os, 'walk', forbidden), \
                mock.patch.object(rc.os, 'scandir', forbidden), mock.patch.object(Path, 'rglob', forbidden), \
                mock.patch.object(Path, 'glob', forbidden), mock.patch.object(Path, 'iterdir', forbidden):
            self.assertEqual(self.collect()['verdict'], 'complete')
        self.assertEqual(listed, [str(self.out/TF_DIR)])

class CliTest(Fixture):
    def test_writes_one_immutable_receipt_outside_the_output(self):
        receipt = self.tmp/'receipts/collect-1.json'
        self.assertEqual(rc.main(['--out-root', str(self.root), '--build-number', BUILD, '--receipt', str(receipt)]), 0)
        doc = json.loads(receipt.read_text())
        self.assertEqual((doc['receiptType'], doc['verdict'], doc['signed'], doc['releaseReady']),
            ('rango-collection', 'complete', False, False))
        self.assertEqual(doc['artifacts']['otatools']['sha256'], hashlib.sha256(OTATOOLS_ZIP).hexdigest())
        self.assertEqual(rc.main(['--out-root', str(self.root), '--build-number', BUILD, '--receipt', str(receipt)]), 2,
            'never overwritten')
        (self.out/OTATOOLS).unlink()
        partial = self.tmp/'receipts/collect-2.json'
        self.assertEqual(rc.main(['--out-root', str(self.root), '--build-number', BUILD, '--receipt', str(partial)]), 1)
        self.assertEqual(json.loads(partial.read_text())['artifacts']['otatools']['state'], 'missing',
            'an incomplete collection is still recorded')

    def test_invalid_usage_writes_nothing(self):
        for argv in (['--out-root', 'relative', '--build-number', BUILD],
                ['--out-root', str(self.root), '--build-number', '../2026103001'],
                ['--out-root', str(self.root), '--build-number', '123'],
                ['--out-root', str(self.root) + '/../src', '--build-number', BUILD]):
            with self.subTest(argv=argv):
                self.assertEqual(rc.main([*argv, '--receipt', str(self.tmp/'r.json')]), 2)
        inside = self.out/'collect.json'
        self.assertEqual(rc.main(['--out-root', str(self.root), '--build-number', BUILD, '--receipt', str(inside)]), 2)
        self.assertFalse(inside.exists())
        self.assertFalse((self.tmp/'r.json').exists())

    def test_the_collector_runs_nothing_and_reaches_no_signer_or_network(self):
        code = (SCRIPTS/'rango-collect.py').read_text()
        for banned in ('subprocess', 'extractall', 'socket', 'urllib', 'gcloud', 'host_secrets', 'import host', 'rmtree',
                'os.remove', '.write_bytes(', '.unlink(', '.rename(', 'os.walk', 'rglob'):
            self.assertNotIn(banned, code, banned)

if __name__ == '__main__':
    unittest.main()
