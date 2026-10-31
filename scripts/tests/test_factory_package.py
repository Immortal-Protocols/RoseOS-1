"""factory-package.py (FLAVOR001/FLAVOR002) against synthetic Frankel/Rango archives built in memory from
scripts/tests/fixtures/factory-package.

Real tools where the runner has them: OpenSSH ssh-keygen (fresh throwaway test keys only) and AOSP simg2img. The
lpunpack inside the synthetic otatools archive is a test double for a synthetic super container (SYNTHLP1), because no
real lpunpack is available offline; a simg2img double decoding the real Android sparse format replaces simg2img only
when the runner has none. No real image, production key, OTA, host, phone or network is used."""
import hashlib
import importlib.util
import io
import itertools
import json
import os
from pathlib import Path
import shutil
import stat
import struct
import subprocess
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
PKMD = {'frankel': b'synthetic frankel avb public key metadata', 'rango': b'synthetic rango avb public key metadata'}
SPARSE = b'\x3a\xff\x26\xed'
GSC = {'evt.ec.bin': b'synthetic gsc evt', 'prod.ec.bin': b'synthetic gsc prod'}
GENERATOR = SCRIPTS/'tests/fixtures/factory-package/device_common-1f5ba267'
BLOCK = 4096
SSH = shutil.which('ssh-keygen')
REAL_SIMG2IMG = shutil.which('simg2img')
RETAINED = ['os/retained-rango/release.json', 'os/release-notes/rango-2026103001.md', 'os/devices/pixel10.json',
            'os/release-pipeline/frankel.json', 'os/flavors/frankel.json', 'os/flavors/rango.json', 'os/flavors/blazer.json']

def image(part):
    magic = {'boot': b'ANDROID!', 'init_boot': b'ANDROID!', 'vendor_boot': b'VNDRBOOT', 'vendor_kernel_boot': b'VNDRBOOT'}
    return magic.get(part, b'AVB0' if part.startswith('vbmeta') else b'') + f'synthetic {part}'.encode() * 8

def dynamic_image(part):
    # Logical partition images are whole blocks, as a sparse image of one always flattens to whole blocks.
    return (f'synthetic logical {part} '.encode() * 900)[:BLOCK * (2 + len(part) % 3)]

def sparse_image(raw, start_block=0, count=None, total=None):
    """Real Android sparse format v1.0: DONT_CARE before/after one RAW run of whole blocks."""
    raw = raw + b'\0' * (-len(raw) % BLOCK)
    total = total if total is not None else len(raw) // BLOCK
    count = count if count is not None else total - start_block
    chunks = []
    if start_block:
        chunks.append(struct.pack('<HHII', 0xCAC3, 0, start_block, 12))
    data = raw[start_block * BLOCK:(start_block + count) * BLOCK]
    chunks.append(struct.pack('<HHII', 0xCAC1, 0, count, 12 + len(data)) + data)
    if start_block + count < total:
        chunks.append(struct.pack('<HHII', 0xCAC3, 0, total - start_block - count, 12))
    header = struct.pack('<IHHHHIIII', 0xED26FF3A, 1, 0, 28, 12, BLOCK, total, len(chunks), 0)
    return header + b''.join(chunks)

def lp_container(partitions):
    """SYNTHLP1: a synthetic super layout read only by the lpunpack test double."""
    table, body, offset = [], b'', 0
    for name, data in partitions:
        table.append([name, offset, len(data)])
        body += data
        offset += len(data)
    header = json.dumps(table).encode()
    return b'SYNTHLP1' + struct.pack('<I', len(header)) + header + body

def super_segments(partitions):
    raw = lp_container(partitions)
    raw += b'\0' * (-len(raw) % BLOCK)
    total, half = len(raw) // BLOCK, len(raw) // BLOCK // 2
    return [sparse_image(raw, 0, half, total), sparse_image(raw, half, total - half, total)]

def super_partitions(drop=(), extra=(), b_data=b'', replace=None):
    parts = []
    for part in DYNAMIC:
        if part not in drop:
            parts += [(f'{part}_a', (replace or {}).get(part, dynamic_image(part))), (f'{part}_b', b_data if part == 'system' else b'')]
    return parts + list(extra)

LPUNPACK_DOUBLE = '''import json, struct, sys
from pathlib import Path
data = Path(sys.argv[1]).read_bytes()
if data[:8] != b'SYNTHLP1':
    sys.exit('lpunpack double: not a SYNTHLP1 container')
size = struct.unpack('<I', data[8:12])[0]
table, base = json.loads(data[12:12 + size]), 12 + size
for name, offset, length in table:
    chunk = data[base + offset:base + offset + length]
    if len(chunk) != length:
        sys.exit('lpunpack double: truncated')
    (Path(sys.argv[2]) / (name + '.img')).write_bytes(chunk)
'''

SIMG2IMG_DOUBLE = '''import struct, sys
out = open(sys.argv[-1], 'wb')
for path in sys.argv[1:-1]:
    data = open(path, 'rb').read()
    magic, major, minor, hsz, csz, blk, total, chunks, _ = struct.unpack('<IHHHHIIII', data[:28])
    if magic != 0xED26FF3A:
        sys.exit('not sparse')
    pos, block = hsz, 0
    for _ in range(chunks):
        if len(data) < pos + csz:
            sys.exit('truncated')
        kind, _, count, size = struct.unpack('<HHII', data[pos:pos + 12])
        body = data[pos + csz:pos + size]
        if len(body) != size - csz:
            sys.exit('truncated')
        if kind == 0xCAC1:
            out.seek(block * blk); out.write(body)
        elif kind == 0xCAC2:
            out.seek(block * blk); out.write(body * (count * blk // 4))
        elif kind not in (0xCAC3, 0xCAC4):
            sys.exit('bad chunk')
        block += count if kind != 0xCAC4 else 0
        pos += size
    out.truncate(max(out.seek(0, 2), total * blk))
'''

