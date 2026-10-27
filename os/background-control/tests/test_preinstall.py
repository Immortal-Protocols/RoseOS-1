"""Preinstall planning for policy-pinned APKs; synthetic bytes only, no device or private APK."""
import hashlib
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[3]/'scripts'))
from background_apks import preinstall_blueprint, preinstall_make_block, preinstall_plan

class PreinstallTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        def apk(name, data):
            path = Path(self.temp.name)/name
            path.write_bytes(data)
            return path, hashlib.sha256(data).hexdigest()
        self.agent, agent_sha = apk('Agent.apk', b'controller')
        self.voice, voice_sha = apk('rose-agent.apk', b'rose agent')
        self.calc, calc_sha = apk('calculator.apk', b'calculator')
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
        altered.write_bytes(b'rose agent, re-signed')
        for apks in ([self.agent], [self.voice, self.voice], [altered]):
            with self.subTest(apks=apks), self.assertRaises(ValueError):
                preinstall_plan(self.policy, apks)

    def test_blueprint_keeps_existing_signature_and_is_not_privileged(self):
        text = preinstall_blueprint('RosePreinstall_dev_pixel_voice')
        for required in ('android_app_import {', 'name: "RosePreinstall_dev_pixel_voice"', 'apk: "app.apk"',
                'presigned: true', 'product_specific: true'):
            self.assertIn(required, text)
        for forbidden in ('privileged', 'certificate', 'platform'):
            self.assertNotIn(forbidden, text)

    def test_make_block_lists_modules(self):
        block = preinstall_make_block(['RosePreinstall_a', 'RosePreinstall_b'])
        self.assertTrue(block.startswith('\n'))
        self.assertIn('PRODUCT_PACKAGES += RosePreinstall_a RosePreinstall_b\n', block)

if __name__ == '__main__':unittest.main()
