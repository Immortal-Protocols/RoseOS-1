"""factory-package.py (FLAVOR001) against synthetic Frankel/Rango archives built in memory from
scripts/tests/fixtures/factory-package. No real image, key, OTA, host, phone or tool execution."""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest import mock
import zipfile

SCRIPTS = Path(__file__).resolve().parents[1]
ROOT = SCRIPTS.parent
spec = importlib.util.spec_from_file_location('factory_package', SCRIPTS/'factory-package.py')
fp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fp)

FIXTURES = SCRIPTS/'tests/fixtures/factory-package'
AB = (FIXTURES/'ab_partitions.txt').read_text().split()
MISC = (FIXTURES/'misc_info.txt').read_text()
DYNAMIC = ['system', 'system_ext', 'product', 'vendor']
BUILD = '2026103101'
BOOTLOADER = 'deepspace-17.4-16119390'
BASEBAND = 'g5400i-260604-260807-B-16035863'
PKMD = b'synthetic avb public key metadata'
SPARSE = b'\x3a\xff\x26\xed'
# Inputs whose bytes these tests must leave unchanged (published/retained metadata).
RETAINED = ['os/retained-rango/release.json', 'os/release-notes/rango-2026103001.md', 'os/devices/pixel10.json',
            'os/release-pipeline/frankel.json']

def image(part):
    magic = {'boot': b'ANDROID!', 'init_boot': b'ANDROID!', 'vendor_boot': b'VNDRBOOT', 'vendor_kernel_boot': b'VNDRBOOT'}
    return magic.get(part, b'AVB0' if part.startswith('vbmeta') else b'') + f'synthetic {part}'.encode() * 8

def android_info(device, bootloader=BOOTLOADER, baseband=BASEBAND):
    return (f'require board={device}\n\nrequire version-bootloader={bootloader}\n'
            f'require version-baseband={baseband}\nrequire partition-exists=vendor_kernel_boot\n').encode()

def zip_bytes(entries):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, data in entries:
            archive.writestr(name, data)
    return buffer.getvalue()

def target_files(device, build=BUILD, ab=None, misc=MISC, drop=()):
    entries = {'META/ab_partitions.txt': '\n'.join(ab or AB).encode(), 'META/misc_info.txt': misc.encode(),
               'OTA/android-info.txt': android_info(device), 'SYSTEM/build.prop': f'ro.build.version.incremental={build}\n'.encode(),
               'RADIO/bootloader.img': b'synthetic bootloader', 'RADIO/radio.img': b'synthetic radio'}
    for part in AB:
        if part not in DYNAMIC and part != 'abl':
            entries[f'IMAGES/{part}.img'] = image(part)
    return zip_bytes([(k, v) for k, v in entries.items() if k not in drop])

def install(device, build=BUILD, top=None, info=None, replace=None, drop=(), extra=()):
    top = top or f'{device}-install-{build}'
    files = {'android-info.txt': info or android_info(device),
             'flash-all.sh': b'# synthetic stand-in, not a flasher\n# erase userdata\n',
             'script.txt': b'# synthetic stand-in\n# erase userdata\n',
             'avb_pkmd.bin': PKMD,
             f'bootloader-{device}-{BOOTLOADER}.img': b'synthetic bootloader',
             f'radio-{device}-{BASEBAND}.img': b'synthetic radio',
             'super_1.img': SPARSE + b'synthetic super 1', 'super_2.img': SPARSE + b'synthetic super 2'}
    for part in AB:
        if part not in DYNAMIC and part != 'abl':
            files[f'{part}.img'] = image(part)
    files.update(replace or {})
    entries = [(f'{top}/{k}', v) for k, v in files.items() if k not in drop] + list(extra)
    return zip_bytes(entries)

def ota(device):
    return zip_bytes([('payload.bin', b'CrAU synthetic'), ('payload_properties.txt', b'FILE_HASH=x\n'),
                      ('META-INF/com/android/metadata', f'ota-type=AB\npre-device={device}\npost-build-incremental={BUILD}\n'.encode())])