def script(source):
    return f'#!{sys.executable}\n'.encode() + source.encode()

def otatools_zip(drop=(), lpunpack=LPUNPACK_DOUBLE):
    entries = {'bin/lpunpack': script(lpunpack), 'lib64/README': b'synthetic'}
    entries['bin/simg2img'] = Path(REAL_SIMG2IMG).read_bytes() if REAL_SIMG2IMG else script(SIMG2IMG_DOUBLE)
    return zip_bytes([(k, v) for k, v in entries.items() if k not in drop])

def android_info(device, bootloader=BOOTLOADER, baseband=BASEBAND):
    return (f'require board={device}\n\nrequire version-bootloader={bootloader}\n'
            f'require version-baseband={baseband}\nrequire partition-exists=vendor_kernel_boot\n').encode()

def zip_bytes(entries):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, data in entries:
            archive.writestr(name, data)
    return buffer.getvalue()

def target_files(device, build=BUILD, ab=None, misc=MISC, drop=(), images=None):
    entries = {'META/ab_partitions.txt': '\n'.join(ab or AB).encode(), 'META/misc_info.txt': misc.encode(),
               'OTA/android-info.txt': android_info(device), 'SYSTEM/build.prop': f'ro.build.version.incremental={build}\n'.encode(),
               'RADIO/bootloader.img': b'synthetic bootloader', 'RADIO/radio.img': b'synthetic radio'}
    entries.update({f'VENDOR/firmware/dauntless/{name}': data for name, data in GSC.items()})
    for part in AB:
        if part not in DYNAMIC and part != 'abl':
            entries[f'IMAGES/{part}.img'] = image(part)
    for part in DYNAMIC:
        # product is stored sparse, as target-files may store it; the checker must flatten it with simg2img.
        entries[f'IMAGES/{part}.img'] = sparse_image(dynamic_image(part)) if part == 'product' else dynamic_image(part)
    entries.update(images or {})
    return zip_bytes([(k, v) for k, v in entries.items() if k not in drop])

