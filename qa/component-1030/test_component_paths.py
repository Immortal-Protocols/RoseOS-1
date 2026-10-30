"""QA-COMPONENT-1030-001 (advisory, QA-only). Read-only assertions on the REAL changed files of a RoseOS-1 tree
(QA_SRC, default: this checkout) for the #202 self-update path and the launcher recovery path. Nothing here copies or
re-implements product logic; it checks where and whether the shipped code does what the acceptance rows need.
Source evidence only: no Android build, emulator, device or network.

Rows marked GAP are expected to fail on main 6b30ede (findings sent to owners); they turn green when fixed."""
from pathlib import Path
import os
import re
import unittest

SRC = Path(os.environ.get('QA_SRC', Path(__file__).resolve().parents[2]))
VOICE = SRC/'workspace/rose-agent/app/src/dev/pixel/voice'
PINNED = SRC/'os/tests/fixtures/rose-agent-self-update/PackageInstallerSession.java'


def method(text, signature):
    """Body of the first method whose declaration contains signature (brace-matched)."""
    start = text.index(signature)
    i, depth = text.index('{', start), 0
    while True:
        depth += {'{': 1, '}': -1}.get(text[i], 0)
        if depth == 0:
            return text[start:i + 1]
        i += 1


class Q202PlatformOrder(unittest.TestCase):
    """The patched check sees real APK identity and runs after fs-verity setup (pinned frameworks/base 84183aff)."""

    def test_identity_is_parsed_from_the_added_apk_before_the_system_app_check(self):
        body = method(PINNED.read_text(), 'private PackageLite validateApkInstallLocked()')
        check = body.index('System app updates from unknown sources are blocked')
        self.assertLess(body.index('enableFsVerityToAddedApksWithIdsig()'), check)
        self.assertLess(body.index('mSigningDetails = apk.getSigningDetails()'), check)
        self.assertLess(body.index('mVersionCode = apk.getLongVersionCode()'), check)

    def test_fs_verity_is_enabled_only_from_a_sibling_idsig_file(self):
        body = method(PINNED.read_text(), 'private void enableFsVerityToAddedApksWithIdsig()')
        self.assertIn('new File(file.getPath() + V4Signature.EXT).exists()', body)


class Q202AppSelfUpdatePath(unittest.TestCase):
    """The app-created session on the real self-update route (VoiceService.installSelf -> Delivery.installSelf)."""

    @classmethod
    def setUpClass(cls):
        cls.body = method((VOICE/'Delivery.java').read_text(), 'static void installSelf(')

    def test_session_matches_the_patch_preconditions(self):
        self.assertIn('SessionParams.MODE_FULL_INSTALL', self.body)
        self.assertIn('params.setAppPackageName(self)', self.body)
        for forbidden in ('setStaged', 'setInstallAsApex', 'MODE_INHERIT_EXISTING'):
            self.assertNotIn(forbidden, self.body)

    def test_signer_and_version_are_checked_before_commit_and_failure_abandons(self):
        self.assertLess(self.body.index('SelfUpdate.verify('), self.body.index('session.commit('))
        self.assertIn('sameSigners(archive,installed)', self.body)
        self.assertIn('abandonSession(sessionId)', self.body)

    def test_GAP_self_update_stages_the_v4_idsig_beside_base_apk(self):
        # #202 correction: a system-package update needs fs-verity, which needs base.apk.idsig in the same session.
        self.assertIn('"base.apk.idsig"', self.body, 'Delivery.installSelf stages only base.apk')

    def test_GAP_github_offer_requires_and_binds_the_idsig_asset(self):
        direct = (VOICE/'DirectUpdate.java').read_text()
        self.assertRegex(direct, r'\.apk\.idsig', 'DirectUpdate eligibility needs only the APK and receipt assets')


class Q202ReleasePipeline(unittest.TestCase):
    def test_GAP_app_only_release_keeps_the_signed_idsig(self):
        text = (SRC/'scripts/host-app-release.py').read_text()
        self.assertIn('.idsig', text, 'host-app-release.py writes only the APK, receipt and SHA256SUMS')


class LauncherRecovery(unittest.TestCase):
    def test_recovery_decisions_exist_as_pure_logic(self):
        self.assertTrue((VOICE/'UpdateRecovery.java').exists())

    def test_PENDING_recovery_is_wired_into_the_real_install_path(self):
        users = [p.name for p in VOICE.glob('*.java') if p.name != 'UpdateRecovery.java'
                 and re.search(r'\bUpdateRecovery\.', p.read_text())]
        self.assertTrue(users, 'UpdateRecovery is not called from Delivery/InstallReceiver/VoiceService (PR209: awaiting App handoff)')


if __name__ == '__main__':
    unittest.main(verbosity=2)