class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='factory-package-test-'))
        self.addCleanup(shutil.rmtree, self.tmp)
        self.before = {p: hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in RETAINED}
        # A repository copy whose frankel pin is the synthetic key, so the pinned-identity path runs for real.
        self.root = self.tmp/'repo'
        for rel in ('os/flavors/frankel.json', 'os/flavors/rango.json', 'os/devices/pixel10.json',
                    'os/release-pipeline/frankel.json', 'scripts/tests/fixtures/permission-apex/script/generate-release.sh') + tuple(
                    f'os/tests/fixtures/adevtool-17df3b79/adevtool/vendor-skels/google_devices/{d}/firmware/android-info.txt'
                    for d in ('frankel', 'rango')):
            (self.root/rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT/rel, self.root/rel)
        pipeline = json.loads((self.root/'os/release-pipeline/frankel.json').read_text())
        pipeline['identities']['avbPublicKeySha256'] = hashlib.sha256(PKMD).hexdigest()
        (self.root/'os/release-pipeline/frankel.json').write_text(json.dumps(pipeline))

    def tearDown(self):
        self.assertEqual(self.before, {p: hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in RETAINED})

    def write(self, name, data):
        path = self.tmp/name
        path.write_bytes(data)
        return path

    def classify(self, archive, flavor, tf=None, build=BUILD, key=None, root=None):
        return fp.classify(archive, flavor, tf, build, key, root or self.root)

    def complete(self, device):
        return (self.write(f'{device}-install-{BUILD}.zip', install(device)), self.write(f'{device}-target_files.zip', target_files(device)))

    def refused(self, result, *codes):
        self.assertFalse(result['factoryReady'], result)
        text = ' '.join(result['problems'] + result['missing'])
        for code in codes:
            self.assertIn(code, text)

class Contracts(Fixture):
    def test_both_flavors_load_and_match_their_sources(self):
        for name, model in (('frankel', 'Pixel 10'), ('rango', 'Pixel 10 Pro Fold')):
            flavor = fp.load_flavor(name)
            self.assertEqual((flavor['device']['model'], flavor['device']['board']), (model, name))
            self.assertTrue(flavor['artifacts']['factory']['dataWipe'] and flavor['artifacts']['factory']['requiresUnlockedBootloader'])
            self.assertFalse(flavor['artifacts']['ota']['dataWipe'] or flavor['artifacts']['factory']['currentlyProduced'])
            self.assertTrue(flavor['missingForFactory'] and all({'input', 'owner', 'next'} <= set(m) for m in flavor['missingForFactory']))

    def test_drift_from_device_contract_or_pinned_firmware_refuses_the_contract(self):
        path = self.root/'os/flavors/rango.json'
        flavor = json.loads(path.read_text())
        flavor['firmware']['baseband'] = 'g5400i-guessed'
        path.write_text(json.dumps(flavor))
        with self.assertRaisesRegex(fp.ContractError, 'firmware.baseband'):
            fp.load_flavor('rango', self.root)
        with self.assertRaises(fp.ContractError):
            fp.load_flavor('../rango', self.root)

    def test_retained_rango_2026103001_is_ota_only_and_never_factory_ready(self):
        retained = fp.load_flavor('rango')['retained']
        self.assertEqual([(r['build'], r['artifactRoles'], r['factoryReady'], r['phoneValidated']) for r in retained],
                         [('2026103001', ['ota-update'], False, False)])
        self.assertEqual(json.loads((ROOT/'os/retained-rango/release.json').read_text())['target']['build'], '2026103001')

    def test_rango_identity_is_runtime_only_and_frankel_pin_is_read_from_the_pipeline(self):
        self.assertIsNone(fp.pinned_avb_key(fp.load_flavor('rango')))
        self.assertEqual(fp.pinned_avb_key(fp.load_flavor('frankel')),
                         json.loads((ROOT/'os/release-pipeline/frankel.json').read_text())['identities']['avbPublicKeySha256'])