def install(device, build=BUILD, top=None, info=None, replace=None, drop=(), extra=(), segments=None):
    top = top or f'{device}-install-{build}'
    files = {'android-info.txt': info or android_info(device),
             'flash-all.sh': b'# synthetic stand-in, not a flasher\n# erase userdata\n',
             'script.txt': b'# synthetic stand-in\n# erase userdata\n',
             'avb_pkmd.bin': PKMD[device],
             f'bootloader-{device}-{BOOTLOADER}.img': b'synthetic bootloader',
             f'radio-{device}-{BASEBAND}.img': b'synthetic radio', **GSC}
    for index, segment in enumerate(segments or super_segments(super_partitions()), 1):
        files[f'super_{index}.img'] = segment
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
        self.work = self.tmp/'work'
        self.serial = itertools.count()
        self.work.mkdir()
        # A repository copy pinned to synthetic AVB keys and (when ssh-keygen exists) a throwaway factory-signing key.
        self.root = self.tmp/'repo'
        for rel in ('os/flavors/frankel.json', 'os/flavors/rango.json', 'os/flavors/blazer.json', 'os/devices/pixel10.json',
                    'os/release-pipeline/frankel.json', 'scripts/tests/fixtures/permission-apex/script/generate-release.sh') + tuple(
                    f'scripts/tests/fixtures/factory-package/device_common-1f5ba267/{f}'
                    for f in ('generate-factory-images-common.sh', 'clear-factory-images-variables.sh')) + tuple(
                    f'os/tests/fixtures/adevtool-17df3b79/adevtool/vendor-skels/google_devices/{d}/firmware/android-info.txt'
                    for d in ('frankel', 'rango', 'blazer')):
            (self.root/rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT/rel, self.root/rel)
        self.edit_json('os/release-pipeline/frankel.json', lambda d: d['identities'].update(
            avbPublicKeySha256=hashlib.sha256(PKMD['frankel']).hexdigest()))
        self.edit_json('os/flavors/rango.json', lambda d: d['identity'].update(
            avbPublicKeySha256=hashlib.sha256(PKMD['rango']).hexdigest()))
        self.key = None
        if SSH:
            self.key = self.keypair('factory')
            for device in ('frankel', 'rango'):
                self.edit_json(f'os/flavors/{device}.json', lambda d: d['factorySigning'].update(
                    principal='synthetic-factory@test', publicKey=' '.join(self.key.with_suffix('.pub').read_text().split()[:2])))
        self.otatools = self.write('otatools.zip', otatools_zip())

    def tearDown(self):
        self.assertEqual(self.before, {p: hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in RETAINED})
        self.assertEqual(list(self.work.iterdir()), [], 'job-owned verification files must be removed')

    def edit_json(self, rel, change):
        path = self.root/rel
        data = json.loads(path.read_text())
        change(data)
        path.write_text(json.dumps(data))

    def keypair(self, name):
        key = self.tmp/f'{name}-key'
        subprocess.run([SSH, '-q', '-t', 'ed25519', '-N', '', '-C', 'synthetic', '-f', str(key)], check=True)
        return key

    def sign(self, path, key=None, namespace='factory images'):
        Path(str(path) + '.sig').unlink(missing_ok=True)
        subprocess.run([SSH, '-q', '-Y', 'sign', '-n', namespace, '-f', str(key or self.key), str(path)], check=True,
                       capture_output=True)

    def write(self, name, data):
        path = self.tmp/name
        path.write_bytes(data)
        return path

    def observation(self, tf, device, **change):
        document = fp.observe(tf, device, BUILD, 'synthetic test', self.root)
        document.update(synthetic=True, **change)
        return self.write(f'{device}-observation-{next(self.serial)}.json', json.dumps(document).encode())

    def firmware(self, device, partitions=('abl',), **change):
        document = {'schema': fp.FIRMWARE, 'device': device, 'build': BUILD, 'source': 'synthetic test', 'synthetic': True,
                    'bootloader': {'sha256': hashlib.sha256(b'synthetic bootloader').hexdigest(), 'partitions': list(partitions)},
                    'radio': {'sha256': hashlib.sha256(b'synthetic radio').hexdigest(), 'partitions': []}}
        document.update(change)
        return self.write(f'{device}-firmware-{next(self.serial)}.json', json.dumps(document).encode())

    def evidence(self, device, tf, **change):
        evidence = dict(target_files_observation=self.observation(tf, device), firmware_observation=self.firmware(device),
                        otatools=self.otatools, otatools_sha256=hashlib.sha256(self.otatools.read_bytes()).hexdigest(),
                        work_dir=self.work, allow_synthetic=True)
        evidence.update(change)
        return evidence

    def classify(self, archive, flavor, tf=None, build=BUILD, key=None, root=None, sign=True, **change):
        if sign and SSH and archive.is_file():
            self.sign(archive)
        evidence = self.evidence(flavor, tf, **change) if tf else dict(work_dir=self.work, **change)
        return fp.classify(archive, flavor, tf, build, key, root or self.root, **evidence)

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
            self.assertEqual((flavor['factorySigning']['namespace'], flavor['factorySigning']['publicKey']), ('factory images', None))
            self.assertEqual(flavor['inventory']['observed'], [])

    def test_drift_from_device_contract_or_pinned_firmware_refuses_the_contract(self):
        path = self.root/'os/flavors/rango.json'
        flavor = json.loads(path.read_text())
        flavor['firmware']['baseband'] = 'g5400i-guessed'
        path.write_text(json.dumps(flavor))
        with self.assertRaisesRegex(fp.ContractError, 'firmware.baseband'):
            fp.load_flavor('rango', self.root)
        with self.assertRaises(fp.ContractError):
            fp.load_flavor('../rango', self.root)

    def test_signing_pin_must_be_complete_and_namespace_from_the_generator(self):
        self.edit_json('os/flavors/frankel.json', lambda d: d['factorySigning'].update(namespace='images'))
        with self.assertRaisesRegex(fp.ContractError, 'factorySigning.namespace'):
            fp.load_flavor('frankel', self.root)
        self.edit_json('os/flavors/frankel.json', lambda d: d['factorySigning'].update(namespace='factory images', principal=None,
                                                                                       publicKey='ssh-ed25519 AAAA'))
        with self.assertRaisesRegex(fp.ContractError, 'principal'):
            fp.load_flavor('frankel', self.root)

    def test_retained_rango_2026103001_is_ota_only_and_never_factory_ready(self):
        retained = fp.load_flavor('rango')['retained']
        self.assertEqual([(r['build'], r['artifactRoles'], r['factoryReady'], r['phoneValidated']) for r in retained],
                         [('2026103001', ['ota-update'], False, False)])
        self.assertEqual(json.loads((ROOT/'os/retained-rango/release.json').read_text())['target']['build'], '2026103001')

    def test_rango_published_avb_digest_is_pinned_with_provenance_and_conflicts_refuse(self):
        rango = fp.load_flavor('rango')
        self.assertEqual(fp.pinned_avb_key(rango), 'aee947942b80c74dab9956209b7f7836a1824d9aeb1e9dd782dc157c63fd8f27')
        self.assertIn('429262439', rango['identity']['avbPublicKeySha256From'])
        self.assertIn('not an independently reread bootstrap journal', rango['identity']['avbProvenance'])
        self.assertEqual(fp.pinned_avb_key(fp.load_flavor('frankel')),
                         json.loads((ROOT/'os/release-pipeline/frankel.json').read_text())['identities']['avbPublicKeySha256'])
        binding = self.write('first-use.json', json.dumps({'avbPublicKeySha256': '1' * 64}).encode())
        archive, tf = self.complete('rango')
        with self.assertRaisesRegex(fp.ContractError, 'identity_conflict'):
            fp.classify(archive, 'rango', tf, BUILD, None, ROOT, first_use_binding=binding)
        with self.assertRaisesRegex(fp.ContractError, 'differs from the pinned'):
            fp.classify(archive, 'rango', tf, BUILD, '1' * 64, ROOT)
        self.edit_json('os/flavors/rango.json', lambda d: d['identity'].update(
            avbPublicKeySha256=json.loads((self.root/'os/release-pipeline/frankel.json').read_text())['identities']['avbPublicKeySha256']))
        with self.assertRaisesRegex(fp.ContractError, 'signer_collision'):
            fp.load_flavor('rango', self.root)

