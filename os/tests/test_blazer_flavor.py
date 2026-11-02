"""FAM-BLAZER-FLAVOR-003: the Pixel 10 Pro (blazer) OS flavor contract, os/flavors/blazer.json.

Loads it through the shared loader (scripts/factory-package.py load_flavor), which cross-checks it against
os/devices/pixel10.json, the pinned adevtool 17df3b79 android-info.txt and the GrapheneOS/script generate-release.sh
fixture, and checks that a wrong device, guessed firmware or drifted provenance refuses. Readiness stays false: no
blazer build, signing, publication or phone receipt exists. Source and fixture logic only: no build, host or device."""
from pathlib import Path
import copy
import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('factory_package', ROOT/'scripts/factory-package.py')
fp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fp)

FLAVOR = 'os/flavors/blazer.json'
SKELS = 'os/tests/fixtures/adevtool-17df3b79/adevtool/vendor-skels/google_devices'
SOURCES = (FLAVOR, 'os/flavors/frankel.json', 'os/devices/pixel10.json', 'os/release-pipeline/frankel.json',
    'scripts/tests/fixtures/permission-apex/script/generate-release.sh',
    f'{SKELS}/blazer/firmware/android-info.txt', f'{SKELS}/frankel/firmware/android-info.txt')

def zip_bytes(members):
    from io import BytesIO
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        for name, data in members:
            archive.writestr(name, data)
    return buffer.getvalue()

def ota(device, build='2026103100'):
    return zip_bytes([('payload.bin', b'CrAU synthetic'), ('payload_properties.txt', b'FILE_HASH=x\n'),
        ('META-INF/com/android/metadata', f'ota-type=AB\npre-device={device}\npost-build-incremental={build}\n'.encode())])

class BlazerFlavorTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='blazer-flavor-'))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.root = self.tmp/'repo'
        for rel in SOURCES:
            (self.root/rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT/rel, self.root/rel)

    def edit(self, rel, change):
        path = self.root/rel
        data = json.loads(path.read_text())
        change(data)
        path.write_text(json.dumps(data))

    def refuses(self, pattern):
        with self.assertRaisesRegex(fp.ContractError, pattern):
            fp.load_flavor('blazer', self.root)

    def test_contract_loads_from_the_pinned_sources(self):
        flavor = fp.load_flavor('blazer')
        device, entry = flavor['device'], json.loads((ROOT/'os/devices/pixel10.json').read_text())['devices']['blazer']
        self.assertEqual((device['codename'], device['board'], device['product'], device['model'], device['variant']),
            ('blazer', 'blazer', 'blazer', 'Pixel 10 Pro', 'user'))
        self.assertEqual(device['lunch'], 'blazer-cur-user')
        self.assertEqual(entry['lunch'], {'expected': 'blazer-cur-user', 'probe': 'notExecuted', 'evidence': None})
        provenance = flavor['provenance']
        self.assertEqual((provenance['release'], provenance['vendorBuildId'], provenance['adevtool']),
            ('2026100600', 'CP3A.261005.005', '17df3b79a73e67ad68387b35c7d41c85e1d8103c'))
        self.assertEqual(provenance['androidInfoFixture'], f'{SKELS}/blazer/firmware/android-info.txt')
        info = fp.android_info((ROOT/provenance['androidInfoFixture']).read_text())
        self.assertEqual(info, {'board': ['blazer'], 'version-bootloader': [flavor['firmware']['bootloader']],
            'version-baseband': [flavor['firmware']['baseband']], 'partition-exists': ['vendor_kernel_boot']})
        self.assertEqual(provenance['releaseScript']['generate-release.sh'],
            hashlib.sha256((ROOT/provenance['releaseScript']['fixture']).read_bytes()).hexdigest())
        script = (ROOT/provenance['releaseScript']['fixture']).read_text()
        self.assertIn('elif [[ $DEVICE == @(rango|mustang|blazer|frankel) ]]; then', script, 'factory MAX_DOWNLOAD_SIZE')
        self.assertIn('@(yogi|kodiak|grizzly|cubs|rango|mustang|blazer|frankel)', script, 'OTA_ARGS=--disable_ublk')
        cli = subprocess.run([sys.executable, str(ROOT/'scripts/factory-package.py'), 'contract', '--flavor', 'blazer'],
            capture_output=True, text=True)
        self.assertEqual(cli.returncode, 0, cli.stderr)
        self.assertEqual(json.loads(cli.stdout)['flavor'], 'blazer')

    def test_readiness_stays_false_without_real_receipts(self):
        flavor = fp.load_flavor('blazer')
        self.assertEqual(flavor['readiness'], {'buildReady': False, 'factoryReady': False, 'phoneValidated': False,
            'receipts': [], 'basis': flavor['readiness']['basis']})
        self.assertEqual((flavor['retained'], flavor['inventory']['observed']), ([], []))
        self.assertFalse(flavor['artifacts']['factory']['currentlyProduced'])
        self.assertEqual((flavor['factorySigning']['principal'], flavor['factorySigning']['publicKey'],
            flavor['factorySigning']['status']), (None, None, 'missing'))
        self.assertIsNone(fp.pinned_avb_key(flavor), 'no blazer signing identity exists to pin')
        self.assertTrue(all({'input', 'owner', 'next'} <= set(m) for m in flavor['missingForFactory']))
        self.assertTrue(any('build-only recipe' in m['input'] for m in flavor['missingForFactory']))
        # Anything classified refuses: a matching OTA is in-place only, and nothing can be factory-ready.
        for name, data in (('blazer-ota.zip', ota('blazer')), ('other.zip', zip_bytes([('readme.txt', b'x')]))):
            (self.tmp/name).write_bytes(data)
            result = fp.classify(self.tmp/name, 'blazer', work_dir=self.tmp)
            self.assertFalse(result['factoryReady'] or result['structurallyComplete'] or result['signatureVerified'], name)
            self.assertFalse(result['limits']['phoneValidated'])

    def test_wrong_device_is_rejected(self):
        (self.tmp/'frankel-ota.zip').write_bytes(ota('frankel'))
        result = fp.classify(self.tmp/'frankel-ota.zip', 'blazer', work_dir=self.tmp)
        self.assertFalse(result['otaApplicable'])
        self.assertIn('wrong-device:ota pre-device frankel', result['problems'])
        original = json.loads((self.root/FLAVOR).read_text())
        for change, pattern in (
                (lambda d: d['device'].update(codename='frankel'), 'device.codename'),
                (lambda d: d['device'].update(board='rango'), 'device.board'),
                (lambda d: d['device'].update(model='Pixel 10 Pro XL'), 'device.model'),
                (lambda d: d['device'].update(lunch='blazer-cur-userdebug'), 'device.lunch'),
                (lambda d: d['provenance'].update(androidInfoFixture=f'{SKELS}/frankel/firmware/android-info.txt'),
                    'android-info board'),
                (lambda d: d.update(flavor='rango'), 'not a schemaVersion 1 roseos.os-flavor for blazer')):
            (self.root/FLAVOR).write_text(json.dumps(original))
            self.edit(FLAVOR, change)
            self.refuses(pattern)
        (self.root/FLAVOR).write_text(json.dumps(original))
        self.edit('os/devices/pixel10.json', lambda d: d['devices'].pop('blazer'))
        self.refuses('blazer: not in os/devices/pixel10.json')

    def test_guessed_firmware_is_rejected(self):
        original = json.loads((self.root/FLAVOR).read_text())
        for change, pattern in (
                (lambda d: d['firmware'].update(bootloader='deepspace-17.4-guessed'), 'firmware.bootloader'),
                (lambda d: d['firmware'].update(baseband='g5400i-guessed'), 'firmware.baseband'),
                (lambda d: d['firmware'].update(partitionExists=[]), 'firmware.partitionExists')):
            (self.root/FLAVOR).write_text(json.dumps(original))
            self.edit(FLAVOR, change)
            self.refuses(pattern)
        (self.root/FLAVOR).write_text(json.dumps(original))
        info = self.root/SKELS/'blazer/firmware/android-info.txt'
        info.write_text(info.read_text().replace('g5400i-260604-260807-B-16035863', 'g5400i-other'))
        self.refuses('android-info baseband')

    def test_drifted_provenance_or_identity_is_refused(self):
        original = json.loads((self.root/FLAVOR).read_text())
        frankel_key = json.loads((ROOT/'os/release-pipeline/frankel.json').read_text())['identities']['avbPublicKeySha256']
        for change, pattern in (
                (lambda d: d['provenance'].update(release='2026100700'), 'provenance.release'),
                (lambda d: d['provenance'].update(vendorBuildId='CP3A.261105.001'), 'provenance.vendorBuildId'),
                (lambda d: d['provenance'].update(adevtool='0' * 40), 'provenance.adevtool'),
                (lambda d: d['provenance']['releaseScript'].update({'generate-release.sh': '0' * 64}), 'releaseScript sha256'),
                (lambda d: d['factorySigning'].update(namespace='images'), 'factorySigning.namespace'),
                (lambda d: d['factorySigning'].update(principal='rose'), 'factorySigning needs both'),
                (lambda d: d['artifacts']['factory'].update(dataWipe=False), 'install-mode declarations'),
                (lambda d: d['identity'].update(avbPublicKeySha256='not-a-digest'), 'not a sha256'),
                (lambda d: d['identity'].update(avbPublicKeySha256=frankel_key), 'signer_collision')):
            (self.root/FLAVOR).write_text(json.dumps(copy.deepcopy(original)))
            self.edit(FLAVOR, change)
            self.refuses(pattern)
        (self.root/FLAVOR).write_text(json.dumps(original))
        script = self.root/'scripts/tests/fixtures/permission-apex/script/generate-release.sh'
        script.write_bytes(script.read_bytes() + b'\n# drift\n')
        self.refuses('releaseScript sha256')
        with self.assertRaisesRegex(fp.ContractError, 'invalid flavor name'):
            fp.load_flavor('../blazer', self.root)

if __name__ == '__main__':
    unittest.main()