class Positive(Fixture):
    def test_complete_frankel_package_is_factory_ready_and_packaged_with_receipt(self):
        archive, tf = self.complete('frankel')
        self.write(archive.name + '.sig', b'synthetic signature')
        receipt = fp.package(archive, 'frankel', tf, self.tmp/'out', BUILD, None, self.root)
        self.assertTrue(receipt['factoryReady'])
        self.assertEqual((receipt['role'], receipt['missing'], receipt['problems']), ('factory-install', [], []))
        self.assertEqual(receipt['inventory']['firmwareCarried'], ['abl'])
        self.assertEqual(receipt['inventory']['dynamic'], sorted(DYNAMIC))
        self.assertEqual(receipt['inventory']['superSegments'], 2)
        self.assertTrue(receipt['installMode']['dataWipe'])
        self.assertEqual(receipt['limits']['phoneValidated'], False)
        written = json.loads((self.tmp/'out/factory-package.json').read_text())
        self.assertEqual(written['files'][archive.name], hashlib.sha256(archive.read_bytes()).hexdigest())
        self.assertTrue(written['signaturePresent'])
        self.assertEqual((self.tmp/'out'/archive.name).read_bytes(), archive.read_bytes())

    def test_complete_rango_package_needs_its_runtime_identity(self):
        archive, tf = self.complete('rango')
        self.refused(self.classify(archive, 'rango', tf), 'identity:rango avbPublicKeySha256')
        result = self.classify(archive, 'rango', tf, key=hashlib.sha256(PKMD).hexdigest())
        self.assertTrue(result['factoryReady'], result)
        receipt = fp.package(archive, 'rango', tf, self.tmp/'out', BUILD, hashlib.sha256(PKMD).hexdigest(), self.root)
        self.assertFalse(receipt['signaturePresent'])

    def test_cli_exit_codes(self):
        archive, tf = self.complete('rango')
        key = hashlib.sha256(PKMD).hexdigest()
        with mock.patch.object(fp, 'ROOT', self.root), mock.patch('sys.stdout', io.StringIO()) as out:
            self.assertEqual(fp.main(['classify', str(archive), '--flavor', 'rango', '--target-files', str(tf),
                                      '--avb-public-key-sha256', key]), 0)
            self.assertTrue(json.loads(out.getvalue())['factoryReady'])
        with mock.patch.object(fp, 'ROOT', self.root), mock.patch('sys.stdout', io.StringIO()), mock.patch('sys.stderr', io.StringIO()):
            self.assertEqual(fp.main(['classify', str(archive), '--flavor', 'rango']), 3)
            self.assertEqual(fp.main(['package', str(archive), '--flavor', 'rango', '--target-files', str(tf),
                                      '--out', str(self.tmp)]), 2)
            self.assertEqual(fp.main(['classify', str(archive), '--flavor', 'rango', '--build', '1']), 2)
            self.assertEqual(fp.main(['package', str(self.write('o.zip', ota('rango'))), '--flavor', 'rango',
                                      '--target-files', str(tf), '--out', str(self.tmp/'new')]), 3)
        self.assertFalse((self.tmp/'new').exists())