@unittest.skipUnless(SSH, 'needs OpenSSH ssh-keygen')
class Positive(Fixture):
    def test_complete_frankel_package_is_structurally_content_and_signature_verified(self):
        archive, tf = self.complete('frankel')
        self.sign(archive)
        receipt = fp.package(archive, 'frankel', tf, self.tmp/'out', BUILD, None, self.root, **self.evidence('frankel', tf))
        self.assertTrue(receipt['factoryReady'])
        self.assertTrue(receipt['structurallyComplete'] and receipt['contentVerified'] and receipt['signatureVerified'])
        self.assertEqual((receipt['role'], receipt['missing'], receipt['problems']), ('factory-install', [], []))
        self.assertEqual(receipt['inventory']['firmwareCarried'], {'abl': 'bootloader'})
        self.assertEqual(sorted(receipt['content']['dynamic']), sorted(DYNAMIC))
        self.assertEqual(receipt['content']['dynamic']['system']['sha256'], hashlib.sha256(dynamic_image('system')).hexdigest())
        self.assertEqual(receipt['content']['dynamic']['product']['sha256'], hashlib.sha256(dynamic_image('product')).hexdigest())
        self.assertEqual(receipt['content']['tools']['otatools']['sha256'], hashlib.sha256(self.otatools.read_bytes()).hexdigest())
        self.assertEqual(receipt['inventory']['targetFilesSha256'], hashlib.sha256(tf.read_bytes()).hexdigest())
        self.assertTrue(receipt['evidence']['targetFilesObservation']['synthetic'])
        self.assertEqual(receipt['signature']['principal'], 'synthetic-factory@test')
        self.assertEqual(receipt['limits']['phoneValidated'], False)
        written = json.loads((self.tmp/'out/factory-package.json').read_text())
        self.assertEqual(written['files'][archive.name], hashlib.sha256(archive.read_bytes()).hexdigest())
        self.assertEqual((self.tmp/'out'/(archive.name + '.sig')).read_bytes(), Path(str(archive) + '.sig').read_bytes())

    def test_complete_rango_package_with_its_pinned_published_identity(self):
        archive, tf = self.complete('rango')
        result = self.classify(archive, 'rango', tf)
        self.assertTrue(result['factoryReady'], result)
        binding = self.write('first-use.json', json.dumps({'avbPublicKeySha256': hashlib.sha256(PKMD['rango']).hexdigest()}).encode())
        self.assertTrue(self.classify(archive, 'rango', tf, first_use_binding=binding)['factoryReady'])

    def test_structure_alone_is_not_ready(self):
        archive, tf = self.complete('frankel')
        result = self.classify(archive, 'frankel', tf, otatools=None, otatools_sha256=None)
        self.assertTrue(result['structurallyComplete'] and result['signatureVerified'])
        self.assertFalse(result['contentVerified'] or result['factoryReady'])
        self.refused(result, 'otatools (--otatools ZIP')

    def test_cli_exit_codes_and_names_only_observation(self):
        archive, tf = self.complete('rango')
        with mock.patch.object(fp, 'ROOT', self.root), mock.patch('sys.stdout', io.StringIO()):
            self.assertEqual(fp.main(['observe', str(tf), '--flavor', 'rango', '--build', BUILD, '--source', 'synthetic',
                                      '--out', str(self.tmp/'obs.json')]), 0)
        obs = json.loads((self.tmp/'obs.json').read_text())
        self.assertFalse(obs['synthetic'])
        self.assertIn('IMAGES/boot.img', obs['members'])
        self.assertNotIn('SYSTEM/build.prop', obs['members'])
        self.assertNotIn('synthetic boot', json.dumps(obs))
        self.sign(archive)
        common = ['--flavor', 'rango', '--target-files', str(tf), '--target-files-observation', str(self.tmp/'obs.json'),
                  '--otatools', str(self.otatools), '--otatools-sha256', hashlib.sha256(self.otatools.read_bytes()).hexdigest(),
                  '--work-dir', str(self.work), '--build', BUILD]
        with mock.patch.object(fp, 'ROOT', self.root), mock.patch('sys.stdout', io.StringIO()) as out, mock.patch('sys.stderr', io.StringIO()):
            # The CLI never accepts a synthetic firmware observation, so the positive path runs through the API above.
            self.assertEqual(fp.main(['classify', str(archive), *common, '--firmware-observation', str(self.firmware('rango'))]), 3)
            self.assertIn('firmware-observation-synthetic', out.getvalue())
            self.assertEqual(fp.main(['classify', str(archive), '--flavor', 'rango', '--build', '1']), 2)
            self.assertEqual(fp.main(['package', str(archive), *common, '--out', str(self.tmp)]), 2)
            self.assertEqual(fp.main(['package', str(self.write('o.zip', ota('rango'))), *common, '--out', str(self.tmp/'new')]), 3)
            self.assertEqual(fp.main(['observe', str(tf), '--flavor', 'rango', '--build', BUILD, '--source', 's',
                                      '--out', str(self.tmp/'obs.json')]), 2)
        self.assertFalse((self.tmp/'new').exists())

