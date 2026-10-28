"""Integration inputs land with fresh mtimes; identical bytes are a no-op; different bytes are refused."""
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
SCRIPTS = Path(__file__).resolve().parents[2]/'scripts'
sys.path.insert(0, str(SCRIPTS))
from integration_copy import Conflict, copy_fresh, install_bytes, install_copy

OLD = 946684800  # 2000-01-01, older than any build output

class IntegrationCopyTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.source = self.root/'Agent.apk'
        self.source.write_bytes(b'new signed bytes')
        self.source.chmod(0o640)
        os.utime(self.source, (OLD, OLD))
        self.target = self.root/'tree/packages/apps/PixelBackgroundControl/Agent.apk'

    def leftovers(self):
        return [p.name for p in self.root.rglob('.*')]

    def test_old_dated_input_becomes_fresh_with_its_mode(self):
        before = time.time() - 1
        self.assertTrue(install_copy(self.source, self.target))
        self.assertEqual(self.target.read_bytes(), b'new signed bytes')
        self.assertGreaterEqual(self.target.stat().st_mtime, before)
        self.assertEqual(self.target.stat().st_mode & 0o777, 0o640)
        self.assertEqual(self.leftovers(), [])

    def test_identical_existing_target_is_left_untouched(self):
        self.target.parent.mkdir(parents=True)
        self.target.write_bytes(b'new signed bytes')
        os.utime(self.target, (OLD, OLD))
        self.assertFalse(install_copy(self.source, self.target))
        self.assertEqual(self.target.stat().st_mtime, OLD)

    def test_different_existing_target_or_symlink_is_refused(self):
        self.target.parent.mkdir(parents=True)
        self.target.write_bytes(b'other bytes')
        with self.assertRaisesRegex(Conflict, 'Unexpected existing overlay target'):
            install_copy(self.source, self.target)
        self.assertEqual(self.target.read_bytes(), b'other bytes')
        self.target.unlink()
        outside = self.root/'outside.apk'
        outside.write_bytes(b'new signed bytes')
        self.target.symlink_to(outside)
        with self.assertRaisesRegex(Conflict, 'symlink'):
            install_copy(self.source, self.target)
        self.assertEqual(self.leftovers(), [])

    def test_generated_bytes_follow_the_same_rules(self):
        blueprint = self.root/'tree/packages/apps/RosePreinstall/M/Android.bp'
        before = time.time() - 1
        self.assertTrue(install_bytes(blueprint, b'android_app_import {}\n'))
        self.assertGreaterEqual(blueprint.stat().st_mtime, before)
        self.assertEqual(blueprint.stat().st_mode & 0o777, 0o644)
        os.utime(blueprint, (OLD, OLD))
        self.assertFalse(install_bytes(blueprint, b'android_app_import {}\n'))
        self.assertEqual(blueprint.stat().st_mtime, OLD)
        with self.assertRaises(Conflict):
            install_bytes(blueprint, b'different\n')

    def test_copy_fresh_replaces_an_output_with_a_new_mtime(self):
        output = self.root/'out/Agent-unsigned.apk'
        output.parent.mkdir()
        output.write_bytes(b'stale')
        before = time.time() - 1
        copy_fresh(self.source, output)
        self.assertEqual(output.read_bytes(), b'new signed bytes')
        self.assertGreaterEqual(output.stat().st_mtime, before)

    def test_integration_scripts_no_longer_preserve_source_mtimes(self):
        for name in ('apply-os-changes.py', 'build-background-agent.py'):
            text = (SCRIPTS/name).read_text()
            self.assertNotIn('copy2', text, name)
            self.assertIn('integration_copy', text, name)

if __name__ == '__main__':
    unittest.main()
