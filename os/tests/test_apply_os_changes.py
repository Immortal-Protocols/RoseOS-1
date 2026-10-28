"""Argument checks of scripts/apply-os-changes.py that run before any source access."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[2]/'scripts/apply-os-changes.py'
CHANNEL_ERROR = 'needs --updater-url'

def run(*args):
    with tempfile.TemporaryDirectory() as missing:
        return subprocess.run([sys.executable, str(SCRIPT), str(Path(missing)/'no-source'), '--check', *args],
            capture_output=True, text=True)

class UpdaterChannelTest(unittest.TestCase):
    def test_product_build_without_channel_is_refused(self):
        for args in (['--agent-tray'], ['--background-policy', 'p.json', '--background-agent', 'a.apk']):
            result = run(*args)
            self.assertEqual(result.returncode, 2, args)
            self.assertIn(CHANNEL_ERROR, result.stderr, args)

    def test_explicit_choice_passes_the_argument_check(self):
        # Later checks fail on the missing source tree; the channel check itself must not.
        for args in (['--agent-tray', '--updater-url', 'https://example.invalid/channel/'],
                     ['--agent-tray', '--keep-upstream-updater'], []):
            result = run(*args)
            self.assertNotIn(CHANNEL_ERROR, result.stderr, args)

    def test_conflicting_choices_are_refused(self):
        result = run('--agent-tray', '--updater-url', 'https://example.invalid/', '--keep-upstream-updater')
        self.assertEqual(result.returncode, 2)
        self.assertIn('mutually exclusive', result.stderr)

class InstalledIdentitiesTest(unittest.TestCase):
    def test_needs_the_background_policy(self):
        result = run('--installed-identities', 'installed.json')
        self.assertEqual(result.returncode, 2)
        self.assertIn('--installed-identities needs --background-policy', result.stderr)

if __name__ == '__main__':
    unittest.main()