@unittest.skipUnless(SSH, 'needs OpenSSH ssh-keygen')
class Signature(Fixture):
    def test_production_contract_has_no_factory_signing_identity(self):
        archive, tf = self.complete('frankel')
        self.sign(archive)
        result = fp.classify(archive, 'frankel', tf, BUILD, None, ROOT, **self.evidence('frankel', tf))
        self.refused(result, 'factory-signing identity: pinned principal and ssh-ed25519 public key for frankel')
        self.assertFalse(result['signatureVerified'])

    def test_missing_wrong_namespace_wrong_key_tampered_and_adjacent_signers(self):
        archive, tf = self.complete('frankel')
        self.refused(self.classify(archive, 'frankel', tf, sign=False), 'factory:frankel-install-2026103101.zip.sig')
        self.sign(archive, namespace='file')
        self.refused(self.classify(archive, 'frankel', tf, sign=False), 'signature-invalid')
        other = self.keypair('other')
        self.sign(archive, key=other)
        # An attacker-supplied allowed_signers beside the archive is never read.
        (self.tmp/'allowed_signers').write_text('synthetic-factory@test namespaces="factory images" '
                                                + ' '.join(other.with_suffix('.pub').read_text().split()[:2]) + '\n')
        self.refused(self.classify(archive, 'frankel', tf, sign=False), 'signature-invalid')
        self.sign(archive)
        signature = Path(str(archive) + '.sig').read_bytes()
        tampered = self.write(archive.name, install('frankel', replace={'script.txt': b'# erase userdata\n# changed\n'}))
        Path(str(tampered) + '.sig').write_bytes(signature)
        self.refused(self.classify(tampered, 'frankel', tf, sign=False), 'signature-invalid')

@unittest.skipUnless(SSH, 'needs OpenSSH ssh-keygen')
class Content(Fixture):
    def segments(self, **change):
        return super_segments(super_partitions(**change))

    def test_real_simg2img_reads_the_test_sparse_encoding(self):
        if not REAL_SIMG2IMG:
            self.skipTest('no AOSP simg2img on this runner')
        parts = super_partitions()
        segments = [self.write(f's{i}.img', s) for i, s in enumerate(super_segments(parts))]
        subprocess.run([REAL_SIMG2IMG, *map(str, segments), str(self.tmp/'raw')], check=True, capture_output=True)
        expected = lp_container(parts)
        self.assertEqual((self.tmp/'raw').read_bytes()[:len(expected)], expected)

    def test_missing_extra_corrupt_truncated_and_mixed_dynamic_partitions(self):
        _, tf = self.complete('frankel')
        truncated = self.segments()
        truncated[1] = truncated[1][:-2000]
        for segments, code in ((self.segments(drop=('vendor',)), 'dynamic-missing:vendor'),
                               (self.segments(extra=[('odm_a', b'odm')]), 'dynamic-extra:odm'),
                               (self.segments(replace={'system': dynamic_image('system')[:-1] + b'X'}), 'image-mismatch:dynamic system'),
                               (self.segments(replace={'vendor': dynamic_image('vendor')[:-1]}), 'image-mismatch:dynamic vendor'),
                               (self.segments(b_data=b'stale'), 'mixed-partition:system_b.img'),
                               (truncated, 'content-tool-failed:simg2img super')):
            result = self.classify(self.write('a.zip', install('frankel', segments=segments)), 'frankel', tf)
            self.refused(result, code)
            self.assertTrue(result['structurallyComplete'] and not result['contentVerified'])
        not_lp = [sparse_image(b'not a super image' * 500)]
        self.refused(self.classify(self.write('a.zip', install('frankel', segments=not_lp)), 'frankel', tf), 'content-tool-failed:lpunpack')

    def test_tool_identity_and_bounded_work(self):
        archive, tf = self.complete('frankel')
        self.refused(self.classify(archive, 'frankel', tf, otatools_sha256='0' * 64), 'tool-identity-mismatch:otatools')
        bare = self.write('bare.zip', otatools_zip(drop=('bin/lpunpack',)))
        self.refused(self.classify(archive, 'frankel', tf, otatools=bare, otatools_sha256=hashlib.sha256(bare.read_bytes()).hexdigest()),
                     'otatools:bin/lpunpack')
        self.refused(self.classify(archive, 'frankel', tf, max_work_bytes=4096), 'work-space:')
        tf = self.write('tf.zip', target_files('frankel', drop=('IMAGES/vendor.img',)))
        self.refused(self.classify(archive, 'frankel', tf), 'target-files:IMAGES/vendor.img (dynamic)')

@unittest.skipUnless(SSH, 'needs OpenSSH ssh-keygen')
class Evidence(Fixture):
    def test_firmware_carried_needs_bound_observation_never_absence(self):
        archive, tf = self.complete('frankel')
        self.refused(self.classify(archive, 'frankel', tf, firmware_observation=None), 'firmware-observation for A/B partitions without an IMAGES/ image: abl')
        self.refused(self.classify(archive, 'frankel', tf, firmware_observation=self.firmware('frankel', partitions=())),
                     'firmware-carried evidence for abl')
        wrong = self.firmware('frankel', bootloader={'sha256': '0' * 64, 'partitions': ['abl']})
        self.refused(self.classify(archive, 'frankel', tf, firmware_observation=wrong), 'firmware-observation-mismatch:bootloader')
        both = self.firmware('frankel', radio={'sha256': hashlib.sha256(b'synthetic radio').hexdigest(), 'partitions': ['abl']})
        self.refused(self.classify(archive, 'frankel', tf, firmware_observation=both), 'partition in both images abl')
        unsynthetic = json.loads(self.firmware('frankel').read_text())
        unsynthetic['synthetic'] = False
        fw = self.write('fw-real-shape.json', json.dumps(unsynthetic).encode())
        result = self.classify(archive, 'frankel', tf, allow_synthetic=False, firmware_observation=fw)
        self.refused(result, 'target-files-observation-synthetic')
        self.assertNotIn('firmware-observation-synthetic', ' '.join(result['problems']))
        self.refused(self.classify(archive, 'frankel', tf, allow_synthetic=False), 'firmware-observation-synthetic')

    def test_target_files_observation_binds_hash_and_member_names(self):
        archive, tf = self.complete('frankel')
        self.refused(self.classify(archive, 'frankel', tf, target_files_observation=None), 'target-files-observation (release:')
        other = self.write('other-tf.zip', target_files('frankel', images={'RADIO/extra.img': b'x'}))
        self.refused(self.classify(archive, 'frankel', tf, target_files_observation=self.observation(other, 'frankel')),
                     'observation-mismatch:target-files sha256', 'observation-mismatch:member names')
        self.refused(self.classify(archive, 'frankel', tf, target_files_observation=self.observation(tf, 'frankel', build='2026103102')),
                     'observation-mismatch:build')

