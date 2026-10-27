"""Preinstall planning for policy-pinned APKs; synthetic bytes only, no device or private APK."""
import hashlib
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile
sys.path.insert(0, str(Path(__file__).resolve().parents[3]/'scripts'))
from background_apks import preinstall_blueprint, preinstall_make_block, preinstall_plan, preprocessed_issues

def apk_bytes(tag, dex_compressed=False, misalign=False, native=None):
    """A minimal APK-shaped ZIP: classes.dex stored and 4-byte aligned unless asked otherwise, and an
    optional lib/ entry ('stored' page aligned, or 'deflated')."""
    path = Path(tempfile.mkstemp(suffix='.zip')[1])
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr('AndroidManifest.xml', tag, compress_type=zipfile.ZIP_DEFLATED)
        info = zipfile.ZipInfo('classes.dex')
        info.compress_type = zipfile.ZIP_DEFLATED if dex_compressed else zipfile.ZIP_STORED
        data_offset = archive.fp.tell() + 30 + len(info.filename)
        info.extra = b'\0' * ((-data_offset) % 4 + (1 if misalign else 0))
        archive.writestr(info, b'dex\n' + tag.encode() * 64)
        if native:
            lib = zipfile.ZipInfo('lib/arm64-v8a/libx.so')
            lib.compress_type = zipfile.ZIP_STORED if native == 'stored' else zipfile.ZIP_DEFLATED
            data_offset = archive.fp.tell() + 30 + len(lib.filename)
            lib.extra = b'\0' * ((-data_offset) % 4096)
            archive.writestr(lib, b'\x7fELF' + tag.encode() * 64)
    data = path.read_bytes()
    path.unlink()
    return data

class PreinstallTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        def apk(name, data):
            path = Path(self.temp.name)/name
            path.write_bytes(data)
            return path, hashlib.sha256(data).hexdigest()
        self.apk = apk
        self.agent, agent_sha = apk('Agent.apk', apk_bytes('controller'))
        self.voice, voice_sha = apk('rose-agent.apk', apk_bytes('rose agent'))
        self.calc, calc_sha = apk('calculator.apk', apk_bytes('calculator'))
        self.policy = {'schemaVersion': 1, 'sdk': 37,
            'agent': {'packageName': 'dev.pixel.backgroundcontrol', 'certificates': ['A'*64], 'apkSha256': agent_sha},
            'voice': {'packageName': 'dev.pixel.voice', 'certificates': ['A'*64], 'apkSha256': voice_sha},
            'targets': [{'packageName': 'dev.pixel.calculator.agent', 'certificates': ['A'*64], 'apkSha256': calc_sha}]}

    def test_pinned_rose_agent_and_target_are_planned(self):
        plan = preinstall_plan(self.policy, [self.calc, self.voice])
        self.assertEqual([(p['module'], p['packageName']) for p in plan], [
            ('RosePreinstall_dev_pixel_calculator_agent', 'dev.pixel.calculator.agent'),
            ('RosePreinstall_dev_pixel_voice', 'dev.pixel.voice')])
        self.assertEqual(plan[1]['apk'], self.voice)

    def test_rejects_unpinned_controller_duplicate_and_altered_bytes(self):
        altered = Path(self.temp.name)/'altered.apk'
        altered.write_bytes(apk_bytes('rose agent, re-signed'))
        for apks in ([self.agent], [self.voice, self.voice], [altered]):
            with self.subTest(apks=apks), self.assertRaises(ValueError):
                preinstall_plan(self.policy, apks)

    def test_blueprint_keeps_existing_signature_and_is_not_privileged(self):
        text = preinstall_blueprint('RosePreinstall_dev_pixel_voice')
        for required in ('android_app_import {', 'name: "RosePreinstall_dev_pixel_voice"', 'apk: "app.apk"',
                'presigned: true', 'preprocessed: true', 'product_specific: true'):
            self.assertIn(required, text)
        for forbidden in ('privileged', 'certificate', 'platform'):
            self.assertNotIn(forbidden, text)

    def test_rejects_packages_sharing_a_module_name(self):
        first, first_sha = self.apk('first.apk', apk_bytes('first'))
        second, second_sha = self.apk('second.apk', apk_bytes('second'))
        self.policy['targets'] = [
            {'packageName': 'dev.foo_bar', 'certificates': ['A'*64], 'apkSha256': first_sha},
            {'packageName': 'dev_foo.bar', 'certificates': ['A'*64], 'apkSha256': second_sha}]
        self.assertEqual(len(preinstall_plan(self.policy, [first])), 1)
        with self.assertRaisesRegex(ValueError, 'RosePreinstall_dev_foo_bar'):
            preinstall_plan(self.policy, [first, second])

    def test_preprocessed_import_follows_pinned_validator(self):
        # Matches the builder's pinned validator: compressed dex is fine for a non-privileged import.
        for name, data in (('stored.apk', apk_bytes('a')), ('deflated-dex.apk', apk_bytes('b', dex_compressed=True)),
                ('stored-lib.apk', apk_bytes('c', native='stored'))):
            with self.subTest(name):
                self.assertEqual(preprocessed_issues(self.apk(name, data)[0]), [])
        for name, data, problem in (('compressed-lib.apk', apk_bytes('rose agent', native='deflated'), 'compressed'),
                ('misaligned.apk', apk_bytes('rose agent', misalign=True), 'not aligned')):
            with self.subTest(name):
                path, sha = self.apk(name, data)
                self.assertIn(problem, ' '.join(preprocessed_issues(path)))
                self.policy['voice']['apkSha256'] = sha
                with self.assertRaisesRegex(ValueError, 'cannot be imported unchanged'):
                    preinstall_plan(self.policy, [path])

    def test_make_block_lists_modules(self):
        block = preinstall_make_block(['RosePreinstall_a', 'RosePreinstall_b'])
        self.assertTrue(block.startswith('\n'))
        self.assertIn('PRODUCT_PACKAGES += RosePreinstall_a RosePreinstall_b\n', block)

if __name__ == '__main__':unittest.main()