class Negative(Fixture):
    def test_ota_only_is_applicable_in_place_but_never_factory(self):
        for device in ('frankel', 'rango'):
            result = self.classify(self.write('o.zip', ota(device)), device, self.complete(device)[1])
            self.refused(result, 'not-factory:ota-update')
            self.assertTrue(result['otaApplicable'])
            self.assertFalse(result['installMode']['dataWipe'])
            with self.assertRaises(fp.Refused):
                fp.package(self.tmp/'o.zip', device, self.complete(device)[1], self.tmp/'out', BUILD, None, self.root)
        wrong = self.classify(self.write('o.zip', ota('frankel')), 'rango')
        self.refused(wrong, 'wrong-device:ota pre-device frankel')
        self.assertFalse(wrong['otaApplicable'])

    def test_target_files_img_zip_and_extracted_images_are_not_factory(self):
        self.refused(self.classify(self.write('tf.zip', target_files('rango')), 'rango'), 'not-factory:target-files')
        img = zip_bytes([('android-info.txt', android_info('rango')), ('boot.img', image('boot'))])
        self.refused(self.classify(self.write('img.zip', img), 'rango'), 'not-factory:img-zip')
        loose = zip_bytes([('boot.img', image('boot')), ('system.img', b'x')])
        self.refused(self.classify(self.write('loose.zip', loose), 'rango'), 'not-factory:unknown')
        self.refused(self.classify(self.tmp/'absent.zip', 'rango'), 'artifact:absent.zip')

    def test_without_target_files_inventory_completeness_is_refused(self):
        archive, _ = self.complete('frankel')
        self.refused(self.classify(archive, 'frankel'), 'target-files inventory')
        tf = self.write('tf.zip', target_files('frankel', drop=('META/ab_partitions.txt', 'RADIO/radio.img')))
        self.refused(self.classify(archive, 'frankel', tf), 'target-files:META/ab_partitions.txt')
        tf = self.write('tf.zip', target_files('frankel', drop=('RADIO/radio.img',)))
        self.refused(self.classify(archive, 'frankel', tf), 'target-files:RADIO/radio.img')

    def test_wrong_device(self):
        archive, _ = self.complete('frankel')
        self.refused(self.classify(archive, 'rango', self.complete('rango')[1], key='0' * 64),
                     'wrong-device:archive directory frankel-install', 'wrong-device:android-info board')
        self.refused(self.classify(self.complete('rango')[0], 'rango', self.write('tf.zip', target_files('frankel')), key='0' * 64),
                     'wrong-device:target-files board')

    def test_mixed_firmware(self):
        _, tf = self.complete('frankel')
        for archive, code in (
                (install('frankel', info=android_info('frankel', baseband='g5400i-older')), 'mixed-firmware:android-info version-baseband'),
                (install('frankel', extra=[(f'frankel-install-{BUILD}/radio-frankel-g5400i-older.img', b'r')]), 'mixed-firmware:unexpected radio-'),
                (install('frankel', replace={f'bootloader-frankel-{BOOTLOADER}.img': b'other bootloader'}), 'differs from target-files RADIO/bootloader.img')):
            self.refused(self.classify(self.write('a.zip', archive), 'frankel', tf), code)

    def test_missing_boot_chain_or_required_firmware(self):
        _, tf = self.complete('frankel')
        for drop, code in ((('boot.img',), 'factory:boot.img'), (('vbmeta.img',), 'factory:vbmeta.img'),
                           ((f'bootloader-frankel-{BOOTLOADER}.img',), f'factory:bootloader-frankel-{BOOTLOADER}.img'),
                           ((f'radio-frankel-{BASEBAND}.img',), 'factory:radio-frankel'),
                           (('super_1.img', 'super_2.img'), 'factory:super_1.img'), (('android-info.txt',), 'factory:android-info.txt'),
                           (('avb_pkmd.bin',), 'factory:avb_pkmd.bin')):
            self.refused(self.classify(self.write('a.zip', install('frankel', drop=drop)), 'frankel', tf), code)

    def test_duplicate_partition_traversal_and_links(self):
        _, tf = self.complete('rango')
        top = f'rango-install-{BUILD}'
        with self.assertWarns(UserWarning):
            dup = install('rango', extra=[(f'{top}/boot.img', image('boot'))])
        self.refused(self.classify(self.write('a.zip', dup), 'rango', tf), f'duplicate-entry:artifact:{top}/boot.img')
        self.refused(self.classify(self.write('a.zip', install('rango', extra=[(f'{top}/../evil.img', b'x')])), 'rango', tf),
                     'unsafe-path:artifact')
        self.refused(self.classify(self.write('a.zip', install('rango', extra=[(f'{top}/sub/boot.img', b'x')])), 'rango', tf),
                     'unexpected-member')
        buffer = io.BytesIO(install('rango'))
        with zipfile.ZipFile(buffer, 'a') as archive:
            link = zipfile.ZipInfo(f'{top}/link.img')
            link.external_attr = (0o120777 << 16)
            archive.writestr(link, '/dev/block/by-name/boot')
        self.refused(self.classify(self.write('a.zip', buffer.getvalue()), 'rango', tf), 'link-entry:artifact')
        tf = self.write('tf.zip', target_files('rango', ab=AB + ['boot']))
        self.refused(self.classify(self.complete('rango')[0], 'rango', tf), 'duplicate-partition:META/ab_partitions.txt')
        self.refused(self.classify(self.write('a.zip', install('rango', replace={'extra.img': b'x'})), 'rango', self.complete('rango')[1]),
                     'unexpected-image:extra.img')

    def test_corrupt_archive_and_images(self):
        _, tf = self.complete('frankel')
        for replace, code in (({'vbmeta.img': b'XXXX' + b'v' * 20}, 'corrupt-image:vbmeta.img header'),
                              ({'boot.img': b'ANDROID!' + b'other'}, 'image-mismatch:boot.img'),
                              ({'super_2.img': b'not sparse'}, 'corrupt-image:super_2.img'),
                              ({'dtbo.img': b''}, 'corrupt-image:dtbo.img empty')):
            self.refused(self.classify(self.write('a.zip', install('frankel', replace=replace)), 'frankel', tf), code)
        self.refused(self.classify(self.write('a.zip', install('frankel', drop=('super_1.img',))), 'frankel', tf), 'not contiguous')
        stored = io.BytesIO()
        with zipfile.ZipFile(stored, 'w', zipfile.ZIP_STORED) as archive:
            archive.writestr(f'frankel-install-{BUILD}/android-info.txt', android_info('frankel'))
        data = stored.getvalue().replace(b'require board=frankel', b'require board=frankeX')
        self.refused(self.classify(self.write('a.zip', data), 'frankel', tf), 'corrupt-entry:artifact')
        self.refused(self.classify(self.write('a.zip', b'PK\x03\x04 truncated'), 'frankel', tf), 'corrupt-archive:artifact')

    def test_unsigned_or_mismatched_identity(self):
        archive, tf = self.complete('frankel')
        self.refused(self.classify(archive, 'frankel', tf, root=ROOT), 'identity-mismatch:avb_pkmd.bin')
        self.refused(self.classify(self.write('a.zip', install('frankel', replace={'avb_pkmd.bin': b'test key'})), 'frankel', tf),
                     'identity-mismatch:avb_pkmd.bin')
        self.refused(self.classify(archive, 'frankel', tf, build='2026103102'), 'identity-mismatch:archive build',
                     'identity-mismatch:target-files build')
        with self.assertRaisesRegex(fp.ContractError, 'differs from the pinned'):
            self.classify(archive, 'frankel', tf, key='0' * 64)

    def test_install_mode_declarations(self):
        _, tf = self.complete('frankel')
        quiet = {'flash-all.sh': b'# synthetic stand-in\n', 'script.txt': b'# synthetic stand-in\n'}
        self.refused(self.classify(self.write('a.zip', install('frankel', replace=quiet)), 'frankel', tf), 'install-mode:')
        tf = self.write('tf.zip', target_files('frankel', misc=MISC.replace('ab_update=true', 'ab_update=false')))
        self.refused(self.classify(self.complete('frankel')[0], 'frankel', tf), 'install-semantics:target-files ab_update')

if __name__ == '__main__':
    unittest.main()