@unittest.skipUnless(SSH, 'needs OpenSSH ssh-keygen')
class Negative(Fixture):
    def test_ota_only_is_applicable_in_place_but_never_factory(self):
        for device in ('frankel', 'rango'):
            result = self.classify(self.write('o.zip', ota(device)), device, self.complete(device)[1])
            self.refused(result, 'not-factory:ota-update')
            self.assertTrue(result['otaApplicable'])
            self.assertFalse(result['installMode']['dataWipe'] or result['structurallyComplete'])
            with self.assertRaises(fp.Refused):
                fp.package(self.tmp/'o.zip', device, self.complete(device)[1], self.tmp/'out', BUILD, None, self.root,
                           **self.evidence(device, self.complete(device)[1]))
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
        tf = self.write('tf.zip', target_files('frankel', drop=('META/ab_partitions.txt',)))
        self.refused(fp.classify(archive, 'frankel', tf, BUILD, None, self.root, work_dir=self.work), 'target-files:META/ab_partitions.txt')
        tf = self.write('tf.zip', target_files('frankel', drop=('RADIO/radio.img',)))
        self.refused(self.classify(archive, 'frankel', tf), 'target-files:RADIO/radio.img')

    def test_wrong_device(self):
        archive, _ = self.complete('frankel')
        self.refused(self.classify(archive, 'rango', self.complete('rango')[1]),
                     'wrong-device:archive directory frankel-install', 'wrong-device:android-info board')
        tf = self.write('tf.zip', target_files('frankel'))
        self.refused(self.classify(self.complete('rango')[0], 'rango', tf, target_files_observation=None), 'wrong-device:target-files board')

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

    def test_unsigned_or_mismatched_avb_identity_and_build(self):
        archive, tf = self.complete('frankel')
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

class Generator(Fixture):
    def test_pinned_device_common_fixtures_are_byte_identical_and_never_executable(self):
        provenance = json.loads((GENERATOR/'PROVENANCE.json').read_text())
        self.assertEqual(provenance['revision'], '1f5ba2671e4b04e3bd8899b3df2c5e30a894b35c')
        for entry in provenance['files']:
            data = (GENERATOR/entry['fixture']).read_bytes()
            self.assertEqual(hashlib.sha1(b'blob %d\0' % len(data) + data).hexdigest(), entry['gitBlob'])
            self.assertEqual(hashlib.sha256(data).hexdigest(), entry['sha256'])
            self.assertFalse((GENERATOR/entry['fixture']).stat().st_mode & 0o111)
            self.assertIn(b'Licensed under the Apache License, Version 2.0', data)
        for name in ('frankel', 'rango'):
            pinned = fp.load_flavor(name)['inventory']['generatorFixture']['files']
            self.assertEqual(pinned, {e['fixture']: e['sha256'] for e in provenance['files']})

    def test_contract_layout_claims_are_anchored_in_the_generator(self):
        flavor = fp.load_flavor('rango')
        self.assertEqual(flavor['artifacts']['factory']['generatorMembers']['gscFirmware']['source'], 'VENDOR/firmware/dauntless/')
        self.edit_json('os/flavors/rango.json', lambda d: d['artifacts']['factory']['generatorMembers']['gscFirmware'].update(
            source='VENDOR/firmware/epic/'))
        with self.assertRaisesRegex(fp.ContractError, 'gscFirmware.source'):
            fp.load_flavor('rango', self.root)
        self.edit_json('os/flavors/rango.json', lambda d: (d['artifacts']['factory']['generatorMembers']['gscFirmware'].update(
            source='VENDOR/firmware/dauntless/'), d['artifacts']['factory']['generatorMembers']['anchors'].append(
            'cp tmp/RADIO/guessed.img tmp/$PRODUCT-$VERSION/guessed.img')))
        with self.assertRaisesRegex(fp.ContractError, 'generator anchor'):
            fp.load_flavor('rango', self.root)

    def test_frankel_and_rango_are_pinned_and_an_unpinned_contract_never_completes(self):
        for name in ('frankel', 'rango'):
            self.assertTrue(fp.pinned_layout(fp.load_flavor(name)), name)
        # An older contract shape (no generatorMembers, as other flavors may still use) loads but cannot complete.
        def unpin(d):
            factory = d['artifacts']['factory']
            for key in ('generatorMembers', 'optimizeMembers', 'intermediate'):
                factory.pop(key)
            d['inventory']['generatorFixture'] = {'status': 'not in this repository', 'files': ['generate-factory-images-common.sh']}
        self.edit_json('os/flavors/rango.json', unpin)
        self.assertFalse(fp.pinned_layout(fp.load_flavor('rango', self.root)))
        archive, tf = self.complete('rango')
        result = self.classify(archive, 'rango', tf)
        self.refused(result, 'contract:rango pinned generator layout')
        self.assertFalse(result['structurallyComplete'])

    def test_fixture_drift_refuses_the_contract(self):
        target = self.root/'scripts/tests/fixtures/factory-package/device_common-1f5ba267'
        (target/'generate-factory-images-common.sh').write_bytes(
            (GENERATOR/'generate-factory-images-common.sh').read_bytes().replace(b'GSCFIRMWARESRC=dauntless', b'GSCFIRMWARESRC=epic'))
        with self.assertRaisesRegex(fp.ContractError, 'generatorFixture generate-factory-images-common.sh sha256'):
            fp.load_flavor('frankel', self.root)

