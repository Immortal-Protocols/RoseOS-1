"""Argument checks of scripts/apply-os-changes.py that run before any source access, and the default
locked-session patch contract of product builds."""
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT/'scripts/apply-os-changes.py'
sys.path.insert(0, str(ROOT/'scripts'))
from background_apks import BACKGROUND_FRAMEWORK_PATCHES  # noqa: E402
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

def added(patch, path):
    body = patch.split(f'diff --git a/{path} b/{path}\n', 1)[1].split('\ndiff --git ', 1)[0]
    return [l[1:] for l in body.splitlines() if l.startswith('+') and not l.startswith('+++')]

class LockedSessionDefaultTest(unittest.TestCase):
    """Every product build carries the locked-session resources, symbols and owner-gated guards, switched off
    (the retained P1 tree has them, and the release verifier requires the resource present and false)."""
    PATCH = (ROOT/'os/patches/locked-control-frameworks-base.patch').read_text()

    def test_product_builds_always_apply_the_locked_session_patch_last(self):
        self.assertEqual(BACKGROUND_FRAMEWORK_PATCHES[-1], 'locked-control-frameworks-base.patch')
        patcher = SCRIPT.read_text()
        self.assertIn("projects['frameworks/base'] += BACKGROUND_FRAMEWORK_PATCHES", patcher)
        self.assertNotIn("projects['frameworks/base'].append('locked-control-frameworks-base.patch')", patcher)
        for patch in BACKGROUND_FRAMEWORK_PATCHES:
            self.assertTrue((ROOT/'os/patches'/patch).is_file(), patch)

    def test_resources_and_symbols_are_coherent_and_off(self):
        config = added(self.PATCH, 'core/res/res/values/config.xml')
        self.assertIn('    <bool name="config_pixelComputerControlLockedSessions">false</bool>', config)
        self.assertIn('    <integer name="config_pixelComputerControlLockedAuthMaxAgeMs">14400000</integer>', config)
        self.assertFalse(any('>true<' in line for line in config))
        self.assertEqual(added(self.PATCH, 'core/res/res/values/symbols.xml'), [
            '    <java-symbol type="bool" name="config_pixelComputerControlLockedSessions" />',
            '    <java-symbol type="integer" name="config_pixelComputerControlLockedAuthMaxAgeMs" />'])

    def test_off_resource_refuses_locked_creation_and_guards_stay(self):
        allowlist = '\n'.join(added(self.PATCH, 'services/companion/java/com/android/server/companion/virtual/'
            'computercontrol/ComputerControlAllowlistController.java'))
        self.assertRegex(allowlist, r'if \(!resources\.getBoolean\(R\.bool\.config_pixelComputerControlLockedSessions\)\) \{\s*return false;')
        self.assertIn('if (!hasPrototypeControl(packageName, packageManager)) {', allowlist)
        session = '\n'.join(added(self.PATCH, 'services/companion/java/com/android/server/companion/virtual/'
            'computercontrol/ComputerControlSessionImpl.java'))
        self.assertEqual(session.count('isOwnerGatedWhileLocked('), 4)  # three guards and the helper

    def test_only_the_owner_option_turns_locked_sessions_on(self):
        patcher = SCRIPT.read_text()
        self.assertEqual(len(re.findall(r'config_pixelComputerControlLockedSessions">true<', patcher)), 1)
        self.assertIn("    if a.locked_control:\n        marker = '    <bool name=\"config_pixelComputerControlLockedSessions\">false</bool>'", patcher)
        config = json.loads((ROOT/'os/release-pipeline/frankel.json').read_text())
        self.assertEqual((config['framework']['lockedSessions'], config['framework']['prototypeAgentsEmpty']), (False, True))
        self.assertNotIn('--locked-control', config['host']['integrate']['reconcile']['applyArgs'])
        # The pinned candidate's own patcher predates this default; reconcile runs this checkout's patcher on its bundle.
        self.assertEqual(config['host']['integrate']['reconcile']['patcher'], 'trusted')
        self.assertNotIn('--prototype-control', (ROOT/'scripts/host-integrate.py').read_text())

if __name__ == '__main__':
    unittest.main()