@unittest.skipUnless(SSH, 'needs OpenSSH ssh-keygen')
class GscFirmware(Fixture):
    def test_gsc_firmware_must_be_present_byte_equal_and_nothing_else(self):
        archive, tf = self.complete('rango')
        self.assertEqual(self.classify(archive, 'rango', tf)['inventory']['gscFirmware'], sorted(GSC))
        self.refused(self.classify(self.write('a.zip', install('rango', drop=('evt.ec.bin',))), 'rango', tf),
                     'factory:evt.ec.bin (GSC firmware from target-files)')
        self.refused(self.classify(self.write('a.zip', install('rango', replace={'prod.ec.bin': b'other'})), 'rango', tf),
                     'mixed-firmware:prod.ec.bin differs from target-files VENDOR/firmware/dauntless/prod.ec.bin')
        self.refused(self.classify(self.write('a.zip', install('rango', replace={'notes.txt': b'x'})), 'rango', tf),
                     'unexpected-member:notes.txt')
        bat = self.classify(self.write('a.zip', install('rango', replace={'flash-all.bat': b':: synthetic'})), 'rango', tf)
        self.assertTrue(bat['factoryReady'], bat)
        tf = self.write('tf.zip', target_files('rango', drop=tuple(f'VENDOR/firmware/dauntless/{n}' for n in GSC)))
        self.refused(self.classify(archive, 'rango', tf), 'target-files:VENDOR/firmware/dauntless/* (GSC firmware')
        tf = self.write('tf.zip', target_files('rango', images={'VENDOR/firmware/dauntless/sub/x.bin': b'x'}))
        self.refused(self.classify(archive, 'rango', tf), 'gsc-firmware-nested:VENDOR/firmware/dauntless/sub/x.bin')

    def test_generator_intermediate_is_refused_as_not_the_release_artifact(self):
        top = f'rango-factory-{BUILD}'
        intermediate = zip_bytes([(f'{top}/image-rango-{BUILD}.zip', b'synthetic img zip'), (f'{top}/flash-all.sh', b'# synthetic'),
                                  (f'{top}/bootloader-rango-{BOOTLOADER}.img', b'synthetic bootloader')])
        result = self.classify(self.write(f'rango-factory-{BUILD}.zip', intermediate), 'rango', self.complete('rango')[1])
        self.refused(result, 'not-factory:factory-intermediate')
        self.assertEqual(result['role'], 'factory-intermediate')

class PeerIdentity(Fixture):
    """FOLD-FACTORY-004: AVB pins compared across every flavor through the real loader, never recursively."""
    def frankel_pin(self):
        return json.loads((self.root/'os/release-pipeline/frankel.json').read_text())['identities']['avbPublicKeySha256']

    def set_pin(self, flavor, value, field='avbPublicKeySha256'):
        self.edit_json(f'os/flavors/{flavor}.json', lambda d: d['identity'].update({field: value}))

    def test_repository_flavors_have_distinct_or_unknown_pins(self):
        pins = {name: fp.pinned_avb_key(fp.load_flavor(name)) for name in ('frankel', 'rango', 'blazer')}
        self.assertIsNone(pins['blazer'])
        self.assertEqual(len({p for p in pins.values() if p}), 2)

    def test_blazer_rango_collision_refuses_both_loads(self):
        self.set_pin('blazer', hashlib.sha256(PKMD['rango']).hexdigest())
        for name, peer in (('blazer', 'rango'), ('rango', 'blazer')):
            with self.assertRaisesRegex(fp.ContractError, f'{name}: signer_collision: the pinned AVB key equals the {peer} key'):
                fp.load_flavor(name, self.root)
        fp.load_flavor('frankel', self.root)

    def test_frankel_collision_refuses_frankel_and_the_duplicate(self):
        self.set_pin('blazer', self.frankel_pin())
        for name, peer in (('blazer', 'frankel'), ('frankel', 'blazer')):
            with self.assertRaisesRegex(fp.ContractError, f'{name}: signer_collision: the pinned AVB key equals the {peer} key'):
                fp.load_flavor(name, self.root)

    def test_distinct_and_unknown_pins_load(self):
        self.set_pin('blazer', '2' * 64)
        for name in ('frankel', 'rango', 'blazer'):
            fp.load_flavor(name, self.root)
        self.set_pin('blazer', None)
        self.set_pin('rango', None)
        # Two unknown identities are not a collision.
        self.assertEqual([fp.pinned_avb_key(fp.load_flavor(n, self.root), self.root) for n in ('rango', 'blazer')], [None, None])

    def test_malformed_peer_identity_is_never_skipped(self):
        cases = ((lambda d: d['identity'].update(avbPublicKeySha256='not-a-sha256'), 'peer blazer: avbPublicKeySha256 is not a sha256'),
                 (lambda d: d['identity'].update(avbPublicKeySha256=7), 'peer blazer: avbPublicKeySha256 is not a sha256'),
                 (lambda d: d['identity'].update(avbPublicKeySha256='A' * 64), 'peer blazer: avbPublicKeySha256 is not a sha256'),
                 (lambda d: d['identity'].update(avbPublicKeySha256=None, avbPublicKeySha256From='os/release-pipeline/frankel.json identities.missing'),
                  'peer blazer: avbPublicKeySha256From .* does not resolve'),
                 (lambda d: d['identity'].update(avbPublicKeySha256=None, avbPublicKeySha256From='os/release-pipeline/frankel.json target.device'),
                  'peer blazer: avbPublicKeySha256From .* is not a sha256'),
                 (lambda d: d['identity'].update(avbPublicKeySha256From=['x']), 'peer blazer: avbPublicKeySha256From is not text'),
                 (lambda d: d.update(identity='none'), 'peer blazer: identity is not an object'),
                 (lambda d: d.pop('identity'), 'peer blazer contract has no identity'),
                 (lambda d: d.update(flavor='rango'), 'peer blazer contract has no identity for flavor blazer'))
        original = (self.root/'os/flavors/blazer.json').read_text()
        for change, pattern in cases:
            (self.root/'os/flavors/blazer.json').write_text(original)
            self.edit_json('os/flavors/blazer.json', change)
            with self.assertRaisesRegex(fp.ContractError, 'rango: ' + pattern):
                fp.load_flavor('rango', self.root)
        (self.root/'os/flavors/blazer.json').write_text('{ not json')
        with self.assertRaisesRegex(fp.ContractError, 'frankel: peer blazer contract is unreadable'):
            fp.load_flavor('frankel', self.root)

    def test_own_malformed_pin_refuses(self):
        self.set_pin('rango', '')
        self.edit_json('os/flavors/rango.json', lambda d: d['identity'].update(avbPublicKeySha256='', avbPublicKeySha256From=None))
        with self.assertRaisesRegex(fp.ContractError, 'rango: avbPublicKeySha256 is not a sha256'):
            fp.load_flavor('rango', self.root)

    def test_peers_are_read_without_recursive_full_loads(self):
        with mock.patch.object(fp, 'load_flavor', wraps=fp.load_flavor) as loader:
            fp.load_flavor('rango', self.root)
        self.assertEqual(loader.call_count, 1)

class Readiness(Fixture):
    """FOLD-FACTORY-004: the optional readiness declaration is strict and can never claim a result."""
    def refuses(self, change, pattern, flavor='blazer'):
        self.edit_json(f'os/flavors/{flavor}.json', change)
        with self.assertRaisesRegex(fp.ContractError, f'{flavor}: ' + pattern):
            fp.load_flavor(flavor, self.root)

    def test_real_declaration_and_absent_block_load(self):
        self.assertEqual({k: v for k, v in fp.load_flavor('blazer')['readiness'].items() if k != 'basis'},
                         {'buildReady': False, 'factoryReady': False, 'phoneValidated': False, 'receipts': []})
        for name in ('frankel', 'rango'):
            self.assertNotIn('readiness', fp.load_flavor(name))
        self.edit_json('os/flavors/rango.json', lambda d: d.update(readiness={'buildReady': False, 'factoryReady': False,
            'phoneValidated': False, 'receipts': ['os-release run 40974306111 receipt']}))
        fp.load_flavor('rango', self.root)

    def test_positive_claims_are_unsupported(self):
        for flag in fp.READINESS_FLAGS:
            original = (self.root/'os/flavors/blazer.json').read_text()
            self.refuses(lambda d: d['readiness'].update({flag: True, 'receipts': ['some real-looking receipt']}),
                         rf"unsupported readiness claim \['{flag}'\]")
            (self.root/'os/flavors/blazer.json').write_text(original)

    def test_strict_types_keys_and_synthetic_receipts(self):
        original = (self.root/'os/flavors/blazer.json').read_text()
        for change, pattern in ((lambda d: d['readiness'].update(factoryReady=0), r"readiness \['factoryReady'\] must be true or false"),
                                (lambda d: d['readiness'].update(phoneValidated='false'), r"readiness \['phoneValidated'\] must be"),
                                (lambda d: d['readiness'].update(receipts='none'), 'readiness.receipts must be a list'),
                                (lambda d: d['readiness'].update(receipts=['']), 'readiness.receipts must be a list'),
                                (lambda d: d['readiness'].update(receipts=[{'id': 1}]), 'readiness.receipts must be a list'),
                                (lambda d: d['readiness'].update(basis=1), 'readiness.basis must be text'),
                                (lambda d: d['readiness'].update(otaReady=False), r"readiness keys: unknown \['otaReady'\]"),
                                (lambda d: d['readiness'].pop('buildReady'), r"readiness keys: unknown \[\], missing \['buildReady'\]"),
                                (lambda d: d.update(readiness=[False]), 'readiness must be an object'),
                                (lambda d: d['readiness'].update(receipts=['Synthetic test package receipt']),
                                 'readiness.receipts names synthetic evidence, which is never a readiness receipt')):
            (self.root/'os/flavors/blazer.json').write_text(original)
            self.refuses(change, pattern)

    @unittest.skipUnless(SSH, 'needs OpenSSH ssh-keygen')
    def test_synthetic_artifact_success_never_changes_declared_readiness(self):
        archive, tf = self.complete('rango')
        self.assertTrue(self.classify(archive, 'rango', tf)['factoryReady'])
        self.assertEqual(fp.load_flavor('blazer', self.root)['readiness']['factoryReady'], False)
        self.assertNotIn('readiness', fp.load_flavor('rango', self.root))

if __name__ == '__main__':
    unittest.main()
